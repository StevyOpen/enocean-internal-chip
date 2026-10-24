"""Sensor entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_BILLION,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    Platform,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .catalog import EntitySpec
from .entity import ChipEntity, setup_entities
from .esp3 import Esp3Packet
from .profiles import POSITIONS, Readings

if TYPE_CHECKING:
    from . import ChipConfigEntry
    from .hub import ChipDevice

_MEASUREMENT = SensorStateClass.MEASUREMENT

DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "temperature": SensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
    ),
    "humidity": SensorEntityDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=_MEASUREMENT,
        suggested_display_precision=0,
    ),
    "illuminance": SensorEntityDescription(
        key="illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=_MEASUREMENT,
        suggested_display_precision=0,
    ),
    "supply_voltage": SensorEntityDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=_MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Useful to whoever installs and maintains the system, noise for
        # everyone else: enable it from the entity settings when needed.
        entity_registry_enabled_default=False,
    ),
    "battery": SensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=_MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "window_position": SensorEntityDescription(
        key="window_position",
        translation_key="window_position",
        device_class=SensorDeviceClass.ENUM,
        options=POSITIONS,
    ),
    "handle_position": SensorEntityDescription(
        key="handle_position",
        translation_key="handle_position",
        device_class=SensorDeviceClass.ENUM,
        options=POSITIONS,
    ),
    "co": SensorEntityDescription(
        key="co",
        device_class=SensorDeviceClass.CO,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=_MEASUREMENT,
    ),
    "co2": SensorEntityDescription(
        key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=_MEASUREMENT,
    ),
    "voc": SensorEntityDescription(
        key="voc",
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_BILLION,
        state_class=_MEASUREMENT,
    ),
    "power": SensorEntityDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=_MEASUREMENT,
    ),
    "energy": SensorEntityDescription(
        key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "gas_flow": SensorEntityDescription(
        key="gas_flow",
        translation_key="gas_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
        state_class=_MEASUREMENT,
    ),
    "gas_total": SensorEntityDescription(
        key="gas_total",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "water_flow": SensorEntityDescription(
        key="water_flow",
        translation_key="water_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
        state_class=_MEASUREMENT,
    ),
    "water_total": SensorEntityDescription(
        key="water_total",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "wind_speed": SensorEntityDescription(
        key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=_MEASUREMENT,
    ),
}

#: A battery reading lower than the previous one by more than this is
#: ignored. Some buttons report a dip right after a press, while the radio
#: draws current; it is not the real charge.
BATTERY_MAX_DROP = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(device: ChipDevice, spec: EntitySpec) -> ChipSensor | None:
        description = DESCRIPTIONS.get(spec.kind)
        if description is None:
            return None
        if spec.kind == "battery":
            return ChipBatterySensor(device, spec, description)
        return ChipSensor(device, spec, description)

    setup_entities(hass, entry, async_add_entities, Platform.SENSOR, factory)


class ChipSensor(ChipEntity, RestoreSensor):
    """A sensor fed by one key of the decoded readings."""

    def __init__(self, device: ChipDevice, spec: EntitySpec, description: SensorEntityDescription) -> None:
        super().__init__(device, spec)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Battery sensors report every few hours: without this a restart
        # would leave every entity unknown until the next telegram.
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        if self.kind not in readings:
            return
        self._attr_native_value = readings[self.kind]
        self.async_write_ha_state()


class ChipBatterySensor(ChipSensor):
    """Battery level that ignores single dips but follows a real drop.

    A sudden low reading is held back once. If the next telegram confirms it,
    it is a real drop and it is shown.
    """

    _held_back: int | None = None

    @callback
    def _handle_readings(self, readings: Readings, packet: Esp3Packet) -> None:
        value = readings.get("battery")
        if value is None:
            return
        previous = self._attr_native_value
        sudden_drop = isinstance(previous, (int, float)) and previous - value > BATTERY_MAX_DROP
        confirmed = self._held_back is not None and abs(self._held_back - value) <= 5
        if sudden_drop and not confirmed:
            self._held_back = value
            return
        self._held_back = None
        self._attr_native_value = value
        self.async_write_ha_state()
