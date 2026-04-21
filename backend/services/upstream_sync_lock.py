"""
Delta Engine — Per-Sport Upstream Sync Lock
============================================
Phase D4 (2026-04-21). Sport-scoped coordination primitive between the
full-sync pipeline and the near-real-time delta engine.

Semantics
---------
* FULL SYNC acquires `exclusive(sport)` — blocks ALL delta ticks for
  THAT sport while held. Other sports are unaffected.
* DELTA TICK calls `try_acquire_tick(sport)` — returns False immediately
  if the sport's full sync is in flight. Delta cleanly skips and retries
  on the next cadence (plan §7.1).
* Lock is in-process / per-event-loop (asyncio.Lock). Production-grade
  cross-replica coordination is deferred to D8+ (Mongo changestream lease
  or Redis lock) and is out of scope for D4.

Non-goals (intentionally NOT solved here)
-----------------------------------------
* Cross-process / cross-pod coordination — we only have one backend pod.
* Fair queuing — delta is cadence-driven and happy to skip.
* Read-write vs read-only distinction — full sync is the ONLY writer to
  the shared collections during its window; delta ticks always write a
  disjoint subset (the scored RT upserts keyed by (canonical_key, RT tag)).
  A simple exclusive/shared pattern suffices.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class UpstreamSyncLock:
    """Per-sport coordination primitive. Singleton via `get_upstream_sync_lock()`."""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._holders: Dict[str, Dict[str, Any]] = {}

    def _lock_for(self, sport: str) -> asyncio.Lock:
        """Return (creating if necessary) the asyncio.Lock for `sport`."""
        if sport not in self._locks:
            self._locks[sport] = asyncio.Lock()
        return self._locks[sport]

    @asynccontextmanager
    async def exclusive(self, sport: str, *, holder: Optional[str] = None):
        """Context manager acquired by the FULL SYNC pipeline.

        While held: delta ticks for `sport` are blocked via
        `try_acquire_tick(sport)` → False.

        Example:
            async with lock.exclusive("mlb", holder="master_sync"):
                await pipeline.run_master_sync()
        """
        lock = self._lock_for(sport)
        await lock.acquire()
        try:
            self._holders[sport] = {
                "holder": holder or "unknown",
                "acquired_at": datetime.now(timezone.utc),
            }
            logger.info(
                f"[UPSTREAM_LOCK:{sport}] EXCLUSIVE acquired by "
                f"{self._holders[sport]['holder']}"
            )
            yield
        finally:
            self._holders.pop(sport, None)
            lock.release()
            logger.info(f"[UPSTREAM_LOCK:{sport}] EXCLUSIVE released")

    def try_acquire_tick(self, sport: str) -> bool:
        """Delta-tick pre-gate. Returns True iff no full sync is holding
        the sport's exclusive lock.

        This is a READ on the lock's state — it does NOT actually acquire
        the lock (delta ticks write a disjoint subset of docs and can run
        freely in parallel with another delta tick for the same sport,
        though the DeltaEngine enforces per-sport serial ticks via its
        own lock).
        """
        lock = self._locks.get(sport)
        if lock is None:
            return True  # no lock created yet ⇒ nothing holds it
        return not lock.locked()

    def is_held(self, sport: str) -> bool:
        """Inverse of `try_acquire_tick` — convenience for observability."""
        return not self.try_acquire_tick(sport)

    def describe(self, sport: Optional[str] = None) -> Dict[str, Any]:
        """Inspect current lock state for one sport or all sports."""
        if sport is not None:
            h = self._holders.get(sport) or {}
            return {
                "sport": sport,
                "held": self.is_held(sport),
                "holder": h.get("holder"),
                "acquired_at": h.get("acquired_at"),
            }
        return {s: self.describe(s) for s in self._locks.keys()}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_singleton: Optional[UpstreamSyncLock] = None


def get_upstream_sync_lock() -> UpstreamSyncLock:
    global _singleton
    if _singleton is None:
        _singleton = UpstreamSyncLock()
    return _singleton


__all__ = ["UpstreamSyncLock", "get_upstream_sync_lock"]
