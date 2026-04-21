from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

from .common import utc_iso
from .http_session import build_hardened_session


LOG = logging.getLogger("prodesk.alerts")


class AlertNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.telegram_enabled = bool(cfg.get("telegram_enabled", False))
        self.timeout_sec = float(cfg.get("timeout_sec", 4.0))
        self.min_interval_sec = float(cfg.get("min_interval_sec", 30.0))
        webhook_env = str(cfg.get("webhook_url_env", "POLY_BOT_ALERT_WEBHOOK"))
        self.webhook_url = os.getenv(webhook_env)
        telegram_token_env = str(cfg.get("telegram_bot_token_env", "POLY_BOT_TELEGRAM_TOKEN"))
        telegram_chat_env = str(cfg.get("telegram_chat_id_env", "POLY_BOT_TELEGRAM_CHAT_ID"))
        self.telegram_token = os.getenv(telegram_token_env)
        self.telegram_chat_id = os.getenv(telegram_chat_env)
        self.telegram_parse_mode = str(cfg.get("telegram_parse_mode", "Markdown"))
        self._last_sent_by_key: Dict[str, float] = {}
        self.session = build_hardened_session(user_agent="polymarket-bro-alerts/0.1")

    def close(self) -> None:
        self.session.close()

    def notify(self, level: str, message: str, payload: Optional[Dict[str, Any]] = None, key: str = "default") -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        last = self._last_sent_by_key.get(key)
        if last is not None and (now - last) < self.min_interval_sec:
            return False

        body = {
            "ts_utc": utc_iso(),
            "level": level,
            "message": message,
            "payload": payload or {},
        }
        succeeded = False
        if self.webhook_url:
            try:
                resp = self.session.post(self.webhook_url, json=body, timeout=self.timeout_sec)
                resp.raise_for_status()
                succeeded = True
            except requests.RequestException as exc:
                LOG.warning("Alert webhook send failed: %s", exc)
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            if self._send_telegram(level=level, message=message, payload=payload or {}):
                succeeded = True
        if not succeeded:
            return False
        self._last_sent_by_key[key] = now
        return True

    def _send_telegram(self, *, level: str, message: str, payload: Dict[str, Any]) -> bool:
        if not self.telegram_token or not self.telegram_chat_id:
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload_text = ""
        if payload:
            parts = [f"{k}={v}" for k, v in payload.items()]
            payload_text = "\n" + "\n".join(parts[:12])
        text = f"*[{level.upper()}]* {message}{payload_text}"
        body = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": self.telegram_parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = self.session.post(url, json=body, timeout=self.timeout_sec)
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            LOG.warning("Telegram alert send failed: %s", exc)
            return False
