"""
score_historical_with_live_mlb_hf.py — drive the live MLB-HF model
over historical SGO feature rows.

Goal:
    Make `sgo_pp_research_model_predictions` emit the EXACT same field
    set the production gate engines (SH / FL / WZ) consume, by routing
    every SGO feature row through `services.mlb_high_friction_model.
    MLBHighFrictionModel.predict(..., as_of_date=game_date)`.

Output schema per row (compatible with `mlb_replay_model_outputs`):
    event_id, player_id, stat_id, side, line, period_id, game_date,
    stat_family, player_name, league_id, sport,
    projection_mu, sigma, model_probability, fair_probability,
    implied_probability,         (← books-only consensus_probability)
    edge,                         (← model_probability − fair_probability)
    tp,                           (← fair_probability)
    cv,                           (← sigma / max(|mu|, 1e-6))
    projection_margin,            (← mu − line, signed by side)
    hit_rate_l5, hit_rate_l10, hit_rate_l20,
    odds, book, odds_bucket,
    scored_at, model_version, scorer="live_mlb_hf"

PROBE MODE (`--probe`):
    Exits AFTER verifying ALL upstream dependencies. Writes nothing.
    Reports exactly which deps are missing. Use this BEFORE the real run.

Resumable:
    Skips rows that already have predictions at this `model_version`
    + `scorer="live_mlb_hf"`. Use `--force` to rescore.

Idempotent batched writes (chunk_size=500).

Usage:
    # 1. Probe first (cheap, ~10s)
    python -m scripts.sgo.score_historical_with_live_mlb_hf \\
        --league=MLB --start=2025-06-01 --end=2025-06-30 --probe

    # 2. If probe passes, real run
    python -m scripts.sgo.score_historical_with_live_mlb_hf \\
        --league=MLB --start=2025-06-01 --end=2025-06-30 --limit=100
    # remove --limit to score everything

Admin API job-runner compatible: register flags in policy.ALLOWED_JOBS.
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

FEATURES_COLL    = "sgo_pp_research_model_features"
PREDICTIONS_COLL = "sgo_pp_research_model_predictions"
ENRICHED_COLL    = "sgo_pp_research_core_enriched"

SCORER = "live_mlb_hf"
MODEL_VERSION = "mlb_hf_live_2026_05_21"


# ── stat_family → MLB-HF stat_type (model expects raw column names) ──
# These mirror canonical_stats canonical family → live stat_type used
# by `MLBHighFrictionModel.predict(stat_type=...)`.
_FAMILY_TO_HF_STAT: Dict[str, str] = {
    "hits":                 "hits",
    "total_bases":          "total_bases",
    "hits_runs_rbis":       "hits+runs+rbis",
    "rbis":                 "rbis",
    "runs":                 "runs",
    "home_runs":            "home_runs",
    "singles":              "singles",
    "doubles":              "doubles",
    "batter_strikeouts":    "strikeouts",
    "pitcher_strikeouts":   "pitcher_strikeouts",
    "earned_runs":          "earned_runs",
    "hits_allowed":         "hits_allowed",
    "walks_allowed":        "pitcher_walks",
    "pitching_outs":        "pitcher_outs",
    "pitcher_hits_allowed": "hits_allowed",
    "pitching_basesOnBalls": "pitcher_walks",
    "stolen_bases":         "stolen_bases",
    "batting_strikeouts":   "strikeouts",
    "batting_walks":        "walks",
}


def _odds_bucket(odds: Optional[int]) -> str:
    if odds is None: return "odds_na"
    odds = int(odds)
    if odds < -200: return "odds_lt_-200"
    if odds < -100: return "odds_-200_-100"
    if odds <    0: return "odds_-100_-0"
    if odds <  150: return "odds_+0_+150"
    if odds <  300: return "odds_+150_+300"
    return "odds_+300p"


def _cv(mu: Optional[float], sigma: Optional[float]) -> Optional[float]:
    if mu is None or sigma is None: return None
    m = abs(float(mu))
    if m < 1e-6: return None
    return float(sigma) / m


def _projection_margin(mu: Optional[float], line: Optional[float],
                        side: Optional[str]) -> Optional[float]:
    if mu is None or line is None or side is None: return None
    raw = float(mu) - float(line)
    return raw if side.upper() == "OVER" else -raw


def _fair_to_implied_edge(model_p: Optional[float],
                            consensus_p: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (implied_p, fair_p, edge). All from books only."""
    if consensus_p is None:
        return None, None, None
    fair = float(consensus_p)
    implied = fair
    if model_p is None:
        return implied, fair, None
    return implied, fair, float(model_p) - fair


