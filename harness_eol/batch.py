"""Batch evaluation (F18): many simulated cabs with random faults, test -> repair what
was diagnosed -> retest, and the detection / localisation / false-reject metrics."""
from __future__ import annotations

import csv
import json
import random
import statistics
import time
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .data import load_variant  # noqa: E402
from .plant import Cab, Fault, above_limit, applicable_kinds, fault_targets, match, sample_fault, severity  # noqa: E402
from .results import PASS  # noqa: E402
from .sequencer import run_cab  # noqa: E402

N_FAULTS_P = [(0, 0.50), (1, 0.35), (2, 0.15)]


def _n_faults(rng: random.Random) -> int:
    x, acc = rng.random(), 0.0
    for n, p in N_FAULTS_P:
        acc += p
        if x < acc:
            return n
    return 0


def make_cab_faults(v, rng: random.Random) -> list[Fault]:
    faults: list[Fault] = []
    used: set[str] = set()
    for _ in range(_n_faults(rng)):
        for _attempt in range(50):
            f = sample_fault(v, rng, rng.choice(applicable_kinds(v)))
            if not fault_targets(f) & used:
                faults.append(f)
                used |= fault_targets(f)
                break
    return faults


def rework_loop(v, faults: list[Fault], serial: str, seed: int, max_loops: int = 3) -> dict:
    """Run, repair every fault a finding points to, rerun. Returns the cab history."""
    remaining = list(faults)
    loops = []
    found_at: dict[str, int] = {}
    exact: dict[str, bool] = {}
    for loop in range(1, max_loops + 1):
        rec = run_cab(Cab.build(v, remaining), f"{serial}-r{loop}", seed)
        cats = [f.category for f in rec.findings]
        loops.append({"verdict": rec.verdict, "findings": cats, "test_time_s": rec.test_time_s,
                      "steps": {s.step: s.status for s in rec.steps}})
        if rec.verdict == PASS:
            break
        repaired = []
        for fault in remaining:
            m = [match(fault, fd, v) for fd in rec.findings]
            if "exact" in m or "partial" in m:
                found_at.setdefault(fault.spec, loop)
                exact[fault.spec] = exact.get(fault.spec, False) or "exact" in m
                repaired.append(fault)
        if not repaired:
            break
        remaining = [f for f in remaining if f not in repaired]
    return {"loops": loops, "found_at": found_at, "exact": exact,
            "remaining": [f.spec for f in remaining], "final": loops[-1]["verdict"]}


