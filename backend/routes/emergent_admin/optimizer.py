"""
Auto-Optimizer — large-scale threshold grid search over the cached
`sgo_propvision_full_pipeline_replay` collection (produced by the SSOT
historical replay pipeline).

Key design choice: this module does NOT re-run the production pipeline.
The replay cache already contains every prop's `model_probability`,
`cv`, `edge`, `tp`, `hit_rate_l*`, plus the graded `outcome_numeric`.
The optimizer iterates threshold combos in-process over the cached
rows per (tier × stat_family × odds_bucket × side) cell. This makes
hundreds of thousands of combo evaluations fast (single-digit minutes
on a typical month-long replay window).

Endpoints (all token-gated, audit-logged):

    POST   /api/emergent-admin/optimizer/run
        Start a background optimization task.

    GET    /api/emergent-admin/optimizer/{run_id}
        Poll progress + best-so-far.

    GET    /api/emergent-admin/optimizer/{run_id}/results
        Top / worst / best-by-tier / best-by-family / best-by-bucket.

    POST   /api/emergent-admin/optimizer/{run_id}/save_as_default
        Promote the top-K configs into `candidate_thresholds` with
        `status="candidate"`. Per-cell — does NOT touch live gates.

    POST   /api/emergent-admin/optimizer/{run_id}/set_testing_default
        Mark the top config of the run as the "testing default" preset
        the Sweep tab loads on the next page open. Live gates untouched.

Concurrency: per-cell evaluation is dispatched via asyncio.gather with
a safe worker semaphore (default 4). Progress + best-so-far are stored
in-process and persisted to `optimizer_runs` every 5 seconds.
"""
from __future__ import annotations
import asyncio
import itertools
import logging
import math
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db
from workers.queue import enqueue as worker_enqueue

logger = logging.getLogger(__name__)
router = APIRouter()

REPLAY_COLL          = "sgo_propvision_full_pipeline_replay"
CANDIDATE_THRESH     = "candidate_thresholds"
OPTIMIZER_RUNS       = "optimizer_runs"
# Per-cell-config rows streamed to Mongo instead of held in uvicorn RAM.
# Indexed (run_id, score desc) so readers can $sort + $limit on the
# server. Bounded retention enforced via per-cell top-K (200) at write.
OPTIMIZER_RESULTS    = "optimizer_run_results"
TESTING_DEFAULTS     = "admin_testing_defaults"

# Cap on `state["failures"]` so it can never grow unbounded inside an
# inflight run (a degenerate sweep can otherwise log thousands of rows).
MAX_INLINE_FAILURES = 50

# Default grid values (used only when the request doesn't supply ranges)
DEFAULT_GRID: Dict[str, List[float]] = {
    "hr_l20_min": [0.55, 0.65, 0.70, 0.75, 0.80],
    "hr_l10_min": [0.55, 0.65, 0.70],
    "hr_l5_min":  [0.50, 0.60, 0.70],
    "cv_max":     [0.50, 0.70, 0.90, 1.10],
    "edge_min":   [0.02, 0.05, 0.08, 0.10],
    "tp_min":     [0.50, 0.55, 0.60, 0.65],
}

DEFAULT_TIERS         = ["safe_haven", "front_lines", "war_zone"]
DEFAULT_ODDS_BUCKETS  = [
    "odds_lt_-200", "odds_-200_-100", "odds_-100_-0",
    "odds_+0_+150", "odds_+150_+300", "odds_+300p",
]

# In-process state — survives across requests as long as backend stays up.
_RUNS: Dict[str, Dict[str, Any]] = {}
# Strong references to background tasks (prevents asyncio GC mid-run).
_OPT_TASKS: set = set()


# ── Request / response models ──────────────────────────────────────
class GridSpec(BaseModel):
    hr_l20_min: Optional[List[float]] = None
    hr_l10_min: Optional[List[float]] = None
    hr_l5_min:  Optional[List[float]] = None
    cv_max:     Optional[List[float]] = None
    edge_min:   Optional[List[float]] = None
    tp_min:     Optional[List[float]] = None


class RequiredFilters(BaseModel):
    """Non-swept hard requirements applied to every candidate row."""
    vision_score_min:           Optional[float] = None
    sharp_book_count_min:       Optional[int]   = None
    devig_book_count_min:       Optional[int]   = None
    market_width_max:           Optional[float] = None
    consensus_disagreement_max: Optional[float] = None
    projection_margin_min:      Optional[float] = None


class OptimizerRunBody(BaseModel):
    sport:                 str   = Field(default="MLB")
    start:                 str
    end:                   str
    tiers:                 List[str] = Field(default_factory=lambda: list(DEFAULT_TIERS))
    stat_families:         Optional[List[str]] = None    # None == all
    odds_buckets:          Optional[List[str]] = None    # None == all
    sides:                 List[str] = Field(default_factory=lambda: ["OVER", "UNDER"])
    min_bets:              int   = Field(default=30, ge=1)
    max_configs_per_cell:  int   = Field(default=500, ge=1, le=50_000)
    optimization_goal:     str   = Field(default="balanced")
    grid:                  GridSpec = Field(default_factory=GridSpec)
    filters:               RequiredFilters = Field(default_factory=RequiredFilters)
    worker_limit:          int   = Field(default=4, ge=1, le=16)
    # When True, each cell restricts to rows where THIS tier's gates
    # actually passed in the prod runner. Default OFF: tier is just a
    # label, all cells query the full row pool. (Strict mode produces
    # tiny samples on real data — only use when you have specifically
    # backfilled enough rows.)
    enforce_tier_gates:    bool  = Field(default=False)


