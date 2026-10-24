#!/usr/bin/env python3
"""An ESP3 module emulator on a pseudo-terminal, for trying the integration without hardware.

Linux and macOS only. Run it, then give the integration the port it prints
(or the --link path):

    python3 scripts/fake_module.py --link /tmp/enocean-fake --demo

It answers what the integration asks (version, gateway address read and
write) and acknowledges every radio telegram, printing what was sent. With
--demo it also "receives" a few telegrams every now and then, as if sensors
were nearby: a thermometer, a window contact and a rocker switch.

Type a preset name from the integration's simulator (for example
``window_open``) and press Enter to make it arrive at once.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import pty
import select
import sys
import time
import tty

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "enocean_internal_chip"


def _load(name: str):
    """Load a module of the integration by path, without importing Home Assistant."""
    spec = importlib.util.spec_from_file_location(f"eic_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


esp3 = _load("esp3")

DEMO = [
    # (seconds between telegrams, rorg, payload, sender, status)
    (20, esp3.RORG_4BS, [0x00, 0x00, 0x76, 0x08], "05:10:00:01", 0x00),  # 21.5 °C
    (45, esp3.RORG_1BS, [0x08], "05:10:00:02", 0x00),  # contact open
    (60, esp3.RORG_RPS, [0x30], "05:10:00:03", 0x30),  # rocker A0
]

VERSION = bytes([2, 11, 1, 0, 2, 6, 3, 0]) + bytes.fromhex("0F0E0D0C") + bytes(4) + b"FAKE ESP3".ljust(16, b"\x00")


class FakeModule:
    def __init__(self, base_id: bytes, remaining: int, verbose: bool) -> None:
        self.base_id = base_id
        self.remaining = remaining
        self.verbose = verbose
        self.master, slave = pty.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self._slave = slave  # keep it open, or the port disappears between connections
        self.parser = esp3.FrameParser()

    def log(self, text: str) -> None:
        print(time.strftime("%H:%M:%S"), text, flush=True)

    def write(self, packet) -> None:
        os.write(self.master, packet.encode())

    def receive(self, rorg: int, payload: list[int] | bytes, sender: str, status: int = 0) -> None:
        raw = esp3.parse_id(sender)
        self.write(esp3.radio_packet(rorg, payload, raw, status, subtel=1, dbm=0x45))
        self.log(f"radio in  {sender} {bytes([rorg, *payload]).hex(' ').upper()}")

    def handle(self, packet) -> None:
        if packet.packet_type == esp3.COMMON_COMMAND:
            code = packet.data[0]
            if code == esp3.CO_RD_VERSION:
                self.write(esp3.response_packet(esp3.RET_OK, VERSION))
                self.log("version asked")
            elif code == esp3.CO_RD_IDBASE:
                self.write(esp3.response_packet(esp3.RET_OK, self.base_id, bytes([self.remaining])))
                self.log(f"address asked: {esp3.format_id(self.base_id)}, {self.remaining} changes left")
            elif code == esp3.CO_WR_IDBASE:
                new = bytes(packet.data[1:5])
                if self.remaining <= 0:
                    self.write(esp3.response_packet(0x91))
                    self.log("address change refused: no changes left")
                else:
                    self.base_id = new
                    self.remaining -= 1
                    self.write(esp3.response_packet(esp3.RET_OK))
                    self.log(f"address changed to {esp3.format_id(new)}, {self.remaining} left")
            else:
                self.write(esp3.response_packet(0x02))
                self.log(f"command 0x{code:02X} not supported")
        elif packet.packet_type == esp3.RADIO_ERP1:
            self.write(esp3.response_packet(esp3.RET_OK))
            self.log(
                f"radio out from {packet.sender_id} to {esp3.format_id(packet.destination)}: "
                f"{packet.data[:-5].hex(' ').upper()} status {packet.status:02X}"
            )


def presets() -> dict:
    """Preset telegrams from the integration's simulator, if it can be loaded."""
    try:
        source = (
            (ROOT / "simulator.py").read_text(encoding="utf-8").replace("from .esp3 import", "from eic_esp3 import")
        )
        namespace: dict = {"__name__": "eic_simulator"}
        exec(compile(source, "simulator.py", "exec"), namespace)
        return namespace["PRESETS_BY_KEY"]
    except Exception as err:
        print(f"Presets not available: {err}", file=sys.stderr)
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--link", help="also make the port available at this path (a symbolic link)")
    parser.add_argument("--base-id", default="FFB4C300", help="gateway address to start with")
    parser.add_argument("--changes-left", type=int, default=10, help="address changes the module allows")
    parser.add_argument("--demo", action="store_true", help="receive a few sensor telegrams now and then")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    module = FakeModule(bytes.fromhex(args.base_id), args.changes_left, args.verbose)
    if args.link:
        link = Path(args.link)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(module.port)
    module.log(f"Fake ESP3 module on {module.port}" + (f" (also {args.link})" if args.link else ""))
    known = presets()
    if known:
        module.log(f"Type a preset name and Enter to receive it, e.g. {', '.join(list(known)[:4])}")

    next_demo = [time.monotonic() + 5 + index * 3 for index in range(len(DEMO))]
    watch = [module.master] + ([sys.stdin] if sys.stdin.isatty() or not sys.stdin.closed else [])
    try:
        while True:
            ready, _, _ = select.select(watch, [], [], 0.5)
            if module.master in ready:
                try:
                    chunk = os.read(module.master, 1024)
                except OSError:
                    chunk = b""
                for packet in module.parser.feed(chunk):
                    module.handle(packet)
            if sys.stdin in ready:
                line = sys.stdin.readline()
                if not line:
                    watch = [module.master]
                elif (preset := known.get(line.strip())) is not None:
                    module.receive(preset.rorg, preset.payload, preset.default_sender, preset.status)
                elif line.strip():
                    module.log(f"unknown preset: {line.strip()}")
            if args.demo:
                now = time.monotonic()
                for index, (every, rorg, payload, sender, status) in enumerate(DEMO):
                    if now >= next_demo[index]:
                        module.receive(rorg, payload, sender, status)
                        next_demo[index] = now + every
    except KeyboardInterrupt:
        pass
    finally:
        if args.link and Path(args.link).is_symlink():
            Path(args.link).unlink()


if __name__ == "__main__":
    main()
