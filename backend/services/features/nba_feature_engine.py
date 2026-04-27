"""
NBA Feature Engine — context features for VK retrain
=====================================================

NEW, OPTIONAL feature service. **Does not modify scoring, gating,
probability, or any production endpoint.** Feature output is consumed
only by the future VK retrain job; production scoring continues to use
its existing 105-feature input today.

Output layout (per player + game):
    {
      "context_features": {
        "usage_vacuum_factor": float | None,
        "key_player_out_flag": int | None,            # 0/1
        "team_usage_removed_pct": float | None,
        "blowout_risk": float | None,                  # 0..1
        "rest_days": int | None,
        "back_to_back_flag": int | None,               # 0/1
        "pace_differential": float | None,             # normalized [-1, 1]
        "defensive_matchup_tier": "elite|average|weak"|None,
        "potential_assists_rate": float | None,
        "home_away_split_delta": float | None,
        "feature_coverage": float                      # 0..1
      }
    }

Persistence: new collection `nba_player_context_features` (Option B —
keeps the master_hub doc small and avoids any risk to existing readers
of `nba_master_hub_2026`). Indexed on (`bdl_player_id`, `game_id`).

Public API:
    build_player_context_features(db, bdl_player_id, game_id) → dict
    build_team_context(db, team_abbr, game_id)               → dict
    enrich_slate(db, sport="nba")                            → coverage report
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Constants — chosen to match production conventions exactly so the
# trained model sees the same numeric ranges as inference.
# =============================================================================
KEY_PLAYER_USAGE_THRESHOLD = 0.20  # 20%+ usage → "key player"
SPREAD_NORMALIZER = 15.0           # blowout_risk = min(|spread|/15, 1.0)
LEAGUE_AVG_PACE = 100.0            # for pace_differential normalization
PACE_DIFF_NORMALIZER = 10.0        # caps pace_diff at ±10 pts/100poss
HUB_COLLECTION = "nba_master_hub_2026"
INJURY_COLLECTION = "injuries_normalized"
INJURY_LEGACY_COLLECTION = "live_injuries"
ADV_STATS_COLLECTION = "bdl_advanced_stats"
DEF_MOMENTUM_COLLECTION = "defensive_momentum_cache"
ODDS_COLLECTION = "dg_raw_odds_markets"
OUTPUT_COLLECTION = "nba_player_context_features"

OUT_STATUSES = {
    "OUT", "OUT_FOR_SEASON", "OUT_INDEFINITELY", "IL",
    "10-DAY-IL", "15-DAY-IL", "60-DAY-IL", "DNP",
}


# =============================================================================
# Internal helpers — pure functions, easy to unit-test.
# =============================================================================
def _normalize_pace_differential(team_pace: Optional[float],
                                  opp_pace: Optional[float]) -> Optional[float]:
    if team_pace is None or opp_pace is None:
        return None
    diff = team_pace - opp_pace
    return max(-1.0, min(1.0, diff / PACE_DIFF_NORMALIZER))


def _bucket_def_tier(def_rating: Optional[float]) -> Optional[str]:
    """Map opponent positional defensive rating to {elite,average,weak}.
    Uses NBA league-typical breakpoints (DRtg ≤ 110 elite, ≥ 116 weak)."""
    if def_rating is None:
        return None
    if def_rating <= 110.0:
        return "elite"
    if def_rating >= 116.0:
        return "weak"
    return "average"


def _safe_avg(values: List[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    return (sum(nums) / len(nums)) if nums else None


def _coverage(features: Dict[str, Any]) -> float:
    """Fraction of NON-None values among the 10 spec features."""
    spec = (
        "usage_vacuum_factor", "key_player_out_flag", "team_usage_removed_pct",
        "blowout_risk", "rest_days", "back_to_back_flag",
        "pace_differential", "defensive_matchup_tier",
        "potential_assists_rate", "home_away_split_delta",
    )
    filled = sum(1 for k in spec if features.get(k) is not None)
    return round(filled / len(spec), 3)


# =============================================================================
# Data lookups — all read-only; no external API calls.
# =============================================================================
async def _load_team_roster_usage(
    db, team_abbr: str
) -> Dict[int, Dict[str, Any]]:
    """{bdl_player_id (int): {usage_rate, player_name, l10_minutes}}.
    Usage = season-mean `usage_percentage` from `bdl_advanced_stats`.
    """
    out: Dict[int, Dict[str, Any]] = {}
    # 1) Roster from master_hub
    cursor = db[HUB_COLLECTION].find(
        {"team_abbr": team_abbr.upper()},
        {"bdl_player_id": 1, "bdl_id": 1, "display_name": 1,
         "player_name": 1}
    )
    pid_to_name: Dict[int, str] = {}
    async for row in cursor:
        pid = row.get("bdl_player_id") or row.get("bdl_id")
        if pid is None:
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        nm = row.get("display_name") or row.get("player_name") or ""
        pid_to_name[pid] = nm
    if not pid_to_name:
        return out
    # 2) Aggregate season usage from advanced stats
    pipe = [
        {"$match": {"player_id": {"$in": list(pid_to_name.keys())}}},
        {"$group": {
            "_id": "$player_id",
            "usage_avg": {"$avg": "$usage_percentage"},
            "n": {"$sum": 1},
        }},
    ]
    async for d in db[ADV_STATS_COLLECTION].aggregate(pipe):
        pid = int(d["_id"])
        out[pid] = {
            "usage_rate": float(d["usage_avg"] or 0.0),
            "player_name": pid_to_name.get(pid, ""),
            "n_games": int(d["n"]),
        }
    # Fill any roster member missing from adv_stats
    for pid, nm in pid_to_name.items():
        if pid not in out:
            out[pid] = {"usage_rate": 0.0, "player_name": nm, "n_games": 0}
    return out


async def _load_team_out_set(db, team_abbr: str) -> set:
    """Set of `bdl_id`s for team's OUT players from canonical injuries."""
    now = datetime.now(timezone.utc)
    out_ids: set = set()
    out_names: set = set()
    async for inj in db[INJURY_COLLECTION].find(
        {"sport": "nba", "team": team_abbr.upper()}
    ):
        status = (inj.get("status") or "").upper().strip()
        if status in OUT_STATUSES or inj.get("is_out") is True:
            bid = inj.get("bdl_id")
            if bid is not None:
                try: out_ids.add(int(bid))
                except (TypeError, ValueError): pass
            nm = (inj.get("player_name") or "").strip().lower()
            if nm:
                out_names.add(nm)
    # Legacy fallback
    async for inj in db[INJURY_LEGACY_COLLECTION].find({
        "sport": "nba", "team": team_abbr.upper(),
        "$or": [{"expires_at": None}, {"expires_at": {"$gte": now}}],
    }):
        if (inj.get("status") or "").upper().strip() in OUT_STATUSES \
                or inj.get("is_out") is True:
            bid = inj.get("bdl_id") or inj.get("bdl_player_id")
            if bid is not None:
                try: out_ids.add(int(bid))
                except (TypeError, ValueError): pass
            nm = (inj.get("player_name") or "").strip().lower()
            if nm:
                out_names.add(nm)
    return out_ids, out_names


