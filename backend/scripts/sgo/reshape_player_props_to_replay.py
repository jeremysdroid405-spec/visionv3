"""
reshape_player_props_to_replay.py — Adapter that feeds player props into the
unified `sgo_propvision_full_pipeline_replay` collection.

WHY THIS EXISTS
    The historical gate-replay grid reads from
    `sgo_propvision_full_pipeline_replay`. Player prop ML results live in
    `player_model_prop_features` (Phase 2B). This adapter scores each
    row using the trained player XGB model, translates the result into
    the unified replay schema, and upserts it with `prop_type="player"`.

WHAT IT DOES NOT DO
    - Does not touch prop_type="team" rows.
    - Does not modify the player gate pipeline or live routing.
    - Does not touch NCAAF, NFL, or other sports.

FIELD MAPPING (player_model_prop_features → replay row)
    prop_type              = "player"
    league_id              = sport.upper()
    player_id, player_name
    event_id, game_date, stat_family, side, line
    stat_family            → stat_family (same field)
    hit_rate_l5/l10/l20   → from player_model_prop_features directly
    cv                     → from player_model_prop_features directly
    implied_probability    → from player_model_prop_features directly
    clean_odds             → from player_model_prop_features directly
    model_probability      → from trained player XGB scorer
    tp                     = model_probability
    edge                   = model_probability - implied_probability
    selected_tier          = tier_for_clean_odds(clean_odds)
    outcome_resolved, outcome_numeric, hit  → lifted from prop row
    pipeline_version       = "player_xgb_v1_scored"

UPSERT KEY
    (prop_type="player", event_id, player_id, stat_family, side, line,
     period_id, pipeline_version)

USAGE
    python -m scripts.sgo.reshape_player_props_to_replay --sport nba --dry-run
    python -m scripts.sgo.reshape_player_props_to_replay --sport nba
    python -m scripts.sgo.reshape_player_props_to_replay --sport all
"""
from __future__ import annotations
import argparse
import asyncio
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import numpy as np
import pymongo
from pymongo import UpdateOne

from scripts.sgo.historical_full_pipeline_replay import _odds_bucket
from scripts.sgo.train_player_xgb import FEATURE_COLS, row_to_features
from services.scoring.gates.thresholds import UNIVERSAL_SAFE_HAVEN_MAX, UNIVERSAL_WAR_ZONE_MIN

PIPELINE_VERSION = "player_xgb_v1_scored"
SSOT_SOURCE = "player_model_prop_features"
SRC_COLL = "player_model_prop_features"
DST_COLL = "sgo_propvision_full_pipeline_replay"

SUPPORTED_SPORTS = ("nba", "mlb")
ARTIFACT_ROOT = Path("/app/backend/models/player_xgb")


# ───── tier routing (same universal thresholds as team pipeline) ─────
def tier_for_clean_odds(american_odds: Optional[int]) -> str:
    if american_odds is None:
        return "front_lines"
    if american_odds <= UNIVERSAL_SAFE_HAVEN_MAX:
        return "safe_haven"
    if american_odds >= UNIVERSAL_WAR_ZONE_MIN:
        return "war_zone"
    return "front_lines"


