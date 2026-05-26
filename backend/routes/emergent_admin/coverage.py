"""
Local Warehouse Coverage — read-only health check across the local
replay data warehouse. Powers the /admin/testing offline-mode UX so
the operator knows whether a replay can run fully from cache or needs
a one-time SGO ingest first.

Tracked collections (replay-ready warehouse):
  • sgo_player_stats                    — graded historical box scores
  • sgo_pp_research_model_features      — pre-game features
  • sgo_pp_research_model_predictions   — model TP/sigma per prop
  • sgo_propvision_full_pipeline_replay — SSOT-replay rows (final)

For each collection × date in window:
  - row_count
  - "stale" if row_count < `stale_threshold` (default 1 — i.e. only zero is stale)
A date is "replay_ready" when ALL FOUR collections have ≥ 1 row on it.

Endpoints (token-gated, audit-logged):

  GET /api/emergent-admin/coverage/
      Query: sport, start, end, [stale_threshold]
      Returns: per-collection stats + per-date matrix + replay-ready %

  GET /api/emergent-admin/coverage/missing
      Query: sport, start, end, [collection]
      Returns: list of dates missing from the given (or every) collection
              with the recommended fix-job per gap

Performance: a single aggregation per collection over the date range. For
30-day windows this is sub-100ms; for season-long windows under 1s.
"""
from __future__ import annotations
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db
from .policy import job_allowed
from workers.queue import enqueue as worker_enqueue, is_heavy

logger = logging.getLogger("emergent_admin.coverage")
router = APIRouter()

# Per-collection metadata: how does it identify (sport, date), and what
# job fixes a gap?
WAREHOUSE: List[Dict[str, Any]] = [
    {
        "key": "stats",
        "coll": "sgo_player_stats",
        "label": "Historical stats",
        "league_field": "league_id",
        "date_field":   "game_date",
        "fix_job":      "scripts.sgo.ingest_historical_player_stats",
    },
    {
        "key": "features",
        "coll": "sgo_pp_research_model_features",
        "label": "Model features",
        "league_field": "league_id",
        "date_field":   "game_date",
        "fix_job":      "scripts.sgo.build_historical_model_features",
    },
    {
        "key": "predictions",
        "coll": "sgo_pp_research_model_predictions",
        "label": "Model predictions",
        "league_field": "league_id",
        "date_field":   "game_date",
        "fix_job":      "scripts.sgo.score_historical_with_live_mlb_hf",
    },
    {
        "key": "replay",
        "coll": "sgo_propvision_full_pipeline_replay",
        "label": "Full-pipeline replay rows",
        "league_field": "league_id",
        "date_field":   "game_date",
        "fix_job":      "scripts.sgo.historical_full_pipeline_replay",
    },
]


def _enum_dates(start: str, end: str) -> List[str]:
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(400, f"bad date: {exc}") from exc
    if e < s:
        raise HTTPException(400, "end < start")
    out: List[str] = []
    cur = s
    while cur <= e:
        out.append(cur.isoformat()); cur += timedelta(days=1)
    return out


