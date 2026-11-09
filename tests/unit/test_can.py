import random

import pytest

from harness_eol import j1939 as J
from harness_eol.canbus import VirtualTimeBus
from harness_eol.canchecks import run_can_step
from harness_eol.ecu_sim import make_ecus
from harness_eol.plant import Cab


def can_step(v, *faults, seed=4):
    cab = Cab.build(v, list(faults))
    bus = VirtualTimeBus(make_ecus(cab.modules, v.db, random.Random(seed)))
    step, frames = run_can_step(bus, v)
    return step, frames


def found(step):
    return sorted((f.category, f.ecu) for f in step.findings)


@pytest.mark.req("F12", "F13", "F14", "F15", "F16", "F20")
def test_good_network(cmd):
    step, frames = can_step(cmd)
    assert step.findings == []
    claims = [f for f in frames if J.parse_id(f.can_id).pgn == J.PGN_ADDRESS_CLAIM]
    assert len(claims) == 2 * 5          # spontaneous at power-up + answer to the request
    again, frames2 = can_step(cmd)
    assert [(f.t, f.can_id) for f in frames] == [(f.t, f.can_id) for f in frames2]   # deterministic


@pytest.mark.req("F12")
def test_address_conflict_lower_name_wins(cmd):
    # HVAC takes DASH's address; DASH has the lower NAME (function 0x82 < 0x85), so HVAC loses
    step, frames = can_step(cmd, "conflict:HVAC/DASH")
    assert found(step) == [("ADDRESS_CONFLICT", "HVAC")]
    assert step.findings[0].value == "DASH"
    assert any(J.parse_id(f.can_id).sa == J.NULL_ADDR for f in frames)


@pytest.mark.req("F12")
def test_conflict_other_way_round(cmd):
    step, _ = can_step(cmd, "conflict:DASH/HVAC")   # DASH wins at HVAC's address
    assert found(step) == [("ADDRESS_CONFLICT", "HVAC"), ("WRONG_ADDRESS", "DASH")]


@pytest.mark.req("F12")
def test_wrong_address_and_missing(cmd):
    assert found(can_step(cmd, "addr:LIGHT/0x45")[0]) == [("WRONG_ADDRESS", "LIGHT")]
    cab = Cab.build(cmd, ["unplugged:PDU"])
    bus = VirtualTimeBus(make_ecus(cab.modules, cmd.db, random.Random(1)))
    step, _ = run_can_step(bus, cmd)
    assert ("NODE_MISSING", "PDU") in found(step)


@pytest.mark.req("F13")
@pytest.mark.parametrize("factor", ["1.3", "0.7"])
def test_cycle_time(cmd, factor):
    assert found(can_step(cmd, f"cycle:DASH/{factor}")[0]) == [("CYCLE_TIME", "DASH")]


@pytest.mark.req("F13")
def test_cycle_within_tolerance_passes(cmd):
    assert can_step(cmd, "cycle:DASH/1.05")[0].findings == []


@pytest.mark.req("F14")
def test_active_dtc(cmd):
    step, _ = can_step(cmd, "dtc:HVAC/520400/9")
    (f,) = step.findings
    assert (f.category, f.ecu, f.value) == ("ACTIVE_DTC", "HVAC", 520400) and "FMI 9" in f.detail


@pytest.mark.req("F15")
def test_gateway_signals(cmd):
    assert found(can_step(cmd, "remote")[0]) == [("SIGNAL_RANGE", "RCG")]
    assert found(can_step(cmd, "alive")[0]) == [("ALIVE_COUNTER", "RCG")]


@pytest.mark.req("F16")
def test_version_response_and_termination_config(cmd):
    assert found(can_step(cmd, "sw:PDU/3.1.9")[0]) == [("SW_VERSION", "PDU")]
    assert found(can_step(cmd, "noresp:LIGHT")[0]) == [("NO_RESPONSE", "LIGHT")]
    assert found(can_step(cmd, "noterm:RCG")[0]) == [("TERMINATION_CONFIG", "RCG")]


@pytest.mark.req("F12", "F13", "F15")
def test_base_variant(base):
    step, _ = can_step(base)
    assert step.findings == []
    assert not any(m.name.startswith("SW RCG") for m in step.measurements)
