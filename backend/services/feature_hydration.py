"""Live-props game-context hydration.

Called from `services/universal_odds_sync.py` immediately after identity
stamping and immediately before `live_props.insert_many(...)`. Mutates
each prop in place so the canonical pool that `nba_scoring` /
`mlb_scoring` read from carries the game-context fields the downstream
ML models need.

Read sources (all local, no external API calls):
  • `<sport>_master_hub_2026`         — player → team mapping
  • `dg_raw_odds_markets`              — `totals` + `team_totals` markets
  • `live_injuries`                    — per-team injury summary (NBA + MLB)

Write fields (per prop):
  Common
    team, opponent_team, is_home_team, is_away_team,
    team_total, game_total,
    live_injuries_team, live_injuries_opp,
    context_imputed_fields  (list, for Step 5 missing-value policy)
  NBA-only
    rest_days, is_b2b, expected_minutes
  MLB-only
    park_team, venue, team_implied_runs,
    probable_pitcher, opp_pitcher_throws, opp_pitcher_id, opp_pitcher_name,
    batting_order, lineup_confirmed

Guardrail: this module only writes ADDITIONAL keys onto each prop.
It never overwrites existing identity / odds / market fields.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Team-name alias maps (full name <-> 3-letter abbreviation).
# Built once per hydration run from the master hub.
# =============================================================================
async def _build_team_alias_map(db, sport: str) -> Dict[str, str]:
    """Return {alias_lower: 3-letter-abbr}. Aliases include full name,
    short name, abbreviation, all lowercased."""
    hub_coll = "nba_master_hub_2026" if sport == "nba" else "mlb_master_hub_2026"
    aliases: Dict[str, str] = {}
    cursor = db[hub_coll].find(
        {}, {"team": 1, "team_abbr": 1, "team_name": 1,
             "team_full_name": 1, "team_full": 1, "bdl_game_logs": 1}
    )
    async for row in cursor:
        abbr = row.get("team_abbr") or row.get("team")
        if not abbr:
            continue
        abbr = str(abbr).upper().strip()
        for k in ("team_name", "team_full_name", "team_full"):
            v = row.get(k)
            if v:
                aliases[str(v).lower().strip()] = abbr
        # MLB sometimes leaves team_name blank — fall back to game logs
        logs = row.get("bdl_game_logs") or []
        if logs and isinstance(logs, list):
            for lg in logs[:3]:
                tn = (lg or {}).get("team_name")
                if tn:
                    aliases[str(tn).lower().strip()] = abbr
                    break
        aliases[abbr.lower()] = abbr
    return aliases


def _team_to_abbr(team: Optional[str], aliases: Dict[str, str]) -> Optional[str]:
    if not team:
        return None
    return aliases.get(str(team).lower().strip())


# =============================================================================
# Player → team map (by bdl_player_id).
# =============================================================================
async def _build_player_team_map(
    db, sport: str
) -> Dict[int, Dict[str, Any]]:
    """{bdl_player_id: {"team_abbr","team_full"}}. Falls back to game-log
    `team_name` when hub `team_name` is missing (common on MLB)."""
    hub_coll = "nba_master_hub_2026" if sport == "nba" else "mlb_master_hub_2026"
    out: Dict[int, Dict[str, Any]] = {}
    cursor = db[hub_coll].find(
        {}, {"bdl_id": 1, "bdl_player_id": 1, "team": 1,
             "team_abbr": 1, "team_name": 1, "team_full_name": 1,
             "team_full": 1, "bdl_game_logs": 1}
    )
    async for row in cursor:
        pid = row.get("bdl_player_id") or row.get("bdl_id")
        if pid is None:
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        abbr = (row.get("team_abbr") or row.get("team") or "").upper().strip()
        full = (
            row.get("team_full_name")
            or row.get("team_name")
            or row.get("team_full")
            or ""
        )
        if not full:
            logs = row.get("bdl_game_logs") or []
            for lg in logs[:3]:
                tn = (lg or {}).get("team_name")
                if tn:
                    full = tn
                    break
        out[pid] = {"team_abbr": abbr or None, "team_full": (full or "").strip() or None}
    return out


# =============================================================================
# Vegas totals + team-totals from dg_raw_odds_markets.
# We pick the median line across books to avoid book-specific outliers.
# =============================================================================
async def _build_vegas_totals_map(
    db, sport: str, event_ids: List[str]
) -> Tuple[Dict[str, float], Dict[Tuple[str, str], float]]:
    """Returns (game_total_by_event, team_total_by_event_team_lower).

    `team_total_by_event_team_lower` is keyed by `(event_id, team_full.lower())`.
    """
    if not event_ids:
        return {}, {}
    # Game totals
    gt_pipeline = [
        {"$match": {"sport": sport, "market_key": "totals",
                    "event_id": {"$in": event_ids}}},
        {"$group": {"_id": "$event_id", "lines": {"$push": "$line"}}},
    ]
    game_totals: Dict[str, float] = {}
    async for d in db["dg_raw_odds_markets"].aggregate(gt_pipeline):
        ls = sorted([float(x) for x in d["lines"] if x is not None])
        if ls:
            game_totals[d["_id"]] = ls[len(ls) // 2]  # median

    # Team totals
    tt_pipeline = [
        {"$match": {"sport": sport, "market_key": "team_totals",
                    "event_id": {"$in": event_ids}}},
        {"$group": {
            "_id": {"event": "$event_id", "team": "$player_name"},
            "lines": {"$push": "$line"},
        }},
    ]
    team_totals: Dict[Tuple[str, str], float] = {}
    async for d in db["dg_raw_odds_markets"].aggregate(tt_pipeline):
        ls = sorted([float(x) for x in d["lines"] if x is not None])
        if not ls:
            continue
        team = (d["_id"]["team"] or "").lower().strip()
        if not team:
            continue
        team_totals[(d["_id"]["event"], team)] = ls[len(ls) // 2]
    return game_totals, team_totals


# =============================================================================
# Live injuries summary per team.
# =============================================================================
async def _build_injury_summary(db, sport: str) -> Dict[str, Dict[str, Any]]:
    """{team_abbr: {"out_count","dtd_count","out_players":[..]}}."""
    out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "out_count": 0, "dtd_count": 0, "out_players": []
    })
    now = datetime.now(timezone.utc)
    cursor = db["live_injuries"].find({"sport": sport})
    async for inj in cursor:
        # respect TTL semantics — skip if explicitly expired
        exp = inj.get("expires_at")
        if exp and isinstance(exp, datetime) and exp < now.replace(tzinfo=exp.tzinfo):
            continue
        team = (inj.get("team") or "").upper().strip()
        if not team:
            continue
        if inj.get("is_out"):
            out[team]["out_count"] += 1
            out[team]["out_players"].append(inj.get("player_name") or "")
        else:
            out[team]["dtd_count"] += 1
    return out


# =============================================================================
# Rest-days / b2b derivation from master_hub.bdl_game_logs (NBA only).
# =============================================================================
def _derive_rest_b2b(
    last_log_date_iso: Optional[str], commence_time_iso: Optional[str]
) -> Tuple[Optional[int], Optional[int]]:
    """Returns (rest_days, is_b2b). 0/1 for is_b2b, None when undeterminable."""
    if not last_log_date_iso or not commence_time_iso:
        return None, None
    try:
        last = datetime.fromisoformat(
            str(last_log_date_iso).replace("Z", "+00:00")
        )
        comm = datetime.fromisoformat(
            str(commence_time_iso).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None, None
    diff_days = (comm.date() - last.date()).days
    rest_days = max(0, diff_days - 1)
    is_b2b = 1 if diff_days == 1 else 0
    return rest_days, is_b2b


# =============================================================================
# Build a `bdl_player_id -> last_game_date_iso` map (NBA — for rest_days).
# =============================================================================
async def _build_last_game_date_map(db, sport: str) -> Dict[int, str]:
    if sport != "nba":
        return {}
    out: Dict[int, str] = {}
    cursor = db["nba_master_hub_2026"].find(
        {}, {"bdl_id": 1, "bdl_player_id": 1, "bdl_game_logs": 1}
    )
    async for row in cursor:
        pid = row.get("bdl_player_id") or row.get("bdl_id")
        if pid is None:
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        logs = row.get("bdl_game_logs") or []
        # logs are typically newest-first
        for lg in logs[:3]:
            d = (lg or {}).get("date")
            if d:
                out[pid] = d
                break
    return out


# =============================================================================
# MAIN ENTRY
# =============================================================================
async def hydrate_game_context_on_props(
    db, sport: str, props: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Mutate `props` in place; add game-context fields. Idempotent.

    Returns a hydration-coverage report:
        {
          "sport": "nba",
          "props": 540,
          "team_resolved": 540,
          "team_total_filled": 488,
          "game_total_filled": 488,
          "injuries_filled": 540,
          "imputed_field_summary": {"probable_pitcher": 540, ...},
        }
    """
    if sport not in ("nba", "mlb") or not props:
        return {"sport": sport, "props": len(props or []), "skipped": True}

    aliases = await _build_team_alias_map(db, sport)
    player_team = await _build_player_team_map(db, sport)
    event_ids = sorted({p.get("event_id") for p in props if p.get("event_id")})
    game_totals, team_totals = await _build_vegas_totals_map(db, sport, event_ids)
    injuries_by_team = await _build_injury_summary(db, sport)
    last_game_date = await _build_last_game_date_map(db, sport)

    counters = {
        "team_resolved": 0,
        "is_home_resolved": 0,
        "team_total_filled": 0,
        "game_total_filled": 0,
        "injuries_filled": 0,
        "rest_days_filled": 0,
        "park_team_filled": 0,
    }
    imputed_summary: Dict[str, int] = defaultdict(int)

    for p in props:
        imputed_fields: List[str] = []

        home = (p.get("home_team") or "").strip()
        away = (p.get("away_team") or "").strip()
        home_abbr = _team_to_abbr(home, aliases)
        away_abbr = _team_to_abbr(away, aliases)

        # ---- Player team resolution (Global Identity Rule) ----
        bid = p.get("bdl_player_id")
        team_info = player_team.get(int(bid)) if bid is not None else None
        team_abbr = (team_info or {}).get("team_abbr")
        team_full = (team_info or {}).get("team_full")
        if team_abbr:
            counters["team_resolved"] += 1
            p["team"] = team_abbr  # canonical: 3-letter abbr
            p["team_full"] = team_full
        else:
            p["team"] = None
            p["team_full"] = None
            imputed_fields.append("team")

        # ---- Home / away resolution ----
        is_home_team = None
        if team_abbr and home_abbr and away_abbr:
            if team_abbr == home_abbr:
                is_home_team = 1
            elif team_abbr == away_abbr:
                is_home_team = 0
        if is_home_team is not None:
            counters["is_home_resolved"] += 1
            p["is_home_team"] = is_home_team
            p["is_away_team"] = 1 - is_home_team
            p["opponent_team"] = away_abbr if is_home_team == 1 else home_abbr
        else:
            p["is_home_team"] = None
            p["is_away_team"] = None
            p["opponent_team"] = None
            imputed_fields.append("is_home_team")
            imputed_fields.append("opponent_team")

        # ---- Vegas totals ----
        ev = p.get("event_id")
        gt = game_totals.get(ev) if ev else None
        if gt is not None:
            counters["game_total_filled"] += 1
            p["game_total"] = gt
        else:
            p["game_total"] = None
            imputed_fields.append("game_total")

        # team_total: keyed by (event, team-full-lower)
        tt = None
        if ev and team_full:
            tt = team_totals.get((ev, team_full.lower()))
        if tt is not None:
            counters["team_total_filled"] += 1
            p["team_total"] = tt
        else:
            p["team_total"] = None
            imputed_fields.append("team_total")

        # ---- Live injuries summary ----
        if team_abbr or (is_home_team is not None and home_abbr and away_abbr):
            counters["injuries_filled"] += 1
            ti = injuries_by_team.get(team_abbr or "", {})
            opp_abbr = p.get("opponent_team")
            oi = injuries_by_team.get(opp_abbr or "", {})
            p["live_injuries_team"] = {
                "out_count": ti.get("out_count", 0),
                "dtd_count": ti.get("dtd_count", 0),
                "out_players": list(ti.get("out_players") or []),
            }
            p["live_injuries_opp"] = {
                "out_count": oi.get("out_count", 0),
                "dtd_count": oi.get("dtd_count", 0),
                "out_players": list(oi.get("out_players") or []),
            }
            p["live_injury_count"] = (
                ti.get("out_count", 0) + oi.get("out_count", 0)
            )
        else:
            p["live_injuries_team"] = None
            p["live_injuries_opp"] = None
            p["live_injury_count"] = None
            imputed_fields.append("live_injuries")

        # ---- Sport-specific ----
        if sport == "nba":
            # rest_days / is_b2b from last game log + commence_time
            last = last_game_date.get(int(bid)) if bid is not None else None
            rd, b2b = _derive_rest_b2b(last, p.get("commence_time"))
            if rd is not None:
                counters["rest_days_filled"] += 1
                p["rest_days"] = rd
                p["is_b2b"] = b2b
            else:
                p["rest_days"] = None
                p["is_b2b"] = None
                imputed_fields.append("rest_days")
                imputed_fields.append("is_b2b")
            # expected_minutes is not derivable locally — flag for Step 5
            p["expected_minutes"] = None
            imputed_fields.append("expected_minutes")
            # starter — not derivable without rotation feed
            p["starter"] = None
            imputed_fields.append("starter")
        else:  # mlb
            # park_team = home_team (the venue); abbr form for HF lookup
            if home_abbr:
                counters["park_team_filled"] += 1
                p["park_team"] = home_abbr
                p["venue"] = home_abbr
            else:
                p["park_team"] = None
                p["venue"] = None
                imputed_fields.append("park_team")
            # team_implied_runs == team_total (Vegas team total IS the
            # implied run line). Already filled above as `team_total`.
            p["team_implied_runs"] = p.get("team_total")
            # MOCKED until external feed: probable_pitcher, lineup
            p["probable_pitcher"] = None
            p["opp_pitcher_id"] = None
            p["opp_pitcher_name"] = None
            p["opp_pitcher_throws"] = None
            p["batting_order"] = None
            p["lineup_confirmed"] = False
            for k in ("probable_pitcher", "opp_pitcher_throws",
                      "batting_order", "lineup_confirmed"):
                imputed_fields.append(k)

        # Stash imputed list on the prop (Step 5 missing-value policy
        # will surface this on score docs via downstream join).
        p["context_imputed_fields"] = sorted(set(imputed_fields))
        for f in p["context_imputed_fields"]:
            imputed_summary[f] += 1

    report = {
        "sport": sport,
        "props": len(props),
        **counters,
        "imputed_field_summary": dict(sorted(
            imputed_summary.items(), key=lambda x: -x[1]
        )),
    }
    logger.info(
        f"[CTX_HYDRATE:{sport}] props={report['props']}  "
        f"team_resolved={counters['team_resolved']}  "
        f"is_home={counters['is_home_resolved']}  "
        f"team_total={counters['team_total_filled']}  "
        f"game_total={counters['game_total_filled']}  "
        f"injuries={counters['injuries_filled']}  "
        f"top_imputed={list(report['imputed_field_summary'].items())[:5]}"
    )
    return report
