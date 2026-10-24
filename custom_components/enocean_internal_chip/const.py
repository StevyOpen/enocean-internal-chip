"""Constants for the EnOcean Internal Chip integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "enocean_internal_chip"
NAME: Final = "EnOcean Internal Chip"
VERSION: Final = "0.1.1"

CONF_PORT: Final = "port"

# Options
CONF_AUTO_LABELS: Final = "auto_labels"
CONF_ROLE_LABELS: Final = "role_labels"
CONF_ALLOW_SIMULATION: Final = "allow_simulation"
CONF_NOTIFY_TEACH_IN: Final = "notify_teach_in"

DEFAULT_OPTIONS: Final = {
    CONF_AUTO_LABELS: True,
    CONF_ROLE_LABELS: True,
    CONF_ALLOW_SIMULATION: False,
    CONF_NOTIFY_TEACH_IN: True,
}

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Dispatcher signals, formatted with the config entry ID.
SIGNAL_NEW_ENTITIES: Final = f"{DOMAIN}_new_entities_{{}}"
SIGNAL_GATEWAY_STATUS: Final = f"{DOMAIN}_gateway_status_{{}}"

# Events on the Home Assistant bus.
EVENT_NO_FEEDBACK: Final = f"{DOMAIN}_no_feedback"

# Panel
PANEL_URL_PATH: Final = "enocean-chip"
PANEL_ELEMENT: Final = "enocean-chip-panel"
STATIC_URL: Final = f"/{DOMAIN}_static"
API_PREFIX: Final = f"/api/{DOMAIN}"
