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
    """{team_abbr: {"out_count","dtd_count","out_players":[..],
                    "dtd_players":[..],"out_bdl_ids":[..],"dtd_bdl_ids":[..]}}.

    Source priority (2026-05):
      • NBA: `injuries_normalized` (canonical, written by InjurySensor —
        merges BDL + ESPN + NBA Official, refreshed every cycle).
        `live_injuries` is deprecated for NBA per `live_injury_micro_sync.py`.
      • MLB: `injuries_normalized` first, then `live_injuries` as fallback
        (some legacy MLB code paths still write there with TTL).

    Status normalization:
      • OUT, OUT_FOR_SEASON, OUT_INDEFINITELY, IL → out
      • DAY-TO-DAY, DOUBTFUL, QUESTIONABLE, PROBABLE → dtd
      • Anything else → ignored

    Identity preference (2026-05): join via `bdl_id` (int) wherever
    available; fall back to lowercased player_name when the source
    has no ID.
    """
    out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "out_count": 0, "dtd_count": 0,
        "out_players": [], "dtd_players": [],
        "out_bdl_ids": [], "dtd_bdl_ids": [],
    })
    OUT_STATUSES = {
        # NBA + ESPN
        "OUT", "OUT_FOR_SEASON", "OUT_INDEFINITELY", "IL",
        "10-DAY-IL", "15-DAY-IL", "60-DAY-IL", "DNP",
        # MLB-canonical InjurySensor codes (2026-04-29 parity fix).
        # The MLB pipeline writes these into `injuries_normalized` —
        # without them, `_build_injury_summary` returned an empty dict
        # for every MLB team, so `team_injury_context` never reached
        # MLB props and Vision Intel had nothing to say about injuries.
        "IL_SHORT", "IL_STANDARD", "IL_EXTENDED", "OUT_FOR_SEASON",
    }
    DTD_STATUSES = {
        # NBA + ESPN
        "DAY-TO-DAY", "DAYTODAY", "DTD", "DOUBTFUL",
        "QUESTIONABLE", "PROBABLE", "GTD",
        # MLB-canonical InjurySensor codes (uses underscore form)
        "DAY_TO_DAY",
    }
    seen_keys = set()  # de-dupe across sources by (team, bdl_id-or-name)

    async def _ingest_cursor(cursor):
        async for inj in cursor:
            team = (inj.get("team") or "").upper().strip()
            if not team:
                continue
            pname = (inj.get("player_name") or "").strip()
            bid_raw = inj.get("bdl_id") or inj.get("bdl_player_id")
            try:
                bid = int(bid_raw) if bid_raw is not None else None
            except (TypeError, ValueError):
                bid = None
            # Prefer bdl_id as the dedup key; fall back to name.
            key = (team, ("bdl", bid) if bid is not None else ("nm", pname.lower()))
            if key in seen_keys:
                continue
            status_raw = (inj.get("status") or "").upper().strip()
            if status_raw in OUT_STATUSES or inj.get("is_out") is True:
                out[team]["out_count"] += 1
                if pname:
                    out[team]["out_players"].append(pname)
                if bid is not None:
                    out[team]["out_bdl_ids"].append(bid)
                seen_keys.add(key)
            elif status_raw in DTD_STATUSES:
                out[team]["dtd_count"] += 1
                if pname:
                    out[team]["dtd_players"].append(pname)
                if bid is not None:
                    out[team]["dtd_bdl_ids"].append(bid)
                seen_keys.add(key)

    # Canonical source: injuries_normalized
    await _ingest_cursor(
        db["injuries_normalized"].find({"sport": sport})
    )
    # Fallback / legacy: live_injuries (TTL-bound, no bdl_id). Skip
    # expired rows.
    now = datetime.now(timezone.utc)
    await _ingest_cursor(
        db["live_injuries"].find({
            "sport": sport,
            "$or": [
                {"expires_at": None},
                {"expires_at": {"$gte": now}},
            ],
        })
    )
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
# Per-player minutes / usage estimate from `bdl_game_logs[0..9]` (NBA).
# Used to size the injury-vacuum (team_minutes_removed, key_player_out_flag).
# =============================================================================
def _parse_minutes(min_str: Any) -> Optional[float]:
    """`bdl_game_logs[*].min` is typically 'MM:SS' or numeric. Return float
    minutes or None when unparseable."""
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


