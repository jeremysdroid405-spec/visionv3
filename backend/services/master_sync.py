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
from typing import Any, Dict, List

from config.version_tags import MLB_LIVE, NBA_LIVE, for_sport
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)


SUPPORTED_SPORTS = ("nba", "mlb")


class MasterSyncBypassError(RuntimeError):
    """Raised when `run_master_sync()` is called without holding
    `UpstreamSyncLock.exclusive(sport)`. The lock is the only signal
    the delta detector has that a full sync is in flight; bypassing
    it produces partial-write races that have caused production
    tier-freeze incidents (see /app/memory/CHANGELOG.md 2026-05-07)."""


async def run_master_sync(
    db,
    sport: str,
    *,
    _admin_override: bool = False,
) -> Dict[str, Any]:
    """Run the universal master sync for a single sport.

    Concurrency contract (HARD ENFORCED 2026-05-07):
      All callers MUST hold `UpstreamSyncLock.exclusive(sport)` for
      the duration of this call. The lock is acquired by
      `RebuildCoordinator.dispatch_master_sync()` and
      `_execute_rebuild()`. A bare call here without the lock used to
      log CRITICAL only (toothless); it now raises
      `MasterSyncBypassError` so the bypass cannot reach production.

      `_admin_override=True` is the ONLY escape hatch and exists for
      one-time bootstrap scripts (`scripts/init_database.py`). It MUST
      NOT be used by route handlers, scheduled jobs, callbacks, or
      runtime code paths.
    """
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport for master_sync: {sport!r}")

    # Hard concurrency gate. The detector relies on the lock to know a
    # full sync is in flight; without it, the delta engine can rescore
    # against partially-written collections (the production race).
    if not _admin_override:
        try:
            from services.upstream_sync_lock import get_upstream_sync_lock
            lock = get_upstream_sync_lock()
        except Exception as _swept_exc:
            log_silent_failure("services.master_sync.run_master_sync.lock_import", _swept_exc)
            lock = None
        if lock is None or not lock.is_held(sport):
            raise MasterSyncBypassError(
                f"[MASTER_SYNC:{sport}] run_master_sync called without "
                f"UpstreamSyncLock. All callers MUST go through "
                f"`RebuildCoordinator.dispatch_master_sync(sport)` or "
                f"acquire `lock.exclusive(sport)` themselves. "
                f"`_admin_override=True` is reserved for one-time "
                f"bootstrap scripts only."
            )

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
    # NOTE: redundant `delete_many({})` removed 2026-04-28 — the inner
    # `sync_sport_props` now uses stage-then-prune (sync_batch_id) so
    # the live-props collection is NEVER empty during a sync window.
    t0 = datetime.now(timezone.utc)
    try:
        from services.universal_odds_sync import get_universal_odds_service
        from services.config.collection_names import COLL

        old_count = await db[COLL("live_props", sport)].count_documents({})

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
                        except (ValueError, TypeError) as _swept_exc:
                            log_silent_failure("services.master_sync.run_master_sync", _swept_exc)  # sweep-auto-converted
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

    # -----------------------------------------------------------------
    # Step 4 — read-side enrichment: defensive momentum (NBA only)
    #
    # NOT scoring. Does not affect projections, gates, ECDF, tiers,
    # recompute math, thresholds, or Ferrari tier logic. Pure UI
    # decoration. Writes `momentum_data` onto `nba_prop_scores` docs at
    # `version_tag=final-nba-rt` so PlayerDetailPage's existing read
    # path (Fix A overlay in `routes/player.py`) can render
    # `MomentumTrackerFull`.
    # -----------------------------------------------------------------
    if sport == "nba":
        try:
            ts = datetime.now(timezone.utc)
            md_metrics = await _enrich_nba_momentum(db)
            md_metrics["duration_seconds"] = (
                datetime.now(timezone.utc) - ts
            ).total_seconds()
            metrics["steps"]["4_momentum_enrichment_nba"] = md_metrics
        except Exception as exc:
            logger.exception(f"[MASTER_SYNC:{sport}] momentum enrichment failed")
            metrics["errors"].append(f"momentum_enrichment: {exc}")

    # -----------------------------------------------------------------
    # Step 5 — read-side enrichment: tempo / pace_delta (NBA only)
    #
    # Replaces stale legacy cached_board pace_delta values
    # ({display:"0.0", tempo_label:"Neutral Pace", expected_game_pace:"98.0"}
    # written by deleted `optimized_sync_engine` / `cached_board_builder_service`
    # before 2026-04-22) with current
    # `IntelSuiteCalculator._calculate_pace_delta(team, opponent, board_pick)`
    # output. NOT scoring. Does not affect projections, gates, ECDF,
    # tiers, recompute math, or thresholds.
    # -----------------------------------------------------------------
    if sport == "nba":
        try:
            ts = datetime.now(timezone.utc)
            pd_metrics = await _enrich_nba_pace_delta(db)
            pd_metrics["duration_seconds"] = (
                datetime.now(timezone.utc) - ts
            ).total_seconds()
            metrics["steps"]["5_pace_delta_enrichment_nba"] = pd_metrics
        except Exception as exc:
            logger.exception(
                f"[MASTER_SYNC:{sport}] pace_delta enrichment failed"
            )
            metrics["errors"].append(f"pace_delta_enrichment: {exc}")

    # -----------------------------------------------------------------
    # Step 6 — read-side enrichment: Gemini Vision Intel summaries
    #
    # Active-board scope only: Safe Haven + Front Lines + War Zone
    # (~20–50 picks per slate, capped at 75). Replaces the deterministic
    # `_generate_vision_fallback` template with real Gemini-authored
    # narratives ON BOARD PICKS ONLY. NOT scoring. Does not affect
    # projections, gates, ECDF, tiers, recompute math, or thresholds.
    # -----------------------------------------------------------------
    if sport in ("nba", "mlb"):
        # Sport-aware enricher dispatch (2026-05-05): MLB now has a
        # dedicated `_enrich_mlb_board_vision_intel` that mirrors the
        # NBA path so MLB tier endpoints surface real Gemini narratives
        # instead of `vision_intel: None`.
        enricher = (
            _enrich_nba_board_vision_intel if sport == "nba"
            else _enrich_mlb_board_vision_intel
        )
        try:
            ts = datetime.now(timezone.utc)
            vi_metrics = await enricher(db)
            vi_metrics["duration_seconds"] = (
                datetime.now(timezone.utc) - ts
            ).total_seconds()
            metrics["steps"][f"6_vision_intel_enrichment_{sport}"] = vi_metrics
        except Exception as exc:
            logger.exception(
                f"[MASTER_SYNC:{sport}] vision_intel enrichment failed"
            )
            metrics["errors"].append(f"vision_intel_enrichment: {exc}")

    # -----------------------------------------------------------------
    # Step 7 — cached_board freshness stamp (2026-05-07 P0 §3 fix)
    #
    # `{nba,mlb}_cached_board` writers (mlb_cached_board_builder full
    # rebuild + master_sync's three NBA/MLB enrichment overlays) did
    # not previously stamp doc-level freshness. SLO §3 had no canonical
    # signal. This stamp closes that gap by writing the Phase 4
    # freshness contract `{updated_at, last_publish_ts,
    # source_score_max_scored_at, sport, version_tag}` on EVERY doc in
    # the just-built/just-enriched cached_board. Idempotent; one
    # `update_many` per sport per master_sync run.
    # -----------------------------------------------------------------
    if sport in ("nba", "mlb"):
        try:
            ts = datetime.now(timezone.utc)
            from services.board_freshness import stamp_cached_board_freshness
            cb_metrics = await stamp_cached_board_freshness(db, sport, now=ts)
            metrics["steps"]["7_cached_board_freshness_stamp"] = cb_metrics
        except Exception as exc:
            logger.warning(
                f"[MASTER_SYNC:{sport}] cached_board freshness stamp failed: {exc}"
            )
            metrics["errors"].append(f"cached_board_freshness_stamp: {exc}")

    completed = datetime.now(timezone.utc)
    metrics["completed_at"] = completed.isoformat()
    metrics["total_duration_seconds"] = (completed - started).total_seconds()
    metrics["success"] = len(metrics["errors"]) == 0

    logger.info(
        f"[MASTER_SYNC:{sport}] COMPLETE in "
        f"{metrics['total_duration_seconds']:.1f}s success={metrics['success']}"
    )

    # ----------------------------------------------------------------
    # OBSERVABILITY ONLY (2026-04-25): persist a flat sync_history doc
    # for every master_sync invocation. Pure logging — no change to
    # return value, no change to publish path, no behaviour change. The
    # write is best-effort: a failure here MUST NOT affect the caller.
    # See /app/memory/PRD.md "Sync Lifecycle Audit, 2026-04-25" §9
    # Fix-3 for the architectural rationale (precondition for
    # health-gate + staging in subsequent PRs).
    # ----------------------------------------------------------------
    try:
        await _persist_sync_history(db, sport, metrics, started, completed)
    except Exception as exc:
        logger.warning(f"[MASTER_SYNC:{sport}] sync_history persist failed: {exc}")

    return metrics