# ── Combo generation ──────────────────────────────────────────────
def _resolve_grid(spec: GridSpec) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for axis, default in DEFAULT_GRID.items():
        v = getattr(spec, axis, None)
        out[axis] = list(v) if v else list(default)
    return out


def _enumerate_combos(grid: Dict[str, List[float]],
                          max_per_cell: int) -> List[Dict[str, float]]:
    axes = sorted(grid.keys())
    spaces = [grid[a] for a in axes]
    total = 1
    for s in spaces:
        total *= max(1, len(s))
    if total <= max_per_cell:
        return [dict(zip(axes, vals)) for vals in itertools.product(*spaces)]
    # Random sample without replacement — bounded by max_per_cell
    sampled: set = set()
    out: List[Dict[str, float]] = []
    attempts = 0
    while len(out) < max_per_cell and attempts < max_per_cell * 4:
        attempts += 1
        choice = tuple(random.choice(s) for s in spaces)
        if choice in sampled:
            continue
        sampled.add(choice)
        out.append(dict(zip(axes, choice)))
    return out


# ── Replay-row loading per cell ────────────────────────────────────
def _row_passes_filters(row: Dict[str, Any],
                            f: RequiredFilters) -> bool:
    if f.vision_score_min is not None:
        v = row.get("vision_score")
        if v is None or v < f.vision_score_min:
            return False
    if f.sharp_book_count_min is not None:
        v = row.get("sharp_book_count")
        if v is None or v < f.sharp_book_count_min:
            return False
    if f.devig_book_count_min is not None:
        v = row.get("devig_book_count")
        if v is None or v < f.devig_book_count_min:
            return False
    if f.market_width_max is not None:
        v = row.get("market_width")
        if v is None or v > f.market_width_max:
            return False
    if f.consensus_disagreement_max is not None:
        v = row.get("consensus_disagreement")
        if v is None or v > f.consensus_disagreement_max:
            return False
    if f.projection_margin_min is not None:
        v = row.get("projection_margin")
        if v is None or v < f.projection_margin_min:
            return False
    return True


def _row_passes_combo(row: Dict[str, Any],
                          combo: Dict[str, float]) -> bool:
    # Direction has already been enforced by the SSOT pipeline (rows with
    # no projection_margin direction match never reach the cache). Apply
    # the swept thresholds only.
    pairs = [
        ("hit_rate_l20",      "hr_l20_min", False),
        ("hit_rate_l10",      "hr_l10_min", False),
        ("hit_rate_l5",       "hr_l5_min",  False),
        ("cv",                "cv_max",     True),
        ("edge",              "edge_min",   False),
        ("model_probability", "tp_min",     False),
    ]
    for row_key, combo_key, is_max in pairs:
        thr = combo.get(combo_key)
        if thr is None:
            continue
        v = row.get(row_key)
        if v is None:
            return False
        # hit_rate is stored as percentage 0-100 in the replay cache; the
        # grid thresholds use 0-1. Normalize.
        if row_key.startswith("hit_rate"):
            v_norm = v / 100.0 if v > 1.0 else v
        else:
            v_norm = v
        if is_max:
            if v_norm > thr:
                return False
        else:
            if v_norm < thr:
                return False
    return True


def _payout_units(row: Dict[str, Any]) -> Optional[float]:
    """American odds → win=units / lose=-1 / push=0. None when ungraded."""
    on = row.get("outcome_numeric")
    odds = row.get("odds")
    if on is None or odds is None:
        return None
    try:
        o = float(odds)
        if on == 1:
            return o / 100.0 if o > 0 else 100.0 / abs(o)
        if on == 0.5:
            return 0.0
        return -1.0
    except (TypeError, ValueError):
        return None


