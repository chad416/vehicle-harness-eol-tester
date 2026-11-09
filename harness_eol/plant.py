"""The simulated product: a built cab (harness network + fitted modules) and the fault
catalogue that can be injected into it (SPEC section 5)."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .data import CHASSIS, Variant, is_pin
from .j1939 import Dtc
from .network import Network
from .results import Finding

SHORT_OHM = 0.05


@dataclass
class Module:
    ecu: str
    conn: str
    sa: int
    function: int
    sw: str
    termination: bool
    idle_a: float
    fitted: bool = True
    dtc: Dtc | None = None
    cycle_factor: float = 1.0
    no_response: bool = False
    remote: bool = False
    alive_stuck: bool = False


@dataclass(frozen=True)
class Fault:
    kind: str
    args: tuple[str, ...] = ()

    @property
    def spec(self) -> str:
        return self.kind + (":" + "/".join(self.args) if self.args else "")

    def __str__(self) -> str:
        return self.spec


KINDS = ("open", "highres", "short", "leak", "miswire", "damaged", "stud", "noterm", "extraterm",
         "idle", "unplugged", "addr", "conflict", "cycle", "dtc", "remote", "alive", "sw", "noresp")


def parse_fault(spec: str) -> Fault:
    kind, _, rest = spec.partition(":")
    if kind not in KINDS:
        raise ValueError(f"unknown fault kind {kind!r} (known: {', '.join(KINDS)})")
    return Fault(kind, tuple(rest.split("/")) if rest else ())


@dataclass
class Cab:
    variant: Variant
    network: Network
    modules: dict[str, Module]
    faults: list[Fault] = field(default_factory=list)

    @classmethod
    def build(cls, variant: Variant, faults: list[Fault | str] = ()) -> "Cab":
        net = Network.from_variant(variant)
        currents = {c["module"]: c["nominal_a"] for c in variant.recipe["power"]["circuits"]}
        mods = {
            n["ecu"]: Module(n["ecu"], n["conn"], n["sa"], n["function"], n["sw"], n["termination"],
                             currents[n["ecu"]])
            for n in variant.nodes
        }
        cab = cls(variant, net, mods, [])
        for f in faults:
            cab.inject(parse_fault(f) if isinstance(f, str) else f)
        return cab

    def inject(self, f: Fault) -> None:
        apply_fault(self, f)
        self.faults.append(f)


def _wire_end_pin(v: Variant, wire_id: str) -> str:
    w = v.wire_by_id[wire_id]
    return w.a if is_pin(w.a) else w.b if is_pin(w.b) else w.a


def apply_fault(cab: Cab, f: Fault) -> None:
    v, n, m, a = cab.variant, cab.network, cab.modules, f.args
    k = f.kind
    if k == "open":
        n.remove(a[0])
    elif k == "highres":
        n.add_series(a[0], float(a[1]))
    elif k == "short":
        n.add(a[0], a[1], SHORT_OHM, "SHORT")
    elif k == "leak":
        n.add(_wire_end_pin(v, a[0]), CHASSIS, float(a[1]), f"LEAK:{a[0]}")
    elif k == "miswire":
        n.swap_pins(a[0], a[1])
    elif k == "damaged":
        n.damaged[a[0]] = float(a[1])
    elif k == "stud":
        n.add_series("STUD", float(a[0]))
    elif k == "noterm":
        m[a[0]].termination = False
    elif k == "extraterm":
        m[a[0]].termination = True
    elif k == "idle":
        m[a[0]].idle_a = float(a[1])
    elif k == "unplugged":
        m[a[0]].fitted = False
    elif k == "addr":
        m[a[0]].sa = int(a[1], 0)
    elif k == "conflict":
        m[a[0]].sa = m[a[1]].sa
    elif k == "cycle":
        m[a[0]].cycle_factor = float(a[1])
    elif k == "dtc":
        m[a[0]].dtc = Dtc(int(a[1]), int(a[2]))
    elif k == "remote":
        m["RCG"].remote = True
    elif k == "alive":
        m["RCG"].alive_stuck = True
    elif k == "sw":
        m[a[0]].sw = a[1]
    elif k == "noresp":
        m[a[0]].no_response = True


# --- matching findings to faults (repair by diagnosis, batch metrics) -------------
def _net(v: Variant, x: str) -> str | None:
    if x in v.wire_by_id:
        return v.wire_by_id[x].net
    return v.pin_net.get(x)


def match(f: Fault, fd: Finding, v: Variant) -> str | None:
    """'exact' if the finding names the fault's location, 'partial' if it points to the
    right area (net, bus) only, None if unrelated."""
    k, a, c = f.kind, f.args, fd.category
    nets = set(fd.nets)
    if k == "open":
        if c == "OPEN" and a[0] in fd.wires:
            return "exact"
        return "partial" if c == "OPEN" and _net(v, a[0]) in nets else None
    if k == "highres":
        if c == "HIGH_RES" and a[0] in fd.wires:
            return "exact"
        return "partial" if c in ("HIGH_RES", "BOND") and _net(v, a[0]) in nets else None
    if k == "short":
        pair = {_net(v, a[0]), _net(v, a[1])}
        if c == "SHORT" and pair <= nets:
            return "exact"
        return "partial" if c in ("SHORT", "LEAK", "IR_LOW") and pair & nets else None
    if k == "leak":
        return "exact" if c in ("LEAK", "IR_LOW") and _net(v, a[0]) in nets else None
    if k == "miswire":
        if c == "MISWIRE" and {a[0], a[1]} in (set(fd.pins), set(fd.alt_pins)):
            return "exact"
        return "partial" if c in ("OPEN", "SHORT") and {_net(v, a[0]), _net(v, a[1])} & nets else None
    if k == "damaged":
        return "exact" if c in ("BREAKDOWN", "IR_LOW") and _net(v, a[0]) in nets else None
    if k == "stud":
        if c == "BOND" or (c == "HIGH_RES" and "STUD" in fd.wires):
            return "exact"
        return "partial" if c == "HIGH_RES" and "GND" in nets else None
    if k in ("noterm", "extraterm"):
        if c == "TERMINATION_CONFIG" and fd.ecu == a[0]:
            return "exact"
        return "partial" if c == "TERMINATION" else None
    if k == "idle":
        return "exact" if c in ("OVERCURRENT", "UNDERCURRENT") and fd.ecu == a[0] else None
    if k == "unplugged":
        if c in ("UNDERCURRENT", "NODE_MISSING") and fd.ecu == a[0]:
            return "exact"
        return "partial" if c == "TERMINATION" else None
    if k == "addr":
        if c == "WRONG_ADDRESS" and fd.ecu == a[0]:
            return "exact"
        return "partial" if c == "NODE_MISSING" and fd.ecu == a[0] else None
    if k == "conflict":
        if c in ("ADDRESS_CONFLICT", "WRONG_ADDRESS") and a[0] in (fd.ecu, fd.value):
            return "exact"
        return "partial" if c in ("ADDRESS_CONFLICT", "NODE_MISSING") and a[1] in (fd.ecu, fd.value) else None
    if k == "cycle":
        return "exact" if c == "CYCLE_TIME" and fd.ecu == a[0] else None
    if k == "dtc":
        return "exact" if c == "ACTIVE_DTC" and fd.ecu == a[0] else None
    if k == "remote":
        return "exact" if c == "SIGNAL_RANGE" and fd.ecu == "RCG" else None
    if k == "alive":
        return "exact" if c == "ALIVE_COUNTER" and fd.ecu == "RCG" else None
    if k == "sw":
        return "exact" if c == "SW_VERSION" and fd.ecu == a[0] else None
    if k == "noresp":
        return "exact" if c == "NO_RESPONSE" and fd.ecu == a[0] else None
    return None


_STUD_LIMIT: dict[str, float] = {}


def stud_limit(v: Variant) -> float:
    """Smallest added stud resistance the bond check must catch: bond limit minus the
    worst nominal pin-to-chassis resistance of the bonded net."""
    if v.recipe_sha256 not in _STUD_LIMIT:
        rc = v.recipe["continuity"]
        pins = [p for n in rc["chassis_bonded_nets"] for p in v.net_pins[n] if p != CHASSIS]
        r = Network.from_variant(v).resistance_matrix(pins + [CHASSIS])
        _STUD_LIMIT[v.recipe_sha256] = rc["bond_max_ohm"] - float(r[:-1, -1].max())
    return _STUD_LIMIT[v.recipe_sha256]


def above_limit(f: Fault, v: Variant) -> bool:
    """Should the recipe limits catch this fault? (False = below the detection limit.)
    A 10 % margin keeps measurement noise at the limit out of the escape count."""
    r = v.recipe
    if f.kind == "highres":
        return float(f.args[1]) >= 1.1 * r["continuity"]["max_extra_ohm"]
    if f.kind == "leak":
        return float(f.args[1]) <= 0.9 * r["insulation"]["ir_min_ohm"]
    if f.kind == "stud":
        return float(f.args[0]) >= 1.1 * stud_limit(v)
    if f.kind == "damaged":
        return float(f.args[1]) < r["insulation"]["hipot_v"]
    return True


def severity(f: Fault) -> float | None:
    """Fault size for the detection-vs-size chart (ohm), or None."""
    if f.kind in ("highres", "leak"):
        return float(f.args[1])
    if f.kind == "stud":
        return float(f.args[0])
    return None


# --- random faults for the batch run --------------------------------------------
def applicable_kinds(v: Variant) -> list[str]:
    ks = list(KINDS)
    if "RCG" not in [n["ecu"] for n in v.nodes]:
        ks = [k for k in ks if k not in ("remote", "alive")]
    return ks


def _logu(rng: random.Random, lo: float, hi: float) -> float:
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def sample_fault(v: Variant, rng: random.Random, kind: str) -> Fault:
    bonded = set(v.recipe["continuity"]["chassis_bonded_nets"])
    wires = [w.wire for w in v.wires]
    iso_wires = [w.wire for w in v.wires if w.net not in bonded]
    ecus = [n["ecu"] for n in v.nodes]
    if kind == "open":
        return Fault(kind, (rng.choice(wires),))
    if kind == "highres":
        return Fault(kind, (rng.choice(wires), f"{_logu(rng, 0.02, 2.0):.3f}"))
    if kind == "short":
        pins = v.probe_pins
        while True:
            p, q = rng.sample(pins, 2)
            if v.pin_net[p] != v.pin_net[q]:
                return Fault(kind, (p, q))
    if kind == "leak":
        return Fault(kind, (rng.choice(iso_wires), f"{_logu(rng, 1e4, 1e9):.4g}"))
    if kind == "miswire":
        by_conn: dict[str, list[str]] = {}
        for p in v.probe_pins:
            if p != CHASSIS:
                by_conn.setdefault(p.split(":")[0], []).append(p)
        while True:
            pins = by_conn[rng.choice(sorted(by_conn))]
            if len(pins) < 2:
                continue
            p, q = rng.sample(pins, 2)
            if v.pin_net[p] != v.pin_net[q]:
                return Fault(kind, (p, q))
    if kind == "damaged":
        return Fault(kind, (rng.choice(iso_wires), f"{rng.uniform(550, 950):.0f}"))
    if kind == "stud":
        return Fault(kind, (f"{_logu(rng, 0.02, 1.0):.3f}",))
    if kind == "noterm":
        return Fault(kind, (rng.choice([n["ecu"] for n in v.nodes if n["termination"]]),))
    if kind == "extraterm":
        return Fault(kind, (rng.choice([n["ecu"] for n in v.nodes if not n["termination"]]),))
    if kind == "idle":
        c = rng.choice(v.recipe["power"]["circuits"])
        factor = rng.uniform(1.6, 3.0) if rng.random() < 0.5 else rng.uniform(0.05, 0.4)
        return Fault(kind, (c["module"], f"{c['nominal_a'] * factor:.3f}"))
    if kind == "unplugged":
        return Fault(kind, (rng.choice([e for e in ecus if e != "PDU"]),))
    if kind == "addr":
        used = {n["sa"] for n in v.nodes}
        sa = rng.choice([s for s in range(0x40, 0x80) if s not in used])
        return Fault(kind, (rng.choice(ecus), hex(sa)))
    if kind == "conflict":
        e, o = rng.sample(ecus, 2)
        return Fault(kind, (e, o))
    if kind == "cycle":
        factor = rng.uniform(1.3, 2.0) if rng.random() < 0.5 else rng.uniform(0.5, 0.8)
        return Fault(kind, (rng.choice(ecus), f"{factor:.2f}"))
    if kind == "dtc":
        return Fault(kind, (rng.choice(ecus), str(rng.randint(520192, 524287)), str(rng.randint(1, 19))))
    if kind in ("remote", "alive"):
        return Fault(kind)
    if kind == "sw":
        e = rng.choice(ecus)
        ma, mi, pa = (int(x) for x in v.node(e)["sw"].split("."))
        return Fault(kind, (e, f"{ma}.{mi + rng.randint(1, 3)}.{pa}"))
    if kind == "noresp":
        return Fault(kind, (rng.choice(ecus),))
    raise ValueError(kind)


def fault_targets(f: Fault) -> set[str]:
    """Things a fault touches, so that a batch cab doesn't get two faults on one item."""
    if f.kind in ("remote", "alive"):
        return {"RCG"}
    if f.kind == "stud":
        return {"STUD"}
    return set(f.args[:2]) if f.kind in ("short", "miswire", "conflict") else {f.args[0]}