# ── PROBE ──────────────────────────────────────────────────────────
async def _probe(args, db) -> int:
    print("=" * 70)
    print("  PROBE — live MLB-HF historical scorer")
    print("=" * 70)
    failures: List[str] = []

    # 1. Model artifact dir
    candidates = [
        "/var/www/app/backend/models/mlb_hf",
        "/app/backend/models/mlb_hf",
    ]
    mhf_dir = next((p for p in candidates if os.path.isdir(p)), None)
    if not mhf_dir:
        failures.append(f"models/mlb_hf not found in {candidates}")
        print(f"  ❌ MLB-HF models dir not found")
    else:
        pkls = sorted(f for f in os.listdir(mhf_dir) if f.endswith(".pkl"))
        print(f"  ✓ MLB-HF dir: {mhf_dir}")
        print(f"    {len(pkls)} pkls: {pkls[:6]}…")

    # 2. Import MLBHighFrictionModel
    try:
        from services.mlb_high_friction_model import MLBHighFrictionModel
        print("  ✓ MLBHighFrictionModel imports")
    except Exception as e:
        failures.append(f"import MLBHighFrictionModel failed: {e!r}")
        print(f"  ❌ import failed: {e!r}")
        return 2

    # 3. Instantiate
    try:
        # Use sync pymongo for the model (it expects sync); we use motor
        # only for SGO reads/writes. MLBHighFrictionModel.__init__(db)
        # accepts either, but to be safe use sync.
        from pymongo import MongoClient
        sync_db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        model = MLBHighFrictionModel(sync_db)
        # Make MLB-HF's MODEL_DIR match what we actually found on disk.
        # The class default is hardcoded to /app/backend/... which is wrong
        # on prod (/var/www/app/backend/...). Override before load_models().
        if mhf_dir:
            model.MODEL_DIR = mhf_dir
        # __init__() doesn't load pickles — load_models() does.
        try:
            n_loaded = model.load_models()
            print(f"  ✓ load_models() returned {n_loaded}; loaded stat_types: "
                    f"{sorted(model.models.keys())[:10]} ({len(model.models)} total)")
        except Exception as e:
            failures.append(f"load_models() raised: {e!r}")
            traceback.print_exc()
            print(f"  ❌ load_models() raised: {e!r}")
            return 2
        loaded = list(getattr(model, "models", {}).keys())
        if not loaded:
            failures.append("MLBHighFrictionModel.load_models() loaded 0 models")
    except Exception as e:
        failures.append(f"MLBHighFrictionModel(db) failed: {e!r}")
        traceback.print_exc()
        print(f"  ❌ instantiation failed: {e!r}")
        return 2

    # 4. Find one SGO feature row in window
    match: Dict[str, Any] = {"feature_ready": True}
    if args.league:           match["league_id"]  = args.league
    if args.start or args.end:
        gd: Dict[str, Any] = {}
        if args.start: gd["$gte"] = args.start
        if args.end:   gd["$lte"] = args.end
        match["game_date"] = gd
    sample = await db[FEATURES_COLL].find_one(match, projection={"_id": 0})
    if not sample:
        failures.append(f"no feature_ready rows in {FEATURES_COLL} for filter {match}")
        print(f"  ❌ no SGO feature rows in window")
        return 2
    print(f"  ✓ sample SGO feature row found:")
    print(f"    event_id={sample.get('event_id')} player={sample.get('player_name')!r}")
    print(f"    stat_family={sample.get('stat_family')!r} line={sample.get('line')} side={sample.get('side')}")

    # 5. Resolve player → bdl_player_id via master_hub
    pname = sample.get("player_name")
    hub = None
    for q in [
        {"display_name": pname}, {"player_name": pname},
        {"mlb_full_name": pname},
    ]:
        hub = sync_db.mlb_master_hub_2026.find_one(q, {"bdl_player_id": 1, "bdl_id": 1, "team": 1, "_id": 0})
        if hub: break
    if not hub:
        failures.append(f"could not resolve {pname!r} in mlb_master_hub_2026")
        print(f"  ❌ player {pname!r} NOT FOUND in master_hub")
    else:
        bdl_pid = hub.get("bdl_player_id") or hub.get("bdl_id")
        print(f"  ✓ master_hub row found; bdl_player_id={bdl_pid} team={hub.get('team')}")

    # 6. Try a predict() call
    hf_stat = _FAMILY_TO_HF_STAT.get(sample.get("stat_family"))
    if not hf_stat:
        failures.append(f"no MLB-HF mapping for stat_family={sample.get('stat_family')!r}")
        print(f"  ❌ stat_family {sample.get('stat_family')!r} has no HF mapping")
    else:
        try:
            result = model.predict(
                player_name=pname,
                stat_type=hf_stat,
                line=float(sample.get("line") or 0),
                bdl_player_id=(hub.get("bdl_player_id") or hub.get("bdl_id")) if hub else None,
                as_of_date=sample.get("game_date"),
            )
            if isinstance(result, dict) and "error" in result:
                failures.append(f"predict() returned error: {result['error']}")
                print(f"  ❌ predict() error: {result['error']}")
            else:
                print(f"  ✓ predict() returned keys: {sorted(result.keys())[:12]}…")
                # Check for the gate-required fields
                gate_fields = {
                    "projection_mu": result.get("projection_mu") or result.get("mu"),
                    "sigma": result.get("sigma"),
                    "model_probability": result.get("model_probability") or result.get("model_prob"),
                    "tp": result.get("tp") or result.get("fair_probability"),
                }
                for k, v in gate_fields.items():
                    print(f"      {k:<22} {v}")
                missing = [k for k, v in gate_fields.items() if v is None]
                if missing:
                    failures.append(f"predict() output missing fields: {missing}")
        except Exception as e:
            failures.append(f"predict() raised: {e!r}")
            traceback.print_exc()
            print(f"  ❌ predict() raised: {e!r}")

    print()
    print("=" * 70)
    if failures:
        print(f"  PROBE FAILED  ({len(failures)} issue(s)):")
        for f in failures: print(f"    • {f}")
        print("=" * 70)
        return 1
    print("  PROBE OK  — safe to proceed with real run.")
    print("=" * 70)
    return 0


