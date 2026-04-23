"""
Admin Routes
=============
Administrative and cache management endpoints.
"""
import os
from fastapi import APIRouter, Header, HTTPException, Body
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin"])

# References set via dependency injection
_stats_manager = None
_db = None


def set_admin_deps(stats_manager, db):
    """Set admin route dependencies."""
    global _stats_manager, _db
    _stats_manager = stats_manager
    _db = db


def get_stats_manager():
    """Get the stats manager instance."""
    if _stats_manager is None:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    return _stats_manager


@router.get("/cache-status")
async def get_cache_status():
    """Get cache statistics"""
    stats = get_stats_manager()
    status = await stats.get_cache_status()
    return {"success": True, "data": status}


@router.post("/clear-expired-cache")
async def clear_expired_cache():
    """Clear expired cache entries"""
    stats = get_stats_manager()
    deleted_count = await stats.clear_expired_cache()
    return {"success": True, "deleted_count": deleted_count}


@router.post("/sync-rosters")
async def sync_rosters(force: bool = False):
    """
    Sync NBA rosters for all 30 teams
    This creates a global player database for fast lookups
    """
    stats = get_stats_manager()
    result = await stats.sync_nba_rosters(force=force)
    return {"success": True, "sync_result": result}


@router.post("/clear-all-cache")
async def clear_all_cache():
    """Clear ALL cache (use when changing seasons)"""
    stats = get_stats_manager()
    deleted_count = await stats.clear_all_cache()
    return {"success": True, "deleted_count": deleted_count, "reason": "Season change - cleared all 2024 data"}


@router.get("/todays-games")
async def get_todays_games():
    """Get today's NBA games from BallDontLie"""
    stats = get_stats_manager()
    result = await stats.get_todays_games_summary()
    return result


@router.post("/trigger-daily-sync")
async def trigger_daily_sync():
    """Manually trigger the autonomous daily sync"""
    stats = get_stats_manager()
    result = await stats.autonomous_daily_sync()
    return {"success": True, "sync_result": result}


@router.post("/sync-lakers-test")
async def sync_lakers_test():
    """
    Test Lakers roster sync for season 2025 using BallDontLie
    """
    stats = get_stats_manager()
    logger.info("Testing Lakers roster sync for season 2025 (BallDontLie)...")
    
    # Lakers team ID in BallDontLie is 14
    player_ids = await stats.sync_players_for_team(14)
    
    return {
        "success": True,
        "message": "Lakers roster synced successfully via BallDontLie",
        "players_synced": len(player_ids),
        "data_source": "BallDontLie API"
    }


@router.get("/rate-limit-status")
async def get_rate_limit_status():
    """
    Get current API rate limit status.
    
    Returns:
    - active_buckets: Number of active rate limit buckets
    - tiers: Configuration for each rate limit tier
    - enabled: Whether rate limiting is active
    """
    from middleware import get_rate_limit_storage, RATE_LIMIT_TIERS
    import os
    
    storage = get_rate_limit_storage()
    stats = storage.get_stats()
    
    tiers = {
        tier: {
            "requests_per_minute": config.requests_per_minute,
            "burst_size": config.burst_size
        }
        for tier, config in RATE_LIMIT_TIERS.items()
    }
    
    return {
        "success": True,
        "rate_limit": {
            "enabled": os.environ.get("RATE_LIMITING_ENABLED", "true").lower() == "true",
            "active_buckets": stats["active_buckets"],
            "last_cleanup": stats["last_cleanup"],
            "tiers": tiers
        }
    }


