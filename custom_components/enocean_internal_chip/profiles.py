"""Decoders for EnOcean Equipment Profiles (EEP).

Every decoder is a pure function. It receives the ERP1 data bytes exactly as
the radio delivers them (RORG, payload, 4-byte sender ID, status byte) and
returns a dict of named readings. It returns an empty dict when the telegram
carries nothing for that profile: a teach-in telegram, a different RORG, a
value the profile marks as invalid.

Bit positions follow the EEP specification: offset 0 is the most significant
bit of the first payload byte (DB3 for 4BS telegrams).

Where the ``enocean`` Python library ships a definition for a profile, the test
suite checks these decoders against it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .esp3 import RORG_1BS, RORG_4BS, RORG_RPS, RORG_UTE, RORG_VLD

Readings = dict[str, Any]
Decoder = Callable[[bytes], Readings]

# Window and handle positions, used as states of ENUM sensors.
CLOSED = "closed"
TILTED = "tilted"
OPEN = "open"
POSITIONS = [CLOSED, TILTED, OPEN]

# D2-03-0A button actions, used as event types.
BUTTON_ACTIONS = {
    0x01: "short_press",
    0x02: "double_press",
    0x03: "long_press",
    0x04: "long_release",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def payload_of(data: bytes) -> bytes:
    """The user data between the RORG byte and the sender ID."""
    return bytes(data[1:-5]) if len(data) >= 6 else b""


def bits(payload: bytes, offset: int, size: int) -> int:
    """Read ``size`` bits starting at EEP ``offset`` (0 = MSB of byte 0)."""
    total = len(payload) * 8
    if offset + size > total:
        raise ValueError("bit field outside the payload")
    value = int.from_bytes(payload, "big")
    return (value >> (total - offset - size)) & ((1 << size) - 1)


def scale(raw: float, rmin: float, rmax: float, smin: float, smax: float, digits: int = 1) -> float:
    """Linear mapping of a raw value, as the EEP tables describe it."""
    return round(smin + (raw - rmin) * (smax - smin) / (rmax - rmin), digits)


def is_4bs_data(payload: bytes) -> bool:
    """4BS telegrams carry data only when the LRN bit (DB0.3) is set."""
    return len(payload) == 4 and bool(payload[3] & 0x08)


def parse_4bs_teach_in(data: bytes) -> tuple[str, int] | None:
    """EEP and manufacturer ID from a 4BS teach-in telegram with profile info.

    Returns ``None`` for data telegrams and for teach-ins that do not declare
    their profile (LRN type bit DB0.7 cleared).
    """
    if not data or data[0] != RORG_4BS:
        return None
    payload = payload_of(data)
    if len(payload) != 4 or payload[3] & 0x08 or not payload[3] & 0x80:
        return None
    func = payload[0] >> 2
    ptype = ((payload[0] & 0x03) << 5) | (payload[1] >> 3)
    manufacturer = ((payload[1] & 0x07) << 8) | payload[2]
    return f"A5-{func:02X}-{ptype:02X}", manufacturer


def parse_ute_request(data: bytes) -> dict[str, Any] | None:
    """Fields of a UTE teach-in query, or ``None`` if it is not one."""
    if len(data) < 13 or data[0] != RORG_UTE:
        return None
    db6 = data[1]
    if db6 & 0x0F != 0x00:  # CMD 0 = teach-in query; 1 would be a response
        return None
    return {
        "bidirectional": bool(db6 & 0x80),
        "response_expected": not db6 & 0x40,
        # 0 = teach-in, 1 = deletion, 2 = not specified
        "request": (db6 >> 4) & 0x03,
        "channels": data[2],
        "manufacturer": ((data[4] & 0x07) << 8) | data[3],
        "eep": f"{data[7]:02X}-{data[6]:02X}-{data[5]:02X}",
    }


def _voltage_250(raw: int) -> float | None:
    """Supply voltage coded 0..250 -> 0..5.0 V. 251..255 are error codes."""
    return None if raw > 250 else scale(raw, 0, 250, 0, 5.0, 2)


def _voltage_255(raw: int) -> float:
    """Supply voltage coded 0..255 -> 0..5.1 V."""
    return scale(raw, 0, 255, 0, 5.1, 2)


def _clean(readings: Readings) -> Readings:
    return {key: value for key, value in readings.items() if value is not None}


# --------------------------------------------------------------------------
# RPS (F6)
# --------------------------------------------------------------------------


def decode_f6_02(data: bytes) -> Readings:
    """F6-02-01 / F6-02-02 rocker switch with two rockers.

    With the NU status bit set the data byte names the button: bits 7..5 are
    the first action (0 = AI, 1 = A0, 2 = BI, 3 = B0), bit 4 the energy bow.
    With NU cleared it is a release, or several buttons at once.
    """
    if len(data) < 7 or data[0] != RORG_RPS:
        return {}
    byte = data[1]
    status = data[-1]
    if status & 0x10:  # NU: a single, named button
        second = (byte >> 1) & 0x07 if byte & 0x01 else None
        return {
            "rocker": {
                "pressed": bool(byte & 0x10),
                "action": (byte >> 5) & 0x07,
                "second_action": second,
            }
        }
    return {"rocker": {"pressed": bool(byte & 0x10), "action": None, "second_action": None}}


def decode_f6_01_01(data: bytes) -> Readings:
    """F6-01-01 push button: data bit 4 is the button state."""
    if len(data) < 7 or data[0] != RORG_RPS:
        return {}
    return {"push": bool(data[1] & 0x10)}


def decode_f6_05_02(data: bytes) -> Readings:
    """F6-05-02 smoke detector: 0x00 no alarm, 0x10 alarm, 0x30 low battery."""
    if len(data) < 7 or data[0] != RORG_RPS:
        return {}
    byte = data[1]
    if byte == 0x10:
        return {"smoke": True, "battery_low": False}
    if byte == 0x00:
        return {"smoke": False, "battery_low": False}
    if byte == 0x30:
        return {"battery_low": True}
    return {}


def decode_f6_10_00(data: bytes) -> Readings:
    """F6-10-00 mechanical window handle."""
    if len(data) < 7 or data[0] != RORG_RPS:
        return {}
    code = (data[1] & 0x70) >> 4
    if code == 0x07:
        position = CLOSED
    elif code in (0x04, 0x06):
        position = OPEN
    elif code == 0x05:
        position = TILTED
    else:
        return {}
    return {"handle_position": position, "opening": position != CLOSED}


# --------------------------------------------------------------------------
# 1BS (D5)
# --------------------------------------------------------------------------


def decode_d5_00_01(data: bytes) -> Readings:
    """D5-00-01 single input contact: bit 0 is 1 when closed."""
    if len(data) < 7 or data[0] != RORG_1BS:
        return {}
    byte = data[1]
    if not byte & 0x08:  # teach-in
        return {}
    return {"opening": not byte & 0x01}


# --------------------------------------------------------------------------
# 4BS (A5)
# --------------------------------------------------------------------------

#: A5-02-xx: temperature range of each 8-bit type. Raw 255 is the minimum.
A5_02_RANGES: dict[int, tuple[float, float]] = {
    0x01: (-40, 0),
    0x02: (-30, 10),
    0x03: (-20, 20),
    0x04: (-10, 30),
    0x05: (0, 40),
    0x06: (10, 50),
    0x07: (20, 60),
    0x08: (30, 70),
    0x09: (40, 80),
    0x0A: (50, 90),
    0x0B: (60, 100),
    0x10: (-60, 20),
    0x11: (-50, 30),
    0x12: (-40, 40),
    0x13: (-30, 50),
    0x14: (-20, 60),
    0x15: (-10, 70),
    0x16: (0, 80),
    0x17: (10, 90),
    0x18: (20, 100),
    0x19: (30, 110),
    0x1A: (40, 120),
    0x1B: (50, 130),
}

#: A5-02-20 and A5-02-30 use 10 bits. Raw 1023 is the minimum.
A5_02_RANGES_10BIT: dict[int, tuple[float, float]] = {
    0x20: (-10, 41.2),
    0x30: (-40, 62.3),
}


def _decode_a5_02(ptype: int) -> Decoder:
    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        if ptype in A5_02_RANGES_10BIT:
            low, high = A5_02_RANGES_10BIT[ptype]
            return {"temperature": scale(bits(payload, 14, 10), 1023, 0, low, high)}
        low, high = A5_02_RANGES[ptype]
        return {"temperature": scale(payload[2], 255, 0, low, high)}

    return decode


def _decode_a5_04(ptype: int) -> Decoder:
    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        if ptype == 0x03:
            return {
                "humidity": scale(payload[0], 0, 255, 0, 100),
                "temperature": scale(bits(payload, 14, 10), 0, 1023, -20, 60),
            }
        low, high = (0, 40) if ptype == 0x01 else (-20, 60)
        readings: Readings = {"humidity": scale(payload[1], 0, 250, 0, 100)}
        if payload[3] & 0x02:  # TSN: temperature sensor available
            readings["temperature"] = scale(payload[2], 0, 250, low, high)
        return readings

    return decode


def _decode_a5_06(ptype: int) -> Decoder:
    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        if ptype == 0x03:
            raw = bits(payload, 8, 10)
            return _clean(
                {
                    "supply_voltage": _voltage_250(payload[0]),
                    "illuminance": float(raw) if raw <= 1000 else None,
                }
            )
        # 0x01 and 0x02: two ranges, DB0.0 says which byte is valid.
        if ptype == 0x01:
            ill2, ill1 = (300, 30000), (600, 60000)
        else:
            ill2, ill1 = (0, 510), (0, 1020)
        if payload[3] & 0x01:
            lux = scale(payload[1], 0, 255, *ill2, 0)
        else:
            lux = scale(payload[2], 0, 255, *ill1, 0)
        return {"supply_voltage": _voltage_255(payload[0]), "illuminance": lux}

    return decode


def _decode_a5_07(ptype: int) -> Decoder:
    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        if ptype == 0x01:
            readings: Readings = {"motion": bool(payload[2] & 0x80)}
            if payload[3] & 0x01:  # SVA: supply voltage available
                readings["supply_voltage"] = _voltage_250(payload[0])
            return _clean(readings)
        readings = {
            "motion": bool(payload[3] & 0x80),
            "supply_voltage": _voltage_250(payload[0]),
        }
        if ptype == 0x03:
            raw = bits(payload, 8, 10)
            readings["illuminance"] = float(raw) if raw <= 1000 else None
        return _clean(readings)

    return decode


A5_08_RANGES = {
    0x01: ((0, 510), (0, 51)),
    0x02: ((0, 1020), (0, 51)),
    0x03: ((0, 1530), (-30, 50)),
}


def _decode_a5_08(ptype: int) -> Decoder:
    lux_range, temp_range = A5_08_RANGES[ptype]

    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        return {
            "supply_voltage": _voltage_255(payload[0]),
            "illuminance": scale(payload[1], 0, 255, *lux_range, 0),
            "temperature": scale(payload[2], 0, 255, *temp_range),
            # PIRS: 0 = PIR on (motion), 1 = PIR off
            "motion": not payload[3] & 0x02,
        }

    return decode


def decode_a5_09_02(data: bytes) -> Readings:
    """A5-09-02 carbon monoxide, 0..1020 ppm."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    readings: Readings = {
        "supply_voltage": _voltage_250(payload[0]),
        "co": scale(payload[1], 0, 255, 0, 1020, 0),
    }
    if payload[3] & 0x02:
        readings["temperature"] = scale(payload[2], 0, 255, 0, 51)
    return _clean(readings)


