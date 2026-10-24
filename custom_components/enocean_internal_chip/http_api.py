"""REST endpoints behind the panel.

Every view needs an authenticated Home Assistant user who is an
administrator: the panel can add devices, send radio telegrams and change the
gateway address, which are all administrator tasks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING, Any, Concatenate

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import KEY_HASS, KEY_HASS_USER, HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from . import simulator
from .catalog import DEVICE_TYPES, get_type
from .const import API_PREFIX, CONF_ALLOW_SIMULATION, DEFAULT_OPTIONS, DOMAIN, VERSION
from .esp3 import format_id, id_to_int, parse_id
from .gateway import valid_base_id
from .hub import entity_kind_from_unique_id
from .ledger import CHANGES_BEFORE_CONFIRMATION, CONFIRMATION_WORD, RESERVED_CHANGES
from .monitor import MAX_TELEGRAMS

if TYPE_CHECKING:
    from .hub import ChipDevice, ChipHub

_LOGGER = logging.getLogger(__name__)

TELEGRAM_PAGE_DEFAULT = 100
TELEGRAM_PAGE_MAX = 500

CATEGORIES = (
    ("button", "Buttons"),
    ("contact", "Contacts"),
    ("window", "Windows"),
    ("motion", "Motion"),
    ("climate", "Climate"),
    ("air", "Air quality"),
    ("smoke", "Smoke and gas"),
    ("weather", "Weather"),
    ("meter", "Meters"),
    ("switch", "Switches and plugs"),
    ("light", "Lighting"),
    ("cover", "Covers"),
)


class ApiError(Exception):
    """An error the panel shows as it is."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


type Handler = Callable[Concatenate[HomeAssistantView, web.Request, ChipHub, ...], Awaitable[web.Response]]


def admin_only(handler: Handler) -> Callable[..., Awaitable[web.Response]]:
    """Check the user, find the hub and turn errors into JSON answers."""

    @wraps(handler)
    async def wrapper(view: HomeAssistantView, request: web.Request, *args: Any, **kwargs: Any) -> web.Response:
        if not request[KEY_HASS_USER].is_admin:
            raise Unauthorized
        hass: HomeAssistant = request.app[KEY_HASS]
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return view.json_message("The integration is not loaded", HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            return await handler(view, request, entries[0].runtime_data, *args, **kwargs)
        except ApiError as err:
            return view.json({"message": str(err), **err.extra}, err.status)
        except vol.Invalid as err:
            return view.json_message(f"Invalid request: {err}", HTTPStatus.BAD_REQUEST)
        except HomeAssistantError as err:
            return view.json_message(str(err), HTTPStatus.BAD_REQUEST)

    return wrapper


async def _body(request: web.Request, schema: vol.Schema) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError as err:
        raise ApiError("The request body is not valid JSON") from err
    return schema(data if isinstance(data, dict) else {})


def _user(request: web.Request) -> str:
    return request[KEY_HASS_USER].name or request[KEY_HASS_USER].id


def _device_id(value: str) -> str:
    raw = parse_id(value)
    if raw is None:
        raise ApiError("The device ID must be 4 hexadecimal bytes, like 01:A2:B3:C4")
    return format_id(raw)


# ── shapes ─────────────────────────────────────────────────────────────


@callback
def _gateway_summary(hub: ChipHub) -> dict[str, Any]:
    gateway = hub.gateway
    version = gateway.version
    return {
        "port": gateway.port,
        "connected": gateway.connected,
        "ready": gateway.connected and gateway.base_id is not None,
        "last_error": gateway.last_error,
        "base_id": format_id(gateway.base_id) or None,
        "remaining_writes": gateway.remaining_writes,
        "sent_frames": gateway.sent_frames,
        "chip": {
            "app_version": version.app_version,
            "api_version": version.api_version,
            "chip_id": version.chip_id,
            "chip_version": version.chip_version,
            "description": version.description,
        }
        if version
        else None,
    }


@callback
def _pairing(hub: ChipHub) -> dict[str, Any]:
    window = hub.pairing
    seconds = window.remaining if window else 0
    return {
        "open": seconds > 0,
        "seconds": seconds,
        "device_id": window.device_id if window and seconds else None,
    }


@callback
def _device_dict(hub: ChipHub, device: ChipDevice) -> dict[str, Any]:
    hass = hub.hass
    data = device.as_dict()
    data["can_send"] = device.can_send
    data["keeps_asking"] = hub.monitor.keeps_asking(device.id)
    data["pairing_requests"] = hub.monitor.pairing_requests(device.id)
    ha_device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device.id)})
    data["ha_device_id"] = ha_device.id if ha_device else None
    data["area_id"] = ha_device.area_id if ha_device else None
    area = ar.async_get(hass).async_get_area(ha_device.area_id) if ha_device and ha_device.area_id else None
    data["area_name"] = area.name if area else None
    data["display_name"] = (ha_device.name_by_user or ha_device.name) if ha_device else device.name
    entities = (
        er.async_entries_for_device(er.async_get(hass), ha_device.id, include_disabled_entities=True)
        if ha_device
        else []
    )
    data["entities"] = [
        {
            "entity_id": entry.entity_id,
            "kind": entity_kind_from_unique_id(entry.unique_id),
            "name": entry.name or entry.original_name,
            "disabled": entry.disabled_by is not None,
        }
        for entry in entities
    ]
    return data


