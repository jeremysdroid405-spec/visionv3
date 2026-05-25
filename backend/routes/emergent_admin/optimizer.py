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

# Canonical tier-by-odds boundaries used by the live runner
# (`services/scoring/gates/thresholds.py::resolve_target_tier`). The
# optimizer routes rows into tiers the SAME WAY production does — by
# `reference_odds` range — instead of filtering on a `{tier}_pass`
# boolean. The boolean reflects whether a row passed prod's full gate
# stack (including `coverage_gate`, which requires live `book_count`
# data that historical replay does not carry → would be False for
# 100% of historical rows and starve the optimizer of samples).
try:
    from services.scoring.gates.thresholds import (
        UNIVERSAL_SAFE_HAVEN_MAX,
        UNIVERSAL_WAR_ZONE_MIN,
    )
except Exception:  # noqa: BLE001 — keep optimizer importable in tests
    UNIVERSAL_SAFE_HAVEN_MAX = -300
    UNIVERSAL_WAR_ZONE_MIN   = 150


def _tier_odds_filter(tier: str) -> Dict[str, Any]:
    """Mongo filter clause that mirrors `resolve_target_tier` exactly.

    Returns the odds-range predicate for the given tier. Rows with
    `odds == null` are intentionally excluded — they can't be routed
    to any tier and they can't produce a graded payout.
    """
    if tier == "safe_haven":
        return {"odds": {"$ne": None, "$lte": UNIVERSAL_SAFE_HAVEN_MAX}}
    if tier == "war_zone":
        return {"odds": {"$ne": None, "$gte": UNIVERSAL_WAR_ZONE_MIN}}
    if tier == "front_lines":
        return {"odds": {"$ne": None,
                              "$gt":  UNIVERSAL_SAFE_HAVEN_MAX,
                              "$lt":  UNIVERSAL_WAR_ZONE_MIN}}
    # Unknown tier — return a never-matches clause rather than {}.
    return {"_unknown_tier_": tier}


# 2026-05-24 — Multi-book universe filter helper.
# Translates `OptimizerRunBody.book_filter` into the Mongo predicate
# applied alongside the tier-odds filter in `_evaluate_cell` and
# `/preflight`. Returns {} for "any" (no filter).
_BOOK_FILTER_MAP: Dict[str, Dict[str, Any]] = {
    "any":            {},
    "pp_only":        {"playable_on_pp":      True},
    "dk_only":        {"playable_on_dk":      True},
    "fd_only":        {"playable_on_fd":      True},
    "mgm_only":       {"playable_on_mgm":     True},
    "caesars_only":   {"playable_on_caesars": True},
    "bol_only":       {"playable_on_bol":     True},
}


def _book_filter_clause(book_filter: str) -> Dict[str, Any]:
    """Returns the Mongo clause for the requested book filter.
    `multi_book` requires `available_books` length ≥ 2 (consensus
    strength) — implemented via a `$expr` because Mongo doesn't have
    a clean shorthand for "array length ≥ N"."""
    if book_filter == "multi_book":
        return {"$expr": {"$gte": [{"$size": {"$ifNull":
                                                            ["$available_books", []]}}, 2]}}
    return dict(_BOOK_FILTER_MAP.get(book_filter, {}))


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
# Each axis includes a `None` sentinel (encoded here as `-inf` for `min_`
# axes and `+inf` for `max_` axes) meaning "don't filter on this axis".
# This lets a single grid sweep ask both
#   (a) "what's the best combo with this constraint" AND
#   (b) "what happens if I relax this constraint entirely"
# without the operator having to manually run separate sweeps.
DEFAULT_GRID: Dict[str, List[float]] = {
    # 2026-05-24 — Sized for the operator's "up to 1M combos" budget
    # while keeping wildcards on every axis. 4×3×3×4×5×6 = 4,320
    # combos/cell · across 14 fam × 5 buckets × 3 tiers = 210 cells
    # → 907,200 combos per 3-tier run. Comfortably under 1M and runs
    # in ~5-7 min on the prod worker.
    "hr_l20_min": [float("-inf"), 0.55, 0.65, 0.75],
    "hr_l10_min": [float("-inf"), 0.55, 0.65],
    "hr_l5_min":  [float("-inf"), 0.55, 0.65],
    "cv_max":     [float("+inf"), 0.70, 0.90, 1.10],
    "edge_min":   [float("-inf"), 0.02, 0.05, 0.08, 0.10],
    "tp_min":     [float("-inf"), 0.50, 0.55, 0.60, 0.65, 0.70],
}

