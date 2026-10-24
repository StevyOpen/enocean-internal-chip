"""Cover entities: D2-05-00 roller shutters and blinds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .catalog import EntitySpec
from .entity import ChipActuatorEntity, setup_entities
from .esp3 import RORG_VLD, Esp3Packet
from .profiles import Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice

# D2-05-00 commands (low nibble of the last byte; the high nibble is the channel).
CMD_GO_TO = 0x01
CMD_STOP = 0x02
CMD_QUERY = 0x03

QUERY_AFTER_STOP = 2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> CoverEntity | None:
        return ShutterCover(device, spec) if spec.kind == "cover_vld" else None

    setup_entities(hass, entry, async_add_entities, Platform.COVER, factory)


class ShutterCover(ChipActuatorEntity, CoverEntity, RestoreEntity):
    """D2-05-00 actuator.

    The profile counts 0 % as fully open and 100 % as closed; Home Assistant
    counts the other way round. The conversion happens in one place: the
    decoder for readings, ``_go_to`` for commands.
    """

    _attr_name = None
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION | CoverEntityFeature.STOP
    )
    channel = 0

    def __init__(self, device: ChipDevice, spec: EntitySpec) -> None:
        super().__init__(device, spec)
        self._queried = False

    @property
    def is_closed(self) -> bool | None:
        if self._attr_current_cover_position is None:
            return None
        return self._attr_current_cover_position == 0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and isinstance(last.attributes.get(ATTR_CURRENT_POSITION), int):
            self._attr_current_cover_position = last.attributes[ATTR_CURRENT_POSITION]
        self._query()

    @callback
    def _gateway_changed(self) -> None:
        if not self.device.can_send:
            self._queried = False
        self._query()
        super()._gateway_changed()

    @callback
    def _query(self, *, force: bool = False) -> None:
        if (self._queried and not force) or not self.device.can_send:
            return
        self._queried = True
        try:
            self.device.send(RORG_VLD, [(self.channel << 4) | CMD_QUERY], addressed=True)
        except HomeAssistantError:
            self._queried = False

    @callback
    def _go_to(self, ha_position: int) -> None:
        profile_position = 100 - max(0, min(100, ha_position))
        self.device.send(
            RORG_VLD,
            [profile_position & 0x7F, 0x00, 0x00, (self.channel << 4) | CMD_GO_TO],
            addressed=True,
        )
        # Shown at once; the reply from the actuator corrects it if needed.
        self._attr_current_cover_position = ha_position
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        self._go_to(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        self._go_to(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        self._go_to(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        self.device.send(RORG_VLD, [(self.channel << 4) | CMD_STOP], addressed=True)
        self.async_on_remove(async_call_later(self.hass, QUERY_AFTER_STOP, self._query_after_stop))

    @callback
    def _query_after_stop(self, _now: Any) -> None:
        self._query(force=True)

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        cover = readings.get("cover")
        if not cover or cover.get("position") is None:
            return
        self._attr_current_cover_position = cover["position"]
        self.async_write_ha_state()
