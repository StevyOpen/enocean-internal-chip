"""Every simulated telegram must mean what its name says."""

from __future__ import annotations

import pytest

from custom_components.enocean_internal_chip import profiles, simulator
from custom_components.enocean_internal_chip.esp3 import RORG_4BS, RORG_UTE


@pytest.mark.parametrize("preset", simulator.PRESETS, ids=lambda p: p.key)
def test_presets_decode(preset: simulator.Preset) -> None:
    packet = simulator.build(preset.key)
    assert packet.sender_id == preset.default_sender
    assert packet.dbm == -simulator.SIMULATED_DBM
    if preset.rorg == RORG_UTE:
        assert profiles.parse_ute_request(packet.data)["eep"] == preset.eep
    elif preset.rorg == RORG_4BS and preset.key.startswith("teach_in"):
        assert profiles.parse_4bs_teach_in(packet.data)[0] == preset.eep
    else:
        assert profiles.decode(preset.eep, packet.data), preset.key


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("temperature", {"temperature": 21.5}),
        ("temperature_humidity", {"temperature": 22.4, "humidity": 55.2}),
        ("window_tilted", {"window_position": "tilted"}),
        ("handle_open", {"handle_position": "open"}),
        ("switch_power", {"power": 120}),
        ("switch_energy", {"energy": 12.5}),
        ("cover_position", {"cover": {"position": 40, "angle": None}}),
        ("meter_power", {"power": 1500.0}),
        ("meter_energy", {"energy": 1234.5}),
    ],
)
def test_preset_values(key: str, expected: dict) -> None:
    preset = simulator.PRESETS_BY_KEY[key]
    readings = profiles.decode(preset.eep, simulator.build(key).data)
    for name, value in expected.items():
        assert readings[name] == value


def test_custom_sender_and_errors() -> None:
    assert simulator.build("contact_open", "01-a2-b3-c4").sender_id == "01:A2:B3:C4"
    with pytest.raises(ValueError):
        simulator.build("nope")
    with pytest.raises(ValueError):
        simulator.build("contact_open", "xyz")
    with pytest.raises(ValueError):
        simulator.build_custom(0xA5, b"", "05:00:00:01")
    packet = simulator.build_custom(0xA5, bytes([0, 0, 118, 8]), "05:00:00:01")
    assert profiles.decode("A5-02-05", packet.data) == {"temperature": 21.5}
