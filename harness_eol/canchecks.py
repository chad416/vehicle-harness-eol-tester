"""CAN network step (F12-F16): address claim, cyclic timing, DM1, signal plausibility,
alive counters, software version and termination configuration."""
from __future__ import annotations

import statistics

from . import j1939 as J
from .canbus import Frame
from .data import Variant
from .results import Finding, Measurement, StepResult


def run_can_step(bus, v: Variant, gap_allow_s: float = 0.0) -> tuple[StepResult, list[Frame]]:
    """`gap_allow_s` widens the max-gap limit on live buses, where the simulated ECUs run
    as a Python thread on a non-real-time OS (the mean-period limit stays as in the recipe)."""
    rc = v.recipe["can"]
    tester = rc["tester_sa"]
    step = StepResult("S6", "CAN network (J1939): nodes, timing, DM1, signals, versions", "")
    frames: list[Frame] = []

    bus.power_on()
    frames += bus.collect(rc["claim_listen_s"])
    bus.send(*J.request(J.PGN_ADDRESS_CLAIM, tester))
    frames += bus.collect(rc["request_listen_s"])
    t_mon0 = frames[-1].t if frames else 0.0
    monitor = bus.collect(rc["monitor_s"])
    frames += monitor

    # --- address claim (F12) -------------------------------------------------
    by_function = {n["function"]: n for n in v.nodes}
    claimed: dict[str, int] = {}          # ecu -> address it holds
    cannot: set[str] = set()
    unexpected: dict[int, int] = {}
    for f in frames:
        cid = J.parse_id(f.can_id)
        if cid.pgn != J.PGN_ADDRESS_CLAIM:
            continue
        name = J.Name.from_bytes(f.data)
        node = by_function.get(name.function)
        if node is None:
            unexpected[cid.sa] = name.function
        elif cid.sa == J.NULL_ADDR:
            cannot.add(node["ecu"])
        else:
            claimed[node["ecu"]] = cid.sa
    holder = {sa: ecu for ecu, sa in claimed.items() if ecu not in cannot}
    for node in v.nodes:
        e = node["ecu"]
        sa = claimed.get(e)
        if e in cannot:
            wanted = sa if sa is not None else node["sa"]
            winner = holder.get(wanted) or holder.get(node["sa"])
            step.findings.append(Finding("ADDRESS_CONFLICT", f"{e} could not claim an address "
                                         f"(0x{wanted:02X} held by {winner})",
                                         ecu=e, value=winner))
            continue
        if sa is None:
            step.findings.append(Finding("NODE_MISSING", f"{e} (function 0x{node['function']:02X}) did not claim",
                                         ecu=e))
            continue
        ok = sa == node["sa"]
        step.measurements.append(Measurement(f"Address {e}", f"0x{sa:02X}", "", f"0x{node['sa']:02X}",
                                             f"0x{node['sa']:02X}", ok))
        if not ok:
            step.findings.append(Finding("WRONG_ADDRESS", f"{e} at 0x{sa:02X}, expected 0x{node['sa']:02X}",
                                         ecu=e, value=f"0x{sa:02X}"))
    for sa, fn in sorted(unexpected.items()):
        step.findings.append(Finding("UNEXPECTED_NODE", f"unknown NAME function 0x{fn:02X} at 0x{sa:02X}",
                                     value=f"0x{sa:02X}"))
    present = {e: sa for e, sa in claimed.items() if e not in cannot}

    # --- cyclic messages (F13) ----------------------------------------------
    by_pgn_sa: dict[tuple[int, int], list[Frame]] = {}
    for f in monitor:
        cid = J.parse_id(f.can_id)
        by_pgn_sa.setdefault((cid.pgn, cid.sa), []).append(f)
    timing = {}
    for msg in v.db.messages:
        e = msg.senders[0]
        if e not in present:
            continue
        nominal = msg.cycle_time / 1000.0
        fs = by_pgn_sa.get((J.parse_id(msg.frame_id).pgn, present[e]), [])
        ts = [f.t for f in fs]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        timing[msg.name] = gaps
        if len(gaps) < 2:
            step.findings.append(Finding("CYCLE_TIME", f"{msg.name}: {len(ts)} frame(s) in {rc['monitor_s']} s",
                                         ecu=e, value=len(ts)))
            continue
        mean = statistics.fmean(gaps)
        worst = max(gaps)
        ok_mean = abs(mean - nominal) <= rc["cycle_tol"] * nominal
        gap_lim = rc["max_gap_factor"] * nominal + gap_allow_s
        ok_gap = worst <= gap_lim
        step.measurements.append(Measurement(f"Period {msg.name}", round(mean * 1000, 2), "ms",
                                             round(nominal * (1 - rc["cycle_tol"]) * 1000, 1),
                                             round(nominal * (1 + rc["cycle_tol"]) * 1000, 1), ok_mean))
        step.measurements.append(Measurement(f"Max gap {msg.name}", round(worst * 1000, 2), "ms", None,
                                             round(gap_lim * 1000, 1), ok_gap))
        if not (ok_mean and ok_gap):
            step.findings.append(Finding("CYCLE_TIME", f"{msg.name}: mean {mean * 1000:.1f} ms, max gap "
                                         f"{worst * 1000:.1f} ms (nominal {nominal * 1000:.0f} ms)",
                                         ecu=e, value=round(mean * 1000, 2)))

    # --- DM1 (F14) ------------------------------------------------------------
    for e, sa in present.items():
        dm1 = [f for f in frames if J.parse_id(f.can_id).pgn == J.PGN_DM1 and J.parse_id(f.can_id).sa == sa]
        if not dm1:
            bus.send(*J.request(J.PGN_DM1, tester, sa))
            dm1 = [f for f in bus.collect(rc["resp_timeout_s"])
                   if J.parse_id(f.can_id).pgn == J.PGN_DM1 and J.parse_id(f.can_id).sa == sa]
        if not dm1:
            step.findings.append(Finding("DM1_MISSING", f"{e}: no DM1", ecu=e))
            continue
        lamps, dtcs = J.decode_dm1(dm1[-1].data)
        step.measurements.append(Measurement(f"Active DTCs {e}", len(dtcs), "", 0, 0, not dtcs))
        for d in dtcs:
            step.findings.append(Finding("ACTIVE_DTC", f"{e}: SPN {d.spn} FMI {d.fmi} OC {d.oc}", ecu=e,
                                         value=d.spn))

    # --- signals and alive counters (F15) --------------------------------------
    decoded: dict[str, list[dict]] = {}
    for msg in v.db.messages:
        e = msg.senders[0]
        if e in present:
            fs = by_pgn_sa.get((J.parse_id(msg.frame_id).pgn, present[e]), [])
            decoded[msg.name] = [msg.decode(f.data, decode_choices=False) for f in fs]
    for s in rc["signals"]:
        vals = decoded.get(s["msg"])
        if not vals:
            continue
        e = v.db.get_message_by_name(s["msg"]).senders[0]
        val = vals[-1][s["signal"]]
        ok = s["min"] <= val <= s["max"]
        step.measurements.append(Measurement(f"{s['msg']}.{s['signal']}", round(float(val), 3), "",
                                             s["min"], s["max"], ok))
        if not ok:
            step.findings.append(Finding("SIGNAL_RANGE", f"{s['msg']}.{s['signal']} = {val} "
                                         f"(allowed {s['min']}..{s['max']})", ecu=e, value=float(val)))
    for a in rc["alive_counters"]:
        vals = [d[a["signal"]] for d in decoded.get(a["msg"], [])]
        if len(vals) < 3:
            continue
        e = v.db.get_message_by_name(a["msg"]).senders[0]
        bad = sum(1 for x, y in zip(vals, vals[1:]) if (y - x) % 256 != 1)
        step.measurements.append(Measurement(f"{a['msg']}.{a['signal']} steps not +1", bad, "", 0, 0, bad == 0))
        if bad:
            step.findings.append(Finding("ALIVE_COUNTER", f"{a['msg']}.{a['signal']}: {bad} of {len(vals) - 1} "
                                         f"steps not +1", ecu=e, value=bad))

    # --- version and termination config (F16) ----------------------------------
    for node in v.nodes:
        e = node["ecu"]
        if e not in present:
            continue
        bus.send(*J.version_request(tester, present[e]))
        resp = [f for f in bus.collect(rc["resp_timeout_s"])
                if J.parse_id(f.can_id).pgn == J.PGN_PROP_A and J.parse_id(f.can_id).sa == present[e]
                and J.parse_id(f.can_id).da == tester]
        decoded_resp = J.decode_version_response(resp[0].data) if resp else None
        if decoded_resp is None:
            step.findings.append(Finding("NO_RESPONSE", f"{e}: no answer to GET_VERSION within "
                                         f"{rc['resp_timeout_s'] * 1000:.0f} ms", ecu=e))
            continue
        sw, term = decoded_resp
        ok = sw == node["sw"]
        step.measurements.append(Measurement(f"SW {e}", sw, "", node["sw"], node["sw"], ok))
        if not ok:
            step.findings.append(Finding("SW_VERSION", f"{e}: {sw}, expected {node['sw']}", ecu=e, value=sw))
        if term != node["termination"]:
            step.findings.append(Finding("TERMINATION_CONFIG", f"{e}: termination {'on' if term else 'off'}, "
                                         f"expected {'on' if node['termination'] else 'off'}", ecu=e))
    step.duration_s = rc["claim_listen_s"] + rc["request_listen_s"] + rc["monitor_s"] + \
        len(present) * rc["resp_timeout_s"]
    step.note = f"monitor window from t={t_mon0:.3f} s; {len(frames)} frames"
    step.finish()
    step.timing = timing  # type: ignore[attr-defined]  # for the report chart
    return step, frames