# ───── model loader / scorer ─────
class _PlayerModelCache:
    """Lazy-load and cache player XGB artifacts per (sport, stat_family)."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._missing: set = set()

    def score(
        self, prop: Dict[str, Any], sport: str,
    ) -> Optional[Dict[str, Any]]:
        sf = prop.get("stat_family") or ""
        key = f"{sport}/{sf}"
        if key in self._missing:
            return None
        if key not in self._cache:
            path = ARTIFACT_ROOT / sport / f"{sf.replace(' ', '_').replace('/', '_')}.pkl"
            if not path.exists():
                self._missing.add(key)
                return None
            try:
                with path.open("rb") as fh:
                    self._cache[key] = pickle.load(fh)
            except Exception as e:
                print(f"  [model] failed to load {path}: {e}")
                self._missing.add(key)
                return None

        artifact = self._cache[key]
        model = artifact["model"]
        scaler = artifact["scaler"]
        stat_family_index = {sf2: i for i, sf2 in
                             enumerate(artifact.get("stat_families") or [])}

        try:
            vec = row_to_features(prop, stat_family_index)
            X = np.array([vec], dtype=np.float64)
            X_s = scaler.transform(X)
            prob = float(model.predict_proba(X_s)[0, 1])
        except Exception as e:
            print(f"  [model] scoring error for {key}: {e}")
            return None

        implied = prop.get("implied_probability")
        edge = (round(prob - implied, 4)
                if implied is not None else None)

        return {
            "model_probability": round(prob, 4),
            "edge": edge,
            "model_version": artifact.get("version"),
        }


# ───── replay row assembly ─────
def assemble_replay_row(
    prop: Dict[str, Any],
    sport: str,
    model_score: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Translate one player_model_prop_features doc into one replay row
    matching the schema read by historical_gate_replay_grid.py."""
    clean_odds = prop.get("clean_odds")
    tier = tier_for_clean_odds(clean_odds)
    bucket = _odds_bucket(clean_odds)
    sh_pass = (tier == "safe_haven")
    fl_pass = (tier == "front_lines")
    wz_pass = (tier == "war_zone")

    model_prob = (model_score or {}).get("model_probability")
    edge = (model_score or {}).get("edge")
    model_ver = (model_score or {}).get("model_version")
    implied = prop.get("implied_probability")

    return {
        # identity
        "event_id":               prop.get("event_id"),
        "league_id":              sport.upper(),
        "sport":                  sport,
        "prop_type":              "player",
        "player_id":              prop.get("player_id"),
        "player_name":            prop.get("player_name"),
        "player_name_normalized": (
            prop.get("player_name") or "").lower().strip(),
        # bet
        "stat_family":            prop.get("stat_family"),
        "market_category":        prop.get("stat_family"),
        "side":                   prop.get("side"),
        "line":                   prop.get("line"),
        "period_id":              prop.get("period_id"),
        # odds
        "clean_odds":             clean_odds,
        "implied_probability":    implied,
        "odds_bucket":            bucket,
        # priors
        "hit_rate_l5":            prop.get("hit_rate_l5"),
        "hit_rate_l10":           prop.get("hit_rate_l10"),
        "hit_rate_l20":           prop.get("hit_rate_l20"),
        "cv":                     prop.get("cv"),
        "avg_line":               prop.get("avg_line"),
        # scoring
        "tp":                     model_prob,
        "model_probability":      model_prob,
        "edge":                   edge,
        "fair_probability":       model_prob,
        "vision_score":           None,
        "model_version":          model_ver,
        # tiers
        "tier":                   tier,
        "selected_tier":          tier,
        "safe_haven_pass":        sh_pass,
        "front_lines_pass":       fl_pass,
        "war_zone_pass":          wz_pass,
        "safe_haven_failed_reasons":  ([] if sh_pass else ["tier_route"]),
        "front_lines_failed_reasons": ([] if fl_pass else ["tier_route"]),
        "war_zone_failed_reasons":    ([] if wz_pass else ["tier_route"]),
        "gate_reasons":           ([] if model_score else ["no_player_xgb_model"]),
        # outcome
        "outcome_resolved":  bool(prop.get("outcome_resolved")),
        "outcome_numeric":   prop.get("outcome_numeric"),
        "hit":               prop.get("hit"),
        # provenance
        "pipeline_version":  PIPELINE_VERSION,
        "ssot_source":       SSOT_SOURCE,
        "scored_at":         datetime.now(timezone.utc),
        "as_of_date":        prop.get("game_date"),
        "game_date":         prop.get("game_date"),
    }


