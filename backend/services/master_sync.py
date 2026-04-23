"""
Universal Master Sync — the ONLY master-sync path (post-consolidation, 2026-04-22).

Sport-agnostic. Three canonical steps:

  1. Universal odds sync  → writes `{sport}_live_props`
     (via `services.universal_odds_sync.sync_sport_props(sport)`)

  2. Sport-specific supplemental ingest (BDL game logs, cached-board
     intersection, etc.) — kept as optional hooks per sport so the
     downstream scoring pass has fresh stat context.

  3. Canonical scoring  → writes `{sport}_prop_scores` at
     `final-{sport}` AND `final-{sport}-rt` tags (the live UI reads the
     `-rt` tag).

All legacy paths (DemonGoblinEngine, NBAMasterSync, UnifiedPipeline,
optimized_sync_engine, ferrari_tier_service, oracle_apex_service,
mlb_tier_service, cached_board_builder_service) have been deleted.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)


SUPPORTED_SPORTS = ("nba", "mlb")


async def run_master_sync(db, sport: str) -> Dict[str, Any]:
    """Run the universal master sync for a single sport."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport for master_sync: {sport!r}")

    started = datetime.now(timezone.utc)
    metrics: Dict[str, Any] = {
        "pipeline": "UNIVERSAL_MASTER_SYNC",
        "sport": sport,
        "started_at": started.isoformat(),
        "steps": {},
        "errors": [],
    }

    # -----------------------------------------------------------------
    # Step 0 — active-roster sync (Global Identity Rule, 2026-04-23).
    # Keeps the sport's master hub up-to-date with BDL's
    # `/players/active` roster so every live prop can be stamped with
    # a canonical `bdl_player_id` at ingest. Cheap (cursor-paginated
    # single endpoint) and safely idempotent via upsert on `bdl_id`.
    # -----------------------------------------------------------------
    t_roster = datetime.now(timezone.utc)
    try:
        from services.bdl_universal_sync import get_bdl_universal_service
        svc = get_bdl_universal_service(db)
        roster = await svc.sync_players(sport=sport)
        metrics["steps"]["0_roster_sync"] = {
            "duration_seconds": (datetime.now(timezone.utc) - t_roster).total_seconds(),
            "players_count": roster.get("players_count", 0),
            "saved_count": roster.get("saved_count", 0),
        }
    except Exception as exc:
        logger.warning(f"[MASTER_SYNC:{sport}] roster sync failed: {exc}")
        metrics["steps"]["0_roster_sync"] = {
            "duration_seconds": (datetime.now(timezone.utc) - t_roster).total_seconds(),
            "error": str(exc),
        }

    # -----------------------------------------------------------------
    # Step 1 — universal odds sync
    # -----------------------------------------------------------------
    t0 = datetime.now(timezone.utc)
    try:
        from services.universal_odds_sync import get_universal_odds_service
        from services.config.collection_names import COLL

        old_count = await db[COLL("live_props", sport)].count_documents({})
        await db[COLL("live_props", sport)].delete_many({})

        odds_result = await get_universal_odds_service(db).sync_sport_props(sport)
        metrics["steps"]["1_odds_sync"] = {
            "duration_seconds": (datetime.now(timezone.utc) - t0).total_seconds(),
            "old_props_cleared": old_count,
            "events_count": odds_result.get("events_count", 0),
            "total_props": odds_result.get("total_props", 0),
            "bookmaker_counts": odds_result.get("bookmaker_counts", {}),
            "credits_used": odds_result.get("credits_used"),
            "markets_discovered": odds_result.get("markets_discovered"),
        }
    except Exception as exc:
        logger.exception(f"[MASTER_SYNC:{sport}] odds_sync failed")
        metrics["errors"].append(f"odds_sync: {exc}")
        metrics["steps"]["1_odds_sync"] = {
            "duration_seconds": (datetime.now(timezone.utc) - t0).total_seconds(),
            "error": str(exc),
        }

    # -----------------------------------------------------------------
    # Step 2 — sport-specific supplemental ingest
    # -----------------------------------------------------------------
    t1 = datetime.now(timezone.utc)
    sup_metrics: Dict[str, Any] = {}
    try:
        if sport == "nba":
            # NBA: warm BDL game logs so the scoring pass has hit-rate context.
            try:
                from services.bdl_game_logs_sync_batched import (
                    run_bdl_game_logs_sync_batched,
                )
                logs = await run_bdl_game_logs_sync_batched(db)
                sup_metrics["bdl_game_logs_players_synced"] = logs.get(
                    "players_synced", 0
                )
            except Exception as exc:
                logger.warning(f"[MASTER_SYNC:nba] BDL game logs prefetch failed: {exc}")
                sup_metrics["bdl_game_logs_error"] = str(exc)
        elif sport == "mlb":
            # MLB: build the intersection cached board + pre-warm BDL splits.
            try:
                from services.mlb_cached_board_builder import get_mlb_board_builder
                board = await get_mlb_board_builder(db).build_cached_board()
                sup_metrics["cached_board_props"] = board.get("props_enriched", 0)
            except Exception as exc:
                logger.warning(f"[MASTER_SYNC:mlb] cached_board build failed: {exc}")
                sup_metrics["cached_board_error"] = str(exc)
            try:
                from services.bdl_splits_cache import (
                    prefetch_all_splits,
                    clear_cache,
                    _splits_cache,
                )
                from services.config.collection_names import COLL

                pids = set()
                cursor = db[COLL("board_cache", "mlb")].find(
                    {}, {"bdl_id": 1, "player_id": 1}
                )
                async for doc in cursor:
                    pid = doc.get("bdl_id") or doc.get("player_id")
                    if pid:
                        try:
                            pids.add(int(pid))
                        except (ValueError, TypeError):
                            pass
                clear_cache()
                cached_ok = await prefetch_all_splits(pids)
                sup_metrics["bdl_splits_players_cached"] = cached_ok
                sup_metrics["bdl_splits_api_calls"] = len(_splits_cache)
            except Exception as exc:
                logger.warning(f"[MASTER_SYNC:mlb] BDL splits prefetch failed: {exc}")
                sup_metrics["bdl_splits_error"] = str(exc)
        sup_metrics["duration_seconds"] = (
            datetime.now(timezone.utc) - t1
        ).total_seconds()
        metrics["steps"]["2_supplemental_ingest"] = sup_metrics
    except Exception as exc:
        logger.exception(f"[MASTER_SYNC:{sport}] supplemental step failed")
        metrics["errors"].append(f"supplemental: {exc}")
        metrics["steps"]["2_supplemental_ingest"] = {
            "duration_seconds": (datetime.now(timezone.utc) - t1).total_seconds(),
            "error": str(exc),
        }

    # -----------------------------------------------------------------
    # Step 3 — canonical scoring (final-{sport} + final-{sport}-rt)
    # -----------------------------------------------------------------
    try:
        from services.scoring.recompute import recompute_sport

        for tag in (f"final-{sport}", f"final-{sport}-rt"):
            ts = datetime.now(timezone.utc)
            result = await recompute_sport(
                db=db, sport=sport, version_tag=tag, dry_run=False
            )
            metrics["steps"][f"3_scoring_{tag}"] = {
                "duration_seconds": (datetime.now(timezone.utc) - ts).total_seconds(),
                "processed": result.get("processed", 0),
                "written": result.get("written", 0),
                "replaced": result.get("replaced", 0),
                "tier_distribution": result.get("tier_distribution", {}),
                "version_tag": result.get("version_tag"),
            }
    except Exception as exc:
        logger.exception(f"[MASTER_SYNC:{sport}] scoring failed")
        metrics["errors"].append(f"scoring: {exc}")

    completed = datetime.now(timezone.utc)
    metrics["completed_at"] = completed.isoformat()
    metrics["total_duration_seconds"] = (completed - started).total_seconds()
    metrics["success"] = len(metrics["errors"]) == 0

    logger.info(
        f"[MASTER_SYNC:{sport}] COMPLETE in "
        f"{metrics['total_duration_seconds']:.1f}s success={metrics['success']}"
    )
    return metrics
