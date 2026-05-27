"""
grid_sweep.py — v2.  PP-FREE outcome-side calibration sweep.

Architecture (post 2026-05-21 correction):

    Books        →  market truth   (consensus_prob, best_book_prob,
                                       sharp_consensus_prob, devig depth,
                                       width, disagreement)
    PrizePicks   →  availability   (the prop existed at this line/side)
                    + payout schedule  (optional, EV layer only)

PP odds NEVER participate in:
    - edge calc                  - implied probability
    - devig                       - consensus pricing
    - TP                          - calibration

Filter axes (books-only):
    consensus_prob_min            ∈ {.50, .55, .60, .65, .70, .75}
    devig_book_count_min          ∈ {1, 2, 3, 5, 7}
    sharp_book_count_min          ∈ {0, 1, 2, 3}
    market_width_max              ∈ {None, .05, .10, .15, .25}
    consensus_disagreement_max    ∈ {None, .05, .10, .15, .25}

Headline metric is CALIBRATION DELTA:
    calibration_delta = hit_rate − consensus_prob_avg
A positive delta means the filter found a pocket where the consensus market
*underestimated* true win rate. That's the only thing PP can't fake for us.

Persists `version=2`, `methodology="market_truth_pp_free"` so v1 contaminated
runs and v2 corrected runs are trivially distinguishable.
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
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient

VERSION = 2
METHODOLOGY = "market_truth_pp_free"

DEFAULT_GRID: Dict[str, List[Optional[float]]] = {
    "consensus_prob_min":         [0.50, 0.55, 0.60, 0.65, 0.70, 0.75],
    "devig_book_count_min":       [1, 2, 3, 5, 7],
    "sharp_book_count_min":       [0, 1, 2, 3],
    "market_width_max":           [None, 0.05, 0.10, 0.15, 0.25],
    "consensus_disagreement_max": [None, 0.05, 0.10, 0.15, 0.25],
}

OUTCOMES = "sgo_pp_research_outcomes"
ENRICHED = "sgo_pp_research_core_enriched"
RUNS     = "research_grid_runs"
RESULTS  = "research_grid_results"


def _f(x: Any) -> Optional[float]:
    if x is None: return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v): return None
        return v
    except (TypeError, ValueError):
        return None


async def _load_dataset(
    db, *, league: Optional[str], start: Optional[str], end: Optional[str],
    exclude_families: List[str],
) -> List[Dict[str, Any]]:
    """Pull graded outcomes joined to BOOKS-ONLY enriched fields.

    Note: we read `consensus_probability`, `sharp_consensus_probability`,
    `best_book_probability`, `devig_book_count`, `sharp_book_count`,
    `market_width`, `consensus_disagreement` — ALL of which are derived
    from sportsbooks only. We deliberately do NOT read
    `pp_implied_probability`, `edge_vs_consensus`, or `best_book_edge`
    (the latter two are contaminated derivatives of pp_implied).
    """
    match: Dict[str, Any] = {"outcome_resolved": True}
    if league:                match["league_id"]  = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    if exclude_families:      match["stat_family"] = {"$nin": exclude_families}

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
                    # BOOKS-ONLY FIELDS:
                    "consensus_probability":        1,
                    "sharp_consensus_probability":  1,
                    "best_book_probability":        1,
                    "devig_book_count":             1,
                    "sharp_book_count":             1,
                    "market_width":                 1,
                    "consensus_disagreement":       1,
                }},
                {"$limit": 1},
            ],
            "as": "enr",
        }},
        {"$match": {"enr.0": {"$exists": True}}},
        {"$project": {
            "_id": 0,
            "stat_family": 1, "side": 1,
            "outcome_numeric": 1, "hit": 1,
            "consensus_prob":         {"$arrayElemAt": ["$enr.consensus_probability", 0]},
            "sharp_consensus_prob":   {"$arrayElemAt": ["$enr.sharp_consensus_probability", 0]},
            "best_book_prob":         {"$arrayElemAt": ["$enr.best_book_probability", 0]},
            "devig_book_count":       {"$arrayElemAt": ["$enr.devig_book_count", 0]},
            "sharp_book_count":       {"$arrayElemAt": ["$enr.sharp_book_count", 0]},
            "market_width":           {"$arrayElemAt": ["$enr.market_width", 0]},
            "consensus_disagreement": {"$arrayElemAt": ["$enr.consensus_disagreement", 0]},
        }},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db[OUTCOMES].aggregate(pipeline, allowDiskUse=True):
        r["_cons"]  = _f(r.get("consensus_prob"))
        r["_sharp_c"] = _f(r.get("sharp_consensus_prob"))
        r["_best"]  = _f(r.get("best_book_prob"))
        r["_devig"] = _f(r.get("devig_book_count"))
        r["_sharp"] = _f(r.get("sharp_book_count"))
        r["_mw"]    = _f(r.get("market_width"))
        r["_cd"]    = _f(r.get("consensus_disagreement"))
        r["_oc"]    = r.get("outcome_numeric")
        rows.append(r)
    return rows


def _eval_cell(rows: Iterable[Dict[str, Any]],
                cons_min: float, devig_min: int, sharp_min: int,
                mw_max: Optional[float], cd_max: Optional[float]
                ) -> Dict[str, Any]:
    n = wins = losses = pushes = 0
    s_cons = s_sharp_c = s_best = 0.0
    n_cons = n_sharp_c = n_best = 0
    s_devig = s_sharp = 0.0
    s_mw = s_cd = 0.0
    n_mw = n_cd = 0
    for r in rows:
        c = r["_cons"]
        if c is None or c < cons_min:           continue
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
        s_cons += c; n_cons += 1
        s_devig += d; s_sharp += s
        if r["_sharp_c"] is not None:
            s_sharp_c += r["_sharp_c"]; n_sharp_c += 1
        if r["_best"] is not None:
            s_best += r["_best"]; n_best += 1
        if r["_mw"] is not None:
            s_mw += r["_mw"]; n_mw += 1
        if r["_cd"] is not None:
            s_cd += r["_cd"]; n_cd += 1

    if n == 0:
        return {
            "consensus_prob_min": cons_min,
            "devig_book_count_min": devig_min,
            "sharp_book_count_min": sharp_min,
            "market_width_max": mw_max,
            "consensus_disagreement_max": cd_max,
            "n_bets": 0, "n_wins": 0, "n_losses": 0, "n_pushes": 0,
            "hit_rate": None, "consensus_prob_avg": None,
            "sharp_consensus_prob_avg": None, "best_book_prob_avg": None,
            "calibration_delta_consensus": None,
            "calibration_delta_sharp": None,
            "calibration_delta_best_book": None,
            "avg_devig_book_count": None, "avg_sharp_book_count": None,
            "avg_market_width": None, "avg_consensus_disagreement": None,
        }

    hit = wins / n
    cons_avg   = s_cons / n_cons if n_cons else None
    sharp_avg  = s_sharp_c / n_sharp_c if n_sharp_c else None
    best_avg   = s_best / n_best if n_best else None
    return {
        "consensus_prob_min": cons_min,
        "devig_book_count_min": devig_min,
        "sharp_book_count_min": sharp_min,
        "market_width_max": mw_max,
        "consensus_disagreement_max": cd_max,

        "n_bets": n, "n_wins": wins, "n_losses": losses, "n_pushes": pushes,
        "hit_rate": hit,

        "consensus_prob_avg": cons_avg,
        "sharp_consensus_prob_avg": sharp_avg,
        "best_book_prob_avg": best_avg,

        "calibration_delta_consensus":  (hit - cons_avg)  if cons_avg  is not None else None,
        "calibration_delta_sharp":       (hit - sharp_avg) if sharp_avg is not None else None,
        "calibration_delta_best_book":   (hit - best_avg)  if best_avg  is not None else None,

        "avg_devig_book_count": s_devig / n,
        "avg_sharp_book_count": s_sharp / n,
        "avg_market_width":          s_mw / n_mw if n_mw else None,
        "avg_consensus_disagreement": s_cd / n_cd if n_cd else None,
    }


def _iter_grid(grid: Dict[str, List[Any]]):
    for cm in grid["consensus_prob_min"]:
        for dm in grid["devig_book_count_min"]:
            for sm in grid["sharp_book_count_min"]:
                for mw in grid["market_width_max"]:
                    for cd in grid["consensus_disagreement_max"]:
                        yield cm, dm, sm, mw, cd


def _fmt(c: Dict[str, Any]) -> str:
    return (f"cons≥{c['consensus_prob_min']:>4.2f}  "
              f"devig≥{int(c['devig_book_count_min'])}  "
              f"sharp≥{int(c['sharp_book_count_min'])}  "
              f"mw≤{(c['market_width_max'] if c['market_width_max'] is not None else 'any'):<6}  "
              f"cd≤{(c['consensus_disagreement_max'] if c['consensus_disagreement_max'] is not None else 'any'):<6}  "
              f"n={c['n_bets']:>5}  "
              f"hit={(c['hit_rate'] or 0) * 100:>5.1f}%  "
              f"mkt={(c['consensus_prob_avg'] or 0) * 100:>5.1f}%  "
              f"Δ={(c['calibration_delta_consensus'] or 0) * 100:>+6.2f}pp")


async def _run(args: argparse.Namespace) -> int:
    # 2026-05-26 — try/finally around client.close() so the subprocess
    # exits cleanly even when an exception bubbles. Otherwise motor
    # background tasks keep `asyncio.run(_run())` alive → worker sees
    # status='running' forever → pipeline next-step never fires.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        return await _run_body(args, client[os.environ["DB_NAME"]])
    finally:
        client.close()


async def _run_body(args: argparse.Namespace, db) -> int:

    grid = dict(DEFAULT_GRID)
    if args.config:
        with open(args.config) as fh:
            grid.update(json.load(fh))

    excl = [s.strip() for s in (args.exclude_stat_family or "").split(",") if s.strip()]

    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    await db[RUNS].insert_one({
        "run_id": run_id,
        "version": VERSION,
        "methodology": METHODOLOGY,
        "params": {
            "league": args.league, "start": args.start, "end": args.end,
            "dataset": args.dataset,
            "exclude_stat_family": excl,
            "min_bets": args.min_bets,
            "grid": grid,
        },
        "status": "running",
        "started_at": started,
        "finished_at": None,
    })

    print("=" * 76)
    print(f"  GRID SWEEP v{VERSION}  ({METHODOLOGY})")
    print(f"  run_id={run_id}")
    print(f"  league={args.league}  window={args.start}…{args.end}  "
            f"min_bets={args.min_bets}")
    print(f"  exclude_stat_family={excl or '<none>'}")
    print("  axes:")
    for k, v in grid.items():
        print(f"    {k:<32} {v}")
    print(
        "  CALIBRATION DELTA Δ = hit_rate − consensus_prob_avg.\n"
        "  + Δ ⇒ filter found a pocket where the consensus market\n"
        "        UNDERESTIMATED true win rate (real signal).\n"
        "  Headline ROI vs PP odds is intentionally NOT computed:\n"
        "  PP is an availability/payout layer, not a pricing source.")
    print("=" * 76)

    rows = await _load_dataset(
        db, league=args.league, start=args.start, end=args.end,
        exclude_families=excl,
    )
    print(f"\n  eligible rows after join: {len(rows):,}")
    if not rows:
        await db[RUNS].update_one(
            {"run_id": run_id},
            {"$set": {"status": "succeeded_empty",
                        "finished_at": datetime.now(timezone.utc),
                        "n_eligible_rows": 0}})
        print(f"\n  run_id={run_id}  (empty)")
        return 0

    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_side:   Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_family[r.get("stat_family") or "_unknown"].append(r)
        by_side[(r.get("side") or "_unknown").upper()].append(r)

    # 2026-05-26 — STREAMING writes instead of accumulating all cells
    # in RAM. WHY: with 3,000 grid combos × ALL slice + ~22 fam slices
    # + 2 side slices = ~75,000 cells held in memory = ~75 MB just for
    # the cell lists. Combined with the ~150 MB row dataset, motor
    # pool buffers, and any concurrent /results aggregation, the
    # worker easily blew its 4 GiB rlimit and got SIGKILL'd. With
    # streaming, peak buffer is FLUSH_EVERY × cell_size (~1 MB)
    # regardless of how many combos × slices we run.
    n_total = 0
    qualified: List[Dict[str, Any]] = []
    # Per-family best (top 10 by Δ, top 10 by HR). We track running
    # winners on the fly so we don't need to keep the full fam_cells
    # list in memory.
    fam_best: Dict[str, Dict[str, Any]] = {}
    side_best: Dict[str, Dict[str, Any]] = {}
    # ALL-slice qualified cells we want for the top-10 / worst-10
    # reports. Bounded to ~200 — small enough to keep.
    FLUSH_EVERY = 1000
    write_buffer: List[Dict[str, Any]] = []

    async def _flush():
        if write_buffer and not args.dry_run:
            await db[RESULTS].insert_many(write_buffer, ordered=False)
        write_buffer.clear()

    def _stamp(cell: Dict[str, Any]) -> Dict[str, Any]:
        cell["run_id"] = run_id
        cell["version"] = VERSION
        cell["methodology"] = METHODOLOGY
        return cell

    for cm, dm, sm, mw, cd in _iter_grid(grid):
        n_total += 1
        # ── ALL slice ─────────────────────────────────────────
        all_cell = _stamp({"slice": "ALL",
                                 **_eval_cell(rows, cm, dm, sm, mw, cd)})
        write_buffer.append(all_cell)
        if (all_cell["n_bets"] or 0) >= args.min_bets:
            qualified.append(all_cell)
        # ── per stat_family ───────────────────────────────────
        for fam, fr in by_family.items():
            fam_cell = _stamp({"slice": "STAT_FAMILY", "stat_family": fam,
                                       **_eval_cell(fr, cm, dm, sm, mw, cd)})
            write_buffer.append(fam_cell)
            if (fam_cell["n_bets"] or 0) >= args.min_bets:
                d = fam_cell.get("calibration_delta_consensus")
                if d is not None:
                    if fam not in fam_best or d > (fam_best[fam].get("calibration_delta_consensus") or -1):
                        fam_best[fam] = fam_cell
        # ── per side ──────────────────────────────────────────
        for sd, sr in by_side.items():
            side_cell = _stamp({"slice": "SIDE", "side": sd,
                                        **_eval_cell(sr, cm, dm, sm, mw, cd)})
            write_buffer.append(side_cell)
            if (side_cell["n_bets"] or 0) >= args.min_bets:
                d = side_cell.get("calibration_delta_consensus")
                if d is not None:
                    if sd not in side_best or d > (side_best[sd].get("calibration_delta_consensus") or -1):
                        side_best[sd] = side_cell
        if len(write_buffer) >= FLUSH_EVERY:
            await _flush()
    await _flush()

    await db[RUNS].update_one(
        {"run_id": run_id},
        {"$set": {"status": "succeeded",
                    "finished_at": datetime.now(timezone.utc),
                    "n_eligible_rows": len(rows),
                    "n_cells_total": n_total,
                    "n_cells_qualified": len(qualified)}})

    print(f"\n  total cells: {n_total:,}   qualified (≥{args.min_bets} bets): "
            f"{len(qualified):,}")

    if qualified:
        print("\n  ── TOP 10 by CALIBRATION DELTA (Δ) ─────────────────────")
        for c in sorted(qualified,
                          key=lambda x: (x.get("calibration_delta_consensus") or -1),
                          reverse=True)[:10]:
            print(f"    {_fmt(c)}")

        print("\n  ── TOP 10 by HIT RATE ──────────────────────────────────")
        for c in sorted(qualified,
                          key=lambda x: (x.get("hit_rate") or -1),
                          reverse=True)[:10]:
            print(f"    {_fmt(c)}")

        print("\n  ── WORST 10 by CALIBRATION DELTA ───────────────────────")
        for c in sorted(qualified,
                          key=lambda x: (x.get("calibration_delta_consensus") or 1))[:10]:
            print(f"    {_fmt(c)}")
    else:
        print("\n  No cells met min_bets gate.")

    # `fam_best` / `side_best` are now tracked on the fly inside the
    # streaming sweep loop above (no longer need to scan `fam_cells`
    # or `side_cells` lists — they don't exist by design).
    if fam_best:
        print("\n  ── BEST CALIBRATION Δ PER STAT_FAMILY ──────────────────")
        for fam, c in sorted(
            fam_best.items(),
            key=lambda kv: (kv[1].get("calibration_delta_consensus") or -1),
            reverse=True):
            print(f"    {fam:<24} {_fmt(c)}")

    if side_best:
        print("\n  ── BEST CALIBRATION Δ PER SIDE ─────────────────────────")
        for sd, c in side_best.items():
            print(f"    {sd:<6} {_fmt(c)}")

    # Recommended candidates — high Δ AND volume ≥ max(min_bets, 100)
    stable = sorted(
        [c for c in qualified
          if (c["n_bets"] or 0) >= max(args.min_bets, 100)
          and (c.get("calibration_delta_consensus") or -1) > 0],
        key=lambda x: x["calibration_delta_consensus"],
        reverse=True,
    )[:3]
    if stable:
        print("\n  ── RECOMMENDED CANDIDATE CONFIGS (Δ>0, n≥100) ──────────")
        for i, c in enumerate(stable, 1):
            print(f"    #{i}  {_fmt(c)}")
        if not args.dry_run:
            await db["candidate_thresholds"].insert_many([
                {"run_id": run_id, "rank": i + 1,
                 "version": VERSION, "methodology": METHODOLOGY,
                 "params": {k: c[k] for k in (
                     "consensus_prob_min", "devig_book_count_min",
                     "sharp_book_count_min", "market_width_max",
                     "consensus_disagreement_max")},
                 "metrics": {k: c[k] for k in (
                     "n_bets", "hit_rate", "consensus_prob_avg",
                     "best_book_prob_avg",
                     "calibration_delta_consensus",
                     "calibration_delta_best_book")},
                 "created_at": datetime.now(timezone.utc),
                 "league": args.league}
                for i, c in enumerate(stable)
            ], ordered=False)
    else:
        print("\n  No stable Δ>0 cells found.")

    print(f"\n  saved run_id = {run_id}")
    print(f"  → research_grid_runs    (1 doc)")
    print(f"  → research_grid_results ({n_total:,} cells written)")
    if stable and not args.dry_run:
        print(f"  → candidate_thresholds  ({len(stable)} docs)")
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league", default="MLB")
    p.add_argument("--start",  required=True, help="YYYY-MM-DD")
    p.add_argument("--end",    required=True, help="YYYY-MM-DD")
    p.add_argument("--dataset", default=OUTCOMES,
                     help="(informational — driver is sgo_pp_research_outcomes)")
    p.add_argument("--exclude-stat-family", default="fantasy_score",
                     help="Comma-separated families to skip")
    p.add_argument("--min-bets", type=int, default=30,
                     help="Min bets per cell to be 'qualified'")
    p.add_argument("--config", default=None, help="JSON to override grid")
    p.add_argument("--dry-run", action="store_true",
                     help="Compute + report; do NOT write to Mongo")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