def decode_a5_09_04(data: bytes) -> Readings:
    """A5-09-04 CO2 with optional humidity and temperature."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    readings: Readings = {"co2": scale(payload[1], 0, 255, 0, 2550, 0)}
    if payload[3] & 0x04:
        readings["humidity"] = scale(payload[0], 0, 200, 0, 100)
    if payload[3] & 0x02:
        readings["temperature"] = scale(payload[2], 0, 255, 0, 51)
    return readings


def decode_a5_09_05(data: bytes) -> Readings:
    """A5-09-05 VOC concentration in ppb, with a scale multiplier."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    multiplier = (0.01, 0.1, 1, 10)[payload[3] & 0x03]
    concentration = ((payload[0] << 8) | payload[1]) * multiplier
    return {"voc": round(concentration, 2), "voc_id": payload[2]}


def decode_a5_09_08(data: bytes) -> Readings:
    """A5-09-08 and A5-09-09 pure CO2, 0..2000 ppm."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    return {"co2": scale(payload[1], 0, 255, 0, 2000, 0)}


_A5_12_KEYS = {
    0x01: ("power", "energy"),
    0x02: ("gas_flow", "gas_total"),
    0x03: ("water_flow", "water_total"),
}


def _decode_a5_12(ptype: int) -> Decoder:
    current_key, total_key = _A5_12_KEYS[ptype]

    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        value = bits(payload, 0, 24) / (10 ** (payload[3] & 0x03))
        # DT (DB0.2): 1 = current value, 0 = cumulative value
        key = current_key if payload[3] & 0x04 else total_key
        return {key: round(value, 3), "tariff": payload[3] >> 4}

    return decode


def decode_a5_13_01(data: bytes) -> Readings:
    """A5-13-01 weather station. Other A5-13 telegrams carry another identifier."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    if payload[3] >> 4 != 0x01:
        return {}
    return {
        "illuminance": scale(payload[0], 0, 255, 0, 999, 0),
        "temperature": scale(payload[1], 0, 255, -40, 80),
        "wind_speed": scale(payload[2], 0, 255, 0, 70),
        "rain": bool(payload[3] & 0x02),
        "night": bool(payload[3] & 0x04),
    }


