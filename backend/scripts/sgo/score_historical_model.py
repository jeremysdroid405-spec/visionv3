"""
score_historical_model.py — run a model against sgo_pp_research_model_features.

Reads:   sgo_pp_research_model_features  (immutable)
Writes:  sgo_pp_research_model_predictions

Model loading (pluggable):
    --model-path /path/to/model.joblib    (joblib first, falls back to pickle)
    OR
    --model-entrypoint backend.live.model:predict_fn
        where predict_fn(features_dict: dict) -> float in [0, 1]
        (interpreted as P(prop hits given side, line))

When --model-path is used we expect an estimator with `.predict_proba(X)` that
returns shape (n, 2) with the positive class at column 1. If `.predict_proba`
is missing we fall back to `.predict(X)` and clip to [0,1].

For a model trained on a specific feature vector, you can also pass
    --feature-keys feat_a,feat_b,feat_c
to control the column order of X. If omitted, we use the alphabetical order
of the union of keys observed in `model_input_features` for the first 1000
feature_ready rows (and print the inferred order).

Output fields (per prediction):
    model_probability       float   P(prop hits)
    model_edge_vs_pp        float   model_probability − pp_implied_probability
    model_edge_vs_consensus float   model_probability − consensus_probability
    predicted_outcome       "WIN"|"LOSS"  (based on model_probability >= 0.5)
    model_version           str
    scored_at               UTC datetime

Resumable & idempotent (unique anchor key).

Usage:
    python -m scripts.sgo.score_historical_model \\
        --model-path /var/www/app/backend/models/propvision_v3.joblib \\
        --model-version v3 --league MLB
    python -m scripts.sgo.score_historical_model \\
        --model-entrypoint backend.live.model:predict_one --model-version live
"""
from __future__ import annotations
import argparse
import asyncio
import importlib
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

from ._index_utils import ensure_indexes as _shared_ensure_indexes

SRC_COLL = "sgo_pp_research_model_features"
OUT_COLL = "sgo_pp_research_model_predictions"


def _resolve_colls(league: Optional[str], src_override: Optional[str],
                    out_override: Optional[str]) -> Tuple[str, str]:
    """Per-league routing for source (features) and output (predictions),
    matching the pattern used by build_pp_research_core,
    build_historical_outcomes and build_historical_model_features.
    """
    league_u = (league or "").upper()
    if league_u == "NFL":
        default_src = "sgo_nfl_research_model_features"
        default_out = "sgo_nfl_research_model_predictions"
    elif league_u == "NCAAF":
        default_src = "sgo_ncaaf_research_model_features"
        default_out = "sgo_ncaaf_research_model_predictions"
    else:
        default_src = SRC_COLL
        default_out = OUT_COLL
    return (src_override or default_src, out_override or default_out)


# ───────────────────────────── model loading ──────────────────────────────
def _load_model_from_path(path: str):
    try:
        import joblib
        return joblib.load(path)
    except ImportError:
        pass
    except Exception as e:
        # Try pickle fallback
        sys.stderr.write(f"  [warn] joblib.load failed ({e!r}), trying pickle\n")
    import pickle
    with open(path, "rb") as f:
        return pickle.load(f)


def _build_predictor_from_estimator(est) -> Callable[[List[List[float]]],
                                                       List[float]]:
    """Wrap any sklearn-like estimator → callable batch(rows[]) → probs[]."""
    if hasattr(est, "predict_proba"):
        def f(X):
            import numpy as np
            arr = np.asarray(X, dtype=float)
            p = est.predict_proba(arr)
            # positive class assumed at col 1 for binary
            if hasattr(p, "shape") and len(p.shape) == 2 and p.shape[1] >= 2:
                return [float(x) for x in p[:, 1]]
            return [float(x) for x in p]
        return f
    if hasattr(est, "predict"):
        def f(X):
            import numpy as np
            arr = np.asarray(X, dtype=float)
            p = est.predict(arr)
            return [max(0.0, min(1.0, float(x))) for x in p]
        return f
    raise ValueError(f"Model object has neither predict_proba nor predict: "
                       f"{type(est)}")


