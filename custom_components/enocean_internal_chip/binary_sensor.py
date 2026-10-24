"""Binary sensor entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .catalog import EntitySpec
from .entity import ChipEntity, setup_entities
from .esp3 import Esp3Packet
from .profiles import Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice

DESCRIPTIONS: dict[str, BinarySensorEntityDescription] = {
    "opening": BinarySensorEntityDescription(key="opening", device_class=BinarySensorDeviceClass.OPENING),
    "opening_window": BinarySensorEntityDescription(key="opening_window", device_class=BinarySensorDeviceClass.WINDOW),
    "motion": BinarySensorEntityDescription(key="motion", device_class=BinarySensorDeviceClass.MOTION),
    "vibration": BinarySensorEntityDescription(key="vibration", device_class=BinarySensorDeviceClass.VIBRATION),
    "smoke": BinarySensorEntityDescription(key="smoke", device_class=BinarySensorDeviceClass.SMOKE),
    "battery_low": BinarySensorEntityDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "rain": BinarySensorEntityDescription(
        key="rain", translation_key="rain", device_class=BinarySensorDeviceClass.MOISTURE
    ),
    "rocker_state_a": BinarySensorEntityDescription(key="rocker_state_a", translation_key="rocker_state_a"),
    "rocker_state_b": BinarySensorEntityDescription(key="rocker_state_b", translation_key="rocker_state_b"),
    "button_toggle": BinarySensorEntityDescription(key="button_toggle", translation_key="button_toggle"),
}

# Rocker actions: 0 = AI, 1 = A0, 2 = BI, 3 = B0. Button 0 turns the virtual
# state on, button I turns it off.
ROCKER_ACTIONS = {
    "rocker_state_a": {1: True, 0: False},
    "rocker_state_b": {3: True, 2: False},
}

# The reading that feeds each simple binary sensor.
READING_KEYS = {
    "opening": "opening",
    "opening_window": "opening",
    "motion": "motion",
    "vibration": "vibration",
    "smoke": "smoke",
    "battery_low": "battery_low",
    "rain": "rain",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> ChipBinarySensor | None:
        description = DESCRIPTIONS.get(spec.kind)
        if description is None:
            return None
        if spec.kind in ROCKER_ACTIONS:
            return RockerStateSensor(device, spec, description)
        if spec.kind == "button_toggle":
            return ButtonToggleSensor(device, spec, description)
        return ChipBinarySensor(device, spec, description)

    setup_entities(hass, entry, async_add_entities, Platform.BINARY_SENSOR, factory)


class ChipBinarySensor(ChipEntity, BinarySensorEntity, RestoreEntity):
    def __init__(self, device: ChipDevice, spec: EntitySpec, description: BinarySensorEntityDescription) -> None:
        super().__init__(device, spec)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last.state == STATE_ON

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        key = READING_KEYS.get(self.kind, self.kind)
        if key not in readings:
            return
        self._attr_is_on = bool(readings[key])
        self.async_write_ha_state()


class RockerStateSensor(ChipBinarySensor):
    """On/off state of one rocker, kept by the integration.

    A rocker switch has no state of its own: it only sends presses. This
    entity remembers the last button pressed, which is handy in automations
    and dashboards.
    """

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        rocker = readings.get("rocker")
        if not rocker or not rocker.get("pressed"):
            return
        actions = ROCKER_ACTIONS[self.kind]
        for action in (rocker.get("action"), rocker.get("second_action")):
            if action in actions:
                self._attr_is_on = actions[action]
                self.async_write_ha_state()


class ButtonToggleSensor(ChipBinarySensor):
    """A virtual on/off state that flips on every short press."""

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        if readings.get("button") != "short_press":
            return
        self._attr_is_on = not self._attr_is_on
        self.async_write_ha_state()
