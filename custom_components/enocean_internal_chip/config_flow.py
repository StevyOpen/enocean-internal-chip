"""Config flow: find the EnOcean module, then options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import gateway
from .const import (
    CONF_ALLOW_SIMULATION,
    CONF_AUTO_LABELS,
    CONF_NOTIFY_TEACH_IN,
    CONF_PORT,
    CONF_ROLE_LABELS,
    DEFAULT_OPTIONS,
    DOMAIN,
    NAME,
)
from .esp3 import ChipVersion

MANUAL = "manual"


class ChipConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick a detected module or type the port by hand."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, ChipVersion] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ChipOptionsFlow:
        return ChipOptionsFlow()

    async def _async_detect(self) -> dict[str, ChipVersion]:
        def detect() -> dict[str, ChipVersion]:
            found: dict[str, ChipVersion] = {}
            for port in gateway.candidate_ports():
                if (version := gateway.probe(port)) is not None:
                    found[port] = version
            return found

        return await self.hass.async_add_executor_job(detect)

    async def _async_probe(self, port: str) -> ChipVersion | None:
        return await self.hass.async_add_executor_job(gateway.probe, port)

    async def _async_finish(self, port: str, version: ChipVersion) -> ConfigFlowResult:
        await self.async_set_unique_id(version.chip_id)
        self._abort_if_unique_id_configured(updates={CONF_PORT: port})
        return self.async_create_entry(title=NAME, data={CONF_PORT: port})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            port = user_input[CONF_PORT]
            if port == MANUAL:
                return await self.async_step_manual()
            version = self._found.get(port) or await self._async_probe(port)
            if version is not None:
                return await self._async_finish(port, version)
            return self.async_show_form(
                step_id="user", data_schema=self._port_schema(), errors={"base": "cannot_connect"}
            )

        self._found = await self._async_detect()
        if not self._found:
            return await self.async_step_manual()
        return self.async_show_form(step_id="user", data_schema=self._port_schema())

    def _port_schema(self) -> vol.Schema:
        options = [
            SelectOptionDict(value=port, label=_describe(port, version)) for port, version in self._found.items()
        ]
        options.append(SelectOptionDict(value=MANUAL, label="Enter the port by hand"))
        return vol.Schema(
            {
                vol.Required(CONF_PORT): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_PORT].strip()
            version = await self._async_probe(port)
            if version is not None:
                return await self._async_finish(port, version)
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_PORT, default="/dev/ttyUSB0"): TextSelector()}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Move to another port, for example after replacing the USB stick."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_PORT].strip()
            version = await self._async_probe(port)
            if version is not None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=version.chip_id, data_updates={CONF_PORT: port}
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, "")): TextSelector()}),
            errors=errors,
        )


class ChipOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(key, default=bool(current[key])): BooleanSelector()
                    for key in (
                        CONF_AUTO_LABELS,
                        CONF_ROLE_LABELS,
                        CONF_NOTIFY_TEACH_IN,
                        CONF_ALLOW_SIMULATION,
                    )
                }
            ),
        )


def _describe(port: str, version: ChipVersion) -> str:
    name = version.description or "EnOcean module"
    return f"{name} on {port} (chip {version.chip_id}, firmware {version.app_version})"
