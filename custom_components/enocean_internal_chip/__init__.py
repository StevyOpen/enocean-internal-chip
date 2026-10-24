"""EnOcean Internal Chip: EnOcean devices through a USB stick or a built-in module."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import serial
import voluptuous as vol

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from . import simulator
from .const import CONF_PORT, DOMAIN, PANEL_ELEMENT, PANEL_URL_PATH, PLATFORMS, STATIC_URL, VERSION
from .http_api import async_register_views
from .hub import PAIRING_WINDOW, ChipHub
from .labels import async_setup_labels

_LOGGER = logging.getLogger(__name__)

type ChipConfigEntry = ConfigEntry[ChipHub]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PANEL_DIR = Path(__file__).parent / "www"
PANEL_FILE = "enocean-chip-panel.js"

SERVICE_SIMULATE = "simulate"
SERVICE_OPEN_PAIRING = "open_pairing"

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Required("preset"): vol.In(list(simulator.PRESETS_BY_KEY)),
        vol.Optional("sender"): cv.string,
    }
)
OPEN_PAIRING_SCHEMA = vol.Schema(
    {vol.Optional("seconds", default=PAIRING_WINDOW): vol.All(vol.Coerce(int), vol.Range(min=10, max=600))}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Static files, API views and services exist once, whatever the entries."""
    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(PANEL_DIR), cache_headers=True)])
    async_register_views(hass)
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ChipConfigEntry) -> bool:
    hub = ChipHub(hass, entry, entry.data[CONF_PORT])
    try:
        await hub.async_setup()
    except (serial.SerialException, OSError) as err:
        raise ConfigEntryNotReady(f"Cannot open {entry.data[CONF_PORT]}: {err}") from err
    entry.runtime_data = hub

    # Before the platforms, so the first entities get their labels too.
    entry.async_on_unload(async_setup_labels(hass, hub))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChipConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ChipConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a device from the Home Assistant device page."""
    hub = entry.runtime_data
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == hub.gateway_identifier:
            return False
        if identifier in hub.devices:
            await hub.async_remove(identifier, from_registry=False)
            _LOGGER.info("EnOcean device %s removed from the device page", identifier)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    # The version in the URL changes with the file, so browsers never keep an
    # old copy of the panel after an update.
    digest = await hass.async_add_executor_job(_short_hash, PANEL_DIR / PANEL_FILE)
    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_ELEMENT,
            sidebar_title="EnOcean",
            sidebar_icon="mdi:radio-tower",
            module_url=f"{STATIC_URL}/{PANEL_FILE}?v={VERSION}-{digest}",
            require_admin=True,
            config={"domain": DOMAIN, "version": VERSION},
        )
    except ValueError:
        _LOGGER.debug("The EnOcean panel is already registered")


@callback
def _register_services(hass: HomeAssistant) -> None:
    def loaded_hub() -> ChipHub:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("The EnOcean Internal Chip integration is not loaded")
        return entries[0].runtime_data

    async def simulate(call: ServiceCall) -> None:
        hub = loaded_hub()
        try:
            hub.inject(simulator.build(call.data["preset"], call.data.get("sender")))
        except (ValueError, HomeAssistantError) as err:
            raise ServiceValidationError(str(err)) from err

    async def open_pairing(call: ServiceCall) -> None:
        loaded_hub().open_pairing(call.data["seconds"])

    hass.services.async_register(DOMAIN, SERVICE_SIMULATE, simulate, schema=SIMULATE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_OPEN_PAIRING, open_pairing, schema=OPEN_PAIRING_SCHEMA)


def _short_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"
