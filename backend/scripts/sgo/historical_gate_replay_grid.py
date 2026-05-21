"""
historical_gate_replay_grid.py — per-tier × per-stat_family threshold
sweep over `sgo_propvision_full_pipeline_replay`.

For each tier ∈ {safe_haven, front_lines, war_zone}:
  For each stat_family in the data:
    Sweep production gate thresholds INDEPENDENTLY:
        hr_l20_min   ∈ [.55, .60, .65, .70, .75, .80]
        hr_l5_min    ∈ [.40, .50, .60, .70, .80]
        cv_max       ∈ [.50, .70, .90, 1.10, 1.30]
        edge_min     ∈ [0, .025, .05, .075, .10]
        tp_min       ∈ [.50, .55, .60, .65]
    Cross-split by:
        side ∈ {OVER, UNDER}
        odds_bucket

Per cell metrics:
    n_bets, n_wins, hit_rate, calibration_delta (= hit_rate − consensus_avg),
    avg_hr_l20, avg_cv, avg_edge, avg_tp, daily_consistency
        (= sd of daily hit rate, lower is better)

Writes:
    research_grid_runs                (1 run header)
    research_grid_results             (one row per cell)
    candidate_gate_configs            (top-N per tier × stat_family)
"""
from __future__ import annotations
import argparse
import asyncio
import os
import statistics
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path); break

from motor.motor_asyncio import AsyncIOMotorClient

REPLAY  = "sgo_propvision_full_pipeline_replay"
RUNS    = "research_grid_runs"
RESULTS = "research_grid_results"

VERSION = 1
METHODOLOGY = "per_tier_per_stat_family"

DEFAULT_GRID: Dict[str, List[float]] = {
    "hr_l20_min":   [0.55, 0.60, 0.65, 0.70, 0.75, 0.80],
    "hr_l5_min":    [0.40, 0.50, 0.60, 0.70, 0.80],
    "cv_max":       [0.50, 0.70, 0.90, 1.10, 1.30],
    "edge_min":     [0.0, 0.025, 0.05, 0.075, 0.10],
    "tp_min":       [0.50, 0.55, 0.60, 0.65],
}

TIERS = ("safe_haven", "front_lines", "war_zone")


def _f(x: Any) -> Optional[float]:
    if x is None: return None
    try:
        v = float(x); return v
    except (TypeError, ValueError):
        return None


async def _load(db, *, start: str, end: str, league: str,
                  tier_filter: Optional[str]) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {
        "league_id": league,
        "game_date": {"$gte": start, "$lte": end},
    }
    if tier_filter:
        # Drive by the tier the live pipeline actually selected so the
        # baseline view = production behavior. Sweep then explores
        # parameter changes WITHIN each tier's universe.
        pass
    proj = {"_id": 0, "event_id": 1, "player_id": 1, "stat_id": 1,
            "side": 1, "line": 1, "game_date": 1, "stat_family": 1,
            "selected_tier": 1, "odds_bucket": 1,
            "outcome_numeric": 1, "hit_rate_l20": 1, "hit_rate_l5": 1,
            "hit_rate_l10": 1, "cv": 1, "edge": 1, "tp": 1,
            "model_probability": 1, "projection_margin": 1,
            "fair_probability": 1, "implied_probability": 1,
            "safe_haven_pass": 1, "front_lines_pass": 1, "war_zone_pass": 1}
    rows: List[Dict[str, Any]] = []
    async for r in db[REPLAY].find(match, proj):
        r["_hr20"] = _f(r.get("hit_rate_l20"))
        r["_hr5"]  = _f(r.get("hit_rate_l5"))
        r["_cv"]   = _f(r.get("cv"))
        r["_edge"] = _f(r.get("edge"))
        r["_tp"]   = _f(r.get("tp")) or _f(r.get("model_probability"))
        r["_fair"] = _f(r.get("fair_probability"))
        rows.append(r)
    return rows