async def _build_player_minutes_usage_map(
    db, sport: str
) -> Dict[int, Dict[str, Any]]:
    """{bdl_player_id (int): {"avg_minutes_l10","usage_proxy_l10",
                              "team_abbr","player_name"}}.

    Usage proxy: (FGA + 0.44*FTA + TO) per minute averaged over L10,
    rescaled to per-36. Keyed by `bdl_player_id` (not player_name) so
    identity joins match the injuries source via the same canonical key.
    """
    if sport != "nba":
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    cursor = db["nba_master_hub_2026"].find(
        {}, {"bdl_id": 1, "bdl_player_id": 1, "player_name": 1,
             "team_abbr": 1, "team": 1, "bdl_game_logs": 1}
    )
    async for row in cursor:
        pid_raw = row.get("bdl_player_id") or row.get("bdl_id")
        if pid_raw is None:
            continue
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError):
            continue
        team = (row.get("team_abbr") or row.get("team") or "").upper().strip()
        logs = row.get("bdl_game_logs") or []
        if not logs:
            continue
        l10 = logs[:10]
        mins = [_parse_minutes(g.get("min")) for g in l10]
        mins = [m for m in mins if m is not None and m > 0]
        if not mins:
            continue
        avg_min = sum(mins) / len(mins)
        usage_per_game = []
        for g in l10:
            m = _parse_minutes(g.get("min"))
            if m is None or m <= 0:
                continue
            fga = float(g.get("fga") or 0)
            fta = float(g.get("fta") or 0)
            tov = float(g.get("turnover") or 0)
            usage_per_game.append((fga + 0.44 * fta + tov) / m * 36.0)
        usage_l10 = (
            sum(usage_per_game) / len(usage_per_game)
            if usage_per_game else 0.0
        )
        out[pid] = {
            "avg_minutes_l10": round(avg_min, 2),
            "usage_proxy_l10": round(usage_l10, 2),
            "team_abbr": team,
            "player_name": row.get("player_name") or "",
        }
    return out