async def _persist_sync_history(
    db,
    sport: str,
    metrics: Dict[str, Any],
    started: datetime,
    completed: datetime,
) -> None:
    """Compose a flat sync_history document and insert it. Pure
    observability — best-effort, isolated from sync return path.

    Fields mirror the spec at /app/memory/PRD.md §sync_history.
    Counts that aren't in the in-memory metrics dict are computed via
    cheap collection counts on the just-written collections.
    """
    from services.config.collection_names import COLL

    steps = metrics.get("steps") or {}
    odds_step = steps.get("1_odds_sync") or {}
    scoring_rt_step = steps.get(f"3_scoring_final-{sport}-rt") or {}
    scoring_base_step = steps.get(f"3_scoring_final-{sport}") or {}

    # ----- counts derived from the in-memory metrics --------------
    events_discovered = int(odds_step.get("events_count") or 0)
    discovered_markets_list = odds_step.get("markets_discovered") or []
    discovered_market_count = (
        len(discovered_markets_list)
        if isinstance(discovered_markets_list, (list, tuple, set))
        else 0
    )
    bookmaker_counts = odds_step.get("bookmaker_counts") or {}
    odds_sync_total_props = int(odds_step.get("total_props") or 0)
    scored_props_count = int(
        scoring_rt_step.get("written")
        or scoring_base_step.get("written")
        or 0
    )

    # ----- counts derived from live collections (post-publish) ----
    live_props_count = 0
    distinct_stat_types = 0
    distinct_events = 0
    raw_market_count = 0
    pp_available_count = 0
    sportsbook_fallback_count = 0
    distinct_market_keys = 0
    anchor_book_breakdown: Dict[str, int] = {}
    try:
        live_coll_name = COLL("live_props", sport)
        live_props_count = await db[live_coll_name].count_documents({})
        distinct_stat_types = len(
            await db[live_coll_name].distinct("stat_type")
        )
        distinct_events = len(
            await db[live_coll_name].distinct("event_id")
        )
        # ---- Universal SSOT coverage metrics (2026-04-25) ----
        # `playable_on_pp == True` -> PrizePicks-playable canonicals.
        pp_available_count = await db[live_coll_name].count_documents(
            {"playable_on_pp": True}
        )
        # `source_anchor == "sportsbook_fallback"` -> canonicals seeded
        # by a sportsbook because PrizePicks did not list them.
        sportsbook_fallback_count = await db[live_coll_name].count_documents(
            {"source_anchor": "sportsbook_fallback"}
        )
        # Per-anchor-book breakdown of canonical-pool sourcing.
        try:
            anchor_pipeline = [
                {"$match": {"anchor_book": {"$ne": None}}},
                {"$group": {"_id": "$anchor_book", "n": {"$sum": 1}}},
            ]
            async for row in db[live_coll_name].aggregate(anchor_pipeline):
                anchor_book_breakdown[str(row.get("_id") or "unknown")] = int(
                    row.get("n") or 0
                )
        except Exception as exc:
            logger.debug(
                f"[SYNC_HISTORY:{sport}] anchor_book aggregate failed: {exc}"
            )
    except Exception as exc:
        logger.debug(
            f"[SYNC_HISTORY:{sport}] live_props count fetch failed: {exc}"
        )
    try:
        # `dg_raw_odds_markets` is replaced per (sport, event_id) each
        # sync, so a snapshot count immediately after master_sync gives
        # this run's raw-row volume.
        raw_market_count = await db["dg_raw_odds_markets"].count_documents(
            {"sport": sport}
        )
        # Distinct raw market keys this sport saw (h2h, spreads,
        # player_points, player_points_alternate, team_totals, etc.)
        distinct_market_keys = len(
            await db["dg_raw_odds_markets"].distinct(
                "market_key", {"sport": sport}
            )
        )
    except Exception as exc:
        logger.debug(
            f"[SYNC_HISTORY:{sport}] raw_market count fetch failed: {exc}"
        )

    # ----- status / published derivation --------------------------
    errors: List[str] = list(metrics.get("errors") or [])
    warnings: List[str] = []
    for step_name, step_data in steps.items():
        if isinstance(step_data, dict) and step_data.get("error"):
            warnings.append(f"{step_name}: {step_data['error']}")

    odds_sync_errored = bool(odds_step.get("error"))
    scoring_errored = any(
        s.lower().startswith("scoring:") for s in errors
    ) or bool(scoring_rt_step.get("error"))

    if odds_sync_errored or scoring_errored or scored_props_count == 0:
        status = "failed"
    elif errors or warnings:
        status = "partial"
    else:
        status = "success"

    published = scored_props_count > 0 and not scoring_errored

    sync_record = {
        "sport": sport,
        "started_at": started,
        "finished_at": completed,
        "duration_seconds": float(metrics.get("total_duration_seconds") or 0.0),

        "status": status,
        "published": published,

        # ---- sync metrics ----
        "events_discovered": events_discovered,
        "events_attempted": events_discovered,
        "events_succeeded": distinct_events,
        "discovered_market_count": discovered_market_count,
        "raw_market_count": raw_market_count,
        "live_props_count": live_props_count,
        "scored_props_count": scored_props_count,
        "distinct_stat_types": distinct_stat_types,
        "distinct_events": distinct_events,
        # ---- Universal SSOT coverage (2026-04-25) ----
        # `pp_available_count` = canonicals PrizePicks quoted (the
        # PP-playable subset). `sportsbook_fallback_count` = canonicals
        # seeded by another book because PrizePicks didn't list them.
        # `distinct_market_keys` = raw market keys discovered across
        # `dg_raw_odds_markets` (h2h, spreads, player_*, team_totals…).
        # `anchor_book_breakdown` = canonical-pool sourcing per book.
        "pp_available_count": pp_available_count,
        "sportsbook_fallback_count": sportsbook_fallback_count,
        "distinct_market_keys": distinct_market_keys,
        "anchor_book_breakdown": anchor_book_breakdown,

        # ---- book / data health ----
        "bookmaker_counts": dict(bookmaker_counts) if bookmaker_counts else {},
        "discovered_markets": (
            list(discovered_markets_list)
            if isinstance(discovered_markets_list, (list, tuple, set))
            else []
        ),

        # ---- error tracking ----
        "errors": errors,
        "warnings": warnings,

        # ---- audit ----
        "pipeline": metrics.get("pipeline", "UNIVERSAL_MASTER_SYNC"),
        "odds_sync_total_props": odds_sync_total_props,
        "credits_used": odds_step.get("credits_used") or {},
        "steps_summary": {
            name: {
                k: v
                for k, v in (step or {}).items()
                if k in (
                    "duration_seconds", "processed", "written", "replaced",
                    "events_count", "total_props", "error",
                    "tier_distribution",
                )
            }
            for name, step in steps.items()
            if isinstance(step, dict)
        },
    }

    await db["sync_history"].insert_one(sync_record)
    logger.info(
        f"[SYNC_HISTORY:{sport}] persisted run "
        f"status={status} published={published} "
        f"live={live_props_count} scored={scored_props_count} "
        f"events={distinct_events}/{events_discovered} "
        f"stats={distinct_stat_types} "
        f"pp_available={pp_available_count} "
        f"sportsbook_fallback={sportsbook_fallback_count} "
        f"market_keys={distinct_market_keys}"
    )



# =====================================================================
# NBA Defensive Momentum Read-Side Enrichment Helper
#
# Purpose:
#   Wire the orphaned `DefensiveMomentumService` (present, functional,
#   but unwired since the legacy `optimized_sync_engine` was deleted on
#   2026-04-22) back into the active universal sync pipeline as a
#   pure UI-decoration step.
#
# Strict scope:
#   - Read `nba_prop_scores` at `version_tag=final-nba-rt`
#   - Group by (opponent_team_abbr, canonical_stat_family)
#   - Call existing `DefensiveMomentumService.calculate_momentum_modifier`
#     once per pair (cached in-memory + Mongo `defensive_momentum_cache`)
#   - Bulk-write the returned `momentum_data` dict back onto every
#     matching score doc
#
# Out of scope (per directive):
#   - Scoring math / projections
#   - Gates / Universal Gate Engine / thresholds
#   - ECDF / probability layer
#   - Recompute / Ferrari tier logic
#   - `nba_cached_board` writes (cached_board enrichment is a separate
#     pipeline; routes/player.py overlay reads `momentum_data` from the
#     score doc when the cached_board entry is missing)
# =====================================================================

