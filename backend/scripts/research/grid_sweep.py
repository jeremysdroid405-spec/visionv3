"""
grid_sweep.py — Outcome-side grid sweep over `sgo_pp_research_outcomes`.

Driver:
    sgo_pp_research_outcomes   (where outcome_resolved=True)
Joined to:
    sgo_pp_research_core_enriched   (for edge/book-count/market-width fields)

Filter axes (all configurable):
    edge_min                 ∈ {0, .025, .05, .075, .10, .15}
    devig_book_count_min     ∈ {1, 2, 3, 5, 7}
    sharp_book_count_min     ∈ {0, 1, 2, 3}
    market_width_max         ∈ {None, .05, .10, .15, .25}     (None = any)
    consensus_disagreement_max ∈ {None, .05, .10, .15, .25}   (None = any)

Per-cell metrics: n_bets, n_wins, n_losses, hit_rate, total_profit_units,
ROI (= profit / n_bets, stake=1 unit), avg_edge, avg_pp_implied.

Persists:
    research_grid_runs           one row per sweep run
    research_grid_results        one row per (cell, slice) combination

stdout report:
    • top ROI grids (with min_bets gate)
    • top hit-rate grids
    • best per stat_family
    • worst 10 grids
    • saved run_id
    • 3 recommended candidate configs (sweet spots)

Pure: no external HTTP. Uses ONLY motor + already-installed deps.

Usage
    python -m scripts.research.grid_sweep \\
        --league=MLB --start=2025-06-01 --end=2025-06-30 \\
        --dataset=sgo_pp_research_outcomes \\
        --exclude-stat-family=fantasy_score \\
        --min-bets=30

Add `--config=path/to/preset.json` to override the default grid.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient

# ── Default grid (what the user asked for) ───────────────────────────
DEFAULT_GRID: Dict[str, List[Optional[float]]] = {
    "edge_min":                   [0.0, 0.025, 0.05, 0.075, 0.10, 0.15],
    "devig_book_count_min":       [1, 2, 3, 5, 7],
    "sharp_book_count_min":       [0, 1, 2, 3],
    "market_width_max":           [None, 0.05, 0.10, 0.15, 0.25],
    "consensus_disagreement_max": [None, 0.05, 0.10, 0.15, 0.25],
}

OUTCOMES = "sgo_pp_research_outcomes"
ENRICHED = "sgo_pp_research_core_enriched"
RUNS     = "research_grid_runs"
RESULTS  = "research_grid_results"


# ── Helpers ─────────────────────────────────────────────────────────
def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _roi_for_bet(outcome_numeric: Optional[int],
                  pp_implied: Optional[float]) -> Optional[float]:
    """Per-bet PnL in units (stake=1). Returns None if not computable."""
    if outcome_numeric is None or pp_implied is None or pp_implied <= 0:
        return None
    if outcome_numeric == 1:                    # win
        return (1.0 / pp_implied) - 1.0
    if outcome_numeric == 0:                    # loss
        return -1.0
    return 0.0                                    # push


# ── Stage 1: pull eligible rows joined enriched↔outcomes ─────────────
async def _load_dataset(
    db,
    *,
    league: Optional[str],
    start: Optional[str],
    end: Optional[str],
    exclude_families: List[str],
) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {"outcome_resolved": True}
    if league:
        match["league_id"] = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    if exclude_families:
        match["stat_family"] = {"$nin": exclude_families}

    # $lookup against enriched on the composite key.
    pipeline = [
        {"$match": match},
        {"$lookup": {
            "from": ENRICHED,
            "let": {
                "ev": "$event_id", "pid": "$player_id", "sid": "$stat_id",
                "sd": "$side", "ln": "$line", "per": "$period_id",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$event_id", "$$ev"]},
                    {"$eq": ["$player_id", "$$pid"]},
                    {"$eq": ["$stat_id", "$$sid"]},
                    {"$eq": ["$side", "$$sd"]},
                    {"$eq": ["$line", "$$ln"]},
                    {"$eq": [{"$ifNull": ["$period_id", None]},
                              {"$ifNull": ["$$per", None]}]},
                ]}}},
                {"$project": {
                    "_id": 0,
                    "best_book_edge": 1, "edge_vs_consensus": 1,
                    "best_book_probability": 1,
                    "pp_implied_probability": 1,
                    "devig_book_count": 1, "sharp_book_count": 1,
                    "market_width": 1, "consensus_disagreement": 1,
                }},
                {"$limit": 1},
            ],
            "as": "enr",
        }},
        {"$match": {"enr.0": {"$exists": True}}},
        {"$project": {
            "_id": 0,
            "stat_family": 1, "side": 1, "outcome_numeric": 1, "hit": 1,
            "best_book_edge":          {"$arrayElemAt": ["$enr.best_book_edge", 0]},
            "edge_vs_consensus":       {"$arrayElemAt": ["$enr.edge_vs_consensus", 0]},
            "pp_implied_probability":  {"$arrayElemAt": ["$enr.pp_implied_probability", 0]},
            "devig_book_count":        {"$arrayElemAt": ["$enr.devig_book_count", 0]},
            "sharp_book_count":        {"$arrayElemAt": ["$enr.sharp_book_count", 0]},
            "market_width":            {"$arrayElemAt": ["$enr.market_width", 0]},
            "consensus_disagreement":  {"$arrayElemAt": ["$enr.consensus_disagreement", 0]},
        }},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db[OUTCOMES].aggregate(pipeline, allowDiskUse=True):
        # Cast everything we filter on to float once, up-front.
        r["_edge"]  = _to_float(r.get("best_book_edge"))
        r["_devig"] = _to_float(r.get("devig_book_count"))
        r["_sharp"] = _to_float(r.get("sharp_book_count"))
        r["_mw"]    = _to_float(r.get("market_width"))
        r["_cd"]    = _to_float(r.get("consensus_disagreement"))
        r["_imp"]   = _to_float(r.get("pp_implied_probability"))
        r["_oc"]    = r.get("outcome_numeric")
        rows.append(r)
    return rows


# ── Stage 2: cell aggregation ───────────────────────────────────────
def _eval_cell(rows: Iterable[Dict[str, Any]],
                edge_min: float,
                devig_min: int,
                sharp_min: int,
                mw_max: Optional[float],
                cd_max: Optional[float]) -> Dict[str, Any]:
    n = wins = losses = pushes = 0
    profit = 0.0
    sum_edge = 0.0; sum_imp = 0.0; ne = 0; ni = 0
    for r in rows:
        e = r["_edge"]
        if e is None or e < edge_min:           continue
        d = r["_devig"]
        if d is None or d < devig_min:          continue
        s = r["_sharp"]
        if s is None or s < sharp_min:          continue
        if mw_max is not None:
            mw = r["_mw"]
            if mw is None or mw > mw_max:       continue
        if cd_max is not None:
            cd = r["_cd"]
            if cd is None or cd > cd_max:       continue

        n += 1
        oc = r["_oc"]
        if oc == 1:   wins += 1
        elif oc == 0: losses += 1
        else:         pushes += 1
        pnl = _roi_for_bet(oc, r["_imp"])
        if pnl is not None:
            profit += pnl
        sum_edge += e; ne += 1
        if r["_imp"] is not None:
            sum_imp += r["_imp"]; ni += 1

    hit_rate = (wins / n) if n else None
    roi      = (profit / n) if n else None
    return {
        "edge_min": edge_min,
        "devig_book_count_min": devig_min,
        "sharp_book_count_min": sharp_min,
        "market_width_max": mw_max,
        "consensus_disagreement_max": cd_max,
        "n_bets": n,
        "n_wins": wins,
        "n_losses": losses,
        "n_pushes": pushes,
        "hit_rate": hit_rate,
        "total_profit_units": profit if n else 0.0,
        "roi": roi,
        "avg_edge": (sum_edge / ne) if ne else None,
        "avg_pp_implied": (sum_imp / ni) if ni else None,
    }


def _iter_grid(grid: Dict[str, List[Any]]):
    for em in grid["edge_min"]:
        for dm in grid["devig_book_count_min"]:
            for sm in grid["sharp_book_count_min"]:
                for mw in grid["market_width_max"]:
                    for cd in grid["consensus_disagreement_max"]:
                        yield em, dm, sm, mw, cd


# ── Stage 3: persist + report ───────────────────────────────────────
async def _run(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    grid = dict(DEFAULT_GRID)
    if args.config:
        with open(args.config) as fh:
            override = json.load(fh)
        grid.update(override)

    exclude_families = [s.strip() for s in (args.exclude_stat_family or "").split(",")
                         if s.strip()]

    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    run_doc = {
        "run_id": run_id,
        "params": {
            "league": args.league,
            "start": args.start, "end": args.end,
            "dataset": args.dataset,
            "exclude_stat_family": exclude_families,
            "min_bets": args.min_bets,
            "grid": grid,
        },
        "status": "running",
        "started_at": started,
        "finished_at": None,
        "n_eligible_rows": 0,
        "n_cells_total": 0,
        "n_cells_qualified": 0,
    }
    await db[RUNS].insert_one(run_doc)

    if args.dry_run:
        # Still pull dataset count, but skip persisting per-cell results
        print("[grid_sweep] --dry-run: will compute but not persist results.")

    print("=" * 76)
    print(f"  GRID SWEEP  run_id={run_id}")
    print(f"  league={args.league}  window={args.start}…{args.end}  "
            f"dataset={args.dataset}  min_bets={args.min_bets}")
    print(f"  exclude_stat_family={exclude_families or '<none>'}")
    print("  grid axes:")
    for k, v in grid.items():
        print(f"    {k:<32} {v}")
    print("=" * 76)

    rows = await _load_dataset(
        db,
        league=args.league, start=args.start, end=args.end,
        exclude_families=exclude_families,
    )
    print(f"\n  eligible rows after join: {len(rows):,}")
    if not rows:
        print("  → no rows. Nothing to sweep. Saving empty run.")
        await db[RUNS].update_one(
            {"run_id": run_id},
            {"$set": {"status": "succeeded_empty",
                        "finished_at": datetime.now(timezone.utc),
                        "n_eligible_rows": 0}})
        print(f"\n  run_id={run_id}  (empty)")
        return 0

    # Bucket index for stat_family splits
    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_side:   Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_family[r.get("stat_family") or "_unknown"].append(r)
        by_side[(r.get("side") or "_unknown").upper()].append(r)

    # Sweep
    all_cells: List[Dict[str, Any]] = []
    family_cells: List[Dict[str, Any]] = []
    side_cells:   List[Dict[str, Any]] = []
    n_total = 0
    for em, dm, sm, mw, cd in _iter_grid(grid):
        n_total += 1
        all_cells.append({"slice": "ALL", **_eval_cell(rows, em, dm, sm, mw, cd)})
        for fam, fr in by_family.items():
            family_cells.append({"slice": "STAT_FAMILY", "stat_family": fam,
                                    **_eval_cell(fr, em, dm, sm, mw, cd)})
        for sd, sr in by_side.items():
            side_cells.append({"slice": "SIDE", "side": sd,
                                **_eval_cell(sr, em, dm, sm, mw, cd)})

    qualified = [c for c in all_cells if (c["n_bets"] or 0) >= args.min_bets]

    # Persist
    if not args.dry_run:
        # Tag all rows with run_id and bulk-insert
        for batch in (all_cells, family_cells, side_cells):
            for c in batch: c["run_id"] = run_id
        bulk = all_cells + family_cells + side_cells
        # chunk to avoid huge single insert
        chunk = 1000
        for i in range(0, len(bulk), chunk):
            await db[RESULTS].insert_many(bulk[i:i+chunk], ordered=False)

    await db[RUNS].update_one(
        {"run_id": run_id},
        {"$set": {"status": "succeeded",
                    "finished_at": datetime.now(timezone.utc),
                    "n_eligible_rows": len(rows),
                    "n_cells_total": n_total,
                    "n_cells_qualified": len(qualified)}})

    # ── Report ──────────────────────────────────────────────────────
    def _fmt_cell(c: Dict[str, Any]) -> str:
        return (f"edge≥{c['edge_min']:>5.3f}  "
                  f"devig≥{int(c['devig_book_count_min'])}  "
                  f"sharp≥{int(c['sharp_book_count_min'])}  "
                  f"mw≤{(c['market_width_max'] if c['market_width_max'] is not None else 'any'):<6}  "
                  f"cd≤{(c['consensus_disagreement_max'] if c['consensus_disagreement_max'] is not None else 'any'):<6}  "
                  f"n={c['n_bets']:>5}  "
                  f"hit={(c['hit_rate'] or 0)*100:>5.1f}%  "
                  f"roi={(c['roi'] or 0)*100:>+6.2f}%")

    print(f"\n  total cells: {n_total:,}   qualified (≥{args.min_bets} bets): {len(qualified):,}")

    if qualified:
        print("\n  ── TOP 10 ROI (qualified) ──────────────────────────────")
        for c in sorted(qualified, key=lambda x: (x["roi"] or -1), reverse=True)[:10]:
            print(f"    {_fmt_cell(c)}")

        print("\n  ── TOP 10 HIT-RATE (qualified) ─────────────────────────")
        for c in sorted(qualified, key=lambda x: (x["hit_rate"] or -1), reverse=True)[:10]:
            print(f"    {_fmt_cell(c)}")

        print("\n  ── WORST 10 ROI (qualified) ────────────────────────────")
        for c in sorted(qualified, key=lambda x: (x["roi"] or 1))[:10]:
            print(f"    {_fmt_cell(c)}")
    else:
        print("\n  No cells met min_bets gate.")

    # Best per stat_family (top ROI per family that meets min_bets)
    fam_best: Dict[str, Dict[str, Any]] = {}
    for c in family_cells:
        if (c["n_bets"] or 0) < args.min_bets:    continue
        fam = c["stat_family"]
        if fam not in fam_best or (c["roi"] or -1) > (fam_best[fam]["roi"] or -1):
            fam_best[fam] = c
    if fam_best:
        print("\n  ── BEST GRID PER STAT_FAMILY ───────────────────────────")
        for fam, c in sorted(fam_best.items(),
                                key=lambda kv: (kv[1]["roi"] or -1),
                                reverse=True):
            print(f"    {fam:<22} {_fmt_cell(c)}")

    # Recommended candidate configs: take top-ROI qualified cells whose
    # n_bets ≥ max(min_bets, 100) — bias toward stability.
    stable = sorted(
        [c for c in qualified if (c["n_bets"] or 0) >= max(args.min_bets, 100)],
        key=lambda x: (x["roi"] or -1), reverse=True,
    )[:3]
    if stable:
        print("\n  ── RECOMMENDED CANDIDATE CONFIGS ───────────────────────")
        for i, c in enumerate(stable, 1):
            print(f"    #{i}  {_fmt_cell(c)}")
        # Also persist as candidate_thresholds drafts via the writable
        # collection, so the user can promote them via Admin API later.
        if not args.dry_run:
            await db["candidate_thresholds"].insert_many([
                {"run_id": run_id, "rank": i + 1,
                 "params": {k: v for k, v in c.items()
                            if k in ("edge_min", "devig_book_count_min",
                                     "sharp_book_count_min",
                                     "market_width_max",
                                     "consensus_disagreement_max")},
                 "metrics": {k: c[k] for k in
                              ("n_bets", "hit_rate", "roi",
                               "total_profit_units", "avg_edge",
                               "avg_pp_implied")},
                 "created_at": datetime.now(timezone.utc),
                 "league": args.league}
                for i, c in enumerate(stable)
            ], ordered=False)
    else:
        print("\n  No 'stable' cells (n_bets≥100). Inspect the qualified set.")

    print(f"\n  saved run_id = {run_id}")
    print("  → research_grid_runs       (1 doc)")
    print(f"  → research_grid_results    ({len(all_cells)+len(family_cells)+len(side_cells):,} docs)")
    if stable and not args.dry_run:
        print(f"  → candidate_thresholds     ({len(stable)} docs)")
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league", default="MLB")
    p.add_argument("--start",  required=True, help="YYYY-MM-DD")
    p.add_argument("--end",    required=True, help="YYYY-MM-DD")
    p.add_argument("--dataset", default=OUTCOMES,
                     help="(informational — driver is always sgo_pp_research_outcomes)")
    p.add_argument("--exclude-stat-family", default="fantasy_score",
                     help="Comma-separated families to skip")
    p.add_argument("--min-bets", type=int, default=30,
                     help="Min bets per cell to be 'qualified'")
    p.add_argument("--config", default=None,
                     help="Path to JSON file overriding any grid axis")
    p.add_argument("--dry-run", action="store_true",
                     help="Compute + report; do NOT write to Mongo")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
