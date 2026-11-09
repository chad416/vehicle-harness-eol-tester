"""Result types shared by all test steps (F17)."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


@dataclass
class Finding:
    category: str                     # OPEN, SHORT, MISWIRE, ..., ACTIVE_DTC
    detail: str
    nets: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    wires: list[str] = field(default_factory=list)   # candidate wires
    alt_pins: list[str] = field(default_factory=list)  # equally likely alternative (miswire)
    ecu: str | None = None
    value: float | str | None = None


@dataclass
class Measurement:
    name: str
    value: float | str | None
    unit: str = ""
    low: float | str | None = None
    high: float | str | None = None
    ok: bool = True


@dataclass
class StepResult:
    step: str
    title: str
    status: str
    measurements: list[Measurement] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0.0
    note: str = ""

    @classmethod
    def blocked(cls, step: str, title: str, why: str) -> "StepResult":
        return cls(step, title, BLOCKED, note=why)

    def finish(self) -> "StepResult":
        if self.status != BLOCKED:
            self.status = FAIL if self.findings else PASS
        return self


def _clean(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return str(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def to_jsonable(obj) -> dict:
    return _clean(asdict(obj))