# Full team name → 3-letter abbreviation. Used to translate live_props
# `home_team` / `away_team` (full names) to the abbreviations the
# DefensiveMomentumService expects.
_NBA_TEAM_NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC",
    "Los Angeles Lakers": "LAL", "LA Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


# Collapse alternate market keys to short stat codes recognized by
# `DefensiveMomentumService.STAT_PROXY_MAP`. Anything unknown falls
# through to DEFAULT_PROXY (DRTG) inside the service.
_STAT_FAMILY_ALIAS = {
    "PLAYER_POINTS": "PTS",
    "PLAYER_POINTS_ALTERNATE": "PTS",
    "PLAYER_REBOUNDS": "REB",
    "PLAYER_REBOUNDS_ALTERNATE": "REB",
    "PLAYER_ASSISTS": "AST",
    "PLAYER_ASSISTS_ALTERNATE": "AST",
    "PLAYER_THREES": "3PM",
    "PLAYER_THREES_ALTERNATE": "3PM",
    "PLAYER_BLOCKS": "BLK",
    "PLAYER_BLOCKS_ALTERNATE": "BLK",
    "PLAYER_STEALS": "STL",
    "PLAYER_STEALS_ALTERNATE": "STL",
    "PLAYER_TURNOVERS": "TO",
    "PLAYER_BLOCKS_STEALS": "BLK+STL",
    "PLAYER_BLOCKS_STEALS_ALTERNATE": "BLK+STL",
    "PLAYER_POINTS_REBOUNDS": "P+R",
    "PLAYER_POINTS_REBOUNDS_ALTERNATE": "P+R",
    "PLAYER_POINTS_ASSISTS": "P+A",
    "PLAYER_POINTS_ASSISTS_ALTERNATE": "P+A",
    "PLAYER_REBOUNDS_ASSISTS": "R+A",
    "PLAYER_REBOUNDS_ASSISTS_ALTERNATE": "R+A",
    "PLAYER_POINTS_REBOUNDS_ASSISTS": "PRA",
    "PLAYER_POINTS_REBOUNDS_ASSISTS_ALTERNATE": "PRA",
}


def _canon_stat(stat: str | None) -> str | None:
    if not stat:
        return None
    upper = stat.strip().upper()
    return _STAT_FAMILY_ALIAS.get(upper, upper)


def _to_team_abbr(name: str | None) -> str | None:
    if not name:
        return None
    if isinstance(name, str) and len(name) <= 4 and name.isupper():
        return name
    return _NBA_TEAM_NAME_TO_ABBR.get(name)


async def _enrich_nba_momentum(db) -> dict:
    """
    Populate `nba_prop_scores.<doc>.momentum_data` for every
    `final-nba-rt` doc whose (opponent_abbr, canonical_stat) yields a
    valid `DefensiveMomentumService.calculate_momentum_modifier` result.

    Read-only with respect to scoring. Does not modify projections,
    gates, ECDF outputs, tiers, recompute math, or thresholds.
    """
    from collections import defaultdict
    from pymongo import UpdateMany
    from services.defensive_momentum_service import get_momentum_service

    metrics = {
        "props_total": 0,
        "props_enriched": 0,
        "props_skipped": 0,
        "skip_reasons": {},
        "pairs_total": 0,
        "pairs_computed": 0,
        "pairs_failed": 0,
        "pairs_no_profile": 0,
        "cached_board_updates": 0,
        "cached_board_skipped": 0,
    }

    skip = metrics["skip_reasons"]

    def _bump(reason: str, n: int = 1) -> None:
        skip[reason] = skip.get(reason, 0) + n
        metrics["props_skipped"] += n

    # ------------------------------------------------------------------
    # Step A: event_id → (home_abbr, away_abbr) from `nba_live_props`
    #         and team_abbr → today's opponent abbr (slate-wide map)
    # ------------------------------------------------------------------
    event_map: dict = {}
    team_to_opp_today: dict = {}
    async for lp in db["nba_live_props"].find(
        {}, {"_id": 0, "event_id": 1, "home_team": 1, "away_team": 1}
    ):
        eid = lp.get("event_id")
        if not eid or eid in event_map:
            continue
        home = _to_team_abbr(lp.get("home_team"))
        away = _to_team_abbr(lp.get("away_team"))
        if home and away:
            event_map[eid] = (home, away)
            team_to_opp_today.setdefault(home, away)
            team_to_opp_today.setdefault(away, home)

    # ------------------------------------------------------------------
    # Step B: bdl_player_id → team_abbr from `nba_master_hub_2026`
    # ------------------------------------------------------------------
    player_team: dict = {}
    async for mh in db["nba_master_hub_2026"].find(
        {}, {"_id": 0, "bdl_id": 1, "bdl_player_id": 1, "team_abbr": 1, "team": 1}
    ):
        pid = mh.get("bdl_id") or mh.get("bdl_player_id")
        ta = mh.get("team_abbr") or mh.get("team")
        if pid is None or not ta:
            continue
        try:
            player_team[int(pid)] = ta
        except (TypeError, ValueError):
            continue

    # ------------------------------------------------------------------
    # Step C: Ensure DefensiveMomentumService cache is loaded once
    # ------------------------------------------------------------------
    momentum = get_momentum_service(db)
    try:
        await momentum.ensure_cache()
    except Exception as exc:  # cache build failure isolates here
        logger.warning(
            f"[MASTER_SYNC:nba] momentum ensure_cache failed: {exc}"
        )
        skip["ensure_cache_failed"] = str(exc)
        return metrics

    # ------------------------------------------------------------------
    # Step D: Walk score docs, derive (opp_abbr, stat_canon), group by
    # canonical_key (the stable identity already used by recompute).
    # ------------------------------------------------------------------
    bulk_by_pair: dict = defaultdict(list)
    cursor = db["nba_prop_scores"].find(
        {"version_tag": NBA_LIVE},
        {
            "_id": 0, "event_id": 1, "bdl_player_id": 1, "stat_type": 1,
            "canonical_key": 1, "player_name": 1,
        },
    )

    async for d in cursor:
        metrics["props_total"] += 1
        ck = d.get("canonical_key")
        eid = d.get("event_id")
        bid = d.get("bdl_player_id")
        stat = _canon_stat(d.get("stat_type"))

        if not ck:
            _bump("no_canonical_key")
            continue
        if not eid or eid not in event_map:
            _bump("no_event_match")
            continue
        if bid is None:
            _bump("no_bdl_id")
            continue
        try:
            team_abbr = player_team.get(int(bid))
        except (TypeError, ValueError):
            team_abbr = None
        if not team_abbr:
            _bump("no_team_lookup")
            continue
        if not stat:
            _bump("no_stat_type")
            continue

        home, away = event_map[eid]
        if team_abbr == home:
            opp = away
        elif team_abbr == away:
            opp = home
        else:
            _bump("team_not_in_event")
            continue

        bulk_by_pair[(opp, stat)].append(ck)

    # ------------------------------------------------------------------
    # Step E: One service call per unique (opp, stat) pair → bulk write
    # ------------------------------------------------------------------
    bulk_ops: list = []
    metrics["pairs_total"] = len(bulk_by_pair)

    for (opp, stat), keys in bulk_by_pair.items():
        try:
            _modifier, momentum_data = momentum.calculate_momentum_modifier(
                opp, stat
            )
            metrics["pairs_computed"] += 1
        except Exception as exc:
            logger.warning(
                f"[MASTER_SYNC:nba] momentum calc failed for {opp}/{stat}: {exc}"
            )
            metrics["pairs_failed"] += 1
            _bump("momentum_calc_failed", len(keys))
            continue

        if not momentum_data:
            metrics["pairs_no_profile"] += 1
            _bump("no_momentum_profile", len(keys))
            continue

        bulk_ops.append(
            UpdateMany(
                {
                    "version_tag": NBA_LIVE,
                    "canonical_key": {"$in": keys},
                },
                {"$set": {"momentum_data": momentum_data}},
            )
        )
        metrics["props_enriched"] += len(keys)

    if bulk_ops:
        await db["nba_prop_scores"].bulk_write(bulk_ops, ordered=False)

    # ------------------------------------------------------------------
    # Step F: Mirror momentum_data into `nba_cached_board.props[*]` so
    # that the existing cached_board overlay paths in
    # `routes/ferrari_tiers.py` and `routes/player.py` (the latter via
    # `_BOARD_ENRICHMENT_FIELDS`) can flow `momentum_data` through to
    # the UI. Cached_board is the authoritative read path for
    # display-side enrichment in the active universal architecture; the
    # score doc carries the field but Ferrari/player endpoints overlay
    # from cached_board.
    #
    # Update strategy: line-and-direction-agnostic. `momentum_data`
    # depends only on (opponent_team, stat_family), so we resolve once
    # per (player, stat_canon) and update every matching cached_board
    # prop at once via `arrayFilters`.
    # ------------------------------------------------------------------
    cb_bulk: list = []
    cb_pair_cache: dict = {}

    async for cb_doc in db["nba_cached_board"].find(
        {}, {"_id": 1, "player_name": 1, "props": 1}
    ):
        props_arr = cb_doc.get("props") or []
        if not props_arr:
            continue
        cb_player = cb_doc.get("player_name")
        if not cb_player:
            metrics["cached_board_skipped"] += 1
            continue
        # Resolve player team via master_hub (do NOT trust cached_board's
        # stale `event_id` — cached_board may not have been refreshed
        # for today's slate). Then map team_abbr → today's opponent
        # using the slate-wide `team_to_opp_today` map built from
        # live_props in Step A.
        mh_doc = await db["nba_master_hub_2026"].find_one(
            {"display_name": cb_player},
            {"_id": 0, "team_abbr": 1, "team": 1},
        )
        team_abbr = (mh_doc or {}).get("team_abbr") or (mh_doc or {}).get("team")
        if not team_abbr:
            metrics["cached_board_skipped"] += 1
            continue
        opp = team_to_opp_today.get(team_abbr)
        if not opp:
            # Player's team is not playing today → no fresh opponent.
            metrics["cached_board_skipped"] += 1
            continue

        # Group this player's prop indexes by stat_canon
        idx_by_stat: dict = defaultdict(list)
        for prop_entry in props_arr:
            stat_canon = _canon_stat(prop_entry.get("stat_type"))
            if not stat_canon:
                continue
            # Use raw stat_type for the arrayFilter match (cached_board
            # may store either the short code or alternate market
            # string; either way we match by raw stat_type).
            idx_by_stat[stat_canon].append(prop_entry.get("stat_type"))

        for stat_canon, raw_stats in idx_by_stat.items():
            cache_key = (opp, stat_canon)
            if cache_key not in cb_pair_cache:
                try:
                    _mod, md = momentum.calculate_momentum_modifier(opp, stat_canon)
                    cb_pair_cache[cache_key] = md
                except Exception:
                    cb_pair_cache[cache_key] = None
            md = cb_pair_cache[cache_key]
            if not md:
                continue
            unique_raw_stats = list({rs for rs in raw_stats if rs})
            if not unique_raw_stats:
                continue
            cb_bulk.append(
                UpdateMany(
                    {"_id": cb_doc["_id"]},
                    {"$set": {"props.$[el].momentum_data": md}},
                    array_filters=[{"el.stat_type": {"$in": unique_raw_stats}}],
                )
            )
            metrics["cached_board_updates"] += 1

    if cb_bulk:
        # Chunk bulk writes (Mongo bulk_write is fine up to a few thousand,
        # but explicit chunking guards against very large slates).
        CHUNK = 500
        for i in range(0, len(cb_bulk), CHUNK):
            await db["nba_cached_board"].bulk_write(
                cb_bulk[i : i + CHUNK], ordered=False
            )

    # 2026-05-04 SSOT: surface skip reasons + coverage at INFO so
    # missing-momentum coverage is observable post-run. Per
    # FIELD_OWNERSHIP.md:momentum_data the writer is the single owner
    # for this field; emitting the bucketed counters here is the only
    # source of truth for "why did N rows not get a momentum chip".
    coverage_pct = (
        round(metrics['props_enriched'] / metrics['props_total'] * 100.0, 2)
        if metrics.get('props_total') else 0.0
    )
    skip_breakdown = {k: v for k, v in skip.items() if v}
    logger.info(
        f"[MASTER_SYNC:nba] momentum_enrichment: total_candidates="
        f"{metrics['props_total']} enriched_count={metrics['props_enriched']} "
        f"skipped_count={metrics['props_skipped']} "
        f"coverage_pct={coverage_pct} "
        f"pairs={metrics['pairs_computed']}/{metrics['pairs_total']} "
        f"cached_board_updates={metrics.get('cached_board_updates', 0)} "
        f"skip_reasons={skip_breakdown}"
    )
    return metrics