# ── status and listening ───────────────────────────────────────────────


class StatusView(HomeAssistantView):
    url = f"{API_PREFIX}/status"
    name = f"api:{DOMAIN}:status"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        return self.json(
            {
                "version": VERSION,
                "gateway": _gateway_summary(hub),
                "listening": hub.monitor.listening,
                "snapshot": hub.monitor.snapshot is not None,
                "pairing": _pairing(hub),
                "counts": {
                    "devices": len(hub.devices),
                    "heard": len(hub.monitor.heard),
                    "telegrams": hub.monitor.telegram_count(),
                },
                "options": {key: bool(hub.option(key)) for key in DEFAULT_OPTIONS},
            }
        )


class ListenView(HomeAssistantView):
    url = f"{API_PREFIX}/listen/{{action}}"
    name = f"api:{DOMAIN}:listen"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub, action: str) -> web.Response:
        monitor = hub.monitor
        adopted = set(hub.devices)
        if action == "start":
            monitor.listening = True
        elif action == "stop":
            monitor.listening = False
        elif action == "snapshot":
            monitor.take_snapshot(adopted)
            monitor.listening = True
        elif action == "drop_snapshot":
            monitor.drop_snapshot()
        elif action == "clear":
            monitor.clear(adopted)
        else:
            raise ApiError(f"Unknown action: {action}", HTTPStatus.NOT_FOUND)
        return self.json(
            {"listening": monitor.listening, "snapshot": monitor.snapshot is not None, "heard": len(monitor.heard)}
        )


# ── nearby transmitters ────────────────────────────────────────────────


class HeardView(HomeAssistantView):
    url = f"{API_PREFIX}/heard"
    name = f"api:{DOMAIN}:heard"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        items = []
        for item in hub.monitor.heard_list():
            device = hub.devices.get(item["id"])
            suggested = get_type(item.get("suggested_type"))
            item["adopted"] = device is not None
            item["name"] = device.name if device else None
            item["muted"] = item["id"] in hub.store.muted
            item["suggested_type_name"] = suggested.name if suggested else None
            items.append(item)
        return self.json(
            {
                "listening": hub.monitor.listening,
                "snapshot": hub.monitor.snapshot is not None,
                "devices": items,
            }
        )


class HeardItemView(HomeAssistantView):
    url = f"{API_PREFIX}/heard/{{device_id}}/{{action}}"
    name = f"api:{DOMAIN}:heard_item"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub, device_id: str, action: str) -> web.Response:
        device_id = _device_id(device_id)
        if action == "forget":
            if device_id in hub.devices:
                raise ApiError("Adopted devices stay in the list. Remove the device first.")
            return self.json({"forgotten": hub.monitor.forget(device_id)})
        if action == "mute":
            return self.json({"muted": hub.store.toggle_muted(device_id)})
        raise ApiError(f"Unknown action: {action}", HTTPStatus.NOT_FOUND)


# ── adopted devices ────────────────────────────────────────────────────

ADOPT_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Required("name"): vol.All(cv.string, vol.Length(max=80)),
        vol.Required("type"): cv.string,
        vol.Optional("eep"): vol.Any(None, cv.string),
        vol.Optional("area_id"): vol.Any(None, cv.string),
        vol.Optional("manufacturer_id"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0, max=0x7FF))),
    },
    extra=vol.REMOVE_EXTRA,
)

UPDATE_SCHEMA = vol.Schema(
    {
        vol.Optional("name"): vol.All(cv.string, vol.Length(max=80)),
        vol.Optional("type"): cv.string,
        vol.Optional("eep"): vol.Any(None, cv.string),
        vol.Optional("area_id"): vol.Any(None, cv.string),
        vol.Optional("manufacturer_id"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0, max=0x7FF))),
    },
    extra=vol.REMOVE_EXTRA,
)


