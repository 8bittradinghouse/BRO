from __future__ import annotations

import collections
import dataclasses
from typing import Any, Deque, Dict

from .common import clamp


MODE_NORMAL = "normal"
MODE_CAUTIOUS = "cautious"
MODE_MAKER_ONLY = "maker_only"
MODE_SAFE_STOP = "safe_stop"


@dataclasses.dataclass(frozen=True)
class OperatingModeSnapshot:
    state: str
    previous_state: str
    changed: bool
    reason: str
    stale_reject_ratio: float
    outage_ratio: float
    disarmed_ratio: float
    error_ratio: float
    sample_count: int
    risk_reject_count: int
    stale_reject_count: int


class OperatingModeController:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.window_cycles = max(5, int(cfg.get("window_cycles", 40)))
        self.caution_stale_reject_ratio = clamp(float(cfg.get("caution_stale_reject_ratio", 0.35)), 0.0, 1.0)
        self.maker_only_stale_reject_ratio = clamp(float(cfg.get("maker_only_stale_reject_ratio", 0.55)), 0.0, 1.0)
        # Require minimum stale/risk evidence before stale-reject ratios can drive
        # operating-mode escalation. This avoids thin-sample ratio spikes (e.g. 1/1)
        # from prematurely latching safe-stop.
        self.caution_min_stale_reject_count = max(1, int(cfg.get("caution_min_stale_reject_count", 2)))
        self.caution_min_risk_reject_count = max(1, int(cfg.get("caution_min_risk_reject_count", 4)))
        self.maker_only_min_stale_reject_count = max(1, int(cfg.get("maker_only_min_stale_reject_count", 3)))
        self.maker_only_min_risk_reject_count = max(1, int(cfg.get("maker_only_min_risk_reject_count", 8)))
        self.caution_outage_ratio = clamp(float(cfg.get("caution_outage_ratio", 0.20)), 0.0, 1.0)
        self.maker_only_outage_ratio = clamp(float(cfg.get("maker_only_outage_ratio", 0.40)), 0.0, 1.0)
        self.caution_disarmed_ratio = clamp(float(cfg.get("caution_disarmed_ratio", 0.50)), 0.0, 1.0)
        self.maker_only_disarmed_ratio = clamp(float(cfg.get("maker_only_disarmed_ratio", 0.75)), 0.0, 1.0)
        self.caution_error_ratio = clamp(float(cfg.get("caution_error_ratio", 0.10)), 0.0, 1.0)
        self.maker_only_error_ratio = clamp(float(cfg.get("maker_only_error_ratio", 0.25)), 0.0, 1.0)
        self.recover_healthy_cycles = max(1, int(cfg.get("recover_healthy_cycles", 30)))
        self.safe_stop_severe_cycles = max(1, int(cfg.get("safe_stop_severe_cycles", 20)))
        self.cautious_size_mult = max(0.01, float(cfg.get("cautious_size_mult", 0.75)))
        self.maker_only_size_mult = max(0.01, float(cfg.get("maker_only_size_mult", 0.45)))
        self.cautious_spread_mult = max(0.5, float(cfg.get("cautious_spread_mult", 1.10)))
        self.maker_only_spread_mult = max(0.5, float(cfg.get("maker_only_spread_mult", 1.25)))

        self._state = MODE_NORMAL
        self._healthy_streak = 0
        self._severe_streak = 0
        self._history: Deque[Dict[str, float]] = collections.deque(maxlen=self.window_cycles)

    @property
    def state(self) -> str:
        return self._state

    def _snapshot_metrics(self) -> Dict[str, float]:
        if not self._history:
            return {
                "stale_reject_ratio": 0.0,
                "outage_ratio": 0.0,
                "disarmed_ratio": 0.0,
                "error_ratio": 0.0,
                "sample_count": 0.0,
            }
        samples = float(len(self._history))
        stale_rejects = sum(row["stale_rejects"] for row in self._history)
        risk_rejects = max(1.0, sum(row["risk_rejects"] for row in self._history))
        outage_cycles = sum(row["outage_cycle"] for row in self._history)
        disarmed_cycles = sum(row["disarmed_cycle"] for row in self._history)
        error_cycles = sum(row["error_cycle"] for row in self._history)
        return {
            "stale_reject_ratio": stale_rejects / risk_rejects,
            "outage_ratio": outage_cycles / samples,
            "disarmed_ratio": disarmed_cycles / samples,
            "error_ratio": error_cycles / samples,
            "sample_count": samples,
        }

    def observe_cycle(
        self,
        *,
        risk_rejects: int,
        stale_rejects: int,
        outage_cycle: bool,
        disarmed_cycle: bool,
        error_cycle: bool,
    ) -> OperatingModeSnapshot:
        previous = self._state
        if not self.enabled:
            self._state = MODE_NORMAL
            return OperatingModeSnapshot(
                state=self._state,
                previous_state=previous,
                changed=self._state != previous,
                reason="disabled",
                stale_reject_ratio=0.0,
                outage_ratio=0.0,
                disarmed_ratio=0.0,
                error_ratio=0.0,
                sample_count=0,
                risk_reject_count=0,
                stale_reject_count=0,
            )

        self._history.append(
            {
                "risk_rejects": float(max(0, int(risk_rejects))),
                "stale_rejects": float(max(0, int(stale_rejects))),
                "outage_cycle": 1.0 if outage_cycle else 0.0,
                "disarmed_cycle": 1.0 if disarmed_cycle else 0.0,
                "error_cycle": 1.0 if error_cycle else 0.0,
            }
        )

        metrics = self._snapshot_metrics()
        stale_ratio = metrics["stale_reject_ratio"]
        outage_ratio = metrics["outage_ratio"]
        disarmed_ratio = metrics["disarmed_ratio"]
        error_ratio = metrics["error_ratio"]
        sample_count = int(metrics["sample_count"])
        risk_reject_count = int(sum(row["risk_rejects"] for row in self._history))
        stale_reject_count = int(sum(row["stale_rejects"] for row in self._history))
        caution_stale_ratio_eligible = (
            risk_reject_count >= self.caution_min_risk_reject_count
            and stale_reject_count >= self.caution_min_stale_reject_count
        )
        maker_only_stale_ratio_eligible = (
            risk_reject_count >= self.maker_only_min_risk_reject_count
            and stale_reject_count >= self.maker_only_min_stale_reject_count
        )

        severe = (
            (maker_only_stale_ratio_eligible and stale_ratio >= self.maker_only_stale_reject_ratio)
            or outage_ratio >= self.maker_only_outage_ratio
            or disarmed_ratio >= self.maker_only_disarmed_ratio
            or error_ratio >= self.maker_only_error_ratio
        )
        moderate = (
            (caution_stale_ratio_eligible and stale_ratio >= self.caution_stale_reject_ratio)
            or outage_ratio >= self.caution_outage_ratio
            or disarmed_ratio >= self.caution_disarmed_ratio
            or error_ratio >= self.caution_error_ratio
        )
        healthy = not moderate and not severe
        if healthy:
            self._healthy_streak += 1
        else:
            self._healthy_streak = 0
        if severe:
            self._severe_streak += 1
        else:
            self._severe_streak = 0

        reason = "hold"
        if self._state == MODE_NORMAL:
            if severe:
                self._state = MODE_MAKER_ONLY
                reason = "severe_from_normal"
            elif moderate:
                self._state = MODE_CAUTIOUS
                reason = "moderate_from_normal"
        elif self._state == MODE_CAUTIOUS:
            if severe:
                self._state = MODE_MAKER_ONLY
                reason = "severe_from_cautious"
            elif self._healthy_streak >= self.recover_healthy_cycles:
                self._state = MODE_NORMAL
                reason = "recovered_to_normal"
        elif self._state == MODE_MAKER_ONLY:
            if self._severe_streak >= self.safe_stop_severe_cycles:
                self._state = MODE_SAFE_STOP
                reason = "persistent_severe_safe_stop"
            elif self._healthy_streak >= self.recover_healthy_cycles:
                self._state = MODE_CAUTIOUS
                reason = "recovered_to_cautious"
        else:
            reason = "safe_stop_latched"

        return OperatingModeSnapshot(
            state=self._state,
            previous_state=previous,
            changed=self._state != previous,
            reason=reason,
            stale_reject_ratio=stale_ratio,
            outage_ratio=outage_ratio,
            disarmed_ratio=disarmed_ratio,
            error_ratio=error_ratio,
            sample_count=sample_count,
            risk_reject_count=risk_reject_count,
            stale_reject_count=stale_reject_count,
        )

    def size_multiplier(self) -> float:
        if self._state == MODE_CAUTIOUS:
            return self.cautious_size_mult
        if self._state == MODE_MAKER_ONLY:
            return self.maker_only_size_mult
        if self._state == MODE_SAFE_STOP:
            return 0.0
        return 1.0

    def spread_multiplier(self) -> float:
        if self._state == MODE_CAUTIOUS:
            return self.cautious_spread_mult
        if self._state == MODE_MAKER_ONLY:
            return self.maker_only_spread_mult
        if self._state == MODE_SAFE_STOP:
            return self.maker_only_spread_mult
        return 1.0