def _eval(rows: List[Dict[str, Any]],
            hr20_min: float, hr5_min: float, cv_max: float,
            edge_min: float, tp_min: float) -> Dict[str, Any]:
    n = w = 0
    sum_hr20 = sum_cv = sum_edge = sum_tp = sum_fair = 0.0
    n_hr20 = n_cv = n_edge = n_tp = n_fair = 0
    daily: Dict[str, List[int]] = defaultdict(list)

    for r in rows:
        if r["_hr20"] is None or r["_hr20"] < hr20_min:  continue
        if r["_hr5"]  is None or r["_hr5"]  < hr5_min:   continue
        if r["_cv"]   is None or r["_cv"]   > cv_max:    continue
        if r["_edge"] is None or r["_edge"] < edge_min:  continue
        if r["_tp"]   is None or r["_tp"]   < tp_min:    continue
        n += 1
        oc = r.get("outcome_numeric")
        if oc == 1: w += 1
        daily[r.get("game_date") or "?"].append(int(oc == 1))
        if r["_hr20"] is not None: sum_hr20 += r["_hr20"]; n_hr20 += 1
        if r["_cv"]   is not None: sum_cv   += r["_cv"];   n_cv   += 1
        if r["_edge"] is not None: sum_edge += r["_edge"]; n_edge += 1
        if r["_tp"]   is not None: sum_tp   += r["_tp"];   n_tp   += 1
        if r["_fair"] is not None: sum_fair += r["_fair"]; n_fair += 1

    if n == 0:
        return {"hr_l20_min": hr20_min, "hr_l5_min": hr5_min,
                 "cv_max": cv_max, "edge_min": edge_min, "tp_min": tp_min,
                 "n_bets": 0, "n_wins": 0, "hit_rate": None,
                 "consensus_avg": None, "calibration_delta": None,
                 "avg_hr_l20": None, "avg_cv": None, "avg_edge": None,
                 "avg_tp": None, "daily_consistency": None,
                 "daily_days": 0}

    hit_rate = w / n
    consensus_avg = sum_fair / n_fair if n_fair else None
    calibration_delta = (
        hit_rate - consensus_avg if consensus_avg is not None else None)
    daily_rates = [sum(v) / len(v) for v in daily.values() if v]
    daily_consistency = (statistics.pstdev(daily_rates)
                            if len(daily_rates) >= 2 else None)

    return {
        "hr_l20_min": hr20_min, "hr_l5_min": hr5_min,
        "cv_max": cv_max, "edge_min": edge_min, "tp_min": tp_min,
        "n_bets": n, "n_wins": w, "hit_rate": hit_rate,
        "consensus_avg": consensus_avg,
        "calibration_delta": calibration_delta,
        "avg_hr_l20": sum_hr20 / n_hr20 if n_hr20 else None,
        "avg_cv":     sum_cv / n_cv if n_cv else None,
        "avg_edge":   sum_edge / n_edge if n_edge else None,
        "avg_tp":     sum_tp / n_tp if n_tp else None,
        "daily_consistency": daily_consistency,
        "daily_days":  len(daily),
    }


def _iter_grid(g: Dict[str, List[float]]):
    for h20 in g["hr_l20_min"]:
        for h5 in g["hr_l5_min"]:
            for cv in g["cv_max"]:
                for e in g["edge_min"]:
                    for t in g["tp_min"]:
                        yield h20, h5, cv, e, t


