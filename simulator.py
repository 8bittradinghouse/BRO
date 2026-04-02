#!/usr/bin/env python3
"""Offline simulation harness for Bro on 5-minute BTC YES/NO micro-markets.

This script exercises the same core stack used by execution mode:
- MarketMakingStrategy
- OrderManager
- RiskEngine
- PaperGateway

It generates synthetic order books for multiple rolling 5-minute YES/NO pairs
and runs stress scenarios to validate behavior before live soak.
"""

from __future__ import annotations

import argparse
import collections
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import pathlib
import random
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from prodesk.common import clamp, utc_iso, utc_now
from prodesk.config import load_execution_config, validate_execution_config
from prodesk.gateway import PaperGateway
from prodesk.logging_utils import DailyJsonlWriter
from prodesk.latency_verifier import STATE_ARMED, STATE_DISARMED, STATE_PROBATION, LatencyVerifier
from prodesk.models import BookTop, Position
from prodesk.order_manager import OrderManager
from prodesk.risk import RiskEngine
from prodesk.strategy import MarketMakingStrategy
from prodesk.telemetry import Telemetry


SCENARIOS = (
    "baseline",
    "vol_spike",
    "stale_books",
    "crossed_books",
    "future_skew",
    "feed_outage",
    "target_rotation",
    "chaos_day",
    "lag_stable",
    "lag_jitter",
    "lag_collapse",
)

DIFFICULTY_LEVELS = ("normal", "hard", "nightmare")

BASE_SCENARIO_PAIRS: Dict[str, int] = {
    "baseline": 3,
    "vol_spike": 3,
    "stale_books": 2,
    "crossed_books": 2,
    "future_skew": 2,
    "feed_outage": 2,
    "target_rotation": 3,
    "chaos_day": 4,
    "lag_stable": 3,
    "lag_jitter": 3,
    "lag_collapse": 3,
}


@dataclasses.dataclass(frozen=True)
class ScenarioSettings:
    target_pairs: int
    base_sigma: float
    spike_sigma: float
    base_spread: float
    spike_spread: float
    spike_start_step: int
    spike_end_step: int
    stale_every_steps: int
    crossed_every_steps: int
    future_every_steps: int
    future_offset_sec: float
    outage_start_step: int
    outage_end_step: int
    outage_fraction: float
    shock_probability: float
    shock_sigma: float
    thin_liquidity_probability: float
    thin_liquidity_size: float
    force_rotation: bool
    rotate_every_steps: int


@dataclasses.dataclass
class Window:
    market_id: str
    strike: float
    start_step: int
    expiry_step: int
    yes_token: str
    no_token: str


@dataclasses.dataclass
class ScenarioResult:
    name: str
    run_label: str
    steps: int
    completed_steps: int
    seed: int
    difficulty: str
    fills: int
    actions: int
    max_open_orders: int
    risk_rejects: int
    stale_rejects: int
    crossed_rejects: int
    future_rejects: int
    top_risk_reject_reason: str
    top_risk_reject_count: int
    outage_steps: int
    shock_events: int
    forced_rotations: int
    distinct_tokens_seen: int
    latency_armed_steps: int
    latency_probation_steps: int
    latency_disarmed_steps: int
    final_latency_state: str
    error_events: int
    kill_switch: bool
    kill_reason: str
    final_pnl: float
    maker_rebates: float
    taker_fees: float
    slippage_cost: float
    adverse_selection_cost: float
    net_pnl_after_costs: float
    avg_expected_fill_prob: float
    avg_quality_score: float
    low_quality_skips: int
    passed: bool
    notes: List[str]


@dataclasses.dataclass
class WalletSimPolicy:
    enabled: bool
    chain_id: int
    account_address: str
    allowed_assets: Set[str]
    allowed_actions: Set[str]
    allowed_spenders: Set[str]
    security_probe_every_steps: int


class SimulatedWalletSession:
    """MetaMask-like permission/session simulator for harness safety checks."""

    def __init__(self, policy: WalletSimPolicy):
        self.policy = policy
        self.connected = False
        self.current_chain_id: Optional[int] = None
        self.account_address = policy.account_address
        self.approved_spenders: Set[str] = set()
        self.approved_count = 0
        self.blocked_count = 0
        self.submit_signatures = 0
        self.cancel_signatures = 0
        self.nonce_checks = 0
        self.gas_fee_events = 0
        self.policy_violations = 0
        self.last_error = ""

    def _record_blocked(self, reason: str) -> bool:
        self.blocked_count += 1
        self.last_error = reason
        return False

    def _allow_action(self, action: str) -> bool:
        action_norm = str(action).strip().upper()
        if action_norm not in self.policy.allowed_actions:
            return self._record_blocked(f"action_not_allowed:{action_norm}")
        return True

    def connect(self, *, chain_id: int, account_address: str) -> bool:
        if not self.policy.enabled:
            return True
        if not self._allow_action("connect"):
            return False
        if int(chain_id) != int(self.policy.chain_id):
            return self._record_blocked(f"invalid_chain_id:{chain_id}")
        self.current_chain_id = int(chain_id)
        self.connected = True
        self.account_address = str(account_address)
        return True

    def approve_spender(self, *, spender: str, asset: str) -> bool:
        if not self.policy.enabled:
            return True
        if not self._allow_action("approve_spender"):
            return False
        asset_norm = str(asset).strip().upper()
        spender_norm = str(spender).strip().upper()
        if asset_norm not in self.policy.allowed_assets:
            return self._record_blocked(f"asset_not_allowed:{asset_norm}")
        if spender_norm not in self.policy.allowed_spenders:
            return self._record_blocked(f"spender_not_allowed:{spender_norm}")
        self.approved_spenders.add(spender_norm)
        self.approved_count += 1
        return True

    def sign_order(self, *, order_id: str, notional_usdc: float, spender: str) -> bool:
        if not self.policy.enabled:
            return True
        if not self._allow_action("sign_order"):
            return False
        if not self.connected or self.current_chain_id != self.policy.chain_id:
            return self._record_blocked("wallet_not_connected")
        if notional_usdc <= 0.0:
            return self._record_blocked("invalid_notional")
        spender_norm = str(spender).strip().upper()
        if spender_norm not in self.approved_spenders:
            return self._record_blocked(f"spender_not_approved:{spender_norm}")
        self.submit_signatures += 1
        self.nonce_checks += 1
        self.gas_fee_events += 1
        return True

    def sign_cancel(self, *, order_id: str) -> bool:
        if not self.policy.enabled:
            return True
        if not self._allow_action("cancel_order"):
            return False
        if not self.connected:
            return self._record_blocked("wallet_not_connected")
        self.cancel_signatures += 1
        self.nonce_checks += 1
        self.gas_fee_events += 1
        return True

    def probe_restricted_actions(self) -> None:
        if not self.policy.enabled:
            return
        # Expected blocked operations: withdraw/bridge and unknown spender approval.
        for forbidden_action in ("WITHDRAW", "BRIDGE"):
            if forbidden_action in self.policy.allowed_actions:
                self.policy_violations += 1
            else:
                self.blocked_count += 1
        if "APPROVE_SPENDER" in self.policy.allowed_actions:
            if "UNKNOWN" in self.policy.allowed_spenders:
                self.policy_violations += 1
            else:
                self.blocked_count += 1

    def summary(self) -> Dict[str, Any]:
        return {
            "enabled": self.policy.enabled,
            "connected": self.connected,
            "chain_id": self.current_chain_id,
            "account_address": self.account_address,
            "approved_spenders": sorted(self.approved_spenders),
            "approved_count": self.approved_count,
            "order_submit_signatures": self.submit_signatures,
            "order_cancel_signatures": self.cancel_signatures,
            "nonce_checks": self.nonce_checks,
            "gas_fee_events": self.gas_fee_events,
            "blocked_attempts": self.blocked_count,
            "policy_violations": self.policy_violations,
            "last_error": self.last_error,
        }


