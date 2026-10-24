"""Persistent data: adopted devices, muted transmitters, type usage counts.

Everything lives in one Home Assistant ``Store`` file, so it is part of the
regular backups. Areas are not stored here: they belong to the Home Assistant
device registry, which is the single source of truth for them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.devices"
SAVE_DELAY = 2


class DeviceRecord(TypedDict, total=False):
    name: str
    type: str
    eep: str
    #: Offset from the Base ID used as sender for one-way actuators.
    sender_offset: int | None
    #: Manufacturer ID sent in 4BS teach-in telegrams.
    manufacturer_id: int | None
    #: Readings seen at least once, for entities created on demand.
    seen: list[str]
    added_at: str


class ChipStore:
    """Wrapper around the storage file with small, explicit operations."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.devices: dict[str, DeviceRecord] = {}
        self.muted: set[str] = set()
        #: How many times each device type has been chosen, ever. It only goes
        #: up: removing a device does not undo the experience of having used it.
        self.type_usage: dict[str, int] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.devices = {key.upper(): value for key, value in (data.get("devices") or {}).items()}
        self.muted = {item.upper() for item in data.get("muted") or []}
        self.type_usage = {
            key: value
            for key, value in (data.get("type_usage") or {}).items()
            if isinstance(key, str) and isinstance(value, int) and value > 0
        }

    def _data(self) -> dict[str, Any]:
        return {
            "devices": self.devices,
            "muted": sorted(self.muted),
            "type_usage": self.type_usage,
        }

    def save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # ── devices ────────────────────────────────────────────────────────

    def add(self, device_id: str, record: DeviceRecord) -> DeviceRecord:
        record.setdefault("added_at", datetime.now(UTC).isoformat(timespec="seconds"))
        record.setdefault("seen", [])
        self.devices[device_id] = record
        self.count_type(record.get("type"))
        self.save()
        return record

    def update(self, device_id: str, **changes: Any) -> DeviceRecord | None:
        record = self.devices.get(device_id)
        if record is None:
            return None
        previous_type = record.get("type")
        for key, value in changes.items():
            record[key] = value  # type: ignore[literal-required]
        if record.get("type") != previous_type:
            self.count_type(record.get("type"))
        self.save()
        return record

    def remove(self, device_id: str) -> bool:
        if self.devices.pop(device_id, None) is None:
            return False
        self.save()
        return True

    def mark_seen(self, device_id: str, kind: str) -> bool:
        """Remember that ``kind`` was reported. True the first time."""
        record = self.devices.get(device_id)
        if record is None:
            return False
        seen = record.setdefault("seen", [])
        if kind in seen:
            return False
        seen.append(kind)
        self.save()
        return True

    def free_sender_offset(self) -> int | None:
        """Lowest free sender offset. 0 is the Base ID itself and stays shared."""
        used = {record.get("sender_offset") for record in self.devices.values()}
        for offset in range(1, 128):
            if offset not in used:
                return offset
        return None

    # ── muted transmitters ─────────────────────────────────────────────

    def toggle_muted(self, device_id: str) -> bool:
        """Mute or unmute. Returns the new state."""
        if device_id in self.muted:
            self.muted.discard(device_id)
            muted = False
        else:
            self.muted.add(device_id)
            muted = True
        self.save()
        return muted

    # ── usage ──────────────────────────────────────────────────────────

    def count_type(self, type_key: str | None) -> None:
        if type_key:
            self.type_usage[type_key] = self.type_usage.get(type_key, 0) + 1
