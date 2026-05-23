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

    Sorted server-side by the user-selected metric (descending for top,
    ascending for worst). Rows missing the metric are excluded from the
    ranked lists but still counted in `n_total` / `n_qualified`.
    """
    db = _get_db()
    # Confirm the run exists; gives the UI a clean 404 vs "no rows".
    run = await db[RUNS_COLL].find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, f"run_id not found: {run_id}")

    metric_field = _coerce_metric_key(sort_metric)

    q: Dict[str, Any] = {"run_id": run_id}
    if slice_:
        q["slice"] = slice_
    if tier:
        q["tier"] = tier
    if stat_family:
        q["stat_family"] = stat_family
    if side:
        q["side"] = side.upper()

    cells: List[Dict[str, Any]] = []
    async for c in db[RESULTS_COLL].find(q, {"_id": 0}):
        _annotate(c)
        cells.append(c)
    n_total = len(cells)

    qualified = [c for c in cells if (c.get("n_bets") or 0) >= min_bets]
    n_qualified = len(qualified)

    rankable = [
        c for c in qualified
        if _ranking_value(c, metric_field) is not None
    ]
    rankable.sort(
        key=lambda c: _ranking_value(c, metric_field) or 0.0,
        reverse=True,
    )

    top = rankable[:top_k]
    worst = list(reversed(rankable[-top_k:])) if rankable else []

    # Best by tier / stat_family / side / odds_bucket — bucketed off the
    # tier-family slice when present, else off ALL/STAT_FAMILY slices.
    def _bucket(key: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for c in rankable:
            k = c.get(key)
            if k is None:
                continue
            v = _ranking_value(c, metric_field) or 0.0
            cur = out.get(k)
            cur_v = _ranking_value(cur, metric_field) if cur else None
            if cur is None or v > (cur_v or -1e9):
                out[k] = c
        return out

    best_by_tier        = _bucket("tier")
    best_by_stat_family = _bucket("stat_family")
    best_by_side        = _bucket("side")
    best_by_odds_bucket = _bucket("odds_bucket")

    return {
        "ok": True,
        "run_id": run_id,
        "methodology":  run.get("methodology"),
        "version":      run.get("version"),
        "params":       run.get("params"),
        "status":       run.get("status"),
        "started_at":   run.get("started_at"),
        "finished_at": run.get("finished_at"),
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
