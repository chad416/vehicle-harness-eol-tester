import shutil

import pytest

from harness_eol.data import CHASSIS, DataError, load_variant

pytestmark = pytest.mark.req("F01")


def test_variants_load(cmd, base):
    assert len(cmd.wires) == 78 and len(base.wires) == 66
    assert "RCG" in [n["ecu"] for n in cmd.nodes] and "RCG" not in [n["ecu"] for n in base.nodes]
    assert cmd.pin_net[CHASSIS] == "GND"
    assert cmd.can_pins("X1") == ("X1:3", "X1:4")
    assert "GND" not in cmd.insulation_groups()


def test_netlist_is_reproducible():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, str(root / "tools" / "gen_harness.py"), "--check"], capture_output=True)
    assert r.returncode == 0, r.stdout


def _copy(tmp_path):
    from harness_eol.data import DATA

    dst = tmp_path / "data"
    shutil.copytree(DATA, dst)
    return dst


def test_rejects_pin_in_two_nets(tmp_path):
    d = _copy(tmp_path)
    p = d / "variants" / "cmd" / "netlist.csv"
    lines = p.read_text().splitlines()
    lines.append("W999,EXTRA,X1:5,X3:30,1.0,0.5,RD")   # X1:5 already belongs to DASH_SW1
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(DataError, match="X1:5 used by nets"):
        load_variant("cmd", d)


def test_rejects_pin_outside_connector(tmp_path):
    d = _copy(tmp_path)
    p = d / "variants" / "cmd" / "netlist.csv"
    p.write_text(p.read_text().replace("X7:4", "X7:9"))
    with pytest.raises(DataError, match="outside"):
        load_variant("cmd", d)


def test_rejects_disconnected_net(tmp_path):
    d = _copy(tmp_path)
    p = d / "variants" / "cmd" / "netlist.csv"
    lines = p.read_text().splitlines()
    lines.append("W999,LAMP_1,X2:22,X6:39,1.0,0.5,RD")   # second, unconnected piece of LAMP_1
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(DataError, match="LAMP_1 is not connected"):
        load_variant("cmd", d)


def test_rejects_string_limit(tmp_path):
    d = _copy(tmp_path)
    p = d / "variants" / "cmd" / "recipe.yaml"
    p.write_text(p.read_text().replace("1.0e+8", "1.0e8"))   # YAML 1.1 reads this as a string
    with pytest.raises(DataError, match="ir_min_ohm must be a number"):
        load_variant("cmd", d)
