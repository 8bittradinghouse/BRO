from __future__ import annotations

import datetime as dt
import email.utils
import os
import pathlib
from typing import Any, Dict, List

import requests

from .common import utc_now
from .gateway import _normalize_evm_address, _normalize_private_key
from .market_discovery import MarketDiscovery
from .market_data import RestBookClient
from .http_session import build_hardened_session
from .paths import validate_runtime_write_paths
from .secrets import SecretLoadError, load_auth_secrets
from .security import run_security_checks
from .state_store import load_state


def run_preflight_checks(
    cfg: Dict[str, Any],
    *,
    mode_override: str | None = None,
    confirm_live: bool = False,
) -> List[str]:
    mode = (mode_override or str(cfg.get("mode", "paper"))).lower()
    findings: List[str] = []

    log_dir = pathlib.Path(cfg["storage"]["log_dir"]).resolve()
    state_path = pathlib.Path(cfg["storage"]["state_path"]).resolve()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        state_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        findings.append(f"storage_paths_not_writable: {exc}")

    runtime_cfg = cfg.get("runtime", {})
    guard_stop_file_raw = str(runtime_cfg.get("guard_stop_file", "")).strip()
    clear_guard_stop_on_start = bool(runtime_cfg.get("clear_guard_stop_on_start", False))
    if guard_stop_file_raw:
        guard_stop_path = pathlib.Path(guard_stop_file_raw).resolve()
        try:
            guard_stop_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            findings.append(f"guard_stop_path_not_writable:{exc}")
        if guard_stop_path.exists() and guard_stop_path.is_dir():
            findings.append("guard_stop_path_is_directory")
        if mode == "live" and guard_stop_path.exists() and not clear_guard_stop_on_start:
            findings.append("guard_stop_file_present")

    token_ids = [str(x) for x in cfg["targets"]["token_ids"]]
    if len(token_ids) != len(set(token_ids)):
        findings.append("duplicate_token_ids_detected")
    discovery_enabled = bool(cfg.get("targets", {}).get("discovery", {}).get("enabled", False))
    if not token_ids and not discovery_enabled:
        findings.append("no_static_token_ids")

    if mode == "live":
        preflight_cfg = cfg.get("preflight", {})
        if bool(preflight_cfg.get("require_live_confirmation", True)) and not confirm_live:
            findings.append("live_confirmation_missing")

        auth_cfg = cfg["auth"]
        pk_env = str(auth_cfg.get("private_key_env", "POLYMARKET_PRIVATE_KEY")).strip() or "POLYMARKET_PRIVATE_KEY"
        funder_env = str(auth_cfg.get("funder_env", "POLYMARKET_FUNDER")).strip() or "POLYMARKET_FUNDER"
        private_key_source = _auth_secret_source(auth_cfg, source_key="private_key_source", legacy_env=pk_env)
        funder_source = _auth_secret_source(auth_cfg, source_key="funder_source", legacy_env=funder_env)

        missing_env_names: List[str] = []
        for source, legacy_env in ((private_key_source, pk_env), (funder_source, funder_env)):
            source_mode = str(source.get("mode", "env")).strip().lower() or "env"
            if source_mode != "env":
                continue
            env_name = str(source.get("env", legacy_env)).strip() or legacy_env
            if not os.getenv(env_name):
                missing_env_names.append(env_name)
        for env_name in missing_env_names:
            findings.append(f"missing_env:{env_name}")

        if not missing_env_names:
            try:
                private_key, funder, source_meta = load_auth_secrets(auth_cfg)
            except SecretLoadError as exc:
                findings.append(f"secret_load_failed:{exc}")
            else:
                try:
                    _normalize_private_key(private_key)
                except ValueError as exc:
                    source = str(source_meta.get("private_key_source", "private_key"))
                    findings.append(f"invalid_private_key:{source}:{exc}")
                try:
                    _normalize_evm_address(funder)
                except ValueError as exc:
                    source = str(source_meta.get("funder_source", "funder"))
                    findings.append(f"invalid_funder:{source}:{exc}")

        security_cfg = cfg.get("security", {})
        if bool(security_cfg.get("require_live_security_ack", True)):
            ack_env = str(security_cfg.get("live_security_ack_env", "SECURITY_ACK")).strip() or "SECURITY_ACK"
            required_value = str(security_cfg.get("live_security_ack_value", "YES")).strip() or "YES"
            ack_value = str(os.getenv(ack_env, "")).strip()
            if ack_value != required_value:
                findings.append(f"security_ack_missing:{ack_env}")

    if state_path.exists():
        try:
            load_state(state_path)
        except (OSError, ValueError) as exc:
            findings.append(f"state_file_invalid:{exc}")

    findings.extend(run_security_checks(cfg, mode=mode))
    findings.extend(validate_runtime_write_paths(cfg))

    preflight_cfg = cfg.get("preflight", {})
    chainlink_cfg = cfg.get("chainlink", {})
    if bool(chainlink_cfg.get("enabled", False)):
        try:
            import websockets  # noqa: F401
        except ImportError:
            findings.append("chainlink_websockets_dependency_missing")
    md_ws_cfg = cfg.get("market_data", {}).get("ws", {})
    if bool(md_ws_cfg.get("enabled", False)):
        try:
            import websockets  # noqa: F401
        except ImportError:
            findings.append("market_data_ws_dependency_missing")
    metrics_cfg = cfg.get("metrics", {})
    if bool(metrics_cfg.get("enabled", False)):
        try:
            import prometheus_client  # noqa: F401
        except ImportError:
            findings.append("prometheus_dependency_missing")
    alerts_cfg = cfg.get("alerts", {})
    if bool(alerts_cfg.get("enabled", False)):
        webhook_env = str(alerts_cfg.get("webhook_url_env", "POLY_BOT_ALERT_WEBHOOK"))
        has_webhook = bool(os.getenv(webhook_env))
        telegram_enabled = bool(alerts_cfg.get("telegram_enabled", False))
        telegram_token_env = str(alerts_cfg.get("telegram_bot_token_env", "POLY_BOT_TELEGRAM_TOKEN"))
        telegram_chat_env = str(alerts_cfg.get("telegram_chat_id_env", "POLY_BOT_TELEGRAM_CHAT_ID"))
        has_telegram = bool(os.getenv(telegram_token_env)) and bool(os.getenv(telegram_chat_env))
        if telegram_enabled and not has_telegram:
            findings.append("telegram_alert_env_missing")
        if not has_webhook and not has_telegram:
            findings.append("alert_sink_not_configured")

    if discovery_enabled and not token_ids:
        discovery = MarketDiscovery(cfg)
        try:
            result = discovery.discover()
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, requests.RequestException) as exc:
            findings.append(f"discovery_failed:{exc}")
            result = None
        finally:
            discovery.close()
        if result is None or not result.token_ids:
            findings.append("discovery_no_tokens")
        else:
            token_ids = [str(x) for x in result.token_ids]

    if bool(preflight_cfg.get("check_market_data", True)):
        failures = _check_market_data(cfg, token_ids)
        allowed = int(preflight_cfg.get("max_market_data_failures", 0))
        if failures > allowed:
            findings.append(f"market_data_check_failed:{failures}_tokens")
    if bool(preflight_cfg.get("check_clock_sync", False)):
        skew = _clock_skew_seconds(
            base_url=str(cfg.get("auth", {}).get("host", cfg.get("market_data", {}).get("clob_url", ""))),
            timeout_sec=float(preflight_cfg.get("endpoint_timeout_sec", 4.0)),
        )
        if skew is None:
            findings.append("clock_sync_check_unavailable")
        else:
            max_skew = float(preflight_cfg.get("max_clock_skew_sec", 2.5))
            if abs(skew) > max_skew:
                findings.append(f"clock_skew_exceeded:{skew:.3f}s")
    if bool(preflight_cfg.get("check_endpoint_health", False)):
        endpoint_urls = _endpoint_urls(cfg)
        timeout_sec = float(preflight_cfg.get("endpoint_timeout_sec", 4.0))
        failed = _endpoint_health_failures(endpoint_urls, timeout_sec=timeout_sec)
        if failed:
            findings.append(f"endpoint_health_failed:{','.join(failed)}")

    return findings