DEFAULT_TIERS         = ["safe_haven", "front_lines", "war_zone"]
# Must MATCH `scripts/sgo/historical_full_pipeline_replay._odds_bucket`
# verbatim — those are the literal strings the SSOT pipeline writes
# into the replay cache, and the optimizer matches on equality.
DEFAULT_ODDS_BUCKETS  = [
    "odds_lt_-200", "odds_-200_-100", "odds_-100_-0",
    "odds_+0_+150", "odds_+150_+300", "odds_+300p",
    # `odds_na` exists in the data (46 rows in the prod MLB window)
    # but represents missing odds and can never produce a graded
    # payout, so we deliberately don't sweep it.
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
    # User strategy: surface thin-but-consistent combos for cross-month
    # validation. A combo with 5-10 bets in one month that recurs month
    # after month at high `daily_consistency` is the actual edge — we
    # explicitly do NOT want to filter it out. Set to 3 (lowest sensible
    # floor; 1-2 bets is pure coin-flip and produces meaningless metrics).
    min_bets:              int   = Field(default=3, ge=1)
    max_configs_per_cell:  int   = Field(default=500, ge=1, le=50_000)
    optimization_goal:     str   = Field(default="balanced")
    grid:                  GridSpec = Field(default_factory=GridSpec)
    filters:               RequiredFilters = Field(default_factory=RequiredFilters)
    worker_limit:          int   = Field(default=4, ge=1, le=16)
    # Tier routing is by ODDS RANGE (matches the live runner's
    # `resolve_target_tier` — safe_haven ≤ -300, front_lines -299..149,
    # war_zone ≥ +150). The optimizer's tier dimension is always
    # odds-routed.  This flag is OPT-IN and adds the prod gate-pass
    # boolean `{tier}_pass=True` ON TOP of the odds-range filter —
    # useful for "what would prod actually have bet" parity checks,
    # but produces empty cells on most historical windows because
    # historical replay has no live `book_count` so `coverage_gate`
    # always fails. Default OFF per user instruction
    # ("not reject, just create different buckets").
    enforce_tier_gates:    bool  = Field(default=False)
    # 2026-05-24 — Multi-book universe filter. The reshape pipeline
    # now flows EVERY book's rows into `sgo_propvision_full_pipeline_replay`
    # (anchor priority: PP → DK → FD → MGM → Caesars → BOL). This
    # filter lets the operator slice the optimizer to a single book
    # (or "any" / "best_available"). Default "any" → multi-book.
    # Possible values:
    #   "any"            — all rows (default; "Best Available" mode)
    #   "pp_only"        — playable_on_pp = True
    #   "dk_only"        — playable_on_dk = True
    #   "fd_only"        — playable_on_fd = True
    #   "mgm_only"       — playable_on_mgm = True
    #   "caesars_only"   — playable_on_caesars = True
    #   "bol_only"       — playable_on_bol = True
    #   "multi_book"     — len(available_books) >= 2 (consensus
    #                      strength filter — props at least 2 books
    #                      are willing to take)
    book_filter:           str   = Field(default="any")


# ── Combo generation ──────────────────────────────────────────────
def _resolve_grid(spec: Optional[GridSpec]) -> Dict[str, List[float]]:
    """2026-05-24 — ALWAYS returns the full brute-force `DEFAULT_GRID`.

    The user-supplied `spec` is intentionally IGNORED at search time per
    user directive:

      > "the grid should create the absolute 5 best combos for every
      >  tier from brute force. the settings on the grid should only
      >  be used to filter that AFTER the absolute best are displayed"

    The form values are stored on the run state as
    `state.display_filter_grid` and applied client-side when the UI
    renders Top-N tables — they NEVER narrow the search itself.
    """
    return {axis: list(default) for axis, default in DEFAULT_GRID.items()}


def _user_grid_to_display_filter(spec: Optional[GridSpec]
                                            ) -> Dict[str, List[float]]:
    """Capture the operator's submitted grid for client-side post-
    filtering. Returns {} when nothing was customized (i.e. the
    frontend can render the unfiltered brute-force ranking)."""
    if spec is None:
        return {}
    out: Dict[str, List[float]] = {}
    for axis in DEFAULT_GRID:
        v = getattr(spec, axis, None)
        if v:
            out[axis] = list(v)
    return out


def _enumerate_combos(grid: Dict[str, List[float]],
                          max_per_cell: int) -> List[Dict[str, float]]:
    """Enumerate threshold combinations deterministically.

    Always returns the FULL Cartesian product of the grid. No random
    sampling (the original source of "same window, different Top 1
    every run") and no stride decimation. `max_per_cell` is now a
    soft warning ceiling — combos beyond it are still tested; the
    parameter is retained for backwards-compat and reporting only.
    """
    axes = sorted(grid.keys())
    spaces = [grid[a] for a in axes]
    return [dict(zip(axes, vals)) for vals in itertools.product(*spaces)]


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
        # Wildcard sentinel: ±inf means "don't filter on this axis".
        # Skip the row-value check entirely so a missing row value
        # doesn't kill a wildcard combo.
        if is_max and thr == float("+inf"):
            continue
        if (not is_max) and thr == float("-inf"):
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
    # ── Daily consistency ────────────────────────────────────────────
    # Previous formula: `1 - stddev / |mean|`. Blew up to massive
    # negative values whenever daily PnL averaged near zero (profitable
    # days cancelling losing days), because the denominator collapsed.
    # That single metric then dominated `_score()` and produced
    # rankings where a 53.3% HR / -0.8% ROI config "scored" -150.
    # New definition: proportion of days with positive net PnL. Range
    # is naturally [0, 1] (1 = every day profitable). Returned as
    # `None` when there are < 2 days so we don't pretend to a stat we
    # don't have.
    if len(daily_vals) >= 2:
        n_profitable = sum(1 for v in daily_vals if v > 0)
        daily_consistency = n_profitable / len(daily_vals)
    else:
        daily_consistency = None
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
    cons = metrics.get("daily_consistency")
    # Defensive: legacy cells with the old unbounded formula could
    # store nonsensical values like -99.72. Clamp to [0, 1] which is
    # the well-defined range of the new "proportion profitable days"
    # metric. None → 0.0 (no signal).
    if cons is None or not isinstance(cons, (int, float)):
        cons = 0.0
    else:
        cons = max(0.0, min(1.0, float(cons)))
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
    # Sample-size penalty — DELIBERATELY MILD. The user's strategy is
    # to find thin-but-consistent combos that recur across multiple
    # monthly windows; the optimizer must NOT bury a 5-bet @ 80%-HR
    # combo under a 50-bet @ 52%-HR combo just because the latter has
    # more rows. We retain a gentle log-scale nudge so 5 bets isn't
    # treated as equivalent to 50 bets, but the weight is 0.25 (was
    # 1.0) and the baseline is `max(min_bets, 10)` (was hardcoded 50)
    # so the penalty zeroes out as soon as `n` clears `max(min_bets, 10)`.
    sample_penalty = -0.25 * max(0.0,
                                            math.log10(max(baseline_n, 1)
                                                          / max(n, 1)))
    return hr_score + roi_score + cal_score + cons_score + dd_penalty + sample_penalty


# ── Cell-level worker ──────────────────────────────────────────────
async def _evaluate_cell(state: Dict[str, Any], db, *,
                              tier: str, stat_family: str, odds_bucket: str,
                              sides: List[str], body: OptimizerRunBody,
                              combos: List[Dict[str, float]],
                              required: RequiredFilters,
                              ) -> List[Dict[str, Any]]:
    # ── Tier filter (ODDS-RANGE ROUTING, matches live runner) ──────
    # Tiers are pure odds-range buckets — same boundaries as
    # `resolve_target_tier`: safe_haven ≤ -300, war_zone ≥ +150,
    # front_lines in between. We do NOT filter on `{tier}_pass`
    # because that boolean requires live `book_count` data that
    # historical replay rows don't carry (every historical row fails
    # `coverage_gate`, making the boolean useless for backtesting).
    # `enforce_tier_gates=True` is an opt-in to ALSO require the
    # prod gate-pass on top — exposed for parity validation, never
    # the default.
    q: Dict[str, Any] = {
        "league_id": body.sport,
        "game_date": {"$gte": body.start, "$lte": body.end},
        "stat_family": stat_family,
        "odds_bucket": odds_bucket,
        **_tier_odds_filter(tier),
        **_book_filter_clause(getattr(body, "book_filter", "any")),
    }
    if getattr(body, "enforce_tier_gates", False):
        q[f"{tier}_pass"] = True
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
    # baseline = max(min_bets, 10). The `50` floor that used to live
    # here turned every thin-but-real combo into a "tiny sample"
    # penalty, even when the user explicitly wanted thin combos to
    # rank. `_score` now only applies the gentle log-scale penalty
    # when `n` is below the operator's own min_bets floor (or 10,
    # whichever is larger). Above that the penalty is zero.
    sample_baseline = max(body.min_bets, 10)
    overfit_threshold = max(body.min_bets, 25)
    for combo in combos:
        if state.get("cancelled"):
            break
        metrics = _evaluate_combo(rows, combo, body.min_bets)
        state["combos_tested"] += 1
        if metrics is None:
            state["combos_skipped_low_sample"] += 1
            continue
        score = _score(metrics, body.optimization_goal,
                          baseline_n=sample_baseline)
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
            # Sample fingerprint: rows whose threshold combos produce
            # the IDENTICAL filtered sample (same n_bets, wins,
            # losses, pushes, profit) are mathematically equivalent.
            # Stored so the results endpoint can collapse duplicates.
            "sample_sig": (metrics.get("n_bets"), metrics.get("wins"),
                              metrics.get("losses"), metrics.get("pushes"),
                              round(metrics.get("profit_units") or 0.0, 4)),
        })
    # Persist ALL evaluated combos for this cell — no per-cell cap.
    # The user explicitly wants brute-force, deterministic results,
    # not a sample. Tiebreak by threshold dict (str-sorted) so ties
    # at the same score always sort the same way across runs.
    def _sort_key(r: Dict[str, Any]) -> Tuple[Any, ...]:
        thr = r.get("thresholds") or {}
        thr_repr = tuple(sorted(thr.items()))
        return (r["score"] is None,         # ungradable last
                -(r["score"] or 0.0),       # score desc
                thr_repr)                   # deterministic tiebreak
    cell_results.sort(key=_sort_key)
    return cell_results


