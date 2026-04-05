#!/usr/bin/env python3
"""Generate nightly soak metrics from Bro execution logs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from collections import Counter
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from prodesk.artifact_identity import build_artifact_identity
from prodesk.jsonl_utils import load_jsonl
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.runtime_semantics import classify_runtime
from prodesk.session_phase import enforce_validation_phase

REPORT_SCHEMA_VERSION = 2
DEFAULT_MAX_LINES_PER_FILE = 200000


def parse_ts(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _load_run_manifest(log_dir: pathlib.Path, run_id: Optional[str]) -> Dict[str, Any]:
    target = str(run_id or "").strip()
    if not target:
        return {}
    manifest_path = log_dir / f"run_manifest_{target}.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dated_log_file_date(prefix: str, path: pathlib.Path) -> Optional[dt.date]:
    name = str(path.name or "")
    if not name.startswith(f"{prefix}_") or not name.endswith(".jsonl"):
        return None
    raw = name[len(prefix) + 1 : -6]
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def _select_run_scoped_files(*, log_dir: pathlib.Path, prefix: str, run_id: Optional[str]) -> List[pathlib.Path]:
    files = sorted(log_dir.glob(f"{prefix}_*.jsonl"))
    if not files:
        return []
    target = str(run_id or "").strip()
    if not target:
        return files

    manifest = _load_run_manifest(log_dir, target)
    manifest_key = f"{prefix}_path"
    raw_manifest_path = str(manifest.get(manifest_key) or "").strip()
    if not raw_manifest_path:
        return files
    manifest_name = pathlib.Path(raw_manifest_path).name
    if not manifest_name:
        return files

    matched_indices = [idx for idx, path in enumerate(files) if path.name == manifest_name]
    if not matched_indices:
        return files

    selected_indices = set(matched_indices)

    start_ts = parse_ts(manifest.get("start_ts"))
    end_ts = parse_ts(manifest.get("end_ts"))
    desired_dates: set[dt.date] = set()
    if start_ts is not None:
        desired_dates.add(start_ts.date())
    if end_ts is not None:
        desired_dates.add(end_ts.date())
    elif start_ts is not None:
        # Active run: include current UTC date in case it crossed midnight.
        desired_dates.add(dt.datetime.now(dt.timezone.utc).date())

    if desired_dates:
        date_to_idx: Dict[dt.date, int] = {}
        for idx, path in enumerate(files):
            row_date = _dated_log_file_date(prefix, path)
            if row_date is not None:
                date_to_idx[row_date] = idx
        for wanted in desired_dates:
            idx = date_to_idx.get(wanted)
            if idx is not None:
                selected_indices.add(idx)

    return [files[idx] for idx in sorted(selected_indices)]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:
        return default
    return out


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return None


def _is_quote_active_row(row: Dict[str, Any]) -> bool:
    open_orders = row.get("gauge.open_orders")
    if isinstance(open_orders, (int, float)) and float(open_orders) > 0:
        return True
    quote_active = row.get("gauge.quote_active")
    if isinstance(quote_active, (int, float)) and float(quote_active) > 0:
        return True
    # Rolling 60s usage gauges are authoritative status-row activity signals and
    # prevent undercounting when cumulative counters are emitted sparsely.
    orders_used_60s = row.get("gauge.orders_used_60s")
    if isinstance(orders_used_60s, (int, float)) and float(orders_used_60s) > 0:
        return True
    cancels_used_60s = row.get("gauge.cancels_used_60s")
    if isinstance(cancels_used_60s, (int, float)) and float(cancels_used_60s) > 0:
        return True
    actions_last_cycle = row.get("gauge.actions_last_cycle")
    if isinstance(actions_last_cycle, (int, float)) and float(actions_last_cycle) > 0:
        return True
    taker_actions_last_cycle = row.get("gauge.taker_actions_last_cycle")
    if isinstance(taker_actions_last_cycle, (int, float)) and float(taker_actions_last_cycle) > 0:
        return True
    taker_submitted_last_cycle = row.get("gauge.taker_submitted_last_cycle")
    if isinstance(taker_submitted_last_cycle, (int, float)) and float(taker_submitted_last_cycle) > 0:
        return True
    taker_fills_last_cycle = row.get("gauge.taker_fills_last_cycle")
    if isinstance(taker_fills_last_cycle, (int, float)) and float(taker_fills_last_cycle) > 0:
        return True
    return False


def _is_no_target_standdown_row(row: Dict[str, Any]) -> bool:
    state = str(row.get("runtime_state") or "").strip().lower()
    if state == "no_target_standdown":
        return True
    for key in ("no_target_standdown", "gauge.no_target_standdown"):
        value = row.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and float(value) > 0:
            return True
    return False


def _is_quote_window_row(row: Dict[str, Any]) -> bool:
    if _is_no_target_standdown_row(row):
        return False
    for key in (
        "active_targets_present",
        "gauge.active_targets_present",
        "gauge.target_discovery_active_targets",
        "target_count",
        "gauge.target_count",
    ):
        value = row.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and float(value) > 0:
            return True
    return False


def _status_activity_diagnostics(status_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    total_rows = float(len(status_rows))
    if total_rows <= 0:
        return {
            "status_rows_total": 0.0,
            "quote_window_rows": 0.0,
            "quote_window_ratio": 0.0,
            "quote_active_rows": 0.0,
            "quote_active_ratio": 0.0,
            "quote_active_within_window_ratio": 0.0,
            "participation_rows": 0.0,
            "participation_ratio": 0.0,
            "participation_within_window_ratio": 0.0,
        }

    quote_window_rows = 0.0
    quote_active_rows = 0.0
    participation_rows = 0.0
    prev_counters: Dict[str, float] = {}

    for row in status_rows:
        in_window = _is_quote_window_row(row)
        if in_window:
            quote_window_rows += 1.0

        is_active = _is_quote_active_row(row)
        if is_active:
            quote_active_rows += 1.0

        participated = False
        for key in (
            "gauge.actions_last_cycle",
            "gauge.taker_actions_last_cycle",
            "gauge.taker_submitted_last_cycle",
            "gauge.taker_fills_last_cycle",
            "gauge.order_submission_attempts_last_cycle",
            "order_submission_attempts_last_cycle",
        ):
            value = row.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                participated = True
                break

        for key in (
            "counter.orders_submitted",
            "counter.orders_canceled",
            "counter.taker_orders_submitted",
            "counter.taker_orders_filled",
            "counter.risk_rejects",
        ):
            value = row.get(key)
            if not isinstance(value, (int, float)):
                continue
            current = float(value)
            prior = prev_counters.get(key)
            if prior is not None and current > prior:
                participated = True
            prev_counters[key] = current

        if participated:
            participation_rows += 1.0

    return {
        "status_rows_total": total_rows,
        "quote_window_rows": quote_window_rows,
        "quote_window_ratio": (quote_window_rows / total_rows) if total_rows > 0 else 0.0,
        "quote_active_rows": quote_active_rows,
        "quote_active_ratio": (quote_active_rows / total_rows) if total_rows > 0 else 0.0,
        "quote_active_within_window_ratio": (quote_active_rows / quote_window_rows) if quote_window_rows > 0 else 0.0,
        "participation_rows": participation_rows,
        "participation_ratio": (participation_rows / total_rows) if total_rows > 0 else 0.0,
        "participation_within_window_ratio": (participation_rows / quote_window_rows) if quote_window_rows > 0 else 0.0,
    }


def _quote_uptime_ratio(status_rows: List[Dict[str, Any]]) -> float:
    if not status_rows:
        return 0.0
    active_rows = 0
    prev_submitted: Optional[float] = None
    prev_canceled: Optional[float] = None
    prev_risk_rejects: Optional[float] = None
    timeline: List[Tuple[Optional[dt.datetime], bool]] = []

    for row in status_rows:
        active = _is_quote_active_row(row)
        submitted = _safe_float(row.get("counter.orders_submitted"), default=float("nan"))
        canceled = _safe_float(row.get("counter.orders_canceled"), default=float("nan"))
        risk_rejects = _safe_float(row.get("counter.risk_rejects"), default=float("nan"))
        if submitted != submitted:
            submitted = None
        if canceled != canceled:
            canceled = None
        if risk_rejects != risk_rejects:
            risk_rejects = None
        if submitted is not None and prev_submitted is not None and float(submitted) > float(prev_submitted):
            active = True
        if canceled is not None and prev_canceled is not None and float(canceled) > float(prev_canceled):
            active = True
        if risk_rejects is not None and prev_risk_rejects is not None and float(risk_rejects) > float(prev_risk_rejects):
            active = True
        if active:
            active_rows += 1
        timeline.append((parse_ts(row.get("ts_utc")), active))
        if submitted is not None:
            prev_submitted = float(submitted)
        if canceled is not None:
            prev_canceled = float(canceled)
        if risk_rejects is not None:
            prev_risk_rejects = float(risk_rejects)

    # Prefer time-weighted occupancy when status timestamps are available.
    if len(timeline) >= 2:
        deltas: List[float] = []
        weighted_total = 0.0
        weighted_active = 0.0
        for idx in range(len(timeline) - 1):
            ts_cur, active_cur = timeline[idx]
            ts_next, _ = timeline[idx + 1]
            if ts_cur is None or ts_next is None:
                continue
            dt_sec = (ts_next - ts_cur).total_seconds()
            if dt_sec <= 0:
                continue
            deltas.append(dt_sec)
            weighted_total += dt_sec
            if active_cur:
                weighted_active += dt_sec
        if weighted_total > 0 and deltas:
            tail_dt = float(median(deltas))
            tail_active = bool(timeline[-1][1])
            weighted_total += tail_dt
            if tail_active:
                weighted_active += tail_dt
            return weighted_active / weighted_total

    return float(active_rows) / float(len(status_rows))


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    clipped = max(0.0, min(1.0, float(q)))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * clipped))
    return float(ordered[idx])


def _counter_delta(current_value: Any, prev_value: Optional[float]) -> Tuple[float, Optional[float]]:
    current = _safe_float(current_value, default=float("nan"))
    if current != current:  # NaN
        return 0.0, prev_value
    current = float(current)
    if prev_value is None:
        return 0.0, current
    if current >= prev_value:
        return max(0.0, current - prev_value), current
    # Counter reset/restart: treat current value as post-reset accumulation.
    return max(0.0, current), current


def _market_data_source_stats(status_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    ws_delta = 0.0
    rest_delta = 0.0
    total_delta = 0.0
    ws_rows = 0.0
    rest_rows = 0.0

    prev_ws: Optional[float] = None
    prev_rest: Optional[float] = None
    prev_total: Optional[float] = None
    for row in status_rows:
        ws_inc, prev_ws = _counter_delta(row.get("counter.book_updates_ws"), prev_ws)
        rest_inc, prev_rest = _counter_delta(row.get("counter.book_updates_rest"), prev_rest)
        total_inc, prev_total = _counter_delta(row.get("counter.book_updates"), prev_total)
        ws_delta += ws_inc
        rest_delta += rest_inc
        total_delta += total_inc
        if ws_inc > 0.0:
            ws_rows += 1.0
        if rest_inc > 0.0:
            rest_rows += 1.0

    source_total = ws_delta + rest_delta
    source_rest_ratio = (rest_delta / source_total) if source_total > 0.0 else 0.0
    return {
        "book_updates_ws_delta": float(ws_delta),
        "book_updates_rest_delta": float(rest_delta),
        "book_updates_total_delta": float(total_delta),
        "book_updates_rest_ratio": float(source_rest_ratio),
        "status_rows_with_ws_updates": float(ws_rows),
        "status_rows_with_rest_updates": float(rest_rows),
    }


def _harness_realism_grade(
    *,
    events: List[Dict[str, Any]],
    edge_truth: Dict[str, Any],
) -> Tuple[int, Dict[str, int]]:
    breakdown: Dict[str, int] = {
        "tod_liquidity_scaling": 0,
        "maker_queue_proxy_depth_model": 0,
        "taker_depth_slippage_model": 0,
        "taker_lag_emulation_with_unknown_guard": 0,
        "truth_surface_completeness": 0,
    }

    fill_rows = [evt for evt in events if str(evt.get("event_type") or "") == "fill"]
    if any(
        isinstance(evt.get("paper_liquidity_depth_multiplier"), (int, float))
        and float(evt.get("paper_liquidity_depth_multiplier")) != 1.0
        for evt in fill_rows
    ):
        breakdown["tod_liquidity_scaling"] = 20

    if any(
        str(evt.get("paper_queue_position_mode") or "").strip().lower() == "bounded_top_depth_proxy"
        and isinstance(evt.get("paper_maker_depth_consumption_ratio"), (int, float))
        for evt in fill_rows
    ):
        breakdown["maker_queue_proxy_depth_model"] = 20

    if any(
        str(evt.get("fill_policy_basis") or "").strip().lower()
        in {"bounded_visible_liquidity_top_of_book", "bounded_visible_liquidity_top_of_book_with_queue_proxy"}
        for evt in fill_rows
    ):
        breakdown["taker_depth_slippage_model"] = 20

    lag_rows = [
        evt for evt in fill_rows if str(evt.get("paper_chainlink_lag_class") or "").strip().lower()
    ]
    if lag_rows:
        unknown_fail_closed_ok = True
        for row in lag_rows:
            lag_class = str(row.get("paper_chainlink_lag_class") or "").strip().lower()
            penalty = _safe_float(row.get("paper_chainlink_lag_penalty_bps"), default=0.0)
            if lag_class == "unknown" and abs(float(penalty)) > 1e-9:
                unknown_fail_closed_ok = False
                break
        if unknown_fail_closed_ok:
            breakdown["taker_lag_emulation_with_unknown_guard"] = 20

    if float(_safe_float(edge_truth.get("rows_total"), 0.0)) > 0.0:
        breakdown["truth_surface_completeness"] = 20

    grade = int(sum(int(v) for v in breakdown.values()))
    return grade, breakdown


def _fill_capture_stats(events: List[Dict[str, Any]]) -> Dict[str, float]:
    latest_mid_by_token: Dict[str, float] = {}
    capture = 0.0
    adverse = 0.0
    fills = 0
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "")
            mid = evt.get("midpoint")
            if token_id and isinstance(mid, (int, float)):
                latest_mid_by_token[token_id] = float(mid)
            continue
        if event_type != "fill":
            continue
        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        mid = latest_mid_by_token.get(token_id)
        price = evt.get("price")
        size = evt.get("size")
        if mid is None or not isinstance(price, (int, float)) or not isinstance(size, (int, float)):
            continue
        qty = float(size)
        if side == "BUY":
            delta = (float(mid) - float(price)) * qty
        elif side == "SELL":
            delta = (float(price) - float(mid)) * qty
        else:
            continue
        if delta >= 0:
            capture += delta
        else:
            adverse += abs(delta)
        fills += 1
    return {
        "fills_scored": float(fills),
        "realized_capture": capture,
        "adverse_selection": adverse,
        "capture_minus_adverse": capture - adverse,
    }


def _taker_stage_net_breakout(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    latest_mid_by_token: Dict[str, float] = {}
    taker_stage_by_order_id: Dict[str, str] = {}
    stage_capture: Counter[str] = Counter()
    stage_adverse: Counter[str] = Counter()
    stage_fills: Counter[str] = Counter()

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "").strip()
            midpoint = evt.get("midpoint")
            if token_id and isinstance(midpoint, (int, float)):
                latest_mid_by_token[token_id] = float(midpoint)
            continue
        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            if "sniper_taker" not in reason:
                continue
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            stage = str(evt.get("stage") or "UNKNOWN").strip().upper() or "UNKNOWN"
            taker_stage_by_order_id[order_id] = stage
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if not order_id:
            continue
        stage = taker_stage_by_order_id.get(order_id)
        if not stage:
            continue
        token_id = str(evt.get("token_id") or "").strip()
        side = str(evt.get("side") or "").strip().upper()
        mid = latest_mid_by_token.get(token_id)
        price = evt.get("price")
        size = evt.get("size")
        if mid is None or not isinstance(price, (int, float)) or not isinstance(size, (int, float)):
            continue
        qty = float(size)
        if side == "BUY":
            delta = (float(mid) - float(price)) * qty
        elif side == "SELL":
            delta = (float(price) - float(mid)) * qty
        else:
            continue
        if delta >= 0.0:
            stage_capture[stage] += float(delta)
        else:
            stage_adverse[stage] += abs(float(delta))
        stage_fills[stage] += 1

    out: Dict[str, Dict[str, float]] = {}
    for stage in sorted(set(stage_fills.keys()) | set(stage_capture.keys()) | set(stage_adverse.keys())):
        capture = float(stage_capture.get(stage, 0.0))
        adverse = float(stage_adverse.get(stage, 0.0))
        out[stage] = {
            "fills_scored": float(stage_fills.get(stage, 0)),
            "capture": capture,
            "adverse_selection": adverse,
            "net": capture - adverse,
        }
    return out


def _edge_quality_by_regime(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    order_reason_by_id: Dict[str, str] = {}
    regime = "disarmed"
    taker_submits = Counter()
    taker_fills = Counter()

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "latency_regime_change":
            regime = str(evt.get("state") or regime)
        elif event_type == "sniper_taker_submit":
            taker_submits[regime] += 1
        elif event_type == "order_submit":
            order_id = str(evt.get("order_id") or "")
            reason = str(evt.get("reason") or "")
            if order_id:
                order_reason_by_id[order_id] = reason
        elif event_type == "fill":
            order_id = str(evt.get("order_id") or "")
            reason = order_reason_by_id.get(order_id, "")
            if "sniper_taker" in reason:
                taker_fills[regime] += 1

    out: Dict[str, Dict[str, float]] = {}
    regimes = set(taker_submits.keys()) | set(taker_fills.keys())
    for name in sorted(regimes):
        submits = float(taker_submits.get(name, 0))
        fills = float(taker_fills.get(name, 0))
        fill_rate = (fills / submits) if submits > 0 else 0.0
        out[name] = {"taker_submits": submits, "taker_fills": fills, "taker_fill_rate": fill_rate}
    return out


def _filter_rows_by_run_id(rows: List[Dict[str, Any]], run_id: Optional[str]) -> List[Dict[str, Any]]:
    if not run_id:
        return rows
    target = str(run_id).strip()
    if not target:
        return rows
    out: List[Dict[str, Any]] = []
    for row in rows:
        value = row.get("run_id")
        if value is None:
            continue
        if str(value) == target:
            out.append(row)
    return out


def _load_contract_scoped_files(
    *,
    contract: Optional[Dict[str, Any]],
    prefix: str,
) -> List[pathlib.Path]:
    if contract is None:
        return []
    stream = {"status": "status", "events": "events", "errors": "errors"}.get(prefix, prefix)
    slice_path = run_contract_slice_path(contract, stream=stream)
    if slice_path is not None:
        return [slice_path]
    key = f"{prefix}_path"
    raw = str(contract.get(key) or "").strip()
    if not raw:
        return []
    path = pathlib.Path(raw).expanduser().resolve()
    if path.exists():
        return [path]
    return []


def _run_duration_minutes(events: List[Dict[str, Any]], status: List[Dict[str, Any]], errors: List[Dict[str, Any]]) -> float:
    ts_values: List[dt.datetime] = []
    for row in (events + status + errors):
        ts = parse_ts(row.get("ts_utc"))
        if ts is not None:
            ts_values.append(ts)
    if len(ts_values) < 2:
        return 0.0
    span = max(ts_values) - min(ts_values)
    return max(0.0, span.total_seconds() / 60.0)


def _stale_data_stats(events: List[Dict[str, Any]]) -> Dict[str, float]:
    stale_book_rejects = 0.0
    stale_oracle_blocks = 0.0
    disarmed_blocks = 0.0
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "risk_reject":
            reason = str(evt.get("reason") or "")
            if "stale_book" in reason:
                stale_book_rejects += 1.0
        elif event_type == "edge_evaluation":
            action = str(evt.get("action_taken") or "").strip().lower()
            if action != "none":
                continue
            reason = str(evt.get("block_reason") or "").strip().lower()
            if "stale" in reason:
                stale_oracle_blocks += 1.0
            if "latency_not_armed" in reason or "token_lag_not_verified" in reason:
                disarmed_blocks += 1.0
    return {
        "stale_book_rejects": stale_book_rejects,
        "stale_oracle_edge_blocks": stale_oracle_blocks,
        "disarmed_edge_blocks": disarmed_blocks,
    }


def _latency_distribution(events: List[Dict[str, Any]]) -> Dict[str, float]:
    lag_ms: List[float] = []
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type not in {"latency_sample", "leadlag_book_move"}:
            continue
        lag = _safe_float(evt.get("reaction_lag_ms", evt.get("lag_ms")), default=-1.0)
        if lag >= 0:
            lag_ms.append(lag)
    if not lag_ms:
        return {
            "sample_count": 0.0,
            "median_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "sample_count": float(len(lag_ms)),
        "median_ms": _percentile(lag_ms, 0.5),
        "p90_ms": _percentile(lag_ms, 0.9),
        "p95_ms": _percentile(lag_ms, 0.95),
        "max_ms": max(lag_ms),
    }


def _sniper_stats(events: List[Dict[str, Any]], duration_minutes: float) -> Dict[str, float]:
    sniper_order_ids: set[str] = set()
    latest_mid_by_token: Dict[str, float] = {}
    submits = 0.0
    fills = 0.0
    wins = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "")
            midpoint = evt.get("midpoint")
            if token_id and isinstance(midpoint, (int, float)):
                latest_mid_by_token[token_id] = float(midpoint)
            continue
        if event_type == "order_submit" and "sniper_taker" in str(evt.get("reason") or ""):
            submits += 1.0
            order_id = str(evt.get("order_id") or "")
            if order_id:
                sniper_order_ids.add(order_id)
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "")
        if order_id not in sniper_order_ids:
            continue
        fills += 1.0
        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        fill_price = _safe_float(evt.get("price"), default=-1.0)
        mid = latest_mid_by_token.get(token_id)
        if fill_price <= 0 or mid is None:
            continue
        if side == "BUY" and mid >= fill_price:
            wins += 1.0
        elif side == "SELL" and mid <= fill_price:
            wins += 1.0

    return {
        "submits": submits,
        "fills": fills,
        "fill_rate": (fills / submits) if submits > 0 else 0.0,
        # Midpoint-relative proxy only; not realized pnl/win attribution truth.
        "midpoint_win_rate_proxy": (wins / fills) if fills > 0 else 0.0,
        "fire_rate_per_min": (submits / duration_minutes) if duration_minutes > 0 else 0.0,
    }


def _execution_path_stats(events: List[Dict[str, Any]], duration_minutes: float) -> Dict[str, float]:
    maker_order_ids: set[str] = set()
    taker_bonus_order_ids: set[str] = set()
    maker_filled_order_ids: set[str] = set()
    taker_bonus_filled_order_ids: set[str] = set()
    maker_submits = 0.0
    taker_bonus_submits = 0.0
    maker_fills = 0.0
    taker_bonus_fills = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            order_id = str(evt.get("order_id") or "")
            if "sniper_taker" in reason or "taker_bonus" in reason:
                taker_bonus_submits += 1.0
                if order_id:
                    taker_bonus_order_ids.add(order_id)
            else:
                maker_submits += 1.0
                if order_id:
                    maker_order_ids.add(order_id)
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "")
        if order_id in taker_bonus_order_ids:
            taker_bonus_fills += 1.0
            if order_id:
                taker_bonus_filled_order_ids.add(order_id)
        elif order_id in maker_order_ids:
            maker_fills += 1.0
            if order_id:
                maker_filled_order_ids.add(order_id)

    return {
        "maker_submits": maker_submits,
        "maker_fills": maker_fills,
        "maker_filled_orders": float(len(maker_filled_order_ids)),
        "maker_fill_rate": (float(len(maker_filled_order_ids)) / maker_submits) if maker_submits > 0 else 0.0,
        "maker_fire_rate_per_min": (maker_submits / duration_minutes) if duration_minutes > 0 else 0.0,
        "taker_bonus_submits": taker_bonus_submits,
        "taker_bonus_fills": taker_bonus_fills,
        "taker_bonus_filled_orders": float(len(taker_bonus_filled_order_ids)),
        "taker_bonus_fill_rate": (
            float(len(taker_bonus_filled_order_ids)) / taker_bonus_submits
        )
        if taker_bonus_submits > 0
        else 0.0,
        "taker_bonus_fire_rate_per_min": (taker_bonus_submits / duration_minutes) if duration_minutes > 0 else 0.0,
    }


def _maker_regression_sentinel(
    execution_paths: Dict[str, Any],
    edge_truth: Dict[str, Any],
    duration_minutes: float,
) -> Dict[str, Any]:
    maker_submits = _safe_float(execution_paths.get("maker_submits"))
    maker_fills = _safe_float(execution_paths.get("maker_fills"))
    maker_fill_rate = _safe_float(execution_paths.get("maker_fill_rate"))
    maker_fire_rate_per_min = _safe_float(execution_paths.get("maker_fire_rate_per_min"))

    near_zero_submit_rate_threshold_per_min = 0.25
    near_zero_fill_count_threshold = 1.0
    low_fill_rate_threshold = 0.05
    low_fill_rate_requires_submits = 5.0

    submit_count_threshold = (
        max(1.0, near_zero_submit_rate_threshold_per_min * duration_minutes)
        if duration_minutes > 0
        else 1.0
    )
    near_zero_submits = maker_submits <= submit_count_threshold
    near_zero_fills = maker_fills <= near_zero_fill_count_threshold
    fill_rate_collapse = (
        maker_submits >= low_fill_rate_requires_submits and maker_fill_rate < low_fill_rate_threshold
    )
    triggered = (near_zero_submits and near_zero_fills) or fill_rate_collapse

    regression_reasons: List[str] = []
    if near_zero_submits and near_zero_fills:
        regression_reasons.append("near_zero_maker_submit_fill_pattern")
    if fill_rate_collapse:
        regression_reasons.append("maker_fill_rate_collapse")

    maker_no_submission_categories = edge_truth.get("maker_no_submission_category_distribution")
    if not isinstance(maker_no_submission_categories, dict):
        maker_no_submission_categories = {}

    watch_categories = {
        "quote_quality_skip_fill_probability": int(
            _safe_float(maker_no_submission_categories.get("quote_quality_skip_fill_probability"))
        ),
        "quote_quality_skip_queue_depth": int(
            _safe_float(maker_no_submission_categories.get("quote_quality_skip_queue_depth"))
        ),
        "replace_guard_min_rest": int(
            _safe_float(maker_no_submission_categories.get("replace_guard_min_rest"))
        ),
    }

    return {
        "observational_only": True,
        "maker_behavior_freeze_state": "provisional_freeze_no_runtime_change",
        "triggered": bool(triggered),
        "regression_reasons": regression_reasons,
        "maker_submits": float(maker_submits),
        "maker_fills": float(maker_fills),
        "maker_fill_rate": float(maker_fill_rate),
        "maker_fire_rate_per_min": float(maker_fire_rate_per_min),
        "thresholds": {
            "near_zero_submit_rate_threshold_per_min": float(near_zero_submit_rate_threshold_per_min),
            "near_zero_submit_count_threshold": float(submit_count_threshold),
            "near_zero_fill_count_threshold": float(near_zero_fill_count_threshold),
            "low_fill_rate_threshold": float(low_fill_rate_threshold),
            "low_fill_rate_requires_submits": float(low_fill_rate_requires_submits),
        },
        "watch_item_primary": "quote_quality_skip_and_replace_guard_distribution",
        "watch_item_distribution": watch_categories,
    }


def _edge_truth_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [evt for evt in events if str(evt.get("event_type") or "").strip() == "edge_evaluation"]
    maker_rows = 0
    taker_rows = 0
    action_rows = 0
    blocked_rows = 0
    maker_blocked_rows = 0
    taker_blocked_rows = 0
    reasons = Counter()
    maker_reasons = Counter()
    taker_reasons = Counter()
    maker_no_submission_causes = Counter()
    maker_no_submission_categories = Counter()
    maker_market_reference_modes = Counter()
    maker_market_reference_fallback_bid_count = 0
    maker_market_reference_fallback_ask_count = 0
    maker_reference_direct_midpoint_activity = 0
    maker_reference_bounded_fallback_activity = 0
    maker_reference_direct_midpoint_action_activity = 0
    maker_reference_bounded_fallback_action_activity = 0
    for row in rows:
        scope = str(row.get("evaluation_scope") or "").strip().lower()
        action = str(row.get("action_taken") or "").strip().lower()
        market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower() or "missing"
        market_reference_source_side = str(row.get("market_reference_source_side") or "").strip().lower() or "none"
        if scope == "maker":
            maker_rows += 1
            maker_market_reference_modes[market_reference_mode] += 1
            if market_reference_mode == "direct_midpoint":
                maker_reference_direct_midpoint_activity += 1
                if action == "maker":
                    maker_reference_direct_midpoint_action_activity += 1
            elif market_reference_mode == "bounded_single_side_touch":
                maker_reference_bounded_fallback_activity += 1
                if action == "maker":
                    maker_reference_bounded_fallback_action_activity += 1
                if market_reference_source_side == "bid":
                    maker_market_reference_fallback_bid_count += 1
                elif market_reference_source_side == "ask":
                    maker_market_reference_fallback_ask_count += 1
        elif scope == "taker":
            taker_rows += 1
        if action in {"maker", "taker"}:
            action_rows += 1
        elif action == "none":
            blocked_rows += 1
            reason = str(row.get("block_reason") or "").strip() or "missing"
            reasons[reason] += 1
            if scope == "maker":
                maker_blocked_rows += 1
                maker_reasons[reason] += 1
                if reason == "maker_no_submission":
                    cause = str(row.get("maker_no_submission_cause") or "").strip().lower() or "unspecified"
                    maker_no_submission_causes[cause] += 1
                    category = str(row.get("maker_no_submission_category") or "").strip().lower() or "unknown"
                    maker_no_submission_categories[category] += 1
            elif scope == "taker":
                taker_blocked_rows += 1
                taker_reasons[reason] += 1
    return {
        "rows_total": float(len(rows)),
        "maker_rows": float(maker_rows),
        "taker_rows": float(taker_rows),
        "action_rows": float(action_rows),
        "blocked_rows": float(blocked_rows),
        "maker_blocked_rows": float(maker_blocked_rows),
        "taker_blocked_rows": float(taker_blocked_rows),
        "block_reason_distribution": dict(sorted(reasons.items(), key=lambda item: item[0])),
        "maker_block_reason_distribution": dict(sorted(maker_reasons.items(), key=lambda item: item[0])),
        "taker_block_reason_distribution": dict(sorted(taker_reasons.items(), key=lambda item: item[0])),
        "maker_no_submission_cause_distribution": dict(
            sorted(maker_no_submission_causes.items(), key=lambda item: item[0])
        ),
        "maker_no_submission_category_distribution": dict(
            sorted(maker_no_submission_categories.items(), key=lambda item: item[0])
        ),
        "maker_market_reference_mode_distribution": dict(
            sorted(maker_market_reference_modes.items(), key=lambda item: item[0])
        ),
        "maker_market_reference_fallback_count": float(maker_reference_bounded_fallback_activity),
        "maker_market_reference_fallback_bid_count": float(maker_market_reference_fallback_bid_count),
        "maker_market_reference_fallback_ask_count": float(maker_market_reference_fallback_ask_count),
        "maker_reference_direct_midpoint_activity": float(maker_reference_direct_midpoint_activity),
        "maker_reference_bounded_fallback_activity": float(maker_reference_bounded_fallback_activity),
        "maker_reference_direct_midpoint_action_activity": float(
            maker_reference_direct_midpoint_action_activity
        ),
        "maker_reference_bounded_fallback_action_activity": float(
            maker_reference_bounded_fallback_action_activity
        ),
    }


def _mode_transition_timeline(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for evt in events:
        if str(evt.get("event_type") or "") != "operating_mode_transition":
            continue
        out.append(
            {
                "ts_utc": str(evt.get("ts_utc") or ""),
                "state": str(evt.get("state") or ""),
                "previous_state": str(evt.get("previous_state") or ""),
                "reason": str(evt.get("reason") or ""),
            }
        )
    return out


def _control_authority_clarity(status: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest = status[-1] if status else {}
    if not isinstance(latest, dict):
        latest = {}
    semantics_obj = latest.get("alert_semantics")
    if not isinstance(semantics_obj, dict):
        semantics_obj = {}

    transport_enabled_raw = semantics_obj.get("transport_enabled", latest.get("alert_transport_enabled"))
    auto_stop_authority_raw = semantics_obj.get(
        "auto_stop_control_authority_enabled",
        latest.get("auto_stop_control_authority_enabled"),
    )
    transport_enabled = _as_bool(transport_enabled_raw)
    auto_stop_control_authority_enabled = _as_bool(auto_stop_authority_raw)
    transport_disable_control_authority_unchanged = _as_bool(
        semantics_obj.get(
            "transport_disable_control_authority_unchanged",
            latest.get("transport_disable_control_authority_unchanged"),
        )
    )
    fields_present = bool(
        (transport_enabled is not None)
        and (auto_stop_control_authority_enabled is not None)
    )
    if (
        fields_present
        and (transport_enabled is False)
        and (auto_stop_control_authority_enabled is True)
        and (transport_disable_control_authority_unchanged is None)
    ):
        transport_disable_control_authority_unchanged = True

    if not fields_present:
        semantics = "unknown_status_fields_missing"
        observation_status = "unknown_missing_fields"
    elif (transport_enabled is False) and bool(transport_disable_control_authority_unchanged):
        semantics = "transport_disabled_control_authority_unchanged"
        observation_status = "explicit"
    else:
        semantics = "transport_and_control_enabled_or_not_applicable"
        observation_status = "explicit"

    return {
        "alert_transport_enabled": transport_enabled,
        "auto_stop_control_authority_enabled": auto_stop_control_authority_enabled,
        "transport_disable_control_authority_unchanged": transport_disable_control_authority_unchanged,
        "transport_layer_class": str(semantics_obj.get("transport_layer_class") or "notification_transport"),
        "control_authority_class": str(semantics_obj.get("control_authority_class") or "risk_control_authority"),
        "transport_disable_semantics": semantics,
        "control_authority_observation_status": observation_status,
    }


def _resolve_starvation_mode(
    *,
    order_submit_total: float,
    fill_total: float,
    runtime_classification: Dict[str, Any],
    kill_switch_events: float,
    safe_stop_transitions: float,
    maker_only_transitions: float,
) -> Dict[str, Any]:
    runtime_class_name = str(runtime_classification.get("classification") or "").strip().upper()
    primary = str(runtime_classification.get("primary_suppression_cause") or "").strip()
    contributing = [str(x).strip() for x in (runtime_classification.get("contributing_suppression_causes") or []) if str(x).strip()]
    ambiguous = bool(runtime_classification.get("ambiguous_suppression_cause", False))
    no_orders = order_submit_total <= 0.0
    no_fills = fill_total <= 0.0
    suppressed = bool(
        no_orders
        and (
            kill_switch_events > 0.0
            or safe_stop_transitions > 0.0
            or maker_only_transitions > 0.0
            or runtime_class_name in {"INVALID_DEADLOCK", "INVALID_SAFETY", "NON_PROMOTABLE_NO_PARTICIPATION"}
            or bool(primary)
            or bool(contributing)
        )
    )

    if not suppressed:
        mode = "none"
        explanation = "execution_not_suppression_dominated"
    elif kill_switch_events > 0.0:
        mode = "kill_switch"
        explanation = "kill_switch_events_detected_with_zero_submits"
    elif runtime_class_name in {"INVALID_DEADLOCK", "INVALID_SAFETY"} or safe_stop_transitions > 0.0:
        mode = "safety_halt"
        explanation = "runtime_safety_or_safe_stop_suppression_with_zero_submits"
    elif maker_only_transitions > 0.0:
        mode = "maker_only_gate"
        explanation = "maker_only_transitions_with_zero_submits"
    elif runtime_class_name == "NON_PROMOTABLE_NO_PARTICIPATION":
        mode = "readiness_hold"
        explanation = "non_promotable_no_participation_with_zero_submits"
    else:
        mode = "unknown"
        explanation = "suppression_detected_without_unique_mode"

    return {
        "suppression_dominated_run": suppressed,
        "execution_starvation_mode": mode,
        "protected_no_trade_explanation": explanation,
        "order_submit_total": float(order_submit_total),
        "fill_total": float(fill_total),
        "runtime_primary_suppression_cause": primary or "none",
        "runtime_contributing_suppression_causes": sorted(set(contributing)),
        "runtime_ambiguous_suppression_cause": ambiguous,
    }


def _first_event(events: List[Dict[str, Any]], event_type: str) -> Dict[str, Any]:
    for evt in events:
        if str(evt.get("event_type") or "").strip() != event_type:
            continue
        return {
            "event_type": event_type,
            "ts_utc": str(evt.get("ts_utc") or ""),
            "reason": str(evt.get("reason") or ""),
            "reasons": list(evt.get("reasons") or []) if isinstance(evt.get("reasons"), list) else [],
            "state": str(evt.get("state") or ""),
            "previous_state": str(evt.get("previous_state") or ""),
        }
    return {}


def _first_block_reason(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        action = str(evt.get("action_taken") or "").strip().lower()
        block_reason = str(evt.get("block_reason") or "").strip()
        if action != "none" or not block_reason:
            continue
        return {
            "event_type": "edge_evaluation",
            "ts_utc": str(evt.get("ts_utc") or ""),
            "block_reason": block_reason,
        }
    return {}


def _protection_path_trigger_chain(
    *,
    events: List[Dict[str, Any]],
    runtime_classification: Dict[str, Any],
    starvation: Dict[str, Any],
) -> Dict[str, Any]:
    ordered_events = sorted(
        [evt for evt in events if isinstance(evt, dict)],
        key=lambda evt: str(evt.get("ts_utc") or ""),
    )
    first_block = _first_block_reason(ordered_events)
    first_mode_transition = _first_event(ordered_events, "operating_mode_transition")
    first_auto_stop = _first_event(ordered_events, "alert_policy_auto_stop")
    first_kill_switch_cancel = _first_event(ordered_events, "kill_switch_cancel_all")
    first_latency_regime_change = _first_event(ordered_events, "latency_regime_change")
    suppression_dominated = bool(starvation.get("suppression_dominated_run", False))
    if suppression_dominated:
        interpretation = "causal_suppression_chain"
    else:
        interpretation = "observational_timeline_only"
    return {
        "trigger_chain_interpretation": interpretation,
        "suppression_dominated_run": suppression_dominated,
        "first_blocked_edge_signal": first_block,
        "first_latency_regime_change": first_latency_regime_change,
        "first_operating_mode_transition": first_mode_transition,
        "first_alert_policy_auto_stop": first_auto_stop,
        "first_kill_switch_cancel_all": first_kill_switch_cancel,
        "runtime_primary_suppression_cause": str(runtime_classification.get("primary_suppression_cause") or "none"),
        "runtime_contributing_suppression_causes": list(runtime_classification.get("contributing_suppression_causes") or []),
        "runtime_ambiguous_suppression_cause": bool(runtime_classification.get("ambiguous_suppression_cause", False)),
        "final_execution_starvation_mode": str(starvation.get("execution_starvation_mode") or "unknown"),
        "final_protected_no_trade_explanation": str(starvation.get("protected_no_trade_explanation") or ""),
    }


def _pickoff_indicator(events: List[Dict[str, Any]], *, horizon_sec: float = 3.0, adverse_threshold: float = 0.003) -> Dict[str, float]:
    rows: List[Tuple[dt.datetime, Dict[str, Any]]] = []
    for evt in events:
        ts = parse_ts(evt.get("ts_utc"))
        if ts is not None:
            rows.append((ts, evt))
    rows.sort(key=lambda item: item[0])

    fills: List[Tuple[dt.datetime, Dict[str, Any]]] = [
        (ts, evt) for ts, evt in rows if str(evt.get("event_type") or "") == "fill"
    ]
    books_by_token: Dict[str, List[Tuple[dt.datetime, float]]] = {}
    for ts, evt in rows:
        if str(evt.get("event_type") or "") != "book_top":
            continue
        token_id = str(evt.get("token_id") or "")
        midpoint = evt.get("midpoint")
        if token_id and isinstance(midpoint, (int, float)):
            books_by_token.setdefault(token_id, []).append((ts, float(midpoint)))

    scored = 0.0
    adverse = 0.0
    for fill_ts, fill_evt in fills:
        token_id = str(fill_evt.get("token_id") or "")
        side = str(fill_evt.get("side") or "").upper()
        fill_price = _safe_float(fill_evt.get("price"), default=-1.0)
        if token_id == "" or fill_price <= 0 or side not in {"BUY", "SELL"}:
            continue
        books = books_by_token.get(token_id, [])
        horizon = fill_ts + dt.timedelta(seconds=max(0.1, float(horizon_sec)))
        future_mid: Optional[float] = None
        for ts, mid in books:
            if ts <= fill_ts:
                continue
            if ts > horizon:
                break
            future_mid = mid
            break
        if future_mid is None:
            continue
        scored += 1.0
        if side == "BUY":
            delta = future_mid - fill_price
        else:
            delta = fill_price - future_mid
        if delta < (-abs(float(adverse_threshold))):
            adverse += 1.0

    return {
        "fills_scored": scored,
        "adverse_after_fill_count": adverse,
        "adverse_after_fill_ratio": (adverse / scored) if scored > 0 else 0.0,
        "horizon_sec": float(horizon_sec),
        "adverse_threshold": float(adverse_threshold),
    }


def _maker_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    timing_gate_blocked_edge_eval = 0.0
    timing_gate_blocked_decision = 0.0
    one_sided_decision_buy = 0.0
    one_sided_decision_sell = 0.0
    one_sided_submit_buy = 0.0
    one_sided_submit_sell = 0.0
    edge_bucket_submit = Counter()
    edge_bucket_fill = Counter()
    aggressiveness_application_counts = Counter()
    order_bucket_by_id: Dict[str, str] = {}

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "edge_evaluation":
            if (
                str(evt.get("evaluation_scope") or "").strip().lower() == "maker"
                and str(evt.get("action_taken") or "").strip().lower() == "none"
                and str(evt.get("block_reason") or "").strip().lower() == "maker_timing_gate_closed"
            ):
                timing_gate_blocked_edge_eval += 1.0
            continue

        if event_type == "maker_competitiveness_decision":
            if bool(evt.get("timing_gate_blocked", False)):
                timing_gate_blocked_decision += 1.0
            if bool(evt.get("one_sided_active", False)):
                policy = str(evt.get("side_policy") or "").strip().upper()
                if policy == "BUY_ONLY":
                    one_sided_decision_buy += 1.0
                elif policy == "SELL_ONLY":
                    one_sided_decision_sell += 1.0
            continue

        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            if "sniper_taker" in reason or "taker_bonus" in reason:
                continue
            comp = evt.get("maker_competitiveness")
            if not isinstance(comp, dict):
                continue
            bucket = str(comp.get("edge_bucket") or "unknown").strip().lower() or "unknown"
            edge_bucket_submit[bucket] += 1
            order_id = str(evt.get("order_id") or "").strip()
            if order_id:
                order_bucket_by_id[order_id] = bucket

            if _safe_float(comp.get("size_multiplier_competitiveness"), 1.0) > 1.0:
                aggressiveness_application_counts["size_scaled"] += 1
            if _safe_float(comp.get("spread_multiplier_competitiveness"), 1.0) < 1.0:
                aggressiveness_application_counts["spread_tightened"] += 1
            if _safe_float(comp.get("requote_delta_multiplier_competitiveness"), 1.0) < 1.0:
                aggressiveness_application_counts["requote_tightened"] += 1

            if bool(comp.get("one_sided_active", False)):
                policy = str(comp.get("side_policy") or "").strip().upper()
                if policy == "BUY_ONLY":
                    one_sided_submit_buy += 1.0
                elif policy == "SELL_ONLY":
                    one_sided_submit_sell += 1.0
            continue

        if event_type == "fill":
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            bucket = order_bucket_by_id.get(order_id)
            if bucket:
                edge_bucket_fill[bucket] += 1

    return {
        "timing_gate_blocked_count_edge_eval": float(timing_gate_blocked_edge_eval),
        "timing_gate_blocked_count_decision": float(timing_gate_blocked_decision),
        "one_sided_activation_decision_buy_count": float(one_sided_decision_buy),
        "one_sided_activation_decision_sell_count": float(one_sided_decision_sell),
        "one_sided_activation_submit_buy_count": float(one_sided_submit_buy),
        "one_sided_activation_submit_sell_count": float(one_sided_submit_sell),
        "maker_submit_edge_bucket_distribution": dict(
            sorted(edge_bucket_submit.items(), key=lambda item: item[0])
        ),
        "maker_fill_edge_bucket_distribution": dict(
            sorted(edge_bucket_fill.items(), key=lambda item: item[0])
        ),
        "aggressiveness_application_counts": dict(
            sorted(aggressiveness_application_counts.items(), key=lambda item: item[0])
        ),
    }


def _taker_edge_bucket(edge_abs: Any) -> str:
    value = _safe_float(edge_abs, default=-1.0)
    if value < 0.0:
        return "unknown"
    if value <= 0.10:
        return "le_0p10"
    if value <= 0.30:
        return "0p10_0p30"
    if value <= 0.60:
        return "0p30_0p60"
    return "gt_0p60"


def _conviction_bucket(conviction_score: Any) -> str:
    value = _safe_float(conviction_score, default=-1.0)
    if value < 0.0:
        return "unknown"
    if value <= 0.33:
        return "le_0p33"
    if value <= 0.66:
        return "0p33_0p66"
    return "gt_0p66"


def _taker_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    decision_timing_window = Counter()
    decision_edge_bucket = Counter()
    decision_conviction_bucket = Counter()
    decision_block_reason = Counter()
    decision_aggressiveness = Counter()
    decision_multi_oracle_status = Counter()
    submit_edge_bucket = Counter()
    submit_conviction_bucket = Counter()
    submit_timing_window = Counter()
    submit_multi_oracle_status = Counter()
    fill_edge_bucket = Counter()
    lag_class_distribution = Counter()
    aggressiveness_application_counts = Counter()
    order_edge_bucket_by_id: Dict[str, str] = {}
    order_is_taker_by_id: Dict[str, bool] = {}
    hard_min_unachievable_count = 0.0
    dynamic_size_capped_by_risk_count = 0.0
    multi_oracle_confirmation_count = 0.0
    multi_oracle_boost_applied_count = 0.0
    outside_window_blocked_count_edge_eval = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()

        if event_type == "edge_evaluation":
            if (
                str(evt.get("evaluation_scope") or "").strip().lower() == "taker"
                and str(evt.get("action_taken") or "").strip().lower() == "none"
                and str(evt.get("block_reason") or "").strip().lower() == "taker_outside_final_window"
            ):
                outside_window_blocked_count_edge_eval += 1.0
            continue

        if event_type == "sniper_taker_decision":
            timing_window = str(evt.get("timing_window_class") or "unknown").strip().lower() or "unknown"
            decision_timing_window[timing_window] += 1
            edge_bucket = _taker_edge_bucket(evt.get("edge_abs"))
            conviction_bucket = _conviction_bucket(evt.get("conviction_score"))
            decision_edge_bucket[edge_bucket] += 1
            decision_conviction_bucket[conviction_bucket] += 1
            block_reason = str(evt.get("block_reason") or "").strip().lower()
            if block_reason:
                decision_block_reason[block_reason] += 1
            aggressiveness_level = str(evt.get("aggressiveness_level") or "unknown").strip().lower() or "unknown"
            decision_aggressiveness[aggressiveness_level] += 1
            decision_multi_oracle_status[
                str(evt.get("multi_oracle_status") or "unknown").strip().lower() or "unknown"
            ] += 1
            if bool(evt.get("hard_min_unachievable", False)):
                hard_min_unachievable_count += 1.0
            if bool(evt.get("dynamic_size_capped_by_risk", False)):
                dynamic_size_capped_by_risk_count += 1.0
            if bool(evt.get("multi_oracle_confirmation", False)):
                multi_oracle_confirmation_count += 1.0
            if bool(evt.get("multi_oracle_boost_applied", False)):
                multi_oracle_boost_applied_count += 1.0
            continue

        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            is_taker_sniper = "sniper_taker" in reason or "taker_bonus" in reason
            order_id = str(evt.get("order_id") or "").strip()
            if order_id:
                order_is_taker_by_id[order_id] = is_taker_sniper
            if not is_taker_sniper:
                continue
            comp = evt.get("taker_competitiveness")
            if not isinstance(comp, dict):
                continue
            edge_bucket = _taker_edge_bucket(comp.get("edge_abs"))
            conviction_bucket = _conviction_bucket(comp.get("conviction_score"))
            timing_window = str(comp.get("timing_window_class") or "unknown").strip().lower() or "unknown"
            submit_edge_bucket[edge_bucket] += 1
            submit_conviction_bucket[conviction_bucket] += 1
            submit_timing_window[timing_window] += 1
            submit_multi_oracle_status[
                str(comp.get("multi_oracle_status") or "unknown").strip().lower() or "unknown"
            ] += 1
            if order_id:
                order_edge_bucket_by_id[order_id] = edge_bucket

            if _safe_float(comp.get("price_aggress_bps_applied"), 0.0) > 0.0:
                aggressiveness_application_counts["price_aggressed"] += 1
            if bool(comp.get("hard_min_floor_applied", False)):
                aggressiveness_application_counts["hard_min_floor_applied"] += 1
            if bool(comp.get("dynamic_size_capped_by_risk", False)):
                aggressiveness_application_counts["dynamic_size_capped_by_risk"] += 1
            if bool(comp.get("multi_oracle_confirmation", False)):
                aggressiveness_application_counts["multi_oracle_confirmation"] += 1
            if bool(comp.get("multi_oracle_boost_applied", False)):
                aggressiveness_application_counts["multi_oracle_boost_applied"] += 1
            continue

        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if not order_id or not bool(order_is_taker_by_id.get(order_id)):
            continue
        edge_bucket = order_edge_bucket_by_id.get(order_id, "unknown")
        fill_edge_bucket[edge_bucket] += 1
        lag_class = str(evt.get("paper_chainlink_lag_class") or "unknown").strip().lower() or "unknown"
        lag_class_distribution[lag_class] += 1

    return {
        "outside_window_blocked_count_edge_eval": float(outside_window_blocked_count_edge_eval),
        "decision_timing_window_distribution": dict(sorted(decision_timing_window.items(), key=lambda item: item[0])),
        "decision_edge_bucket_distribution": dict(sorted(decision_edge_bucket.items(), key=lambda item: item[0])),
        "decision_conviction_bucket_distribution": dict(
            sorted(decision_conviction_bucket.items(), key=lambda item: item[0])
        ),
        "decision_block_reason_distribution": dict(
            sorted(decision_block_reason.items(), key=lambda item: item[0])
        ),
        "decision_aggressiveness_distribution": dict(
            sorted(decision_aggressiveness.items(), key=lambda item: item[0])
        ),
        "decision_multi_oracle_status_distribution": dict(
            sorted(decision_multi_oracle_status.items(), key=lambda item: item[0])
        ),
        "submit_edge_bucket_distribution": dict(sorted(submit_edge_bucket.items(), key=lambda item: item[0])),
        "submit_conviction_bucket_distribution": dict(
            sorted(submit_conviction_bucket.items(), key=lambda item: item[0])
        ),
        "submit_timing_window_distribution": dict(
            sorted(submit_timing_window.items(), key=lambda item: item[0])
        ),
        "submit_multi_oracle_status_distribution": dict(
            sorted(submit_multi_oracle_status.items(), key=lambda item: item[0])
        ),
        "fill_edge_bucket_distribution": dict(sorted(fill_edge_bucket.items(), key=lambda item: item[0])),
        "lag_class_distribution": dict(sorted(lag_class_distribution.items(), key=lambda item: item[0])),
        "aggressiveness_application_counts": dict(
            sorted(aggressiveness_application_counts.items(), key=lambda item: item[0])
        ),
        "hard_min_unachievable_count_decision": float(hard_min_unachievable_count),
        "dynamic_size_capped_by_risk_count_decision": float(dynamic_size_capped_by_risk_count),
        "multi_oracle_confirmation_count_decision": float(multi_oracle_confirmation_count),
        "multi_oracle_boost_applied_count_decision": float(multi_oracle_boost_applied_count),
    }


def _risk_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    decision_count_by_lane = Counter()
    reject_count_by_lane = Counter()
    reject_reason_distribution = Counter()
    scaling_class_distribution = Counter()
    exposure_utilization_ratios: List[float] = []
    near_cap_count = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type not in {"order_submit", "risk_reject"}:
            continue
        lane = str(evt.get("submission_lane") or "unknown").strip().lower() or "unknown"
        if event_type == "risk_reject":
            reason = str(evt.get("reason") or "unknown").strip().lower() or "unknown"
            reject_reason_distribution[reason] += 1
            reject_count_by_lane[lane] += 1
        basis = evt.get("risk_decision_basis")
        if not isinstance(basis, dict):
            continue
        decision_count_by_lane[lane] += 1
        dynamic_scaling = basis.get("dynamic_scaling")
        if isinstance(dynamic_scaling, dict):
            scaling_class = str(dynamic_scaling.get("scaling_class") or "unknown").strip().lower() or "unknown"
            scaling_class_distribution[scaling_class] += 1
        global_guard = basis.get("global_exposure_guard")
        if isinstance(global_guard, dict):
            ratio = _safe_float(global_guard.get("projected_to_cap_ratio"), default=-1.0)
            if ratio >= 0.0:
                exposure_utilization_ratios.append(float(ratio))
            if bool(global_guard.get("near_cap", False)):
                near_cap_count += 1.0

    return {
        "decision_count_by_lane": dict(sorted(decision_count_by_lane.items(), key=lambda item: item[0])),
        "reject_count_by_lane": dict(sorted(reject_count_by_lane.items(), key=lambda item: item[0])),
        "reject_reason_distribution": dict(sorted(reject_reason_distribution.items(), key=lambda item: item[0])),
        "scaling_class_distribution": dict(sorted(scaling_class_distribution.items(), key=lambda item: item[0])),
        "global_exposure_utilization_sample_count": float(len(exposure_utilization_ratios)),
        "global_exposure_utilization_ratio_p50": _percentile(exposure_utilization_ratios, 0.50),
        "global_exposure_utilization_ratio_p90": _percentile(exposure_utilization_ratios, 0.90),
        "global_exposure_utilization_ratio_max": (
            max(exposure_utilization_ratios) if exposure_utilization_ratios else 0.0
        ),
        "global_exposure_near_cap_count": float(near_cap_count),
        "global_exposure_cap_reject_count": float(reject_reason_distribution.get("global_exposure_cap", 0)),
    }


def _maker_sizing_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    maker_submit_rows = 0
    maker_size_resolution_rows = 0
    hard_min_notional_floor_applied = 0
    hard_min_share_floor_applied = 0
    depth_target_notional_floor_applied = 0
    hard_max_notional_cap_applied = 0
    hard_max_share_cap_applied = 0
    hard_floor_active_rows = 0
    depth_scaling_active_rows = 0
    resolved_notional_values: List[float] = []
    visible_depth_values: List[float] = []
    effective_depth_values: List[float] = []
    depth_target_ratio_values: List[float] = []

    for evt in events:
        if str(evt.get("event_type") or "").strip().lower() != "order_submit":
            continue
        if str(evt.get("submission_lane") or "").strip().lower() != "maker":
            continue
        maker_submit_rows += 1
        size_resolution = evt.get("size_resolution")
        if not isinstance(size_resolution, dict):
            continue
        maker_size_resolution_rows += 1
        reasons = {
            str(item).strip().lower()
            for item in list(size_resolution.get("size_decision_reasons") or [])
            if str(item).strip()
        }
        if "maker_hard_min_notional_floor" in reasons:
            hard_min_notional_floor_applied += 1
        if "maker_hard_min_shares_floor" in reasons:
            hard_min_share_floor_applied += 1
        if "maker_depth_target_notional_floor" in reasons:
            depth_target_notional_floor_applied += 1
        if "maker_hard_max_notional_cap" in reasons:
            hard_max_notional_cap_applied += 1
        if "maker_hard_max_shares_cap" in reasons:
            hard_max_share_cap_applied += 1

        hard_floor_active = _as_bool(size_resolution.get("maker_hard_floor_active"))
        if hard_floor_active is True:
            hard_floor_active_rows += 1
        elif hard_floor_active is None:
            # backward-safe inference for older rows where explicit boolean may be absent
            min_notional = _safe_float(size_resolution.get("maker_hard_min_notional_usd"), 0.0)
            min_shares = _safe_float(size_resolution.get("maker_hard_min_shares"), 0.0)
            if min_notional > 0.0 or min_shares > 0.0:
                hard_floor_active_rows += 1

        depth_scaling_active = _as_bool(size_resolution.get("maker_depth_scaling_active"))
        if depth_scaling_active is True:
            depth_scaling_active_rows += 1

        resolved_notional = size_resolution.get("resolved_notional_usd")
        if isinstance(resolved_notional, (int, float)):
            resolved_notional_values.append(float(resolved_notional))
        visible_depth = size_resolution.get("visible_depth_shares")
        if isinstance(visible_depth, (int, float)):
            visible_depth_values.append(float(visible_depth))
        effective_depth = size_resolution.get("effective_depth_shares")
        if isinstance(effective_depth, (int, float)):
            effective_depth_values.append(float(effective_depth))
        depth_ratio = size_resolution.get("maker_depth_target_ratio_applied")
        if isinstance(depth_ratio, (int, float)):
            depth_target_ratio_values.append(float(depth_ratio))

    return {
        "maker_submit_rows": float(maker_submit_rows),
        "maker_size_resolution_rows": float(maker_size_resolution_rows),
        "hard_min_notional_floor_applied_count": float(hard_min_notional_floor_applied),
        "hard_min_share_floor_applied_count": float(hard_min_share_floor_applied),
        "depth_target_notional_floor_applied_count": float(depth_target_notional_floor_applied),
        "hard_max_notional_cap_applied_count": float(hard_max_notional_cap_applied),
        "hard_max_share_cap_applied_count": float(hard_max_share_cap_applied),
        "hard_floor_active_rows": float(hard_floor_active_rows),
        "depth_scaling_active_rows": float(depth_scaling_active_rows),
        "resolved_notional_usd_p50": _percentile(resolved_notional_values, 0.50),
        "resolved_notional_usd_p90": _percentile(resolved_notional_values, 0.90),
        "resolved_notional_usd_max": max(resolved_notional_values) if resolved_notional_values else 0.0,
        "visible_depth_shares_p50": _percentile(visible_depth_values, 0.50),
        "effective_depth_shares_p50": _percentile(effective_depth_values, 0.50),
        "depth_target_ratio_applied_p50": _percentile(depth_target_ratio_values, 0.50),
    }


def build_report(
    log_dir: pathlib.Path,
    *,
    run_id: Optional[str] = None,
    auto_resolve_run_id: bool = True,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="nightly_soak_report", session_phase=session_phase)
    explicit_run_id = str(run_id or "").strip() or None
    resolved_log_dir = log_dir.resolve()
    contract = resolve_run_contract(
        log_dir=resolved_log_dir,
        run_id=explicit_run_id,
        run_contract_path_override=run_contract_path,
        allow_open=(normalized_phase == "validate_active"),
    )
    if contract is not None:
        contract_run_id = str(contract.get("run_id") or "").strip()
        if explicit_run_id and contract_run_id != explicit_run_id:
            raise ValueError(f"run_contract_run_id_mismatch:{contract_run_id}!={explicit_run_id}")
        resolved_run_id = contract_run_id or explicit_run_id
        event_files = _load_contract_scoped_files(contract=contract, prefix="events")
        status_files = _load_contract_scoped_files(contract=contract, prefix="status")
        error_files = _load_contract_scoped_files(contract=contract, prefix="errors")
        if not event_files:
            event_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="events", run_id=resolved_run_id)
        if not status_files:
            status_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="status", run_id=resolved_run_id)
        if not error_files:
            error_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="errors", run_id=resolved_run_id)
        # Contract-scoped files are run slices; do full-load for deterministic replay.
        events = apply_contract_bounds(load_jsonl(event_files, max_lines_per_file=0), contract)
        status = apply_contract_bounds(load_jsonl(status_files, max_lines_per_file=0), contract)
        errors = apply_contract_bounds(load_jsonl(error_files, max_lines_per_file=0), contract)
    else:
        resolved_run_id = explicit_run_id
        event_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="events", run_id=resolved_run_id)
        status_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="status", run_id=resolved_run_id)
        error_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="errors", run_id=resolved_run_id)
        events = _filter_rows_by_run_id(load_jsonl(event_files, max_lines_per_file=max_lines_per_file), resolved_run_id)
        status = _filter_rows_by_run_id(load_jsonl(status_files, max_lines_per_file=max_lines_per_file), resolved_run_id)
        errors = _filter_rows_by_run_id(load_jsonl(error_files, max_lines_per_file=max_lines_per_file), resolved_run_id)

    reject_reasons = Counter(
        str(evt.get("reason") or "unknown")
        for evt in events
        if str(evt.get("event_type") or "") == "risk_reject"
    )
    status_rows = len(status)
    quote_uptime = _quote_uptime_ratio(status)
    quote_diagnostics = _status_activity_diagnostics(status)
    by_component = Counter(str(err.get("component") or "unknown") for err in errors)
    capture_stats = _fill_capture_stats(events)
    taker_stage_net_breakout = _taker_stage_net_breakout(events)
    edge_quality = _edge_quality_by_regime(events)
    maker_competitiveness = _maker_competitiveness_stats(events)
    taker_competitiveness = _taker_competitiveness_stats(events)
    risk_competitiveness = _risk_competitiveness_stats(events)
    maker_sizing_competitiveness = _maker_sizing_competitiveness_stats(events)
    duration_minutes = _run_duration_minutes(events, status, errors)
    stale_stats = _stale_data_stats(events)
    latency_stats = _latency_distribution(events)
    sniper = _sniper_stats(events, duration_minutes)
    execution_paths = _execution_path_stats(events, duration_minutes)
    edge_truth = _edge_truth_summary(events)
    harness_realism_grade, harness_realism_grade_breakdown = _harness_realism_grade(
        events=events,
        edge_truth=edge_truth,
    )
    maker_regression_sentinel = _maker_regression_sentinel(
        execution_paths=execution_paths,
        edge_truth=edge_truth,
        duration_minutes=duration_minutes,
    )
    mode_timeline = _mode_transition_timeline(events)
    pickoff = _pickoff_indicator(events)
    runtime_classification = classify_runtime(status_rows=status, events=events)
    control_authority = _control_authority_clarity(status)
    market_data_source = _market_data_source_stats(status)
    kill_switch_events = float(
        sum(1 for evt in events if str(evt.get("event_type") or "") == "kill_switch_cancel_all")
    )
    safe_stop_transitions = float(
        sum(1 for evt in mode_timeline if str(evt.get("state") or "") == "safe_stop")
    )
    maker_only_transitions = float(
        sum(1 for evt in mode_timeline if str(evt.get("state") or "") == "maker_only")
    )
    order_submit_total = float(_safe_float(execution_paths.get("maker_submits")) + _safe_float(execution_paths.get("taker_bonus_submits")))
    fill_total = float(_safe_float(execution_paths.get("maker_fills")) + _safe_float(execution_paths.get("taker_bonus_fills")))
    starvation = _resolve_starvation_mode(
        order_submit_total=order_submit_total,
        fill_total=fill_total,
        runtime_classification=runtime_classification,
        kill_switch_events=kill_switch_events,
        safe_stop_transitions=safe_stop_transitions,
        maker_only_transitions=maker_only_transitions,
    )
    protection_path_trigger_chain = _protection_path_trigger_chain(
        events=events,
        runtime_classification=runtime_classification,
        starvation=starvation,
    )
    latest_operating_mode_state = 0.0
    if status:
        mode_val = status[-1].get("gauge.operating_mode_state")
        if isinstance(mode_val, (int, float)):
            latest_operating_mode_state = float(mode_val)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ts_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "log_dir": str(resolved_log_dir),
        "session_phase": normalized_phase,
        "run_contract_path": str(contract.get("_path", "")) if isinstance(contract, dict) else "",
        "run_id_filter": resolved_run_id,
        "run_id_resolution": (
            "explicit"
            if explicit_run_id
            else "all_runs"
        ),
        "artifact_identity": build_artifact_identity(log_dir=resolved_log_dir, run_id=resolved_run_id),
        "event_files": len(event_files),
        "status_files": len(status_files),
        "error_files": len(error_files),
        "duration_minutes": duration_minutes,
        "status_rows": status_rows,
        "error_rows": len(errors),
        "quote_uptime_ratio": quote_uptime,
        "quote_diagnostics": quote_diagnostics,
        "reject_reason_distribution": dict(sorted(reject_reasons.items(), key=lambda x: x[0])),
        "errors_by_component": dict(sorted(by_component.items(), key=lambda x: x[0])),
        "stale_data": stale_stats,
        "latency_distribution_ms": latency_stats,
        "sniper": sniper,
        "execution_paths": execution_paths,
        "maker_regression_sentinel": maker_regression_sentinel,
        "edge_truth": edge_truth,
        "maker_competitiveness": maker_competitiveness,
        "taker_competitiveness": taker_competitiveness,
        "risk_competitiveness": risk_competitiveness,
        "maker_sizing_competitiveness": maker_sizing_competitiveness,
        "harness_realism_grade": int(harness_realism_grade),
        "harness_realism_grade_breakdown": dict(harness_realism_grade_breakdown),
        "maker_market_reference_fallback_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_count")
        ),
        "maker_market_reference_fallback_bid_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_bid_count")
        ),
        "maker_market_reference_fallback_ask_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_ask_count")
        ),
        "maker_reference_direct_midpoint_activity": _safe_float(
            edge_truth.get("maker_reference_direct_midpoint_activity")
        ),
        "maker_reference_bounded_fallback_activity": _safe_float(
            edge_truth.get("maker_reference_bounded_fallback_activity")
        ),
        "maker_reference_direct_midpoint_action_activity": _safe_float(
            edge_truth.get("maker_reference_direct_midpoint_action_activity")
        ),
        "maker_reference_bounded_fallback_action_activity": _safe_float(
            edge_truth.get("maker_reference_bounded_fallback_action_activity")
        ),
        "mode_transitions": mode_timeline,
        "kill_switch_events": kill_switch_events,
        "safe_stop_transitions": safe_stop_transitions,
        "maker_only_transitions": maker_only_transitions,
        "primary_suppression_cause": str(runtime_classification.get("primary_suppression_cause") or "none"),
        "contributing_suppression_causes": list(runtime_classification.get("contributing_suppression_causes") or []),
        "ambiguous_suppression_cause": bool(runtime_classification.get("ambiguous_suppression_cause", False)),
        "suppression_dominated_run": bool(starvation.get("suppression_dominated_run", False)),
        "execution_starvation_mode": str(starvation.get("execution_starvation_mode") or "unknown"),
        "protected_no_trade_explanation": str(starvation.get("protected_no_trade_explanation") or ""),
        "control_authority_clarity": control_authority,
        "protection_path_trigger_chain": protection_path_trigger_chain,
        "latest_operating_mode_state": latest_operating_mode_state,
        "pickoff_indicator": pickoff,
        "market_data_source": market_data_source,
        "execution_quality": capture_stats,
        "taker_stage_net_breakout": taker_stage_net_breakout,
        "edge_activation_quality_by_regime": edge_quality,
        "runtime_classification": runtime_classification,
    }


def render_human_summary(report: Dict[str, Any]) -> str:
    top_reject = sorted(report.get("reject_reason_distribution", {}).items(), key=lambda x: x[1], reverse=True)[:5]
    latency = report.get("latency_distribution_ms", {})
    sniper = report.get("sniper", {})
    paths = report.get("execution_paths", {})
    stale = report.get("stale_data", {})
    pickoff = report.get("pickoff_indicator", {})
    market_data_source = report.get("market_data_source", {})
    mode_transitions = report.get("mode_transitions", [])
    eq = report.get("execution_quality", {})
    taker_stage_net = report.get("taker_stage_net_breakout", {}) if isinstance(report.get("taker_stage_net_breakout"), dict) else {}
    edge_truth = report.get("edge_truth", {}) if isinstance(report.get("edge_truth"), dict) else {}
    maker_comp = report.get("maker_competitiveness", {}) if isinstance(report.get("maker_competitiveness"), dict) else {}
    taker_comp = report.get("taker_competitiveness", {}) if isinstance(report.get("taker_competitiveness"), dict) else {}
    risk_comp = report.get("risk_competitiveness", {}) if isinstance(report.get("risk_competitiveness"), dict) else {}
    maker_size_comp = (
        report.get("maker_sizing_competitiveness", {})
        if isinstance(report.get("maker_sizing_competitiveness"), dict)
        else {}
    )
    runtime_class = report.get("runtime_classification", {}) if isinstance(report.get("runtime_classification"), dict) else {}
    runtime_class_name = str(runtime_class.get("classification") or "")
    runtime_promotable = bool(runtime_class.get("promotion_eligible", False))
    primary_suppression_cause = str(report.get("primary_suppression_cause") or "none")
    starvation_mode = str(report.get("execution_starvation_mode") or "unknown")
    suppression_dominated_run = bool(report.get("suppression_dominated_run", False))

    lines = [
        f"log_dir={report.get('log_dir')}",
        f"duration_minutes={_safe_float(report.get('duration_minutes')):.2f}",
        f"quote_uptime_ratio={_safe_float(report.get('quote_uptime_ratio')):.4f}",
        f"error_rows={int(_safe_float(report.get('error_rows')))}",
        (
            "harness_realism="
            + f"grade={int(_safe_float(report.get('harness_realism_grade')))},"
            + f"breakdown={json.dumps(report.get('harness_realism_grade_breakdown', {}), sort_keys=True)}"
        ),
        f"top_rejects={top_reject}",
        (
            "latency_ms="
            + f"samples={int(_safe_float(latency.get('sample_count')))},"
            + f"median={_safe_float(latency.get('median_ms')):.2f},"
            + f"p90={_safe_float(latency.get('p90_ms')):.2f},"
            + f"p95={_safe_float(latency.get('p95_ms')):.2f}"
        ),
        (
            "sniper="
            + f"submits={int(_safe_float(sniper.get('submits')))},"
            + f"fills={int(_safe_float(sniper.get('fills')))},"
            + f"fill_rate={_safe_float(sniper.get('fill_rate')):.4f},"
            + f"midpoint_win_rate_proxy={_safe_float(sniper.get('midpoint_win_rate_proxy')):.4f},"
            + f"fire_rate_per_min={_safe_float(sniper.get('fire_rate_per_min')):.4f}"
        ),
        (
            "execution_paths="
            + f"maker_submits={int(_safe_float(paths.get('maker_submits')))},"
            + f"maker_fills={int(_safe_float(paths.get('maker_fills')))},"
            + f"taker_bonus_submits={int(_safe_float(paths.get('taker_bonus_submits')))},"
            + f"taker_bonus_fills={int(_safe_float(paths.get('taker_bonus_fills')))}"
        ),
        (
            "maker_regression_sentinel="
            + f"triggered={1 if bool((report.get('maker_regression_sentinel') or {}).get('triggered', False)) else 0},"
            + f"reasons={list((report.get('maker_regression_sentinel') or {}).get('regression_reasons') or [])}"
        ),
        (
            "maker_reference_activity="
            + f"direct_midpoint={int(_safe_float(edge_truth.get('maker_reference_direct_midpoint_activity')))},"
            + f"bounded_fallback={int(_safe_float(edge_truth.get('maker_reference_bounded_fallback_activity')))},"
            + f"fallback_bid={int(_safe_float(edge_truth.get('maker_market_reference_fallback_bid_count')))},"
            + f"fallback_ask={int(_safe_float(edge_truth.get('maker_market_reference_fallback_ask_count')))}"
        ),
        (
            "maker_competitiveness="
            + f"timing_gate_blocked={int(_safe_float(maker_comp.get('timing_gate_blocked_count_edge_eval')))},"
            + f"one_sided_submit_buy={int(_safe_float(maker_comp.get('one_sided_activation_submit_buy_count')))},"
            + f"one_sided_submit_sell={int(_safe_float(maker_comp.get('one_sided_activation_submit_sell_count')))},"
            + f"aggressiveness={json.dumps(maker_comp.get('aggressiveness_application_counts', {}), sort_keys=True)}"
        ),
        (
            "taker_competitiveness="
            + f"outside_window_blocked={int(_safe_float(taker_comp.get('outside_window_blocked_count_edge_eval')))},"
            + f"hard_min_unachievable={int(_safe_float(taker_comp.get('hard_min_unachievable_count_decision')))},"
            + f"dynamic_capped={int(_safe_float(taker_comp.get('dynamic_size_capped_by_risk_count_decision')))},"
            + f"aggressiveness={json.dumps(taker_comp.get('aggressiveness_application_counts', {}), sort_keys=True)}"
        ),
        (
            "risk_competitiveness="
            + f"decisions={json.dumps(risk_comp.get('decision_count_by_lane', {}), sort_keys=True)},"
            + f"rejects={json.dumps(risk_comp.get('reject_count_by_lane', {}), sort_keys=True)},"
            + f"scaling_classes={json.dumps(risk_comp.get('scaling_class_distribution', {}), sort_keys=True)},"
            + f"global_exposure_rejects={int(_safe_float(risk_comp.get('global_exposure_cap_reject_count')))}"
        ),
        (
            "maker_sizing_competitiveness="
            + f"submit_rows={int(_safe_float(maker_size_comp.get('maker_submit_rows')))},"
            + f"hard_min_notional_applied={int(_safe_float(maker_size_comp.get('hard_min_notional_floor_applied_count')))},"
            + f"depth_target_applied={int(_safe_float(maker_size_comp.get('depth_target_notional_floor_applied_count')))},"
            + f"resolved_notional_p50={_safe_float(maker_size_comp.get('resolved_notional_usd_p50')):.2f},"
            + f"resolved_notional_p90={_safe_float(maker_size_comp.get('resolved_notional_usd_p90')):.2f}"
        ),
        (
            "stale="
            + f"stale_book_rejects={int(_safe_float(stale.get('stale_book_rejects')))},"
            + f"stale_oracle_blocks={int(_safe_float(stale.get('stale_oracle_edge_blocks')))},"
            + f"disarmed_blocks={int(_safe_float(stale.get('disarmed_edge_blocks')))}"
        ),
        (
            "pickoff="
            + f"fills_scored={int(_safe_float(pickoff.get('fills_scored')))},"
            + f"adverse_count={int(_safe_float(pickoff.get('adverse_after_fill_count')))},"
            + f"adverse_ratio={_safe_float(pickoff.get('adverse_after_fill_ratio')):.4f}"
        ),
        (
            "market_data_source="
            + f"ws_delta={int(_safe_float(market_data_source.get('book_updates_ws_delta')))},"
            + f"rest_delta={int(_safe_float(market_data_source.get('book_updates_rest_delta')))},"
            + f"rest_ratio={_safe_float(market_data_source.get('book_updates_rest_ratio')):.4f}"
        ),
        (
            "execution_quality="
            + f"fills_scored={int(_safe_float(eq.get('fills_scored')))},"
            + f"capture={_safe_float(eq.get('realized_capture')):.6f},"
            + f"adverse={_safe_float(eq.get('adverse_selection')):.6f},"
            + f"net={_safe_float(eq.get('capture_minus_adverse')):.6f}"
        ),
        f"taker_stage_net_breakout={json.dumps(taker_stage_net, sort_keys=True)}",
        f"runtime_classification={runtime_class_name or 'UNKNOWN'}",
        f"runtime_promotion_eligible={1 if runtime_promotable else 0}",
        f"primary_suppression_cause={primary_suppression_cause}",
        f"suppression_dominated_run={1 if suppression_dominated_run else 0}",
        f"execution_starvation_mode={starvation_mode}",
        f"mode_transition_count={len(mode_transitions)}",
    ]
    if mode_transitions:
        last = mode_transitions[-1]
        lines.append(
            "last_mode_transition="
            + f"{last.get('ts_utc')}:{last.get('previous_state')}->{last.get('state')}:{last.get('reason')}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro nightly soak report")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument("--summary-out", default="", help="Optional human-readable summary path")
    parser.add_argument("--run-id", required=True, help="Run_id filter")
    parser.add_argument(
        "--run-contract",
        default="",
        help="Optional run contract JSON path for deterministic replay",
    )
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file; set 0 for full-file scans",
    )
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve()
    run_id = str(args.run_id).strip() or None
    report = build_report(
        log_dir,
        run_id=run_id,
        auto_resolve_run_id=False,
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )
    summary_text = render_human_summary(report)

    json_out_path: Optional[pathlib.Path] = None
    if args.out:
        json_out_path = pathlib.Path(args.out).resolve()
        json_out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    summary_out = str(args.summary_out).strip()
    if summary_out:
        summary_path = pathlib.Path(summary_out).resolve()
    elif json_out_path is not None:
        summary_path = json_out_path.with_suffix(".txt")
    else:
        summary_path = None
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary_text, encoding="utf-8")

    print(summary_text, end="")


if __name__ == "__main__":
    main()
