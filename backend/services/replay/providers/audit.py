"""Sport-agnostic audit/serial scheme for the universal replay harness.

Pipeline files used to compute `production_pipeline_version` come from
the SportReplayAdapter (each sport pins its own scoring sources).

Collection names are sport-prefixed via the adapter for `*_replay_runs`,
`*_replay_outputs`, `*_replay_cards`, etc.
"""
from __future__ import annotations
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.replay.providers.sport_adapter import SportReplayAdapter

_BACKEND_ROOT = Path("/app/backend")


def compute_production_pipeline_version(adapter: SportReplayAdapter) -> str:
    """SHA-256 over the sport's pipeline-file bytes (from adapter.config)."""
    h = hashlib.sha256()
    for rel in adapter.config.default_pipeline_files:
        path = _BACKEND_ROOT / rel
        if not path.exists():
            h.update(b"MISSING:" + rel.encode("utf-8")); continue
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def git_commit_sha() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_BACKEND_ROOT), stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return None


async def snapshot_input_collection_versions(
    db: AsyncIOMotorDatabase, *,
    adapter: SportReplayAdapter,
    game_date: str, snapshot_iso: str,
) -> Dict[str, Dict[str, Any]]:
    """Capture row-counts + max-timestamp pins for every input collection
    the replay will read for this sport×date×snapshot."""
    out: Dict[str, Dict[str, Any]] = {}
    cfg = adapter.config

    n = await db[cfg.odds_collection].count_documents(
        {"game_date": game_date, "snapshot_iso": snapshot_iso})
    out[cfg.odds_collection] = {
        "count": n, "scope": {"game_date": game_date,
                                 "snapshot_iso": snapshot_iso}}

    n = await db[cfg.feature_cache_collection].count_documents(
        {"game_date": game_date})
    out[cfg.feature_cache_collection] = {
        "count": n, "scope": {"game_date": game_date}}

    out[cfg.master_hub_collection] = {
        "count": await db[cfg.master_hub_collection].count_documents({})}

    # MLB-only collections — captured for parity/auditability
    if cfg.sport == "mlb":
        sc_latest = await db.mlb_statcast_player_features.find_one(
            {"as_of_date": {"$lte": game_date}},
            {"_id": 0, "as_of_date": 1}, sort=[("as_of_date", -1)])
        out["mlb_statcast_player_features"] = {
            "count": await db.mlb_statcast_player_features.count_documents({}),
            "max_as_of_date_le_gamedate": (sc_latest or {}).get("as_of_date"),
        }

    return out


async def next_replay_serial(db: AsyncIOMotorDatabase, *,
                               adapter: SportReplayAdapter,
                               date: str, tier: str,
                               snapshot_iso: str,
                               prefix: str = "PRODREPLAY") -> str:
    """Atomic global serial: `{SPORT}-{PREFIX}-{YYYYMMDD}-{TIER}-{HHMMUTC}-{NNNNN}`"""
    coll = f"{adapter.SPORT}_{prefix.lower()}_serial_counter"
    res = await db[coll].find_one_and_update(
        {"_id": f"{adapter.SPORT}_{prefix.lower()}_global"},
        {"$inc": {"seq": 1}}, upsert=True, return_document=True)
    if res is None:
        res = await db[coll].find_one(
            {"_id": f"{adapter.SPORT}_{prefix.lower()}_global"})
    seq = int((res or {}).get("seq", 1))
    yyyymmdd = date.replace("-", "")
    hhmm = snapshot_iso[11:13] + snapshot_iso[14:16]
    tier_short = adapter.config.tier_short_codes.get(
        tier, tier[:2].upper())
    return (f"{adapter.SPORT.upper()}-{prefix}-{yyyymmdd}-"
            f"{tier_short}-{hhmm}UTC-{seq:05d}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Collection name resolution (sport-prefixed)
# ─────────────────────────────────────────────────────────────────────
def runs_collection_name(adapter: SportReplayAdapter) -> str:
    return f"{adapter.SPORT}_production_replay_runs"


def outputs_collection_name(adapter: SportReplayAdapter) -> str:
    return f"{adapter.SPORT}_production_replay_outputs"


def cards_collection_name(adapter: SportReplayAdapter) -> str:
    return f"{adapter.SPORT}_production_replay_cards"


def serial_counter_collection_name(adapter: SportReplayAdapter,
                                     prefix: str = "PRODREPLAY") -> str:
    return f"{adapter.SPORT}_{prefix.lower()}_serial_counter"
