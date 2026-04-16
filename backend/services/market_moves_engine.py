"""
Market Moves Engine — Exit-Reason Classification
==================================================
Shared board-diff system for NBA and MLB.

Tracks picks on visible board tiers (Safe Haven, Front Lines, War Zone)
and generates classified exit events when picks leave the board.

Exit Reason Taxonomy (checked in priority order):
  locked               — Game started (commence_time in past, within 4h)
  injury_repriced      — Player has tier 3+ injury in injuries_normalized
  prop_removed         — Prop no longer exists in raw book/odds board
  line_changed         — Prop exists but line shifted from what was on board
  odds_changed         — Prop exists, same line, but odds shifted materially
  validation_failed    — Prop in candidate pool but failed validation gates
  displaced_by_higher  — Prop scored but outranked by better candidates
  no_longer_qualified  — Prop exists on book but scoring dropped below threshold
  unknown              — None of the above matched

Architecture:
  - Previous board snapshot persisted to MongoDB (survives restarts)
  - On each publish, diff old vs new snapshot
  - For each pick that left: classify the exit reason from structural data
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


# =========================================================================
# SNAPSHOT I/O
# =========================================================================

async def _read_live_board(db, sport: str) -> Dict[str, dict]:
    """Read the current published board directly from tier collections.
    Captures market identity fields for exact line-change matching."""
    cols = TIER_COLLECTIONS.get(sport, {})
    snapshot = {}
    for tier_name, col_name in cols.items():
        cursor = db[col_name].find(
            {},
            {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1, "market": 1, "price": 1, "direction": 1},
        )
        async for doc in cursor:
            player = doc.get("player_name", "")
            stat = doc.get("stat_type", "")
            pid = _pick_id(sport, player, stat)
            snapshot[pid] = {
                "player_name": player,
                "stat_type": stat,
                "line": doc.get("line"),
                "market": doc.get("market", ""),
                "price": doc.get("price"),
                "direction": doc.get("direction", "Over"),
                "tier": tier_name,
                "sport": sport,
            }
    return snapshot


def _snapshot_from_tiers(tiers: Dict[str, List[Dict]], sport: str) -> Dict[str, dict]:
    """Build snapshot from in-memory tier dicts (used by UnifiedPipeline).
    Captures market identity fields for exact line-change matching."""
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
                "market": pick.get("market", ""),
                "price": pick.get("price"),
                "direction": pick.get("direction", "Over"),
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


# =========================================================================
# EXIT-REASON CLASSIFICATION
# =========================================================================

# Maximum line delta before requiring exact market match
# Beyond this threshold, a line "change" is suspicious and defaults to prop_removed
LINE_DELTA_SANITY_THRESHOLD = 3.0


async def _classify_exits(
    db,
    old_snapshot: Dict[str, dict],
    new_snapshot: Dict[str, dict],
    sport: str,
    candidate_pool: Optional[List[Dict]] = None,
) -> List[dict]:
    """
    Diff old vs new board and classify WHY each pick left.

    Line-change matching rules:
      - Must match on exact market identity (same book, same market key,
        same player, same stat, same direction)
      - If exact match not found, classify as prop_removed, not line_changed
      - If line delta > LINE_DELTA_SANITY_THRESHOLD without exact market match,
        force prop_removed

    Args:
        db: Motor database
        old_snapshot: Previous board state {pick_id: pick_dict}
        new_snapshot: Current board state {pick_id: pick_dict}
        sport: "nba" or "mlb"
        candidate_pool: Optional scored props list from the pipeline.

    Returns list of classified exit events.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    exited_pids = [pid for pid in old_snapshot if pid not in new_snapshot]
    if not exited_pids:
        return []

    # ---- Gather classification data ----

    # 1. Injury lookup
    injury_by_name = {}
    inj_cursor = db.injuries_normalized.find(
        {"sport": sport, "tier_level": {"$gte": 3}},
        {"_id": 0, "player_name": 1, "status": 1, "tier_level": 1},
    )
    async for doc in inj_cursor:
        injury_by_name[doc["player_name"].lower()] = doc

    # 2. Game-lock lookup
    locked_teams = set()
    cached_scores = await db.live_scores_cache.find_one({})
    if cached_scores:
        for game in cached_scores.get("games", []):
            ct = game.get("commence_time")
            if not ct:
                continue
            try:
                ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00")) if isinstance(ct, str) else ct
                if not ct_dt.tzinfo:
                    ct_dt = ct_dt.replace(tzinfo=timezone.utc)
                delta_s = (ct_dt - now).total_seconds()
                if -14400 < delta_s < 0:
                    locked_teams.add(game.get("home_team", "").upper())
                    locked_teams.add(game.get("away_team", "").upper())
            except (ValueError, TypeError):
                continue

    # 3. Raw book board: player_name (lower) -> list of prop dicts from dg_cached_board
    #    This is the EXACT source — per-book, per-market, per-line
    book_props_by_player: Dict[str, List[dict]] = {}
    exited_names = list({old_snapshot[pid]["player_name"] for pid in exited_pids})
    board_cursor = db.dg_cached_board.find(
        {"player_name": {"$in": exited_names}},
        {"_id": 0, "player_name": 1, "team": 1, "props": 1},
    )
    async for doc in board_cursor:
        name_lower = doc["player_name"].lower()
        book_props_by_player[name_lower] = doc.get("props", [])

    # 4. Player team lookup
    player_team = {}
    for doc_name in exited_names:
        props = book_props_by_player.get(doc_name.lower(), [])
        if props:
            # Extract team from prop data
            for p in props:
                ht = p.get("home_team", "")
                at = p.get("away_team", "")
                if ht or at:
                    player_team[doc_name.lower()] = ht or at
                    break
    # Fallback to master hub for team
    missing_teams = [n for n in exited_names if n.lower() not in player_team]
    if missing_teams:
        hub_cursor = db.nba_master_hub_2026.find(
            {"display_name": {"$in": missing_teams}},
            {"_id": 0, "display_name": 1, "team": 1},
        )
        async for doc in hub_cursor:
            player_team[doc["display_name"].lower()] = (doc.get("team") or "").upper()

    # 5. Candidate pool
    candidate_by_key = {}
    if candidate_pool:
        for prop in candidate_pool:
            key = f"{prop.get('player_name', '').lower()}|{prop.get('stat_type', '').lower()}"
            candidate_by_key[key] = prop
    else:
        scored_cursor = db.ferrari_scored.find(
            {},
            {"_id": 0, "player_name": 1, "stat_type": 1, "board_score": 1, "line": 1, "market": 1, "validation": 1},
        )
        async for doc in scored_cursor:
            key = f"{doc.get('player_name', '').lower()}|{doc.get('stat_type', '').lower()}"
            candidate_by_key[key] = doc

    # ---- Classify each exit ----

    events = []
    for pid in exited_pids:
        old_pick = old_snapshot[pid]
        player = old_pick["player_name"]
        stat = old_pick["stat_type"]
        old_line = old_pick.get("line")
        old_market = old_pick.get("market", "")
        old_direction = old_pick.get("direction", "Over")
        old_tier = old_pick["tier"]
        name_lower = player.lower()
        prop_key = f"{name_lower}|{stat.lower()}"

        # Check if same player+stat ended up on the new board (different tier/line)
        for npid, npick in new_snapshot.items():
            if npick["player_name"].lower() == name_lower and npick["stat_type"].lower() == stat.lower():
                new_line = npick.get("line")
                new_market = npick.get("market", "")
                # Exact market match required for line_changed
                if old_market and new_market and old_market == new_market and new_line != old_line:
                    events.append(_build_event(
                        sport, pid, player, stat, old_tier, old_line, new_line,
                        status="line_moved", exit_reason="line_changed",
                        now_iso=now_iso,
                        detail=f"market={old_market}",
                    ))
                break
        else:
            # Pick fully left the board. Classify why.
            team = player_team.get(name_lower, "")

            # Priority 1: Game locked
            if team and team in locked_teams:
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="locked", exit_reason="locked", now_iso=now_iso,
                ))
                continue

            # Priority 2: Injury
            inj = injury_by_name.get(name_lower)
            if inj:
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="moved_off_board", exit_reason="injury_repriced",
                    detail=f"{inj['status']} (tier {inj['tier_level']})",
                    now_iso=now_iso,
                ))
                continue

            # Priority 3: Check raw book board for EXACT market match
            player_props = book_props_by_player.get(name_lower, [])
            exact_match = _find_exact_market_match(player_props, stat, old_market, old_direction)

            if exact_match:
                new_book_line = exact_match.get("line")
                if new_book_line is not None and old_line is not None and new_book_line != old_line:
                    delta = abs(new_book_line - old_line)
                    if delta <= LINE_DELTA_SANITY_THRESHOLD:
                        # Reasonable delta with exact market match → line_changed
                        events.append(_build_event(
                            sport, pid, player, stat, old_tier, old_line, new_book_line,
                            status="moved_off_board", exit_reason="line_changed",
                            detail=f"market={old_market} delta={delta:.1f}",
                            now_iso=now_iso,
                        ))
                        continue
                    else:
                        # Extreme delta even with exact market → log and flag
                        logger.warning(
                            f"[MARKET_MOVES] Extreme line delta for {player} {stat}: "
                            f"{old_line} -> {new_book_line} (delta={delta:.1f}, market={old_market}). "
                            f"Classifying as prop_removed."
                        )
                        events.append(_build_event(
                            sport, pid, player, stat, old_tier, old_line, new_book_line,
                            status="moved_off_board", exit_reason="prop_removed",
                            detail=f"extreme_delta={delta:.1f} market={old_market}",
                            now_iso=now_iso,
                        ))
                        continue

            # Priority 4: Check if ANY prop exists for this player+stat on the book
            any_stat_match = _find_any_stat_match(player_props, stat)

            if not any_stat_match and not player_props:
                # Player entirely gone from the book
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="moved_off_board", exit_reason="prop_removed",
                    now_iso=now_iso,
                ))
                continue

            if not any_stat_match:
                # Player on book but this stat is gone
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="moved_off_board", exit_reason="prop_removed",
                    detail="stat_market_removed",
                    now_iso=now_iso,
                ))
                continue

            # Stat still exists on book but not on our board
            # Check candidate pool
            candidate = candidate_by_key.get(prop_key)

            if not candidate:
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="no_longer_qualified", exit_reason="no_longer_qualified",
                    now_iso=now_iso,
                ))
                continue

            validation = candidate.get("validation", {})
            if validation and not validation.get("is_fully_validated", True):
                events.append(_build_event(
                    sport, pid, player, stat, old_tier, old_line, None,
                    status="moved_off_board", exit_reason="validation_failed",
                    now_iso=now_iso,
                ))
                continue

            # In candidate pool, validated → outranked
            events.append(_build_event(
                sport, pid, player, stat, old_tier, old_line, None,
                status="moved_off_board", exit_reason="displaced_by_higher",
                now_iso=now_iso,
            ))

    return events


