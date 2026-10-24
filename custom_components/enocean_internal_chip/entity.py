"""Base classes and the shared platform setup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .catalog import KIND_PLATFORM, EntitySpec
from .const import DOMAIN, SIGNAL_GATEWAY_STATUS, SIGNAL_NEW_ENTITIES
from .esp3 import Esp3Packet
from .profiles import Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice


class ChipEntity(Entity):
    """An entity that belongs to one adopted device and one entity kind."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: ChipDevice, spec: EntitySpec) -> None:
        self.device = device
        self.kind = spec.kind
        self._attr_unique_id = f"{device.unique_prefix}-{spec.kind}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device.id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.device.add_listener(self._handle_readings))

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        """Update from a decoded telegram. Override in subclasses."""


class ChipActuatorEntity(ChipEntity):
    """An entity that sends commands: available only while the gateway is."""

    @property
    def available(self) -> bool:
        return self.device.can_send

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_GATEWAY_STATUS.format(self.device.hub.entry.entry_id),
                self._gateway_changed,
            )
        )

    @callback
    def _gateway_changed(self) -> None:
        self.async_write_ha_state()


def setup_entities(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: Platform,
    factory: Callable[[ChipDevice, EntitySpec], Entity | None],
) -> None:
    """Create this platform's entities now and whenever devices are added."""

    @callback
    def build(device: ChipDevice, specs: list[EntitySpec]) -> None:
        entities = [
            entity
            for spec in specs
            if KIND_PLATFORM.get(spec.kind) == platform and (entity := factory(device, spec)) is not None
        ]
        if entities:
            async_add_entities(entities)

    hub = entry.runtime_data
    for device in hub.devices.values():
        build(device, device.entity_specs())
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_ENTITIES.format(entry.entry_id), build))
