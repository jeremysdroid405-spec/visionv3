"""
Market Moves Engine
====================
Shared board-diff system for NBA and MLB.

Tracks picks that were on visible board tiers (Safe Haven, Front Lines, War Zone)
and generates events when those picks leave or change state.

Architecture:
  - Single engine, both sports
  - Previous board snapshot persisted to MongoDB (survives restarts)
  - diff_from_db() reads LIVE tier collections and diffs against snapshot
  - Any code path that publishes tiers can call diff_and_update()
  - Events auto-expire after 20 minutes
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_TTL_MINUTES = 20
SNAPSHOT_COLLECTION = "market_moves_snapshots"
EVENTS_COLLECTION = "market_moves"

# Collection mappings per sport
TIER_COLLECTIONS = {
    "nba": {
        "safe_haven": "elite_safe_haven",
        "front_lines": "elite_front_lines",
        "war_zone": "elite_war_zone",
    },
    "mlb": {
        "safe_haven": "mlb_safe_haven",
        "front_lines": "mlb_front_lines",
        "war_zone": "mlb_war_zone",
    },
}


def _pick_id(sport: str, player_name: str, stat_type: str) -> str:
    return f"{sport}|{player_name}|{stat_type}".lower()


def _format_tier(tier_key: str) -> str:
    return {
        "safe_haven": "Safe Haven",
        "front_lines": "Front Lines",
        "war_zone": "War Zone",
    }.get(tier_key, tier_key.replace("_", " ").title())


async def _read_live_board(db, sport: str) -> Dict[str, dict]:
    """Read the current published board directly from tier collections."""
    cols = TIER_COLLECTIONS.get(sport, {})
    snapshot = {}
    for tier_name, col_name in cols.items():
        cursor = db[col_name].find({}, {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1})
        async for doc in cursor:
            player = doc.get("player_name", "")
            stat = doc.get("stat_type", "")
            pid = _pick_id(sport, player, stat)
            snapshot[pid] = {
                "player_name": player,
                "stat_type": stat,
                "line": doc.get("line"),
                "tier": tier_name,
                "sport": sport,
            }
    return snapshot


def _snapshot_from_tiers(tiers: Dict[str, List[Dict]], sport: str) -> Dict[str, dict]:
    """Build snapshot from in-memory tier dicts (used by UnifiedPipeline)."""
    snapshot = {}
    for tier_name, picks in tiers.items():
        for pick in picks:
            player = pick.get("player_name", "")
            stat = pick.get("stat_type", "")
            pid = _pick_id(sport, player, stat)
            snapshot[pid] = {
                "player_name": player,
                "stat_type": stat,
                "line": pick.get("line"),
                "tier": tier_name,
                "sport": sport,
            }
    return snapshot


async def _load_snapshot(db, sport: str) -> Dict[str, dict]:
    doc = await db[SNAPSHOT_COLLECTION].find_one({"sport": sport}, {"_id": 0})
    if doc and doc.get("picks"):
        return doc["picks"]
    return {}


async def _save_snapshot(db, sport: str, snapshot: Dict[str, dict]):
    await db[SNAPSHOT_COLLECTION].update_one(
        {"sport": sport},
        {"$set": {"sport": sport, "picks": snapshot, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


def _compute_diff(old_snapshot: Dict[str, dict], new_snapshot: Dict[str, dict], sport: str) -> List[dict]:
    """Pure diff logic — no DB access."""
    events = []
    now = datetime.now(timezone.utc)

    for pid, old_pick in old_snapshot.items():
        if pid in new_snapshot:
            continue

        player = old_pick["player_name"]
        stat = old_pick["stat_type"]
        old_line = old_pick.get("line")
        old_tier = old_pick["tier"]

        # Check if same player+stat exists in new board with different line
        new_line = None
        status = "Moved off board"
        for npid, npick in new_snapshot.items():
            if (npick["player_name"].lower() == player.lower()
                    and npick["stat_type"].lower() == stat.lower()):
                new_line = npick.get("line")
                if new_line != old_line:
                    status = "Line moved"
                break

        events.append({
            "sport": sport,
            "pick_id": pid,
            "player_name": player,
            "stat_type": stat,
            "previous_tier": _format_tier(old_tier),
            "old_line": old_line,
            "new_line": new_line,
            "status": status,
            "changed_at": now.isoformat(),
        })

    return events


async def _persist_events(db, events: List[dict]):
    if not events:
        return
    collection = db[EVENTS_COLLECTION]
    for event in events:
        await collection.update_one(
            {"pick_id": event["pick_id"]},
            {"$set": event},
            upsert=True,
        )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    await collection.delete_many({"changed_at": {"$lt": cutoff.isoformat()}})


# =========================================================================
# PUBLIC API — two entry points, same diff logic
# =========================================================================

async def diff_and_update_from_tiers(db, sport: str, tiers: Dict[str, List[Dict]]) -> List[dict]:
    """
    Entry point for UnifiedPipeline (has tier dicts in memory).
    Diffs against stored snapshot, persists events, saves new snapshot.
    """
    new_snapshot = _snapshot_from_tiers(tiers, sport)
    old_snapshot = await _load_snapshot(db, sport)
    await _save_snapshot(db, sport, new_snapshot)

    if not old_snapshot:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: First run, storing {len(new_snapshot)} picks as baseline")
        return []

    events = _compute_diff(old_snapshot, new_snapshot, sport)
    if events:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: {len(events)} board changes detected")
        await _persist_events(db, events)
    else:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: No board changes (old={len(old_snapshot)}, new={len(new_snapshot)})")
    return events


async def diff_and_update_from_db(db, sport: str) -> List[dict]:
    """
    Entry point for ANY write path that doesn't pass tier dicts.
    Reads live tier collections directly, diffs against stored snapshot.
    Call this AFTER writing to tier collections.
    """
    new_snapshot = await _read_live_board(db, sport)
    if not new_snapshot:
        return []

    old_snapshot = await _load_snapshot(db, sport)
    await _save_snapshot(db, sport, new_snapshot)

    if not old_snapshot:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: First run (from DB read), storing {len(new_snapshot)} picks as baseline")
        return []

    events = _compute_diff(old_snapshot, new_snapshot, sport)
    if events:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: {len(events)} board changes detected (from DB read)")
        await _persist_events(db, events)
    else:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: No board changes (old={len(old_snapshot)}, new={len(new_snapshot)})")
    return events


async def get_recent_events(db, sport: Optional[str] = None, limit: int = 10) -> List[dict]:
    """Fetch recent market moves events, optionally filtered by sport."""
    collection = db[EVENTS_COLLECTION]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    query = {"changed_at": {"$gte": cutoff.isoformat()}}
    if sport:
        query["sport"] = sport
    cursor = collection.find(query, {"_id": 0}).sort("changed_at", -1).limit(limit)
    return await cursor.to_list(length=limit)
