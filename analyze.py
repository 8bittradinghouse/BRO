#!/usr/bin/env python3
"""Analyze Polymarket observer JSONL logs."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import statistics
from typing import Dict, Optional


def parse_float(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: Optional[str]) -> Optional[dt.datetime]:
    if not value or not isinstance(value, str):
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


@dataclasses.dataclass
class TokenStats:
    records: int = 0
    spreads: list = dataclasses.field(default_factory=list)
    prev_spread: Optional[float] = None
    spread_transitions: int = 0
    spread_changes: int = 0

    prev_bid: Optional[float] = None
    prev_ask: Optional[float] = None
    quote_transitions: int = 0
    quote_changes: int = 0

    bid_transitions: int = 0
    bid_changes: int = 0
    ask_transitions: int = 0
    ask_changes: int = 0

    trade_events: int = 0
    prev_trade_ts: Optional[str] = None

    first_ts: Optional[dt.datetime] = None
    last_ts: Optional[dt.datetime] = None


def update_stats(stats: TokenStats, rec: dict) -> None:
    stats.records += 1

    ts = parse_ts(rec.get("ts_utc"))
    if ts is not None:
        if stats.first_ts is None or ts < stats.first_ts:
            stats.first_ts = ts
        if stats.last_ts is None or ts > stats.last_ts:
            stats.last_ts = ts

    spread = parse_float(rec.get("spread"))
    if spread is not None:
        stats.spreads.append(spread)
        if stats.prev_spread is not None:
            stats.spread_transitions += 1
            if spread != stats.prev_spread:
                stats.spread_changes += 1
        stats.prev_spread = spread

    bid = parse_float(rec.get("best_bid_price"))
    ask = parse_float(rec.get("best_ask_price"))
    prev_bid = stats.prev_bid
    prev_ask = stats.prev_ask

    if bid is not None and ask is not None and prev_bid is not None and prev_ask is not None:
        stats.quote_transitions += 1
        if bid != prev_bid or ask != prev_ask:
            stats.quote_changes += 1

    if bid is not None:
        if prev_bid is not None:
            stats.bid_transitions += 1
            if bid != prev_bid:
                stats.bid_changes += 1
        stats.prev_bid = bid

    if ask is not None:
        if prev_ask is not None:
            stats.ask_transitions += 1
            if ask != prev_ask:
                stats.ask_changes += 1
        stats.prev_ask = ask

    trade_ts = rec.get("last_trade_ts")
    if isinstance(trade_ts, str) and trade_ts:
        if trade_ts != stats.prev_trade_ts:
            stats.trade_events += 1
            stats.prev_trade_ts = trade_ts
    elif rec.get("trade_event"):
        stats.trade_events += 1


def pct(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return num / den


def fmt(value: Optional[float], ndigits: int = 6) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{ndigits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze observer JSONL logs")
    parser.add_argument("--log-dir", default="./logs", help="Directory containing market_*.jsonl")
    parser.add_argument("--pattern", default="market_*.jsonl", help="Glob pattern to select market logs")
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve()
    files = sorted(log_dir.glob(args.pattern))
    if not files:
        print(f"No files matched {args.pattern} in {log_dir}")
        return

    by_token: Dict[str, TokenStats] = {}

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
                token_id = str(rec.get("token_id") or "")
                if not token_id:
                    continue
                stats = by_token.setdefault(token_id, TokenStats())
                update_stats(stats, rec)

    if not by_token:
        print("No usable token records found")
        return

    print(f"Analyzed {len(files)} files in {log_dir}")
    print(
        "token_id,records,mean_spread,median_spread,spread_change_rate,spread_same_rate,trades_per_min,quote_change_rate,bid_change_rate,ask_change_rate"
    )

    for token_id in sorted(by_token.keys()):
        stats = by_token[token_id]

        mean_spread = statistics.mean(stats.spreads) if stats.spreads else None
        median_spread = statistics.median(stats.spreads) if stats.spreads else None

        spread_change_rate = pct(stats.spread_changes, stats.spread_transitions)
        spread_same_rate = None if spread_change_rate is None else (1.0 - spread_change_rate)

        duration_min = None
        if stats.first_ts is not None and stats.last_ts is not None:
            duration_min = (stats.last_ts - stats.first_ts).total_seconds() / 60.0

        trades_per_min = None
        if duration_min is not None and duration_min > 0:
            trades_per_min = stats.trade_events / duration_min

        quote_change_rate = pct(stats.quote_changes, stats.quote_transitions)
        bid_change_rate = pct(stats.bid_changes, stats.bid_transitions)
        ask_change_rate = pct(stats.ask_changes, stats.ask_transitions)

        print(
            ",".join(
                [
                    token_id,
                    str(stats.records),
                    fmt(mean_spread),
                    fmt(median_spread),
                    fmt(spread_change_rate),
                    fmt(spread_same_rate),
                    fmt(trades_per_min),
                    fmt(quote_change_rate),
                    fmt(bid_change_rate),
                    fmt(ask_change_rate),
                ]
            )
        )


if __name__ == "__main__":
    main()