class DevicesView(HomeAssistantView):
    url = f"{API_PREFIX}/devices"
    name = f"api:{DOMAIN}:devices"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        devices = sorted(
            (_device_dict(hub, device) for device in hub.devices.values()),
            key=lambda item: (item["display_name"] or "").casefold(),
        )
        return self.json({"devices": devices})

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub) -> web.Response:
        body = await _body(request, ADOPT_SCHEMA)
        area_id = body.get("area_id") or None
        if area_id and ar.async_get(hub.hass).async_get_area(area_id) is None:
            raise ApiError("That area does not exist")
        device = await hub.async_adopt(
            body["id"],
            name=body["name"],
            type_key=body["type"],
            eep=body.get("eep"),
            area_id=area_id,
            manufacturer_id=body.get("manufacturer_id"),
        )
        _LOGGER.info("%s added EnOcean device %s as %s", _user(request), device.id, device.record.get("type"))
        return self.json({"device": _device_dict(hub, device)}, HTTPStatus.CREATED)


class DeviceView(HomeAssistantView):
    url = f"{API_PREFIX}/devices/{{device_id}}"
    name = f"api:{DOMAIN}:device"

    @admin_only
    async def patch(self, request: web.Request, hub: ChipHub, device_id: str) -> web.Response:
        body = await _body(request, UPDATE_SCHEMA)
        device = await hub.async_update(
            _device_id(device_id),
            name=body.get("name"),
            type_key=body.get("type"),
            eep=body.get("eep"),
            manufacturer_id=body.get("manufacturer_id"),
        )
        if "area_id" in body:
            area_id = body["area_id"] or None
            if area_id and ar.async_get(hub.hass).async_get_area(area_id) is None:
                raise ApiError("That area does not exist")
            registry = dr.async_get(hub.hass)
            ha_device = registry.async_get_device(identifiers={(DOMAIN, device.id)})
            if ha_device is not None:
                registry.async_update_device(ha_device.id, area_id=area_id)
        return self.json({"device": _device_dict(hub, device)})

    @admin_only
    async def delete(self, request: web.Request, hub: ChipHub, device_id: str) -> web.Response:
        """Remove a device and its entities.

        The entities go with the device, so the caller has to say so with
        ``?entities=remove``. Without it the answer only says what would go.
        """
        device_id = _device_id(device_id)
        device = hub.devices.get(device_id)
        if device is None:
            raise ApiError(f"Device {device_id} is not adopted", HTTPStatus.NOT_FOUND)
        entities = hub.entity_count(device_id)
        if request.query.get("entities") != "remove":
            raise ApiError(
                f"{device.name} has {entities} entities. Repeat the request with "
                "entities=remove to remove the device and its entities.",
                HTTPStatus.CONFLICT,
                entities=entities,
            )
        await hub.async_remove(device_id)
        _LOGGER.warning(
            "%s removed EnOcean device %s (%s) and its %d entities",
            _user(request),
            device_id,
            device.name,
            entities,
        )
        return self.json({"removed": device_id, "entities": entities})


class DevicePairView(HomeAssistantView):
    url = f"{API_PREFIX}/devices/{{device_id}}/pair"
    name = f"api:{DOMAIN}:device_pair"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub, device_id: str) -> web.Response:
        result = await hub.async_pair(_device_id(device_id))
        result["pairing"] = _pairing(hub)
        return self.json(result)


# ── catalog and areas ──────────────────────────────────────────────────


class TypesView(HomeAssistantView):
    url = f"{API_PREFIX}/types"
    name = f"api:{DOMAIN}:types"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        usage = hub.store.type_usage
        types = [{**device_type.as_dict(), "usage": usage.get(device_type.key, 0)} for device_type in DEVICE_TYPES]
        return self.json({"types": types, "categories": [{"key": key, "name": name} for key, name in CATEGORIES]})


class AreasView(HomeAssistantView):
    url = f"{API_PREFIX}/areas"
    name = f"api:{DOMAIN}:areas"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        areas = sorted(ar.async_get(hub.hass).async_list_areas(), key=lambda area: area.name.casefold())
        return self.json({"areas": [{"id": area.id, "name": area.name} for area in areas]})


# ── telegram log ───────────────────────────────────────────────────────


