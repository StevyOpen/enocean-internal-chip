"""Ledger of the gateway address (Base ID).

Why it exists
-------------
The Base ID is the address the gateway transmits with. Two-way actuators are
addressed by their own ID, but one-way actuators only remember the *sender*
they were taught with: they obey any gateway that transmits with the same
address. Changing the Base ID therefore changes which actuators listen to this
gateway, and two gateways with the same Base ID control the same actuators.

On top of that, the module allows only a small number of Base ID changes in
its whole life (usually 10). Once they are used up, the address is fixed for
good.

What it records
---------------
* the original address, captured the first time the integration reads it,
  before anything can overwrite it. The module only reports its *current*
  address, so this is the only place the original one survives;
* every change made from here: when, from, to, an optional note, and how
  many changes the module said were left afterwards;
* whether address changes are unlocked on this gateway. They are locked by
  default and need an administrator to unlock them.

A note of honesty: "original address" means the address the module had the
first time this integration read it. If the module had been rewritten before,
the ledger cannot know. That is why the date it was captured is stored too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.gateway_address"

#: Changes that are never spent on a new address: they stay available to go
#: back to the original one, and to retry if that write fails.
RESERVED_CHANGES = 2

#: After this many changes the panel asks to type a confirmation word.
CHANGES_BEFORE_CONFIRMATION = 5

CONFIRMATION_WORD = "CONTINUE"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AddressLedger:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "unlocked": False,
            "unlocked_at": None,
            "original": None,
            "original_seen_at": None,
            "current": None,
            "note": None,
            "history": [],
        }

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        self.data = self._empty()
        if isinstance(stored, dict):
            self.data.update(stored)
        if not isinstance(self.data.get("history"), list):
            self.data["history"] = []

    async def _async_save(self) -> None:
        await self._store.async_save(self.data)

    # ── reads ──────────────────────────────────────────────────────────

    @property
    def unlocked(self) -> bool:
        return bool(self.data.get("unlocked"))

    @property
    def changes_made(self) -> int:
        return len(self.data.get("history") or [])

    @property
    def needs_confirmation(self) -> bool:
        return self.changes_made >= CHANGES_BEFORE_CONFIRMATION

    @property
    def uses_foreign_address(self) -> bool:
        """True when the current address is not the original one.

        Unknown is not "foreign": with no original recorded this says False,
        otherwise every gateway would carry a warning nobody reads after a week.
        """
        original = self.data.get("original")
        current = self.data.get("current")
        return bool(original and current and original != current)

    # ── writes ─────────────────────────────────────────────────────────

    async def async_record_read(self, current: str | None) -> None:
        """Note the address the module reports, and keep the first one ever seen."""
        if not current:
            return
        changed = False
        if not self.data.get("original"):
            self.data["original"] = current
            self.data["original_seen_at"] = _now()
            changed = True
        if self.data.get("current") != current:
            # Changed from outside (another tool, or a module moved between
            # gateways): the ledger follows what the module says.
            self.data["current"] = current
            changed = True
        if changed:
            await self._async_save()

    async def async_record_write(
        self, *, previous: str | None, current: str, note: str | None, remaining: int | None
    ) -> None:
        self.data["current"] = current
        self.data["note"] = note
        self.data["history"] = [
            *self.data.get("history", []),
            {
                "at": _now(),
                "from": previous,
                "to": current,
                "note": note,
                "remaining_after": remaining,
            },
        ]
        await self._async_save()

    async def async_set_unlocked(self, unlocked: bool) -> None:
        self.data["unlocked"] = bool(unlocked)
        self.data["unlocked_at"] = _now() if unlocked else None
        await self._async_save()
