#!/usr/bin/env python3
"""Analyze Chainlink vs book-move lead/lag events from execution logs."""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import defaultdict
from typing import Dict, List, Optional


def parse_float(value) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return num / den


def fmt(value: Optional[float], ndigits: int = 6) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{ndigits}f}"


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    vals = sorted(values)
    idx = (len(vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(vals) - 1)
    if hi == lo:
        return vals[lo]
    frac = idx - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze lead/lag between Chainlink ticks and book midpoint moves")
    parser.add_argument("--log-dir", default="./logs_exec", help="Directory containing events_*.jsonl")
    parser.add_argument("--pattern", default="events_*.jsonl", help="Glob pattern for events logs")
    parser.add_argument("--max-lag-ms", type=float, default=3000.0, help="Threshold for 'fast reaction' hit rate")
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve()
    files = sorted(log_dir.glob(args.pattern))
    if not files:
        print(f"No files matched {args.pattern} in {log_dir}")
        return

    lag_by_token: Dict[str, List[float]] = defaultdict(list)
    moves_by_token: Dict[str, int] = defaultdict(int)
    fast_hits_by_token: Dict[str, int] = defaultdict(int)
    missing_tick_by_token: Dict[str, int] = defaultdict(int)
    chainlink_ticks = 0

    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = rec.get("event_type")
                if event_type == "chainlink_tick":
                    chainlink_ticks += 1
                    continue
                if event_type != "leadlag_book_move":
                    continue
                token_id = str(rec.get("token_id") or "")
                if not token_id:
                    continue
                moves_by_token[token_id] += 1
                lag_ms = parse_float(rec.get("lag_ms"))
                if lag_ms is None:
                    missing_tick_by_token[token_id] += 1
                    continue
                lag_by_token[token_id].append(lag_ms)
                if lag_ms <= args.max_lag_ms:
                    fast_hits_by_token[token_id] += 1

    print(f"Analyzed {len(files)} files in {log_dir}")
    print(f"chainlink_ticks={chainlink_ticks}")
    print(
        "token_id,moves,lag_samples,missing_tick_ref,mean_lag_ms,median_lag_ms,p95_lag_ms,fast_reaction_rate"
    )

    for token_id in sorted(moves_by_token.keys()):
        lags = lag_by_token.get(token_id, [])
        moves = moves_by_token[token_id]
        miss = missing_tick_by_token.get(token_id, 0)
        mean_lag = statistics.mean(lags) if lags else None
        median_lag = statistics.median(lags) if lags else None
        p95_lag = percentile(lags, 0.95) if lags else None
        fast_rate = pct(fast_hits_by_token.get(token_id, 0), len(lags))
        print(
            ",".join(
                [
                    token_id,
                    str(moves),
                    str(len(lags)),
                    str(miss),
                    fmt(mean_lag, ndigits=3),
                    fmt(median_lag, ndigits=3),
                    fmt(p95_lag, ndigits=3),
                    fmt(fast_rate, ndigits=4),
                ]
            )
        )


if __name__ == "__main__":
    main()