def _load_entrypoint(spec: str) -> Callable[[Dict[str, Any]], float]:
    """Spec format: 'module.path:func_name'."""
    if ":" not in spec:
        raise ValueError(f"--model-entrypoint must be 'module.path:func', got {spec!r}")
    mod_path, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, fn_name)
    if not callable(fn):
        raise ValueError(f"Entrypoint {spec} is not callable")
    return fn


# ───────────────────────────── feature-key inference ──────────────────────
async def _infer_feature_keys(db: AsyncIOMotorDatabase, *,
                                  league: Optional[str],
                                  sample_n: int = 1000) -> List[str]:
    match: Dict[str, Any] = {"feature_ready": True}
    if league: match["league_id"] = league
    keys: set = set()
    cursor = db[SRC_COLL].find(
        match, {"_id": 0, "model_input_features": 1}).limit(sample_n)
    async for d in cursor:
        f = d.get("model_input_features") or {}
        for k, v in f.items():
            if isinstance(v, (int, float, bool)) or v is None:
                keys.add(k)
    return sorted(keys)


def _row_to_vector(feats: Dict[str, Any], keys: List[str]) -> List[float]:
    out: List[float] = []
    for k in keys:
        v = feats.get(k)
        if v is None or (isinstance(v, float) and (v != v)):  # NaN-safe
            out.append(0.0)  # naive impute; document this
        elif isinstance(v, bool):
            out.append(1.0 if v else 0.0)
        else:
            try: out.append(float(v))
            except (TypeError, ValueError): out.append(0.0)
    return out


# ───────────────────────────── indexes ────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    await _shared_ensure_indexes(db[OUT_COLL], [
        {"keys": [("event_id", ASCENDING), ("player_id", ASCENDING),
                  ("stat_id", ASCENDING), ("side", ASCENDING),
                  ("line", ASCENDING), ("period_id", ASCENDING),
                  ("model_version", ASCENDING)],
         "unique": True, "name": "pred_anchor_pk"},
        {"keys": "league_id",        "name": "league_id_1"},
        {"keys": "game_date",        "name": "game_date_1"},
        {"keys": "stat_family",      "name": "stat_family_1"},
        {"keys": "model_probability","name": "model_probability_1"},
        {"keys": "model_edge_vs_pp", "name": "model_edge_vs_pp_1"},
        {"keys": "model_version",    "name": "model_version_1"},
    ])


