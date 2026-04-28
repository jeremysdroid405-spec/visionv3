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

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Minimum projected minutes increase to qualify
MIN_MINUTES_BUMP = 2.0

# Shared relevance gates — prevent inactive / non-rotation players from
# generating injury-advantage alerts. Applied after the tier + recency
# gates in `_get_meaningful_injuries()` for every sport.
MIN_MPG_FOR_VACUUM = 10.0
MIN_GP_FOR_VACUUM = 5

# Season-ending / long-term injury freshness cap. Old OFS rows that
# resurface via re-sync (causing `status_changed_at` to restamp) must
# ALSO have been first observed within this window to qualify as a
# "live" advantage event. Measured in hours.
MAX_OFS_FRESHNESS_HOURS = 48
_LONG_TERM_STATUSES = {"OUT_FOR_SEASON", "IL_EXTENDED"}

# Dynamic recency windows (hours)
RECENCY_DEFAULT_HOURS = 12    # No games nearby
RECENCY_PREGAME_HOURS = 6     # Within 2h of tipoff
RECENCY_LIVE_HOURS = 2        # Game already started

# Board tier sources per sport — post Hard Consolidation (2026-04-22)
# all sports read from `{sport}_prop_scores @ final-{sport}-rt` filtered
# by the canonical `tier` field. Legacy collections (elite_*, mlb_*)
# are deleted.
TIER_LABELS = {
    "safe_haven": "Safe Haven",
    "front_lines": "Front Lines",
    "war_zone": "War Zone",
}

# Minutes boost estimates by injured player's role
# Based on typical usage redistribution when a starter goes out
MINUTES_BOOST_BY_TIER = {
    # tier 5 (OUT_FOR_SEASON / IL_EXTENDED) is capped at-or-below
    # tier 4 — a season-ending absence was baked into lineups weeks
    # ago and should never out-boost a genuine late scratch.
    5: {"primary": 4.0, "secondary": 2.5, "tertiary": 1.5},
    4: {"primary": 5.0, "secondary": 3.5, "tertiary": 2.0},   # OUT / IL_STANDARD
    3: {"primary": 3.0, "secondary": 2.0, "tertiary": 1.0},   # DOUBTFUL / IL_SHORT
}

