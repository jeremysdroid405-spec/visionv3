"""Universal Command Center prop source — sport-agnostic SSOT reader.

System-level helper used by the Command Center route (and only the
Command Center route). Reads canonical prop rows from
`{sport}_prop_scores` filtered to `version_tag=final-{sport}-rt` and
`active=True`. No cached_board, no stat-level joins, no legacy aliases,
no sport-specific branching beyond the collection name.

Adding a new sport requires zero changes here — the helper picks up
any sport registered in `services.scheduled_sports.SCHEDULED_SPORTS`.

Canonical row contract (per the Command Center spec, 2026-05-08):

    canonical_key, sport, player_name, stat_type, line,
    recommendation, hit_rate_l5, hit_rate_l10, hit_rate_l20,
    hit_rate_over, hit_rate_under, p_true_active, edge_vs_fair,
    vision_score, cv, team, opponent, tier, tier_reason,
    pp_odds, dk_odds, fd_odds, bol_odds, mgm_odds,
    tier_reference_book, tier_reference_odds,
    event_id, game_start_utc, bdl_player_id, is_home

Legacy aliases that are NEVER read or emitted on this path:
    h5_rate, h10_rate, hit_rate, hit_rates
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.scheduled_sports import SCHEDULED_SPORTS


# ---------------------------------------------------------------------------
# Canonical projection — pulled directly from the score doc.
# ---------------------------------------------------------------------------

_CANONICAL_FIELDS: tuple = (
    "canonical_key",
    "sport",
    "player_name",
    "stat_type",
    "line",
    "recommendation",
    "hit_rate_l5",
    "hit_rate_l10",
    "hit_rate_l20",
    "hit_rate_over",
    "hit_rate_under",
    "p_true_active",
    "edge_vs_fair",
    "vision_score",
    "cv",
    "team",
    "opponent",
    "tier",
    "tier_reason",
    "pp_odds",
    "dk_odds",
    "fd_odds",
    "bol_odds",
    "mgm_odds",
    "tier_reference_book",
    "tier_reference_odds",
    "event_id",
    "game_start_utc",
    "bdl_player_id",
    "is_home",
)


def _to_canonical_prop(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Project a `{sport}_prop_scores` doc onto the universal Command
    Center prop shape. ONE adapter — no per-sport overrides, no
    cached_board enrichment, no legacy aliases.
    """
    out: Dict[str, Any] = {}
    for f in _CANONICAL_FIELDS:
        out[f] = doc.get(f)
    # `direction` is a frontend convenience copy of `recommendation`,
    # lowercased to match the existing SimulationLeg expectation. The
    # source of truth remains `recommendation`.
    rec = doc.get("recommendation")
    out["direction"] = rec.lower() if isinstance(rec, str) and rec else None
    return out


def _escape_regex(s: str) -> str:
    return re.escape(s or "")


def supported_sports() -> List[str]:
    """Sports registered in the scheduler — the universe of valid
    `sport` query params for the Command Center."""
    return list(SCHEDULED_SPORTS.keys())


def is_supported_sport(sport: Optional[str]) -> bool:
    return bool(sport) and (sport or "").lower() in SCHEDULED_SPORTS


async def get_command_center_props(
    db,
    sport: str,
    *,
    player_name: Optional[str] = None,
    canonical_key: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Universal prop fetch for the Command Center.

    One of `player_name` / `canonical_key` must be provided. Returns
    canonical rows ordered by `ranking_score_v2` desc (so the top
    pick surfaces first when the UI groups by stat).
    """
    sport_norm = (sport or "").lower()
    if sport_norm not in SCHEDULED_SPORTS:
        raise ValueError(
            f"Unsupported sport '{sport}'. Registered: {supported_sports()}"
        )

    if not player_name and not canonical_key:
        raise ValueError("player_name or canonical_key is required")

    collection = f"{sport_norm}_prop_scores"
    version_tag = f"final-{sport_norm}-rt"

    query: Dict[str, Any] = {"version_tag": version_tag, "active": True}
    if canonical_key:
        query["canonical_key"] = canonical_key
    else:
        # Case-insensitive exact match on the canonical name.
        query["player_name"] = re.compile(
            f"^{_escape_regex(player_name)}$", re.I
        )

    cursor = (
        db[collection]
        .find(query, {"_id": 0})
        .sort([("ranking_score_v2", -1)])
        .limit(limit)
    )
    docs: List[Dict[str, Any]] = await cursor.to_list(length=limit)
    return [_to_canonical_prop(d) for d in docs]


async def get_player_meta(
    db, sport: str, player_name: str
) -> Dict[str, Any]:
    """Minimal player metadata for the Command Center profile header.

    Read from `{sport}_master_hub_2026` once. Sport-specific only in
    the collection name (parallel to the prop-scores lookup above).
    Returns an empty dict if the player isn't in master_hub — the
    canonical prop rows already carry `team` / `opponent` and the
    frontend headshot component falls back gracefully.
    """
    sport_norm = (sport or "").lower()
    if sport_norm not in SCHEDULED_SPORTS:
        return {}

    collection = f"{sport_norm}_master_hub_2026"
    rx = re.compile(f"^{_escape_regex(player_name)}$", re.I)
    doc = await db[collection].find_one(
        {"$or": [{"display_name": rx}, {"player_name": rx}]},
        {
            "_id": 0,
            "display_name": 1,
            "player_name": 1,
            "photo_url": 1,
            "headshot_url": 1,
            "position": 1,
            "jersey_number": 1,
            "team": 1,
            "team_abbr": 1,
            "bdl_player_id": 1,
            "bdl_id": 1,
            "official_mlb_id": 1,
            "sport": 1,
        },
    )
    if not doc:
        return {}

    # Headshot URL rewrite — `/static/*` hits the React dev server and
    # returns the app shell; `/api/static/*` is mounted on the backend
    # and serves the PNG directly. Same fix used in routes/player.py
    # and routes/ferrari_tiers.py.
    for fld in ("photo_url", "headshot_url"):
        v = doc.get(fld)
        if isinstance(v, str) and v.startswith("/static/"):
            doc[fld] = "/api" + v

    return {
        "player_name": doc.get("display_name") or doc.get("player_name"),
        "team": doc.get("team") or doc.get("team_abbr"),
        "position": doc.get("position"),
        "jersey_number": doc.get("jersey_number"),
        "photo_url": doc.get("photo_url") or doc.get("headshot_url"),
        "bdl_player_id": (
            doc.get("bdl_player_id")
            or doc.get("bdl_id")
            or doc.get("official_mlb_id")
        ),
        "sport": doc.get("sport") or sport_norm,
    }
