"""
Team Master Hub admin endpoints (Phase 1.A.1).

Two endpoints — both token-gated, both preview-safe:

    POST /api/emergent-admin/team-master-hub/seed
        body: { "dry_run": bool = false }
        → runs (or simulates) the deterministic seeder against
          MongoDB and returns matched / modified / upserted counts
          plus the post-seed audit payload.

    GET  /api/emergent-admin/team-master-hub/audit
        → returns the read-only coverage report:
          total, by_sport, missing_sgo, duplicates, inactive,
          indexes_present.

Hard constraints (per Phase 1.A.1 brief):
    - PREVIEW POD ONLY. No SGO API calls, no production touch.
    - Uses the shared `services.team_master_hub` package as the
      single source of truth — the CLI script calls the same
      `seed_and_audit` / `audit_team_master_hub` functions.
    - Every action audit-logged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.team_master_hub import (
    audit_team_master_hub,
    seed_and_audit,
)
from services.team_master_hub.collections import (
    collections_status,
    ensure_team_collections,
)
from services.team_master_hub.ingest_policy import policy_summary
from services.team_master_hub.ingest_runs import list_ingest_runs
from workers.team import (
    SUPPORTED_SPORTS,
    TeamMatchupsIngestWorker,
    TeamOddsIngestWorker,
    TeamOutcomesGrader,
    dispatch_guard_ok,
)
from workers.team.team_events_sync import fetch_and_sync as _events_sync

from .auth import _get_db, audit_log, require_admin_token

logger = logging.getLogger("emergent_admin.team_master_hub")
router = APIRouter()


class SeedBody(BaseModel):
    """Seed-endpoint request body. `dry_run=True` skips index +
    bulk_write and returns the audit of the current collection
    state alongside a `seed_preview` block describing what a real
    run would do.
    """
    dry_run: bool = Field(default=False)


@router.post("/seed")
async def seed_endpoint(
    body: SeedBody,
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    db = _get_db()
    try:
        result = await seed_and_audit(db, dry_run=body.dry_run)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"seed file missing: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[team_master_hub.seed] failed")
        raise HTTPException(500, f"seed failed: {exc}") from exc

    # Audit summary — compact, never leaks the full team_id list
    seed_block = result.get("seed") or {}
    audit_block = result.get("audit") or {}
    await audit_log(
        request,
        action="team_master_hub_seed",
        params={"dry_run": body.dry_run},
        response_summary={
            "dry_run":     body.dry_run,
            "matched":     seed_block.get("matched"),
            "modified":    seed_block.get("modified"),
            "upserted":    seed_block.get("upserted"),
            "total_after": audit_block.get("total"),
        },
        **auth,
    )
    return result


@router.get("/audit")
async def audit_endpoint(
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    db = _get_db()
    try:
        report = await audit_team_master_hub(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[team_master_hub.audit] failed")
        raise HTTPException(500, f"audit failed: {exc}") from exc

    await audit_log(
        request,
        action="team_master_hub_audit",
        params={},
        response_summary={
            "total":             report["total"],
            "by_sport":          report["by_sport"],
            "missing_sgo_count": report["missing_sgo_count"],
            "duplicates_count":  report["duplicates_count"],
            "inactive_count":    report["inactive_count"],
            "indexes_present":   report["indexes_present"],
        },
        **auth,
    )
    return report


# ── Phase 1.A.2 — collections + worker probe endpoints ───────────────
@router.post("/ensure-collections")
async def ensure_collections_endpoint(
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Create the ten team-side collections + their §1.2 indexes.

    Idempotent. NO documents inserted. Preview-only.
    """
    db = _get_db()
    try:
        result = await ensure_team_collections(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[team_master_hub.ensure_collections] failed")
        raise HTTPException(500, f"ensure_collections failed: {exc}") from exc

    n_new = sum(1 for c in result["collections"] if c["is_new"])
    n_idx_created = sum(len(c["indexes_created"])
                            for c in result["collections"])
    await audit_log(
        request,
        action="team_ensure_collections",
        params={},
        response_summary={
            "n_collections":           result["n_collections"],
            "n_new_collections":       n_new,
            "n_indexes_created_total": n_idx_created,
        },
        **auth,
    )
    return result


@router.get("/collections-status")
async def collections_status_endpoint(
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    db = _get_db()
    report = await collections_status(db)
    await audit_log(
        request,
        action="team_collections_status",
        params={},
        response_summary={
            "n_collections": report["n_collections"],
            "n_present":     sum(1 for c in report["collections"]
                                     if c["present"]),
        },
        **auth,
    )
    return report


_WORKER_REGISTRY = {
    "odds":     TeamOddsIngestWorker,
    "outcomes": TeamOutcomesGrader,
    "matchups": TeamMatchupsIngestWorker,
}


@router.get("/workers/probe")
async def workers_probe_endpoint(
    request: Request,
    worker: str,
    sport:  str,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Probe a team worker for the given sport. ZERO network calls.

    Args:
      worker: one of `odds`, `outcomes`, `matchups`.
      sport:  one of `mlb`, `nba`, `nfl`.
    """
    if worker not in _WORKER_REGISTRY:
        raise HTTPException(
            400,
            f"unknown worker: {worker!r}. "
            f"Available: {sorted(_WORKER_REGISTRY.keys())}",
        )
    sport_l = (sport or "").lower()
    if sport_l not in SUPPORTED_SPORTS:
        raise HTTPException(
            400, f"unsupported sport: {sport!r}",
        )
    instance = _WORKER_REGISTRY[worker](sport_l)
    probe = instance.probe()
    ok, reasons = dispatch_guard_ok()
    await audit_log(
        request,
        action="team_worker_probe",
        params={"worker": worker, "sport": sport_l},
        response_summary={
            "dispatch_allowed": ok,
            "dispatch_reasons": reasons,
        },
        **auth,
    )
    return {"ok": True,
            "global_dispatch_allowed": ok,
            "global_dispatch_reasons": reasons,
            "probe": probe}


@router.get("/ingest-policy")
async def ingest_policy_endpoint(
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Read-only snapshot of the effective team-ingest policy
    (Phase 1.A.3.0). Never mutates anything; never calls SGO.
    """
    report = policy_summary()
    await audit_log(
        request,
        action="team_ingest_policy_view",
        params={},
        response_summary={
            "dispatch_allowed":   report["dispatch_guard"]["allowed"],
            "dry_run_default":    report["dry_run_default"],
            "retry_count":        report["retry"]["count"],
            "live_ttl_hours":     report["retention"]["live_ttl_hours"],
        },
        **auth,
    )
    return report


# ── Phase 1.A.3.1 — read-only audit query for ingest runs ────────────
_MAX_INGEST_RUNS_LIMIT = 100


@router.get("/ingest-runs")
async def ingest_runs_endpoint(
    request: Request,
    sport:   Optional[str] = None,
    status:  Optional[str] = None,
    limit:   int = 25,
    offset:  int = 0,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Read-only view over `team_odds_ingest_runs` (Phase 1.A.3.1).

    Latest rows first (sorted `started_at` desc). `_id` and
    `guard_reasons` are redacted; a `guard_blocked` boolean is
    surfaced in their place.
    """
    if sport is not None:
        sport_l = sport.lower()
        if sport_l not in SUPPORTED_SPORTS:
            raise HTTPException(
                400,
                f"unsupported sport: {sport!r}. "
                f"Supported: {sorted(SUPPORTED_SPORTS)}",
            )
        sport = sport_l
    if limit < 1 or limit > _MAX_INGEST_RUNS_LIMIT:
        raise HTTPException(
            400,
            f"limit must be in [1, {_MAX_INGEST_RUNS_LIMIT}] "
            f"(got {limit})",
        )
    if offset < 0:
        raise HTTPException(400, f"offset must be ≥ 0 (got {offset})")

    db = _get_db()
    report = await list_ingest_runs(
        db, sport=sport, status=status,
        limit=limit, offset=offset,
    )
    await audit_log(
        request,
        action="team_ingest_runs_query",
        params={"sport": sport, "status": status,
                  "limit": limit, "offset": offset},
        response_summary={
            "n_total":    report["n_total"],
            "n_returned": report["n_returned"],
        },
        **auth,
    )
    return report


# ── Phase 1.A.4a — team event schedule sync ──────────────────────────
import os as _os
import re as _re


class EventsSyncBody(BaseModel):
    """Body for POST /team-master-hub/events-sync.

    `dry_run=True` (default) → no writes. Pass `dry_run=False` to
    actually upsert into `team_matchups`. Dispatch guard still applies.
    """
    sport:    str  = Field(..., description="mlb | nba | nfl")
    date:     str  = Field(..., description="UTC date YYYY-MM-DD")
    dry_run:  bool = Field(default=True)


_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.post("/events-sync")
async def events_sync_endpoint(
    body: EventsSyncBody,
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Pull every SGO event for `(sport, date)` and upsert into
    `team_matchups`. Phase 1.A.4a: one date per call, MLB-only
    verified, no cron, no grading, no historical odds.
    """
    sport_l = (body.sport or "").lower()
    if sport_l not in SUPPORTED_SPORTS:
        raise HTTPException(
            400, f"unsupported sport: {body.sport!r}. "
                 f"Supported: {sorted(SUPPORTED_SPORTS)}")
    if not _DATE_RE.match(body.date or ""):
        raise HTTPException(
            400, f"date must be 'YYYY-MM-DD' (got {body.date!r})")

    db = _get_db()
    api_key = _os.environ.get("SGO_API_KEY", "")
    audit = await _events_sync(
        db, sport=sport_l, game_date=body.date,
        api_key=api_key, dry_run=body.dry_run,
    )

    await audit_log(
        request,
        action="team_events_sync",
        params={"sport": sport_l, "date": body.date,
                  "dry_run": body.dry_run},
        response_summary={
            "status":       audit["status"],
            "n_sgo_events": audit["n_sgo_events"],
            "n_normalized": audit["n_normalized"],
            "n_unresolved": audit["n_unresolved"],
            "n_writes":     audit["n_writes"],
            "n_upserted":   audit["n_upserted"],
            "n_modified":   audit["n_modified"],
        },
        **auth,
    )
    return audit


@router.get("/events-status")
async def events_status_endpoint(
    request: Request,
    sport: Optional[str] = None,
    game_date: Optional[str] = None,
    limit: int = 25,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Read-only summary of `team_matchups` content.

    Filters: `sport`, `game_date`. Counts plus the latest `limit`
    matchups (latest by `updated_at`).
    """
    if sport is not None:
        sport_l = sport.lower()
        if sport_l not in SUPPORTED_SPORTS:
            raise HTTPException(
                400, f"unsupported sport: {sport!r}")
        sport = sport_l
    if game_date is not None and not _DATE_RE.match(game_date):
        raise HTTPException(
            400, f"game_date must be 'YYYY-MM-DD' (got {game_date!r})")
    if limit < 1 or limit > 100:
        raise HTTPException(
            400, f"limit must be in [1, 100] (got {limit})")

    db = _get_db()
    flt: Dict[str, Any] = {}
    if sport:
        flt["sport"] = sport
    if game_date:
        flt["game_date"] = game_date

    n_total = await db["team_matchups"].count_documents(flt)
    rows: List[Dict[str, Any]] = []  # type: ignore[name-defined]
    cursor = db["team_matchups"].find(
        flt, projection={"_id": 0, "status_raw": 0}
    ).sort("updated_at", -1).limit(limit)
    async for d in cursor:
        rows.append(d)

    by_status: Dict[str, int] = {}  # type: ignore[name-defined]
    async for d in db["team_matchups"].aggregate([
        {"$match": flt},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        by_status[d["_id"] or "null"] = int(d["n"])

    await audit_log(
        request,
        action="team_events_status",
        params={"sport": sport, "game_date": game_date, "limit": limit},
        response_summary={"n_total": n_total, "by_status": by_status},
        **auth,
    )
    return {
        "ok":         True,
        "filter":     flt,
        "n_total":    int(n_total),
        "n_returned": len(rows),
        "by_status":  by_status,
        "rows":       rows,
    }


# ── Phase 1.A.4.acquire — historical pull endpoint ──────────────────
class HistoricalAcquireBody(BaseModel):
    """Body for POST /team-master-hub/historical-acquire."""
    sport:    str  = Field(..., description="mlb | nba | nfl")
    start:    str  = Field(..., description="UTC start date YYYY-MM-DD")
    end:      str  = Field(..., description="UTC end date YYYY-MM-DD")
    dry_run:  bool = Field(default=True)
    markets:  Optional[str] = Field(
        default=None,
        description="comma-separated market_key allow-list, or null/'all' "
                    "to acquire every market",
    )


@router.post("/historical-acquire")
async def historical_acquire_endpoint(
    body: HistoricalAcquireBody,
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Pull historical matchups + odds for `(sport, [start, end])`.
    DRY-RUN by default; pass `dry_run=False` to write.

    Writes to:
      - {team_matchups | nfl_matchups}
      - {team_historical_props | nfl_historical_props}
      - historical_acquire_runs (audit, always)
    """
    sport_l = (body.sport or "").lower()
    from workers.team.historical_ingest import (
        SPORT_COLLECTIONS as _SC,
        acquire_historical_window as _acquire,
    )
    if sport_l not in _SC:
        raise HTTPException(
            400, f"unsupported sport: {body.sport!r}. "
                 f"Supported: {sorted(_SC.keys())}")
    if not _DATE_RE.match(body.start or ""):
        raise HTTPException(
            400, f"start must be 'YYYY-MM-DD' (got {body.start!r})")
    if not _DATE_RE.match(body.end or ""):
        raise HTTPException(
            400, f"end must be 'YYYY-MM-DD' (got {body.end!r})")
    market_keys: Optional[tuple] = None
    if body.markets and body.markets.strip() and \
            body.markets.lower() != "all":
        market_keys = tuple(
            mk.strip() for mk in body.markets.split(",") if mk.strip())

    db = _get_db()
    api_key = _os.environ.get("SGO_API_KEY", "")
    audit = await _acquire(
        db, sport=sport_l,
        start_date=body.start, end_date=body.end,
        api_key=api_key, dry_run=body.dry_run,
        market_keys=market_keys,
    )

    await audit_log(
        request,
        action="team_historical_acquire",
        params={"sport": sport_l, "start": body.start, "end": body.end,
                  "dry_run": body.dry_run,
                  "markets": body.markets or "all"},
        response_summary={
            "status":          audit["status"],
            "n_sgo_events":    audit["n_sgo_events"],
            "n_matchups":      audit["n_matchups_written"],
            "n_props_written": audit["n_props_written"],
            "n_props_upserted": audit["n_props_upserted"],
            "n_unresolved":    audit["n_unresolved"],
        },
        **auth,
    )
    return audit


@router.get("/historical-acquire-runs")
async def historical_acquire_runs_endpoint(
    request: Request,
    sport: Optional[str] = None,
    limit: int = 25,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Read-only browse of `historical_acquire_runs`. Latest first."""
    if sport is not None:
        sport_l = sport.lower()
        if sport_l not in ("mlb", "nba", "nfl"):
            raise HTTPException(
                400, f"unsupported sport: {sport!r}")
        sport = sport_l
    if limit < 1 or limit > 100:
        raise HTTPException(
            400, f"limit must be in [1, 100] (got {limit})")
    db = _get_db()
    flt: Dict[str, Any] = {}
    if sport:
        flt["sport"] = sport
    rows: List[Dict[str, Any]] = []
    cursor = db["historical_acquire_runs"].find(
        flt, projection={"_id": 0, "per_date_counts": 0}
    ).sort("started_at", -1).limit(limit)
    async for d in cursor:
        rows.append(d)
    n_total = await db["historical_acquire_runs"].count_documents(flt)
    return {"ok": True, "n_total": int(n_total),
            "n_returned": len(rows), "rows": rows}


# ── Phase 4 — NFL player-prop historical ─────────────────────────────
class NflPlayerHistoricalAcquireBody(BaseModel):
    start:   str  = Field(..., description="UTC YYYY-MM-DD inclusive")
    end:     str  = Field(..., description="UTC YYYY-MM-DD inclusive")
    dry_run: bool = Field(default=True)


@router.post("/nfl-player-historical-acquire")
async def nfl_player_historical_acquire_endpoint(
    body: NflPlayerHistoricalAcquireBody,
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Pull NFL player-prop historical odds for `(start, end)` UTC
    window and upsert into `nfl_player_historical_props`. Phase 4:
    NFL only, acquire-all (no stat-family filter).
    """
    from workers.team.historical_player_ingest import (
        acquire_player_historical_window as _acq_player,
    )
    if not _DATE_RE.match(body.start or ""):
        raise HTTPException(
            400, f"start must be 'YYYY-MM-DD' (got {body.start!r})")
    if not _DATE_RE.match(body.end or ""):
        raise HTTPException(
            400, f"end must be 'YYYY-MM-DD' (got {body.end!r})")

    db = _get_db()
    api_key = _os.environ.get("SGO_API_KEY", "")
    audit = await _acq_player(
        db, sport="nfl",
        start_date=body.start, end_date=body.end,
        api_key=api_key, dry_run=body.dry_run,
    )
    await audit_log(
        request,
        action="nfl_player_historical_acquire",
        params={"start": body.start, "end": body.end,
                  "dry_run": body.dry_run},
        response_summary={
            "status":          audit["status"],
            "n_sgo_events":    audit["n_sgo_events"],
            "n_props_written": audit["n_props_written"],
            "n_props_upserted": audit["n_props_upserted"],
        },
        **auth,
    )
    return audit