def _parse_csv_list(value: Any, default: List[str]) -> Set[str]:
    if isinstance(value, list):
        items = [str(x).strip().upper() for x in value if str(x).strip()]
    else:
        text = str(value).strip()
        if not text:
            items = [str(x).strip().upper() for x in default if str(x).strip()]
        else:
            items = [x.strip().upper() for x in text.split(",") if x.strip()]
    return set(items)


def _build_wallet_policy(sim_cfg: Dict[str, Any]) -> WalletSimPolicy:
    return WalletSimPolicy(
        enabled=bool(sim_cfg.get("wallet_sim_enabled", True)),
        chain_id=max(1, int(sim_cfg.get("wallet_sim_chain_id", 137))),
        account_address=str(sim_cfg.get("wallet_sim_account", "0x000000000000000000000000000000000000dEaD")).strip(),
        allowed_assets=_parse_csv_list(sim_cfg.get("wallet_sim_allowed_assets", "USDC,POL"), ["USDC", "POL"]),
        allowed_actions=_parse_csv_list(
            sim_cfg.get("wallet_sim_allowed_actions", "CONNECT,APPROVE_SPENDER,SIGN_ORDER,CANCEL_ORDER,NONCE_CHECK,GAS_FEE"),
            ["CONNECT", "APPROVE_SPENDER", "SIGN_ORDER", "CANCEL_ORDER", "NONCE_CHECK", "GAS_FEE"],
        ),
        allowed_spenders=_parse_csv_list(
            sim_cfg.get("wallet_sim_allowed_spenders", "CLOB_EXCHANGE,USDC,NEG_RISK_ADAPTER"),
            ["CLOB_EXCHANGE", "USDC", "NEG_RISK_ADAPTER"],
        ),
        security_probe_every_steps=max(0, int(sim_cfg.get("wallet_sim_security_probe_every_steps", 45))),
    )


class MemoryEventLogger:
    def __init__(self, log_dir: pathlib.Path, prefix: str):
        self._writer = DailyJsonlWriter(log_dir, prefix)
        self.events: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.status: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        row = {"event_type": event_type, **payload}
        self.events.append(row)
        self._writer.write(row)

    def log_error(self, payload: Dict[str, Any]) -> None:
        self.errors.append(payload)
        self._writer.write({"event_type": "error", **payload})

    def log_status(self, payload: Dict[str, Any]) -> None:
        self.status.append(payload)
        self._writer.write({"event_type": "status", **payload})

    def close(self) -> None:
        self._writer.close()


def _make_window(index: int, step: int, dt_sec: float, strike: float) -> Window:
    horizon_steps = max(1, int(round(300.0 / dt_sec)))
    market_id = f"btc5m_{index}"
    return Window(
        market_id=market_id,
        strike=strike,
        start_step=step,
        expiry_step=step + horizon_steps,
        yes_token=f"{market_id}_yes",
        no_token=f"{market_id}_no",
    )


def _probability_up(spot: float, strike: float, sec_to_expiry: float, vol_scale: float) -> float:
    t = max(1.0, sec_to_expiry)
    width = max(20.0, vol_scale * 90.0 * math.sqrt(t / 300.0))
    z = clamp((spot - strike) / width, -20.0, 20.0)
    return 1.0 / (1.0 + math.exp(-z))


def _make_top(
    token_id: str,
    mid: float,
    spread: float,
    size: float,
    *,
    stale: bool = False,
    crossed: bool = False,
    future_skew_sec: float = 0.0,
    now_ts: Optional[dt.datetime] = None,
) -> BookTop:
    half = max(0.0005, spread / 2.0)
    bid = clamp(mid - half, 0.001, 0.999)
    ask = clamp(mid + half, 0.001, 0.999)
    if crossed:
        bid = clamp(mid + half, 0.001, 0.999)
        ask = clamp(mid - half, 0.001, 0.999)
    if ask <= bid and not crossed:
        ask = clamp(bid + 0.001, 0.001, 0.999)
    ts = now_ts or utc_now()
    if stale:
        ts = ts - dt.timedelta(seconds=20)
    if future_skew_sec > 0.0:
        ts = ts + dt.timedelta(seconds=future_skew_sec)
    return BookTop(
        token_id=token_id,
        ts_utc=utc_iso(ts),
        source="sim",
        best_bid_price=bid,
        best_bid_size=size,
        best_ask_price=ask,
        best_ask_size=size,
    )


