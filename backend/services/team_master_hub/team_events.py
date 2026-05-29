"""
Phase 1.A.4a — Team Event Schedule normalizer.

Pure transform from a raw SGO v2 event dict into the `team_matchups`
row shape (matchup = schedule SSOT, one row per `(sport, event_id)`).

Hard scope:
  - MLB only (1.A.4a). NBA/NFL roll in via the same code path once MLB
    is verified — sport is just an argument.
  - SGO only. No other providers.
  - No grading, no results, no historical odds. Pure schedule.
  - Lenient unresolved-teams policy: if a team name can't be matched
    in `team_master_hub`, write the row with `team_id=None` and
    surface the name(s) on a `unresolved_teams` field.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.team_master_hub.sgo_event_helpers import (
    derive_game_date,
    extract_event_start_iso,
)

# Sport → league string for the `league` column.
_SPORT_TO_LEAGUE = {"mlb": "MLB", "nba": "NBA", "nfl": "NFL"}


# ── status classifier ──────────────────────────────────────────────
def classify_status(raw_status: Any) -> str:
    """Flatten SGO's `event.status` block to a single enum value.

    Priority order (first match wins):
        cancelled  → "cancelled"
        delayed    → "postponed"
        completed  → "completed"
        live       → "live"
        started AND NOT completed AND NOT live → "live"
        default    → "scheduled"

    `raw_status` is usually a dict like:
        {"started": bool, "completed": bool, "live": bool,
         "cancelled": bool, "delayed": bool, "ended": bool, ...}
    """
    if not isinstance(raw_status, dict):
        return "scheduled"

    def _truthy(key: str) -> bool:
        v = raw_status.get(key)
        return bool(v) if v is not None else False

    if _truthy("cancelled"):
        return "cancelled"
    if _truthy("delayed") or _truthy("postponed"):
        return "postponed"
    if _truthy("completed") or _truthy("ended"):
        return "completed"
    if _truthy("live"):
        return "live"
    if _truthy("started"):
        return "live"
    return "scheduled"


# ── team-name extraction (event-level) ─────────────────────────────
def _team_name_block(ev: Dict[str, Any],
                       role: str) -> Tuple[Optional[str], List[str]]:
    """Return `(canonical_name, all_variants)` for a role (home/away).

    `all_variants` is every non-empty string variant SGO surfaces:
    long, short, abbrev, market, display, plus the bare `name` field.
    Used both for master-hub lookup and to surface unresolved names.
    """
    teams = ev.get("teams") or {}
    block = teams.get(role) or {} if isinstance(teams, dict) else {}
    if not isinstance(block, dict):
        return (None, [])
    names = block.get("names") or {}
    variants: List[str] = []
    canonical: Optional[str] = None
    if isinstance(names, dict):
        for key in ("long", "short", "display", "market", "abbrev"):
            v = names.get(key)
            if isinstance(v, str) and v.strip():
                if canonical is None and key in ("long", "short", "display"):
                    canonical = v
                variants.append(v)
    if isinstance(block.get("name"), str) and block["name"].strip():
        if canonical is None:
            canonical = block["name"]
        variants.append(block["name"])
    return (canonical, variants)


# ── venue extraction ───────────────────────────────────────────────
def _extract_venue(ev: Dict[str, Any]) -> Optional[str]:
    """Best-effort venue/stadium name. SGO surfaces it at a couple of
    paths depending on sport. Returns None if none found.
    """
    venue = ev.get("venue")
    if isinstance(venue, str) and venue.strip():
        return venue
    if isinstance(venue, dict):
        for key in ("name", "stadium", "displayName", "long"):
            v = venue.get(key)
            if isinstance(v, str) and v.strip():
                return v
    for key in ("stadium", "stadiumName", "location"):
        v = ev.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


# ── full event → matchup row ───────────────────────────────────────
def normalize_event_to_matchup(
    ev: Dict[str, Any],
    *,
    sport: str,
    team_id_lookup: Dict[str, str],
    fetched_at: datetime,
    source_endpoint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Transform one SGO event into a `team_matchups` row.

    Lenient: a row is still emitted when home_team_id or away_team_id
    can't be resolved (the field becomes None and the unresolved
    name(s) are added to `unresolved_teams`). The only hard-drop case
    is a missing `event_id`.
    """
    event_id = ev.get("eventID") or ev.get("event_id")
    if not event_id:
        return None

    league = _SPORT_TO_LEAGUE.get(sport.lower())
    home_name, home_variants = _team_name_block(ev, "home")
    away_name, away_variants = _team_name_block(ev, "away")

    home_team_id: Optional[str] = None
    away_team_id: Optional[str] = None
    for v in home_variants:
        if v in team_id_lookup:
            home_team_id = team_id_lookup[v]
            break
    for v in away_variants:
        if v in team_id_lookup:
            away_team_id = team_id_lookup[v]
            break

    unresolved: List[str] = []
    if home_team_id is None and home_name:
        unresolved.append(home_name)
    if away_team_id is None and away_name:
        unresolved.append(away_name)

    commence_iso = extract_event_start_iso(ev)
    game_date    = derive_game_date(commence_iso)
    status       = classify_status(ev.get("status"))

    row: Dict[str, Any] = {
        "event_id":        event_id,
        "sport":           sport.lower(),
        "league":          league,
        "home_team_id":    home_team_id,
        "away_team_id":    away_team_id,
        "home_team_name":  home_name,
        "away_team_name":  away_name,
        "commence_time":   commence_iso or None,
        "game_date":       game_date,
        "venue":           _extract_venue(ev),
        "status":          status,
        "status_raw":      ev.get("status") if isinstance(
                                ev.get("status"), dict) else None,
        "source":          "sgo",
        "source_endpoint": source_endpoint,
        "fetched_at":      fetched_at,
        "updated_at":      fetched_at,
        "unresolved_teams": unresolved or None,
    }
    return row


# ── master hub lookup helper ───────────────────────────────────────
async def build_team_id_lookup(db, *, sport: str) -> Dict[str, str]:
    """Return a name→team_id map covering every `display_names` variant
    for the given sport. Pure read from `team_master_hub`.
    """
    lookup: Dict[str, str] = {}
    cursor = db["team_master_hub"].find(
        {"sport": sport.lower()},
        {"_id": 0, "team_id": 1, "display_names": 1},
    )
    async for d in cursor:
        tid = d.get("team_id")
        if not tid:
            continue
        names = d.get("display_names") or {}
        for v in names.values():
            if isinstance(v, str) and v:
                lookup[v] = tid
    return lookup


__all__ = [
    "build_team_id_lookup",
    "classify_status",
    "normalize_event_to_matchup",
]
