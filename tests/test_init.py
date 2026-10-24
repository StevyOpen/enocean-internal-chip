"""Setup, entities, pairing and labels with a fake module."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr

from custom_components.enocean_internal_chip import simulator
from custom_components.enocean_internal_chip.const import CONF_ALLOW_SIMULATION, DOMAIN, EVENT_NO_FEEDBACK
from custom_components.enocean_internal_chip.esp3 import RORG_4BS, RORG_RPS, RORG_UTE, RORG_VLD, parse_id

from .conftest import FakeModule


def hub_of(entry: MockConfigEntry):
    return entry.runtime_data


async def receive(hass: HomeAssistant, module: FakeModule, preset: str, sender: str | None = None) -> None:
    module.receive(simulator.build(preset, sender))
    await hass.async_block_till_done()


def entity_id_for(hass: HomeAssistant, device_id: str, kind: str, platform: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{device_id.replace(':', '')}-{kind}")
    assert entity_id, f"no {platform} entity for {kind}"
    return entity_id


async def test_setup_reads_the_module(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    assert setup_integration.state is ConfigEntryState.LOADED
    assert hub.gateway.base_id == bytes.fromhex("FFA1B200")
    assert hub.gateway.remaining_writes == 10
    assert hub.gateway.version.description == "GATEWAYCTRL"
    gateway = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, hub.gateway_identifier)})
    assert gateway is not None
    assert gateway.sw_version == "2.11.1.0"
    assert hub.ledger.data["original"] == "FF:A1:B2:00"


async def test_setup_retries_when_the_port_fails(
    hass: HomeAssistant, fake_module: FakeModule, config_entry: MockConfigEntry, no_frontend
) -> None:
    fake_module.fail_open = True
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, setup_integration: MockConfigEntry, no_frontend) -> None:
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    assert setup_integration.state is ConfigEntryState.NOT_LOADED
    no_frontend.assert_awaited_once()


async def test_sensor_device(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:21", name="Living room", type_key="temperature_humidity", eep="A5-04-01")
    await hass.async_block_till_done()
    temperature = entity_id_for(hass, "05:00:00:21", "temperature", "sensor")
    humidity = entity_id_for(hass, "05:00:00:21", "humidity", "sensor")
    assert hass.states.get(temperature).state == STATE_UNKNOWN

    await receive(hass, fake_module, "temperature_humidity")
    assert float(hass.states.get(temperature).state) == 22.4
    assert float(hass.states.get(humidity).state) == 55.2

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "05:00:00:21")})
    assert device.name == "Living room"
    assert device.model_id == "A5-04-01"
    assert device.via_device_id is not None


async def test_rocker_events_and_state(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:01", name="Hall switch", type_key="rocker_switch")
    await hass.async_block_till_done()
    event_a = entity_id_for(hass, "05:00:00:01", "rocker_event_a", "event")
    state_a = entity_id_for(hass, "05:00:00:01", "rocker_state_a", "binary_sensor")

    await receive(hass, fake_module, "rocker_a0")
    assert hass.states.get(event_a).attributes["event_type"] == "press_0"
    assert hass.states.get(state_a).state == STATE_ON
    await receive(hass, fake_module, "rocker_release")
    assert hass.states.get(event_a).attributes["event_type"] == "release_0"
    await receive(hass, fake_module, "rocker_ai")
    assert hass.states.get(state_a).state == STATE_OFF


async def test_window_sensor_and_labels(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:11", name="Bedroom window", type_key="window_sensor_vibration")
    await hass.async_block_till_done()
    position = entity_id_for(hass, "05:00:00:11", "window_position", "sensor")
    opening = entity_id_for(hass, "05:00:00:11", "opening_window", "binary_sensor")
    vibration = entity_id_for(hass, "05:00:00:11", "vibration", "binary_sensor")

    await receive(hass, fake_module, "window_tilted")
    assert hass.states.get(position).state == "tilted"
    assert hass.states.get(opening).state == STATE_ON
    await receive(hass, fake_module, "window_vibration")
    assert hass.states.get(position).state == "closed"
    assert hass.states.get(vibration).state == STATE_ON

    labels = lr.async_get(hass)
    entry = er.async_get(hass).async_get(opening)
    names = {labels.async_get_label(label_id).name for label_id in entry.labels}
    assert names == {"Doors & windows", "Perimeter"}
    voltage = er.async_get(hass).async_get(entity_id_for(hass, "05:00:00:11", "supply_voltage", "sensor"))
    assert {labels.async_get_label(label_id).name for label_id in voltage.labels} == {"Device health"}
    assert voltage.disabled_by is not None


async def test_labels_respect_options(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hass.config_entries.async_update_entry(setup_integration, options={"auto_labels": False})
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:10", name="Door", type_key="contact")
    await hass.async_block_till_done()
    entry = er.async_get(hass).async_get(entity_id_for(hass, "05:00:00:10", "opening", "binary_sensor"))
    assert not entry.labels


async def test_teach_in_notification_and_monitor(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module
) -> None:
    hub = hub_of(setup_integration)
    await receive(hass, fake_module, "teach_in_4bs")
    notifications = hass.data["persistent_notification"]
    assert any("05:00:00:41" in item["message"] for item in notifications.values())
    # Not listening: the teach-in is logged but the transmitter does not join the nearby list.
    assert hub.monitor.telegram_list(10)[0]["teach_in"] == "4BS"
    assert "05:00:00:41" not in hub.monitor.heard
    hub.monitor.listening = True
    await receive(hass, fake_module, "teach_in_4bs")
    assert hub.monitor.heard["05:00:00:41"]["eep"] == "A5-02-05"
    assert hub.monitor.heard["05:00:00:41"]["suggested_type"] == "temperature"


async def test_ute_answer_only_while_pairing_is_open(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module
) -> None:
    hub = hub_of(setup_integration)
    await receive(hass, fake_module, "teach_in_ute")
    assert fake_module.radio_sent(RORG_UTE) == []

    hub.open_pairing(60)
    await receive(hass, fake_module, "teach_in_ute")
    answers = fake_module.radio_sent(RORG_UTE)
    assert len(answers) == 1
    answer = answers[0]
    assert answer.payload[0] == 0x91
    assert answer.payload[1:] == simulator.build("teach_in_ute").payload[1:]
    assert answer.sender == bytes.fromhex("FFA1B200")
    assert answer.destination == parse_id("05:00:00:40")

    # A window opened for another device does not answer this one.
    hub.open_pairing(60, device_id="01:02:03:04")
    await receive(hass, fake_module, "teach_in_ute")
    assert len(fake_module.radio_sent(RORG_UTE)) == 1


async def test_adopted_device_is_always_answered(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module
) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:40", name="Plug", type_key="switch_actuator", eep="D2-01-12")
    await receive(hass, fake_module, "teach_in_ute")
    assert len(fake_module.radio_sent(RORG_UTE)) == 1


async def test_two_way_switch(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:30", name="Plug", type_key="switch_actuator", eep="D2-01-12")
    await hass.async_block_till_done()
    switch = entity_id_for(hass, "05:00:00:30", "switch_vld", "switch")
    # The status was asked for as soon as the entity was added.
    query = fake_module.radio_sent(RORG_VLD)[-1]
    assert query.payload == bytes([0x03, 0x00])
    assert query.destination == parse_id("05:00:00:30")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    command = fake_module.radio_sent(RORG_VLD)[-1]
    assert command.payload == bytes([0x01, 0x00, 0x64])
    assert command.status == 0x8F
    assert hass.states.get(switch).attributes["command_state"] == "sending"

    await receive(hass, fake_module, "switch_on")
    assert hass.states.get(switch).state == STATE_ON
    assert hass.states.get(switch).attributes["command_state"] == "confirmed"

    # On-demand entities appear with the first reading.
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "05000030-power") is None
    await receive(hass, fake_module, "switch_power")
    power = entity_id_for(hass, "05:00:00:30", "power", "sensor")
    assert float(hass.states.get(power).state) == 120


async def test_two_way_switch_without_feedback(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.enocean_internal_chip.switch.VERIFY_TIMEOUT", 0.01)
    events = async_capture_events(hass, EVENT_NO_FEEDBACK)
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:30", name="Plug", type_key="switch_actuator", eep="D2-01-12")
    await hass.async_block_till_done()
    switch = entity_id_for(hass, "05:00:00:30", "switch_vld", "switch")
    before = len(fake_module.radio_sent(RORG_VLD))
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    for _ in range(10):
        await hass.async_block_till_done()
        if events:
            break
        await __import__("asyncio").sleep(0.02)
    assert len(fake_module.radio_sent(RORG_VLD)) - before == 3  # first try and two retries
    assert hass.states.get(switch).attributes["command_state"] == "no_feedback"
    assert events[0].data["attempts"] == 3


async def test_one_way_actuators_get_their_own_sender(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module
) -> None:
    hub = hub_of(setup_integration)
    first = await hub.async_adopt("00:00:00:01", name="Relay 1", type_key="rocker_actuator")
    second = await hub.async_adopt("00:00:00:02", name="Dimmer", type_key="dimmer")
    await hass.async_block_till_done()
    assert first.sender == bytes.fromhex("FFA1B201")
    assert second.sender == bytes.fromhex("FFA1B202")

    result = await hub.async_pair("00:00:00:01")
    assert result["result"] == "sent"
    presses = fake_module.radio_sent(RORG_RPS)
    assert {packet.sender for packet in presses} == {bytes.fromhex("FFA1B201")}

    await hub.async_pair("00:00:00:02")
    teach_in = fake_module.radio_sent(RORG_4BS)[-1]
    assert teach_in.payload == bytes([0x38 << 2 | 0x08 >> 5, (0x08 << 3) & 0xF8 | 0x07, 0xFF, 0x80])
    assert teach_in.sender == bytes.fromhex("FFA1B202")

    light = entity_id_for(hass, "00:00:00:02", "dimmer", "light")
    await hass.services.async_call("light", "turn_on", {"entity_id": light, "brightness": 128}, blocking=True)
    assert fake_module.radio_sent(RORG_4BS)[-1].payload == bytes([0x02, 50, 0x01, 0x09])
    assert hass.states.get(light).state == STATE_ON


async def test_cover(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:31", name="Shutter", type_key="cover_actuator")
    await hass.async_block_till_done()
    cover = entity_id_for(hass, "05:00:00:31", "cover_vld", "cover")
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 30}, blocking=True)
    assert fake_module.radio_sent(RORG_VLD)[-1].payload == bytes([70, 0x00, 0x00, 0x01])
    await receive(hass, fake_module, "cover_position")
    assert hass.states.get(cover).attributes["current_position"] == 40


async def test_change_type_removes_old_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module
) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:22", name="Sensor", type_key="motion_sensor", eep="A5-07-01")
    await hass.async_block_till_done()
    motion = entity_id_for(hass, "05:00:00:22", "motion", "binary_sensor")
    await hub.async_update("05:00:00:22", type_key="temperature", eep="A5-02-05", name="Renamed")
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(motion) is None
    assert entity_id_for(hass, "05:00:00:22", "temperature", "sensor")
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "05:00:00:22")})
    assert device.name == "Renamed"
    assert device.model_id == "A5-02-05"


async def test_remove_device(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:10", name="Door", type_key="contact")
    await hass.async_block_till_done()
    assert hub.entity_count("05:00:00:10") == 1
    await hub.async_remove("05:00:00:10")
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "05:00:00:10")}) is None
    assert "05:00:00:10" not in hub.store.devices


async def test_simulate_service(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "simulate", {"preset": "contact_open"}, blocking=True)
    hass.config_entries.async_update_entry(setup_integration, options={CONF_ALLOW_SIMULATION: True})
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:10", name="Door", type_key="contact")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "simulate", {"preset": "contact_open"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id_for(hass, "05:00:00:10", "opening", "binary_sensor")).state == STATE_ON
    assert hub.monitor.telegram_list(1)[0]["simulated"] is True


async def test_state_survives_a_restart(hass: HomeAssistant, setup_integration: MockConfigEntry, fake_module) -> None:
    hub = hub_of(setup_integration)
    await hub.async_adopt("05:00:00:20", name="Thermometer", type_key="temperature")
    await hass.async_block_till_done()
    await receive(hass, fake_module, "temperature")
    sensor = entity_id_for(hass, "05:00:00:20", "temperature", "sensor")
    assert float(hass.states.get(sensor).state) == 21.5
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(sensor).state) == 21.5
    assert "05:00:00:20" in hub_of(setup_integration).devices