def _build_base_cfg(config_path: pathlib.Path) -> Dict[str, Any]:
    cfg = load_execution_config(config_path)
    cfg = copy.deepcopy(cfg)
    cfg["mode"] = "paper"
    cfg["targets"]["discovery"]["enabled"] = False
    cfg["targets"]["token_ids"] = ["bootstrap_yes", "bootstrap_no"]
    cfg["chainlink"]["enabled"] = False
    cfg["alerts"]["enabled"] = False
    cfg["metrics"]["enabled"] = False
    cfg["runtime"]["cancel_all_on_exit"] = False
    # Simulator event timestamps are synthetic and must remain reproducible across
    # same-seed runs; keep replace-min-rest disabled in this harness context.
    cfg["runtime"]["maker_replace_min_rest_sec"] = 0.0
    cfg["risk"]["max_book_age_sec"] = 8.0
    validate_execution_config(cfg)
    return cfg


def _config_fingerprint(cfg: Dict[str, Any]) -> str:
    rendered = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _write_json_atomic(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".tmp_",
        suffix=".json",
        delete=False,
    ) as tf:
        tmp_path = pathlib.Path(tf.name)
        json.dump(payload, tf, indent=2, ensure_ascii=True, sort_keys=True, default=str)
        tf.write("\n")
    tmp_path.replace(path)


def _roll_windows(
    active: List[Window],
    *,
    step: int,
    dt_sec: float,
    spot: float,
    next_index: int,
    target_pairs: int,
) -> int:
    active[:] = [w for w in active if step < w.expiry_step]
    while len(active) < target_pairs:
        jitter = ((next_index % 5) - 2) * 12.5
        active.append(_make_window(next_index, step, dt_sec, strike=spot + jitter))
        next_index += 1
    return next_index


def _phase_window(steps: int, start_ratio: float, end_ratio: float) -> Tuple[int, int]:
    if steps <= 1:
        return 0, 0
    start = int(max(0.0, start_ratio) * (steps - 1))
    end = int(max(start_ratio, end_ratio) * (steps - 1))
    end = min(steps - 1, max(start, end))
    return start, end


def _difficulty_scale(difficulty: str) -> Dict[str, float]:
    if difficulty == "hard":
        return {
            "sigma_mult": 1.8,
            "spread_mult": 1.2,
            "fault_freq_mult": 1.5,
            "outage_add": 0.15,
            "shock_add": 0.02,
            "thin_liq_add": 0.10,
            "rotation_mult": 0.75,
        }
    if difficulty == "nightmare":
        return {
            "sigma_mult": 2.8,
            "spread_mult": 1.4,
            "fault_freq_mult": 2.2,
            "outage_add": 0.30,
            "shock_add": 0.05,
            "thin_liq_add": 0.20,
            "rotation_mult": 0.55,
        }
    return {
        "sigma_mult": 1.0,
        "spread_mult": 1.0,
        "fault_freq_mult": 1.0,
        "outage_add": 0.0,
        "shock_add": 0.0,
        "thin_liq_add": 0.0,
        "rotation_mult": 1.0,
    }


def _build_scenario_settings(
    *,
    name: str,
    cfg: Dict[str, Any],
    steps: int,
    difficulty: str,
    target_pairs_override: Optional[int],
) -> ScenarioSettings:
    diff = _difficulty_scale(difficulty)
    max_pairs_cfg = max(1, int(cfg["targets"]["discovery"].get("max_pairs", 3)))

    if target_pairs_override is not None:
        target_pairs = max(1, int(target_pairs_override))
    elif difficulty == "normal":
        target_pairs = min(max_pairs_cfg, BASE_SCENARIO_PAIRS.get(name, 3))
    else:
        target_pairs = max_pairs_cfg

    vol_start, vol_end = _phase_window(steps, 0.33, 0.53)
    outage_start, outage_end = _phase_window(steps, 0.25, 0.55)

    rotate_every = 0
    if name == "target_rotation":
        rotate_every = 45
    elif name == "chaos_day":
        rotate_every = 25

    rotate_every = int(max(1, round(rotate_every * diff["rotation_mult"]))) if rotate_every > 0 else 0

    stale_every = 0
    if name in {"stale_books", "chaos_day"}:
        stale_every = 9
    crossed_every = 0
    if name in {"crossed_books", "chaos_day"}:
        crossed_every = 11
    future_every = 0
    if name in {"future_skew", "chaos_day"}:
        future_every = 13
    if stale_every > 0:
        stale_every = max(2, int(round(stale_every / diff["fault_freq_mult"])))
    if crossed_every > 0:
        crossed_every = max(2, int(round(crossed_every / diff["fault_freq_mult"])))
    if future_every > 0:
        future_every = max(2, int(round(future_every / diff["fault_freq_mult"])))

    outage_fraction = 0.0
    if name in {"feed_outage", "chaos_day"}:
        outage_fraction = 0.45
    if outage_fraction > 0.0:
        outage_fraction = clamp(outage_fraction + diff["outage_add"], 0.0, 0.95)

    shock_probability = 0.0
    shock_sigma = 0.0
    if name in {"vol_spike", "chaos_day"}:
        shock_probability = 0.01
        shock_sigma = 95.0
    if shock_probability > 0.0:
        shock_probability = clamp(shock_probability + diff["shock_add"], 0.0, 0.5)
        shock_sigma *= diff["sigma_mult"]

    thin_liq_probability = 0.0
    if name in {"feed_outage", "chaos_day"}:
        thin_liq_probability = 0.05
    if thin_liq_probability > 0.0:
        thin_liq_probability = clamp(thin_liq_probability + diff["thin_liq_add"], 0.0, 0.6)

    base_sigma = 6.0 * diff["sigma_mult"]
    spike_sigma = 30.0 * diff["sigma_mult"]
    base_spread = 0.025 * diff["spread_mult"]
    spike_spread = 0.05 * diff["spread_mult"]
    if name not in {"vol_spike", "chaos_day"}:
        spike_sigma = base_sigma
        spike_spread = base_spread

    return ScenarioSettings(
        target_pairs=target_pairs,
        base_sigma=base_sigma,
        spike_sigma=spike_sigma,
        base_spread=clamp(base_spread, 0.004, 0.3),
        spike_spread=clamp(spike_spread, 0.004, 0.35),
        spike_start_step=vol_start,
        spike_end_step=vol_end,
        stale_every_steps=stale_every,
        crossed_every_steps=crossed_every,
        future_every_steps=future_every,
        future_offset_sec=5.0,
        outage_start_step=outage_start,
        outage_end_step=outage_end,
        outage_fraction=outage_fraction,
        shock_probability=shock_probability,
        shock_sigma=shock_sigma,
        thin_liquidity_probability=thin_liq_probability,
        thin_liquidity_size=2.0,
        force_rotation=(name in {"target_rotation", "chaos_day"}),
        rotate_every_steps=rotate_every,
    )