async def _run_optimizer(run_id: str, body: OptimizerRunBody) -> None:
    db = _get_db()
    state = _RUNS[run_id]
    try:
        state["status"] = "running"
        # Discover families + buckets from the actual data window so
        # we never silently drop a family or bucket that exists. The
        # operator can still narrow via `body.stat_families` /
        # `body.odds_buckets`; an unset value means "everything that
        # exists for this window".
        replay_window: Dict[str, Any] = {
            "league_id": body.sport,
            "game_date": {"$gte": body.start, "$lte": body.end},
        }
        stat_families = body.stat_families
        if not stat_families:
            stat_families = await db[REPLAY_COLL].distinct(
                "stat_family", replay_window)
            stat_families = sorted([s for s in stat_families if s])
        odds_buckets = body.odds_buckets
        if not odds_buckets:
            odds_buckets = await db[REPLAY_COLL].distinct(
                "odds_bucket", replay_window)
            # Always exclude `odds_na` from sweeps — those rows have
            # no graded payout (no odds → no ROI possible).
            odds_buckets = sorted(
                [b for b in odds_buckets if b and b != "odds_na"])
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


class GridDiagnoseBody(BaseModel):
    sport: str = Field(default="MLB")
    start: str
    end:   str
    grid: Optional[GridSpec] = None


