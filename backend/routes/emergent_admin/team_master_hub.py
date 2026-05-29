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
from typing import Any, Dict, Optional

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