# =====================================================================
# NBA Tempo / Pace Delta Read-Side Enrichment Helper
#
# Purpose:
#   Replace stale legacy `intel_suite.pace_delta` values currently sitting
#   in `nba_cached_board.props[*]` (written by the deleted
#   `optimized_sync_engine` / `cached_board_builder_service` ≈2026-04-21
#   with flat `team_pace=98.0, opp_pace=98.0, tempo_label="Neutral Pace"`
#   defaults across all teams) with current per-(team, opponent) output
#   from `IntelSuiteCalculator._calculate_pace_delta`.
#
# Strict scope:
#   - Read `nba_prop_scores` at `version_tag=final-nba-rt`
#   - Group by (team_abbr, opponent_team_abbr) — pace_delta is stat- and
#     line-agnostic, depending only on the matchup
#   - Call `IntelSuiteCalculator._calculate_pace_delta` once per pair
#   - Write `intel_suite.pace_delta` onto every matching score doc
#   - Mirror onto `nba_cached_board.props[*].intel_suite.pace_delta` for
#     all of the player's props (line/direction/stat agnostic)
#
# Out of scope (per directive):
#   - Scoring math / projections
#   - Gates / Universal Gate Engine / thresholds
#   - ECDF / probability layer
#   - Recompute / Ferrari tier logic
#   - Frontend layout
# =====================================================================


