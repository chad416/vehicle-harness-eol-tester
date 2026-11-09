import pytest

from harness_eol import j1939 as J

pytestmark = pytest.mark.req("F12", "F14", "F16")


def test_pdu2_id_roundtrip():
    cid = J.make_id(0xFF30, 0x32)
    assert cid == 0x18FF3032
    p = J.parse_id(cid)
    assert (p.prio, p.pgn, p.sa, p.da) == (6, 0xFF30, 0x32, J.GLOBAL)


def test_pdu1_destination_specific():
    cid = J.make_id(J.PGN_PROP_A, 0xF9, 0x33)
    p = J.parse_id(cid)
    assert p.pgn == J.PGN_PROP_A and p.da == 0x33 and p.sa == 0xF9


def test_request_and_claim_layout():
    cid, data = J.request(J.PGN_ADDRESS_CLAIM, 0xF9)
    assert J.parse_id(cid).pgn == J.PGN_REQUEST
    assert data == bytes([0x00, 0xEE, 0x00])
    name = J.Name(identity=1234, manufacturer=0x2AA, function=0x86, ecu_instance=2, industry_group=0)
    cid, data = J.address_claim(name, 0x34)
    assert J.parse_id(cid).pgn == J.PGN_ADDRESS_CLAIM and J.parse_id(cid).sa == 0x34
    assert J.Name.from_bytes(data) == name


def test_name_field_positions():
    n = J.Name(identity=0x1FFFFF, manufacturer=0, function=0xAB)
    v = n.encode()
    assert v & 0x1FFFFF == 0x1FFFFF
    assert (v >> 40) & 0xFF == 0xAB
    assert J.Name.decode(v) == n


def test_name_order_decides_conflict():
    a = J.Name(identity=1, manufacturer=1, function=0x82)
    b = J.Name(identity=1, manufacturer=1, function=0x85)
    assert a.encode() < b.encode()


@pytest.mark.parametrize("spn,fmi", [(520192, 3), (524287, 31), (100, 1)])
def test_dm1_roundtrip(spn, fmi):
    lamps, dtcs = J.decode_dm1(J.encode_dm1(J.Dtc(spn, fmi, 5)))
    assert dtcs == [J.Dtc(spn, fmi, 5)]
    assert lamps & 0b1100 == 0b0100


def test_dm1_no_dtc():
    assert J.decode_dm1(J.encode_dm1(None)) == (0, [])


def test_version_roundtrip():
    cid, data = J.version_response(0x30, 0xF9, "2.4.1", True)
    assert J.parse_id(cid).da == 0xF9
    assert J.decode_version_response(data) == ("2.4.1", True)
    assert J.decode_version_response(bytes(8)) is None
