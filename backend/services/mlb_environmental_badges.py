"""
MLB Environmental Badge Adapter
================================

Bridges the existing — but never-with-context-invoked — `MLBBadgeService`
to the board-pick post-process path. Reads cached weather (`MLBWeatherService`,
in-process 15-min cache) and umpire / referee assignments
(`RefereeScraperService`, hourly-synced collection cache), constructs the
input dicts `MLBBadgeService.evaluate_all_badges` expects, then merges
the resulting badges into `pick["scout_badges"]`.

Why this lives in its own module
--------------------------------
* Keeps `services/badge_enrichment.py` a pure dispatcher (sport routing
  only — no data fetching).
* Keeps `services/mlb_badge_system.py` UNMODIFIED (this adapter is the
  only consumer for board-time enrichment; the original score/route
  call sites are untouched).
* Failure-isolated. Any read failure produces an empty input which the
  badge service silently no-ops on.

Field-name translations
-----------------------
`MLBWeatherService.get_weather()` returns:
    wind_speed / wind_direction / type / temperature / stadium / ...
`MLBBadgeService.evaluate_wind_boost(weather, park)` reads:
    weather["windspeed"] / weather["winddirection"]
    park["type"]         (must equal "outdoor")
This adapter performs the translation explicitly so neither the weather
service nor the badge service need to change.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Stat types that should not invoke per-pick badge work — the badge
# service is designed for OVER-side evaluation; keeping the gate
# narrow prevents wasted async lookups on UNDER picks (which the
# universal scout step also skips for the same reason).
_SUPPORTED_STAT_TYPES_FALLBACK = "Total Bases"


async def _resolve_team_context(pick: Dict[str, Any], db: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (player_team_abbrev, home_team_abbrev, opponent_team_abbrev).

    Pre-2026-05-08 the MLB ferrari board pipeline overlaid these onto
    the score-doc-derived pick from the player's `mlb_cached_board`
    document, but the existing overlay path projects only a narrow
    field whitelist; team / home_team / away_team are absent from the
    response shape. Rather than expand that overlay (which would
    affect every consumer), this adapter performs ONE additional
    targeted read against `mlb_cached_board` (keyed on player_name —
    one doc per player) and pulls the team triple from the matching
    prop element. Read-only; no writes; cached at the motor / mongo
    driver level."""
    # Fast path: pick already carries the fields (defense in depth for
    # any future caller that does overlay them).
    pt = (pick.get("team") or "").strip().upper() or None
    home = (pick.get("home_team") or pick.get("park_team") or "").strip().upper() or None
    opp  = (pick.get("opponent") or pick.get("opponent_team") or "").strip().upper() or None
    if pt and home:
        return pt, home, opp

    player_name = pick.get("player_name")
    canonical_key = pick.get("canonical_key")
    line = pick.get("line")
    stat_type = pick.get("stat_type")
    if not (player_name and db is not None):
        return pt, home, opp

    try:
        cb_doc = await db["mlb_cached_board"].find_one(
            {"player_name": player_name},
            {"_id": 0, "team": 1, "props": 1},
        )
    except Exception:
        logger.debug("[MLB_ENV_BADGE] cached_board lookup failed for %s",
                     player_name, exc_info=False)
        return pt, home, opp
    if not cb_doc:
        return pt, home, opp

    if not pt and cb_doc.get("team"):
        pt = str(cb_doc["team"]).strip().upper() or None

    # Find the matching prop entry in the player's cached_board doc.
    target_prop = None
    for prop in cb_doc.get("props") or []:
        if (
            prop.get("canonical_key") == canonical_key
            or (
                prop.get("line") == line
                and (
                    prop.get("stat_type") == stat_type
                    or prop.get("stat_type_extracted") == stat_type
                )
            )
        ):
            target_prop = prop
            break
    if not target_prop and (cb_doc.get("props") or []):
        # Last resort: use the first prop (all props in this doc are
        # for the same player, so park_team / home / opponent are
        # the same across all of them).
        target_prop = cb_doc["props"][0]
    if target_prop:
        if not home:
            home = (
                (target_prop.get("park_team") or "").strip().upper()
                or (target_prop.get("home_team") or "").strip().upper()
                or None
            )
        if not opp:
            opp = (
                (target_prop.get("opponent_team") or "").strip().upper()
                or (target_prop.get("opponent") or "").strip().upper()
                or None
            )
    return pt, home, opp


