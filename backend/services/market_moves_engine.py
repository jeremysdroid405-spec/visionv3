"""
Market Moves Engine
====================
Shared board-diff system for NBA and MLB.

Tracks picks that were on visible board tiers (Safe Haven, Front Lines, War Zone)
and generates events when those picks leave or change state.

Architecture:
  - Single engine, both sports
  - Previous board snapshot persisted to MongoDB (survives restarts)
  - On each pipeline publish, diffs old vs new
  - Writes events to MongoDB `market_moves` collection
  - Events auto-expire after 20 minutes
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# TTL for market moves events (20 minutes)
EVENT_TTL_MINUTES = 20
MAX_EVENTS_PER_SPORT = 10

# MongoDB collection names
SNAPSHOT_COLLECTION = "market_moves_snapshots"
EVENTS_COLLECTION = "market_moves"


def _pick_id(sport: str, player_name: str, stat_type: str) -> str:
    """Stable pick identifier across refreshes."""
    return f"{sport}|{player_name}|{stat_type}".lower()


def _snapshot_board(tiers: Dict[str, List[Dict]], sport: str) -> Dict[str, dict]:
    """Build a flat lookup of all visible board picks keyed by pick_id."""
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


async def _load_previous_snapshot(db, sport: str) -> Dict[str, dict]:
    """Load previous board snapshot from MongoDB."""
    doc = await db[SNAPSHOT_COLLECTION].find_one({"sport": sport}, {"_id": 0})
    if doc and doc.get("picks"):
        return doc["picks"]
    return {}


async def _save_snapshot(db, sport: str, snapshot: Dict[str, dict]):
    """Persist current board snapshot to MongoDB."""
    await db[SNAPSHOT_COLLECTION].update_one(
        {"sport": sport},
        {"$set": {"sport": sport, "picks": snapshot, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


async def compute_board_diff(
    db,
    sport: str,
    new_tiers: Dict[str, List[Dict]],
) -> List[dict]:
    """
    Compare previous visible board vs new visible board.
    Returns a list of Market Moves events for picks that left the board.

    Called AFTER atomic publish with the new tier data.
    """
    new_snapshot = _snapshot_board(new_tiers, sport)
    old_snapshot = await _load_previous_snapshot(db, sport)

    # Save new snapshot for next diff
    await _save_snapshot(db, sport, new_snapshot)

    # First run — no previous state to diff against
    if not old_snapshot:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: First run, storing {len(new_snapshot)} picks as baseline")
        return []

    events = []
    now = datetime.now(timezone.utc)

    # Find picks that were on the old board but are NOT on the new board
    for pid, old_pick in old_snapshot.items():
        if pid in new_snapshot:
            continue

        # Pick LEFT the board — determine why
        player = old_pick["player_name"]
        stat = old_pick["stat_type"]
        old_line = old_pick.get("line")
        old_tier = old_pick["tier"]

        # Try to find the player+stat in new tiers with different line
        new_line = None
        status = "Moved off board"
        for tier_name, picks in new_tiers.items():
            for pick in picks:
                if (pick.get("player_name", "").lower() == player.lower()
                        and pick.get("stat_type", "").lower() == stat.lower()):
                    new_line = pick.get("line")
                    if new_line != old_line:
                        status = "Line moved"
                    break
            if new_line is not None:
                break

        event = {
            "sport": sport,
            "pick_id": pid,
            "player_name": player,
            "stat_type": stat,
            "previous_tier": _format_tier(old_tier),
            "old_line": old_line,
            "new_line": new_line,
            "status": status,
            "changed_at": now.isoformat(),
        }
        events.append(event)

    if events:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: {len(events)} board changes detected")
    else:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: No board changes (old={len(old_snapshot)}, new={len(new_snapshot)})")

    return events


def _format_tier(tier_key: str) -> str:
    """Convert tier key to display label."""
    return {
        "safe_haven": "Safe Haven",
        "front_lines": "Front Lines",
        "war_zone": "War Zone",
    }.get(tier_key, tier_key.replace("_", " ").title())


async def persist_events(db, events: List[dict]):
    """Write events to MongoDB (upsert by pick_id) and prune expired ones."""
    if not events:
        return

    collection = db[EVENTS_COLLECTION]

    # Upsert each event by pick_id to avoid duplicates
    for event in events:
        await collection.update_one(
            {"pick_id": event["pick_id"]},
            {"$set": event},
            upsert=True,
        )

    # Prune events older than TTL
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    result = await collection.delete_many({"changed_at": {"$lt": cutoff.isoformat()}})
    if result.deleted_count:
        logger.info(f"[MARKET_MOVES] Pruned {result.deleted_count} expired events")


async def get_recent_events(db, sport: Optional[str] = None, limit: int = 10) -> List[dict]:
    """Fetch recent market moves events, optionally filtered by sport."""
    collection = db[EVENTS_COLLECTION]

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    query = {"changed_at": {"$gte": cutoff.isoformat()}}
    if sport:
        query["sport"] = sport

    cursor = collection.find(query, {"_id": 0}).sort("changed_at", -1).limit(limit)
    return await cursor.to_list(length=limit)