async def _run(args: argparse.Namespace) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    grid = dict(DEFAULT_GRID)
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    await db[RUNS].insert_one({
        "run_id": run_id, "version": VERSION, "methodology": METHODOLOGY,
        "params": {"league": args.league, "start": args.start,
                     "end": args.end, "min_bets": args.min_bets,
                     "grid": grid},
        "status": "running", "started_at": started, "finished_at": None,
    })

    print("=" * 78)
    print(f"  PER-TIER × PER-STAT_FAMILY GRID  v{VERSION}")
    print(f"  run_id={run_id}")
    print(f"  league={args.league} window={args.start}..{args.end} "
            f"min_bets={args.min_bets}")
    print(f"  grid axes:")
    for k, v in grid.items():
        print(f"    {k:<14} {v}")
    print("=" * 78)

    rows = await _load(db, start=args.start, end=args.end,
                          league=args.league, tier_filter=None)
    print(f"  rows in replay collection: {len(rows):,}")
    if not rows:
        await db[RUNS].update_one(
            {"run_id": run_id},
            {"$set": {"status": "succeeded_empty",
                        "finished_at": datetime.now(timezone.utc)}})
        return 0

    by_tier_fam_side: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    by_tier_fam:      Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    by_tier:          Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        # The live pipeline assigns one tier per row (SH > FL > WZ).
        # For the sweep, we evaluate "what if the gate had been THIS
        # threshold set instead of the live one" — so we sweep the
        # universe of ALL rows that landed in each tier. A row in
        # safe_haven counts for the SH sweep regardless of whether
        # FL/WZ also passed (FL/WZ universe is everything they pass on,
        # which by construction overlaps).
        for tier in TIERS:
            if r.get(f"{tier}_pass"):
                by_tier[tier].append(r)
                fam = r.get("stat_family") or "_unknown"
                by_tier_fam[(tier, fam)].append(r)
                sd = (r.get("side") or "").upper() or "?"
                by_tier_fam_side[(tier, fam, sd)].append(r)

    print()
    for tier in TIERS:
        n = len(by_tier[tier])
        print(f"  {tier:<12} universe size = {n:,}")

    # ── Sweep ──────────────────────────────────────────────────
    bulk: List[Dict[str, Any]] = []
    n_cells_total = 0
    n_cells_qualified = 0
    candidates: List[Dict[str, Any]] = []

    for (tier, fam), pool in by_tier_fam.items():
        if len(pool) < args.min_bets: continue
        for h20, h5, cv, e, t in _iter_grid(grid):
            n_cells_total += 1
            metrics = _eval(pool, h20, h5, cv, e, t)
            cell = {"run_id": run_id, "version": VERSION,
                     "methodology": METHODOLOGY,
                     "slice": "TIER_FAMILY",
                     "tier": tier, "stat_family": fam,
                     **metrics}
            bulk.append(cell)
            if (metrics["n_bets"] or 0) >= args.min_bets:
                n_cells_qualified += 1
                if (metrics.get("calibration_delta") or -1) > 0:
                    candidates.append(cell)

    # Side-split for top tier+family combos
    for (tier, fam, sd), pool in by_tier_fam_side.items():
        if len(pool) < args.min_bets: continue
        for h20, h5, cv, e, t in _iter_grid(grid):
            n_cells_total += 1
            metrics = _eval(pool, h20, h5, cv, e, t)
            cell = {"run_id": run_id, "version": VERSION,
                     "methodology": METHODOLOGY,
                     "slice": "TIER_FAMILY_SIDE",
                     "tier": tier, "stat_family": fam, "side": sd,
                     **metrics}
            bulk.append(cell)
            if (metrics["n_bets"] or 0) >= args.min_bets:
                n_cells_qualified += 1

    if not args.dry_run:
        for i in range(0, len(bulk), 1000):
            await db[RESULTS].insert_many(bulk[i:i+1000], ordered=False)

    # Per (tier, stat_family) best by hit_rate AND best by calibration
    best_per_pair_hr: Dict[Any, Dict[str, Any]] = {}
    best_per_pair_dl: Dict[Any, Dict[str, Any]] = {}
    for c in bulk:
        if c["slice"] != "TIER_FAMILY":                continue
        if (c.get("n_bets") or 0) < args.min_bets:     continue
        pk = (c["tier"], c["stat_family"])
        hr = c.get("hit_rate") or -1
        if pk not in best_per_pair_hr or hr > (best_per_pair_hr[pk]["hit_rate"] or -1):
            best_per_pair_hr[pk] = c
        dl = c.get("calibration_delta")
        if dl is not None:
            if pk not in best_per_pair_dl or dl > (best_per_pair_dl[pk]["calibration_delta"] or -1):
                best_per_pair_dl[pk] = c

    # Save candidates
    saved_candidates: List[Dict[str, Any]] = []
    if not args.dry_run:
        for pk, c in best_per_pair_dl.items():
            saved_candidates.append({
                "run_id": run_id, "league": args.league,
                "tier": c["tier"], "stat_family": c["stat_family"],
                "params": {k: c[k] for k in
                              ("hr_l20_min", "hr_l5_min", "cv_max",
                               "edge_min", "tp_min")},
                "metrics": {k: c[k] for k in
                                ("n_bets", "hit_rate", "consensus_avg",
                                 "calibration_delta", "avg_hr_l20",
                                 "avg_cv", "avg_edge", "avg_tp",
                                 "daily_consistency", "daily_days")},
                "rank_by": "calibration_delta",
                "created_at": datetime.now(timezone.utc),
            })
        if saved_candidates:
            await db["candidate_gate_configs"].insert_many(
                saved_candidates, ordered=False)

    await db[RUNS].update_one(
        {"run_id": run_id},
        {"$set": {"status": "succeeded",
                    "finished_at": datetime.now(timezone.utc),
                    "n_cells_total": n_cells_total,
                    "n_cells_qualified": n_cells_qualified,
                    "n_candidates_saved": len(saved_candidates)}})

    # Report
    print()
    print(f"  cells evaluated: {n_cells_total:,}  qualified (n≥{args.min_bets}): {n_cells_qualified:,}")
    print()
    print("  ── BEST (tier × stat_family) by HIT RATE ─────────────")
    print(f"  {'tier':<12}{'stat_family':<22}{'n':>5}{'hit':>7}{'Δ':>7}"
            f"{'edge':>7}{'cv':>6}{'tp':>6}")
    for pk, c in sorted(best_per_pair_hr.items(),
                          key=lambda kv: -(kv[1].get('hit_rate') or 0)):
        print(f"  {c['tier']:<12}{c['stat_family']:<22}"
                f"{c['n_bets']:>5}"
                f"{(c.get('hit_rate') or 0)*100:>6.1f}%"
                f"{(c.get('calibration_delta') or 0)*100:>+6.1f}"
                f"{(c.get('avg_edge') or 0)*100:>+6.2f}"
                f"{(c.get('avg_cv') or 0):>6.2f}"
                f"{(c.get('avg_tp') or 0)*100:>5.1f}%")
    print()
    print("  ── BEST (tier × stat_family) by CALIBRATION DELTA ─────")
    print(f"  {'tier':<12}{'stat_family':<22}{'n':>5}{'hit':>7}{'Δ':>7}")
    for pk, c in sorted(best_per_pair_dl.items(),
                          key=lambda kv: -(kv[1].get('calibration_delta') or -1)):
        print(f"  {c['tier']:<12}{c['stat_family']:<22}"
                f"{c['n_bets']:>5}"
                f"{(c.get('hit_rate') or 0)*100:>6.1f}%"
                f"{(c.get('calibration_delta') or 0)*100:>+6.1f}")
    print()
    print(f"  run_id={run_id}")
    print(f"  → research_grid_results  ({len(bulk):,} docs)")
    print(f"  → candidate_gate_configs ({len(saved_candidates)} docs)")
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league",   default="MLB")
    p.add_argument("--start",    required=True)
    p.add_argument("--end",      required=True)
    p.add_argument("--min-bets", type=int, default=20)
    p.add_argument("--dry-run",  action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
