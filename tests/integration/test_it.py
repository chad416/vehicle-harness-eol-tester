"""Integration tests IT01-IT08 (SPEC section 6)."""
import json
import os
import random
import sys
import time

import pytest

from harness_eol.batch import rework_loop, run_batch
from harness_eol.plant import Cab, above_limit, match, parse_fault, sample_fault
from harness_eol.report import write_pdf
from harness_eol.sequencer import run_cab

# one representative of every fault kind, with the category it must produce first
CATALOGUE = [
    ("cmd", "open:W050", "OPEN"),
    ("cmd", "highres:W020/0.5", "HIGH_RES"),
    ("cmd", "short:X1:5/X3:13", "SHORT"),
    ("cmd", "short:X6:21/CHASSIS", "SHORT"),
    ("cmd", "leak:W040/5e4", "LEAK"),
    ("cmd", "leak:W040/5e6", "IR_LOW"),
    ("cmd", "miswire:X1:3/X1:4", "MISWIRE"),
    ("cmd", "damaged:W041/700", "BREAKDOWN"),
    ("cmd", "stud:0.3", "BOND"),
    ("cmd", "noterm:RCG", "TERMINATION_CONFIG"),
    ("base", "noterm:HVAC", "TERMINATION_CONFIG"),
    ("cmd", "extraterm:LIGHT", "TERMINATION_CONFIG"),
    ("cmd", "idle:DASH/2.5", "OVERCURRENT"),
    ("cmd", "idle:RCG/0.2", "UNDERCURRENT"),
    ("cmd", "unplugged:HVAC", "NODE_MISSING"),
    ("cmd", "addr:PDU/0x52", "WRONG_ADDRESS"),
    ("cmd", "conflict:LIGHT/PDU", "ADDRESS_CONFLICT"),
    ("base", "cycle:HVAC/1.6", "CYCLE_TIME"),
    ("cmd", "dtc:DASH/521000/4", "ACTIVE_DTC"),
    ("cmd", "remote", "SIGNAL_RANGE"),
    ("cmd", "alive", "ALIVE_COUNTER"),
    ("cmd", "sw:HVAC/1.3.7", "SW_VERSION"),
    ("base", "noresp:PDU", "NO_RESPONSE"),
]


@pytest.mark.it("IT01")
@pytest.mark.req("F09", "F17", "F20")
@pytest.mark.parametrize("variant", ["cmd", "base"])
def test_it01_good_cab_passes(request, variant):
    v = request.getfixturevalue(variant)
    rec = run_cab(Cab.build(v), f"GOOD-{variant}")
    assert rec.verdict == "PASS", [f.detail for f in rec.findings]
    assert [s.status for s in rec.steps] == ["PASS"] * 6
    again = run_cab(Cab.build(v), f"GOOD-{variant}")
    assert json.dumps(rec.to_json()) == json.dumps(again.to_json())


@pytest.mark.it("IT02")
@pytest.mark.req("F02", "F03", "F04", "F05", "F06", "F07", "F08", "F10", "F11", "F12", "F13", "F14",
                 "F15", "F16")
@pytest.mark.parametrize("variant,spec,category", CATALOGUE, ids=[c[1] for c in CATALOGUE])
def test_it02_every_fault_found_and_located(request, variant, spec, category):
    v = request.getfixturevalue(variant)
    f = parse_fault(spec)
    rec = run_cab(Cab.build(v, [f]), "IT02")
    assert rec.verdict == "FAIL"
    assert category in [x.category for x in rec.findings]
    assert any(match(f, fd, v) == "exact" for fd in rec.findings), [x.detail for x in rec.findings]


@pytest.mark.it("IT03")
@pytest.mark.req("F09")
def test_it03_interlock(cmd):
    rec = run_cab(Cab.build(cmd, ["short:X1:5/X3:13", "dtc:HVAC/520200/3"]), "IT03")
    st = {s.step: s.status for s in rec.steps}
    assert st == {"S1": "FAIL", "S2": "BLOCKED", "S3": "BLOCKED", "S4": "PASS", "S5": "BLOCKED", "S6": "BLOCKED"}
    rec = run_cab(Cab.build(cmd, ["highres:W020/0.5"]), "IT03b")
    assert rec.steps[1].status == "PASS" and rec.steps[4].status == "BLOCKED"   # HV allowed, power not
    rec = run_cab(Cab.build(cmd, ["idle:HVAC/4.0"]), "IT03c")
    assert rec.steps[5].status == "BLOCKED" and "overcurrent" in rec.steps[5].note


