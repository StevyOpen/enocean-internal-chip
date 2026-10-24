"""Generic Home Assistant labels for new entities.

When an entity of this integration is created for the first time, it gets a
label that says what kind of thing it is ("Doors & windows", "Climate", ...)
and, if the option is on, a label for its role ("Perimeter", "24/7 safety").
Labels make dashboards, automations and filters easy to build without
remembering entity IDs.

The labels are plain Home Assistant labels: they can be renamed, recoloured or
removed like any other. Entities that already have labels are never touched,
and an entity is labelled only once, when it first appears.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, label_registry as lr

from .const import CONF_AUTO_LABELS, CONF_ROLE_LABELS, DOMAIN
from .hub import entity_kind_from_unique_id

if TYPE_CHECKING:
    from .hub import ChipHub

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabelStyle:
    name: str
    icon: str
    color: str


# What the device is. Picked from the device type category.
LIGHTING = LabelStyle("Lighting", "mdi:lightbulb-group", "amber")
PLUGS = LabelStyle("Plugs & relays", "mdi:power-socket-eu", "orange")
BUTTONS = LabelStyle("Buttons", "mdi:gesture-tap-button", "purple")
DOORS_WINDOWS = LabelStyle("Doors & windows", "mdi:door-open", "indigo")
MOTION = LabelStyle("Motion", "mdi:motion-sensor", "cyan")
SMOKE_GAS = LabelStyle("Smoke & gas", "mdi:smoke-detector-variant", "red")
COVERS = LabelStyle("Covers", "mdi:window-shutter", "brown")
CLIMATE = LabelStyle("Climate", "mdi:thermometer", "light-blue")
AIR = LabelStyle("Air quality", "mdi:air-filter", "teal")
ENERGY = LabelStyle("Energy & meters", "mdi:meter-electric", "green")
WEATHER = LabelStyle("Weather", "mdi:weather-partly-rainy", "blue")
HEALTH = LabelStyle("Device health", "mdi:battery-heart-variant", "blue-grey")

# What the entity is for. Optional.
PERIMETER = LabelStyle("Perimeter", "mdi:shield-home", "deep-orange")
INTERIOR = LabelStyle("Interior", "mdi:home-outline", "light-green")
SAFETY = LabelStyle("24/7 safety", "mdi:alarm-light", "pink")

ALL_STYLES = (
    LIGHTING,
    PLUGS,
    BUTTONS,
    DOORS_WINDOWS,
    MOTION,
    SMOKE_GAS,
    COVERS,
    CLIMATE,
    AIR,
    ENERGY,
    WEATHER,
    HEALTH,
    PERIMETER,
    INTERIOR,
    SAFETY,
)

CATEGORY_LABELS: dict[str, LabelStyle] = {
    "button": BUTTONS,
    "contact": DOORS_WINDOWS,
    "window": DOORS_WINDOWS,
    "motion": MOTION,
    "smoke": SMOKE_GAS,
    "cover": COVERS,
    "climate": CLIMATE,
    "air": AIR,
    "meter": ENERGY,
    "weather": WEATHER,
    "switch": PLUGS,
    "light": LIGHTING,
}

#: Entities about the device itself rather than what it measures.
HEALTH_KINDS = frozenset({"battery", "battery_low", "supply_voltage"})

ROLE_LABELS: dict[str, LabelStyle] = {
    "opening": PERIMETER,
    "opening_window": PERIMETER,
    "window_position": PERIMETER,
    "handle_position": PERIMETER,
    "vibration": PERIMETER,
    "motion": INTERIOR,
    "smoke": SAFETY,
    "co": SAFETY,
}


def labels_for(category: str | None, kind: str, *, roles: bool) -> list[LabelStyle]:
    """The labels a new entity of ``kind`` gets, in order."""
    styles: list[LabelStyle] = []
    if kind in HEALTH_KINDS:
        styles.append(HEALTH)
    elif (style := CATEGORY_LABELS.get(category or "")) is not None:
        styles.append(style)
    if roles and (role := ROLE_LABELS.get(kind)) is not None:
        styles.append(role)
    return styles


@callback
def ensure_label(hass: HomeAssistant, style: LabelStyle) -> str:
    """Return the ID of the label named ``style.name``, creating it if needed."""
    registry = lr.async_get(hass)
    if (label := registry.async_get_label_by_name(style.name)) is not None:
        return label.label_id
    return registry.async_create(style.name, icon=style.icon, color=style.color).label_id


@callback
def async_setup_labels(hass: HomeAssistant, hub: ChipHub) -> CALLBACK_TYPE:
    """Label entities of this config entry as they are created."""

    @callback
    def is_create(data: er.EventEntityRegistryUpdatedData) -> bool:
        return data["action"] == "create"

    @callback
    def created(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if not hub.option(CONF_AUTO_LABELS):
            return
        registry = er.async_get(hass)
        entry = registry.async_get(event.data["entity_id"])
        if entry is None or entry.platform != DOMAIN or entry.config_entry_id != hub.entry.entry_id or entry.labels:
            return
        kind = entity_kind_from_unique_id(entry.unique_id)
        device = hub.devices.get(_device_id(entry.unique_id))
        if kind is None or device is None:
            return
        category = device.type.category if device.type else None
        styles = labels_for(category, kind, roles=bool(hub.option(CONF_ROLE_LABELS)))
        if not styles:
            return
        label_ids = {ensure_label(hass, style) for style in styles}
        registry.async_update_entity(entry.entity_id, labels=label_ids)
        _LOGGER.debug("Labelled %s with %s", entry.entity_id, [s.name for s in styles])

    return hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, created, event_filter=is_create)


def _device_id(unique_id: str) -> str:
    """``01A2B3C4-temperature`` -> ``01:A2:B3:C4``."""
    prefix, _, _ = unique_id.partition("-")
    return ":".join(prefix[i : i + 2] for i in range(0, len(prefix), 2)).upper()
