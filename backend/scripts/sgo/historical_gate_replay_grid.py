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
from services.replay.markets import REPLAY_BOOK_WHITELIST_PHASE1

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

TEAM_GRID: Dict[str, List[float]] = {
    "model_probability_min": [0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.75],
    "edge_min":              [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10],
}
TOP_N_TEAM = 5

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
    # One row per book in the replay collection → group by unique bet so
    # n_bets isn't inflated ~18x.  _implied = best (max) odds across books;
    # _consensus_implied = avg across books; all other fields from first row.
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    async for r in db[REPLAY].find(match, proj):
        key = (
            r.get("event_id"),
            r.get("player_id"),
            r.get("stat_family"),
            (r.get("side") or "").upper(),
        )
        groups[key].append(r)
    rows: List[Dict[str, Any]] = []
    for group in groups.values():
        r = min(group, key=lambda x: x.get("implied_probability") or 1.0)
        implieds = [v for v in (_f(g.get("implied_probability")) for g in group)
                    if v is not None]
        r["_hr20"] = _f(r.get("hit_rate_l20"))
        r["_hr5"]  = _f(r.get("hit_rate_l5"))
        r["_cv"]   = _f(r.get("cv"))
        r["_edge"] = _f(r.get("edge"))
        r["_tp"]   = _f(r.get("tp")) or _f(r.get("model_probability"))
        r["_fair"] = _f(r.get("fair_probability"))
        r["_implied"]           = min(implieds) if implieds else None
        r["_consensus_implied"] = sum(implieds) / len(implieds) if implieds else None
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


async def _load_team(db, *, start: str, end: str, league: str) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {
        "prop_type": "team",
        "league_id": league,
        "game_date": {"$gte": start, "$lte": end},
        "book": {"$in": list(REPLAY_BOOK_WHITELIST_PHASE1)},
        "clean_odds": {"$exists": True, "$ne": None},
    }
    proj = {"_id": 0, "market_category": 1, "model_probability": 1,
            "edge": 1, "implied_probability": 1, "fair_probability": 1,
            "outcome_numeric": 1, "game_date": 1, "event_id": 1, "side": 1, "clean_odds": 1}
    # One row per book in the replay collection → group by unique bet so
    # n_bets / profit / ROI aren't inflated ~18x.  Same dedup logic as _load:
    # _implied = best (max) odds across books (fallback to fair_probability);
    # _consensus_implied = avg implied across books.
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    async for r in db[REPLAY].find(match, proj):
        key = (
            r.get("event_id"),
            (r.get("side") or "").upper(),
            r.get("market_category"),
            r.get("line"),
        )
        groups[key].append(r)
    rows: List[Dict[str, Any]] = []
    for group in groups.values():
        r = min(group, key=lambda x: x.get("implied_probability") or 1.0)
        implieds = [v for v in (_f(g.get("implied_probability")) for g in group)
                    if v is not None]
        r["_prob"]              = _f(r.get("model_probability"))
        r["_edge"]              = _f(r.get("edge"))
        r["_implied"]           = (min(implieds) if implieds
                                   else _f(r.get("fair_probability")))
        r["_consensus_implied"] = sum(implieds) / len(implieds) if implieds else None
        rows.append(r)
    return rows


def _eval_team(rows: List[Dict[str, Any]],
               prob_min: float, edge_min: float) -> Dict[str, Any]:
    n = w = 0
    total_profit = 0.0
    sum_prob = sum_edge = 0.0

    for r in rows:
        if r["_prob"]    is None or r["_prob"]    < prob_min: continue
        if r["_edge"]    is None or r["_edge"]    < edge_min: continue
        if r["_implied"] is None or r["_implied"] <= 0:       continue
        oc = r.get("outcome_numeric")
        if oc == 0.5:
            continue
        n += 1
        if oc == 1:
            w += 1
            total_profit += (1.0 / r["_implied"]) - 1.0
        else:
            total_profit -= 1.0
        sum_prob += r["_prob"]
        sum_edge += r["_edge"]

    if n == 0:
        return {"prob_min": prob_min, "edge_min": edge_min,
                "n_bets": 0, "n_wins": 0, "hit_rate": None,
                "total_profit": None, "roi": None,
                "avg_model_prob": None, "avg_edge": None}

    return {
        "prob_min":       prob_min,
        "edge_min":       edge_min,
        "n_bets":         n,
        "n_wins":         w,
        "hit_rate":       w / n,
        "total_profit":   total_profit,
        "roi":            total_profit / n,
        "avg_model_prob": sum_prob / n,
        "avg_edge":       sum_edge / n,
    }


