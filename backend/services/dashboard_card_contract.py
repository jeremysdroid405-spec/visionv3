"""
Universal Dashboard Pick Card Contract  —  Sport-Agnostic Normalizer
====================================================================

Front-page tier dashboards (Safe Haven / Front Lines / War Zone) all
render the SAME UI component (`UniversalPlayerCard` compact mode).  To
keep the component sport-agnostic, every pick — regardless of sport —
must expose the same 8 fields:

    player_name      string
    team             string | null
    stat_line        string | null      "PTS 14.5"
    big_pick_text    string | null      "OVER 14.5 PTS"
    projection       float  | null
    hit_rate         float  | null      side-correct percentage
    avg              float  | null
    short_sentence   string | null      truncated vision_intel; no fallback

`stamp_dashboard_card_contract` is a PURE display-layer normalizer.

It NEVER:
  * mutates μ / σ / gates / thresholds / tier routing / selection
  * recomputes projections, hit-rates, edges, or any model output
  * removes or relabels any existing field — only ADDS the 8 above
  * generates fabricated `short_sentence` text — vision_intel or null

It is invoked once per Ferrari-tier API response (after the picks are
selected and post-processed) and is fully idempotent.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- friendly stat-name mappings (display-only) ---------------------
_STAT_SHORT_NBA = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Pts+Rebs+Asts": "P+R+A", "Pts+Rebs": "P+R", "Pts+Asts": "P+A",
    "Rebs+Asts": "R+A", "3-PT Made": "3PM", "Steals": "STL",
    "Blocks": "BLK", "Turnovers": "TO", "Stl+Blk": "S+B",
    "Double-Double": "DD", "Triple-Double": "TD",
}
_STAT_SHORT_MLB = {
    "Total Bases": "TB", "Hits": "H", "Singles": "1B", "Doubles": "2B",
    "Triples": "3B", "Home Runs": "HR", "Hits+Runs+RBIs": "H+R+RBI",
    "RBIs": "RBI", "Runs": "R", "Stolen Bases": "SB",
    "Batter Walks": "BB", "Batter Strikeouts": "K", "Earned Runs": "ER",
    "Hits Allowed": "HA", "Pitcher Strikeouts": "K", "Walks Allowed": "BB",
    "Pitches Thrown": "PIT", "Hits+Walks+Earned Runs": "H+BB+ER",
}
_MAX_SENTENCE = 140


def _stat_short(stat_type: str) -> str:
    if not stat_type:
        return ""
    return (
        _STAT_SHORT_NBA.get(stat_type)
        or _STAT_SHORT_MLB.get(stat_type)
        or stat_type.upper()
    )


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truncate_vision(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        raw = raw.get("summary") or raw.get("text") or raw.get("line")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) <= _MAX_SENTENCE:
        return s
    head = s[: _MAX_SENTENCE].rsplit(" ", 1)[0]
    return head + "…"


def to_card_contract(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Build the 8-field contract from any sport's pick dict."""
    # ── 1. player_name ────────────────────────────────────────────────
    player_name = pick.get("player_name") or pick.get("player") or pick.get("name")

    # ── 2. team ──────────────────────────────────────────────────────
    team = (
        pick.get("team")
        or pick.get("team_abbr")
        or pick.get("player_team")
        or pick.get("home_team_abbr")
        or pick.get("away_team_abbr")
    )

    # ── helpers ──────────────────────────────────────────────────────
    stat_type = pick.get("stat_type") or ""
    line = pick.get("line")
    side = (pick.get("recommendation") or pick.get("direction") or "").upper().strip()
    if side not in ("OVER", "UNDER"):
        side = "OVER"
    stat_short = _stat_short(stat_type)
    line_str = f"{line}" if line is not None else ""

    # ── 3. stat_line — "PTS 14.5" ────────────────────────────────────
    stat_line = (
        f"{stat_short} {line_str}".strip()
        if stat_short or line_str
        else None
    ) or None

    # ── 4. big_pick_text — "OVER 14.5 PTS" ───────────────────────────
    big_pick_text = (
        f"{side} {line_str} {stat_short}".strip()
        if line_str
        else None
    ) or None

    # ── 5. projection ────────────────────────────────────────────────
    projection = (
        _f(pick.get("vk_predicted"))
        or _f(pick.get("model_projection"))
        or _f(pick.get("projection"))
    )

    # ── 6. hit_rate — side-correct percentage ────────────────────────
    hr_over  = _f(pick.get("hit_rate_over"))
    hr_under = _f(pick.get("hit_rate_under"))
    if side == "UNDER" and hr_under is not None:
        hit_rate = hr_under
    elif hr_over is not None:
        hit_rate = hr_over
    else:
        # NBA cards already populate `h10_rate` upstream of this point.
        hit_rate = _f(pick.get("h10_rate")) or _f(pick.get("hit_rate"))

    # ── 7. avg — historical mean (any reasonable source) ─────────────
    avg = (
        _f(pick.get("season_avg"))
        or _f(pick.get("l20_avg"))
        or _f(pick.get("l10_avg"))
        or _f(pick.get("l5_avg"))
        or _f(pick.get("eb_player_career_mean"))   # MLB Empirical-Bayes prior
    )

    # ── 8. short_sentence — truncated vision_intel ONLY (no fallback) ─
    short_sentence = _truncate_vision(
        pick.get("vision_intel") or pick.get("vision_summary")
    )

    return {
        "player_name":    player_name,
        "team":           team,
        "stat_line":      stat_line,
        "big_pick_text":  big_pick_text,
        "projection":     projection,
        "hit_rate":       hit_rate,
        "avg":            avg,
        "short_sentence": short_sentence,
    }


