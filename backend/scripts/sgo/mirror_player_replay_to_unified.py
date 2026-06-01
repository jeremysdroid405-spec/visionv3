"""scripts/sgo/mirror_player_replay_to_unified.py

Mirror every player replay-output collection on this Mongo into the
unified optimizer source ``sgo_propvision_full_pipeline_replay`` with
``prop_type="player"`` — without touching team rows.

2026-06-01 update (v2)
──────────────────────
Now auto-detects TWO source patterns and merges them per sport:

  PRIMARY:  ``*_propvision_full_pipeline_outputs``   (current pipeline)
  LEGACY:   ``*_production_replay_outputs``         (old pipeline)

When BOTH exist for a sport, the PRIMARY is used; the LEGACY is
skipped with a warning. Operator can override either way with
``--source <name>`` (repeatable).

Sports auto-detected: MLB / NBA / NFL / NCAAF (and any future
``{sport}_propvision_full_pipeline_outputs`` is picked up
automatically).

CLI
───
  # Dry-run + field-coverage report (no writes)
  python -m scripts.sgo.mirror_player_replay_to_unified

  # Real mirror
  python -m scripts.sgo.mirror_player_replay_to_unified --commit

  # Force a specific source
  python -m scripts.sgo.mirror_player_replay_to_unified --commit \
      --source nba_propvision_full_pipeline_outputs

Safety
──────
- DELETES ``{prop_type: {$ne: "team"}}`` from the unified collection
  before the first source mirror runs (only when ``--commit`` is set).
  Team rows are NEVER touched.
- Bulk-inserts in chunks of 10,000 with ``ordered=False`` so a single
  malformed row doesn't abort the whole sport.
- Operator must pass ``--commit`` explicitly. Default is dry-run.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import InsertOne, ASCENDING

sys.path.insert(0, "/app/backend")
from scripts.sgo.historical_full_pipeline_replay import _odds_bucket  # noqa: E402

load_dotenv("/app/backend/.env")
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mirror_player_replay")

UNIFIED_COLL = "sgo_propvision_full_pipeline_replay"
PRIMARY_SUFFIX = "_propvision_full_pipeline_outputs"
LEGACY_SUFFIX = "_production_replay_outputs"
CHUNK = 10_000
KNOWN_SPORTS = {"mlb", "nba", "nfl", "ncaaf", "ncaab", "nhl"}

# Fields we attempt to extract from each source row. The mapper is
# tolerant — a missing field becomes None. The coverage report counts
# how many rows had a non-None value for each.
TRACKED_OUTPUT_FIELDS = [
    "event_id", "player_id", "player_name", "market_key", "market",
    "stat_family", "side", "line", "book", "odds", "model_probability",
    "tp", "implied_probability", "edge", "vision_score", "tier",
    "gate_pass", "gate_reasons", "hit", "outcome", "actual_value",
    "stake_units", "profit_units", "game_date", "snapshot_iso",
]


# ─── helpers ────────────────────────────────────────────────────────
def _infer_sport_from_coll_name(name: str) -> Optional[str]:
    head = name.split("_", 1)[0].lower()
    return head.upper() if head in KNOWN_SPORTS else None


def _normalize_sport(row: Dict[str, Any], coll_name: str) -> Tuple[str, str]:
    s = row.get("sport") or row.get("league") or row.get("league_id")
    if isinstance(s, str) and s.strip():
        up = s.strip().upper()
        return up, up.lower()
    fb = _infer_sport_from_coll_name(coll_name)
    if fb:
        return fb, fb.lower()
    return "UNKNOWN", "unknown"


def _infer_stat_family(market: str) -> Optional[str]:
    if not isinstance(market, str) or not market:
        return None
    parts = market.lower().split("_")
    if parts[-1] in {"over", "under", "home", "away", "yes", "no"}:
        parts = parts[:-1]
    return "_".join(parts) or None


def _infer_tier_from_odds_bucket(bucket: Optional[str]) -> Optional[str]:
    if not bucket:
        return None
    if bucket in {"odds_lt_-200", "odds_-200_-100"}:
        return "safe_haven"
    if bucket in {"odds_+150_+300", "odds_+300p"}:
        return "war_zone"
    if bucket in {"odds_-100_+0", "odds_+0_+150"}:
        return "front_lines"
    return None


def _to_iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    return v if isinstance(v, str) else None


def normalize_row(row: Dict[str, Any], coll_name: str) -> Optional[Dict[str, Any]]:
    """Build a unified-schema doc. Returns None if the row lacks a
    market_key (the one truly required field for optimizer cells)."""
    market = row.get("market_key") or row.get("market") or ""
    if not market:
        return None
    league_id, sport_lower = _normalize_sport(row, coll_name)
    odds = row.get("odds")
    bucket = _odds_bucket(odds) if odds is not None else None
    tier = row.get("tier") or _infer_tier_from_odds_bucket(bucket)
    stat_family = (
        row.get("stat_family")
        or row.get("market_category")
        or _infer_stat_family(market)
    )
    fair = (
        row.get("tp") if row.get("tp") is not None
        else row.get("fair_probability")
    )
    gate_reasons = (
        row.get("gate_reasons") or row.get("failed_gates") or []
    )
    hit = row.get("hit")
    grade_status = row.get("grade_status") or row.get("outcome")
    if hit is None and grade_status in {"win", "loss", "push"}:
        hit = {"win": True, "loss": False, "push": None}[grade_status]
    return {
        "prop_type":         "player",
        "league_id":         league_id,
        "sport":             sport_lower,
        "event_id":          row.get("event_id"),
        "player_id":         row.get("player_id"),
        "player_name":       row.get("player_name"),
        "market_key":        market,
        "market":            row.get("market"),
        "stat_family":       stat_family,
        "side":              row.get("side"),
        "line":              row.get("line"),
        "book":              row.get("book"),
        "odds":              odds,
        "odds_bucket":       bucket,
        "model_probability": row.get("model_probability"),
        "tp":                fair,
        "implied_probability": row.get("implied_probability"),
        "edge":              row.get("edge"),
        "vision_score":      row.get("vision_score"),
        "tier":              tier,
        "gate_pass":         row.get("gate_pass"),
        "gate_reasons":      gate_reasons if isinstance(gate_reasons, list) else [],
        "hit":               hit,
        "outcome":           grade_status,
        "actual_value":      row.get("actual_value")
                                if row.get("actual_value") is not None
                                else row.get("actual"),
        "stake_units":       row.get("stake_units"),
        "profit_units":      row.get("profit_units"),
        "game_date":         row.get("game_date"),
        "snapshot_iso":      _to_iso(row.get("commence_time"))
                                or _to_iso(row.get("snapshot_iso")),
        # lineage
        "source_coll":       coll_name,
        "mirrored_at":       datetime.now(timezone.utc),
        "pipeline_version":  "player_v1_mirrored",
    }


# ─── discovery ─────────────────────────────────────────────────────
async def discover_sources(
    db, *, override: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Return ``{sport_upper: coll_name}`` for every detected sport.

    PRIMARY suffix beats LEGACY suffix for the same sport. Operator
    overrides win over both.
    """
    cols = set(await db.list_collection_names())
    chosen: Dict[str, str] = {}

    if override:
        for name in override:
            if name not in cols:
                logger.warning(f"--source {name!r} not found in DB; skipping")
                continue
            sport = _infer_sport_from_coll_name(name) or "UNKNOWN"
            chosen[sport] = name
        return chosen

    primary: Dict[str, str] = {}
    legacy: Dict[str, str] = {}
    for c in cols:
        cl = c.lower()
        if cl.endswith(PRIMARY_SUFFIX):
            head = cl[:-len(PRIMARY_SUFFIX)]
            if head in KNOWN_SPORTS:
                primary[head.upper()] = c
        elif cl.endswith(LEGACY_SUFFIX):
            head = cl[:-len(LEGACY_SUFFIX)]
            if head in KNOWN_SPORTS:
                legacy[head.upper()] = c
    # Apply precedence.
    for sport, coll in primary.items():
        chosen[sport] = coll
    for sport, coll in legacy.items():
        if sport in chosen:
            logger.info(
                f"[{sport}] legacy {coll!r} skipped — primary "
                f"{chosen[sport]!r} takes precedence"
            )
        else:
            chosen[sport] = coll
    return chosen


