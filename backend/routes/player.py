"""Universal player endpoint — post HARD CONSOLIDATION (2026-04-22).

Replaces the deleted `/api/v3/player-with-badges/{name}` endpoint that
lived in `routes/cached_data.py`. Reads exclusively from the canonical
universal collections:

- `{sport}_prop_scores` at version_tag `final-{sport}-rt` for live props
- `{sport}_master_hub_2026` for player metadata (team, position, photo)

No DemonGoblinEngine. No CachedBoardBuilderService. No DB lookups outside
the universal path.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
logger = logging.getLogger(__name__)

_db = None


def set_player_db(db):
    global _db
    _db = db


def _escape_regex(s: str) -> str:
    return re.escape(s or "")


def _normalize_tier(tier: str) -> str:
    t = (tier or "").lower()
    if t in ("safe_haven", "elite_goblin", "elite_goblins"):
        return "safe_haven"
    if t in ("war_zone", "elite_demon", "elite_demons"):
        return "war_zone"
    return t


def _classify_demon_goblin(pick: Dict[str, Any]) -> str:
    """Bucket a pick as demon / goblin / neutral based on the
    canonical `recommendation` + book odds, matching the legacy
    player-with-badges contract."""
    rec = (pick.get("recommendation") or "").upper()
    odds = pick.get("tier_reference_odds")
    if odds is None:
        return "neutral"
    try:
        odds = int(odds)
    except (TypeError, ValueError):
        return "neutral"
    if rec == "UNDER" and odds <= -150:
        return "goblin"
    if rec == "OVER" and odds >= 150:
        return "demon"
    return "neutral"


def _score_to_prop(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a `{sport}_prop_scores` document into the legacy prop
    dict expected by the UI."""
    return {
        "stat_type": doc.get("stat_type"),
        "line": doc.get("line"),
        "recommendation": doc.get("recommendation"),
        "tier": doc.get("tier"),
        "tier_reason": doc.get("tier_reason"),
        "tier_reference_book": doc.get("tier_reference_book"),
        "tier_reference_odds": doc.get("tier_reference_odds"),
        "pp_odds": doc.get("pp_odds"),
        "dk_odds": doc.get("dk_odds"),
        "fd_odds": doc.get("fd_odds"),
        "bol_odds": doc.get("bol_odds"),
        "mgm_odds": doc.get("mgm_odds"),
        "book_count": doc.get("book_count"),
        "coverage_class": doc.get("coverage_class"),
        "hit_rate_over": doc.get("hit_rate_over"),
        "hit_rate_under": doc.get("hit_rate_under"),
        "edge_pct": doc.get("edge_pct"),
        "tp": doc.get("tp"),
        "tp_method": doc.get("tp_method"),
        "tp_books_list": doc.get("tp_books_list"),
        "p_true_active": doc.get("p_true_active"),
        "p_true_method": doc.get("p_true_method"),
        "ranking_score_v2": doc.get("ranking_score_v2"),
        "pp_multiplier": doc.get("pp_multiplier"),
        "pp_multiplier_label": doc.get("pp_multiplier_label"),
        "pp_playable": doc.get("pp_playable"),
        "pp_utility": doc.get("pp_utility"),
        "vision_score": doc.get("vision_score"),
        "event_id": doc.get("event_id"),
        "game_start_utc": doc.get("game_start_utc"),
        "canonical_key": doc.get("canonical_key"),
    }


async def _lookup_player_hub(sport: str, name: str) -> Optional[Dict[str, Any]]:
    """Return a minimal hub document for this player or None."""
    if _db is None:
        return None
    col = f"{sport}_master_hub_2026"
    name_field = "display_name" if sport == "nba" else "display_name"
    # Exact (case-insensitive) match first.
    rx = re.compile(f"^{_escape_regex(name)}$", re.I)
    doc = await _db[col].find_one(
        {name_field: rx},
        {
            "_id": 0,
            "bdl_game_logs": 0,
            "bdl_splits": 0,
            "game_logs": 0,
            "history": 0,
            "history_stats": 0,
        },
    )
    if doc:
        return doc
    # Fuzzy contains match as a second pass.
    rx2 = re.compile(_escape_regex(name), re.I)
    return await _db[col].find_one(
        {name_field: rx2},
        {
            "_id": 0,
            "bdl_game_logs": 0,
            "bdl_splits": 0,
            "game_logs": 0,
            "history": 0,
            "history_stats": 0,
        },
    )


