"""
Injury Normalization Layer
===========================
Single source of truth for injury data across NBA and MLB.

Source: BallDontLie API (player_injuries endpoint)
  - NBA: /nba/v1/player_injuries
  - MLB: /mlb/v1/player_injuries

All raw BDL statuses are mapped through a normalized severity hierarchy.
No downstream consumer reads raw BDL status directly.

Writes to: `injuries_normalized` collection (replaces dg_injuries + bdl_injuries)
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

BDL_ENDPOINTS = {
    "nba": "https://api.balldontlie.io/nba/v1/player_injuries",
    "mlb": "https://api.balldontlie.io/mlb/v1/player_injuries",
}

BDL_TEAM_ENDPOINTS = {
    "nba": "https://api.balldontlie.io/nba/v1/teams",
    "mlb": "https://api.balldontlie.io/mlb/v1/teams",
}

COLLECTION_NAME = "injuries_normalized"

# Cached team_id → abbreviation maps (fetched once per sync)
_team_cache: Dict[str, Dict[int, str]] = {}

# =========================================================================
# NORMALIZED STATUS HIERARCHY
# =========================================================================
# Every raw BDL status maps to exactly one normalized tier.
# Tier levels: 0 (healthy) → 4 (done for season)
# Downstream logic uses tier_level for severity comparisons.

STATUS_MAP = {
    # NBA raw statuses
    "Probable":        {"tier": "PROBABLE",        "tier_level": 1, "risk": "LOW",    "color": "green"},
    "Questionable":    {"tier": "QUESTIONABLE",    "tier_level": 2, "risk": "MEDIUM", "color": "yellow"},
    "Doubtful":        {"tier": "DOUBTFUL",        "tier_level": 3, "risk": "HIGH",   "color": "orange"},
    "Out":             {"tier": "OUT",             "tier_level": 4, "risk": "HIGH",   "color": "red"},
    "Out For Season":  {"tier": "OUT_FOR_SEASON",  "tier_level": 5, "risk": "CRITICAL","color": "red"},
    # MLB raw statuses (IL designations)
    "Day-To-Day":      {"tier": "DAY_TO_DAY",      "tier_level": 2, "risk": "MEDIUM", "color": "yellow"},
    "7-Day IL":        {"tier": "IL_SHORT",         "tier_level": 3, "risk": "HIGH",   "color": "orange"},
    "10-Day-IL":       {"tier": "IL_SHORT",         "tier_level": 3, "risk": "HIGH",   "color": "orange"},
    "10-Day IL":       {"tier": "IL_SHORT",         "tier_level": 3, "risk": "HIGH",   "color": "orange"},
    "15-Day-IL":       {"tier": "IL_STANDARD",      "tier_level": 4, "risk": "HIGH",   "color": "red"},
    "15-Day IL":       {"tier": "IL_STANDARD",      "tier_level": 4, "risk": "HIGH",   "color": "red"},
    "60-Day-IL":       {"tier": "IL_EXTENDED",      "tier_level": 5, "risk": "CRITICAL","color": "red"},
    "60-Day IL":       {"tier": "IL_EXTENDED",      "tier_level": 5, "risk": "CRITICAL","color": "red"},
    "Paternity":       {"tier": "PATERNITY",        "tier_level": 1, "risk": "LOW",    "color": "green"},
    "Bereavement":     {"tier": "BEREAVEMENT",      "tier_level": 1, "risk": "LOW",    "color": "green"},
    "Suspended":       {"tier": "SUSPENDED",        "tier_level": 4, "risk": "HIGH",   "color": "red"},
}

DEFAULT_STATUS = {"tier": "UNKNOWN", "tier_level": 2, "risk": "MEDIUM", "color": "yellow"}


def normalize_status(raw_status: str) -> dict:
    """Map a raw BDL status to normalized tier. Never returns raw status downstream."""
    return STATUS_MAP.get(raw_status, STATUS_MAP.get(raw_status.strip(), DEFAULT_STATUS))


def _parse_date(val) -> Optional[str]:
    """Parse a date string to ISO format, handling various BDL formats."""
    if not val:
        return None
    if isinstance(val, str):
        # Strip timezone suffix if present
        clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return val[:10] if len(val) >= 10 else val
    return None


def _normalize_nba_record(entry: dict, synced_at: str) -> dict:
    """Normalize a single BDL NBA injury record."""
    player = entry.get("player", {})
    raw_status = entry.get("status", "Unknown")
    norm = normalize_status(raw_status)

    return {
        "sport": "nba",
        "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "bdl_id": player.get("id"),
        "team": "",  # NBA player_injuries doesn't nest team — filled below
        "team_id": player.get("team_id"),
        "position": player.get("position", ""),
        "raw_status": raw_status,
        "status": norm["tier"],
        "tier_level": norm["tier_level"],
        "risk": norm["risk"],
        "color": norm["color"],
        "return_date": _parse_date(entry.get("return_date")),
        "injury_date": None,  # NBA endpoint doesn't provide injury date
        "description": entry.get("description", ""),
        "short_comment": (entry.get("description") or "")[:120],
        "injury_type": None,
        "injury_detail": None,
        "injury_side": None,
        "synced_at": synced_at,
        "source": "BDL",
    }


def _normalize_mlb_record(entry: dict, synced_at: str) -> dict:
    """Normalize a single BDL MLB injury record."""
    player = entry.get("player", {})
    team = player.get("team", {}) if isinstance(player.get("team"), dict) else {}
    raw_status = entry.get("status", "Unknown")
    norm = normalize_status(raw_status)

    return {
        "sport": "mlb",
        "player_name": player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "bdl_id": player.get("id"),
        "team": team.get("abbreviation", ""),
        "team_id": team.get("id"),
        "position": player.get("position", ""),
        "raw_status": raw_status,
        "status": norm["tier"],
        "tier_level": norm["tier_level"],
        "risk": norm["risk"],
        "color": norm["color"],
        "return_date": _parse_date(entry.get("return_date")),
        "injury_date": _parse_date(entry.get("date")),
        "description": entry.get("long_comment") or entry.get("description") or "",
        "short_comment": entry.get("short_comment") or (entry.get("long_comment") or "")[:120],
        "injury_type": entry.get("type"),
        "injury_detail": entry.get("detail"),
        "injury_side": entry.get("side"),
        "synced_at": synced_at,
        "source": "BDL",
    }


# =========================================================================
# FETCH + NORMALIZE + PERSIST
# =========================================================================

async def _get_team_map(sport: str) -> Dict[int, str]:
    """Fetch and cache BDL team_id → abbreviation mapping."""
    if sport in _team_cache and _team_cache[sport]:
        return _team_cache[sport]

    endpoint = BDL_TEAM_ENDPOINTS.get(sport)
    if not endpoint:
        return {}

    bdl_key = os.environ.get("BDL_API_KEY", "")
    if not bdl_key:
        return {}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(endpoint, headers={"Authorization": bdl_key})
            resp.raise_for_status()
            teams = resp.json().get("data", [])
            mapping = {t["id"]: t["abbreviation"] for t in teams if "id" in t and "abbreviation" in t}
            _team_cache[sport] = mapping
            return mapping
    except Exception as e:
        logger.warning(f"[INJURY_NORM] Team map fetch failed for {sport}: {e}")
        return _team_cache.get(sport, {})


async def fetch_and_normalize(sport: str) -> List[dict]:
    """
    Fetch injuries from BDL for a single sport.
    Returns list of normalized injury records (not yet persisted).
    """
    endpoint = BDL_ENDPOINTS.get(sport)
    if not endpoint:
        raise ValueError(f"Unknown sport: {sport}")

    bdl_key = os.environ.get("BDL_API_KEY", "")
    if not bdl_key:
        logger.error("[INJURY_NORM] BDL_API_KEY not set")
        return []

    normalizer = _normalize_nba_record if sport == "nba" else _normalize_mlb_record
    synced_at = datetime.now(timezone.utc).isoformat()
    records = []
    cursor = None

    # Fetch team map for team_id → abbreviation resolution
    team_map = await _get_team_map(sport)

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            url = f"{endpoint}?per_page=100"
            if cursor:
                url += f"&cursor={cursor}"

            response = await client.get(url, headers={"Authorization": bdl_key})
            response.raise_for_status()
            data = response.json()

            for entry in data.get("data", []):
                record = normalizer(entry, synced_at)
                # Resolve team_id → abbreviation if missing
                if not record.get("team") and record.get("team_id"):
                    record["team"] = team_map.get(record["team_id"], "")
                records.append(record)

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            cursor = next_cursor

    logger.info(f"[INJURY_NORM] {sport.upper()}: Fetched {len(records)} injuries from BDL")
    return records


async def sync_injuries(db, sport: str) -> dict:
    """
    Fetch, normalize, and persist injuries for one sport.
    
    Tracks change recency:
      - first_seen_at: when this injury was first observed
      - status_changed_at: when the status tier last changed
    
    On each sync, compares new records to existing ones before overwriting.
    """
    records = await fetch_and_normalize(sport)
    collection = db[COLLECTION_NAME]
    now = datetime.now(timezone.utc).isoformat()

    # Load previous state for change tracking
    prev_by_bdl_id: Dict[int, dict] = {}
    cursor = collection.find({"sport": sport}, {"_id": 0, "bdl_id": 1, "status": 1, "tier_level": 1, "return_date": 1, "first_seen_at": 1, "status_changed_at": 1})
    async for doc in cursor:
        bid = doc.get("bdl_id")
        if bid:
            prev_by_bdl_id[bid] = doc

    # Stamp each record with recency fields
    new_count = 0
    changed_count = 0
    for rec in records:
        bid = rec.get("bdl_id")
        prev = prev_by_bdl_id.get(bid) if bid else None

        if not prev:
            # New injury — never seen before
            rec["first_seen_at"] = now
            rec["status_changed_at"] = now
            new_count += 1
        else:
            # Existing injury — preserve first_seen, check for status change
            rec["first_seen_at"] = prev.get("first_seen_at", now)
            if prev.get("tier_level") != rec.get("tier_level") or prev.get("return_date") != rec.get("return_date"):
                rec["status_changed_at"] = now
                changed_count += 1
            else:
                rec["status_changed_at"] = prev.get("status_changed_at", now)

    # Atomic replace
    await collection.delete_many({"sport": sport})
    if records:
        await collection.insert_many(records)

    tiers = {}
    for r in records:
        tiers[r["status"]] = tiers.get(r["status"], 0) + 1

    logger.info(f"[INJURY_NORM] {sport.upper()}: Persisted {len(records)} (new={new_count}, changed={changed_count}) → {tiers}")

    return {
        "sport": sport,
        "count": len(records),
        "new": new_count,
        "changed": changed_count,
        "tiers": tiers,
        "synced_at": now,
    }


async def sync_all(db) -> dict:
    """Sync both NBA and MLB injuries."""
    nba = await sync_injuries(db, "nba")
    mlb = await sync_injuries(db, "mlb")
    return {"nba": nba, "mlb": mlb}


async def get_injuries(db, sport: Optional[str] = None, min_tier_level: int = 0) -> List[dict]:
    """
    Read normalized injuries from DB.
    Optionally filter by sport and minimum severity tier.
    """
    query: dict = {}
    if sport:
        query["sport"] = sport
    if min_tier_level > 0:
        query["tier_level"] = {"$gte": min_tier_level}

    cursor = db[COLLECTION_NAME].find(query, {"_id": 0})
    return await cursor.to_list(length=500)


def is_meaningful_change(old: dict, new: dict) -> Tuple[bool, str]:
    """
    Determine if an injury record change is meaningful enough to trigger a rebuild.

    Meaningful changes:
      - tier_level changed (status escalation or de-escalation)
      - return_date shifted
      - new injury (not in old data)

    Returns (is_meaningful, reason).
    """
    if not old:
        return True, "new_injury"

    if old.get("tier_level") != new.get("tier_level"):
        direction = "escalated" if (new.get("tier_level", 0) > old.get("tier_level", 0)) else "de-escalated"
        return True, f"status_{direction}"

    if old.get("return_date") != new.get("return_date"):
        return True, "return_date_shifted"

    return False, "no_change"
