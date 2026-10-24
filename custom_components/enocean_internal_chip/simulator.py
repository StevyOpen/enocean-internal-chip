"""Ready-made telegrams for trying the integration without a device.

Each preset builds the radio telegram a real device of that profile would
send. Simulated telegrams take the same path as received ones (log, decoder,
entities), marked as simulated. They are off unless the "Allow simulated
telegrams" option is on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .esp3 import (
    BROADCAST_ID,
    RORG_1BS,
    RORG_4BS,
    RORG_RPS,
    RORG_UTE,
    RORG_VLD,
    Esp3Packet,
    parse_id,
    radio_packet,
)

#: Signal strength given to simulated telegrams: -64 dBm, a good link.
SIMULATED_DBM = 64

# RPS status byte: T21 set, NU set for a named button, cleared for a release.
RPS_PRESSED = 0x30
RPS_RELEASED = 0x20


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    name: str
    group: str
    eep: str
    rorg: int
    payload: bytes
    status: int = 0x00
    default_sender: str = "05:00:00:01"

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "name": self.name,
            "group": self.group,
            "eep": self.eep,
            "default_sender": self.default_sender,
        }


def _p(
    key: str,
    name: str,
    group: str,
    eep: str,
    rorg: int,
    payload: list[int],
    status: int = 0x00,
    sender: str = "05:00:00:01",
) -> Preset:
    return Preset(key, name, group, eep, rorg, bytes(payload), status, sender)


PRESETS: tuple[Preset, ...] = (
    # Buttons
    _p(
        "rocker_a0",
        "Rocker A, upper button pressed",
        "Buttons",
        "F6-02-01",
        RORG_RPS,
        [0x30],
        RPS_PRESSED,
        "05:00:00:01",
    ),
    _p(
        "rocker_ai",
        "Rocker A, lower button pressed",
        "Buttons",
        "F6-02-01",
        RORG_RPS,
        [0x10],
        RPS_PRESSED,
        "05:00:00:01",
    ),
    _p(
        "rocker_b0",
        "Rocker B, upper button pressed",
        "Buttons",
        "F6-02-01",
        RORG_RPS,
        [0x70],
        RPS_PRESSED,
        "05:00:00:01",
    ),
    _p(
        "rocker_bi",
        "Rocker B, lower button pressed",
        "Buttons",
        "F6-02-01",
        RORG_RPS,
        [0x50],
        RPS_PRESSED,
        "05:00:00:01",
    ),
    _p("rocker_release", "Rocker released", "Buttons", "F6-02-01", RORG_RPS, [0x00], RPS_RELEASED, "05:00:00:01"),
    _p("push_press", "Push button pressed", "Buttons", "F6-01-01", RORG_RPS, [0x10], RPS_PRESSED, "05:00:00:02"),
    _p("push_release", "Push button released", "Buttons", "F6-01-01", RORG_RPS, [0x00], RPS_RELEASED, "05:00:00:02"),
    _p(
        "button_short",
        "Button short press, battery 90 %",
        "Buttons",
        "D2-03-0A",
        RORG_VLD,
        [90, 0x01],
        0x00,
        "05:00:00:03",
    ),
    _p("button_double", "Button double press", "Buttons", "D2-03-0A", RORG_VLD, [90, 0x02], 0x00, "05:00:00:03"),
    _p("button_long", "Button long press", "Buttons", "D2-03-0A", RORG_VLD, [90, 0x03], 0x00, "05:00:00:03"),
    # Doors and windows
    _p("contact_open", "Contact open", "Doors and windows", "D5-00-01", RORG_1BS, [0x08], 0x00, "05:00:00:10"),
    _p("contact_closed", "Contact closed", "Doors and windows", "D5-00-01", RORG_1BS, [0x09], 0x00, "05:00:00:10"),
    _p(
        "window_closed",
        "Window closed",
        "Doors and windows",
        "A5-14-0A",
        RORG_4BS,
        [0xB4, 0, 0, 0x08],
        0x00,
        "05:00:00:11",
    ),
    _p(
        "window_tilted",
        "Window tilted",
        "Doors and windows",
        "A5-14-0A",
        RORG_4BS,
        [0xB4, 0, 0, 0x0A],
        0x00,
        "05:00:00:11",
    ),
    _p(
        "window_open", "Window open", "Doors and windows", "A5-14-0A", RORG_4BS, [0xB4, 0, 0, 0x0E], 0x00, "05:00:00:11"
    ),
    _p(
        "window_vibration",
        "Window closed, vibration alarm",
        "Doors and windows",
        "A5-14-0A",
        RORG_4BS,
        [0xB4, 0, 0, 0x09],
        0x00,
        "05:00:00:11",
    ),
    _p(
        "handle_closed", "Handle closed", "Doors and windows", "F6-10-00", RORG_RPS, [0xF0], RPS_RELEASED, "05:00:00:12"
    ),
    _p(
        "handle_tilted", "Handle tilted", "Doors and windows", "F6-10-00", RORG_RPS, [0xD0], RPS_RELEASED, "05:00:00:12"
    ),
    _p("handle_open", "Handle open", "Doors and windows", "F6-10-00", RORG_RPS, [0xC0], RPS_RELEASED, "05:00:00:12"),
    # Climate and safety
    _p(
        "temperature",
        "Temperature 21.5 °C",
        "Climate and safety",
        "A5-02-05",
        RORG_4BS,
        [0, 0, 118, 0x08],
        0x00,
        "05:00:00:20",
    ),
    _p(
        "temperature_humidity",
        "22.4 °C, humidity 55.2 %",
        "Climate and safety",
        "A5-04-01",
        RORG_4BS,
        [0, 138, 140, 0x0A],
        0x00,
        "05:00:00:21",
    ),
    _p(
        "motion_on",
        "Motion detected",
        "Climate and safety",
        "A5-07-01",
        RORG_4BS,
        [0xB4, 0, 0x80, 0x09],
        0x00,
        "05:00:00:22",
    ),
    _p(
        "motion_off",
        "No motion",
        "Climate and safety",
        "A5-07-01",
        RORG_4BS,
        [0xB4, 0, 0x00, 0x09],
        0x00,
        "05:00:00:22",
    ),
    _p("smoke_alarm", "Smoke alarm", "Climate and safety", "F6-05-02", RORG_RPS, [0x10], RPS_PRESSED, "05:00:00:23"),
    _p(
        "smoke_clear",
        "Smoke alarm cleared",
        "Climate and safety",
        "F6-05-02",
        RORG_RPS,
        [0x00],
        RPS_PRESSED,
        "05:00:00:23",
    ),
    _p(
        "smoke_battery",
        "Smoke detector battery low",
        "Climate and safety",
        "F6-05-02",
        RORG_RPS,
        [0x30],
        RPS_PRESSED,
        "05:00:00:23",
    ),
    # Actuators and meters
    _p(
        "switch_on",
        "Switch reports on",
        "Actuators and meters",
        "D2-01-12",
        RORG_VLD,
        [0x04, 0x00, 0x64],
        0x00,
        "05:00:00:30",
    ),
    _p(
        "switch_off",
        "Switch reports off",
        "Actuators and meters",
        "D2-01-12",
        RORG_VLD,
        [0x04, 0x00, 0x00],
        0x00,
        "05:00:00:30",
    ),
    _p(
        "switch_power",
        "Switch reports 120 W",
        "Actuators and meters",
        "D2-01-12",
        RORG_VLD,
        [0x07, 0x60, 0, 0, 0, 120],
        0x00,
        "05:00:00:30",
    ),
    _p(
        "switch_energy",
        "Switch reports 12.5 kWh",
        "Actuators and meters",
        "D2-01-12",
        RORG_VLD,
        [0x07, 0x20, 0, 0, 0x30, 0xD4],
        0x00,
        "05:00:00:30",
    ),
    _p(
        "cover_position",
        "Shutter reports 40 % open",
        "Actuators and meters",
        "D2-05-00",
        RORG_VLD,
        [60, 0x7F, 0x00, 0x04],
        0x00,
        "05:00:00:31",
    ),
    _p(
        "meter_power",
        "Electricity meter 1500 W",
        "Actuators and meters",
        "A5-12-01",
        RORG_4BS,
        [0x00, 0x05, 0xDC, 0x0C],
        0x00,
        "05:00:00:32",
    ),
    _p(
        "meter_energy",
        "Electricity meter 1234.5 kWh",
        "Actuators and meters",
        "A5-12-01",
        RORG_4BS,
        [0x00, 0x30, 0x39, 0x09],
        0x00,
        "05:00:00:32",
    ),
    # Teach-in
    _p(
        "teach_in_ute",
        "Pairing request from a two-way switch (D2-01-12)",
        "Teach-in",
        "D2-01-12",
        RORG_UTE,
        # Bidirectional, response expected, teach-in or deletion; 1 channel;
        # manufacturer 0x7FF; then TYPE, FUNC, RORG of the profile.
        [0xA0, 0x01, 0xFF, 0x07, 0x12, 0x01, 0xD2],
        0x00,
        "05:00:00:40",
    ),
    _p(
        "teach_in_4bs",
        "Teach-in from a temperature sensor (A5-02-05)",
        "Teach-in",
        "A5-02-05",
        RORG_4BS,
        [0x08, 0x2F, 0xFF, 0x80],
        0x00,
        "05:00:00:41",
    ),
)

PRESETS_BY_KEY: dict[str, Preset] = {preset.key: preset for preset in PRESETS}


def build(preset_key: str, sender: str | None = None) -> Esp3Packet:
    """The telegram of ``preset_key``, sent by ``sender`` or the preset's default."""
    preset = PRESETS_BY_KEY.get(preset_key)
    if preset is None:
        raise ValueError(f"Unknown preset: {preset_key}")
    raw_sender = parse_id(sender or preset.default_sender)
    if raw_sender is None:
        raise ValueError("The sender must be 4 hexadecimal bytes, like 05:00:00:01")
    return received(preset.rorg, preset.payload, raw_sender, preset.status)


def build_custom(rorg: int, payload: bytes, sender: str, status: int = 0x00) -> Esp3Packet:
    """A free-form telegram, for profiles without a preset."""
    raw_sender = parse_id(sender)
    if raw_sender is None:
        raise ValueError("The sender must be 4 hexadecimal bytes, like 05:00:00:01")
    if not 0 <= rorg <= 0xFF or not 1 <= len(payload) <= 14:
        raise ValueError("The telegram needs a RORG byte and 1 to 14 data bytes")
    return received(rorg, payload, raw_sender, status)


def received(rorg: int, payload: bytes, sender: bytes, status: int = 0x00) -> Esp3Packet:
    """A telegram as the module delivers it: subtelegram 1, broadcast, with signal."""
    return radio_packet(rorg, payload, sender, status, BROADCAST_ID, subtel=1, dbm=SIMULATED_DBM)