@pytest.mark.it("IT04")
@pytest.mark.req("F18")
def test_it04_repair_by_diagnosis(cmd):
    h = rework_loop(cmd, [parse_fault("open:W050"), parse_fault("dtc:HVAC/520200/3")], "IT04", 0)
    assert [x["verdict"] for x in h["loops"]] == ["FAIL", "FAIL", "PASS"]
    assert h["found_at"] == {"open:W050": 1, "dtc:HVAC/520200/3": 2}   # DTC hidden behind the interlock
    assert h["remaining"] == []


@pytest.mark.it("IT05")
@pytest.mark.req("F19")
def test_it05_python_can_virtual_bus(cmd):
    rec = run_cab(Cab.build(cmd), "IT05-good", bus="virtual:it05a")
    assert rec.verdict == "PASS", [f.detail for f in rec.findings]
    rec = run_cab(Cab.build(cmd, ["sw:LIGHT/1.9.9", "conflict:HVAC/DASH", "cycle:PDU/1.5"]), "IT05-bad",
                  bus="virtual:it05b")
    assert {(f.category, f.ecu) for f in rec.findings} >= {("SW_VERSION", "LIGHT"), ("ADDRESS_CONFLICT", "HVAC"),
                                                         ("CYCLE_TIME", "PDU")}


def _vcan0() -> bool:
    return sys.platform.startswith("linux") and os.path.exists("/sys/class/net/vcan0")


@pytest.mark.it("IT06")
@pytest.mark.req("F19")
@pytest.mark.skipif(not _vcan0(), reason="needs Linux with a vcan0 interface")
def test_it06_socketcan_vcan0(cmd):
    rec = run_cab(Cab.build(cmd), "IT06-good", bus="socketcan:vcan0")
    assert rec.verdict == "PASS", [f.detail for f in rec.findings]
    rec = run_cab(Cab.build(cmd, ["dtc:RCG/520777/2", "noresp:PDU"]), "IT06-bad", bus="socketcan:vcan0")
    assert {(f.category, f.ecu) for f in rec.findings} == {("ACTIVE_DTC", "RCG"), ("NO_RESPONSE", "PDU")}


@pytest.mark.it("IT07")
@pytest.mark.req("F18", "F20", "P02")
def test_it07_batch(tmp_path):
    t0 = time.perf_counter()
    s = run_batch(100, 1, out=tmp_path)
    wall = time.perf_counter() - t0
    assert s["false_rejects"] == 0
    assert s["escapes_above_limit"] == 0
    assert s["cabs_not_passing_after_rework"] == 0
    assert s["detected_above_limit"] == s["above_limit"]
    assert wall < 60
    for name in ("summary.json", "cabs.csv", "pareto.png", "detection_by_kind.png"):
        assert (tmp_path / name).stat().st_size > 0
    s2 = run_batch(100, 1)
    for k in ("faults_injected", "first_pass_yield", "exact_localisation", "pareto_first_run"):
        assert s[k] == s2[k]


@pytest.mark.it("IT08")
@pytest.mark.req("P01", "F17")
def test_it08_timing_and_report(cmd, tmp_path):
    t0 = time.perf_counter()
    rec = run_cab(Cab.build(cmd, ["miswire:X1:5/X1:6"]), "IT08")
    assert time.perf_counter() - t0 < 2.0
    p = write_pdf(rec, cmd, tmp_path / "IT08.pdf", tmp_path)
    assert p.read_bytes()[:5] == b"%PDF-" and (tmp_path / "IT08_p1.png").exists()
    j = rec.save(tmp_path)
    data = json.loads(j.read_text())
    assert data["verdict"] == "FAIL" and data["steps"][0]["findings"][0]["category"] == "MISWIRE"


@pytest.mark.req("F18")
def test_sampled_faults_respect_limits(cmd):
    rng = random.Random(9)
    for kind in ("highres", "leak", "stud", "damaged"):
        for _ in range(30):
            f = sample_fault(cmd, rng, kind)
            assert isinstance(above_limit(f, cmd), bool)