async def _enrich_nba_pace_delta(db) -> dict:
    """
    Refresh `intel_suite.pace_delta` for every NBA prop on today's
    slate using the current `IntelSuiteCalculator._calculate_pace_delta`.

    Read-only with respect to scoring. Writes only the `pace_delta`
    sub-key of `intel_suite`, leaving every other intel_suite field
    untouched (`matchup_dvp`, `momentum_data`, `vision_insight`, etc.).
    """
    from collections import defaultdict
    from pymongo import UpdateMany
    from services.intel_suite_calculator import IntelSuiteCalculator

    metrics = {
        "props_total": 0,
        "props_enriched": 0,
        "props_skipped": 0,
        "skip_reasons": {},
        "pairs_total": 0,
        "pairs_computed": 0,
        "pairs_failed": 0,
        "pairs_no_profile": 0,
        "cached_board_updates": 0,
        "cached_board_skipped": 0,
        "stale_neutral_pace_before": 0,
    }
    skip = metrics["skip_reasons"]

    def _bump(reason: str, n: int = 1) -> None:
        skip[reason] = skip.get(reason, 0) + n
        metrics["props_skipped"] += n

    # ------------------------------------------------------------------
    # Step A: event_id → (home_abbr, away_abbr) and team_abbr → today's
    # opponent_abbr (slate-wide). Reuse the same maps the momentum
    # helper builds above; this helper rebuilds them locally to keep
    # the function self-contained.
    # ------------------------------------------------------------------
    event_map: dict = {}
    team_to_opp_today: dict = {}
    async for lp in db["nba_live_props"].find(
        {}, {"_id": 0, "event_id": 1, "home_team": 1, "away_team": 1}
    ):
        eid = lp.get("event_id")
        if not eid or eid in event_map:
            continue
        home = _to_team_abbr(lp.get("home_team"))
        away = _to_team_abbr(lp.get("away_team"))
        if home and away:
            event_map[eid] = (home, away)
            team_to_opp_today.setdefault(home, away)
            team_to_opp_today.setdefault(away, home)

    # ------------------------------------------------------------------
    # Step B: bdl_player_id → team_abbr from `nba_master_hub_2026`
    # ------------------------------------------------------------------
    player_team: dict = {}
    async for mh in db["nba_master_hub_2026"].find(
        {}, {"_id": 0, "bdl_id": 1, "bdl_player_id": 1, "team_abbr": 1, "team": 1}
    ):
        pid = mh.get("bdl_id") or mh.get("bdl_player_id")
        ta = mh.get("team_abbr") or mh.get("team")
        if pid is None or not ta:
            continue
        try:
            player_team[int(pid)] = ta
        except (TypeError, ValueError):
            continue

    # ------------------------------------------------------------------
    # Stale fingerprint count (before)
    # ------------------------------------------------------------------
    metrics["stale_neutral_pace_before"] = await db["nba_cached_board"].count_documents(
        {"props.intel_suite.pace_delta.tempo_label": "Neutral Pace"}
    )

    # ------------------------------------------------------------------
    # Step C: Walk score docs, derive (team_abbr, opp_abbr) per doc,
    # group by canonical_key for bulk write.
    # ------------------------------------------------------------------
    bulk_by_pair: dict = defaultdict(list)
    cursor = db["nba_prop_scores"].find(
        {"version_tag": NBA_LIVE},
        {
            "_id": 0, "event_id": 1, "bdl_player_id": 1,
            "canonical_key": 1, "player_name": 1,
        },
    )

    async for d in cursor:
        metrics["props_total"] += 1
        ck = d.get("canonical_key")
        eid = d.get("event_id")
        bid = d.get("bdl_player_id")

        if not ck:
            _bump("no_canonical_key")
            continue
        if not eid or eid not in event_map:
            _bump("no_event_match")
            continue
        if bid is None:
            _bump("no_bdl_id")
            continue
        try:
            team_abbr = player_team.get(int(bid))
        except (TypeError, ValueError):
            team_abbr = None
        if not team_abbr:
            _bump("no_team_lookup")
            continue

        home, away = event_map[eid]
        if team_abbr == home:
            opp = away
        elif team_abbr == away:
            opp = home
        else:
            _bump("team_not_in_event")
            continue

        bulk_by_pair[(team_abbr, opp)].append(ck)

    # ------------------------------------------------------------------
    # Step D: Compute pace_delta once per (team, opp) pair via the
    # current producer. `_calculate_pace_delta` is a pure synchronous
    # table lookup with no I/O, so we call it inline.
    # ------------------------------------------------------------------
    calc = IntelSuiteCalculator(db)
    pair_cache: dict = {}
    metrics["pairs_total"] = len(bulk_by_pair)

    bulk_ops: list = []
    for (team_abbr, opp_abbr), keys in bulk_by_pair.items():
        try:
            pd = calc._calculate_pace_delta(team_abbr, opp_abbr, None)
            metrics["pairs_computed"] += 1
        except Exception as exc:
            logger.warning(
                f"[MASTER_SYNC:nba] pace_delta calc failed for "
                f"{team_abbr}/{opp_abbr}: {exc}"
            )
            metrics["pairs_failed"] += 1
            _bump("pace_delta_calc_failed", len(keys))
            continue

        if not pd:
            metrics["pairs_no_profile"] += 1
            _bump("no_pace_delta_profile", len(keys))
            continue

        pair_cache[(team_abbr, opp_abbr)] = pd
        # Write `intel_suite.pace_delta` onto matching score docs.
        # `$set` on a nested path creates the parent `intel_suite` sub-doc
        # if absent (score docs do not currently carry intel_suite). We
        # write only this single sub-key — no other intel_suite fields
        # are touched.
        bulk_ops.append(
            UpdateMany(
                {
                    "version_tag": NBA_LIVE,
                    "canonical_key": {"$in": keys},
                },
                {"$set": {"intel_suite.pace_delta": pd}},
            )
        )
        metrics["props_enriched"] += len(keys)

    if bulk_ops:
        await db["nba_prop_scores"].bulk_write(bulk_ops, ordered=False)

    # ------------------------------------------------------------------
    # Step E: Mirror into `nba_cached_board.props[*].intel_suite.pace_delta`
    # so that the existing `routes/ferrari_tiers.py` and
    # `routes/player.py` overlay paths (which read intel_suite from
    # cached_board, NOT from score docs) deliver fresh values to the UI.
    # pace_delta is line/direction/stat-agnostic, so we update ALL of a
    # player's cached_board props in a single arrayFilters write per
    # player.
    # ------------------------------------------------------------------
    cb_bulk: list = []
    async for cb_doc in db["nba_cached_board"].find(
        {}, {"_id": 1, "player_name": 1}
    ):
        cb_player = cb_doc.get("player_name")
        if not cb_player:
            metrics["cached_board_skipped"] += 1
            continue
        mh_doc = await db["nba_master_hub_2026"].find_one(
            {"display_name": cb_player},
            {"_id": 0, "team_abbr": 1, "team": 1},
        )
        team_abbr = (mh_doc or {}).get("team_abbr") or (mh_doc or {}).get("team")
        if not team_abbr:
            metrics["cached_board_skipped"] += 1
            continue
        opp = team_to_opp_today.get(team_abbr)
        if not opp:
            metrics["cached_board_skipped"] += 1
            continue
        pd = pair_cache.get((team_abbr, opp))
        if pd is None:
            try:
                pd = calc._calculate_pace_delta(team_abbr, opp, None)
                pair_cache[(team_abbr, opp)] = pd
            except Exception:
                metrics["cached_board_skipped"] += 1
                continue
        if not pd:
            metrics["cached_board_skipped"] += 1
            continue
        # Update every prop in this player's cached_board doc — pace
        # depends only on (team, opp), not on stat or line. arrayFilter
        # `el: {}` matches all elements of the props array.
        cb_bulk.append(
            UpdateMany(
                {"_id": cb_doc["_id"]},
                {"$set": {"props.$[el].intel_suite.pace_delta": pd}},
                array_filters=[{"el": {"$exists": True}}],
            )
        )
        metrics["cached_board_updates"] += 1

    if cb_bulk:
        CHUNK = 500
        for i in range(0, len(cb_bulk), CHUNK):
            await db["nba_cached_board"].bulk_write(
                cb_bulk[i : i + CHUNK], ordered=False
            )

    # Stale fingerprint count (after) — for parity with the metric we
    # surface in the sync result.
    metrics["stale_neutral_pace_after"] = await db["nba_cached_board"].count_documents(
        {"props.intel_suite.pace_delta.tempo_label": "Neutral Pace"}
    )

    logger.info(
        f"[MASTER_SYNC:nba] pace_delta_enrichment: enriched="
        f"{metrics['props_enriched']}/{metrics['props_total']} "
        f"pairs={metrics['pairs_computed']}/{metrics['pairs_total']} "
        f"cb_updates={metrics['cached_board_updates']} "
        f"stale_before={metrics['stale_neutral_pace_before']} "
        f"stale_after={metrics.get('stale_neutral_pace_after', 0)}"
    )
    return metrics


# =====================================================================
# NBA Vision Intel Read-Side Enrichment Helper (Active Board Only)
#
# Purpose:
#   Replace deterministic `_generate_vision_fallback` template text with
#   real Gemini-authored narratives ONLY for picks currently visible on
#   the user's board (Safe Haven, Front Lines, War Zone). No full-slate
#   sweep — content-hash cache + 75-pick cap keep API usage minimal.
#
# Strict scope:
#   - Query `nba_prop_scores` at `version_tag=final-nba-rt` AND
#     `tier ∈ {safe_haven, front_lines, war_zone}` AND `active=True`
#   - Compute `_vision_intel_content_hash`; skip if stored hash matches
#     and `vision_intel` is non-empty (cache hit)
#   - Cap remaining new/changed picks at MAX_BOARD_VISION_INTEL_PICKS
#   - Group by tier, call `VisionIntelService.analyze_tier_batch(...,
#     strict=True)` once per tier
#   - Persist on `nba_prop_scores`: vision_intel, vision_intel_content_hash,
#     vision_intel_generated_at
#   - Mirror onto `nba_cached_board.props[*].vision_intel` for
#     non-board reads that already overlay cached_board
#
# Out of scope (per directive):
#   - Scoring math / projections
#   - Gates / Universal Gate Engine / thresholds
#   - ECDF / probability layer
#   - Recompute / Ferrari tier logic (read-only consumer)
#   - Frontend
# =====================================================================

MAX_BOARD_VISION_INTEL_PICKS = 200
# Per-tier caps sized to comfortably cover real-world slate distributions
# (`front_lines` regularly has 100+ active score docs; `safe_haven` /
# `war_zone` rarely exceed 30). With three batched Gemini calls per
# recompute the cost is bounded; runaway protection is via the global
# `MAX_BOARD_VISION_INTEL_PICKS` ceiling.
PICKS_PER_TIER_CAP = {
    "war_zone": 50,
    "front_lines": 120,
    "safe_haven": 50,
}