# ─── mirror per source ─────────────────────────────────────────────
async def mirror_source(
    db, *, sport: str, source: str, commit: bool,
) -> Dict[str, Any]:
    src = db[source]
    total = await src.estimated_document_count()
    logger.info(
        f"[{sport}/{source}] starting — total≈{total:,}  "
        f"commit={commit}"
    )

    written = 0
    skipped_no_market = 0
    skipped_no_sport = 0
    field_present_counts: Dict[str, int] = {f: 0 for f in TRACKED_OUTPUT_FIELDS}
    buf: List[InsertOne] = []
    sample: Optional[Dict[str, Any]] = None

    cursor = src.find({}, batch_size=CHUNK)
    async for row in cursor:
        doc = normalize_row(row, source)
        if doc is None:
            skipped_no_market += 1
            continue
        if doc["sport"] == "unknown":
            skipped_no_sport += 1
            continue
        # coverage stats
        for f in TRACKED_OUTPUT_FIELDS:
            v = doc.get(f)
            if v not in (None, "", [], {}):
                field_present_counts[f] += 1
        if sample is None:
            sample = doc
        buf.append(InsertOne(doc))
        if len(buf) >= CHUNK:
            if commit:
                await db[UNIFIED_COLL].bulk_write(buf, ordered=False)
            written += len(buf)
            buf.clear()
            if written % (CHUNK * 5) == 0:
                logger.info(
                    f"[{sport}/{source}] progress: written={written:,} "
                    f"({100*written/max(total,1):.1f}%)"
                )
    if buf:
        if commit:
            await db[UNIFIED_COLL].bulk_write(buf, ordered=False)
        written += len(buf)

    logger.info(
        f"[{sport}/{source}] DONE  written={written:,} "
        f"skipped_no_market={skipped_no_market:,} "
        f"skipped_no_sport={skipped_no_sport:,}"
    )
    return {
        "sport":             sport,
        "source":            source,
        "source_count":      total,
        "rows_written":      written if commit else 0,
        "rows_normalizable": written,
        "skipped": {
            "no_market_key": skipped_no_market,
            "no_sport":      skipped_no_sport,
        },
        "field_coverage": {
            f: {
                "present": field_present_counts[f],
                "pct":     round(100.0 * field_present_counts[f] /
                                  max(written, 1), 1),
            }
            for f in TRACKED_OUTPUT_FIELDS
        },
        "sample": sample,
    }


