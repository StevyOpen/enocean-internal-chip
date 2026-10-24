"""Diagnostics must not leak radio addresses."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.enocean_internal_chip import simulator
from custom_components.enocean_internal_chip.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_hide_addresses(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = setup_integration.runtime_data
    await hub.async_adopt("05:00:00:20", name="Thermometer", type_key="temperature")
    fake_module.receive(simulator.build("temperature"))
    await hass.async_block_till_done()
    data = await async_get_config_entry_diagnostics(hass, setup_integration)
    text = json.dumps(data)
    assert "05:00:00:20" not in text
    assert "FF:A1:B2" not in text
    assert data["devices"][0]["device"] == "device_1"
    assert data["radio"]["recent"][0]["sender"] == "device_1"
    assert data["gateway"]["address_known"] is True