# Vision Intel enrichment universe (2026-05-05 hot-fix).
# Pulls visible tier picks via the SAME universal board reader the
# `/api/v3/(mlb/)?ferrari/{tier}` endpoints use — NOT a bulk query
# against the active master pool. Without this, MLB FL alone admits
# ~9,000 active score docs, none of which are visible cards. The
# limit per tier matches the route's `Query(le=50)` ceiling so any
# UI request fits inside the enriched set.
VISION_INTEL_TIERS = ("safe_haven", "front_lines", "war_zone")
VISION_INTEL_FETCH_LIMIT_PER_TIER = 50


async def _attach_badges_in_memory(
    picks: list, sport: str, db
) -> dict:
    """Attach `scout_badges` and `context_badges` to in-memory pick dicts
    BEFORE `analyze_tier_batch` is called (CHANGELOG 2026-05-05 Finding A).

    Performance and context badges are NOT persisted to `*_prop_scores` —
    they're stamped at API request time only. Vision Intel reads
    directly from `*_prop_scores`, so without this step Gemini receives
    `"badges": "None", "context": "None"` in every prompt body and the
    Option C semantic-bucket plumbing is dead-on-arrival.

    Mutates `picks` in place. Read-only with respect to the DB:
      * `scout_badges` derived locally via the universal generator.
      * `context_badges` looked up from `{sport}_master_hub_2026`
        (current SSOT). If the master_hub doc is missing or the
        `context_badges` field is empty, the pick is simply not
        annotated — Vision Intel renders `"context": "None"` for that
        slot.

    Returns a metrics dict for observability:
        {scout_attached, context_attached, master_hub_lookups}
    """
    if not picks:
        return {"scout_attached": 0, "context_attached": 0, "master_hub_lookups": 0}

    from services.performance_badges import generate_performance_badges
    from config.db_config import get_collection_name

    metrics = {"scout_attached": 0, "context_attached": 0, "master_hub_lookups": 0}

    # 1. scout_badges — pure function, no DB hit per pick.
    for p in picks:
        sb = generate_performance_badges(p)
        if sb:
            p["scout_badges"] = sb
            metrics["scout_attached"] += 1

    # 2. context_badges — single batched lookup against master_hub.
    names = sorted({p.get("player_name") for p in picks if p.get("player_name")})
    if names:
        master_hub = db[get_collection_name("master_hub", sport)]
        ctx_by_name: dict = {}
        async for hub in master_hub.find(
            {"display_name": {"$in": list(names)}},
            {"_id": 0, "display_name": 1, "context_badges": 1},
        ):
            cb = hub.get("context_badges") or []
            if cb:
                ctx_by_name[hub.get("display_name")] = cb
        metrics["master_hub_lookups"] = len(names)
        for p in picks:
            cb = ctx_by_name.get(p.get("player_name"))
            if cb:
                p["context_badges"] = cb
                metrics["context_attached"] += 1

    return metrics




