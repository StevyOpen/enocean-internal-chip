"""EEP decoders.

Where the ``enocean`` library ships a definition for a profile, the decoders
are compared with it over a range of raw values. The other profiles are
checked against telegrams worked out from the EEP specification.
"""

from __future__ import annotations

import warnings

import pytest

from custom_components.enocean_internal_chip import profiles
from custom_components.enocean_internal_chip.profiles import (
    A5_02_RANGES,
    A5_02_RANGES_10BIT,
    decode,
    parse_4bs_teach_in,
    parse_ute_request,
    summarize,
)

SENDER = [0x05, 0x00, 0x00, 0x20]


def telegram(rorg: int, payload: list[int], status: int = 0x00) -> bytes:
    return bytes([rorg, *payload, *SENDER, status])


# ── comparison with the enocean library ────────────────────────────────

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    enocean_packet = pytest.importorskip("enocean.protocol.packet")
    from enocean.protocol.constants import PACKET


def library(rorg: int, payload: list[int], func: int, ptype: int, status: int = 0, **kwargs) -> dict:
    data = [rorg, *payload, *SENDER, status]
    packet = enocean_packet.RadioPacket(PACKET.RADIO, data=data, optional=[1, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0])
    packet.parse_eep(func, ptype, **kwargs)
    return {
        key: value["raw_value"] if isinstance(value["value"], str) else value["value"]
        for key, value in packet.parsed.items()
    }


@pytest.mark.parametrize("ptype", sorted(A5_02_RANGES))
@pytest.mark.parametrize("raw", [0, 1, 77, 128, 200, 254, 255])
def test_a5_02_8bit_matches_library(ptype: int, raw: int) -> None:
    ours = decode(f"A5-02-{ptype:02X}", telegram(0xA5, [0, 0, raw, 0x08]))
    theirs = library(0xA5, [0, 0, raw, 0x08], 0x02, ptype)
    assert ours["temperature"] == pytest.approx(theirs["TMP"], abs=0.051)


@pytest.mark.parametrize("ptype", sorted(A5_02_RANGES_10BIT))
@pytest.mark.parametrize("raw", [0, 1, 300, 512, 700, 1022, 1023])
def test_a5_02_10bit_matches_library(ptype: int, raw: int) -> None:
    payload = [0, (raw >> 8) & 0x03, raw & 0xFF, 0x08]
    ours = decode(f"A5-02-{ptype:02X}", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x02, ptype)
    assert ours["temperature"] == pytest.approx(theirs["TMP"], abs=0.051)


