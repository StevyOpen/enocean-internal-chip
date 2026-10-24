"""Diagnostics download.

Radio addresses are replaced with placeholders: someone nearby who knows the
address of a sensor or of this gateway could send telegrams in its name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import DEFAULT_OPTIONS

if TYPE_CHECKING:
    from . import ChipConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ChipConfigEntry) -> dict[str, Any]:
    hub = entry.runtime_data
    gateway = hub.gateway
    version = gateway.version
    aliases = {device_id: f"device_{index}" for index, device_id in enumerate(sorted(hub.devices), 1)}

    def alias(device_id: str) -> str:
        return aliases.get(device_id, "other")

    return {
        "options": {key: hub.option(key) for key in DEFAULT_OPTIONS},
        "gateway": {
            "port": gateway.port,
            "connected": gateway.connected,
            "address_known": gateway.base_id is not None,
            "remaining_writes": gateway.remaining_writes,
            "last_error": gateway.last_error,
            "sent_frames": gateway.sent_frames,
            "app_version": version.app_version if version else None,
            "api_version": version.api_version if version else None,
            "description": version.description if version else None,
        },
        "address_ledger": {
            "unlocked": hub.ledger.unlocked,
            "changes_made": hub.ledger.changes_made,
            "uses_foreign_address": hub.ledger.uses_foreign_address,
        },
        "devices": [
            {
                "device": alias(device_id),
                "type": device.record.get("type"),
                "eep": device.eep,
                "own_sender": bool(device.record.get("sender_offset")),
                "seen": device.record.get("seen"),
                "last_seen": device.last_seen.isoformat(timespec="seconds") if device.last_seen else None,
                "dbm": device.dbm,
                "pairing_requests": hub.monitor.pairing_requests(device_id),
            }
            for device_id, device in sorted(hub.devices.items())
        ],
        "radio": {
            "nearby": len(hub.monitor.heard),
            "telegrams": hub.monitor.telegram_count(),
            "listening": hub.monitor.listening,
            "recent": [
                {
                    "at": item["at"],
                    "sender": alias(item["sender"]),
                    "rorg": item["rorg"],
                    "dbm": item["dbm"],
                    "summary": item["summary"],
                    "teach_in": item["teach_in"],
                }
                for item in hub.monitor.telegram_list(50)
            ],
        },
    }
