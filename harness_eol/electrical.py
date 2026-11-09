"""Electrical steps: continuity/miswire/short/leak/resistance/bond (F02-F06), insulation
(F07), dielectric withstand (F08), CAN termination (F10) and power-up current (F11)."""
from __future__ import annotations

import random

import numpy as np

from .data import CHASSIS, Variant
from .network import Network, tree_wires
from .plant import Cab
from .results import BLOCKED, Finding, Measurement, StepResult

IR_METER_MAX = 1.0e10
ARC_OHM = 1.0e3


def _noisy(r_true: np.ndarray, rng: random.Random) -> np.ndarray:
    """4-wire meter: 0.5 % + 1 mOhm (one sigma), symmetric."""
    n = r_true.shape[0]
    nrng = np.random.default_rng(rng.getrandbits(63))
    rel = nrng.normal(0.0, 0.005, (n, n))
    off = nrng.normal(0.0, 0.001, (n, n))
    rel = np.triu(rel, 1) + np.triu(rel, 1).T
    off = np.triu(off, 1) + np.triu(off, 1).T
    with np.errstate(invalid="ignore"):
        out = np.where(np.isinf(r_true), np.inf, r_true * (1 + rel) + off)
    np.fill_diagonal(out, 0.0)
    return np.maximum(out, 0.0)


_REF: dict[tuple[str, str], np.ndarray] = {}


def reference_matrix(v: Variant) -> np.ndarray:
    key = (v.name, v.recipe_sha256)
    if key not in _REF:
        _REF[key] = Network.from_variant(v).resistance_matrix(v.probe_pins)
    return _REF[key]


def _groups(conn: np.ndarray) -> list[int]:
    n = conn.shape[0]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ii, jj = np.nonzero(np.triu(conn, 1))
    for i, j in zip(ii, jj):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    return [find(i) for i in range(n)]


