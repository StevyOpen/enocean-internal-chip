"""The EnOcean module on the serial port.

``SerialLink`` is a small reader/writer thread with its own ESP3 framing.
``Gateway`` sits on top of it in the event loop: it sends commands and waits
for their responses, keeps the gateway address (Base ID) and reconnects when
the port goes away.

Every packet sent to the module gets exactly one RESPONSE back, in order. The
gateway keeps a queue of what it is waiting for, so a response always lands on
the request it belongs to, also when radio telegrams are sent in between.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import glob
import logging
import os
import queue
import threading
import time
from typing import Any

import serial

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .esp3 import (
    CO_RD_IDBASE,
    CO_RD_VERSION,
    CO_WR_IDBASE,
    EVENT,
    RADIO_ERP1,
    RESPONSE,
    RET_OK,
    RETURN_CODES,
    ChipVersion,
    Esp3Packet,
    FrameParser,
    common_command,
    format_id,
    id_to_int,
    int_to_id,
)

_LOGGER = logging.getLogger(__name__)

BAUD_RATE = 57600

#: Pause between two frames written to the module. Without it some USB sticks
#: drop telegrams when several are sent in a row.
INTER_FRAME_DELAY = 0.08

COMMAND_TIMEOUT = 2.0
RECONNECT_DELAY = 15
BASE_ID_RETRY_DELAY = 5

#: Responses older than this are considered lost, so one missing response
#: cannot shift every following answer onto the wrong request.
STALE_EXPECTATION = 5.0

#: Range of addresses the module accepts as its Base ID.
BASE_ID_MIN = 0xFF800000
BASE_ID_MAX = 0xFFFFFF80


class SerialLink(threading.Thread):
    """Blocking serial I/O in its own thread. Packets go out through callbacks."""

    def __init__(
        self,
        port: str,
        on_packet: Callable[[Esp3Packet], None],
        on_lost: Callable[[str], None],
    ) -> None:
        super().__init__(name=f"enocean-chip {port}", daemon=True)
        self._port = port
        self._on_packet = on_packet
        self._on_lost = on_lost
        self._outbox: queue.Queue[bytes] = queue.Queue()
        self._stop_flag = threading.Event()
        self._serial: serial.Serial | None = None
        self.parser = FrameParser()

    def open(self) -> None:
        """Open the port. Raises ``serial.SerialException`` on failure."""
        self._serial = serial.Serial(self._port, BAUD_RATE, timeout=0.05)

    def write(self, frame: bytes) -> None:
        self._outbox.put(frame)

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        assert self._serial is not None
        reason = "stopped"
        try:
            while not self._stop_flag.is_set():
                while True:
                    try:
                        frame = self._outbox.get_nowait()
                    except queue.Empty:
                        break
                    self._serial.write(frame)
                    time.sleep(INTER_FRAME_DELAY)
                chunk = self._serial.read(max(1, self._serial.in_waiting))
                if chunk:
                    for packet in self.parser.feed(chunk):
                        self._on_packet(packet)
        except (serial.SerialException, OSError) as err:
            reason = str(err) or err.__class__.__name__
        finally:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
        if not self._stop_flag.is_set():
            self._on_lost(reason)


class Gateway:
    """The EnOcean module, as the rest of the integration sees it."""

    def __init__(
        self,
        hass: HomeAssistant,
        port: str,
        on_radio: Callable[[Esp3Packet], None],
    ) -> None:
        self.hass = hass
        self.port = port
        self._on_radio = on_radio
        self._link: SerialLink | None = None
        self._expected: deque[tuple[asyncio.Future[Esp3Packet] | None, float]] = deque()
        self._command_lock = asyncio.Lock()
        self._status_listeners: list[Callable[[], None]] = []
        self._retry_unsub: CALLBACK_TYPE | None = None
        self._stopping = False

        self.connected = False
        self.last_error: str | None = None
        self.base_id: bytes | None = None
        #: Base ID changes the module still allows. ``None`` means the module
        #: did not say, which is not the same as zero.
        self.remaining_writes: int | None = None
        self.version: ChipVersion | None = None
        self.sent_frames = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Open the port and read the module's identity.

        Raises ``serial.SerialException`` / ``OSError`` if the port cannot be
        opened, so the config entry can retry later.
        """
        self._stopping = False
        await self._async_open()
        await self._async_identify()

    async def async_stop(self) -> None:
        self._stopping = True
        self._cancel_retry()
        link, self._link = self._link, None
        if link is not None:
            link.stop()
            await self.hass.async_add_executor_job(link.join, 2)
        self.connected = False
        for future, _ in self._expected:
            if future is not None and not future.done():
                future.cancel()
        self._expected.clear()

    async def _async_open(self) -> None:
        link = SerialLink(self.port, self._packet_from_thread, self._lost_from_thread)
        await self.hass.async_add_executor_job(link.open)
        link.start()
        self._link = link
        self.connected = True
        self.last_error = None
        self._notify()
        _LOGGER.info("EnOcean module connected on %s", self.port)

    async def _async_identify(self) -> None:
        try:
            response = await self.async_command(CO_RD_VERSION)
            if response.return_code == RET_OK:
                self.version = ChipVersion.from_response(response.response_data)
        except TimeoutError:
            _LOGGER.warning("The module on %s did not answer the version request", self.port)
        if not await self.async_refresh_base_id():
            self._schedule_retry(BASE_ID_RETRY_DELAY, self._async_retry_base_id)

    # ── status listeners ───────────────────────────────────────────────

    @callback
    def add_status_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._status_listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._status_listeners:
                self._status_listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._status_listeners):
            listener()

    # ── thread entry points ────────────────────────────────────────────

    def _packet_from_thread(self, packet: Esp3Packet) -> None:
        try:
            self.hass.loop.call_soon_threadsafe(self._handle_packet, packet)
        except RuntimeError:  # loop closed during shutdown
            pass

    def _lost_from_thread(self, reason: str) -> None:
        try:
            self.hass.loop.call_soon_threadsafe(self._handle_lost, reason)
        except RuntimeError:
            pass

    @callback
    def _handle_lost(self, reason: str) -> None:
        if self._stopping:
            return
        _LOGGER.error("Lost the EnOcean module on %s: %s", self.port, reason)
        self._link = None
        self.connected = False
        self.last_error = reason
        for future, _ in self._expected:
            if future is not None and not future.done():
                future.set_exception(ConnectionError(reason))
        self._expected.clear()
        self._notify()
        self._schedule_retry(RECONNECT_DELAY, self._async_reconnect)

    # ── retries ────────────────────────────────────────────────────────

    @callback
    def _schedule_retry(self, delay: float, job: Callable[[], Any]) -> None:
        self._cancel_retry()

        @callback
        def run(_now: Any) -> None:
            self._retry_unsub = None
            self.hass.async_create_background_task(job(), "enocean_internal_chip retry")

        self._retry_unsub = async_call_later(self.hass, delay, run)

    @callback
    def _cancel_retry(self) -> None:
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None

    async def _async_reconnect(self) -> None:
        if self._stopping or self.connected:
            return
        try:
            await self._async_open()
        except (serial.SerialException, OSError) as err:
            self.last_error = str(err)
            self._schedule_retry(RECONNECT_DELAY, self._async_reconnect)
            return
        await self._async_identify()

    async def _async_retry_base_id(self) -> None:
        if self._stopping or not self.connected or self.base_id:
            return
        if not await self.async_refresh_base_id():
            self._schedule_retry(BASE_ID_RETRY_DELAY, self._async_retry_base_id)

    # ── packets in ─────────────────────────────────────────────────────

    @callback
    def _handle_packet(self, packet: Esp3Packet) -> None:
        if packet.packet_type == RESPONSE:
            self._resolve(packet)
        elif packet.packet_type == RADIO_ERP1:
            self._on_radio(packet)
        elif packet.packet_type == EVENT:
            _LOGGER.debug("Module event: %s", packet.data.hex())

    @callback
    def _resolve(self, response: Esp3Packet) -> None:
        now = self.hass.loop.time()
        while self._expected and self._expected[0][1] < now - STALE_EXPECTATION:
            future, _ = self._expected.popleft()
            if future is not None and not future.done():
                future.set_exception(TimeoutError("response lost"))
        if not self._expected:
            return
        future, _ = self._expected.popleft()
        if future is not None and not future.done():
            future.set_result(response)

    # ── packets out ────────────────────────────────────────────────────

    @callback
    def _write(self, packet: Esp3Packet, future: asyncio.Future[Esp3Packet] | None) -> None:
        if self._link is None:
            raise ConnectionError(f"EnOcean module on {self.port} is not connected")
        self._expected.append((future, self.hass.loop.time()))
        self._link.write(packet.encode())
        self.sent_frames += 1

    @callback
    def send(self, packet: Esp3Packet) -> None:
        """Send a radio telegram. Raises ``ConnectionError`` when offline."""
        self._write(packet, None)

    async def async_command(self, code: int, args: bytes = b"", timeout: float = COMMAND_TIMEOUT) -> Esp3Packet:
        """Send a common command and return the module's response."""
        async with self._command_lock:
            future: asyncio.Future[Esp3Packet] = self.hass.loop.create_future()
            self._write(common_command(code, args), future)
            async with asyncio.timeout(timeout):
                return await future

    # ── gateway address ────────────────────────────────────────────────

    def sender_for(self, offset: int) -> bytes:
        """Sender ID ``offset`` positions after the Base ID (0..127)."""
        if self.base_id is None:
            raise ConnectionError("the gateway address is not known yet")
        if not 0 <= offset <= 127:
            raise ValueError("sender offset must be between 0 and 127")
        return int_to_id(id_to_int(self.base_id) + offset)

    async def async_refresh_base_id(self) -> bytes | None:
        """Ask the module for its current Base ID. ``None`` if it did not answer."""
        try:
            response = await self.async_command(CO_RD_IDBASE)
        except (TimeoutError, ConnectionError):
            return None
        data = response.response_data
        if response.return_code != RET_OK or len(data) < 4:
            return None
        changed = self.base_id != bytes(data[:4])
        self.base_id = bytes(data[:4])
        self.remaining_writes = response.optional[0] if response.optional else None
        if changed:
            _LOGGER.info(
                "Gateway address %s (address changes left: %s)",
                format_id(self.base_id),
                "unknown" if self.remaining_writes is None else self.remaining_writes,
            )
            self._notify()
        return self.base_id

    async def async_write_base_id(self, new_id: bytes) -> dict[str, Any]:
        """Write a new Base ID and read it back to know what really happened.

        The return code alone is not proof: the address is read again, and the
        write counts as done only if the module now reports the new address.
        """
        previous = self.base_id
        code: int | None = None
        try:
            response = await self.async_command(CO_WR_IDBASE, bytes(new_id), timeout=5)
            code = response.return_code
        except TimeoutError:
            code = None
        self.base_id = None
        read_back = None
        for _ in range(3):
            read_back = await self.async_refresh_base_id()
            if read_back is not None:
                break
            await asyncio.sleep(1)
        success = read_back == bytes(new_id)
        _LOGGER.warning(
            "Gateway address change %s -> %s: %s (code %s, changes left %s)",
            format_id(previous),
            format_id(new_id),
            "done" if success else "not done",
            code,
            self.remaining_writes,
        )
        self._notify()
        return {
            "success": success,
            "code": code,
            "reason": None if success else _describe_code(code),
            "previous": format_id(previous),
            "current": format_id(read_back),
            "remaining_writes": self.remaining_writes,
        }


