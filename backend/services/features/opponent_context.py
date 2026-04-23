"""NBA opponent-context feature pipeline (2026-04-23).

Purpose
-------
Produce clean, numeric, reproducible opponent-context features that can
be dropped into both the retrain pipeline and live scoring WITHOUT
reusing any Vision Intel outputs. All features are lagged (no same-game
leakage): the target game's own stats never contribute to the features
attached to that game.

Source data (read-only, MongoDB)
--------------------------------
- `bdl_historical_game_logs` : per-player box-score rows with
  {player_id, game_id, team_id, date, pts, reb, ast, fg3m, ...}.
  Team-game totals are summed to the `bdl_team_totals` index.
- `bdl_advanced_stats`       : per-player advanced row with
  {game_id, team_id, opponent_team_id, is_home, pace,
  defensive_rating, offensive_rating}. Team-level pace / ratings
  are averaged across player rows in the same (team_id, game_id).

Features emitted (14)
---------------------
1. Stat-family opponent allowed (L10, lagged):
   - opp_pts_allowed_L10
   - opp_reb_allowed_L10
   - opp_ast_allowed_L10
   - opp_3pm_allowed_L10
2. Relative strength (opponent_allowed − league_avg_allowed):
   - opp_pts_allowed_vs_avg
   - opp_reb_allowed_vs_avg
   - opp_ast_allowed_vs_avg
   - opp_3pm_allowed_vs_avg
3. Team context (L10 rolling, lagged):
   - opp_def_rating
   - opp_pace
   - team_pace
4. Situational (target game):
   - home_flag        (1.0 home, 0.0 away, 0.0 unknown)
   - rest_days        (0..6, capped; 3 if season opener / no prior game)
   - back_to_back_flag (1 if rest_days == 0 else 0)

Public API
----------
- `build_opponent_context_store(sync_db, seasons)` : construct once per
  pipeline run. Returns an `OpponentContextStore`.
- `store.get_features(team_id, opponent_team_id, game_id, game_date,
                      is_home=None)` : returns a dict of the 14
  features, or a zero-safe dict if any dependency is missing.

The store is pure in-memory and read-only; it does no mutation against
Mongo. Training and live scoring import the SAME module so features
cannot drift between them.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


FEATURE_SCHEMA: Tuple[str, ...] = (
    # Opponent allowed (L10, lagged)
    "opp_pts_allowed_L10",
    "opp_reb_allowed_L10",
    "opp_ast_allowed_L10",
    "opp_3pm_allowed_L10",
    # Relative to league average
    "opp_pts_allowed_vs_avg",
    "opp_reb_allowed_vs_avg",
    "opp_ast_allowed_vs_avg",
    "opp_3pm_allowed_vs_avg",
    # Team context (L10 rolling, lagged)
    "opp_def_rating",
    "opp_pace",
    "team_pace",
    # Situational (target game)
    "home_flag",
    "rest_days",
    "back_to_back_flag",
)
assert len(FEATURE_SCHEMA) == 14


# Caps for rest-day feature. Capped at 6 days so preseason / trade gaps
# don't produce extreme values the model hasn't seen in training.
_REST_DAYS_CAP = 6
_REST_DAYS_DEFAULT = 3  # season opener / missing prior game


def _safe_date(value: Any) -> Optional[_date]:
    if value is None:
        return None
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                return datetime.strptime(s[:len(fmt) - 2 if "%f" in fmt else None] or s, fmt).date()
            except Exception:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            return None
    return None


@dataclass
class OpponentContextStore:
    """In-memory index of team-game-level allowed stats + rolling means."""

    # per (team_id, game_id) → {pts, reb, ast, fg3m, game_date}
    team_game_allowed: Dict[Tuple[int, int], Dict[str, float]] = field(
        default_factory=dict,
    )
    # per (team_id, game_id) → {pace, defensive_rating, offensive_rating, is_home, opponent_team_id}
    team_game_context: Dict[Tuple[int, int], Dict[str, Any]] = field(
        default_factory=dict,
    )
    # per team_id, chronologically sorted list of (game_date, game_id)
    team_games_sorted: Dict[int, List[Tuple[_date, int]]] = field(
        default_factory=dict,
    )
    # League average allowed per (pts/reb/ast/fg3m) across all team-games
    # in the loaded seasons. Used for the *_vs_avg differentials.
    league_avg_allowed: Dict[str, float] = field(default_factory=dict)
    # Opponent map: game_id → {team_id: opponent_team_id}
    opponent_map: Dict[int, Dict[int, int]] = field(default_factory=dict)
    seasons: Tuple[int, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Rolling-window helper: given a team's chronological game list,
    # return the list of (pts/reb/ast/fg3m) allowed in the N games
    # STRICTLY BEFORE the target game (leakage-safe).
    # ------------------------------------------------------------------
    def _prior_games(self, team_id: int, game_id: int) -> List[Tuple[_date, int]]:
        seq = self.team_games_sorted.get(team_id) or []
        if not seq:
            return []
        # Find index of the target game; slice everything before it.
        for i, (_gdate, gid) in enumerate(seq):
            if gid == game_id:
                return seq[:i]
        # Target game not in training set — means it's a future / live
        # game, so every recorded game is "prior".
        return list(seq)

    def _rolling_allowed(
        self, team_id: int, game_id: int, window: int = 10,
    ) -> Dict[str, float]:
        """Mean of stats that OPPONENTS scored against `team_id` across
        the last `window` games strictly before `game_id`.

        `team_game_allowed[(t, g)]` stores what team `t` SCORED in game
        `g` (summed box-score totals). "What team_id allowed" is
        therefore indexed by the OPPONENT's (team, game) key, resolved
        via `opponent_map`.
        """
        prior = self._prior_games(team_id, game_id)
        if not prior:
            return {k: 0.0 for k in ("pts", "reb", "ast", "fg3m")}
        window_games = prior[-window:]
        vals = {"pts": [], "reb": [], "ast": [], "fg3m": []}
        for _gdate, gid in window_games:
            opp_id = (self.opponent_map.get(int(gid)) or {}).get(int(team_id))
            if opp_id is None:
                continue
            row = self.team_game_allowed.get((int(opp_id), int(gid)))
            if not row:
                continue
            for k in vals:
                v = row.get(k)
                if v is not None:
                    vals[k].append(float(v))
        out: Dict[str, float] = {}
        for k, arr in vals.items():
            out[k] = float(sum(arr) / len(arr)) if arr else 0.0
        return out

    def _rolling_context(
        self, team_id: int, game_id: int, window: int = 10,
    ) -> Dict[str, float]:
        prior = self._prior_games(team_id, game_id)
        if not prior:
            return {"pace": 0.0, "defensive_rating": 0.0, "offensive_rating": 0.0}
        window_games = prior[-window:]
        pace, dr, orr = [], [], []
        for _gdate, gid in window_games:
            ctx = self.team_game_context.get((team_id, gid)) or {}
            if ctx.get("pace") is not None:
                pace.append(float(ctx["pace"]))
            if ctx.get("defensive_rating") is not None:
                dr.append(float(ctx["defensive_rating"]))
            if ctx.get("offensive_rating") is not None:
                orr.append(float(ctx["offensive_rating"]))
        return {
            "pace": float(sum(pace) / len(pace)) if pace else 0.0,
            "defensive_rating": float(sum(dr) / len(dr)) if dr else 0.0,
            "offensive_rating": float(sum(orr) / len(orr)) if orr else 0.0,
        }

    def _rest_days(
        self, team_id: int, game_id: int, game_date: Optional[_date],
    ) -> int:
        if game_date is None:
            return _REST_DAYS_DEFAULT
        prior = self._prior_games(team_id, game_id)
        if not prior:
            return _REST_DAYS_DEFAULT
        last_date, _ = prior[-1]
        if last_date is None:
            return _REST_DAYS_DEFAULT
        delta = (game_date - last_date).days - 1
        if delta < 0:
            return 0
        return min(delta, _REST_DAYS_CAP)

    # ------------------------------------------------------------------
    # Public feature builder
    # ------------------------------------------------------------------
    def get_features(
        self,
        team_id: Optional[int],
        opponent_team_id: Optional[int],
        game_id: Optional[int],
        game_date: Optional[Any] = None,
        is_home: Optional[bool] = None,
    ) -> Dict[str, float]:
        """Return the 14-feature opponent-context vector for a sample.

        If any required identifier is missing we return a zero-safe
        vector with `home_flag=0`, `rest_days=3` (league median), and
        all rolling averages at 0. The trainer treats this as an
        "unknown context" sample; downstream model should have learned
        a sensible default on such inputs by seeing similar rows during
        training.
        """
        gdate = _safe_date(game_date)

        team_rolling_ctx = (
            self._rolling_context(team_id, game_id)
            if team_id is not None and game_id is not None else
            {"pace": 0.0, "defensive_rating": 0.0}
        )
        if opponent_team_id is not None and game_id is not None:
            opp_allowed = self._rolling_allowed(opponent_team_id, game_id, window=10)
            opp_ctx = self._rolling_context(opponent_team_id, game_id, window=10)
        else:
            opp_allowed = {"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0}
            opp_ctx = {"pace": 0.0, "defensive_rating": 0.0}

        league = self.league_avg_allowed or {}
        # Rest days based on the PLAYER's team, not the opponent.
        rest = _REST_DAYS_DEFAULT
        if team_id is not None and game_id is not None:
            rest = self._rest_days(team_id, game_id, gdate)

        # Resolve is_home: prefer explicit caller arg, else look it up
        # on the target game's context row when we have one. The
        # context row is only present on TRAINING samples because it
        # comes from the SAME game's advanced-stats row — live scoring
        # passes `is_home` explicitly via schedule metadata.
        resolved_is_home: Optional[bool] = is_home
        if resolved_is_home is None and team_id is not None and game_id is not None:
            ctx = self.team_game_context.get((team_id, game_id)) or {}
            v = ctx.get("is_home")
            if v is not None:
                resolved_is_home = bool(v)

        feats: Dict[str, float] = {
            "opp_pts_allowed_L10":  round(opp_allowed["pts"], 3),
            "opp_reb_allowed_L10":  round(opp_allowed["reb"], 3),
            "opp_ast_allowed_L10":  round(opp_allowed["ast"], 3),
            "opp_3pm_allowed_L10":  round(opp_allowed["fg3m"], 3),
            "opp_pts_allowed_vs_avg": round(
                opp_allowed["pts"] - league.get("pts", 0.0), 3,
            ),
            "opp_reb_allowed_vs_avg": round(
                opp_allowed["reb"] - league.get("reb", 0.0), 3,
            ),
            "opp_ast_allowed_vs_avg": round(
                opp_allowed["ast"] - league.get("ast", 0.0), 3,
            ),
            "opp_3pm_allowed_vs_avg": round(
                opp_allowed["fg3m"] - league.get("fg3m", 0.0), 3,
            ),
            "opp_def_rating": round(opp_ctx.get("defensive_rating", 0.0), 3),
            "opp_pace":       round(opp_ctx.get("pace", 0.0), 3),
            "team_pace":      round(team_rolling_ctx.get("pace", 0.0), 3),
            "home_flag": 1.0 if resolved_is_home else 0.0,
            "rest_days": float(rest),
            "back_to_back_flag": 1.0 if rest == 0 else 0.0,
        }
        return feats

    def summary(self) -> Dict[str, Any]:
        return {
            "seasons": list(self.seasons),
            "teams_indexed": len(self.team_games_sorted),
            "team_games_rows": len(self.team_game_allowed),
            "context_rows": len(self.team_game_context),
            "league_avg_allowed": self.league_avg_allowed,
            "opponent_game_pairs": len(self.opponent_map),
        }


# -----------------------------------------------------------------
# Build — synchronous Mongo API (pymongo). Designed to run once per
# pipeline execution. Training uses pymongo; live scoring adapters
# have async Motor — they should construct the store from a background
# thread at startup via `build_opponent_context_store` on a sync
# MongoClient, then reuse the in-memory store for subsequent live
# lookups. The store is pure data; no DB handle is retained.
# -----------------------------------------------------------------
def _aggregate_team_game_allowed(
    logs_coll, seasons: Iterable[int],
) -> Tuple[Dict[Tuple[int, int], Dict[str, float]], Dict[int, Dict[int, int]]]:
    """Sum per-(team_id, game_id) box-score totals and build the
    opponent-id map for each game. Returns:
      - {(team_id, game_id): {pts, reb, ast, fg3m, game_date}}
      - {game_id: {team_id: opponent_team_id}}
    """
    pipeline = [
        {"$match": {"season": {"$in": list(seasons)}}},
        {"$group": {
            "_id": {"team_id": "$team_id", "game_id": "$game_id"},
            "pts":  {"$sum": "$pts"},
            "reb":  {"$sum": "$reb"},
            "ast":  {"$sum": "$ast"},
            "fg3m": {"$sum": "$fg3m"},
            "game_date": {"$first": "$date"},
        }},
    ]
    team_game_allowed: Dict[Tuple[int, int], Dict[str, float]] = {}
    games_to_teams: Dict[int, List[int]] = defaultdict(list)
    for row in logs_coll.aggregate(pipeline, allowDiskUse=True):
        pid = row["_id"]
        tid = pid.get("team_id")
        gid = pid.get("game_id")
        if tid is None or gid is None:
            continue
        team_game_allowed[(int(tid), int(gid))] = {
            "pts":  float(row.get("pts") or 0.0),
            "reb":  float(row.get("reb") or 0.0),
            "ast":  float(row.get("ast") or 0.0),
            "fg3m": float(row.get("fg3m") or 0.0),
            "game_date": row.get("game_date"),
        }
        games_to_teams[int(gid)].append(int(tid))

    # Build opponent map: for each game, the two team_ids are mutual opponents.
    opponent_map: Dict[int, Dict[int, int]] = {}
    for gid, team_ids in games_to_teams.items():
        uniq = list({t for t in team_ids})
        if len(uniq) != 2:
            # Data quality: skip games that don't have exactly two teams.
            continue
        a, b = uniq
        opponent_map[gid] = {a: b, b: a}
    return team_game_allowed, opponent_map


def _aggregate_team_game_context(
    adv_coll, seasons: Iterable[int],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """Average per-(team_id, game_id) pace / ratings across player rows,
    and capture is_home / opponent_team_id for the target-game lookup."""
    pipeline = [
        {"$match": {"season": {"$in": list(seasons)}}},
        {"$group": {
            "_id": {"team_id": "$team_id", "game_id": "$game_id"},
            "pace":              {"$avg": "$pace"},
            "defensive_rating":  {"$avg": "$defensive_rating"},
            "offensive_rating":  {"$avg": "$offensive_rating"},
            "is_home":           {"$first": "$is_home"},
            "opponent_team_id":  {"$first": "$opponent_team_id"},
            "game_date":         {"$first": "$game_date"},
        }},
    ]
    out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for row in adv_coll.aggregate(pipeline, allowDiskUse=True):
        pid = row["_id"]
        tid = pid.get("team_id")
        gid = pid.get("game_id")
        if tid is None or gid is None:
            continue
        out[(int(tid), int(gid))] = {
            "pace":              row.get("pace"),
            "defensive_rating":  row.get("defensive_rating"),
            "offensive_rating":  row.get("offensive_rating"),
            "is_home":           row.get("is_home"),
            "opponent_team_id":  row.get("opponent_team_id"),
            "game_date":         row.get("game_date"),
        }
    return out


def build_opponent_context_store(
    sync_db, seasons: Iterable[int],
) -> OpponentContextStore:
    """Construct the store by scanning historical + advanced collections.

    `sync_db` must be a pymongo Database handle. Seasons is the list
    of integer seasons to include in feature history.
    """
    seasons_t = tuple(int(s) for s in seasons)
    logger.info(
        f"[OPP_CTX] building store for seasons={seasons_t}..."
    )
    logs_coll = sync_db["bdl_historical_game_logs"]
    adv_coll  = sync_db["bdl_advanced_stats"]

    team_game_allowed, opponent_map = _aggregate_team_game_allowed(
        logs_coll, seasons_t,
    )
    team_game_context = _aggregate_team_game_context(adv_coll, seasons_t)

    # Build per-team chronological game list for rolling / rest-day
    # computation. Use game_date from the logs aggregation (most
    # authoritative) with game_id as tiebreaker to keep ordering stable
    # for same-day games (rare but possible at season start).
    by_team: Dict[int, List[Tuple[_date, int]]] = defaultdict(list)
    for (tid, gid), row in team_game_allowed.items():
        d = _safe_date(row.get("game_date"))
        if d is None:
            continue
        by_team[tid].append((d, gid))
    for tid in list(by_team.keys()):
        by_team[tid].sort(key=lambda x: (x[0], x[1]))

    # League averages — mean across every (team, game) row.
    totals = {"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0}
    n = 0
    for row in team_game_allowed.values():
        totals["pts"]  += float(row.get("pts")  or 0.0)
        totals["reb"]  += float(row.get("reb")  or 0.0)
        totals["ast"]  += float(row.get("ast")  or 0.0)
        totals["fg3m"] += float(row.get("fg3m") or 0.0)
        n += 1
    league_avg = (
        {k: round(v / n, 3) for k, v in totals.items()} if n else
        {"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0}
    )

    store = OpponentContextStore(
        team_game_allowed=team_game_allowed,
        team_game_context=team_game_context,
        team_games_sorted=dict(by_team),
        league_avg_allowed=league_avg,
        opponent_map=opponent_map,
        seasons=seasons_t,
    )
    logger.info(
        f"[OPP_CTX] built: {store.summary()}"
    )
    return store


def resolve_opponent_team_id(
    store: OpponentContextStore, team_id: Optional[int], game_id: Optional[int],
) -> Optional[int]:
    """Return the opponent team_id for (team_id, game_id), or None."""
    if team_id is None or game_id is None:
        return None
    game_opps = store.opponent_map.get(int(game_id))
    if not game_opps:
        return None
    return game_opps.get(int(team_id))