def continuity(cab: Cab, rng: random.Random) -> StepResult:
    v = cab.variant
    rc = v.recipe["continuity"]
    t = v.recipe["timing"]
    pins = v.probe_pins
    ix = {p: i for i, p in enumerate(pins)}
    r = _noisy(cab.network.resistance_matrix(pins), rng)
    ref = reference_matrix(v)
    gid = _groups(r < rc["connected_max_ohm"])
    groups: dict[int, list[str]] = {}
    for p, g in zip(pins, gid):
        groups.setdefault(g, []).append(p)
    group_of = {p: g for p, g in zip(pins, gid)}
    label = dict(v.pin_net)
    step = StepResult("S1", "Continuity, miswire, isolation, resistance, ground bond", "")
    step.duration_s = len(pins) * (t["continuity_per_pin_s"] + t["resistance_per_pin_s"])

    def pure(g: int, lab: dict) -> bool:
        return len({lab[p] for p in groups[g]}) == 1

    def one_group(net: str, lab: dict) -> bool:
        return len({group_of[p] for p, n in lab.items() if n == net}) == 1

    # 1. miswires: a swap of two pins at one connector that makes the picture consistent
    miswired_nets: set[str] = set()
    cand = sorted(p for g, ps in groups.items() if not pure(g, label) for p in ps if p != CHASSIS)
    used: set[str] = set()

    def swap_explains(lab: dict, p: str, q: str) -> dict | None:
        if p.split(":")[0] != q.split(":")[0] or lab[p] == lab[q]:
            return None
        trial = dict(lab)
        trial[p], trial[q] = lab[q], lab[p]
        ok = (pure(group_of[p], trial) and pure(group_of[q], trial)
              and one_group(lab[p], trial) and one_group(lab[q], trial))
        return trial if ok else None

    for i, p in enumerate(cand):
        for q in cand[i + 1:]:
            if p in used or q in used:
                continue
            trial = swap_explains(label, p, q)
            if trial is None:
                continue
            nets = {label[p], label[q]}
            # A swap of two point-to-point wires at one end looks the same as a swap at the
            # other end: report the other pair as an equal alternative.
            alt = [(a, b) for j, a in enumerate(cand) for b in cand[j + 1:]
                   if {a, b}.isdisjoint({p, q}) and a not in used and b not in used
                   and {label[a], label[b]} == nets and swap_explains(label, a, b) is not None]
            alt_pins = list(alt[0]) if alt else []
            where = f"{p} <-> {q}" + (f" or {alt_pins[0]} <-> {alt_pins[1]}" if alt_pins else "")
            step.findings.append(Finding("MISWIRE", f"{where} swapped ({label[p]} / {label[q]})",
                                         nets=sorted(nets), pins=[p, q], alt_pins=alt_pins))
            miswired_nets |= nets
            label = trial
            used |= {p, q, *alt_pins}

    # 2. hard shorts: one connected group holding several nets
    shorted: set[str] = set()
    for ps in groups.values():
        nets = sorted({label[p] for p in ps})
        if len(nets) > 1:
            shorted |= set(nets)
            worst = min(r[ix[a], ix[b]] for a in ps for b in ps if label[a] != label[b])
            step.findings.append(Finding("SHORT", f"nets connected: {', '.join(nets)}", nets=nets,
                                         value=round(float(worst), 4)))

    # 3. opens: a net split over several (pure) groups
    for net, npins in v.net_pins.items():
        if net in shorted or net in miswired_nets:
            continue
        frags: dict[int, list[str]] = {}
        for p in npins:
            frags.setdefault(group_of[p], []).append(p)
        if len(frags) < 2:
            continue
        tw = tree_wires(v, net)
        allp = set(npins)
        main = max(frags.values(), key=lambda f: (len(f), npins[0] in f))
        for frag in frags.values():
            if frag is main:
                continue
            fs = set(frag)
            cands = sorted(w for w, below in tw.items() if below == fs or allp - below == fs)
            step.findings.append(Finding("OPEN", f"{net}: {', '.join(frag)} cut off from the net",
                                         nets=[net], pins=frag, wires=cands))

    # 4. resistive leaks between groups
    lo, hi = rc["connected_max_ohm"], rc["isolated_min_ohm"]
    ii, jj = np.nonzero(np.triu((r >= lo) & (r < hi), 1))
    leaks: dict[tuple[str, str], float] = {}
    for i, j in zip(ii, jj):
        a, b = sorted((label[pins[i]], label[pins[j]]))
        if a != b:
            leaks[(a, b)] = min(leaks.get((a, b), np.inf), float(r[i, j]))
    for (a, b), val in sorted(leaks.items()):
        step.findings.append(Finding("LEAK", f"{a} to {b}: {val:.4g} ohm", nets=[a, b], value=round(val, 1)))

    # 5. wire resistance against the netlist
    for net, npins in v.net_pins.items():
        if net in shorted or net in miswired_nets or len({group_of[p] for p in npins}) > 1:
            continue
        root = npins[0]
        exc = {p: float(r[ix[root], ix[p]] - ref[ix[root], ix[p]]) for p in npins[1:]}
        worst = max(exc.values()) if exc else 0.0
        step.measurements.append(Measurement(f"R excess {net}", round(worst, 4), "ohm", None,
                                             rc["max_extra_ohm"], worst <= rc["max_extra_ohm"]))
        flagged = {p for p, e in exc.items() if e > rc["max_extra_ohm"]}
        if flagged:
            tw = tree_wires(v, net)
            cands = sorted(w for w, below in tw.items() if below == flagged)
            if not cands:
                sup = [(len(b), w) for w, b in tw.items() if flagged <= b]
                m = min(s for s, _ in sup) if sup else 0
                cands = sorted(w for s, w in sup if s == m)
            step.findings.append(Finding("HIGH_RES", f"{net}: +{worst:.3f} ohm at {', '.join(sorted(flagged))}",
                                         nets=[net], pins=sorted(flagged), wires=cands, value=round(worst, 4)))

    # 6. ground bond
    for net in rc["chassis_bonded_nets"]:
        bad: list[tuple[str, float]] = []
        for p in v.net_pins[net]:
            if p == CHASSIS or not np.isfinite(r[ix[p], ix[CHASSIS]]):
                continue
            val = float(r[ix[p], ix[CHASSIS]])
            ok = val <= rc["bond_max_ohm"]
            step.measurements.append(Measurement(f"Bond {p}", round(val, 4), "ohm", None, rc["bond_max_ohm"], ok))
            if not ok:
                bad.append((p, val))
        if bad:
            worst = max(x for _, x in bad)
            step.findings.append(Finding("BOND", f"{len(bad)} pin(s) to chassis above {rc['bond_max_ohm']} ohm, "
                                         f"worst {worst:.3f} ohm", nets=[net], pins=[p for p, _ in bad],
                                         value=round(worst, 4)))
    return step.finish()


def _breakdown_v(cab: Cab, net: str) -> float:
    vals = [bd for wid, bd in cab.network.damaged.items() if cab.variant.wire_by_id[wid].net == net]
    return min(vals) if vals else float("inf")


def insulation(cab: Cab, rng: random.Random) -> StepResult:
    v = cab.variant
    ri = v.recipe["insulation"]
    groups = v.insulation_groups()
    step = StepResult("S2", f"Insulation resistance at {ri['ir_test_v']} V DC", "")
    step.duration_s = len(groups) * v.recipe["timing"]["ir_per_group_s"]
    for net in groups:
        true = cab.network.insulation_ohm(net)
        arc = _breakdown_v(cab, net) < ri["ir_test_v"]
        reading = ARC_OHM if arc else min(true * (1 + rng.gauss(0, 0.05)), IR_METER_MAX)
        ok = reading >= ri["ir_min_ohm"]
        step.measurements.append(Measurement(f"IR {net}", float(f"{reading:.3g}"), "ohm", ri["ir_min_ohm"], None, ok))
        if not ok:
            why = "breakdown during IR test" if arc else f"{reading:.3g} ohm"
            step.findings.append(Finding("IR_LOW", f"{net}: {why}", nets=[net], value=float(f"{reading:.3g}")))
    return step.finish()