def _compute_team_injury_features(
    team_abbr: Optional[str],
    injuries_by_team: Dict[str, Dict[str, Any]],
    minutes_usage_map: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute team-level injury aggregates for the given team.

    Identity rule (2026-05): join `injuries_normalized.bdl_id` against
    `minutes_usage_map` keyed by `bdl_player_id`. Falls back to name
    match only when the source lacks a bdl_id (legacy `live_injuries`
    rows).

    Returns a dict with:
      injury_count           — out + dtd
      out_count              — players with status == OUT
      dtd_count              — players with status DTD/Q/P/D
      out_players            — list of player names (out)
      missing_minutes        — sum of L10 avg minutes across out players
      missing_usage_pct      — sum of L10 usage proxies across out players
      usage_vacuum_factor    — 1 + (missing_usage_pct / team_total_usage)
      team_minutes_removed   — alias of missing_minutes (spec naming)
      team_usage_removed_pct — alias of missing_usage_pct
      key_player_out_flag    — 1 if any of the team's top-2 minutes
                               leaders is out, else 0
      injury_data_is_imputed — 1 only when team_abbr is missing
    """
    base = {
        "injury_count": 0, "out_count": 0, "dtd_count": 0,
        "out_players": [],
        "missing_minutes": 0.0, "missing_usage_pct": 0.0,
        "usage_vacuum_factor": 1.0,
        "team_minutes_removed": 0.0,
        "team_usage_removed_pct": 0.0,
        "key_player_out_flag": 0,
        "injury_data_is_imputed": 1 if not team_abbr else 0,
    }
    if not team_abbr:
        return base
    team_inj = injuries_by_team.get(team_abbr)
    if team_inj is None:
        # Not present in the canonical map. The caller passes a sport-
        # wide flag (`injury_source_empty`) when the entire collection
        # was empty (= true imputation). Otherwise: a team simply has
        # zero reported injuries, which is a valid real value.
        base["injury_data_is_imputed"] = 0
        return base
    base["injury_data_is_imputed"] = 0
    out_players = list(team_inj.get("out_players") or [])
    base["out_count"] = int(team_inj.get("out_count") or 0)
    base["dtd_count"] = int(team_inj.get("dtd_count") or 0)
    base["injury_count"] = base["out_count"] + base["dtd_count"]
    base["out_players"] = out_players

    # Minutes / usage aggregates from `nba_master_hub_2026` L10.
    if not minutes_usage_map:
        return base

    # 1) Primary join: bdl_id → minutes/usage payload.
    out_bdl_ids = set(team_inj.get("out_bdl_ids") or [])
    matched_pids: set = set()
    missing_min = 0.0
    missing_usage = 0.0
    for pid in out_bdl_ids:
        m = minutes_usage_map.get(pid)
        if not m:
            continue
        matched_pids.add(pid)
        missing_min += float(m.get("avg_minutes_l10") or 0.0)
        missing_usage += float(m.get("usage_proxy_l10") or 0.0)

    # 2) Fallback name match for any out player whose bdl_id either
    # wasn't in the source row OR didn't resolve in master_hub. Build
    # a name index lazily.
    matched_names = {
        minutes_usage_map[p]["player_name"].lower()
        for p in matched_pids
        if minutes_usage_map.get(p)
    }
    # Rough name normalization: strip common suffixes/prefixes.
    def _norm(n: str) -> str:
        return (n or "").lower().replace(" jr.", "").replace(" sr.", "")\
            .replace(" iii", "").replace(" ii", "").strip()
    name_index: Dict[str, int] = {}
    for pid_, payload_ in minutes_usage_map.items():
        nm = _norm(payload_.get("player_name") or "")
        if nm:
            name_index.setdefault(nm, pid_)
    for pname in out_players:
        nm = _norm(pname)
        pid = name_index.get(nm)
        if pid is None or pid in matched_pids:
            continue
        if (minutes_usage_map.get(pid, {}).get("player_name", "").lower()
                in matched_names):
            continue
        m = minutes_usage_map.get(pid) or {}
        if (m.get("team_abbr") or "").upper() != team_abbr:
            continue
        matched_pids.add(pid)
        missing_min += float(m.get("avg_minutes_l10") or 0.0)
        missing_usage += float(m.get("usage_proxy_l10") or 0.0)

    base["missing_minutes"] = round(missing_min, 2)
    base["missing_usage_pct"] = round(missing_usage, 2)
    base["team_minutes_removed"] = round(missing_min, 2)
    base["team_usage_removed_pct"] = round(missing_usage, 2)

    # Team-level minutes / usage totals (top-13 roster) for vacuum factor.
    roster = [v for v in minutes_usage_map.values()
              if (v.get("team_abbr") or "").upper() == team_abbr]
    roster_sorted = sorted(
        roster, key=lambda r: -float(r.get("avg_minutes_l10") or 0.0)
    )
    total_team_usage = sum(
        float(r.get("usage_proxy_l10") or 0.0) for r in roster_sorted[:13]
    )
    if total_team_usage > 0:
        base["usage_vacuum_factor"] = round(
            1.0 + missing_usage / total_team_usage, 3
        )
    # Key player flag: any of the team's top-2 minute leaders is out.
    top2_pids = set()
    for v in roster_sorted[:2]:
        # Recover the bdl_id by lookup (rare iteration, ok).
        for pid_, payload_ in minutes_usage_map.items():
            if payload_ is v:
                top2_pids.add(pid_)
                break
    if top2_pids & matched_pids:
        base["key_player_out_flag"] = 1
    return base


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
    # MLB only — projected/confirmed lineup card map.  Empty until an
    # external lineup feed populates `mlb_projected_lineups`.  Strict
    # no-leakage is enforced inside `lookup_slot` (as_of <= commence_time).
    if sport == "mlb":
        from services.mlb_lineups_loader import load_slot_map as _mlb_load_slot_map
        mlb_slot_map = await _mlb_load_slot_map(db, event_ids)
    else:
        mlb_slot_map = {}
    # 2026-05 — per-player minutes / usage (NBA only) used to size
    # `team_minutes_removed`, `team_usage_removed_pct`,
    # `usage_vacuum_factor`, `key_player_out_flag`.
    minutes_usage_map = await _build_player_minutes_usage_map(db, sport)
    # Sport-wide flag: when no canonical injury source returned ANY
    # rows, every prop's injury_context is imputed.
    injury_source_empty = (sum(
        v.get("out_count", 0) + v.get("dtd_count", 0)
        for v in injuries_by_team.values()
    ) == 0)

    counters = {
        "team_resolved": 0,
        "is_home_resolved": 0,
        "team_total_filled": 0,
        "game_total_filled": 0,
        "injuries_filled": 0,
        "rest_days_filled": 0,
        "park_team_filled": 0,
        "lineup_slot_filled": 0,
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

        # ---- Live injuries summary (legacy — basic counts) ----
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

        # ---- Team-level injury context (2026-05) ----
        # Available on NBA props (minutes_usage_map populated only for NBA).
        # `injury_data_is_imputed` flips to 1 when source data was empty
        # OR the player's team couldn't be resolved.
        team_inj_ctx = _compute_team_injury_features(
            team_abbr, injuries_by_team, minutes_usage_map,
        )
        opp_inj_ctx = _compute_team_injury_features(
            p.get("opponent_team"), injuries_by_team, minutes_usage_map,
        )
        if injury_source_empty:
            team_inj_ctx["injury_data_is_imputed"] = 1
            opp_inj_ctx["injury_data_is_imputed"] = 1
        p["team_injury_context"] = team_inj_ctx
        p["opp_injury_context"] = opp_inj_ctx
        # Spec field aliases for downstream consumers.
        p["team_injury_count"] = team_inj_ctx["injury_count"]
        p["team_out_count"] = team_inj_ctx["out_count"]
        p["missing_usage_estimate"] = team_inj_ctx["team_usage_removed_pct"]
        p["missing_minutes_estimate"] = team_inj_ctx["team_minutes_removed"]
        p["usage_vacuum_factor"] = team_inj_ctx["usage_vacuum_factor"]
        p["key_player_out_flag"] = team_inj_ctx["key_player_out_flag"]
        if team_inj_ctx["injury_data_is_imputed"] == 1:
            imputed_fields.append("injury_data")

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
            # MOCKED until external feed: probable_pitcher
            p["probable_pitcher"] = None
            p["opp_pitcher_id"] = None
            p["opp_pitcher_name"] = None
            p["opp_pitcher_throws"] = None
            # ---- batting_order via mlb_projected_lineups (strict no-leakage) ----
            from services.mlb_lineups_loader import lookup_slot as _mlb_lookup_slot
            slot, confirmed, lu_source = _mlb_lookup_slot(
                mlb_slot_map,
                p.get("event_id"),
                p.get("bdl_player_id"),
                p.get("commence_time"),
            )
            p["batting_order"] = slot
            p["lineup_confirmed"] = bool(confirmed)
            if slot is not None:
                counters["lineup_slot_filled"] += 1
                p["lineup_source"] = lu_source
            else:
                p["lineup_source"] = None
                # Still flag as imputed when slot is missing, so Step-5
                # missing-value policy stays honest.
                imputed_fields.append("batting_order")
                if not confirmed:
                    imputed_fields.append("lineup_confirmed")
            for k in ("probable_pitcher", "opp_pitcher_throws"):
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
        f"lineup_slots={counters['lineup_slot_filled']}  "
        f"top_imputed={list(report['imputed_field_summary'].items())[:5]}"
    )
    return report
