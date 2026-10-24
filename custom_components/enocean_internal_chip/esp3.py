"""EnOcean Serial Protocol 3 (ESP3): framing, CRC and the packets we use.

This module has no Home Assistant imports, so it can be tested on its own and
reused by the fake gateway in the test suite.

An ESP3 frame looks like this::

    0x55 | data length (2) | optional length (1) | packet type (1) | CRC8 header
         | data ... | optional ... | CRC8 data

Radio telegrams (packet type 0x01, "ERP1") carry the RORG as the first data
byte, then the payload, then the 4-byte sender ID and the status byte. Their
optional part holds the sub-telegram number, the destination ID, the signal
strength and the security level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SYNC_BYTE = 0x55

# Packet types
RADIO_ERP1 = 0x01
RESPONSE = 0x02
EVENT = 0x04
COMMON_COMMAND = 0x05

# Common commands
CO_WR_RESET = 0x02
CO_RD_VERSION = 0x03
CO_WR_IDBASE = 0x07
CO_RD_IDBASE = 0x08

# Return codes
RET_OK = 0x00

#: Return codes a module may answer with. The last three are specific to
#: CO_WR_IDBASE. A write is never trusted on its return code alone: the gateway
#: reads the address back to confirm it (see ``gateway.py``).
RETURN_CODES: dict[int, str] = {
    0x00: "ok",
    0x01: "the module reported an error",
    0x02: "the module does not support this command",
    0x03: "wrong parameter",
    0x04: "operation denied by the module",
    0x05: "the module is locked",
    0x82: "flash memory error in the module",
    0x90: "address outside the range accepted by the module",
    0x91: "no address changes left on this module",
}

# RORG values
RORG_RPS = 0xF6
RORG_1BS = 0xD5
RORG_4BS = 0xA5
RORG_VLD = 0xD2
RORG_UTE = 0xD4
RORG_SIG = 0xD0

RORG_NAMES: dict[int, str] = {
    RORG_RPS: "RPS",
    RORG_1BS: "1BS",
    RORG_4BS: "4BS",
    RORG_VLD: "VLD",
    RORG_UTE: "UTE",
    RORG_SIG: "SIG",
}

BROADCAST_ID = b"\xff\xff\xff\xff"

# Sub-telegram number to use when sending (ESP3 specification: send = 3).
SEND_SUBTEL = 0x03


def _build_crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _build_crc_table()


def crc8(data: bytes | bytearray) -> int:
    """CRC8 with polynomial 0x07, as required by ESP3."""
    crc = 0
    for byte in data:
        crc = _CRC_TABLE[crc ^ byte]
    return crc


@dataclass(slots=True)
class Esp3Packet:
    """One ESP3 packet, received or to be sent."""

    packet_type: int
    data: bytes
    optional: bytes = b""
    #: Set by the simulator, so logs and the telegram list can tell them apart.
    simulated: bool = field(default=False, compare=False)

    def encode(self) -> bytes:
        """Return the full frame, ready for the serial port."""
        header = bytes(
            (
                (len(self.data) >> 8) & 0xFF,
                len(self.data) & 0xFF,
                len(self.optional) & 0xFF,
                self.packet_type & 0xFF,
            )
        )
        body = bytes(self.data) + bytes(self.optional)
        return bytes((SYNC_BYTE,)) + header + bytes((crc8(header),)) + body + bytes((crc8(body),))

    # Radio helpers. They only make sense for RADIO_ERP1 packets.

    @property
    def is_radio(self) -> bool:
        return self.packet_type == RADIO_ERP1 and len(self.data) >= 6

    @property
    def rorg(self) -> int | None:
        return self.data[0] if self.data else None

    @property
    def sender(self) -> bytes:
        return bytes(self.data[-5:-1]) if len(self.data) >= 6 else b""

    @property
    def sender_id(self) -> str:
        return format_id(self.sender) if self.sender else ""

    @property
    def status(self) -> int:
        return self.data[-1] if self.data else 0

    @property
    def payload(self) -> bytes:
        """The user data between the RORG and the sender ID."""
        return bytes(self.data[1:-5]) if len(self.data) >= 6 else b""

    @property
    def destination(self) -> bytes:
        return bytes(self.optional[1:5]) if len(self.optional) >= 5 else BROADCAST_ID

    @property
    def dbm(self) -> int | None:
        """Received signal strength (negative dBm), when the module reports it."""
        if len(self.optional) >= 6 and self.optional[5] not in (0x00, 0xFF):
            return -self.optional[5]
        return None

    # Response helpers.

    @property
    def return_code(self) -> int | None:
        return self.data[0] if self.packet_type == RESPONSE and self.data else None

    @property
    def response_data(self) -> bytes:
        return bytes(self.data[1:]) if self.packet_type == RESPONSE else b""


class FrameParser:
    """Turns a byte stream into packets. Bad frames are skipped, not raised."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0

    def feed(self, chunk: bytes) -> list[Esp3Packet]:
        self._buffer.extend(chunk)
        packets: list[Esp3Packet] = []
        while True:
            start = self._buffer.find(SYNC_BYTE)
            if start < 0:
                self._buffer.clear()
                return packets
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 6:
                return packets
            header = bytes(self._buffer[1:5])
            if crc8(header) != self._buffer[5]:
                # Not a real frame start: drop this sync byte and look again.
                self.crc_errors += 1
                del self._buffer[0]
                continue
            data_len = (header[0] << 8) | header[1]
            opt_len = header[2]
            total = 6 + data_len + opt_len + 1
            if len(self._buffer) < total:
                return packets
            body = bytes(self._buffer[6 : 6 + data_len + opt_len])
            if crc8(body) != self._buffer[total - 1]:
                self.crc_errors += 1
                del self._buffer[0]
                continue
            packets.append(
                Esp3Packet(
                    packet_type=header[3],
                    data=body[:data_len],
                    optional=body[data_len:],
                )
            )
            del self._buffer[:total]