def run_scenario(
    *,
    name: str,
    cfg: Dict[str, Any],
    steps: int,
    dt_sec: float,
    seed: int,
    out_dir: pathlib.Path,
    difficulty: str = "normal",
    target_pairs_override: Optional[int] = None,
    run_label: Optional[str] = None,
) -> ScenarioResult:
    if steps <= 0:
        raise ValueError(f"steps must be > 0, got {steps!r}")
    if dt_sec <= 0.0:
        raise ValueError(f"dt_sec must be > 0, got {dt_sec!r}")
    if not str(name).strip():
        raise ValueError("scenario name must be non-empty")
    if difficulty not in DIFFICULTY_LEVELS:
        raise ValueError(f"difficulty must be one of {DIFFICULTY_LEVELS}, got {difficulty!r}")
    rng = random.Random(seed)
    run_label_final = str(run_label or f"seed_{seed}").strip() or f"seed_{seed}"
    scenario_dir = out_dir / name / run_label_final
    scenario_dir.mkdir(parents=True, exist_ok=True)
    events = MemoryEventLogger(scenario_dir, "sim_events")
    telemetry = Telemetry()
    gateway = PaperGateway()
    latency_verifier = LatencyVerifier(cfg.get("latency_verifier", {}))
    settings = _build_scenario_settings(
        name=name,
        cfg=cfg,
        steps=steps,
        difficulty=difficulty,
        target_pairs_override=target_pairs_override,
    )
    scenario_meta = {
        "scenario": name,
        "run_label": run_label_final,
        "seed": int(seed),
        "difficulty": difficulty,
        "steps": int(steps),
        "dt_sec": float(dt_sec),
        "target_pairs_override": target_pairs_override,
        "settings": dataclasses.asdict(settings),
        "config_fingerprint_sha256": _config_fingerprint(cfg),
        "ts_utc": utc_iso(),
    }
    _write_json_atomic(scenario_dir / "scenario_meta.json", scenario_meta)

    active_windows: List[Window] = []
    next_window_index = 0
    target_pairs = settings.target_pairs
    spot = 65000.0
    next_window_index = _roll_windows(
        active_windows,
        step=0,
        dt_sec=dt_sec,
        spot=spot,
        next_index=next_window_index,
        target_pairs=target_pairs,
    )
    token_ids: List[str] = []
    for window in active_windows:
        token_ids.extend([window.yes_token, window.no_token])
    cfg = copy.deepcopy(cfg)
    cfg["targets"]["token_ids"] = token_ids
    validate_execution_config(cfg)

    sim_start_ts = utc_now()
    sim_step_ref = {"value": 0}

    def sim_utc_now() -> dt.datetime:
        return sim_start_ts + dt.timedelta(seconds=float(sim_step_ref["value"]) * dt_sec)

    def sim_monotonic() -> float:
        return float(sim_step_ref["value"]) * dt_sec

    positions = {token_id: Position(token_id=token_id) for token_id in token_ids}
    risk = RiskEngine(
        cfg["risk"],
        positions,
        monotonic_fn=sim_monotonic,
        utc_now_fn=sim_utc_now,
    )
    strategy = MarketMakingStrategy(cfg["strategy"])
    manager = OrderManager(
        gateway=gateway,
        strategy=strategy,
        risk=risk,
        events=events,
        telemetry=telemetry,
        runtime_cfg=cfg["runtime"],
        strategy_cfg=cfg["strategy"],
        now_fn=sim_utc_now,
    )

    fills_total = 0
    actions_total = 0
    max_open_orders = 0
    completed_steps = 0
    final_pnl = 0.0
    maker_rebates = 0.0
    taker_fees = 0.0
    slippage_cost = 0.0
    adverse_selection_cost = 0.0
    outage_steps = 0
    shock_events = 0
    forced_rotations = 0
    seen_tokens: Set[str] = set(token_ids)
    latency_armed_steps = 0
    latency_probation_steps = 0
    latency_disarmed_steps = 0
    final_latency_state = STATE_DISARMED
    notes: List[str] = []
    order_meta_by_id: Dict[str, Dict[str, Any]] = {}
    event_cursor = 0
    sim_cost_cfg = cfg.get("simulation", {})
    maker_rebate_bps = max(0.0, float(sim_cost_cfg.get("maker_rebate_bps", 0.50)))
    taker_fee_curve_rate = max(0.0, float(sim_cost_cfg.get("taker_fee_curve_rate", 0.0624)))
    taker_slippage_bps = max(0.0, float(sim_cost_cfg.get("taker_slippage_bps", 2.0)))
    adverse_selection_bps = max(0.0, float(sim_cost_cfg.get("adverse_selection_bps", 1.0)))
    quality_probs: List[float] = []
    quality_scores: List[float] = []
    wallet_policy = _build_wallet_policy(sim_cost_cfg)
    wallet = SimulatedWalletSession(wallet_policy)
    if wallet_policy.enabled:
        connected = wallet.connect(chain_id=wallet_policy.chain_id, account_address=wallet_policy.account_address)
        approved = wallet.approve_spender(spender="CLOB_EXCHANGE", asset="USDC")
        events.log_event(
            "wallet_session_start",
            {
                "ts_utc": utc_iso(sim_utc_now()),
                "wallet_connected": connected,
                "wallet_chain_id": wallet_policy.chain_id,
                "wallet_account": wallet_policy.account_address,
                "wallet_approval_seeded": approved,
            },
        )
        if not connected:
            notes.append(f"wallet_connect_failed:{wallet.last_error}")
        if not approved:
            notes.append(f"wallet_approve_failed:{wallet.last_error}")
    try:
        for step in range(steps):
            sim_step_ref["value"] = step
            if settings.force_rotation:
                if settings.rotate_every_steps > 0 and step > 0 and step % settings.rotate_every_steps == 0 and active_windows:
                    # Force rolling-market churn so order cleanup logic is exercised.
                    oldest = min(active_windows, key=lambda w: w.start_step)
                    active_windows.remove(oldest)
                    forced_rotations += 1
                    telemetry.incr("forced_rotations")
                next_window_index = _roll_windows(
                    active_windows,
                    step=step,
                    dt_sec=dt_sec,
                    spot=spot,
                    next_index=next_window_index,
                    target_pairs=target_pairs,
                )
            else:
                active_windows[:] = [w for w in active_windows if step < w.expiry_step]
                if not active_windows:
                    next_window_index = _roll_windows(
                        active_windows,
                        step=step,
                        dt_sec=dt_sec,
                        spot=spot,
                        next_index=next_window_index,
                        target_pairs=target_pairs,
                    )

            in_spike_phase = settings.spike_start_step <= step <= settings.spike_end_step
            sigma = settings.spike_sigma if in_spike_phase else settings.base_sigma
            if settings.shock_probability > 0.0 and rng.random() < settings.shock_probability:
                spot += rng.gauss(0.0, settings.shock_sigma)
                shock_events += 1
                telemetry.incr("spot_shocks")
            spot += rng.gauss(0.0, sigma)

            tracked_tokens = {token for w in active_windows for token in (w.yes_token, w.no_token)}
            books: Dict[str, BookTop] = {}
            seen_tokens.update(tracked_tokens)
            spread = settings.spike_spread if in_spike_phase else settings.base_spread

            stale_mode = settings.stale_every_steps > 0 and (step % settings.stale_every_steps == 0)
            crossed_mode = settings.crossed_every_steps > 0 and (step % settings.crossed_every_steps == 0)
            future_mode = settings.future_every_steps > 0 and (step % settings.future_every_steps == 0)
            outage_mode = (
                settings.outage_fraction > 0.0
                and settings.outage_start_step <= step <= settings.outage_end_step
            )
            dropped_this_step = False

            for i, window in enumerate(active_windows):
                sec_to_expiry = max(0.0, (window.expiry_step - step) * dt_sec)
                p_yes = _probability_up(spot, window.strike, sec_to_expiry, vol_scale=1.0 + sigma / 20.0)
                p_no = clamp(1.0 - p_yes, 0.001, 0.999)
                size = 80.0 + abs(rng.gauss(0.0, 20.0))
                sim_now = sim_utc_now()
                if settings.thin_liquidity_probability > 0.0 and rng.random() < settings.thin_liquidity_probability:
                    size = settings.thin_liquidity_size + abs(rng.gauss(0.0, 2.0))

                if outage_mode and rng.random() < settings.outage_fraction:
                    dropped_this_step = True
                    continue

                yes_top = _make_top(
                    window.yes_token,
                    p_yes,
                    spread,
                    size,
                    stale=stale_mode,
                    crossed=(crossed_mode and i == 0),
                    future_skew_sec=(settings.future_offset_sec if future_mode and i == 0 else 0.0),
                    now_ts=sim_now,
                )
                no_top = _make_top(
                    window.no_token,
                    p_no,
                    spread,
                    size,
                    stale=stale_mode,
                    crossed=False,
                    future_skew_sec=(settings.future_offset_sec if future_mode and i == 0 else 0.0),
                    now_ts=sim_now,
                )
                books[yes_top.token_id] = yes_top
                books[no_top.token_id] = no_top
                gateway.on_book(yes_top)
                gateway.on_book(no_top)

            if dropped_this_step:
                outage_steps += 1
                telemetry.incr("outage_steps")

            if name in {"lag_stable", "lag_jitter", "lag_collapse"}:
                for token_id in tracked_tokens:
                    lag_ms = 0.0
                    if name == "lag_stable":
                        lag_ms = max(1.0, 180.0 + rng.gauss(0.0, 12.0))
                    elif name == "lag_jitter":
                        # Regime mixing: some samples show edge, others collapse.
                        if rng.random() < 0.4:
                            lag_ms = max(1.0, 180.0 + rng.gauss(0.0, 20.0))
                        else:
                            lag_ms = max(1.0, 55.0 + rng.gauss(0.0, 25.0))
                    elif name == "lag_collapse":
                        if step < int(steps * 0.50):
                            lag_ms = max(1.0, 170.0 + rng.gauss(0.0, 15.0))
                        else:
                            lag_ms = max(1.0, 25.0 + rng.gauss(0.0, 8.0))
                    latency_verifier.observe(token_id=token_id, lag_ms=lag_ms, ingest_lag_ms=lag_ms * 0.35)

            if wallet_policy.enabled and wallet_policy.security_probe_every_steps > 0:
                if step > 0 and (step % wallet_policy.security_probe_every_steps == 0):
                    wallet.probe_restricted_actions()
                    events.log_event(
                        "wallet_security_probe",
                        {
                            "ts_utc": utc_iso(sim_utc_now()),
                            "step": step,
                            "blocked_attempts": wallet.blocked_count,
                            "policy_violations": wallet.policy_violations,
                        },
                    )

            lat_snapshot = latency_verifier.snapshot(active_tokens=sorted(tracked_tokens))
            final_latency_state = lat_snapshot.state
            if lat_snapshot.state == STATE_ARMED:
                latency_armed_steps += 1
            elif lat_snapshot.state == STATE_PROBATION:
                latency_probation_steps += 1
            else:
                latency_disarmed_steps += 1

            stale_canceled = manager.cancel_stale_orders(action_budget=2)
            if stale_canceled:
                telemetry.incr("stale_quote_cancels", stale_canceled)

            if settings.force_rotation:
                orphan_canceled = manager.cancel_non_target_orders(tracked_tokens, action_budget=6)
                if orphan_canceled:
                    telemetry.incr("orphan_cancels", orphan_canceled)

            summary = manager.step(books, tracked_tokens=tracked_tokens)
            fills_total += int(summary["fills"])
            actions_total += int(summary["actions"])
            max_open_orders = max(max_open_orders, int(summary["open_orders"]))

            if event_cursor < len(events.events):
                for evt in events.events[event_cursor:]:
                    event_type = str(evt.get("event_type") or "")
                    if event_type == "order_submit":
                        order_id = str(evt.get("order_id") or "")
                        if order_id:
                            order_meta_by_id[order_id] = {
                                "tif": str(evt.get("tif") or "GTC").upper(),
                                "post_only": evt.get("post_only"),
                                "reason": str(evt.get("reason") or ""),
                                "quality_score": float(evt.get("quality_score") or 0.0),
                            }
                        efp = evt.get("expected_fill_prob")
                        qs = evt.get("quality_score")
                        if isinstance(efp, (int, float)):
                            quality_probs.append(float(efp))
                        if isinstance(qs, (int, float)):
                            quality_scores.append(float(qs))
                        if wallet_policy.enabled:
                            order_id = str(evt.get("order_id") or "")
                            notional = float(evt.get("price") or 0.0) * float(evt.get("size") or 0.0)
                            ok = wallet.sign_order(order_id=order_id, notional_usdc=notional, spender="CLOB_EXCHANGE")
                            events.log_event(
                                "wallet_sign_order",
                                {
                                    "ts_utc": utc_iso(sim_utc_now()),
                                    "order_id": order_id,
                                    "wallet_ok": ok,
                                    "wallet_error": wallet.last_error if not ok else "",
                                },
                            )
                            if not ok:
                                notes.append(f"wallet_sign_order_failed:{wallet.last_error}")
                    elif event_type == "order_cancel":
                        if wallet_policy.enabled:
                            order_id = str(evt.get("order_id") or "")
                            ok = wallet.sign_cancel(order_id=order_id)
                            events.log_event(
                                "wallet_sign_cancel",
                                {
                                    "ts_utc": utc_iso(sim_utc_now()),
                                    "order_id": order_id,
                                    "wallet_ok": ok,
                                    "wallet_error": wallet.last_error if not ok else "",
                                },
                            )
                            if not ok:
                                notes.append(f"wallet_sign_cancel_failed:{wallet.last_error}")
                    elif event_type == "fill":
                        order_id = str(evt.get("order_id") or "")
                        meta = order_meta_by_id.get(order_id, {})
                        side = str(evt.get("side") or "").upper()
                        token_id = str(evt.get("token_id") or "")
                        price = float(evt.get("price") or 0.0)
                        size = float(evt.get("size") or 0.0)
                        if size <= 0.0 or price <= 0.0:
                            continue
                        notional = price * size
                        tif = str(meta.get("tif") or "GTC").upper()
                        post_only = meta.get("post_only")
                        is_taker = bool(post_only is False or tif in {"IOC", "FOK"})
                        if is_taker:
                            p = clamp(price, 0.001, 0.999)
                            taker_fees += notional * (p * (1.0 - p) * taker_fee_curve_rate)
                            slippage_cost += notional * (taker_slippage_bps / 10_000.0)
                        else:
                            maker_rebates += notional * (maker_rebate_bps / 10_000.0)
                        top = books.get(token_id)
                        if top is not None and top.midpoint is not None:
                            mid = float(top.midpoint)
                            if side == "BUY":
                                adverse_selection_cost += max(0.0, price - mid) * size
                            elif side == "SELL":
                                adverse_selection_cost += max(0.0, mid - price) * size
                            adverse_selection_cost += notional * (adverse_selection_bps / 10_000.0)
                event_cursor = len(events.events)

            mids = {token_id: top.midpoint for token_id, top in books.items()}
            final_pnl, _ = risk.mark_to_market(mids)
            completed_steps += 1

            if risk.kill_switch:
                notes.append(f"kill_switch at step={step}")
                break
    finally:
        events.close()

    # Recount with full scan so reason tally is exact.
    reason_counts = collections.Counter(
        str(evt.get("reason") or "unknown")
        for evt in events.events
        if evt.get("event_type") == "risk_reject"
    )
    stale_rejects = int(reason_counts.get("stale_book", 0))
    crossed_rejects = int(reason_counts.get("crossed_market", 0))
    future_rejects = int(reason_counts.get("future_book_timestamp", 0))
    risk_rejects = sum(reason_counts.values())
    top_reason = "none"
    top_reason_count = 0
    if reason_counts:
        top_reason, top_reason_count = reason_counts.most_common(1)[0]
    kill_switch = bool(risk.kill_switch)
    kill_reason = str(risk.kill_reason or "")
    error_events = len(events.errors)
    distinct_tokens_seen = len(seen_tokens)
    net_pnl_after_costs = final_pnl + maker_rebates - taker_fees - slippage_cost - adverse_selection_cost
    avg_expected_fill_prob = (sum(quality_probs) / float(len(quality_probs))) if quality_probs else 0.0
    avg_quality_score = (sum(quality_scores) / float(len(quality_scores))) if quality_scores else 0.0
    low_quality_skips = int(telemetry.counters.get("low_quality_quote_skips", 0))

    passed = True
    if kill_switch:
        passed = False
        notes.append(f"kill_reason={kill_reason}")
    if name == "stale_books" and stale_rejects == 0:
        passed = False
        notes.append("expected stale_book rejections but saw none")
    if name == "crossed_books" and crossed_rejects == 0:
        passed = False
        notes.append("expected crossed_market rejections but saw none")
    if name == "future_skew" and future_rejects == 0:
        passed = False
        notes.append("expected future_book_timestamp rejections but saw none")
    if name in {"feed_outage", "chaos_day"} and outage_steps == 0:
        passed = False
        notes.append("expected outage phase but saw no dropped books")
    if name in {"target_rotation", "chaos_day"} and distinct_tokens_seen <= target_pairs * 2:
        passed = False
        notes.append("expected token churn but tracked token set did not rotate")
    if name == "lag_stable":
        expected_armed_steps = max(5, int(steps * 0.30))
        if latency_armed_steps < expected_armed_steps:
            passed = False
            notes.append(
                f"expected latency verifier to arm for >= {expected_armed_steps} steps, got {latency_armed_steps}"
            )
    if name == "lag_collapse":
        if latency_armed_steps == 0:
            passed = False
            notes.append("expected latency verifier to arm before collapse")
        if latency_disarmed_steps == 0:
            passed = False
            notes.append("expected latency verifier to disarm after collapse")
        if final_latency_state == STATE_ARMED:
            passed = False
            notes.append("expected final latency state not armed after collapse")
    if completed_steps < steps:
        passed = False
        notes.append("did not complete scenario")
    if error_events > 0:
        passed = False
        notes.append(f"error_events={error_events}")
    if wallet_policy.enabled:
        wallet_summary = wallet.summary()
        _write_json_atomic(scenario_dir / "wallet_sim_summary.json", wallet_summary)
        if not wallet_summary.get("connected", False):
            passed = False
            notes.append("wallet_not_connected")
        if int(wallet_summary.get("policy_violations", 0)) > 0:
            passed = False
            notes.append("wallet_policy_violation")
        if int(wallet_summary.get("order_submit_signatures", 0)) <= 0:
            passed = False
            notes.append("wallet_no_submit_signatures")
    else:
        _write_json_atomic(
            scenario_dir / "wallet_sim_summary.json",
            {"enabled": False, "reason": "wallet_sim_disabled"},
        )

    return ScenarioResult(
        name=name,
        run_label=run_label_final,
        steps=steps,
        completed_steps=completed_steps,
        seed=int(seed),
        difficulty=difficulty,
        fills=fills_total,
        actions=actions_total,
        max_open_orders=max_open_orders,
        risk_rejects=risk_rejects,
        stale_rejects=stale_rejects,
        crossed_rejects=crossed_rejects,
        future_rejects=future_rejects,
        top_risk_reject_reason=top_reason,
        top_risk_reject_count=int(top_reason_count),
        outage_steps=outage_steps,
        shock_events=shock_events,
        forced_rotations=forced_rotations,
        distinct_tokens_seen=distinct_tokens_seen,
        latency_armed_steps=latency_armed_steps,
        latency_probation_steps=latency_probation_steps,
        latency_disarmed_steps=latency_disarmed_steps,
        final_latency_state=final_latency_state,
        error_events=error_events,
        kill_switch=kill_switch,
        kill_reason=kill_reason,
        final_pnl=final_pnl,
        maker_rebates=maker_rebates,
        taker_fees=taker_fees,
        slippage_cost=slippage_cost,
        adverse_selection_cost=adverse_selection_cost,
        net_pnl_after_costs=net_pnl_after_costs,
        avg_expected_fill_prob=avg_expected_fill_prob,
        avg_quality_score=avg_quality_score,
        low_quality_skips=low_quality_skips,
        passed=passed,
        notes=notes,
    )


