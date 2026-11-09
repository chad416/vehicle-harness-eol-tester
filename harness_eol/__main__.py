"""Command line: `python -m harness_eol run|batch|faults`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import load_variant
from .plant import KINDS, Cab
from .report import write_pdf
from .results import PASS
from .sequencer import run_cab

FAULT_HELP = """fault specs (repeat --fault):
  open:W017            highres:W017/0.5      short:X1:5/X3:13      short:X2:5/CHASSIS
  leak:W040/5e6        miswire:X1:5/X1:6     damaged:W041/700      stud:0.3
  noterm:RCG           extraterm:LIGHT       idle:HVAC/3.0         unplugged:LIGHT
  addr:HVAC/0x41       conflict:HVAC/DASH    cycle:PDU/1.5         dtc:LIGHT/520300/7
  remote               alive                 sw:PDU/3.2.2          noresp:DASH"""


def cmd_run(a) -> int:
    v = load_variant(a.variant)
    cab = Cab.build(v, a.fault or [])
    rec = run_cab(cab, a.serial, a.seed, a.bus)
    out = Path(a.out)
    rec.save(out)
    write_pdf(rec, v, out / f"{a.serial}.pdf", out / "png" if a.png else None)
    print(f"{rec.serial}  variant {v.name}  bus {rec.bus}  ->  {rec.verdict}   "
          f"(modelled test time {rec.test_time_s:.0f} s)")
    for s in rec.steps:
        print(f"  {s.step}  {s.status:<7}  {s.title}" + (f"   [{s.note}]" if s.status == "BLOCKED" else ""))
        for f in s.findings:
            where = ", ".join(f.wires) if f.wires else ""
            print(f"        {f.category:<18} {f.detail}" + (f"   -> wire {where}" if where else ""))
    print(f"  record: {out / (a.serial + '.json')}   report: {out / (a.serial + '.pdf')}")
    return 0 if rec.verdict == PASS else 1


def cmd_batch(a) -> int:
    from .batch import run_batch

    s = run_batch(a.n, a.seed, tuple(a.variants.split(",")), Path(a.out))
    print(json.dumps({k: v for k, v in s.items() if k not in ("per_kind", "pareto_first_run")}, indent=2))
    return 0 if s["false_rejects"] == 0 and s["escapes_above_limit"] == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness_eol", description="Simulated EOL electrical test of a vehicle cab.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="test one cab", epilog=FAULT_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    r.add_argument("--variant", default="cmd", choices=["cmd", "base"])
    r.add_argument("--serial", default="CAB-0001")
    r.add_argument("--fault", action="append", help="inject a fault (simulation), see below")
    r.add_argument("--bus", default="vtime", help="vtime | virtual[:channel] | socketcan[:vcan0]")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--out", default="out")
    r.add_argument("--png", action="store_true", help="also save the report pages as PNG")
    r.set_defaults(fn=cmd_run)
    b = sub.add_parser("batch", help="many cabs with random faults, test-repair-retest metrics")
    b.add_argument("--n", type=int, default=100)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--variants", default="cmd,base")
    b.add_argument("--out", default="results/batch_seed1")
    b.set_defaults(fn=cmd_batch)
    f = sub.add_parser("faults", help="list fault kinds")
    f.set_defaults(fn=lambda a: print(", ".join(KINDS) + "\n\n" + FAULT_HELP) or 0)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
