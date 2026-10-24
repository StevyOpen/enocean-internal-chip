"""Switch entities.

Radio telegrams can get lost, so a command is not trusted on its own:

* two-way actuators (D2-01) confirm every command with a status telegram.
  If no confirmation arrives within 1.5 seconds the command is sent again,
  up to two more times. After that the entity keeps the requested state but
  reports ``command_state: no_feedback`` and fires an event, so the problem is
  visible instead of silent;
* one-way actuators cannot confirm anything. Their commands are sent twice,
  which makes a lost telegram much less likely, and the state is "assumed".
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .catalog import EntitySpec
from .const import EVENT_NO_FEEDBACK
from .entity import ChipActuatorEntity, setup_entities
from .esp3 import RORG_4BS, RORG_RPS, RORG_VLD, Esp3Packet
from .hub import RPS_OFF, RPS_ON, RPS_RELEASE, RPS_STATUS_PRESSED, RPS_STATUS_RELEASED
from .profiles import Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice

VERIFY_TIMEOUT = 1.5
MAX_RETRIES = 2
DUPLICATE_DELAY = 0.25

# Status byte used for two-way commands, as tested with real actuators.
VLD_STATUS = 0x8F


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> SwitchEntity | None:
        if spec.kind == "switch_vld":
            return TwoWaySwitch(device, spec)
        if spec.kind == "switch_rps":
            return RockerTelegramSwitch(device, spec)
        if spec.kind == "switch_central":
            return CentralCommandSwitch(device, spec)
        return None

    setup_entities(hass, entry, async_add_entities, Platform.SWITCH, factory)


class _ChipSwitch(ChipActuatorEntity, SwitchEntity, RestoreEntity):
    _attr_name = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON


class TwoWaySwitch(_ChipSwitch):
    """D2-01 actuator with confirmation and retries."""

    channel = 0

    def __init__(self, device: ChipDevice, spec: EntitySpec) -> None:
        super().__init__(device, spec)
        self._sequence = 0
        self._sent_at = 0.0
        self._feedback_at = 0.0
        self._command_state = "idle"
        self._attempts = 0
        self._last_command: str | None = None
        self._last_command_at: str | None = None
        self._last_feedback_at: str | None = None
        self._queried = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "command_state": self._command_state,
            "command_attempts": self._attempts,
            "last_command": self._last_command,
            "last_command_at": self._last_command_at,
            "last_feedback_at": self._last_feedback_at,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._query_status()

    @callback
    def _gateway_changed(self) -> None:
        if not self.device.can_send:
            self._queried = False
        self._query_status()
        super()._gateway_changed()

    @callback
    def _query_status(self) -> None:
        """Ask the actuator for its state, once the gateway can talk."""
        if self._queried or not self.device.can_send:
            return
        self._queried = True
        try:
            self.device.send(RORG_VLD, [0x03, self.channel], VLD_STATUS, addressed=True)
        except HomeAssistantError:
            self._queried = False

    @callback
    def _send_output(self, on: bool) -> None:
        self.device.send(RORG_VLD, [0x01, self.channel & 0x1F, 0x64 if on else 0x00], VLD_STATUS, addressed=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._command(False)

    @callback
    def _command(self, on: bool) -> None:
        self._sequence += 1
        self._send_output(on)
        self._attr_is_on = on
        self._command_state = "sending"
        self._attempts = 1
        self._last_command = "on" if on else "off"
        self._last_command_at = dt_util.utcnow().isoformat(timespec="seconds")
        self._sent_at = self.hass.loop.time()
        self.async_write_ha_state()
        self.hass.async_create_task(self._verify(self._sequence, on, MAX_RETRIES))

    async def _verify(self, sequence: int, on: bool, retries_left: int) -> None:
        await asyncio.sleep(VERIFY_TIMEOUT)
        if sequence != self._sequence:
            return  # a newer command took over
        if self._feedback_at >= self._sent_at - 0.05:
            self._command_state = "confirmed"
            self.async_write_ha_state()
            return
        if retries_left > 0 and self.device.can_send:
            self._attempts += 1
            try:
                self._send_output(on)
            except HomeAssistantError:
                retries_left = 1  # gateway gone: stop retrying after this round
            self._sent_at = self.hass.loop.time()
            self.async_write_ha_state()
            await self._verify(sequence, on, retries_left - 1)
            return
        self._command_state = "no_feedback"
        self.async_write_ha_state()
        self.hass.bus.async_fire(
            EVENT_NO_FEEDBACK,
            {
                "entity_id": self.entity_id,
                "device_id": self.device.id,
                "command": self._last_command,
                "attempts": self._attempts,
            },
        )

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        status = readings.get("switch")
        if not status or status.get("channel") != self.channel:
            return
        self._attr_is_on = status["on"]
        self._feedback_at = self.hass.loop.time()
        self._last_feedback_at = dt_util.utcnow().isoformat(timespec="seconds")
        if self._command_state == "sending":
            self._command_state = "confirmed"
        self.async_write_ha_state()


class RockerTelegramSwitch(_ChipSwitch):
    """One-way actuator driven with rocker telegrams from its own sender ID."""

    _attr_assumed_state = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._press(RPS_ON)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._press(RPS_OFF)
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _press(self, button: int) -> None:
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(DUPLICATE_DELAY)
            self.device.send(RORG_RPS, [button], RPS_STATUS_PRESSED)
            await asyncio.sleep(0.15)
            self.device.send(RORG_RPS, [RPS_RELEASE], RPS_STATUS_RELEASED)


class CentralCommandSwitch(_ChipSwitch):
    """One-way actuator driven with A5-38-08 command 1 (switching)."""

    _attr_assumed_state = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._switch(False)

    async def _switch(self, on: bool) -> None:
        # DB3 command 1, no time, DB0: data telegram (LRN bit) + switching bit.
        payload = [0x01, 0x00, 0x00, 0x09 if on else 0x08]
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(DUPLICATE_DELAY)
            self.device.send(RORG_4BS, payload)
        self._attr_is_on = on
        self.async_write_ha_state()