async def _enrich_nba_board_vision_intel(db) -> dict:
    """
    Generate Gemini Vision Intel narratives for active-board NBA picks
    (Safe Haven + Front Lines + War Zone only). Read-only with respect
    to scoring.
    """
    from datetime import datetime, timezone
    from collections import defaultdict
    from pymongo import UpdateMany
    from routes.ferrari_tiers import (
        _vision_intel_content_hash,
        _is_cache_fresh,
    )
    from services.vision_intel_service import get_vision_intel_service

    metrics = {
        "safe_haven_count":       0,
        "front_lines_count":      0,
        "war_zone_count":         0,
        "total_visible_picks":    0,
        "board_picks_total":      0,   # alias of total_visible_picks (legacy)
        "cache_hits":             0,
        "cache_miss_to_call":     0,
        "capped_at":              MAX_BOARD_VISION_INTEL_PICKS,
        "per_tier_caps":          PICKS_PER_TIER_CAP,
        "fetch_limit_per_tier":   VISION_INTEL_FETCH_LIMIT_PER_TIER,
        "after_cap_to_call":      0,
        "to_call":                0,
        "gemini_calls":           0,
        "tiers_called":           {},
        "gemini_returned":        0,
        "gemini_empty_or_failed": 0,
        "score_docs_written":     0,
        "cached_board_writes":    0,
        "skip_reasons":           {},
        "fallback_in_db_after":   0,
    }
    skip = metrics["skip_reasons"]

    # Service availability check (no point selecting picks if disabled).
    vis = get_vision_intel_service()
    if not getattr(vis, "enabled", False):
        metrics["skip_reasons"]["service_disabled"] = (
            "VisionIntelService.enabled=False (missing GOOGLE_API_KEY or SDK)"
        )
        return metrics

    # ------------------------------------------------------------------
    # Step A: Pull VISIBLE tier picks via the universal board reader —
    # the SAME path `/api/v3/ferrari/{tier}` serves to the dashboard.
    # `get_board()` applies the published board reconcile + per-player
    # dedup, so a tier returns at most one pick per player ordered by
    # the adapter's tier sort key. Limit per tier mirrors the route's
    # `Query(le=50)` ceiling so any UI request fits inside the enriched
    # set. Replaces the old bulk
    # `nba_prop_scores.find({tier ∈ BOARD_TIERS, active=True})` query
    # which admitted hundreds of non-visible active props and starved
    # the per-tier cap (CHANGELOG 2026-05-05 enrichment-universe fix).
    # ------------------------------------------------------------------
    from services.board.reader import get_board

    board_picks: list = []
    per_tier_visible: dict = {}
    for tier_name in VISION_INTEL_TIERS:
        tier_picks = await get_board(
            db, sport="nba", tier=tier_name,
            limit=VISION_INTEL_FETCH_LIMIT_PER_TIER,
        )
        per_tier_visible[tier_name] = len(tier_picks)
        board_picks.extend(tier_picks)

    metrics["safe_haven_count"]    = per_tier_visible.get("safe_haven", 0)
    metrics["front_lines_count"]   = per_tier_visible.get("front_lines", 0)
    metrics["war_zone_count"]      = per_tier_visible.get("war_zone", 0)
    metrics["total_visible_picks"] = len(board_picks)
    metrics["board_picks_total"]   = len(board_picks)  # legacy alias

    if not board_picks:
        return metrics

    # ------------------------------------------------------------------
    # Step B: Content-hash cache filter. A pick is skipped iff its
    # currently-stored hash matches the freshly-computed hash AND
    # vision_intel is already populated.
    # ------------------------------------------------------------------
    to_call: list = []
    for p in board_picks:
        cached_view = {
            "vision_intel": p.get("vision_intel"),
            "vision_intel_content_hash": p.get("vision_intel_content_hash"),
        }
        if _is_cache_fresh(p, cached_view):
            metrics["cache_hits"] += 1
            continue
        to_call.append(p)
    metrics["cache_miss_to_call"] = len(to_call)
    metrics["to_call"]            = len(to_call)  # canonical name (2026-05-05)

    # Cap. Apply a per-tier cap first (so a populous tier like
    # `front_lines` cannot starve the smaller `safe_haven`/`war_zone`
    # board), then enforce the global cap. Within each tier, sort by
    # descending `vision_score` so the most actionable picks always win
    # the cap fight. With `get_board()` upstream, the visible universe
    # is small enough that this cap rarely fires — kept as a safety
    # net only.
    if to_call:
        by_tier_cap: dict = defaultdict(list)
        for p in to_call:
            by_tier_cap[p.get("tier")].append(p)
        capped: list = []
        for tier_name in ("war_zone", "front_lines", "safe_haven"):
            tier_picks = by_tier_cap.get(tier_name, [])
            tier_picks.sort(
                key=lambda d: float(d.get("vision_score") or 0), reverse=True
            )
            capped.extend(tier_picks[:PICKS_PER_TIER_CAP.get(tier_name, 50)])
        if len(capped) > MAX_BOARD_VISION_INTEL_PICKS:
            capped = capped[:MAX_BOARD_VISION_INTEL_PICKS]
        to_call = capped
    metrics["after_cap_to_call"] = len(to_call)
    metrics["to_call"]           = len(to_call)  # canonical name (2026-05-05)

    if not to_call:
        return metrics

    # ------------------------------------------------------------------
    # Step C: Group by tier. analyze_tier_batch issues ONE Gemini call
    # per tier (the existing batched prompt path).
    # ------------------------------------------------------------------
    by_tier: dict = defaultdict(list)
    for p in to_call:
        by_tier[p.get("tier")].append(p)

    # ------------------------------------------------------------------
    # Step C.5: Attach in-memory `scout_badges` + `context_badges` to
    # each pick BEFORE analyze_tier_batch (CHANGELOG 2026-05-05 Finding
    # A). Read-only DB lookups; nothing persisted back.
    # ------------------------------------------------------------------
    badge_metrics = await _attach_badges_in_memory(to_call, "nba", db)
    metrics["badges_attached"] = badge_metrics

    # ------------------------------------------------------------------
    # Step D: Run Gemini per tier. `strict=True` → no fallback substitution.
    # Slots Gemini fails to fill come back as None and stay uncovered.
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    score_bulk: list = []
    cb_pairs: list = []  # (player_name, stat_type, line, direction, vi_text)

    for tier_name, tier_picks in by_tier.items():
        # Build the prop dicts shape expected by analyze_tier_batch —
        # mirrors what `_enrich_under_picks_with_gemini` passes today.
        # Chunk: Gemini's batched response truncates above ~20 props per
        # call, so split into deterministic chunks before invoking.
        CHUNK = 20
        results: list = []
        chunk_failed = False
        for i in range(0, len(tier_picks), CHUNK):
            chunk = tier_picks[i : i + CHUNK]
            metrics["gemini_calls"] += 1
            try:
                chunk_results = await vis.analyze_tier_batch(
                    chunk, tier_name, strict=True
                )
            except Exception as exc:
                logger.warning(
                    f"[MASTER_SYNC:nba] vision_intel chunk failed "
                    f"for {tier_name} ({i}-{i+len(chunk)}): {exc}"
                )
                results.extend([None] * len(chunk))
                chunk_failed = True
                continue
            results.extend(chunk_results or [None] * len(chunk))
        if chunk_failed:
            skip[f"chunk_failed_{tier_name}"] = True

        # Pair Gemini results with source picks via EXACT canonical_key
        # lookup — NOT positional zip. `analyze_tier_batch` re-sorts the
        # output by `composite_score`, which breaks order alignment with
        # `tier_picks` (sorted earlier by `vision_score` from the cap
        # step). Positional zip would stamp the wrong narrative on the
        # wrong canonical_key. CHANGELOG 2026-05-05 prop_id mis-mapping
        # fix. `_merge_intel_to_prop` does `enriched = {**prop}` so
        # `canonical_key` round-trips. Results without a canonical_key
        # in the source batch are silently discarded.
        out_by_ck = {
            o.get("canonical_key"): o
            for o in results
            if o and o.get("canonical_key")
        }
        tier_returned = 0
        tier_empty = 0
        for src in tier_picks:
            ck = src.get("canonical_key")
            if not ck:
                tier_empty += 1
                continue
            out = out_by_ck.get(ck)
            vi = ((out or {}).get("vision_intel") or "").strip() if out else ""
            if not vi:
                tier_empty += 1
                continue
            tier_returned += 1
            content_hash = _vision_intel_content_hash(src)
            score_bulk.append(
                UpdateMany(
                    {
                        "canonical_key": ck,
                        "version_tag": NBA_LIVE,
                    },
                    {
                        "$set": {
                            "vision_intel": vi,
                            "vision_intel_content_hash": content_hash,
                            "vision_intel_generated_at": now,
                        }
                    },
                )
            )
            cb_pairs.append((
                src.get("player_name"),
                src.get("stat_type"),
                src.get("line"),
                (src.get("direction") or src.get("recommendation") or "Over"),
                vi,
            ))

        metrics["tiers_called"][tier_name] = {
            "selected": len(tier_picks),
            "gemini_returned": tier_returned,
            "gemini_empty": tier_empty,
        }
        metrics["gemini_returned"] += tier_returned
        metrics["gemini_empty_or_failed"] += tier_empty

    if score_bulk:
        await db["nba_prop_scores"].bulk_write(score_bulk, ordered=False)
        metrics["score_docs_written"] = len(score_bulk)

    # ------------------------------------------------------------------
    # Step E: Mirror onto `nba_cached_board.props[*].vision_intel` so
    # the existing `routes/player.py` cached_board overlay (already
    # whitelists `vision_intel` in `_BOARD_ENRICHMENT_FIELDS`) and
    # `routes/ferrari_tiers.py` (which reads `vision_intel` from
    # cached_board) deliver Gemini-authored text to the UI without
    # touching scoring.
    # ------------------------------------------------------------------
    cb_bulk: list = []
    for player_name, stat_type, line, direction, vi in cb_pairs:
        if not (player_name and stat_type):
            continue
        # Match by player_name (cached_board is keyed per player) and
        # the (stat_type, line, direction) prop element — this exactly
        # mirrors what cached_board uses upstream.
        cb_bulk.append(
            UpdateMany(
                {"player_name": player_name},
                {"$set": {"props.$[el].vision_intel": vi}},
                array_filters=[{
                    "el.stat_type": stat_type,
                    "el.line": line,
                    "el.direction": direction,
                }],
            )
        )

    if cb_bulk:
        CHUNK = 500
        for i in range(0, len(cb_bulk), CHUNK):
            await db["nba_cached_board"].bulk_write(
                cb_bulk[i : i + CHUNK], ordered=False
            )
        metrics["cached_board_writes"] = len(cb_bulk)

    # Coverage sanity: count props that still carry the fallback
    # template fingerprint AFTER the run (informational only).
    metrics["fallback_in_db_after"] = await db["nba_prop_scores"].count_documents(
        {
            "version_tag": NBA_LIVE,
            "tier": {"$in": list(VISION_INTEL_TIERS)},
            "$or": [
                {"vision_intel": None},
                {"vision_intel": ""},
                {"vision_intel": {"$exists": False}},
            ],
        }
    )

    logger.info(
        f"[MASTER_SYNC:nba] vision_intel_enrichment: "
        f"safe_haven={metrics['safe_haven_count']} "
        f"front_lines={metrics['front_lines_count']} "
        f"war_zone={metrics['war_zone_count']} "
        f"total_visible={metrics['total_visible_picks']} "
        f"cache_hits={metrics['cache_hits']} "
        f"to_call={metrics['to_call']} "
        f"gemini_calls={metrics['gemini_calls']} "
        f"gemini_returned={metrics['gemini_returned']} "
        f"empty={metrics['gemini_empty_or_failed']} "
        f"score_writes={metrics['score_docs_written']} "
        f"cb_writes={metrics['cached_board_writes']}"
    )
    return metrics