@router.get("/roster-status")
async def get_roster_status():
    """Get roster sync status and statistics"""
    stats = get_stats_manager()
    from config.settings import CURRENT_SEASON
    
    try:
        total_players = await stats.league_roster.count_documents({})
        
        # Get teams count
        teams = await stats.league_roster.distinct("team_name")
        
        # Get last sync time
        latest = await stats.league_roster.find_one(
            {},
            sort=[("synced_at", -1)]
        )
        
        last_synced = latest.get("synced_at") if latest else None
        
        return {
            "success": True,
            "total_players": total_players,
            "total_teams": len(teams),
            "teams": sorted(teams),
            "last_synced": last_synced,
            "season": CURRENT_SEASON
        }
    except Exception as e:
        logger.error(f"Roster status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== DVP MANAGEMENT ====================

@router.get("/dvp-status")
async def get_dvp_status():
    """
    Get DvP (Defense vs Position) service status.
    
    Returns current data source, cache age, and configuration.
    """
    from services.dvp_service import get_dvp_status as dvp_status
    
    status = dvp_status()
    return {
        "success": True,
        "dvp": status
    }


@router.post("/dvp-refresh")
async def trigger_dvp_refresh():
    """
    Manually trigger a DvP data refresh.
    
    This forces a fresh fetch from the BallDontLie API and updates
    both the in-memory cache and MongoDB storage.
    """
    from services.dvp_service import force_refresh_dvp
    
    try:
        result = await force_refresh_dvp()
        return {
            "success": result["success"],
            "message": "DvP refresh completed",
            "result": result
        }
    except Exception as e:
        logger.error(f"DvP refresh error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dvp-rankings")
async def get_dvp_rankings():
    """
    Get current DvP rankings data.
    
    Returns the full defensive rankings for all teams and stat categories.
    """
    from services.dvp_service import get_dvp_rankings_with_source
    
    try:
        rankings, headers = await get_dvp_rankings_with_source()
        
        return {
            "success": True,
            "headers": headers,
            "rankings": rankings,
            "stat_types": list(rankings.keys()) if rankings else [],
            "teams_count": len(next(iter(rankings.values()))) if rankings else 0
        }
    except Exception as e:
        logger.error(f"DvP rankings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dvp-analysis/{opponent_team}/{stat_type}")
async def get_dvp_analysis(opponent_team: str, stat_type: str, player_position: str = None):
    """
    Get DvP analysis for a specific matchup.
    
    Args:
        opponent_team: 3-letter team abbreviation (e.g., "LAL", "BOS")
        stat_type: Stat type (e.g., "PTS", "REB", "player_points")
        player_position: Optional player position for matchup multiplier (e.g., "C", "PG")
    
    Returns:
        Complete DvP analysis including modifier, label, rank, and matchup multiplier.
    """
    from services.dvp_service import get_full_dvp_analysis
    
    try:
        analysis = get_full_dvp_analysis(opponent_team.upper(), stat_type, player_position)
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"DvP analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# File download endpoints
from fastapi.responses import FileResponse

@router.get("/download/api-traffic-csv")
async def download_api_traffic_csv():
    """Download API traffic report as CSV"""
    return FileResponse(
        path="/app/frontend/public/api_traffic_report.csv",
        filename="propvision_api_traffic.csv",
        media_type="text/csv"
    )

@router.get("/download/unused-endpoints-csv")
async def download_unused_endpoints_csv():
    """Download unused endpoints as CSV"""
    return FileResponse(
        path="/app/frontend/public/unused_endpoints.csv",
        filename="propvision_unused_endpoints.csv",
        media_type="text/csv"
    )

@router.get("/download/backend-code-json")
async def download_backend_code_json():
    """Download backend code export as JSON"""
    return FileResponse(
        path="/app/frontend/public/backend_code_export.json",
        filename="propvision_backend_code.json",
        media_type="application/json"
    )

# ---------------------------------------------------------------------------
# Injury-Triggered Rescore Observability (internal / debug)
# ---------------------------------------------------------------------------
# Read-only snapshot of the in-process InjuryTriggeredRescore service so we
# can verify injury reactions without tailing logs. Protected by a shared
# secret env var (`ADMIN_DEBUG_TOKEN`): if unset the endpoint returns 503
# so it's off-by-default in any environment where the operator hasn't
# explicitly opted in. No DB I/O, no recompute side-effects — just the
# service's own in-memory counters.
# ---------------------------------------------------------------------------
def _require_admin_debug_token(provided: str | None) -> None:
    expected = os.environ.get("ADMIN_DEBUG_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_DEBUG_TOKEN not configured; debug endpoint disabled",
        )
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


@router.get("/injury-rescore-stats")
async def injury_rescore_stats(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Read-only snapshot of the injury-triggered rescore service.

    Minimal exposure (no DB I/O):
      - events_received
      - recomputes
      - last_trigger         (last event handled, incl. player / team / latency)
      - last_latency_ms
      - last_players_patched_count  (board_players_patched from last_trigger)

    Auth: `X-Admin-Token` must match env `ADMIN_DEBUG_TOKEN`. If the env var
    is unset the endpoint responds 503 (disabled by default).
    """
    _require_admin_debug_token(x_admin_token)

    from services.injury_triggered_rescore import get_rescore_service

    svc = get_rescore_service()
    stats = svc.stats()
    last = stats.get("last_trigger") or {}

    return {
        "events_received": stats.get("events_received", 0),
        "recomputes": stats.get("recomputes", 0),
        "last_latency_ms": stats.get("last_latency_ms", 0),
        "last_players_patched_count": last.get("board_players_patched", 0),
        "last_trigger": last or None,
    }


@router.get("/full-sync-stats")
async def full_sync_stats(
    sport: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Read-only snapshot of the last full sync (NBA and/or MLB).

    Source: `RebuildCoordinator._metrics['last_publish_counts'][sport]`
    which is written by `_execute_rebuild()` on every successful pipeline
    run. Zero new persistence, zero hot-path impact — we just reformat
    fields that are already captured in-memory by the coordinator.

    Query params:
      - ?sport=nba          → payload for NBA only
      - ?sport=mlb          → payload for MLB only
      - (no sport)          → combined payload: {"nba": {...}, "mlb": {...}}

    Per-sport returned fields:
      - last_full_sync_at           (ISO UTC; null if no run yet)
      - last_full_sync_duration_ms  (int ms; null if no run yet)
      - last_full_sync_props_written (sum of per-tier collection counts)
      - last_trigger                (event_type, source, success, run_id,
        collections)

    Auth: `X-Admin-Token` must match env `ADMIN_DEBUG_TOKEN`. Env unset ⇒
    503 (disabled by default); missing/wrong header ⇒ 401.
    """
    _require_admin_debug_token(x_admin_token)

    from services.rebuild_coordinator import get_coordinator

    coord = get_coordinator()
    publish_counts = coord._metrics.get("last_publish_counts") or {}

    def _format(last: dict) -> dict:
        if not last:
            return {
                "last_full_sync_at": None,
                "last_full_sync_duration_ms": None,
                "last_full_sync_props_written": None,
                "last_trigger": None,
            }
        collections = last.get("collections") or {}
        try:
            props_written = int(sum(int(v or 0) for v in collections.values()))
        except (TypeError, ValueError):
            props_written = None
        try:
            duration_ms = int(round(float(last.get("duration_s", 0.0)) * 1000))
        except (TypeError, ValueError):
            duration_ms = None
        return {
            "last_full_sync_at": last.get("timestamp"),
            "last_full_sync_duration_ms": duration_ms,
            "last_full_sync_props_written": props_written,
            "last_trigger": {
                "event_type": last.get("trigger"),
                "source": last.get("source"),
                "success": last.get("success"),
                "run_id": last.get("run_id"),
                "collections": collections,
            },
        }

    if sport is None:
        return {
            "nba": _format(publish_counts.get("nba") or {}),
            "mlb": _format(publish_counts.get("mlb") or {}),
        }

    s = sport.strip().lower()
    if s not in ("nba", "mlb"):
        raise HTTPException(status_code=400, detail="sport must be 'nba' or 'mlb'")
    return _format(publish_counts.get(s) or {})


@router.get("/collection-migration-status")
async def collection_migration_status(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Read-only report of the Phase-A canonical-naming migration.

    For each (sport, concept) pair managed by `config.collections`,
    reports the CURRENT storage collection name, the CANONICAL target
    name, and whether the pair has already migrated. Drives migration
    dashboards and regression audits — zero DB I/O, no side-effects.

    Auth: identical to /injury-rescore-stats and /full-sync-stats.
    """
    _require_admin_debug_token(x_admin_token)

    from config.collections import migration_status, SUPPORTED_SPORTS

    full = migration_status()
    summary = {"sports": SUPPORTED_SPORTS, "total_pairs": 0, "migrated": 0, "pending": 0}
    for _, by_concept in full.items():
        for _, info in by_concept.items():
            summary["total_pairs"] += 1
            if info["migrated"]:
                summary["migrated"] += 1
            else:
                summary["pending"] += 1
    return {"summary": summary, "by_sport": full}


@router.get("/board-engine-stats")
async def board_engine_stats(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Read-only snapshot of the universal real-time ingest engine
    (`services/board/engine.py`). Zero DB I/O, zero mutation.

    Per-sport counters:
      - events_received / events_processed / events_skipped
      - props_upserted (cumulative)
      - last_event_at / last_source / last_keys_count / last_written /
        last_skipped / last_duration_ms / last_error

    Auth: identical to /injury-rescore-stats.
    """
    _require_admin_debug_token(x_admin_token)
    from services.board.engine import stats_snapshot
    return {"by_sport": stats_snapshot()}


@router.get("/board-drift-audit")
async def board_drift_audit(
    sport: str | None = None,
    limit: int | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Live A/B drift report for the 48h Step 6 observation window.

    Returns TWO sections per sport:

      `in_memory`: current-process ring buffer snapshot. Rebuilds on
        restart. Classifies each of the last ≤500 real-time upserts
        against the CURRENT score doc.
      `persisted`: rolling-window (1h / 6h / 24h / 48h) classification
        sourced from the MongoDB `board_drift_ledger` collection
        (72h TTL, restart-safe). Surfaces a historical convergence
        record for Step 6 gating.

    Query params:
      - ?sport=nba         → audit NBA only
      - ?sport=mlb         → audit MLB only
      - (no sport)         → audit every registered sport
      - ?limit=N           → in-memory only: audit only the N most
                             recent ring-buffer entries

    Auth: identical to /injury-rescore-stats.
    """
    _require_admin_debug_token(x_admin_token)

    # Local imports so the module can be reloaded without restarting
    # the whole server.
    from services.board.drift_audit import (
        audit, audit_persisted, snapshot,
    )
    from services.board.adapters import registered_sports

    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    async def _per_sport(s: str) -> dict:
        return {
            "in_memory": {
                "ledger": snapshot(s),
                "audit": await audit(_db, s, limit=limit),
            },
            "persisted": await audit_persisted(_db, s),
        }

    if sport:
        return {"sport": sport, **await _per_sport(sport)}
    return {"by_sport": {s: await _per_sport(s) for s in registered_sports()}}


# ---------------------------------------------------------------------------
# Universal Gate Stats — data-driven threshold tuning
# ---------------------------------------------------------------------------
# Pure aggregation over `{sport}_prop_scores @ final-{sport}-rt`. No recompute,
# no scoring calls, no writes. Reads `gate_eval` (emitted by the Universal
# Gate Engine) and returns per-gate failure rates, multi-fail combos,
# near-miss deltas, and breakdowns by stat_family / tier so operators can
# tune thresholds using data instead of guesswork.
# ---------------------------------------------------------------------------
from collections import Counter, defaultdict
from statistics import median as _median

# Gate-type → canonical reason-code name ("tp_gate" → "gate_tp_fail").
# Kept in sync with ReasonCode._PER_GATE_FAIL but imported lazily so this
# module has no hard dependency on services.scoring.gates at import time.
_GATE_REASON_KEY = {
    "coverage_gate": "gate_coverage_fail",
    "hit_rate_gate": "gate_hit_rate_fail",
    "tp_gate":       "gate_tp_fail",
    "cv_gate":       "gate_cv_fail",
    "edge_gate":     "gate_edge_fail",
    "ceiling_gate":  "gate_ceiling_fail",
    "context_gate":  "gate_context_fail",
}

# Near-miss thresholds (in metric-native units). hit_rate/tp/edge/ceiling
# are in percentage points; cv is a raw ratio.
_NEAR_MISS_BANDS = {
    "tp_gate":       (2.0, 5.0),
    "hit_rate_gate": (2.0, 5.0),
    "edge_gate":     (2.0, 5.0),
    "ceiling_gate":  (2.0, 5.0),
    "cv_gate":       (0.02, 0.05),
    "coverage_gate": (1.0, 2.0),
}


def _threshold_scalar(thr):
    """Extract a numeric threshold from a gate_details.threshold value.
    hit_rate_gate stores a dict ({"min": X, "window": ...}); all other
    numeric gates store a scalar. Returns None for non-numeric (context)."""
    if isinstance(thr, (int, float)):
        return float(thr)
    if isinstance(thr, dict):
        for k in ("min", "min_cv_floor", "max", "min_books"):
            if k in thr and isinstance(thr[k], (int, float)):
                return float(thr[k])
    return None


def _signed_margin(actual, threshold_val, comparator):
    """Margin against the pass-condition in metric-native units.
    Positive ⇒ passed by this much; negative ⇒ failed by |this| much.
    Returns None if inputs are not numeric."""
    if actual is None or threshold_val is None:
        return None
    if not isinstance(actual, (int, float)):
        return None
    if comparator == ">=":
        return float(actual) - float(threshold_val)
    if comparator == "<=":
        return float(threshold_val) - float(actual)
    return None


@router.get("/v3/admin/identity-status")
async def identity_status_report(
    sport: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Global Identity Rule (2026-04-23) observability panel.

    Surfaces `identity_status` / `hit_rate_status` / `cv_status`
    breakdowns for every sport in the live scoring table
    (`final-{sport}-rt`). Paired with live-props ingest counts so
    operators can catch drift between ingest-side stamping and
    scoring-side usage without tailing logs.

    Query params:
      - sport (optional): 'nba' | 'mlb'. Omit to report on all sports
        registered in `scheduled_sports.SCHEDULED_SPORTS`.

    Auth: `X-Admin-Token` header must match env `ADMIN_DEBUG_TOKEN`.
    """
    _require_admin_debug_token(x_admin_token)

    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    # Which sports to report on
    if sport:
        sports = [sport.strip().lower()]
    else:
        try:
            from services.scheduled_sports import SCHEDULED_SPORTS
            sports = list(SCHEDULED_SPORTS.keys())
        except Exception:
            sports = ["nba", "mlb"]

    report: Dict[str, Any] = {}
    for s in sports:
        scores_coll = _db[f"{s}_prop_scores"]
        live_coll = _db[f"{s}_live_props"]
        version_tag = f"final-{s}-rt"

        # Live-props ingest coverage
        live_total = await live_coll.count_documents({})
        live_resolved = await live_coll.count_documents(
            {"bdl_player_id": {"$ne": None}}
        )
        live_missing = await live_coll.count_documents(
            {"identity_status": "missing_bdl_id"}
        )

        # Scored-doc identity + metric status
        scored_total = await scores_coll.count_documents(
            {"version_tag": version_tag}
        )
        id_resolved = await scores_coll.count_documents(
            {"version_tag": version_tag, "identity_status": "resolved"}
        )
        id_missing = await scores_coll.count_documents(
            {"version_tag": version_tag, "identity_status": "missing_bdl_id"}
        )

        # HR / CV status breakdown on scored docs
        hr_counts: Dict[str, int] = {}
        cv_counts: Dict[str, int] = {}
        for status in ("computed", "missing_source_distribution",
                       "unavailable_stat_family", "missing_bdl_id"):
            hr_counts[status] = await scores_coll.count_documents(
                {"version_tag": version_tag, "hit_rate_status": status}
            )
            cv_counts[status] = await scores_coll.count_documents(
                {"version_tag": version_tag, "cv_status": status}
            )

        # Top unresolved player names for quick triage. Aggregated on
        # live_props since scored docs exclude the name field in some
        # output projections.
        unresolved_pipeline = [
            {"$match": {"identity_status": "missing_bdl_id"}},
            {"$group": {"_id": "$player_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20},
        ]
        unresolved_samples = []
        async for d in live_coll.aggregate(unresolved_pipeline):
            unresolved_samples.append(
                {"player_name": d["_id"], "prop_count": d["count"]}
            )

        report[s] = {
            "live_props": {
                "total": live_total,
                "resolved": live_resolved,
                "missing_bdl_id": live_missing,
                "resolution_pct": (
                    round(live_resolved / live_total * 100.0, 2)
                    if live_total else None
                ),
            },
            "scored_props": {
                "version_tag": version_tag,
                "total": scored_total,
                "identity": {
                    "resolved": id_resolved,
                    "missing_bdl_id": id_missing,
                },
                "hit_rate_status": hr_counts,
                "cv_status": cv_counts,
            },
            "top_unresolved_players": unresolved_samples,
        }

    return {"sports": report}



@router.get("/v3/admin/gate-stats")
async def gate_stats(
    sport: str,
    tier: str | None = None,
    stat_family: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Aggregate gate performance over `{sport}_prop_scores @ final-{sport}-rt`.

    Query params:
      - sport (required): 'nba' | 'mlb' | ...
      - tier (optional): 'safe_haven' | 'front_lines' | 'war_zone'
      - stat_family (optional): e.g. 'pts', 'hits', 'total_bases'

    Filters apply against the Universal Gate Engine's own target tier /
    stat_family (fields on `gate_eval.tier` / `gate_eval.stat_family`) so
    the report always reflects what the engine was trying to evaluate —
    regardless of whether the prop ultimately qualified.

    Returns: total_props, total_passed, total_failed, per-gate failure
    breakdown, multi-fail combos, near-miss deltas, by_stat_family, and
    by_tier (only when the `tier` query param is not set).

    Auth: `X-Admin-Token` header must match env `ADMIN_DEBUG_TOKEN`.
    """
    _require_admin_debug_token(x_admin_token)

    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    s = (sport or "").strip().lower()
    if not s:
        raise HTTPException(status_code=400, detail="sport query param is required")

    version_tag = f"final-{s}-rt"
    q: dict = {"version_tag": version_tag, "gate_eval": {"$exists": True}}
    if tier:
        q["gate_eval.tier"] = tier
    if stat_family:
        q["gate_eval.stat_family"] = stat_family

    coll = _db[f"{s}_prop_scores"]
    projection = {"_id": 0, "gate_eval": 1}

    # Accumulators
    total_props = 0
    total_passed = 0
    total_failed = 0

    per_gate_pass: Counter = Counter()
    per_gate_fail: Counter = Counter()
    per_gate_eval: Counter = Counter()   # total times each gate actually ran

    combo_counter: Counter = Counter()

    # Per-gate delta lists (all evaluated props) + failed-only miss magnitudes
    per_gate_deltas: dict = defaultdict(list)
    per_gate_fail_miss: dict = defaultdict(list)
    per_gate_near_miss_small: Counter = Counter()  # within tight band
    per_gate_near_miss_wide: Counter = Counter()   # within wider band

    # Breakdowns
    by_stat_family_counts: dict = defaultdict(lambda: {
        "total_props": 0, "passed": 0, "failed": 0,
        "fail_by_gate": Counter(),
    })
    by_tier_counts: dict = defaultdict(lambda: {
        "total_props": 0, "passed": 0, "failed": 0,
        "fail_by_gate": Counter(),
    })

    cursor = coll.find(q, projection)
    async for doc in cursor:
        gate_eval = doc.get("gate_eval") or {}
        if not gate_eval:
            continue

        total_props += 1
        overall_passed = bool(gate_eval.get("passed"))
        if overall_passed:
            total_passed += 1
        else:
            total_failed += 1

        passed_gates = list(gate_eval.get("passed_gates") or [])
        failed_gates = list(gate_eval.get("failed_gates") or [])

        # Per-gate pass/fail
        for g in passed_gates:
            per_gate_pass[g] += 1
            per_gate_eval[g] += 1
        for g in failed_gates:
            per_gate_fail[g] += 1
            per_gate_eval[g] += 1

        # Multi-fail combos (only when something failed)
        if failed_gates:
            key = tuple(sorted(failed_gates))
            combo_counter[key] += 1

        # Near-miss / delta analysis per-gate
        gate_details = gate_eval.get("gate_details") or {}
        for g_type, detail in gate_details.items():
            if not isinstance(detail, dict):
                continue
            thr_scalar = _threshold_scalar(detail.get("threshold"))
            actual = detail.get("actual")
            comparator = detail.get("comparator")
            margin = _signed_margin(actual, thr_scalar, comparator)
            if margin is None:
                continue
            per_gate_deltas[g_type].append(margin)
            if not detail.get("passed", True):
                miss_mag = -margin  # failed ⇒ margin is negative
                per_gate_fail_miss[g_type].append(miss_mag)
                tight_band, wide_band = _NEAR_MISS_BANDS.get(
                    g_type, (2.0, 5.0)
                )
                if miss_mag <= tight_band:
                    per_gate_near_miss_small[g_type] += 1
                if miss_mag <= wide_band:
                    per_gate_near_miss_wide[g_type] += 1

        # Per-stat-family breakdown
        fam = gate_eval.get("stat_family") or "_unknown"
        rec = by_stat_family_counts[fam]
        rec["total_props"] += 1
        if overall_passed:
            rec["passed"] += 1
        else:
            rec["failed"] += 1
        for g in failed_gates:
            rec["fail_by_gate"][g] += 1

        # Per-tier breakdown (only computed/returned if no tier filter)
        if not tier:
            tkey = gate_eval.get("tier") or "_unknown"
            trec = by_tier_counts[tkey]
            trec["total_props"] += 1
            if overall_passed:
                trec["passed"] += 1
            else:
                trec["failed"] += 1
            for g in failed_gates:
                trec["fail_by_gate"][g] += 1

    # ---- Build response ----
    def _pct(num, den):
        return round((num / den * 100.0), 2) if den else 0.0

    gate_failures = {}
    for g_type, fail_ct in per_gate_fail.items():
        eval_ct = per_gate_eval.get(g_type, 0)
        reason_key = _GATE_REASON_KEY.get(g_type, f"gate_{g_type}_fail")
        gate_failures[reason_key] = {
            "gate_type": g_type,
            "fail_count": int(fail_ct),
            "pass_count": int(per_gate_pass.get(g_type, 0)),
            "evaluated": int(eval_ct),
            "fail_rate": _pct(fail_ct, total_props),
            "fail_rate_when_evaluated": _pct(fail_ct, eval_ct),
        }
    # Include pass-only gates (never failed) so the map is complete.
    for g_type, pass_ct in per_gate_pass.items():
        reason_key = _GATE_REASON_KEY.get(g_type, f"gate_{g_type}_fail")
        if reason_key not in gate_failures:
            gate_failures[reason_key] = {
                "gate_type": g_type,
                "fail_count": 0,
                "pass_count": int(pass_ct),
                "evaluated": int(per_gate_eval.get(g_type, 0)),
                "fail_rate": 0.0,
                "fail_rate_when_evaluated": 0.0,
            }

    multi_fail_combos = [
        {"failed_gates": list(combo), "count": int(ct)}
        for combo, ct in combo_counter.most_common(15)
    ]

    near_miss: dict = {}
    for g_type, deltas in per_gate_deltas.items():
        if not deltas:
            continue
        miss_list = per_gate_fail_miss.get(g_type) or []
        near_miss[g_type] = {
            "avg_delta": round(sum(deltas) / len(deltas), 4),
            "median_delta": round(_median(deltas), 4),
            "sample_size": len(deltas),
            "avg_fail_miss": (
                round(sum(miss_list) / len(miss_list), 4) if miss_list else None
            ),
            "fail_sample_size": len(miss_list),
            "near_miss_tight": int(per_gate_near_miss_small.get(g_type, 0)),
            "near_miss_wide": int(per_gate_near_miss_wide.get(g_type, 0)),
            "near_miss_bands": {
                "tight": _NEAR_MISS_BANDS.get(g_type, (2.0, 5.0))[0],
                "wide":  _NEAR_MISS_BANDS.get(g_type, (2.0, 5.0))[1],
            },
        }

    def _finalize_breakdown(rec: dict) -> dict:
        total = rec["total_props"]
        top_failing_gate = None
        if rec["fail_by_gate"]:
            top_g, _ct = rec["fail_by_gate"].most_common(1)[0]
            top_failing_gate = _GATE_REASON_KEY.get(top_g, f"gate_{top_g}_fail")
        return {
            "total_props": total,
            "passed": rec["passed"],
            "failed": rec["failed"],
            "pass_rate": _pct(rec["passed"], total),
            "top_failing_gate": top_failing_gate,
            "fail_by_gate": {
                _GATE_REASON_KEY.get(g, f"gate_{g}_fail"): int(c)
                for g, c in rec["fail_by_gate"].items()
            },
        }

    by_stat_family = {
        fam: _finalize_breakdown(rec)
        for fam, rec in by_stat_family_counts.items()
    }

    response: dict = {
        "sport": s,
        "tier": tier,
        "stat_family": stat_family,
        "version_tag": version_tag,
        "total_props": total_props,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "overall_pass_rate": _pct(total_passed, total_props),
        "gate_failures": gate_failures,
        "multi_fail_combos": multi_fail_combos,
        "near_miss": near_miss,
        "by_stat_family": by_stat_family,
    }

    if not tier:
        response["by_tier"] = {
            t: _finalize_breakdown(rec)
            for t, rec in by_tier_counts.items()
        }

    return response


# ---------------------------------------------------------------------------
# Threshold Simulator — preview how changing one gate threshold would
# affect prop qualification. Pure aggregation over `gate_eval`: no
# recompute, no scoring calls, no writes.
# ---------------------------------------------------------------------------

# Short gate name → canonical gate_type key used in `gate_eval.gate_details`.
_GATE_SHORT_TO_TYPE: Dict[str, str] = {
    "tp": "tp_gate",
    "cv": "cv_gate",
    "hit_rate": "hit_rate_gate",
    "edge": "edge_gate",
    "ceiling": "ceiling_gate",
    "coverage": "coverage_gate",
}


class ThresholdSimulateRequest(BaseModel):
    sport: str
    tier: str
    stat_family: Optional[str] = None
    gate: str = Field(..., description="Short gate name: tp|cv|hit_rate|edge|ceiling|coverage")
    current_threshold: float
    proposed_threshold: float
    mode: Optional[str] = Field(
        default=None,
        description="'min' (actual>=threshold passes) or 'max' (actual<=threshold passes). "
                    "Inferred from the stored gate comparator if omitted.",
    )
    sample_limit: int = Field(default=25, ge=1, le=500)


def _proposed_gate_pass(actual: Any, threshold: float, mode: str) -> bool:
    """Re-evaluate ONE gate under a proposed threshold. Returns False if
    actual is missing/non-numeric so missing-signal props don't get
    quietly unlocked."""
    if actual is None or not isinstance(actual, (int, float)):
        return False
    if mode == "max":
        return float(actual) <= float(threshold)
    return float(actual) >= float(threshold)  # default: min


@router.post("/v3/admin/threshold-simulate")
async def threshold_simulate(
    payload: ThresholdSimulateRequest = Body(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Simulate a single-gate threshold change over live
    `{sport}_prop_scores @ final-{sport}-rt.gate_eval` — no recompute,
    no writes.

    Reads the persisted `gate_details[<gate>].actual` for each prop and
    re-evaluates ONLY that one gate under `proposed_threshold`, keeping
    every other gate's outcome fixed as originally stored. This answers
    "how many picks would unlock if we move this knob?" without touching
    scoring logic.

    Classification per prop:
      * newly_qualified  : currently failing overall → would pass overall
                           under the proposed threshold
      * newly_rejected   : currently passing overall → would fail overall
                           under the proposed threshold (only relevant
                           when tightening)
      * unchanged_pass   : passes overall before and after
      * unchanged_fail   : still blocked overall (by this gate or others)

    Response also flags `blocked_by_other_gates`: props whose simulated
    gate *would* pass but who still fail overall because other gates
    remain failed. Those are NOT "real unlocks".

    Auth: `X-Admin-Token` matching `ADMIN_DEBUG_TOKEN`.
    """
    _require_admin_debug_token(x_admin_token)

    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    sport = (payload.sport or "").strip().lower()
    tier = (payload.tier or "").strip()
    if not sport or not tier:
        raise HTTPException(status_code=400, detail="sport and tier are required")

    gate_key = (payload.gate or "").strip().lower()
    gate_type = _GATE_SHORT_TO_TYPE.get(gate_key)
    if gate_type is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown gate '{payload.gate}'; allowed: "
                   f"{sorted(_GATE_SHORT_TO_TYPE.keys())}",
        )

    mode = (payload.mode or "").strip().lower() or None
    if mode is not None and mode not in ("min", "max"):
        raise HTTPException(status_code=400, detail="mode must be 'min' or 'max'")

    version_tag = f"final-{sport}-rt"
    q: Dict[str, Any] = {
        "version_tag": version_tag,
        "gate_eval": {"$exists": True},
        "gate_eval.tier": tier,
        f"gate_eval.gate_details.{gate_type}": {"$exists": True},
    }
    if payload.stat_family:
        q["gate_eval.stat_family"] = payload.stat_family

    projection = {
        "_id": 0,
        "gate_eval": 1,
        "player_name": 1,
        "stat_type": 1,
        "line": 1,
        "recommendation": 1,
        "tier_reference_book": 1,
        "tier_reference_odds": 1,
    }

    coll = _db[f"{sport}_prop_scores"]

    # Accumulators
    total_props = 0
    currently_passing = 0
    newly_qualified: List[Dict[str, Any]] = []
    newly_rejected: List[Dict[str, Any]] = []
    blocked_by_other_gates: List[Dict[str, Any]] = []

    unchanged_pass = 0
    unchanged_fail = 0

    near_miss_1 = 0
    near_miss_2 = 0
    near_miss_5 = 0

    inferred_mode: Optional[str] = None

    cursor = coll.find(q, projection)
    async for doc in cursor:
        gate_eval = doc.get("gate_eval") or {}
        gate_details = gate_eval.get("gate_details") or {}
        detail = gate_details.get(gate_type)
        if not isinstance(detail, dict):
            continue

        total_props += 1

        comparator = detail.get("comparator")
        # Infer mode from stored comparator if caller didn't set one.
        if mode is None:
            if comparator == "<=":
                inferred_mode = "max"
            else:
                inferred_mode = "min"
            effective_mode = inferred_mode
        else:
            effective_mode = mode

        actual = detail.get("actual")
        originally_passed_this_gate = bool(detail.get("passed"))
        overall_passed_original = bool(gate_eval.get("passed"))

        if overall_passed_original:
            currently_passing += 1

        # Re-evaluate ONLY this gate under proposed threshold.
        proposed_pass_this_gate = _proposed_gate_pass(
            actual, payload.proposed_threshold, effective_mode
        )

        # Other gates' outcomes are frozen. Overall pass under the
        # proposed change = (every other gate still passes) AND
        # (this gate now passes).
        failed_gates_orig = list(gate_eval.get("failed_gates") or [])
        other_failed = [g for g in failed_gates_orig if g != gate_type]
        overall_passed_proposed = (len(other_failed) == 0) and proposed_pass_this_gate

        # Near-miss distribution — distance from CURRENT threshold in
        # metric-native units, for props where THIS gate failed.
        if (not originally_passed_this_gate) and isinstance(actual, (int, float)):
            try:
                dist = abs(float(actual) - float(payload.current_threshold))
                if dist <= 1.0:
                    near_miss_1 += 1
                if dist <= 2.0:
                    near_miss_2 += 1
                if dist <= 5.0:
                    near_miss_5 += 1
            except (TypeError, ValueError):
                pass

        # Classify
        def _sample_shape() -> Dict[str, Any]:
            return {
                "player": doc.get("player_name"),
                "stat": doc.get("stat_type"),
                "line": doc.get("line"),
                "recommendation": doc.get("recommendation"),
                "reference_book": doc.get("tier_reference_book"),
                "reference_odds": doc.get("tier_reference_odds"),
                "actual": actual,
                "old_threshold": payload.current_threshold,
                "new_threshold": payload.proposed_threshold,
                "delta": (
                    round(float(actual) - float(payload.current_threshold), 4)
                    if isinstance(actual, (int, float)) else None
                ),
                "other_failed_gates": other_failed,
            }

        if (not overall_passed_original) and overall_passed_proposed:
            if len(newly_qualified) < payload.sample_limit:
                newly_qualified.append(_sample_shape())
            else:
                # Still count; just don't store the full sample.
                newly_qualified.append(None)  # sentinel for counting below
        elif overall_passed_original and (not overall_passed_proposed):
            if len(newly_rejected) < payload.sample_limit:
                newly_rejected.append(_sample_shape())
            else:
                newly_rejected.append(None)
        elif overall_passed_original and overall_passed_proposed:
            unchanged_pass += 1
        else:
            unchanged_fail += 1
            # If this gate would now pass but prop still blocked → fake unlock.
            if proposed_pass_this_gate and (not originally_passed_this_gate):
                if len(blocked_by_other_gates) < payload.sample_limit:
                    blocked_by_other_gates.append({
                        "player": doc.get("player_name"),
                        "stat": doc.get("stat_type"),
                        "line": doc.get("line"),
                        "actual": actual,
                        "reason": [
                            _GATE_REASON_KEY.get(g, f"gate_{g}_fail")
                            for g in other_failed
                        ],
                    })

    # Separate real counts from the truncated sample lists.
    newly_qualified_total = len(newly_qualified)
    newly_rejected_total = len(newly_rejected)
    newly_qualified_samples = [s for s in newly_qualified if s is not None]
    newly_rejected_samples = [s for s in newly_rejected if s is not None]

    summary = {
        "total_props": total_props,
        "currently_passing": currently_passing,
        "newly_qualified": newly_qualified_total,
        "newly_rejected": newly_rejected_total,
        "net_change": newly_qualified_total - newly_rejected_total,
        "projected_passing": currently_passing + newly_qualified_total - newly_rejected_total,
        "unchanged_pass": unchanged_pass,
        "unchanged_fail": unchanged_fail,
    }

    return {
        "sport": sport,
        "tier": tier,
        "stat_family": payload.stat_family,
        "gate": gate_key,
        "gate_type": gate_type,
        "mode": mode or inferred_mode,
        "mode_source": "explicit" if mode is not None else "inferred_from_comparator",
        "current_threshold": payload.current_threshold,
        "proposed_threshold": payload.proposed_threshold,
        "version_tag": version_tag,
        "summary": summary,
        "newly_qualified_samples": newly_qualified_samples,
        "newly_rejected_samples": newly_rejected_samples,
        "blocked_by_other_gates": blocked_by_other_gates,
        "near_miss_distribution": {
            "within_1pct": near_miss_1,
            "within_2pct": near_miss_2,
            "within_5pct": near_miss_5,
            "note": "units are metric-native (percentage points for tp/hit_rate/edge/ceiling; raw ratio for cv)",
        },
    }



# ---------------------------------------------------------------------------
# PRA Dual-Projection Audit (2026-04-23)
# ---------------------------------------------------------------------------
from datetime import datetime, timezone


def _player_archetype_from_position(pos: Optional[str], name: Optional[str]) -> str:
    if not pos:
        return "unknown"
    p = str(pos).upper()
    if "G" in p and "F" not in p:
        return "guard"
    if "F" in p and "C" not in p:
        return "wing"
    if "C" in p:
        return "big"
    return "unknown"


def _line_bucket(line: float) -> str:
    if line < 20: return "<20"
    if line < 30: return "20-30"
    if line < 40: return "30-40"
    if line < 50: return "40-50"
    return "50+"


@router.post("/v3/admin/pra-audit/settle")
async def pra_audit_settle(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Walk unsettled `nba_pra_projection_audit` rows and resolve
    each one's actual pts / reb / ast from
    `nba_master_hub_2026.bdl_game_logs`. Idempotent."""
    _require_admin_debug_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    audit = _db["nba_pra_projection_audit"]
    hub = _db["nba_master_hub_2026"]

    logs_by_name: Dict[str, list] = {}
    async for h in hub.find(
        {"bdl_game_logs_count": {"$gt": 0}},
        {"_id": 0, "display_name": 1, "bdl_game_logs": 1},
    ):
        nm = (h.get("display_name") or "").lower()
        logs_by_name[nm] = h.get("bdl_game_logs") or []

    settled = 0
    still_pending = 0
    from datetime import timedelta as _td
    async for row in audit.find({"settled": {"$ne": True}}, {"_id": 0}):
        game_start = row.get("game_start_utc")
        if not game_start:
            still_pending += 1
            continue
        try:
            dt = game_start
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            target_dates = {dt.date().isoformat(), (dt.date() - _td(days=1)).isoformat()}
        except Exception:
            still_pending += 1
            continue
        player_logs = logs_by_name.get((row.get("player_name") or "").lower()) or []
        hit = None
        for g in player_logs:
            if str(g.get("date") or "") in target_dates:
                hit = g
                break
        if hit is None:
            still_pending += 1
            continue
        pts = hit.get("pts"); reb = hit.get("reb"); ast = hit.get("ast")
        if pts is None or reb is None or ast is None:
            still_pending += 1
            continue
        pra_actual = float(pts) + float(reb) + float(ast)
        await audit.update_one(
            {
                "event_id": row["event_id"],
                "player_name": row["player_name"],
                "line": row["line"],
                "recommendation": row["recommendation"],
            },
            {"$set": {
                "actual_pts": int(pts),
                "actual_reb": int(reb),
                "actual_ast": int(ast),
                "actual_pra": round(pra_actual, 2),
                "settled": True,
                "settled_at": datetime.now(timezone.utc),
            }},
        )
        settled += 1

    total = await audit.count_documents({})
    settled_total = await audit.count_documents({"settled": True})
    return {
        "status": "ok",
        "settled_this_run": settled,
        "still_pending_this_run": still_pending,
        "total_audit_rows": total,
        "total_settled": settled_total,
        "total_pending": total - settled_total,
    }


@router.get("/v3/admin/pra-audit/report")
async def pra_audit_report(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Aggregate the PRA dual-projection audit: counts, MAE (direct
    vs actual, synth vs actual), hit-rate calibration, per-archetype
    and per-line-bucket breakdowns, and top outperformance samples."""
    _require_admin_debug_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialised")

    audit = _db["nba_pra_projection_audit"]
    hub = _db["nba_master_hub_2026"]

    pos_by_name: Dict[str, str] = {}
    async for h in hub.find(
        {}, {"_id": 0, "display_name": 1, "position": 1, "bdl_position": 1},
    ):
        nm = (h.get("display_name") or "").lower()
        pos_by_name[nm] = h.get("position") or h.get("bdl_position") or ""

    total_rows = await audit.count_documents({})
    both_available = await audit.count_documents({"projection_compare_status": "both_available"})
    direct_only = await audit.count_documents({"projection_compare_status": "direct_only"})
    synth_only = await audit.count_documents({"projection_compare_status": "synth_only"})
    settled = await audit.count_documents({"settled": True})
    settled_both = await audit.count_documents(
        {"settled": True, "projection_compare_status": "both_available"}
    )

    from collections import defaultdict
    deltas: List[float] = []
    delta_by_arch: Dict[str, List[float]] = defaultdict(list)
    delta_by_bucket: Dict[str, List[float]] = defaultdict(list)
    async for row in audit.find(
        {"projection_compare_status": "both_available"},
        {"_id": 0, "projection_delta_pct": 1, "line": 1, "player_name": 1},
    ):
        dp = row.get("projection_delta_pct")
        if dp is None:
            continue
        deltas.append(float(dp))
        arch = _player_archetype_from_position(
            pos_by_name.get((row.get("player_name") or "").lower()), row.get("player_name"),
        )
        delta_by_arch[arch].append(float(dp))
        delta_by_bucket[_line_bucket(float(row.get("line") or 0))].append(float(dp))

    def _stats(xs: List[float]) -> Dict[str, Any]:
        if not xs:
            return {"n": 0}
        from statistics import median as _median
        return {
            "n": len(xs),
            "avg_abs_delta_pct": round(sum(xs) / len(xs), 3),
            "median_abs_delta_pct": round(_median(xs), 3),
            "max_abs_delta_pct": round(max(xs), 3),
        }

    divergence = {
        "all": _stats(deltas),
        "by_archetype": {k: _stats(v) for k, v in delta_by_arch.items()},
        "by_line_bucket": {k: _stats(v) for k, v in delta_by_bucket.items()},
    }

    direct_errs: List[float] = []; synth_errs: List[float] = []
    direct_signed: List[float] = []; synth_signed: List[float] = []
    direct_hit_match = 0; synth_hit_match = 0; counted_hits = 0
    synth_wins: List[Dict[str, Any]] = []
    direct_wins: List[Dict[str, Any]] = []
    err_by_arch: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: {"direct": [], "synth": []}
    )
    err_by_bucket: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: {"direct": [], "synth": []}
    )

    async for row in audit.find(
        {"settled": True, "projection_compare_status": "both_available"},
        {"_id": 0},
    ):
        actual = row.get("actual_pra")
        pd = row.get("model_projection_direct")
        ps = row.get("model_projection_synth")
        line = row.get("line")
        rec = row.get("recommendation")
        if actual is None or pd is None or ps is None or line is None:
            continue
        arch = _player_archetype_from_position(
            pos_by_name.get((row.get("player_name") or "").lower()), row.get("player_name"),
        )
        bucket = _line_bucket(float(line))

        d_err_abs = abs(float(pd) - float(actual))
        s_err_abs = abs(float(ps) - float(actual))
        direct_errs.append(d_err_abs); synth_errs.append(s_err_abs)
        direct_signed.append(float(pd) - float(actual))
        synth_signed.append(float(ps) - float(actual))
        err_by_arch[arch]["direct"].append(d_err_abs)
        err_by_arch[arch]["synth"].append(s_err_abs)
        err_by_bucket[bucket]["direct"].append(d_err_abs)
        err_by_bucket[bucket]["synth"].append(s_err_abs)

        actual_over = float(actual) > float(line)
        if (float(pd) > float(line)) == actual_over: direct_hit_match += 1
        if (float(ps) > float(line)) == actual_over: synth_hit_match += 1
        counted_hits += 1

        edge = d_err_abs - s_err_abs
        sample = {
            "player": row.get("player_name"), "line": line, "side": rec,
            "actual": actual, "direct": pd, "synth": ps,
            "direct_err": round(d_err_abs, 3), "synth_err": round(s_err_abs, 3),
            "edge": round(edge, 3),
        }
        if edge >= 2.0:
            synth_wins.append(sample)
        elif edge <= -2.0:
            direct_wins.append(sample)

    def _mae(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 3) if xs else None

    accuracy = {
        "settled_samples": len(direct_errs),
        "direct_mae": _mae(direct_errs),
        "synth_mae": _mae(synth_errs),
        "direct_bias": round(sum(direct_signed) / len(direct_signed), 3) if direct_signed else None,
        "synth_bias": round(sum(synth_signed) / len(synth_signed), 3) if synth_signed else None,
        "direct_side_accuracy_pct": (
            round(direct_hit_match / counted_hits * 100.0, 2) if counted_hits else None
        ),
        "synth_side_accuracy_pct": (
            round(synth_hit_match / counted_hits * 100.0, 2) if counted_hits else None
        ),
        "by_archetype": {
            k: {"n": len(v["direct"]), "direct_mae": _mae(v["direct"]), "synth_mae": _mae(v["synth"])}
            for k, v in err_by_arch.items()
        },
        "by_line_bucket": {
            k: {"n": len(v["direct"]), "direct_mae": _mae(v["direct"]), "synth_mae": _mae(v["synth"])}
            for k, v in err_by_bucket.items()
        },
        "synth_outperforms_direct_samples": sorted(synth_wins, key=lambda r: -r["edge"])[:10],
        "direct_outperforms_synth_samples": sorted(direct_wins, key=lambda r: r["edge"])[:10],
    }

    return {
        "collection": "nba_pra_projection_audit",
        "counts": {
            "total_audit_rows": total_rows,
            "both_available": both_available,
            "direct_only": direct_only,
            "synth_only": synth_only,
            "settled": settled,
            "settled_both_available": settled_both,
            "pending": total_rows - settled,
        },
        "divergence_audit": divergence,
        "accuracy_audit": accuracy,
        "notes": [
            "Run POST /api/v3/admin/pra-audit/settle first to backfill "
            "actuals from nba_master_hub_2026.bdl_game_logs.",
            "accuracy_audit is populated only when settled_samples > 0. "
            "Before the first game concludes, only divergence_audit is meaningful.",
        ],
    }