# --------------------------------------------------------------------------
# IDs
# --------------------------------------------------------------------------


def format_id(raw: bytes | bytearray | list[int] | None) -> str:
    """``b"\\x01\\xa2\\xb3\\xc4"`` -> ``"01:A2:B3:C4"``."""
    if not raw or len(raw) != 4:
        return ""
    return ":".join(f"{b & 0xFF:02X}" for b in raw)


def parse_id(text: str | None) -> bytes | None:
    """Accept ``01:A2:B3:C4``, ``01-A2-B3-C4``, ``01a2b3c4`` or with spaces.

    Returns ``None`` when the text is not a 4-byte hexadecimal ID.
    """
    if not isinstance(text, str):
        return None
    cleaned = "".join(ch for ch in text.strip() if ch not in ":-_ ").upper()
    if len(cleaned) != 8:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def id_to_int(raw: bytes) -> int:
    return int.from_bytes(raw, "big")


def int_to_id(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


# --------------------------------------------------------------------------
# Building packets
# --------------------------------------------------------------------------


def radio_packet(
    rorg: int,
    payload: bytes | list[int],
    sender: bytes,
    status: int = 0x00,
    destination: bytes = BROADCAST_ID,
    *,
    subtel: int = SEND_SUBTEL,
    dbm: int = 0xFF,
) -> Esp3Packet:
    """An ERP1 radio telegram."""
    data = bytes((rorg,)) + bytes(payload) + bytes(sender) + bytes((status & 0xFF,))
    optional = bytes((subtel,)) + bytes(destination) + bytes((dbm & 0xFF, 0x00))
    return Esp3Packet(RADIO_ERP1, data, optional)


def common_command(code: int, args: bytes | list[int] = b"") -> Esp3Packet:
    return Esp3Packet(COMMON_COMMAND, bytes((code,)) + bytes(args))


def response_packet(code: int, data: bytes = b"", optional: bytes = b"") -> Esp3Packet:
    return Esp3Packet(RESPONSE, bytes((code,)) + bytes(data), bytes(optional))


@dataclass(slots=True, frozen=True)
class ChipVersion:
    """What CO_RD_VERSION tells us about the module."""

    app_version: str
    api_version: str
    chip_id: str
    chip_version: str
    description: str

    @classmethod
    def from_response(cls, data: bytes) -> ChipVersion | None:
        """Parse the response data (return code already stripped)."""
        if len(data) < 16:
            return None
        description = bytes(data[16:32]).split(b"\x00", 1)[0]
        return cls(
            app_version=".".join(str(b) for b in data[0:4]),
            api_version=".".join(str(b) for b in data[4:8]),
            chip_id=data[8:12].hex().upper(),
            chip_version=data[12:16].hex().upper(),
            description=description.decode("ascii", "replace").strip(),
        )
