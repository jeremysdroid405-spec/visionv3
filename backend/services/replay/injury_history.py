"""
Replay injury / usage layer — historical, as-of-time.

Reconstructs the inputs production scoring expects on every prop:
    prop["usage_vacuum_factor"]   ← team-level
    prop["usage_spike"]           ← player-level (bool flag)

PRODUCTION CONTRACT (matched verbatim)
--------------------------------------
`services.scoring.feature_hydration._compute_team_injury_features`:
    usage_vacuum_factor = 1.0 + (missing_usage_pct / team_total_usage_top13)

    missing_usage_pct  = Σ usage_proxy_l10 over OUT rotation players
    team_total_usage   = Σ usage_proxy_l10 over the top-13 minute leaders
    usage_proxy_per_g  = (fga + 0.44 * fta + tov) / minutes * 36.0
    usage_proxy_l10    = mean of usage_proxy_per_g over last 10 games where min > 0

`services.scoring.vision_v2._context_component`:
    inj signal = clip01((usage_vacuum_factor - 1.0) / 0.5)  · side
    sp  signal = bool→0.5 / float→clip01(value)              · side

HISTORICAL "OUT" RECONSTRUCTION
-------------------------------
Live `injuries_normalized` is non-existent for past dates. The only
strictly-as-of signal is BDL game logs (`bdl_historical_game_logs`).

A rotation player counts as OUT for `snapshot_date` when:
  - they appear in the team's L20-day rotation top-13 (by avg minutes in
    the trailing window), AND
  - they did NOT play (`min == 0` OR no row) in the team's last
    `RECENT_ABSENCE_THRESHOLD` games strictly BEFORE `snapshot_date`.

This uses ONLY pre-snapshot data — never the target game's box score
— and matches a real-world "missed last 3 → on the OUT report"
heuristic. Three games of consecutive absence is the standard signal
production injury reports converge on within 24-48 hours.

USAGE SPIKE
-----------
Per-player short-window vs long-window usage shift:
    usage_l3   = mean usage_proxy over last 3 games where min > 0
    usage_l10  = mean usage_proxy over last 10 games where min > 0
    magnitude  = (usage_l3 - usage_l10) / max(usage_l10, 1.0)
    flag       = magnitude >= USAGE_SPIKE_THRESHOLD  (default 0.15)

`prop["usage_spike"]` is set to the bool flag (matches the
production contract — vision_v2 accepts bool OR float). The blob
also exposes the magnitude for diagnostics.

CACHING / LEAKAGE
-----------------
- Strictly filtered to `date < snapshot_date.isoformat()` everywhere.
- All aggregations call `assert_no_future_games` with the snapshot ts
  before returning.
- Team blob cached per (snapshot_date, team_id); player blob cached
  per (snapshot_date, bdl_player_id). The replay engine drives both
  caches process-wide and persists the resulting `injury_blob` onto
  the Stage-B cache row — the incremental scorer never re-aggregates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .leakage_checks import assert_no_future_games

logger = logging.getLogger(__name__)


ROTATION_LOOKBACK_DAYS = 20
ROTATION_TOP_N = 13
USAGE_LOOKBACK_GAMES = 10
USAGE_SHORT_WINDOW = 3
RECENT_ABSENCE_THRESHOLD = 3
USAGE_SPIKE_THRESHOLD = 0.15
KEY_PLAYER_TOP_N = 2


# ---------------------------------------------------------------- helpers
def _as_date_str(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.date().isoformat()


def _parse_minutes(raw: Any) -> float:
    """`bdl_historical_game_logs.min` is a string ('26', '26:30', '0',
    '', None). Return parsed minutes as float; 0.0 when missing or
    'DNP'-style."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return 0.0
    if ":" in s:
        # 'MM:SS'
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _usage_proxy_per_game(g: Dict[str, Any]) -> Optional[float]:
    """Production formula. Returns None if minutes < 5 (matches
    feature_hydration L10-window filter)."""
    m = _parse_minutes(g.get("min"))
    if m < 5.0:
        return None
    fga = float(g.get("fga") or 0)
    fta = float(g.get("fta") or 0)
    tov = float(g.get("turnover") or 0)
    return (fga + 0.44 * fta + tov) / m * 36.0