class TelegramsView(HomeAssistantView):
    url = f"{API_PREFIX}/telegrams"
    name = f"api:{DOMAIN}:telegrams"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        try:
            limit = int(request.query.get("limit", TELEGRAM_PAGE_DEFAULT))
            offset = int(request.query.get("offset", 0))
        except ValueError as err:
            raise ApiError("limit and offset must be numbers") from err
        limit = max(1, min(limit, TELEGRAM_PAGE_MAX))
        offset = max(0, offset)
        items = hub.monitor.telegram_list(MAX_TELEGRAMS)
        if sender := request.query.get("sender"):
            sender = _device_id(sender)
            items = [item for item in items if item["sender"] == sender]
        if request.query.get("adopted") == "1":
            items = [item for item in items if item["adopted"]]
        return self.json(
            {"total": len(items), "offset": offset, "limit": limit, "telegrams": items[offset : offset + limit]}
        )

    @admin_only
    async def delete(self, request: web.Request, hub: ChipHub) -> web.Response:
        count = hub.monitor.telegram_count()
        hub.monitor.clear_telegrams()
        _LOGGER.info("%s cleared the EnOcean telegram log (%d telegrams)", _user(request), count)
        return self.json({"cleared": count})


# ── gateway ────────────────────────────────────────────────────────────


@callback
def _gateway_details(hub: ChipHub) -> dict[str, Any]:
    ledger = hub.ledger
    return {
        "gateway": _gateway_summary(hub),
        "pairing": _pairing(hub),
        "address": {
            "unlocked": ledger.unlocked,
            "unlocked_at": ledger.data.get("unlocked_at"),
            "original": ledger.data.get("original"),
            "original_seen_at": ledger.data.get("original_seen_at"),
            "current": ledger.data.get("current"),
            "uses_foreign_address": ledger.uses_foreign_address,
            "changes_made": ledger.changes_made,
            "needs_confirmation": ledger.needs_confirmation,
            "history": list(reversed(ledger.data.get("history") or [])),
            "rules": {
                "reserved_changes": RESERVED_CHANGES,
                "changes_before_confirmation": CHANGES_BEFORE_CONFIRMATION,
                "confirmation_word": CONFIRMATION_WORD,
            },
            "one_way_actuators": sum(1 for device in hub.devices.values() if device.record.get("sender_offset")),
        },
    }


class GatewayView(HomeAssistantView):
    url = f"{API_PREFIX}/gateway"
    name = f"api:{DOMAIN}:gateway"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        return self.json(_gateway_details(hub))


ADDRESS_SCHEMA = vol.Schema(
    {
        vol.Required("action"): vol.In(("unlock", "lock", "refresh", "write", "restore")),
        vol.Optional("address"): cv.string,
        vol.Optional("expected_current"): cv.string,
        vol.Optional("note"): vol.Any(None, vol.All(cv.string, vol.Length(max=200))),
        vol.Optional("confirm"): cv.string,
    },
    extra=vol.REMOVE_EXTRA,
)


class GatewayAddressView(HomeAssistantView):
    url = f"{API_PREFIX}/gateway/address"
    name = f"api:{DOMAIN}:gateway_address"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub) -> web.Response:
        body = await _body(request, ADDRESS_SCHEMA)
        action = body["action"]
        async with hub.address_lock:
            if action == "unlock":
                await hub.ledger.async_set_unlocked(True)
                _LOGGER.warning("%s unlocked gateway address changes", _user(request))
            elif action == "lock":
                await hub.ledger.async_set_unlocked(False)
            elif action == "refresh":
                if await hub.gateway.async_refresh_base_id() is None:
                    raise ApiError("The module did not answer", HTTPStatus.SERVICE_UNAVAILABLE)
            else:
                result = await self._write(request, hub, body)
                return self.json({**result, **_gateway_details(hub)})
        return self.json(_gateway_details(hub))

    async def _write(self, request: web.Request, hub: ChipHub, body: dict[str, Any]) -> dict[str, Any]:
        gateway, ledger = hub.gateway, hub.ledger
        if not ledger.unlocked:
            raise ApiError("Address changes are locked on this gateway. Unlock them first.")
        if not gateway.connected or gateway.base_id is None:
            raise ApiError("The gateway is not ready", HTTPStatus.SERVICE_UNAVAILABLE)
        current = format_id(gateway.base_id)
        if parse_id(body.get("expected_current")) != gateway.base_id:
            raise ApiError(
                f"The gateway address is now {current}, not the one shown on the page. Check it and try again.",
                HTTPStatus.CONFLICT,
            )
        remaining = gateway.remaining_writes

        if body["action"] == "restore":
            original = ledger.data.get("original")
            if not original:
                raise ApiError("No original address is recorded for this gateway")
            if original == current:
                raise ApiError("The gateway already uses its original address")
            target = parse_id(original)
            if remaining is not None and remaining < 1:
                raise ApiError("The module allows no more address changes")
        else:
            target = parse_id(body.get("address"))
            if target is None or not valid_base_id(target):
                raise ApiError("The address must be between FF:80:00:00 and FF:FF:FF:80")
            if id_to_int(target) & 0x7F:
                raise ApiError("The address must end in 00 or 80 (it covers 128 sender IDs)")
            if target == gateway.base_id:
                raise ApiError("The gateway already uses this address")
            if remaining is not None and remaining <= RESERVED_CHANGES:
                raise ApiError(
                    f"The module allows only {remaining} more changes. They are kept to go "
                    "back to the original address."
                )

        if (ledger.needs_confirmation or remaining is None) and body.get("confirm") != CONFIRMATION_WORD:
            raise ApiError(
                f"Type {CONFIRMATION_WORD} to confirm this change.",
                needs_confirmation=True,
            )

        assert target is not None
        result = await gateway.async_write_base_id(target)
        if result["success"]:
            await ledger.async_record_write(
                previous=current,
                current=result["current"],
                note=body.get("note"),
                remaining=result["remaining_writes"],
            )
        _LOGGER.warning(
            "%s changed the gateway address %s -> %s: %s",
            _user(request),
            current,
            format_id(target),
            "done" if result["success"] else result["reason"],
        )
        return result