class PreflightBody(BaseModel):
    sport: str = Field(default="MLB")
    start: str
    end:   str
    enforce_tier_gates: bool = False


@router.post("/grid-diagnose")
async def grid_diagnose(body: GridDiagnoseBody, request: Request,
                              auth=Depends(require_admin_token)):
    """Per-axis 'how many rows pass each threshold value' breakdown.

    Designed to make it obvious WHY a cell ends up with 0 graded rows
    after the grid is applied. Reports for each axis the data's
    actual percentiles, and for each grid value how many GRADED rows
    pass that threshold. Flags axes whose most-strict value cuts
    too aggressively.
    """
    db = _get_db()
    league = body.sport.upper()
    base_match: Dict[str, Any] = {
        "league_id": league,
        "game_date": {"$gte": body.start, "$lte": body.end},
        "outcome_numeric": {"$in": [0, 1, 0.5]},
    }
    n_graded = await db[REPLAY_COLL].count_documents(base_match)
    if n_graded == 0:
        return {"ok": True, "sport": league, "start": body.start,
                  "end": body.end, "n_graded": 0, "axes": {},
                  "issues": [], "diagnosis": "No graded rows in this window."}
    grid = _resolve_grid(body.grid) if body.grid else dict(DEFAULT_GRID)
    axes = [
        ("hit_rate_l20", "hr_l20_min", "min"),
        ("hit_rate_l10", "hr_l10_min", "min"),
        ("hit_rate_l5",  "hr_l5_min",  "min"),
        ("cv",           "cv_max",     "max"),
        ("edge",         "edge_min",   "min"),
        ("model_probability", "tp_min", "min"),
    ]
    out: Dict[str, Any] = {}
    issues: List[str] = []
    for row_field, axis_key, kind in axes:
        pipe = [
            {"$match": {**base_match, row_field: {"$ne": None}}},
            {"$group": {"_id": None, "values": {"$push": f"${row_field}"}}},
        ]
        agg = await db[REPLAY_COLL].aggregate(pipe).to_list(1)
        if not agg:
            out[axis_key] = {"row_field": row_field, "n_with_value": 0}
            issues.append(f"`{row_field}` is null on all {n_graded} graded rows — "
                            f"every combo using `{axis_key}` will pass 0 rows.")
            continue
        vals = sorted(agg[0]["values"])
        n = len(vals)
        if row_field.startswith("hit_rate") and vals and vals[-1] > 1.0:
            vals = [v / 100.0 for v in vals]

        def pct(p: float) -> float:
            i = max(0, min(n - 1, int(round((n - 1) * p))))
            return round(vals[i], 4)
        per_threshold = []
        for thr in grid.get(axis_key, []):
            if (kind == "min" and thr == float("-inf")) or \
               (kind == "max" and thr == float("+inf")):
                per_threshold.append({"threshold": "wildcard",
                                            "n_pass": n,
                                            "pct_pass": 100.0})
                continue
            if kind == "min":
                npass = sum(1 for v in vals if v >= thr)
            else:
                npass = sum(1 for v in vals if v <= thr)
            per_threshold.append({
                "threshold": thr, "n_pass": npass,
                "pct_pass": round(npass * 100.0 / max(n, 1), 1),
            })
        numeric = [p for p in per_threshold if p["threshold"] != "wildcard"]
        if numeric:
            strict = (max(numeric, key=lambda x: x["threshold"])
                          if kind == "min"
                          else min(numeric, key=lambda x: x["threshold"]))
            if strict["n_pass"] < 30:
                issues.append(
                    f"`{axis_key}` most-strict value {strict['threshold']} "
                    f"passes only {strict['n_pass']} rows "
                    f"({strict['pct_pass']}%). Widen grid for this axis.")
        out[axis_key] = {
            "row_field": row_field, "kind": kind, "n_with_value": n,
            "data_percentiles": {
                "p10": pct(0.10), "p25": pct(0.25), "p50": pct(0.50),
                "p75": pct(0.75), "p90": pct(0.90), "p99": pct(0.99),
            },
            "grid_values": grid.get(axis_key),
            "per_threshold": per_threshold,
        }
    await audit_log(request, action="optimizer_grid_diagnose",
                       params={"sport": league, "start": body.start,
                                 "end": body.end},
                       response_summary={"n_graded": n_graded,
                                               "issues": len(issues)},
                       **auth)
    return {
        "ok": True, "sport": league, "start": body.start, "end": body.end,
        "n_graded": n_graded, "axes": out, "issues": issues,
        "diagnosis": ("Grid looks healthy" if not issues
                          else f"⚠ {len(issues)} grid axis issues detected"),
    }


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
        **_book_filter_clause(getattr(body, "book_filter", "any")),
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
    # Per-tier preview — runs the SAME odds-range filter the optimizer
    # uses (`_tier_odds_filter`), plus the opt-in `{tier}_pass=True`
    # when `enforce_tier_gates` is set.
    by_tier: List[Dict[str, Any]] = []
    for tier in DEFAULT_TIERS:
        tier_match = {**base_match, **_tier_odds_filter(tier)}
        if body.enforce_tier_gates:
            tier_match[f"{tier}_pass"] = True
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
            f"{thin}. Historical replay rows lack live `book_count`, "
            f"so the prod `coverage_gate` rejects virtually everything. "
            f"Leave `enforce_tier_gates=false` (default) — tiers will "
            f"still be routed by odds range, just without the "
            f"live-only gate filter."
        )
    elif any(t["n_graded"] < 30 for t in by_tier):
        thin = [f"{t['tier']}({t['n_graded']})" for t in by_tier
                  if t["n_graded"] < 30]
        diagnosis = (
            f"⚠ Thin tier samples: {', '.join(thin)} < 30 graded "
            f"rows. Tiers are routed by odds (safe_haven ≤ -300, "
            f"front_lines -299..+149, war_zone ≥ +150). Widen the "
            f"window or use `--tiers` to focus on the populated tier."
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
        # 2026-05-24 — capture the operator's submitted grid as a
        # client-side post-display filter (the backend always brute-
        # forces on DEFAULT_GRID per user directive).
        "display_filter_grid": _user_grid_to_display_filter(body.grid),
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

    # ── Dedup pipeline ─────────────────────────────────────────────
    # Many threshold combos in the grid filter to the same row set
    # (e.g. nothing in the data discriminates between tp_min=0.50 and
    # tp_min=0.55), producing 6-12 IDENTICAL rows in Top-25. Collapse
    # by (tier, family, bucket, sample_sig) keeping the row with the
    # most permissive thresholds (= the lowest combined threshold
    # values) so the operator sees one canonical config per sample.
    def _dedup_pipeline(extra_match: Optional[Dict[str, Any]] = None,
                              sort_dir: int = -1) -> List[Dict[str, Any]]:
        m = {**graded_filter}
        if extra_match:
            m.update(extra_match)
        return [
            {"$match": m},
            {"$sort": {"score": sort_dir, "tier": 1, "stat_family": 1,
                          "odds_bucket": 1, "n_bets": -1}},
            {"$group": {
                "_id": {"tier": "$tier", "stat_family": "$stat_family",
                          "odds_bucket": "$odds_bucket",
                          "sample_sig": "$sample_sig"},
                "doc": {"$first": "$$ROOT"},
                "n_equivalent_combos": {"$sum": 1},
            }},
            {"$replaceRoot": {"newRoot": {"$mergeObjects": [
                "$doc", {"n_equivalent_combos": "$n_equivalent_combos"}]}}},
            {"$project": {"_id": 0}},
            {"$sort": {"score": sort_dir, "tier": 1, "stat_family": 1,
                          "odds_bucket": 1, "n_bets": -1}},
        ]

    top_pipeline = _dedup_pipeline(sort_dir=-1) + [
        {"$skip": offset}, {"$limit": limit}]
    top = [d async for d in db[OPTIMIZER_RESULTS].aggregate(
        top_pipeline, allowDiskUse=True)]

    worst_pipeline = _dedup_pipeline(sort_dir=1) + [{"$limit": limit}]
    worst = [d async for d in db[OPTIMIZER_RESULTS].aggregate(
        worst_pipeline, allowDiskUse=True)]

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
            # Deterministic sort: same tiebreak as the top/worst
            # queries, so "Best by …" can never disagree with the
            # corresponding row in the Top-25 table.
            {"$sort": {"score": -1, "tier": 1, "stat_family": 1,
                          "odds_bucket": 1, "n_bets": -1}},
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

    # ── Family coverage (ALL discovered families) ───────────────────
    # Per-family aggregate from the results collection ONLY covers
    # families that wrote rows. The operator wants to see EVERY family
    # the optimizer was meant to evaluate, including the ones that
    # produced 0 cells (so they know it's a data-thinness problem,
    # not a missing-family bug). Build a placeholder row for every
    # family in `state.stat_families` that the aggregation didn't
    # cover, with a structured `status` field.
    fam_cov_pipeline = [
        {"$match": {"run_id": run_id}},
        {"$group": {
            "_id": "$stat_family",
            "n_cells": {"$sum": 1},
            "n_graded_cells": {"$sum": {"$cond": [
                {"$ne": ["$score", None]}, 1, 0]}},
            "best_score": {"$max": "$score"},
            "best_n_bets": {"$max": "$n_bets"},
        }},
        {"$sort": {"n_graded_cells": -1, "_id": 1}},
        {"$project": {"_id": 0, "stat_family": "$_id",
                          "n_cells": 1, "n_graded_cells": 1,
                          "best_score": 1, "best_n_bets": 1}},
    ]
    family_coverage = [d async for d in db[OPTIMIZER_RESULTS].aggregate(
        fam_cov_pipeline, allowDiskUse=True)]
    # Surface families that were discovered but produced ZERO cells
    # (= every cell in every (tier,bucket) combination had no rows
    # after the tier-odds + bucket filter). These would otherwise be
    # invisible to the operator. Mark them `status="no_rows_after_tier_filter"`.
    seen = {f.get("stat_family") for f in family_coverage}
    state = run.get("state") or {}
    discovered = set(state.get("stat_families", []) or [])
    for sf in sorted(discovered - seen):
        family_coverage.append({
            "stat_family":     sf,
            "n_cells":         0,
            "n_graded_cells":  0,
            "best_score":      None,
            "best_n_bets":     None,
            "status":          "no_rows_after_tier_filter",
        })
    # Annotate the families that DID produce rows so the UI can flag
    # the all-skipped-low-sample case distinctly from "no rows".
    for fc in family_coverage:
        if "status" in fc:
            continue
        if fc.get("n_graded_cells", 0) == 0 and fc.get("n_cells", 0) > 0:
            fc["status"] = "all_skipped_low_sample"
        elif fc.get("n_graded_cells", 0) > 0:
            fc["status"] = "graded"
        else:
            fc["status"] = "unknown"

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
        "family_coverage": family_coverage,
        # 2026-05-24 — operator's submitted grid for client-side
        # post-display filtering. The backend ALWAYS brute-forces on
        # `DEFAULT_GRID`; this field is the optional UI-side filter.
        "display_filter_grid": (run.get("state") or {})
                                  .get("display_filter_grid", {}),
    }


