"""
Research Results API — dedicated endpoints for the Universal Research
Result Architecture.

Reads from:
    research_grid_runs      (1 doc per sweep; the run header)
    research_grid_results   (1 doc per evaluated cell, multiple slices)
    candidate_thresholds    (top stable configs promoted by a sweep)

Two sweep schemas land in the same collections and are auto-detected by
the `methodology` / `slice` fields:

    A) market_truth_pp_free   (scripts/research/grid_sweep.py)
       - slice ∈ {"ALL", "STAT_FAMILY", "SIDE"}
       - filters: consensus_prob_min, devig_book_count_min, …
       - calibration metric: calibration_delta_consensus

    B) per_tier_per_stat_family (scripts/sgo/historical_gate_replay_grid.py)
       - slice ∈ {"TIER_FAMILY", "TIER_FAMILY_SIDE"}
       - filters: hr_l20_min, hr_l5_min, cv_max, edge_min, tp_min
       - calibration metric: calibration_delta

Sort metrics exposed to the UI ("dropdown" per user choice):

    hit_rate, calibration_delta, calibration_delta_consensus,
    n_bets, profit_units, roi

`calibration_delta` is aliased to `calibration_delta_consensus` when
sorting/filtering schema-A rows, so the same dropdown works on both.

All endpoints sit under  /api/emergent-admin/research/*  and require
X-Admin-Token. They are audit-logged via the shared helper.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import audit_log, require_admin_token, _get_db

logger = logging.getLogger(__name__)
router = APIRouter()

RUNS_COLL       = "research_grid_runs"
RESULTS_COLL    = "research_grid_results"
CANDIDATES_COLL = "candidate_thresholds"
REPLAY_COLL     = "sgo_propvision_full_pipeline_replay"

# Sort metrics permitted from the UI. Map to the actual field on the cell
# doc; "calibration_delta_any" is a synthetic key that prefers the
# market-truth field and falls back to the per-tier one.
SORT_METRICS: Dict[str, str] = {
    "hit_rate":                       "hit_rate",
    "calibration_delta":              "calibration_delta",
    "calibration_delta_consensus":    "calibration_delta_consensus",
    "calibration_delta_any":          "_calibration_delta_any",
    "n_bets":                         "n_bets",
    "profit_units":                   "profit_units",
    "roi":                            "roi",
}


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(d, dict):
        d.pop("_id", None)
    return d


def _coerce_metric_key(metric: str) -> str:
    if metric not in SORT_METRICS:
        raise HTTPException(
            400,
            f"invalid sort metric '{metric}'. allowed: "
            f"{sorted(SORT_METRICS.keys())}",
        )
    return SORT_METRICS[metric]


def _ranking_value(cell: Dict[str, Any], field: str) -> Optional[float]:
    """Pull the ranking number off the cell. Handles the synthetic
    `_calibration_delta_any` which prefers consensus delta then falls
    back to the legacy delta."""
    if field == "_calibration_delta_any":
        v = cell.get("calibration_delta_consensus")
        if v is None:
            v = cell.get("calibration_delta")
        return v
    v = cell.get(field)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _annotate(cell: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure a unified `calibration_delta_any` is present on every cell
    so the frontend has one field to render no matter the methodology."""
    cell["calibration_delta_any"] = (
        cell.get("calibration_delta_consensus")
        if cell.get("calibration_delta_consensus") is not None
        else cell.get("calibration_delta")
    )
    return cell


# ── Run list ──────────────────────────────────────────────────────────
@router.get("/grid-runs")
async def list_runs(
    request: Request,
    sport: Optional[str] = Query(default=None, description="MLB / NFL / NBA"),
    methodology: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    auth=Depends(require_admin_token),
):
    db = _get_db()
    q: Dict[str, Any] = {}
    if sport:
        q["params.league"] = sport.upper()
    if methodology:
        q["methodology"] = methodology
    if status:
        q["status"] = status
    cur = db[RUNS_COLL].find(q, {"_id": 0}).sort("started_at", -1).limit(limit)
    docs: List[Dict[str, Any]] = []
    async for d in cur:
        docs.append(_strip(d))
    return {"ok": True, "n": len(docs), "runs": docs}