async def _per_date_counts(db, *, coll: str, league_field: str,
                                date_field: str, sport: str,
                                start: str, end: str) -> Dict[str, int]:
    pipeline = [
        {"$match": {
            league_field: sport,
            date_field:   {"$gte": start, "$lte": end},
        }},
        {"$group": {"_id": f"${date_field}", "n": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for d in db[coll].aggregate(pipeline, allowDiskUse=True, maxTimeMS=30_000):
        out[str(d["_id"])] = int(d.get("n", 0))
    return out


@router.get("")
@router.get("/")
async def coverage(request: Request,
                        sport: str = Query(...),
                        start: str = Query(...),
                        end:   str = Query(...),
                        stale_threshold: int = Query(1, ge=0),
                        auth=Depends(require_admin_token)):
    sport = sport.upper()
    db = _get_db()
    days = _enum_dates(start, end)
    day_set = set(days)

    # Pull counts per collection
    by_coll: Dict[str, Dict[str, Any]] = {}
    for entry in WAREHOUSE:
        counts = await _per_date_counts(
            db, coll=entry["coll"],
            league_field=entry["league_field"],
            date_field=entry["date_field"],
            sport=sport, start=start, end=end,
        )
        # Constrain to in-range dates (just in case)
        counts = {k: v for k, v in counts.items() if k in day_set}
        days_with_rows = [d for d, n in counts.items() if n >= 1]
        stale_days = [d for d, n in counts.items()
                          if 0 < n < stale_threshold]
        missing_days = [d for d in days if d not in counts]
        by_coll[entry["key"]] = {
            "key": entry["key"], "collection": entry["coll"],
            "label": entry["label"], "fix_job": entry["fix_job"],
            "row_count": sum(counts.values()),
            "days_total":      len(days),
            "days_with_rows":  len(days_with_rows),
            "days_stale":      len(stale_days),
            "days_missing":    len(missing_days),
            "coverage_pct":    round(len(days_with_rows) * 100.0 / max(len(days), 1), 2),
            "preview_missing": missing_days[:20],
            "preview_stale":   stale_days[:20],
        }

    # Replay-readiness = ALL 4 layers populated on a given date
    layer_keys = [e["key"] for e in WAREHOUSE]
    ready_days: List[str] = []
    unready_days: List[Dict[str, Any]] = []
    # Build a per-day map of which layers have rows
    layer_day_set: Dict[str, set] = {}
    for entry in WAREHOUSE:
        counts = await _per_date_counts(
            db, coll=entry["coll"],
            league_field=entry["league_field"],
            date_field=entry["date_field"],
            sport=sport, start=start, end=end,
        )
        layer_day_set[entry["key"]] = {d for d, n in counts.items()
                                                if n >= 1 and d in day_set}
    for d in days:
        missing = [k for k in layer_keys if d not in layer_day_set[k]]
        if not missing:
            ready_days.append(d)
        else:
            unready_days.append({"date": d, "missing_layers": missing})

    payload = {
        "ok": True,
        "sport": sport, "start": start, "end": end,
        "days_in_window": len(days),
        "stale_threshold": stale_threshold,
        "by_collection": by_coll,
        "replay_ready_days":       len(ready_days),
        "replay_ready_pct":        round(len(ready_days) * 100.0
                                            / max(len(days), 1), 2),
        "preview_ready_days":      ready_days[:20],
        "preview_unready_days":    unready_days[:30],
        "offline_mode_available":  len(ready_days) == len(days)
                                      and len(days) > 0,
    }
    await audit_log(request, action="warehouse_coverage",
                       params={"sport": sport, "start": start, "end": end},
                       response_summary={
                           "replay_ready_pct": payload["replay_ready_pct"],
                           "offline_mode_available": payload["offline_mode_available"],
                       }, **auth)
    return payload


@router.get("/missing")
async def coverage_missing(request: Request,
                                sport:      str = Query(...),
                                start:      str = Query(...),
                                end:        str = Query(...),
                                collection: Optional[str] = Query(None),
                                auth=Depends(require_admin_token)):
    sport = sport.upper()
    db = _get_db()
    days = _enum_dates(start, end)
    day_set = set(days)
    entries = WAREHOUSE
    if collection:
        entries = [e for e in WAREHOUSE
                       if e["coll"] == collection or e["key"] == collection]
        if not entries:
            raise HTTPException(400, f"unknown collection: {collection}")
    out: Dict[str, Any] = {"ok": True, "sport": sport,
                                "start": start, "end": end,
                                "by_collection": {}}
    for entry in entries:
        counts = await _per_date_counts(
            db, coll=entry["coll"],
            league_field=entry["league_field"],
            date_field=entry["date_field"],
            sport=sport, start=start, end=end,
        )
        present = {k for k, v in counts.items() if v >= 1 and k in day_set}
        missing = [d for d in days if d not in present]
        if not missing:
            continue
        out["by_collection"][entry["key"]] = {
            "collection": entry["coll"],
            "n_missing":  len(missing),
            "dates":      missing,
            "fix_job":    entry["fix_job"],
            "fix_args_template": ["--league", sport,
                                       "--start", missing[0],
                                       "--end",   missing[-1]],
        }
    await audit_log(request, action="warehouse_coverage_missing",
                       params={"sport": sport, "start": start, "end": end,
                                "collection": collection},
                       response_summary={
                           "layers_with_gaps": list(out["by_collection"].keys()),
                       }, **auth)
    return out



# ── Backfill with cache-preflight ───────────────────────────────────
JOBS_COLL = "emergent_admin_jobs"


class BackfillBody(BaseModel):
    """One-shot backfill request that gates on existing cache count.

    Either `key` (warehouse entry key like 'stats') OR `fix_job`
    (the python module name) must be supplied. `sport`, `start`, `end`
    are required. If `force=False` (default), and the source collection
    already has rows for the window, we return `status='cached_skip'`
    without enqueueing.
    """
    key:      Optional[str] = Field(default=None, description="WAREHOUSE entry key, e.g. 'stats'")
    fix_job:  Optional[str] = Field(default=None, description="Module name; resolved against WAREHOUSE")
    sport:    str           = Field(...)
    start:    str           = Field(...)
    end:      str           = Field(...)
    force:    bool          = Field(default=False)
    extra_args: List[str]   = Field(default_factory=list)


def _resolve_warehouse_entry(key: Optional[str], fix_job: Optional[str]) -> Dict[str, Any]:
    if key:
        for e in WAREHOUSE:
            if e["key"] == key:
                return e
    if fix_job:
        for e in WAREHOUSE:
            if e["fix_job"] == fix_job:
                return e
    raise HTTPException(400, f"unknown warehouse entry (key={key!r} fix_job={fix_job!r})")


@router.post("/backfill")
async def backfill(body: BackfillBody, request: Request,
                       auth=Depends(require_admin_token)):
    """Cache-preflight + enqueue. Refuses to spawn a redundant worker
    job when the source collection already holds rows for the window.

    Response shape (always 200 unless validation fails):
        {ok: true,
         status: "cached_skip" | "queued",
         row_count, days_with_rows, days_in_window,
         job_id?: str, routed_to?: "research_worker"|"inline",
         collection: str, fix_job: str}
    """
    entry = _resolve_warehouse_entry(body.key, body.fix_job)
    sport = body.sport.upper()
    db = _get_db()
    days = _enum_dates(body.start, body.end)
    counts = await _per_date_counts(
        db, coll=entry["coll"],
        league_field=entry["league_field"],
        date_field=entry["date_field"],
        sport=sport, start=body.start, end=body.end,
    )
    counts = {k: v for k, v in counts.items() if k in set(days)}
    row_count = sum(counts.values())
    days_with_rows = len([d for d, n in counts.items() if n >= 1])

    # ── Preflight cache hit ────────────────────────────────────────
    if not body.force and row_count > 0:
        await audit_log(request, action="coverage_backfill_cached_skip",
                          params={"sport": sport, "start": body.start,
                                    "end": body.end, "key": entry["key"]},
                          response_summary={"row_count": row_count,
                                                "days_with_rows": days_with_rows},
                          **auth)
        return {
            "ok": True, "status": "cached_skip",
            "row_count": row_count,
            "days_with_rows": days_with_rows,
            "days_in_window": len(days),
            "collection": entry["coll"],
            "fix_job":    entry["fix_job"],
            "message": (
                f"{entry['coll']} already has {row_count} rows for "
                f"{sport} {body.start}..{body.end} ({days_with_rows}/{len(days)} days). "
                "Pass force=true to re-run."
            ),
        }

    # ── Cache miss → enqueue ────────────────────────────────────────
    module = entry["fix_job"]
    if not job_allowed(module):
        raise HTTPException(403, f"job module not allowlisted: {module}")
    args = ["--league", sport, "--start", body.start, "--end", body.end,
              *(body.extra_args or [])]
    job_id = str(uuid.uuid4())
    routed_to = "inline"
    if is_heavy(module):
        await worker_enqueue(
            job_id, module=module, args=args,
            agent_id=auth["agent_id"], token_hash=auth["token_hash"],
            kind="script",
        )
        routed_to = "research_worker"
    else:
        # Light jobs run inline through the existing jobs path.
        doc = {
            "job_id":      job_id,
            "module":      module,
            "args":        args,
            "status":      "queued",
            "queued_at":   datetime.now(timezone.utc),
            "agent_id":    auth["agent_id"],
            "token_hash":  auth["token_hash"],
            "log":         [],
        }
        await db[JOBS_COLL].insert_one(doc)
        # Fire the inline runner (uses the same machinery as /jobs/run)
        import asyncio
        from .jobs import _run_job, _RUNNER_TASKS
        task = asyncio.create_task(_run_job(job_id, module, args))
        _RUNNER_TASKS.add(task)
        task.add_done_callback(_RUNNER_TASKS.discard)

    await audit_log(request, action="coverage_backfill_enqueue",
                       params={"sport": sport, "start": body.start,
                                "end": body.end, "key": entry["key"],
                                "module": module, "args": args,
                                "force": body.force, "job_id": job_id,
                                "routed_to": routed_to},
                       response_summary={"job_id": job_id,
                                              "routed_to": routed_to,
                                              "preflight_rows": row_count},
                       **auth)
    logger.info("[backfill] enqueued module=%s args=%s job_id=%s "
                  "routed_to=%s preflight_rows=%s",
                  module, args, job_id, routed_to, row_count)
    return {
        "ok": True, "status": "queued",
        "job_id": job_id, "routed_to": routed_to,
        "preflight_rows": row_count,
        "preflight_days_with_rows": days_with_rows,
        "days_in_window": len(days),
        "collection": entry["coll"],
        "fix_job":    module,
        "args":       args,
    }
