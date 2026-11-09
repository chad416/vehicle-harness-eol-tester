"""Per-serial PDF report (F17) and the connector map figure."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .data import CHASSIS, Variant  # noqa: E402
from .results import BLOCKED, FAIL, PASS  # noqa: E402

COL = {PASS: "#2e7d32", FAIL: "#c62828", BLOCKED: "#9e9e9e"}
PIN_COL = {"ok": "#66bb6a", "unused": "#eceff1", "OPEN": "#e53935", "MISWIRE": "#fb8c00",
           "SHORT": "#8e24aa", "HIGH_RES": "#fdd835", "LEAK": "#ab47bc", "BOND": "#fdd835"}
A4 = (8.27, 11.69)


def pin_status(record, v: Variant) -> dict[str, str]:
    st = {p: "ok" for p in v.probe_pins if p != CHASSIS}
    s1 = record.steps[0]
    for f in s1.findings:
        if f.category in ("OPEN", "MISWIRE", "HIGH_RES", "BOND"):
            for p in f.pins:
                if p in st:
                    st[p] = f.category
        elif f.category in ("SHORT", "LEAK"):
            for n in f.nets:
                for p in v.net_pins.get(n, []):
                    if p in st and st[p] == "ok":
                        st[p] = f.category
    return st


def draw_connector_map(ax, v: Variant, status: dict[str, str], upto: int | None = None, title: str = ""):
    """Pins of every connector as a grid; `upto` shows only the first N pins in scan order."""
    order = [p for p in v.probe_pins if p != CHASSIS]
    shown = set(order if upto is None else order[:upto])
    ax.set_xlim(0, 21)
    conns = list(v.connectors.values())
    ax.set_ylim(len(conns) * 1.6 + 0.4, -0.4)
    ax.axis("off")
    for row, c in enumerate(conns):
        y = row * 1.6
        ax.text(0, y + 0.55, f"{c.conn}", fontsize=8, fontweight="bold", va="center")
        ax.text(0, y + 1.05, textwrap.shorten(c.description, 24, placeholder="…"), fontsize=5,
                va="center", color="#555")
        per_row = 20
        for k in range(c.pins):
            pin = f"{c.conn}:{k + 1}"
            s = status.get(pin, "unused")
            if pin in status and pin not in shown:
                s = "pending"
            colour = PIN_COL.get(s, "#cfd8dc") if s != "pending" else "#ffffff"
            x = 2.6 + (k % per_row) * 0.9
            yy = y + (0.15 if k < per_row else 0.8) if c.pins > per_row else y + 0.45
            ax.add_patch(Rectangle((x, yy), 0.75, 0.5, facecolor=colour, edgecolor="#607d8b", lw=0.4))
            ax.text(x + 0.375, yy + 0.25, str(k + 1), fontsize=4, ha="center", va="center")
    handles = [Rectangle((0, 0), 1, 1, facecolor=PIN_COL[k]) for k in
               ("ok", "OPEN", "MISWIRE", "SHORT", "HIGH_RES", "unused")]
    ax.legend(handles, ["pass", "open", "miswire", "short/leak", "high R / bond", "not used"],
              loc="lower center", bbox_to_anchor=(0.5, -0.06), ncol=6, fontsize=6, frameon=False)
    if title:
        ax.set_title(title, fontsize=9, loc="left")


def _page1(pdf, record, v: Variant):
    fig = plt.figure(figsize=A4)
    fig.text(0.07, 0.955, "End-of-line electrical test report", fontsize=15, fontweight="bold")
    fig.text(0.07, 0.935, "Simulated test station · vehicle-harness-eol-tester", fontsize=8, color="#555")
    verdict_col = COL[record.verdict]
    fig.patches.append(Rectangle((0.72, 0.925), 0.21, 0.045, transform=fig.transFigure,
                                 facecolor=verdict_col, edgecolor="none"))
    fig.text(0.825, 0.947, record.verdict, fontsize=16, fontweight="bold", color="white", ha="center", va="center")
    meta = [("Serial", record.serial), ("Variant", f"{v.name}: {v.recipe['description']}"),
            ("Recipe SHA-256", record.recipe_sha256[:16] + "…"), ("Tester", record.tester_version),
            ("CAN bus", record.bus), ("Modelled test time", f"{record.test_time_s:.0f} s")]
    for i, (k, val) in enumerate(meta):
        fig.text(0.07, 0.90 - i * 0.017, k, fontsize=8, color="#555")
        fig.text(0.25, 0.90 - i * 0.017, val, fontsize=8)

    ax = fig.add_axes([0.07, 0.60, 0.86, 0.18])
    ax.axis("off")
    rows = [[s.step, s.title, s.status, str(len(s.findings)), f"{s.duration_s:.1f}"] for s in record.steps]
    tab = ax.table(cellText=rows, colLabels=["Step", "Test", "Result", "Findings", "Time s"],
                   colWidths=[0.07, 0.62, 0.11, 0.1, 0.1], loc="upper left", cellLoc="left")
    tab.auto_set_font_size(False)
    tab.set_fontsize(7.5)
    tab.scale(1, 1.35)
    for i, s in enumerate(record.steps, start=1):
        tab[i, 2].set_facecolor(COL[s.status])
        tab[i, 2].get_text().set_color("white")
    for j in range(5):
        tab[0, j].set_facecolor("#eceff1")

    fig.text(0.07, 0.585, "Findings (where to repair)", fontsize=10, fontweight="bold")
    findings = [(s.step, f) for s in record.steps for f in s.findings]
    notes = [f"{s.step}: {s.note}" for s in record.steps if s.status == BLOCKED or s.note.startswith("not tested")]
    ax2 = fig.add_axes([0.07, 0.08, 0.86, 0.49])
    ax2.axis("off")
    if findings:
        rows = []
        for step, f in findings[:22]:
            where = ", ".join(f.wires) if f.wires else ", ".join(f.pins[:6]) or (f.ecu or ", ".join(f.nets))
            if f.alt_pins:
                where = f"{where} or {', '.join(f.alt_pins)}"
            rows.append([step, f.category, textwrap.shorten(f.detail, 58, placeholder="…"),
                         textwrap.shorten(where, 24, placeholder="…")])
        t2 = ax2.table(cellText=rows, colLabels=["Step", "Category", "Detail", "Location"],
                       colWidths=[0.07, 0.21, 0.51, 0.21], loc="upper left", cellLoc="left")
        t2.auto_set_font_size(False)
        t2.set_fontsize(7)
        t2.scale(1, 1.3)
        for j in range(4):
            t2[0, j].set_facecolor("#eceff1")
        if len(findings) > 22:
            fig.text(0.07, 0.07, f"… {len(findings) - 22} more in the JSON record", fontsize=7)
    else:
        ax2.text(0, 0.98, "None. All limits met.", fontsize=9, va="top")
    for i, n in enumerate(notes):
        fig.text(0.07, 0.05 - i * 0.013, textwrap.shorten(n, 120), fontsize=7, color="#555")
    pdf.savefig(fig)
    return fig


def _page2(pdf, record, v: Variant):
    fig = plt.figure(figsize=A4)
    fig.text(0.07, 0.96, f"{record.serial}: measurements", fontsize=12, fontweight="bold")
    s1, s2, _, s4, s5, s6 = record.steps
    ax = fig.add_axes([0.07, 0.62, 0.86, 0.30])
    draw_connector_map(ax, v, pin_status(record, v), title="S1 continuity per pin")

    ax = fig.add_axes([0.10, 0.38, 0.38, 0.19])
    ir = [m for m in s2.measurements if isinstance(m.value, float)]
    if ir:
        vals = [m.value for m in ir]
        ax.bar(range(len(vals)), vals, color=["#66bb6a" if m.ok else "#e53935" for m in ir], width=0.8)
        ax.set_yscale("log")
        ax.axhline(v.recipe["insulation"]["ir_min_ohm"], color="#c62828", ls="--", lw=1, label="minimum")
        ax.set_ylim(1e3, 3e10)
        ax.set_xticks([])
        ax.legend(fontsize=6)
    else:
        ax.text(0.5, 0.5, s2.status, ha="center", transform=ax.transAxes)
    ax.set_title(f"S2 insulation resistance, {len(ir)} nets (ohm, meter max 10 G)", fontsize=8)
    ax.tick_params(labelsize=6)

    ax = fig.add_axes([0.58, 0.38, 0.35, 0.19])
    cur = s5.measurements
    if cur:
        names = [m.name.replace("I ", "") for m in cur]
        for i, m in enumerate(cur):
            ax.add_patch(Rectangle((i - 0.4, m.low), 0.8, m.high - m.low, facecolor="#e3f2fd", edgecolor="#90caf9"))
            ax.plot(i, m.value, "o", color="#2e7d32" if m.ok else "#c62828")
        ax.set_xticks(range(len(cur)))
        ax.set_xticklabels(names, rotation=30, fontsize=6, ha="right")
        ax.set_xlim(-0.6, len(cur) - 0.4)
        ax.set_ylim(0, max(max(m.high for m in cur), max(m.value for m in cur)) * 1.15)
    else:
        ax.text(0.5, 0.5, s5.status, ha="center", transform=ax.transAxes)
    ax.set_title("S5 idle current per circuit (A), band = limits", fontsize=8)
    ax.tick_params(labelsize=6)

    ax = fig.add_axes([0.10, 0.07, 0.83, 0.24])
    timing = getattr(s6, "timing", None)
    if timing:
        names = list(timing)
        for i, n in enumerate(names):
            gaps = timing[n]
            nominal = v.db.get_message_by_name(n).cycle_time
            tol = v.recipe["can"]["cycle_tol"]
            ax.add_patch(Rectangle((i - 0.35, 1 - tol), 0.7, 2 * tol, facecolor="#e3f2fd", edgecolor="#90caf9"))
            if gaps:
                rel = [g * 1000 / nominal for g in gaps]
                ax.plot([i] * len(rel), rel, "_", color="#78909c", ms=10, alpha=0.5)
                mean = sum(rel) / len(rel)
                ok = abs(mean - 1) <= tol
                ax.plot(i, mean, "o", color="#2e7d32" if ok else "#c62828", ms=7)
            ax.text(i, 0.25, f"{nominal} ms", fontsize=6, ha="center")
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, fontsize=7)
        ax.set_ylim(0.2, max(1.6, max((g * 1000 / v.db.get_message_by_name(n).cycle_time
                                       for n in names for g in timing[n]), default=1) * 1.08))
        ax.set_xlim(-0.6, len(names) - 0.4)
        ax.set_ylabel("period / nominal", fontsize=7)
    else:
        ax.text(0.5, 0.5, s6.status, ha="center", transform=ax.transAxes)
    ax.set_title("S6 CAN cyclic messages: every gap (grey), mean (dot), tolerance band", fontsize=8)
    ax.tick_params(labelsize=6)
    pdf.savefig(fig)
    return fig


def write_pdf(record, v: Variant, path: Path, png_dir: Path | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        f1 = _page1(pdf, record, v)
        f2 = _page2(pdf, record, v)
        info = pdf.infodict()
        info["Title"] = f"EOL test report {record.serial}"
        info["Subject"] = f"variant {v.name}, verdict {record.verdict}"
    if png_dir is not None:
        png_dir.mkdir(parents=True, exist_ok=True)
        f1.savefig(png_dir / f"{record.serial}_p1.png", dpi=110)
        f2.savefig(png_dir / f"{record.serial}_p2.png", dpi=110)
    plt.close(f1)
    plt.close(f2)
    return path