# ─── index management ─────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    idx_specs = [
        ("prop_type_1_sport_1",
            [("prop_type", ASCENDING), ("sport", ASCENDING)]),
        ("pt_sport_tier",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("tier", ASCENDING)]),
        ("pt_sport_stat_family",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("stat_family", ASCENDING)]),
        ("pt_sport_market_key",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("market_key", ASCENDING)]),
        ("pt_sport_odds_bucket",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("odds_bucket", ASCENDING)]),
        ("pt_sport_game_date",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("game_date", ASCENDING)]),
        ("pt_sport_player_id",
            [("prop_type", ASCENDING), ("sport", ASCENDING), ("player_id", ASCENDING)]),
    ]
    for name, spec in idx_specs:
        try:
            await db[UNIFIED_COLL].create_index(spec, background=True, name=name)
            logger.info(f"[INDEX] ensured {name}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[INDEX] {name}: {e}")


# ─── preflight report ─────────────────────────────────────────────
async def preflight_report(db) -> Dict[str, Any]:
    pipe_pt_sp = [
        {"$group": {"_id": {"pt": "$prop_type", "sp": "$league_id"},
                     "n": {"$sum": 1}}},
        {"$sort": {"_id.pt": 1, "_id.sp": 1}},
    ]
    by_pt_sport: List[Dict[str, Any]] = []
    async for r in db[UNIFIED_COLL].aggregate(pipe_pt_sp):
        by_pt_sport.append({
            "prop_type": r["_id"]["pt"], "sport": r["_id"]["sp"],
            "n": r["n"],
        })

    pipe_tier = [
        {"$group": {"_id": {"pt": "$prop_type", "tier": "$tier"},
                     "n": {"$sum": 1}}},
        {"$sort": {"_id.pt": 1, "_id.tier": 1}},
    ]
    by_tier: List[Dict[str, Any]] = []
    async for r in db[UNIFIED_COLL].aggregate(pipe_tier):
        by_tier.append({
            "prop_type": r["_id"]["pt"], "tier": r["_id"]["tier"],
            "n": r["n"],
        })

    total = await db[UNIFIED_COLL].estimated_document_count()
    player_total = await db[UNIFIED_COLL].count_documents(
        {"prop_type": "player"})
    team_total = await db[UNIFIED_COLL].count_documents(
        {"prop_type": "team"})

    sample_player = await db[UNIFIED_COLL].find_one(
        {"prop_type": "player"}, projection={"_id": 0})
    return {
        "source":            UNIFIED_COLL,
        "prop_type_filter":  "player|team",
        "eligible_total":    total,
        "by_prop_type":      {
            "player": player_total, "team": team_total,
            "all":    total,
        },
        "by_prop_type_sport": by_pt_sport,
        "by_tier":           by_tier,
        "sample_player_row": sample_player,
    }


