"""Simulated control units. One class serves both buses: the virtual-time bus calls it
with simulated time, the live runner calls it with wall-clock time."""
from __future__ import annotations

import random

import cantools

from . import j1939 as J
from .plant import Module

DM1_PERIOD_S = 1.0

# Signal values a healthy unit reports at EOL (anything not listed is 0).
DEFAULTS = {
    "DASH_STATUS": {"backlight_pct": 40, "key_on": 1},
    "PDU_STATUS": {"supply_voltage": 27.0, "total_current": 4.3},
    "HVAC_STATUS": {"cabin_temp": 21.5, "fan_speed_pct": 30},
    "RCG_STATUS": {"estop_chain_ok": 1, "link_quality_pct": 0},
}

Frame = tuple[float, int, bytes]  # (time, can id, data)


class SimEcu:
    def __init__(self, m: Module, db: cantools.database.Database, rng: random.Random,
                 jitter_s: float = 0.0004, identity: int = 1000):
        self.m = m
        self.rng = rng
        self.jitter_s = jitter_s
        self.name = J.Name(identity=identity, manufacturer=0x2AA, function=m.function,
                           industry_group=0)
        self.msgs = [msg for msg in db.messages if m.ecu in msg.senders]
        self.silent = False
        self.alive = 0
        self._next: list[float] = []
        self._periods: list[float] = []
        self.powered = False

    # -- schedule ------------------------------------------------------------
    def power_on(self, t: float) -> list[Frame]:
        self.powered = True
        start = t + self.rng.uniform(0.02, 0.08)
        self._periods = [msg.cycle_time / 1000.0 * self.m.cycle_factor for msg in self.msgs] + [DM1_PERIOD_S]
        self._next = [start + 0.01 + self.rng.uniform(0, p) for p in self._periods]
        return [(start, *J.address_claim(self.name, self.m.sa))]

    def next_due(self) -> float | None:
        if not self.powered or self.silent or not self._next:
            return None
        return min(self._next)

    def poll(self, t: float) -> list[Frame]:
        out: list[Frame] = []
        if self.silent:
            return out
        for i, due in enumerate(self._next):
            while due <= t:
                out.append((due + self.rng.uniform(0, self.jitter_s), *self._build(i)))
                due += self._periods[i]
            self._next[i] = due
        return out

    def _build(self, i: int) -> tuple[int, bytes]:
        if i == len(self.msgs):
            return J.make_id(J.PGN_DM1, self.m.sa), J.encode_dm1(self.m.dtc)
        msg = self.msgs[i]
        vals = {s.name: 0 for s in msg.signals}
        vals.update(DEFAULTS.get(msg.name, {}))
        if msg.name == "RCG_STATUS":
            vals["mode"] = 2 if self.m.remote else 0
            vals["alive_counter"] = self.alive
            if not self.m.alive_stuck:
                self.alive = (self.alive + 1) % 256
        if msg.name == "HVAC_STATUS":
            vals["cabin_temp"] = round(21.5 + self.rng.gauss(0, 0.1), 1)
        pgn = J.parse_id(msg.frame_id).pgn
        return J.make_id(pgn, self.m.sa), msg.encode(vals)

    # -- reactions ---------------------------------------------------------
    def on_frame(self, t: float, can_id: int, data: bytes) -> list[Frame]:
        if not self.powered:
            return []
        cid = J.parse_id(can_id)
        if cid.pgn == J.PGN_ADDRESS_CLAIM and cid.sa == self.m.sa and not self.silent:
            other = J.Name.from_bytes(data).encode()
            if other == self.name.encode():
                return []
            if self.name.encode() < other:  # we win: re-assert the claim
                return [(t + 0.001, *J.address_claim(self.name, self.m.sa))]
            self.silent = True              # we lose: cannot claim
            return [(t + 0.001, *J.address_claim(self.name, J.NULL_ADDR))]
        if cid.pgn == J.PGN_REQUEST and cid.da in (J.GLOBAL, self.m.sa):
            req = int.from_bytes(bytes(data[:3]), "little")
            if req == J.PGN_ADDRESS_CLAIM:
                sa = J.NULL_ADDR if self.silent else self.m.sa
                return [(t + self.rng.uniform(0.001, 0.010), *J.address_claim(self.name, sa))]
            if req == J.PGN_DM1 and not self.silent:
                return [(t + 0.002, J.make_id(J.PGN_DM1, self.m.sa), J.encode_dm1(self.m.dtc))]
        if (cid.pgn == J.PGN_PROP_A and cid.da == self.m.sa and not self.silent and not self.m.no_response
                and len(data) and data[0] == J.CMD_GET_VERSION):
            return [(t + self.rng.uniform(0.005, 0.015),
                     *J.version_response(self.m.sa, cid.sa, self.m.sw, self.m.termination))]
        return []


def make_ecus(modules: dict[str, Module], db, rng: random.Random, jitter_s: float = 0.0004) -> list[SimEcu]:
    return [SimEcu(m, db, random.Random(rng.getrandbits(32)), jitter_s, identity=1000 + i)
            for i, m in enumerate(modules.values()) if m.fitted]
