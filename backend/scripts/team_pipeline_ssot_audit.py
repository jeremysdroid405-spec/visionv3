"""
team_pipeline_ssot_audit.py — SSOT audit for the cross-sport team-prop
pipeline (MLB / NBA / NFL).

For each sport, checks every stage of the team pipeline:

    ingest         → team_live_props (live odds for that sport)
    features       → team_model_features (rolling priors)
    outcomes       → team_historical_outcomes (graded truth)
    feature_cache  → team_model_prop_features (joined features+outcomes,
                       the reshape source)
    score adapter  → trained XGB artifacts under
                       /app/backend/models/team_xgb/{sport}/
                       {h2h|spread|game_total|team_total}.pkl
                       (the live + replay scorer dispatches by
                        (sport, market_category) here)
    reshape        → sgo_propvision_full_pipeline_replay rows with
                       prop_type='team' AND sport={sport} (output of
                       scripts.sgo.reshape_team_props_to_replay)
    replay adapter → same rows must carry model_probability +
                       model_version='team_xgb_v1' + pipeline_version=
                       'team_v1_scored'  (i.e. the replay/optimizer
                        sees scored rows, not bare odds)
    grid           → optimizer_runs containing
                       request.prop_type='team' AND request.sport=
                       {sport.upper()} with status=succeeded

Output:
  ┌──────────┬────────┬──────────┬──────────┬───────────────┬──────┬──────────┬────────┬──────┐
  │ Sport    │ Ingest │ Features │ Outcomes │ Feature Cache │ Score│ Reshape  │ Replay │ Grid │
  ├──────────┼────────┼──────────┼──────────┼───────────────┼──────┼──────────┼────────┼──────┤
  │ MLB      │   48   │   6,057  │  879,931 │     825,794   │  ✓   │  825,794 │ 825,794│   ✓  │
  │ NBA      │   30   │   3,283  │  413,454 │     391,851   │  ✓   │  391,851 │ 391,851│   ✓  │
  │ NFL      │   0    │   1,212  │  124,287 │     115,819   │  ✓   │  115,819 │ 115,819│   ✓  │
  └──────────┴────────┴──────────┴──────────┴───────────────┴──────┴──────────┴────────┴──────┘

A `✓` means the stage is fully populated/operational for that sport.
A `✗` means the stage is empty or the adapter artifact is missing.
A numeric value means the row count for that stage.

Exit code:
  0  if every sport with non-zero feature_cache also has score, reshape,
     replay, grid all ✓ (i.e. no late-stage gap).
  1  if any sport with data is missing a downstream adapter.

Usage:
  python -m scripts.team_pipeline_ssot_audit
  python -m scripts.team_pipeline_ssot_audit --json     # machine-readable
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient


SPORTS = ("mlb", "nba", "nfl")
MARKET_CATEGORIES = ("h2h", "spread", "game_total", "team_total")
ARTIFACT_ROOT = Path("/app/backend/models/team_xgb")
REPLAY_COLL = "sgo_propvision_full_pipeline_replay"


async def _count(db, coll: str, flt: Dict[str, Any]) -> int:
    try:
        return await db[coll].count_documents(flt)
    except Exception:
        return -1


def _score_adapter_present(sport: str) -> Dict[str, bool]:
    """Per-market-category artifact existence check."""
    return {
        mc: (ARTIFACT_ROOT / sport / f"{mc}.pkl").exists()
        for mc in MARKET_CATEGORIES
    }


async def _audit_sport(db, sport: str) -> Dict[str, Any]:
    """Compute every stage's count + adapter status for one sport."""
    league = sport.upper()
    ingest = await _count(db, "team_live_props", {"sport": sport})
    features = await _count(db, "team_model_features", {"sport": sport})
    outcomes = await _count(db, "team_historical_outcomes",
                              {"sport": sport})
    feature_cache = await _count(db, "team_model_prop_features",
                                   {"sport": sport})
    reshape = await _count(
        db, REPLAY_COLL, {"prop_type": "team", "sport": sport})
    replay_scored = await _count(
        db, REPLAY_COLL,
        {"prop_type": "team", "sport": sport,
         "model_probability": {"$ne": None},
         "model_version": "team_xgb_v1"})
    grid_ok = (await _count(
        db, "optimizer_runs",
        {"request.prop_type": "team",
         "request.sport": league,
         "status": "succeeded"})) > 0
    adapter = _score_adapter_present(sport)
    score_ok = all(adapter.values())
    return {
        "sport": sport,
        "league": league,
        "ingest": ingest,
        "features": features,
        "outcomes": outcomes,
        "feature_cache": feature_cache,
        "score_adapter": adapter,
        "score_ok": score_ok,
        "reshape": reshape,
        "replay_scored": replay_scored,
        "replay_ok": (reshape > 0 and replay_scored == reshape),
        "grid_ok": grid_ok,
    }