def _find_exact_market_match(
    player_props: List[dict], stat: str, old_market: str, old_direction: str,
) -> Optional[dict]:
    """
    Find a prop in the player's book data that matches the EXACT market identity.
    Requires: same market key and same direction.
    """
    if not old_market:
        return None

    for prop in player_props:
        prop_market = prop.get("market", "")
        prop_direction = prop.get("direction", "Over")
        if prop_market == old_market and prop_direction == old_direction:
            return prop

    return None


def _find_any_stat_match(player_props: List[dict], stat: str) -> Optional[dict]:
    """Check if ANY prop for this stat exists in the player's book data."""
    market_map = {
        "pts": "point", "reb": "rebound", "ast": "assist",
        "pra": "points_rebounds_assists", "stl": "steal", "blk": "block",
        "to": "turnover", "3pm": "three",
    }
    keyword = market_map.get(stat.lower(), stat.lower())

    for prop in player_props:
        market = prop.get("market", "").lower()
        if keyword in market:
            return prop
    return None


def _build_event(
    sport: str, pid: str, player: str, stat: str,
    old_tier: str, old_line, new_line,
    status: str, exit_reason: str,
    now_iso: str, detail: str = "",
) -> dict:
    event = {
        "sport": sport,
        "pick_id": pid,
        "player_name": player,
        "stat_type": stat,
        "previous_tier": _format_tier(old_tier),
        "old_line": old_line,
        "new_line": new_line,
        "status": status,
        "exit_reason": exit_reason,
        "changed_at": now_iso,
    }
    if detail:
        event["exit_detail"] = detail
    return event