# ---------------------------------------------------------------- team rotation
async def _team_rotation_window(
    db, *, team_id: int, as_of_date: str,
    lookback_days: int = ROTATION_LOOKBACK_DAYS,
) -> List[Dict[str, Any]]:
    """All bdl rows for `team_id` over the trailing `lookback_days`
    strictly before `as_of_date`. Used to compute per-player avg
    minutes + L10 usage proxies + recent-absence detection.
    """
    end_d = datetime.fromisoformat(as_of_date).date()
    start_d = end_d - timedelta(days=lookback_days)
    rows: List[Dict[str, Any]] = []
    cursor = db["bdl_historical_game_logs"].find(
        {"team_id":  int(team_id),
         "date":     {"$gte": start_d.isoformat(),
                      "$lt":  as_of_date}},
        {"_id": 0,
         "player_id":   1, "player_name": 1,
         "game_id":     1, "date":        1,
         "min":         1, "fga":         1,
         "fta":         1, "turnover":    1},
    ).sort("date", -1)
    async for d in cursor:
        rows.append(d)
    if rows:
        # Defense-in-depth: the find filter already excludes future
        # rows but the leakage checker enforces the contract.
        assert_no_future_games(
            [{"game_date": r.get("date")} for r in rows],
            as_of_ts=datetime.fromisoformat(as_of_date)
            .replace(tzinfo=timezone.utc),
            timestamp_field="game_date",
        )
    return rows


