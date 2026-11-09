"""Test sequence with interlocks (F09) and the per-serial record (F17)."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from . import electrical as E
from .canbus import LiveBus, VirtualTimeBus
from .canchecks import run_can_step
from .ecu_sim import make_ecus
from .plant import Cab
from .results import BLOCKED, FAIL, PASS, StepResult, to_jsonable

HV_BLOCKERS = {"SHORT", "MISWIRE"}
LIVE_GAP_ALLOW_S = 0.15   # scheduling allowance for the Python ECU thread on live buses (SPEC F13)


@dataclass
class Record:
    serial: str
    variant: str
    recipe_sha256: str
    verdict: str
    steps: list[StepResult]
    bus: str
    tester_version: str = __version__
    injected_faults: list[str] = field(default_factory=list)   # simulation only, for evaluation
    frames: list = field(default_factory=list, repr=False)
    handling_s: float = 0.0   # connect adapters, fit/remove modules (modelled)

    @property
    def findings(self):
        return [f for s in self.steps for f in s.findings]

    @property
    def test_time_s(self) -> float:
        return self.handling_s + sum(s.duration_s for s in self.steps)

    def to_json(self) -> dict:
        return {
            "serial": self.serial,
            "variant": self.variant,
            "recipe_sha256": self.recipe_sha256,
            "tester_version": self.tester_version,
            "bus": self.bus,
            "verdict": self.verdict,
            "test_time_s": round(self.test_time_s, 2),
            "steps": [to_jsonable(s) for s in self.steps],
            "simulation": {"injected_faults": self.injected_faults},
        }

    def save(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{self.serial}.json"
        p.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        return p


def make_bus(cab: Cab, bus: str, rng: random.Random):
    if bus == "vtime":
        return VirtualTimeBus(make_ecus(cab.modules, cab.variant.db, rng))
    interface, _, channel = bus.partition(":")
    return LiveBus(make_ecus(cab.modules, cab.variant.db, rng, jitter_s=0.0), interface,
                   channel or ("vcan0" if interface == "socketcan" else "eol"))


def run_cab(cab: Cab, serial: str, seed: int = 0, bus: str = "vtime") -> Record:
    """Run the whole sequence on one cab. `bus` is 'vtime', 'virtual[:channel]' or 'socketcan[:vcan0]'."""
    rng = random.Random(f"{seed}-{serial}")
    v = cab.variant
    steps: list[StepResult] = []

    s1 = E.continuity(cab, rng)
    steps.append(s1)
    hv_ok = not any(f.category in HV_BLOCKERS for f in s1.findings)
    if hv_ok:
        s2 = E.insulation(cab, rng)
        s3 = E.hipot(cab, s2)
    else:
        why = "S1 found a short or miswire: no high voltage on this harness"
        s2 = StepResult.blocked("S2", "Insulation resistance", why)
        s3 = StepResult.blocked("S3", "Dielectric withstand", why)
    steps += [s2, s3]
    s4 = E.termination(cab, rng)
    steps.append(s4)

    if all(s.status == PASS for s in (s1, s2, s3)):
        s5 = E.power_up(cab, rng)
    else:
        s5 = StepResult.blocked("S5", "Power-up", "harness or insulation not passed: cab is not energised")
    steps.append(s5)

    frames = []
    if s5.status != BLOCKED and not any(f.category == "OVERCURRENT" for f in s5.findings):
        b = make_bus(cab, bus, rng)
        try:
            s6, frames = run_can_step(b, v, gap_allow_s=0.0 if bus == "vtime" else LIVE_GAP_ALLOW_S)
        finally:
            b.close()
    else:
        why = "power-up blocked" if s5.status == BLOCKED else "overcurrent at power-up"
        s6 = StepResult.blocked("S6", "CAN network", why)
    steps.append(s6)

    verdict = PASS if all(s.status == PASS for s in steps) else FAIL
    return Record(serial, v.name, v.recipe_sha256, verdict, steps, bus,
                  injected_faults=[f.spec for f in cab.faults], frames=frames,
                  handling_s=v.recipe["timing"]["handling_s"])