def _iter_team_grid(g: Dict[str, List[float]]):
    for prob in g["model_probability_min"]:
        for e in g["edge_min"]:
            yield prob, e


async def _run_team(args: argparse.Namespace, db) -> int:
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    methodology = "per_market_category_team"

    await db[RUNS].insert_one({
        "run_id": run_id, "version": VERSION, "methodology": methodology,
        "mode": "team",
        "params": {"league": args.league, "start": args.start,
                   "end": args.end, "min_bets": args.min_bets,
                   "grid": TEAM_GRID},
        "status": "running", "started_at": started, "finished_at": None,
    })

    print("=" * 78)
    print(f"  TEAM MODE — PER-MARKET_CATEGORY GRID  v{VERSION}")
    print(f"  run_id={run_id}")
    print(f"  league={args.league} window={args.start}..{args.end} "
          f"min_bets={args.min_bets}")
    print(f"  grid axes:")
    for k, v in TEAM_GRID.items():
        print(f"    {k:<26} {v}")
    print("=" * 78)

    rows = await _load_team(db, start=args.start, end=args.end, league=args.league)
    print(f"  team rows in replay collection: {len(rows):,}")
    if not rows:
        await db[RUNS].update_one(
            {"run_id": run_id},
            {"$set": {"status": "succeeded_empty",
                      "finished_at": datetime.now(timezone.utc)}})
        return 0

    by_market: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cat = r.get("market_category") or "_unknown"
        by_market[cat].append(r)

    print()
    for cat, pool in sorted(by_market.items()):
        print(f"  {cat:<30} universe size = {len(pool):,}")

    FLUSH_EVERY = 1000
    write_buffer: List[Dict[str, Any]] = []
    n_cells_total = 0

    async def _flush():
        if write_buffer and not args.dry_run:
            await db[RESULTS].insert_many(write_buffer, ordered=False)
        write_buffer.clear()

    # top_per_market: market_category -> list of up to TOP_N_TEAM best cells
    top_per_market: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for cat, pool in by_market.items():
        for prob_min, edge_min in _iter_team_grid(TEAM_GRID):
            n_cells_total += 1
            metrics = _eval_team(pool, prob_min, edge_min)
            cell = {
                "run_id": run_id, "version": VERSION,
                "methodology": methodology, "mode": "team",
                "market_category": cat,
                **metrics,
            }
            write_buffer.append(cell)
            if len(write_buffer) >= FLUSH_EVERY:
                await _flush()

            if (metrics["n_bets"] or 0) < args.min_bets:
                continue
            if metrics["total_profit"] is None:
                continue

            bucket = top_per_market[cat]
            bucket.append(cell)
            bucket.sort(key=lambda x: -(x["total_profit"] or 0))
            if len(bucket) > TOP_N_TEAM:
                bucket.pop()

    await _flush()

    # Save top-N per market_category to candidate_gate_configs
    saved_candidates: List[Dict[str, Any]] = []
    if not args.dry_run:
        for cat, bucket in top_per_market.items():
            for rank, c in enumerate(bucket, start=1):
                saved_candidates.append({
                    "run_id":          run_id,
                    "league":          args.league,
                    "mode":            "team",
                    "methodology":     methodology,
                    "market_category": cat,
                    "rank":            rank,
                    "params": {
                        "prob_min": c["prob_min"],
                        "edge_min": c["edge_min"],
                    },
                    "metrics": {k: c[k] for k in
                                ("n_bets", "n_wins", "hit_rate",
                                 "total_profit", "roi",
                                 "avg_model_prob", "avg_edge")},
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
                  "n_candidates_saved": len(saved_candidates)}})

    # Print summary table per market
    print()
    for cat, bucket in sorted(top_per_market.items()):
        print(f"\n  ── {cat} ───────────────────────────────────────────")
        print(f"  {'rank':>4}  {'n':>5}  {'profit':>8}  {'roi':>7}  "
              f"{'hit%':>6}  {'prob_min':>8}  {'edge_min':>8}")
        for rank, c in enumerate(bucket, start=1):
            print(f"  {rank:>4}  {c['n_bets']:>5}  "
                  f"{(c['total_profit'] or 0):>+8.2f}  "
                  f"{(c['roi'] or 0)*100:>+6.2f}%  "
                  f"{(c['hit_rate'] or 0)*100:>5.1f}%  "
                  f"{c['prob_min']:>8.2f}  "
                  f"{c['edge_min']:>8.2f}")

    print()
    print(f"  run_id={run_id}")
    print(f"  → research_grid_results  ({n_cells_total:,} docs)")
    print(f"  → candidate_gate_configs ({len(saved_candidates)} docs)")
    return 0


