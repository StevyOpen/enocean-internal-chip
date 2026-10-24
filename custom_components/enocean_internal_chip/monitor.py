"""What the radio hears: transmitters nearby and a log of recent telegrams.

Receiving and looking for new devices are two different things:

* the radio is always received. Every telegram goes to the log, and adopted
  devices are updated whatever the panel shows;
* "listening for new devices" only decides whether a transmitter never heard
  before may join the list of nearby devices. Turning it off keeps the list
  clean (no neighbours' sensors) while the log keeps running.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from datetime import timedelta
import time
from typing import Any

from homeassistant.util import dt as dt_util

from .esp3 import RORG_NAMES, Esp3Packet

MAX_TELEGRAMS = 2000
TELEGRAM_DAYS = 7
MAX_HEARD = 500

#: Pairing requests close together that mean "the device is not taking the
#: answer". A device that accepts asks once, maybe twice; one that does not
#: keeps asking every few seconds until its learn mode times out.
REPEATED_REQUESTS = 4
REQUEST_WINDOW = 300


class RadioMonitor:
    def __init__(self) -> None:
        self.heard: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.telegrams: deque[dict[str, Any]] = deque(maxlen=MAX_TELEGRAMS)
        self.listening = False
        self.snapshot: set[str] | None = None
        self._requests: dict[str, list[float]] = {}

    # ── recording ──────────────────────────────────────────────────────

    def record(
        self,
        packet: Esp3Packet,
        *,
        summary: str,
        adopted: bool,
        eep: str | None = None,
        suggested_type: str | None = None,
        teach_in: str | None = None,
    ) -> None:
        """Log a telegram and update the nearby list.

        ``teach_in`` describes a teach-in telegram ("4BS", "UTE"), if it is one.
        ``eep`` is only passed when the telegram declares its profile.
        """
        now = dt_util.utcnow().isoformat(timespec="seconds")
        sender = packet.sender_id
        rorg = packet.rorg or 0
        self.telegrams.append(
            {
                "at": now,
                "sender": sender,
                "rorg": RORG_NAMES.get(rorg, f"0x{rorg:02X}"),
                "dbm": packet.dbm,
                "raw": packet.data.hex(" ").upper(),
                "summary": summary,
                "teach_in": teach_in,
                "adopted": adopted,
                "simulated": packet.simulated,
            }
        )
        self._prune()

        entry = self.heard.get(sender)
        if entry is None:
            if not (self.listening or adopted):
                return
            if len(self.heard) >= MAX_HEARD:
                self.heard.popitem(last=False)
            entry = self.heard[sender] = {
                "id": sender,
                "first_seen": now,
                "count": 0,
                "eep": None,
                "suggested_type": None,
            }
        entry["rorg"] = RORG_NAMES.get(rorg, f"0x{rorg:02X}")
        entry["last_seen"] = now
        entry["count"] = entry.get("count", 0) + 1
        entry["dbm"] = packet.dbm
        entry["summary"] = summary
        if teach_in:
            entry["teach_in"] = teach_in
        if eep:
            # The profile is only declared at teach-in. Data telegrams never
            # overwrite it: their bytes are measurements, not FUNC/TYPE.
            entry["eep"] = eep
            entry["suggested_type"] = suggested_type

    def seed(self, device_id: str, eep: str | None) -> None:
        """Put an adopted device in the list after a restart, before it transmits."""
        if device_id in self.heard:
            return
        self.heard[device_id] = {
            "id": device_id,
            "first_seen": None,
            "last_seen": None,
            "count": 0,
            "eep": eep,
            "suggested_type": None,
            "rorg": None,
            "dbm": None,
            "summary": "",
        }

    def _prune(self) -> None:
        if not self.telegrams:
            return
        limit = (dt_util.utcnow() - timedelta(days=TELEGRAM_DAYS)).isoformat(timespec="seconds")
        while self.telegrams and self.telegrams[0]["at"] < limit:
            self.telegrams.popleft()

    # ── pairing requests ───────────────────────────────────────────────

    def note_pairing_request(self, sender: str) -> int:
        now = time.monotonic()
        recent = [t for t in self._requests.get(sender, []) if now - t <= REQUEST_WINDOW]
        recent.append(now)
        self._requests[sender] = recent[-50:]
        return len(recent)

    def pairing_requests(self, sender: str) -> int:
        now = time.monotonic()
        return sum(1 for t in self._requests.get(sender, []) if now - t <= REQUEST_WINDOW)

    def keeps_asking(self, sender: str) -> bool:
        """Not a diagnosis, a hint: "paired at once" versus "asking in vain"."""
        return self.pairing_requests(sender) >= REPEATED_REQUESTS

    # ── views ──────────────────────────────────────────────────────────

    def telegram_list(self, limit: int) -> list[dict[str, Any]]:
        self._prune()
        entries = list(self.telegrams)[-limit:]
        entries.reverse()
        return entries

    def telegram_count(self) -> int:
        self._prune()
        return len(self.telegrams)

    def heard_list(self) -> list[dict[str, Any]]:
        devices = []
        for entry in self.heard.values():
            item = dict(entry)
            item["pairing_requests"] = self.pairing_requests(entry["id"])
            item["keeps_asking"] = self.keeps_asking(entry["id"])
            if self.snapshot is not None:
                item["new_since_snapshot"] = entry["id"] not in self.snapshot
            devices.append(item)
        return devices

    # ── list maintenance ───────────────────────────────────────────────

    def forget(self, device_id: str) -> bool:
        return self.heard.pop(device_id, None) is not None

    def clear(self, keep: set[str] | None = None) -> int:
        keep = keep or set()
        for device_id in [key for key in self.heard if key not in keep]:
            del self.heard[device_id]
        return len(self.heard)

    def take_snapshot(self, keep: set[str]) -> int:
        """Forget unadopted devices and treat the rest as known.

        Anything heard after this shows up as new: press the button of the
        device you are looking for and it is the only new one.
        """
        self.clear(keep)
        self.snapshot = set(self.heard)
        return len(self.snapshot)

    def drop_snapshot(self) -> None:
        self.snapshot = None

    def clear_telegrams(self) -> None:
        self.telegrams.clear()