# ─── main ─────────────────────────────────────────────────────────
async def main_async(args) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    logger.info(f"DB_NAME={os.environ['DB_NAME']}  target={UNIFIED_COLL}")

    sources = await discover_sources(db, override=args.source or None)
    if not sources:
        logger.error(
            "No player replay-output collections detected. Looked for "
            f"`*{PRIMARY_SUFFIX}` and `*{LEGACY_SUFFIX}`. Use "
            "`--source <name>` to pass explicit collection name(s)."
        )
        return 2

    logger.info("Sources to mirror:")
    for sp, coll in sources.items():
        n = await db[coll].estimated_document_count()
        logger.info(f"  {sp:<6} ← {coll:<55s}  ({n:,} rows)")

    commit = args.commit
    if not commit:
        logger.warning(
            "DRY-RUN — pass --commit to actually delete non-team rows "
            "and write. The dry-run still produces a full field-"
            "coverage report per source."
        )

    if commit:
        before = await db[UNIFIED_COLL].count_documents(
            {"prop_type": "team"})
        wiped = await db[UNIFIED_COLL].delete_many(
            {"prop_type": {"$ne": "team"}})
        after_team = await db[UNIFIED_COLL].count_documents(
            {"prop_type": "team"})
        logger.info(
            f"[WIPE] removed {wiped.deleted_count:,} non-team rows; "
            f"team rows: before={before:,}  after={after_team:,}"
        )
        assert before == after_team, "team rows must NEVER change"

    audit: List[Dict[str, Any]] = []
    for sport, coll in sources.items():
        audit.append(await mirror_source(
            db, sport=sport, source=coll, commit=commit))

    if commit:
        await ensure_indexes(db)

    report = await preflight_report(db)

    # ── final report ──
    logger.info("=" * 78)
    logger.info("PLAYER REPLAY MIRROR — SUMMARY")
    logger.info("=" * 78)
    logger.info(f"Mode: {'COMMIT' if commit else 'DRY-RUN'}")
    logger.info("")
    logger.info("Per-source results:")
    for a in audit:
        logger.info(
            f"  {a['sport']:<6s}  {a['source']:<55s}  "
            f"source={a['source_count']:>10,}  "
            f"normalizable={a['rows_normalizable']:>10,}  "
            f"skipped_no_market={a['skipped']['no_market_key']:>8,}  "
            f"skipped_no_sport={a['skipped']['no_sport']:>8,}"
        )
        # Top 10 coverage misses to spotlight schema gaps.
        misses = sorted(
            ((f, c["pct"]) for f, c in a["field_coverage"].items()),
            key=lambda x: x[1],
        )[:8]
        logger.info(f"    lowest-coverage fields: {misses}")

    logger.info("")
    logger.info("Optimizer preflight (unified collection):")
    logger.info(f"  source:           {report['source']}")
    logger.info(f"  prop_type=player: {report['by_prop_type']['player']:,}")
    logger.info(f"  prop_type=team:   {report['by_prop_type']['team']:,}")
    logger.info(f"  prop_type=all:    {report['by_prop_type']['all']:,}")
    logger.info(f"  by_prop_type_sport:")
    for r in report["by_prop_type_sport"]:
        logger.info(
            f"    pt={r['prop_type']:<8s} sport={str(r['sport']):<6s} "
            f"n={r['n']:>10,}"
        )
    logger.info(f"  by_tier:")
    for r in report["by_tier"]:
        logger.info(
            f"    pt={r['prop_type']:<8s} tier={str(r['tier']):<12s} "
            f"n={r['n']:>10,}"
        )
    if report["sample_player_row"]:
        logger.info("  sample_player_row keys (truncated):")
        sp = report["sample_player_row"]
        for k in ("prop_type", "league_id", "sport", "player_name",
                   "market_key", "stat_family", "side", "line", "odds",
                   "odds_bucket", "model_probability", "tp", "edge",
                   "vision_score", "tier", "hit", "outcome",
                   "actual_value", "game_date"):
            v = sp.get(k)
            logger.info(f"    {k:<22s} {v!r}"[:170])
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", action="append",
                   help="Explicit source coll name. Repeatable.")
    p.add_argument("--commit", action="store_true",
                   help="Actually delete non-team rows and write. "
                          "Without this, runs in dry-run mode.")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