async def _run(args: argparse.Namespace) -> int:
    # 2026-05-26 — Wrap entire body in try/finally + client.close() so
    # `asyncio.run(_run())` can actually exit. Without this motor
    # holds the loop open and the subprocess hangs after the script
    # logically completes — see historical_full_pipeline_replay.py
    # for the matching fix and the symptom write-up.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        return await _run_body(args, client[os.environ["DB_NAME"]])
    finally:
        client.close()


async def _run_body(args: argparse.Namespace, db) -> int:
    if args.min_bets is None:
        args.min_bets = 1 if args.mode == "team" else 20
    if args.mode == "team":
        return await _run_team(args, db)
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
    # 2026-05-26 — STREAMING writes instead of accumulating the full
    # bulk list in RAM. WHY:
    #   The old code built `bulk: List[Dict]` over the entire sweep
    #   (3,000 combos × ~66 tier×fam groups + ~132 tier×fam×side
    #   groups ≈ 594,000 cells × ~1 KB each = ~600 MB resident).
    #   Combined with the row dataset, motor pool buffers, and any
    #   concurrent /results aggregation, peak RSS easily blew the
    #   4 GiB worker rlimit and the kernel SIGKILL'd it. Symptom for
    #   the operator: "optimizer 504'd and killed the OOM."
    #
    #   With streaming, peak buffer is bounded to FLUSH_EVERY × cell_size
    #   (1 MB) regardless of how many combos × groups the sweep
    #   produces.
    n_cells_total = 0
    n_cells_qualified = 0
    candidates: List[Dict[str, Any]] = []
    # For "best per (tier, family)" we need to remember the winning
    # cells. Storing only the WINNERS (not all 594k cells) keeps the
    # dict tiny — at most 3 tiers × ~22 fams = ~66 entries.
    best_per_pair_hr: Dict[Any, Dict[str, Any]] = {}
    best_per_pair_dl: Dict[Any, Dict[str, Any]] = {}
    FLUSH_EVERY = 1000
    write_buffer: List[Dict[str, Any]] = []

    async def _flush():
        if write_buffer and not args.dry_run:
            await db[RESULTS].insert_many(write_buffer, ordered=False)
        write_buffer.clear()

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
            write_buffer.append(cell)
            if len(write_buffer) >= FLUSH_EVERY:
                await _flush()
            if (metrics["n_bets"] or 0) >= args.min_bets:
                n_cells_qualified += 1
                # Track best-per-(tier,fam) ON THE FLY so we don't have
                # to hold the full bulk list to find winners later.
                pk = (tier, fam)
                hr = metrics.get("hit_rate") or -1
                if pk not in best_per_pair_hr or hr > (best_per_pair_hr[pk]["hit_rate"] or -1):
                    best_per_pair_hr[pk] = cell
                dl = metrics.get("calibration_delta")
                if dl is not None:
                    if pk not in best_per_pair_dl or dl > (best_per_pair_dl[pk]["calibration_delta"] or -1):
                        best_per_pair_dl[pk] = cell
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
            write_buffer.append(cell)
            if len(write_buffer) >= FLUSH_EVERY:
                await _flush()
            if (metrics["n_bets"] or 0) >= args.min_bets:
                n_cells_qualified += 1

    # Final flush
    await _flush()

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
    print(f"  → research_grid_results  ({n_cells_total:,} docs)")
    print(f"  → candidate_gate_configs ({len(saved_candidates)} docs)")
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league",   default="MLB")
    p.add_argument("--start",    required=True)
    p.add_argument("--end",      required=True)
    p.add_argument("--mode",     choices=["player", "team"], default="player")
    p.add_argument("--min-bets", type=int, default=None,
                   help="minimum bets per cell to qualify (default: 20 for player, 1 for team)")
    p.add_argument("--dry-run",  action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    rc = asyncio.run(_run(_parse()))
    from scripts.sgo.handoff import update_handoff
    update_handoff()
    sys.exit(rc)