async def _enrich_mlb_board_vision_intel(db) -> dict:
    """Generate Gemini Vision Intel narratives for active-board MLB picks
    (Safe Haven + Front Lines + War Zone only). MLB mirror of
    `_enrich_nba_board_vision_intel` (2026-05-05 wire-up). Read-only with
    respect to scoring — only writes the post-recompute fields
    `vision_intel`, `vision_intel_content_hash`, `vision_intel_generated_at`
    to `mlb_prop_scores` and mirrors `vision_intel` onto
    `mlb_cached_board.props[]`. Caps + per-tier ceilings + content-hash
    cache mirror NBA so cost envelope is bounded the same way.

    Differences from the NBA function:
      * Reads `mlb_prop_scores` at `MLB_LIVE`.
      * Calls `MLBVisionIntel.analyze_tier_batch(strict=True)` so failed
        / empty Gemini slots come back as empty strings (we do NOT
        write the deterministic fallback templates to the DB).
      * Mirrors onto `mlb_cached_board` with the same `(player_name,
        stat_type, line, direction)` array_filter pattern.
    """
    from datetime import datetime, timezone
    from collections import defaultdict
    from pymongo import UpdateMany
    from routes.ferrari_tiers import (
        _vision_intel_content_hash,
        _is_cache_fresh,
    )
    from services.mlb_vision_intel import get_mlb_vision_intel

    metrics = {
        "safe_haven_count":       0,
        "front_lines_count":      0,
        "war_zone_count":         0,
        "total_visible_picks":    0,
        "board_picks_total":      0,   # alias of total_visible_picks (legacy)
        "cache_hits":             0,
        "cache_miss_to_call":     0,
        "capped_at":              MAX_BOARD_VISION_INTEL_PICKS,
        "per_tier_caps":          PICKS_PER_TIER_CAP,
        "fetch_limit_per_tier":   VISION_INTEL_FETCH_LIMIT_PER_TIER,
        "after_cap_to_call":      0,
        "to_call":                0,
        "gemini_calls":           0,
        "tiers_called":           {},
        "gemini_returned":        0,
        "gemini_empty_or_failed": 0,
        "score_docs_written":     0,
        "cached_board_writes":    0,
        "skip_reasons":           {},
        "fallback_in_db_after":   0,
    }
    skip = metrics["skip_reasons"]

    vis = get_mlb_vision_intel()
    if not getattr(vis, "enabled", False):
        skip["service_disabled"] = (
            "MLBVisionIntel.enabled=False (missing GOOGLE_API_KEY or SDK)"
        )
        return metrics

    # ── Step A: Pull VISIBLE tier picks via the universal board reader ─
    # SAME path `/api/v3/mlb/ferrari/{tier}` serves to the dashboard.
    # `get_board()` applies the published board reconcile + per-player
    # dedup so each tier returns at most one pick per player ordered by
    # the adapter's tier sort key. Replaces the old bulk
    # `mlb_prop_scores.find({tier ∈ BOARD_TIERS, active=True})` query
    # which admitted ~9,000 active FL props (none visible) and starved
    # the cap (CHANGELOG 2026-05-05 enrichment-universe fix). MLB and
    # NBA paths now match.
    from services.board.reader import get_board

    board_picks: list = []
    per_tier_visible: dict = {}
    for tier_name in VISION_INTEL_TIERS:
        tier_picks = await get_board(
            db, sport="mlb", tier=tier_name,
            limit=VISION_INTEL_FETCH_LIMIT_PER_TIER,
        )
        per_tier_visible[tier_name] = len(tier_picks)
        board_picks.extend(tier_picks)

    metrics["safe_haven_count"]    = per_tier_visible.get("safe_haven", 0)
    metrics["front_lines_count"]   = per_tier_visible.get("front_lines", 0)
    metrics["war_zone_count"]      = per_tier_visible.get("war_zone", 0)
    metrics["total_visible_picks"] = len(board_picks)
    metrics["board_picks_total"]   = len(board_picks)  # legacy alias

    if not board_picks:
        return metrics

    # ── Step B: Content-hash cache filter ─────────────────────────────
    to_call: list = []
    for p in board_picks:
        cached_view = {
            "vision_intel":              p.get("vision_intel"),
            "vision_intel_content_hash": p.get("vision_intel_content_hash"),
        }
        if _is_cache_fresh(p, cached_view):
            metrics["cache_hits"] += 1
            continue
        to_call.append(p)
    metrics["cache_miss_to_call"] = len(to_call)
    metrics["to_call"]            = len(to_call)  # canonical name (2026-05-05)

    # ── Cap (per-tier first, then global). Visible universe is small
    # enough post-`get_board()` that this cap rarely fires — kept as a
    # safety net only. ────────────────────────────────────────────────
    if to_call:
        by_tier_cap: dict = defaultdict(list)
        for p in to_call:
            by_tier_cap[p.get("tier")].append(p)
        capped: list = []
        for tier_name in ("war_zone", "front_lines", "safe_haven"):
            tier_picks = by_tier_cap.get(tier_name, [])
            tier_picks.sort(
                key=lambda d: float(d.get("vision_score") or 0), reverse=True
            )
            capped.extend(tier_picks[:PICKS_PER_TIER_CAP.get(tier_name, 50)])
        if len(capped) > MAX_BOARD_VISION_INTEL_PICKS:
            capped = capped[:MAX_BOARD_VISION_INTEL_PICKS]
        to_call = capped
    metrics["after_cap_to_call"] = len(to_call)
    metrics["to_call"]           = len(to_call)  # canonical name (2026-05-05)

    if not to_call:
        return metrics

    # ── Step C: Group by tier ─────────────────────────────────────────
    by_tier: dict = defaultdict(list)
    for p in to_call:
        by_tier[p.get("tier")].append(p)

    # ── Step C.5: in-memory badge attachment (Finding A) ─────────────
    # Mirrors NBA. Read-only DB lookups against `mlb_master_hub_2026`;
    # nothing persisted back to score docs.
    badge_metrics = await _attach_badges_in_memory(to_call, "mlb", db)
    metrics["badges_attached"] = badge_metrics

    # ── Step D: Gemini per tier (chunked + strict) ────────────────────
    now = datetime.now(timezone.utc)
    score_bulk: list = []
    cb_pairs: list = []  # (player_name, stat_type, line, direction, vi_text)

    for tier_name, tier_picks in by_tier.items():
        CHUNK = 20
        results: list = []
        chunk_failed = False
        for i in range(0, len(tier_picks), CHUNK):
            chunk = tier_picks[i: i + CHUNK]
            metrics["gemini_calls"] += 1
            try:
                chunk_results = await vis.analyze_tier_batch(
                    chunk, tier_name, strict=True
                )
            except Exception as exc:
                logger.warning(
                    f"[MASTER_SYNC:mlb] vision_intel chunk failed "
                    f"for {tier_name} ({i}-{i+len(chunk)}): {exc}"
                )
                results.extend([None] * len(chunk))
                chunk_failed = True
                continue
            results.extend(chunk_results or [None] * len(chunk))
        if chunk_failed:
            skip[f"chunk_failed_{tier_name}"] = True

        # Pair Gemini results with source picks via EXACT canonical_key
        # lookup — NOT positional zip. See NBA path comment above.
        # MLB mirror of the same fix; positional zip was stamping
        # narratives on the wrong canonical_keys (e.g. "Witt" content
        # landing on Josh Jung's row).
        out_by_ck = {
            o.get("canonical_key"): o
            for o in results
            if o and o.get("canonical_key")
        }
        tier_returned = 0
        tier_empty = 0
        for src in tier_picks:
            ck = src.get("canonical_key")
            if not ck:
                tier_empty += 1
                continue
            out = out_by_ck.get(ck)
            vi = ((out or {}).get("vision_intel") or "").strip() if out else ""
            if not vi:
                tier_empty += 1
                continue
            tier_returned += 1
            content_hash = _vision_intel_content_hash(src)
            score_bulk.append(
                UpdateMany(
                    {
                        "canonical_key": ck,
                        "version_tag":   MLB_LIVE,
                    },
                    {
                        "$set": {
                            "vision_intel":              vi,
                            "vision_intel_content_hash": content_hash,
                            "vision_intel_generated_at": now,
                        }
                    },
                )
            )
            cb_pairs.append((
                src.get("player_name"),
                src.get("stat_type"),
                src.get("line"),
                (src.get("direction") or src.get("recommendation") or "Over"),
                vi,
            ))

        metrics["tiers_called"][tier_name] = {
            "selected":        len(tier_picks),
            "gemini_returned": tier_returned,
            "gemini_empty":    tier_empty,
        }
        metrics["gemini_returned"] += tier_returned
        metrics["gemini_empty_or_failed"] += tier_empty

    if score_bulk:
        await db["mlb_prop_scores"].bulk_write(score_bulk, ordered=False)
        metrics["score_docs_written"] = len(score_bulk)

    # ── Step E: Mirror onto mlb_cached_board.props[] ──────────────────
    cb_bulk: list = []
    for player_name, stat_type, line, direction, vi in cb_pairs:
        if not (player_name and stat_type):
            continue
        cb_bulk.append(
            UpdateMany(
                {"player_name": player_name},
                {"$set": {"props.$[el].vision_intel": vi}},
                array_filters=[{
                    "el.stat_type": stat_type,
                    "el.line":      line,
                    "el.direction": direction,
                }],
            )
        )

    if cb_bulk:
        CHUNK = 500
        for i in range(0, len(cb_bulk), CHUNK):
            await db["mlb_cached_board"].bulk_write(
                cb_bulk[i: i + CHUNK], ordered=False
            )
        metrics["cached_board_writes"] = len(cb_bulk)

    metrics["fallback_in_db_after"] = await db["mlb_prop_scores"].count_documents(
        {
            "version_tag": MLB_LIVE,
            "tier":        {"$in": list(VISION_INTEL_TIERS)},
            "$or": [
                {"vision_intel": None},
                {"vision_intel": ""},
                {"vision_intel": {"$exists": False}},
            ],
        }
    )

    logger.info(
        f"[MASTER_SYNC:mlb] vision_intel_enrichment: "
        f"safe_haven={metrics['safe_haven_count']} "
        f"front_lines={metrics['front_lines_count']} "
        f"war_zone={metrics['war_zone_count']} "
        f"total_visible={metrics['total_visible_picks']} "
        f"cache_hits={metrics['cache_hits']} "
        f"to_call={metrics['to_call']} "
        f"gemini_calls={metrics['gemini_calls']} "
        f"gemini_returned={metrics['gemini_returned']} "
        f"empty={metrics['gemini_empty_or_failed']} "
        f"score_writes={metrics['score_docs_written']} "
        f"cb_writes={metrics['cached_board_writes']}"
    )
    return metrics
