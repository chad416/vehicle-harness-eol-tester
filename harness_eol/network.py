"""Resistor-network model of the harness and the simulated instruments that measure it."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from .data import CHASSIS, Variant

METER_RANGE_OHM = 1.0e7   # continuity meter: above this it reads open
STUD_OHM = 0.001          # ground stud to chassis bond
RHO_INSULATION = 2.0e11   # ohm * m, nominal insulation leakage of a wire


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    r: float
    tag: str          # wire id, "STUD", "TERM:<ecu>", "SHORT", "LEAK:<wire>"
    net: str | None   # net of a wire edge, None for anything else


@dataclass
class Network:
    edges: list[Edge]
    wire_length: dict[str, float]           # net -> total wire length (insulation model)
    damaged: dict[str, float] = field(default_factory=dict)  # wire id -> breakdown voltage

    @classmethod
    def from_variant(cls, v: Variant) -> "Network":
        edges = [Edge(w.a, w.b, w.r_nominal, w.wire, w.net) for w in v.wires]
        bonded_stud = sorted({n for w in v.wires for n in (w.a, w.b) if n.startswith("GS")})
        for gs in bonded_stud:
            edges.append(Edge(gs, CHASSIS, STUD_OHM, "STUD", None))
        lengths: dict[str, float] = {}
        for w in v.wires:
            lengths[w.net] = lengths.get(w.net, 0.0) + w.length_m
        return cls(edges, lengths)

    def copy(self) -> "Network":
        return Network(list(self.edges), dict(self.wire_length), dict(self.damaged))

    # -- fault primitives ------------------------------------------------
    def remove(self, tag: str) -> None:
        n = len(self.edges)
        self.edges = [e for e in self.edges if e.tag != tag]
        if len(self.edges) == n:
            raise KeyError(tag)

    def add_series(self, tag: str, extra_ohm: float) -> None:
        for i, e in enumerate(self.edges):
            if e.tag == tag:
                self.edges[i] = replace(e, r=e.r + extra_ohm)
                return
        raise KeyError(tag)

    def add(self, a: str, b: str, r: float, tag: str) -> None:
        self.edges.append(Edge(a, b, r, tag, None))

    def swap_pins(self, p: str, q: str) -> None:
        def sw(n: str) -> str:
            return q if n == p else p if n == q else n
        self.edges = [replace(e, a=sw(e.a), b=sw(e.b)) for e in self.edges]

    # -- analysis ----------------------------------------------------------
    def resistance_matrix(self, probes: list[str], max_ohm: float = METER_RANGE_OHM) -> np.ndarray:
        """Effective resistance between every pair of probe nodes (inf = open).

        Edges above `max_ohm` are invisible to the low-voltage meter. Each connected
        component is solved with the Laplacian pseudo-inverse.
        """
        edges = [e for e in self.edges if e.r <= max_ohm]
        nodes = sorted({n for e in edges for n in (e.a, e.b)} | set(probes))
        idx = {n: i for i, n in enumerate(nodes)}
        parent = list(range(len(nodes)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for e in edges:
            ra, rb = find(idx[e.a]), find(idx[e.b])
            if ra != rb:
                parent[ra] = rb
        comps: dict[int, list[int]] = {}
        for i in range(len(nodes)):
            comps.setdefault(find(i), []).append(i)
        n_p = len(probes)
        out = np.full((n_p, n_p), np.inf)
        np.fill_diagonal(out, 0.0)
        probe_idx = [idx[p] for p in probes]
        for members in comps.values():
            pr = [k for k, gi in enumerate(probe_idx) if find(gi) == find(members[0])]
            if len(pr) < 2:
                continue
            local = {gi: j for j, gi in enumerate(members)}
            lap = np.zeros((len(members), len(members)))
            for e in edges:
                ia, ib = idx[e.a], idx[e.b]
                if ia in local and ib in local:
                    g = 1.0 / e.r
                    a, b = local[ia], local[ib]
                    lap[a, a] += g
                    lap[b, b] += g
                    lap[a, b] -= g
                    lap[b, a] -= g
            pinv = np.linalg.pinv(lap, hermitian=True)
            loc = np.array([local[probe_idx[k]] for k in pr])
            d = np.diag(pinv)[loc]
            sub = d[:, None] + d[None, :] - 2 * pinv[np.ix_(loc, loc)]
            out[np.ix_(pr, pr)] = np.maximum(sub, 0.0)
        return out

    def group_nodes(self, net: str) -> set[str]:
        return {n for e in self.edges if e.net == net for n in (e.a, e.b)}

    def insulation_ohm(self, net: str) -> float:
        """True insulation resistance of one net against everything else."""
        group = self.group_nodes(net)
        g = self.wire_length.get(net, 0.0) / RHO_INSULATION
        for e in self.edges:
            if (e.a in group) != (e.b in group):
                g += 1.0 / e.r
        return 1.0 / g if g > 0 else float("inf")


def tree_wires(v: Variant, net: str) -> dict[str, set[str]]:
    """For each wire of a net: probe pins on the far side of that wire, seen from the
    net's first probe pin (used to localise opens and high resistance)."""
    from .data import is_pin

    ws = v.nets[net]
    adj: dict[str, list[tuple[str, str]]] = {}
    for w in ws:
        adj.setdefault(w.a, []).append((w.b, w.wire))
        adj.setdefault(w.b, []).append((w.a, w.wire))
    if net in v.recipe["continuity"]["chassis_bonded_nets"]:
        for gs in [n for n in adj if n.startswith("GS")]:
            adj.setdefault(gs, []).append((CHASSIS, "STUD"))
            adj.setdefault(CHASSIS, []).append((gs, "STUD"))
    root = v.net_pins[net][0]
    out: dict[str, set[str]] = {}

    def below(node: str, parent: str | None) -> set[str]:
        acc = {node} if (is_pin(node) or node == CHASSIS) else set()
        for nb, wid in adj[node]:
            if nb == parent:
                continue
            sub = below(nb, node)
            out[wid] = sub
            acc |= sub
        return acc

    below(root, None)
    return out
