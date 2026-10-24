"""The panel API: access rules, devices, log, simulation, gateway address."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.enocean_internal_chip import simulator
from custom_components.enocean_internal_chip.const import CONF_ALLOW_SIMULATION

BASE = "/api/enocean_internal_chip"


@pytest.fixture
async def client(hass: HomeAssistant, setup_integration: MockConfigEntry, hass_client):
    return await hass_client()


async def test_everything_needs_an_administrator(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_client,
    hass_client_no_auth,
    hass_read_only_access_token,
) -> None:
    anonymous = await hass_client_no_auth()
    assert (await anonymous.get(f"{BASE}/status")).status == HTTPStatus.UNAUTHORIZED
    reader = await hass_client(hass_read_only_access_token)
    for method, path in (
        ("get", "status"),
        ("get", "devices"),
        ("post", "listen/start"),
        ("get", "telegrams"),
        ("delete", "telegrams"),
        ("get", "gateway"),
        ("post", "gateway/address"),
        ("post", "simulate"),
    ):
        response = await getattr(reader, method)(f"{BASE}/{path}")
        assert response.status == HTTPStatus.UNAUTHORIZED, path


async def test_status(client) -> None:
    response = await client.get(f"{BASE}/status")
    assert response.status == HTTPStatus.OK
    data = await response.json()
    assert data["gateway"]["ready"] is True
    assert data["gateway"]["base_id"] == "FF:A1:B2:00"
    assert data["gateway"]["chip"]["chip_id"] == "018A3B4C"
    assert data["pairing"]["open"] is False
    assert data["options"]["allow_simulation"] is False


async def test_adopt_edit_and_remove(hass: HomeAssistant, client, fake_module) -> None:
    response = await client.post(f"{BASE}/devices", json={"id": "05-00-00-10", "name": "Front door", "type": "contact"})
    assert response.status == HTTPStatus.CREATED
    device = (await response.json())["device"]
    assert device["id"] == "05:00:00:10"
    assert device["eep"] == "D5-00-01"
    await hass.async_block_till_done()

    response = await client.post(f"{BASE}/devices", json={"id": "05:00:00:10", "name": "Again", "type": "contact"})
    assert response.status == HTTPStatus.BAD_REQUEST
    assert "already adopted" in (await response.json())["message"]

    response = await client.post(f"{BASE}/devices", json={"id": "xx", "name": "Bad", "type": "contact"})
    assert response.status == HTTPStatus.BAD_REQUEST

    response = await client.patch(f"{BASE}/devices/05000010", json={"name": "Back door"})
    assert (await response.json())["device"]["display_name"] == "Back door"

    listing = await (await client.get(f"{BASE}/devices")).json()
    assert [item["display_name"] for item in listing["devices"]] == ["Back door"]
    assert len(listing["devices"][0]["entities"]) == 1

    # Removing takes the entities along, so it has to be asked for explicitly.
    response = await client.delete(f"{BASE}/devices/05000010")
    assert response.status == HTTPStatus.CONFLICT
    assert (await response.json())["entities"] == 1
    response = await client.delete(f"{BASE}/devices/05000010?entities=remove")
    assert response.status == HTTPStatus.OK
    assert (await response.json())["entities"] == 1
    listing = await (await client.get(f"{BASE}/devices")).json()
    assert listing["devices"] == []


async def test_types_and_areas(client) -> None:
    types = await (await client.get(f"{BASE}/types")).json()
    assert any(item["key"] == "window_sensor" and item["verified"] for item in types["types"])
    assert {"key": "cover", "name": "Covers"} in types["categories"]
    areas = await (await client.get(f"{BASE}/areas")).json()
    assert isinstance(areas["areas"], list)


async def test_nearby_list(hass: HomeAssistant, client, fake_module) -> None:
    fake_module.receive(simulator.build("contact_open"))
    await hass.async_block_till_done()
    heard = await (await client.get(f"{BASE}/heard")).json()
    assert heard["devices"] == []

    await client.post(f"{BASE}/listen/start")
    fake_module.receive(simulator.build("contact_open"))
    await hass.async_block_till_done()
    heard = await (await client.get(f"{BASE}/heard")).json()
    assert [item["id"] for item in heard["devices"]] == ["05:00:00:10"]

    mute = await (await client.post(f"{BASE}/heard/05000010/mute")).json()
    assert mute["muted"] is True

    snapshot = await (await client.post(f"{BASE}/listen/snapshot")).json()
    assert snapshot == {"listening": True, "snapshot": True, "heard": 0}
    fake_module.receive(simulator.build("temperature"))
    await hass.async_block_till_done()
    heard = await (await client.get(f"{BASE}/heard")).json()
    assert heard["devices"][0]["new_since_snapshot"] is True

    forget = await (await client.post(f"{BASE}/heard/05000020/forget")).json()
    assert forget["forgotten"] is True
    assert (await client.post(f"{BASE}/listen/bogus")).status == HTTPStatus.NOT_FOUND


async def test_telegram_log(hass: HomeAssistant, client, fake_module) -> None:
    for _ in range(5):
        fake_module.receive(simulator.build("temperature"))
    fake_module.receive(simulator.build("contact_open"))
    await hass.async_block_till_done()
    page = await (await client.get(f"{BASE}/telegrams?limit=2")).json()
    assert page["total"] == 6
    assert len(page["telegrams"]) == 2
    assert page["telegrams"][0]["sender"] == "05:00:00:10"  # newest first
    page = await (await client.get(f"{BASE}/telegrams?sender=05000020&offset=4")).json()
    assert page["total"] == 5
    assert len(page["telegrams"]) == 1
    page = await (await client.get(f"{BASE}/telegrams?limit=100000")).json()
    assert page["limit"] == 500
    cleared = await (await client.delete(f"{BASE}/telegrams")).json()
    assert cleared["cleared"] == 6


async def test_simulation_needs_the_option(
    hass: HomeAssistant, client, setup_integration: MockConfigEntry, fake_module
) -> None:
    response = await client.post(f"{BASE}/simulate", json={"preset": "temperature"})
    assert response.status == HTTPStatus.BAD_REQUEST
    hass.config_entries.async_update_entry(setup_integration, options={CONF_ALLOW_SIMULATION: True})
    response = await client.post(f"{BASE}/simulate", json={"preset": "temperature", "sender": "01:02:03:04"})
    assert (await response.json())["sent"] == "01:02:03:04"
    response = await client.post(
        f"{BASE}/simulate", json={"rorg": "A5", "data": "00 00 76 08", "status": "00", "sender": "01020305"}
    )
    assert response.status == HTTPStatus.OK
    response = await client.post(f"{BASE}/simulate", json={"rorg": "A5", "data": "zz", "sender": "01020305"})
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_pairing_window(client) -> None:
    data = await (await client.post(f"{BASE}/gateway/pairing", json={"action": "open", "seconds": 60})).json()
    assert data["pairing"]["open"] is True
    assert 55 <= data["pairing"]["seconds"] <= 60
    data = await (await client.post(f"{BASE}/gateway/pairing", json={"action": "close"})).json()
    assert data["pairing"]["open"] is False


async def address(client, **body):
    response = await client.post(f"{BASE}/gateway/address", json=body)
    return response.status, await response.json()


async def test_gateway_address_rules(client, fake_module) -> None:
    status, data = await address(client, action="write", address="FF:C0:00:00", expected_current="FF:A1:B2:00")
    assert status == HTTPStatus.BAD_REQUEST
    assert "locked" in data["message"]

    status, data = await address(client, action="unlock")
    assert data["address"]["unlocked"] is True

    for bad in ("FF:7F:00:00", "00:00:00:00", "FF:C0:00:01"):
        status, data = await address(client, action="write", address=bad, expected_current="FF:A1:B2:00")
        assert status == HTTPStatus.BAD_REQUEST, bad

    status, data = await address(client, action="write", address="FF:C0:00:00", expected_current="FF:00:00:00")
    assert status == HTTPStatus.CONFLICT

    status, data = await address(
        client, action="write", address="FF:C0:00:00", expected_current="FF:A1:B2:00", note="Second gateway"
    )
    assert status == HTTPStatus.OK
    assert data["success"] is True
    assert data["current"] == "FF:C0:00:00"
    assert data["remaining_writes"] == 9
    assert fake_module.base_id == bytes.fromhex("FFC00000")
    assert data["address"]["uses_foreign_address"] is True
    assert data["address"]["history"][0]["note"] == "Second gateway"

    status, data = await address(client, action="restore", expected_current="FF:C0:00:00")
    assert data["success"] is True
    assert fake_module.base_id == bytes.fromhex("FFA1B200")
    assert data["address"]["uses_foreign_address"] is False


async def test_gateway_address_keeps_a_reserve(client, fake_module, setup_integration: MockConfigEntry) -> None:
    fake_module.remaining = 2
    await setup_integration.runtime_data.gateway.async_refresh_base_id()
    await address(client, action="unlock")
    status, data = await address(client, action="write", address="FF:C0:00:00", expected_current="FF:A1:B2:00")
    assert status == HTTPStatus.BAD_REQUEST
    assert "only 2 more changes" in data["message"]


async def test_gateway_address_asks_for_the_word(client, fake_module, setup_integration: MockConfigEntry) -> None:
    ledger = setup_integration.runtime_data.ledger
    ledger.data["history"] = [{"at": "2026-01-01T00:00:00+00:00", "from": "a", "to": "b"}] * 5
    await address(client, action="unlock")
    status, data = await address(client, action="write", address="FF:C0:00:00", expected_current="FF:A1:B2:00")
    assert status == HTTPStatus.BAD_REQUEST
    assert data["needs_confirmation"] is True
    status, data = await address(
        client, action="write", address="FF:C0:00:00", expected_current="FF:A1:B2:00", confirm="CONTINUE"
    )
    assert data["success"] is True


async def test_device_pairing_endpoint(hass: HomeAssistant, client, fake_module) -> None:
    await client.post(f"{BASE}/devices", json={"id": "05:00:00:30", "name": "Plug", "type": "switch_actuator"})
    await hass.async_block_till_done()
    data = await (await client.post(f"{BASE}/devices/05000030/pair")).json()
    assert data["result"] == "window_open"
    assert data["pairing"]["device_id"] == "05:00:00:30"
    response = await client.post(f"{BASE}/devices/09090909/pair")
    assert response.status == HTTPStatus.BAD_REQUEST