@pytest.mark.parametrize("raw", [(0, 0), (138, 140), (250, 250), (17, 3)])
def test_a5_04_01_matches_library(raw: tuple[int, int]) -> None:
    payload = [0, raw[0], raw[1], 0x0A]
    ours = decode("A5-04-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x04, 0x01)
    assert ours["humidity"] == pytest.approx(theirs["HUM"], abs=0.051)
    assert ours["temperature"] == pytest.approx(theirs["TMP"], abs=0.051)


def test_a5_04_01_without_temperature_sensor() -> None:
    assert "temperature" not in decode("A5-04-01", telegram(0xA5, [0, 138, 140, 0x08]))


@pytest.mark.parametrize("payload", [[140, 0x01, 0x23, 0x08], [0, 0, 0, 0x08], [255, 0x03, 0xFF, 0x08]])
def test_a5_04_03_matches_library(payload: list[int]) -> None:
    ours = decode("A5-04-03", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x04, 0x03)
    assert ours["humidity"] == pytest.approx(theirs["HUM"], abs=0.051)
    assert ours["temperature"] == pytest.approx(theirs["TMP"], abs=0.051)


@pytest.mark.parametrize("db0", [0x08, 0x09])
@pytest.mark.parametrize("raw", [(0x40, 0x80), (0, 0), (255, 255)])
def test_a5_06_01_matches_library(db0: int, raw: tuple[int, int]) -> None:
    payload = [0xB4, raw[0], raw[1], db0]
    ours = decode("A5-06-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x06, 0x01)
    expected = theirs["ILL2"] if theirs["RS"] == 1 else theirs["ILL1"]
    assert ours["illuminance"] == pytest.approx(expected, abs=0.6)
    assert ours["supply_voltage"] == pytest.approx(theirs["SVC"], abs=0.01)


@pytest.mark.parametrize("db1", [0x00, 0x80, 0x7F, 0xFF])
def test_a5_07_01_matches_library(db1: int) -> None:
    payload = [0xB4, 0x00, db1, 0x09]
    ours = decode("A5-07-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x07, 0x01)
    assert ours["motion"] == (theirs["PIR"] == 1)
    assert ours["supply_voltage"] == pytest.approx(theirs["SVC"], abs=0.01)


@pytest.mark.parametrize("db0", [0x08, 0x0A])
@pytest.mark.parametrize("raw", [(0x40, 0x80), (0, 0), (255, 255)])
def test_a5_08_01_matches_library(db0: int, raw: tuple[int, int]) -> None:
    payload = [0xB4, raw[0], raw[1], db0]
    ours = decode("A5-08-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x08, 0x01)
    assert ours["illuminance"] == pytest.approx(theirs["ILL"], abs=0.6)
    assert ours["temperature"] == pytest.approx(theirs["TMP"], abs=0.051)
    assert ours["motion"] == (theirs["PIRS"] == 0)


@pytest.mark.parametrize("db0", [0x08, 0x09])
def test_a5_14_01_matches_library(db0: int) -> None:
    payload = [0xB4, 0x00, 0x00, db0]
    ours = decode("A5-14-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x14, 0x01)
    assert ours["opening"] == (theirs["CT"] == 1)
    assert ours["supply_voltage"] == pytest.approx(theirs["SVC"], abs=0.01)


@pytest.mark.parametrize(
    ("payload", "key"),
    [([0x00, 0x05, 0xDC, 0x0C], "power"), ([0x00, 0x30, 0x39, 0x09], "energy"), ([0x12, 0x34, 0x56, 0x0B], "energy")],
)
def test_a5_12_01_matches_library(payload: list[int], key: str) -> None:
    ours = decode("A5-12-01", telegram(0xA5, payload))
    theirs = library(0xA5, payload, 0x12, 0x01)
    assert ours[key] == pytest.approx(theirs["MR"] / 10 ** theirs["DIV"], abs=0.001)
    assert (key == "power") == (theirs["DT"] == 1)


@pytest.mark.parametrize("byte", [0x08, 0x09])
def test_d5_00_01_matches_library(byte: int) -> None:
    ours = decode("D5-00-01", telegram(0xD5, [byte]))
    theirs = library(0xD5, [byte], 0x00, 0x01)
    assert ours["opening"] == (theirs["CO"] == 0)


@pytest.mark.parametrize(("byte", "position"), [(0xF0, "closed"), (0xD0, "tilted"), (0xC0, "open"), (0xE0, "open")])
def test_f6_10_00_handle(byte: int, position: str) -> None:
    ours = decode("F6-10-00", telegram(0xF6, [byte], 0x20))
    assert ours["handle_position"] == position
    theirs = library(0xF6, [byte], 0x10, 0x00, 0x20)
    # The library names the movement: 3 = down (closed), 1 = up (tilted), 0/2 = horizontal (open).
    assert {3: "closed", 1: "tilted", 0: "open", 2: "open"}[theirs["WIN"]] == position


@pytest.mark.parametrize(
    ("byte", "status", "action", "pressed"),
    [
        (0x30, 0x30, 1, True),
        (0x10, 0x30, 0, True),
        (0x70, 0x30, 3, True),
        (0x50, 0x30, 2, True),
        (0x00, 0x20, None, False),
    ],
)
def test_f6_02_rocker_matches_library(byte: int, status: int, action: int | None, pressed: bool) -> None:
    ours = decode("F6-02-02", telegram(0xF6, [byte], status))["rocker"]
    theirs = library(0xF6, [byte], 0x02, 0x02, status)
    assert ours["pressed"] == pressed == (theirs["EB"] == 1)
    assert ours["action"] == action
    if action is not None:
        assert theirs["R1"] == action


@pytest.mark.parametrize("position", [0, 40, 60, 100])
def test_d2_05_00_matches_library(position: int) -> None:
    payload = [position, 0x7F, 0x00, 0x04]
    ours = decode("D2-05-00", telegram(0xD2, payload))
    theirs = library(0xD2, payload, 0x05, 0x00, command=4)
    assert theirs["POS"] == position
    assert ours["cover"]["position"] == 100 - position  # Home Assistant counts 100 as open


@pytest.mark.parametrize("level", [0, 1, 50, 100])
def test_d2_01_status_matches_library(level: int) -> None:
    payload = [0x04, 0x00, level]
    ours = decode("D2-01-01", telegram(0xD2, payload))
    theirs = library(0xD2, payload, 0x01, 0x01, command=4)
    assert ours["switch"] == {"channel": theirs["IO"], "level": theirs["OV"], "on": level > 0}


# ── profiles worked out from the specification ─────────────────────────


@pytest.mark.parametrize(
    ("eep", "payload", "expected"),
    [
        ("A5-04-02", [0, 125, 125, 0x0A], {"humidity": 50.0, "temperature": 20.0}),
        ("A5-06-02", [0xB4, 0x00, 0xFF, 0x08], {"illuminance": 1020.0}),
        ("A5-06-03", [0xB4, 0x0F, 0xA0, 0x08], {"illuminance": 62.0}),
        ("A5-07-02", [0xB4, 0, 0, 0x88], {"motion": True, "supply_voltage": 3.6}),
        ("A5-07-03", [0xB4, 0x19, 0x00, 0x08], {"motion": False, "illuminance": 100.0}),
        ("A5-08-02", [0xB4, 0xFF, 0x00, 0x08], {"illuminance": 1020.0, "temperature": 0.0, "motion": True}),
        ("A5-08-03", [0xB4, 0xFF, 0xFF, 0x0A], {"illuminance": 1530.0, "temperature": 50.0, "motion": False}),
        ("A5-09-02", [0xB4, 0xFF, 0xFF, 0x0A], {"co": 1020, "temperature": 51.0}),
        ("A5-09-04", [200, 0x80, 0x80, 0x0E], {"co2": 1280, "humidity": 100.0, "temperature": 25.6}),
        ("A5-09-05", [0x01, 0xF4, 0x00, 0x0A], {"voc": 500.0}),
        ("A5-09-08", [0, 0xFF, 0, 0x08], {"co2": 2000}),
        (
            "A5-13-01",
            [0xFF, 0x55, 0xFF, 0x1A],
            {"illuminance": 999, "temperature": 0.0, "wind_speed": 70.0, "rain": True},
        ),
        ("A5-12-02", [0x00, 0x00, 0x7B, 0x0D], {"gas_flow": 12.3}),
        ("A5-12-03", [0x00, 0x30, 0x39, 0x0A], {"water_total": 123.45}),
        ("A5-14-09", [0xB4, 0, 0, 0x0A], {"window_position": "tilted", "opening": True}),
        ("A5-14-0A", [0xB4, 0, 0, 0x09], {"window_position": "closed", "opening": False, "vibration": True}),
        ("A5-14-0A", [0xB4, 0, 0, 0x0F], {"window_position": "open", "opening": True, "vibration": True}),
        ("A5-38-08", [0x02, 0x40, 0x01, 0x09], {"brightness": 64, "light_on": True}),
    ],
)
def test_specification_vectors(eep: str, payload: list[int], expected: dict) -> None:
    readings = decode(eep, telegram(0xA5, payload))
    for key, value in expected.items():
        assert readings[key] == pytest.approx(value, abs=0.051) if isinstance(value, float) else readings[key] == value


@pytest.mark.parametrize(
    ("byte", "expected"),
    [
        (0x10, {"smoke": True, "battery_low": False}),
        (0x00, {"smoke": False, "battery_low": False}),
        (0x30, {"battery_low": True}),
        (0x77, {}),
    ],
)
def test_f6_05_02_smoke(byte: int, expected: dict) -> None:
    assert decode("F6-05-02", telegram(0xF6, [byte], 0x30)) == expected


def test_f6_01_01_push_button() -> None:
    assert decode("F6-01-01", telegram(0xF6, [0x10], 0x30)) == {"push": True}
    assert decode("F6-01-01", telegram(0xF6, [0x00], 0x20)) == {"push": False}


def test_d2_01_measurements() -> None:
    assert decode("D2-01-12", telegram(0xD2, [0x07, 0x60, 0, 0, 0, 120])) == {"power": 120, "channel": 0}
    assert decode("D2-01-12", telegram(0xD2, [0x07, 0x20, 0, 0, 0x30, 0xD4])) == {"energy": 12.5, "channel": 0}
    assert decode("D2-01-12", telegram(0xD2, [0x04, 0x00, 0x7F])) == {}


def test_d2_03_0a_button() -> None:
    assert decode("D2-03-0A", telegram(0xD2, [90, 0x02])) == {"button": "double_press", "battery": 90}
    assert decode("D2-03-0A", telegram(0xD2, [0xFF, 0x01])) == {"button": "short_press"}
    assert decode("D2-03-0A", telegram(0xD2, [90, 0x09])) == {}


# ── teach-in telegrams and edge cases ──────────────────────────────────


def test_4bs_teach_in_with_profile() -> None:
    assert parse_4bs_teach_in(telegram(0xA5, [0x08, 0x2F, 0xFF, 0x80])) == ("A5-02-05", 0x7FF)
    # A data telegram is not a teach-in, nor is a teach-in without profile.
    assert parse_4bs_teach_in(telegram(0xA5, [0x08, 0x2F, 0xFF, 0x08])) is None
    assert parse_4bs_teach_in(telegram(0xA5, [0x08, 0x2F, 0xFF, 0x00])) is None


def test_4bs_decoders_ignore_teach_in() -> None:
    assert decode("A5-02-05", telegram(0xA5, [0x08, 0x2F, 0xFF, 0x80])) == {}


def test_ute_request() -> None:
    request = parse_ute_request(telegram(0xD4, [0xA0, 0x01, 0xFF, 0x07, 0x12, 0x01, 0xD2]))
    assert request == {
        "bidirectional": True,
        "response_expected": True,
        "request": 2,
        "channels": 1,
        "manufacturer": 0x7FF,
        "eep": "D2-01-12",
    }
    # Command 1 is a response, not a request.
    assert parse_ute_request(telegram(0xD4, [0x91, 0x01, 0xFF, 0x07, 0x12, 0x01, 0xD2])) is None


def test_wrong_rorg_and_short_data_give_nothing() -> None:
    assert decode("A5-02-05", telegram(0xF6, [0x30])) == {}
    assert decode("D2-05-00", b"\xd2\x01") == {}
    assert decode("XX-00-00", telegram(0xA5, [0, 0, 0, 8])) == {}


def test_summary() -> None:
    assert summarize({"temperature": 21.5, "humidity": 40.0}) == "temperature 21.5 °C, humidity 40.0 %"
    assert summarize({"rocker": {"pressed": True, "action": 1, "second_action": None}}) == "rocker A0 pressed"
    assert summarize({"opening": True}) == "opening yes"
    assert profiles.summarize({}) == ""
