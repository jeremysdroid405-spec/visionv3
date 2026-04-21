"""
Sport-agnostic master-sync pipeline steps.

Final carbon-copy enforcement (2026-04-21, D1 residual cleanup):
removes the last standalone per-sport orchestrator class
(`services/mlb_master_sync.py`). The 6-step MLB pipeline is re-expressed
as composable `PipelineStep` objects registered on the sport adapter and
executed by `UnifiedPipeline.run_master_sync()`. NBA adapters can
register their own step list without any mlb_* churn.

Architectural contract:
  - Every step is stateless; shared state lives in a `context` dict
    passed between steps.
  - Steps return a metrics dict (duration_seconds + sport-specific
    counters) that the pipeline accumulates into the master-sync
    response body. Identical semantics to the old
    `MLBMasterSync.run_master_sync()` `metrics["steps"][...]` shape.
  - Exceptions in one step are logged and appended to
    `context["errors"]`; they do not abort subsequent steps.
    (Matches MLBMasterSync behaviour.)
"""
from __future__ import annotations

import abc
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)


class PipelineStep(abc.ABC):
    """Base class for sport-agnostic master-sync pipeline steps."""

    name: str = "unnamed"

    @abc.abstractmethod
    async def run(
        self, adapter, db, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# MLB-specific step implementations.
# ---------------------------------------------------------------------------
# These wrap existing shared service functions — nothing new is computed.
# The only change vs the deleted MLBMasterSync is the shape of the driver
# (sport-agnostic step framework rather than a hand-written orchestrator
# class). Skipped steps (4 oracle tier rebuild, 5 lineup ripple) remain
# callable but are gated off by Stage-4's MLB_WRITE_LEGACY_TIERS flag and
# therefore no-op in the live carbon-copy flow.
# ---------------------------------------------------------------------------

class MLBOddsSyncStep(PipelineStep):
    """Step 1 — clear stale `mlb_live_props` and pull fresh odds (DK/MGM/PP)."""
    name = "1_odds_sync"

    async def run(self, adapter, db, context):
        from services.universal_odds_sync import get_universal_odds_service
        from services.config.collection_names import COLL

        started = datetime.now(timezone.utc)
        old_count = await db[COLL("live_props", "mlb")].count_documents({})
        await db[COLL("live_props", "mlb")].delete_many({})
        logger.info(f"[MLB_PIPELINE] Step 1: cleared {old_count} stale props")

        odds = await get_universal_odds_service(db).sync_sport_props("mlb")
        dur = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "duration_seconds": dur,
            "old_props_cleared": old_count,
            "events_count": odds.get("events_count", 0),
            "total_props": odds.get("total_props", 0),
            "prizepicks_props": odds.get("bookmaker_counts", {}).get("prizepicks", 0),
            "draftkings_props": odds.get("bookmaker_counts", {}).get("draftkings", 0),
        }


class MLBCachedBoardBuildStep(PipelineStep):
    """Step 2 — build the intersection cached board (players with PP + odds)."""
    name = "2_cached_board"

    async def run(self, adapter, db, context):
        from services.mlb_cached_board_builder import get_mlb_board_builder
        from services.config.collection_names import COLL

        started = datetime.now(timezone.utc)

        pp_players: Set[str] = set()
        async for p in db[COLL("live_props", "mlb")].find(
            {"bookmaker": "prizepicks"}, {"player_name": 1}
        ):
            nm = (p.get("player_name") or "").strip().lower()
            if nm:
                pp_players.add(nm)

        odds_players: Set[str] = set()
        async for p in db[COLL("live_props", "mlb")].find(
            {"bookmaker": {"$in": ["draftkings", "pinnacle"]}},
            {"player_name": 1},
        ):
            nm = (p.get("player_name") or "").strip().lower()
            if nm:
                odds_players.add(nm)

        intersection = pp_players & odds_players
        logger.info(
            f"[MLB_PIPELINE] Step 2 intersection: pp={len(pp_players)} "
            f"odds={len(odds_players)} both={len(intersection)}"
        )

        builder = get_mlb_board_builder(db)
        result = await builder.build_cached_board()
        dur = (datetime.now(timezone.utc) - started).total_seconds()

        # Collect player_ids for the BDL prefetch step. Saves a second scan.
        context["mlb_board_player_ids"] = await self._board_player_ids(db)

        return {
            "duration_seconds": dur,
            "players_matched": len(intersection),
            "players_prizepicks_only": len(pp_players - odds_players),
            "players_odds_only": len(odds_players - pp_players),
            "total_props_in_board": result.get("props_enriched", 0),
        }

    async def _board_player_ids(self, db) -> Set[int]:
        from services.config.collection_names import COLL
        pids: Set[int] = set()
        cursor = db[COLL("board_cache", "mlb")].find(
            {}, {"bdl_id": 1, "player_id": 1, "props": 1}
        )
        async for doc in cursor:
            pid = doc.get("bdl_id") or doc.get("player_id")
            if not pid:
                continue
            has_hitter = any(
                (p.get("stat_key") or p.get("stat_type") or "").upper()
                not in (
                    "K", "OUTS", "ER", "PITCHER STRIKEOUTS",
                    "PITCHING OUTS", "WALKS ALLOWED", "HITS ALLOWED",
                    "EARNED RUNS",
                )
                for p in (doc.get("props") or [])
            )
            if has_hitter:
                try:
                    pids.add(int(pid))
                except (ValueError, TypeError):
                    pass
        return pids


class MLBBDLSplitsPrefetchStep(PipelineStep):
    """Step 3 — BDL splits prefetch (rate-limit protection + warm cache)."""
    name = "3_bdl_prefetch"

    async def run(self, adapter, db, context):
        from services.bdl_splits_cache import (
            prefetch_all_splits, clear_cache, _splits_cache,
        )

        started = datetime.now(timezone.utc)
        player_ids = context.get("mlb_board_player_ids") or set()
        clear_cache()
        cached_ok = await prefetch_all_splits(player_ids)
        dur = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "duration_seconds": dur,
            "players_requested": len(player_ids),
            "players_cached": cached_ok,
            "api_calls_made": len(_splits_cache),
            "api_calls_saved": 331 - len(player_ids),
        }


class MLBCanonicalRTScoringStep(PipelineStep):
    """Step 6-RT — write `final-mlb-rt` shadow (the live UI tag).

    The canonical `final-mlb` tag is written inside the main scoring
    pass by `MLBAdapter.enrich_and_score()` → `recompute_sport("mlb",
    "final-mlb")` (Stage 2). This post-score step mirrors that pass at
    the `-rt` tag so the UI reader (`MLBBoardAdapter.version_tag =
    "final-mlb-rt"`, Stage 7) is always fresh.
    """
    name = "6rt_realtime_shadow"

    async def run(self, adapter, db, context):
        from services.scoring.recompute import recompute_sport

        started = datetime.now(timezone.utc)
        result = await recompute_sport(
            db=db, sport="mlb", version_tag="final-mlb-rt", dry_run=False,
        )
        dur = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "duration_seconds": dur,
            "processed": result.get("processed", 0),
            "written": result.get("written", 0),
            "replaced": result.get("replaced", 0),
            "tier_distribution": result.get("tier_distribution", {}),
            "version_tag": result.get("version_tag"),
        }
