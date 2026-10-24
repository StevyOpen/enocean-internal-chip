"""Light entities: A5-38-08 dimmers.

The telegrams follow the dimmer support of the official Home Assistant
EnOcean integration: command 2, dimming value 0..100, ramp time 1 second.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .catalog import EntitySpec
from .entity import ChipActuatorEntity, setup_entities
from .esp3 import RORG_4BS, Esp3Packet
from .profiles import Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> LightEntity | None:
        return Dimmer(device, spec) if spec.kind == "dimmer" else None

    setup_entities(hass, entry, async_add_entities, Platform.LIGHT, factory)


class Dimmer(ChipActuatorEntity, LightEntity, RestoreEntity):
    _attr_name = None
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, device: ChipDevice, spec: EntitySpec) -> None:
        super().__init__(device, spec)
        self._attr_brightness = 255
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
            if isinstance(last.attributes.get(ATTR_BRIGHTNESS), int):
                self._attr_brightness = last.attributes[ATTR_BRIGHTNESS]

    async def async_turn_on(self, **kwargs: Any) -> None:
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = brightness
        level = max(1, math.floor((self._attr_brightness or 255) / 256 * 100))
        self.device.send(RORG_4BS, [0x02, level, 0x01, 0x09])
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.device.send(RORG_4BS, [0x02, 0x00, 0x01, 0x09])
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        if "brightness" not in readings:
            return
        level = readings["brightness"]
        self._attr_is_on = level > 0
        if level > 0:
            self._attr_brightness = min(255, math.floor(level / 100 * 256))
        self.async_write_ha_state()
