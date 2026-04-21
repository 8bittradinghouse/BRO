from __future__ import annotations

import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from .common import first_non_none, parse_float, utc_iso
from .http_session import build_hardened_session
from .models import BookTop


def _parse_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _best_level(levels: Any, is_bid: bool) -> Tuple[Optional[float], Optional[float]]:
    best_price: Optional[float] = None
    best_size: Optional[float] = None
    for row in _parse_json_list(levels):
        price = None
        size = None
        if isinstance(row, dict):
            price = parse_float(first_non_none(row.get("price"), row.get("p"), row.get("px")))
            size = parse_float(
                first_non_none(
                    row.get("size"),
                    row.get("s"),
                    row.get("quantity"),
                    row.get("amount"),
                    row.get("shares"),
                )
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = parse_float(row[0])
            size = parse_float(row[1])
        if price is None:
            continue
        if best_price is None:
            best_price, best_size = price, size
            continue
        if is_bid and price > best_price:
            best_price, best_size = price, size
        if (not is_bid) and price < best_price:
            best_price, best_size = price, size
    return best_price, best_size


def _http_get_json(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout_sec: float,
    max_retries: int,
) -> Dict[str, Any]:
    delay = 0.4
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout_sec)
        except requests.RequestException:
            if attempt >= max_retries:
                raise
            time.sleep(delay)
            delay = min(4.0, delay * 2.0)
            continue
        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()
            retry_after = parse_float(resp.headers.get("Retry-After"))
            sleep_for = delay if retry_after is None else max(0.0, min(retry_after, 30.0))
            time.sleep(sleep_for)
            delay = min(8.0, delay * 2.0)
            continue
        if resp.status_code >= 500 and attempt < max_retries:
            time.sleep(delay)
            delay = min(4.0, delay * 2.0)
            continue
        resp.raise_for_status()
        try:
            parsed = resp.json()
        except ValueError:
            if attempt >= max_retries:
                raise RuntimeError(f"invalid JSON payload from {url}")
            time.sleep(delay)
            delay = min(4.0, delay * 2.0)
            continue
        if not isinstance(parsed, dict):
            raise RuntimeError(f"unexpected payload type for {url}")
        return parsed
    raise RuntimeError(f"exhausted retries for {url}")


class RestBookClient:
    def __init__(self, clob_url: str, book_path: str, timeout_sec: float, max_retries: int):
        self.url = f"{clob_url.rstrip('/')}{book_path}"
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._thread_local = threading.local()
        self._sessions: List[requests.Session] = []
        self._sessions_lock = threading.Lock()

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = build_hardened_session(user_agent="polymarket-bro-executor/0.1")
            self._thread_local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions = []
        for session in sessions:
            try:
                session.close()
            except (OSError, RuntimeError):
                continue

    def fetch_book(self, token_id: str) -> Tuple[BookTop, Dict[str, Any]]:
        payload = _http_get_json(
            self._session(),
            self.url,
            params={"token_id": token_id},
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
        )
        bid_price, bid_size = _best_level(first_non_none(payload.get("bids"), payload.get("buys")), is_bid=True)
        ask_price, ask_size = _best_level(first_non_none(payload.get("asks"), payload.get("sells")), is_bid=False)

        best_bid = parse_float(first_non_none(payload.get("best_bid"), payload.get("bestBid")))
        best_ask = parse_float(first_non_none(payload.get("best_ask"), payload.get("bestAsk")))
        best_bid_size = parse_float(first_non_none(payload.get("best_bid_size"), payload.get("bestBidSize")))
        best_ask_size = parse_float(first_non_none(payload.get("best_ask_size"), payload.get("bestAskSize")))

        if best_bid is not None:
            bid_price = best_bid
        if best_ask is not None:
            ask_price = best_ask
        if best_bid_size is not None:
            bid_size = best_bid_size
        if best_ask_size is not None:
            ask_size = best_ask_size

        top = BookTop(
            token_id=token_id,
            ts_utc=utc_iso(),
            source="rest",
            best_bid_price=bid_price,
            best_bid_size=bid_size,
            best_ask_price=ask_price,
            best_ask_size=ask_size,
        )
        return top, payload
