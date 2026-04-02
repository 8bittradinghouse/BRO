from __future__ import annotations

import collections
import statistics
import time
from typing import Deque, Dict, Optional, Tuple


class RealizedVolTracker:
    """Tracks short-horizon midpoint volatility per token."""

    def __init__(self, window_sec: float):
        self.window_sec = max(1.0, float(window_sec))
        self._history: Dict[str, Deque[Tuple[float, float]]] = {}

    def update(self, token_id: str, midpoint: Optional[float]) -> Optional[float]:
        if midpoint is None or midpoint <= 0:
            return None
        now = time.monotonic()
        buf = self._history.setdefault(token_id, collections.deque())
        buf.append((now, midpoint))
        cutoff = now - self.window_sec
        while buf and buf[0][0] < cutoff:
            buf.popleft()
        if len(buf) < 3:
            return None

        returns = []
        prev = buf[0][1]
        for _, price in list(buf)[1:]:
            if prev > 0:
                returns.append((price - prev) / prev)
            prev = price
        if len(returns) < 2:
            return None
        return float(statistics.pstdev(returns))

    def prune_tokens(self, active_tokens: set[str]) -> None:
        for token_id in list(self._history.keys()):
            if token_id not in active_tokens:
                self._history.pop(token_id, None)
