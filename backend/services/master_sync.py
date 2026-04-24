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

    completed = datetime.now(timezone.utc)
    metrics["completed_at"] = completed.isoformat()
    metrics["total_duration_seconds"] = (completed - started).total_seconds()
    metrics["success"] = len(metrics["errors"]) == 0

    logger.info(
        f"[MASTER_SYNC:{sport}] COMPLETE in "
        f"{metrics['total_duration_seconds']:.1f}s success={metrics['success']}"
    )
    return metrics



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
        {"version_tag": "final-nba-rt"},
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
                    "version_tag": "final-nba-rt",
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

    logger.info(
        f"[MASTER_SYNC:nba] momentum_enrichment: enriched="
        f"{metrics['props_enriched']}/{metrics['props_total']} "
        f"pairs={metrics['pairs_computed']}/{metrics['pairs_total']} "
        f"skipped={metrics['props_skipped']}"
    )
    return metrics
