"""
Market Moves Engine
====================
Shared board-diff system for NBA and MLB.

Tracks picks that were on visible board tiers (Safe Haven, Front Lines, War Zone)
and generates events when those picks leave or change state.

Architecture:
  - Single engine, both sports
  - In-memory previous-board snapshot per sport
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

# In-memory snapshots of previous board state, keyed by sport
_previous_boards: Dict[str, Dict[str, dict]] = {}


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


def compute_board_diff(
    sport: str,
    new_tiers: Dict[str, List[Dict]],
) -> List[dict]:
    """
    Compare previous visible board vs new visible board.
    Returns a list of Market Moves events for picks that left the board.

    Called AFTER atomic publish with the new tier data.
    """
    global _previous_boards

    new_snapshot = _snapshot_board(new_tiers, sport)
    old_snapshot = _previous_boards.get(sport, {})

    # Update stored snapshot for next diff
    _previous_boards[sport] = new_snapshot

    # First run — no previous state to diff against
    if not old_snapshot:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: First run, storing {len(new_snapshot)} picks as baseline")
        return []

    events = []
    now = datetime.now(timezone.utc)

    # Find picks that were on the old board but are NOT on the new board
    for pid, old_pick in old_snapshot.items():
        if pid in new_snapshot:
            # Still on board — check if line changed (tier swap is not a "move off")
            new_pick = new_snapshot[pid]
            old_line = old_pick.get("line")
            new_line = new_pick.get("line")
            if old_line is not None and new_line is not None and old_line != new_line:
                # Line changed but still on board — not an event per spec
                # (only track picks that LEFT the board)
                pass
            continue

        # Pick LEFT the board — determine why
        player = old_pick["player_name"]
        stat = old_pick["stat_type"]
        old_line = old_pick.get("line")
        old_tier = old_pick["tier"]

        # Check if pick exists in new board with a different line
        # (same player+stat but different line → "Line moved")
        new_match = new_snapshot.get(pid)
        # pid already checked above — if we're here, it's not in new_snapshot

        # Try to find the player+stat anywhere in new tiers with different line
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

        # Build event
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
    """Write events to MongoDB and prune expired ones."""
    if not events:
        return

    collection = db["market_moves"]

    # Insert new events
    await collection.insert_many(events)

    # Prune events older than TTL
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    result = await collection.delete_many({"changed_at": {"$lt": cutoff.isoformat()}})
    if result.deleted_count:
        logger.info(f"[MARKET_MOVES] Pruned {result.deleted_count} expired events")


async def get_recent_events(db, sport: Optional[str] = None, limit: int = 10) -> List[dict]:
    """Fetch recent market moves events, optionally filtered by sport."""
    collection = db["market_moves"]

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EVENT_TTL_MINUTES)
    query = {"changed_at": {"$gte": cutoff.isoformat()}}
    if sport:
        query["sport"] = sport

    cursor = collection.find(query, {"_id": 0}).sort("changed_at", -1).limit(limit)
    return await cursor.to_list(length=limit)
