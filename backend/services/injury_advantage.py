"""
Live Injury Advantage — Strict Board-Scoped Engine
====================================================
Only fires when a player ALREADY ON THE LIVE BOARD gains meaningful
playing time due to a RECENT same-team injury change.

INPUT FIREWALL:
  This engine reads ONLY from injuries_normalized (BDL-derived).
  All trigger decisions use ONLY structural fields:
    tier_level, status_changed_at, team, player_name, return_date
  Narrative fields (description, short_comment, injury_type, injury_detail,
  injury_side) are quarantined under display_only and NEVER participate
  in advantage computation. They appear in output for UI display only.

Dynamic Recency Window:
  Default:              12 hours
  Within 2h of tipoff:   6 hours  (late scratch zone — only very recent changes matter)
  After game start:      2 hours  (minimal — game is live, stale injuries irrelevant)

Rules:
  1. Injury must be meaningful (tier_level >= 3: OUT, DOUBTFUL, OFS, IL)
  2. Injury must be RECENT (status_changed_at within dynamic recency window)
  3. Beneficiary must be on a visible board tier
  4. Beneficiary must be on the SAME TEAM as the injured player
  5. Projected minutes increase must exceed MIN_MINUTES_BUMP
  6. If no board pick qualifies, section is empty
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from services.injury_normalization import STRUCTURAL_FIELDS

logger = logging.getLogger(__name__)

# Minimum projected minutes increase to qualify
MIN_MINUTES_BUMP = 2.0

# Dynamic recency windows (hours)
RECENCY_DEFAULT_HOURS = 12    # No games nearby
RECENCY_PREGAME_HOURS = 6     # Within 2h of tipoff
RECENCY_LIVE_HOURS = 2        # Game already started

# Board tier collections per sport
TIER_COLLECTIONS = {
    "nba": ["elite_safe_haven", "elite_front_lines", "elite_war_zone"],
    "mlb": ["mlb_safe_haven", "mlb_front_lines", "mlb_war_zone"],
}

TIER_LABELS = {
    "elite_safe_haven": "Safe Haven",
    "elite_front_lines": "Front Lines",
    "elite_war_zone": "War Zone",
    "mlb_safe_haven": "Safe Haven",
    "mlb_front_lines": "Front Lines",
    "mlb_war_zone": "War Zone",
}

# Minutes boost estimates by injured player's role
# Based on typical usage redistribution when a starter goes out
MINUTES_BOOST_BY_TIER = {
    5: {"primary": 6.0, "secondary": 4.0, "tertiary": 2.5},   # OUT_FOR_SEASON / IL_EXTENDED
    4: {"primary": 5.0, "secondary": 3.5, "tertiary": 2.0},   # OUT / IL_STANDARD
    3: {"primary": 3.0, "secondary": 2.0, "tertiary": 1.0},   # DOUBTFUL / IL_SHORT
}

# Usage bump estimates (percentage points)
USAGE_BOOST_BY_TIER = {
    5: {"primary": 5.0, "secondary": 3.0, "tertiary": 1.5},
    4: {"primary": 4.0, "secondary": 2.5, "tertiary": 1.0},
    3: {"primary": 2.5, "secondary": 1.5, "tertiary": 0.5},
}


async def _get_recency_window(db, sport: str) -> int:
    """
    Determine the recency window based on game proximity.

    Reads commence_time from live_scores_cache to find the nearest game.
    Games that started more than 4 hours ago are considered finished.
    Returns hours as int.
    """
    try:
        cached = await db.live_scores_cache.find_one({})
        if not cached:
            return RECENCY_DEFAULT_HOURS

        now = datetime.now(timezone.utc)
        games = cached.get("games", [])

        for game in games:
            ct = game.get("commence_time")
            if not ct:
                continue
            try:
                if isinstance(ct, str):
                    ct = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                if not ct.tzinfo:
                    ct = ct.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            delta_seconds = (ct - now).total_seconds()

            # Skip finished games (started > 4h ago)
            if delta_seconds < -14400:
                continue

            # Game is live (started within last 4 hours)
            if delta_seconds < 0:
                return RECENCY_LIVE_HOURS

            # Within 2 hours of tipoff
            if delta_seconds < 7200:
                return RECENCY_PREGAME_HOURS

        return RECENCY_DEFAULT_HOURS
    except Exception:
        return RECENCY_DEFAULT_HOURS


async def _get_board_picks(db, sport: str) -> List[dict]:
    """Load all picks from visible board tiers for a sport."""
    picks = []
    for col_name in TIER_COLLECTIONS.get(sport, []):
        cursor = db[col_name].find({}, {"_id": 0})
        async for doc in cursor:
            doc["_board_tier"] = TIER_LABELS.get(col_name, col_name)
            doc["_board_collection"] = col_name
            picks.append(doc)
    return picks


async def _get_meaningful_injuries(db, sport: str) -> List[dict]:
    """
    Get injuries that are:
      - tier_level >= 3 (OUT, DOUBTFUL, OFS, IL)
      - status_changed_at within dynamic recency window

    Dynamic window tightens as games approach:
      12h default -> 6h pregame -> 2h live

    FIREWALL: Query and filter use ONLY structural fields.
    display_only is fetched separately for UI output but NEVER drives logic.
    """
    window_hours = await _get_recency_window(db, sport)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    projection = {"_id": 0, "display_only": 1}
    for f in STRUCTURAL_FIELDS:
        projection[f] = 1

    cursor = db["injuries_normalized"].find(
        {
            "sport": sport,
            "tier_level": {"$gte": 3},
            "status_changed_at": {"$gte": cutoff},
        },
        projection,
    )
    results = await cursor.to_list(length=300)

    logger.debug(f"[INJURY_ADV] {sport.upper()}: {len(results)} meaningful injuries (window={window_hours}h, cutoff={cutoff[:16]})")

    return results


def _estimate_benefit(injury_tier: int, rank: str) -> dict:
    """Estimate minutes and usage bump based on injury severity and beneficiary rank."""
    mins = MINUTES_BOOST_BY_TIER.get(injury_tier, {}).get(rank, 0)
    usage = USAGE_BOOST_BY_TIER.get(injury_tier, {}).get(rank, 0)
    return {"minutes_bump": mins, "usage_bump": usage}


async def compute_injury_advantages(db, sport: str) -> List[dict]:
    """
    Compute Live Injury Advantages for a single sport.

    Returns a list of advantage objects, one per qualifying board pick,
    sorted by projected minutes increase descending.
    """
    board_picks = await _get_board_picks(db, sport)
    window_hours = await _get_recency_window(db, sport)
    injuries = await _get_meaningful_injuries(db, sport)

    if not board_picks or not injuries:
        return []

    # Index injuries by team
    injuries_by_team: Dict[str, List[dict]] = {}
    for inj in injuries:
        team = inj.get("team", "")
        if team:
            injuries_by_team.setdefault(team, []).append(inj)

    # For each board pick, check if same-team injuries create an advantage
    advantages = []
    seen_players = set()  # one advantage per player (best stat line)

    for pick in board_picks:
        player = pick.get("player_name", "")
        team = pick.get("team", "")
        stat = pick.get("stat_type", "")

        if player.lower() in seen_players:
            continue

        team_injuries = injuries_by_team.get(team, [])
        if not team_injuries:
            continue

        # Don't boost yourself (injured player on the board)
        team_injuries = [i for i in team_injuries if i.get("player_name", "").lower() != player.lower()]
        if not team_injuries:
            continue

        # Find the most impactful injury on this team
        best_injury = max(team_injuries, key=lambda i: i.get("tier_level", 0))
        injury_tier = best_injury.get("tier_level", 3)

        # Count how many board picks are on this team (determines rank)
        same_team_board = [p for p in board_picks if p.get("team") == team and p.get("player_name", "").lower() != best_injury.get("player_name", "").lower()]
        my_index = next((i for i, p in enumerate(same_team_board) if p.get("player_name") == player), 0)

        if my_index == 0:
            rank = "primary"
        elif my_index == 1:
            rank = "secondary"
        else:
            rank = "tertiary"

        benefit = _estimate_benefit(injury_tier, rank)
        minutes_bump = benefit["minutes_bump"]
        usage_bump = benefit["usage_bump"]

        # STRICT: Must exceed minimum minutes threshold
        if minutes_bump < MIN_MINUTES_BUMP:
            continue

        seen_players.add(player.lower())

        # DISPLAY_ONLY: narrative sourced from quarantined namespace
        display = best_injury.get("display_only", {})
        injury_desc = (display.get("description") or display.get("short_comment") or "")[:120]

        advantages.append({
            "sport": sport,
            "beneficiary_name": player,
            "beneficiary_team": team,
            "stat_type": stat,
            "line": pick.get("line"),
            "board_tier": pick.get("_board_tier"),
            # Structural fields from injury record
            "injured_player": best_injury.get("player_name"),
            "injured_status": best_injury.get("status"),
            "injured_tier_level": injury_tier,
            "injury_return_date": best_injury.get("return_date"),
            # Display-only narrative — NEVER used for logic
            "injury_description": injury_desc,
            # Computed advantage metrics
            "minutes_bump": minutes_bump,
            "usage_bump": usage_bump,
            "rank": rank,
            "recency_window_hours": window_hours,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        })

    # Sort by minutes bump descending
    advantages.sort(key=lambda a: -a["minutes_bump"])

    if advantages:
        logger.info(f"[INJURY_ADV] {sport.upper()}: {len(advantages)} board picks with injury advantage")
    return advantages


async def compute_all(db) -> dict:
    """Compute injury advantages for both sports."""
    nba = await compute_injury_advantages(db, "nba")
    mlb = await compute_injury_advantages(db, "mlb")
    nba_window = await _get_recency_window(db, "nba")
    mlb_window = await _get_recency_window(db, "mlb")
    return {
        "nba": nba,
        "mlb": mlb,
        "total": len(nba) + len(mlb),
        "recency_window": {"nba": nba_window, "mlb": mlb_window},
    }