PAIRING_SCHEMA = vol.Schema(
    {
        vol.Required("action"): vol.In(("open", "close")),
        vol.Optional("seconds", default=180): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
    },
    extra=vol.REMOVE_EXTRA,
)


class GatewayPairingView(HomeAssistantView):
    url = f"{API_PREFIX}/gateway/pairing"
    name = f"api:{DOMAIN}:gateway_pairing"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub) -> web.Response:
        body = await _body(request, PAIRING_SCHEMA)
        if body["action"] == "open":
            hub.open_pairing(body["seconds"])
        else:
            hub.close_pairing()
        return self.json({"pairing": _pairing(hub)})


class ReloadView(HomeAssistantView):
    url = f"{API_PREFIX}/reload"
    name = f"api:{DOMAIN}:reload"

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub) -> web.Response:
        hub.hass.config_entries.async_schedule_reload(hub.entry.entry_id)
        return self.json({"reloading": True}, HTTPStatus.ACCEPTED)


# ── simulation ─────────────────────────────────────────────────────────

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Optional("preset"): cv.string,
        vol.Optional("sender"): cv.string,
        vol.Optional("rorg"): cv.string,
        vol.Optional("data"): cv.string,
        vol.Optional("status"): cv.string,
    },
    extra=vol.REMOVE_EXTRA,
)


def _hex_bytes(text: str | None) -> bytes:
    cleaned = "".join(ch for ch in (text or "") if ch not in " :-")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as err:
        raise ApiError("Bytes must be written in hexadecimal, like A5 00 00 76 08") from err


class SimulateView(HomeAssistantView):
    url = f"{API_PREFIX}/simulate"
    name = f"api:{DOMAIN}:simulate"

    @admin_only
    async def get(self, request: web.Request, hub: ChipHub) -> web.Response:
        return self.json(
            {
                "allowed": bool(hub.option(CONF_ALLOW_SIMULATION)),
                "presets": [preset.as_dict() for preset in simulator.PRESETS],
            }
        )

    @admin_only
    async def post(self, request: web.Request, hub: ChipHub) -> web.Response:
        body = await _body(request, SIMULATE_SCHEMA)
        try:
            if body.get("preset"):
                packet = simulator.build(body["preset"], body.get("sender"))
            else:
                rorg = _hex_bytes(body.get("rorg"))
                status = _hex_bytes(body.get("status") or "00")
                if len(rorg) != 1 or len(status) != 1:
                    raise ApiError("RORG and status are one byte each")
                packet = simulator.build_custom(
                    rorg[0], _hex_bytes(body.get("data")), body.get("sender") or "", status[0]
                )
        except ValueError as err:
            raise ApiError(str(err)) from err
        hub.inject(packet)
        return self.json({"sent": packet.sender_id, "raw": packet.data.hex(" ").upper()})


VIEWS: tuple[type[HomeAssistantView], ...] = (
    StatusView,
    ListenView,
    HeardView,
    HeardItemView,
    DevicesView,
    DeviceView,
    DevicePairView,
    TypesView,
    AreasView,
    TelegramsView,
    GatewayView,
    GatewayAddressView,
    GatewayPairingView,
    ReloadView,
    SimulateView,
)


@callback
def async_register_views(hass: HomeAssistant) -> None:
    for view in VIEWS:
        hass.http.register_view(view())
