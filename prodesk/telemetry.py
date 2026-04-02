from __future__ import annotations

import dataclasses
from typing import Dict


@dataclasses.dataclass
class Telemetry:
    counters: Dict[str, int] = dataclasses.field(default_factory=dict)
    gauges: Dict[str, float] = dataclasses.field(default_factory=dict)

    def incr(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> Dict[str, float]:
        row: Dict[str, float] = {}
        for key, value in self.counters.items():
            row[f"counter.{key}"] = float(value)
        for key, value in self.gauges.items():
            row[f"gauge.{key}"] = value
        return row

