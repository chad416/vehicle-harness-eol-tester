"""Generate the two cab harness variants (connectors.csv + netlist.csv).

The output plays the role of a wiring-CAD export. It is checked in; re-run with
`python tools/gen_harness.py` and `--check` verifies the checked-in files match.
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 7

CONNECTORS = [
    # conn, description, pins, module plugged in at EOL (or "" for test adapter)
    ("X1", "Dashboard module", 24, "DASH"),
    ("X2", "Lighting module", 24, "LIGHT"),
    ("X3", "Power distribution unit", 32, "PDU"),
    ("X4", "HVAC module", 16, "HVAC"),
    ("X5", "Remote-control gateway", 12, "RCG"),
    ("X6", "Chassis interface (to chassis harness)", 40, ""),
    ("X7", "Diagnostic connector", 8, ""),
]

COLOURS = ["RD", "BK", "BU", "GN", "YE", "WH", "OG", "VT", "GY", "BN"]


def build(variant: str) -> tuple[list[tuple], list[dict]]:
    rnd = random.Random(f"{SEED}-{variant}")
    cmd = variant == "cmd"
    conns = [c for c in CONNECTORS if cmd or c[0] != "X5"]
    wires: list[dict] = []

    def wire(net, a, b, csa, lo=0.4, hi=3.5, colour=None):
        wires.append(
            {
                "wire": f"W{len(wires) + 1:03d}",
                "net": net,
                "from": a,
                "to": b,
                "length_m": round(rnd.uniform(lo, hi), 2),
                "csa_mm2": csa,
                "colour": colour or rnd.choice(COLOURS),
            }
        )

    # --- power ---------------------------------------------------------
    wire("BATT_24V", "X6:1", "X3:1", 6.0, 1.5, 3.5, "RD")
    feeds = [("PWR_DASH", "X3:5", "X1:1", 1.5), ("PWR_LIGHT", "X3:6", "X2:1", 2.5),
             ("PWR_HVAC", "X3:7", "X4:1", 2.5), ("PWR_DIAG", "X3:9", "X7:1", 0.75)]
    if cmd:
        feeds.insert(3, ("PWR_RCG", "X3:8", "X5:1", 1.0))
    for net, a, b, csa in feeds:
        wire(net, a, b, csa, colour="RD")

    # --- ground: module grounds to splice SP1, splice to stud GS1 --------
    gnd_pins = ["X1:2", "X2:2", "X4:2", "X7:2"] + (["X5:2"] if cmd else [])
    for p in gnd_pins:
        wire("GND", p, "SP1", 1.5, colour="BK")
    wire("GND", "SP1", "GS1", 6.0, 0.3, 0.8, "BK")
    wire("GND", "X3:3", "GS1", 6.0, 0.3, 1.2, "BK")
    wire("GND", "X3:4", "GS1", 6.0, 0.3, 1.2, "BK")
    wire("GND", "X6:3", "GS1", 6.0, 0.3, 1.0, "BK")

    # --- CAN backbone with stubs (daisy chain through splices) -------------
    nodes = [("X1", 3, 4), ("X2", 3, 4), ("X3", 10, 11), ("X4", 3, 4)]
    if cmd:
        nodes.append(("X5", 3, 4))
    nodes.append(("X7", 3, 4))
    for net, idx, col, sp0 in (("CAN_H", 1, "YE", 2), ("CAN_L", 2, "GN", 12)):
        prev = None
        for i, (conn, ph, pl) in enumerate(nodes):
            pin = f"{conn}:{ph if idx == 1 else pl}"
            sp = f"SP{sp0 + i}"
            if i == len(nodes) - 1:  # last node: backbone ends directly at the pin
                wire(net, prev, pin, 0.5, 0.3, 2.5, col)
                break
            wire(net, sp, pin, 0.5, 0.1, 0.6, col)  # stub
            if prev is not None:
                wire(net, prev, sp, 0.5, 0.4, 2.5, col)  # backbone segment
            prev = sp

    # --- dash switches -> PDU logic inputs --------------------------------
    for i in range(8):
        wire(f"DASH_SW{i + 1}", f"X1:{5 + i}", f"X3:{12 + i}", 0.5)
    # --- lamp outputs -> chassis interface ---------------------------------
    for i in range(10):
        wire(f"LAMP_{i + 1}", f"X2:{5 + i}", f"X6:{4 + i}", 1.0, 1.0, 4.5)
    # --- lamp indicators -> dash -------------------------------------------
    for i in range(4):
        wire(f"IND_{i + 1}", f"X2:{15 + i}", f"X1:{13 + i}", 0.5)
    # --- HVAC sensors from the chassis interface, shared return ------------
    for i in range(4):
        wire(f"SENS_{i + 1}", f"X6:{15 + i}", f"X4:{5 + i}", 0.5, 1.0, 4.0)
    wire("SENS_RTN", "X4:9", "SP20", 0.5)
    wire("SENS_RTN", "SP20", "X6:19", 0.5)
    wire("SENS_RTN", "SP20", "X6:20", 0.5)
    for i in range(2):
        wire(f"FAN_{i + 1}", f"X4:{10 + i}", f"X6:{21 + i}", 1.5, 1.0, 3.0)
    # --- PDU outputs to chassis -------------------------------------------
    for i in range(6):
        wire(f"PDU_OUT_{i + 1}", f"X3:{20 + i}", f"X6:{26 + i}", 2.5, 1.0, 4.5)
    # --- remote-control gateway (cmd only) ---------------------------------
    if cmd:
        wire("RCG_EN", "X1:17", "X5:5", 0.5)
        wire("RCG_ESTOP_A", "X1:18", "X5:6", 0.75)
        wire("RCG_ESTOP_B", "X5:7", "X6:23", 0.75, 1.0, 4.0)
        wire("RCG_LINK_LED", "X5:10", "X1:19", 0.5)
        wire("CAN2_H", "X5:8", "X6:24", 0.5, 1.0, 4.0, "YE")
        wire("CAN2_L", "X5:9", "X6:25", 0.5, 1.0, 4.0, "GN")
    return conns, wires


def render(variant: str) -> dict[str, str]:
    conns, wires = build(variant)
    out = {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["conn", "description", "pins", "module"])
    w.writerows(conns)
    out["connectors.csv"] = buf.getvalue()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(wires[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(wires)
    out["netlist.csv"] = buf.getvalue()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if checked-in files differ")
    args = ap.parse_args()
    bad = 0
    for v in ("cmd", "base"):
        d = ROOT / "data" / "variants" / v
        for name, text in render(v).items():
            p = d / name
            if args.check:
                if not p.exists() or p.read_text(encoding="utf-8") != text:
                    print(f"DIFFERS: {p}")
                    bad += 1
            else:
                d.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8", newline="\n")
                print(f"wrote {p.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