def _print_results(results: List[ScenarioResult], out_dir: pathlib.Path) -> None:
    print(f"simulation_log_dir={out_dir}")
    print(
        "scenario,run_label,seed,difficulty,passed,completed_steps,steps,fills,actions,max_open_orders,risk_rejects,stale_rejects,crossed_rejects,future_rejects,top_risk_reject_reason,top_risk_reject_count,outage_steps,shock_events,forced_rotations,distinct_tokens_seen,latency_armed_steps,latency_probation_steps,latency_disarmed_steps,final_latency_state,error_events,kill_switch,final_pnl,maker_rebates,taker_fees,slippage_cost,adverse_selection_cost,net_pnl_after_costs,avg_expected_fill_prob,avg_quality_score,low_quality_skips"
    )
    for res in results:
        print(
            ",".join(
                [
                    res.name,
                    res.run_label,
                    str(res.seed),
                    res.difficulty,
                    "yes" if res.passed else "no",
                    str(res.completed_steps),
                    str(res.steps),
                    str(res.fills),
                    str(res.actions),
                    str(res.max_open_orders),
                    str(res.risk_rejects),
                    str(res.stale_rejects),
                    str(res.crossed_rejects),
                    str(res.future_rejects),
                    res.top_risk_reject_reason,
                    str(res.top_risk_reject_count),
                    str(res.outage_steps),
                    str(res.shock_events),
                    str(res.forced_rotations),
                    str(res.distinct_tokens_seen),
                    str(res.latency_armed_steps),
                    str(res.latency_probation_steps),
                    str(res.latency_disarmed_steps),
                    str(res.final_latency_state),
                    str(res.error_events),
                    "yes" if res.kill_switch else "no",
                    f"{res.final_pnl:.6f}",
                    f"{res.maker_rebates:.6f}",
                    f"{res.taker_fees:.6f}",
                    f"{res.slippage_cost:.6f}",
                    f"{res.adverse_selection_cost:.6f}",
                    f"{res.net_pnl_after_costs:.6f}",
                    f"{res.avg_expected_fill_prob:.6f}",
                    f"{res.avg_quality_score:.6f}",
                    str(res.low_quality_skips),
                ]
            )
        )
        if res.notes:
            print(f"notes[{res.name}:{res.run_label}]={'; '.join(res.notes)}")


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    data = sorted(float(v) for v in values)
    if len(data) == 1:
        return data[0]
    q_clamped = clamp(q, 0.0, 1.0)
    idx = int(round((len(data) - 1) * q_clamped))
    idx = max(0, min(len(data) - 1, idx))
    return data[idx]


