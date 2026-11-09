import math

import numpy as np
import pytest

from harness_eol.data import CHASSIS
from harness_eol.network import Edge, Network, tree_wires

pytestmark = pytest.mark.req("F02", "F05", "F07")


def net(*edges):
    return Network([Edge(a, b, r, t, "N") for a, b, r, t in edges], {"N": 1.0})


def test_series_and_parallel():
    n = net(("A", "B", 1.0, "w1"), ("B", "C", 2.0, "w2"), ("A", "C", 3.0, "w3"))
    r = n.resistance_matrix(["A", "C"])
    assert r[0, 1] == pytest.approx(1.5)   # 3 || 3


def test_disconnected_is_open_and_meter_range():
    n = net(("A", "B", 1.0, "w1"), ("C", "D", 2e7, "leak"))
    r = n.resistance_matrix(["A", "B", "C", "D"])
    assert math.isinf(r[0, 2]) and math.isinf(r[2, 3])   # 20 MOhm is beyond the meter range
    assert r[0, 1] == pytest.approx(1.0)


def test_star_with_splice():
    n = net(("P1", "S", 0.1, "a"), ("P2", "S", 0.2, "b"), ("P3", "S", 0.3, "c"))
    r = n.resistance_matrix(["P1", "P2", "P3"])
    assert r[0, 1] == pytest.approx(0.3) and r[1, 2] == pytest.approx(0.5)
    assert np.allclose(r, r.T)


def test_fault_primitives():
    n = net(("A", "B", 1.0, "w1"))
    n.add_series("w1", 0.5)
    assert n.edges[0].r == 1.5
    n.swap_pins("A", "Z")
    assert n.edges[0].a == "Z"
    with pytest.raises(KeyError):
        n.remove("nope")


def test_insulation_counts_crossing_edges():
    n = Network([Edge("A", "B", 1.0, "w1", "N"), Edge("B", CHASSIS, 1e6, "LEAK", None)], {"N": 2.0})
    ir = n.insulation_ohm("N")
    assert ir == pytest.approx(1 / (1e-6 + 2.0 / 2e11))


def test_tree_wires_downstream_sets(cmd):
    tw = tree_wires(cmd, "CAN_H")
    # root is the first CAN_H pin (X1:3); the stub to X2:3 has only X2:3 below it
    stub_x2 = [w.wire for w in cmd.nets["CAN_H"] if "X2:3" in (w.a, w.b)][0]
    assert tw[stub_x2] == {"X2:3"}
    assert len(tw) == len(cmd.nets["CAN_H"])