# ───────────────────────────── main scoring ───────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    t0 = time.time()

    # Per-league routing for SRC/OUT collections (mirrors the other
    # SGO pipeline scripts). Rebind module-globals so existing
    # helpers (_infer_feature_keys, ensure_out_indexes, the find loop)
    # pick up the per-league collections without signature changes.
    global SRC_COLL, OUT_COLL
    SRC_COLL, OUT_COLL = _resolve_colls(
        args.league, getattr(args, "src_coll", None),
        getattr(args, "out_coll", None))

    print(f"[{datetime.now(timezone.utc).isoformat()}] score_historical_model")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  resume={args.resume}  "
          f"model_version={args.model_version}  "
          f"src_coll={SRC_COLL}  out_coll={OUT_COLL}")

    # Load predictor
    batch_predict: Optional[Callable[[List[List[float]]], List[float]]] = None
    single_predict: Optional[Callable[[Dict[str, Any]], float]] = None
    feature_keys: List[str] = []

    if args.model_path:
        est = _load_model_from_path(args.model_path)
        batch_predict = _build_predictor_from_estimator(est)
        if args.feature_keys:
            feature_keys = [k.strip() for k in args.feature_keys.split(",")
                              if k.strip()]
        else:
            feature_keys = await _infer_feature_keys(db, league=args.league)
            print(f"  [keys] inferred {len(feature_keys)} feature keys: "
                  f"{feature_keys[:8]}{'...' if len(feature_keys)>8 else ''}")
    elif args.model_entrypoint:
        single_predict = _load_entrypoint(args.model_entrypoint)
    else:
        print("  [err] need either --model-path or --model-entrypoint")
        client.close()
        return 2

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes")
            client.close()
            return 2
        if not args.dry_run:
            existing = await db[OUT_COLL].count_documents(
                {"model_version": args.model_version})
            print(f"  [drop] removing {existing} rows for "
                  f"model_version={args.model_version}")
            await db[OUT_COLL].delete_many(
                {"model_version": args.model_version})

    await ensure_out_indexes(db)

    # Source match
    src_match: Dict[str, Any] = {"feature_ready": True}
    if args.league: src_match["league_id"] = args.league
    if args.start or args.end:
        gd: Dict[str, Any] = {}
        if args.start: gd["$gte"] = args.start
        if args.end:   gd["$lte"] = args.end
        src_match["game_date"] = gd

    # Resume: skip already-scored docs at this model_version
    already_done: set = set()
    if args.resume and not args.dry_run:
        async for r in db[OUT_COLL].find(
            {"model_version": args.model_version},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                         "stat_id": 1, "side": 1, "line": 1, "period_id": 1}
        ):
            already_done.add((r.get("event_id"), r.get("player_id"),
                                r.get("stat_id"), (r.get("side") or "").upper(),
                                r.get("line"), r.get("period_id")))
        print(f"  [resume] {len(already_done):,} docs already scored — will skip")

    BATCH = 500
    upserts: List[UpdateOne] = []
    pending_rows: List[Tuple[Dict[str, Any], List[float]]] = []
    pending_single: List[Dict[str, Any]] = []
    processed = 0
    scored = 0
    skipped = 0
    failed = 0
    sample_docs: List[Dict[str, Any]] = []
    log_every = 10_000
    next_log = log_every

    async def flush():
        nonlocal pending_rows, pending_single, upserts, scored
        if not pending_rows and not pending_single:
            return
        # Batch path
        if pending_rows:
            X = [vec for _, vec in pending_rows]
            try:
                probs = batch_predict(X)
            except Exception as e:
                print(f"    [batch-predict err] {e!r}; falling back row-by-row")
                probs = []
                for vec in X:
                    try: probs.append(float(batch_predict([vec])[0]))
                    except Exception: probs.append(None)
            for (doc, _vec), p in zip(pending_rows, probs):
                _emit(doc, p)
            pending_rows = []
        if pending_single:
            for doc in pending_single:
                try:
                    p = float(single_predict(
                        doc.get("model_input_features") or {}))
                except Exception as e:
                    p = None
                _emit(doc, p)
            pending_single = []
        if upserts and not args.dry_run:
            await db[OUT_COLL].bulk_write(upserts, ordered=False)
            upserts = []

    def _emit(doc: Dict[str, Any], p: Optional[float]) -> None:
        nonlocal scored, failed
        if p is None or (isinstance(p, float) and (p != p)):
            failed += 1
            return
        # Clamp
        p = max(0.0, min(1.0, float(p)))
        pp_imp   = doc.get("pp_implied_probability")
        cons_p   = doc.get("consensus_probability")
        model_edge_vs_pp = (p - pp_imp) if pp_imp is not None else None
        model_edge_vs_consensus = (p - cons_p) if cons_p is not None else None
        out = {
            "event_id":      doc.get("event_id"),
            "league_id":     doc.get("league_id"),
            "game_date":     doc.get("game_date"),
            "player_id":     doc.get("player_id"),
            "stat_id":       doc.get("stat_id"),
            "stat_family":   doc.get("stat_family"),
            "side":          doc.get("side"),
            "line":          doc.get("line"),
            "period_id":     doc.get("period_id"),
            # passthroughs
            "pp_implied_probability": pp_imp,
            "consensus_probability":  cons_p,
            "edge_vs_consensus":      doc.get("edge_vs_consensus"),
            "devig_book_count":       doc.get("devig_book_count"),
            "sharp_book_count":       doc.get("sharp_book_count"),
            "has_valid_devig":        doc.get("has_valid_devig"),
            # predictions
            "model_probability":         p,
            "model_edge_vs_pp":          model_edge_vs_pp,
            "model_edge_vs_consensus":   model_edge_vs_consensus,
            "predicted_outcome":         "WIN" if p >= 0.5 else "LOSS",
            "model_version":             args.model_version,
            "feature_version":           doc.get("feature_version"),
            "scored_at":                 datetime.now(timezone.utc),
        }
        scored += 1
        if len(sample_docs) < 2:
            sample_docs.append(out)
        filt = {
            "event_id":  out["event_id"], "player_id": out["player_id"],
            "stat_id":   out["stat_id"], "side": out["side"],
            "line":      out["line"], "period_id": out["period_id"],
            "model_version": out["model_version"],
        }
        upserts.append(UpdateOne(filt, {"$set": out}, upsert=True))

    async for doc in db[SRC_COLL].find(src_match, {"_id": 0}):
        processed += 1
        uid = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               (doc.get("side") or "").upper(), doc.get("line"),
               doc.get("period_id"))
        if uid in already_done:
            skipped += 1
            continue

        feats = doc.get("model_input_features") or {}
        if batch_predict is not None:
            vec = _row_to_vector(feats, feature_keys)
            pending_rows.append((doc, vec))
            if len(pending_rows) >= BATCH:
                await flush()
        else:
            pending_single.append(doc)
            if len(pending_single) >= BATCH:
                await flush()

        if processed >= next_log:
            el = time.time() - t0
            rate = processed / el if el > 0 else 0
            print(f"  processed={processed:,}  scored={scored:,}  "
                  f"skipped={skipped:,}  failed={failed:,}  "
                  f"rate={rate:,.0f}/s")
            next_log += log_every

    await flush()
    runtime = time.time() - t0

    print()
    print("=" * 72)
    print(f"  score_historical_model SUMMARY")
    print("=" * 72)
    print(f"  feature_ready docs:       {processed:,}")
    print(f"  scored:                   {scored:,}")
    print(f"  skipped (resume):         {skipped:,}")
    print(f"  failed:                   {failed:,}")
    print(f"  runtime:                  {runtime:,.1f}s")
    if sample_docs:
        import json
        print(f"\n  Sample predictions:")
        for d in sample_docs:
            print("    " + "─" * 60)
            print("    " + json.dumps(d, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    p.add_argument("--model-path", default=None,
                    help="Path to joblib/pickle estimator with .predict_proba")
    p.add_argument("--model-entrypoint", default=None,
                    help="module.path:func_name returning P(hit) for a "
                         "features dict")
    p.add_argument("--model-version", required=True,
                    help="Tag stored on each prediction row")
    p.add_argument("--feature-keys", default=None,
                    help="Comma-separated explicit feature order for "
                         "--model-path; defaults to inferred alphabetical")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--drop-existing", action="store_true",
                    help="Drop only rows with this --model-version")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--src-coll", default=None,
                    help="Override source features collection. "
                          "Defaults to sgo_pp_research_model_features (MLB/NBA), "
                          "sgo_nfl_research_model_features when --league=NFL, or "
                          "sgo_ncaaf_research_model_features when --league=NCAAF.")
    p.add_argument("--out-coll", default=None,
                    help="Override output predictions collection. "
                          "Defaults to sgo_pp_research_model_predictions (MLB/NBA), "
                          "sgo_nfl_research_model_predictions when --league=NFL, or "
                          "sgo_ncaaf_research_model_predictions when --league=NCAAF.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