def run_batch(n: int = 100, seed: int = 1, variants=("cmd", "base"), out: Path | None = None) -> dict:
    t0 = time.perf_counter()
    rng = random.Random(seed)
    vs = {name: load_variant(name) for name in variants}
    cabs = []
    for i in range(n):
        v = vs[variants[i % len(variants)]]
        faults = make_cab_faults(v, rng)
        serial = f"B{seed}-{i + 1:04d}"
        h = rework_loop(v, faults, serial, seed)
        cabs.append({"serial": serial, "variant": v.name, "faults": faults, **h})

    per_kind: dict[str, Counter] = {}
    sizes = []
    pareto = Counter()
    false_rejects, fault_free = 0, 0
    first_pass = 0
    for c in cabs:
        v = vs[c["variant"]]
        first = c["loops"][0]
        pareto.update(first["findings"])
        if first["verdict"] == PASS:
            first_pass += 1
        if not c["faults"]:
            fault_free += 1
            false_rejects += first["verdict"] != PASS
        for f in c["faults"]:
            k = per_kind.setdefault(f.kind, Counter())
            lim = above_limit(f, v)
            k["n"] += 1
            k["above_limit"] += lim
            det = f.spec in c["found_at"]
            k["detected"] += det and lim
            k["exact"] += bool(c["exact"].get(f.spec)) and lim
            k["escape_above_limit"] += (not det) and lim
            k["escape_below_limit"] += (not det) and not lim
            k["found_on_retest"] += det and c["found_at"][f.spec] > 1
            if severity(f) is not None:
                sizes.append({"kind": f.kind, "size": severity(f), "detected": det, "above_limit": lim})
    total_faults = sum(k["n"] for k in per_kind.values())
    tt_first = [c["loops"][0]["test_time_s"] for c in cabs]
    summary = {
        "seed": seed, "cabs": n, "variants": list(variants), "faults_injected": total_faults,
        "fault_free_cabs": fault_free, "false_rejects": false_rejects,
        "first_pass_yield": round(first_pass / n, 3),
        "escapes_above_limit": sum(k["escape_above_limit"] for k in per_kind.values()),
        "escapes_below_limit": sum(k["escape_below_limit"] for k in per_kind.values()),
        "detected_above_limit": sum(k["detected"] for k in per_kind.values()),
        "above_limit": sum(k["above_limit"] for k in per_kind.values()),
        "exact_localisation": sum(k["exact"] for k in per_kind.values()),
        "cabs_not_passing_after_rework": sum(1 for c in cabs if c["final"] != PASS),
        "test_time_first_run_s": {"mean": round(statistics.fmean(tt_first), 1), "max": round(max(tt_first), 1)},
        "per_kind": {k: dict(c) for k, c in sorted(per_kind.items())},
        "pareto_first_run": dict(pareto.most_common()),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with open(out / "cabs.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["serial", "variant", "faults", "first_verdict", "loops", "final", "remaining"])
            for c in cabs:
                w.writerow([c["serial"], c["variant"], " ".join(f.spec for f in c["faults"]),
                            c["loops"][0]["verdict"], len(c["loops"]), c["final"], " ".join(c["remaining"])])
        plot_pareto(summary, out / "pareto.png")
        plot_by_kind(summary, out / "detection_by_kind.png")
        (out / "sizes.json").write_text(json.dumps(sizes, indent=1), encoding="utf-8")
    return summary


def size_sweep(variant: str = "cmd", seed: int = 3, per_kind: int = 40) -> list[dict]:
    """Detection against fault size for the three graded fault kinds."""
    v = load_variant(variant)
    rng = random.Random(seed)
    out = []
    for kind in ("highres", "stud", "leak"):
        for i in range(per_kind):
            f = sample_fault(v, rng, kind)
            rec = run_cab(Cab.build(v, [f]), f"S{seed}-{kind}-{i}", seed)
            det = any(match(f, fd, v) for fd in rec.findings)
            out.append({"kind": kind, "size": severity(f), "detected": det, "above_limit": above_limit(f, v)})
    return out


def plot_pareto(summary: dict, path: Path) -> None:
    items = list(summary["pareto_first_run"].items())
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=120)
    if items:
        names, counts = zip(*items)
        ax.bar(names, counts, color="#5c6bc0")
        cum, tot, acc = [], sum(counts), 0
        for c in counts:
            acc += c
            cum.append(100 * acc / tot)
        ax2 = ax.twinx()
        ax2.plot(names, cum, "o-", color="#ef6c00", ms=3)
        ax2.set_ylim(0, 105)
        ax2.set_ylabel("cumulative %", fontsize=8)
        ax2.tick_params(labelsize=7)
    ax.set_title(f"Findings on first test, {summary['cabs']} cabs (seed {summary['seed']})", fontsize=10)
    ax.tick_params(axis="x", labelrotation=60, labelsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.set_ylabel("count", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_by_kind(summary: dict, path: Path) -> None:
    kinds = list(summary["per_kind"])
    pk = summary["per_kind"]
    det = [100 * pk[k].get("detected", 0) / max(pk[k].get("above_limit", 0), 1) for k in kinds]
    exa = [100 * pk[k].get("exact", 0) / max(pk[k].get("above_limit", 0), 1) for k in kinds]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=120)
    y = range(len(kinds))
    ax.barh([i + 0.2 for i in y], det, height=0.4, color="#66bb6a", label="detected")
    ax.barh([i - 0.2 for i in y], exa, height=0.4, color="#42a5f5", label="exact location")
    for i, k in enumerate(kinds):
        ax.text(101, i, f"n={pk[k].get('above_limit', 0)}", va="center", fontsize=7)
    ax.set_yticks(list(y))
    ax.set_yticklabels(kinds, fontsize=7)
    ax.set_xlim(0, 112)
    ax.set_xlabel("% of injected faults above the detection limit", fontsize=8)
    ax.legend(fontsize=7, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, frameon=False)
    ax.set_title(f"Detection and localisation by fault type ({summary['cabs']} cabs, seed {summary['seed']})",
                 fontsize=10, loc="right")
    ax.tick_params(axis="x", labelsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_sizes(rows: list[dict], v, path: Path) -> None:
    from .plant import stud_limit

    r = v.recipe
    spec = {"highres": ("High-resistance wire: added ohm", r["continuity"]["max_extra_ohm"]),
            "stud": ("Loose ground stud: added ohm (bond limit)", stud_limit(v)),
            "leak": ("Insulation leak to chassis: ohm", r["insulation"]["ir_min_ohm"])}
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.0), dpi=120)
    for ax, (kind, (title, limit)) in zip(axes, spec.items()):
        pts = [x for x in rows if x["kind"] == kind]
        ax.scatter([p["size"] for p in pts], [1 if p["detected"] else 0 for p in pts],
                   c=["#2e7d32" if p["detected"] else "#c62828" for p in pts], s=14, alpha=0.8)
        ax.axvline(limit, color="#555", ls="--", lw=1)
        ax.set_xscale("log")
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["missed", "found"], fontsize=7)
        ax.set_ylim(-0.4, 1.4)
        ax.set_title(title, fontsize=8)
        ax.tick_params(axis="x", labelsize=7)
        ax.text(limit, 1.3, " limit", fontsize=7, color="#555")
    fig.suptitle("Detection against fault size (single fault per cab)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
