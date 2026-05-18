"""Universal historical gate-threshold grid-search tool.

Optimises gate thresholds across sports, tiers, and stat families using
HISTORICAL test data. NO production gate changes. NO live serving
changes.

Architecture
------------
1. **Candidate pool** — Build ONCE per `(sport, snapshot, tier)` by
   running `run_pipeline(...)` with the existing test-only override
   knobs set to permissive values (numeric metric floors widened to
   admit every eligible canonical row). The resulting `mlb_test_outputs`
   rows carry the metrics + actuals + grade we need.
2. **In-memory combo evaluation** — For each grid combo, apply the
   combo's metric floors/ceilings to the cached candidate pool rows
   and aggregate W/L/P/HR/ROI/breakdowns. No additional pipeline runs.
   This is fast (thousands of combos / sec) and avoids DB churn.

What is preserved at production behaviour (NOT swept):
  • PP-illegal / non-playable / no-odds eligibility (upstream).
  • Tier odds-bucket routing (`tier_odds_bucket_fail` short-circuit).
  • Canonical engine (best-price routing, devig method, book counts).
  • `direction_gate` (OVER sign of `projection − line`).
  • `coverage_gate` (`min_books >= 1`).
  • `margin_gate` (the line==0.5 cv→margin swap is NOT overridable).
  • Grading + ROI settlement (per-row sportsbook American odds).

What CAN be swept (numeric floors/ceilings on metrics):
  • hit_rate_l20_min, hit_rate_l10_min, hit_rate_l5_min
  • cv_max
  • tp_min       — model-probability floor (matches `tp_gate.min` semantics)
  • edge_min     — pp edge floor
  • projection_delta_min  — `projection_mu - line`
  • book_count_min        — min canonical book coverage
  • odds bucket / routing band (informational; already filtered by tier)

Run-mode auto-selection (under `max_combinations`):
  • If the requested full grid exceeds `max_combinations`, the script
    falls back to a paired-gate sweep on the most impactful pairs
    (HR×CV, HR×TP, EDGE×TP, HR×EDGE) and reports each plus a
    head-to-head with the production baseline.

NOTE: Multi-tier extension. The SH override knobs in
`production_replay_runner.py` currently activate ONLY when
`tier == "safe_haven"` so this script also evaluates FL/WZ candidates
in-memory IF the candidate-pool builder is invoked for that tier (the
permissive overrides won't fire, but the canonical evaluator's
gate-failure waterfall is still readable via `failed_gates`). For full
parity FL/WZ support, extend the override scope or implement a
candidate-pool builder that re-evaluates gates from scratch.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.pipeline import run_pipeline, PIPELINE_VERSION


# ── Universal default ranges (from the spec) ───────────────────────
UNIVERSAL_DEFAULTS: Dict[str, List[Any]] = {
    "hit_rate_l20_min":     [90, 85, 80, 75, 70, 65, 60, 55, 50],
    "cv_max":               [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10],
    "tp_min":               [75, 70, 65, 60, 55, 50],
    "edge_min":             [10, 5, 2.5, 0, -2.5, -5],
    "book_count_min":       [1, 2, 3, 4, 5],
}


# ── Sport-aware production-baseline thresholds (informational) ─────
# Source of truth: `services/scoring/gates/thresholds.py`. The
# baseline numbers below are PER-SPORT/TIER snapshots used only to
# stamp the comparison rows in the artifact. They are NOT used to
# evaluate the candidate pool — production gate evaluation already
# happened upstream when the pool was built.
PROD_BASELINES: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("mlb", "safe_haven"): {
        "hit_rate_l20_min_typical": 70.0,  # varies per stat family (70-85)
        "cv_max_typical":           0.60,
        "tp_min_typical":           50.0,
        "edge_min_typical":         0.01,
        "book_count_min":           1,
        "note":  ("Per-family; see thresholds.py _MLB_SAFE_HAVEN. "
                  "tp_gate evaluates p_model_pct; one_sided rejected "
                  "unless elite-binary override rescues."),
    },
    ("mlb", "front_lines"): {
        "hit_rate_l20_min_typical": 70.0,
        "cv_max_typical":           0.55,
        "tp_min_typical":           50.0,
        "edge_min_typical":         5.0,
        "book_count_min":           1,
        "note": "Per-family; see thresholds.py _MLB_FRONT_LINES.",
    },
    ("mlb", "war_zone"): {
        "note": "WZ uses _MLB_WAR_ZONE_OVER_2026_05_16 (no tp_gate).",
    },
}


# ──────────────────────────────────────────────────────────────────
@dataclass
class Combo:
    """A single grid combo. None ⇒ no constraint for that dimension."""
    hit_rate_l20_min: Optional[float] = None
    hit_rate_l10_min: Optional[float] = None
    hit_rate_l5_min:  Optional[float] = None
    cv_max:           Optional[float] = None
    tp_min:           Optional[float] = None
    edge_min:         Optional[float] = None
    projection_delta_min: Optional[float] = None
    book_count_min:   Optional[int]   = None
    odds_bucket:      Optional[str]   = None  # informational only

    def as_label(self) -> str:
        parts: List[str] = []
        d = asdict(self)
        keys_short = {
            "hit_rate_l20_min": "HR20",
            "hit_rate_l10_min": "HR10",
            "hit_rate_l5_min":  "HR5",
            "cv_max":           "CV<=",
            "tp_min":           "TP",
            "edge_min":         "EDG",
            "projection_delta_min": "ΔMU",
            "book_count_min":   "BK",
            "odds_bucket":      "BKT",
        }
        for k, v in d.items():
            if v is None:
                continue
            parts.append(f"{keys_short[k]}={v}")
        return " ".join(parts) if parts else "OPEN"

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Candidate:
    """One row from the candidate pool, keyed by canonical identity.

    Carries the metrics the in-memory eval reads + the grade outcome.
    """
    # Identity
    test_id: str
    sport: str
    snapshot_iso: str
    game_date: str
    event_id: str
    player: str
    stat_family: str
    side: str
    market: str
    line: float
    is_alternate_market: bool
    book: str
    # Metrics (in pp where applicable; cv unitless)
    hit_rate_l20: Optional[float]
    hit_rate_l10: Optional[float]
    hit_rate_l5:  Optional[float]
    cv:           Optional[float]
    tp:           Optional[float]   # canonical TP (devig market prob, pp)
    edge_pct:     Optional[float]
    p_model_pct:  Optional[float]   # tp_gate evaluates THIS
    projection_mu: Optional[float]
    projection_delta: Optional[float]  # mu - line
    book_count:   Optional[int]
    tp_source:    Optional[str]
    devig_method: Optional[str]
    routed_tier:  Optional[str]
    odds:         Optional[int]
    # Grade
    grade_status: Optional[str]
    actual_value: Optional[float]
    profit_units: float = 0.0
    stake_units:  float = 0.0

    @property
    def is_devig(self) -> bool:
        return self.tp_source == "devig"

    @property
    def is_one_sided(self) -> bool:
        return self.tp_source == "one_sided"

    @property
    def bucket(self) -> str:
        if self.is_devig:
            return "devig"
        if self.is_one_sided:
            return ("one_sided_alt" if self.is_alternate_market
                    else "one_sided_std")
        return "unknown"


# ──────────────────────────────────────────────────────────────────
def _row_to_candidate(r: Dict[str, Any], *,
                       test_id: str,
                       sport: str,
                       snapshot_iso: str) -> Candidate:
    model_p = r.get("model_probability")
    p_model_pct = (
        round(float(model_p) * 100.0, 4)
        if isinstance(model_p, (int, float)) else None
    )
    mu = r.get("projection_mu")
    line = r.get("line")
    proj_delta = None
    if isinstance(mu, (int, float)) and isinstance(line, (int, float)):
        proj_delta = round(float(mu) - float(line), 4)
    # Book count — for canonical rows prefer the side-specific count.
    cp_over = r.get("canonical_book_count_over")
    cp_under = r.get("canonical_book_count_under")
    side = (r.get("side") or "OVER").upper()
    book_count = (cp_over if side == "OVER" else cp_under)
    if book_count is None:
        book_count = r.get("canonical_book_count_either_side")
    return Candidate(
        test_id=test_id, sport=sport, snapshot_iso=snapshot_iso,
        game_date=str(r.get("game_date") or "")[:10],
        event_id=str(r.get("event_id") or ""),
        player=str(r.get("player_name_normalized") or ""),
        stat_family=str(r.get("stat_family") or ""),
        side=side,
        market=str(r.get("market") or ""),
        line=float(line) if isinstance(line, (int, float)) else 0.0,
        is_alternate_market=bool(r.get("is_alternate_market")),
        book=str(r.get("book") or ""),
        hit_rate_l20=r.get("hit_rate_l20"),
        hit_rate_l10=r.get("hit_rate_l10"),
        hit_rate_l5=r.get("hit_rate_l5"),
        cv=r.get("cv"),
        tp=r.get("tp"),
        edge_pct=r.get("edge_pct"),
        p_model_pct=p_model_pct,
        projection_mu=mu if isinstance(mu, (int, float)) else None,
        projection_delta=proj_delta,
        book_count=int(book_count) if isinstance(book_count, (int, float))
                  else None,
        tp_source=r.get("tp_source"),
        devig_method=r.get("devig_method"),
        routed_tier=r.get("routed_tier"),
        odds=int(r["odds"]) if r.get("odds") is not None else None,
        grade_status=r.get("grade_status"),
        actual_value=r.get("actual_value"),
        profit_units=float(r.get("profit_units") or 0.0),
        stake_units=float(r.get("stake_units") or 0.0),
    )


# ──────────────────────────────────────────────────────────────────
def _passes_combo(c: Candidate, k: Combo) -> bool:
    """In-memory combo eval. None on the combo means no constraint."""
    if k.hit_rate_l20_min is not None:
        if c.hit_rate_l20 is None or c.hit_rate_l20 < k.hit_rate_l20_min:
            return False
    if k.hit_rate_l10_min is not None:
        if c.hit_rate_l10 is None or c.hit_rate_l10 < k.hit_rate_l10_min:
            return False
    if k.hit_rate_l5_min is not None:
        if c.hit_rate_l5 is None or c.hit_rate_l5 < k.hit_rate_l5_min:
            return False
    if k.cv_max is not None:
        if c.cv is None or c.cv > k.cv_max:
            return False
    if k.tp_min is not None:
        if c.p_model_pct is None or c.p_model_pct < k.tp_min:
            return False
    if k.edge_min is not None:
        if c.edge_pct is None or c.edge_pct < k.edge_min:
            return False
    if k.projection_delta_min is not None:
        if c.projection_delta is None \
                or c.projection_delta < k.projection_delta_min:
            return False
    if k.book_count_min is not None:
        if c.book_count is None or c.book_count < k.book_count_min:
            return False
    if k.odds_bucket is not None:
        if c.routed_tier != k.odds_bucket:
            return False
    return True


# ──────────────────────────────────────────────────────────────────
def _wilson_ci(wins: int, n_decided: int, z: float = 1.96
               ) -> Tuple[Optional[float], Optional[float]]:
    """95 % Wilson score interval for a proportion. Returns
    `(low_pct, high_pct)` in percentage points, or `(None, None)`
    when n=0."""
    if n_decided <= 0:
        return (None, None)
    p = wins / n_decided
    denom = 1 + z * z / n_decided
    centre = (p + z * z / (2 * n_decided)) / denom
    half = (z * math.sqrt(p * (1 - p) / n_decided
                          + z * z / (4 * n_decided * n_decided))) / denom
    return (round(100.0 * (centre - half), 2),
            round(100.0 * (centre + half), 2))


def _bootstrap_roi_ci(unit_pnls: List[float],
                       n_resamples: int = 1000,
                       seed: int = 42,
                       confidence: float = 0.95,
                       ) -> Tuple[Optional[float], Optional[float]]:
    """Percentile bootstrap CI for ROI %, computed over a list of
    per-pick profit-per-1u-stake values (push = 0.0, exclude
    ungraded). Returns `(low_pct, high_pct)` or `(None, None)` when
    the list is empty.

    Stake is implicitly 1u per graded pick — matches the existing
    runner contract (`stake_units` is always 1.0 when gate_pass=True
    and grade_status != ungraded).
    """
    if not unit_pnls:
        return (None, None)
    rng = random.Random(seed)
    n = len(unit_pnls)
    rois: List[float] = []
    for _ in range(n_resamples):
        sample = [unit_pnls[rng.randrange(n)] for _ in range(n)]
        rois.append(100.0 * sum(sample) / n)  # stake=1u/pick
    rois.sort()
    alpha = (1.0 - confidence) / 2.0
    low_idx = int(alpha * n_resamples)
    high_idx = int((1.0 - alpha) * n_resamples) - 1
    return (round(rois[low_idx], 2), round(rois[high_idx], 2))


def _aggregate(passing: List[Candidate]) -> Dict[str, Any]:
    """Aggregate W/L/P/HR/ROI/avg metrics for a list of passing rows.

    Adds 95 % Wilson CI for hit-rate and 95 % bootstrap CI for ROI%.
    `n_total` = every row that passed the combo (regardless of grade).
    `n_graded` = wins+losses+pushes. `n_ungraded` surfaced separately
    so the leaderboard can never silently use ungraded rows.
    """
    wins = losses = pushes = ungraded = 0
    stake = profit = 0.0
    odds_vals: List[int] = []
    tps: List[float] = []; cvs: List[float] = []
    hr20s: List[float] = []; hr10s: List[float] = []; hr5s: List[float] = []
    pnls_graded: List[float] = []  # only graded picks for CI
    for c in passing:
        st = c.grade_status
        if st == "win":
            wins += 1
            pnls_graded.append(c.profit_units)
        elif st == "loss":
            losses += 1
            pnls_graded.append(c.profit_units)
        elif st == "push":
            pushes += 1
            pnls_graded.append(c.profit_units)
        else:
            ungraded += 1
        stake += c.stake_units
        profit += c.profit_units
        if c.odds is not None:
            odds_vals.append(c.odds)
        if c.tp is not None:           tps.append(c.tp)
        if c.cv is not None:           cvs.append(c.cv)
        if c.hit_rate_l20 is not None: hr20s.append(c.hit_rate_l20)
        if c.hit_rate_l10 is not None: hr10s.append(c.hit_rate_l10)
        if c.hit_rate_l5  is not None: hr5s.append(c.hit_rate_l5)
    decided = wins + losses
    graded = decided + pushes
    hr_pct = (100.0 * wins / decided) if decided else None
    roi_pct = (100.0 * profit / stake) if stake else None
    hr_lo, hr_hi = _wilson_ci(wins, decided)
    roi_lo, roi_hi = _bootstrap_roi_ci(pnls_graded)
    def _mean(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 3) if xs else None
    return {
        "n_total": len(passing),
        "n_graded": graded,
        "n_ungraded": ungraded,
        # Legacy aliases kept so existing leaderboards keep working.
        "n": len(passing),
        "graded": graded,
        "wins": wins, "losses": losses, "pushes": pushes,
        "ungraded": ungraded,
        "hit_rate_pct": round(hr_pct, 2) if hr_pct is not None else None,
        "hit_rate_ci95_low": hr_lo,
        "hit_rate_ci95_high": hr_hi,
        "roi_pct": round(roi_pct, 2) if roi_pct is not None else None,
        "roi_ci95_low": roi_lo,
        "roi_ci95_high": roi_hi,
        "profit_units": round(profit, 4),
        "stake_units": round(stake, 4),
        "avg_odds": _mean([float(x) for x in odds_vals]),
        "avg_tp": _mean(tps),
        "avg_cv": _mean(cvs),
        "avg_hr_l20": _mean(hr20s),
        "avg_hr_l10": _mean(hr10s),
        "avg_hr_l5":  _mean(hr5s),
    }


def _bucket_breakdown(passing: List[Candidate]) -> Dict[str, Any]:
    by_b: Dict[str, List[Candidate]] = defaultdict(list)
    for c in passing:
        by_b[c.bucket].append(c)
    out: Dict[str, Any] = {}
    for b in ("devig", "one_sided_std", "one_sided_alt", "unknown"):
        out[b] = _aggregate(by_b.get(b, []))
    return out


def _stat_family_breakdown(
        passing: List[Candidate]) -> List[Dict[str, Any]]:
    by_sf: Dict[str, List[Candidate]] = defaultdict(list)
    for c in passing:
        by_sf[c.stat_family].append(c)
    rows = []
    for sf, items in by_sf.items():
        rows.append({"stat_family": sf, **_aggregate(items)})
    rows.sort(key=lambda r: (-(r["wins"] or 0), (r["profit_units"] or 0)))
    return rows


def _side_breakdown(passing: List[Candidate]) -> List[Dict[str, Any]]:
    by_s: Dict[str, List[Candidate]] = defaultdict(list)
    for c in passing:
        by_s[c.side].append(c)
    return [{"side": s, **_aggregate(v)} for s, v in by_s.items()]


def _losing_archetypes(passing: List[Candidate]) -> List[Dict[str, Any]]:
    arch: Dict[Tuple[str, str], List[Candidate]] = defaultdict(list)
    for c in passing:
        arch[(c.stat_family, c.side)].append(c)
    rows = []
    for (sf, side), items in arch.items():
        a = _aggregate(items)
        rows.append({"stat_family": sf, "side": side, **a})
    # Sort by most losses, then by most-negative profit.
    rows.sort(key=lambda r: (-(r["losses"] or 0),
                              (r["profit_units"] or 0.0)))
    return rows[:10]


def _gate_failure_waterfall(
        all_candidates: List[Candidate], combo: Combo,
) -> Dict[str, int]:
    """For each candidate, find the FIRST combo dimension it fails.
    Returns ordered counts so we can see which threshold killed each
    pool member."""
    order = [
        ("hit_rate_l20_min", lambda c, k: c.hit_rate_l20 is None
            or c.hit_rate_l20 < k),
        ("hit_rate_l10_min", lambda c, k: c.hit_rate_l10 is None
            or c.hit_rate_l10 < k),
        ("hit_rate_l5_min", lambda c, k: c.hit_rate_l5 is None
            or c.hit_rate_l5 < k),
        ("cv_max",           lambda c, k: c.cv is None or c.cv > k),
        ("tp_min",           lambda c, k: c.p_model_pct is None
            or c.p_model_pct < k),
        ("edge_min",         lambda c, k: c.edge_pct is None
            or c.edge_pct < k),
        ("projection_delta_min", lambda c, k: c.projection_delta is None
            or c.projection_delta < k),
        ("book_count_min",   lambda c, k: c.book_count is None
            or c.book_count < k),
    ]
    counts: Counter = Counter()
    for c in all_candidates:
        for dim, pred in order:
            v = getattr(combo, dim)
            if v is None:
                continue
            if pred(c, v):
                counts[dim] += 1
                break
        else:
            counts["passes"] += 1
    return dict(counts)


# ──────────────────────────────────────────────────────────────────
def _balanced_score(metrics: Dict[str, Any], *,
                     volume_target: int,
                     min_sample: int) -> Optional[float]:
    """Balanced score: 50% HR + 30% ROI + 20% volume. Returns None
    if `graded < min_sample`."""
    graded = metrics.get("graded") or 0
    if graded < min_sample:
        return None
    hr = metrics.get("hit_rate_pct") or 0.0
    roi = metrics.get("roi_pct") or 0.0
    vol = min(1.0, graded / float(max(volume_target, 1)))
    # Normalise HR to 0..1 around a 70% pivot — anything below 50 is
    # zero, anything above 90 is one.
    hr_norm = max(0.0, min(1.0, (hr - 50.0) / 40.0))
    # ROI normalisation: -15% → 0, +15% → 1 (clipped). Linear.
    roi_norm = max(0.0, min(1.0, (roi + 15.0) / 30.0))
    return round(0.5 * hr_norm + 0.3 * roi_norm + 0.2 * vol, 4)


# ──────────────────────────────────────────────────────────────────
def _expand_grid(grid: Dict[str, List[Any]],
                  dims_in_use: List[str]) -> List[Combo]:
    """Produce all combinations for the specified dims; other Combo
    dims left None."""
    values_per_dim = [grid[d] for d in dims_in_use]
    combos: List[Combo] = []
    for tup in itertools.product(*values_per_dim):
        kwargs = dict(zip(dims_in_use, tup))
        combos.append(Combo(**kwargs))
    return combos


def _select_combos(
        max_combinations: int,
        grid_overrides: Optional[Dict[str, List[Any]]] = None,
) -> Tuple[List[Combo], List[Dict[str, Any]]]:
    """Pick the mode that fits inside `max_combinations`.

    Returns (combos, breakdown_log).
    """
    grid = {**UNIVERSAL_DEFAULTS, **(grid_overrides or {})}
    plan: List[Dict[str, Any]] = []

    full_dims = ["hit_rate_l20_min", "cv_max", "tp_min",
                 "edge_min", "book_count_min"]
    full_n = math.prod(len(grid[d]) for d in full_dims)
    plan.append({
        "mode_evaluated": "full_grid",
        "dims": full_dims, "n_combos": full_n,
        "fits": full_n <= max_combinations,
    })
    if full_n <= max_combinations:
        return _expand_grid(grid, full_dims), plan

    # Fallback: paired-gate sweeps over the most impactful pairs.
    pair_dims_list = [
        ["hit_rate_l20_min", "cv_max"],
        ["hit_rate_l20_min", "tp_min"],
        ["edge_min", "tp_min"],
        ["hit_rate_l20_min", "edge_min"],
    ]
    combos: List[Combo] = []
    for pair_dims in pair_dims_list:
        n_pair = math.prod(len(grid[d]) for d in pair_dims)
        plan.append({"mode_evaluated": "paired_sweep",
                     "dims": pair_dims, "n_combos": n_pair})
        combos.extend(_expand_grid(grid, pair_dims))
    # Add single-gate sweeps too (lightweight).
    for d in ["hit_rate_l20_min", "cv_max", "tp_min",
              "edge_min", "book_count_min"]:
        combos.extend(_expand_grid(grid, [d]))
        plan.append({"mode_evaluated": "single_gate",
                     "dims": [d], "n_combos": len(grid[d])})
    # Dedupe via canonical tuple.
    seen = set()
    dedup: List[Combo] = []
    for c in combos:
        key = tuple(sorted(c.as_dict().items()))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
        if len(dedup) >= max_combinations:
            break
    return dedup, plan


# ──────────────────────────────────────────────────────────────────
async def _build_candidate_pool(
        db, *,
        sport: str, tier: str,
        snapshot_iso: str, game_date: str,
        test_id: str,
) -> Tuple[List[Candidate], Dict[str, Any]]:
    """Run ONE permissive pipeline pass and read back the rows.

    Permissive override values widen every overridable numeric gate
    to admit every eligible canonical row that survives structural
    gates. `allow_one_sided_for_accuracy_test=True` so one-sided
    props enter the pool. Production thresholds are NOT mutated.
    """
    # Permissive values — broad enough to clear any real candidate
    # while still requiring the actual signed metric to exist.
    summary = await run_pipeline(
        db, sport=sport, mode="historical",
        snapshot_time=snapshot_iso,
        output_namespace="test",
        test_id=test_id,
        tier=tier,
        notes=f"grid_search candidate pool {test_id}",
        allow_one_sided_for_accuracy_test=True,
        sh_tp_gate_min_override=0.0,
        sh_edge_gate_min_override=-1000.0,
        sh_hit_rate_gate_min_override=0.0,
        sh_cv_gate_max_override=1000.0,
    )
    serial = summary["serial"]
    rows: List[Candidate] = []
    async for r in db[f"{sport}_test_outputs"].find(
        {"replay_serial": serial},
        projection={"_id": 0},
    ):
        rows.append(_row_to_candidate(
            r, test_id=test_id, sport=sport, snapshot_iso=snapshot_iso,
        ))
    return rows, summary


# ──────────────────────────────────────────────────────────────────
def _dates_in_range(start_iso: str, end_iso: str) -> List[str]:
    s = datetime.strptime(start_iso, "%Y-%m-%d").date()
    e = datetime.strptime(end_iso,   "%Y-%m-%d").date()
    out = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


# ──────────────────────────────────────────────────────────────────
async def grid_search(
        *,
        sport: str = "mlb",
        tier: str = "safe_haven",
        date_start: str,
        date_end: str,
        snapshot_time: str = "11:00:00Z",
        stat_families: Optional[List[str]] = None,
        sides: Optional[List[str]] = None,
        allow_one_sided_for_accuracy_test: bool = True,
        min_graded_sample_size: int = 20,
        max_combinations: int = 250,
        grid_overrides: Optional[Dict[str, List[Any]]] = None,
        artifact_dir: Path = Path("/app/backend/audits"),
) -> Dict[str, Any]:
    """Execute the grid search and persist artifacts."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    sport = sport.lower()
    tier = tier.lower()

    dates = _dates_in_range(date_start, date_end)
    snapshot_isos = [f"{d}T{snapshot_time}" for d in dates]
    print(f"\n{'='*72}")
    print("  UNIVERSAL GATE GRID SEARCH")
    print(f"  sport             = {sport}")
    print(f"  tier              = {tier}")
    print(f"  dates             = {date_start} → {date_end} "
          f"({len(dates)} days)")
    print(f"  snapshot          = T{snapshot_time}")
    print(f"  allow_one_sided   = {allow_one_sided_for_accuracy_test}")
    print(f"  min_graded_n      = {min_graded_sample_size}")
    print(f"  max_combinations  = {max_combinations}")
    print(f"  pipeline_version  = {PIPELINE_VERSION}")
    print(f"{'='*72}")

    # ── Combo selection (dry-run estimate) ─────────────────────────
    combos, plan = _select_combos(
        max_combinations, grid_overrides=grid_overrides,
    )
    print("\n[plan] selection log:")
    for p in plan:
        print(f"  mode={p['mode_evaluated']:<14s} "
              f"dims={p['dims']} n={p['n_combos']} "
              f"{'(fits)' if p.get('fits') else ''}")
    print(f"[plan] FINAL combo count = {len(combos)}")

    # ── Build the candidate pool (one pass per date) ───────────────
    pool: List[Candidate] = []
    pool_summaries: List[Dict[str, Any]] = []
    for d_iso, snap_iso in zip(dates, snapshot_isos):
        test_id = (f"GRID-{sport.upper()}-"
                   f"{d_iso.replace('-','')}-CANDPOOL")
        print(f"\n[pool] {d_iso} {snap_iso} test_id={test_id}")
        try:
            day_pool, day_summary = await _build_candidate_pool(
                db, sport=sport, tier=tier,
                snapshot_iso=snap_iso, game_date=d_iso,
                test_id=test_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [pool][ERROR] {exc!r} — skipping this date.")
            continue
        # Optional stat_family / side filters
        if stat_families:
            day_pool = [c for c in day_pool
                        if c.stat_family in stat_families]
        if sides:
            day_pool = [c for c in day_pool if c.side in sides]
        # Universal floor: must have AT LEAST been routed to the right
        # tier (the override knobs already enforce this for SH; for
        # safety we also drop any rows whose `routed_tier` mismatches).
        day_pool = [c for c in day_pool
                    if c.routed_tier is None or c.routed_tier == tier]
        pool.extend(day_pool)
        pool_summaries.append({
            "date": d_iso, "snapshot_iso": snap_iso,
            "serial": day_summary["serial"],
            "rows_scanned": day_summary["rows_scanned"],
            "candidates_after_filters": len(day_pool),
        })
        print(f"  [pool] +{len(day_pool)} candidates "
              f"(scanned {day_summary['rows_scanned']})")

    if not pool:
        print("[ERROR] empty candidate pool — aborting.")
        client.close()
        return {"error": "empty_pool"}

    # Pool-level diagnostics
    total = len(pool)
    devig_n = sum(1 for c in pool if c.is_devig)
    onesd_n = sum(1 for c in pool if c.is_one_sided)
    print(f"\n[pool] TOTAL candidates across all dates: {total} "
          f"(devig={devig_n}, one_sided={onesd_n})")
    graded_n = sum(1 for c in pool
                   if c.grade_status in ("win", "loss", "push"))
    print(f"[pool] graded_n={graded_n}")

    # ── Production baseline row (informational, computed in-memory
    # using the typical SH thresholds) ─────────────────────────────
    baseline_thresholds = PROD_BASELINES.get(
        (sport, tier), {}
    )
    baseline_combo = Combo(
        hit_rate_l20_min=baseline_thresholds.get(
            "hit_rate_l20_min_typical"
        ),
        cv_max=baseline_thresholds.get("cv_max_typical"),
        tp_min=baseline_thresholds.get("tp_min_typical"),
        edge_min=baseline_thresholds.get("edge_min_typical"),
        book_count_min=baseline_thresholds.get("book_count_min"),
    )

    # ── Evaluate every combo in-memory ─────────────────────────────
    print(f"\n[eval] evaluating {len(combos)} combos against "
          f"{total} candidates (in-memory; <1 ms each)…")
    rows_out: List[Dict[str, Any]] = []
    for combo in combos:
        passing = [c for c in pool if _passes_combo(c, combo)]
        agg = _aggregate(passing)
        bal = _balanced_score(
            agg, volume_target=max(graded_n // 4, 20),
            min_sample=min_graded_sample_size,
        )
        rows_out.append({
            "combo_label": combo.as_label(),
            "combo": combo.as_dict(),
            "overall": agg,
            "balanced_score": bal,
            "by_bucket": _bucket_breakdown(passing),
            "by_stat_family": _stat_family_breakdown(passing),
            "by_side": _side_breakdown(passing),
            "losing_archetypes": _losing_archetypes(passing),
            "gate_failure_waterfall": _gate_failure_waterfall(
                pool, combo
            ),
        })

    baseline_passing = [c for c in pool if _passes_combo(c, baseline_combo)]
    baseline_row = {
        "combo_label": "PROD_BASELINE_TYPICAL",
        "combo": baseline_combo.as_dict(),
        "overall": _aggregate(baseline_passing),
        "balanced_score": _balanced_score(
            _aggregate(baseline_passing),
            volume_target=max(graded_n // 4, 20),
            min_sample=min_graded_sample_size,
        ),
        "by_bucket": _bucket_breakdown(baseline_passing),
        "by_stat_family": _stat_family_breakdown(baseline_passing),
        "by_side": _side_breakdown(baseline_passing),
        "losing_archetypes": _losing_archetypes(baseline_passing),
        "gate_failure_waterfall": _gate_failure_waterfall(
            pool, baseline_combo
        ),
    }

    # ── Leaderboards (filtered by min_graded_sample_size) ──────────
    qualified_rows = [r for r in rows_out
                      if (r["overall"].get("graded") or 0)
                      >= min_graded_sample_size]

    def _top(n, key):
        # Filter Nones for keyed metric; sort descending.
        rows = [r for r in qualified_rows
                if r["overall"].get(key) is not None]
        rows.sort(key=lambda r: r["overall"][key], reverse=True)
        return rows[:n]

    def _bottom(n, key):
        rows = [r for r in qualified_rows
                if r["overall"].get(key) is not None]
        rows.sort(key=lambda r: r["overall"][key])
        return rows[:n]

    leaderboards = {
        "by_hit_rate":      _top(20, "hit_rate_pct"),
        "by_roi":           _top(20, "roi_pct"),
        "by_profit_units":  _top(20, "profit_units"),
        "by_balanced_score": sorted(
            [r for r in qualified_rows if r["balanced_score"] is not None],
            key=lambda r: r["balanced_score"], reverse=True,
        )[:20],
        "worst_by_roi": _bottom(20, "roi_pct"),
    }

    # Recommended combo = top-balanced
    recommended = (leaderboards["by_balanced_score"][0]
                   if leaderboards["by_balanced_score"] else None)

    # ── CSV leaderboard ───────────────────────────────────────────
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    csv_path = artifact_dir / (
        f"universal_gate_grid_search_"
        f"{sport}_{tier}_{date_start}_{date_end}_{stamp}.csv"
    )
    json_path = artifact_dir / (
        f"universal_gate_grid_search_"
        f"{sport}_{tier}_{date_start}_{date_end}_{stamp}.json"
    )
    csv_cols = [
        "combo_label", "n_total", "n_graded", "n_ungraded",
        "wins", "losses", "pushes",
        "hit_rate_pct", "hit_rate_ci95_low", "hit_rate_ci95_high",
        "roi_pct", "roi_ci95_low", "roi_ci95_high",
        "profit_units", "stake_units",
        "avg_odds", "avg_tp", "avg_cv", "avg_hr_l20",
        "devig_n_graded", "devig_hr", "devig_roi",
        "onesided_std_n_graded", "onesided_std_hr", "onesided_std_roi",
        "onesided_alt_n_graded", "onesided_alt_hr", "onesided_alt_roi",
        "balanced_score",
    ]
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(csv_cols)
        for r in [baseline_row] + rows_out:
            ovr = r["overall"]
            bb = r["by_bucket"]
            dv = bb.get("devig", {})
            os_std = bb.get("one_sided_std", {})
            os_alt = bb.get("one_sided_alt", {})
            w.writerow([
                r["combo_label"], ovr["n_total"], ovr["n_graded"],
                ovr["n_ungraded"], ovr["wins"], ovr["losses"], ovr["pushes"],
                ovr["hit_rate_pct"], ovr["hit_rate_ci95_low"],
                ovr["hit_rate_ci95_high"],
                ovr["roi_pct"], ovr["roi_ci95_low"], ovr["roi_ci95_high"],
                ovr["profit_units"], ovr["stake_units"],
                ovr["avg_odds"], ovr["avg_tp"], ovr["avg_cv"],
                ovr["avg_hr_l20"],
                dv.get("n_graded"), dv.get("hit_rate_pct"),
                dv.get("roi_pct"),
                os_std.get("n_graded"), os_std.get("hit_rate_pct"),
                os_std.get("roi_pct"),
                os_alt.get("n_graded"), os_alt.get("hit_rate_pct"),
                os_alt.get("roi_pct"),
                r["balanced_score"],
            ])

    payload = {
        "audit_kind": "universal_gate_grid_search",
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "sport": sport, "tier": tier,
        "date_start": date_start, "date_end": date_end,
        "snapshot_time": snapshot_time,
        "stat_families_filter": stat_families,
        "sides_filter": sides,
        "allow_one_sided_for_accuracy_test": (
            allow_one_sided_for_accuracy_test
        ),
        "min_graded_sample_size": min_graded_sample_size,
        "max_combinations": max_combinations,
        "pipeline_version": PIPELINE_VERSION,
        "selection_plan": plan,
        "pool_summaries": pool_summaries,
        "pool_total": total,
        "pool_graded": graded_n,
        "pool_devig": devig_n,
        "pool_one_sided": onesd_n,
        "production_baseline": baseline_row,
        "all_rows": rows_out,
        "leaderboards": leaderboards,
        "recommended_combo": recommended,
        "csv_leaderboard": str(csv_path),
        "rerun_command": (
            f"python /app/backend/audits/universal_gate_grid_search.py "
            f"--sport {sport} --tier {tier} "
            f"--date-start {date_start} --date-end {date_end} "
            f"--snapshot-time {snapshot_time} "
            f"--min-graded-sample-size {min_graded_sample_size} "
            f"--max-combinations {max_combinations}"
            + (" --allow-one-sided" if allow_one_sided_for_accuracy_test
               else "")
        ),
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    # ── Console report ────────────────────────────────────────────
    def _print_lb(title: str, rows: List[Dict[str, Any]]):
        print(f"\n{'='*120}")
        print(f"  {title}")
        print(f"{'='*120}")
        print(f"  {'rank':>4s} {'label':<32s} "
              f"{'tot':>4s} {'grd':>4s} {'ung':>4s} "
              f"{'HR':>5s} {'HRci95':>13s} "
              f"{'ROI':>6s} {'ROIci95':>14s} "
              f"{'P&L':>8s} {'BAL':>5s} "
              f"{'devig_g':>7s} {'os_std_g':>8s}")
        for i, r in enumerate(rows, 1):
            ovr = r["overall"]
            bb = r["by_bucket"]
            hr_ci = (f"[{ovr['hit_rate_ci95_low']},"
                     f"{ovr['hit_rate_ci95_high']}]"
                     if ovr['hit_rate_ci95_low'] is not None else "—")
            roi_ci = (f"[{ovr['roi_ci95_low']},"
                      f"{ovr['roi_ci95_high']}]"
                      if ovr['roi_ci95_low'] is not None else "—")
            bal = (f"{r['balanced_score']:>5.3f}"
                   if r['balanced_score'] is not None else "  —  ")
            print(
                f"  {i:>4d} {r['combo_label'][:32]:<32s} "
                f"{ovr['n_total']:>4d} {ovr['n_graded']:>4d} "
                f"{ovr['n_ungraded']:>4d} "
                f"{str(ovr['hit_rate_pct']):>5s} "
                f"{hr_ci:>13s} "
                f"{str(ovr['roi_pct']):>6s} "
                f"{roi_ci:>14s} "
                f"{ovr['profit_units']:>8.3f} {bal} "
                f"{bb['devig'].get('n_graded',0):>7d} "
                f"{bb['one_sided_std'].get('n_graded',0):>8d}"
            )

    _print_lb("TOP 20 by BALANCED SCORE (HR 50 % + ROI 30 % + Vol 20 %)",
               leaderboards["by_balanced_score"])
    _print_lb("TOP 20 by HIT-RATE",       leaderboards["by_hit_rate"])
    _print_lb("TOP 20 by ROI",            leaderboards["by_roi"])
    _print_lb("TOP 20 by PROFIT UNITS",   leaderboards["by_profit_units"])
    _print_lb("WORST 20 by ROI",          leaderboards["worst_by_roi"])

    bo = baseline_row["overall"]
    bb = baseline_row["by_bucket"]
    print(f"\n[baseline] PROD_BASELINE_TYPICAL → "
          f"tot={bo['n_total']} grd={bo['n_graded']} ung={bo['n_ungraded']} "
          f"HR={bo['hit_rate_pct']} "
          f"[{bo['hit_rate_ci95_low']},{bo['hit_rate_ci95_high']}] "
          f"ROI={bo['roi_pct']} "
          f"[{bo['roi_ci95_low']},{bo['roi_ci95_high']}] "
          f"P&L={bo['profit_units']}")
    print(f"  devig_only:        n_grd={bb['devig']['n_graded']} "
          f"HR={bb['devig']['hit_rate_pct']} "
          f"ROI={bb['devig']['roi_pct']}")
    print(f"  one_sided_std:     n_grd={bb['one_sided_std']['n_graded']} "
          f"HR={bb['one_sided_std']['hit_rate_pct']} "
          f"ROI={bb['one_sided_std']['roi_pct']}")

    print(f"\n[artifact-json] {json_path}")
    print(f"[artifact-csv]  {csv_path}")
    if recommended:
        ovr = recommended["overall"]
        bb = recommended["by_bucket"]
        print(f"\n[recommended] {recommended['combo_label']}")
        print(f"  combo = {recommended['combo']}")
        print(f"  overall: tot={ovr['n_total']} grd={ovr['n_graded']} "
              f"ung={ovr['n_ungraded']} "
              f"HR={ovr['hit_rate_pct']} "
              f"[CI {ovr['hit_rate_ci95_low']},{ovr['hit_rate_ci95_high']}] "
              f"ROI={ovr['roi_pct']} "
              f"[CI {ovr['roi_ci95_low']},{ovr['roi_ci95_high']}] "
              f"P&L={ovr['profit_units']} "
              f"balanced={recommended['balanced_score']}")
        print(f"  devig_only:    n_grd={bb['devig'].get('n_graded')} "
              f"HR={bb['devig'].get('hit_rate_pct')} "
              f"ROI={bb['devig'].get('roi_pct')}")
        print(f"  one_sided_std: n_grd={bb['one_sided_std'].get('n_graded')} "
              f"HR={bb['one_sided_std'].get('hit_rate_pct')} "
              f"ROI={bb['one_sided_std'].get('roi_pct')}")
        print(f"  one_sided_alt: n_grd={bb['one_sided_alt'].get('n_graded')} "
              f"HR={bb['one_sided_alt'].get('hit_rate_pct')} "
              f"ROI={bb['one_sided_alt'].get('roi_pct')}")
    print(f"\n[rerun] {payload['rerun_command']}")

    client.close()
    return payload


# ──────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sport", default="mlb")
    p.add_argument("--tier", default="safe_haven")
    p.add_argument("--date-start", required=True)
    p.add_argument("--date-end",   required=True)
    p.add_argument("--snapshot-time", default="11:00:00Z")
    p.add_argument("--stat-families", default=None,
                    help="comma-separated list")
    p.add_argument("--sides", default=None,
                    help="comma-separated list (OVER,UNDER)")
    p.add_argument("--allow-one-sided", action="store_true",
                    default=True)
    p.add_argument("--min-graded-sample-size", type=int, default=20)
    p.add_argument("--max-combinations", type=int, default=250)
    return p.parse_args()


async def _main():
    args = _parse_args()
    stat_fams = (args.stat_families.split(",")
                 if args.stat_families else None)
    sides = args.sides.split(",") if args.sides else None
    await grid_search(
        sport=args.sport, tier=args.tier,
        date_start=args.date_start, date_end=args.date_end,
        snapshot_time=args.snapshot_time,
        stat_families=stat_fams, sides=sides,
        allow_one_sided_for_accuracy_test=args.allow_one_sided,
        min_graded_sample_size=args.min_graded_sample_size,
        max_combinations=args.max_combinations,
    )


if __name__ == "__main__":
    asyncio.run(_main())
