"""The runtime heart of the integration.

The hub owns the gateway, the stored devices and the radio monitor. Every
telegram goes through ``handle_radio``: it is logged, decoded once with the
profile of the device that sent it, and handed to that device's entities.
Pairing requests from two-way devices are answered here, but only while the
pairing window is open or when the device is already adopted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from . import profiles
from .catalog import (
    KIND_PLATFORM,
    OWN_SENDER_PAIRING,
    PAIRING_4BS,
    PAIRING_RPS,
    PAIRING_UTE,
    DeviceType,
    EntitySpec,
    get_type,
    suggest_type,
)
from .const import (
    CONF_ALLOW_SIMULATION,
    CONF_NOTIFY_TEACH_IN,
    DEFAULT_OPTIONS,
    DOMAIN,
    PANEL_URL_PATH,
    SIGNAL_GATEWAY_STATUS,
    SIGNAL_NEW_ENTITIES,
)
from .esp3 import (
    BROADCAST_ID,
    RORG_4BS,
    RORG_RPS,
    RORG_UTE,
    Esp3Packet,
    format_id,
    parse_id,
    radio_packet,
)
from .gateway import Gateway
from .ledger import AddressLedger
from .monitor import REPEATED_REQUESTS, RadioMonitor
from .store import ChipStore, DeviceRecord

_LOGGER = logging.getLogger(__name__)

PAIRING_WINDOW = 180

#: Manufacturer ID put in 4BS teach-in telegrams unless the user picks
#: another: 0x7FF is the ID the EnOcean Alliance reserves for multi-user use.
DEFAULT_MANUFACTURER_ID = 0x7FF

# UTE response, DB6: bidirectional, result, command 1 (response).
UTE_TEACH_IN_ACCEPTED = 0x91
UTE_DELETION_ACCEPTED = 0xA1

# RPS rocker telegrams used to drive one-way actuators.
RPS_ON = 0x50  # button BI pressed
RPS_OFF = 0x70  # button B0 pressed
RPS_RELEASE = 0x00
RPS_STATUS_PRESSED = 0x30
RPS_STATUS_RELEASED = 0x20


type ReadingsListener = Callable[[profiles.Readings, Esp3Packet], None]


class ChipDevice:
    """An adopted device: its identity, profile and live listeners."""

    def __init__(self, hub: ChipHub, device_id: str, record: DeviceRecord) -> None:
        self.hub = hub
        self.id = device_id
        self.address = parse_id(device_id) or b"\x00\x00\x00\x00"
        self.record = record
        self._listeners: list[ReadingsListener] = []
        self.last_seen: datetime | None = None
        self.dbm: int | None = None

    @property
    def name(self) -> str:
        return self.record.get("name") or self.id

    @property
    def type(self) -> DeviceType | None:
        return get_type(self.record.get("type"))

    @property
    def eep(self) -> str:
        device_type = self.type
        eep = (self.record.get("eep") or "").upper()
        if device_type and eep not in device_type.eeps:
            return device_type.default_eep
        return eep

    @property
    def unique_prefix(self) -> str:
        return self.id.replace(":", "")

    @property
    def device_info(self) -> DeviceInfo:
        device_type = self.type
        return DeviceInfo(
            identifiers={(DOMAIN, self.id)},
            name=self.name,
            manufacturer="EnOcean",
            model=device_type.name if device_type else "Unknown type",
            model_id=self.eep,
            serial_number=self.id,
            via_device=(DOMAIN, self.hub.gateway_identifier),
        )

    def entity_specs(self) -> list[EntitySpec]:
        device_type = self.type
        if device_type is None:
            return []
        return device_type.entities_for(self.eep, set(self.record.get("seen") or []))

    @property
    def sender(self) -> bytes:
        """The address this gateway uses when it talks to the device."""
        offset = self.record.get("sender_offset")
        return self.hub.gateway.sender_for(offset or 0)

    @property
    def can_send(self) -> bool:
        return self.hub.gateway.connected and self.hub.gateway.base_id is not None

    @callback
    def add_listener(self, listener: ReadingsListener) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @callback
    def handle(self, packet: Esp3Packet, readings: profiles.Readings) -> None:
        self.last_seen = dt_util.utcnow()
        self.dbm = packet.dbm
        for listener in list(self._listeners):
            listener(readings, packet)

    @callback
    def send(self, rorg: int, payload: bytes | list[int], status: int = 0x00, *, addressed: bool = False) -> None:
        """Send a telegram to this device from its sender ID.

        ``addressed`` puts the device ID in the destination field, which two-way
        devices need. One-way actuators listen to broadcasts from their sender.
        """
        if not self.can_send:
            raise HomeAssistantError("The EnOcean gateway is not ready")
        destination = self.address if addressed else BROADCAST_ID
        try:
            self.hub.gateway.send(radio_packet(rorg, payload, self.sender, status, destination))
        except ConnectionError as err:
            raise HomeAssistantError(f"The EnOcean gateway is not ready: {err}") from err

    def as_dict(self) -> dict[str, Any]:
        device_type = self.type
        return {
            "id": self.id,
            "name": self.name,
            "type": self.record.get("type"),
            "type_name": device_type.name if device_type else None,
            "eep": self.eep,
            "pairing": device_type.pairing if device_type else None,
            "verified": device_type.verified if device_type else False,
            "sender": format_id(self.hub.gateway.sender_for(self.record["sender_offset"]))
            if self.record.get("sender_offset") and self.hub.gateway.base_id
            else None,
            "manufacturer_id": self.record.get("manufacturer_id"),
            "added_at": self.record.get("added_at"),
            "last_seen": self.last_seen.isoformat(timespec="seconds") if self.last_seen else None,
            "dbm": self.dbm,
        }


@dataclass(slots=True)
class PairingWindow:
    until: float
    device_id: str | None = None

    @property
    def remaining(self) -> int:
        return max(0, round(self.until - time.monotonic()))


class ChipHub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, port: str) -> None:
        self.hass = hass
        self.entry = entry
        self.store = ChipStore(hass)
        self.ledger = AddressLedger(hass)
        self.monitor = RadioMonitor()
        self.gateway = Gateway(hass, port, self.handle_radio)
        self.devices: dict[str, ChipDevice] = {}
        self.pairing: PairingWindow | None = None
        #: Held while the gateway address is being changed: one write at a time.
        self.address_lock = asyncio.Lock()
        self._notified: set[str] = set()
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def gateway_identifier(self) -> str:
        return f"gateway_{self.entry.entry_id}"

    def option(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULT_OPTIONS.get(key))

    # ── lifecycle ──────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        await self.store.async_load()
        await self.ledger.async_load()
        for device_id, record in self.store.devices.items():
            self.devices[device_id] = ChipDevice(self, device_id, record)
            self.monitor.seed(device_id, record.get("eep"))
        self._unsubs.append(self.gateway.add_status_listener(self._gateway_changed))
        await self.gateway.async_start()
        self._register_gateway_device()

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.gateway.async_stop()
        await self.store.async_flush()

    @callback
    def _register_gateway_device(self) -> None:
        version = self.gateway.version
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.gateway_identifier)},
            name="EnOcean gateway",
            manufacturer="EnOcean",
            model=version.description if version and version.description else "ESP3 module",
            sw_version=version.app_version if version else None,
            serial_number=version.chip_id if version else None,
        )

    @callback
    def _gateway_changed(self) -> None:
        base = format_id(self.gateway.base_id) if self.gateway.base_id else None
        if base:
            self.hass.async_create_task(self.ledger.async_record_read(base))
        async_dispatcher_send(self.hass, SIGNAL_GATEWAY_STATUS.format(self.entry.entry_id))

    # ── radio in ───────────────────────────────────────────────────────

    @callback
    def handle_radio(self, packet: Esp3Packet) -> None:
        if not packet.is_radio:
            return
        sender = packet.sender_id
        device = self.devices.get(sender)
        readings: profiles.Readings = {}
        teach_in: str | None = None
        declared_eep: str | None = None

        if packet.rorg == RORG_UTE:
            request = profiles.parse_ute_request(packet.data)
            if request is not None:
                teach_in = "UTE"
                declared_eep = request["eep"]
                self._handle_ute(packet, request, adopted=device is not None)
        elif packet.rorg == RORG_4BS:
            declared = profiles.parse_4bs_teach_in(packet.data)
            if declared is not None:
                teach_in = "4BS"
                declared_eep = declared[0]

        if device is not None and teach_in is None:
            readings = profiles.decode(device.eep, packet.data)

        suggested = suggest_type(declared_eep)
        if teach_in:
            summary = f"teach-in, profile {declared_eep}" if declared_eep else "teach-in"
        else:
            summary = profiles.summarize(readings)
        self.monitor.record(
            packet,
            summary=summary,
            adopted=device is not None,
            eep=declared_eep,
            suggested_type=suggested.key if suggested else None,
            teach_in=teach_in,
        )

        if device is not None:
            self._note_on_demand(device, readings)
            device.handle(packet, readings)
        elif teach_in and sender not in self.store.muted:
            self._notify_new_device(sender, declared_eep, suggested)

    @callback
    def _note_on_demand(self, device: ChipDevice, readings: profiles.Readings) -> None:
        """Create on-demand entities the first time their reading shows up."""
        device_type = device.type
        if device_type is None:
            return
        new_specs = [
            spec
            for spec in device_type.entities
            if spec.on_demand
            and spec.applies_to(device.eep)
            and spec.kind in readings
            and self.store.mark_seen(device.id, spec.kind)
        ]
        if new_specs:
            async_dispatcher_send(self.hass, SIGNAL_NEW_ENTITIES.format(self.entry.entry_id), device, new_specs)

    @callback
    def _notify_new_device(self, sender: str, eep: str | None, suggested: DeviceType | None) -> None:
        if not self.option(CONF_NOTIFY_TEACH_IN) or sender in self._notified:
            return
        self._notified.add(sender)
        lines = [f"A device with ID **{sender}** sent a teach-in telegram."]
        if eep:
            lines.append(f"Profile: **{eep}**.")
        if suggested:
            lines.append(f"Suggested type: {suggested.name}.")
        lines.append(f"Open the [EnOcean panel](/{PANEL_URL_PATH}) to add it.")
        persistent_notification.async_create(
            self.hass,
            "\n\n".join(lines),
            title="New EnOcean device",
            notification_id=f"{DOMAIN}_teach_in_{sender.replace(':', '')}",
        )

    # ── pairing ────────────────────────────────────────────────────────

    def pairing_open_for(self, sender: str, adopted: bool) -> bool:
        if adopted:
            return True
        window = self.pairing
        if window is None or window.remaining <= 0:
            return False
        return window.device_id in (None, sender)

    @callback
    def open_pairing(self, seconds: int = PAIRING_WINDOW, device_id: str | None = None) -> PairingWindow:
        self.pairing = PairingWindow(time.monotonic() + seconds, device_id)
        return self.pairing

    @callback
    def close_pairing(self) -> None:
        self.pairing = None

    @callback
    def _handle_ute(self, packet: Esp3Packet, request: dict[str, Any], adopted: bool) -> None:
        sender = packet.sender_id
        count = self.monitor.note_pairing_request(sender)
        if count == REPEATED_REQUESTS:
            _LOGGER.warning(
                "%s keeps asking to pair (%d times) although the gateway answers "
                "every time. The missing step is usually on the device: many "
                "actuators want a short press on their own button after entering "
                "learn mode",
                sender,
                count,
            )
        if not request["response_expected"]:
            return
        if not self.pairing_open_for(sender, adopted):
            _LOGGER.debug("Pairing request from %s ignored: pairing window closed", sender)
            return
        if self.gateway.base_id is None:
            _LOGGER.warning("Pairing request from %s, but the gateway address is not known yet", sender)
            return
        code = UTE_DELETION_ACCEPTED if request["request"] == 1 else UTE_TEACH_IN_ACCEPTED
        payload = bytes((code,)) + bytes(packet.data[2:8])
        response = radio_packet(RORG_UTE, payload, self.gateway.base_id, 0x8F, packet.sender)
        try:
            self.gateway.send(response)
        except ConnectionError as err:
            _LOGGER.warning("Could not answer the pairing request from %s: %s", sender, err)
            return
        _LOGGER.info("Answered the pairing request from %s (profile %s)", sender, request["eep"])

    async def async_pair(self, device_id: str) -> dict[str, Any]:
        """Do what pairing means for this device type, and say what happened."""
        device = self._get(device_id)
        device_type = device.type
        if device_type is None:
            raise HomeAssistantError("Unknown device type")

        if device_type.pairing == PAIRING_UTE:
            window = self.open_pairing(device_id=device.id)
            if self.monitor.keeps_asking(device.id):
                return {
                    "result": "keeps_asking",
                    "message": (
                        f"{device.name} asked to pair {self.monitor.pairing_requests(device.id)} "
                        "times in the last minutes and the gateway answered every time. "
                        "The missing step is on the device: after learn mode, give it the "
                        "confirmation press on its own button. Reset it only if it keeps "
                        "asking after that."
                    ),
                    "seconds": window.remaining,
                }
            return {
                "result": "window_open",
                "message": (
                    f"Pairing is open for {window.remaining} seconds. Put {device.name} "
                    "into learn mode now: the gateway answers as soon as it asks."
                ),
                "seconds": window.remaining,
            }

        if not device.can_send:
            raise HomeAssistantError("The EnOcean gateway is not ready")

        if device_type.pairing == PAIRING_RPS:
            for _ in range(2):
                device.send(RORG_RPS, [RPS_ON], RPS_STATUS_PRESSED)
                await asyncio.sleep(0.2)
                device.send(RORG_RPS, [RPS_RELEASE], RPS_STATUS_RELEASED)
                await asyncio.sleep(0.5)
            return {
                "result": "sent",
                "message": (
                    f"Teach-in sent from {format_id(device.sender)}. If {device.name} was "
                    "in learn mode it should confirm now, usually with its LED."
                ),
            }

        if device_type.pairing == PAIRING_4BS:
            func, ptype = int(device.eep[3:5], 16), int(device.eep[6:8], 16)
            manufacturer = device.record.get("manufacturer_id") or DEFAULT_MANUFACTURER_ID
            payload = [
                (func << 2) | (ptype >> 5),
                ((ptype << 3) & 0xF8) | ((manufacturer >> 8) & 0x07),
                manufacturer & 0xFF,
                0x80,  # teach-in with profile and manufacturer
            ]
            for _ in range(2):
                device.send(RORG_4BS, payload)
                await asyncio.sleep(0.3)
            return {
                "result": "sent",
                "message": (
                    f"Teach-in for {device.eep} sent from {format_id(device.sender)} "
                    f"(manufacturer ID 0x{manufacturer:03X})."
                ),
            }

        return {"result": "nothing_to_do", "message": f"{device.name} only transmits: there is nothing to pair."}

    # ── adopted devices ────────────────────────────────────────────────

    def _get(self, device_id: str) -> ChipDevice:
        raw = parse_id(device_id)
        device = self.devices.get(format_id(raw)) if raw else None
        if device is None:
            raise HomeAssistantError(f"Device {device_id} is not adopted")
        return device

    async def async_adopt(
        self,
        device_id: str,
        *,
        name: str,
        type_key: str,
        eep: str | None = None,
        area_id: str | None = None,
        manufacturer_id: int | None = None,
    ) -> ChipDevice:
        raw = parse_id(device_id)
        if raw is None:
            raise HomeAssistantError("The device ID must be 4 hexadecimal bytes, like 01:A2:B3:C4")
        device_id = format_id(raw)
        if device_id in self.devices:
            raise HomeAssistantError(f"{device_id} is already adopted")
        device_type = get_type(type_key)
        if device_type is None:
            raise HomeAssistantError(f"Unknown device type: {type_key}")
        eep = (eep or device_type.default_eep).upper()
        if eep not in device_type.eeps:
            raise HomeAssistantError(f"{device_type.name} does not support profile {eep}")

        record: DeviceRecord = {"name": name.strip() or device_id, "type": type_key, "eep": eep}
        if device_type.pairing in OWN_SENDER_PAIRING:
            offset = self.store.free_sender_offset()
            if offset is None:
                raise HomeAssistantError("All 127 sender IDs of this gateway are in use")
            record["sender_offset"] = offset
        if device_type.pairing == PAIRING_4BS:
            record["manufacturer_id"] = manufacturer_id or DEFAULT_MANUFACTURER_ID

        self.store.add(device_id, record)
        device = self.devices[device_id] = ChipDevice(self, device_id, record)
        self.monitor.seed(device_id, eep)

        registry = dr.async_get(self.hass)
        entry = registry.async_get_or_create(config_entry_id=self.entry.entry_id, **device.device_info)
        if area_id:
            registry.async_update_device(entry.id, area_id=area_id)

        async_dispatcher_send(self.hass, SIGNAL_NEW_ENTITIES.format(self.entry.entry_id), device, device.entity_specs())
        persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_teach_in_{device_id.replace(':', '')}")
        return device

    async def async_update(
        self,
        device_id: str,
        *,
        name: str | None = None,
        type_key: str | None = None,
        eep: str | None = None,
        manufacturer_id: int | None = None,
    ) -> ChipDevice:
        """Rename, or change type and profile.

        A new type or profile changes the set of entities: the ones that no
        longer apply are removed and the entry is reloaded to create the new
        ones. Entities that stay keep their names and settings.
        """
        device = self._get(device_id)
        registry = dr.async_get(self.hass)
        ha_device = registry.async_get_device(identifiers={(DOMAIN, device.id)})

        if name is not None and name.strip() and name.strip() != device.name:
            self.store.update(device.id, name=name.strip())
            if ha_device is not None:
                changes: dict[str, Any] = {"name": name.strip()}
                if ha_device.name_by_user:
                    # The latest rename wins, wherever it was made.
                    changes["name_by_user"] = None
                registry.async_update_device(ha_device.id, **changes)

        if manufacturer_id is not None:
            self.store.update(device.id, manufacturer_id=manufacturer_id)

        new_type = get_type(type_key) if type_key else device.type
        if new_type is None:
            raise HomeAssistantError(f"Unknown device type: {type_key}")
        new_eep = (eep or (device.eep if new_type is device.type else new_type.default_eep)).upper()
        if new_eep not in new_type.eeps:
            raise HomeAssistantError(f"{new_type.name} does not support profile {new_eep}")

        if new_type.key != device.record.get("type") or new_eep != device.eep:
            old_kinds = {spec.kind for spec in device.entity_specs()}
            changes = {"type": new_type.key, "eep": new_eep}
            if new_type.pairing in OWN_SENDER_PAIRING and not device.record.get("sender_offset"):
                offset = self.store.free_sender_offset()
                if offset is None:
                    raise HomeAssistantError("All 127 sender IDs of this gateway are in use")
                changes["sender_offset"] = offset
            self.store.update(device.id, **changes)
            new_kinds = {spec.kind for spec in device.entity_specs()}
            self._remove_entities(device, old_kinds - new_kinds)
            if ha_device is not None:
                registry.async_update_device(ha_device.id, model=new_type.name, model_id=new_eep)
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
        return device

    @callback
    def _remove_entities(self, device: ChipDevice, kinds: set[str]) -> None:
        if not kinds:
            return
        registry = er.async_get(self.hass)
        for kind in kinds:
            platform = KIND_PLATFORM[kind]
            entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{device.unique_prefix}-{kind}")
            if entity_id:
                registry.async_remove(entity_id)

    async def async_remove(self, device_id: str, *, from_registry: bool = True) -> bool:
        device = self._get(device_id)
        self.store.remove(device.id)
        self.devices.pop(device.id, None)
        if from_registry:
            registry = dr.async_get(self.hass)
            ha_device = registry.async_get_device(identifiers={(DOMAIN, device.id)})
            if ha_device is not None:
                registry.async_remove_device(ha_device.id)
        return True

    def entity_count(self, device_id: str) -> int:
        registry = dr.async_get(self.hass)
        ha_device = registry.async_get_device(identifiers={(DOMAIN, device_id)})
        if ha_device is None:
            return 0
        return len(er.async_entries_for_device(er.async_get(self.hass), ha_device.id, True))

    # ── simulation ─────────────────────────────────────────────────────

    @callback
    def inject(self, packet: Esp3Packet) -> None:
        """Feed a telegram as if it came from the radio. Needs the option on."""
        if not self.option(CONF_ALLOW_SIMULATION):
            raise HomeAssistantError("Simulated telegrams are turned off in the integration options")
        packet.simulated = True
        self.handle_radio(packet)


def entity_kind_from_unique_id(unique_id: str) -> str | None:
    """``01A2B3C4-temperature`` -> ``temperature``."""
    _, _, kind = unique_id.partition("-")
    return kind or None
