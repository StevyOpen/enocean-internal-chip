"""The device types this integration knows how to handle.

A device type ties together what a device sends (its EEP profiles), which
entities it gets in Home Assistant, and how it is paired. Only profiles that
have a working decoder in ``profiles.py`` are listed: a type that cannot
create entities is not offered.

``verified`` marks the types that have been run against real devices. The
others are implemented from the EEP specification and are expected to work,
but have not been checked on hardware yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.const import Platform

from .profiles import A5_02_RANGES, A5_02_RANGES_10BIT

# How a device is paired with the gateway.
PAIRING_NONE = "none"  # sensors: the gateway only listens
PAIRING_UTE = "ute"  # the device asks, the gateway answers (UTE teach-in)
PAIRING_RPS = "rps"  # the gateway sends rocker telegrams from its own sender
PAIRING_4BS = "4bs"  # the gateway sends a 4BS teach-in telegram

#: Pairing methods that need a sender ID of their own for each actuator.
#: One-way actuators remember the sender, not the destination: two of them
#: taught with the same sender would always switch together.
OWN_SENDER_PAIRING = frozenset({PAIRING_RPS, PAIRING_4BS})


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """One entity of a device type."""

    kind: str
    #: Only for these profiles. Empty means every profile of the type.
    eeps: tuple[str, ...] = ()
    #: Created only after the device has reported this reading at least once.
    on_demand: bool = False

    def applies_to(self, eep: str) -> bool:
        return not self.eeps or eep in self.eeps


@dataclass(frozen=True, slots=True)
class DeviceType:
    key: str
    name: str
    category: str
    eeps: tuple[str, ...]
    entities: tuple[EntitySpec, ...]
    description: str
    pairing: str = PAIRING_NONE
    verified: bool = False
    icon: str = "mdi:radio-tower"
    notes: str = ""
    keywords: tuple[str, ...] = field(default=())

    @property
    def default_eep(self) -> str:
        return self.eeps[0]

    @property
    def family(self) -> str:
        return {"F6": "RPS", "D5": "1BS", "A5": "4BS", "D2": "VLD"}.get(self.eeps[0][:2], "")

    def entities_for(self, eep: str, seen: set[str] | frozenset[str] = frozenset()) -> list[EntitySpec]:
        return [spec for spec in self.entities if spec.applies_to(eep) and (not spec.on_demand or spec.kind in seen)]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "family": self.family,
            "eeps": list(self.eeps),
            "description": self.description,
            "pairing": self.pairing,
            "verified": self.verified,
            "icon": self.icon,
            "notes": self.notes,
            "entities": [
                {"kind": spec.kind, "eeps": list(spec.eeps), "on_demand": spec.on_demand} for spec in self.entities
            ],
        }


def _e(kind: str, *eeps: str, on_demand: bool = False) -> EntitySpec:
    return EntitySpec(kind, tuple(eeps), on_demand)


#: Which platform creates each entity kind.
KIND_PLATFORM: dict[str, Platform] = {
    # binary sensors
    "opening": Platform.BINARY_SENSOR,
    "opening_window": Platform.BINARY_SENSOR,
    "motion": Platform.BINARY_SENSOR,
    "vibration": Platform.BINARY_SENSOR,
    "smoke": Platform.BINARY_SENSOR,
    "battery_low": Platform.BINARY_SENSOR,
    "rain": Platform.BINARY_SENSOR,
    "rocker_state_a": Platform.BINARY_SENSOR,
    "rocker_state_b": Platform.BINARY_SENSOR,
    "button_toggle": Platform.BINARY_SENSOR,
    # sensors
    "temperature": Platform.SENSOR,
    "humidity": Platform.SENSOR,
    "illuminance": Platform.SENSOR,
    "supply_voltage": Platform.SENSOR,
    "battery": Platform.SENSOR,
    "window_position": Platform.SENSOR,
    "handle_position": Platform.SENSOR,
    "co": Platform.SENSOR,
    "co2": Platform.SENSOR,
    "voc": Platform.SENSOR,
    "power": Platform.SENSOR,
    "energy": Platform.SENSOR,
    "gas_flow": Platform.SENSOR,
    "gas_total": Platform.SENSOR,
    "water_flow": Platform.SENSOR,
    "water_total": Platform.SENSOR,
    "wind_speed": Platform.SENSOR,
    # events
    "rocker_event_a": Platform.EVENT,
    "rocker_event_b": Platform.EVENT,
    "push_event": Platform.EVENT,
    "button_event": Platform.EVENT,
    # actuators
    "switch_vld": Platform.SWITCH,
    "switch_rps": Platform.SWITCH,
    "switch_central": Platform.SWITCH,
    "cover_vld": Platform.COVER,
    "dimmer": Platform.LIGHT,
}

_TEMPERATURE_EEPS = ("A5-02-05",) + tuple(
    f"A5-02-{t:02X}" for t in sorted((*A5_02_RANGES, *A5_02_RANGES_10BIT)) if t != 0x05
)
_D2_01_EEPS = tuple(f"D2-01-{t:02X}" for t in range(0x00, 0x13))

_UTE_NOTES = (
    "Pairing: open the pairing window on the Gateway tab (or press Pair on the "
    "device), then put the device into learn mode. The gateway answers the "
    "request by itself. Many actuators also want a short press on their own "
    "button to confirm: if the device keeps asking, that confirmation is "
    "usually the missing step. Reset the device only if it keeps asking after "
    "the confirmation."
)

_OWN_SENDER_NOTES = (
    "This actuator gets a sender ID of its own, taken from the gateway address "
    "range. Pairing: put the actuator into learn mode, then press Pair. The "
    "gateway sends the teach-in telegram from that sender ID."
)

DEVICE_TYPES: tuple[DeviceType, ...] = (
    # ── Buttons and switches ────────────────────────────────────────────
    DeviceType(
        key="rocker_switch",
        name="Rocker switch, two rockers",
        category="button",
        eeps=("F6-02-01", "F6-02-02"),
        entities=(
            _e("rocker_event_a"),
            _e("rocker_event_b"),
            _e("rocker_state_a"),
            _e("rocker_state_b"),
        ),
        description=(
            "Battery-free wall switch with two rockers. Every rocker gives an "
            "event entity (press and release of the upper and lower button) and "
            "an on/off state: upper button on, lower button off."
        ),
        verified=True,
        icon="mdi:light-switch",
        keywords=("wall switch", "rocker"),
    ),
    DeviceType(
        key="push_button",
        name="Push button, single",
        category="button",
        eeps=("F6-01-01",),
        entities=(_e("push_event"),),
        description="Single push button. Sends an event when pressed and when released.",
        icon="mdi:gesture-tap-button",
    ),
    DeviceType(
        key="smart_button",
        name="Push button with battery level",
        category="button",
        eeps=("D2-03-0A",),
        entities=(_e("button_event"), _e("button_toggle"), _e("battery")),
        description=(
            "Battery powered button that tells short, double and long presses "
            "apart. Also gives a virtual on/off state that flips on every short "
            "press, and the battery level."
        ),
        pairing=PAIRING_UTE,
        verified=True,
        icon="mdi:gesture-double-tap",
        notes=_UTE_NOTES,
    ),
    # ── Doors and windows ───────────────────────────────────────────────
    DeviceType(
        key="contact",
        name="Door or window contact",
        category="contact",
        eeps=("D5-00-01",),
        entities=(_e("opening"),),
        description="Magnetic contact: open or closed.",
        icon="mdi:door-open",
    ),
    DeviceType(
        key="contact_voltage",
        name="Door or window contact with supply voltage",
        category="contact",
        eeps=("A5-14-01",),
        entities=(_e("opening"), _e("supply_voltage")),
        description="Magnetic contact that also reports its supply voltage.",
        icon="mdi:door-open",
    ),
    DeviceType(
        key="window_sensor",
        name="Window sensor: closed, tilted, open",
        category="window",
        eeps=("A5-14-09",),
        entities=(_e("window_position"), _e("opening_window"), _e("supply_voltage")),
        description=(
            "Window sensor that tells closed, tilted and open apart. Also gives "
            "an open/closed entity for alarms and automations."
        ),
        verified=True,
        icon="mdi:window-open-variant",
        notes=(
            "Some sensors ship with telegram encryption turned on. While it is "
            "on, the gateway receives the telegrams but cannot read them and the "
            "entities stay unknown: turn it off as the device manual explains."
        ),
    ),
    DeviceType(
        key="window_sensor_vibration",
        name="Window sensor with vibration alarm",
        category="window",
        eeps=("A5-14-0A",),
        entities=(
            _e("window_position"),
            _e("opening_window"),
            _e("vibration"),
            _e("supply_voltage"),
        ),
        description=(
            "Window sensor with closed, tilted and open positions plus a vibration alarm for break-in attempts."
        ),
        verified=True,
        icon="mdi:window-shutter-alert",
        notes=(
            "Some sensors ship with telegram encryption turned on: turn it off "
            "as the device manual explains, or the entities stay unknown. Many "
            "models report vibration only while the window is closed, so test "
            "it with the window shut. Some keep the alarm on until it is reset "
            "on the sensor itself."
        ),
    ),
    DeviceType(
        key="window_handle",
        name="Window handle: closed, tilted, open",
        category="window",
        eeps=("F6-10-00",),
        entities=(_e("handle_position"), _e("opening_window")),
        description="Battery-free window handle that reports its position.",
        icon="mdi:window-closed-variant",
    ),
    # ── Motion and climate ──────────────────────────────────────────────
    DeviceType(
        key="motion_sensor",
        name="Motion sensor",
        category="motion",
        eeps=("A5-07-01", "A5-07-02", "A5-07-03"),
        entities=(_e("motion"), _e("supply_voltage"), _e("illuminance", "A5-07-03")),
        description="Occupancy sensor. A5-07-03 also measures light.",
        icon="mdi:motion-sensor",
    ),
    DeviceType(
        key="multi_sensor",
        name="Light, temperature and motion sensor",
        category="motion",
        eeps=("A5-08-01", "A5-08-02", "A5-08-03"),
        entities=(_e("motion"), _e("illuminance"), _e("temperature"), _e("supply_voltage")),
        description="Room sensor with light level, temperature and occupancy.",
        icon="mdi:motion-sensor",
    ),
    DeviceType(
        key="temperature",
        name="Temperature sensor",
        category="climate",
        eeps=_TEMPERATURE_EEPS,
        entities=(_e("temperature"),),
        description=(
            "Temperature sensor. The A5-02 profiles differ only in their range: "
            "pick the one the device declares at teach-in."
        ),
        icon="mdi:thermometer",
    ),
    DeviceType(
        key="temperature_humidity",
        name="Temperature and humidity sensor",
        category="climate",
        eeps=("A5-04-01", "A5-04-02", "A5-04-03"),
        entities=(_e("temperature"), _e("humidity")),
        description="Temperature and relative humidity.",
        icon="mdi:thermometer-water",
    ),
    DeviceType(
        key="light_sensor",
        name="Light sensor",
        category="climate",
        eeps=("A5-06-01", "A5-06-02", "A5-06-03"),
        entities=(_e("illuminance"), _e("supply_voltage")),
        description="Light level in lux.",
        icon="mdi:brightness-5",
    ),
    DeviceType(
        key="co2_sensor",
        name="CO2 sensor",
        category="air",
        eeps=("A5-09-04", "A5-09-08", "A5-09-09"),
        entities=(
            _e("co2"),
            _e("humidity", "A5-09-04"),
            _e("temperature", "A5-09-04"),
        ),
        description="Carbon dioxide. A5-09-04 can also report humidity and temperature.",
        icon="mdi:molecule-co2",
    ),
    DeviceType(
        key="co_sensor",
        name="Carbon monoxide sensor",
        category="smoke",
        eeps=("A5-09-02",),
        entities=(_e("co"), _e("temperature"), _e("supply_voltage")),
        description="Carbon monoxide concentration, up to 1020 ppm.",
        icon="mdi:molecule-co",
    ),
    DeviceType(
        key="voc_sensor",
        name="VOC sensor",
        category="air",
        eeps=("A5-09-05",),
        entities=(_e("voc"),),
        description="Volatile organic compounds in ppb.",
        icon="mdi:air-filter",
    ),
    DeviceType(
        key="smoke_detector",
        name="Smoke detector",
        category="smoke",
        eeps=("F6-05-02",),
        entities=(_e("smoke"), _e("battery_low")),
        description="Smoke alarm and low battery warning.",
        icon="mdi:smoke-detector-variant",
    ),
    DeviceType(
        key="weather_station",
        name="Weather station",
        category="weather",
        eeps=("A5-13-01",),
        entities=(_e("illuminance"), _e("temperature"), _e("wind_speed"), _e("rain")),
        description="Dawn light level, outdoor temperature, wind speed and rain.",
        icon="mdi:weather-partly-rainy",
    ),
    # ── Meters ──────────────────────────────────────────────────────────
    DeviceType(
        key="electricity_meter",
        name="Electricity meter",
        category="meter",
        eeps=("A5-12-01",),
        entities=(_e("power"), _e("energy")),
        description="Current power and cumulative energy.",
        icon="mdi:meter-electric",
    ),
    DeviceType(
        key="gas_meter",
        name="Gas meter",
        category="meter",
        eeps=("A5-12-02",),
        entities=(_e("gas_flow"), _e("gas_total")),
        description="Current gas flow and cumulative volume.",
        icon="mdi:meter-gas",
    ),
    DeviceType(
        key="water_meter",
        name="Water meter",
        category="meter",
        eeps=("A5-12-03",),
        entities=(_e("water_flow"), _e("water_total")),
        description="Current water flow and cumulative volume.",
        icon="mdi:water-pump",
    ),
    # ── Actuators ───────────────────────────────────────────────────────
    DeviceType(
        key="switch_actuator",
        name="Switching actuator or smart plug",
        category="switch",
        eeps=_D2_01_EEPS,
        entities=(
            _e("switch_vld"),
            _e("power", on_demand=True),
            _e("energy", on_demand=True),
        ),
        description=(
            "Two-way switch: the actuator confirms every command, and the "
            "integration repeats a command that gets no answer. Power and energy "
            "entities appear by themselves once the device reports them."
        ),
        pairing=PAIRING_UTE,
        verified=True,
        icon="mdi:power-socket-eu",
        notes=_UTE_NOTES,
    ),
    DeviceType(
        key="rocker_actuator",
        name="Actuator controlled by rocker telegrams",
        category="switch",
        eeps=("F6-02-01",),
        entities=(_e("switch_rps"),),
        description=(
            "One-way relay or plug that obeys rocker switch telegrams. It does "
            "not confirm commands, so every command is sent twice."
        ),
        pairing=PAIRING_RPS,
        icon="mdi:electric-switch",
        notes=_OWN_SENDER_NOTES,
    ),
    DeviceType(
        key="central_switch",
        name="Switching actuator, central command",
        category="switch",
        eeps=("A5-38-08",),
        entities=(_e("switch_central"),),
        description="One-way switching actuator driven with A5-38-08 command 1.",
        pairing=PAIRING_4BS,
        icon="mdi:toggle-switch-outline",
        notes=_OWN_SENDER_NOTES,
    ),
    DeviceType(
        key="dimmer",
        name="Dimming actuator, central command",
        category="light",
        eeps=("A5-38-08",),
        entities=(_e("dimmer"),),
        description="Dimmer driven with A5-38-08 command 2.",
        pairing=PAIRING_4BS,
        icon="mdi:lightbulb-on-outline",
        notes=_OWN_SENDER_NOTES,
    ),
    DeviceType(
        key="cover_actuator",
        name="Roller shutter or blind actuator",
        category="cover",
        eeps=("D2-05-00",),
        entities=(_e("cover_vld"),),
        description=(
            "Two-way shutter actuator with position control. The actuator "
            "reports its position after a move; the integration also asks for "
            "it at start-up and after a stop."
        ),
        pairing=PAIRING_UTE,
        verified=True,
        icon="mdi:window-shutter",
        notes=_UTE_NOTES,
    ),
)

TYPES_BY_KEY: dict[str, DeviceType] = {device_type.key: device_type for device_type in DEVICE_TYPES}


def get_type(key: str | None) -> DeviceType | None:
    return TYPES_BY_KEY.get(key or "")


def suggest_type(eep: str | None) -> DeviceType | None:
    """The best device type for a profile seen at teach-in, if any.

    Sensors win over actuators that share a profile: a profile announced by a
    device in a teach-in telegram belongs to what the device *sends*.
    """
    if not eep:
        return None
    eep = eep.upper()
    for device_type in DEVICE_TYPES:
        if eep in device_type.eeps and device_type.pairing not in OWN_SENDER_PAIRING:
            return device_type
    for device_type in DEVICE_TYPES:
        if eep in device_type.eeps:
            return device_type
    return None