async def stamp_dashboard_card_contract(
    db, picks: List[Dict[str, Any]], sport: str
) -> None:
    """Stamp the 8 contract fields onto every pick in `picks`, in place.

    Side-effect-only ADDITIVE. Never removes or rewrites existing keys.
    Idempotent: re-running over the same picks produces the same fields.

    For MLB, also merges `team` from `mlb_live_props` when the pick has
    no team-side identity field — `mlb_prop_scores` strips this on
    write, so we re-attach at response time.
    """
    if not picks:
        return
    sport = (sport or "").lower()

    # ── MLB-only: attach `team` from mlb_live_props (one batch query) ─
    if sport == "mlb":
        await _attach_mlb_team(db, picks)

    # ── Stamp the 8 contract fields onto every pick ───────────────────
    for p in picks:
        contract = to_card_contract(p)
        for k, v in contract.items():
            # Additive: only set when key absent OR currently null/empty.
            existing = p.get(k)
            if existing in (None, "", "—"):
                p[k] = v


async def _attach_mlb_team(db, picks: List[Dict[str, Any]]) -> None:
    need = [p for p in picks
            if not (p.get("team") or p.get("team_abbr"))
            and p.get("bdl_player_id") is not None
            and p.get("event_id")]
    if not need:
        return
    # Batch fetch live_props rows for the unique (bdl_id, event_id) pairs.
    pairs = list({(p["bdl_player_id"], p["event_id"]) for p in need})
    pids   = sorted({pid for pid, _ in pairs})
    eids   = sorted({eid for _, eid in pairs})
    cursor = db["mlb_live_props"].find(
        {"bdl_player_id": {"$in": pids}, "event_id": {"$in": eids}},
        {"_id": 0, "bdl_player_id": 1, "event_id": 1, "team": 1,
         "opponent_team": 1, "team_full": 1,
         "home_team": 1, "away_team": 1, "is_home_team": 1, "venue": 1},
    )
    lookup: Dict[tuple, Dict[str, Any]] = {}
    async for d in cursor:
        key = (d.get("bdl_player_id"), d.get("event_id"))
        if key not in lookup:
            lookup[key] = d
    stamped = 0
    for p in need:
        d = lookup.get((p["bdl_player_id"], p["event_id"]))
        if not d:
            continue
        # Additive only.
        for src, dest in (
            ("team", "team"),
            ("team_full", "team_full"),
            ("opponent_team", "opponent_abbr"),
            ("home_team", "home_team"),
            ("away_team", "away_team"),
            ("is_home_team", "is_home_team"),
            ("venue", "venue"),
        ):
            v = d.get(src)
            if v is not None and p.get(dest) in (None, "", "—"):
                p[dest] = v
        stamped += 1
    if stamped:
        logger.info("[CARD_CONTRACT:mlb] team stamped on %d/%d picks",
                    stamped, len(need))


__all__ = ["to_card_contract", "stamp_dashboard_card_contract"]