def _per_player_rollups(
    rows: List[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """Group team-window rows by player → rollups used downstream.

    Returns {player_id: {
        "player_name", "games_played", "avg_minutes",
        "usage_proxy_l10", "recent_played_dates",
    }}.
    """
    by_player: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        pid = r.get("player_id")
        if pid is None:
            continue
        by_player.setdefault(int(pid), []).append(r)

    out: Dict[int, Dict[str, Any]] = {}
    for pid, recs in by_player.items():
        recs_sorted = sorted(recs, key=lambda x: x.get("date") or "",
                              reverse=True)
        played = [r for r in recs_sorted
                  if _parse_minutes(r.get("min")) > 0]
        avg_min = (sum(_parse_minutes(r.get("min")) for r in played)
                   / len(played) if played else 0.0)
        usages = [_usage_proxy_per_game(r) for r in played[:USAGE_LOOKBACK_GAMES]]
        usages = [u for u in usages if u is not None]
        usage_l10 = (sum(usages) / len(usages)) if usages else 0.0
        out[pid] = {
            "player_name":     (recs_sorted[0].get("player_name")
                                or "").strip(),
            "games_in_window": len(recs_sorted),
            "games_played":    len(played),
            "avg_minutes":     round(avg_min, 2),
            "usage_proxy_l10": round(usage_l10, 2),
            "recent_played":   [r.get("date") for r in played],
            "all_dates":       [r.get("date") for r in recs_sorted],
        }
    return out


async def _team_recent_game_dates(
    db, *, team_id: int, as_of_date: str,
    n_games: int = RECENT_ABSENCE_THRESHOLD,
) -> List[str]:
    """The team's last `n_games` distinct game-dates strictly before
    `as_of_date`. Used to flag "missed last N in a row" players."""
    pipe = [
        {"$match": {"team_id":  int(team_id),
                     "date":     {"$lt": as_of_date}}},
        {"$group": {"_id": "$date"}},
        {"$sort":  {"_id": -1}},
        {"$limit": int(n_games)},
    ]
    out: List[str] = []
    async for d in db["bdl_historical_game_logs"].aggregate(pipe):
        out.append(d["_id"])
    return out


# ---------------------------------------------------------------- public api
async def compute_team_injury_blob(
    db, *,
    team_id: Optional[int],
    snapshot_ts: datetime,
    rotation_lookback_days: int = ROTATION_LOOKBACK_DAYS,
    rotation_top_n: int = ROTATION_TOP_N,
    recent_absence_threshold: int = RECENT_ABSENCE_THRESHOLD,
) -> Dict[str, Any]:
    """Returns the team-level injury blob:

        {
          "team_id":               int | None,
          "rotation":              [{player_id, player_name,
                                     avg_minutes, usage_proxy_l10,
                                     is_out, missed_last_n}, …],
          "out_player_ids":        [int, …],
          "out_player_names":      [str, …],
          "out_count":             int,
          "missing_minutes":       float,
          "missing_usage_pct":     float,
          "team_total_usage":      float,
          "usage_vacuum_factor":   float,   # ≥ 1.0 (1.0 = no vacuum)
          "key_player_out_flag":   0 | 1,
          "rotation_compression":  float,   # 0..1
          "feature_completeness":  "team_injury_full" |
                                   "team_injury_partial" |
                                   "team_injury_missing",
          "error":                 str | None,
          "lineage": {
              "rotation_lookback_days":   int,
              "rotation_top_n":           int,
              "recent_absence_threshold": int,
              "as_of_date":               str,
          },
        }
    """
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    as_of_date = _as_date_str(snapshot_ts)
    lineage = {
        "rotation_lookback_days":   rotation_lookback_days,
        "rotation_top_n":           rotation_top_n,
        "recent_absence_threshold": recent_absence_threshold,
        "as_of_date":               as_of_date,
    }

    if team_id is None:
        return {
            "team_id":              None,
            "rotation":             [],
            "out_player_ids":       [],
            "out_player_names":     [],
            "out_count":            0,
            "missing_minutes":      0.0,
            "missing_usage_pct":    0.0,
            "team_total_usage":     0.0,
            "usage_vacuum_factor":  1.0,
            "key_player_out_flag":  0,
            "rotation_compression": 0.0,
            "feature_completeness": "team_injury_missing",
            "error":                "team_id_unresolved",
            "lineage":              lineage,
        }

    rows = await _team_rotation_window(
        db, team_id=team_id, as_of_date=as_of_date,
        lookback_days=rotation_lookback_days,
    )
    if not rows:
        return {
            "team_id":              int(team_id),
            "rotation":             [],
            "out_player_ids":       [],
            "out_player_names":     [],
            "out_count":            0,
            "missing_minutes":      0.0,
            "missing_usage_pct":    0.0,
            "team_total_usage":     0.0,
            "usage_vacuum_factor":  1.0,
            "key_player_out_flag":  0,
            "rotation_compression": 0.0,
            "feature_completeness": "team_injury_missing",
            "error":                "no_recent_team_games",
            "lineage":              lineage,
        }

    per_player = _per_player_rollups(rows)
    # Top-N rotation: highest avg minutes.
    rotation_sorted = sorted(
        per_player.items(),
        key=lambda kv: -float(kv[1].get("avg_minutes") or 0.0),
    )[:rotation_top_n]

    last_n_dates = await _team_recent_game_dates(
        db, team_id=team_id, as_of_date=as_of_date,
        n_games=recent_absence_threshold,
    )
    last_n_set = set(last_n_dates)

    # Identify OUT: in rotation, played 0 of last_n_set games.
    out_pids: List[int] = []
    rotation_dump: List[Dict[str, Any]] = []
    for pid, payload in rotation_sorted:
        played_dates = set(payload.get("recent_played") or [])
        played_in_recent = played_dates & last_n_set
        is_out = (len(last_n_set) >= recent_absence_threshold
                  and len(played_in_recent) == 0)
        rotation_dump.append({
            "player_id":       int(pid),
            "player_name":     payload.get("player_name"),
            "avg_minutes":     payload.get("avg_minutes"),
            "usage_proxy_l10": payload.get("usage_proxy_l10"),
            "is_out":          bool(is_out),
            "missed_last_n":   recent_absence_threshold - len(played_in_recent),
        })
        if is_out:
            out_pids.append(int(pid))

    # Aggregates.
    missing_minutes = sum(
        float(per_player[pid].get("avg_minutes") or 0.0)
        for pid in out_pids
    )
    missing_usage = sum(
        float(per_player[pid].get("usage_proxy_l10") or 0.0)
        for pid in out_pids
    )
    team_total_usage = sum(
        float(per_player[pid].get("usage_proxy_l10") or 0.0)
        for pid, _ in rotation_sorted
    )
    if team_total_usage > 0:
        usage_vacuum_factor = round(
            1.0 + missing_usage / team_total_usage, 3
        )
    else:
        usage_vacuum_factor = 1.0

    # Key player flag: any of the team's top-2 minute leaders is out.
    top_k_pids = {pid for pid, _ in rotation_sorted[:KEY_PLAYER_TOP_N]}
    key_player_out_flag = 1 if (top_k_pids & set(out_pids)) else 0

    rotation_compression = round(
        min(1.0, max(0.0, len(out_pids) / max(rotation_top_n, 1))), 4,
    )
    out_names = [per_player[pid]["player_name"] for pid in out_pids]

    completeness = (
        "team_injury_full"
        if (len(rotation_dump) >= rotation_top_n // 2 and team_total_usage > 0)
        else ("team_injury_partial" if rotation_dump
              else "team_injury_missing")
    )

    return {
        "team_id":              int(team_id),
        "rotation":             rotation_dump,
        "out_player_ids":       out_pids,
        "out_player_names":     out_names,
        "out_count":            len(out_pids),
        "missing_minutes":      round(missing_minutes, 2),
        "missing_usage_pct":    round(missing_usage, 2),
        "team_total_usage":     round(team_total_usage, 2),
        "usage_vacuum_factor":  usage_vacuum_factor,
        "key_player_out_flag":  key_player_out_flag,
        "rotation_compression": rotation_compression,
        "feature_completeness": completeness,
        "error":                None,
        "lineage":              lineage,
    }


async def compute_player_usage_spike(
    db, *,
    bdl_player_id: Optional[int],
    snapshot_ts: datetime,
    short_window: int = USAGE_SHORT_WINDOW,
    long_window: int = USAGE_LOOKBACK_GAMES,
    threshold: float = USAGE_SPIKE_THRESHOLD,
) -> Dict[str, Any]:
    """Returns:
        {
          "bdl_player_id":          int | None,
          "usage_l3":               float | None,
          "usage_l10":              float | None,
          "usage_spike_magnitude":  float | None,   # (l3-l10)/max(l10,1)
          "usage_spike_flag":       bool,
          "games_used_short":       int,
          "games_used_long":        int,
          "feature_completeness":   "usage_spike_full" |
                                    "usage_spike_partial" |
                                    "usage_spike_missing",
          "error":                  str | None,
        }
    """
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    as_of_date = _as_date_str(snapshot_ts)

    if bdl_player_id is None:
        return {
            "bdl_player_id":         None,
            "usage_l3":              None,
            "usage_l10":             None,
            "usage_spike_magnitude": None,
            "usage_spike_flag":      False,
            "games_used_short":      0,
            "games_used_long":       0,
            "feature_completeness":  "usage_spike_missing",
            "error":                 "player_id_unresolved",
        }

    cursor = db["bdl_historical_game_logs"].find(
        {"player_id": int(bdl_player_id),
         "date":      {"$lt": as_of_date}},
        {"_id": 0, "date": 1, "min": 1,
         "fga": 1, "fta": 1, "turnover": 1},
    ).sort("date", -1).limit(int(long_window) * 2)

    raw: List[Dict[str, Any]] = [d async for d in cursor]
    if raw:
        assert_no_future_games(
            [{"game_date": r.get("date")} for r in raw],
            as_of_ts=snapshot_ts,
            timestamp_field="game_date",
        )

    played = [r for r in raw if _parse_minutes(r.get("min")) > 0]
    short = [_usage_proxy_per_game(r) for r in played[:short_window]]
    long_ = [_usage_proxy_per_game(r) for r in played[:long_window]]
    short = [u for u in short if u is not None]
    long_ = [u for u in long_ if u is not None]

    usage_l3 = (sum(short) / len(short)) if short else None
    usage_l10 = (sum(long_) / len(long_)) if long_ else None

    if usage_l3 is None or usage_l10 is None:
        completeness = ("usage_spike_partial"
                         if (usage_l3 or usage_l10) else "usage_spike_missing")
        return {
            "bdl_player_id":         int(bdl_player_id),
            "usage_l3":              round(usage_l3, 2)
                                     if usage_l3 is not None else None,
            "usage_l10":             round(usage_l10, 2)
                                     if usage_l10 is not None else None,
            "usage_spike_magnitude": None,
            "usage_spike_flag":      False,
            "games_used_short":      len(short),
            "games_used_long":       len(long_),
            "feature_completeness":  completeness,
            "error":                 None,
        }

    magnitude = (usage_l3 - usage_l10) / max(usage_l10, 1.0)
    flag = magnitude >= threshold

    return {
        "bdl_player_id":         int(bdl_player_id),
        "usage_l3":              round(usage_l3, 2),
        "usage_l10":             round(usage_l10, 2),
        "usage_spike_magnitude": round(magnitude, 4),
        "usage_spike_flag":      bool(flag),
        "games_used_short":      len(short),
        "games_used_long":       len(long_),
        "feature_completeness":  "usage_spike_full",
        "error":                 None,
    }


def assemble_injury_blob(
    *,
    team_blob: Dict[str, Any],
    spike_blob: Dict[str, Any],
) -> Dict[str, Any]:
    """Combine team-level + player-level into the prop-ready injury
    blob. The two production-read fields (`usage_vacuum_factor`,
    `usage_spike`) live at the top level; everything else is
    diagnostic.

    `usage_spike` is the BOOL flag (matches vision_v2 contract:
    bool=True → 0.5 directional contribution).
    """
    team_full = (team_blob or {}).get("feature_completeness") == "team_injury_full"
    spike_full = (spike_blob or {}).get("feature_completeness") == "usage_spike_full"
    if team_full and spike_full:
        completeness = "injury_full"
    elif team_full or spike_full:
        completeness = "injury_partial"
    else:
        completeness = "injury_missing"

    return {
        "usage_vacuum_factor":  (team_blob or {}).get("usage_vacuum_factor"),
        "usage_spike":          bool((spike_blob or {}).get("usage_spike_flag")),
        "key_player_out_flag":  (team_blob or {}).get("key_player_out_flag"),
        "rotation_compression": (team_blob or {}).get("rotation_compression"),
        "out_count":            (team_blob or {}).get("out_count"),
        "out_player_names":     (team_blob or {}).get("out_player_names"),
        "missing_minutes":      (team_blob or {}).get("missing_minutes"),
        "missing_usage_pct":    (team_blob or {}).get("missing_usage_pct"),
        "team_total_usage":     (team_blob or {}).get("team_total_usage"),
        "team_blob":            team_blob,
        "spike_blob":           spike_blob,
        "feature_completeness": completeness,
        "error":                (team_blob or {}).get("error")
                                or (spike_blob or {}).get("error"),
    }


__all__ = [
    "ROTATION_LOOKBACK_DAYS", "ROTATION_TOP_N",
    "RECENT_ABSENCE_THRESHOLD", "USAGE_SPIKE_THRESHOLD",
    "USAGE_LOOKBACK_GAMES", "USAGE_SHORT_WINDOW",
    "compute_team_injury_blob",
    "compute_player_usage_spike",
    "assemble_injury_blob",
    "_parse_minutes", "_usage_proxy_per_game",
]
