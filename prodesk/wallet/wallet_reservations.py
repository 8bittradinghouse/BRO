from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple


@dataclass
class WalletReservations:
    pending_locks: Dict[str, float] = field(default_factory=dict)
    order_locks: Dict[str, float] = field(default_factory=dict)
    completed_locks: Set[str] = field(default_factory=set)
    lock_seq: int = 0

    @staticmethod
    def _clamp_non_negative(value: float) -> float:
        return max(0.0, float(value))

    def _assert_invariants(self) -> None:
        for mapping_name, mapping in (("pending", self.pending_locks), ("order", self.order_locks)):
            for key, value in mapping.items():
                if not str(key or "").strip():
                    raise ValueError(f"wallet_{mapping_name}_lock_key_empty")
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise ValueError(f"wallet_{mapping_name}_lock_not_finite")
                if numeric < 0.0:
                    raise ValueError(f"wallet_{mapping_name}_lock_negative")
        overlap = set(self.pending_locks.keys()) & set(self.completed_locks)
        if overlap:
            raise ValueError(f"wallet_pending_completed_overlap:{sorted(overlap)}")
        for key in self.completed_locks:
            if not str(key or "").strip():
                raise ValueError("wallet_completed_lock_key_empty")
        if self.locked_total() < 0.0:
            raise ValueError("wallet_locked_total_negative")

    def _normalize_and_assert(self) -> None:
        self.pending_locks = {
            str(k): self._clamp_non_negative(v)
            for k, v in self.pending_locks.items()
            if str(k or "").strip() and self._clamp_non_negative(v) > 0.0
        }
        self.order_locks = {
            str(k): self._clamp_non_negative(v)
            for k, v in self.order_locks.items()
            if str(k or "").strip() and self._clamp_non_negative(v) > 0.0
        }
        self.completed_locks = {str(k).strip() for k in self.completed_locks if str(k or "").strip()}
        self._assert_invariants()

    def next_lock_id(self) -> str:
        self.lock_seq += 1
        return f"wallet-lock-{self.lock_seq}"

    def create_pending(self, notional_usd: float) -> str:
        lock_id = self.next_lock_id()
        self.pending_locks[lock_id] = self._clamp_non_negative(notional_usd)
        self.completed_locks.discard(lock_id)
        self._normalize_and_assert()
        return lock_id

    def confirm_submission(self, *, lock_id: str, order_id: str, order_open: bool) -> Tuple[bool, str]:
        key = str(lock_id or "").strip()
        if not key:
            return False, "wallet_lock_id_missing_on_submit"
        if key in self.completed_locks:
            return True, "wallet_lock_id_idempotent_completed"
        if key not in self.pending_locks:
            oid_existing = str(order_id or "").strip()
            if oid_existing and oid_existing in self.order_locks:
                return True, "wallet_lock_id_idempotent_order_exists"
            return False, f"wallet_lock_id_unknown:{key}"
        lock_notional = self._clamp_non_negative(self.pending_locks.pop(key))
        if order_open:
            oid = str(order_id or "").strip()
            if not oid:
                return False, "wallet_order_id_missing_for_open_lock"
            self.order_locks[oid] = self._clamp_non_negative(self.order_locks.get(oid, 0.0) + lock_notional)
        self.completed_locks.add(key)
        self._normalize_and_assert()
        return True, "ok"

    def release_pending(self, lock_id: str) -> None:
        key = str(lock_id or "").strip()
        if key:
            self.pending_locks.pop(key, None)
            self.completed_locks.add(key)
            self._normalize_and_assert()

    def release_order(self, order_id: str) -> None:
        key = str(order_id or "").strip()
        if key:
            self.order_locks.pop(key, None)
            self._normalize_and_assert()

    def settle_fill(self, *, order_id: Optional[str], notional_usd: float, tolerance: float) -> None:
        oid = str(order_id or "").strip()
        if not oid or oid not in self.order_locks:
            return
        current = self._clamp_non_negative(self.order_locks.get(oid, 0.0))
        remaining = current - self._clamp_non_negative(notional_usd)
        if remaining <= max(0.0, float(tolerance)):
            self.order_locks.pop(oid, None)
        else:
            self.order_locks[oid] = self._clamp_non_negative(remaining)
        self._normalize_and_assert()

    def locked_total(self) -> float:
        pending_total = sum(self._clamp_non_negative(v) for v in self.pending_locks.values())
        order_total = sum(self._clamp_non_negative(v) for v in self.order_locks.values())
        return self._clamp_non_negative(pending_total + order_total)

    def snapshot(self) -> dict:
        pending_total = float(sum(self._clamp_non_negative(v) for v in self.pending_locks.values()))
        order_total = float(sum(self._clamp_non_negative(v) for v in self.order_locks.values()))
        return {
            "pending_lock_usdc": pending_total,
            "order_lock_usdc": order_total,
            "locked_usdc": float(self._clamp_non_negative(pending_total + order_total)),
            "completed_lock_count": int(len(self.completed_locks)),
        }
