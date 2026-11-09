"""Load and validate a cab variant: connectors, netlist, recipe and DBC (F01)."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import cantools
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RHO_CU = 0.0178  # ohm * mm^2 / m
CRIMP_OHM = 0.0005
CHASSIS = "CHASSIS"
PIN_RE = re.compile(r"^(X\d+):(\d+)$")
NODE_RE = re.compile(r"^(SP\d+|GS\d+)$")


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class Connector:
    conn: str
    description: str
    pins: int
    module: str


@dataclass(frozen=True)
class Wire:
    wire: str
    net: str
    a: str
    b: str
    length_m: float
    csa_mm2: float
    colour: str

    @property
    def r_nominal(self) -> float:
        return RHO_CU * self.length_m / self.csa_mm2 + 2 * CRIMP_OHM


def is_pin(node: str) -> bool:
    return PIN_RE.match(node) is not None


@dataclass
class Variant:
    name: str
    connectors: dict[str, Connector]
    wires: list[Wire]
    recipe: dict
    db: cantools.database.Database
    recipe_sha256: str
    wire_by_id: dict[str, Wire] = field(init=False)

    def __post_init__(self) -> None:
        self.wire_by_id = {w.wire: w for w in self.wires}

    @cached_property
    def nets(self) -> dict[str, list[Wire]]:
        out: dict[str, list[Wire]] = {}
        for w in self.wires:
            out.setdefault(w.net, []).append(w)
        return out

    @cached_property
    def pin_net(self) -> dict[str, str]:
        """Probe-able pin -> expected net. CHASSIS belongs to the chassis-bonded net."""
        out: dict[str, str] = {}
        for w in self.wires:
            for n in (w.a, w.b):
                if is_pin(n):
                    out[n] = w.net
        bonded = self.recipe["continuity"]["chassis_bonded_nets"]
        if bonded:
            out[CHASSIS] = bonded[0]
        return out

    @cached_property
    def net_pins(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for p, n in sorted(self.pin_net.items()):
            out.setdefault(n, []).append(p)
        return out

    @cached_property
    def probe_pins(self) -> list[str]:
        return sorted(self.pin_net)

    @property
    def nodes(self) -> list[dict]:
        return self.recipe["can"]["nodes"]

    def node(self, ecu: str) -> dict:
        for n in self.nodes:
            if n["ecu"] == ecu:
                return n
        raise KeyError(ecu)

    def can_pins(self, conn: str) -> tuple[str, str]:
        """CAN_H and CAN_L pin of a connector."""
        h = [p for p in self.net_pins["CAN_H"] if p.startswith(conn + ":")]
        lo = [p for p in self.net_pins["CAN_L"] if p.startswith(conn + ":")]
        if len(h) != 1 or len(lo) != 1:
            raise DataError(f"{conn}: expected one CAN_H and one CAN_L pin")
        return h[0], lo[0]

    def insulation_groups(self) -> list[str]:
        bonded = set(self.recipe["continuity"]["chassis_bonded_nets"])
        return [n for n in sorted(self.nets) if n not in bonded]


def load_variant(name: str, data_dir: Path = DATA) -> Variant:
    vdir = data_dir / "variants" / name
    if not vdir.is_dir():
        raise DataError(f"unknown variant {name!r}")
    connectors: dict[str, Connector] = {}
    with open(vdir / "connectors.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            connectors[r["conn"]] = Connector(r["conn"], r["description"], int(r["pins"]), r["module"])
    wires = []
    with open(vdir / "netlist.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            wires.append(Wire(r["wire"], r["net"], r["from"], r["to"], float(r["length_m"]),
                              float(r["csa_mm2"]), r["colour"]))
    raw = (vdir / "recipe.yaml").read_bytes()
    recipe = yaml.safe_load(raw)
    db = cantools.database.load_file(str(data_dir / "protocol" / "cab.dbc"))
    v = Variant(name, connectors, wires, recipe, db, hashlib.sha256(raw).hexdigest())
    validate(v)
    return v


def validate(v: Variant) -> None:
    errors = []
    seen_ids = set()
    pin_owner: dict[str, str] = {}
    for w in v.wires:
        if w.wire in seen_ids:
            errors.append(f"duplicate wire id {w.wire}")
        seen_ids.add(w.wire)
        if w.length_m <= 0 or w.csa_mm2 <= 0:
            errors.append(f"{w.wire}: length and cross-section must be > 0")
        for n in (w.a, w.b):
            m = PIN_RE.match(n)
            if m:
                conn, pin = m.group(1), int(m.group(2))
                if conn not in v.connectors:
                    errors.append(f"{w.wire}: unknown connector {conn}")
                elif not 1 <= pin <= v.connectors[conn].pins:
                    errors.append(f"{w.wire}: {n} outside 1..{v.connectors[conn].pins}")
                if pin_owner.setdefault(n, w.net) != w.net:
                    errors.append(f"{n} used by nets {pin_owner[n]} and {w.net}")
            elif not NODE_RE.match(n):
                errors.append(f"{w.wire}: bad node name {n!r}")
    # each net must be connected
    for net, ws in v.nets.items():
        adj: dict[str, set[str]] = {}
        for w in ws:
            adj.setdefault(w.a, set()).add(w.b)
            adj.setdefault(w.b, set()).add(w.a)
        start = next(iter(adj))
        stack, seen = [start], {start}
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if seen != set(adj):
            errors.append(f"net {net} is not connected in the netlist")
    for section in ("continuity", "insulation", "can_termination"):
        for key, val in v.recipe[section].items():
            if key.endswith(("_ohm", "_v", "_a", "_s", "_tol")) and not isinstance(val, (int, float)):
                errors.append(f"recipe {section}.{key} must be a number, got {val!r}")
    senders = {s for m in v.db.messages for s in m.senders}
    for node in v.nodes:
        if node["conn"] not in v.connectors:
            errors.append(f"node {node['ecu']}: unknown connector {node['conn']}")
        if node["ecu"] not in senders:
            errors.append(f"node {node['ecu']}: no messages in the DBC")
    for s in v.recipe["can"]["signals"]:
        msg = v.db.get_message_by_name(s["msg"])
        if s["signal"] not in [sig.name for sig in msg.signals]:
            errors.append(f"signal {s['msg']}.{s['signal']} not in the DBC")
    if errors:
        raise DataError("; ".join(errors))