def _build_aggregate_summary(results: List[ScenarioResult]) -> List[Dict[str, Any]]:
    by_scenario: Dict[str, List[ScenarioResult]] = {}
    for res in results:
        by_scenario.setdefault(res.name, []).append(res)

    out: List[Dict[str, Any]] = []
    for scenario in sorted(by_scenario.keys()):
        rows = by_scenario[scenario]
        runs = len(rows)
        passed_runs = sum(1 for r in rows if r.passed)
        pass_rate = (float(passed_runs) / float(runs)) if runs > 0 else 0.0
        net_vals = [float(r.net_pnl_after_costs) for r in rows]
        reject_vals = [float(r.risk_rejects) for r in rows]
        kill_switch_runs = sum(1 for r in rows if r.kill_switch)
        out.append(
            {
                "scenario": scenario,
                "runs": runs,
                "passed_runs": passed_runs,
                "pass_rate": pass_rate,
                "kill_switch_runs": kill_switch_runs,
                "net_pnl_after_costs_p50": _percentile(net_vals, 0.50),
                "net_pnl_after_costs_p95": _percentile(net_vals, 0.95),
                "risk_rejects_p50": _percentile(reject_vals, 0.50),
                "risk_rejects_p95": _percentile(reject_vals, 0.95),
            }
        )
    return out