def _resolve_player_team_pair(pick: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Synchronous fast-path: pick-only resolver. Returns whatever the
    pick already carries; caller falls back to `_resolve_team_context`
    for the cached_board read when these are missing."""
    player_team = (pick.get("team") or "").strip().upper() or None
    opp = (pick.get("opponent") or pick.get("opponent_team") or "").strip().upper() or None
    return player_team, opp


def _build_park_dict(weather_blob: Optional[Dict[str, Any]], home_team: Optional[str]) -> Dict[str, Any]:
    """`MLBBadgeService.evaluate_wind_boost` expects a `park` dict with
    a `type` ("outdoor" / "dome") key, and `evaluate_hitters_haven`
    reads `name`, `factor`, `team`. Build that envelope from whatever
    we have in the weather blob (the existing MLBWeatherService folds
    stadium info into the weather dict)."""
    park: Dict[str, Any] = {}
    if weather_blob:
        # MLBWeatherService returns "type" ("outdoor"/"dome") and "stadium"
        # (full park name). It does NOT carry a numeric park factor.
        park["type"] = weather_blob.get("type")
        park["name"] = weather_blob.get("stadium")
    if home_team:
        park["team"] = home_team
    return park


def _adapt_weather_for_badge_service(
    weather_blob: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Translate `MLBWeatherService.get_weather()` output to the field
    names the badge service expects (`windspeed` vs `wind_speed`,
    `winddirection` vs `wind_direction`)."""
    if not weather_blob:
        return None
    return {
        "windspeed":     weather_blob.get("wind_speed", 0),
        "winddirection": weather_blob.get("wind_direction", 0),
        "temperature":   weather_blob.get("temperature"),
        "stadium":       weather_blob.get("stadium"),
        "type":          weather_blob.get("type"),
    }


async def _read_weather(player_team: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort cached read. Returns None silently on any failure."""
    if not player_team:
        return None
    try:
        from services.mlb_weather_service import get_weather_service
        ws = get_weather_service()
        return await ws.get_weather(player_team)
    except Exception:
        logger.debug("[MLB_ENV_BADGE] weather lookup failed for %s",
                     player_team, exc_info=False)
        return None


def _read_umpire_for_team(player_team: Optional[str], db: Any) -> Optional[Dict[str, Any]]:
    """The existing `RefereeScraperService` exposes per-team assignment
    lookups via `get_ref_for_team(team_abbrev)`. The MLB cold_zone gate
    keys on `strike_zone_ratio`; that field is not yet computed by the
    referee scraper for MLB umpires (it's an existing data gap, not
    addressed by this patch). When `strike_zone_ratio` is absent the
    badge service silently no-ops on `cold_zone` — exactly the
    behaviour the directive asks for ("don't fake / generate
    fallback intel")."""
    if not player_team:
        return None
    try:
        from services.referee_scraper_service import get_referee_service
        rs = get_referee_service(db)
        ref = rs.get_ref_for_team(player_team)
        if not ref:
            return None
        # Only carry through the keys the badge service actually reads.
        return {
            "name":              ref.get("umpire") or ref.get("crew_chief"),
            "strike_zone_ratio": ref.get("strike_zone_ratio"),
        }
    except Exception:
        logger.debug("[MLB_ENV_BADGE] umpire lookup failed for %s",
                     player_team, exc_info=False)
        return None


def _resolve_opponent_pitcher(pick: Dict[str, Any]) -> Optional[str]:
    """Return the opposing pitcher's name if the board pick already
    carries it (overlaid by the cached_board reader / intel_suite step).
    No collection lookups added by this patch."""
    candidates = (
        pick.get("opponent_pitcher"),
        pick.get("opp_pitcher"),
        (pick.get("intel_suite") or {}).get("opponent_pitcher"),
        (pick.get("matchup") or {}).get("opponent_pitcher"),
    )
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _merge_badges_into_scout(
    pick: Dict[str, Any], new_badges: list,
) -> None:
    """Append new badges to `pick['scout_badges']` deduped by
    `id` / `badge_key`. Pure write to the canonical scout-badges field;
    no other fields touched (per directive: do not write to
    `intel_suite.context_badges` in this patch)."""
    if not new_badges:
        return
    existing = list(pick.get("scout_badges") or [])
    seen = {(b.get("id") or b.get("badge_key"))
            for b in existing if isinstance(b, dict)}
    seen.discard(None)
    for b in new_badges:
        if not isinstance(b, dict):
            continue
        bid = b.get("id") or b.get("badge_key")
        if bid and bid not in seen:
            existing.append(b)
            seen.add(bid)
    pick["scout_badges"] = existing


# ─── public entry point ──────────────────────────────────────────────
async def apply_mlb_environmental_badges(pick: Dict[str, Any], db: Any) -> None:
    """Add MLB environmental badges to `pick['scout_badges']`. Mutates
    pick in place. Never raises — caller (`badge_enrichment.enrich_pick_badges`)
    also wraps in its own try/except as defense-in-depth.

    Steps:
        1. Resolve player_team / opponent_team from pick.
        2. Best-effort read weather (in-process cache).
        3. Best-effort read umpire (collection cache).
        4. Build `park` dict from weather blob + team.
        5. Translate weather field names for the badge service.
        6. Resolve opponent_pitcher from pick (no DB lookup added).
        7. Call MLBBadgeService.evaluate_all_badges with the populated
           context. Merge result into pick['scout_badges'] (deduped).
    """
    if not isinstance(pick, dict):
        return
    player_name = pick.get("player_name")
    stat_type = pick.get("stat_type") or _SUPPORTED_STAT_TYPES_FALLBACK
    line = pick.get("line")
    if not player_name:
        return

    player_team, home_team, _opp_team = await _resolve_team_context(pick, db)
    # Use home_team for stadium lookup when the player is the visiting team —
    # weather/park always belong to the host stadium.
    weather_team = home_team or player_team

    weather_blob = await _read_weather(weather_team)
    umpire_data = _read_umpire_for_team(weather_team, db)
    park = _build_park_dict(weather_blob, home_team or weather_team)
    weather = _adapt_weather_for_badge_service(weather_blob)
    opponent_pitcher = _resolve_opponent_pitcher(pick)

    # Fast no-op if NONE of the situational inputs are available — the
    # badge service would only return scout-side badges that the
    # universal scout step already produced.
    if not (weather or park.get("type") or umpire_data or opponent_pitcher):
        return

    try:
        from services.mlb_badge_system import get_mlb_badge_service
        badge_service = get_mlb_badge_service(db)
        new_badges = await badge_service.evaluate_all_badges(
            player_name=player_name,
            stat_type=stat_type,
            prop={"line": line, "player_name": player_name, "stat_type": stat_type},
            weather=weather,
            park=park if park.get("type") else None,
            umpire_data=umpire_data,
            opponent_pitcher=opponent_pitcher,
        )
    except Exception:
        logger.exception(
            "[MLB_ENV_BADGE] evaluate_all_badges raised for %s/%s",
            player_name, stat_type,
        )
        return

    _merge_badges_into_scout(pick, new_badges)


__all__ = [
    "apply_mlb_environmental_badges",
]