def _auth_secret_source(auth_cfg: Dict[str, Any], *, source_key: str, legacy_env: str) -> Dict[str, Any]:
    source = auth_cfg.get(source_key)
    if not isinstance(source, dict):
        return {"mode": "env", "env": legacy_env}
    return source


def _check_market_data(cfg: Dict[str, Any], token_ids: List[str]) -> int:
    md = cfg["market_data"]
    client = RestBookClient(
        clob_url=str(md["clob_url"]),
        book_path=str(md["book_path"]),
        timeout_sec=float(md["timeout_sec"]),
        max_retries=int(md["max_retries"]),
    )
    failures = 0
    try:
        for token_id in token_ids:
            try:
                top, _ = client.fetch_book(token_id)
            except (OSError, RuntimeError, ValueError, requests.RequestException):
                failures += 1
                continue
            if top.best_bid_price is None and top.best_ask_price is None:
                failures += 1
    finally:
        client.close()
    return failures


def _endpoint_urls(cfg: Dict[str, Any]) -> List[str]:
    preflight_cfg = cfg.get("preflight", {})
    urls = [str(u).strip() for u in preflight_cfg.get("endpoint_urls", []) if str(u).strip()]
    if urls:
        return urls
    out: List[str] = []
    md = cfg.get("market_data", {})
    clob_url = str(md.get("clob_url", "")).rstrip("/")
    book_path = str(md.get("book_path", "/book"))
    if clob_url:
        out.append(f"{clob_url}{book_path}")
    discovery = cfg.get("targets", {}).get("discovery", {})
    if bool(discovery.get("enabled", False)):
        gamma_url = str(discovery.get("gamma_url", "")).rstrip("/")
        markets_path = str(discovery.get("markets_path", "/markets"))
        if gamma_url:
            out.append(f"{gamma_url}{markets_path}")
    return out


def _endpoint_health_failures(urls: List[str], *, timeout_sec: float) -> List[str]:
    failed: List[str] = []
    if not urls:
        return failed
    session = build_hardened_session(
        user_agent="polymarket-bro-preflight/0.1",
        total_retries=0,
    )
    try:
        for url in urls:
            try:
                resp = session.get(url, timeout=timeout_sec)
                if resp.status_code >= 500:
                    failed.append(f"{url}:http_{resp.status_code}")
            except (requests.RequestException, RuntimeError) as exc:
                failed.append(f"{url}:{exc.__class__.__name__}")
    finally:
        session.close()
    return failed


def _clock_skew_seconds(*, base_url: str, timeout_sec: float) -> float | None:
    if not base_url:
        return None
    session = build_hardened_session(
        user_agent="polymarket-bro-preflight/0.1",
        total_retries=0,
    )
    try:
        resp = session.get(base_url, timeout=timeout_sec)
        date_header = resp.headers.get("Date")
        if not date_header:
            return None
        parsed = email.utils.parsedate_to_datetime(date_header)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        exchange_now = parsed.astimezone(dt.timezone.utc)
        local_now = utc_now()
        return float((local_now - exchange_now).total_seconds())
    except (requests.RequestException, RuntimeError, TypeError, ValueError, OverflowError):
        return None
    finally:
        session.close()
