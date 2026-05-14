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
        "hit_rate_over": doc.get("hit_rate_over"),     # legacy alias surfaced for back-compat
        "hit_rate_under": doc.get("hit_rate_under"),
        # 2026-05-01 — Universal hit-rate window trio so the
        # player-detail page is byte-equivalent to the pick card
        # on L20 (gate input) / L10 / L5 / sample size.
        # SSOT Tier F (2026-05-04): OVER-side L20 reads canonical
        # `hit_rate_l20` first with legacy `hit_rate_over` fallback
        # for pre-dual-write docs.
        "hit_rate_l5":     doc.get("hit_rate_l5"),
        "hit_rate_l10":    doc.get("hit_rate_l10"),
        "hit_rate_l20":    (
            doc.get("hit_rate_under")
            if (doc.get("recommendation") or "OVER").upper() == "UNDER"
            else (doc.get("hit_rate_l20") or doc.get("hit_rate_over"))
        ),
        "hit_rate_sample_size": doc.get("hit_rate_sample_size"),
        # SSOT Tier F #2 (2026-05-04): canonical `edge_vs_fair` (was
        # legacy `edge_pct`).
        "edge_vs_fair": doc.get("edge_vs_fair"),
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
        "vision_score_raw": doc.get("vision_score_raw"),
        "event_id": doc.get("event_id"),
        "game_start_utc": doc.get("game_start_utc"),
        "canonical_key": doc.get("canonical_key"),
        "bdl_player_id": doc.get("bdl_player_id"),
        "cv": doc.get("cv"),
        "model_projection": doc.get("model_projection"),
        "sport": doc.get("sport"),
        # Read-side decoration written by master_sync Step 4
        # (`_enrich_nba_momentum`). Pure UI display field; not part of
        # scoring math.
        "momentum_data": doc.get("momentum_data"),
        # Vision Intel narrative + cache fingerprint written by
        # master_sync Step 6 (`_enrich_nba_board_vision_intel`) for
        # board-tier picks only. Pure UI display fields.
        "vision_intel": doc.get("vision_intel"),
        "vision_intel_content_hash": doc.get("vision_intel_content_hash"),
        "vision_intel_generated_at": doc.get("vision_intel_generated_at"),
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
#
# 2026-05-05 SSOT enforcement: `hit_rates` removed from this list.
# `cached_board.hit_rates` is a line-dependent nested bag whose
# l*_rate / l*_hit_count fields would mask the canonical, side-aware
# `hit_rate_l5/l10/l20` carried on the score doc. Per-prop overlay
# kept hit_rates aligned to the same line, but the stat-level
# fallback below joins on (player, stat) only and would smear stale
# hit_rates across lines. Score doc fields are the SSOT for the
# user-visible L5/L10/L20 trio.
#
# 2026-05-07 P0 Phase 4B: `h5_rate`, `h10_rate`, `h20_rate` removed
# from this overlay list. Same SSOT logic as the `hit_rates` removal:
# canonical `hit_rate_l5/l10/l20` is the score-doc SSOT (verified
# 100% present on all visible picks pre-removal). Bringing the
# legacy aliases over from cached_board duplicated and routinely
# diverged from the canonical (audit found 9/20 NBA picks where
# `h5_rate` ≠ `hit_rate_l5` by 5-20 percentage points).
_BOARD_ENRICHMENT_FIELDS = (
    "l5_avg", "l10_avg", "l20_avg", "season_avg",
    "intel_suite", "scout_badges", "context_badges",
    "active_badges", "vision_intel", "vision_summary",
    "momentum_data",
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


# Cross-pipeline stat-name alias (2026-04-24).
# Canonical SSOT lives in `services/scoring/stat_family.py`.
# Imported here so routes/player.py and routes/ferrari_tiers.py share
# one normalizer and cannot drift apart.
from services.scoring.stat_family import canonical_stat_family as _canonical_stat_family  # noqa: E402
from services.observability import log_silent_failure


async def _build_nba_cached_board_index(player_name: str) -> Dict[str, Dict[tuple, Dict[str, Any]]]:
    """Return three indices for ONE player:
       by_line:  (STAT_U, line_f, DIR_U) -> {"prop","player"}
       by_stat:  (STAT_U,)               -> {"prop","player"}  (first match)
       player_doc: the parent cached_board doc
    Accommodates line drift between `nba_cached_board` and
    `nba_prop_scores` — averages/intel are line-agnostic at the
    player-stat level, so the stat-level index lets us enrich even
    when lines have drifted (or the line isn't in cached_board).
    """
    empty = {"by_line": {}, "by_stat": {}, "player_doc": None}
    if _db is None:
        return empty
    rx = re.compile(f"^{_escape_regex(player_name)}$", re.I)
    player_doc = await _db["nba_cached_board"].find_one(
        {"player_name": rx}, {"_id": 0}
    )
    if not player_doc:
        return empty
    by_line: Dict[tuple, Dict[str, Any]] = {}
    by_stat: Dict[tuple, Dict[str, Any]] = {}
    for p in (player_doc.get("props") or []):
        if not isinstance(p, dict):
            continue
        stat_u = _canonical_stat_family(p.get("stat_type"))
        if not stat_u:
            continue
        try:
            line_f = float(p.get("line")) if p.get("line") is not None else None
        except (TypeError, ValueError):
            line_f = None
        dir_u = (p.get("direction") or "").strip().upper()
        entry = {"prop": p, "player": player_doc}
        if line_f is not None and dir_u:
            by_line.setdefault((stat_u, line_f, dir_u), entry)
        by_stat.setdefault((stat_u,), entry)
    return {"by_line": by_line, "by_stat": by_stat, "player_doc": player_doc}


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

    # ID-first resolution (2026-04-24). If the path param is a numeric
    # bdl_player_id, resolve to the canonical display_name via
    # master_hub first, then continue by name. Stable identity —
    # immune to event_id / canonical_key / line drift.
    identity_path = None  # diagnostic flag returned in payload
    param = str(player_name or "").strip()
    if param.isdigit():
        try:
            bdl_int = int(param)
        except (TypeError, ValueError):
            bdl_int = None
        if bdl_int is not None:
            hub_by_id = await _db[f"{sport}_master_hub_2026"].find_one(
                {"$or": [
                    {"bdl_player_id": bdl_int},
                    {"bdl_id": bdl_int},
                    {"nba_id": bdl_int},
                ]},
                {"_id": 0, "display_name": 1, "player_name": 1},
            )
            if hub_by_id:
                resolved = hub_by_id.get("display_name") or hub_by_id.get("player_name")
                if resolved:
                    player_name = resolved
                    identity_path = "bdl_player_id"
    if identity_path is None:
        identity_path = "player_name"

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

    # Route-level fallback (2026-04-24): even when score_docs is empty,
    # if the player exists in master_hub return a 200 with whatever
    # identity + cached_board enrichment is available. Only 404 when
    # the player genuinely doesn't exist in master_hub.
    hub_preview = await _lookup_player_hub(sport, player_name)
    if not score_docs and not hub_preview:
        raise HTTPException(
            status_code=404,
            detail=f"Player '{player_name}' not found in scores or master_hub ({sport})",
        )

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
    # The cached_board lines often drift from the scored lines (e.g.
    # PTS 13.5 in cached_board vs PTS 11.5 in the score doc) because
    # both pipelines sync independently. Line-agnostic fields
    # (player-stat averages, intel_suite, badges, season margin) are
    # IDENTICAL across lines for a given (player, stat), so we fall
    # back to a stat-level match when the exact (stat, line, dir)
    # triple misses.
    #
    # 2026-05-05 SSOT enforcement: `hit_rates` excluded — line-dependent
    # data must NOT cross line boundaries. See `_BOARD_ENRICHMENT_FIELDS`
    # above for the full rationale.
    STAT_LEVEL_FIELDS = {
        "l5_avg", "l10_avg", "l20_avg", "season_avg",
        "intel_suite", "scout_badges", "context_badges", "active_badges",
        "vision_intel", "vision_summary",
        "movement_delta", "movement_direction", "movement_strength",
        "is_anomaly", "is_goblin_anomaly", "is_demon_anomaly",
        "is_vision_enriched",
        "season_margin",
    }
    if sport == "nba" and props:
        idx = await _build_nba_cached_board_index(canonical_name)
        by_line = idx["by_line"]
        by_stat = idx["by_stat"]
        cb_player = idx["player_doc"] or {}
        live_by_key = await _fetch_live_props_for_player(sport, canonical_name)
        for p in props:
            try:
                line_f = float(p["line"])
            except (TypeError, ValueError):
                line_f = None
            stat_u = _canonical_stat_family(p.get("stat_type"))
            dir_u = (p.get("recommendation") or "").strip().upper()
            # Exact (stat, line, dir)
            entry = by_line.get((stat_u, line_f, dir_u))
            if entry is None:
                opp = "UNDER" if dir_u == "OVER" else "OVER"
                entry = by_line.get((stat_u, line_f, opp))
            # Line-level overlay (full enrichment, incl. line-specific fields)
            # 2026-05-05 SSOT firewall: even line-exact cached_board
            # entries are non-owner sources for `prop_scores`-owned
            # fields. The firewall blocks owned-field overwrites and
            # honours sticky-write semantics.
            from services.field_ownership.firewall import safe_overlay
            if entry:
                board_prop = entry.get("prop") or {}
                safe_overlay(
                    p,
                    {f: board_prop.get(f) for f in _BOARD_ENRICHMENT_FIELDS
                     if board_prop.get(f) is not None},
                )
            # Stat-level overlay (line-agnostic fields). Fires whether
            # or not the line-level match succeeded.
            stat_entry = by_stat.get((stat_u,))
            if stat_entry:
                sp = stat_entry.get("prop") or {}
                safe_overlay(
                    p,
                    {f: sp.get(f) for f in STAT_LEVEL_FIELDS
                     if sp.get(f) is not None},
                )
            # Player-level fields from cached_board parent
            safe_overlay(
                p,
                {f: cb_player.get(f) for f in _BOARD_PLAYER_FIELDS
                 if cb_player.get(f) not in (None, "", [])},
            )
            # Live props: event/team
            lp = live_by_key.get(p.get("canonical_key") or "")
            if lp:
                if not p.get("home_team") and lp.get("home_team"):
                    p["home_team"] = lp["home_team"]
                if not p.get("away_team") and lp.get("away_team"):
                    p["away_team"] = lp["away_team"]
                if not p.get("bdl_player_id") and lp.get("bdl_player_id"):
                    p["bdl_player_id"] = lp["bdl_player_id"]
            # Headshot URL rewrite — see routes/ferrari_tiers.py for the
            # ingress-routing rationale. /static/* hits the React dev
            # server and returns the app shell; /api/static/* is mounted
            # on the backend and serves the PNG directly.
            for fld in ("photo_url", "headshot_url"):
                v = p.get(fld)
                if isinstance(v, str) and v.startswith("/static/"):
                    p[fld] = "/api" + v

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
    # Collect any cached_board parent fields captured above (or re-derive).
    board_fallback: Dict[str, Any] = {}
    if sport == "nba" and score_docs:
        try:
            idx2 = await _build_nba_cached_board_index(canonical_name)
            pp = idx2.get("player_doc") or {}
            for fld in _BOARD_PLAYER_FIELDS:
                v = pp.get(fld)
                if v not in (None, "", []):
                    board_fallback[fld] = v
        except Exception as _swept_exc:
            log_silent_failure("routes.player.get_player_with_badges", _swept_exc)  # sweep-auto-converted
    team = hub.get("team") or hub.get("team_full") or board_fallback.get("team")
    position = hub.get("position") or board_fallback.get("position")
    photo_url = (
        hub.get("photo_url") or hub.get("headshot_url")
        or board_fallback.get("photo_url") or board_fallback.get("headshot_url")
    )
    # Rewrite /static/... → /api/static/... so the k8s ingress routes
    # to the backend (not the React dev server which serves app-shell HTML).
    if isinstance(photo_url, str) and photo_url.startswith("/static/"):
        photo_url = "/api" + photo_url
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

    # 2026-05-15 — Defensive board read. Lifecycle contract requires
    # `active=True` on every served doc. Inactive docs (orphans
    # scheduled for TTL purge) are NEVER returned. Docs missing the
    # `active` field are surfaced as a warning so we can spot any
    # publisher path bypassing services/boards/board_lifecycle.py.
    docs = await _db[f"{sport}_prop_scores"].find(
        {"version_tag": version_tag, "active": True},
        {"_id": 0},
    ).sort([("ranking_score_v2", -1)]).to_list(length=limit * 20)

    # Detect lifecycle-non-compliant docs (would have been served if
    # not for the explicit `active=True` filter). Log-only.
    try:
        n_missing = await _db[f"{sport}_prop_scores"].count_documents({
            "version_tag": version_tag,
            "$or": [
                {"active": {"$exists": False}},
                {"active": None},
            ],
        })
        if n_missing:
            import logging as _logging
            _logging.getLogger("services.boards.lifecycle").warning(
                "[BOARD:%s] %d docs at version_tag=%s lack `active` "
                "field — publisher path bypassing universal "
                "lifecycle helper (services/boards/board_lifecycle.py). "
                "Run /api/v3/admin/board-lifecycle/normalize.",
                sport, n_missing, version_tag,
            )
    except Exception:  # noqa: BLE001
        pass

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
