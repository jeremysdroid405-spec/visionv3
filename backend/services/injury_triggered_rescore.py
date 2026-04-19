"""
Injury-Triggered Targeted Re-Score (Phase 3)
=============================================
Subscribes to `BoardEvent(event_type="injury_change", sport="nba")` on the
central event bus (published by `services.injury_sensor`) and patches ONLY
the props / player docs affected by that injury event:

    - `nba_prop_scores`  (version_tag='final-nba')  — re-scored via the
      full VK2/gate/PP stack, but scoped to the impacted player set only.
    - `dg_cached_board`                           — refresh injury_status +
      injured_teammates + synced_at for each impacted player doc so the
      Dashboard's Live Injury Advantage / Usage Ripple surfaces react in
      seconds rather than waiting for the next hourly full sync.

Upstream canonical sources:
    injuries_normalized  (written by InjurySensor; merges BDL + ESPN + NBA
    Official; already dedupes, tier-levels, and publishes events)

Scope of impacted players per event (Phase 3 cut):
    1. the injured/returning player's own props        (direct effect)
    2. same-team players on the same slate             (teammate usage shift)

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

Re-score mechanism:
    Monkey-patches `NBAScoringAdapter.load_live_props` for the duration of
    ONE `recompute(db, sports=["nba"], version_tag="final-nba")` call,
    scoping the props query to only the impacted player set. The rest of
    the pipeline (VK2 models, empirical residual, gate stack, tier
    assignment, PP utility) is unchanged — so this produces bit-identical
    scores for the affected props as a full-slate recompute would. The
    monkey-patch is always reverted inside a try/finally.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from services.event_bus import BoardEvent, get_event_bus
from services.scoring import recompute as _recompute_mod

from services.config.collection_names import COLL

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
            "board_players_patched": 0,
            "failures": 0,
            "last_latency_ms": 0,
            "last_trigger": None,
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

    async def trigger_manual(self, event: BoardEvent) -> None:
        """Public hook for tests / admin tools to enqueue a synthetic event
        without going through the event bus. Enforces the same filters."""
        await self._on_event(event)

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
        slate. `dg_cached_board` is the canonical "players with props
        today" source (one doc per player, carries `team`), so we resolve
        teammates there. `dg_live_props` has no `team` field — historical
        reason — so it cannot be used for this lookup.
        """
        impacted: Set[str] = set(event.affected_players)
        if not impacted or self._db is None:
            return impacted

        hint_team = event.metadata.get("team")

        try:
            # 1) Resolve the team(s) the injured players belong to. Prefer
            #    the event's team hint; supplement via dg_cached_board.
            teams: Set[str] = set()
            if hint_team:
                teams.add(hint_team)
            async for doc in self._db[COLL("board_cache", "nba")].find(
                {"player_name": {"$in": list(impacted)}},
                {"_id": 0, "team": 1},
            ):
                t = doc.get("team")
                if t:
                    teams.add(t)

            if not teams:
                return impacted

            # 2) Pull all players on those teams that have an entry in
            #    dg_cached_board (= have props on today's slate).
            async for doc in self._db[COLL("board_cache", "nba")].find(
                {"team": {"$in": list(teams)}},
                {"_id": 0, "player_name": 1},
            ):
                pn = doc.get("player_name")
                if pn:
                    impacted.add(pn)
        except Exception as e:
            logger.warning(
                f"[INJURY-RESCORE] player resolver failed (falling back to "
                f"solo player set): {e}"
            )

        return impacted

    async def _handle(self, event: BoardEvent) -> None:
        """Recompute scores + patch cached board for impacted players.

        Sequence:
            1. resolve impacted set (player + same-team teammates)
            2. scoped recompute → nba_prop_scores rows rewritten
            3. targeted dg_cached_board patch → injury_status,
               injured_teammates, synced_at
        """
        t0 = datetime.now(timezone.utc).timestamp()
        impacted = await self._resolve_impacted_players(event)
        logger.info(
            f"[INJURY-RESCORE] trigger players={event.affected_players[:3]}... "
            f"severity={event.severity} | rescoring {len(impacted)} impacted players"
        )

        written = await self._scoped_recompute(sorted(impacted))
        patched = await self._patch_cached_board(
            impacted_players=sorted(impacted),
            team_hint=event.metadata.get("team"),
            event_players=event.affected_players,
        )

        self._stats["recomputes"] += 1
        self._stats["props_rescored"] += int(written or 0)
        self._stats["board_players_patched"] += int(patched or 0)
        self._stats["last_latency_ms"] = int(
            (datetime.now(timezone.utc).timestamp() - t0) * 1000
        )
        self._stats["last_trigger"] = {
            "players": event.affected_players[:5],
            "team": event.metadata.get("team"),
            "severity": event.severity,
            "impacted_count": len(impacted),
            "props_rescored": int(written or 0),
            "board_players_patched": int(patched or 0),
            "latency_ms": self._stats["last_latency_ms"],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            f"[INJURY-RESCORE] done: {written} props rescored, "
            f"{patched} board players patched in "
            f"{self._stats['last_latency_ms']} ms"
        )

    async def _scoped_recompute(self, impacted_players: List[str]) -> int:
        """Monkey-patch NBAScoringAdapter.load_live_props for the duration
        of a single recompute so only the impacted players' props are
        processed. Returns the number of score docs written."""
        if not impacted_players:
            return 0

        # Import here to avoid circulars at module-load time.
        from services.scoring.adapters.nba_scoring import NBAScoringAdapter

        _orig = NBAScoringAdapter.load_live_props

        async def _scoped(inner_self, db, limit: Optional[int] = None):
            cursor = db[inner_self.live_props_collection].find(
                {"player_name": {"$in": impacted_players}},
                {"_id": 0},
            )
            if limit:
                cursor = cursor.limit(int(limit))
            props = await cursor.to_list(length=None)
            logger.info(
                f"[INJURY-RESCORE] scoped load_live_props: {len(props)} props for "
                f"{len(impacted_players)} players"
            )
            return props

        NBAScoringAdapter.load_live_props = _scoped
        try:
            result = await _recompute_mod.recompute(
                db=self._db,
                sports=["nba"],
                version_tag=_VERSION_TAG,
            )
        finally:
            NBAScoringAdapter.load_live_props = _orig

        # recompute() returns {"written": {sport: N}, ...}
        written_map = (result or {}).get("written") or {}
        if isinstance(written_map, dict):
            return int(written_map.get("nba", 0) or 0)
        return 0

    async def _patch_cached_board(
        self,
        impacted_players: List[str],
        team_hint: Optional[str],
        event_players: List[str],
    ) -> int:
        """Refresh `injury_status`, `injured_teammates`, and `synced_at`
        on each impacted player doc in `dg_cached_board`.

        Source of truth: `injuries_normalized` (written by InjurySensor).
        We DO NOT recompute usage_bump here — that stays with the hourly
        insights pipeline. The goal is narrow: make the Dashboard reflect
        the new injury context for the affected players immediately.

        Returns the number of player docs updated.
        """
        if not impacted_players or self._db is None:
            return 0

        now_iso = datetime.now(timezone.utc).isoformat()
        event_players_lc = {(p or "").strip().lower() for p in event_players}

        # 1) Resolve the team(s) we need injury rows for. Start from the
        #    event's team hint; supplement with teams found for impacted
        #    players in dg_cached_board (handles multi-team edge cases).
        teams: Set[str] = set()
        if team_hint:
            teams.add(team_hint)

        player_docs: Dict[str, Dict[str, Any]] = {}
        async for doc in self._db[COLL("board_cache", "nba")].find(
            {"player_name": {"$in": impacted_players}},
            {"_id": 0, "player_name": 1, "team": 1},
        ):
            pn = doc.get("player_name")
            if pn:
                player_docs[pn] = doc
                if doc.get("team"):
                    teams.add(doc["team"])

        # 2) Load current NBA injury rows for those teams, keyed by
        #    (team, player_name_lower).
        injuries_by_team: Dict[str, List[Dict[str, Any]]] = {}
        injury_by_key: Dict[tuple, Dict[str, Any]] = {}
        if teams:
            async for rec in self._db[COLL.shared("injuries")].find(
                {"sport": "nba", "team": {"$in": list(teams)}},
                {
                    "_id": 0,
                    "player_name": 1,
                    "team": 1,
                    "status": 1,
                    "tier_level": 1,
                    "return_date": 1,
                },
            ):
                t = rec.get("team") or ""
                pn_lc = (rec.get("player_name") or "").strip().lower()
                if not t or not pn_lc:
                    continue
                injuries_by_team.setdefault(t, []).append(rec)
                injury_by_key[(t, pn_lc)] = rec

        # 3) For each impacted player, build the new injury_status +
        #    injured_teammates list and write it.
        patched = 0
        for pn in impacted_players:
            pdoc = player_docs.get(pn)
            team = (pdoc or {}).get("team") or team_hint
            if not team:
                continue

            pn_lc = pn.strip().lower()
            self_rec = injury_by_key.get((team, pn_lc))
            self_status = None
            if self_rec:
                # Quarantine: we only use the structural `status` tier
                # (not raw display text), per the structural/display
                # firewall. tier_level >=2 means Questionable+.
                if int(self_rec.get("tier_level", 0) or 0) >= 2:
                    self_status = self_rec.get("status")

            teammates: List[str] = []
            for rec in injuries_by_team.get(team, []):
                other_name = rec.get("player_name") or ""
                if other_name.strip().lower() == pn_lc:
                    continue
                if int(rec.get("tier_level", 0) or 0) < 3:
                    # Only OUT / DOUBTFUL teammates contribute to the UI
                    # Usage Ripple badge (tier_level 3+).
                    continue
                teammates.append(other_name)

            # Trigger player goes to the FRONT of the list for UI clarity.
            if pn_lc in event_players_lc:
                # nothing extra — this is the player themselves
                pass
            else:
                # If the trigger was a teammate's injury, make sure that
                # teammate is at the top of the list.
                for ep in event_players:
                    if ep in teammates:
                        teammates.remove(ep)
                        teammates.insert(0, ep)
                        break

            result = await self._db[COLL("board_cache", "nba")].update_one(
                {"player_name": pn},
                {
                    "$set": {
                        "injury_status": self_status,
                        "injured_teammates": teammates,
                        "synced_at": now_iso,
                        "last_injury_rescore_at": now_iso,
                    }
                },
            )
            if result.modified_count > 0 or result.matched_count > 0:
                patched += 1

        return patched

    def stats(self) -> Dict[str, Any]:
        return {**self._stats, "queue_size": self._queue.qsize()}


# Module-global singleton
_instance: Optional[InjuryTriggeredRescore] = None


def get_rescore_service() -> InjuryTriggeredRescore:
    global _instance
    if _instance is None:
        _instance = InjuryTriggeredRescore()
    return _instance
