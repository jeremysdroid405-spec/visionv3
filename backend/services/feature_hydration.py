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
    opp_pitcher_era, opp_pitcher_whip, opp_pitcher_k9,
    batting_order, lineup_confirmed
    (matchup flags `same_hand_matchup` / `opposite_hand_matchup` are
    derived in `services/scoring/adapters/mlb_scoring._propagate_phase1_context`
    once `batter_hand` is stamped — keeps single source of truth.)

Guardrail: this module only writes ADDITIONAL keys onto each prop.
It never overwrites existing identity / odds / market fields.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Phase 2B (2026-05-15) — pitcher-prop stat types. Used to decide when
# to populate `opposing_lineup` on a prop (pitcher props only — for
# batter props the lineup-feature block stays imputed downstream).
_PITCHER_STAT_TYPES = frozenset({
    "Pitcher Strikeouts",
    "Pitcher Outs",
    "Earned Runs",
    "Hits Allowed",
    "Walks Allowed",
    "Pitcher Walks",
})


def _attach_inline_rolling_to_lineup(
    lineup: Optional[List[Dict[str, Any]]],
    game_date: Optional[str],
    sc_rolling_cache: Dict[int, Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Decorate each batter dict in `lineup` with an inline `rolling_14`
    block resolved as-of `game_date` from the pre-fetched SC cache.
    Returns a NEW list (does not mutate caller-supplied dicts)."""
    if not lineup:
        return lineup
    gd = str(game_date)[:10] if game_date else None
    out: List[Dict[str, Any]] = []
    for b in lineup:
        b2 = dict(b)
        bid = b2.get("batter_id")
        by_date = sc_rolling_cache.get(int(bid)) if bid is not None else None
        if by_date and gd:
            earlier = [d for d in by_date.keys() if d and d <= gd]
            if earlier:
                pick = max(earlier)
                rolling = (by_date[pick] or {}).get("rolling_14") or {}
                if rolling:
                    b2["rolling_14"] = rolling
        out.append(b2)
    return out


async def _hydrate_opposing_lineup_for_pitcher(
    *,
    opp_team_abbr: Optional[str],
    game_date: Optional[str],
    lineup_cache: Dict[Tuple[str, str], Optional[List[Dict[str, Any]]]],
    sc_rolling_cache: Dict[int, Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Per-prop lookup against the run-level lineup cache populated at
    the top of `hydrate_game_context_on_props`. Decorates each batter
    with an inline `rolling_14` block resolved as-of `game_date`.
    """
    if not opp_team_abbr or not game_date:
        return None
    key = (opp_team_abbr, str(game_date)[:10])
    lineup = lineup_cache.get(key)
    return _attach_inline_rolling_to_lineup(
        lineup, game_date, sc_rolling_cache,
    )


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


def _commence_date_iso(ct: Any) -> Optional[str]:
    """Coerce a commence_time (datetime / ISO str / epoch ms) into the
    YYYY-MM-DD UTC date string the MLB Stats API expects. Returns
    ``None`` when the input is unparseable so the caller can skip
    fetching pitchers for that prop without raising."""
    if ct is None:
        return None
    if isinstance(ct, datetime):
        d = ct if ct.tzinfo else ct.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(ct, (int, float)):
        try:
            d = datetime.fromtimestamp(float(ct) / 1000.0, tz=timezone.utc)
            return d.strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(ct, str):
        s = ct.strip()
        if not s:
            return None
        # Fast path: ISO date prefix.
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return None
    return None


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
        # Phase 2A (2026-05-15) — fetch real probable-pitcher index from
        # the free MLB Stats API. One index per UTC date that any prop
        # touches; typically 1 date per ingest cycle but doubleheaders
        # spanning UTC midnight produce 2.  Failures degrade gracefully
        # to an empty index — fields stay None, downstream behaviour is
        # identical to the previous mock path.
        from services.mlb_probable_pitcher import (
            get_probable_pitcher_index as _get_pp_index,
        )
        mlb_pp_indexes: Dict[str, Any] = {}
        unique_dates: set = set()
        for _p in props:
            _ct = _p.get("commence_time")
            _d = _commence_date_iso(_ct)
            if _d:
                unique_dates.add(_d)
        for _d in unique_dates:
            try:
                mlb_pp_indexes[_d] = await _get_pp_index(_d)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[CTX_HYDRATE:mlb] probable pitcher fetch failed "
                    "date=%s err=%s", _d, exc,
                )
                mlb_pp_indexes[_d] = None

        # ── Phase 2B (2026-05-15) — Opposing-lineup pre-fetch ───────
        # Pre-resolve opposing lineups for every (opp_team, game_date)
        # pair touched by a pitcher prop. One BDL-or-fallback lookup
        # per pair, NOT per prop — props sharing the same opponent on
        # the same day share a single lineup payload.
        # Each batter dict in the resolved lineup is enriched with
        # an inline `rolling_14` block read from
        # `mlb_statcast_player_features` as-of the game date so the
        # downstream model can compute lineup_strength_14d without
        # a second lookup loop.
        from services.mlb_live_lineup_feed import (
            fetch_opposing_lineup as _mlb_fetch_opposing_lineup,
        )
        # Collect unique pitcher-prop (opp_team, game_date) pairs.
        mlb_pitcher_pairs: set = set()
        for _p in props:
            if (_p.get("stat_type") or "").strip() in _PITCHER_STAT_TYPES:
                ot = _p.get("opponent_team")
                gd = _commence_date_iso(_p.get("commence_time"))
                if ot and gd:
                    mlb_pitcher_pairs.add((ot, gd))
        mlb_lineup_cache: Dict[Tuple[str, str],
                               Optional[List[Dict[str, Any]]]] = {}
        mlb_sc_rolling_cache: Dict[int, Dict[str, Any]] = {}
        for opp_team, gd in mlb_pitcher_pairs:
            try:
                lineup = await _mlb_fetch_opposing_lineup(
                    db, opp_team, gd,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[CTX_HYDRATE:mlb] opposing lineup fetch failed "
                    "opp=%s date=%s err=%s", opp_team, gd, exc,
                )
                lineup = None
            mlb_lineup_cache[(opp_team, gd)] = lineup
            # Collect batter IDs whose rolling-14 we still need.
            if lineup:
                for b in lineup:
                    bid = b.get("batter_id")
                    if bid is not None and bid not in mlb_sc_rolling_cache:
                        mlb_sc_rolling_cache[int(bid)] = {}
        # Batch-fetch rolling_14 for all batters across all pairs.
        # `mlb_statcast_player_features` is keyed by (player_id,
        # game_date). We pick the latest doc dated <= game_date for
        # the relevant batter — falls back gracefully when no SC row
        # exists (lineup_strength stays imputed).
        if mlb_sc_rolling_cache:
            try:
                async for sc_doc in db["mlb_statcast_player_features"].find(
                    {"player_id": {"$in": list(mlb_sc_rolling_cache.keys())}},
                    {"_id": 0, "player_id": 1,
                     "game_date": 1, "rolling_14": 1},
                ):
                    bid = int(sc_doc.get("player_id"))
                    gd = sc_doc.get("game_date") or ""
                    mlb_sc_rolling_cache.setdefault(bid, {})[str(gd)[:10]] = {
                        "rolling_14": sc_doc.get("rolling_14") or {}
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[CTX_HYDRATE:mlb] sc_rolling pre-fetch failed err=%s",
                    exc,
                )
    else:
        mlb_slot_map = {}
        mlb_pp_indexes = {}
        mlb_lineup_cache = {}
        mlb_sc_rolling_cache = {}
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
            # ── Phase 2A (2026-05-15) — Probable Pitcher Wiring ─────────
            # Replaces the four previously-mocked fields with values
            # from `services.mlb_probable_pitcher`. Selection rule:
            # opposing pitcher = pitcher on the OTHER side of the game.
            #   batter is home → opp pitcher = away.probable
            #   batter is away → opp pitcher = home.probable
            # Falls back to None when:
            #   • home_abbr/away_abbr unresolved
            #   • is_home_team unresolved (sided lookup ambiguous)
            #   • MLB Stats API returned no probable pitcher
            opp_pitcher = None
            pp_date = _commence_date_iso(p.get("commence_time"))
            pp_idx = mlb_pp_indexes.get(pp_date) if pp_date else None
            if pp_idx is not None and home_abbr and away_abbr:
                pair = pp_idx.get(home_abbr, away_abbr)
                if pair:
                    if is_home_team == 1:
                        opp_pitcher = pair.get("away")
                    elif is_home_team == 0:
                        opp_pitcher = pair.get("home")
            if opp_pitcher:
                p["opp_pitcher_id"] = opp_pitcher.get("id")
                p["opp_pitcher_name"] = opp_pitcher.get("name")
                p["opp_pitcher_throws"] = opp_pitcher.get("throws")
                p["opp_pitcher_era"] = opp_pitcher.get("era")
                p["opp_pitcher_whip"] = opp_pitcher.get("whip")
                p["opp_pitcher_k9"] = opp_pitcher.get("k9")
                # Display alias used by UI / picks.
                p["probable_pitcher"] = opp_pitcher.get("name")
                counters["probable_pitcher_filled"] = counters.get(
                    "probable_pitcher_filled", 0) + 1
            else:
                p["opp_pitcher_id"] = None
                p["opp_pitcher_name"] = None
                p["opp_pitcher_throws"] = None
                p["opp_pitcher_era"] = None
                p["opp_pitcher_whip"] = None
                p["opp_pitcher_k9"] = None
                p["probable_pitcher"] = None
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
                if p.get(k) is None:
                    imputed_fields.append(k)

            # ── Phase 2B (2026-05-15) — Opposing Lineup Hydration ───────
            # Pitcher props only: when the player IS the pitcher (stat
            # in `_PITCHER_STAT_TYPES`), populate `opposing_lineup` with
            # the opponent team's batters as resolved by the live feed:
            #   BDL posted lineup → last-played fallback → None.
            # Each batter dict carries `batter_id`, `stand`, and an
            # inline `rolling_14` block (k_rate/bb_rate/wOBA/xwOBA/
            # hard_hit_rate/barrel_rate) read as-of the prop's
            # commence_date from `mlb_statcast_player_features`. The
            # downstream model emits the canonical 21-feature
            # lineup-context block; missing inputs raise
            # `*_is_imputed=1` flags (see
            # services/mlb_lineup_features.py).
            #
            # Batter props leave `opposing_lineup` unset — the
            # lineup-feature block is N/A and is emitted imputed.
            stat_type_norm = (p.get("stat_type") or "").strip()
            if stat_type_norm in _PITCHER_STAT_TYPES:
                lineup = await _hydrate_opposing_lineup_for_pitcher(
                    opp_team_abbr=p.get("opponent_team"),
                    game_date=_commence_date_iso(p.get("commence_time")),
                    lineup_cache=mlb_lineup_cache,
                    sc_rolling_cache=mlb_sc_rolling_cache,
                )
                if lineup:
                    p["opposing_lineup"] = lineup
                    p["opposing_lineup_size"] = len(lineup)
                    counters["opposing_lineup_filled"] = counters.get(
                        "opposing_lineup_filled", 0) + 1
                else:
                    p["opposing_lineup"] = None
                    p["opposing_lineup_size"] = 0
                    imputed_fields.append("opposing_lineup")

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
        f"probable_pitcher_filled={counters.get('probable_pitcher_filled', 0)}  "
        f"top_imputed={list(report['imputed_field_summary'].items())[:5]}"
    )
    return report