# Usage bump estimates (percentage points)
USAGE_BOOST_BY_TIER = {
    5: {"primary": 3.5, "secondary": 2.0, "tertiary": 1.0},
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
        cached = await db[COLL.shared("live_scores_cache")].find_one({})
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
    """Load every visible-board pick from the canonical
    `{sport}_prop_scores @ final-{sport}-rt` scored collection.

    Enriches each pick with `team` (abbreviation) from the sport's
    master_hub so the downstream same-team injury match in
    `compute_injury_advantages` can join against `injuries_normalized`
    (which stores team abbreviations). Score docs don't persist team
    directly — the master_hub is the system of record.

    Resolution strategy (per user 2026-04-24):
      1. `bdl_player_id` on the score doc → master_hub.bdl_player_id
         (authoritative ID-first join).
      2. Fallback to display_name / player_name lookup (covers players
         missing bdl_player_id in master_hub — e.g. 100% of MLB hub
         rows, ~52% of NBA hub rows).

    Unresolved picks are counted and logged at WARNING so a silent
    mapping regression cannot happen undetected.
    """
    picks: List[dict] = []
    version_tag = f"final-{sport}-rt"
    cursor = db[f"{sport}_prop_scores"].find(
        {"version_tag": version_tag, "tier": {"$in": list(TIER_LABELS.keys())}},
        {"_id": 0},
    )
    async for doc in cursor:
        tier = doc.get("tier")
        doc["_board_tier"] = TIER_LABELS.get(tier, tier)
        doc["_board_collection"] = f"{sport}_prop_scores:{tier}"
        picks.append(doc)

    if not picks:
        return picks

    # Collect unique IDs + names in one pass.
    unique_ids = {p.get("bdl_player_id") for p in picks if p.get("bdl_player_id")}
    unique_names = {p.get("player_name") for p in picks if p.get("player_name")}

    hub_coll = COLL("master_hub", sport)
    hub_cursor = db[hub_coll].find(
        {"$or": [
            {"bdl_player_id": {"$in": list(unique_ids)}},
            {"display_name":  {"$in": list(unique_names)}},
            {"player_name":   {"$in": list(unique_names)}},
        ]},
        {"_id": 0, "bdl_player_id": 1, "display_name": 1,
         "player_name": 1, "team_abbr": 1, "team": 1},
    )
    id_to_team: Dict[int, str] = {}
    name_to_team: Dict[str, str] = {}
    async for h in hub_cursor:
        abbr = h.get("team_abbr") or h.get("team")
        if not abbr:
            continue
        bdl_id = h.get("bdl_player_id")
        if bdl_id is not None:
            id_to_team.setdefault(bdl_id, abbr)
        for key in (h.get("display_name"), h.get("player_name")):
            if key:
                name_to_team.setdefault(key, abbr)

    resolved_via_id = resolved_via_name = unresolved = 0
    for p in picks:
        if p.get("team"):
            continue
        bdl_id = p.get("bdl_player_id")
        if bdl_id is not None and bdl_id in id_to_team:
            p["team"] = id_to_team[bdl_id]
            resolved_via_id += 1
            continue
        team = name_to_team.get(p.get("player_name"))
        if team:
            p["team"] = team
            resolved_via_name += 1
        else:
            unresolved += 1

    if unresolved:
        logger.warning(
            f"[INJURY_ADV:{sport.upper()}] unresolved team for {unresolved}/"
            f"{len(picks)} picks — via_id={resolved_via_id} "
            f"via_name={resolved_via_name}"
        )
    else:
        logger.info(
            f"[INJURY_ADV:{sport.upper()}] team resolved for all "
            f"{len(picks)} picks (via_id={resolved_via_id} "
            f"via_name={resolved_via_name})"
        )
    return picks


async def _is_rotation_relevant(db, sport: str, player_name: str) -> bool:
    """Shared relevance gate — returns True iff the injured player has a
    real recent rotation role. Blocks inactive / never-played / zero-minute
    players from generating advantage alerts.

    Qualifies a candidate when ANY of the following is true:
      - NBA only: player appears in `star_usage_cache` (they're a tracked star)
      - player exists in the sport's master_hub with
            games_played >= MIN_GP_FOR_VACUUM
        AND (sport == "nba": advanced_stats.minutes_per_game >= MIN_MPG_FOR_VACUUM)
        (MLB has no "minutes" concept — GP alone is the rotation signal.)

    Missing / null stats => NOT rotation-relevant (fail closed).
    """
    if not player_name:
        return False
    try:
        if sport == "nba":
            # Fast path: tracked stars in usage cache always pass.
            star = await db[COLL("star_usage_cache", "nba")].find_one(
                {"player_name": player_name}, {"_id": 0, "player_name": 1}
            )
            if star:
                return True

        hub = await db[COLL("master_hub", sport)].find_one(
            {"$or": [{"display_name": player_name}, {"player_name": player_name}]},
            {"_id": 0, "games_played": 1, "advanced_stats": 1,
             "bdl_game_logs_count": 1, "total_game_logs": 1},
        )
        if not hub:
            return False

        if sport == "nba":
            adv = hub.get("advanced_stats") or {}
            gp = adv.get("games_played")
            mpg = adv.get("minutes_per_game")
            if gp is None or mpg is None:
                return False
            return gp >= MIN_GP_FOR_VACUUM and mpg >= MIN_MPG_FOR_VACUUM

        # MLB / future sports: GP-only signal. The MLB master_hub
        # populates `games_played` (career) only on ~12% of records,
        # but `bdl_game_logs_count` (recent BDL game logs ingested
        # this season) on ~28% AND on every active rotation player.
        # Use whichever is present — same threshold, no gate change.
        gp = (
            hub.get("games_played")
            or hub.get("bdl_game_logs_count")
            or hub.get("total_game_logs")
        )
        if gp is None:
            return False
        return gp >= MIN_GP_FOR_VACUUM
    except Exception:
        # Fail closed — better to drop a questionable alert than to
        # publish a bogus one under an infra hiccup.
        return False


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

    cursor = db[COLL.shared("injuries")].find(
        {
            "sport": sport,
            "tier_level": {"$gte": 3},
            "status_changed_at": {"$gte": cutoff},
        },
        projection,
    )
    results = await cursor.to_list(length=300)

    # Shared post-query filters (apply to EVERY sport):
    #   1. OFS / IL_EXTENDED freshness cap — a season-ending row must ALSO
    #      have been first observed within MAX_OFS_FRESHNESS_HOURS, else
    #      it's a stale restamp re-presenting as "fresh".
    #   2. Rotation relevance — injured player must have a real recent
    #      rotation footprint (GP / MPG / star_usage_cache).
    ofs_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=MAX_OFS_FRESHNESS_HOURS)
    ).isoformat()
    filtered: List[dict] = []
    dropped_ofs = 0
    dropped_irrelevant = 0
    for row in results:
        status = (row.get("status") or "").upper()
        if status in _LONG_TERM_STATUSES:
            first_seen = row.get("first_seen_at")
            if not first_seen or first_seen < ofs_cutoff:
                dropped_ofs += 1
                continue
        if not await _is_rotation_relevant(db, sport, row.get("player_name", "")):
            dropped_irrelevant += 1
            continue
        filtered.append(row)

    logger.debug(
        f"[INJURY_ADV] {sport.upper()}: {len(filtered)}/{len(results)} meaningful "
        f"(window={window_hours}h, cutoff={cutoff[:16]}, "
        f"dropped_ofs_stale={dropped_ofs}, dropped_not_rotation={dropped_irrelevant})"
    )

    return filtered


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

    # Build a per-team usage-sorted teammate ranking UP-FRONT so every
    # per-pick lookup uses the same deterministic ordering rather than the
    # incidental iteration order of `board_picks` (2026-04-21 fix).
    # See services/usage_resolver.py for the multi-sport contract.
    from services.usage_resolver import rank_teammates_by_usage
    team_rank_map: Dict[str, Dict[str, int]] = {}           # team -> {player_lower -> rank}
    team_usage_src: Dict[str, Dict[str, str]] = {}          # team -> {player_lower -> source}
    for team, team_injuries in injuries_by_team.items():
        injured_names_lower = {
            i.get("player_name", "").lower() for i in team_injuries
        }
        teammates = [
            {"player_name": p.get("player_name", "")}
            for p in board_picks
            if p.get("team") == team
            and p.get("player_name", "").lower() not in injured_names_lower
        ]
        # Dedupe by name (board has multiple lines per player)
        seen = set(); unique = []
        for t in teammates:
            k = (t["player_name"] or "").lower()
            if k and k not in seen:
                seen.add(k); unique.append(t)
        ranked = await rank_teammates_by_usage(db, sport, unique)
        team_rank_map[team] = {
            (t["player_name"] or "").lower(): t["usage_rank"] for t in ranked
        }
        team_usage_src[team] = {
            (t["player_name"] or "").lower(): t.get("usage_source", "unavailable")
            for t in ranked
        }

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

        # Usage-ranked beneficiary position (2026-04-21 replaces my_index
        # loop-order semantics). Rank 1 = highest-usage rotation teammate.
        rank_lookup = team_rank_map.get(team, {})
        usage_rank = rank_lookup.get(player.lower())
        usage_source_for_pick = (team_usage_src.get(team, {}).get(player.lower())
                                 or "unavailable")
        if usage_rank == 1:
            rank = "primary"
        elif usage_rank == 2:
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
            # Usage-based ranking provenance (2026-04-21 Injury-Rank Phase 2)
            "usage_rank": usage_rank,
            "usage_source": usage_source_for_pick,
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