def upsert_filter(row: Dict[str, Any]) -> Dict[str, Any]:
    """Composite upsert key. prop_type="player" prevents collisions
    with team rows on the same partial index."""
    return {
        "prop_type":        "player",
        "event_id":         row["event_id"],
        "player_id":        row["player_id"],
        "stat_family":      row["stat_family"],
        "side":             row["side"],
        "line":             row["line"],
        "period_id":        row["period_id"],
        "pipeline_version": row["pipeline_version"],
    }


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        await db[DST_COLL].create_index(
            [("event_id",   pymongo.ASCENDING),
             ("player_id",  pymongo.ASCENDING),
             ("stat_family", pymongo.ASCENDING),
             ("side",        pymongo.ASCENDING),
             ("line",        pymongo.ASCENDING),
             ("period_id",   pymongo.ASCENDING),
             ("pipeline_version", pymongo.ASCENDING)],
            name="player_upsert_key",
            partialFilterExpression={"prop_type": "player"},
        )
        await db[DST_COLL].create_index(
            [("prop_type",  pymongo.ASCENDING),
             ("league_id",  pymongo.ASCENDING),
             ("game_date",  pymongo.ASCENDING),
             ("stat_family", pymongo.ASCENDING)],
            name="player_prop_type_query",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


async def reshape_sport(
    db: AsyncIOMotorDatabase,
    *,
    sport: str, dry_run: bool,
    max_props: int, bulk_chunk: int,
) -> Dict[str, Any]:
    print(f"\n  [{sport.upper()}] reshape player props → {DST_COLL}")
    n_total = await db[SRC_COLL].count_documents({"sport": sport})
    print(f"  [{sport.upper()}] source rows in player_model_prop_features: {n_total:,}")

    if not dry_run:
        await _ensure_indexes(db)

    counters = {
        "scanned":                0,
        "scored":                 0,
        "unscored":               0,
        "skipped_no_clean_odds":  0,
        "skipped_implied_filter": 0,
        "rows_emitted":           0,
        "rows_written":           0,
        "dry_run":                dry_run,
    }
    sample_rows: List[Dict] = []
    model_cache = _PlayerModelCache()
    pending: List[UpdateOne] = []

    async def _flush() -> None:
        if not pending:
            return
        if dry_run:
            counters["rows_emitted"] += len(pending)
            pending.clear()
            return
        res = await db[DST_COLL].bulk_write(pending, ordered=False)
        counters["rows_emitted"] += len(pending)
        counters["rows_written"] += (
            (res.upserted_count or 0) + (res.modified_count or 0))
        pending.clear()

    cursor = db[SRC_COLL].find({"sport": sport}).batch_size(2000)
    async for p in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_props:
            print(f"  [{sport.upper()}] hit --max-props={max_props}; stopping.")
            break

        if not p.get("event_id"):
            continue
        if not p.get("player_id"):
            continue

        # implied bounds filter: same 0.10–0.90 as team pipeline
        ip = p.get("implied_probability")
        if ip is None or ip < 0.10 or ip > 0.90:
            counters["skipped_implied_filter"] += 1
            continue

        # clean_odds required — same gate as team pipeline
        if p.get("clean_odds") is None:
            counters["skipped_no_clean_odds"] += 1
            continue

        score = model_cache.score(p, sport)
        counters["scored" if score is not None else "unscored"] += 1

        row = assemble_replay_row(p, sport, score)

        if len(sample_rows) < 5 and score is not None:
            sample_rows.append({
                "player_id":   row["player_id"],
                "game_date":   row["game_date"],
                "stat_family": row["stat_family"],
                "side":        row["side"],
                "line":        row["line"],
                "tier":        row["tier"],
                "tp":          row["tp"],
                "edge":        row["edge"],
                "hr_l20":      row["hit_rate_l20"],
                "cv":          row["cv"],
                "hit":         row["hit"],
            })

        pending.append(UpdateOne(upsert_filter(row), {"$set": row}, upsert=True))
        if len(pending) >= bulk_chunk:
            await _flush()
            if counters["scanned"] % 50000 == 0:
                print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                      f"written={counters['rows_written']:,}  "
                      f"scored={counters['scored']:,}")

    await _flush()
    return {"sport": sport, "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} PLAYER RESHAPE SUMMARY ──")
    print(f"     scanned:                 {c['scanned']:,}")
    print(f"     skipped (no clean_odds): {c['skipped_no_clean_odds']:,}")
    print(f"     skipped (implied oob):   {c['skipped_implied_filter']:,}")
    print(f"     scored by model:         {c['scored']:,}")
    print(f"     unscored (no model):     {c['unscored']:,}")
    print(f"     rows emitted:            {c['rows_emitted']:,}")
    print(f"     rows written/changed:    {c['rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            print(f"        {s['player_id']:<32s} {s['game_date']}  "
                  f"{s['stat_family']:<12s} side={s['side']:<6s} "
                  f"line={s['line']!s:<6s}  tier={s['tier']:<11s}  "
                  f"tp={s['tp']}  edge={s['edge']}  "
                  f"hr_l20={s['hr_l20']}  hit={s['hit']!s}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"reshape_player_props_to_replay  pipeline_version={PIPELINE_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}  "
          f"max_props={args.max_props}  bulk_chunk={args.bulk_chunk}")
    print(f"  CONTRACT: idempotent upserts into {DST_COLL} with "
          "prop_type='player'. Team rows untouched.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await reshape_sport(
                db, sport=sp, dry_run=dry_run,
                max_props=args.max_props, bulk_chunk=args.bulk_chunk)
            _print_summary(r)
        if dry_run:
            print("\n  DRY-RUN — no writes. Re-run without --dry-run to persist.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                   default="all")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-props", type=int, default=10_000_000)
    p.add_argument("--bulk-chunk", type=int, default=1000)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    rc = main()
    from scripts.sgo.handoff import update_handoff
    update_handoff()
    raise SystemExit(rc)
