"""Phase 2B — Live opposing-lineup feed adapter.

Closes the gap between training (post-hoc lineup from
`mlb_statcast_raw` via `mlb_lineup_resolver`) and live prediction
(today's game where the actual lineup isn't yet observed).

Resolution order
────────────────
For a live MLB pitcher prop the predict path calls
`fetch_opposing_lineup(db, opp_team, game_date, *, pitcher_team)`
which tries each source in order until one returns a non-empty list:

  1. **BDL confirmed/posted lineup** for `opp_team` on `game_date`
     (already loaded by `mlb_cached_board_builder.fetch_lineups`).
  2. **Last-game-played lineup** for `opp_team` — the most recent
     game with batting activity in `mlb_statcast_raw`. Captures the
     ~85% of slate-evening situations where lineups are posted
     within ~30 minutes of game time. Drops bench/IL changes.
  3. **None** — caller treats lineup features as imputed.

Output shape mirrors `mlb_lineup_resolver`:
    [{"batter_id": int, "stand": "L|R|S|None",
      "n_pitches": 0, "first_appearance_order": int}, ...]

This module is intentionally side-effect-free — it does NOT cache
across calls. The predict path is invoked once per recompute pass
and the BDL feed is already cached at the cached_board layer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mlb_live_lineup_feed")


def _normalize_team(t: Any) -> Optional[str]:
    if not t:
        return None
    return str(t).strip().upper()[:3] or None


def _normalize_stand(s: Any) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip().upper()[:1]
    return s if s in ("L", "R", "S") else None


def _fetch_bdl_lineup(db, opp_team: str,
                      game_date: str) -> Optional[List[Dict[str, Any]]]:
    """Pull today's BDL-posted lineup for `opp_team`. Returns None
    when no entry exists (lineup not yet posted).
    """
    coll = db["mlb_lineups"] if "mlb_lineups" in db.list_collection_names() else None
    if coll is None:
        return None
    try:
        doc = coll.find_one({
            "team_abbr": opp_team,
            "game_date": str(game_date)[:10],
        }, {"_id": 0})
    except Exception:
        return None
    if not doc:
        return None
    players = doc.get("players") or []
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(players, start=1):
        bid = p.get("mlb_id") or p.get("statcast_id") or p.get("batter_id")
        if bid is None:
            continue
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            continue
        out.append({
            "batter_id": bid,
            "stand": _normalize_stand(p.get("stand") or p.get("bats")),
            "n_pitches": 0,
            "first_appearance_order": i,
        })
    return out or None


async def _fetch_last_played_lineup_async(
    db, opp_team: str, game_date: str,
) -> Optional[List[Dict[str, Any]]]:
    """Find the team's most recent game prior to `game_date` in
    `mlb_statcast_raw` and return that game's batting order.
    """
    opp = _normalize_team(opp_team)
    if not opp:
        return None
    gd = str(game_date)[:10]

    # Most recent prior game where opp_team was either home OR away.
    pipe = [
        {"$match": {
            "$or": [{"home_team": opp}, {"away_team": opp}],
            "game_date": {"$lt": gd},
        }},
        {"$sort": {"game_date": -1, "game_pk": -1}},
        {"$limit": 1},
        {"$project": {"_id": 0, "game_pk": 1, "game_date": 1,
                       "home_team": 1, "away_team": 1}},
    ]
    try:
        hit = await db.mlb_statcast_raw.find_one({
            "$or": [{"home_team": opp}, {"away_team": opp}],
            "game_date": {"$lt": gd},
        }, sort=[("game_date", -1), ("game_pk", -1)],
           projection={"_id": 0, "game_pk": 1, "game_date": 1,
                        "home_team": 1, "away_team": 1})
    except AttributeError:
        # sync client fallback
        cur = db.mlb_statcast_raw.aggregate(pipe, allowDiskUse=True)
        hit = next(iter(cur), None)
    if not hit:
        return None
    is_home = (hit.get("home_team") == opp)
    inning_topbot = "Top" if is_home else "Bot"  # opp bats when they're not pitching

    # Pull distinct batters seen in that game, ordered by first at-bat.
    pipe2 = [
        {"$match": {
            "game_pk": hit["game_pk"],
            "inning_topbot": inning_topbot,
            "batter": {"$ne": None},
        }},
        {"$group": {
            "_id": "$batter",
            "stand": {"$first": "$stand"},
            "first_ab": {"$min": "$at_bat_number"},
            "n_pitches": {"$sum": 1},
        }},
        {"$sort": {"first_ab": 1}},
    ]
    try:
        cur = db.mlb_statcast_raw.aggregate(pipe2, allowDiskUse=True)
        rows: List[Dict[str, Any]] = []
        if hasattr(cur, "__aiter__"):
            async for r in cur:
                rows.append(r)
        else:
            rows.extend(list(cur))
    except Exception:
        return None

    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        bid = r.get("_id")
        if bid is None:
            continue
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            continue
        out.append({
            "batter_id": bid,
            "stand": _normalize_stand(r.get("stand")),
            "n_pitches": int(r.get("n_pitches") or 0),
            "first_appearance_order": i,
        })
        if len(out) >= 9:
            break
    return out or None


def fetch_opposing_lineup_sync(
    db, opp_team: str, game_date: str,
    *, pitcher_team: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Sync entry-point — preferred by training-side validation runs.

    Resolution: BDL → last-played fallback → None.
    """
    opp = _normalize_team(opp_team)
    if not opp:
        return None
    out = _fetch_bdl_lineup(db, opp, game_date)
    if out:
        return out
    # Last-played fallback uses the sync aggregate.
    try:
        gd = str(game_date)[:10]
        is_home_q = {"home_team": opp}
        is_away_q = {"away_team": opp}
        hit = db.mlb_statcast_raw.find_one(
            {"$or": [is_home_q, is_away_q],
             "game_date": {"$lt": gd}},
            {"_id": 0, "game_pk": 1, "game_date": 1,
             "home_team": 1, "away_team": 1},
            sort=[("game_date", -1), ("game_pk", -1)],
        )
        if not hit:
            return None
        is_home = (hit.get("home_team") == opp)
        inning_topbot = "Top" if is_home else "Bot"
        pipe2 = [
            {"$match": {"game_pk": hit["game_pk"],
                         "inning_topbot": inning_topbot,
                         "batter": {"$ne": None}}},
            {"$group": {"_id": "$batter",
                         "stand": {"$first": "$stand"},
                         "first_ab": {"$min": "$at_bat_number"},
                         "n_pitches": {"$sum": 1}}},
            {"$sort": {"first_ab": 1}},
        ]
        rows = list(db.mlb_statcast_raw.aggregate(pipe2, allowDiskUse=True))
        out: List[Dict[str, Any]] = []
        for i, r in enumerate(rows, start=1):
            bid = r.get("_id")
            if bid is None:
                continue
            try:
                bid = int(bid)
            except (TypeError, ValueError):
                continue
            out.append({
                "batter_id": bid,
                "stand": _normalize_stand(r.get("stand")),
                "n_pitches": int(r.get("n_pitches") or 0),
                "first_appearance_order": i,
            })
            if len(out) >= 9:
                break
        return out or None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"last-played fallback failed: {exc!r}")
        return None


async def fetch_opposing_lineup(
    db, opp_team: str, game_date: str,
    *, pitcher_team: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Async entry-point — wired into the live recompute path."""
    opp = _normalize_team(opp_team)
    if not opp:
        return None
    out = _fetch_bdl_lineup(db, opp, game_date)
    if out:
        return out
    return await _fetch_last_played_lineup_async(db, opp, game_date)