def _glyph(passed: bool) -> str:
    return "✓" if passed else "✗"


def _gap_check(rec: Dict[str, Any]) -> List[str]:
    """Return a list of gap names for this sport. A sport with a
    populated feature_cache MUST have score+reshape+replay+grid all OK.
    A sport with zero feature_cache is treated as "not in scope" and
    contributes no gaps."""
    if rec["feature_cache"] <= 0:
        return []
    gaps = []
    if not rec["score_ok"]:
        missing = [mc for mc, ok in rec["score_adapter"].items() if not ok]
        gaps.append(f"score (missing artifacts: {missing})")
    if rec["reshape"] <= 0:
        gaps.append("reshape (no replay rows)")
    elif not rec["replay_ok"]:
        gaps.append(
            f"replay (only {rec['replay_scored']:,}/"
            f"{rec['reshape']:,} replay rows carry model_probability + "
            f"model_version=team_xgb_v1)")
    if not rec["grid_ok"]:
        gaps.append("grid (no succeeded optimizer run for "
                     "(prop_type=team, sport={}))".format(rec["league"]))
    return gaps


def _print_table(records: List[Dict[str, Any]]) -> None:
    head = (f"{'Sport':<6} {'Ingest':>8} {'Features':>10} {'Outcomes':>10} "
            f"{'Feat Cache':>11} {'Score':>6} {'Reshape':>10} "
            f"{'Replay':>10} {'Grid':>6}")
    print("═" * len(head))
    print("TEAM PIPELINE — SSOT AUDIT  (per /app/memory/TEAM_PROPS_ARCHITECTURE.md §14)")
    print("═" * len(head))
    print(head)
    print("─" * len(head))
    for r in records:
        score = _glyph(r["score_ok"])
        replay = _glyph(r["replay_ok"])
        grid = _glyph(r["grid_ok"])
        print(f"{r['league']:<6} "
              f"{r['ingest']:>8,} "
              f"{r['features']:>10,} "
              f"{r['outcomes']:>10,} "
              f"{r['feature_cache']:>11,} "
              f"{score:>6} "
              f"{r['reshape']:>10,} "
              f"{r['replay_scored']:>10,} "
              f"{grid:>6}")
    print("─" * len(head))
    print("Adapter detail (score):")
    for r in records:
        a = r["score_adapter"]
        parts = [f"{mc}={_glyph(ok)}" for mc, ok in a.items()]
        print(f"  {r['league']:<5} {'  '.join(parts)}")
    print()
    any_gaps = False
    for r in records:
        gaps = _gap_check(r)
        if gaps:
            any_gaps = True
            print(f"GAP  {r['league']}:")
            for g in gaps:
                print(f"  - {g}")
    if not any_gaps:
        print("OK  Every sport with feature_cache>0 has full downstream "
               "(score + reshape + replay + grid).")
    print("═" * len(head))


async def amain(args: argparse.Namespace) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    records = [await _audit_sport(db, sp) for sp in SPORTS]
    any_gaps = any(_gap_check(r) for r in records)

    if args.json:
        print(json.dumps(records, indent=2, default=str))
    else:
        _print_table(records)
    return 1 if any_gaps else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of a table")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