async def _load_spread_for_event(
    db, event_id: str, team_full: str
) -> Optional[float]:
    """Median spread (across books) for `team_full` on `event_id`.

    Note: `dg_raw_odds_markets` stores the team identity for spread
    markets in `team_or_side`, not `player_name`. We query both fields
    to be defensive.
    """
    if not event_id or not team_full:
        return None
    pipe = [
        {"$match": {
            "sport": "nba", "market_key": "spreads",
            "event_id": event_id,
            "$or": [
                {"team_or_side": team_full},
                {"player_name": team_full},
            ],
        }},
        {"$group": {"_id": None, "lines": {"$push": "$line"}}},
    ]
    async for d in db[ODDS_COLLECTION].aggregate(pipe):
        ls = sorted(float(x) for x in d["lines"] if x is not None)
        if ls:
            return ls[len(ls) // 2]
    return None


async def _load_defensive_rating(db, team_abbr: str,
                                  stat_type: Optional[str] = None
                                  ) -> Optional[float]:
    """`defensive_momentum_cache.season_def_rating` for team_abbr,
    optionally filtered by stat_type (PTS/REB/AST). Falls back to
    overall when stat-specific row not found."""
    q = {"team": team_abbr.upper()}
    if stat_type:
        q["stat_type"] = stat_type
    doc = await db[DEF_MOMENTUM_COLLECTION].find_one(q)
    if not doc and stat_type:
        doc = await db[DEF_MOMENTUM_COLLECTION].find_one(
            {"team": team_abbr.upper()}
        )
    if not doc:
        return None
    rt = doc.get("season_def_rating")
    return float(rt) if rt is not None else None


def _parse_minutes(min_str: Any) -> Optional[float]:
    if min_str is None:
        return None
    if isinstance(min_str, (int, float)):
        return float(min_str)
    s = str(min_str).strip()
    if not s:
        return None
    if ":" in s:
        try:
            mm, ss = s.split(":", 1)
            return float(mm) + float(ss) / 60.0
        except (ValueError, TypeError):
            return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# =============================================================================
# Public — team-level context.
# =============================================================================
async def build_team_context(
    db, team_abbr: str, game_id: Optional[str] = None,
    *, event_id: Optional[str] = None,
    team_full: Optional[str] = None,
    opp_team_abbr: Optional[str] = None,
    commence_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute a single team's context (usage vacuum, blowout risk,
    pace differential). `event_id` + `team_full` are needed for spread
    lookup; if missing, `blowout_risk` falls back to None."""
    team_abbr = (team_abbr or "").upper().strip()
    out: Dict[str, Any] = {
        "team_abbr": team_abbr,
        "usage_vacuum_factor": None,
        "key_player_out_flag": None,
        "team_usage_removed_pct": None,
        "blowout_risk": None,
        "pace_differential": None,
    }
    if not team_abbr:
        return out

    # Roster + OUT set
    roster_usage = await _load_team_roster_usage(db, team_abbr)
    out_ids, out_names = await _load_team_out_set(db, team_abbr)

    # Resolve OUT players' usage by id (primary) + name (fallback).
    name_index = {(r.get("player_name") or "").lower().strip(): pid
                   for pid, r in roster_usage.items()}
    matched_pids: set = set()
    for pid in out_ids:
        if pid in roster_usage:
            matched_pids.add(pid)
    for nm in out_names:
        norm = nm.replace(" jr.", "").replace(" sr.", "").replace(" iii", "")\
            .replace(" ii", "").strip()
        pid = name_index.get(norm) or name_index.get(nm)
        if pid is not None:
            matched_pids.add(pid)

    if roster_usage:
        team_total_usage = sum(r["usage_rate"] for r in roster_usage.values()) or 1e-9
        out_usage = sum(roster_usage[pid]["usage_rate"]
                        for pid in matched_pids)
        out["team_usage_removed_pct"] = round(out_usage / team_total_usage, 3)
        out["usage_vacuum_factor"] = round(1.0 + out_usage / team_total_usage, 3)
        # Key player flag = any OUT player has usage ≥ threshold
        out["key_player_out_flag"] = int(any(
            roster_usage[pid]["usage_rate"] >= KEY_PLAYER_USAGE_THRESHOLD
            for pid in matched_pids
        ))

    # Blowout risk (median spread for team in this event)
    spread = await _load_spread_for_event(db, event_id or "", team_full or "")
    if spread is not None:
        out["blowout_risk"] = round(min(abs(spread) / SPREAD_NORMALIZER, 1.0), 3)
    out["spread"] = spread

    # Pace differential
    try:
        from services.team_stats_service import TEAM_PACE_2026
        team_pace = TEAM_PACE_2026.get(team_abbr)
        opp_pace = (TEAM_PACE_2026.get((opp_team_abbr or "").upper())
                    if opp_team_abbr else None)
    except Exception:
        team_pace, opp_pace = None, None
    out["pace_differential"] = _normalize_pace_differential(team_pace, opp_pace)
    out["team_pace"] = team_pace
    out["opp_pace"] = opp_pace

    return out


# =============================================================================
# Public — player-level context.
# =============================================================================
async def build_player_context_features(
    db,
    bdl_player_id: int,
    game_id: Optional[str] = None,
    *,
    team_abbr: Optional[str] = None,
    opp_team_abbr: Optional[str] = None,
    event_id: Optional[str] = None,
    team_full: Optional[str] = None,
    commence_time: Optional[str] = None,
    stat_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the full 10-feature context for one player.

    `commence_time` is the game's UTC start ISO; used to derive
    rest_days against the player's most recent game log.
    """
    features: Dict[str, Any] = {
        "usage_vacuum_factor": None,
        "key_player_out_flag": None,
        "team_usage_removed_pct": None,
        "blowout_risk": None,
        "rest_days": None,
        "back_to_back_flag": None,
        "pace_differential": None,
        "defensive_matchup_tier": None,
        "potential_assists_rate": None,
        "home_away_split_delta": None,
    }

    # Pull player + last 10 game logs from the master hub.
    player_doc = await db[HUB_COLLECTION].find_one(
        {"$or": [{"bdl_player_id": bdl_player_id},
                  {"bdl_id": bdl_player_id}]},
        {"team_abbr": 1, "team": 1, "bdl_game_logs": 1,
         "display_name": 1, "player_name": 1}
    )
    if player_doc and not team_abbr:
        team_abbr = (player_doc.get("team_abbr")
                      or player_doc.get("team") or "").upper()

    # Team-level features (shared lookup).
    team_ctx = await build_team_context(
        db, team_abbr or "", game_id,
        event_id=event_id, team_full=team_full,
        opp_team_abbr=opp_team_abbr, commence_time=commence_time,
    )
    for k in ("usage_vacuum_factor", "key_player_out_flag",
              "team_usage_removed_pct", "blowout_risk",
              "pace_differential"):
        features[k] = team_ctx.get(k)

    # rest_days + back_to_back from player's last game log.
    if player_doc and commence_time:
        logs = player_doc.get("bdl_game_logs") or []
        try:
            game_dt = datetime.fromisoformat(
                str(commence_time).replace("Z", "+00:00")
            ).date()
        except (ValueError, TypeError):
            game_dt = None
        last_dt = None
        for lg in logs[:5]:
            d = (lg or {}).get("date")
            if d:
                try:
                    last_dt = datetime.fromisoformat(
                        str(d).replace("Z", "+00:00")
                    ).date()
                    break
                except (ValueError, TypeError):
                    continue
        if game_dt and last_dt:
            diff = (game_dt - last_dt).days
            features["rest_days"] = max(0, diff - 1)
            features["back_to_back_flag"] = 1 if diff == 1 else 0

    # Defensive matchup tier — opponent's positional def rating.
    if opp_team_abbr:
        rt = await _load_defensive_rating(db, opp_team_abbr, stat_type)
        features["defensive_matchup_tier"] = _bucket_def_tier(rt)

    # Tier 3 — potential_assists_rate from recent advanced-stats.
    # `bdl_advanced_stats` doesn't carry potential_assists directly; we
    # use `passes / minutes` as a structural proxy when available.
    pipe = [
        {"$match": {"player_id": int(bdl_player_id)}},
        {"$sort": {"game_date": -1}},
        {"$limit": 10},
        {"$project": {"passes": 1, "matchup_minutes": 1,
                      "minutes": 1, "screen_assists": 1}},
    ]
    pa_per_min = []
    async for d in db[ADV_STATS_COLLECTION].aggregate(pipe):
        passes = d.get("passes")
        m = _parse_minutes(d.get("matchup_minutes") or d.get("minutes"))
        if passes is not None and m and m > 0:
            pa_per_min.append(float(passes) / m)
    if pa_per_min:
        features["potential_assists_rate"] = round(
            sum(pa_per_min) / len(pa_per_min), 3
        )

    # Tier 3 — home_away_split_delta. NBA hub `bdl_game_logs` doesn't
    # carry an `is_home` flag (it's None per inspection). We pull
    # home/away splits from `bdl_advanced_stats` which DOES record
    # `is_home`. Stat-specific via stat_type.
    if stat_type:
        stat_key_map = {
            "PTS": "points", "REB": "rebound_percentage",
            "AST": "assists",
        }
        # bdl_advanced_stats doesn't store raw counting stats for
        # rebounds/points/assists for every row — to keep the proxy
        # simple, we use the master_hub `bdl_game_logs` for the
        # counting stat and the adv-stats `is_home` for the flag,
        # joined on game_id.
        sk = {"PTS": "pts", "REB": "reb", "AST": "ast"}.get(stat_type)
        if sk and player_doc:
            logs = (player_doc.get("bdl_game_logs") or [])[:20]
            game_ids = [lg.get("game_id") for lg in logs
                         if lg.get("game_id") is not None]
            if game_ids:
                home_map = {}
                async for d in db[ADV_STATS_COLLECTION].find(
                    {"player_id": int(bdl_player_id),
                     "game_id": {"$in": game_ids}},
                    {"game_id": 1, "is_home": 1, "_id": 0},
                ):
                    home_map[d["game_id"]] = d.get("is_home")
                home_vals, away_vals = [], []
                for lg in logs:
                    h = home_map.get(lg.get("game_id"))
                    v = lg.get(sk)
                    if v is None or h is None:
                        continue
                    (home_vals if h else away_vals).append(v)
                ha = _safe_avg(home_vals)
                aa = _safe_avg(away_vals)
                if ha is not None and aa is not None:
                    features["home_away_split_delta"] = round(ha - aa, 3)

    features["feature_coverage"] = _coverage(features)
    return features


# =============================================================================
# Public — slate-level enrichment.
# =============================================================================
async def enrich_slate(db, sport: str = "nba") -> Dict[str, Any]:
    """For every `nba_live_props` doc on the current slate, compute
    context features and upsert to `nba_player_context_features`.
    Returns coverage statistics and per-feature missing counts.

    Idempotent: replaces (player, game, stat) row when it already
    exists. Does NOT touch any production collection except its own
    output collection.
    """
    if sport != "nba":
        return {"sport": sport, "skipped": True}

    props = await db["nba_live_props"].find(
        {"bdl_player_id": {"$ne": None}},
        {"_id": 0, "bdl_player_id": 1, "team": 1, "team_full": 1,
         "opponent_team": 1, "event_id": 1, "commence_time": 1,
         "stat_type": 1, "player_name": 1, "home_team": 1, "away_team": 1}
    ).to_list(None)
    if not props:
        return {"sport": sport, "props": 0, "skipped_no_props": True}

    # Dedup at the player-game-stat granularity.
    seen: set = set()
    rows: List[Dict[str, Any]] = []
    for p in props:
        key = (p["bdl_player_id"], p.get("event_id"), p.get("stat_type"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(p)

    coverage_sum = 0.0
    missing_counter: Dict[str, int] = defaultdict(int)
    docs_to_write: List[Dict[str, Any]] = []
    for p in rows:
        try:
            features = await build_player_context_features(
                db,
                bdl_player_id=int(p["bdl_player_id"]),
                team_abbr=p.get("team"),
                opp_team_abbr=p.get("opponent_team"),
                event_id=p.get("event_id"),
                team_full=p.get("team_full"),
                commence_time=p.get("commence_time"),
                stat_type=p.get("stat_type"),
            )
        except Exception as e:
            logger.warning(
                f"[NBA_FEATURES] player={p.get('player_name')} "
                f"stat={p.get('stat_type')} failed: {e}"
            )
            continue
        coverage_sum += features.get("feature_coverage", 0.0)
        for k, v in features.items():
            if v is None:
                missing_counter[k] += 1
        docs_to_write.append({
            "bdl_player_id": int(p["bdl_player_id"]),
            "player_name": p.get("player_name"),
            "team": p.get("team"),
            "opponent_team": p.get("opponent_team"),
            "event_id": p.get("event_id"),
            "stat_type": p.get("stat_type"),
            "commence_time": p.get("commence_time"),
            "context_features": features,
            "computed_at": datetime.now(timezone.utc),
        })

    # Persist (Option B — separate collection).
    if docs_to_write:
        from pymongo import UpdateOne
        ops = [
            UpdateOne(
                {"bdl_player_id": d["bdl_player_id"],
                 "event_id": d["event_id"],
                 "stat_type": d["stat_type"]},
                {"$set": d},
                upsert=True,
            )
            for d in docs_to_write
        ]
        await db[OUTPUT_COLLECTION].bulk_write(ops, ordered=False)
        # Ensure index (idempotent).
        await db[OUTPUT_COLLECTION].create_index(
            [("bdl_player_id", 1), ("event_id", 1), ("stat_type", 1)],
            unique=True, background=True, name="player_event_stat_unique",
        )

    avg_coverage = coverage_sum / max(1, len(docs_to_write))
    full_coverage_n = sum(
        1 for d in docs_to_write
        if d["context_features"].get("feature_coverage", 0) >= 0.99
    )
    report = {
        "sport": sport,
        "rows_seen": len(rows),
        "rows_written": len(docs_to_write),
        "avg_feature_coverage": round(avg_coverage, 3),
        "rows_with_full_coverage": full_coverage_n,
        "rows_with_full_coverage_pct": round(
            full_coverage_n * 100 / max(1, len(docs_to_write)), 1
        ),
        "missing_per_feature": dict(sorted(
            missing_counter.items(), key=lambda x: -x[1]
        )),
    }
    logger.info(
        f"[NBA_FEATURES] slate enrichment: rows={report['rows_written']} "
        f"avg_coverage={report['avg_feature_coverage']} "
        f"full_coverage={report['rows_with_full_coverage_pct']}% "
        f"top_missing={list(report['missing_per_feature'].items())[:3]}"
    )
    return report