def _describe_code(code: int | None) -> str:
    if code is None:
        return "the module did not answer the write request"
    if code == RET_OK:
        return "the module accepted the request but still reports the old address"
    return RETURN_CODES.get(code, f"the module answered with code 0x{code:02X}")


def valid_base_id(new_id: bytes) -> bool:
    """The module accepts Base IDs from FF:80:00:00 to FF:FF:FF:80."""
    return len(new_id) == 4 and BASE_ID_MIN <= id_to_int(new_id) <= BASE_ID_MAX


# ── serial ports ───────────────────────────────────────────────────────

CANDIDATE_GLOBS = (
    "/dev/serial/by-id/*EnOcean*",
    "/dev/serial/by-id/*FTDI*",
    "/dev/tty*FTOA2PV*",
    "/dev/tty.usbserial-*",
    "/dev/ttyAMA0",
    "/dev/ttyS0",
    "/dev/ttyUSB*",
)


def candidate_ports() -> list[str]:
    """Serial ports worth probing, most specific first, without duplicates."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in CANDIDATE_GLOBS:
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            found.append(path)
    return found


def probe(port: str, timeout: float = 1.5) -> ChipVersion | None:
    """Ask ``port`` for the module version. ``None`` if nothing answers.

    Blocking: run it in the executor.
    """
    try:
        with serial.Serial(port, BAUD_RATE, timeout=0.05) as link:
            link.reset_input_buffer()
            parser = FrameParser()
            for _attempt in range(2):
                link.write(common_command(CO_RD_VERSION).encode())
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    for packet in parser.feed(link.read(64)):
                        if packet.packet_type == RESPONSE and packet.return_code == RET_OK:
                            return ChipVersion.from_response(packet.response_data)
    except (serial.SerialException, OSError) as err:
        _LOGGER.debug("Probe of %s failed: %s", port, err)
    return None
