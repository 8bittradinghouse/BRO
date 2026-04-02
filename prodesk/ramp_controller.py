from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class RampSnapshot:
    enabled: bool
    target_usd: float
    sniper_allowed: bool
    changed: bool
    reason: str


class SizeRampController:
    def __init__(self, cfg: Dict[str, Any], *, base_target_usd: float):
        self.enabled = bool(cfg.get("enabled", False))
        self.start_usd = float(cfg.get("start_usd", 1.0))
        self.step_usd = float(cfg.get("step_usd", 1.0))
        self.max_usd = float(cfg.get("max_usd", 20.0))
        self.window_cycles = max(1, int(cfg.get("evaluation_window_cycles", 200)))
        self.downshift_reject_ratio = float(cfg.get("downshift_reject_ratio", 0.35))
        self.downshift_stale_oracle_ratio = float(
            cfg.get("downshift_stale_oracle_ratio", cfg.get("downshift_stale_ratio", 0.25))
        )
        self.downshift_disarmed_ratio = float(cfg.get("downshift_disarmed_ratio", 0.60))
        self.downshift_reconcile_mismatch_ratio = float(cfg.get("downshift_reconcile_mismatch_ratio", 0.05))
        self.disable_sniper_on_breach = bool(cfg.get("disable_sniper_on_breach", True))

        self.target_usd = max(self.start_usd, min(float(base_target_usd), self.max_usd))
        self.sniper_allowed = True
        self._cycles = 0
        self._reject_sum = 0.0
        self._stale_oracle_sum = 0.0
        self._disarmed_sum = 0.0
        self._reconcile_mismatch_sum = 0.0

    def observe_cycle(
        self,
        *,
        reject_ratio: float,
        stale_oracle_ratio: float,
        disarmed_ratio: float,
        reconcile_mismatch_ratio: float,
    ) -> RampSnapshot:
        if not self.enabled:
            return RampSnapshot(
                enabled=False,
                target_usd=self.target_usd,
                sniper_allowed=True,
                changed=False,
                reason="disabled",
            )

        self._cycles += 1
        self._reject_sum += max(0.0, float(reject_ratio))
        self._stale_oracle_sum += max(0.0, float(stale_oracle_ratio))
        self._disarmed_sum += max(0.0, float(disarmed_ratio))
        self._reconcile_mismatch_sum += max(0.0, float(reconcile_mismatch_ratio))
        if self._cycles < self.window_cycles:
            return RampSnapshot(
                enabled=True,
                target_usd=self.target_usd,
                sniper_allowed=self.sniper_allowed,
                changed=False,
                reason="collecting_window",
            )

        avg_reject = self._reject_sum / float(self._cycles)
        avg_stale_oracle = self._stale_oracle_sum / float(self._cycles)
        avg_disarmed = self._disarmed_sum / float(self._cycles)
        avg_reconcile_mismatch = self._reconcile_mismatch_sum / float(self._cycles)
        breached = (
            avg_reject >= self.downshift_reject_ratio
            or avg_stale_oracle >= self.downshift_stale_oracle_ratio
            or avg_disarmed >= self.downshift_disarmed_ratio
            or avg_reconcile_mismatch >= self.downshift_reconcile_mismatch_ratio
        )
        old_target = self.target_usd
        old_sniper_allowed = self.sniper_allowed
        reason = "window_healthy"

        if breached:
            self.target_usd = max(self.start_usd, self.target_usd - self.step_usd)
            reason = (
                "downshift:"
                + f"reject={avg_reject:.3f},"
                + f"stale_oracle={avg_stale_oracle:.3f},"
                + f"disarmed={avg_disarmed:.3f},"
                + f"reconcile={avg_reconcile_mismatch:.3f}"
            )
            if self.disable_sniper_on_breach:
                self.sniper_allowed = False
        else:
            self.target_usd = min(self.max_usd, self.target_usd + self.step_usd)
            self.sniper_allowed = True
            reason = (
                "upshift:"
                + f"reject={avg_reject:.3f},"
                + f"stale_oracle={avg_stale_oracle:.3f},"
                + f"disarmed={avg_disarmed:.3f},"
                + f"reconcile={avg_reconcile_mismatch:.3f}"
            )

        self._cycles = 0
        self._reject_sum = 0.0
        self._stale_oracle_sum = 0.0
        self._disarmed_sum = 0.0
        self._reconcile_mismatch_sum = 0.0
        changed = (self.target_usd != old_target) or (self.sniper_allowed != old_sniper_allowed)
        return RampSnapshot(
            enabled=True,
            target_usd=self.target_usd,
            sniper_allowed=self.sniper_allowed,
            changed=changed,
            reason=reason,
        )
