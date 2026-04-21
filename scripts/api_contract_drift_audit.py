#!/usr/bin/env python3
"""Lightweight API contract drift audit over representative payload samples."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Optional


from prodesk.error_codes import summarize_error_codes


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_first_dict(items: Iterable[Any]) -> Dict[str, Any]:
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _extract_chainlink_payload(sample: Any) -> Dict[str, Any]:
    obj = _as_dict(sample)
    payload = obj.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    payload_obj = _as_dict(payload)
    if payload_obj:
        if isinstance(payload_obj.get("data"), list):
            first_point = _pick_first_dict(payload_obj.get("data") or [])
            if first_point:
                merged = dict(payload_obj)
                merged.update(first_point)
                return merged
        return payload_obj
    data_obj = _as_dict(obj.get("data"))
    if data_obj:
        return data_obj
    return obj


def _extract_book_payload(sample: Any) -> Dict[str, Any]:
    obj = _as_dict(sample)
    payload = obj.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    payload_obj = _as_dict(payload)
    if payload_obj:
        return payload_obj
    data_obj = _as_dict(obj.get("data"))
    if data_obj:
        return data_obj
    return obj


def _require_nonempty(
    findings: List[str],
    *,
    sample_name: str,
    field_name: str,
    value: Any,
) -> None:
    if value is None:
        findings.append(f"api_contract_missing_field:{sample_name}:{field_name}")
        return
    if isinstance(value, str) and not value.strip():
        findings.append(f"api_contract_missing_field:{sample_name}:{field_name}")


def _require_numeric(
    findings: List[str],
    *,
    sample_name: str,
    field_name: str,
    value: Any,
) -> None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        findings.append(f"api_contract_type_mismatch:{sample_name}:{field_name}:numeric")
        return
    if out != out:
        findings.append(f"api_contract_type_mismatch:{sample_name}:{field_name}:numeric")


def run_audit(*, samples_path: pathlib.Path) -> Dict[str, Any]:
    findings: List[str] = []
    payload = json.loads(samples_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("samples root must be an object")

    orders_raw = payload.get("polymarket_orders")
    trades_raw = payload.get("polymarket_trades")
    chainlink_raw = payload.get("chainlink_message")
    book_raw = payload.get("book_feed_message")

    if orders_raw is None:
        findings.append("api_contract_missing_sample:polymarket_orders")
    if trades_raw is None:
        findings.append("api_contract_missing_sample:polymarket_trades")
    if chainlink_raw is None:
        findings.append("api_contract_missing_sample:chainlink_message")
    if book_raw is None:
        findings.append("api_contract_missing_sample:book_feed_message")

    order = _pick_first_dict(_as_list(orders_raw))
    if order:
        for field in ("id", "asset_id", "side", "status"):
            _require_nonempty(findings, sample_name="polymarket_orders", field_name=field, value=order.get(field))
        for field in ("price", "size"):
            _require_numeric(findings, sample_name="polymarket_orders", field_name=field, value=order.get(field))

    trade = _pick_first_dict(_as_list(trades_raw))
    if trade:
        for field in ("id", "asset_id", "side"):
            _require_nonempty(findings, sample_name="polymarket_trades", field_name=field, value=trade.get(field))
        for field in ("price", "size", "timestamp"):
            _require_numeric(findings, sample_name="polymarket_trades", field_name=field, value=trade.get(field))

    chain = _extract_chainlink_payload(chainlink_raw)
    if chain:
        _require_nonempty(findings, sample_name="chainlink_message", field_name="symbol", value=chain.get("symbol"))
        _require_numeric(findings, sample_name="chainlink_message", field_name="value", value=chain.get("value"))
        _require_nonempty(findings, sample_name="chainlink_message", field_name="timestamp", value=chain.get("timestamp"))

    book = _extract_book_payload(book_raw)
    if book:
        _require_nonempty(findings, sample_name="book_feed_message", field_name="asset_id", value=book.get("asset_id"))
        bids = book.get("bids")
        asks = book.get("asks")
        if not isinstance(bids, list):
            findings.append("api_contract_type_mismatch:book_feed_message:bids:list")
        if not isinstance(asks, list):
            findings.append("api_contract_type_mismatch:book_feed_message:asks:list")

    unique_findings = sorted(set(findings))
    return {
        "samples_path": str(samples_path.resolve()),
        "finding_count": len(unique_findings),
        "findings": unique_findings,
        "error_codes": summarize_error_codes(unique_findings),
        "ok": len(unique_findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro API contract drift audit")
    parser.add_argument("--samples", required=True, help="JSON file with representative payload samples")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(samples_path=pathlib.Path(args.samples).resolve())
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
