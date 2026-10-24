# Changelog

## 0.1.1 - 2026-10-24

- The integration has its own icon, in the `brand` folder. Home Assistant
  2026.3 and newer show it on the integration card.
- The HACS check runs without exceptions.

## 0.1.0 - 2026-10-24

First public release.

- Setup from the UI, with detection of ESP3 modules on the serial ports, and
  a reconfigure step to change port.
- Administrator panel with Devices, Nearby, Telegrams, Device types and
  Gateway tabs, also laid out for phones.
- 26 device types: buttons, rocker switches, contacts, window sensors and
  handles, motion, climate, air quality, smoke and CO, weather, meters,
  switching actuators and plugs, dimmers, blind actuators.
- UTE pairing for two-way devices, and a sender address of its own for each
  one-way actuator.
- Switching actuators checked after every command, with two retries and the
  `enocean_internal_chip_no_feedback` event.
- Guarded gateway address changes, with a record of the original address and
  of every change.
- Optional labels by type and by role.
- Simulated telegrams for testing, off by default.
- Diagnostics with device IDs replaced.
- `scripts/fake_module.py`, an ESP3 module emulator for trying the
  integration without hardware.
