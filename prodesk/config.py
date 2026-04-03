from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from .common import parse_float, parse_ts
from .paths import normalize_execution_paths


DEFAULT_EXECUTION_CONFIG: Dict[str, Any] = {
    "bot_name": "Bro",
    "mode": "paper",
    "asset": {
        "symbol": "BTC",
        "chainlink_symbols": ["btc/usd"],
        "discovery_symbols": ["BTC"],
    },
    "runtime": {
        "poll_interval_sec": 1.0,
        "reconcile_interval_sec": 2.0,
        "status_interval_sec": 30.0,
        "duration_min": None,
        "max_actions_per_cycle": 8,
        "rest_fetch_max_workers": 4,
        "cancel_all_on_exit": True,
        "cancel_orphan_orders": True,
        "max_consecutive_failures": 12,
        "seen_trade_ids_max": 200000,
        "persist_seen_trade_ids_max": 5000,
        "max_quote_age_sec": 20.0,
        "order_rate_soft_limit_pct": 0.98,
        "cancel_rate_soft_limit_pct": 0.98,
        "log_book_top": True,
        "log_leadlag_book_move": True,
        "log_async_flush": False,
        "log_flush_every_records": 1,
        "log_flush_interval_sec": 0.25,
        "log_fsync_on_flush": False,
        "guard_stop_file": "",
        "clear_guard_stop_on_start": False,
        "paper_passive_touch_fill_enabled": False,
        "paper_passive_touch_fill_ratio": 0.15,
        "paper_passive_min_rest_sec": 1.0,
        "maker_replace_min_rest_sec": 0.0,
        "paper_passive_min_fill_size": 0.01,
        "paper_passive_near_touch_band": 0.02,
        "paper_passive_near_touch_fill_ratio": 0.08,
        "paper_background_fill_ratio": 0.0,
        "paper_liquidity_tod_scaler_enabled": False,
        "paper_liquidity_tod_start_hour_utc": 2,
        "paper_liquidity_tod_end_hour_utc": 6,
        "paper_liquidity_tod_depth_multiplier": 1.0,
        "paper_queue_position_mode": "not_modeled",
        "paper_queue_position_ahead_ratio": 0.0,
        "paper_chainlink_lag_emulation_enabled": False,
        "paper_chainlink_lag_window_low_sec": 2.0,
        "paper_chainlink_lag_window_high_sec": 15.0,
        "paper_chainlink_lag_penalty_bps_below_window": 0.0,
        "paper_chainlink_lag_penalty_bps_within_window": 0.0,
        "paper_chainlink_lag_penalty_bps_above_window": 0.0,
        "paper_enforce_setup_lock": False,
        "paper_expected_profile_name": "",
        "paper_expected_config_fingerprint_sha256": "",
    },
    "storage": {
        "log_dir": "./logs_exec",
        "state_path": "./logs_exec/state.json",
    },
    "market_data": {
        "clob_url": "https://clob.polymarket.com",
        "book_path": "/book",
        "timeout_sec": 8,
        "max_retries": 2,
        "ws": {
            "enabled": True,
            "url": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            "channel": "market",
            "stale_after_sec": 3.0,
            "heartbeat_timeout_sec": 12.0,
            "ping_interval_sec": 5.0,
            "reconnect_backoff_initial_sec": 1.0,
            "reconnect_backoff_max_sec": 20.0,
        },
    },
    "chainlink": {
        "enabled": True,
        "ws_url": "wss://ws-live-data.polymarket.com",
        "topic": "crypto_prices_chainlink",
        "symbols": ["btc/usd"],
        "symbol_for_targets": "btc/usd",
        "log_ticks": True,
        "heartbeat_timeout_sec": 15.0,
        "ping_interval_sec": 5.0,
        "reconnect_backoff_initial_sec": 1.0,
        "reconnect_backoff_max_sec": 30.0,
        "mid_move_min_delta": 0.001,
        "max_queue_size": 10000,
    },
    "doctrine": {
        "mode": "canonical",
        # Shared oracle freshness rule used by both maker and taker doctrine gates.
        "oracle_max_tick_age_sec": 1.5,
        # Canonical maker reference policy:
        # - when midpoint is unavailable and exactly one ws side is present,
        #   allow explicit bounded single-side reference classification.
        "maker_allow_bounded_single_side_reference": True,
        # Deterministic observe-first hold on new market entry: release only after both pass.
        "min_observe_cycles_on_entry": 2,
        "min_observe_seconds_on_entry": 2.0,
    },
    "latency_verifier": {
        "enabled": True,
        "require_armed_for_maker": True,
        "require_armed_for_sniper": True,
        "window_samples": 400,
        "min_samples": 80,
        "hit_threshold_ms": 120.0,
        "armed_min_median_ms": 120.0,
        "armed_min_hit_rate": 0.6,
        "probation_min_median_ms": 80.0,
        "probation_min_hit_rate": 0.45,
        "arm_consecutive_cycles": 2,
        "disarm_consecutive_cycles": 2,
        "log_sample_events": False,
        "max_sample_lag_ms": 20000.0,
        "score_enabled": True,
        "score_min_for_maker": 0.35,
        "score_min_for_taker": 0.60,
        "score_size_floor": 0.35,
        "score_size_ceiling": 1.25,
        "drift_window_samples": 80,
        "drift_max_median_drop_ms": 40.0,
        "drift_max_hit_rate_drop": 0.20,
    },
    "targets": {
        "token_ids": [],
        "token_expiry_utc_by_token": {},
        "token_side_by_token": {},
        "token_strike_by_token": {},
        "discovery": {
            "enabled": False,
            "gamma_url": "https://gamma-api.polymarket.com",
            "markets_path": "/markets",
            "symbols": ["BTC"],
            "allow_token_ids": [],
            "keywords_any": ["5 minute", "5-minute", "up or down", "up/down"],
            "tags_any": [],
            "require_binary_outcomes": True,
            "max_pairs": 4,
            "refresh_interval_sec": 60.0,
            "page_limit": 200,
            "max_pages": 10,
            "max_markets_scan": 1200,
            "timeout_sec": 8.0,
            "max_retries": 2,
            "require_fee_enabled": True,
        },
    },
    "sniper": {
        "enabled": True,
        # Clear operator semantics:
        # - arming_horizon_sec: broad horizon where sniper can consider markets.
        # - execution_cutoff_sec: latest seconds-to-expiry where taker fire is still allowed.
        # - late_fire_priority_band_sec: most urgent tail band inside execution window.
        "arming_horizon_sec": 20.0,
        "execution_cutoff_sec": 15.0,
        "late_fire_priority_band_sec": 5.0,
        # Backward-compatible aliases (kept for older configs).
        "window_start_sec": 20.0,
        "window_end_sec": 15.0,
        "allow_without_expiry_metadata": False,
        "poll_interval_sec": 0.2,
        "max_actions_per_cycle": 16,
        "cancel_stale_action_budget": 6,
        "cancel_orphan_action_budget": 12,
        "order_rate_soft_limit_pct": 1.0,
        "cancel_rate_soft_limit_pct": 1.0,
        "require_lag_verification": True,
        "lag_window_samples": 300,
        "lag_min_samples": 80,
        "lag_hit_threshold_ms": 120.0,
        "lag_min_median_ms": 120.0,
        "lag_min_hit_rate": 0.6,
        "max_chainlink_tick_age_sec": 1.5,
        "fair_vol_scale": 1.0,
        "taker": {
            "enabled": True,
            "min_edge": 0.015,
            "extreme_edge_mult": 2.0,
            "order_size": 20.0,
            "target_usd": 5.0,
            "max_orders_per_cycle": 2,
            "per_token_cooldown_sec": 0.25,
        },
    },
    "strategy": {
        "base_order_size": 25.0,
        "min_order_size": 5.0,
        "max_order_size": 200.0,
        "min_spread": 0.02,
        "max_spread": 0.25,
        "tick_size": 0.001,
        "inventory_skew_per_share": 0.0002,
        "quote_refresh_min_delta": 0.003,
        "fair_skew_factor": 0.5,
        "execution_quality": {
            "enabled": True,
            "min_expected_fill_prob": 0.06,
            "max_queue_ahead_size": 200.0,
            "queue_depth_scale": 120.0,
            "distance_scale": 0.02,
            "adverse_selection_penalty": 0.3,
        },
        "volatility": {
            "enabled": True,
            "window_sec": 30.0,
            "low_vol_threshold": 0.0015,
            "high_vol_threshold": 0.008,
            "low_vol_spread_mult": 1.35,
            "high_vol_spread_mult": 0.8,
            "low_vol_size_mult": 0.85,
            "high_vol_size_mult": 1.25,
        },
    },
    "sizing": {
        "mode": "shares",
        "min_usd": 1.0,
        "max_usd": 20.0,
        "target_usd": 5.0,
        "rounding": "floor",
        "price_source": "mid",
        "share_step": 0.01,
        "exposure_cap_mode": "per_market_total",
        "maker_competitive_min_notional_usd": 0.0,
        "maker_competitive_max_notional_usd": 0.0,
        "maker_competitive_min_shares": 0.0,
        "maker_competitive_max_shares": 0.0,
        "maker_depth_target_min_ratio": 0.0,
        "maker_depth_target_max_ratio": 0.0,
        "maker_depth_target_ratio": 0.0,
        "maker_liquidity_tod_scaler_enabled": False,
        "maker_liquidity_tod_start_hour_utc": 2,
        "maker_liquidity_tod_end_hour_utc": 6,
        "maker_liquidity_tod_depth_multiplier": 1.0,
    },
    "wallet": {
        "paper_starting_usdc": 1000.0,
        "protected_usdc_reserve": 0.0,
        "max_notional_per_order_usdc": 250.0,
        "min_pol_gas_reserve": 0.1,
        "paper_pol_balance": 10.0,
        "require_allowance": True,
        "paper_allowance_usdc": 1000000.0,
        "nonce_authority": "tx_manager",
        "halt_on_reconcile_mismatch": True,
        "reconcile_tolerance_usdc": 1e-6,
        "expected_chain_id": 137,
        "expected_wallet_address": "",
        "require_live_pol_balance_snapshot": False,
        "require_live_nonce_snapshot": False,
        "require_live_nonce_value": False,
        "require_live_pending_tx_snapshot": False,
        "live_pol_balance_fallback": 1.0,
        "max_live_reconcile_mismatch_count": 2,
    },
    "risk": {
        "max_abs_position_shares": 400.0,
        "max_notional_per_token": 250.0,
        "max_open_orders_per_token": 4,
        "max_total_open_orders": 30,
        "max_order_size": 200.0,
        "min_order_size": 1.0,
        "max_orders_per_min": 120,
        "max_cancels_per_min": 220,
        "max_book_age_sec": 6.0,
        "max_book_future_skew_sec": 2.0,
        "allow_crossed_quotes": False,
        "max_total_loss": None,
        "max_loss_per_token": None,
    },
    "alerts": {
        "enabled": False,
        "webhook_url_env": "POLY_BOT_ALERT_WEBHOOK",
        "telegram_enabled": False,
        "telegram_bot_token_env": "POLY_BOT_TELEGRAM_TOKEN",
        "telegram_chat_id_env": "POLY_BOT_TELEGRAM_CHAT_ID",
        "telegram_parse_mode": "Markdown",
        "contacts": {
            "primary": "",
            "secondary": "",
        },
        "timeout_sec": 4.0,
        "min_interval_sec": 30.0,
        "guardian_hook_file": "",
        "warn_thresholds": {
            "stale_reject_ratio": 0.20,
            "disarmed_ratio": 0.40,
            "error_ratio": 0.10,
            "reconcile_mismatch_ratio": 0.03,
            "mode_transitions_window": 6,
        },
        "page_thresholds": {
            "stale_reject_ratio": 0.35,
            "disarmed_ratio": 0.60,
            "error_ratio": 0.20,
            "reconcile_mismatch_ratio": 0.05,
            "mode_transitions_window": 10,
        },
        "auto_stop_thresholds": {
            "stale_reject_ratio": 0.50,
            "disarmed_ratio": 0.75,
            "error_ratio": 0.25,
            "reconcile_mismatch_ratio": 0.08,
            "mode_transitions_window": 16,
        },
        "auto_stop_min_samples": 12,
        "auto_stop_min_stale_rejects": 8,
        "auto_stop_min_risk_rejects": 24,
    },
    "metrics": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 9108,
        "namespace": "prodesk",
    },
    "preflight": {
        "require_live_confirmation": True,
        "check_market_data": True,
        "max_market_data_failures": 0,
        "check_clock_sync": True,
        "max_clock_skew_sec": 2.5,
        "check_endpoint_health": False,
        "endpoint_timeout_sec": 4.0,
        "endpoint_urls": [],
    },
    "security": {
        "enabled": True,
        "enforce_host_allowlist": True,
        "allowed_hosts": [
            "clob.polymarket.com",
            "ws-subscriptions-clob.polymarket.com",
            "ws-live-data.polymarket.com",
            "gamma-api.polymarket.com",
        ],
        "require_tls": True,
        "block_private_network_hosts": True,
        "enforce_storage_roots": True,
        "allowed_storage_roots": ["./logs_exec"],
        "check_path_symlinks": True,
        "check_file_permissions": True,
        "enforce_local_metrics_bind_in_live": True,
        "allow_root_user_in_paper": True,
        "allow_root_user_in_live": False,
        "require_live_security_ack": True,
        "live_security_ack_env": "SECURITY_ACK",
        "live_security_ack_value": "YES",
    },
    "ramp": {
        "enabled": False,
        "start_usd": 1.0,
        "step_usd": 1.0,
        "max_usd": 20.0,
        "evaluation_window_cycles": 200,
        "downshift_reject_ratio": 0.35,
        "downshift_stale_oracle_ratio": 0.25,
        "downshift_disarmed_ratio": 0.60,
        "downshift_reconcile_mismatch_ratio": 0.05,
        "reconcile_status_path": "",
        "disable_sniper_on_breach": True,
    },
    "operating_mode": {
        "enabled": True,
        "window_cycles": 40,
        "caution_stale_reject_ratio": 0.35,
        "maker_only_stale_reject_ratio": 0.55,
        "caution_outage_ratio": 0.20,
        "maker_only_outage_ratio": 0.40,
        "caution_disarmed_ratio": 0.50,
        "maker_only_disarmed_ratio": 0.75,
        "caution_error_ratio": 0.10,
        "maker_only_error_ratio": 0.25,
        "recover_healthy_cycles": 30,
        "safe_stop_severe_cycles": 20,
        "cautious_size_mult": 0.75,
        "maker_only_size_mult": 0.45,
        "cautious_spread_mult": 1.10,
        "maker_only_spread_mult": 1.25,
        "ws_slo_enforce_health": True,
        "ws_slo_require_book_connected": True,
        "ws_slo_require_chainlink_connected": True,
        "ws_slo_max_book_last_msg_age_sec": 12.0,
        "ws_slo_max_chainlink_last_tick_age_sec": 30.0,
        "ws_slo_bootstrap_grace_sec": 45.0,
    },
    "simulation": {
        "maker_rebate_bps": 0.50,
        "taker_fee_curve_rate": 0.0624,
        "taker_slippage_bps": 2.0,
        "adverse_selection_bps": 1.0,
    },
    "auth": {
        "host": "https://clob.polymarket.com",
        "chain_id": 137,
        "signature_type": 1,
        "private_key_env": "POLYMARKET_PRIVATE_KEY",
        "funder_env": "POLYMARKET_FUNDER",
        "private_key_source": {
            "mode": "env",
            "env": "POLYMARKET_PRIVATE_KEY",
        },
        "funder_source": {
            "mode": "env",
            "env": "POLYMARKET_FUNDER",
        },
        "enforce_post_only": True,
        "allow_taker": False,
        "open_orders_cache_ttl_sec": 0.25,
    },
    "profile": {
        "name": "default",
        "class": "canonical",
    },
}


def deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_raw_with_extends(path: pathlib.Path, *, seen: Optional[Set[pathlib.Path]] = None) -> Tuple[Dict[str, Any], List[pathlib.Path]]:
    cfg_path = path.resolve()
    seen = set(seen or set())
    if cfg_path in seen:
        raise ValueError(f"config extends cycle detected at {cfg_path}")
    seen.add(cfg_path)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("execution config root must be a mapping")

    parents_raw = raw.get("extends")
    parent_entries: List[str] = []
    if isinstance(parents_raw, str) and parents_raw.strip():
        parent_entries = [parents_raw.strip()]
    elif isinstance(parents_raw, list):
        parent_entries = [str(x).strip() for x in parents_raw if str(x).strip()]

    merged: Dict[str, Any] = {}
    source_paths: List[pathlib.Path] = []
    for parent in parent_entries:
        parent_path = pathlib.Path(parent)
        if not parent_path.is_absolute():
            parent_path = (cfg_path.parent / parent_path).resolve()
        parent_raw, parent_sources = _load_raw_with_extends(parent_path, seen=seen)
        merged = deep_merge(merged, parent_raw)
        source_paths.extend(parent_sources)

    local = dict(raw)
    local.pop("extends", None)
    merged = deep_merge(merged, local)
    source_paths.append(cfg_path)
    return merged, source_paths


def _normalize_sniper_semantics(cfg: Dict[str, Any]) -> None:
    sniper = cfg.setdefault("sniper", {})
    start = sniper.get("arming_horizon_sec", sniper.get("window_start_sec", 20.0))
    end = sniper.get("execution_cutoff_sec", sniper.get("window_end_sec", 15.0))
    priority = sniper.get("late_fire_priority_band_sec", min(float(end), 5.0))
    sniper["arming_horizon_sec"] = float(start)
    sniper["execution_cutoff_sec"] = float(end)
    sniper["late_fire_priority_band_sec"] = float(priority)
    # Keep aliases in sync for backwards compatibility.
    sniper["window_start_sec"] = float(sniper["arming_horizon_sec"])
    sniper["window_end_sec"] = float(sniper["execution_cutoff_sec"])


def _normalize_doctrine_semantics(cfg: Dict[str, Any]) -> None:
    doctrine = cfg.setdefault("doctrine", {})
    mode = str(doctrine.get("mode", "canonical")).strip().lower()
    doctrine["mode"] = mode or "canonical"
    if "oracle_max_tick_age_sec" not in doctrine:
        doctrine["oracle_max_tick_age_sec"] = cfg.get("sniper", {}).get("max_chainlink_tick_age_sec", 1.5)
    doctrine["oracle_max_tick_age_sec"] = float(doctrine["oracle_max_tick_age_sec"])
    doctrine["maker_allow_bounded_single_side_reference"] = bool(
        doctrine.get("maker_allow_bounded_single_side_reference", True)
    )
    doctrine["min_observe_cycles_on_entry"] = int(doctrine.get("min_observe_cycles_on_entry", 2))
    doctrine["min_observe_seconds_on_entry"] = float(doctrine.get("min_observe_seconds_on_entry", 2.0))


def _raw_has_path(raw: Dict[str, Any], path: Tuple[str, ...]) -> bool:
    cursor: Any = raw
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return False
        cursor = cursor[key]
    return True