@router.get("/{run_id}/top-per-family")
async def top_per_family(run_id: str, request: Request,
                                top_n: int = 5,
                                tier: Optional[str] = None,
                                include_empty: bool = True,
                                auth=Depends(require_admin_token)):
    """Returns the Top-N graded configurations PER (stat_family,
    odds_bucket), optionally filtered to one tier.

    `top_n` default raised 3 → 5 (2026-05-24) per user request:
        "i want the top 5 combos for every stat type"
    `include_empty=True` (default) adds placeholder groups for every
    discovered stat_family that produced 0 graded combos in this run,
    with `status` set so the UI can render an explanatory row instead
    of silently omitting the family.

    Designed to answer the exact question the operator keeps asking:
    "what are the best 3 threshold combos for each stat?". Sort and
    tiebreak are identical to /results so this output is guaranteed
    consistent with the Top-25 and Best-by views on the same run.
    """
    if top_n < 1 or top_n > 100:
        raise HTTPException(400, "top_n must be 1..100")
    db = _get_db()
    run = await db[OPTIMIZER_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if run is None:
        raise HTTPException(404, f"run_id not found: {run_id}")
    match: Dict[str, Any] = {"run_id": run_id, "score": {"$ne": None}}
    if tier:
        match["tier"] = tier
    # MongoDB aggregation: dedupe equivalent samples FIRST, then group
    # by (family, bucket) and slice top-N.
    pipeline = [
        {"$match": match},
        # Collapse threshold-equivalent rows to one canonical config
        # per sample_sig within each (tier, family, bucket).
        {"$sort": {"score": -1, "tier": 1, "n_bets": -1}},
        {"$group": {
            "_id": {"tier": "$tier", "stat_family": "$stat_family",
                      "odds_bucket": "$odds_bucket",
                      "sample_sig": "$sample_sig"},
            "doc": {"$first": "$$ROOT"},
            "n_equivalent_combos": {"$sum": 1},
        }},
        {"$replaceRoot": {"newRoot": {"$mergeObjects": [
            "$doc", {"n_equivalent_combos": "$n_equivalent_combos"}]}}},
        {"$sort": {"score": -1, "tier": 1, "n_bets": -1}},
        {"$group": {
            "_id": {"stat_family": "$stat_family",
                       "odds_bucket": "$odds_bucket"},
            "configs": {"$push": "$$ROOT"},
        }},
        {"$project": {
            "_id": 0,
            "stat_family": "$_id.stat_family",
            "odds_bucket": "$_id.odds_bucket",
            "configs": {"$slice": ["$configs", top_n]},
        }},
        {"$sort": {"stat_family": 1, "odds_bucket": 1}},
    ]
    groups: List[Dict[str, Any]] = []
    async for g in db[OPTIMIZER_RESULTS].aggregate(pipeline, allowDiskUse=True):
        # Strip Mongo _id from nested docs
        for c in g.get("configs", []):
            c.pop("_id", None)
        g["status"] = "graded"
        groups.append(g)

    # ── Surface discovered-but-empty families ──────────────────────
    # If the optimizer's `state.stat_families` lists 14 families but
    # only 2 produced rows after the tier/bucket filter, the other 12
    # would silently disappear from this endpoint. Add a placeholder
    # group for each missing family so the UI can render an
    # "insufficient data for this tier" message instead of pretending
    # the family doesn't exist.
    if include_empty:
        state = run.get("state") or {}
        discovered_families = list(state.get("stat_families", []) or [])
        seen_families = {g["stat_family"] for g in groups}
        for sf in sorted(set(discovered_families) - seen_families):
            groups.append({
                "stat_family": sf,
                "odds_bucket": None,
                "configs":     [],
                "status":      ("no_rows_after_tier_filter"
                                       if tier else "no_graded_combos"),
            })
        groups.sort(key=lambda g: (g["stat_family"],
                                              g.get("odds_bucket") or ""))
    await audit_log(request, action="optimizer_top_per_family",
                       params={"run_id": run_id, "top_n": top_n, "tier": tier},
                       response_summary={"n_groups": len(groups)},
                       **auth)
    return {
        "ok": True, "run_id": run_id,
        "top_n": top_n, "tier": tier,
        "n_groups": len(groups),
        "groups": groups,
    }


# 2026-05-24 — Tier-organized top-N per stat_family.
#
# User asked: "i need them organized by tier so i can figure out the
# prod gates per stat per tier" — i.e. each tier section should list
# every stat_family's Top-N (across all odds buckets within that
# tier) so the operator can see the full per-tier landscape in one
# response. This is the natural shape for "promote to live gates"
# decisions, which are tier-scoped.
@router.get("/{run_id}/top-by-tier")
async def top_by_tier(run_id: str, request: Request,
                            top_n: int = 3,
                            include_empty: bool = True,
                            auth=Depends(require_admin_token)):
    """Returns Top-N configs per (tier × stat_family), aggregated
    across odds buckets within each tier. Response shape:

        {
          "tiers": {
            "safe_haven":  [{stat_family, configs:[...]}, ...],
            "front_lines": [{stat_family, configs:[...]}, ...],
            "war_zone":    [{stat_family, configs:[...]}, ...],
          }
        }

    With `include_empty=True` (default) every discovered stat_family
    appears in every tier section — empty families get
    `configs:[]` and `status` set so the UI can render an
    explanatory placeholder instead of silently dropping the family.
    """
    if top_n < 1 or top_n > 100:
        raise HTTPException(400, "top_n must be 1..100")
    db = _get_db()
    run = await db[OPTIMIZER_RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if run is None:
        raise HTTPException(404, f"run_id not found: {run_id}")
    pipeline = [
        {"$match": {"run_id": run_id, "score": {"$ne": None}}},
        # Dedupe threshold-equivalent rows the same way top_per_family
        # does — score is identical when sample_sig is identical, so
        # keep one canonical doc per sample_sig per (tier, family).
        {"$sort": {"score": -1, "tier": 1, "n_bets": -1}},
        {"$group": {
            "_id": {"tier": "$tier", "stat_family": "$stat_family",
                      "sample_sig": "$sample_sig"},
            "doc": {"$first": "$$ROOT"},
            "n_equivalent_combos": {"$sum": 1},
        }},
        {"$replaceRoot": {"newRoot": {"$mergeObjects": [
            "$doc", {"n_equivalent_combos": "$n_equivalent_combos"}]}}},
        {"$sort": {"score": -1, "tier": 1, "stat_family": 1, "n_bets": -1}},
        {"$group": {
            "_id": {"tier": "$tier", "stat_family": "$stat_family"},
            "configs": {"$push": "$$ROOT"},
        }},
        {"$project": {
            "_id": 0,
            "tier": "$_id.tier",
            "stat_family": "$_id.stat_family",
            "configs": {"$slice": ["$configs", top_n]},
        }},
        {"$sort": {"tier": 1, "stat_family": 1}},
    ]

    by_tier: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in DEFAULT_TIERS
    }
    seen_by_tier: Dict[str, set] = {t: set() for t in DEFAULT_TIERS}
    async for g in db[OPTIMIZER_RESULTS].aggregate(
                                                  pipeline, allowDiskUse=True):
        tier_name = g.pop("tier", None)
        if tier_name not in by_tier:
            by_tier[tier_name] = []
            seen_by_tier[tier_name] = set()
        # Strip Mongo _id from nested docs
        for c in g.get("configs", []):
            c.pop("_id", None)
        g["status"] = "graded"
        by_tier[tier_name].append(g)
        seen_by_tier[tier_name].add(g["stat_family"])

    # Surface discovered-but-empty (tier × family) pairs so the
    # operator sees every family in every tier section.
    if include_empty:
        state = run.get("state") or {}
        discovered_families = list(state.get("stat_families", []) or [])
        for tier_name, rows in by_tier.items():
            for sf in sorted(set(discovered_families)
                                   - seen_by_tier.get(tier_name, set())):
                rows.append({
                    "stat_family": sf, "configs": [],
                    "status": "no_rows_in_tier",
                })
            rows.sort(key=lambda g: g["stat_family"])

    await audit_log(request, action="optimizer_top_by_tier",
                       params={"run_id": run_id, "top_n": top_n},
                       response_summary={
                           "n_tiers": len(by_tier),
                           "n_total_groups": sum(len(v) for v in by_tier.values()),
                       }, **auth)
    return {
        "ok": True, "run_id": run_id, "top_n": top_n,
        "tier_order": DEFAULT_TIERS,
        "tiers": by_tier,
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
    # Same deterministic sort as /results — saved candidates must
    # match what the operator sees in the Top-K table.
    cur = db[OPTIMIZER_RESULTS].find(
        {"run_id": run_id, "score": {"$ne": None}}, {"_id": 0}
    ).sort([("score", -1), ("tier", 1), ("stat_family", 1),
              ("odds_bucket", 1), ("n_bets", -1)]).limit(int(body.top_k))
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