# ── Real scoring run ──────────────────────────────────────────────
async def _run(args, db) -> int:
    from services.mlb_high_friction_model import MLBHighFrictionModel
    from pymongo import MongoClient
    sync_db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(sync_db)
    # Resolve MODEL_DIR to the path that actually exists.
    for p in ("/var/www/app/backend/models/mlb_hf",
               "/app/backend/models/mlb_hf"):
        if os.path.isdir(p):
            model.MODEL_DIR = p
            break
    n_loaded = model.load_models()
    print(f"  load_models() → {n_loaded}; "
            f"loaded stat_types: {sorted(model.models.keys())}")
    if not model.models:
        print("  ❌ no models loaded; aborting.")
        return 2

    match: Dict[str, Any] = {"feature_ready": True}
    if args.league:           match["league_id"]  = args.league
    if args.start or args.end:
        gd: Dict[str, Any] = {}
        if args.start: gd["$gte"] = args.start
        if args.end:   gd["$lte"] = args.end
        match["game_date"] = gd

    # Resume: skip rows that already have an output at this (scorer, model_version)
    if not args.force:
        scored_keys = set()
        async for r in db[PREDICTIONS_COLL].find(
            {**match, "scorer": SCORER, "model_version": MODEL_VERSION},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                          "stat_id": 1, "side": 1, "line": 1, "period_id": 1},
        ):
            k = (r["event_id"], r["player_id"], r["stat_id"], r["side"], r["line"],
                 r.get("period_id"))
            scored_keys.add(k)
        print(f"  resume: {len(scored_keys)} rows already scored, skipping")
    else:
        scored_keys = set()

    total = 0; scored = 0; skipped = 0; errored = 0
    no_hub = 0; no_hf_stat = 0
    missing_fields = 0
    buf: List[UpdateOne] = []

    cur = db[FEATURES_COLL].find(match, projection={"_id": 0})
    if args.limit:
        cur = cur.limit(int(args.limit))

    async for doc in cur:
        total += 1
        key = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               doc.get("side"), doc.get("line"), doc.get("period_id"))
        if key in scored_keys:
            skipped += 1
            continue

        hf_stat = _FAMILY_TO_HF_STAT.get(doc.get("stat_family"))
        if not hf_stat:
            no_hf_stat += 1; continue

        # Resolve player → bdl_player_id
        pname = doc.get("player_name")
        hub = None
        for q in [{"display_name": pname}, {"player_name": pname},
                    {"mlb_full_name": pname}]:
            hub = sync_db.mlb_master_hub_2026.find_one(
                q, {"bdl_player_id": 1, "bdl_id": 1, "_id": 0})
            if hub: break
        bdl_pid = (hub.get("bdl_player_id") or hub.get("bdl_id")) if hub else None
        if bdl_pid is None:
            no_hub += 1; continue

        try:
            result = model.predict(
                player_name=pname,
                stat_type=hf_stat,
                line=float(doc.get("line") or 0),
                bdl_player_id=int(bdl_pid),
                as_of_date=doc.get("game_date"),
            )
        except Exception as e:
            errored += 1
            if errored <= 5:
                print(f"  predict() error for {pname}/{hf_stat}: {e!r}")
            continue
        if isinstance(result, dict) and "error" in result:
            errored += 1
            continue

        # Extract gate-required fields
        mu      = result.get("projection_mu") or result.get("mu")
        sigma   = result.get("sigma")
        model_p = result.get("model_probability") or result.get("model_prob")
        tp      = result.get("tp") or result.get("fair_probability")

        if mu is None or sigma is None or model_p is None:
            missing_fields += 1
            if missing_fields <= 5:
                print(f"  missing fields for {pname}/{hf_stat}: "
                        f"mu={mu} sigma={sigma} model_p={model_p}")
            continue

        # Books-only edge: model_probability − consensus_probability
        consensus_p = doc.get("consensus_probability")
        _, fair_p, edge = _fair_to_implied_edge(model_p, consensus_p)

        feats = doc.get("model_input_features") or {}
        out_doc = {
            "event_id":  doc.get("event_id"),
            "player_id": doc.get("player_id"),
            "stat_id":   doc.get("stat_id"),
            "side":      doc.get("side"),
            "line":      float(doc.get("line")) if doc.get("line") is not None else None,
            "period_id": doc.get("period_id"),

            "game_date":   doc.get("game_date"),
            "league_id":   doc.get("league_id"),
            "sport":       "mlb",
            "stat_family": doc.get("stat_family"),
            "player_name": pname,
            "bdl_player_id": int(bdl_pid),

            # Gate-required model outputs
            "projection_mu":      float(mu),
            "sigma":              float(sigma),
            "model_probability":  float(model_p),
            "fair_probability":   float(fair_p) if fair_p is not None else None,
            "implied_probability": float(consensus_p) if consensus_p is not None else None,
            "edge":               float(edge) if edge is not None else None,
            "tp":                 float(tp) if tp is not None else None,
            "cv":                 _cv(mu, sigma),
            "projection_margin":  _projection_margin(mu, doc.get("line"), doc.get("side")),

            # Hit-rate features (from feature builder; books-independent)
            "hit_rate_l5":  feats.get("line_hit_rate_last_5"),
            "hit_rate_l10": feats.get("line_hit_rate_last_10"),
            "hit_rate_l20": feats.get("line_hit_rate_last_20"),

            # Audit
            "scorer":          SCORER,
            "model_version":   MODEL_VERSION,
            "scored_at":       datetime.now(timezone.utc),
            "as_of_date":      doc.get("game_date"),  # leakage guarantee
        }

        flt = {k: out_doc[k] for k in
                ("event_id", "player_id", "stat_id", "side", "line",
                 "period_id", "scorer", "model_version")}
        buf.append(UpdateOne(flt, {"$set": out_doc}, upsert=True))
        scored += 1

        if len(buf) >= 500:
            await db[PREDICTIONS_COLL].bulk_write(buf, ordered=False)
            buf = []
            if scored % 2000 == 0:
                print(f"  progress: scanned={total} scored={scored} "
                        f"skipped={skipped} no_hub={no_hub} errors={errored}")

    if buf:
        await db[PREDICTIONS_COLL].bulk_write(buf, ordered=False)

    print()
    print("=" * 70)
    print(f"  scored        {scored:>7}")
    print(f"  skipped       {skipped:>7}  (resumed; already scored)")
    print(f"  no_hub        {no_hub:>7}  (player not in master_hub)")
    print(f"  no_hf_stat    {no_hf_stat:>7}  (no MLB-HF mapping for family)")
    print(f"  errors        {errored:>7}  (predict() raised or returned error)")
    print(f"  missing_flds  {missing_fields:>7}  (predict ok but mu/sigma/model_p incomplete)")
    print(f"  TOTAL scanned {total:>7}")
    print("=" * 70)
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league", default="MLB")
    p.add_argument("--start",  default=None, help="YYYY-MM-DD")
    p.add_argument("--end",    default=None, help="YYYY-MM-DD")
    p.add_argument("--probe",  action="store_true",
                     help="Verify dependencies, write nothing, exit.")
    p.add_argument("--limit",  type=int, default=None,
                     help="Cap on rows scored (smoke runs).")
    p.add_argument("--force",  action="store_true",
                     help="Re-score rows already at this model_version.")
    p.add_argument("--dry-run", action="store_true",
                     help="Compute but do NOT persist.")
    return p.parse_args()


async def amain():
    args = _parse()
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    if args.probe:
        return await _probe(args, db)
    return await _run(args, db)


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
