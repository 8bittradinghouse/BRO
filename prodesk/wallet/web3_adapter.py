from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from ..common import utc_iso
from .wallet_config import WalletConfig
from .wallet_types import (
    AUTHORITY_CLASS_LIVE,
    LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
    NonceSnapshot,
    PendingTxSnapshot,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
)


def _default_web3_factory(rpc_url: str) -> Any:
    try:
        from web3 import HTTPProvider, Web3
    except Exception as exc:  # pragma: no cover - exercised only when dependency missing at runtime
        raise RuntimeError("web3_dependency_unavailable") from exc
    return Web3(HTTPProvider(str(rpc_url)))


@dataclasses.dataclass(frozen=True)
class WalletWeb3ProviderHealth:
    active_provider: str
    active_rpc_url: str
    primary_configured: bool
    failover_configured: bool
    primary_latency_ms: Optional[float]
    failover_latency_ms: Optional[float]
    primary_consecutive_high_latency: int
    failover_active: bool
    sticky_failover_active: bool
    sticky_failover_remaining_seconds: float
    provider_trustworthy: bool
    health_reasons: tuple[str, ...]
    last_switch_reason: str
    last_switch_ts_utc: str


class WalletWeb3Adapter:
    """web3.py-backed mechanics plane for Packet 3 wallet operations.

    Slice 2 introduces the provider and failover model without arming any live
    wallet behavior by itself.
    """

    def __init__(
        self,
        cfg: WalletConfig | Mapping[str, Any],
        *,
        web3_factory: Optional[Callable[[str], Any]] = None,
        monotonic_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if isinstance(cfg, WalletConfig):
            wallet_cfg = cfg
        else:
            from .wallet_config import load_wallet_config

            wallet_cfg = load_wallet_config(cfg)
        self._cfg = wallet_cfg
        self._web3_factory = web3_factory or _default_web3_factory
        self._monotonic_clock = monotonic_clock or time.monotonic

        self._active_provider = "primary"
        self._primary_consecutive_high_latency = 0
        self._sticky_failover_until_mono = 0.0
        self._last_switch_reason = ""
        self._last_switch_ts_utc = ""
        self._primary_latency_ms: Optional[float] = None
        self._failover_latency_ms: Optional[float] = None
        self._primary_last_error = ""
        self._failover_last_error = ""
        self._account_state_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def is_configured(self) -> bool:
        return bool(str(self._cfg.web3_primary_rpc_url) or str(self._cfg.web3_failover_rpc_url))

    def active_provider_name(self) -> str:
        return str(self._active_provider)

    def active_rpc_url(self) -> str:
        if self._active_provider == "failover":
            return str(self._cfg.web3_failover_rpc_url)
        return str(self._cfg.web3_primary_rpc_url)

    def build_active_client(self) -> Any:
        rpc_url = self.active_rpc_url()
        if not rpc_url:
            raise RuntimeError("web3_rpc_url_missing")
        return self._web3_factory(rpc_url)

    def record_rpc_result(
        self,
        *,
        latency_ms: Optional[float],
        ok: bool = True,
        error: str = "",
        rate_limited: bool = False,
        provider_name: Optional[str] = None,
        now_monotonic: Optional[float] = None,
    ) -> WalletWeb3ProviderHealth:
        provider = str(provider_name or self._active_provider or "primary").strip().lower() or "primary"
        now_mono = float(self._monotonic_clock() if now_monotonic is None else now_monotonic)
        if provider == "failover":
            self._failover_latency_ms = float(latency_ms) if latency_ms is not None else self._failover_latency_ms
            self._failover_last_error = str(error or ("rate_limited" if rate_limited else ""))
            return self.health_contract(now_monotonic=now_mono)

        self._primary_latency_ms = float(latency_ms) if latency_ms is not None else self._primary_latency_ms
        self._primary_last_error = str(error or ("rate_limited" if rate_limited else ""))

        if rate_limited:
            self._switch_to_failover(reason="primary_rate_limited", now_monotonic=now_mono)
            return self.health_contract(now_monotonic=now_mono)
        if not ok or error:
            self._switch_to_failover(reason=f"primary_error:{self._primary_last_error or 'unknown'}", now_monotonic=now_mono)
            return self.health_contract(now_monotonic=now_mono)

        threshold = float(self._cfg.web3_failover_max_latency_ms)
        if latency_ms is not None and float(latency_ms) > threshold:
            self._primary_consecutive_high_latency += 1
            if self._primary_consecutive_high_latency >= int(self._cfg.web3_failover_consecutive_high_latency):
                self._switch_to_failover(reason="primary_high_latency_threshold", now_monotonic=now_mono)
        else:
            self._primary_consecutive_high_latency = 0
        return self.health_contract(now_monotonic=now_mono)

    def attempt_primary_restore(self, *, now_monotonic: Optional[float] = None) -> bool:
        now_mono = float(self._monotonic_clock() if now_monotonic is None else now_monotonic)
        if self._active_provider != "failover":
            return False
        if now_mono < self._sticky_failover_until_mono:
            return False
        if not str(self._cfg.web3_primary_rpc_url):
            return False
        self._active_provider = "primary"
        self._primary_consecutive_high_latency = 0
        self._last_switch_reason = "sticky_failover_window_elapsed"
        self._last_switch_ts_utc = utc_iso()
        return True

    def health_contract(self, *, now_monotonic: Optional[float] = None) -> WalletWeb3ProviderHealth:
        now_mono = float(self._monotonic_clock() if now_monotonic is None else now_monotonic)
        primary_configured = bool(str(self._cfg.web3_primary_rpc_url))
        failover_configured = bool(str(self._cfg.web3_failover_rpc_url))
        sticky_remaining = max(0.0, float(self._sticky_failover_until_mono - now_mono))
        sticky_active = bool(self._active_provider == "failover" and sticky_remaining > 0.0)

        reasons: list[str] = []
        if not primary_configured:
            reasons.append("web3_primary_rpc_missing")
        if not failover_configured:
            reasons.append("web3_failover_rpc_missing")
        if self._active_provider == "primary" and self._primary_last_error:
            reasons.append(f"web3_primary_unhealthy:{self._primary_last_error}")
        if self._active_provider == "failover" and self._failover_last_error:
            reasons.append(f"web3_failover_unhealthy:{self._failover_last_error}")

        provider_trustworthy = bool(primary_configured and failover_configured and self.active_rpc_url()) and (
            self._active_provider != "primary" or not self._primary_last_error
        ) and (self._active_provider != "failover" or not self._failover_last_error)

        return WalletWeb3ProviderHealth(
            active_provider=str(self._active_provider),
            active_rpc_url=self.active_rpc_url(),
            primary_configured=bool(primary_configured),
            failover_configured=bool(failover_configured),
            primary_latency_ms=self._primary_latency_ms,
            failover_latency_ms=self._failover_latency_ms,
            primary_consecutive_high_latency=int(self._primary_consecutive_high_latency),
            failover_active=bool(self._active_provider == "failover"),
            sticky_failover_active=bool(sticky_active),
            sticky_failover_remaining_seconds=float(sticky_remaining),
            provider_trustworthy=bool(provider_trustworthy),
            health_reasons=tuple(reasons),
            last_switch_reason=str(self._last_switch_reason),
            last_switch_ts_utc=str(self._last_switch_ts_utc),
        )

    def health_status_mapping(self, *, now_monotonic: Optional[float] = None) -> Dict[str, Any]:
        health = self.health_contract(now_monotonic=now_monotonic)
        return dataclasses.asdict(health)

    @staticmethod
    def normalize_redemption_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(receipt or {})
        status_raw = payload.get("status")
        if isinstance(status_raw, bool):
            receipt_confirmed = bool(status_raw)
        elif isinstance(status_raw, (int, float)):
            receipt_confirmed = bool(int(status_raw) == 1)
        else:
            text = str(status_raw or "").strip().lower()
            receipt_confirmed = text in {"1", "0x1", "true", "confirmed", "success"}
        tx_hash = str(
            payload.get("tx_hash")
            or payload.get("transactionHash")
            or payload.get("hash")
            or ""
        ).strip()
        payout_raw = payload.get("payout_usdc", payload.get("payout_amount_usdc", payload.get("collateral_amount")))
        try:
            payout_usdc = max(0.0, float(payout_raw or 0.0))
        except Exception:
            payout_usdc = 0.0
        detail = str(payload.get("detail") or "")
        return {
            "receipt_confirmed": bool(receipt_confirmed),
            "tx_hash": tx_hash,
            "payout_usdc": float(payout_usdc),
            "detail": detail,
        }

    def dynamic_gas_band(
        self,
        *,
        base_fee_wei: int,
        priority_fee_wei: int,
        congestion_ratio: Optional[float] = None,
        spike: bool = False,
    ) -> Dict[str, Any]:
        base_fee = max(0, int(base_fee_wei))
        priority_fee = max(0, int(priority_fee_wei))
        if spike:
            applied_multiplier = float(self._cfg.web3_gas_spike_max_multiplier)
            multiplier_class = "spike"
        else:
            ratio = 0.0 if congestion_ratio is None else max(0.0, min(1.0, float(congestion_ratio)))
            min_mult = float(self._cfg.web3_gas_normal_min_multiplier)
            max_mult = float(self._cfg.web3_gas_normal_max_multiplier)
            applied_multiplier = min_mult + ((max_mult - min_mult) * ratio)
            multiplier_class = "normal"
        max_fee_per_gas_wei = int(round((base_fee * applied_multiplier) + priority_fee))
        max_priority_fee_per_gas_wei = int(round(priority_fee * applied_multiplier))
        return {
            "base_fee_wei": int(base_fee),
            "priority_fee_wei": int(priority_fee),
            "applied_multiplier": float(applied_multiplier),
            "multiplier_class": multiplier_class,
            "max_fee_per_gas_wei": int(max_fee_per_gas_wei),
            "max_priority_fee_per_gas_wei": int(max_priority_fee_per_gas_wei),
        }

    def canonical_nonce_snapshot(self, address: str) -> NonceSnapshot:
        normalized = self._normalize_address(address)
        if not normalized:
            return NonceSnapshot(
                current_nonce=None,
                pending_nonces=tuple(),
                ts_utc=utc_iso(),
                source=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                healthy=False,
                detail="web3_wallet_address_unavailable",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )
        state = self._canonical_account_pending_state(normalized)
        return NonceSnapshot(
            current_nonce=state.get("current_nonce"),
            pending_nonces=tuple(state.get("pending_nonces", ())),
            ts_utc=utc_iso(),
            source=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            healthy=bool(state.get("healthy", False)),
            detail=str(state.get("detail") or ""),
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def canonical_pending_tx_snapshot(self, address: str) -> PendingTxSnapshot:
        normalized = self._normalize_address(address)
        if not normalized:
            return PendingTxSnapshot(
                pending_count=0,
                order_ids=tuple(),
                tx_ids=tuple(),
                exchange_order_ids=tuple(),
                exchange_client_order_ids=tuple(),
                ts_utc=utc_iso(),
                source=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                healthy=False,
                detail="web3_wallet_address_unavailable",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
                lifecycle_plane=LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
            )
        state = self._canonical_account_pending_state(normalized)
        return PendingTxSnapshot(
            pending_count=int(state.get("pending_count", 0) or 0),
            order_ids=tuple(),
            tx_ids=tuple(str(x) for x in state.get("tx_ids", ()) if str(x).strip()),
            exchange_order_ids=tuple(),
            exchange_client_order_ids=tuple(),
            ts_utc=utc_iso(),
            source=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            healthy=bool(state.get("healthy", False)),
            detail=str(state.get("detail") or ""),
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
            lifecycle_plane=LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
        )

    def _switch_to_failover(self, *, reason: str, now_monotonic: float) -> None:
        self._active_provider = "failover" if str(self._cfg.web3_failover_rpc_url) else "primary"
        self._sticky_failover_until_mono = float(now_monotonic + float(self._cfg.web3_failover_sticky_seconds))
        self._last_switch_reason = str(reason)
        self._last_switch_ts_utc = utc_iso()
        self._primary_consecutive_high_latency = 0

    def _canonical_account_pending_state(self, address: str) -> dict[str, Any]:
        cache_key = str(address).strip().lower()
        now_mono = float(self._monotonic_clock())
        cached = self._account_state_cache.get(cache_key)
        if cached and (now_mono - float(cached[0])) <= 0.25:
            return dict(cached[1])
        if not self.is_configured():
            state = {
                "healthy": False,
                "current_nonce": None,
                "pending_count": 0,
                "pending_nonces": tuple(),
                "tx_ids": tuple(),
                "detail": "web3_rpc_unconfigured",
            }
            self._account_state_cache[cache_key] = (now_mono, dict(state))
            return state
        try:
            latest_nonce = int(
                self._rpc_call(
                    "get_transaction_count_latest",
                    lambda client: client.eth.get_transaction_count(address, "latest"),
                )
            )
            pending_nonce = int(
                self._rpc_call(
                    "get_transaction_count_pending",
                    lambda client: client.eth.get_transaction_count(address, "pending"),
                )
            )
        except Exception as exc:
            state = {
                "healthy": False,
                "current_nonce": None,
                "pending_count": 0,
                "pending_nonces": tuple(),
                "tx_ids": tuple(),
                "detail": f"web3_transaction_count_unavailable:{exc}",
            }
            self._account_state_cache[cache_key] = (now_mono, dict(state))
            return state

        inferred_pending_nonces = tuple(range(latest_nonce, pending_nonce)) if pending_nonce > latest_nonce else tuple()
        inferred_pending_count = max(0, pending_nonce - latest_nonce)
        txpool_state = self._txpool_account_state(address)
        pending_nonces = tuple(txpool_state.get("pending_nonces", ())) or inferred_pending_nonces
        tx_ids = tuple(str(x) for x in txpool_state.get("tx_ids", ()) if str(x).strip())
        pending_count = int(txpool_state.get("pending_count", inferred_pending_count) or inferred_pending_count)
        detail = (
            f"web3_pending_state_ok:latest={latest_nonce}:pending={pending_nonce}"
            if bool(txpool_state.get("txpool_available", False))
            else f"web3_pending_state_ok:transaction_count_delta_only:latest={latest_nonce}:pending={pending_nonce}:{txpool_state.get('detail') or 'txpool_unavailable'}"
        )
        state = {
            "healthy": True,
            "current_nonce": pending_nonce,
            "pending_count": max(0, pending_count),
            "pending_nonces": pending_nonces,
            "tx_ids": tx_ids,
            "detail": detail,
        }
        self._account_state_cache[cache_key] = (now_mono, dict(state))
        return state

    def _txpool_account_state(self, address: str) -> dict[str, Any]:
        try:
            content = self._optional_txpool_content()
        except Exception as exc:
            return {
                "txpool_available": False,
                "pending_count": 0,
                "pending_nonces": tuple(),
                "tx_ids": tuple(),
                "detail": f"txpool_unavailable:{exc}",
            }
        normalized = str(address).strip().lower()
        pending_nonces: set[int] = set()
        tx_ids: list[str] = []
        pending_count = 0
        for section_name in ("pending", "queued"):
            section = content.get(section_name)
            if not isinstance(section, Mapping):
                continue
            account_bucket = None
            for candidate, bucket in section.items():
                if str(candidate).strip().lower() == normalized:
                    account_bucket = bucket
                    break
            if not isinstance(account_bucket, Mapping):
                continue
            for nonce_key, tx_payload in account_bucket.items():
                payloads: Iterable[Any]
                if isinstance(tx_payload, Iterable) and not isinstance(tx_payload, (str, bytes, Mapping)):
                    payloads = list(tx_payload)
                else:
                    payloads = [tx_payload]
                parsed_nonce = self._parse_int_like(nonce_key)
                for payload in payloads:
                    nonce_value = parsed_nonce
                    if isinstance(payload, Mapping):
                        payload_nonce = self._parse_int_like(payload.get("nonce"))
                        if payload_nonce is not None:
                            nonce_value = payload_nonce
                        tx_hash = str(payload.get("hash") or "").strip()
                        if tx_hash:
                            tx_ids.append(tx_hash)
                    if nonce_value is not None and nonce_value >= 0:
                        pending_nonces.add(int(nonce_value))
                    pending_count += 1
        return {
            "txpool_available": True,
            "pending_count": int(pending_count),
            "pending_nonces": tuple(sorted(pending_nonces)),
            "tx_ids": tuple(dict.fromkeys(tx_ids)),
            "detail": "txpool_content",
        }

    def _optional_txpool_content(self) -> Mapping[str, Any]:
        self.attempt_primary_restore()
        client = self.build_active_client()
        return self._read_txpool_content(client)

    def _read_txpool_content(self, client: Any) -> Mapping[str, Any]:
        if hasattr(client, "geth") and hasattr(client.geth, "txpool") and hasattr(client.geth.txpool, "content"):
            payload = client.geth.txpool.content()
        elif hasattr(client, "txpool") and hasattr(client.txpool, "content"):
            payload = client.txpool.content()
        elif hasattr(client, "manager") and hasattr(client.manager, "request_blocking"):
            payload = client.manager.request_blocking("txpool_content", [])
        else:
            raise RuntimeError("txpool_content_unsupported")
        if not isinstance(payload, Mapping):
            raise RuntimeError(f"txpool_content_invalid_type:{type(payload).__name__}")
        return payload

    def _rpc_call(self, label: str, operation: Callable[[Any], Any]) -> Any:
        self.attempt_primary_restore()
        last_exc: Optional[Exception] = None
        attempted_failover = False
        for _ in range(2):
            provider_name = self.active_provider_name()
            start = float(self._monotonic_clock())
            try:
                client = self.build_active_client()
                result = operation(client)
            except Exception as exc:
                end = float(self._monotonic_clock())
                self.record_rpc_result(
                    latency_ms=max(0.0, (end - start) * 1000.0),
                    ok=False,
                    error=self._rpc_error_detail(label, exc),
                    rate_limited=self._looks_rate_limited(exc),
                    provider_name=provider_name,
                    now_monotonic=end,
                )
                last_exc = exc
                if provider_name == "primary" and self.active_provider_name() == "failover" and not attempted_failover:
                    attempted_failover = True
                    continue
                break
            end = float(self._monotonic_clock())
            self.record_rpc_result(
                latency_ms=max(0.0, (end - start) * 1000.0),
                ok=True,
                provider_name=provider_name,
                now_monotonic=end,
            )
            return result
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"web3_rpc_call_failed:{label}")

    @staticmethod
    def _normalize_address(address: str) -> str:
        text = str(address or "").strip()
        if text.startswith("0x") and len(text) == 42:
            return text.lower()
        return ""

    @staticmethod
    def _parse_int_like(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(float(text))
        except Exception:
            return None

    @staticmethod
    def _looks_rate_limited(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "rate limit" in text or "too many requests" in text or "429" in text

    @staticmethod
    def _rpc_error_detail(label: str, exc: Exception) -> str:
        return f"{label}:{exc.__class__.__name__}:{exc}"


def create_wallet_web3_adapter(
    cfg: WalletConfig | Mapping[str, Any],
    *,
    web3_factory: Optional[Callable[[str], Any]] = None,
    monotonic_clock: Optional[Callable[[], float]] = None,
) -> WalletWeb3Adapter:
    return WalletWeb3Adapter(cfg, web3_factory=web3_factory, monotonic_clock=monotonic_clock)


__all__ = [
    "WalletWeb3Adapter",
    "WalletWeb3ProviderHealth",
    "create_wallet_web3_adapter",
]