@router.get("/grid-runs/{run_id}")
async def get_run(run_id: str, request: Request,
                    auth=Depends(require_admin_token)):
    db = _get_db()
    doc = await db[RUNS_COLL].find_one({"run_id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"run_id not found: {run_id}")
    return {"ok": True, "run": _strip(doc)}


# ── Cell results for one run ──────────────────────────────────────────
@router.get("/grid-results/{run_id}")
async def get_grid_results(
    run_id: str, request: Request,
    sort_metric: str = Query(default="hit_rate"),
    min_bets:    int = Query(default=0, ge=0),
    slice_:      Optional[str] = Query(default=None, alias="slice"),
    tier:        Optional[str] = Query(default=None),
    stat_family: Optional[str] = Query(default=None),
    side:        Optional[str] = Query(default=None),
    top_k:       int = Query(default=25, ge=1, le=500),
    auth=Depends(require_admin_token),
):
    """Returns ranked top + worst cells plus best-of breakdowns.

    Sort + limit run server-side on the Mongo aggregation pipeline.
    The endpoint NEVER loads the full cell set into Python memory, so
    runs with 200k+ cells remain cheap.
    """
    db = _get_db()
    run = await db[RUNS_COLL].find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, f"run_id not found: {run_id}")

    metric_field = _coerce_metric_key(sort_metric)
    is_synthetic = metric_field == "_calibration_delta_any"

    base_match: Dict[str, Any] = {"run_id": run_id}
    if slice_:      base_match["slice"]       = slice_
    if tier:        base_match["tier"]        = tier
    if stat_family: base_match["stat_family"] = stat_family
    if side:        base_match["side"]        = side.upper()

    # Counts — n_total ignores min_bets; n_qualified applies it.
    n_total     = await db[RESULTS_COLL].count_documents(base_match)
    qualified_match: Dict[str, Any] = dict(base_match)
    if min_bets > 0:
        qualified_match["n_bets"] = {"$gte": min_bets}
    n_qualified = await db[RESULTS_COLL].count_documents(qualified_match)

    # The synthetic `calibration_delta_any` is computed via $ifNull so the
    # sort happens server-side without per-document Python coercion.
    add_fields: Dict[str, Any] = {}
    if is_synthetic:
        add_fields["_rank_value"] = {
            "$ifNull": ["$calibration_delta_consensus",
                          {"$ifNull": ["$calibration_delta", None]}],
        }
    else:
        add_fields["_rank_value"] = f"${metric_field}"
    add_fields["calibration_delta_any"] = {
        "$ifNull": ["$calibration_delta_consensus",
                      {"$ifNull": ["$calibration_delta", None]}],
    }

    async def _ranked(direction: int, limit: int) -> List[Dict[str, Any]]:
        pipeline: List[Dict[str, Any]] = [
            {"$match": {**qualified_match,
                          # Exclude rows where the chosen metric is null.
                          # We can't push this into $match before $addFields
                          # for the synthetic key, so do it after.
                          }},
            {"$addFields": add_fields},
            {"$match": {"_rank_value": {"$ne": None, "$type": "number"}}},
            {"$sort": {"_rank_value": direction, "_id": 1}},
            {"$limit": limit},
            {"$project": {"_id": 0}},
        ]
        return [d async for d in db[RESULTS_COLL].aggregate(pipeline,
                                                                   allowDiskUse=True)]

    top   = await _ranked(direction=-1, limit=top_k)
    worst = await _ranked(direction= 1, limit=top_k)

    # "Best by X" — one aggregation per bucket. $first picks the highest
    # ranked row per group after sorting by the chosen metric desc.
    async def _bucket(key: str) -> Dict[str, Any]:
        # Skip groups where the key is absent / null.
        pipeline: List[Dict[str, Any]] = [
            {"$match": {**qualified_match, key: {"$ne": None}}},
            {"$addFields": add_fields},
            {"$match": {"_rank_value": {"$ne": None, "$type": "number"}}},
            {"$sort": {"_rank_value": -1, "_id": 1}},
            {"$group": {"_id": f"${key}", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$project": {"_id": 0}},
            {"$limit": 200},
        ]
        out: Dict[str, Any] = {}
        async for d in db[RESULTS_COLL].aggregate(pipeline, allowDiskUse=True):
            k = d.get(key)
            if k is not None and k not in out:
                out[k] = d
        return out

    best_by_tier        = await _bucket("tier")
    best_by_stat_family = await _bucket("stat_family")
    best_by_side        = await _bucket("side")
    best_by_odds_bucket = await _bucket("odds_bucket")

    return {
        "ok": True,
        "run_id": run_id,
        "methodology":  run.get("methodology"),
        "version":      run.get("version"),
        "params":       run.get("params"),
        "status":       run.get("status"),
        "started_at":   run.get("started_at"),
        "finished_at":  run.get("finished_at"),
        "n_total":      n_total,
        "n_qualified":  n_qualified,
        "min_bets":     min_bets,
        "sort_metric":  sort_metric,
        "top":          top,
        "worst":        worst,
        "best_by_tier":        best_by_tier,
        "best_by_stat_family": best_by_stat_family,
        "best_by_side":        best_by_side,
        "best_by_odds_bucket": best_by_odds_bucket,
    }


# ── Paginated raw cell scan (UI table / CSV export) ───────────────────
@router.get("/grid-results/{run_id}/cells")
async def list_grid_cells(
    run_id: str, request: Request,
    sort_metric: str = Query(default="hit_rate"),
    direction:   int = Query(default=-1, ge=-1, le=1),
    min_bets:    int = Query(default=0, ge=0),
    slice_:      Optional[str] = Query(default=None, alias="slice"),
    tier:        Optional[str] = Query(default=None),
    stat_family: Optional[str] = Query(default=None),
    side:        Optional[str] = Query(default=None),
    offset:      int = Query(default=0, ge=0),
    limit:       int = Query(default=100, ge=1, le=500),
    auth=Depends(require_admin_token),
):
    """Paginated cell scan. Hard-capped at 500 rows per response so we
    never blow the API pod's memory."""
    db = _get_db()
    metric_field = _coerce_metric_key(sort_metric)
    is_synthetic = metric_field == "_calibration_delta_any"

    match: Dict[str, Any] = {"run_id": run_id}
    if slice_:      match["slice"]       = slice_
    if tier:        match["tier"]        = tier
    if stat_family: match["stat_family"] = stat_family
    if side:        match["side"]        = side.upper()
    if min_bets > 0:
        match["n_bets"] = {"$gte": min_bets}

    add_fields: Dict[str, Any] = {}
    if is_synthetic:
        add_fields["_rank_value"] = {
            "$ifNull": ["$calibration_delta_consensus",
                          {"$ifNull": ["$calibration_delta", None]}],
        }
    else:
        add_fields["_rank_value"] = f"${metric_field}"
    add_fields["calibration_delta_any"] = {
        "$ifNull": ["$calibration_delta_consensus",
                      {"$ifNull": ["$calibration_delta", None]}],
    }

    n_total = await db[RESULTS_COLL].count_documents(match)

    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$addFields": add_fields},
        {"$sort": {"_rank_value": direction if direction != 0 else -1,
                      "_id": 1}},
        {"$skip": offset},
        {"$limit": limit},
        {"$project": {"_id": 0}},
    ]
    cells = [d async for d in db[RESULTS_COLL].aggregate(pipeline,
                                                                 allowDiskUse=True)]
    return {
        "ok": True, "run_id": run_id,
        "n_total":  n_total,
        "offset":   offset, "limit": limit,
        "returned": len(cells),
        "sort_metric": sort_metric, "direction": direction,
        "cells": cells,
    }


# ── Candidate thresholds promoted by a run ────────────────────────────
@router.get("/candidate-thresholds/{run_id}")
async def get_candidates(run_id: str, request: Request,
                            auth=Depends(require_admin_token)):
    db = _get_db()
    cur = db[CANDIDATES_COLL].find({"run_id": run_id}, {"_id": 0}) \
                                 .sort("rank", 1)
    docs: List[Dict[str, Any]] = []
    async for d in cur:
        docs.append(_strip(d))
    return {"ok": True, "n": len(docs), "candidates": docs}


# ── List the most recent candidate thresholds across all runs ─────────
@router.get("/candidate-thresholds")
async def list_candidates(
    request: Request,
    sport: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    auth=Depends(require_admin_token),
):
    db = _get_db()
    q: Dict[str, Any] = {}
    if sport:
        q["league"] = sport.upper()
    cur = db[CANDIDATES_COLL].find(q, {"_id": 0}) \
                                 .sort("created_at", -1).limit(limit)
    docs: List[Dict[str, Any]] = []
    async for d in cur:
        docs.append(_strip(d))
    return {"ok": True, "n": len(docs), "candidates": docs}


# ── Metric catalog (lets the UI render the sort dropdown from this) ───
@router.get("/_meta/sort-metrics")
async def get_sort_metrics(request: Request,
                                auth=Depends(require_admin_token)):
    return {
        "ok": True,
        "metrics": [
            {"key": "hit_rate",
              "label": "Hit Rate",
              "description": "Win rate over settled bets in the cell."},
            {"key": "calibration_delta_any",
              "label": "Calibration Δ (auto)",
              "description":
                  "Prefers consensus delta (PP-free sweep) and falls "
                  "back to legacy delta. Recommended default."},
            {"key": "calibration_delta_consensus",
              "label": "Calibration Δ vs Consensus",
              "description":
                  "hit_rate − consensus_prob_avg. PP-free schema only."},
            {"key": "calibration_delta",
              "label": "Calibration Δ (legacy)",
              "description":
                  "hit_rate − consensus_avg. Per-tier schema only."},
            {"key": "n_bets",
              "label": "Sample Size",
              "description": "Cells with more historical bets first."},
            {"key": "profit_units",
              "label": "Profit (units)",
              "description":
                  "Total +/-1 unit P&L. Only populated when the sweep "
                  "computes payouts."},
            {"key": "roi",
              "label": "ROI",
              "description":
                  "profit_units / n_bets. Only populated when the "
                  "sweep computes payouts."},
        ],
    }



# ── Most-recent cached pipeline window ────────────────────────────────
@router.get("/last-pipeline-window")
async def last_pipeline_window(
    request: Request,
    sport: str = Query(default="MLB"),
    auth=Depends(require_admin_token),
):
    """Returns the min/max `game_date` present in the SSOT replay cache
    for the requested sport. The Sweep/Optimizer UI uses this to
    autoload a window that's guaranteed to be 100% cached — so the run
    consumes zero SGO credits.

    Response shape:
        { ok, sport, start, end, n_rows, n_distinct_dates }

    `start`/`end` are `null` when the cache is empty for the sport.
    """
    db = _get_db()
    league = sport.upper()
    pipeline = [
        {"$match": {"league_id": league}},
        {"$group": {
            "_id": None,
            "min_date": {"$min": "$game_date"},
            "max_date": {"$max": "$game_date"},
            "n_rows":   {"$sum": 1},
            "dates":    {"$addToSet": "$game_date"},
        }},
        {"$project": {
            "_id": 0,
            "min_date": 1, "max_date": 1, "n_rows": 1,
            "n_distinct_dates": {"$size": "$dates"},
        }},
    ]
    cur = db[REPLAY_COLL].aggregate(pipeline)
    docs = [d async for d in cur]
    if not docs:
        return {"ok": True, "sport": league,
                  "start": None, "end": None,
                  "n_rows": 0, "n_distinct_dates": 0}
    d = docs[0]
    return {
        "ok": True, "sport": league,
        "start": d.get("min_date"), "end": d.get("max_date"),
        "n_rows": d.get("n_rows", 0),
        "n_distinct_dates": d.get("n_distinct_dates", 0),
    }



# ── Replay outcome diagnostic ────────────────────────────────────────
@router.get("/replay-outcome-coverage")
async def replay_outcome_coverage(
    request: Request,
    sport: str  = Query(...),
    start: str  = Query(...),
    end:   str  = Query(...),
    sample_missing: int = Query(default=3, ge=0, le=20),
    auth=Depends(require_admin_token),
):
    """Diagnoses *why* optimizer HR/ROI come back as null/zero.

    The optimizer aggregates the cached replay collection. If those
    rows are missing `outcome_numeric` (because the outcomes join in
    `_mirror_to_legacy` silently failed), every cell reports
    `hit_rate=None, roi=0` no matter how big the sample.

    Returns:
        n_total, n_outcome_resolved, n_with_outcome_numeric,
        n_with_odds, n_with_payout, by_stat_family breakdown,
        and a sample of unresolved rows for triage.
    """
    db = _get_db()
    league = sport.upper()
    match: Dict[str, Any] = {
        "league_id": league,
        "game_date": {"$gte": start, "$lte": end},
    }
    n_total = await db[REPLAY_COLL].count_documents(match)
    if n_total == 0:
        return {"ok": True, "sport": league, "start": start, "end": end,
                  "n_total": 0,
                  "diagnosis": "no rows in replay collection for this window"}

    n_resolved        = await db[REPLAY_COLL].count_documents(
        {**match, "outcome_resolved": True})
    n_outcome_numeric = await db[REPLAY_COLL].count_documents(
        {**match, "outcome_numeric": {"$in": [0, 1, 0.5]}})
    n_with_odds       = await db[REPLAY_COLL].count_documents(
        {**match, "odds": {"$ne": None, "$exists": True}})

    # by stat_family
    fam_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$stat_family",
            "n_total": {"$sum": 1},
            "n_resolved": {"$sum": {"$cond": [
                {"$eq": ["$outcome_resolved", True]}, 1, 0]}},
            "n_with_outcome_numeric": {"$sum": {"$cond": [
                {"$in": ["$outcome_numeric", [0, 1, 0.5]]}, 1, 0]}},
            "n_with_odds": {"$sum": {"$cond": [
                {"$and": [
                    {"$ne": ["$odds", None]},
                    {"$ne": [{"$type": "$odds"}, "missing"]},
                ]}, 1, 0]}},
        }},
        {"$sort": {"n_total": -1}},
        {"$project": {"_id": 0, "stat_family": "$_id",
                          "n_total": 1, "n_resolved": 1,
                          "n_with_outcome_numeric": 1, "n_with_odds": 1}},
    ]
    by_family = [d async for d in db[REPLAY_COLL].aggregate(fam_pipeline,
                                                                       allowDiskUse=True)]

    # sample rows whose outcome is null
    missing_match = {**match,
                        "$or": [
                            {"outcome_numeric": None},
                            {"outcome_resolved": {"$ne": True}},
                        ]}
    sample_unresolved: List[Dict[str, Any]] = []
    if sample_missing > 0:
        async for r in db[REPLAY_COLL].find(
            missing_match,
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                          "player_name": 1, "player_name_normalized": 1,
                          "stat_family": 1, "market": 1, "stat_id": 1,
                          "side": 1, "line": 1, "odds": 1, "game_date": 1,
                          "outcome_resolved": 1, "outcome_numeric": 1,
                          "ssot_source": 1, "pipeline_version": 1}).limit(sample_missing):
            sample_unresolved.append(r)

    # Build a clear, single-line diagnosis the operator can paste into a bug.
    diagnosis: str
    if n_resolved == 0:
        diagnosis = (
            f"CRITICAL: {n_total} replay rows but 0 have outcome_resolved=true. "
            "The mirror→outcomes join is failing. Check that "
            "sgo_pp_research_outcomes has rows for this window AND that "
            "the join keys (event_id, player_name_normalized, market, line, side) "
            "match between RUNNER_OUTPUTS and the outcomes collection. "
            "Re-run `scripts.sgo.build_historical_outcomes` then re-run "
            "the full pipeline replay so the mirror picks up the new outcomes."
        )
    elif n_outcome_numeric < n_resolved:
        diagnosis = (
            f"{n_resolved}/{n_total} rows mark outcome_resolved=true but only "
            f"{n_outcome_numeric} carry a numeric outcome_numeric value. "
            "Likely a unresolved-side mismatch — re-run "
            "build_historical_outcomes with --debug-unresolved."
        )
    elif n_outcome_numeric < n_total * 0.50:
        diagnosis = (
            f"{n_outcome_numeric}/{n_total} ({n_outcome_numeric*100.0/n_total:.1f}%) "
            "rows have a numeric outcome. Backfill more outcomes or shrink the "
            "window."
        )
    else:
        diagnosis = (
            f"{n_outcome_numeric}/{n_total} rows graded — looks healthy. "
            "If the optimizer still shows null HR/ROI, the filters likely "
            "exclude all graded rows; widen min_bets or relax thresholds."
        )

    await audit_log(request, action="replay_outcome_coverage",
                       params={"sport": league, "start": start, "end": end},
                       response_summary={
                           "n_total": n_total,
                           "n_outcome_numeric": n_outcome_numeric,
                           "pct_graded": round(n_outcome_numeric * 100.0
                                                   / max(n_total, 1), 2),
                       }, **auth)
    return {
        "ok": True, "sport": league, "start": start, "end": end,
        "n_total":                  n_total,
        "n_outcome_resolved":       n_resolved,
        "n_with_outcome_numeric":   n_outcome_numeric,
        "n_with_odds":              n_with_odds,
        "pct_graded":               round(n_outcome_numeric * 100.0
                                              / max(n_total, 1), 2),
        "by_stat_family":           by_family,
        "sample_unresolved":        sample_unresolved,
        "diagnosis":                diagnosis,
    }
