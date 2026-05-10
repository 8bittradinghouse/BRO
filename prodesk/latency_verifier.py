from __future__ import annotations

import collections
import dataclasses
import math
import statistics
from typing import Deque, Dict, Iterable, List, Optional, Sequence

from .common import clamp, parse_float


STATE_DISARMED = "disarmed"
STATE_PROBATION = "probation"
STATE_ARMED = "armed"


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    vals = sorted(values)
    idx = max(0.0, min(1.0, q)) * (len(vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(vals) - 1)
    if lo == hi:
        return float(vals[lo])
    frac = idx - lo
    return float(vals[lo] * (1.0 - frac) + vals[hi] * frac)


def _safe_ratio(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)


@dataclasses.dataclass(frozen=True)
class TokenLatencyStats:
    token_id: str
    sample_count: int
    median_lag_ms: float
    p90_lag_ms: float
    p95_lag_ms: float
    hit_rate: float
    median_ingest_lag_ms: float
    median_source_to_book_ms: float
    stddev_lag_ms: float
    drift_median_drop_ms: float
    drift_hit_rate_drop: float
    confidence_score: float


@dataclasses.dataclass(frozen=True)
class LatencySnapshot:
    state: str
    previous_state: str
    changed: bool
    reason: str
    sample_count: int
    token_count: int
    median_lag_ms: float
    p90_lag_ms: float
    p95_lag_ms: float
    hit_rate: float
    armed: bool
    probation: bool
    disarmed: bool


class _TokenWindow:
    def __init__(self, maxlen: int):
        self.lag_ms: Deque[float] = collections.deque(maxlen=maxlen)
        self.ingest_lag_ms: Deque[float] = collections.deque(maxlen=maxlen)
        self.source_to_book_ms: Deque[float] = collections.deque(maxlen=maxlen)


class LatencyVerifier:
    def __init__(self, cfg: Dict[str, object]):
        self.enabled = bool(cfg.get("enabled", True))
        self.require_armed_for_maker = bool(cfg.get("require_armed_for_maker", True))
        self.require_armed_for_taker = bool(cfg.get("require_armed_for_taker", True))
        self.log_sample_events = bool(cfg.get("log_sample_events", False))

        self.window_samples = max(10, int(cfg.get("window_samples", 400)))
        self.min_samples = max(1, int(cfg.get("min_samples", 80)))
        self.hit_threshold_ms = max(0.0, float(cfg.get("hit_threshold_ms", 120.0)))

        self.armed_min_median_ms = max(0.0, float(cfg.get("armed_min_median_ms", 120.0)))
        self.armed_min_hit_rate = max(0.0, min(1.0, float(cfg.get("armed_min_hit_rate", 0.6))))
        self.probation_min_median_ms = max(
            0.0, float(cfg.get("probation_min_median_ms", self.armed_min_median_ms * 0.7))
        )
        self.probation_min_hit_rate = max(
            0.0, min(1.0, float(cfg.get("probation_min_hit_rate", self.armed_min_hit_rate * 0.7)))
        )

        self.arm_consecutive_cycles = max(1, int(cfg.get("arm_consecutive_cycles", 2)))
        self.disarm_consecutive_cycles = max(1, int(cfg.get("disarm_consecutive_cycles", 2)))
        self.max_sample_lag_ms = max(1.0, float(cfg.get("max_sample_lag_ms", 20_000.0)))
        self.score_enabled = bool(cfg.get("score_enabled", True))
        self.score_min_for_maker = clamp(float(cfg.get("score_min_for_maker", 0.35)), 0.0, 1.0)
        self.score_min_for_taker = clamp(float(cfg.get("score_min_for_taker", 0.60)), 0.0, 1.0)
        self.score_size_floor = max(0.01, float(cfg.get("score_size_floor", 0.35)))
        self.score_size_ceiling = max(self.score_size_floor, float(cfg.get("score_size_ceiling", 1.25)))
        self.drift_window_samples = max(10, int(cfg.get("drift_window_samples", 80)))
        self.drift_max_median_drop_ms = max(1.0, float(cfg.get("drift_max_median_drop_ms", 40.0)))
        self.drift_max_hit_rate_drop = clamp(float(cfg.get("drift_max_hit_rate_drop", 0.20)), 0.01, 1.0)

        self._windows_by_token: Dict[str, _TokenWindow] = {}
        self._state = STATE_DISARMED
        self._good_streak = 0
        self._bad_streak = 0

    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-clamp(x, -12.0, 12.0)))

    def _drift_stats(self, lags: List[float]) -> tuple[float, float]:
        if len(lags) < (self.drift_window_samples * 2):
            return 0.0, 0.0
        recent = lags[-self.drift_window_samples :]
        baseline = lags[-(self.drift_window_samples * 2) : -self.drift_window_samples]
        if not baseline or not recent:
            return 0.0, 0.0
        baseline_median = float(statistics.median(baseline))
        recent_median = float(statistics.median(recent))
        baseline_hits = _safe_ratio(sum(1 for lag in baseline if lag >= self.hit_threshold_ms), len(baseline))
        recent_hits = _safe_ratio(sum(1 for lag in recent if lag >= self.hit_threshold_ms), len(recent))
        return max(0.0, baseline_median - recent_median), max(0.0, baseline_hits - recent_hits)

    def _confidence_score(self, *, sample_count: int, median_lag_ms: float, hit_rate: float, stddev_lag_ms: float, drift_median_drop_ms: float, drift_hit_rate_drop: float) -> float:
        if not self.score_enabled:
            return 1.0
        sample_component = clamp(sample_count / max(1.0, float(self.min_samples)), 0.0, 1.0)
        hit_component = clamp(hit_rate / max(0.001, self.armed_min_hit_rate), 0.0, 1.0)
        median_component = clamp(median_lag_ms / max(1.0, self.armed_min_median_ms), 0.0, 1.0)
        variability_ratio = stddev_lag_ms / max(1.0, median_lag_ms)
        stability_component = 1.0 - clamp(variability_ratio, 0.0, 1.0)
        z = (median_lag_ms - self.hit_threshold_ms) / max(1.0, stddev_lag_ms / max(1.0, math.sqrt(sample_count)))
        significance_component = self._sigmoid(z)
        raw = (
            (0.20 * sample_component)
            + (0.25 * hit_component)
            + (0.25 * median_component)
            + (0.15 * stability_component)
            + (0.15 * significance_component)
        )
        drift_penalty = max(
            clamp(drift_median_drop_ms / self.drift_max_median_drop_ms, 0.0, 1.0),
            clamp(drift_hit_rate_drop / self.drift_max_hit_rate_drop, 0.0, 1.0),
        )
        # Drift should degrade confidence, but it should not become a total
        # override when the same token still has strong sample count, median,
        # and hit-rate proof on the active path.
        return clamp(raw - (0.5 * drift_penalty), 0.0, 1.0)

    def prune_tokens(self, active_tokens: Iterable[str]) -> None:
        keep = {str(token_id) for token_id in active_tokens}
        self._windows_by_token = {
            token_id: window for token_id, window in self._windows_by_token.items() if token_id in keep
        }

    def observe(
        self,
        *,
        token_id: str,
        lag_ms: float,
        ingest_lag_ms: Optional[float] = None,
        source_to_book_ms: Optional[float] = None,
    ) -> bool:
        parsed = parse_float(lag_ms)
        if parsed is None:
            return False
        value = float(parsed)
        if value < 0.0 or value > self.max_sample_lag_ms:
            return False

        key = str(token_id).strip()
        if not key:
            return False

        window = self._windows_by_token.get(key)
        if window is None:
            window = _TokenWindow(maxlen=self.window_samples)
            self._windows_by_token[key] = window
        window.lag_ms.append(value)

        ingest = parse_float(ingest_lag_ms)
        if ingest is not None and ingest >= 0.0:
            window.ingest_lag_ms.append(float(min(ingest, self.max_sample_lag_ms)))
        src_book = parse_float(source_to_book_ms)
        if src_book is not None and src_book >= 0.0:
            window.source_to_book_ms.append(float(min(src_book, self.max_sample_lag_ms)))
        return True

    def token_stats(self, token_id: str) -> Optional[TokenLatencyStats]:
        window = self._windows_by_token.get(str(token_id))
        if window is None or not window.lag_ms:
            return None
        lags = list(window.lag_ms)
        sample_count = len(lags)
        hits = sum(1 for lag in lags if lag >= self.hit_threshold_ms)
        ingest_vals = list(window.ingest_lag_ms)
        src_book_vals = list(window.source_to_book_ms)
        stddev_lag_ms = float(statistics.pstdev(lags)) if len(lags) > 1 else 0.0
        drift_median_drop_ms, drift_hit_rate_drop = self._drift_stats(lags)
        confidence_score = self._confidence_score(
            sample_count=sample_count,
            median_lag_ms=float(statistics.median(lags)),
            hit_rate=_safe_ratio(hits, sample_count),
            stddev_lag_ms=stddev_lag_ms,
            drift_median_drop_ms=drift_median_drop_ms,
            drift_hit_rate_drop=drift_hit_rate_drop,
        )
        return TokenLatencyStats(
            token_id=str(token_id),
            sample_count=sample_count,
            median_lag_ms=float(statistics.median(lags)),
            p90_lag_ms=_percentile(lags, 0.90),
            p95_lag_ms=_percentile(lags, 0.95),
            hit_rate=_safe_ratio(hits, sample_count),
            median_ingest_lag_ms=float(statistics.median(ingest_vals)) if ingest_vals else 0.0,
            median_source_to_book_ms=float(statistics.median(src_book_vals)) if src_book_vals else 0.0,
            stddev_lag_ms=stddev_lag_ms,
            drift_median_drop_ms=drift_median_drop_ms,
            drift_hit_rate_drop=drift_hit_rate_drop,
            confidence_score=confidence_score,
        )

    def token_is_verified(self, token_id: str) -> bool:
        stats = self.token_stats(token_id)
        if stats is None:
            return False
        return (
            stats.sample_count >= self.min_samples
            and stats.median_lag_ms >= self.armed_min_median_ms
            and stats.hit_rate >= self.armed_min_hit_rate
            and stats.confidence_score >= self.score_min_for_maker
        )

    def token_score(self, token_id: str) -> float:
        stats = self.token_stats(token_id)
        if stats is None:
            return 0.0
        return float(stats.confidence_score)

    def token_size_multiplier(self, token_id: str) -> float:
        score = self.token_score(token_id)
        return self.score_size_floor + (self.score_size_ceiling - self.score_size_floor) * clamp(score, 0.0, 1.0)

    def _aggregate_lags(self, token_ids: Optional[Sequence[str]]) -> List[float]:
        if token_ids is None:
            windows = self._windows_by_token.values()
        else:
            windows = [self._windows_by_token[token_id] for token_id in token_ids if token_id in self._windows_by_token]
        out: List[float] = []
        for window in windows:
            out.extend(window.lag_ms)
        return out

    def snapshot(self, *, active_tokens: Optional[Sequence[str]] = None) -> LatencySnapshot:
        previous_state = self._state
        if not self.enabled:
            self._state = STATE_ARMED
            return LatencySnapshot(
                state=self._state,
                previous_state=previous_state,
                changed=self._state != previous_state,
                reason="verifier_disabled",
                sample_count=0,
                token_count=0,
                median_lag_ms=0.0,
                p90_lag_ms=0.0,
                p95_lag_ms=0.0,
                hit_rate=0.0,
                armed=True,
                probation=False,
                disarmed=False,
            )

        active = [str(token_id) for token_id in active_tokens] if active_tokens is not None else None
        lags = self._aggregate_lags(active)
        sample_count = len(lags)
        token_count = 0
        if active is None:
            token_count = len(self._windows_by_token)
        else:
            token_count = sum(1 for token_id in active if token_id in self._windows_by_token)

        if sample_count == 0:
            self._good_streak = 0
            self._bad_streak += 1
            self._state = STATE_DISARMED
            reason = "no_samples"
            return LatencySnapshot(
                state=self._state,
                previous_state=previous_state,
                changed=self._state != previous_state,
                reason=reason,
                sample_count=0,
                token_count=token_count,
                median_lag_ms=0.0,
                p90_lag_ms=0.0,
                p95_lag_ms=0.0,
                hit_rate=0.0,
                armed=False,
                probation=False,
                disarmed=True,
            )

        median_lag_ms = float(statistics.median(lags))
        p90_lag_ms = _percentile(lags, 0.90)
        p95_lag_ms = _percentile(lags, 0.95)
        hits = sum(1 for lag in lags if lag >= self.hit_threshold_ms)
        hit_rate = _safe_ratio(hits, sample_count)

        armed_ready = (
            sample_count >= self.min_samples
            and median_lag_ms >= self.armed_min_median_ms
            and hit_rate >= self.armed_min_hit_rate
        )
        probation_ready = (
            sample_count >= self.min_samples
            and median_lag_ms >= self.probation_min_median_ms
            and hit_rate >= self.probation_min_hit_rate
        )

        if armed_ready:
            self._good_streak += 1
        else:
            self._good_streak = 0

        if armed_ready:
            self._bad_streak = 0
        else:
            self._bad_streak += 1

        if self._state != STATE_ARMED and self._good_streak >= self.arm_consecutive_cycles:
            self._state = STATE_ARMED
            reason = "armed_thresholds_met"
        elif self._state == STATE_ARMED and self._bad_streak >= self.disarm_consecutive_cycles:
            self._state = STATE_PROBATION if probation_ready else STATE_DISARMED
            reason = "armed_thresholds_lost"
        elif self._state == STATE_ARMED:
            reason = "armed_hold"
        elif probation_ready:
            self._state = STATE_PROBATION
            reason = "probation_thresholds_met"
        else:
            self._state = STATE_DISARMED
            if sample_count < self.min_samples:
                reason = "insufficient_samples"
            else:
                reason = "lag_edge_not_present"

        return LatencySnapshot(
            state=self._state,
            previous_state=previous_state,
            changed=self._state != previous_state,
            reason=reason,
            sample_count=sample_count,
            token_count=token_count,
            median_lag_ms=median_lag_ms,
            p90_lag_ms=p90_lag_ms,
            p95_lag_ms=p95_lag_ms,
            hit_rate=hit_rate,
            armed=self._state == STATE_ARMED,
            probation=self._state == STATE_PROBATION,
            disarmed=self._state == STATE_DISARMED,
        )
