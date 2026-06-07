"""
Grade completed team matchups and persist results into team_historical_outcomes.

Flow:
  team_matchups (has home_score + away_score)
    → team_live_props (prop rows keyed by event_id)
      → grade_row()
        → team_historical_outcomes (upsert by compound key)

Never mutates team_matchups. Never touches backtesting pipeline scripts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from pymongo import UpdateOne

from scripts.sgo._team_outcome_graders import grade_row
from services.team_live_xgb_scorer import classify_market_category

logger = logging.getLogger(__name__)

MATCHUPS_COLL = "team_matchups"
LIVE_PROPS_COLL = "team_live_props"
OUTCOMES_COLL = "team_historical_outcomes"

# Map market_key (Odds API token) → betTypeID expected by grade_row
_MARKET_KEY_TO_BET_TYPE: Dict[str, str] = {
    "h2h":         "ml",
    "spreads":     "sp",
    "team_totals": "ou",
}


def _build_grader_row(prop: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate a team_live_props doc into the dict shape grade_row accepts.

    Returns None for unknown/unsupported market keys.
    """
    market_key = (prop.get("market_key") or "").lower().strip()
    bet_type = _MARKET_KEY_TO_BET_TYPE.get(market_key)
    if not bet_type:
        return None

    team_id = prop.get("team_id")
    home_team_id = prop.get("home_team_id")
    side_raw = (prop.get("side") or "").upper()

    if market_key == "h2h":
        # team_live_props stores side="ML"; grade_h2h wants "home"/"away"
        side = "home" if team_id == home_team_id else "away"
        stat_entity = None
    elif market_key == "spreads":
        # "HOME" / "AWAY" — grade_spread's _side_norm handles these
        side = side_raw.lower()
        stat_entity = None
    else:
        # team_totals: "OVER"/"UNDER"; grade_team_total needs statEntityID
        side = side_raw.lower()
        stat_entity = "home" if team_id == home_team_id else "away"

    return {
        "betTypeID":     bet_type,
        "statEntityID":  stat_entity,
        "side":          side,
        "line":          prop.get("line"),
    }


def _compound_key(
    event_id: str,
    team_id: Optional[str],
    market_category: Optional[str],
    side: Optional[str],
    line: Any,
    book: Optional[str],
) -> Tuple:
    return (event_id, team_id, market_category, side, line, book)


async def grade_completed_team_matchups(
    db,
    sport: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade all completed matchups for `sport` (or both sports if None).

    Returns an audit dict with keys:
        sport, n_matchups_scanned, n_props_graded,
        n_already_resolved, n_upserted, n_errors
    """
    # ── 1. Load completed matchups ────────────────────────────────────
    matchup_filter: Dict[str, Any] = {
        "home_score": {"$exists": True, "$ne": None},
        "away_score": {"$exists": True, "$ne": None},
    }
    if sport:
        matchup_filter["sport"] = sport.lower()

    matchups: List[Dict[str, Any]] = await db[MATCHUPS_COLL].find(
        matchup_filter
    ).to_list(None)

    n_matchups_scanned = len(matchups)
    n_props_graded = 0
    n_already_resolved = 0
    n_upserted = 0
    n_errors = 0

    for matchup in matchups:
        event_id = matchup.get("event_id")
        if not event_id:
            continue

        home_score = matchup.get("home_score")
        away_score = matchup.get("away_score")
        matchup_sport = matchup.get("sport") or sport or ""

        # ── 2. Load props for this event ──────────────────────────────
        props: List[Dict[str, Any]] = await db[LIVE_PROPS_COLL].find(
            {"event_id": event_id}
        ).to_list(None)
        if not props:
            continue

        # ── 3. Fetch already-resolved compound keys for this event ────
        existing_docs = await db[OUTCOMES_COLL].find(
            {"event_id": event_id, "outcome_resolved": True},
            {"team_id": 1, "market_category": 1,
             "side": 1, "line": 1, "book": 1},
        ).to_list(None)

        resolved_keys: Set[Tuple] = {
            _compound_key(
                event_id,
                d.get("team_id"),
                d.get("market_category"),
                d.get("side"),
                d.get("line"),
                d.get("book"),
            )
            for d in existing_docs
        }
        n_already_resolved += len(resolved_keys)

        # ── 4. Grade each prop row ────────────────────────────────────
        ops: List[UpdateOne] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for prop in props:
            try:
                grader_row = _build_grader_row(prop)
                if grader_row is None:
                    n_errors += 1
                    continue

                outcome = grade_row(grader_row, home_score, away_score)
                market_category = classify_market_category(
                    prop.get("market_key")
                )

                key = _compound_key(
                    event_id,
                    prop.get("team_id"),
                    market_category,
                    prop.get("side"),
                    prop.get("line"),
                    prop.get("book"),
                )
                if key in resolved_keys:
                    continue  # already resolved — never overwrite

                filter_doc = {
                    "event_id":        event_id,
                    "team_id":         prop.get("team_id"),
                    "market_category": market_category,
                    "side":            prop.get("side"),
                    "line":            prop.get("line"),
                    "book":            prop.get("book"),
                }
                set_doc = {
                    **outcome,
                    "event_id":        event_id,
                    "team_id":         prop.get("team_id"),
                    "market_category": market_category,
                    "market_key":      prop.get("market_key"),
                    "side":            prop.get("side"),
                    "line":            prop.get("line"),
                    "book":            prop.get("book"),
                    "sport":           matchup_sport,
                    "home_score":      home_score,
                    "away_score":      away_score,
                    "graded_at":       now_iso,
                }
                ops.append(UpdateOne(
                    filter_doc,
                    {"$set": set_doc, "$setOnInsert": {"created_at": now_iso}},
                    upsert=True,
                ))
                n_props_graded += 1

            except Exception as exc:
                logger.error(
                    "[team_outcome_grader] prop grading error "
                    "event=%s market_key=%s: %s",
                    event_id, prop.get("market_key"), exc,
                )
                n_errors += 1

        # ── 5. Bulk-write this event's outcomes ───────────────────────
        if ops:
            try:
                result = await db[OUTCOMES_COLL].bulk_write(
                    ops, ordered=False
                )
                n_upserted += (
                    len(result.upserted_ids or {})
                    + int(result.modified_count or 0)
                )
            except Exception as exc:
                logger.error(
                    "[team_outcome_grader] bulk_write failed for "
                    "event=%s: %s",
                    event_id, exc,
                )
                n_errors += 1

    return {
        "sport":               sport or "all",
        "n_matchups_scanned":  n_matchups_scanned,
        "n_props_graded":      n_props_graded,
        "n_already_resolved":  n_already_resolved,
        "n_upserted":          n_upserted,
        "n_errors":            n_errors,
    }
