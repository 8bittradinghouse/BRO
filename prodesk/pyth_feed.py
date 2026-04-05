from __future__ import annotations

import dataclasses
import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .common import parse_float, utc_iso


@dataclasses.dataclass
class PythTick:
    symbol: str
    price: float
    source_ts_utc: Optional[str]
    received_ts_utc: str
    received_monotonic: float
    source: str = "pyth_rest"


class PythFeed:
    """Best-effort secondary-oracle adapter with deterministic fail-closed semantics.

    When data is missing/stale/unparseable, callers observe no latest tick and can
    explicitly classify the secondary signal as unknown without inference.
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.rest_url = str(
            cfg.get(
                "rest_url",
                "https://hermes.pyth.network/v2/updates/price/latest?ids=%5B%22Crypto.BTC%2FUSD%22%5D",
            )
        ).strip()
        self.symbol = str(cfg.get("symbol", "BTC/USD")).strip() or "BTC/USD"
        self.request_timeout_sec = max(0.1, float(cfg.get("request_timeout_sec", 1.5)))
        self.poll_interval_sec = max(0.1, float(cfg.get("poll_interval_sec", 0.5)))
        self.max_tick_age_sec = max(0.1, float(cfg.get("max_tick_age_sec", 15.0)))

        self._latest: Optional[PythTick] = None
        self._next_refresh_mono: float = 0.0
        self._connected: bool = False
        self._last_error: Optional[str] = None
        self._requests: int = 0
        self._errors: int = 0

    def start(self) -> None:
        # No background thread. refresh() is called from the main cycle.
        return

    def stop(self) -> None:
        return

    def refresh(self) -> None:
        if not self.enabled:
            return
        now_mono = time.monotonic()
        if now_mono < self._next_refresh_mono:
            return
        self._next_refresh_mono = now_mono + float(self.poll_interval_sec)
        self._requests += 1
        try:
            url = self._build_url(symbol=self.symbol)
            req = urllib.request.Request(url=url, method="GET")
            with urllib.request.urlopen(req, timeout=self.request_timeout_sec) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8", errors="ignore"))
            tick = self._parse_payload(payload)
            if tick is None:
                self._connected = False
                self._latest = None
                self._last_error = "parse_failed"
                self._errors += 1
                return
            self._latest = tick
            self._connected = True
            self._last_error = None
        except Exception as exc:
            self._connected = False
            self._latest = None
            self._last_error = str(exc)
            self._errors += 1

    def get_latest(self, symbol: str) -> Optional[PythTick]:
        if not self.enabled:
            return None
        sym = str(symbol or "").strip().upper()
        expected = str(self.symbol or "").strip().upper()
        if sym and expected and sym != expected:
            return None
        latest = self._latest
        if latest is None:
            return None
        age_sec = max(0.0, time.monotonic() - float(latest.received_monotonic))
        if age_sec > float(self.max_tick_age_sec):
            return None
        return latest

    def status(self) -> Dict[str, Any]:
        latest = self._latest
        age_sec = None
        if latest is not None:
            age_sec = max(0.0, time.monotonic() - float(latest.received_monotonic))
        connected = bool(self._connected and latest is not None and (age_sec is not None and age_sec <= self.max_tick_age_sec))
        return {
            "enabled": self.enabled,
            "connected": connected,
            "last_tick_age_sec": age_sec,
            "requests": int(self._requests),
            "errors": int(self._errors),
            "last_error": self._last_error,
            "symbol": self.symbol,
            "source": "pyth_rest",
        }

    def _build_url(self, *, symbol: str) -> str:
        base = str(self.rest_url or "").strip()
        if not base:
            return ""
        if "{symbol}" in base:
            return base.replace("{symbol}", urllib.parse.quote(symbol, safe=""))
        return base

    @staticmethod
    def _to_utc_iso(value: Any) -> Optional[str]:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                return text
            # Accept already-ISO timestamps without forcing parse.
            return text
        if isinstance(value, (int, float)):
            ts = float(value)
            if not math.isfinite(ts):
                return None
            if ts > 10_000_000_000:
                ts = ts / 1000.0
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"
        return None

    @staticmethod
    def _price_from_obj(obj: Any) -> Optional[float]:
        if isinstance(obj, (int, float, str)):
            value = parse_float(obj)
            return float(value) if value is not None else None
        if not isinstance(obj, dict):
            return None
        raw_price = parse_float(obj.get("price"))
        expo = parse_float(obj.get("expo"))
        if raw_price is not None and expo is not None:
            return float(raw_price) * (10.0 ** int(expo))
        if raw_price is not None:
            return float(raw_price)
        return None

    def _parse_payload(self, payload: Any) -> Optional[PythTick]:
        now_iso = utc_iso()
        now_mono = time.monotonic()
        candidates = []
        if isinstance(payload, list):
            candidates.extend(payload)
        elif isinstance(payload, dict):
            candidates.append(payload)
            parsed = payload.get("parsed")
            if isinstance(parsed, list):
                candidates.extend(parsed)
            data = payload.get("data")
            if isinstance(data, list):
                candidates.extend(data)
            elif isinstance(data, dict):
                candidates.append(data)

        for row in candidates:
            if not isinstance(row, dict):
                continue
            price = self._price_from_obj(row.get("price"))
            if price is None:
                price = self._price_from_obj(row.get("ema_price"))
            if price is None:
                continue
            if not math.isfinite(price) or price <= 0.0:
                continue
            source_ts_utc = self._to_utc_iso(
                row.get("publish_time")
                or row.get("timestamp")
                or row.get("time")
                or row.get("ts_utc")
            )
            return PythTick(
                symbol=self.symbol,
                price=float(price),
                source_ts_utc=source_ts_utc,
                received_ts_utc=now_iso,
                received_monotonic=now_mono,
            )
        return None
