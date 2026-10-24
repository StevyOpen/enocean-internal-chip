"""Config, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_internal_chip.const import CONF_PORT, DOMAIN
from custom_components.enocean_internal_chip.esp3 import ChipVersion

from .conftest import CHIP_ID, PORT

VERSION = ChipVersion("2.11.1.0", "2.6.3.0", CHIP_ID, "454F0103", "GATEWAYCTRL")
FLOW = "custom_components.enocean_internal_chip.config_flow.gateway"

pytestmark = pytest.mark.usefixtures("no_frontend")


async def test_pick_a_detected_module(hass: HomeAssistant) -> None:
    with (
        patch(f"{FLOW}.candidate_ports", return_value=[PORT, "/dev/ttyS0"]),
        patch(f"{FLOW}.probe", side_effect=lambda port: VERSION if port == PORT else None),
        patch("custom_components.enocean_internal_chip.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: PORT})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PORT: PORT}
    assert result["result"].unique_id == CHIP_ID


async def test_manual_port(hass: HomeAssistant) -> None:
    with (
        patch(f"{FLOW}.candidate_ports", return_value=[]),
        patch(f"{FLOW}.probe", return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: "/dev/nothing"})
        assert result["errors"] == {"base": "cannot_connect"}
    with (
        patch(f"{FLOW}.probe", return_value=VERSION),
        patch("custom_components.enocean_internal_chip.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: f" {PORT} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PORT: PORT}


async def test_detected_list_offers_manual_entry(hass: HomeAssistant) -> None:
    with (
        patch(f"{FLOW}.candidate_ports", return_value=[PORT]),
        patch(f"{FLOW}.probe", return_value=VERSION),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: "manual"})
    assert result["step_id"] == "manual"


async def test_only_one_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch(f"{FLOW}.probe", return_value=None):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: "/dev/ttyUSB9"})
    assert result["errors"] == {"base": "cannot_connect"}
    new_version = ChipVersion("2.11.1.0", "2.6.3.0", "0BADC0DE", "454F0103", "GATEWAYCTRL")
    with (
        patch(f"{FLOW}.probe", return_value=new_version),
        patch("custom_components.enocean_internal_chip.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PORT: "/dev/ttyUSB9"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PORT] == "/dev/ttyUSB9"
    assert config_entry.unique_id == "0BADC0DE"


async def test_options(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"auto_labels": False, "role_labels": False, "notify_teach_in": True, "allow_simulation": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["allow_simulation"] is True
    assert config_entry.options["auto_labels"] is False
