"""The small J1939 subset used here: 29-bit id layout, NAME, request, address claim,
DM1 (single frame) and proprietary A command/response."""
from __future__ import annotations

from dataclasses import dataclass

PGN_REQUEST = 0xEA00
PGN_ADDRESS_CLAIM = 0xEE00
PGN_PROP_A = 0xEF00
PGN_DM1 = 0xFECA
GLOBAL = 0xFF
NULL_ADDR = 0xFE          # "cannot claim"

CMD_GET_VERSION = 0x01
RESP_FLAG = 0x80


def make_id(pgn: int, sa: int, da: int = GLOBAL, prio: int = 6) -> int:
    dp = (pgn >> 16) & 0x3
    pf = (pgn >> 8) & 0xFF
    ps = da if pf < 240 else pgn & 0xFF
    return (prio << 26) | (dp << 24) | (pf << 16) | (ps << 8) | (sa & 0xFF)


@dataclass(frozen=True)
class CanId:
    prio: int
    pgn: int
    sa: int
    da: int


def parse_id(can_id: int) -> CanId:
    prio = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return CanId(prio, (dp << 16) | (pf << 8), sa, ps)
    return CanId(prio, (dp << 16) | (pf << 8) | ps, sa, GLOBAL)


@dataclass(frozen=True)
class Name:
    identity: int
    manufacturer: int
    function: int
    ecu_instance: int = 0
    function_instance: int = 0
    vehicle_system: int = 0
    vs_instance: int = 0
    industry_group: int = 0
    arbitrary_address: bool = False

    def encode(self) -> int:
        return (
            (self.identity & 0x1FFFFF)
            | (self.manufacturer & 0x7FF) << 21
            | (self.ecu_instance & 0x7) << 32
            | (self.function_instance & 0x1F) << 35
            | (self.function & 0xFF) << 40
            | (self.vehicle_system & 0x7F) << 49
            | (self.vs_instance & 0xF) << 56
            | (self.industry_group & 0x7) << 60
            | int(self.arbitrary_address) << 63
        )

    @classmethod
    def decode(cls, v: int) -> "Name":
        return cls(
            identity=v & 0x1FFFFF,
            manufacturer=(v >> 21) & 0x7FF,
            ecu_instance=(v >> 32) & 0x7,
            function_instance=(v >> 35) & 0x1F,
            function=(v >> 40) & 0xFF,
            vehicle_system=(v >> 49) & 0x7F,
            vs_instance=(v >> 56) & 0xF,
            industry_group=(v >> 60) & 0x7,
            arbitrary_address=bool(v >> 63),
        )

    def to_bytes(self) -> bytes:
        return self.encode().to_bytes(8, "little")

    @classmethod
    def from_bytes(cls, data: bytes) -> "Name":
        return cls.decode(int.from_bytes(bytes(data[:8]), "little"))


def request(pgn: int, sa: int, da: int = GLOBAL) -> tuple[int, bytes]:
    return make_id(PGN_REQUEST, sa, da), pgn.to_bytes(3, "little")


def address_claim(name: Name, sa: int) -> tuple[int, bytes]:
    return make_id(PGN_ADDRESS_CLAIM, sa, GLOBAL), name.to_bytes()


@dataclass(frozen=True)
class Dtc:
    spn: int
    fmi: int
    oc: int = 1


def encode_dm1(dtc: Dtc | None) -> bytes:
    if dtc is None:
        return bytes([0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF])
    lamp = 0b0000_0100  # amber warning lamp on
    return bytes([
        lamp, 0xFF,
        dtc.spn & 0xFF,
        (dtc.spn >> 8) & 0xFF,
        ((dtc.spn >> 16) & 0x7) << 5 | (dtc.fmi & 0x1F),
        dtc.oc & 0x7F,            # SPN conversion method 0
        0xFF, 0xFF,
    ])


def decode_dm1(data: bytes) -> tuple[int, list[Dtc]]:
    lamps = data[0]
    spn = data[2] | data[3] << 8 | ((data[4] >> 5) & 0x7) << 16
    fmi = data[4] & 0x1F
    oc = data[5] & 0x7F
    if spn == 0 and fmi == 0:
        return lamps, []
    return lamps, [Dtc(spn, fmi, oc)]


def version_request(tester_sa: int, da: int) -> tuple[int, bytes]:
    return make_id(PGN_PROP_A, tester_sa, da), bytes([CMD_GET_VERSION] + [0xFF] * 7)


def version_response(sa: int, da: int, sw: str, termination: bool) -> tuple[int, bytes]:
    major, minor, patch = (int(x) for x in sw.split("."))
    return make_id(PGN_PROP_A, sa, da), bytes(
        [CMD_GET_VERSION | RESP_FLAG, 0x00, major, minor, patch, int(termination), 0xFF, 0xFF])


def decode_version_response(data: bytes) -> tuple[str, bool] | None:
    if len(data) < 6 or data[0] != (CMD_GET_VERSION | RESP_FLAG) or data[1] != 0:
        return None
    return f"{data[2]}.{data[3]}.{data[4]}", bool(data[5] & 1)