# Enrichment fields pulled from `nba_cached_board.props[*]` per prop.
# Score docs don't carry these; they live in the cached_board snapshot
# built by `cached_board_builder_service`.
_BOARD_ENRICHMENT_FIELDS = (
    "l5_avg", "l10_avg", "l20_avg", "season_avg",
    "h5_rate", "h10_rate", "h20_rate",
    "hit_rates",
    "intel_suite", "scout_badges", "context_badges",
    "active_badges", "vision_intel", "vision_summary",
    "margin", "season_margin",
    "movement_delta", "movement_direction", "movement_strength",
    "is_anomaly", "is_goblin_anomaly", "is_demon_anomaly",
    "is_vision_enriched", "is_goblin", "is_demon", "is_standard",
    "prop_type", "pp_utility_category",
    "draftkings_price", "fanduel_price", "betmgm_price",
    "l5_hits", "l10_hits", "l20_hits",
    "apex_reason", "volatility_label", "volatility_score", "volatility_family",
)
# Player-level enrichment from the cached_board parent doc.
_BOARD_PLAYER_FIELDS = (
    "headshot_url", "photo_url", "team", "team_name", "team_logo_url",
    "position", "jersey_number", "opponent", "opponent_abbr",
    "injury_status", "injured_teammates", "nba_id", "nba_com_id", "espn_id",
    "home_team", "away_team",
    # Badges live at player grain on nba_cached_board (not per-prop).
    "context_badges", "scout_badges",
)


async def _build_nba_cached_board_index(player_name: str) -> Dict[tuple, Dict[str, Any]]:
    """Index `nba_cached_board` props for ONE player, keyed by
    (STAT_UPPER, line_float, DIR_UPPER). Player-grain storage keeps
    this a O(props-for-one-player) operation, ~tens of entries.
    Returns empty dict on miss."""
    if _db is None:
        return {}
    rx = re.compile(f"^{_escape_regex(player_name)}$", re.I)
    player_doc = await _db["nba_cached_board"].find_one(
        {"player_name": rx}, {"_id": 0}
    )
    if not player_doc:
        return {}
    index: Dict[tuple, Dict[str, Any]] = {}
    for p in (player_doc.get("props") or []):
        if not isinstance(p, dict):
            continue
        try:
            line_f = float(p.get("line")) if p.get("line") is not None else None
        except (TypeError, ValueError):
            line_f = None
        if line_f is None:
            continue
        key = (
            (p.get("stat_type") or "").strip().upper(),
            line_f,
            (p.get("direction") or "").strip().upper(),
        )
        index[key] = {"prop": p, "player": player_doc}
    return index


async def _fetch_live_props_for_player(sport: str, player_name: str) -> Dict[str, Dict[str, Any]]:
    """Return {canonical_key: live_prop} for a player — one query."""
    if _db is None:
        return {}
    rx = re.compile(f"^{_escape_regex(player_name)}$", re.I)
    out: Dict[str, Dict[str, Any]] = {}
    async for lp in _db[f"{sport}_live_props"].find(
        {"player_name": rx},
        {"_id": 0, "canonical_key": 1, "home_team": 1, "away_team": 1,
         "event_id": 1, "bdl_player_id": 1},
    ):
        ck = lp.get("canonical_key")
        if ck:
            out[ck] = lp
    return out


