"""
score_historical_with_live_mlb_hf.py — drive the live MLB-HF model
over historical SGO feature rows.

Goal:
    Make `sgo_pp_research_model_predictions` emit the EXACT same field
    set the production gate engines (SH / FL / WZ) consume, by routing
    every SGO feature row through `services.mlb_high_friction_model.
    MLBHighFrictionModel.predict(..., as_of_date=game_date)`.

Live model output contract (verified 2026-05-23 against
services/mlb_high_friction_model.py):
    predicted     — μ (float, projection)
    std_dev       — σ (float)
    prob_over     — model probability OVER, **percentage 0-100**
    line          — echo-back of input line
    error         — present only on failure (dict short-circuited)

This scorer normalises the live keys → the historical schema fields:
    projection_mu     ← predicted
    sigma             ← std_dev
    model_probability ← prob_over / 100.0    (NOTE: scale conversion)

Historical/replay schema (`mlb_replay_model_outputs`-compatible):
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

STRICT MODE (`--strict-min-scored-ratio R`):
    Exit non-zero when scored/(scanned - skipped) < R. Use 0.30 to fail
    fast on contract drift (vs the historical 0.0 case).

SAMPLE MODE (`--dump-predictions N`):
    Dump the FIRST N raw predict() return dicts so we can eyeball the
    live model contract without rerunning the whole sweep.

Resumable:
    Skips rows that already have predictions at this `model_version`
    + `scorer="live_mlb_hf"`. Use `--force` to rescore.

Idempotent batched writes (chunk_size=500).

Usage:
    # 1. Probe first (cheap, ~10s)
    python -m scripts.sgo.score_historical_with_live_mlb_hf \\
        --league=MLB --start=2025-06-01 --end=2025-06-30 --probe

    # 2. If probe passes, real run with hard failure on contract drift
    python -m scripts.sgo.score_historical_with_live_mlb_hf \\
        --league=MLB --start=2025-06-01 --end=2025-06-30 \\
        --limit=100 --strict-min-scored-ratio=0.30 --dump-predictions=3
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


# ── Live-model output contract ────────────────────────────────────────
# Verified against services/mlb_high_friction_model.py:1809-1832 (model
# path) and :1461-1481 (analytical pitcher_outs path). Both paths return:
#     predicted   — μ (float)
#     std_dev     — σ (float)
#     prob_over   — model probability OVER, **0-100 percentage**
# These are the ONLY keys we should read for μ/σ/model_p. Historical
# field names (`projection_mu`, `sigma`, `model_probability`) DO NOT
# exist on the live response.
_LIVE_MU_KEY     = "predicted"
_LIVE_SIGMA_KEY  = "std_dev"
_LIVE_PROB_KEY   = "prob_over"   # percentage, 0-100


def _extract_live_outputs(
    result: Dict[str, Any],
    side: Optional[str],
) -> Tuple[Optional[float], Optional[float], Optional[float],
            List[str]]:
    """Pull μ, σ, model_p (0-1) off a live MLBHighFrictionModel.predict()
    return dict and report which fields are missing. `prob_over` is
    converted from percentage to probability and flipped for UNDER bets
    (model_p_UNDER = 1 - model_p_OVER).

    Returns (mu, sigma, model_p, missing_field_names).
    """
    missing: List[str] = []
    mu_raw = result.get(_LIVE_MU_KEY)
    if mu_raw is None:
        missing.append(_LIVE_MU_KEY)
    sigma_raw = result.get(_LIVE_SIGMA_KEY)
    if sigma_raw is None or (isinstance(sigma_raw, (int, float)) and sigma_raw <= 0):
        # σ=0 is unusable downstream (CV blows up); treat as missing.
        missing.append(_LIVE_SIGMA_KEY if sigma_raw is None else f"{_LIVE_SIGMA_KEY}(non-positive)")
    prob_over_raw = result.get(_LIVE_PROB_KEY)
    if prob_over_raw is None:
        missing.append(_LIVE_PROB_KEY)
    try:
        mu_f      = float(mu_raw)    if mu_raw    is not None else None
        sigma_f   = float(sigma_raw) if sigma_raw is not None and sigma_raw > 0 else None
        prob_over = float(prob_over_raw) / 100.0 if prob_over_raw is not None else None
    except (TypeError, ValueError):
        return None, None, None, missing + ["non-numeric"]

    if prob_over is not None:
        # Clamp out-of-range model output (the live path rounds to 0.1
        # so 50.0 ≈ 0.500 is common; we accept the full [0,1] window).
        if prob_over < 0.0: prob_over = 0.0
        if prob_over > 1.0: prob_over = 1.0
        # Flip to UNDER probability when side==UNDER.
        if (side or "").upper() == "UNDER":
            model_p = 1.0 - prob_over
        else:
            model_p = prob_over
    else:
        model_p = None
    return mu_f, sigma_f, model_p, missing


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
                # ── Live model contract validation ─────────────────
                mu, sigma, model_p, missing = _extract_live_outputs(
                    result, sample.get("side"))
                print(f"      predicted (μ)        {result.get(_LIVE_MU_KEY)}")
                print(f"      std_dev    (σ)        {result.get(_LIVE_SIGMA_KEY)}")
                print(f"      prob_over  (0-100)    {result.get(_LIVE_PROB_KEY)}")
                print(f"      → normalised: mu={mu} sigma={sigma} model_p={model_p}")
                if missing:
                    failures.append(f"predict() output missing/invalid: {missing}")
                    print(f"  ❌ missing/invalid: {missing}")
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

    # Diagnostic tracking — per stat_family counters and a small sample
    # of raw predict() return dicts so contract drift is obvious.
    from collections import Counter, defaultdict
    fam_scored:  Counter = Counter()
    fam_missing: Counter = Counter()
    fam_errored: Counter = Counter()
    fam_no_hub:  Counter = Counter()
    fam_no_hf:   Counter = Counter()
    missing_fields_seen: Counter = Counter()   # which fields were absent
    error_messages_seen: Counter = Counter()   # bucket by error string
    sample_predictions: List[Dict[str, Any]] = []
    dump_predictions = int(getattr(args, "dump_predictions", 0) or 0)
    missing_field_examples: List[Dict[str, Any]] = []   # cap at 10
    error_examples: List[Dict[str, Any]] = []           # cap at 10

    cur = db[FEATURES_COLL].find(match, projection={"_id": 0})
    if args.limit:
        cur = cur.limit(int(args.limit))

    async for doc in cur:
        total += 1
        fam = doc.get("stat_family")
        key = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               doc.get("side"), doc.get("line"), doc.get("period_id"))
        if key in scored_keys:
            skipped += 1
            continue

        hf_stat = _FAMILY_TO_HF_STAT.get(fam)
        if not hf_stat:
            no_hf_stat += 1; fam_no_hf[fam] += 1; continue

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
            no_hub += 1; fam_no_hub[fam] += 1; continue

        try:
            result = model.predict(
                player_name=pname,
                stat_type=hf_stat,
                line=float(doc.get("line") or 0),
                bdl_player_id=int(bdl_pid),
                as_of_date=doc.get("game_date"),
            )
        except Exception as e:
            errored += 1; fam_errored[fam] += 1
            err_str = f"{type(e).__name__}: {e}"[:120]
            error_messages_seen[err_str] += 1
            if errored <= 5:
                print(f"  predict() raised for {pname}/{hf_stat}: {e!r}")
                error_examples.append({"player": pname, "stat_family": fam,
                                            "hf_stat": hf_stat, "exception": err_str})
            elif errored == 6:
                print("  (further predict() exceptions suppressed; see counts)")
            continue
        if isinstance(result, dict) and "error" in result:
            errored += 1; fam_errored[fam] += 1
            err_str = str(result.get("error"))[:120]
            error_messages_seen[err_str] += 1
            if errored <= 10:
                print(f"  predict() error for {pname}/{hf_stat}: {err_str!r}")
                error_examples.append({"player": pname, "stat_family": fam,
                                            "hf_stat": hf_stat, "error": err_str})
            elif errored == 11:
                print("  (further predict() error-dicts suppressed)")
            continue

        # Dump first N raw predictions to verify contract.
        if len(sample_predictions) < dump_predictions:
            sample_predictions.append({
                "player_name": pname, "stat_family": fam, "hf_stat": hf_stat,
                "side": doc.get("side"), "line": doc.get("line"),
                "result_keys": sorted(result.keys()),
                "predicted":     result.get(_LIVE_MU_KEY),
                "std_dev":       result.get(_LIVE_SIGMA_KEY),
                "prob_over":     result.get(_LIVE_PROB_KEY),
                "model_version": result.get("model_version"),
            })

        # Live → historical schema normalisation (see _extract_live_outputs).
        mu, sigma, model_p, missing = _extract_live_outputs(
            result, doc.get("side"))
        if missing:
            missing_fields += 1; fam_missing[fam] += 1
            for f in missing:
                missing_fields_seen[f] += 1
            if missing_fields <= 10:
                print(f"  missing fields for {pname}/{hf_stat} side={doc.get('side')}: "
                        f"absent={missing} got={{predicted={result.get(_LIVE_MU_KEY)}, "
                        f"std_dev={result.get(_LIVE_SIGMA_KEY)}, "
                        f"prob_over={result.get(_LIVE_PROB_KEY)}}}")
                missing_field_examples.append({
                    "player": pname, "stat_family": fam, "hf_stat": hf_stat,
                    "side": doc.get("side"),
                    "missing": missing,
                    "raw_keys": sorted(result.keys())[:20],
                })
            elif missing_fields == 11:
                print("  (further missing-field reports suppressed; "
                        "see per-family breakdown at end)")
            continue
        # Defensive belt-and-suspenders: by the time we get here mu, sigma,
        # model_p must all be non-None numerics. Any drift here is a bug.
        if mu is None or sigma is None or model_p is None:
            missing_fields += 1; fam_missing[fam] += 1
            continue

        tp_val  = result.get("tp") or result.get("fair_probability")

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
            "tp":                 float(tp_val) if tp_val is not None else None,
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
        fam_scored[fam] += 1

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

    # ── Diagnostic tables ─────────────────────────────────────────
    eligible = max(1, total - skipped)
    score_ratio = scored / eligible

    if missing_fields_seen:
        print()
        print("  MISSING-FIELD BREAKDOWN  (which output keys were absent on the live response):")
        for k, n in missing_fields_seen.most_common():
            print(f"    {k:<30} {n:>7}")
    if error_messages_seen:
        print()
        print("  ERROR MESSAGE BREAKDOWN  (top 10):")
        for m, n in error_messages_seen.most_common(10):
            print(f"    {n:>5} × {m}")
    fams = sorted(set(fam_scored) | set(fam_missing) | set(fam_errored)
                       | set(fam_no_hub) | set(fam_no_hf))
    if fams:
        print()
        print("  PER STAT_FAMILY  (scored / missing / errored / no_hub / no_hf):")
        print(f"  {'stat_family':<26} {'scored':>7} {'missing':>8} {'errored':>8} {'no_hub':>7} {'no_hf':>6}")
        for f in fams:
            print(f"  {str(f):<26} {fam_scored[f]:>7} {fam_missing[f]:>8} "
                    f"{fam_errored[f]:>8} {fam_no_hub[f]:>7} {fam_no_hf[f]:>6}")

    if sample_predictions:
        print()
        print(f"  SAMPLE RAW PREDICTIONS  (first {len(sample_predictions)}):")
        for i, s in enumerate(sample_predictions, 1):
            print(f"    [{i}] {s['player_name']!r:>24} {s['stat_family']:<22} "
                    f"side={s['side']:<5} line={s['line']!s:<6} → "
                    f"predicted={s['predicted']} std_dev={s['std_dev']} "
                    f"prob_over={s['prob_over']} (keys: {s['result_keys'][:8]}…)")

    print()
    print(f"  ELIGIBLE rows (scanned − skipped) = {eligible}")
    print(f"  SCORE RATIO                     = {score_ratio:.3f}")
    strict_min = float(getattr(args, "strict_min_scored_ratio", 0.0) or 0.0)
    if strict_min > 0.0 and score_ratio < strict_min:
        print()
        print("=" * 70)
        print(f"  ❌ STRICT MODE FAILURE: score_ratio {score_ratio:.3f} "
                f"< {strict_min:.3f} threshold.")
        print(f"     Most failures: missing={missing_fields} errors={errored} "
                f"no_hub={no_hub}")
        if missing_field_examples:
            print(f"     First missing-field examples:")
            for ex in missing_field_examples[:5]:
                print(f"       • {ex}")
        if error_examples:
            print(f"     First error examples:")
            for ex in error_examples[:5]:
                print(f"       • {ex}")
        print("=" * 70)
        return 3
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
    # ── Diagnostic / hard-fail flags (2026-05-23 contract-drift fix) ──
    p.add_argument("--strict-min-scored-ratio", type=float, default=0.0,
                     help="Exit non-zero when scored/(scanned−skipped) < R. "
                          "Catches contract drift early. Try 0.30 for sweeps.")
    p.add_argument("--dump-predictions", type=int, default=0,
                     help="Dump the first N raw predict() return dicts so "
                          "the live-model contract is visible in the log.")
    return p.parse_args()


async def amain():
    args = _parse()
    # 2026-05-26 — open client separately + close in finally so the
    # subprocess can exit cleanly. See
    # historical_full_pipeline_replay.py for write-up.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        if args.probe:
            return await _probe(args, db)
        return await _run(args, db)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
