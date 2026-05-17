"""Audit / serial scheme for production-pipeline replay runs.

Extends the patterns established in `mlb_replay_multi_tier_eval` with
two additional fingerprints specific to the production-pipeline replay:

  - `production_pipeline_version`: SHA-256 of the concatenated source of
    every production file that participates in scoring/gating. Detects
    inadvertent drift between replay and live.

  - `input_collection_versions`: row-count + max-built-at snapshots of
    every input collection used by replay. Detects "I rebuilt the cache
    and now my replay gives different answers" scenarios.

Phase 1 NOTE: these helpers are not yet called by anything. Phase 2's
production-pipeline replay CLI will use them.
"""
from __future__ import annotations
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

# Files whose content uniquely identifies the production scoring pipeline.
# Edits to any of these should produce a new `production_pipeline_version`.
_PIPELINE_FILES = (
    "services/scoring/recompute.py",
    "services/scoring/scoring_stack.py",
    "services/scoring/tier_evaluator.py",
    "services/scoring/gates/engine.py",
    "services/scoring/gates/thresholds.py",
    "services/scoring/gates/schema.py",
    "services/scoring/best_book.py",
    "services/scoring/universal_edge.py",
    "services/scoring/tp_engine.py",
    "services/mlb_high_friction_model.py",
)

_BACKEND_ROOT = Path("/app/backend")

SERIAL_COUNTER_COLL = "mlb_production_replay_serial_counter"
AUDIT_COLL          = "mlb_production_replay_runs"


def compute_production_pipeline_version() -> str:
    """SHA-256 over the concatenated bytes of every pipeline file.

    Stable as long as the production source files don't change. Used as
    the `production_pipeline_version` field on audit rows.
    """
    h = hashlib.sha256()
    for rel in _PIPELINE_FILES:
        path = _BACKEND_ROOT / rel
        if not path.exists():
            h.update(b"MISSING:" + rel.encode("utf-8"))
            continue
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def git_commit_sha() -> Optional[str]:
    """Best-effort git HEAD SHA — returns None if not a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_BACKEND_ROOT),
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return None


async def snapshot_input_collection_versions(
    db: AsyncIOMotorDatabase, *,
    game_date: str, snapshot_iso: str,
) -> Dict[str, Dict[str, Any]]:
    """Capture row-counts + max-timestamp pins for every input collection
    the replay will read, scoped to the date+snapshot.

    Lets Phase 2 detect 'cache was rebuilt between runs' scenarios
    by comparing the snapshot recorded on an audit row vs the current state.
    """
    out: Dict[str, Dict[str, Any]] = {}

    n = await db.mlb_historical_alt_odds_raw.count_documents(
        {"game_date": game_date, "snapshot_iso": snapshot_iso})
    out["mlb_historical_alt_odds_raw"] = {
        "count": n, "scope": {"game_date": game_date, "snapshot_iso": snapshot_iso}}

    n = await db.mlb_replay_feature_cache.count_documents(
        {"game_date": game_date})
    out["mlb_replay_feature_cache"] = {
        "count": n, "scope": {"game_date": game_date}}

    # Statcast — date-of-build pin (most-recent doc ≤ game_date)
    sc_latest = await db.mlb_statcast_player_features.find_one(
        {"as_of_date": {"$lte": game_date}},
        {"_id": 0, "as_of_date": 1},
        sort=[("as_of_date", -1)])
    out["mlb_statcast_player_features"] = {
        "count": await db.mlb_statcast_player_features.count_documents({}),
        "max_as_of_date_le_gamedate": (sc_latest or {}).get("as_of_date"),
    }

    # Master hub — single doc count
    out["mlb_master_hub_2026"] = {
        "count": await db.mlb_master_hub_2026.count_documents({})}

    # BDL game logs from master_hub for this date
    pipeline = [
        {"$project": {"logs": "$bdl_game_logs"}},
        {"$unwind": "$logs"},
        {"$project": {"d": {"$ifNull": [
            {"$substr": ["$logs.date", 0, 10]},
            {"$substr": ["$logs.game_date", 0, 10]}]}}},
        {"$match": {"d": game_date}},
        {"$count": "n"},
    ]
    bdl_for_date = 0
    async for r in db.mlb_master_hub_2026.aggregate(pipeline, allowDiskUse=True):
        bdl_for_date = r["n"]
    out["mlb_master_hub_2026__bdl_game_logs_for_date"] = {
        "count": bdl_for_date, "scope": {"game_date": game_date}}

    return out


async def next_replay_serial(db: AsyncIOMotorDatabase, *,
                               date: str, tier: str,
                               snapshot_iso: str,
                               prefix: str = "PRODREPLAY") -> str:
    """Atomic global serial issuance, MLB-{PREFIX}-{YYYYMMDD}-{TIER}-{HHMMUTC}-{NNNNN}"""
    res = await db[SERIAL_COUNTER_COLL].find_one_and_update(
        {"_id": f"mlb_{prefix.lower()}_global"},
        {"$inc": {"seq": 1}},
        upsert=True, return_document=True,
    )
    if res is None:
        res = await db[SERIAL_COUNTER_COLL].find_one(
            {"_id": f"mlb_{prefix.lower()}_global"})
    seq = int((res or {}).get("seq", 1))
    yyyymmdd = date.replace("-", "")
    hhmm = snapshot_iso[11:13] + snapshot_iso[14:16]
    tier_short = {"safe_haven": "SH", "front_lines": "FL", "war_zone": "WZ"}.get(tier, tier[:2].upper())
    return f"MLB-{prefix}-{yyyymmdd}-{tier_short}-{hhmm}UTC-{seq:05d}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
