"""Shared fixtures: a fake EnOcean module in place of the serial port."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import serial

from homeassistant.core import HomeAssistant

from custom_components.enocean_internal_chip.const import CONF_PORT, DOMAIN
from custom_components.enocean_internal_chip.esp3 import (
    CO_RD_IDBASE,
    CO_RD_VERSION,
    CO_WR_IDBASE,
    COMMON_COMMAND,
    RADIO_ERP1,
    RET_OK,
    Esp3Packet,
    FrameParser,
    response_packet,
)

PORT = "/dev/ttyFAKE0"
CHIP_ID = "018A3B4C"

VERSION_DATA = (
    bytes([2, 11, 1, 0])  # app version
    + bytes([2, 6, 3, 0])  # API version
    + bytes.fromhex(CHIP_ID)
    + bytes([0x45, 0x4F, 0x01, 0x03])  # chip version
    + b"GATEWAYCTRL".ljust(16, b"\x00")
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load integrations from custom_components in every test."""


class FakeModule:
    """What the EnOcean module on the other side of the serial port does."""

    def __init__(self) -> None:
        self.base_id = bytes.fromhex("FFA1B200")
        self.remaining = 10
        self.answer_version = True
        self.fail_open = False
        self.sent: list[Esp3Packet] = []
        self.links: list[FakeLink] = []

    @property
    def link(self) -> FakeLink:
        return self.links[-1]

    def receive(self, packet: Esp3Packet) -> None:
        """A radio telegram arrives from the air."""
        self.link.on_packet(packet)

    def radio_sent(self, rorg: int | None = None) -> list[Esp3Packet]:
        return [packet for packet in self.sent if rorg is None or packet.rorg == rorg]


class FakeLink:
    module: FakeModule

    def __init__(self, port, on_packet, on_lost) -> None:
        self.port = port
        self.on_packet = on_packet
        self.on_lost = on_lost
        self.parser = FrameParser()
        self.module.links.append(self)

    def open(self) -> None:
        if self.module.fail_open:
            raise serial.SerialException(f"could not open port {self.port}")

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass

    def write(self, frame: bytes) -> None:
        for packet in self.parser.feed(frame):
            self._handle(packet)

    def _handle(self, packet: Esp3Packet) -> None:
        module = self.module
        if packet.packet_type == COMMON_COMMAND:
            code = packet.data[0]
            if code == CO_RD_VERSION:
                if module.answer_version:
                    self.on_packet(response_packet(RET_OK, VERSION_DATA))
            elif code == CO_RD_IDBASE:
                self.on_packet(response_packet(RET_OK, module.base_id, bytes([module.remaining])))
            elif code == CO_WR_IDBASE:
                if module.remaining <= 0:
                    self.on_packet(response_packet(0x91))
                else:
                    module.base_id = bytes(packet.data[1:5])
                    module.remaining -= 1
                    self.on_packet(response_packet(RET_OK))
            else:
                self.on_packet(response_packet(0x02))  # not supported
        elif packet.packet_type == RADIO_ERP1:
            module.sent.append(packet)
            self.on_packet(response_packet(RET_OK))


@pytest.fixture
def fake_module() -> Generator[FakeModule]:
    module = FakeModule()
    link_class = type("BoundFakeLink", (FakeLink,), {"module": module})
    with patch("custom_components.enocean_internal_chip.gateway.SerialLink", link_class):
        yield module


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={CONF_PORT: PORT}, unique_id=CHIP_ID, title="EnOcean Internal Chip")


@pytest.fixture
def no_frontend(hass: HomeAssistant) -> Generator[AsyncMock]:
    """Skip the real frontend: tests only need the HTTP views."""
    hass.config.components.update({"frontend", "panel_custom"})
    with (
        patch("homeassistant.components.panel_custom.async_register_panel", new=AsyncMock()) as panel,
        patch("homeassistant.components.frontend.async_remove_panel"),
    ):
        yield panel


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, fake_module: FakeModule, config_entry: MockConfigEntry, no_frontend: AsyncMock
) -> MockConfigEntry:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
