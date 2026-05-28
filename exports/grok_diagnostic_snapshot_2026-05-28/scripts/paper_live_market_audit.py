#!/usr/bin/env python3
"""Run or attach to BRO paper and audit its owned market against live public books."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = (ROOT_DIR / "logs_exec" / "paper_universal").resolve()
DEFAULT_OUT_ROOT = (ROOT_DIR / "tmp" / "paper_live_market_audit").resolve()
PUBLIC_HEADERS = {
    "User-Agent": "8bit-ODA-Jin/1.0",
    "Accept": "application/json",
}
EVENT_TYPES_WITH_SNAPSHOTS = {
    "lifecycle_phase_transition",
    "taker_decision",
    "order_submit",
    "fill",
    "order_cancel",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(value: Optional[dt.datetime] = None) -> str:
    ts = value or utc_now()
    return ts.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def snapshot_dir_name(value: dt.datetime) -> str:
    return f"snapshot_{value.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"


def sanitize_name(value: str) -> str:
    text = str(value or "").strip()
    safe = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_", "."}:
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "item"


def parse_owned_market_ref(value: Any) -> Optional[Dict[str, str]]:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split("|")
    condition_id = str(parts[0] or "").strip()
    if not condition_id.startswith("0x"):
        return None
    return {
        "condition_id": condition_id,
        "window_ts_utc": str(parts[1] or "").strip() if len(parts) > 1 else "",
        "suffix": str(parts[2] or "").strip() if len(parts) > 2 else "",
        "owned_market_ref": text,
    }


def normalize_outcome_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"YES", "UP"}:
        return "YES"
    if text in {"NO", "DOWN"}:
        return "NO"
    return text


def outcome_hint_from_market_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    suffix = text.rsplit("|", 1)[-1]
    return normalize_outcome_label(suffix)


def summarize_book(book_payload: Dict[str, Any]) -> Dict[str, Any]:
    bids = book_payload.get("bids") if isinstance(book_payload, dict) else None
    asks = book_payload.get("asks") if isinstance(book_payload, dict) else None
    bid_levels = list(bids or [])
    ask_levels = list(asks or [])
    best_bid = None
    best_ask = None
    if bid_levels:
        best_bid = max(
            bid_levels,
            key=lambda row: float(str((row or {}).get("price") or 0.0)),
        )
    if ask_levels:
        best_ask = min(
            ask_levels,
            key=lambda row: float(str((row or {}).get("price") or 0.0)),
        )
    return {
        "bid_levels": len(bid_levels),
        "ask_levels": len(ask_levels),
        "top_bid": best_bid,
        "top_ask": best_ask,
    }


def books_all_404(results: Dict[str, Dict[str, Any]]) -> bool:
    if not results:
        return False
    return all(int((payload or {}).get("status_code") or 0) == 404 for payload in results.values())


def should_snapshot_for_event(event: Dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "").strip()
    if event_type in EVENT_TYPES_WITH_SNAPSHOTS:
        return True
    if event_type == "edge_evaluation" and str(event.get("action_taken") or "").strip().lower() not in {"", "none"}:
        return True
    return False


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def load_json(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_json(url: str, *, timeout_sec: float = 8.0) -> Tuple[Optional[Dict[str, Any]], int, str]:
    request = urllib.request.Request(url, headers=PUBLIC_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return None, int(exc.code or 0), body.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - audit tool should surface exact failure
        return None, 0, f"{exc.__class__.__name__}:{exc}"
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None, status, body.decode("utf-8", errors="replace")
    if isinstance(payload, dict):
        return payload, status, ""
    return {"payload": payload}, status, ""


def clob_market_metadata_url(condition_id: str) -> str:
    return f"https://clob.polymarket.com/markets/{condition_id}"


def clob_book_url(token_id: str) -> str:
    query = urllib.parse.urlencode({"token_id": token_id})
    return f"https://clob.polymarket.com/book?{query}"


@dataclass
class ConditionAuditState:
    condition_id: str
    owned_market_ref: str
    market_slug: str
    question: str
    dir: pathlib.Path
    tokens: List[str]
    first_event_type: str
    first_seen_utc: str
    last_snapshot_utc: str = ""
    snapshots: int = 0
    closed: bool = False
    close_reason: str = ""
    close_detected_utc: str = ""
    last_snapshot_reason: str = ""
    metadata_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "owned_market_ref": self.owned_market_ref,
            "market_slug": self.market_slug,
            "question": self.question,
            "dir": str(self.dir),
            "tokens": list(self.tokens),
            "first_event_type": self.first_event_type,
            "first_seen_utc": self.first_seen_utc,
            "last_snapshot_utc": self.last_snapshot_utc,
            "snapshots": self.snapshots,
            "closed": self.closed,
            "close_reason": self.close_reason,
            "close_detected_utc": self.close_detected_utc,
            "last_snapshot_reason": self.last_snapshot_reason,
            "metadata_path": self.metadata_path,
        }


class JsonlRunTail:
    def __init__(self, *, log_dir: pathlib.Path, stem: str, out_path: pathlib.Path, run_id: str):
        self.log_dir = log_dir
        self.stem = stem
        self.out_path = out_path
        self.run_id = run_id
        self.file_positions: Dict[pathlib.Path, int] = {}
        self.seen_hashes: Set[str] = set()
        self.matched_count = 0

    def _candidate_files(self) -> List[pathlib.Path]:
        return sorted(self.log_dir.glob(f"{self.stem}_*.jsonl"))

    def initialize(self) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for path in self._candidate_files():
            matched.extend(self._scan_file(path, start_pos=0))
            self.file_positions[path] = path.stat().st_size if path.exists() else 0
        return matched

    def poll(self) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for path in self._candidate_files():
            start_pos = self.file_positions.get(path)
            if start_pos is None:
                start_pos = 0
            matched.extend(self._scan_file(path, start_pos=start_pos))
            self.file_positions[path] = path.stat().st_size if path.exists() else 0
        return matched

    def _scan_file(self, path: pathlib.Path, *, start_pos: int) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        if not path.exists():
            return matches
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(start_pos)
            for raw_line in handle:
                if self.run_id not in raw_line:
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if str(payload.get("run_id") or "").strip() != self.run_id:
                    continue
                digest = hashlib.sha1(raw_line.encode("utf-8")).hexdigest()
                if digest in self.seen_hashes:
                    continue
                self.seen_hashes.add(digest)
                append_jsonl(self.out_path, payload)
                self.matched_count += 1
                matches.append(payload)
        return matches


class PaperLiveMarketAudit:
    def __init__(
        self,
        *,
        log_dir: pathlib.Path,
        out_dir: pathlib.Path,
        run_id: str,
        snapshot_cadence_sec: float,
    ) -> None:
        self.log_dir = log_dir
        self.out_dir = out_dir
        self.run_id = run_id
        self.snapshot_cadence_sec = snapshot_cadence_sec
        self.conditions: Dict[str, ConditionAuditState] = {}
        self.order_condition_ids: Dict[str, str] = {}
        self.events_tail = JsonlRunTail(
            log_dir=self.log_dir,
            stem="events",
            out_path=self.out_dir / "matching_events.jsonl",
            run_id=self.run_id,
        )
        self.status_tail = JsonlRunTail(
            log_dir=self.log_dir,
            stem="status",
            out_path=self.out_dir / "matching_status.jsonl",
            run_id=self.run_id,
        )
        self.errors_tail = JsonlRunTail(
            log_dir=self.log_dir,
            stem="errors",
            out_path=self.out_dir / "matching_errors.jsonl",
            run_id=self.run_id,
        )
        self.last_cadence_monotonic = 0.0
        self.last_bro_activity_monotonic = time.monotonic()
        self.market_terminal_monotonic = 0.0
        self.market_terminal_reason = ""

    def initialize(self) -> None:
        initial_events = self.events_tail.initialize()
        self.status_tail.initialize()
        self.errors_tail.initialize()
        for event in initial_events:
            self._handle_event(event, initial_backfill=True)
        if initial_events:
            self.last_bro_activity_monotonic = time.monotonic()
        self._write_session_state()
        self._emit_stdout({"run_id": self.run_id, "events_attached": self.events_tail.matched_count})

    def poll(self) -> None:
        new_events = self.events_tail.poll()
        self.status_tail.poll()
        self.errors_tail.poll()
        for event in new_events:
            self._handle_event(event, initial_backfill=False)
        self._capture_cadence_snapshots()
        if new_events:
            self.last_bro_activity_monotonic = time.monotonic()
        self._write_session_state()

    def should_exit_early(self, *, idle_sec: float) -> bool:
        if idle_sec <= 0.0 or self.market_terminal_monotonic <= 0.0:
            return False
        return (time.monotonic() - self.market_terminal_monotonic) >= idle_sec

    def _handle_event(self, event: Dict[str, Any], *, initial_backfill: bool) -> None:
        owned = parse_owned_market_ref(event.get("owned_market_ref"))
        condition: Optional[ConditionAuditState] = None
        if owned:
            condition = self._ensure_condition_state(event=event, owned=owned)
        else:
            condition = self._infer_condition_from_event(event)
        if condition is None:
            return
        event_type = str(event.get("event_type") or "").strip()
        phase = ""
        if event_type == "lifecycle_phase_transition":
            phase = str(event.get("lifecycle_phase") or "").strip()
            if phase == "scan":
                self.market_terminal_monotonic = time.monotonic()
                self.market_terminal_reason = "transition_scan"
        self._remember_order_binding(event=event, condition=condition)
        if not initial_backfill and should_snapshot_for_event(event):
            reason = self._event_snapshot_reason(event)
            self._capture_condition_snapshot(condition, reason=reason, trigger_event=event)
        if event_type == "lifecycle_phase_transition" and phase == "scan":
            self._close_condition_from_bro_transition(condition=condition, event=event)

    def _infer_condition_from_event(self, event: Dict[str, Any]) -> Optional[ConditionAuditState]:
        order_id = str(event.get("order_id") or "").strip()
        if order_id:
            bound_condition_id = self.order_condition_ids.get(order_id, "")
            if bound_condition_id:
                bound_condition = self.conditions.get(bound_condition_id)
                if bound_condition is not None:
                    return bound_condition
        direct_candidates = {
            str(event.get("market_id") or "").strip(),
            str(event.get("token_id") or "").strip(),
            str(event.get("submit_token_id") or "").strip(),
        }
        direct_candidates.discard("")
        if direct_candidates:
            matched = [
                condition
                for condition in self.conditions.values()
                if any(candidate in set(condition.tokens) for candidate in direct_candidates)
            ]
            if len(matched) == 1:
                return matched[0]
        active_conditions = [condition for condition in self.conditions.values() if not condition.closed]
        if len(active_conditions) == 1 and str(event.get("event_type") or "").strip() in EVENT_TYPES_WITH_SNAPSHOTS:
            return active_conditions[0]
        return None

    def _remember_order_binding(self, *, event: Dict[str, Any], condition: ConditionAuditState) -> None:
        order_id = str(event.get("order_id") or "").strip()
        if not order_id:
            return
        self.order_condition_ids[order_id] = condition.condition_id

    def _ensure_condition_state(self, *, event: Dict[str, Any], owned: Dict[str, str]) -> ConditionAuditState:
        condition_id = owned["condition_id"]
        existing = self.conditions.get(condition_id)
        if existing is not None:
            return existing
        metadata, status_code, error_text = fetch_json(clob_market_metadata_url(condition_id))
        if status_code >= 400 or metadata is None:
            market_slug = condition_id
            question = ""
            tokens: List[str] = []
            metadata_payload: Dict[str, Any] = {
                "condition_id": condition_id,
                "fetch_status_code": status_code,
                "fetch_error": error_text,
            }
        else:
            market_slug = str(metadata.get("market_slug") or condition_id).strip() or condition_id
            question = str(metadata.get("question") or "").strip()
            tokens = [str(row.get("token_id") or "").strip() for row in list(metadata.get("tokens") or []) if str(row.get("token_id") or "").strip()]
            metadata_payload = metadata
        market_dir = self.out_dir / f"market_{len(self.conditions) + 1}_{sanitize_name(market_slug)}"
        market_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = market_dir / "market_metadata.json"
        write_json(metadata_path, metadata_payload)
        self.market_terminal_monotonic = 0.0
        self.market_terminal_reason = ""
        state = ConditionAuditState(
            condition_id=condition_id,
            owned_market_ref=owned["owned_market_ref"],
            market_slug=market_slug,
            question=question,
            dir=market_dir,
            tokens=tokens,
            first_event_type=str(event.get("event_type") or "").strip(),
            first_seen_utc=str(event.get("ts_utc") or event.get("ts_event_utc") or utc_iso()).strip(),
            metadata_path=str(metadata_path),
        )
        self.conditions[condition_id] = state
        self._emit_stdout(
            {
                "condition_attached": condition_id,
                "market_slug": market_slug,
                "tokens": tokens,
            }
        )
        return state

    def _capture_cadence_snapshots(self) -> None:
        now_mono = time.monotonic()
        if self.last_cadence_monotonic and (now_mono - self.last_cadence_monotonic) < self.snapshot_cadence_sec:
            return
        self.last_cadence_monotonic = now_mono
        for condition in self.conditions.values():
            if condition.closed:
                continue
            self._capture_condition_snapshot(condition, reason="cadence", trigger_event=None)

    def _capture_condition_snapshot(
        self,
        condition: ConditionAuditState,
        *,
        reason: str,
        trigger_event: Optional[Dict[str, Any]],
    ) -> None:
        if condition.closed:
            return
        snapshot_ts = utc_now()
        snapshot_dir = condition.dir / snapshot_dir_name(snapshot_ts)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw_books: Dict[str, Dict[str, Any]] = {}
        summaries: Dict[str, Any] = {}
        for token_id in condition.tokens:
            payload, status_code, error_text = fetch_json(clob_book_url(token_id))
            payload_to_write: Dict[str, Any]
            if payload is None:
                payload_to_write = {
                    "token_id": token_id,
                    "status_code": status_code,
                    "error": error_text,
                }
            else:
                payload_to_write = dict(payload)
                payload_to_write["status_code"] = status_code
            raw_books[token_id] = payload_to_write
            write_json(snapshot_dir / f"book_{token_id}.json", payload_to_write)
            summaries[token_id] = summarize_book(payload_to_write)
            summaries[token_id]["status_code"] = int(status_code or 0)
        summary_payload = {
            "captured_at_utc": utc_iso(snapshot_ts),
            "condition_id": condition.condition_id,
            "reason": reason,
            "books": summaries,
            "bro_event": self._bro_event_summary(event=trigger_event, condition=condition),
        }
        write_json(snapshot_dir / "summary.json", summary_payload)
        condition.snapshots += 1
        condition.last_snapshot_utc = summary_payload["captured_at_utc"]
        condition.last_snapshot_reason = reason
        if books_all_404(raw_books):
            condition.closed = True
            condition.close_reason = "public_book_404_closed_condition"
            condition.close_detected_utc = summary_payload["captured_at_utc"]
            self.market_terminal_monotonic = time.monotonic()
            self.market_terminal_reason = condition.close_reason
        self._emit_stdout(
            {
                "snapshot": {
                    "condition_id": condition.condition_id,
                    "market_slug": condition.market_slug,
                    "reason": reason,
                    "closed": condition.closed,
                    "captured_at_utc": summary_payload["captured_at_utc"],
                }
            }
        )

    def _close_condition_from_bro_transition(
        self,
        *,
        condition: ConditionAuditState,
        event: Dict[str, Any],
    ) -> None:
        if condition.closed:
            return
        close_ts = str(event.get("ts_event_utc") or event.get("ts_utc") or utc_iso()).strip() or utc_iso()
        condition.closed = True
        condition.close_reason = "bro_transition_scan"
        condition.close_detected_utc = close_ts
        self.market_terminal_monotonic = time.monotonic()
        self.market_terminal_reason = condition.close_reason

    def _bro_event_summary(
        self,
        *,
        event: Optional[Dict[str, Any]],
        condition: ConditionAuditState,
    ) -> Dict[str, Any]:
        if event is None:
            return {}
        token_id = self._token_id_from_event(event=event, condition=condition)
        market_key = str(event.get("market_key") or "").strip()
        return {
            "event_type": str(event.get("event_type") or "").strip(),
            "ts_event_utc": str(event.get("ts_event_utc") or event.get("ts_utc") or "").strip(),
            "lifecycle_phase": str(event.get("lifecycle_phase") or "").strip(),
            "lineage_stage": str(event.get("lineage_stage") or "").strip(),
            "evaluation_scope": str(event.get("evaluation_scope") or "").strip(),
            "submission_lane": str(event.get("submission_lane") or "").strip(),
            "side": str(event.get("side") or "").strip(),
            "price": event.get("price"),
            "size": event.get("size"),
            "market_key": market_key,
            "outcome_hint": outcome_hint_from_market_key(market_key),
            "token_id": token_id,
            "block_reason": str(event.get("block_reason") or "").strip(),
            "maker_gate_reason": str(event.get("maker_gate_reason") or "").strip(),
            "taker_submit_reject_reason": str(event.get("taker_submit_reject_reason") or "").strip(),
            "action_taken": str(event.get("action_taken") or "").strip(),
            "fair_probability": event.get("fair_probability"),
            "market_probability": event.get("market_probability"),
            "edge_value": event.get("edge_value"),
            "edge": event.get("edge"),
            "target_ref": str(event.get("target_ref") or "").strip(),
            "owned_market_ref": str(event.get("owned_market_ref") or "").strip(),
        }

    def _token_id_from_event(self, *, event: Dict[str, Any], condition: ConditionAuditState) -> str:
        direct_candidates = [
            str(event.get("market_id") or "").strip(),
            str(event.get("token_id") or "").strip(),
            str(event.get("submit_token_id") or "").strip(),
        ]
        token_set = set(condition.tokens)
        for candidate in direct_candidates:
            if candidate and candidate in token_set:
                return candidate
        outcome_hint = outcome_hint_from_market_key(event.get("market_key"))
        if outcome_hint:
            metadata = load_json(pathlib.Path(condition.metadata_path))
            for row in list(metadata.get("tokens") or []):
                row_token_id = str(row.get("token_id") or "").strip()
                if not row_token_id:
                    continue
                if normalize_outcome_label(row.get("outcome")) == outcome_hint:
                    return row_token_id
        return ""

    def _event_snapshot_reason(self, event: Dict[str, Any]) -> str:
        event_type = str(event.get("event_type") or "").strip()
        if event_type == "lifecycle_phase_transition":
            phase = str(event.get("lifecycle_phase") or "").strip()
            return f"transition_{phase}" if phase else "transition"
        lane = str(event.get("submission_lane") or "").strip()
        return f"{event_type}_{lane}" if lane else event_type

    def _write_session_state(self) -> None:
        payload = {
            "run_id": self.run_id,
            "out_dir": str(self.out_dir),
            "started_at_utc": str(load_json(self.out_dir / "run_manifest.json").get("started_at_utc") or ""),
            "events_files": [str(path) for path in self.events_tail.file_positions.keys()],
            "status_files": [str(path) for path in self.status_tail.file_positions.keys()],
            "errors_files": [str(path) for path in self.errors_tail.file_positions.keys()],
            "conditions": {cid: state.to_dict() for cid, state in self.conditions.items()},
            "matching_counts": {
                "events": self.events_tail.matched_count,
                "status": self.status_tail.matched_count,
                "errors": self.errors_tail.matched_count,
            },
        }
        write_json(self.out_dir / "session_state.json", payload)

    @staticmethod
    def _emit_stdout(payload: Dict[str, Any]) -> None:
        print(json.dumps(payload, sort_keys=True), flush=True)


def find_new_manifest(*, log_dir: pathlib.Path, known_paths: Set[pathlib.Path], timeout_sec: float) -> pathlib.Path:
    deadline = time.monotonic() + max(1.0, timeout_sec)
    while time.monotonic() <= deadline:
        manifests = sorted(log_dir.glob("run_manifest_*.json"), key=lambda p: p.stat().st_mtime)
        for manifest in manifests:
            if manifest in known_paths:
                continue
            return manifest
        time.sleep(0.5)
    raise RuntimeError("run_manifest_not_observed_for_new_launch")


def launch_broctl(
    *,
    active_minutes: int,
    wait_sec: int,
    out_dir: pathlib.Path,
    do_build: bool,
) -> Tuple[subprocess.Popen[Any], pathlib.Path]:
    stdout_path = out_dir / "broctl_stdout.log"
    handle = stdout_path.open("w", encoding="utf-8")
    cmd = ["broctl", "paper", "--", "--active-minutes", str(active_minutes), "--wait-sec", str(wait_sec)]
    cmd.append("--build" if do_build else "--no-build")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc, stdout_path


def build_run_manifest_payload(*, run_id: str, out_dir: pathlib.Path, manifest_path: pathlib.Path, stdout_path: Optional[pathlib.Path]) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "broctl_stdout_log": str(stdout_path) if stdout_path is not None else "",
        "started_at_utc": utc_iso(),
    }


def run_audit(
    *,
    active_minutes: int,
    wait_sec: int,
    log_dir: pathlib.Path,
    out_root: pathlib.Path,
    snapshot_cadence_sec: float,
    run_id: str,
    proc: Optional[subprocess.Popen[Any]],
    stdout_path: Optional[pathlib.Path],
    manifest_path: pathlib.Path,
) -> pathlib.Path:
    close_idle_sec = 3.0
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    out_dir = (out_root / stamp).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        out_dir / "run_manifest.json",
        build_run_manifest_payload(
            run_id=run_id,
            out_dir=out_dir,
            manifest_path=manifest_path,
            stdout_path=stdout_path,
        ),
    )
    audit = PaperLiveMarketAudit(
        log_dir=log_dir,
        out_dir=out_dir,
        run_id=run_id,
        snapshot_cadence_sec=snapshot_cadence_sec,
    )
    audit.initialize()
    drain_until = 0.0
    while True:
        audit.poll()
        if proc is None and audit.should_exit_early(idle_sec=close_idle_sec):
            break
        if proc is None:
            time.sleep(0.5)
            continue
        ret = proc.poll()
        if ret is None:
            time.sleep(0.5)
            continue
        if drain_until == 0.0:
            drain_until = time.monotonic() + 5.0
        if time.monotonic() >= drain_until:
            break
        time.sleep(0.5)
    audit.poll()
    write_json(
        out_dir / "final_summary.json",
        {
            "run_id": run_id,
            "conditions_seen": len(audit.conditions),
            "matching_counts": {
                "events": audit.events_tail.matched_count,
                "status": audit.status_tail.matched_count,
                "errors": audit.errors_tail.matched_count,
            },
            "conditions": {cid: state.to_dict() for cid, state in audit.conditions.items()},
        },
    )
    return out_dir


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-minutes", type=int, default=10, help="Canonical paper active minutes when launching BRO.")
    parser.add_argument("--wait-sec", type=int, default=25, help="Canonical paper wait seconds when launching BRO.")
    parser.add_argument(
        "--build",
        dest="build_images",
        action="store_true",
        help="Build docker images during launch (default behavior).",
    )
    parser.add_argument(
        "--no-build",
        dest="build_images",
        action="store_false",
        help="Skip docker image build during launch and use the fast canonical path.",
    )
    parser.set_defaults(build_images=True)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Canonical paper log directory.")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="Root directory for audit artifacts.")
    parser.add_argument("--snapshot-cadence-sec", type=float, default=5.0, help="Periodic public book snapshot cadence.")
    parser.add_argument("--run-id", default="", help="Attach to an existing run_id instead of launching broctl.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    log_dir = pathlib.Path(args.log_dir).resolve()
    out_root = pathlib.Path(args.out_root).resolve()
    run_id = str(args.run_id or "").strip()
    proc: Optional[subprocess.Popen[Any]] = None
    stdout_path: Optional[pathlib.Path] = None
    manifest_path: Optional[pathlib.Path] = None

    if run_id:
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        if not manifest_path.exists():
            raise RuntimeError(f"run_manifest_missing_for_run_id:{manifest_path}")
    else:
        known_manifests = set(log_dir.glob("run_manifest_*.json"))
        out_root.mkdir(parents=True, exist_ok=True)
        launch_dir = out_root / "launch_tmp"
        launch_dir.mkdir(parents=True, exist_ok=True)
        proc, stdout_path = launch_broctl(
            active_minutes=args.active_minutes,
            wait_sec=args.wait_sec,
            out_dir=launch_dir,
            do_build=bool(args.build_images),
        )
        manifest_path = find_new_manifest(
            log_dir=log_dir,
            known_paths=known_manifests,
            timeout_sec=max(60.0, float(args.active_minutes * 60)),
        )
        run_id = str(load_json(manifest_path).get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError(f"run_id_missing_in_manifest:{manifest_path}")
    print(json.dumps({"run_id": run_id, "manifest_path": str(manifest_path)}, sort_keys=True), flush=True)
    out_dir = run_audit(
        active_minutes=args.active_minutes,
        wait_sec=args.wait_sec,
        log_dir=log_dir,
        out_root=out_root,
        snapshot_cadence_sec=float(args.snapshot_cadence_sec),
        run_id=run_id,
        proc=proc,
        stdout_path=stdout_path,
        manifest_path=manifest_path,
    )
    print(json.dumps({"completed": True, "out_dir": str(out_dir), "run_id": run_id}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