def decode_a5_14_01(data: bytes) -> Readings:
    """A5-14-01 contact with supply voltage: DB0.0 is 1 when open."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    return _clean({"supply_voltage": _voltage_250(payload[0]), "opening": bool(payload[3] & 0x01)})


_A5_14_POSITIONS = {0b00: CLOSED, 0b01: TILTED, 0b11: OPEN}


def _decode_a5_14_window(with_vibration: bool) -> Decoder:
    """A5-14-09 / A5-14-0A window sensor: position in DB0 bits 2..1.

    A5-14-0A adds a vibration alarm in DB0 bit 0. The position is read with
    that bit masked out: otherwise a telegram sent while someone forces the
    window would not match any position and would be lost.
    """

    def decode(data: bytes) -> Readings:
        payload = payload_of(data)
        if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
            return {}
        readings: Readings = {"supply_voltage": _voltage_250(payload[0])}
        position = _A5_14_POSITIONS.get((payload[3] >> 1) & 0x03)
        if position is not None:
            readings["window_position"] = position
            readings["opening"] = position != CLOSED
        if with_vibration:
            readings["vibration"] = bool(payload[3] & 0x01)
        return _clean(readings)

    return decode


def decode_a5_38_08(data: bytes) -> Readings:
    """A5-38-08 dimming feedback (command 2), as some dimmers send it back."""
    payload = payload_of(data)
    if data[:1] != bytes((RORG_4BS,)) or not is_4bs_data(payload):
        return {}
    if payload[0] == 0x02:
        return {"brightness": min(payload[1], 100), "light_on": bool(payload[3] & 0x01)}
    return {}


# --------------------------------------------------------------------------
# VLD (D2)
# --------------------------------------------------------------------------

#: D2-01 measurement units (CMD 7): energy in kWh or power in W.
_D2_01_UNITS: dict[int, tuple[str, float]] = {
    0: ("energy", 1 / 3_600_000),  # Ws
    1: ("energy", 1 / 1000),  # Wh
    2: ("energy", 1),  # kWh
    3: ("power", 1),  # W
    4: ("power", 1000),  # kW
}


def decode_d2_01(data: bytes) -> Readings:
    """D2-01 electronic switch: status (CMD 4) and measurement (CMD 7)."""
    if len(data) < 8 or data[0] != RORG_VLD:
        return {}
    payload = payload_of(data)
    command = payload[0] & 0x0F
    if command == 0x04 and len(payload) >= 3:
        level = payload[2] & 0x7F
        if level > 100:  # 127 = not valid
            return {}
        return {"switch": {"channel": payload[1] & 0x1F, "level": level, "on": level > 0}}
    if command == 0x07 and len(payload) >= 6:
        unit = payload[1] >> 5
        if unit not in _D2_01_UNITS:
            return {}
        key, factor = _D2_01_UNITS[unit]
        value = int.from_bytes(payload[2:6], "big") * factor
        return {key: round(value, 3), "channel": payload[1] & 0x1F}
    return {}


def decode_d2_03_0a(data: bytes) -> Readings:
    """D2-03-0A push button: battery percentage, then the action."""
    if len(data) < 8 or data[0] != RORG_VLD:
        return {}
    payload = payload_of(data)
    action = BUTTON_ACTIONS.get(payload[1])
    if action is None:
        return {}
    readings: Readings = {"button": action}
    if payload[0] <= 100:
        readings["battery"] = payload[0]
    return readings


def decode_d2_05_00(data: bytes) -> Readings:
    """D2-05-00 blinds control: the reply to a query or a move (CMD 4).

    The profile counts 0 % as fully open; Home Assistant counts 0 as closed.
    """
    if len(data) < 10 or data[0] != RORG_VLD:
        return {}
    payload = payload_of(data)
    if len(payload) < 4 or payload[3] & 0x0F != 0x04:
        return {}
    position = payload[0] & 0x7F
    angle = payload[1] & 0x7F
    return {
        "cover": {
            "position": None if position > 100 else 100 - position,
            "angle": None if angle > 100 else angle,
        }
    }


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

DECODERS: dict[str, Decoder] = {
    "F6-01-01": decode_f6_01_01,
    "F6-02-01": decode_f6_02,
    "F6-02-02": decode_f6_02,
    "F6-05-02": decode_f6_05_02,
    "F6-10-00": decode_f6_10_00,
    "D5-00-01": decode_d5_00_01,
    "A5-09-02": decode_a5_09_02,
    "A5-09-04": decode_a5_09_04,
    "A5-09-05": decode_a5_09_05,
    "A5-09-08": decode_a5_09_08,
    "A5-09-09": decode_a5_09_08,
    "A5-13-01": decode_a5_13_01,
    "A5-14-01": decode_a5_14_01,
    "A5-14-09": _decode_a5_14_window(with_vibration=False),
    "A5-14-0A": _decode_a5_14_window(with_vibration=True),
    "A5-38-08": decode_a5_38_08,
    "D2-03-0A": decode_d2_03_0a,
    "D2-05-00": decode_d2_05_00,
}
DECODERS.update({f"A5-02-{t:02X}": _decode_a5_02(t) for t in (*A5_02_RANGES, *A5_02_RANGES_10BIT)})
DECODERS.update({f"A5-04-{t:02X}": _decode_a5_04(t) for t in (0x01, 0x02, 0x03)})
DECODERS.update({f"A5-06-{t:02X}": _decode_a5_06(t) for t in (0x01, 0x02, 0x03)})
DECODERS.update({f"A5-07-{t:02X}": _decode_a5_07(t) for t in (0x01, 0x02, 0x03)})
DECODERS.update({f"A5-08-{t:02X}": _decode_a5_08(t) for t in A5_08_RANGES})
DECODERS.update({f"A5-12-{t:02X}": _decode_a5_12(t) for t in _A5_12_KEYS})
DECODERS.update({f"D2-01-{t:02X}": decode_d2_01 for t in range(0x00, 0x13)})


def decode(eep: str, data: bytes) -> Readings:
    """Decode ``data`` with the profile ``eep``. Unknown profiles give ``{}``."""
    decoder = DECODERS.get(eep.upper())
    if decoder is None:
        return {}
    try:
        return decoder(bytes(data))
    except (IndexError, ValueError):
        return {}


# --------------------------------------------------------------------------
# Human readable summaries, for the telegram list
# --------------------------------------------------------------------------

_UNITS = {
    "temperature": "°C",
    "humidity": "%",
    "illuminance": "lx",
    "supply_voltage": "V",
    "co2": "ppm",
    "co": "ppm",
    "voc": "ppb",
    "power": "W",
    "energy": "kWh",
    "gas_flow": "l/s",
    "gas_total": "m³",
    "water_flow": "l/s",
    "water_total": "m³",
    "wind_speed": "m/s",
    "battery": "%",
}

_HIDDEN = {"tariff", "channel", "voc_id", "night"}


def summarize(readings: Readings) -> str:
    """One line such as ``temperature 21.3 °C, humidity 45 %``."""
    parts: list[str] = []
    for key, value in readings.items():
        if key in _HIDDEN:
            continue
        label = key.replace("_", " ")
        if key == "rocker":
            if value.get("action") is None:
                parts.append("rocker released")
            else:
                names = ("AI", "A0", "BI", "B0")
                button = names[value["action"]] if value["action"] < 4 else str(value["action"])
                parts.append(f"rocker {button} {'pressed' if value['pressed'] else 'released'}")
        elif key == "switch":
            parts.append(f"channel {value['channel']} {'on' if value['on'] else 'off'} ({value['level']} %)")
        elif key == "cover":
            position = value.get("position")
            parts.append(f"cover position {position} %" if position is not None else "cover position unknown")
        elif isinstance(value, bool):
            parts.append(f"{label} {'yes' if value else 'no'}")
        else:
            unit = _UNITS.get(key)
            parts.append(f"{label} {value} {unit}" if unit else f"{label} {value}")
    return ", ".join(parts)
