"""Event entities for buttons and rocker switches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventDeviceClass, EventEntity, EventEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .catalog import EntitySpec
from .entity import ChipEntity, setup_entities
from .esp3 import Esp3Packet
from .profiles import BUTTON_ACTIONS, Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice

ROCKER_EVENTS = ["press_0", "release_0", "press_i", "release_i"]

DESCRIPTIONS: dict[str, EventEntityDescription] = {
    "rocker_event_a": EventEntityDescription(
        key="rocker_event_a",
        translation_key="rocker_event_a",
        device_class=EventDeviceClass.BUTTON,
        event_types=ROCKER_EVENTS,
    ),
    "rocker_event_b": EventEntityDescription(
        key="rocker_event_b",
        translation_key="rocker_event_b",
        device_class=EventDeviceClass.BUTTON,
        event_types=ROCKER_EVENTS,
    ),
    "push_event": EventEntityDescription(
        key="push_event",
        translation_key="push_event",
        device_class=EventDeviceClass.BUTTON,
        event_types=["press", "release"],
    ),
    "button_event": EventEntityDescription(
        key="button_event",
        translation_key="button_event",
        device_class=EventDeviceClass.BUTTON,
        event_types=list(BUTTON_ACTIONS.values()),
    ),
}

# Rocker actions 0 = AI, 1 = A0, 2 = BI, 3 = B0, per channel: button "0", button "I".
ROCKER_BUTTONS = {
    "rocker_event_a": {1: "0", 0: "i"},
    "rocker_event_b": {3: "0", 2: "i"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> EventEntity | None:
        description = DESCRIPTIONS.get(spec.kind)
        if description is None:
            return None
        if spec.kind in ROCKER_BUTTONS:
            return RockerEvent(device, spec, description)
        if spec.kind == "push_event":
            return PushEvent(device, spec, description)
        return ButtonEvent(device, spec, description)

    setup_entities(hass, entry, async_add_entities, Platform.EVENT, factory)


class _ChipEvent(ChipEntity, EventEntity):
    def __init__(self, device: ChipDevice, spec: EntitySpec, description: EventEntityDescription) -> None:
        super().__init__(device, spec)
        self.entity_description = description

    @callback
    def _fire(self, event_type: str, packet: Esp3Packet, **attributes: object) -> None:
        if packet.simulated:
            attributes["simulated"] = True
        self._trigger_event(event_type, attributes or None)
        self.async_write_ha_state()


class RockerEvent(_ChipEvent):
    """Press and release of the two buttons of one rocker.

    The release telegram does not say which button was let go, so the event
    entity remembers the last press of its own rocker.
    """

    _last_button: str | None = None

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        rocker = readings.get("rocker")
        if not rocker:
            return
        buttons = ROCKER_BUTTONS[self.kind]
        if rocker.get("pressed") and rocker.get("action") is not None:
            button = buttons.get(rocker["action"])
            if button is None:
                button = buttons.get(rocker.get("second_action"))
            if button is None:
                # Another rocker was pressed: forget ours, its release is not coming.
                self._last_button = None
                return
            self._last_button = button
            self._fire(f"press_{button}", packet)
        elif self._last_button is not None:
            button, self._last_button = self._last_button, None
            self._fire(f"release_{button}", packet)


class PushEvent(_ChipEvent):
    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        if "push" in readings:
            self._fire("press" if readings["push"] else "release", packet)


class ButtonEvent(_ChipEvent):
    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        action = readings.get("button")
        if action:
            battery = readings.get("battery")
            if battery is not None:
                self._fire(action, packet, battery_level=battery)
            else:
                self._fire(action, packet)
