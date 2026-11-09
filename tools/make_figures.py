"""Regenerate every image in docs/img from real runs (README screenshots and GIF).

    python tools/make_figures.py            # all
    python tools/make_figures.py --quick    # skip the 500-cab batch and the size sweep
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

from harness_eol.batch import plot_by_kind, plot_pareto, plot_sizes, run_batch, size_sweep  # noqa: E402
from harness_eol.data import CHASSIS, load_variant  # noqa: E402
from harness_eol.plant import Cab  # noqa: E402
from harness_eol.report import draw_connector_map, pin_status, write_pdf  # noqa: E402
from harness_eol.sequencer import run_cab  # noqa: E402

IMG = ROOT / "docs" / "img"
DEMO_HARNESS = ["miswire:X2:3/X2:4", "open:W050", "highres:W012/0.3"]
DEMO_NETWORK = ["conflict:HVAC/DASH", "dtc:LIGHT/520300/7", "cycle:PDU/1.5", "noterm:RCG", "remote"]


def terminal_png(args: list[str], path: Path, title: str) -> None:
    """Run the CLI for real and render its stdout as an image."""
    r = subprocess.run([sys.executable, "-m", "harness_eol", *args], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    text = r.stdout.replace(str(ROOT / "out") + os.sep, "out/").replace(str(ROOT / "out"), "out")
    lines = [f"$ python -m harness_eol {' '.join(args)}"] + text.rstrip().splitlines()
    h = 0.22 * len(lines) + 0.5
    fig = plt.figure(figsize=(11, h), dpi=110)
    fig.patch.set_facecolor("#1e1e1e")
    for i, ln in enumerate(lines):
        col = "#9cdcfe" if i == 0 else "#f48771" if ("FAIL" in ln or "BLOCKED" in ln) else \
            "#b5cea8" if "PASS" in ln else "#d4d4d4"
        fig.text(0.015, 1 - (i + 1) * 0.22 / h, ln, family="monospace", fontsize=8.5, color=col, va="top")
    fig.suptitle(title, x=0.985, ha="right", fontsize=7, color="#808080", y=0.995)
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def scan_gif(v, rec, path: Path) -> None:
    status = pin_status(rec, v)
    order = [p for p in v.probe_pins if p != CHASSIS]
    steps = list(range(0, len(order) + 1, 4)) + [len(order)] * 14
    fig = plt.figure(figsize=(8.4, 4.6), dpi=90)
    ax = fig.add_axes([0.02, 0.06, 0.62, 0.88])
    side = fig.add_axes([0.66, 0.06, 0.33, 0.88])

    def frame(k):
        ax.clear()
        side.clear()
        side.axis("off")
        n = steps[k]
        draw_connector_map(ax, v, status, upto=n, title=f"S1 continuity scan: {n}/{len(order)} pins")
        side.text(0, 0.98, f"{rec.serial}  ({v.name})", fontsize=9, fontweight="bold", va="top")
        if n >= len(order):
            side.text(0, 0.90, "S1 FAIL", fontsize=11, color="#c62828", fontweight="bold", va="top")
            y = 0.80
            for f in rec.steps[0].findings:
                side.text(0, y, f.category, fontsize=8, fontweight="bold", va="top")
                where = (" or ".join([f"{f.pins[0]}<->{f.pins[1]}", f"{f.alt_pins[0]}<->{f.alt_pins[1]}"])
                         if f.alt_pins else f"{f.pins[0]} <-> {f.pins[1]}") if f.category == "MISWIRE" else \
                    ("wire " + ", ".join(f.wires) if f.wires else ", ".join(f.nets))
                side.text(0, y - 0.05, where, fontsize=7.5, va="top")
                side.text(0, y - 0.10, f.detail[:46], fontsize=6.5, color="#555", va="top")
                y -= 0.18
            side.text(0, y - 0.02, "S2-S6 BLOCKED (interlock):\nno HV, no power on this cab", fontsize=7.5,
                      color="#616161", va="top")
        else:
            side.text(0, 0.90, "scanning...", fontsize=10, color="#616161", va="top")

    anim = FuncAnimation(fig, frame, frames=len(steps), interval=140)
    anim.save(path, writer=PillowWriter(fps=7))
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    v = load_variant("cmd")

    rec_a = run_cab(Cab.build(v, DEMO_HARNESS), "CAB-0417")
    write_pdf(rec_a, v, ROOT / "out" / "CAB-0417.pdf", IMG)
    (IMG / "CAB-0417_p1.png").replace(IMG / "report_harness_fail_p1.png")
    (IMG / "CAB-0417_p2.png").replace(IMG / "report_harness_fail_p2.png")
    rec_b = run_cab(Cab.build(v, DEMO_NETWORK), "CAB-0418")
    write_pdf(rec_b, v, ROOT / "out" / "CAB-0418.pdf", IMG)
    (IMG / "CAB-0418_p1.png").replace(IMG / "report_network_fail_p1.png")
    (IMG / "CAB-0418_p2.png").replace(IMG / "report_network_fail_p2.png")
    rec_c = run_cab(Cab.build(v), "CAB-0416")
    write_pdf(rec_c, v, ROOT / "out" / "CAB-0416.pdf", IMG)
    (IMG / "CAB-0416_p1.png").unlink()
    (IMG / "CAB-0416_p2.png").replace(IMG / "report_pass_p2.png")
    scan_gif(v, rec_a, IMG / "continuity_scan.gif")

    terminal_png(["run", "--serial", "CAB-0416"], IMG / "cli_pass.png", "real output")
    terminal_png(["run", "--serial", "CAB-0418", *sum([["--fault", f] for f in DEMO_NETWORK], [])],
                 IMG / "cli_network_fail.png", "real output")
    terminal_png(["run", "--serial", "CAB-0417", *sum([["--fault", f] for f in DEMO_HARNESS], [])],
                 IMG / "cli_harness_fail.png", "real output")

    if not a.quick:
        out = ROOT / "results" / "batch_seed1_n500"
        s = run_batch(500, 1, out=out)
        plot_pareto(s, IMG / "batch_pareto.png")
        plot_by_kind(s, IMG / "batch_detection_by_kind.png")
        rows = size_sweep("cmd", 3, 60)
        (ROOT / "results" / "size_sweep_seed3.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        plot_sizes(rows, v, IMG / "detection_vs_size.png")
        print(json.dumps({k: s[k] for k in ("cabs", "faults_injected", "false_rejects", "escapes_above_limit",
                                             "escapes_below_limit", "detected_above_limit", "above_limit",
                                             "exact_localisation", "first_pass_yield", "wall_s")}, indent=1))
    print("images in", IMG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
