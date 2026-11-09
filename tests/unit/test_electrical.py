import random

import pytest

from harness_eol import electrical as E
from harness_eol.plant import Cab


def s1(v, *faults):
    return E.continuity(Cab.build(v, list(faults)), random.Random(5))


def cats(step):
    return [f.category for f in step.findings]


@pytest.mark.req("F02")
def test_open_point_to_point_names_the_wire(cmd):
    w = [w for w in cmd.nets["LAMP_3"]][0]
    st = s1(cmd, f"open:{w.wire}")
    (f,) = st.findings
    assert f.category == "OPEN" and f.wires == [w.wire] and f.nets == ["LAMP_3"]


@pytest.mark.req("F02")
def test_open_backbone_cuts_off_downstream_nodes(cmd):
    # backbone segment between the first two CAN_L splices: cuts X1:4 off from the rest
    seg = [w for w in cmd.nets["CAN_L"] if {w.a, w.b} == {"SP12", "SP13"}][0]
    (f,) = s1(cmd, f"open:{seg.wire}").findings
    assert f.category == "OPEN" and seg.wire in f.wires


@pytest.mark.req("F03")
@pytest.mark.parametrize("p,q", [("X1:5", "X1:6"), ("X1:3", "X1:4"), ("X6:15", "X6:21")])
def test_miswire_reported_as_swap(cmd, p, q):
    (f,) = s1(cmd, f"miswire:{p}/{q}").findings
    assert f.category == "MISWIRE" and {p, q} in (set(f.pins), set(f.alt_pins))


@pytest.mark.req("F03")
def test_point_to_point_swap_names_both_ends(cmd):
    # SENS_1 (X6:15-X4:5) and FAN_1 (X6:21-X4:10): a swap at X6 equals a swap at X4
    (f,) = s1(cmd, "miswire:X6:15/X6:21").findings
    assert {frozenset(f.pins), frozenset(f.alt_pins)} == {frozenset({"X6:15", "X6:21"}), frozenset({"X4:5", "X4:10"})}
    # CAN_H/CAN_L at a module connector: multi-pin nets, the swap is unique
    (f,) = s1(cmd, "miswire:X2:3/X2:4").findings
    assert set(f.pins) == {"X2:3", "X2:4"} and f.alt_pins == []


@pytest.mark.req("F04")
def test_short_between_nets_and_to_chassis(cmd):
    (f,) = s1(cmd, "short:X1:5/X3:13").findings
    assert f.category == "SHORT" and f.nets == ["DASH_SW1", "DASH_SW2"]
    (f,) = s1(cmd, "short:X2:5/CHASSIS").findings
    assert f.category == "SHORT" and set(f.nets) == {"GND", "LAMP_1"}


@pytest.mark.req("F04")
def test_resistive_leak_band(cmd):
    w = cmd.nets["SENS_2"][0].wire
    (f,) = s1(cmd, f"leak:{w}/2e4").findings
    assert f.category == "LEAK" and set(f.nets) == {"GND", "SENS_2"}
    assert s1(cmd, f"leak:{w}/5e6").findings == []   # above the meter's leak band: left to S2


@pytest.mark.req("F05")
def test_high_resistance_localised(cmd):
    w = [w for w in cmd.nets["CAN_H"] if "X4:3" in (w.a, w.b)][0]
    (f,) = s1(cmd, f"highres:{w.wire}/0.4").findings
    assert f.category == "HIGH_RES" and f.wires == [w.wire] and f.value == pytest.approx(0.4, abs=0.02)


@pytest.mark.req("F05")
def test_high_resistance_below_limit_passes(cmd):
    w = cmd.nets["CAN_H"][0].wire
    assert s1(cmd, f"highres:{w}/0.05").findings == []


@pytest.mark.req("F06")
def test_ground_bond(cmd):
    st = s1(cmd, "stud:0.08")
    assert cats(st) == ["BOND"]
    assert set(cats(s1(cmd, "stud:0.5"))) == {"HIGH_RES", "BOND"}


@pytest.mark.req("F07", "F08")
def test_insulation_and_hipot(cmd):
    w = cmd.nets["FAN_1"][0].wire
    cab = Cab.build(cmd, [f"leak:{w}/5e6"])
    ir = E.insulation(cab, random.Random(1))
    assert cats(ir) == ["IR_LOW"] and ir.findings[0].nets == ["FAN_1"]
    hp = E.hipot(cab, ir)
    assert hp.status == "PASS" and "FAN_1" in hp.note          # not hi-potted after IR fail
    cab = Cab.build(cmd, [f"damaged:{w}/700"])
    ir = E.insulation(cab, random.Random(1))
    assert ir.status == "PASS"                                   # 500 V: insulation holds
    assert cats(E.hipot(cab, ir)) == ["BREAKDOWN"]               # 1000 V: it breaks down


@pytest.mark.req("F10")
@pytest.mark.parametrize("faults,ok,approx", [([], True, 60), (["noterm:DASH"], False, 120),
                                              (["extraterm:PDU"], False, 40)])
def test_termination(cmd, faults, ok, approx):
    st = E.termination(Cab.build(cmd, faults), random.Random(2))
    assert (st.status == "PASS") == ok
    assert st.measurements[0].value == pytest.approx(approx, rel=0.03)


@pytest.mark.req("F11")
def test_power_up_bands(cmd):
    st = E.power_up(Cab.build(cmd, ["idle:HVAC/3.0", "unplugged:LIGHT"]), random.Random(3))
    assert sorted((f.category, f.ecu) for f in st.findings) == [("OVERCURRENT", "HVAC"), ("UNDERCURRENT", "LIGHT")]


@pytest.mark.req("F02", "F03", "F04", "F05", "F06")
def test_good_harness_passes_with_noise(cmd, base):
    for seed in range(20):
        for v in (cmd, base):
            assert E.continuity(Cab.build(v), random.Random(seed)).findings == []