def hipot(cab: Cab, ir_step: StepResult) -> StepResult:
    v = cab.variant
    ri = v.recipe["insulation"]
    failed = {n for f in ir_step.findings for n in f.nets}
    step = StepResult("S3", f"Dielectric withstand {ri['hipot_v']} V DC, {ri['hipot_dwell_s']} s", "")
    groups = [n for n in v.insulation_groups() if n not in failed]
    step.duration_s = len(groups) * (ri["hipot_dwell_s"] + v.recipe["timing"]["hipot_extra_s"])
    for net in groups:
        if _breakdown_v(cab, net) < ri["hipot_v"]:
            step.measurements.append(Measurement(f"Leak {net}", "ARC", "A", None, ri["hipot_max_a"], False))
            step.findings.append(Finding("BREAKDOWN", f"{net}: breakdown at {ri['hipot_v']} V", nets=[net]))
            continue
        leak = ri["hipot_v"] / cab.network.insulation_ohm(net)
        ok = leak <= ri["hipot_max_a"]
        step.measurements.append(Measurement(f"Leak {net}", float(f"{leak:.3g}"), "A", None, ri["hipot_max_a"], ok))
        if not ok:
            step.findings.append(Finding("HIPOT_LEAKAGE", f"{net}: {leak:.3g} A", nets=[net], value=leak))
    if failed:
        step.note = f"not tested (failed IR): {', '.join(sorted(failed))}"
    return step.finish()


def termination(cab: Cab, rng: random.Random) -> StepResult:
    v = cab.variant
    rt = v.recipe["can_termination"]
    net = cab.network.copy()
    fitted_term = []
    for m in cab.modules.values():
        if m.fitted and m.termination:
            h, lo = v.can_pins(m.conn)
            net.add(h, lo, 120.0 * (1 + rng.gauss(0, 0.005)), f"TERM:{m.ecu}")
            fitted_term.append(m.ecu)
    a, b = rt["pins"]
    val = float(net.resistance_matrix([a, b])[0, 1])
    val = val * (1 + rng.gauss(0, 0.002)) if np.isfinite(val) else val
    lo_lim, hi_lim = rt["term_ohm"] * (1 - rt["term_tol"]), rt["term_ohm"] * (1 + rt["term_tol"])
    ok = lo_lim <= val <= hi_lim
    step = StepResult("S4", "CAN termination (power off, modules fitted)", "", duration_s=2.0)
    shown = round(val, 2) if np.isfinite(val) else "open"
    step.measurements.append(Measurement(f"R {a}-{b}", shown, "ohm", round(lo_lim, 1), round(hi_lim, 1), ok))
    if not ok:
        est = 120.0 / val if np.isfinite(val) and val > 0 else 0
        step.findings.append(Finding("TERMINATION",
                                     f"CAN_H-CAN_L {shown} ohm (about {est:.1f} terminations, expected 2)",
                                     nets=["CAN_H", "CAN_L"], pins=[a, b],
                                     value=shown if isinstance(shown, str) else float(shown)))
    return step.finish()


def power_up(cab: Cab, rng: random.Random) -> StepResult:
    v = cab.variant
    rp = v.recipe["power"]
    step = StepResult("S5", f"Power-up at {rp['supply_v']} V, idle current per circuit", "")
    step.duration_s = len(rp["circuits"]) * v.recipe["timing"]["power_per_circuit_s"]
    for c in rp["circuits"]:
        m = cab.modules[c["module"]]
        amps = abs(rng.gauss(0, 0.002)) if not m.fitted else m.idle_a * (1 + rng.gauss(0, 0.03))
        ok = c["min_a"] <= amps <= c["max_a"]
        step.measurements.append(Measurement(f"I {c['circuit']}", round(amps, 3), "A", c["min_a"], c["max_a"], ok))
        if not ok:
            cat = "OVERCURRENT" if amps > c["max_a"] else "UNDERCURRENT"
            step.findings.append(Finding(cat, f"{c['circuit']} ({m.ecu}): {amps:.3f} A", ecu=m.ecu,
                                         value=round(amps, 3)))
    return step.finish()


def blocked(step: str, title: str, why: str) -> StepResult:
    r = StepResult.blocked(step, title, why)
    r.status = BLOCKED
    return r
