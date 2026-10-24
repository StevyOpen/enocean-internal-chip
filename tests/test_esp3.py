"""ESP3 framing and ID helpers."""

from __future__ import annotations

from custom_components.enocean_internal_chip.esp3 import (
    CO_RD_VERSION,
    RADIO_ERP1,
    RESPONSE,
    ChipVersion,
    Esp3Packet,
    FrameParser,
    common_command,
    crc8,
    format_id,
    id_to_int,
    int_to_id,
    parse_id,
    radio_packet,
    response_packet,
)

READ_VERSION_FRAME = bytes.fromhex("55 00 01 00 05 70 03 09")


def test_crc8_matches_the_specification() -> None:
    assert crc8(bytes.fromhex("00010005")) == 0x70
    assert crc8(b"\x03") == 0x09
    assert crc8(b"") == 0x00


def test_encode_common_command() -> None:
    assert common_command(CO_RD_VERSION).encode() == READ_VERSION_FRAME


def test_parser_handles_noise_split_frames_and_bad_crc() -> None:
    telegram = radio_packet(0xF6, [0x30], bytes.fromhex("0501A2B3"), 0x30)
    frame = telegram.encode()
    broken = bytearray(frame)
    broken[-1] ^= 0xFF  # wrong data CRC
    stream = b"\x00\x55\x12" + bytes(broken) + frame[:5]
    parser = FrameParser()
    assert parser.feed(stream) == []
    packets = parser.feed(frame[5:] + READ_VERSION_FRAME)
    assert [packet.packet_type for packet in packets] == [RADIO_ERP1, 0x05]
    assert packets[0].data == telegram.data
    assert packets[0].optional == telegram.optional
    assert parser.crc_errors >= 1


def test_radio_packet_fields() -> None:
    packet = radio_packet(0xA5, [0, 0, 118, 0x08], bytes.fromhex("01A2B3C4"), 0x00, dbm=0x40, subtel=1)
    assert packet.is_radio
    assert packet.rorg == 0xA5
    assert packet.sender_id == "01:A2:B3:C4"
    assert packet.payload == bytes([0, 0, 118, 0x08])
    assert packet.dbm == -64
    assert packet.destination == b"\xff\xff\xff\xff"


def test_response_helpers() -> None:
    packet = response_packet(0x00, b"\xff\xa1\xb2\x00", b"\x0a")
    assert packet.packet_type == RESPONSE
    assert packet.return_code == 0
    assert packet.response_data == b"\xff\xa1\xb2\x00"
    assert Esp3Packet(RADIO_ERP1, b"\xf6").return_code is None


def test_ids() -> None:
    raw = bytes.fromhex("01A2B3C4")
    assert format_id(raw) == "01:A2:B3:C4"
    for text in ("01:A2:B3:C4", "01-a2-b3-c4", "01a2b3c4", " 01 A2 B3 C4 "):
        assert parse_id(text) == raw
    for bad in ("01:A2:B3", "zz:a2:b3:c4", "", None, 42):
        assert parse_id(bad) is None
    assert int_to_id(id_to_int(raw) + 1) == bytes.fromhex("01A2B3C5")
    assert format_id(b"\x01\x02") == ""


def test_chip_version() -> None:
    data = bytes([2, 11, 1, 0, 2, 6, 3, 0]) + bytes.fromhex("018A3B4C") + bytes(4) + b"GATEWAYCTRL\x00\x00\x00\x00\x00"
    version = ChipVersion.from_response(data)
    assert version is not None
    assert version.app_version == "2.11.1.0"
    assert version.api_version == "2.6.3.0"
    assert version.chip_id == "018A3B4C"
    assert version.description == "GATEWAYCTRL"
    assert ChipVersion.from_response(b"\x00" * 10) is None