def _stddev(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _max_drawdown(daily_pnl: List[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for p in daily_pnl:
        cum += p
        if cum > peak:
            peak = cum
        if peak - cum > dd:
            dd = peak - cum
    return dd


def _evaluate_combo(rows: List[Dict[str, Any]],
                          combo: Dict[str, float],
                          min_bets: int) -> Optional[Dict[str, Any]]:
    """Returns metrics or None when sample below min_bets."""
    qual: List[Dict[str, Any]] = []
    for r in rows:
        if _row_passes_combo(r, combo):
            qual.append(r)
    n = len(qual)
    if n < min_bets:
        return None
    wins = losses = pushes = ungraded = 0
    n_with_odds = 0
    n_with_payout = 0
    sum_tp = sum_cv = sum_edge = 0.0
    cnt_tp = cnt_cv = cnt_edge = 0
    daily: Dict[str, float] = {}
    pnl_units = 0.0
    for r in qual:
        on = r.get("outcome_numeric")
        if on == 1:
            wins += 1
        elif on == 0:
            losses += 1
        elif on == 0.5:
            pushes += 1
        else:
            ungraded += 1
        if r.get("odds") is not None:
            n_with_odds += 1
        p = _payout_units(r)
        if p is not None:
            n_with_payout += 1
            pnl_units += p
            daily[r.get("game_date") or "_"] = daily.get(r.get("game_date") or "_", 0.0) + p
        for k, sum_ref, cnt_ref in (("tp",   "sum_tp",   "cnt_tp"),
                                          ("cv",   "sum_cv",   "cnt_cv"),
                                          ("edge", "sum_edge", "cnt_edge")):
            v = r.get(k)
            if v is None and k == "tp":
                v = r.get("model_probability")
            if isinstance(v, (int, float)):
                if sum_ref == "sum_tp":   sum_tp += float(v);   cnt_tp += 1
                elif sum_ref == "sum_cv": sum_cv += float(v);   cnt_cv += 1
                else:                     sum_edge += float(v); cnt_edge += 1
    settled = wins + losses
    hit_rate = (wins / settled) if settled else None
    # ROI denominator = bets we could actually grade (i.e. ones with a
    # numeric payout). Using `n` here silently drove ROI → 0 whenever
    # replay rows had missing outcome_numeric/odds, masking the upstream
    # join failure. We now expose the ungraded count alongside so the
    # operator can see the diagnosis at-a-glance.
    roi      = (pnl_units / n_with_payout) if n_with_payout else None
    avg_tp   = (sum_tp / cnt_tp) if cnt_tp else None
    avg_cv   = (sum_cv / cnt_cv) if cnt_cv else None
    avg_edge = (sum_edge / cnt_edge) if cnt_edge else None
    daily_vals = list(daily.values())
    daily_consistency = (
        1.0 - (_stddev(daily_vals) / (abs(sum(daily_vals) / len(daily_vals)) + 1e-9))
        if len(daily_vals) >= 2 else None
    )
    max_dd = _max_drawdown(daily_vals)
    # Calibration delta = realized hit_rate - avg model TP. Positive means
    # model under-predicts; negative means model over-predicts.
    calibration_delta = (hit_rate - avg_tp) if (hit_rate is not None and avg_tp is not None) else None
    return {
        "n_bets": n,
        "n_graded": settled + pushes,     # rows with outcome ∈ {1, 0, 0.5}
        "n_ungraded": ungraded,           # rows with outcome_numeric == null
        "n_with_odds": n_with_odds,
        "n_with_payout": n_with_payout,
        "wins": wins, "losses": losses, "pushes": pushes,
        "hit_rate": hit_rate, "roi": roi,
        "calibration_delta": calibration_delta,
        "avg_tp": avg_tp, "avg_cv": avg_cv, "avg_edge": avg_edge,
        "daily_consistency": daily_consistency,
        "max_drawdown_units": max_dd,
        "profit_units": pnl_units,
        "n_days": len(daily_vals),
    }


def _score(metrics: Dict[str, Any], goal: str, baseline_n: int) -> Optional[float]:
    """Composite ranking score; higher is better.

    Returns None when the cell has no graded rows. Previously this
    function silently coalesced None metrics to 0.0, which caused
    completely-ungraded cells to score `0.0` and outrank legitimately
    graded cells with negative scores. That produced the "Top 25 are
    all empty" symptom in the optimizer Results panel.
    """
    n_graded = metrics.get("n_graded")
    if n_graded is None:
        # Old cell shape (pre-diagnostic-fields). Fall through to
        # legacy behavior but treat null HR as ungradable.
        if metrics.get("hit_rate") is None and metrics.get("roi") is None:
            return None
    elif n_graded < 1:
        return None
    hr   = metrics.get("hit_rate") or 0.0
    roi  = metrics.get("roi") or 0.0
    cal  = metrics.get("calibration_delta") or 0.0
    cons = metrics.get("daily_consistency") or 0.0
    dd   = metrics.get("max_drawdown_units") or 0.0
    n    = metrics.get("n_bets") or 0
    if goal == "hit_rate":
        return hr
    if goal == "roi":
        return roi
    if goal == "calibration":
        return -abs(cal)  # closer to zero = better-calibrated
    if goal == "stability":
        return cons - 0.01 * dd
    # balanced (default) — penalize tiny samples + drawdown + inconsistency
    hr_score    = 2.0 * max(0.0, hr - 0.55)        # only reward >55%
    roi_score   = 5.0 * roi
    cal_score   = -2.0 * abs(cal)
    cons_score  = 1.5 * cons
    dd_penalty  = -0.02 * dd
    sample_penalty = -1.0 * max(0.0, math.log10(max(baseline_n, 1) / max(n, 1)))
    return hr_score + roi_score + cal_score + cons_score + dd_penalty + sample_penalty


# ── Cell-level worker ──────────────────────────────────────────────
async def _evaluate_cell(state: Dict[str, Any], db, *,
                              tier: str, stat_family: str, odds_bucket: str,
                              sides: List[str], body: OptimizerRunBody,
                              combos: List[Dict[str, float]],
                              required: RequiredFilters,
                              ) -> List[Dict[str, Any]]:
    # Pull the candidate row pool ONCE per cell.
    # `enforce_tier_gates=True` restricts to rows where this tier's
    # gates actually passed in the prod runner; `False` (default)
    # treats tier as a label and queries the full pool. The latter is
    # required because production gates rarely pass on historical
    # data, which would leave the optimizer with nothing to score.
    q: Dict[str, Any] = {
        "league_id": body.sport,
        "game_date": {"$gte": body.start, "$lte": body.end},
        "stat_family": stat_family,
        "odds_bucket": odds_bucket,
        f"{tier}_pass": (True if getattr(body, "enforce_tier_gates", False)
                            else {"$exists": True}),
    }
    if sides:
        q["side"] = {"$in": sides}
    rows: List[Dict[str, Any]] = []
    async for r in db[REPLAY_COLL].find(q, projection={"_id": 0}):
        if _row_passes_filters(r, required):
            rows.append(r)
    if not rows:
        state["cells_skipped_empty"] += 1
        return []
    cell_results: List[Dict[str, Any]] = []
    overfit_threshold = max(body.min_bets, 50)
    for combo in combos:
        if state.get("cancelled"):
            break
        metrics = _evaluate_combo(rows, combo, body.min_bets)
        state["combos_tested"] += 1
        if metrics is None:
            state["combos_skipped_low_sample"] += 1
            continue
        score = _score(metrics, body.optimization_goal,
                          baseline_n=overfit_threshold)
        # Ungradable cell — emit with score=None so it survives in
        # the results table (operator can still see the threshold +
        # sample size) but is sorted to the bottom and never beats a
        # real graded cell for "best-by" rankings.
        ungradable = score is None
        if not ungradable and (state["best"] is None
                                       or score > state["best"].get("score", -1e9)):
            state["best"] = {
                "score": score, "tier": tier, "stat_family": stat_family,
                "odds_bucket": odds_bucket, **metrics, **combo,
            }
        cell_results.append({
            "tier": tier, "stat_family": stat_family, "odds_bucket": odds_bucket,
            "sides": sides, "thresholds": combo,
            **metrics, "score": score, "score_goal": body.optimization_goal,
            "overfit_flag": metrics["n_bets"] < overfit_threshold,
            "ungradable": ungradable,
        })
    # Keep only top-K per cell to bound memory. Sort puts ungradable
    # (score=None) cells at the bottom regardless of their n_bets.
    cell_results.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return cell_results[:200]


async def _run_optimizer(run_id: str, body: OptimizerRunBody) -> None:
    db = _get_db()
    state = _RUNS[run_id]
    try:
        state["status"] = "running"
        # Discover families + buckets if user said "all"
        stat_families = body.stat_families
        if not stat_families:
            stat_families = await db[REPLAY_COLL].distinct("stat_family", {
                "league_id": body.sport,
                "game_date": {"$gte": body.start, "$lte": body.end},
            })
            stat_families = [s for s in stat_families if s]
        odds_buckets = body.odds_buckets or DEFAULT_ODDS_BUCKETS
        cells: List[Tuple[str, str, str]] = [
            (t, sf, ob)
            for t in body.tiers
            for sf in stat_families
            for ob in odds_buckets
        ]
        combos = _enumerate_combos(_resolve_grid(body.grid),
                                          body.max_configs_per_cell)
        total = len(cells) * len(combos)
        state.update({
            "cells_total": len(cells),
            "combos_per_cell": len(combos),
            "total_combos": total,
            "stat_families": stat_families,
            "odds_buckets": odds_buckets,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        # Persist initial state
        await db[OPTIMIZER_RUNS].update_one(
            {"run_id": run_id}, {"$set": {**state, "request": body.model_dump()}},
            upsert=True,
        )

        sem = asyncio.Semaphore(body.worker_limit)
        # Streaming buffer: cell_results land in Mongo via bulk_write
        # every `flush_at` rows or every 5 s. We NEVER hold the full
        # result set in uvicorn memory (was OOM-killing the pod).
        write_buf: List[Dict[str, Any]] = []
        flush_at = 500
        persist_lock = asyncio.Lock()
        last_persist = time.monotonic()
        # Lazy index creation, idempotent.
        try:
            await db[OPTIMIZER_RESULTS].create_index(
                [("run_id", 1), ("score", -1)],
                name="run_id_score_desc", background=True)
            await db[OPTIMIZER_RESULTS].create_index(
                [("run_id", 1), ("tier", 1), ("stat_family", 1),
                  ("odds_bucket", 1)],
                name="run_id_cell", background=True)
        except Exception:  # noqa: BLE001  — index may already exist
            pass

        async def _flush(force: bool = False) -> None:
            nonlocal last_persist, write_buf
            now = time.monotonic()
            if not write_buf:
                if force or now - last_persist > 5.0:
                    last_persist = now
                    await db[OPTIMIZER_RUNS].update_one(
                        {"run_id": run_id},
                        {"$set": {
                            "combos_tested": state["combos_tested"],
                            "best": state["best"],
                            "cells_skipped_empty": state["cells_skipped_empty"],
                            "combos_skipped_low_sample":
                                state["combos_skipped_low_sample"],
                            "cells_done": state["cells_done"],
                            "n_results_persisted": state.get("n_results_persisted", 0),
                        }},
                    )
                return
            to_write = write_buf
            write_buf = []
            await db[OPTIMIZER_RESULTS].insert_many(
                to_write, ordered=False)
            state["n_results_persisted"] = (
                state.get("n_results_persisted", 0) + len(to_write))
            if force or now - last_persist > 5.0:
                last_persist = now
                await db[OPTIMIZER_RUNS].update_one(
                    {"run_id": run_id},
                    {"$set": {
                        "combos_tested": state["combos_tested"],
                        "best": state["best"],
                        "cells_skipped_empty": state["cells_skipped_empty"],
                        "combos_skipped_low_sample":
                            state["combos_skipped_low_sample"],
                        "cells_done": state["cells_done"],
                        "n_results_persisted":
                            state.get("n_results_persisted", 0),
                    }},
                )

        async def _worker(cell: Tuple[str, str, str]):
            async with sem:
                # Re-check cancel flag — both the local state and the
                # Mongo-persisted flag (set by the API cancel endpoint).
                if state.get("cancelled"):
                    return
                t, sf, ob = cell
                # Cheap, every cell. The flag lives on optimizer_runs.
                cancel_doc = await db[OPTIMIZER_RUNS].find_one(
                    {"run_id": run_id}, {"cancelled": 1, "_id": 0})
                if cancel_doc and cancel_doc.get("cancelled"):
                    state["cancelled"] = True
                    return
                try:
                    cell_results = await _evaluate_cell(
                        state, db, tier=t, stat_family=sf, odds_bucket=ob,
                        sides=body.sides, body=body, combos=combos,
                        required=body.filters,
                    )
                    # Tag rows with run_id so the readers can $match.
                    for r in cell_results:
                        r["run_id"] = run_id
                    async with persist_lock:
                        write_buf.extend(cell_results)
                        if len(write_buf) >= flush_at:
                            await _flush()
                        else:
                            # Periodic state-only flush (no row writes).
                            await _flush(force=False)
                    state["cells_done"] += 1
                except Exception as e:
                    logger.exception("[optimizer] cell failed")
                    if len(state["failures"]) < MAX_INLINE_FAILURES:
                        state["failures"].append({
                            "cell": cell, "error": repr(e)[:200],
                        })

        await asyncio.gather(*[_worker(c) for c in cells])
        async with persist_lock:
            await _flush(force=True)

        # Final ranking is delegated to the readers (server-side $sort),
        # so we DO NOT pull every row back into memory here.
        state["status"] = "succeeded" if not state.get("cancelled") else "cancelled"
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        await db[OPTIMIZER_RUNS].update_one(
            {"run_id": run_id}, {"$set": {
                "status": state["status"],
                "finished_at": state["finished_at"],
                "combos_tested": state["combos_tested"],
                "best": state["best"],
                "cells_done": state["cells_done"],
                "failures": state["failures"][:MAX_INLINE_FAILURES],
                "n_results": state.get("n_results_persisted", 0),
                "n_results_persisted": state.get("n_results_persisted", 0),
            }},
        )
    except Exception as e:
        logger.exception("[optimizer] run crashed")
        state["status"] = "failed"
        state["error"] = repr(e)
        await db[OPTIMIZER_RUNS].update_one(
            {"run_id": run_id},
            {"$set": {"status": "failed", "error": repr(e),
                       "finished_at": datetime.now(timezone.utc).isoformat()}},
        )


# ── Endpoints ──────────────────────────────────────────────────────
class PreflightBody(BaseModel):
    sport: str = Field(default="MLB")
    start: str
    end:   str
    enforce_tier_gates: bool = False


@router.post("/preflight")
async def preflight(body: PreflightBody, request: Request,
                          auth=Depends(require_admin_token)):
    """Counts rows the optimizer will actually scan for this window,
    broken down by tier × stat_family × odds_bucket × outcome state.

    Designed to make the "succeeded but no results" failure mode
    impossible: if a cell has 0 graded rows, this surfaces it BEFORE
    you queue a run. Recommend calling this from the Optimizer UI
    every time the window / enforce_tier_gates toggle changes.
    """
    db = _get_db()
    league = body.sport.upper()
    base_match: Dict[str, Any] = {
        "league_id": league,
        "game_date": {"$gte": body.start, "$lte": body.end},
    }
    n_total = await db[REPLAY_COLL].count_documents(base_match)
    if n_total == 0:
        return {"ok": True, "sport": league, "start": body.start, "end": body.end,
                  "enforce_tier_gates": body.enforce_tier_gates,
                  "n_total_in_window": 0,
                  "by_tier": [], "by_stat_family": [], "by_odds_bucket": [],
                  "diagnosis": (
                      f"No rows in {REPLAY_COLL} for this window. "
                      "Run the SSOT historical replay first."
                  )}
    n_graded = await db[REPLAY_COLL].count_documents(
        {**base_match, "outcome_numeric": {"$in": [0, 1, 0.5]}})
    # Per-tier preview — runs the same filter shape the optimizer uses.
    by_tier: List[Dict[str, Any]] = []
    for tier in DEFAULT_TIERS:
        tier_match = {**base_match,
                          f"{tier}_pass": (True if body.enforce_tier_gates
                                              else {"$exists": True})}
        n = await db[REPLAY_COLL].count_documents(tier_match)
        n_graded_tier = await db[REPLAY_COLL].count_documents(
            {**tier_match, "outcome_numeric": {"$in": [0, 1, 0.5]}})
        by_tier.append({"tier": tier, "n_rows": n,
                            "n_graded": n_graded_tier,
                            "pct_graded": round(n_graded_tier * 100.0
                                                    / max(n, 1), 1)})
    # Per-stat_family + per-odds_bucket
    def _pipeline_for(field: str) -> List[Dict[str, Any]]:
        return [
            {"$match": base_match},
            {"$group": {
                "_id": f"${field}",
                "n_rows": {"$sum": 1},
                "n_graded": {"$sum": {"$cond": [
                    {"$in": ["$outcome_numeric", [0, 1, 0.5]]}, 1, 0]}},
            }},
            {"$sort": {"n_rows": -1}},
            {"$project": {"_id": 0, field: "$_id",
                              "n_rows": 1, "n_graded": 1}},
        ]
    by_family = [d async for d in db[REPLAY_COLL].aggregate(
                       _pipeline_for("stat_family"), allowDiskUse=True)]
    by_bucket = [d async for d in db[REPLAY_COLL].aggregate(
                       _pipeline_for("odds_bucket"), allowDiskUse=True)]
    # Diagnosis
    pct_graded = n_graded * 100.0 / max(n_total, 1)
    if pct_graded < 1.0:
        diagnosis = (
            f"⚠ Only {pct_graded:.2f}% of rows are graded "
            f"({n_graded}/{n_total}). Optimizer will produce empty / "
            f"all-ungradable cells. Run /replay-outcome-coverage and "
            f"/replay-outcome-join-diagnose to find the join failure."
        )
    elif body.enforce_tier_gates and any(t["n_graded"] < 30 for t in by_tier):
        thin = [t["tier"] for t in by_tier if t["n_graded"] < 30]
        diagnosis = (
            f"⚠ enforce_tier_gates=true gives <30 graded rows for "
            f"{thin}. Set enforce_tier_gates=false (default) to use "
            f"the full pool, OR widen the date window."
        )
    else:
        diagnosis = (
            f"Healthy: {n_graded:,}/{n_total:,} ({pct_graded:.1f}%) "
            f"rows graded across {len(by_family)} families. "
            f"Optimizer should produce real results."
        )
    await audit_log(request, action="optimizer_preflight",
                      params={"sport": league, "start": body.start,
                                "end": body.end,
                                "enforce_tier_gates": body.enforce_tier_gates},
                      response_summary={"n_total": n_total,
                                              "n_graded": n_graded,
                                              "pct_graded": round(pct_graded, 2)},
                      **auth)
    return {
        "ok": True, "sport": league, "start": body.start, "end": body.end,
        "enforce_tier_gates": body.enforce_tier_gates,
        "n_total_in_window": n_total,
        "n_graded": n_graded,
        "pct_graded": round(pct_graded, 2),
        "by_tier":         by_tier,
        "by_stat_family":  by_family,
        "by_odds_bucket":  by_bucket,
        "diagnosis":       diagnosis,
    }


@router.post("/run")
async def run_optimizer(body: OptimizerRunBody, request: Request,
                            auth=Depends(require_admin_token)):
    db = _get_db()
    # Cheap sanity check — ensure replay cache has rows in window
    n = await db[REPLAY_COLL].count_documents({
        "league_id": body.sport.upper(),
        "game_date": {"$gte": body.start, "$lte": body.end},
    })
    if n == 0:
        raise HTTPException(409,
            f"No rows in {REPLAY_COLL} for {body.sport} "
            f"{body.start}..{body.end}. Run the SSOT historical "
            "replay pipeline first.")
    run_id = f"opt_{uuid.uuid4().hex[:10]}"
    state: Dict[str, Any] = {
        "run_id": run_id, "status": "queued",
        "combos_tested": 0, "combos_skipped_low_sample": 0,
        "cells_skipped_empty": 0, "cells_done": 0,
        "best": None, "failures": [], "cancelled": False,
        "n_results_persisted": 0,
        "agent_id": auth["agent_id"],
    }
    # IMPORTANT: do NOT populate `_RUNS[run_id]` from the API process.
    # The research_worker process is the sole owner of the in-flight
    # state. Keeping uvicorn out of `_RUNS` is critical to avoid the
    # OOM-kill cycle.
    # Persist the full request + initial state to `optimizer_runs` so the
    # out-of-process worker can re-hydrate and run it.
    await db[OPTIMIZER_RUNS].update_one(
        {"run_id": run_id},
        {"$set": {**state, "request": body.model_dump(),
                    "queued_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    # Hand off to the dedicated research_worker daemon. The worker runs
    # the same `_run_optimizer` logic via scripts.research.run_optimizer_cli
    # in a subprocess with nice +10 / RLIMIT_AS / OOM bias / 2h timeout.
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    await worker_enqueue(
        job_id,
        module="scripts.research.run_optimizer_cli",
        args=["--run-id", run_id],
        agent_id=auth["agent_id"],
        token_hash=auth["token_hash"],
        kind="optimizer",
        payload={"run_id": run_id, "sport": body.sport,
                    "start": body.start, "end": body.end},
    )
    await audit_log(request, action="optimizer_run",
                      params={"run_id": run_id, "sport": body.sport,
                                  "start": body.start, "end": body.end,
                                  "tiers": body.tiers, "goal": body.optimization_goal,
                                  "job_id": job_id, "queued": True},
                      response_summary={"replay_rows_in_window": n,
                                            "job_id": job_id},
                      **auth)
    return {"ok": True, "run_id": run_id, "job_id": job_id,
              "queued": True, "replay_rows_in_window": n}


@router.get("/{run_id}")
async def get_status(run_id: str, request: Request,
                          auth=Depends(require_admin_token)):
    state = _RUNS.get(run_id)
    if state is None:
        # Try rehydrate from Mongo
        db = _get_db()
        doc = await db[OPTIMIZER_RUNS].find_one({"run_id": run_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"run_id not found: {run_id}")
        return {"ok": True, "state": doc, "rehydrated": True}
    started = state.get("started_at")
    elapsed = None
    eta = None
    if started:
        try:
            started_ts = datetime.fromisoformat(started).timestamp()
            elapsed = time.time() - started_ts
            tested = max(state.get("combos_tested", 0), 1)
            total  = max(state.get("total_combos", 1), 1)
            if state["status"] == "running":
                eta = max(0.0, elapsed * (total - tested) / tested)
        except Exception:
            pass
    return {"ok": True, "state": {
        "run_id": state["run_id"], "status": state["status"],
        "combos_tested": state["combos_tested"],
        "total_combos": state.get("total_combos"),
        "combos_skipped_low_sample": state["combos_skipped_low_sample"],
        "cells_total": state.get("cells_total"),
        "cells_done": state["cells_done"],
        "cells_skipped_empty": state["cells_skipped_empty"],
        "best": state["best"],
        "failures": state["failures"][-10:],
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "elapsed_s": elapsed, "eta_s": eta,
        "error": state.get("error"),
    }}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request,
                          auth=Depends(require_admin_token)):
    """Set the cancellation flag on the run doc. The worker process
    polls it between cells and exits cleanly. We do NOT poke the in-
    process `_RUNS` slot in uvicorn — that's empty by design (the run
    lives in the worker)."""
    db = _get_db()
    res = await db[OPTIMIZER_RUNS].update_one(
        {"run_id": run_id},
        {"$set": {"cancelled": True,
                    "cancelled_at": datetime.now(timezone.utc)}})
    if res.matched_count == 0:
        raise HTTPException(404, f"run_id not found: {run_id}")
    # Keep the legacy in-process slot in sync when this happens to be
    # the worker process (no-op in the API process).
    state = _RUNS.get(run_id)
    if state is not None:
        state["cancelled"] = True
    await audit_log(request, action="optimizer_cancel",
                      params={"run_id": run_id}, **auth)
    return {"ok": True}


@router.get("/{run_id}/results")
async def get_results(run_id: str, request: Request,
                          limit: int = 25,
                          offset: int = 0,
                          include_ungradable: bool = False,
                          auth=Depends(require_admin_token)):
    """Returns paginated, server-sorted optimizer results.

    Ungradable cells (those with 0 graded rows → score=None) are
    excluded by default and surfaced separately in `ungradable_count`
    + `ungradable_top` (sorted by sample size, so the operator can
    see WHICH high-volume slices are missing grading).
    Pass `include_ungradable=true` to mix them back into `top`.
    """
    db = _get_db()
    run = await db[OPTIMIZER_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if run is None:
        raise HTTPException(404, f"run_id not found: {run_id}")
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be 1..500")
    if offset < 0:
        raise HTTPException(400, "offset must be ≥ 0")

    n_total = await db[OPTIMIZER_RESULTS].count_documents({"run_id": run_id})
    if n_total == 0:
        if run.get("status") in ("queued", "running"):
            return {"ok": True, "run_id": run_id, "n_results": 0,
                      "status": run.get("status"),
                      "top": [], "worst": [],
                      "best_by_tier": {}, "best_by_stat_family": {},
                      "best_by_odds_bucket": {}, "overfit_warnings": [],
                      "ungradable_count": 0, "ungradable_top": [],
                      "note": "no rows persisted yet"}
        raise HTTPException(404,
            f"results not available yet for {run_id}")

    graded_filter: Dict[str, Any] = {"run_id": run_id}
    if not include_ungradable:
        graded_filter["score"] = {"$ne": None}
    n_gradable = await db[OPTIMIZER_RESULTS].count_documents(graded_filter)
    n_ungradable = n_total - n_gradable if not include_ungradable else 0

    top_cur = db[OPTIMIZER_RESULTS].find(
        graded_filter, {"_id": 0}
    ).sort([("score", -1)]).skip(offset).limit(limit)
    top = [d async for d in top_cur]

    worst_cur = db[OPTIMIZER_RESULTS].find(
        graded_filter, {"_id": 0}
    ).sort([("score", 1)]).limit(limit)
    worst = [d async for d in worst_cur]

    ungradable_top: List[Dict[str, Any]] = []
    if not include_ungradable and n_ungradable > 0:
        cur = db[OPTIMIZER_RESULTS].find(
            {"run_id": run_id, "score": None}, {"_id": 0}
        ).sort([("n_bets", -1)]).limit(10)
        ungradable_top = [d async for d in cur]

    async def _best_by(field: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        pipeline = [
            # Exclude ungradable cells from "best by …" — a score=None
            # cell with thousands of rows must never win a group.
            {"$match": {"run_id": run_id, field: {"$ne": None},
                            "score": {"$ne": None}}},
            {"$sort": {"score": -1}},
            {"$group": {"_id": f"${field}", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$project": {"_id": 0}},
            {"$limit": 200},
        ]
        async for d in db[OPTIMIZER_RESULTS].aggregate(
                pipeline, allowDiskUse=True):
            k = d.get(field)
            if k is not None and k not in out:
                out[k] = d
        return out

    best_by_tier        = await _best_by("tier")
    best_by_stat_family = await _best_by("stat_family")
    best_by_odds_bucket = await _best_by("odds_bucket")
    overfit_warnings    = [r for r in top if r.get("overfit_flag")]

    return {
        "ok": True, "run_id": run_id,
        "n_results": n_total,
        "n_gradable": n_gradable,
        "ungradable_count": n_ungradable,
        "ungradable_top": ungradable_top,
        "offset": offset, "limit": limit,
        "status": run.get("status"),
        "top": top, "worst": worst,
        "best_by_tier": best_by_tier,
        "best_by_stat_family": best_by_stat_family,
        "best_by_odds_bucket": best_by_odds_bucket,
        "overfit_warnings": overfit_warnings,
    }


class SaveBody(BaseModel):
    top_k: int = Field(default=10, ge=1, le=200)
    note: str = ""


@router.post("/{run_id}/save_as_candidates")
async def save_as_candidates(run_id: str, body: SaveBody, request: Request,
                                  auth=Depends(require_admin_token)):
    db = _get_db()
    run = await db[OPTIMIZER_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if run is None:
        raise HTTPException(404, f"run_id not found: {run_id}")
    # Server-side sort to pull only top_k — no full materialization.
    cur = db[OPTIMIZER_RESULTS].find(
        {"run_id": run_id}, {"_id": 0}
    ).sort([("score", -1)]).limit(int(body.top_k))
    top = [d async for d in cur]
    if not top:
        raise HTTPException(404, "no results to save")
    now = datetime.now(timezone.utc)
    docs: List[Dict[str, Any]] = []
    for i, r in enumerate(top):
        docs.append({
            "candidate_id": f"{run_id}_{i:03d}",
            "source_run_id": run_id,
            "rank": i + 1,
            "sport": "MLB",
            "tier": r.get("tier"),
            "stat_family": r.get("stat_family"),
            "odds_bucket": r.get("odds_bucket"),
            "side": r.get("sides"),
            "thresholds": {k: r.get(k) for k in
                                ("hr_l20_min", "hr_l10_min", "hr_l5_min",
                                  "cv_max", "edge_min", "tp_min")},
            "metrics": {k: r.get(k) for k in
                              ("n_bets", "wins", "losses", "pushes",
                                "hit_rate", "roi", "calibration_delta",
                                "avg_tp", "avg_cv", "avg_edge",
                                "daily_consistency", "max_drawdown_units",
                                "profit_units", "n_days")},
            "score": r.get("score"),
            "score_goal": r.get("score_goal"),
            "overfit_flag": r.get("overfit_flag"),
            "status": "candidate",
            "created_at": now,
            "note": body.note,
        })
    if docs:
        ops = []
        from pymongo import UpdateOne
        for d in docs:
            ops.append(UpdateOne({"candidate_id": d["candidate_id"]},
                                       {"$set": d}, upsert=True))
        await db[CANDIDATE_THRESH].bulk_write(ops, ordered=False)
    await audit_log(request, action="optimizer_save_candidates",
                      params={"run_id": run_id, "top_k": body.top_k},
                      response_summary={"saved": len(docs)}, **auth)
    return {"ok": True, "saved": len(docs),
              "candidate_ids": [d["candidate_id"] for d in docs]}


@router.post("/{run_id}/set_testing_default")
async def set_testing_default(run_id: str, request: Request,
                                    auth=Depends(require_admin_token)):
    state = _RUNS.get(run_id)
    if state is None or not state.get("best"):
        raise HTTPException(404, "no best config available")
    db = _get_db()
    now = datetime.now(timezone.utc)
    doc = {
        "kind": "testing_default_thresholds",
        "source_run_id": run_id,
        "best": state["best"],
        "set_at": now,
        "set_by": auth["agent_id"],
    }
    await db[TESTING_DEFAULTS].update_one(
        {"kind": "testing_default_thresholds"},
        {"$set": doc}, upsert=True,
    )
    await audit_log(request, action="optimizer_set_testing_default",
                      params={"run_id": run_id},
                      response_summary={"best": state["best"]}, **auth)
    return {"ok": True, "doc": doc}


@router.get("/_meta/testing_default")
async def get_testing_default(request: Request,
                                    auth=Depends(require_admin_token)):
    db = _get_db()
    doc = await db[TESTING_DEFAULTS].find_one(
        {"kind": "testing_default_thresholds"}, projection={"_id": 0})
    return {"ok": True, "doc": doc}