@router.get("/v3/player-with-badges/{player_name}")
async def get_player_with_badges(
    player_name: str,
    sport: str = Query("nba", description="Sport (nba or mlb)"),
):
    """Return a player's live-board summary + all canonical props."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    sport = (sport or "nba").lower()
    if sport not in ("nba", "mlb"):
        raise HTTPException(status_code=400, detail=f"Invalid sport '{sport}'")

    version_tag = f"final-{sport}-rt"

    # Case-insensitive player lookup on the canonical scores collection.
    name_rx = re.compile(f"^{_escape_regex(player_name)}$", re.I)
    cursor = _db[f"{sport}_prop_scores"].find(
        {"version_tag": version_tag, "player_name": name_rx},
        {"_id": 0},
    ).sort([("ranking_score_v2", -1)])
    score_docs: List[Dict[str, Any]] = await cursor.to_list(length=500)

    if not score_docs:
        # Fallback fuzzy match (contains).
        name_rx = re.compile(_escape_regex(player_name), re.I)
        score_docs = await _db[f"{sport}_prop_scores"].find(
            {"version_tag": version_tag, "player_name": name_rx},
            {"_id": 0},
        ).sort([("ranking_score_v2", -1)]).to_list(length=500)

    # Hub enrichment.
    canonical_name = score_docs[0].get("player_name") if score_docs else player_name
    hub = await _lookup_player_hub(sport, canonical_name)

    if not score_docs and not hub:
        return {
            "success": False,
            "message": "Player not in cache",
            "sport": sport,
            "player": None,
        }

    props = [_score_to_prop(d) for d in score_docs]

    # --- Enrichment overlay (2026-04-24) ---------------------------------
    # Score docs don't carry l5/l10/l20 averages, hit-rate history,
    # intel_suite, scout/context badges, margin, movement, or anomaly
    # flags. Those live in `nba_cached_board` (built by
    # cached_board_builder_service) and `nba_live_props` (event/team).
    # We merge both, same way the Ferrari tier endpoints do, so the
    # PlayerDetailPage has the fields it needs to render stats, bar
    # charts, glow, and the Vision Intel modal. No scoring changes.
    if sport == "nba" and props:
        board_index = await _build_nba_cached_board_index(canonical_name)
        live_by_key = await _fetch_live_props_for_player(sport, canonical_name)
        parent_player_fields: Dict[str, Any] = {}
        for entry in board_index.values():
            pp = entry.get("player") or {}
            for fld in _BOARD_PLAYER_FIELDS:
                if parent_player_fields.get(fld) in (None, "", []):
                    val = pp.get(fld)
                    if val not in (None, "", []):
                        parent_player_fields[fld] = val
            if parent_player_fields:
                break
        for p in props:
            # Exact 3-tuple board match
            try:
                line_f = float(p["line"])
            except (TypeError, ValueError):
                line_f = None
            stat_u = (p.get("stat_type") or "").strip().upper()
            dir_u = (p.get("recommendation") or "").strip().upper()
            entry = board_index.get((stat_u, line_f, dir_u))
            # Fallback: opposite side (pulls per-stat context at least)
            if entry is None:
                opp = "UNDER" if dir_u == "OVER" else "OVER"
                entry = board_index.get((stat_u, line_f, opp))
            if entry:
                board_prop = entry.get("prop") or {}
                for fld in _BOARD_ENRICHMENT_FIELDS:
                    if p.get(fld) in (None, "", []):
                        val = board_prop.get(fld)
                        if val is not None:
                            p[fld] = val
            # Live props: event/team fields
            lp = live_by_key.get(p.get("canonical_key") or "")
            if lp:
                if not p.get("home_team") and lp.get("home_team"):
                    p["home_team"] = lp["home_team"]
                if not p.get("away_team") and lp.get("away_team"):
                    p["away_team"] = lp["away_team"]
                if not p.get("bdl_player_id") and lp.get("bdl_player_id"):
                    p["bdl_player_id"] = lp["bdl_player_id"]
            # Player-level fields (team/photo/opponent) backfill —
            # useful when hub lookup returned empty but cached_board has it.
            for fld in _BOARD_PLAYER_FIELDS:
                if p.get(fld) in (None, "", []) and parent_player_fields.get(fld) is not None:
                    p[fld] = parent_player_fields[fld]

    demons = [p for p in props if _classify_demon_goblin(p) == "demon"]
    goblins = [p for p in props if _classify_demon_goblin(p) == "goblin"]

    # First event for context.
    opponent = None
    event_id = None
    game_start = None
    if score_docs:
        opponent = score_docs[0].get("opponent")
        event_id = score_docs[0].get("event_id")
        game_start = score_docs[0].get("game_start_utc")

    # Hub defaults — fall back to cached_board parent when hub is empty.
    hub = hub or {}
    # Collect any cached_board parent fields we captured during overlay
    # (parent_player_fields was populated inside the NBA overlay block).
    board_fallback: Dict[str, Any] = {}
    if sport == "nba" and score_docs:
        # Re-derive once for the player-level payload.
        try:
            bi = await _build_nba_cached_board_index(canonical_name)
            for e in bi.values():
                pp = e.get("player") or {}
                for fld in _BOARD_PLAYER_FIELDS:
                    if board_fallback.get(fld) in (None, "", []):
                        v = pp.get(fld)
                        if v not in (None, "", []):
                            board_fallback[fld] = v
                break
        except Exception:
            pass
    team = hub.get("team") or hub.get("team_full") or board_fallback.get("team")
    position = hub.get("position") or board_fallback.get("position")
    photo_url = (
        hub.get("photo_url") or hub.get("headshot_url")
        or board_fallback.get("photo_url") or board_fallback.get("headshot_url")
    )
    bdl_player_id = hub.get("bdl_id") or hub.get("bdl_player_id") or hub.get("player_id")
    jersey = hub.get("jersey")
    height = hub.get("height")
    weight = hub.get("weight")
    if opponent in (None, "") and board_fallback.get("opponent"):
        opponent = board_fallback["opponent"]

    player_payload = {
        "player_name": canonical_name,
        "bdl_player_id": bdl_player_id,
        "team": team,
        "position": position,
        "jersey": jersey,
        "height": height,
        "weight": weight,
        "photo_url": photo_url,
        "opponent": opponent,
        "event_id": event_id,
        "game_start_utc": game_start,
        "sport": sport,
        "props": props,
        "demons": demons,
        "goblins": goblins,
        "hub_status": hub.get("status") or hub.get("injury"),
    }

    return {
        "success": True,
        "sport": sport,
        "source": "universal_prop_scores",
        "version_tag": version_tag,
        "player": player_payload,
    }


@router.get("/v3/board")
async def get_universal_board(
    sport: str = Query("nba", description="Sport (nba or mlb)"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Universal board — returns every active prop on the slate
    grouped by player. Replaces the deleted `/api/v3/cached-props`.

    Reads exclusively from `{sport}_prop_scores @ final-{sport}-rt`.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    sport = (sport or "nba").lower()
    if sport not in ("nba", "mlb"):
        raise HTTPException(status_code=400, detail=f"Invalid sport '{sport}'")

    version_tag = f"final-{sport}-rt"

    docs = await _db[f"{sport}_prop_scores"].find(
        {"version_tag": version_tag},
        {"_id": 0},
    ).sort([("ranking_score_v2", -1)]).to_list(length=limit * 20)

    players_map: Dict[str, Dict[str, Any]] = {}
    for d in docs:
        pname = d.get("player_name")
        if not pname:
            continue
        key = pname.lower()
        entry = players_map.setdefault(
            key,
            {
                "player_name": pname,
                "team": d.get("team"),
                "opponent": d.get("opponent"),
                "event_id": d.get("event_id"),
                "game_start_utc": d.get("game_start_utc"),
                "props": [],
                "demons": [],
                "goblins": [],
            },
        )
        prop = _score_to_prop(d)
        entry["props"].append(prop)
        bucket = _classify_demon_goblin(prop)
        if bucket == "demon":
            entry["demons"].append(prop)
        elif bucket == "goblin":
            entry["goblins"].append(prop)

    # Hub enrichment in one pass (photo / position / jersey).
    names = [v["player_name"] for v in players_map.values()][:limit]
    name_field = "display_name"
    if names:
        hub_docs = await _db[f"{sport}_master_hub_2026"].find(
            {name_field: {"$in": names}},
            {
                "_id": 0, name_field: 1, "team": 1, "team_full": 1,
                "position": 1, "photo_url": 1, "headshot_url": 1,
                "jersey": 1, "bdl_id": 1, "bdl_player_id": 1,
                "player_id": 1,
            },
        ).to_list(length=len(names))
        for h in hub_docs:
            key = (h.get(name_field) or "").lower()
            if key in players_map:
                p = players_map[key]
                if not p.get("team"):
                    p["team"] = h.get("team") or h.get("team_full")
                if not p.get("position"):
                    p["position"] = h.get("position")
                if not p.get("photo_url"):
                    p["photo_url"] = h.get("photo_url") or h.get("headshot_url")
                if not p.get("jersey"):
                    p["jersey"] = h.get("jersey")
                if not p.get("bdl_player_id"):
                    p["bdl_player_id"] = h.get("bdl_id") or h.get("bdl_player_id") or h.get("player_id")

    players = list(players_map.values())[:limit]
    return {
        "sport": sport,
        "source": "universal_prop_scores",
        "version_tag": version_tag,
        "count": len(players),
        "total_props": sum(len(p["props"]) for p in players),
        "players": players,
    }
