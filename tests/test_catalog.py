"""Consistency between the catalog, the decoders, the platforms and the texts."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from homeassistant.const import Platform

from custom_components.enocean_internal_chip import (
    binary_sensor,
    event,
    http_api,
    labels,
    profiles,
    sensor,
    simulator,
)
from custom_components.enocean_internal_chip.catalog import (
    DEVICE_TYPES,
    KIND_PLATFORM,
    OWN_SENDER_PAIRING,
    PAIRING_UTE,
    get_type,
    suggest_type,
)

ROOT = Path(__file__).parent.parent / "custom_components" / "enocean_internal_chip"

ACTUATOR_KINDS = {"switch_vld", "switch_rps", "switch_central", "cover_vld", "dimmer"}


def test_keys_are_unique() -> None:
    keys = [device_type.key for device_type in DEVICE_TYPES]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("device_type", DEVICE_TYPES, ids=lambda t: t.key)
def test_every_profile_has_a_decoder(device_type) -> None:
    for eep in device_type.eeps:
        assert eep in profiles.DECODERS, f"{device_type.key}: no decoder for {eep}"


@pytest.mark.parametrize("device_type", DEVICE_TYPES, ids=lambda t: t.key)
def test_every_entity_has_a_platform_and_a_class(device_type) -> None:
    for spec in device_type.entities:
        platform = KIND_PLATFORM[spec.kind]
        if platform == Platform.SENSOR:
            assert spec.kind in sensor.DESCRIPTIONS
        elif platform == Platform.BINARY_SENSOR:
            assert spec.kind in binary_sensor.DESCRIPTIONS
        elif platform == Platform.EVENT:
            assert spec.kind in event.DESCRIPTIONS
        else:
            assert spec.kind in ACTUATOR_KINDS
        for eep in spec.eeps:
            assert eep in device_type.eeps


@pytest.mark.parametrize("device_type", DEVICE_TYPES, ids=lambda t: t.key)
def test_every_category_has_a_label_and_a_panel_group(device_type) -> None:
    assert device_type.category in labels.CATEGORY_LABELS
    assert device_type.category in dict(http_api.CATEGORIES)


def test_actuators_have_a_pairing_method() -> None:
    for device_type in DEVICE_TYPES:
        kinds = {spec.kind for spec in device_type.entities}
        if kinds & ACTUATOR_KINDS:
            assert device_type.pairing == PAIRING_UTE or device_type.pairing in OWN_SENDER_PAIRING


def test_suggested_type_prefers_what_the_device_sends() -> None:
    assert suggest_type("F6-02-01").key == "rocker_switch"
    assert suggest_type("a5-02-05").key == "temperature"
    assert suggest_type("D2-01-12").key == "switch_actuator"
    assert suggest_type("A5-38-08").pairing in OWN_SENDER_PAIRING
    assert suggest_type("FF-FF-FF") is None
    assert suggest_type(None) is None


def test_on_demand_entities_wait_for_their_reading() -> None:
    switch = get_type("switch_actuator")
    assert [spec.kind for spec in switch.entities_for("D2-01-12")] == ["switch_vld"]
    assert {spec.kind for spec in switch.entities_for("D2-01-12", {"power"})} == {"switch_vld", "power"}
    motion = get_type("motion_sensor")
    assert "illuminance" not in {spec.kind for spec in motion.entities_for("A5-07-01")}
    assert "illuminance" in {spec.kind for spec in motion.entities_for("A5-07-03")}


def test_translations_cover_every_translation_key() -> None:
    strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((ROOT / "translations" / "en.json").read_text(encoding="utf-8"))
    assert strings == english
    for platform, descriptions in (
        ("sensor", sensor.DESCRIPTIONS),
        ("binary_sensor", binary_sensor.DESCRIPTIONS),
        ("event", event.DESCRIPTIONS),
    ):
        for description in descriptions.values():
            if description.translation_key:
                assert description.translation_key in strings["entity"][platform], description.key
    for key in ("window_position", "handle_position"):
        assert set(strings["entity"]["sensor"][key]["state"]) == set(profiles.POSITIONS)
    event_states = strings["entity"]["event"]["button_event"]["state_attributes"]["event_type"]["state"]
    assert set(event_states) == set(profiles.BUTTON_ACTIONS.values())


def test_service_presets_match_the_simulator() -> None:
    services = (ROOT / "services.yaml").read_text(encoding="utf-8")
    listed = re.findall(r"^            - ([a-z0-9_]+)$", services, re.M)
    assert listed == [preset.key for preset in simulator.PRESETS]
    strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    assert set(strings["selector"]["preset"]["options"]) == set(simulator.PRESETS_BY_KEY)


def test_manifest() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["domain"] == "enocean_internal_chip"