def _print_aggregate_summary(aggregate: List[Dict[str, Any]]) -> None:
    if not aggregate:
        return
    print("aggregate_summary")
    print("scenario,runs,passed_runs,pass_rate,kill_switch_runs,net_pnl_after_costs_p50,net_pnl_after_costs_p95,risk_rejects_p50,risk_rejects_p95")
    for row in aggregate:
        print(
            ",".join(
                [
                    str(row.get("scenario", "")),
                    str(int(row.get("runs", 0))),
                    str(int(row.get("passed_runs", 0))),
                    f"{float(row.get('pass_rate', 0.0)):.6f}",
                    str(int(row.get("kill_switch_runs", 0))),
                    f"{float(row.get('net_pnl_after_costs_p50', 0.0)):.6f}",
                    f"{float(row.get('net_pnl_after_costs_p95', 0.0)):.6f}",
                    f"{float(row.get('risk_rejects_p50', 0.0)):.2f}",
                    f"{float(row.get('risk_rejects_p95', 0.0)):.2f}",
                ]
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro offline 5-minute BTC YES/NO simulator")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--scenario", default="all", choices=("all",) + SCENARIOS, help="Scenario to run")
    parser.add_argument("--steps", type=int, default=360, help="Simulation steps per scenario")
    parser.add_argument("--dt-sec", type=float, default=1.0, help="Seconds per simulation step")
    parser.add_argument("--difficulty", default="normal", choices=DIFFICULTY_LEVELS, help="Stress preset")
    parser.add_argument("--pairs", type=int, default=None, help="Override number of concurrent 5-minute windows")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed")
    parser.add_argument("--seed-count", type=int, default=1, help="Number of seed runs per scenario")
    parser.add_argument("--seed-step", type=int, default=1009, help="Seed increment between runs")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="Minimum required pass rate per scenario across seed runs",
    )
    parser.add_argument("--out-dir", default="./logs_sim", help="Directory for simulation logs")
    args = parser.parse_args()

    config_path = pathlib.Path(args.config).resolve()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = _build_base_cfg(config_path)
    seed_count = max(1, int(args.seed_count))
    seed_step = max(1, int(args.seed_step))
    min_pass_rate = clamp(float(args.min_pass_rate), 0.0, 1.0)
    if int(args.steps) <= 0:
        raise SystemExit("steps must be > 0")
    if float(args.dt_sec) <= 0.0:
        raise SystemExit("dt-sec must be > 0")
    if seed_count > 1000:
        raise SystemExit(f"seed-count too large: {seed_count}; expected <= 1000")

    scenario_names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results: List[ScenarioResult] = []
    base_seed = int(args.seed)
    for idx, name in enumerate(scenario_names):
        scenario_seed_base = base_seed + (idx * seed_step * seed_count)
        for seed_idx in range(seed_count):
            run_seed = scenario_seed_base + (seed_idx * seed_step)
            result = run_scenario(
                name=name,
                cfg=cfg,
                steps=max(1, int(args.steps)),
                dt_sec=max(0.1, float(args.dt_sec)),
                seed=run_seed,
                out_dir=out_dir,
                difficulty=str(args.difficulty),
                target_pairs_override=args.pairs,
                run_label=f"seed_{run_seed}",
            )
            results.append(result)

    _print_results(results, out_dir)
    aggregate = _build_aggregate_summary(results)
    _print_aggregate_summary(aggregate)

    summary_path = out_dir / "summary.json"
    _write_json_atomic(summary_path, [dataclasses.asdict(r) for r in results])
    aggregate_path = out_dir / "aggregate_summary.json"
    _write_json_atomic(aggregate_path, aggregate)
    failed = [r for r in results if not r.passed]
    low_pass_rate = [row for row in aggregate if float(row.get("pass_rate", 0.0)) < min_pass_rate]
    if low_pass_rate:
        names = ",".join(str(row.get("scenario")) for row in low_pass_rate)
        print(f"aggregate_fail_min_pass_rate={min_pass_rate:.3f} scenarios={names}")
        raise SystemExit(1)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