# =========================================================================
# PERSIST
# =========================================================================

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
# PUBLIC API
# =========================================================================

async def diff_and_update_from_tiers(
    db,
    sport: str,
    tiers: Dict[str, List[Dict]],
    candidate_pool: Optional[List[Dict]] = None,
) -> List[dict]:
    """
    Entry point for UnifiedPipeline (has tier dicts in memory).
    Diffs against stored snapshot, classifies exit reasons, persists events.

    Args:
        db: Motor database
        sport: "nba" or "mlb"
        tiers: Final tier dicts {tier_name: [pick_dicts]}
        candidate_pool: Optional scored+validated props from pipeline Phase 4.
                        Enables displaced_by_higher vs no_longer_qualified distinction.
    """
    new_snapshot = _snapshot_from_tiers(tiers, sport)
    old_snapshot = await _load_snapshot(db, sport)
    await _save_snapshot(db, sport, new_snapshot)

    if not old_snapshot:
        logger.info(f"[MARKET_MOVES] {sport.upper()}: First run, storing {len(new_snapshot)} picks as baseline")
        return []

    events = await _classify_exits(db, old_snapshot, new_snapshot, sport, candidate_pool)
    if events:
        reasons = {}
        for e in events:
            r = e.get("exit_reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        logger.info(f"[MARKET_MOVES] {sport.upper()}: {len(events)} exits — {reasons}")
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

    events = await _classify_exits(db, old_snapshot, new_snapshot, sport)
    if events:
        reasons = {}
        for e in events:
            r = e.get("exit_reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        logger.info(f"[MARKET_MOVES] {sport.upper()}: {len(events)} exits (from DB read) — {reasons}")
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
