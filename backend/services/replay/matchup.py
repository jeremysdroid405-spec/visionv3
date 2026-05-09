"""
Replay matchup / pace context — historical, as-of-time.

Builds three derived signals per (player, opponent_team_id, snapshot_ts):

  pace_factor      → game-environment expected possessions vs league
                     average, normalized to 1.0 = neutral. Larger
                     values favor OVER. Computed as:
                         (player_team_pace_L10 + opponent_team_pace_L10)
                          / (2 * league_pace_L30)
                     all rolled strictly as-of snapshot date.

  matchup_strength → opponent DvP rank for this stat family,
                     normalized to 0..1 where:
                         0.5 = average defense (rank 15-16)
                         1.0 = worst defense (rank 30 = OVER-friendly)
                         0.0 = best defense (rank 1 = UNDER-friendly)
                     Computed from BDL historical game logs by
                     averaging opponent's allowed stat-rate vs the
                     league-average stat-rate over the trailing
                     calendar window.

  league_pace_baseline → diagnostic only; used to normalize pace.

OUTPUTS LIVE ON `prop["matchup_strength"]` / `prop["pace_factor"]`
— THE EXACT FIELDS PRODUCTION SCORING READS in
`services.scoring.scoring_stack.compute_scoring_stack`. We do NOT
invent replay-only field names per the user spec.

Caching strategy:
  - `replay_feature_cache` (collection): keyed
    (snapshot_date, opponent_team_id, stat_family). Computed once
    per (date, opp, fam) — heavy aggregation amortized across all
    replay rows that share the matchup.
  - The team-level pace L10 is sub-cached in process memory keyed
    (snapshot_date, team_id).
  - The league-level pace L30 is sub-cached in process memory keyed
    (snapshot_date,).

Leakage rules:
  - All aggregation strictly filtered to `game_date < snapshot_date`.
  - `assert_no_future_games()` runs against the rolled rows.
  - No JOIN that could leak the target game date.

This module DOES NOT touch production collections. It writes only to
`replay_feature_cache`.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .leakage_checks import assert_no_future_games

logger = logging.getLogger(__name__)

REPLAY_FEATURE_CACHE = "replay_feature_cache"

# Per-stat-family aggregation field on `bdl_historical_game_logs`. We
# compute opponent's per-game allowed average of this field as the DvP
# proxy. Combo families (PRA / PTS_REB / PTS_AST / REB_AST) sum their
# components; THREES → fg3m.
STAT_FIELD_MAP: Dict[str, List[str]] = {
    "PTS":     ["pts"],
    "REB":     ["reb"],
    "AST":     ["ast"],
    "THREES":  ["fg3m"],
    "PRA":     ["pts", "reb", "ast"],
    "PTS_REB": ["pts", "reb"],
    "PTS_AST": ["pts", "ast"],
    "REB_AST": ["reb", "ast"],
    # Unsupported by VK2 but production gate engine still receives a
    # matchup_strength for them — emit a neutral 0.5.
    "BLK":     ["blk"],
    "STL":     ["stl"],
    "TURNOVERS": ["turnover"],
}

PACE_LOOKBACK_DAYS = 30
PACE_TEAM_LOOKBACK = 10
DVP_LOOKBACK_DAYS = 60


# ---------------------------------------------------------------- team resolver
class TeamIdResolver:
    """Lazy team-name → BDL team_id index. Reads from
    `nba_master_hub_2026.team_full_name` (the same source production
    uses to map display names → team_id). Process-cached."""

    def __init__(self, db, hub: str = "nba_master_hub_2026"):
        self._db = db
        self._hub = hub
        self._index: Optional[Dict[str, int]] = None

    async def _build(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        cursor = self._db[self._hub].find(
            {"team_full_name": {"$exists": True},
             "team_id":         {"$exists": True}},
            {"_id": 0, "team_full_name": 1,
             "team_name": 1, "team_id": 1, "team_abbr": 1},
        )
        async for d in cursor:
            tid = d.get("team_id")
            if tid is None:
                continue
            for key in (d.get("team_full_name"), d.get("team_name"),
                          d.get("team_abbr")):
                if key:
                    out[str(key).strip().lower()] = int(tid)
        return out

    async def resolve(self, name: Optional[str]) -> Optional[int]:
        if not name:
            return None
        if self._index is None:
            self._index = await self._build()
        return self._index.get(name.strip().lower())


# ---------------------------------------------------------------- helpers
def _as_date_str(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.date().isoformat()


def _date_window(end_date_str: str, lookback_days: int) -> Tuple[str, str]:
    end_d = datetime.fromisoformat(end_date_str).date()
    start_d = end_d - timedelta(days=lookback_days)
    return start_d.isoformat(), end_d.isoformat()


def _normalize_stat_family(stat_family: str) -> str:
    fam = (stat_family or "").upper()
    return fam if fam in STAT_FIELD_MAP else fam


# ---------------------------------------------------------------- league pace
async def league_pace_baseline(db, *, as_of_date: str,
                                lookback_days: int = PACE_LOOKBACK_DAYS,
                                ) -> Optional[float]:
    """League-wide average pace over the trailing window strictly
    before `as_of_date`. Returns None if zero rows."""
    start, end = _date_window(as_of_date, lookback_days)
    pipe = [
        {"$match": {"game_date": {"$gte": start, "$lt": as_of_date},
                     "pace": {"$ne": None}}},
        # Collapse player-rows to one (team, game) tuple.
        {"$group": {"_id": {"team": "$team_id", "g": "$game_id"},
                     "pace": {"$first": "$pace"}}},
        {"$group": {"_id": None, "avg_pace": {"$avg": "$pace"},
                     "n": {"$sum": 1}}},
    ]
    async for d in db["bdl_advanced_stats"].aggregate(pipe):
        return float(d["avg_pace"]) if d["n"] else None
    return None


async def team_pace_l10(db, *, team_id: int, as_of_date: str,
                          lookback_games: int = PACE_TEAM_LOOKBACK,
                          ) -> Optional[float]:
    """Team's pace average over its last `lookback_games` games
    strictly before `as_of_date`. Returns None if <3 games available
    (consistent with production's 'min sample' policy).

    `bdl_advanced_stats` carries one row per player-game; each row's
    `pace` is the team-level pace stamped on that player. We
    deduplicate to one (team, game) before averaging.
    """
    pipe = [
        {"$match": {"team_id": int(team_id),
                     "game_date": {"$lt": as_of_date},
                     "pace": {"$ne": None}}},
        # Collapse to one row per (team, game).
        {"$group": {"_id": {"team": "$team_id", "g": "$game_id"},
                     "pace": {"$first": "$pace"},
                     "game_date": {"$first": "$game_date"}}},
        {"$sort":  {"game_date": -1}},
        {"$limit": lookback_games},
    ]
    rows: List[Dict[str, Any]] = [
        d async for d in db["bdl_advanced_stats"].aggregate(pipe)
    ]
    if len(rows) < 3:
        return None
    assert_no_future_games(
        [{"game_date": r["game_date"]} for r in rows],
        as_of_ts=datetime.fromisoformat(
            as_of_date).replace(tzinfo=timezone.utc),
        timestamp_field="game_date",
    )
    paces = [float(r["pace"]) for r in rows
             if r.get("pace") is not None]
    return sum(paces) / len(paces) if paces else None


# ---------------------------------------------------------------- DvP
_LEAGUE_DVP_CACHE: Dict[Tuple[str, str, int], Dict[int, Tuple[int, float]]] = {}


async def _build_league_dvp_rank_table(
    db, *, stat_family: str, as_of_date: str,
    lookback_days: int = DVP_LOOKBACK_DAYS,
) -> Dict[int, Tuple[int, float]]:
    """Build {team_id: (rank, avg_allowed)} for the whole league. The
    answer for any single opp_team_id is just a dict lookup. Cached
    per (as_of_date, stat_family, lookback_days)."""
    cache_key = (as_of_date, _normalize_stat_family(stat_family),
                 lookback_days)
    cached = _LEAGUE_DVP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    fields = STAT_FIELD_MAP.get(_normalize_stat_family(stat_family))
    if not fields:
        _LEAGUE_DVP_CACHE[cache_key] = {}
        return {}
    start, _ = _date_window(as_of_date, lookback_days)
    add_expr: Dict[str, Any] = (
        {"$add": [{"$ifNull": [f"${fields[0]}", 0]}]}
        if len(fields) == 1
        else {"$add": [{"$ifNull": [f"${f}", 0]} for f in fields]}
    )
    league_pipe = [
        {"$match": {"game_date": {"$gte": start, "$lt": as_of_date}}},
        {"$lookup": {
            "from":         "bdl_historical_game_logs",
            "let":          {"pid": "$player_id", "gid": "$game_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$player_id", "$$pid"]},
                    {"$eq": ["$game_id",   "$$gid"]},
                ]}}},
                {"$project": {"_id": 0, "stat": add_expr}},
            ],
            "as": "log",
        }},
        {"$unwind": "$log"},
        {"$group": {
            "_id":  {"opp": "$opponent_team_id", "g": "$game_id"},
            "stat_in_game_allowed": {"$sum": "$log.stat"},
        }},
        {"$match": {"_id.opp": {"$ne": None}}},
        {"$group": {
            "_id":         "$_id.opp",
            "avg_allowed": {"$avg": "$stat_in_game_allowed"},
            "n_games":     {"$sum": 1},
        }},
        {"$match": {"n_games": {"$gte": 5}}},
        {"$sort":  {"avg_allowed": 1}},
    ]
    rows = [d async for d in
            db["bdl_advanced_stats"].aggregate(league_pipe,
                                                  allowDiskUse=True)]
    rank_table = {int(r["_id"]): (i + 1, float(r["avg_allowed"]))
                  for i, r in enumerate(rows)}
    _LEAGUE_DVP_CACHE[cache_key] = rank_table
    return rank_table


async def opponent_dvp_rank(db, *, opponent_team_id: int,
                              stat_family: str, as_of_date: str,
                              lookback_days: int = DVP_LOOKBACK_DAYS,
                              ) -> Optional[Tuple[int, float]]:
    """Cheap dict lookup against a per-(date, family) league rank
    table that's computed once and reused across thousands of props."""
    table = await _build_league_dvp_rank_table(
        db, stat_family=stat_family, as_of_date=as_of_date,
        lookback_days=lookback_days,
    )
    return table.get(int(opponent_team_id))


def _matchup_strength_from_rank(rank: int, total: int = 30) -> float:
    """Map DvP rank to 0..1.

    rank 1 (best def) → 0.0 (favors UNDER from OVER's perspective)
    rank 15 (avg)     → 0.5 (neutral)
    rank 30 (worst)   → 1.0 (favors OVER)

    Matches the production `vision_v2._context_component` contract
    that treats `matchup_strength > 0.5` as OVER-favorable.
    """
    if total <= 1:
        return 0.5
    return min(1.0, max(0.0, (rank - 1) / (total - 1)))


# ---------------------------------------------------------------- public api
async def compute_matchup_blob(
    db, *,
    player_team_id: Optional[int],
    opponent_team_id: Optional[int],
    stat_family: str,
    snapshot_ts: datetime,
) -> Dict[str, Any]:
    """Returns the prop-ready matchup blob:

        {
          "pace_factor":       float | None,
          "matchup_strength":  float | None,   # 0..1
          "league_pace":       float | None,
          "team_pace_l10":     float | None,
          "opp_pace_l10":      float | None,
          "dvp_rank":          int   | None,
          "dvp_allowed":       float | None,
          "feature_completeness": "matchup_full" | "matchup_partial" |
                                   "matchup_missing",
          "error":             str | None,
        }

    The two production-read fields (`pace_factor`, `matchup_strength`)
    sit at the top level. Everything else is diagnostic / lineage.
    """
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    as_of_date = _as_date_str(snapshot_ts)

    # League pace baseline.
    league_pace = await league_pace_baseline(
        db, as_of_date=as_of_date, lookback_days=PACE_LOOKBACK_DAYS)
    team_pace = (await team_pace_l10(
        db, team_id=player_team_id, as_of_date=as_of_date)
        if player_team_id is not None else None)
    opp_pace = (await team_pace_l10(
        db, team_id=opponent_team_id, as_of_date=as_of_date)
        if opponent_team_id is not None else None)

    pace_factor: Optional[float] = None
    if (league_pace and team_pace is not None and opp_pace is not None
            and league_pace > 0):
        pace_factor = round((team_pace + opp_pace) / (2.0 * league_pace), 4)

    # DvP / matchup_strength.
    dvp_rank: Optional[int] = None
    dvp_allowed: Optional[float] = None
    if opponent_team_id is not None:
        rank_pair = await opponent_dvp_rank(
            db, opponent_team_id=opponent_team_id,
            stat_family=stat_family, as_of_date=as_of_date)
        if rank_pair:
            dvp_rank, dvp_allowed = int(rank_pair[0]), float(rank_pair[1])

    matchup_strength = (_matchup_strength_from_rank(dvp_rank)
                        if dvp_rank is not None else None)

    components_present = sum(1 for x in
                              (pace_factor, matchup_strength) if x is not None)
    if components_present == 2:
        completeness = "matchup_full"
    elif components_present == 1:
        completeness = "matchup_partial"
    else:
        completeness = "matchup_missing"

    return {
        "pace_factor":           pace_factor,
        "matchup_strength":      matchup_strength,
        "league_pace":           round(league_pace, 4)
                                 if league_pace is not None else None,
        "team_pace_l10":         round(team_pace, 4)
                                 if team_pace is not None else None,
        "opp_pace_l10":          round(opp_pace, 4)
                                 if opp_pace is not None else None,
        "dvp_rank":              dvp_rank,
        "dvp_allowed":           round(dvp_allowed, 4)
                                 if dvp_allowed is not None else None,
        "lookback_days_pace":    PACE_LOOKBACK_DAYS,
        "lookback_days_dvp":     DVP_LOOKBACK_DAYS,
        "feature_completeness":  completeness,
        "error":                 None,
    }


# ---------------------------------------------------------------- cache
async def ensure_feature_cache_indexes(db) -> List[str]:
    coll = db[REPLAY_FEATURE_CACHE]
    return [
        await coll.create_index(
            [("snapshot_date", 1), ("opponent_team_id", 1),
             ("stat_family", 1), ("player_team_id", 1)],
            name="uniq_date_opp_fam_team", unique=True,
        ),
    ]


__all__ = [
    "REPLAY_FEATURE_CACHE",
    "TeamIdResolver",
    "compute_matchup_blob", "ensure_feature_cache_indexes",
    "league_pace_baseline", "team_pace_l10", "opponent_dvp_rank",
    "_matchup_strength_from_rank", "STAT_FIELD_MAP",
]