def _attach_config_metadata(cfg: Dict[str, Any], *, source_paths: List[pathlib.Path]) -> None:
    profile = cfg.setdefault("profile", {})
    if isinstance(profile, str):
        profile = {"name": profile}
        cfg["profile"] = profile
    elif not isinstance(profile, dict):
        profile = {"name": "default"}
        cfg["profile"] = profile
    if not str(profile.get("name", "")).strip():
        profile["name"] = source_paths[-1].stem if source_paths else "default"
    profile_class = str(profile.get("class", "canonical")).strip().lower()
    profile["class"] = profile_class or "canonical"

    cfg_for_hash = copy.deepcopy(cfg)
    runtime_for_hash = cfg_for_hash.get("runtime")
    if isinstance(runtime_for_hash, dict):
        # Setup-lock expected fingerprint is an assertion input, not execution behavior.
        # Excluding it from effective config hash avoids self-referential lock recursion.
        runtime_for_hash["paper_expected_config_fingerprint_sha256"] = ""
    cfg_json = json.dumps(cfg_for_hash, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    cfg["_meta"] = {
        "active_profile_name": str(profile.get("name", "")).strip(),
        "config_source_paths": [str(p) for p in source_paths],
        "effective_config_sha256": cfg_hash,
    }


def load_execution_config(path: pathlib.Path) -> Dict[str, Any]:
    raw, source_paths = _load_raw_with_extends(path)
    explicit_doctrine_oracle = _raw_has_path(raw, ("doctrine", "oracle_max_tick_age_sec"))
    explicit_legacy_sniper_oracle = _raw_has_path(raw, ("sniper", "max_chainlink_tick_age_sec"))
    cfg = deep_merge(DEFAULT_EXECUTION_CONFIG, raw)
    _normalize_sniper_semantics(cfg)
    _normalize_doctrine_semantics(cfg)
    _apply_asset_profile(cfg)
    # Hash before runtime path normalization so setup-lock fingerprint is stable
    # across host and docker execution environments.
    _attach_config_metadata(cfg, source_paths=source_paths)
    cfg = normalize_execution_paths(cfg, config_path=path.resolve())
    cfg_meta = cfg.setdefault("_meta", {})
    explicit_fields = cfg_meta.setdefault("explicit_fields", {})
    explicit_fields["doctrine.oracle_max_tick_age_sec"] = bool(explicit_doctrine_oracle)
    explicit_fields["sniper.max_chainlink_tick_age_sec"] = bool(explicit_legacy_sniper_oracle)
    validate_execution_config(cfg)
    return cfg


def _apply_asset_profile(cfg: Dict[str, Any]) -> None:
    asset_cfg = cfg.get("asset", {})
    symbol = str(asset_cfg.get("symbol", "BTC")).upper().strip() or "BTC"

    raw_chainlink_symbols = asset_cfg.get("chainlink_symbols")
    chainlink_symbols: list[str] = []
    if isinstance(raw_chainlink_symbols, list):
        chainlink_symbols = [str(x).lower().strip() for x in raw_chainlink_symbols if str(x).strip()]
    if not chainlink_symbols:
        chainlink_symbols = [f"{symbol.lower()}/usd"]

    raw_discovery_symbols = asset_cfg.get("discovery_symbols")
    discovery_symbols: list[str] = []
    if isinstance(raw_discovery_symbols, list):
        discovery_symbols = [str(x).upper().strip() for x in raw_discovery_symbols if str(x).strip()]
    if not discovery_symbols:
        discovery_symbols = [symbol]

    cfg.setdefault("asset", {})
    cfg["asset"]["symbol"] = symbol
    cfg["asset"]["chainlink_symbols"] = chainlink_symbols
    cfg["asset"]["discovery_symbols"] = discovery_symbols

    cfg.setdefault("chainlink", {})
    cfg["chainlink"]["symbols"] = chainlink_symbols
    cfg["chainlink"]["symbol_for_targets"] = chainlink_symbols[0]

    cfg.setdefault("targets", {}).setdefault("discovery", {})
    cfg["targets"]["discovery"]["symbols"] = discovery_symbols


def _require_positive(name: str, value: Any, allow_zero: bool = False) -> float:
    parsed = parse_float(value)
    if parsed is None:
        raise ValueError(f"{name} must be numeric, got {value!r}")
    if allow_zero:
        if parsed < 0:
            raise ValueError(f"{name} must be >= 0, got {parsed}")
        return parsed
    if parsed <= 0:
        raise ValueError(f"{name} must be > 0, got {parsed}")
    return parsed


def _require_fraction(name: str, value: Any, *, allow_zero: bool = False) -> float:
    parsed = parse_float(value)
    if parsed is None:
        raise ValueError(f"{name} must be numeric, got {value!r}")
    if allow_zero:
        if parsed < 0 or parsed > 1:
            raise ValueError(f"{name} must be in [0, 1], got {parsed}")
    elif parsed <= 0 or parsed > 1:
        raise ValueError(f"{name} must be in (0, 1], got {parsed}")
    return parsed


def validate_execution_config(cfg: Dict[str, Any]) -> None:
    bot_name = str(cfg.get("bot_name", "")).strip()
    if not bot_name:
        raise ValueError("bot_name must be a non-empty string")

    mode = str(cfg.get("mode", "")).lower().strip()
    if mode not in {"paper", "live"}:
        raise ValueError("mode must be one of: paper, live")

    asset_symbol = str(cfg.get("asset", {}).get("symbol", "")).upper().strip()
    if not asset_symbol:
        raise ValueError("asset.symbol must be a non-empty string")
    profile_cfg = cfg.get("profile", {})
    if not isinstance(profile_cfg, dict):
        raise ValueError("profile must be a mapping")
    profile_name = str(profile_cfg.get("name", "")).strip()
    if not profile_name:
        raise ValueError("profile.name must be a non-empty string")
    profile_class = str(profile_cfg.get("class", "canonical")).strip().lower()
    if profile_class not in {"canonical", "degraded"}:
        raise ValueError("profile.class must be one of: canonical, degraded")
    cfg["profile"]["class"] = profile_class

    _require_positive("runtime.poll_interval_sec", cfg["runtime"]["poll_interval_sec"])
    _require_positive("runtime.reconcile_interval_sec", cfg["runtime"]["reconcile_interval_sec"])
    _require_positive("runtime.status_interval_sec", cfg["runtime"]["status_interval_sec"])
    _require_positive("runtime.max_actions_per_cycle", cfg["runtime"]["max_actions_per_cycle"])
    _require_positive("runtime.rest_fetch_max_workers", cfg["runtime"]["rest_fetch_max_workers"])
    _require_positive("runtime.max_consecutive_failures", cfg["runtime"]["max_consecutive_failures"])
    _require_positive("runtime.seen_trade_ids_max", cfg["runtime"]["seen_trade_ids_max"])
    _require_positive("runtime.persist_seen_trade_ids_max", cfg["runtime"]["persist_seen_trade_ids_max"], allow_zero=True)
    _require_positive("runtime.max_quote_age_sec", cfg["runtime"]["max_quote_age_sec"])
    _require_fraction("runtime.order_rate_soft_limit_pct", cfg["runtime"]["order_rate_soft_limit_pct"])
    _require_fraction("runtime.cancel_rate_soft_limit_pct", cfg["runtime"]["cancel_rate_soft_limit_pct"])
    _require_positive("runtime.log_flush_every_records", cfg["runtime"]["log_flush_every_records"])
    _require_positive("runtime.log_flush_interval_sec", cfg["runtime"]["log_flush_interval_sec"])
    _require_fraction(
        "runtime.paper_passive_touch_fill_ratio",
        cfg["runtime"]["paper_passive_touch_fill_ratio"],
        allow_zero=True,
    )
    _require_positive("runtime.paper_passive_min_rest_sec", cfg["runtime"]["paper_passive_min_rest_sec"], allow_zero=True)
    _require_positive("runtime.maker_replace_min_rest_sec", cfg["runtime"]["maker_replace_min_rest_sec"], allow_zero=True)
    _require_positive("runtime.paper_passive_min_fill_size", cfg["runtime"]["paper_passive_min_fill_size"])
    _require_positive("runtime.paper_passive_near_touch_band", cfg["runtime"]["paper_passive_near_touch_band"], allow_zero=True)
    _require_fraction(
        "runtime.paper_passive_near_touch_fill_ratio",
        cfg["runtime"]["paper_passive_near_touch_fill_ratio"],
        allow_zero=True,
    )
    background_fill_ratio = parse_float(cfg["runtime"]["paper_background_fill_ratio"])
    if background_fill_ratio is None or background_fill_ratio < 0 or background_fill_ratio > 1:
        raise ValueError("runtime.paper_background_fill_ratio must be in [0, 1]")
    runtime_tod_start = int(float(cfg["runtime"]["paper_liquidity_tod_start_hour_utc"]))
    runtime_tod_end = int(float(cfg["runtime"]["paper_liquidity_tod_end_hour_utc"]))
    if runtime_tod_start < 0 or runtime_tod_start > 23:
        raise ValueError("runtime.paper_liquidity_tod_start_hour_utc must be in [0, 23]")
    if runtime_tod_end < 0 or runtime_tod_end > 23:
        raise ValueError("runtime.paper_liquidity_tod_end_hour_utc must be in [0, 23]")
    _require_positive(
        "runtime.paper_liquidity_tod_start_hour_utc",
        cfg["runtime"]["paper_liquidity_tod_start_hour_utc"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_liquidity_tod_end_hour_utc",
        cfg["runtime"]["paper_liquidity_tod_end_hour_utc"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_liquidity_tod_depth_multiplier",
        cfg["runtime"]["paper_liquidity_tod_depth_multiplier"],
        allow_zero=True,
    )
    _require_fraction(
        "runtime.paper_queue_position_ahead_ratio",
        cfg["runtime"]["paper_queue_position_ahead_ratio"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_chainlink_lag_window_low_sec",
        cfg["runtime"]["paper_chainlink_lag_window_low_sec"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_chainlink_lag_window_high_sec",
        cfg["runtime"]["paper_chainlink_lag_window_high_sec"],
        allow_zero=True,
    )
    if float(cfg["runtime"]["paper_chainlink_lag_window_high_sec"]) < float(
        cfg["runtime"]["paper_chainlink_lag_window_low_sec"]
    ):
        raise ValueError(
            "runtime.paper_chainlink_lag_window_high_sec must be >= runtime.paper_chainlink_lag_window_low_sec"
        )
    _require_positive(
        "runtime.paper_chainlink_lag_penalty_bps_below_window",
        cfg["runtime"]["paper_chainlink_lag_penalty_bps_below_window"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_chainlink_lag_penalty_bps_within_window",
        cfg["runtime"]["paper_chainlink_lag_penalty_bps_within_window"],
        allow_zero=True,
    )
    _require_positive(
        "runtime.paper_chainlink_lag_penalty_bps_above_window",
        cfg["runtime"]["paper_chainlink_lag_penalty_bps_above_window"],
        allow_zero=True,
    )
    _require_positive("sniper.arming_horizon_sec", cfg["sniper"]["arming_horizon_sec"])
    _require_positive("sniper.execution_cutoff_sec", cfg["sniper"]["execution_cutoff_sec"], allow_zero=True)
    _require_positive(
        "sniper.late_fire_priority_band_sec",
        cfg["sniper"]["late_fire_priority_band_sec"],
        allow_zero=True,
    )
    _require_positive("sniper.poll_interval_sec", cfg["sniper"]["poll_interval_sec"])
    _require_positive("sniper.max_actions_per_cycle", cfg["sniper"]["max_actions_per_cycle"])
    _require_positive("sniper.cancel_stale_action_budget", cfg["sniper"]["cancel_stale_action_budget"])
    _require_positive("sniper.cancel_orphan_action_budget", cfg["sniper"]["cancel_orphan_action_budget"])
    _require_fraction("sniper.order_rate_soft_limit_pct", cfg["sniper"]["order_rate_soft_limit_pct"])
    _require_fraction("sniper.cancel_rate_soft_limit_pct", cfg["sniper"]["cancel_rate_soft_limit_pct"])
    _require_positive("sniper.lag_window_samples", cfg["sniper"]["lag_window_samples"])
    _require_positive("sniper.lag_min_samples", cfg["sniper"]["lag_min_samples"])
    _require_positive("sniper.lag_hit_threshold_ms", cfg["sniper"]["lag_hit_threshold_ms"])
    _require_positive("sniper.lag_min_median_ms", cfg["sniper"]["lag_min_median_ms"])
    _require_fraction("sniper.lag_min_hit_rate", cfg["sniper"]["lag_min_hit_rate"])
    _require_positive("sniper.max_chainlink_tick_age_sec", cfg["sniper"]["max_chainlink_tick_age_sec"])
    _require_positive("doctrine.oracle_max_tick_age_sec", cfg["doctrine"]["oracle_max_tick_age_sec"])
    _require_positive(
        "doctrine.min_observe_cycles_on_entry",
        cfg["doctrine"]["min_observe_cycles_on_entry"],
        allow_zero=True,
    )
    _require_positive(
        "doctrine.min_observe_seconds_on_entry",
        cfg["doctrine"]["min_observe_seconds_on_entry"],
        allow_zero=True,
    )
    _require_positive("sniper.fair_vol_scale", cfg["sniper"]["fair_vol_scale"])
    _require_positive("sniper.taker.min_edge", cfg["sniper"]["taker"]["min_edge"])
    _require_positive("sniper.taker.extreme_edge_mult", cfg["sniper"]["taker"]["extreme_edge_mult"])
    _require_positive("sniper.taker.order_size", cfg["sniper"]["taker"]["order_size"])
    _require_positive("sniper.taker.target_usd", cfg["sniper"]["taker"]["target_usd"])
    _require_positive("sniper.taker.max_orders_per_cycle", cfg["sniper"]["taker"]["max_orders_per_cycle"])
    _require_positive("sniper.taker.per_token_cooldown_sec", cfg["sniper"]["taker"]["per_token_cooldown_sec"], allow_zero=True)
    _require_positive("market_data.timeout_sec", cfg["market_data"]["timeout_sec"])
    _require_positive("market_data.max_retries", cfg["market_data"]["max_retries"], allow_zero=True)
    md_ws = cfg["market_data"]["ws"]
    md_ws_heartbeat = _require_positive("market_data.ws.heartbeat_timeout_sec", md_ws["heartbeat_timeout_sec"])
    md_ws_ping = _require_positive("market_data.ws.ping_interval_sec", md_ws["ping_interval_sec"])
    md_ws_reconnect_initial = _require_positive(
        "market_data.ws.reconnect_backoff_initial_sec",
        md_ws["reconnect_backoff_initial_sec"],
    )
    md_ws_reconnect_max = _require_positive(
        "market_data.ws.reconnect_backoff_max_sec",
        md_ws["reconnect_backoff_max_sec"],
    )
    _require_positive("market_data.ws.stale_after_sec", md_ws["stale_after_sec"])
    heartbeat_timeout = _require_positive("chainlink.heartbeat_timeout_sec", cfg["chainlink"]["heartbeat_timeout_sec"])
    ping_interval = _require_positive("chainlink.ping_interval_sec", cfg["chainlink"]["ping_interval_sec"])
    reconnect_backoff_initial = _require_positive(
        "chainlink.reconnect_backoff_initial_sec",
        cfg["chainlink"]["reconnect_backoff_initial_sec"],
    )
    reconnect_backoff_max = _require_positive(
        "chainlink.reconnect_backoff_max_sec",
        cfg["chainlink"]["reconnect_backoff_max_sec"],
    )
    _require_positive("chainlink.mid_move_min_delta", cfg["chainlink"]["mid_move_min_delta"])
    _require_positive("chainlink.max_queue_size", cfg["chainlink"]["max_queue_size"])
    _require_positive("latency_verifier.window_samples", cfg["latency_verifier"]["window_samples"])
    _require_positive("latency_verifier.min_samples", cfg["latency_verifier"]["min_samples"])
    _require_positive("latency_verifier.hit_threshold_ms", cfg["latency_verifier"]["hit_threshold_ms"])
    _require_positive("latency_verifier.armed_min_median_ms", cfg["latency_verifier"]["armed_min_median_ms"])
    _require_fraction("latency_verifier.armed_min_hit_rate", cfg["latency_verifier"]["armed_min_hit_rate"])
    _require_positive(
        "latency_verifier.probation_min_median_ms",
        cfg["latency_verifier"]["probation_min_median_ms"],
    )
    _require_fraction(
        "latency_verifier.probation_min_hit_rate",
        cfg["latency_verifier"]["probation_min_hit_rate"],
    )
    _require_positive(
        "latency_verifier.arm_consecutive_cycles",
        cfg["latency_verifier"]["arm_consecutive_cycles"],
    )
    _require_positive(
        "latency_verifier.disarm_consecutive_cycles",
        cfg["latency_verifier"]["disarm_consecutive_cycles"],
    )
    _require_positive(
        "latency_verifier.max_sample_lag_ms",
        cfg["latency_verifier"]["max_sample_lag_ms"],
    )
    _require_fraction("latency_verifier.score_min_for_maker", cfg["latency_verifier"]["score_min_for_maker"])
    _require_fraction("latency_verifier.score_min_for_taker", cfg["latency_verifier"]["score_min_for_taker"])
    _require_positive("latency_verifier.score_size_floor", cfg["latency_verifier"]["score_size_floor"])
    _require_positive("latency_verifier.score_size_ceiling", cfg["latency_verifier"]["score_size_ceiling"])
    _require_positive("latency_verifier.drift_window_samples", cfg["latency_verifier"]["drift_window_samples"])
    _require_positive(
        "latency_verifier.drift_max_median_drop_ms",
        cfg["latency_verifier"]["drift_max_median_drop_ms"],
    )
    _require_fraction(
        "latency_verifier.drift_max_hit_rate_drop",
        cfg["latency_verifier"]["drift_max_hit_rate_drop"],
    )
    _require_positive("targets.discovery.refresh_interval_sec", cfg["targets"]["discovery"]["refresh_interval_sec"])
    _require_positive("targets.discovery.max_pairs", cfg["targets"]["discovery"]["max_pairs"])
    _require_positive("targets.discovery.page_limit", cfg["targets"]["discovery"]["page_limit"])
    _require_positive("targets.discovery.max_pages", cfg["targets"]["discovery"]["max_pages"])
    _require_positive("targets.discovery.max_markets_scan", cfg["targets"]["discovery"]["max_markets_scan"])
    _require_positive("targets.discovery.timeout_sec", cfg["targets"]["discovery"]["timeout_sec"])
    _require_positive("targets.discovery.max_retries", cfg["targets"]["discovery"]["max_retries"], allow_zero=True)
    _require_positive("strategy.base_order_size", cfg["strategy"]["base_order_size"])
    _require_positive("strategy.tick_size", cfg["strategy"]["tick_size"])
    _require_positive("strategy.fair_skew_factor", cfg["strategy"]["fair_skew_factor"])
    _require_positive("sizing.min_usd", cfg["sizing"]["min_usd"])
    _require_positive("sizing.max_usd", cfg["sizing"]["max_usd"])
    _require_positive("sizing.target_usd", cfg["sizing"]["target_usd"])
    _require_positive("sizing.share_step", cfg["sizing"]["share_step"])
    _require_positive(
        "sizing.maker_competitive_min_notional_usd",
        cfg["sizing"]["maker_competitive_min_notional_usd"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_competitive_max_notional_usd",
        cfg["sizing"]["maker_competitive_max_notional_usd"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_competitive_min_shares",
        cfg["sizing"]["maker_competitive_min_shares"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_competitive_max_shares",
        cfg["sizing"]["maker_competitive_max_shares"],
        allow_zero=True,
    )
    _require_fraction(
        "sizing.maker_depth_target_min_ratio",
        cfg["sizing"]["maker_depth_target_min_ratio"],
        allow_zero=True,
    )
    _require_fraction(
        "sizing.maker_depth_target_max_ratio",
        cfg["sizing"]["maker_depth_target_max_ratio"],
        allow_zero=True,
    )
    _require_fraction(
        "sizing.maker_depth_target_ratio",
        cfg["sizing"]["maker_depth_target_ratio"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_liquidity_tod_start_hour_utc",
        cfg["sizing"]["maker_liquidity_tod_start_hour_utc"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_liquidity_tod_end_hour_utc",
        cfg["sizing"]["maker_liquidity_tod_end_hour_utc"],
        allow_zero=True,
    )
    _require_positive(
        "sizing.maker_liquidity_tod_depth_multiplier",
        cfg["sizing"]["maker_liquidity_tod_depth_multiplier"],
        allow_zero=True,
    )
    _require_fraction(
        "strategy.execution_quality.min_expected_fill_prob",
        cfg["strategy"]["execution_quality"]["min_expected_fill_prob"],
    )
    _require_positive(
        "strategy.execution_quality.max_queue_ahead_size",
        cfg["strategy"]["execution_quality"]["max_queue_ahead_size"],
        allow_zero=True,
    )
    _require_positive(
        "strategy.execution_quality.queue_depth_scale",
        cfg["strategy"]["execution_quality"]["queue_depth_scale"],
    )
    _require_positive(
        "strategy.execution_quality.distance_scale",
        cfg["strategy"]["execution_quality"]["distance_scale"],
    )
    _require_positive(
        "strategy.execution_quality.adverse_selection_penalty",
        cfg["strategy"]["execution_quality"]["adverse_selection_penalty"],
        allow_zero=True,
    )
    _require_positive("risk.max_abs_position_shares", cfg["risk"]["max_abs_position_shares"])
    _require_positive("risk.max_notional_per_token", cfg["risk"]["max_notional_per_token"])
    _require_positive("risk.max_open_orders_per_token", cfg["risk"]["max_open_orders_per_token"])
    _require_positive("risk.max_total_open_orders", cfg["risk"]["max_total_open_orders"])
    _require_positive("risk.max_orders_per_min", cfg["risk"]["max_orders_per_min"])
    _require_positive("risk.max_cancels_per_min", cfg["risk"]["max_cancels_per_min"])
    _require_positive("risk.max_book_age_sec", cfg["risk"]["max_book_age_sec"])
    _require_positive("risk.max_book_future_skew_sec", cfg["risk"]["max_book_future_skew_sec"], allow_zero=True)
    _require_positive("alerts.timeout_sec", cfg["alerts"]["timeout_sec"])
    _require_positive("alerts.min_interval_sec", cfg["alerts"]["min_interval_sec"])
    _require_positive("metrics.port", cfg["metrics"]["port"])
    _require_positive("preflight.max_market_data_failures", cfg["preflight"]["max_market_data_failures"], allow_zero=True)
    _require_positive("preflight.max_clock_skew_sec", cfg["preflight"]["max_clock_skew_sec"], allow_zero=True)
    _require_positive("preflight.endpoint_timeout_sec", cfg["preflight"]["endpoint_timeout_sec"])
    _require_positive("operating_mode.window_cycles", cfg["operating_mode"]["window_cycles"])
    _require_fraction("operating_mode.caution_stale_reject_ratio", cfg["operating_mode"]["caution_stale_reject_ratio"])
    _require_fraction("operating_mode.maker_only_stale_reject_ratio", cfg["operating_mode"]["maker_only_stale_reject_ratio"])
    _require_fraction("operating_mode.caution_outage_ratio", cfg["operating_mode"]["caution_outage_ratio"])
    _require_fraction("operating_mode.maker_only_outage_ratio", cfg["operating_mode"]["maker_only_outage_ratio"])
    _require_fraction("operating_mode.caution_disarmed_ratio", cfg["operating_mode"]["caution_disarmed_ratio"])
    _require_fraction("operating_mode.maker_only_disarmed_ratio", cfg["operating_mode"]["maker_only_disarmed_ratio"])
    _require_fraction("operating_mode.caution_error_ratio", cfg["operating_mode"]["caution_error_ratio"])
    _require_fraction("operating_mode.maker_only_error_ratio", cfg["operating_mode"]["maker_only_error_ratio"])
    _require_positive("operating_mode.recover_healthy_cycles", cfg["operating_mode"]["recover_healthy_cycles"])
    _require_positive("operating_mode.safe_stop_severe_cycles", cfg["operating_mode"]["safe_stop_severe_cycles"])
    _require_positive("operating_mode.cautious_size_mult", cfg["operating_mode"]["cautious_size_mult"])
    _require_positive("operating_mode.maker_only_size_mult", cfg["operating_mode"]["maker_only_size_mult"])
    _require_positive("operating_mode.cautious_spread_mult", cfg["operating_mode"]["cautious_spread_mult"])
    _require_positive("operating_mode.maker_only_spread_mult", cfg["operating_mode"]["maker_only_spread_mult"])
    _require_positive(
        "operating_mode.ws_slo_max_book_last_msg_age_sec",
        cfg["operating_mode"]["ws_slo_max_book_last_msg_age_sec"],
    )
    _require_positive(
        "operating_mode.ws_slo_max_chainlink_last_tick_age_sec",
        cfg["operating_mode"]["ws_slo_max_chainlink_last_tick_age_sec"],
    )
    _require_positive(
        "operating_mode.ws_slo_bootstrap_grace_sec",
        cfg["operating_mode"].get("ws_slo_bootstrap_grace_sec", 0.0),
        allow_zero=True,
    )
    _require_positive("ramp.start_usd", cfg["ramp"]["start_usd"])
    _require_positive("ramp.step_usd", cfg["ramp"]["step_usd"])
    _require_positive("ramp.max_usd", cfg["ramp"]["max_usd"])
    _require_positive("ramp.evaluation_window_cycles", cfg["ramp"]["evaluation_window_cycles"])
    _require_fraction("ramp.downshift_reject_ratio", cfg["ramp"]["downshift_reject_ratio"])
    _require_fraction(
        "ramp.downshift_stale_oracle_ratio",
        cfg["ramp"].get("downshift_stale_oracle_ratio", cfg["ramp"].get("downshift_stale_ratio")),
    )
    _require_fraction("ramp.downshift_disarmed_ratio", cfg["ramp"]["downshift_disarmed_ratio"])
    _require_fraction(
        "ramp.downshift_reconcile_mismatch_ratio",
        cfg["ramp"]["downshift_reconcile_mismatch_ratio"],
    )
    _require_positive("simulation.maker_rebate_bps", cfg["simulation"]["maker_rebate_bps"], allow_zero=True)
    _require_positive("simulation.taker_fee_curve_rate", cfg["simulation"]["taker_fee_curve_rate"], allow_zero=True)
    _require_positive("simulation.taker_slippage_bps", cfg["simulation"]["taker_slippage_bps"], allow_zero=True)
    _require_positive("simulation.adverse_selection_bps", cfg["simulation"]["adverse_selection_bps"], allow_zero=True)
    _require_positive("strategy.volatility.window_sec", cfg["strategy"]["volatility"]["window_sec"])
    _require_positive("strategy.volatility.low_vol_threshold", cfg["strategy"]["volatility"]["low_vol_threshold"])
    _require_positive("strategy.volatility.high_vol_threshold", cfg["strategy"]["volatility"]["high_vol_threshold"])
    _require_positive("strategy.volatility.low_vol_spread_mult", cfg["strategy"]["volatility"]["low_vol_spread_mult"])
    _require_positive("strategy.volatility.high_vol_spread_mult", cfg["strategy"]["volatility"]["high_vol_spread_mult"])
    _require_positive("strategy.volatility.low_vol_size_mult", cfg["strategy"]["volatility"]["low_vol_size_mult"])
    _require_positive("strategy.volatility.high_vol_size_mult", cfg["strategy"]["volatility"]["high_vol_size_mult"])

    if not isinstance(cfg["targets"]["token_ids"], list):
        raise ValueError("targets.token_ids must be a list")
    if not isinstance(cfg["targets"]["token_expiry_utc_by_token"], dict):
        raise ValueError("targets.token_expiry_utc_by_token must be a mapping")
    if not isinstance(cfg["targets"]["token_side_by_token"], dict):
        raise ValueError("targets.token_side_by_token must be a mapping")
    if not isinstance(cfg["targets"]["token_strike_by_token"], dict):
        raise ValueError("targets.token_strike_by_token must be a mapping")
    for token_id_raw, expiry_raw in cfg["targets"]["token_expiry_utc_by_token"].items():
        token_id = str(token_id_raw).strip()
        if not token_id:
            raise ValueError("targets.token_expiry_utc_by_token has empty token id key")
        if parse_ts(expiry_raw) is None:
            raise ValueError(
                f"targets.token_expiry_utc_by_token[{token_id!r}] must be a valid timestamp, got {expiry_raw!r}"
            )
    for token_id_raw, side_raw in cfg["targets"]["token_side_by_token"].items():
        token_id = str(token_id_raw).strip()
        side = str(side_raw).strip().upper()
        if not token_id:
            raise ValueError("targets.token_side_by_token has empty token id key")
        if side not in {"YES", "NO"}:
            raise ValueError(f"targets.token_side_by_token[{token_id!r}] must be YES or NO, got {side_raw!r}")
    for token_id_raw, strike_raw in cfg["targets"]["token_strike_by_token"].items():
        token_id = str(token_id_raw).strip()
        if not token_id:
            raise ValueError("targets.token_strike_by_token has empty token id key")
        strike = parse_float(strike_raw)
        if strike is None or strike <= 0:
            raise ValueError(f"targets.token_strike_by_token[{token_id!r}] must be > 0, got {strike_raw!r}")
    token_ids = [str(x) for x in cfg["targets"]["token_ids"]]
    if len(token_ids) != len(set(token_ids)):
        raise ValueError("targets.token_ids contains duplicates")
    raw_allow_token_ids = cfg["targets"]["discovery"].get("allow_token_ids", [])
    if not isinstance(raw_allow_token_ids, list):
        raise ValueError("targets.discovery.allow_token_ids must be a list")
    allow_token_ids = [str(x).strip() for x in raw_allow_token_ids]
    if any(not token_id for token_id in allow_token_ids):
        raise ValueError("targets.discovery.allow_token_ids must not contain empty values")
    if len(allow_token_ids) != len(set(allow_token_ids)):
        raise ValueError("targets.discovery.allow_token_ids contains duplicates")
    cfg["targets"]["discovery"]["allow_token_ids"] = allow_token_ids
    if allow_token_ids and token_ids:
        allow_set = set(allow_token_ids)
        missing = [token_id for token_id in token_ids if token_id not in allow_set]
        if missing:
            raise ValueError("targets.token_ids must be subset of targets.discovery.allow_token_ids when provided")
    discovery_enabled = bool(cfg["targets"]["discovery"].get("enabled", False))
    if not cfg["targets"]["token_ids"] and not discovery_enabled:
        raise ValueError("targets.token_ids must be non-empty when targets.discovery.enabled is false")
    if cfg["sniper"]["execution_cutoff_sec"] > cfg["sniper"]["arming_horizon_sec"]:
        raise ValueError("sniper.execution_cutoff_sec must be <= sniper.arming_horizon_sec")
    if cfg["sniper"]["late_fire_priority_band_sec"] > cfg["sniper"]["execution_cutoff_sec"]:
        raise ValueError("sniper.late_fire_priority_band_sec must be <= sniper.execution_cutoff_sec")
    if cfg["latency_verifier"]["probation_min_median_ms"] > cfg["latency_verifier"]["armed_min_median_ms"]:
        raise ValueError("latency_verifier.probation_min_median_ms must be <= latency_verifier.armed_min_median_ms")
    if cfg["latency_verifier"]["probation_min_hit_rate"] > cfg["latency_verifier"]["armed_min_hit_rate"]:
        raise ValueError("latency_verifier.probation_min_hit_rate must be <= latency_verifier.armed_min_hit_rate")
    if cfg["latency_verifier"]["score_min_for_maker"] > cfg["latency_verifier"]["score_min_for_taker"]:
        raise ValueError("latency_verifier.score_min_for_maker must be <= latency_verifier.score_min_for_taker")
    if cfg["latency_verifier"]["score_size_ceiling"] < cfg["latency_verifier"]["score_size_floor"]:
        raise ValueError("latency_verifier.score_size_ceiling must be >= latency_verifier.score_size_floor")
    if cfg["operating_mode"]["maker_only_stale_reject_ratio"] < cfg["operating_mode"]["caution_stale_reject_ratio"]:
        raise ValueError("operating_mode.maker_only_stale_reject_ratio must be >= operating_mode.caution_stale_reject_ratio")
    if cfg["operating_mode"]["maker_only_outage_ratio"] < cfg["operating_mode"]["caution_outage_ratio"]:
        raise ValueError("operating_mode.maker_only_outage_ratio must be >= operating_mode.caution_outage_ratio")
    if cfg["operating_mode"]["maker_only_disarmed_ratio"] < cfg["operating_mode"]["caution_disarmed_ratio"]:
        raise ValueError("operating_mode.maker_only_disarmed_ratio must be >= operating_mode.caution_disarmed_ratio")
    if cfg["operating_mode"]["maker_only_error_ratio"] < cfg["operating_mode"]["caution_error_ratio"]:
        raise ValueError("operating_mode.maker_only_error_ratio must be >= operating_mode.caution_error_ratio")
    if reconnect_backoff_max < reconnect_backoff_initial:
        raise ValueError("chainlink.reconnect_backoff_max_sec must be >= chainlink.reconnect_backoff_initial_sec")
    if ping_interval > heartbeat_timeout:
        raise ValueError("chainlink.ping_interval_sec must be <= chainlink.heartbeat_timeout_sec")
    if md_ws_reconnect_max < md_ws_reconnect_initial:
        raise ValueError("market_data.ws.reconnect_backoff_max_sec must be >= market_data.ws.reconnect_backoff_initial_sec")
    if md_ws_ping > md_ws_heartbeat:
        raise ValueError("market_data.ws.ping_interval_sec must be <= market_data.ws.heartbeat_timeout_sec")
    if cfg["strategy"]["volatility"]["high_vol_threshold"] <= cfg["strategy"]["volatility"]["low_vol_threshold"]:
        raise ValueError("strategy.volatility.high_vol_threshold must be > strategy.volatility.low_vol_threshold")

    min_spread = _require_positive("strategy.min_spread", cfg["strategy"]["min_spread"])
    max_spread = _require_positive("strategy.max_spread", cfg["strategy"]["max_spread"])
    if max_spread < min_spread:
        raise ValueError("strategy.max_spread must be >= strategy.min_spread")

    min_size = _require_positive("strategy.min_order_size", cfg["strategy"]["min_order_size"])
    max_size = _require_positive("strategy.max_order_size", cfg["strategy"]["max_order_size"])
    if max_size < min_size:
        raise ValueError("strategy.max_order_size must be >= strategy.min_order_size")

    sizing_mode = str(cfg["sizing"].get("mode", "shares")).strip().lower()
    if sizing_mode not in {"shares", "notional"}:
        raise ValueError("sizing.mode must be one of: shares, notional")
    rounding_mode = str(cfg["sizing"].get("rounding", "floor")).strip().lower()
    if rounding_mode not in {"floor", "nearest"}:
        raise ValueError("sizing.rounding must be one of: floor, nearest")
    price_source = str(cfg["sizing"].get("price_source", "mid")).strip().lower()
    if price_source not in {"mid", "best_bid", "best_ask"}:
        raise ValueError("sizing.price_source must be one of: mid, best_bid, best_ask")
    exposure_cap_mode = str(cfg["sizing"].get("exposure_cap_mode", "per_market_total")).strip().lower()
    if exposure_cap_mode not in {"per_market_total", "per_side"}:
        raise ValueError("sizing.exposure_cap_mode must be one of: per_market_total, per_side")
    maker_depth_min = float(cfg["sizing"].get("maker_depth_target_min_ratio", 0.0) or 0.0)
    maker_depth_max = float(cfg["sizing"].get("maker_depth_target_max_ratio", 0.0) or 0.0)
    maker_depth_target = float(cfg["sizing"].get("maker_depth_target_ratio", 0.0) or 0.0)
    if maker_depth_max > 0.0 and maker_depth_min > maker_depth_max:
        raise ValueError("sizing.maker_depth_target_max_ratio must be >= sizing.maker_depth_target_min_ratio")
    if maker_depth_target > 0.0:
        if maker_depth_min > 0.0 and maker_depth_target < maker_depth_min:
            raise ValueError("sizing.maker_depth_target_ratio must be >= sizing.maker_depth_target_min_ratio")
        if maker_depth_max > 0.0 and maker_depth_target > maker_depth_max:
            raise ValueError("sizing.maker_depth_target_ratio must be <= sizing.maker_depth_target_max_ratio")
    maker_notional_min = float(cfg["sizing"].get("maker_competitive_min_notional_usd", 0.0) or 0.0)
    maker_notional_max = float(cfg["sizing"].get("maker_competitive_max_notional_usd", 0.0) or 0.0)
    if maker_notional_max > 0.0 and maker_notional_min > maker_notional_max:
        raise ValueError(
            "sizing.maker_competitive_max_notional_usd must be >= sizing.maker_competitive_min_notional_usd"
        )
    maker_shares_min = float(cfg["sizing"].get("maker_competitive_min_shares", 0.0) or 0.0)
    maker_shares_max = float(cfg["sizing"].get("maker_competitive_max_shares", 0.0) or 0.0)
    if maker_shares_max > 0.0 and maker_shares_min > maker_shares_max:
        raise ValueError("sizing.maker_competitive_max_shares must be >= sizing.maker_competitive_min_shares")
    sizing_tod_start = int(float(cfg["sizing"].get("maker_liquidity_tod_start_hour_utc", 0.0) or 0.0))
    sizing_tod_end = int(float(cfg["sizing"].get("maker_liquidity_tod_end_hour_utc", 0.0) or 0.0))
    if sizing_tod_start < 0 or sizing_tod_start > 23:
        raise ValueError("sizing.maker_liquidity_tod_start_hour_utc must be in [0, 23]")
    if sizing_tod_end < 0 or sizing_tod_end > 23:
        raise ValueError("sizing.maker_liquidity_tod_end_hour_utc must be in [0, 23]")
    min_usd = _require_positive("sizing.min_usd", cfg["sizing"]["min_usd"])
    target_usd = _require_positive("sizing.target_usd", cfg["sizing"]["target_usd"])
    max_usd = _require_positive("sizing.max_usd", cfg["sizing"]["max_usd"])
    if max_usd < min_usd:
        raise ValueError("sizing.max_usd must be >= sizing.min_usd")
    if not (min_usd <= target_usd <= max_usd):
        raise ValueError("sizing.target_usd must be between sizing.min_usd and sizing.max_usd")

    wallet_cfg = cfg.get("wallet")
    if not isinstance(wallet_cfg, dict):
        raise ValueError("wallet must be a mapping")
    _require_positive("wallet.paper_starting_usdc", wallet_cfg.get("paper_starting_usdc"), allow_zero=True)
    _require_positive("wallet.protected_usdc_reserve", wallet_cfg.get("protected_usdc_reserve"), allow_zero=True)
    _require_positive(
        "wallet.max_notional_per_order_usdc",
        wallet_cfg.get("max_notional_per_order_usdc"),
        allow_zero=True,
    )
    _require_positive("wallet.min_pol_gas_reserve", wallet_cfg.get("min_pol_gas_reserve"), allow_zero=True)
    _require_positive("wallet.paper_pol_balance", wallet_cfg.get("paper_pol_balance"), allow_zero=True)
    _require_positive("wallet.paper_allowance_usdc", wallet_cfg.get("paper_allowance_usdc"), allow_zero=True)
    _require_positive("wallet.reconcile_tolerance_usdc", wallet_cfg.get("reconcile_tolerance_usdc"))
    _require_positive("wallet.live_pol_balance_fallback", wallet_cfg.get("live_pol_balance_fallback"), allow_zero=True)
    _require_positive(
        "wallet.max_live_reconcile_mismatch_count",
        wallet_cfg.get("max_live_reconcile_mismatch_count"),
    )
    if not isinstance(wallet_cfg.get("require_allowance"), bool):
        raise ValueError("wallet.require_allowance must be boolean")
    if not isinstance(wallet_cfg.get("halt_on_reconcile_mismatch"), bool):
        raise ValueError("wallet.halt_on_reconcile_mismatch must be boolean")
    if not isinstance(wallet_cfg.get("require_live_nonce_snapshot"), bool):
        raise ValueError("wallet.require_live_nonce_snapshot must be boolean")
    if not isinstance(wallet_cfg.get("require_live_pending_tx_snapshot"), bool):
        raise ValueError("wallet.require_live_pending_tx_snapshot must be boolean")
    nonce_authority = str(wallet_cfg.get("nonce_authority", "")).strip().lower()
    if not nonce_authority:
        raise ValueError("wallet.nonce_authority must be a non-empty string")
    if nonce_authority != "tx_manager":
        raise ValueError("wallet.nonce_authority must be tx_manager")
    expected_chain_id = int(wallet_cfg.get("expected_chain_id", 0))
    if expected_chain_id <= 0:
        raise ValueError("wallet.expected_chain_id must be > 0")
    expected_wallet_address = str(wallet_cfg.get("expected_wallet_address", "")).strip()
    if expected_wallet_address and (not expected_wallet_address.startswith("0x") or len(expected_wallet_address) != 42):
        raise ValueError("wallet.expected_wallet_address must be empty or a 0x-prefixed 20-byte address")
    if mode == "live":
        if expected_chain_id != int(cfg["auth"]["chain_id"]):
            raise ValueError("wallet.expected_chain_id must match auth.chain_id in live mode")

    ramp_start = _require_positive("ramp.start_usd", cfg["ramp"]["start_usd"])
    ramp_max = _require_positive("ramp.max_usd", cfg["ramp"]["max_usd"])
    if ramp_max < ramp_start:
        raise ValueError("ramp.max_usd must be >= ramp.start_usd")

    _validate_optional_positive("risk.max_total_loss", cfg["risk"].get("max_total_loss"))
    _validate_optional_positive("risk.max_loss_per_token", cfg["risk"].get("max_loss_per_token"))

    if not isinstance(cfg["alerts"]["enabled"], bool):
        raise ValueError("alerts.enabled must be boolean")
    if not isinstance(cfg["alerts"]["telegram_enabled"], bool):
        raise ValueError("alerts.telegram_enabled must be boolean")
    if not isinstance(cfg["preflight"]["require_live_confirmation"], bool):
        raise ValueError("preflight.require_live_confirmation must be boolean")
    if not isinstance(cfg["preflight"]["check_market_data"], bool):
        raise ValueError("preflight.check_market_data must be boolean")
    if not isinstance(cfg["preflight"]["check_clock_sync"], bool):
        raise ValueError("preflight.check_clock_sync must be boolean")
    if not isinstance(cfg["preflight"]["check_endpoint_health"], bool):
        raise ValueError("preflight.check_endpoint_health must be boolean")
    if not isinstance(cfg["preflight"]["endpoint_urls"], list):
        raise ValueError("preflight.endpoint_urls must be a list")
    for idx, url in enumerate(cfg["preflight"]["endpoint_urls"]):
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"preflight.endpoint_urls[{idx}] must be a non-empty string")
    if not isinstance(cfg["asset"]["symbol"], str):
        raise ValueError("asset.symbol must be a string")
    if not isinstance(cfg["asset"]["chainlink_symbols"], list):
        raise ValueError("asset.chainlink_symbols must be a list")
    if not isinstance(cfg["asset"]["discovery_symbols"], list):
        raise ValueError("asset.discovery_symbols must be a list")
    if not cfg["asset"]["chainlink_symbols"]:
        raise ValueError("asset.chainlink_symbols must be non-empty")
    if not cfg["asset"]["discovery_symbols"]:
        raise ValueError("asset.discovery_symbols must be non-empty")
    for idx, symbol in enumerate(cfg["asset"]["chainlink_symbols"]):
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"asset.chainlink_symbols[{idx}] must be a non-empty string")
    for idx, symbol in enumerate(cfg["asset"]["discovery_symbols"]):
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"asset.discovery_symbols[{idx}] must be a non-empty string")
    if not isinstance(cfg["sizing"]["mode"], str):
        raise ValueError("sizing.mode must be a string")
    if not isinstance(cfg["sizing"]["rounding"], str):
        raise ValueError("sizing.rounding must be a string")
    if not isinstance(cfg["sizing"]["price_source"], str):
        raise ValueError("sizing.price_source must be a string")
    if not isinstance(cfg["sizing"]["exposure_cap_mode"], str):
        raise ValueError("sizing.exposure_cap_mode must be a string")
    if not isinstance(cfg["sizing"]["maker_liquidity_tod_scaler_enabled"], bool):
        raise ValueError("sizing.maker_liquidity_tod_scaler_enabled must be boolean")
    if not isinstance(cfg["security"]["enabled"], bool):
        raise ValueError("security.enabled must be boolean")
    if not isinstance(cfg["security"]["enforce_host_allowlist"], bool):
        raise ValueError("security.enforce_host_allowlist must be boolean")
    if not isinstance(cfg["security"]["require_tls"], bool):
        raise ValueError("security.require_tls must be boolean")
    if not isinstance(cfg["security"]["block_private_network_hosts"], bool):
        raise ValueError("security.block_private_network_hosts must be boolean")
    if not isinstance(cfg["security"]["enforce_storage_roots"], bool):
        raise ValueError("security.enforce_storage_roots must be boolean")
    if not isinstance(cfg["security"]["check_path_symlinks"], bool):
        raise ValueError("security.check_path_symlinks must be boolean")
    if not isinstance(cfg["security"]["check_file_permissions"], bool):
        raise ValueError("security.check_file_permissions must be boolean")
    if not isinstance(cfg["security"]["enforce_local_metrics_bind_in_live"], bool):
        raise ValueError("security.enforce_local_metrics_bind_in_live must be boolean")
    if not isinstance(cfg["security"]["allow_root_user_in_paper"], bool):
        raise ValueError("security.allow_root_user_in_paper must be boolean")
    if not isinstance(cfg["security"]["allow_root_user_in_live"], bool):
        raise ValueError("security.allow_root_user_in_live must be boolean")
    if not isinstance(cfg["security"]["require_live_security_ack"], bool):
        raise ValueError("security.require_live_security_ack must be boolean")
    if not isinstance(cfg["security"]["live_security_ack_env"], str) or not cfg["security"]["live_security_ack_env"].strip():
        raise ValueError("security.live_security_ack_env must be a non-empty string")
    if not isinstance(cfg["security"]["live_security_ack_value"], str) or not cfg["security"]["live_security_ack_value"].strip():
        raise ValueError("security.live_security_ack_value must be a non-empty string")
    if not isinstance(cfg["security"]["allowed_hosts"], list):
        raise ValueError("security.allowed_hosts must be a list")
    if not isinstance(cfg["security"]["allowed_storage_roots"], list):
        raise ValueError("security.allowed_storage_roots must be a list")
    for idx, host in enumerate(cfg["security"]["allowed_hosts"]):
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"security.allowed_hosts[{idx}] must be a non-empty string")
    for idx, root in enumerate(cfg["security"]["allowed_storage_roots"]):
        if not isinstance(root, str) or not root.strip():
            raise ValueError(f"security.allowed_storage_roots[{idx}] must be a non-empty string")
    if bool(cfg["security"]["enforce_host_allowlist"]) and not cfg["security"]["allowed_hosts"]:
        raise ValueError("security.allowed_hosts must be non-empty when security.enforce_host_allowlist is true")
    if bool(cfg["security"]["enforce_storage_roots"]) and not cfg["security"]["allowed_storage_roots"]:
        raise ValueError(
            "security.allowed_storage_roots must be non-empty when security.enforce_storage_roots is true"
        )
    if not isinstance(cfg["ramp"]["enabled"], bool):
        raise ValueError("ramp.enabled must be boolean")
    if not isinstance(cfg["ramp"]["disable_sniper_on_breach"], bool):
        raise ValueError("ramp.disable_sniper_on_breach must be boolean")
    if not isinstance(cfg["ramp"]["reconcile_status_path"], str):
        raise ValueError("ramp.reconcile_status_path must be a string")
    if not isinstance(cfg["alerts"]["guardian_hook_file"], str):
        raise ValueError("alerts.guardian_hook_file must be a string")
    if not isinstance(cfg["alerts"]["warn_thresholds"], dict):
        raise ValueError("alerts.warn_thresholds must be a mapping")
    if not isinstance(cfg["alerts"]["page_thresholds"], dict):
        raise ValueError("alerts.page_thresholds must be a mapping")
    if not isinstance(cfg["alerts"]["auto_stop_thresholds"], dict):
        raise ValueError("alerts.auto_stop_thresholds must be a mapping")
    _require_positive("alerts.auto_stop_min_samples", cfg["alerts"].get("auto_stop_min_samples"), allow_zero=False)
    _require_positive(
        "alerts.auto_stop_min_stale_rejects",
        cfg["alerts"].get("auto_stop_min_stale_rejects"),
        allow_zero=False,
    )
    _require_positive(
        "alerts.auto_stop_min_risk_rejects",
        cfg["alerts"].get("auto_stop_min_risk_rejects"),
        allow_zero=False,
    )
    if not isinstance(cfg["alerts"]["contacts"], dict):
        raise ValueError("alerts.contacts must be a mapping")
    for threshold_label in ("warn_thresholds", "page_thresholds", "auto_stop_thresholds"):
        threshold_cfg = cfg["alerts"][threshold_label]
        for key in ("stale_reject_ratio", "disarmed_ratio", "error_ratio", "reconcile_mismatch_ratio"):
            _require_fraction(f"alerts.{threshold_label}.{key}", threshold_cfg.get(key))
        _require_positive(
            f"alerts.{threshold_label}.mode_transitions_window",
            threshold_cfg.get("mode_transitions_window"),
            allow_zero=True,
        )
    if not isinstance(cfg["targets"]["discovery"]["enabled"], bool):
        raise ValueError("targets.discovery.enabled must be boolean")
    if not isinstance(cfg["targets"]["discovery"].get("allow_token_ids", []), list):
        raise ValueError("targets.discovery.allow_token_ids must be a list")
    if not isinstance(cfg["targets"]["discovery"]["require_binary_outcomes"], bool):
        raise ValueError("targets.discovery.require_binary_outcomes must be boolean")
    if not isinstance(cfg["targets"]["discovery"]["require_fee_enabled"], bool):
        raise ValueError("targets.discovery.require_fee_enabled must be boolean")
    if not isinstance(cfg["sniper"]["enabled"], bool):
        raise ValueError("sniper.enabled must be boolean")
    if not isinstance(cfg["sniper"]["allow_without_expiry_metadata"], bool):
        raise ValueError("sniper.allow_without_expiry_metadata must be boolean")
    if not isinstance(cfg["doctrine"]["mode"], str):
        raise ValueError("doctrine.mode must be a string")
    if not isinstance(cfg["sniper"]["require_lag_verification"], bool):
        raise ValueError("sniper.require_lag_verification must be boolean")
    if not isinstance(cfg["sniper"]["taker"]["enabled"], bool):
        raise ValueError("sniper.taker.enabled must be boolean")
    if not isinstance(cfg["latency_verifier"]["enabled"], bool):
        raise ValueError("latency_verifier.enabled must be boolean")
    if not isinstance(cfg["latency_verifier"]["require_armed_for_maker"], bool):
        raise ValueError("latency_verifier.require_armed_for_maker must be boolean")
    if not isinstance(cfg["latency_verifier"]["require_armed_for_sniper"], bool):
        raise ValueError("latency_verifier.require_armed_for_sniper must be boolean")
    if not isinstance(cfg["latency_verifier"]["log_sample_events"], bool):
        raise ValueError("latency_verifier.log_sample_events must be boolean")
    if not isinstance(cfg["latency_verifier"]["score_enabled"], bool):
        raise ValueError("latency_verifier.score_enabled must be boolean")
    if not isinstance(cfg["chainlink"]["enabled"], bool):
        raise ValueError("chainlink.enabled must be boolean")
    if not isinstance(cfg["chainlink"]["log_ticks"], bool):
        raise ValueError("chainlink.log_ticks must be boolean")
    if not isinstance(cfg["market_data"]["ws"]["enabled"], bool):
        raise ValueError("market_data.ws.enabled must be boolean")
    if not isinstance(cfg["strategy"]["volatility"]["enabled"], bool):
        raise ValueError("strategy.volatility.enabled must be boolean")
    if not isinstance(cfg["strategy"]["execution_quality"]["enabled"], bool):
        raise ValueError("strategy.execution_quality.enabled must be boolean")
    if not isinstance(cfg["metrics"]["enabled"], bool):
        raise ValueError("metrics.enabled must be boolean")
    if not isinstance(cfg["operating_mode"]["ws_slo_enforce_health"], bool):
        raise ValueError("operating_mode.ws_slo_enforce_health must be boolean")
    if not isinstance(cfg["operating_mode"]["ws_slo_require_book_connected"], bool):
        raise ValueError("operating_mode.ws_slo_require_book_connected must be boolean")
    if not isinstance(cfg["operating_mode"]["ws_slo_require_chainlink_connected"], bool):
        raise ValueError("operating_mode.ws_slo_require_chainlink_connected must be boolean")
    if not isinstance(cfg["runtime"]["log_book_top"], bool):
        raise ValueError("runtime.log_book_top must be boolean")
    if not isinstance(cfg["runtime"]["log_leadlag_book_move"], bool):
        raise ValueError("runtime.log_leadlag_book_move must be boolean")
    if not isinstance(cfg["runtime"]["log_async_flush"], bool):
        raise ValueError("runtime.log_async_flush must be boolean")
    doctrine_mode = str(cfg["doctrine"]["mode"]).strip().lower()
    if doctrine_mode not in {"canonical", "degraded"}:
        raise ValueError("doctrine.mode must be one of: canonical, degraded")
    if not isinstance(cfg["doctrine"]["maker_allow_bounded_single_side_reference"], bool):
        raise ValueError("doctrine.maker_allow_bounded_single_side_reference must be boolean")
    cfg_meta = cfg.get("_meta", {}) if isinstance(cfg.get("_meta"), dict) else {}
    explicit_fields = cfg_meta.get("explicit_fields", {}) if isinstance(cfg_meta.get("explicit_fields"), dict) else {}
    explicit_doctrine_oracle = bool(explicit_fields.get("doctrine.oracle_max_tick_age_sec", False))
    explicit_legacy_sniper_oracle = bool(explicit_fields.get("sniper.max_chainlink_tick_age_sec", False))
    if doctrine_mode == "canonical":
        if bool(cfg["sniper"]["allow_without_expiry_metadata"]):
            raise ValueError("sniper.allow_without_expiry_metadata must be false in canonical doctrine mode")
        if explicit_doctrine_oracle and explicit_legacy_sniper_oracle:
            raise ValueError(
                "canonical doctrine mode forbids setting both doctrine.oracle_max_tick_age_sec "
                "and legacy sniper.max_chainlink_tick_age_sec"
            )
        if (not explicit_doctrine_oracle) and explicit_legacy_sniper_oracle:
            raise ValueError(
                "canonical doctrine mode requires doctrine.oracle_max_tick_age_sec and forbids "
                "legacy-only sniper.max_chainlink_tick_age_sec overrides"
            )
    if not isinstance(cfg["runtime"]["log_fsync_on_flush"], bool):
        raise ValueError("runtime.log_fsync_on_flush must be boolean")
    if not isinstance(cfg["runtime"]["guard_stop_file"], str):
        raise ValueError("runtime.guard_stop_file must be a string")
    if not isinstance(cfg["runtime"]["clear_guard_stop_on_start"], bool):
        raise ValueError("runtime.clear_guard_stop_on_start must be boolean")
    if not isinstance(cfg["runtime"]["paper_passive_touch_fill_enabled"], bool):
        raise ValueError("runtime.paper_passive_touch_fill_enabled must be boolean")
    if not isinstance(cfg["runtime"]["paper_liquidity_tod_scaler_enabled"], bool):
        raise ValueError("runtime.paper_liquidity_tod_scaler_enabled must be boolean")
    if not isinstance(cfg["runtime"]["paper_chainlink_lag_emulation_enabled"], bool):
        raise ValueError("runtime.paper_chainlink_lag_emulation_enabled must be boolean")
    runtime_queue_mode = str(cfg["runtime"].get("paper_queue_position_mode", "not_modeled")).strip().lower()
    if runtime_queue_mode not in {"not_modeled", "bounded_top_depth_proxy"}:
        raise ValueError("runtime.paper_queue_position_mode must be one of: not_modeled, bounded_top_depth_proxy")
    cfg["runtime"]["paper_queue_position_mode"] = runtime_queue_mode
    if not isinstance(cfg["runtime"]["paper_enforce_setup_lock"], bool):
        raise ValueError("runtime.paper_enforce_setup_lock must be boolean")
    if not isinstance(cfg["runtime"]["paper_expected_profile_name"], str):
        raise ValueError("runtime.paper_expected_profile_name must be a string")
    if not isinstance(cfg["runtime"]["paper_expected_config_fingerprint_sha256"], str):
        raise ValueError("runtime.paper_expected_config_fingerprint_sha256 must be a string")
    cfg["runtime"]["paper_expected_profile_name"] = str(cfg["runtime"]["paper_expected_profile_name"]).strip()
    expected_fp = str(cfg["runtime"]["paper_expected_config_fingerprint_sha256"]).strip().lower()
    cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = expected_fp
    if expected_fp and (len(expected_fp) != 64 or any(ch not in "0123456789abcdef" for ch in expected_fp)):
        raise ValueError("runtime.paper_expected_config_fingerprint_sha256 must be 64 lowercase hex chars")
    if mode == "paper" and bool(cfg["runtime"]["paper_enforce_setup_lock"]):
        if not str(cfg["runtime"]["paper_expected_profile_name"]).strip():
            raise ValueError("runtime.paper_expected_profile_name is required when paper_enforce_setup_lock=true")
        if not expected_fp:
            raise ValueError(
                "runtime.paper_expected_config_fingerprint_sha256 is required when paper_enforce_setup_lock=true"
            )
        profile_name = str((cfg.get("profile", {}) or {}).get("name", "")).strip()
        expected_profile = str(cfg["runtime"]["paper_expected_profile_name"]).strip()
        if profile_name and expected_profile and profile_name != expected_profile:
            raise ValueError(
                "runtime.paper_expected_profile_name must match profile.name when paper_enforce_setup_lock=true"
            )
        observed_fp = str((cfg.get("_meta", {}) or {}).get("effective_config_sha256", "")).strip().lower()
        if observed_fp and expected_fp != observed_fp:
            raise ValueError(
                "runtime.paper_expected_config_fingerprint_sha256 does not match _meta.effective_config_sha256"
            )
    if not isinstance(cfg["operating_mode"]["enabled"], bool):
        raise ValueError("operating_mode.enabled must be boolean")
    if not isinstance(cfg["auth"]["enforce_post_only"], bool):
        raise ValueError("auth.enforce_post_only must be boolean")
    if not isinstance(cfg["auth"]["allow_taker"], bool):
        raise ValueError("auth.allow_taker must be boolean")
    _require_positive("auth.open_orders_cache_ttl_sec", cfg["auth"]["open_orders_cache_ttl_sec"], allow_zero=True)
    for key in ("private_key_source", "funder_source"):
        src = cfg["auth"].get(key)
        if src is None:
            continue
        if not isinstance(src, dict):
            raise ValueError(f"auth.{key} must be a mapping when provided")
        mode = str(src.get("mode", "env")).strip().lower()
        if mode not in {"env", "file", "manager"}:
            raise ValueError(f"auth.{key}.mode must be one of: env, file, manager")


def _validate_optional_positive(name: str, value: Any) -> Optional[float]:
    if value is None:
        return None
    parsed = parse_float(value)
    if parsed is None or parsed <= 0:
        raise ValueError(f"{name} must be > 0 when provided")
    return parsed
