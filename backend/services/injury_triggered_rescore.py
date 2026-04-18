"""
Injury-Triggered Targeted Re-Score
===================================
Subscribes to `BoardEvent(event_type="injury_change", sport="nba")` on the
central event bus (published by `services.injury_sensor`) and recomputes
ONLY the affected props inside `nba_prop_scores` (version_tag='final-nba')
without touching the rest of the slate.

Upstream canonical source:
    injuries_normalized  (written by InjurySensor; merges BDL + ESPN + NBA
    Official; already dedupes, tier-levels, and publishes events)

Scope of impacted props per event (Phase 3 cut):
    1. the injured/returning player's own props  (direct effect)
    2. same-team players with props on the same slate  (teammate usage shift)

Not in scope (documented as Phase 3.5 follow-up):
    - opponent props (defensive assignment changes)
    - historical model retraining — per-event recompute uses existing VK2
      models unchanged.

Concurrency / safety:
    - All recompute work is serialized through a single asyncio.Lock so
      a recompute in progress never races with a second event.
    - Events for the same player within `_DEDUP_WINDOW_SEC` are coalesced
      to protect against bouncing sensor feeds.
    - The worker runs in a background task so the event bus publish path
      (i.e. InjurySensor._emit_changes) is never blocked.

Re-score path:
    Monkey-patches `NBAScoring.load_live_props` for the duration of ONE
    `recompute(sports=["nba"], version_tag="final-nba")` call, scoping the
    props query to only the impacted player set. The rest of the pipeline
    (VK2 models, empirical residual, gate stack, tier assignment, PP utility)
    is unchanged — so this produces bit-identical scores for the affected
    props as a full-slate recompute would. The monkey-patch is always
    reverted inside a try/finally.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from services.event_bus import BoardEvent, get_event_bus
from services.scoring import recompute as _recompute_mod

logger = logging.getLogger(__name__)


_VERSION_TAG = "final-nba"
_DEDUP_WINDOW_SEC = 10.0


class InjuryTriggeredRescore:
    """Owns the in-process queue + lock for targeted NBA re-scoring."""

    def __init__(self):
        self._queue: "asyncio.Queue[BoardEvent]" = asyncio.Queue()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._subscribed: bool = False
        self._recent: Dict[str, float] = {}  # player_name -> enqueue time
        self._db = None
        self._stats = {
            "events_received": 0,
            "events_coalesced": 0,
            "recomputes": 0,
            "props_rescored": 0,
            "failures": 0,
            "last_latency_ms": 0,
        }

    def start(self, db) -> None:
        """Launch the background worker + subscribe to event bus.
        Idempotent — safe on repeated calls."""
        self._db = db
        if not self._subscribed:
            bus = get_event_bus()
            bus.subscribe(self._on_event, event_types={"injury_change"})
            self._subscribed = True
            logger.info("[INJURY-RESCORE] subscribed to BoardEvent(injury_change)")
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("[INJURY-RESCORE] background worker started")

    async def _on_event(self, event: BoardEvent) -> None:
        """Event bus handler — filter to NBA material changes, enqueue."""
        if event.sport != "nba":
            return
        self._stats["events_received"] += 1
        # Only act on high-severity events (tier_delta >= 2 OR new_injury).
        # InjurySensor classifies return_shift/status ladder moves as medium;
        # these don't move usage distributions enough to justify a re-score.
        if event.severity != "high":
            logger.debug(
                f"[INJURY-RESCORE] ignoring medium-severity event: "
                f"players={event.affected_players[:3]}"
            )
            return

        now = datetime.now(timezone.utc).timestamp()
        # Coalesce bursts of events for the same player
        fresh_players: List[str] = []
        for p in event.affected_players:
            last = self._recent.get(p)
            if last is not None and (now - last) < _DEDUP_WINDOW_SEC:
                self._stats["events_coalesced"] += 1
                continue
            self._recent[p] = now
            fresh_players.append(p)
        # Evict old entries
        cutoff = now - 60.0
        self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        if not fresh_players:
            return

        # Repack with only the fresh players so the worker doesn't
        # double-process coalesced names.
        scoped = BoardEvent(
            sport=event.sport,
            event_type=event.event_type,
            severity=event.severity,
            affected_players=fresh_players,
            source=event.source,
            metadata={**event.metadata, "coalesced_from": len(event.affected_players)},
        )
        await self._queue.put(scoped)

    async def _worker(self) -> None:
        while True:
            try:
                evt = await self._queue.get()
                async with self._lock:
                    await self._handle(evt)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["failures"] += 1
                logger.exception(f"[INJURY-RESCORE] worker loop error: {e}")

    async def _resolve_impacted_players(self, event: BoardEvent) -> Set[str]:
        """Build the set of player names whose props must be rescored.

        Phase 3 scope: each flagged player + same-team players on the same
        slate (resolved via the event_id(s) they appear in on dg_live_props).
        Team lookup falls back to the BoardEvent.metadata team if set.
        """
        impacted: Set[str] = set(event.affected_players)
        if not impacted or self._db is None:
            return impacted

        # event.metadata includes {"team": ..., "changes": [...], "max_tier_delta": N}
        # per InjurySensor._emit_changes. Use team if present.
        hint_team = event.metadata.get("team")

        try:
            # Find event_ids of games these players participate in today
            event_ids: Set[str] = set()
            async for prop in self._db.dg_live_props.find(
                {"player_name": {"$in": list(impacted)}, "sport": "nba"},
                {"_id": 0, "event_id": 1, "team": 1},
            ):
                eid = prop.get("event_id")
                if eid:
                    event_ids.add(eid)

            if not event_ids:
                return impacted

            # Same-team players in those games
            query = {"sport": "nba", "event_id": {"$in": list(event_ids)}}
            if hint_team:
                query["team"] = hint_team
            async for prop in self._db.dg_live_props.find(
                query, {"_id": 0, "player_name": 1}
            ):
                pn = prop.get("player_name")
                if pn:
                    impacted.add(pn)
        except Exception as e:
            logger.warning(
                f"[INJURY-RESCORE] player resolver failed (falling back to "
                f"solo player): {e}"
            )

        return impacted

    async def _handle(self, event: BoardEvent) -> None:
        """Recompute the scores for all props belonging to impacted players.

        Scoping trick: temporarily monkey-patch NBAScoring.load_live_props
        to return ONLY the impacted subset. This reuses the full scoring
        pipeline so gates/VK2/PP utility stay bit-identical — only the work
        done is scoped down. The patch is always reverted in finally.
        """
        t0 = datetime.now(timezone.utc).timestamp()
        impacted = await self._resolve_impacted_players(event)
        logger.info(
            f"[INJURY-RESCORE] trigger players={event.affected_players[:3]}... "
            f"severity={event.severity} | rescoring {len(impacted)} impacted players"
        )

        from services.scoring.adapters.nba_scoring import NBAScoring

        _orig = NBAScoring.load_live_props
        _impacted_list = list(impacted)

        async def _scoped(inner_self, db, limit: Optional[int] = None):
            cursor = db[inner_self.live_props_collection].find(
                {"player_name": {"$in": _impacted_list}},
                {"_id": 0},
            )
            if limit:
                cursor = cursor.limit(int(limit))
            props = await cursor.to_list(length=None)
            logger.info(
                f"[INJURY-RESCORE] scoped load_live_props: {len(props)} props for "
                f"{len(_impacted_list)} players"
            )
            return props

        NBAScoring.load_live_props = _scoped
        try:
            result = await _recompute_mod.recompute(
                sports=["nba"],
                version_tag=_VERSION_TAG,
                config={},
            )
        finally:
            NBAScoring.load_live_props = _orig

        written = (result or {}).get("written", 0)
        self._stats["recomputes"] += 1
        self._stats["props_rescored"] += int(written or 0)
        self._stats["last_latency_ms"] = int(
            (datetime.now(timezone.utc).timestamp() - t0) * 1000
        )
        logger.info(
            f"[INJURY-RESCORE] done: {written} props rescored in "
            f"{self._stats['last_latency_ms']} ms"
        )

    def stats(self) -> Dict[str, Any]:
        return {**self._stats, "queue_size": self._queue.qsize()}


# Module-global singleton
_instance: Optional[InjuryTriggeredRescore] = None


def get_rescore_service() -> InjuryTriggeredRescore:
    global _instance
    if _instance is None:
        _instance = InjuryTriggeredRescore()
    return _instance
