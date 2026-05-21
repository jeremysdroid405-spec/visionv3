"""
ingest_historical_player_stats.py — populate sgo_player_stats so the
outcomes pipeline can grade props.

Driver:
    sgo_pp_research_core_enriched   (for the date/event/player universe)

Reads (immutable):
    sgo_events           (source = sgo: re-extract from preserved .raw payload)
    sgo_players          (player_id ↔ player_name cache)

Writes:
    sgo_player_stats     (idempotent upserts)

SOURCES (--source):
    sgo            (default) Re-extract from sgo_events.raw via the existing
                   normalize.extract_player_stats(). No API calls; uses already-
                   archived event payloads. Works for ALL leagues.
    mlbstatsapi    Fetch from the FREE public MLB Stats API
                   (https://statsapi.mlb.com — no key needed). MLB only.
                   Maps player_name from boxscore → SGO player_id via sgo_players.
    bdl            Fetch from balldontlie.io (BDL_API_KEY required). NBA only.
                   Maps player_name → SGO player_id via sgo_players.
    auto           SGO first, then league-appropriate secondary (MLB→mlbstatsapi,
                   NBA→bdl) for any dates/events still missing stats.
    both           Alias for `auto` (kept for backwards compat).

LEAGUE SUPPORT
    MLB: stats normalized to {hits, singles, doubles, triples, home_runs,
         total_bases, runs, rbi, walks, strikeouts, stolen_bases,
         pitcher_strikeouts, pitching_outs, pitching_hits_allowed,
         pitching_earned_runs, pitching_walks, pitches_thrown, fantasy_score}
    NBA: stats normalized to {points, rebounds, assists, three_pointers_made,
         blocks, steals, turnovers, pra, pts_reb, pts_ast, reb_ast,
         blocks_steals, fantasy_score, minutes}

Usage:
    python -m scripts.sgo.ingest_historical_player_stats --league MLB --dry-run
    python -m scripts.sgo.ingest_historical_player_stats --league MLB
    python -m scripts.sgo.ingest_historical_player_stats --league MLB \\
        --source mlbstatsapi --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.ingest_historical_player_stats --league MLB \\
        --source both --resume
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

import httpx
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

OUT_COLL = "sgo_player_stats"
EVENT_COLL = "sgo_events"
PLAYERS_COLL = "sgo_players"
DRIVER_COLL = "sgo_pp_research_core_enriched"

INGEST_VERSION = "v1"
MLB_BASE = "https://statsapi.mlb.com/api/v1"


# ───────────────────────────── normalize helpers ──────────────────────────
def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _g(d: Optional[Dict[str, Any]], *keys: str) -> Any:
    if not isinstance(d, dict): return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    lm = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
    for k in keys:
        v = lm.get(k.lower())
        if v is not None: return v
    return None


def normalize_stats(raw: Dict[str, Any], *, league: Optional[str] = None
                      ) -> Dict[str, Any]:
    """Project raw provider stats → canonical fields. Missing → None.

    Dispatches by league: MLB stats are derived from baseball box scores,
    NBA stats from basketball box scores. When league is unknown or absent
    we attempt both projections and return whichever has more non-null
    fields (rare path; production callers should pass league).
    """
    if not raw:
        return {}
    lg = (league or "").upper()
    if lg == "MLB":
        return _normalize_mlb_stats(raw)
    if lg == "NBA":
        return _normalize_nba_stats(raw)
    # Auto-detect: try both, pick the one with more signal
    mlb = _normalize_mlb_stats(raw)
    nba = _normalize_nba_stats(raw)
    mlb_signal = sum(1 for v in mlb.values() if v is not None)
    nba_signal = sum(1 for v in nba.values() if v is not None)
    return mlb if mlb_signal >= nba_signal else nba


def _normalize_mlb_stats(raw: Dict[str, Any]) -> Dict[str, Any]:
    hits      = _num(_g(raw, "batting_hits", "hits", "H"))
    singles   = _num(_g(raw, "batting_singles", "singles", "1B"))
    doubles   = _num(_g(raw, "batting_doubles", "doubles", "2B"))
    triples   = _num(_g(raw, "batting_triples", "triples", "3B"))
    hr        = _num(_g(raw, "batting_homeRuns", "homeRuns",
                          "home_runs", "HR"))
    tb        = _num(_g(raw, "batting_totalBases", "totalBases",
                          "total_bases", "TB"))
    if tb is None and hits is not None:
        # Derive TB if all components present: TB = 1B + 2*2B + 3*3B + 4*HR
        if all(v is not None for v in (singles, doubles, triples, hr)):
            tb = singles + 2*doubles + 3*triples + 4*hr  # type: ignore[operator]
    return {
        "hits":                  hits,
        "singles":               singles,
        "doubles":               doubles,
        "triples":               triples,
        "home_runs":             hr,
        "total_bases":           tb,
        "runs":                  _num(_g(raw, "batting_runs", "runs", "R")),
        "rbi":                   _num(_g(raw, "batting_RBI", "batting_rbi",
                                          "rbi", "RBI")),
        "walks":                 _num(_g(raw, "batting_basesOnBalls",
                                          "batting_walks", "walks",
                                          "baseOnBalls", "base_on_balls", "BB")),
        "strikeouts":            _num(_g(raw, "batting_strikeouts",
                                          "strikeouts", "K", "SO")),
        "stolen_bases":          _num(_g(raw, "batting_stolenBases",
                                          "stolenBases", "stolen_bases", "SB")),
        "pitcher_strikeouts":    _num(_g(raw, "pitching_strikeouts",
                                          "pitcher_strikeouts",
                                          "strikeOutsPitched",
                                          "strikeoutsPitched")),
        "pitching_outs":         _num(_g(raw, "pitching_outs", "outs",
                                          "outsPitched")),
        "pitching_hits_allowed": _num(_g(raw, "pitching_hits", "hitsAllowed",
                                          "hits_allowed",
                                          "pitching_hits_allowed")),
        "pitching_earned_runs":  _num(_g(raw, "pitching_earnedRuns",
                                          "earnedRuns", "earned_runs", "ER",
                                          "pitching_earned_runs")),
        "pitching_walks":        _num(_g(raw, "pitching_basesOnBalls",
                                          "pitching_walks", "pitcher_walks",
                                          "walksAllowed", "walks_allowed")),
        "pitches_thrown":        _num(_g(raw, "pitching_pitchesThrown",
                                          "pitchesThrown", "pitches_thrown",
                                          "numberOfPitches", "pitchCount")),
        "fantasy_score":         _num(_g(raw, "fantasyScore", "fantasy_score",
                                          "fantasyPoints")),
    }


def _normalize_nba_stats(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Project raw provider stats → canonical NBA fields."""
    if not raw:
        return {}
    pts = _num(_g(raw, "points", "pts", "PTS"))
    reb = _num(_g(raw, "rebounds", "reb", "REB", "totalRebounds"))
    ast = _num(_g(raw, "assists", "ast", "AST"))
    blk = _num(_g(raw, "blocks", "blk", "BLK"))
    stl = _num(_g(raw, "steals", "stl", "STL"))
    tov = _num(_g(raw, "turnovers", "turnover", "to", "TO", "TOV"))
    tpm = _num(_g(raw, "three_pointers_made", "threePointersMade",
                    "threes_made", "fg3m", "FG3M", "3PM"))
    # Composites
    pra = (pts + reb + ast) if all(v is not None for v in (pts, reb, ast)) else None
    pts_reb = (pts + reb) if all(v is not None for v in (pts, reb)) else None
    pts_ast = (pts + ast) if all(v is not None for v in (pts, ast)) else None
    reb_ast = (reb + ast) if all(v is not None for v in (reb, ast)) else None
    blk_stl = (blk + stl) if all(v is not None for v in (blk, stl)) else None
    # Minutes can come as "MM:SS" string or float
    mins_raw = _g(raw, "minutes", "min", "MIN")
    mins: Optional[float] = None
    if isinstance(mins_raw, str) and ":" in mins_raw:
        try:
            m, s = mins_raw.split(":")
            mins = float(m) + float(s) / 60.0
        except (TypeError, ValueError):
            mins = None
    elif mins_raw is not None:
        mins = _num(mins_raw)
    return {
        "points":              pts,
        "rebounds":            reb,
        "assists":             ast,
        "three_pointers_made": tpm,
        "blocks":              blk,
        "steals":              stl,
        "turnovers":           tov,
        "pra":                 pra,
        "pts_reb":             pts_reb,
        "pts_ast":             pts_ast,
        "reb_ast":             reb_ast,
        "blocks_steals":       blk_stl,
        "fantasy_score":       _num(_g(raw, "fantasyScore", "fantasy_score",
                                        "fantasyPoints")),
        "minutes":             mins,
    }


# ───────────────────────────── source: SGO re-extract ─────────────────────
def _iter_sgo_player_rows(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SGO events store playerStats under various keys. Tolerant to drift."""
    raw = (ev.get("playerStats") or ev.get("players")
           or ev.get("playerResults") or [])
    out = []
    for ps in raw:
        pid = _g(ps, "playerID", "player_id", "id")
        if not pid:
            continue
        out.append({
            "player_id":   pid,
            "player_name": _g(ps, "playerName", "name"),
            "team_id":     _g(ps, "teamID", "team_id"),
            "stats_raw":   _g(ps, "stats", "statistics", "stat") or {},
        })
    return out


async def ingest_from_sgo(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    start: Optional[str], end: Optional[str], dry_run: bool,
    resume: bool,
) -> Dict[str, Any]:
    """Re-extract player stats from sgo_events.raw and upsert."""
    match: Dict[str, Any] = {}
    if league: match["league_id"] = league
    # Date filtering uses sgo_events.start_time (ISO) or game_date if SGO
    # stamped it. Use both with $or for robustness.
    if start or end:
        clauses = []
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        clauses.append({"game_date": gd})
        # start_time as ISO YYYY-MM-DD... compare lexicographically
        st: Dict[str, Any] = {}
        if start: st["$gte"] = start
        if end:   st["$lte"] = end + "T23:59:59Z"
        clauses.append({"start_time": st})
        match["$or"] = clauses

    upserts: List[UpdateOne] = []
    events_scanned = 0
    rows_emitted = 0
    events_with_zero = 0
    coverage: Dict[str, int] = {}
    sample_docs: List[Dict[str, Any]] = []

    async for ev_doc in db[EVENT_COLL].find(match, {"_id": 0}):
        events_scanned += 1
        raw_ev = ev_doc.get("raw") or {}
        eid = ev_doc.get("event_id")
        # Resolve a per-event game_date (yyyy-mm-dd)
        gd = ev_doc.get("game_date")
        if not gd:
            st = ev_doc.get("start_time") or _g(raw_ev, "startTime",
                                                  "commenceTime", "commence_time")
            if isinstance(st, str): gd = st[:10]
        league_id = ev_doc.get("league_id") or _g(raw_ev, "leagueID", "league_id")
        sport_id = ev_doc.get("sport_id") or _g(raw_ev, "sportID", "sport_id")
        home_team = ev_doc.get("home_team_name") or _g(
            raw_ev, "homeTeamName", "homeTeam", "home_team_name")
        away_team = ev_doc.get("away_team_name") or _g(
            raw_ev, "awayTeamName", "awayTeam", "away_team_name")

        player_rows = _iter_sgo_player_rows(raw_ev)
        if not player_rows:
            events_with_zero += 1
            continue

        for pr in player_rows:
            stats = normalize_stats(pr["stats_raw"] or {}, league=league_id)
            # coverage counter — non-null fields
            for k, v in stats.items():
                if v is not None:
                    coverage[k] = coverage.get(k, 0) + 1

            team_id = pr.get("team_id")
            doc = {
                "event_id":    eid,
                "league_id":   league_id,
                "sport_id":    sport_id,
                "game_date":   gd,
                "player_id":   pr["player_id"],
                "player_name": pr["player_name"],
                "team_id":     team_id,
                "team":        home_team if team_id and team_id ==
                                ev_doc.get("home_team_id") else away_team
                                if team_id and team_id == ev_doc.get("away_team_id")
                                else None,
                "opponent":    away_team if team_id and team_id ==
                                ev_doc.get("home_team_id") else home_team
                                if team_id and team_id == ev_doc.get("away_team_id")
                                else None,
                "stats":       stats,
                "raw_source":  {"sgo_event_raw_stats": pr["stats_raw"]},
                "source":      "sgo",
                "ingest_version": INGEST_VERSION,
                "ingested_at": datetime.now(timezone.utc),
            }
            if len(sample_docs) < 2 and any(v is not None
                                              for v in stats.values()):
                sample_docs.append(doc)
            rows_emitted += 1
            filt = {"event_id": eid, "player_id": pr["player_id"]}
            upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
            if len(upserts) >= 1000 and not dry_run:
                await db[OUT_COLL].bulk_write(upserts, ordered=False)
                upserts = []
    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "source":           "sgo",
        "events_scanned":   events_scanned,
        "events_with_zero": events_with_zero,
        "rows_emitted":     rows_emitted,
        "coverage":         coverage,
        "sample_docs":      sample_docs,
    }


# ──────────────────────────── source: MLB Stats API ───────────────────────
async def _mlb_schedule(client: httpx.AsyncClient, date: str
                         ) -> List[Dict[str, Any]]:
    """List of game dicts on a given YYYY-MM-DD."""
    r = await client.get(f"{MLB_BASE}/schedule",
                          params={"sportId": 1, "date": date})
    r.raise_for_status()
    js = r.json()
    games: List[Dict[str, Any]] = []
    for d in (js.get("dates") or []):
        for g in (d.get("games") or []):
            games.append(g)
    return games


async def _mlb_boxscore(client: httpx.AsyncClient, gamePk: int
                          ) -> Optional[Dict[str, Any]]:
    r = await client.get(f"{MLB_BASE}/game/{gamePk}/boxscore")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _mlb_extract_players(bs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk MLB Stats API boxscore → flat list of {name, team, opp, stats}."""
    out: List[Dict[str, Any]] = []
    teams = bs.get("teams") or {}
    home = (teams.get("home") or {})
    away = (teams.get("away") or {})
    home_name = (home.get("team") or {}).get("name")
    away_name = (away.get("team") or {}).get("name")
    for side, opp in (("home", away_name), ("away", home_name)):
        side_obj = teams.get(side) or {}
        team_name = (side_obj.get("team") or {}).get("name")
        players = side_obj.get("players") or {}
        for _pid_key, p in players.items():
            person = p.get("person") or {}
            name = person.get("fullName")
            if not name:
                continue
            stats = (p.get("stats") or {})
            bat   = stats.get("batting") or {}
            pit   = stats.get("pitching") or {}
            merged = {**bat, **pit}
            if not merged:
                continue  # didn't play / no stats
            # Normalize MLB stats API field names to our canonical raw shape
            raw = {
                "hits":          bat.get("hits"),
                "singles":       (bat.get("hits") or 0)
                                  - ((bat.get("doubles") or 0)
                                     + (bat.get("triples") or 0)
                                     + (bat.get("homeRuns") or 0))
                                  if bat.get("hits") is not None else None,
                "doubles":       bat.get("doubles"),
                "triples":       bat.get("triples"),
                "homeRuns":      bat.get("homeRuns"),
                "totalBases":    bat.get("totalBases"),
                "runs":          bat.get("runs"),
                "rbi":           bat.get("rbi"),
                "baseOnBalls":   bat.get("baseOnBalls"),
                "strikeouts":    bat.get("strikeOuts") or bat.get("strikeouts"),
                "stolenBases":   bat.get("stolenBases"),
                # pitching
                "strikeOutsPitched":  pit.get("strikeOuts"),
                "outs":               pit.get("outs"),
                "hitsAllowed":        pit.get("hits"),
                "earnedRuns":         pit.get("earnedRuns"),
                "walksAllowed":       pit.get("baseOnBalls"),
                "numberOfPitches":    pit.get("numberOfPitches")
                                       or pit.get("pitchesThrown"),
            }
            out.append({
                "player_name": name,
                "team":        team_name,
                "opponent":    opp,
                "stats_raw":   raw,
                "mlb_player_id": person.get("id"),
            })
    return out


async def _load_sgo_player_lookup(
    db: AsyncIOMotorDatabase, *, league: str
) -> Dict[str, str]:
    """Returns {player_name.lower() → sgo_player_id} restricted to league."""
    lookup: Dict[str, str] = {}
    async for p in db[PLAYERS_COLL].find(
        {"$or": [{"league_id": league}, {"sport_id": "BASEBALL"}]},
        {"_id": 0, "player_id": 1, "player_name": 1}
    ):
        nm = (p.get("player_name") or "").strip().lower()
        if nm and p.get("player_id"):
            lookup[nm] = p["player_id"]
    return lookup


async def ingest_from_mlbstatsapi(
    db: AsyncIOMotorDatabase, *, league: str,
    start: Optional[str], end: Optional[str], dry_run: bool,
    resume: bool, rpm: int = 30,
    only_dates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch box scores from statsapi.mlb.com for the date window."""
    # Date universe
    if only_dates:
        dates = sorted(only_dates)
    else:
        match: Dict[str, Any] = {"league_id": league}
        if start or end:
            gd: Dict[str, Any] = {}
            if start: gd["$gte"] = start
            if end:   gd["$lte"] = end
            match["game_date"] = gd
        dates = []
        async for r in db[DRIVER_COLL].aggregate(
            [{"$match": match}, {"$group": {"_id": "$game_date"}},
              {"$sort": {"_id": 1}}], allowDiskUse=True):
            if r.get("_id"):
                dates.append(r["_id"])

    if not dates:
        return {"source": "mlbstatsapi", "dates": 0, "games": 0,
                 "rows_emitted": 0, "coverage": {}, "sample_docs": [],
                 "unmapped_names": []}

    name_lookup = await _load_sgo_player_lookup(db, league=league)
    print(f"  [mlbstatsapi] {len(dates)} dates  |  "
          f"sgo_players name map = {len(name_lookup):,} entries")

    rows_emitted = 0
    games_processed = 0
    games_missing = 0
    coverage: Dict[str, int] = {}
    unmapped: set = set()
    sample_docs: List[Dict[str, Any]] = []
    upserts: List[UpdateOne] = []

    # Polite rate limit: ~30 req/min (well under any reasonable quota)
    delay = 60.0 / max(rpm, 1)
    last_call = 0.0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for d in dates:
            try:
                games = await _mlb_schedule(client, d)
            except Exception as e:
                print(f"  [mlbstatsapi] schedule fail {d}: {e!r}")
                continue
            for g in games:
                gamePk = g.get("gamePk")
                if not gamePk:
                    games_missing += 1
                    continue
                # rate limit
                now = time.monotonic()
                if (now - last_call) < delay:
                    await asyncio.sleep(delay - (now - last_call))
                last_call = time.monotonic()
                try:
                    bs = await _mlb_boxscore(client, gamePk)
                except Exception as e:
                    print(f"  [mlbstatsapi] box fail {gamePk}: {e!r}")
                    continue
                if not bs:
                    games_missing += 1
                    continue
                games_processed += 1
                # Map this game to a SGO event_id via team-name fuzzy match
                home_name = ((bs.get("teams") or {}).get("home") or {}
                              ).get("team", {}).get("name")
                away_name = ((bs.get("teams") or {}).get("away") or {}
                              ).get("team", {}).get("name")
                ev = await db[EVENT_COLL].find_one(
                    {"league_id": league, "game_date": d,
                      "$or": [
                        {"home_team_name": home_name,
                          "away_team_name": away_name},
                        {"home_team_name": away_name,
                          "away_team_name": home_name},
                      ]},
                    {"_id": 0, "event_id": 1})
                sgo_event_id = (ev or {}).get("event_id")  # may be None

                for pr in _mlb_extract_players(bs):
                    stats = normalize_stats(pr["stats_raw"], league="MLB")
                    for k, v in stats.items():
                        if v is not None:
                            coverage[k] = coverage.get(k, 0) + 1
                    # Map to SGO player_id
                    nm = (pr["player_name"] or "").strip().lower()
                    sgo_pid = name_lookup.get(nm)
                    if not sgo_pid:
                        # Try "first last" only (drop suffixes)
                        sgo_pid = name_lookup.get(
                            nm.split(",")[0].strip())
                    if not sgo_pid:
                        unmapped.add(pr["player_name"])
                        # Still emit a row keyed by player_name so feature
                        # builders that join by name can still use it.
                        sgo_pid = f"mlbam:{pr['mlb_player_id']}" \
                                  if pr.get("mlb_player_id") else None
                    if not sgo_pid:
                        continue

                    doc = {
                        "event_id":    sgo_event_id,
                        "league_id":   league,
                        "sport_id":    "BASEBALL",
                        "game_date":   d,
                        "player_id":   sgo_pid,
                        "player_name": pr["player_name"],
                        "team":        pr["team"],
                        "opponent":    pr["opponent"],
                        "stats":       stats,
                        "raw_source":  {"mlbstatsapi_raw": pr["stats_raw"],
                                          "mlb_player_id": pr.get("mlb_player_id"),
                                          "gamePk": gamePk},
                        "source":      "mlbstatsapi",
                        "ingest_version": INGEST_VERSION,
                        "ingested_at": datetime.now(timezone.utc),
                    }
                    if len(sample_docs) < 2 and any(v is not None
                                                       for v in stats.values()):
                        sample_docs.append(doc)
                    rows_emitted += 1
                    filt = {"event_id": sgo_event_id, "player_id": sgo_pid}
                    if not sgo_event_id:
                        filt = {"game_date": d, "player_id": sgo_pid,
                                 "league_id": league}
                    upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
                    if len(upserts) >= 1000 and not dry_run:
                        await db[OUT_COLL].bulk_write(upserts, ordered=False)
                        upserts = []
            print(f"  [mlbstatsapi] {d}  games={len(games)}  "
                  f"rows_total={rows_emitted:,}")
    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "source":           "mlbstatsapi",
        "dates":            len(dates),
        "games":            games_processed,
        "games_missing":    games_missing,
        "rows_emitted":     rows_emitted,
        "coverage":         coverage,
        "sample_docs":      sample_docs,
        "unmapped_names":   sorted(list(unmapped))[:30],
        "unmapped_count":   len(unmapped),
    }


# ───────────────────────────── indexes ────────────────────────────────────
# ───────────────────────────── source: BallDontLie (NBA) ──────────────────
BDL_BASE = "https://api.balldontlie.io/v1"


async def _bdl_stats_for_date(client: httpx.AsyncClient, *, api_key: str,
                                 date: str, per_page: int = 100
                                 ) -> List[Dict[str, Any]]:
    """Fetch all player stats rows for one date (paginated)."""
    headers = {"Authorization": api_key}
    out: List[Dict[str, Any]] = []
    cursor: Optional[int] = None
    page = 0
    while True:
        page += 1
        params: Dict[str, Any] = {"dates[]": date, "per_page": per_page}
        if cursor is not None:
            params["cursor"] = cursor
        r = await client.get(f"{BDL_BASE}/stats",
                              headers=headers, params=params)
        if r.status_code == 429:
            await asyncio.sleep(2.0)
            continue
        r.raise_for_status()
        js = r.json()
        rows = js.get("data") or []
        out.extend(rows)
        meta = js.get("meta") or {}
        nxt = meta.get("next_cursor")
        if not nxt:
            break
        cursor = nxt
        if page >= 50:  # safety cap
            break
    return out


async def ingest_from_bdl(
    db: AsyncIOMotorDatabase, *, start: Optional[str], end: Optional[str],
    dry_run: bool, rpm: int = 30, only_dates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch NBA player stats from BallDontLie for the date window."""
    api_key = os.environ.get("BDL_API_KEY", "")
    if not api_key:
        return {"source": "bdl", "error": "BDL_API_KEY not set in env",
                 "dates": 0, "games": 0, "rows_emitted": 0, "coverage": {},
                 "sample_docs": [], "unmapped_count": 0,
                 "unmapped_names": []}

    if only_dates:
        dates = sorted(only_dates)
    else:
        match: Dict[str, Any] = {"league_id": "NBA"}
        if start or end:
            gd: Dict[str, Any] = {}
            if start: gd["$gte"] = start
            if end:   gd["$lte"] = end
            match["game_date"] = gd
        dates = []
        async for r in db[DRIVER_COLL].aggregate(
            [{"$match": match}, {"$group": {"_id": "$game_date"}},
              {"$sort": {"_id": 1}}], allowDiskUse=True):
            if r.get("_id"): dates.append(r["_id"])

    if not dates:
        return {"source": "bdl", "dates": 0, "games": 0, "rows_emitted": 0,
                 "coverage": {}, "sample_docs": [], "unmapped_count": 0,
                 "unmapped_names": []}

    name_lookup = await _load_sgo_player_lookup(db, league="NBA")
    print(f"  [bdl] {len(dates)} dates  |  "
          f"sgo_players(NBA) name map = {len(name_lookup):,} entries")

    rows_emitted = 0
    games_seen: set = set()
    coverage: Dict[str, int] = {}
    unmapped: set = set()
    sample_docs: List[Dict[str, Any]] = []
    upserts: List[UpdateOne] = []

    delay = 60.0 / max(rpm, 1)
    last_call = 0.0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for d in dates:
            now = time.monotonic()
            if (now - last_call) < delay:
                await asyncio.sleep(delay - (now - last_call))
            last_call = time.monotonic()
            try:
                rows = await _bdl_stats_for_date(
                    client, api_key=api_key, date=d)
            except Exception as e:
                print(f"  [bdl] {d} fail: {e!r}")
                continue
            for row in rows:
                game = row.get("game") or {}
                games_seen.add(game.get("id"))
                game_date = (game.get("date") or "")[:10] or d
                player = row.get("player") or {}
                team   = row.get("team") or {}
                p_first = player.get("first_name") or ""
                p_last  = player.get("last_name") or ""
                full_name = f"{p_first} {p_last}".strip()
                # Build raw stat dict in our normalize schema
                raw_stats = {
                    "points":              row.get("pts"),
                    "rebounds":            row.get("reb"),
                    "assists":             row.get("ast"),
                    "blocks":              row.get("blk"),
                    "steals":              row.get("stl"),
                    "turnovers":           row.get("turnover"),
                    "three_pointers_made": row.get("fg3m"),
                    "minutes":             row.get("min"),
                }
                stats = normalize_stats(raw_stats, league="NBA")
                for k, v in stats.items():
                    if v is not None:
                        coverage[k] = coverage.get(k, 0) + 1
                # Map player_name → SGO player_id
                nm = full_name.strip().lower()
                sgo_pid = name_lookup.get(nm)
                if not sgo_pid:
                    unmapped.add(full_name)
                    sgo_pid = f"bdl:{player.get('id')}" \
                              if player.get("id") else None
                if not sgo_pid:
                    continue
                # Try to map BDL game → SGO event_id via (date, team-pair)
                home_id = game.get("home_team_id")
                visitor_id = game.get("visitor_team_id")
                team_name = team.get("full_name") or team.get("name")
                home_name = None
                away_name = None
                if home_id is not None and visitor_id is not None:
                    # We don't have a team_id → name map here; try matching
                    # against sgo_events by game_date alone, fuzzy team check
                    pass  # event_id mapping is best-effort below
                ev = await db[EVENT_COLL].find_one(
                    {"league_id": "NBA", "game_date": game_date,
                      "$or": [
                        {"home_team_name": team_name},
                        {"away_team_name": team_name},
                      ]},
                    {"_id": 0, "event_id": 1, "home_team_name": 1,
                      "away_team_name": 1})
                sgo_event_id = (ev or {}).get("event_id")
                opp = None
                if ev:
                    if ev.get("home_team_name") == team_name:
                        opp = ev.get("away_team_name")
                    elif ev.get("away_team_name") == team_name:
                        opp = ev.get("home_team_name")

                doc = {
                    "event_id":    sgo_event_id,
                    "league_id":   "NBA",
                    "sport_id":    "BASKETBALL",
                    "game_date":   game_date,
                    "player_id":   sgo_pid,
                    "player_name": full_name,
                    "team":        team_name,
                    "opponent":    opp,
                    "stats":       stats,
                    "raw_source":  {"bdl_raw": row,
                                      "bdl_player_id": player.get("id"),
                                      "bdl_game_id": game.get("id")},
                    "source":      "bdl",
                    "ingest_version": INGEST_VERSION,
                    "ingested_at": datetime.now(timezone.utc),
                }
                if len(sample_docs) < 2 and any(v is not None
                                                   for v in stats.values()):
                    sample_docs.append(doc)
                rows_emitted += 1
                filt = ({"event_id": sgo_event_id, "player_id": sgo_pid}
                        if sgo_event_id else
                        {"game_date": game_date, "player_id": sgo_pid,
                          "league_id": "NBA"})
                upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
                if len(upserts) >= 1000 and not dry_run:
                    await db[OUT_COLL].bulk_write(upserts, ordered=False)
                    upserts = []
            print(f"  [bdl] {d}  rows={len(rows)}  "
                  f"rows_total={rows_emitted:,}")
    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "source":         "bdl",
        "dates":          len(dates),
        "games":          len(games_seen - {None}),
        "games_missing":  0,
        "rows_emitted":   rows_emitted,
        "coverage":       coverage,
        "sample_docs":    sample_docs,
        "unmapped_names": sorted(list(unmapped))[:30],
        "unmapped_count": len(unmapped),
    }


# ───────────────────────────── source: SGO API (expandResults) ────────────
def _extract_results_to_player_rows(
    ev: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Walk an SGO event's `results` object (returned ONLY when
    expandResults=true) → flat list of {player_id, stat_entity_id, stats_raw, ...}.

    Defensive against multiple shapes since SGO doesn't publish an exact
    schema for the results object. We probe in order:
      1. event.results.game = {stat_entity_id: {stat_id: value, ...}, ...}
         (the actual SGO production shape — keys include "home"/"away"
          for team aggregates which we skip; player entity IDs look like
          "SHOHEI_OHTANI_1_MLB" / "FIRSTNAME_LASTNAME_<n>_<LEAGUE>")
      2. event.results.byEventEntity[entity_id].stats
      3. event.results.players[]
      4. event.results.byPlayer / event.results.playerStats
    """
    results = ev.get("results") or {}
    out: List[Dict[str, Any]] = []
    teams = ev.get("teams") or {}
    home_team = (teams.get("home") or {}).get("names") or {}
    away_team = (teams.get("away") or {}).get("names") or {}
    home_team_name = (home_team.get("long") or home_team.get("medium")
                       if isinstance(home_team, dict) else None)
    away_team_name = (away_team.get("long") or away_team.get("medium")
                       if isinstance(away_team, dict) else None)

    def _team_for(tid: Any) -> Optional[str]:
        if not tid: return None
        if tid == (teams.get("home") or {}).get("teamID"): return home_team_name
        if tid == (teams.get("away") or {}).get("teamID"): return away_team_name
        return None

    def _opp_for(team_name: Optional[str]) -> Optional[str]:
        if team_name == home_team_name and home_team_name: return away_team_name
        if team_name == away_team_name and away_team_name: return home_team_name
        return None

    def _player_name_from_entity_id(entity_id: str) -> Optional[str]:
        """SHOHEI_OHTANI_1_MLB → 'Shohei Ohtani'. Defensive against weird IDs."""
        if not entity_id or not isinstance(entity_id, str):
            return None
        # Strip trailing _<n>_<LEAGUE> if present
        parts = entity_id.split("_")
        # Find first numeric segment from the end; treat everything before as name.
        cut = len(parts)
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].isdigit():
                cut = i
                break
        name_parts = parts[:cut]
        if not name_parts:
            return None
        return " ".join(p.capitalize() for p in name_parts)

    # Shape 1: results.game (the ACTUAL SGO production shape)
    #   results.game = {
    #     "SHOHEI_OHTANI_1_MLB": {"batting_hits": 2, ...},
    #     "home": {...team aggregates — SKIP...},
    #     "away": {...team aggregates — SKIP...},
    #     "team": {...},  "event": {...}  ← also SKIP
    #   }
    SKIP_KEYS = {"home", "away", "team", "event", "homeTeam", "awayTeam"}
    game_map = results.get("game")
    if isinstance(game_map, dict) and game_map:
        for entity_id, stats_blob in game_map.items():
            if entity_id in SKIP_KEYS:
                continue
            if not isinstance(stats_blob, dict):
                continue
            if not stats_blob:  # empty stats dict
                continue
            # In this shape, the stats blob IS the value-by-statID map itself
            # (no nested .stats key). Each k:v pair is a stat.
            out.append({
                "player_id":      entity_id,
                "stat_entity_id": entity_id,
                "player_name":    _player_name_from_entity_id(entity_id),
                "team_id":        None,
                "team":           None,
                "opponent":       None,
                "stats_raw":      stats_blob,
            })
        if out:
            return out

    # Shape 2: results.byEventEntity = {entity_id: {stats: {statID: val}, ...}}
    #                                                playerID, teamID, ...}}
    by_entity = (results.get("byEventEntity") or results.get("entities") or {})
    if isinstance(by_entity, dict) and by_entity:
        for entity_id, blob in by_entity.items():
            if not isinstance(blob, dict):
                continue
            stats_raw = blob.get("stats") or blob.get("values") or {}
            if not stats_raw:
                continue
            pid = blob.get("playerID") or blob.get("player_id")
            tid = blob.get("teamID")   or blob.get("team_id")
            # Skip team-level aggregate entities ("home"/"away") — those go
            # to team_stats, not player_stats.
            if pid is None and entity_id in ("home", "away"):
                continue
            team_name = _team_for(tid)
            out.append({
                "player_id":      pid or entity_id,
                "stat_entity_id": entity_id,
                "player_name":    blob.get("playerName") or blob.get("name"),
                "team_id":        tid,
                "team":           team_name,
                "opponent":       _opp_for(team_name),
                "stats_raw":      stats_raw,
            })
        if out:
            return out

    # Shape 3: results.players = [{playerID, name, stats: {...}, teamID}]
    players_arr = (results.get("players") or results.get("byPlayer")
                    or results.get("playerStats") or [])
    if isinstance(players_arr, list) and players_arr:
        for p in players_arr:
            if not isinstance(p, dict):
                continue
            stats_raw = p.get("stats") or p.get("values") or {}
            if not stats_raw:
                continue
            pid = p.get("playerID") or p.get("player_id") or p.get("id")
            tid = p.get("teamID")   or p.get("team_id")
            team_name = _team_for(tid)
            out.append({
                "player_id":      pid,
                "stat_entity_id": pid,
                "player_name":    p.get("playerName") or p.get("name"),
                "team_id":        tid,
                "team":           team_name,
                "opponent":       _opp_for(team_name),
                "stats_raw":      stats_raw,
            })
    return out


async def ingest_from_sgo_api(
    db: AsyncIOMotorDatabase, *, league: str,
    start: Optional[str], end: Optional[str], dry_run: bool, resume: bool,
    rpm: int = 250, only_event_ids: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Re-fetch each driver event from SGO with expandResults=true and
    extract player stats from event.results. Canonical SGO path.

    Cache-first (2026-05-21): events that already have rows in
    `sgo_player_stats` for (league_id, game_date, event_id) are SKIPPED
    unless `force=True`. This is the default — historical stats never
    re-fetch unless explicitly forced.
    """
    api_key = os.environ.get("SGO_API_KEY", "")
    if not api_key:
        return {"source": "sgo_api",
                 "error": "SGO_API_KEY not set in env",
                 "events_scanned": 0, "events_with_zero": 0,
                 "rows_emitted": 0, "coverage": {}, "sample_docs": []}

    from scripts.sgo.client import SGOClient  # lazy import

    if only_event_ids is not None:
        event_ids = list(only_event_ids)
    else:
        match: Dict[str, Any] = {"league_id": league}
        if start or end:
            gd: Dict[str, Any] = {}
            if start: gd["$gte"] = start
            if end:   gd["$lte"] = end
            match["game_date"] = gd
        event_ids = []
        async for r in db[DRIVER_COLL].aggregate(
            [{"$match": match}, {"$group": {"_id": "$event_id"}},
              {"$sort": {"_id": 1}}], allowDiskUse=True):
            if r.get("_id"): event_ids.append(r["_id"])

    events_total = len(event_ids)

    # ── Cache-first filter ───────────────────────────────────────────
    events_cached_skipped = 0
    rows_existing = 0
    if events_total and not force:
        cached_ids: set = set()
        cache_query: Dict[str, Any] = {"league_id": league,
                                            "event_id": {"$in": event_ids}}
        async for r in db[OUT_COLL].aggregate([
            {"$match": cache_query},
            {"$group": {"_id": "$event_id", "n": {"$sum": 1}}},
        ], allowDiskUse=True):
            cached_ids.add(r["_id"])
            rows_existing += int(r.get("n", 0))
        if cached_ids:
            event_ids = [e for e in event_ids if e not in cached_ids]
            events_cached_skipped = len(cached_ids)
            print(f"  [sgo_api/cache] {events_cached_skipped}/{events_total} "
                    f"events already in {OUT_COLL} ({rows_existing} rows) — "
                    f"SKIPPED (use --force to refetch)")
    elif force:
        print(f"  [sgo_api/cache] --force set, ignoring cache "
                f"({events_total} events will be re-fetched)")

    print(f"  [sgo_api] {len(event_ids)}/{events_total} events to fetch "
          f"from SGO (with expandResults=true)")
    if not event_ids:
        return {"source": "sgo_api",
                 "events_total":         events_total,
                 "events_cached_skipped": events_cached_skipped,
                 "rows_existing":        rows_existing,
                 "events_fetched":       0,
                 "events_scanned":       0,
                 "events_with_zero":     0,
                 "rows_emitted":         0,
                 "rows_written":         0,
                 "api_calls_saved":      events_cached_skipped,
                 "coverage":             {}, "sample_docs": []}

    coverage: Dict[str, int] = {}
    sample_docs: List[Dict[str, Any]] = []
    upserts: List[UpdateOne] = []
    events_scanned = 0
    events_with_zero = 0
    rows_emitted = 0
    api_failures = 0
    log_every = max(50, len(event_ids) // 20)
    next_log = log_every

    async with SGOClient(api_key=api_key, max_rpm=rpm) as cli:
        for eid in event_ids:
            events_scanned += 1
            try:
                ev = await cli.get_event_with_results(eid)
            except Exception as e:
                api_failures += 1
                if api_failures <= 5:
                    print(f"  [sgo_api] {eid} fail: {e!r}")
                continue
            if not ev:
                events_with_zero += 1
                continue
            player_rows = _extract_results_to_player_rows(ev)
            if not player_rows:
                events_with_zero += 1
                continue
            league_id = _g(ev, "leagueID", "league_id") or league
            sport_id = _g(ev, "sportID", "sport_id")
            gd_val = _g(ev.get("status") or {}, "startsAt") or \
                       _g(ev, "startTime", "commenceTime", "commence_time")
            game_date = gd_val[:10] if isinstance(gd_val, str) else None
            for pr in player_rows:
                stats = normalize_stats(pr["stats_raw"] or {}, league=league_id)
                for k, v in stats.items():
                    if v is not None:
                        coverage[k] = coverage.get(k, 0) + 1
                doc = {
                    "event_id":      eid,
                    "league_id":     league_id,
                    "sport_id":      sport_id,
                    "game_date":     game_date,
                    "player_id":     pr["player_id"],
                    "stat_entity_id": pr.get("stat_entity_id"),
                    "player_name":   pr.get("player_name"),
                    "team_id":       pr.get("team_id"),
                    "team":          pr.get("team"),
                    "opponent":      pr.get("opponent"),
                    "stats":         stats,
                    "stats_sgo_canonical": pr["stats_raw"],
                    "raw_source":    {"sgo_event_results": pr["stats_raw"]},
                    "source":        "sgo_api",
                    "ingest_version": INGEST_VERSION,
                    "ingested_at":   datetime.now(timezone.utc),
                }
                if len(sample_docs) < 2 and any(v is not None
                                                  for v in stats.values()):
                    sample_docs.append(doc)
                rows_emitted += 1
                filt = {"event_id": eid, "player_id": pr["player_id"]}
                upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
                if len(upserts) >= 1000 and not dry_run:
                    await db[OUT_COLL].bulk_write(upserts, ordered=False)
                    upserts = []
            if events_scanned >= next_log:
                print(f"  [sgo_api] processed={events_scanned:,}/"
                      f"{len(event_ids):,}  rows_total={rows_emitted:,}  "
                      f"zero={events_with_zero}  fail={api_failures}")
                next_log += log_every
        if upserts and not dry_run:
            await db[OUT_COLL].bulk_write(upserts, ordered=False)
        api_telemetry = cli.stats()
    print(f"  [sgo_api] api_calls={api_telemetry}")
    return {
        "source":           "sgo_api",
        "events_total":     events_total,
        "events_cached_skipped": events_cached_skipped,
        "rows_existing":    rows_existing,
        "events_fetched":   events_scanned,
        "events_scanned":   events_scanned,
        "events_with_zero": events_with_zero,
        "rows_emitted":     rows_emitted,
        "rows_written":     rows_emitted,
        "api_calls_saved":  events_cached_skipped,
        "api_failures":     api_failures,
        "api_telemetry":    api_telemetry,
        "coverage":         coverage,
        "sample_docs":      sample_docs,
    }


# ───────────────────────────── indexes ────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db[OUT_COLL]
    await c.create_index([("league_id", ASCENDING), ("game_date", ASCENDING)])
    await c.create_index([("league_id", ASCENDING), ("player_id", ASCENDING),
                            ("game_date", ASCENDING)],
                           name="league_player_date")
    await c.create_index([("league_id", ASCENDING), ("event_id", ASCENDING),
                            ("player_id", ASCENDING)],
                           name="league_event_player")
    # 2026-05-21 — cache-first hot lookups
    await c.create_index([("league_id", ASCENDING), ("game_date", ASCENDING),
                            ("event_id", ASCENDING)],
                           name="league_date_event")
    await c.create_index([("league_id", ASCENDING), ("game_date", ASCENDING),
                            ("event_id", ASCENDING), ("player_id", ASCENDING)],
                           name="league_date_event_player")
    await c.create_index([("player_name", ASCENDING),
                            ("game_date", ASCENDING)])
    # Upsert keys
    await c.create_index([("event_id", ASCENDING), ("player_id", ASCENDING)],
                           name="event_player")
    await c.create_index("source")


# ───────────────────────────── main ───────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"ingest_historical_player_stats (v={INGEST_VERSION})")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"source={args.source}  dry_run={args.dry_run}  "
          f"drop={args.drop_existing}")

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes")
            client.close()
            return 2
        if not args.dry_run:
            existing = await db[OUT_COLL].count_documents({})
            print(f"  [drop] {OUT_COLL} has {existing} docs — dropping")
            await db[OUT_COLL].drop()

    await ensure_out_indexes(db)

    results: List[Dict[str, Any]] = []
    gap_event_ids_mlb: Optional[List[str]] = None
    gap_event_ids_nba: Optional[List[str]] = None
    gap_dates_mlb: Optional[List[str]] = None
    gap_dates_nba: Optional[List[str]] = None

    source = args.source
    if source == "both":
        source = "auto"  # backwards-compat alias

    # ── Step 1: SGO API (canonical, primary) ─────────────────────────────
    sgo_api_extractor_mismatch = False
    if source in ("sgo_api", "auto"):
        leagues_to_run = ([args.league] if args.league
                            else ["MLB", "NBA"])
        for lg in leagues_to_run:
            r = await ingest_from_sgo_api(
                db, league=lg, start=args.start, end=args.end,
                dry_run=args.dry_run, resume=args.resume,
                rpm=args.sgo_rpm, force=getattr(args, "force", False))
            results.append(r)
            if r.get("error"):
                print(f"  [sgo_api/{lg}] SKIPPED: {r['error']}")
            else:
                scanned = r['events_scanned']
                zero    = r['events_with_zero']
                emitted = r['rows_emitted']
                fails   = r.get('api_failures', 0)
                print(f"  [sgo_api/{lg}] events_scanned={scanned:,}  "
                      f"events_with_zero={zero:,}  "
                      f"rows_emitted={emitted:,}  api_failures={fails}")
                # Hard warning: 2xx events fetched (scanned > failures) but
                # 0 rows emitted → almost certainly an extractor schema
                # mismatch. Do NOT silently fall back to legacy SGO raw or
                # external APIs since the source actually DOES have data.
                if scanned > 0 and scanned - fails > 0 and emitted == 0:
                    sgo_api_extractor_mismatch = True
                    print(f"  ╔══════════════════════════════════════════════════════════")
                    print(f"  ║ ⚠ HARD WARNING [{lg}]: SGO API returned "
                          f"{scanned - fails} successful events but the")
                    print(f"  ║   extractor emitted 0 player rows. This means the")
                    print(f"  ║   `event.results` schema doesn't match any of the")
                    print(f"  ║   probed shapes (results.game / .byEventEntity /")
                    print(f"  ║   .players[]).  Inspect one event with:")
                    print(f"  ║     python -c \"...; ev = await c.get_event_with_results(EID); print(ev['results'])\"")
                    print(f"  ║   and paste the dict structure so we can extend the extractor.")
                    print(f"  ║   FALLBACKS WILL BE SKIPPED for this league to avoid masking the bug.")
                    print(f"  ╚══════════════════════════════════════════════════════════")

    # ── Step 2: SGO re-extract from sgo_events.raw (free, secondary) ─────
    # SKIP if sgo_api had an extractor mismatch — we don't want to silently
    # mask a real schema problem with a legacy fallback.
    if source in ("sgo", "auto") and not sgo_api_extractor_mismatch:
        r = await ingest_from_sgo(
            db, league=args.league, start=args.start, end=args.end,
            dry_run=args.dry_run, resume=args.resume)
        results.append(r)
        print(f"  [sgo] events_scanned={r['events_scanned']:,}  "
              f"events_with_zero_playerStats={r['events_with_zero']:,}  "
              f"rows_emitted={r['rows_emitted']:,}")
    elif source in ("sgo", "auto") and sgo_api_extractor_mismatch:
        print(f"  [sgo] SKIPPED — sgo_api had extractor mismatch; "
              f"refusing to silently fall back.")

    # ── Gap detection for emergency fallback (auto only) ─────────────────
    if source == "auto":
        async def _gap_dates(lg: str) -> List[str]:
            match: Dict[str, Any] = {"league_id": lg}
            if args.start or args.end:
                gd: Dict[str, Any] = {}
                if args.start: gd["$gte"] = args.start
                if args.end:   gd["$lte"] = args.end
                match["game_date"] = gd
            all_d = set()
            async for d in db[DRIVER_COLL].aggregate(
                [{"$match": match}, {"$group": {"_id": "$game_date"}}],
                allowDiskUse=True):
                if d.get("_id"): all_d.add(d["_id"])
            stats_d = set()
            stats_match = {"league_id": lg}
            if "game_date" in match:
                stats_match["game_date"] = match["game_date"]
            async for d in db[OUT_COLL].aggregate(
                [{"$match": stats_match},
                  {"$group": {"_id": "$game_date"}}], allowDiskUse=True):
                if d.get("_id"): stats_d.add(d["_id"])
            return sorted(all_d - stats_d)

        if args.league in (None, "MLB"):
            gap_dates_mlb = await _gap_dates("MLB")
            if gap_dates_mlb:
                print(f"  [gap-detect/MLB] {len(gap_dates_mlb)} dates still "
                      f"missing — mlbstatsapi emergency fallback will run")
        if args.league in (None, "NBA"):
            gap_dates_nba = await _gap_dates("NBA")
            if gap_dates_nba:
                print(f"  [gap-detect/NBA] {len(gap_dates_nba)} dates still "
                      f"missing — bdl emergency fallback will run")

    # ── Emergency fallback: MLB Stats API ────────────────────────────────
    run_mlb_api = (source == "mlbstatsapi" or
                    (source == "auto" and args.league in (None, "MLB")
                     and gap_dates_mlb
                     and not sgo_api_extractor_mismatch))
    if run_mlb_api:
        r = await ingest_from_mlbstatsapi(
            db, league="MLB", start=args.start, end=args.end,
            dry_run=args.dry_run, resume=args.resume,
            rpm=args.mlb_rpm, only_dates=gap_dates_mlb)
        results.append(r)
        print(f"  [mlbstatsapi] dates={r['dates']:,}  "
              f"games_processed={r['games']:,}  "
              f"games_missing={r['games_missing']:,}  "
              f"rows_emitted={r['rows_emitted']:,}  "
              f"unmapped_names={r.get('unmapped_count', 0):,}")

    # ── Emergency fallback: BallDontLie (NBA) ────────────────────────────
    run_bdl = (source == "bdl" or
                (source == "auto" and args.league in (None, "NBA")
                 and gap_dates_nba
                 and not sgo_api_extractor_mismatch))
    if run_bdl:
        r = await ingest_from_bdl(
            db, start=args.start, end=args.end,
            dry_run=args.dry_run, rpm=args.bdl_rpm,
            only_dates=gap_dates_nba)
        results.append(r)
        if r.get("error"):
            print(f"  [bdl] SKIPPED: {r['error']}")
        else:
            print(f"  [bdl] dates={r['dates']:,}  "
                  f"games_processed={r['games']:,}  "
                  f"rows_emitted={r['rows_emitted']:,}  "
                  f"unmapped_names={r.get('unmapped_count', 0):,}")

    # Summary
    runtime = time.time() - t0
    total_rows = sum(r.get("rows_emitted", 0) for r in results)
    coverage: Dict[str, int] = {}
    for r in results:
        for k, v in (r.get("coverage") or {}).items():
            coverage[k] = coverage.get(k, 0) + v
    final_count = (await db[OUT_COLL].count_documents({})
                   if not args.dry_run else 0)
    final_distinct_players = (
        len(await db[OUT_COLL].distinct("player_id"))
        if not args.dry_run else 0)

    print()
    print("=" * 72)
    print(f"  ingest_historical_player_stats SUMMARY  ({INGEST_VERSION})")
    print("=" * 72)
    for r in results:
        if r["source"] == "sgo":
            print(f"  [sgo]")
            print(f"    events_scanned:        {r['events_scanned']:,}")
            print(f"    events_w/_zero_stats:  {r['events_with_zero']:,}")
            print(f"    rows_emitted:          {r['rows_emitted']:,}")
        elif r["source"] == "sgo_api":
            print(f"  [sgo_api]")
            if r.get("error"):
                print(f"    SKIPPED: {r['error']}")
            else:
                print(f"    events_scanned:        {r['events_scanned']:,}")
                print(f"    events_w/_zero_stats:  {r['events_with_zero']:,}")
                print(f"    rows_emitted:          {r['rows_emitted']:,}")
                print(f"    api_failures:          {r.get('api_failures', 0):,}")
                if r.get("api_telemetry"):
                    print(f"    api_telemetry:         {r['api_telemetry']}")
        elif r["source"] == "mlbstatsapi":
            print(f"  [mlbstatsapi]")
            print(f"    dates:                 {r['dates']:,}")
            print(f"    games_processed:       {r['games']:,}")
            print(f"    games_missing/404:     {r['games_missing']:,}")
            print(f"    rows_emitted:          {r['rows_emitted']:,}")
            print(f"    unmapped_player_names: {r.get('unmapped_count', 0):,}")
            if r.get("unmapped_names"):
                print(f"      (sample: {r['unmapped_names'][:5]}...)")
        elif r["source"] == "bdl":
            print(f"  [bdl]")
            if r.get("error"):
                print(f"    SKIPPED: {r['error']}")
            else:
                print(f"    dates:                 {r['dates']:,}")
                print(f"    games_processed:       {r['games']:,}")
                print(f"    rows_emitted:          {r['rows_emitted']:,}")
                print(f"    unmapped_player_names: {r.get('unmapped_count', 0):,}")
                if r.get("unmapped_names"):
                    print(f"      (sample: {r['unmapped_names'][:5]}...)")
    print(f"  total rows emitted:      {total_rows:,}")
    if not args.dry_run:
        print(f"  {OUT_COLL} doc count:    {final_count:,}")
        print(f"  distinct player_ids:     {final_distinct_players:,}")
    print(f"  runtime:                 {runtime:,.1f}s")
    if coverage:
        print(f"\n  Stat coverage (non-null counts across all rows):")
        for k, n in sorted(coverage.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<25s}  {n:,}")
    sample = next((r.get("sample_docs") for r in results
                    if r.get("sample_docs")), [])
    if sample:
        import json
        print(f"\n  Sample docs (first {len(sample)}):")
        for d in sample:
            print("    " + "─" * 60)
            print("    " + json.dumps(d, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    print(f"\n  Next: rerun the outcomes pipeline:")
    if args.league in (None, "MLB"):
        print(f"    python -m scripts.sgo.build_historical_outcomes \\")
        print(f"        --league MLB --drop-existing --yes")
    if args.league in (None, "NBA"):
        print(f"    python -m scripts.sgo.build_historical_outcomes \\")
        print(f"        --league NBA --drop-existing --yes")
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None,
                    help="Filter to one league (e.g. MLB)")
    p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end",   default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--source", default="auto",
                    choices=["sgo_api", "sgo", "mlbstatsapi", "bdl",
                              "auto", "both"],
                    help="sgo_api = primary SGO /v2/events?expandResults=true | "
                         "sgo = re-extract from sgo_events.raw (free) | "
                         "mlbstatsapi (MLB emergency fallback) | "
                         "bdl (NBA emergency fallback) | "
                         "auto = sgo_api + sgo + emergency fallbacks "
                         "(default) | both = legacy alias for auto")
    p.add_argument("--sgo-rpm", type=int, default=250,
                    help="SGO API rate limit (req/min); default 250 (under "
                         "300 rpm trial cap). Requires SGO_API_KEY env var.")
    p.add_argument("--mlb-rpm", type=int, default=30,
                    help="MLB stats API rate limit (req/min); default 30")
    p.add_argument("--bdl-rpm", type=int, default=30,
                    help="BDL API rate limit (req/min); default 30 "
                         "(BDL_API_KEY env var required)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--drop-existing", action="store_true",
                    help=f"Drop {OUT_COLL} before rebuild (requires --yes)")
    p.add_argument("--yes",    action="store_true")
    p.add_argument("--resume", action="store_true",
                    help="Skip rows already upserted (currently no-op; "
                         "upserts are idempotent regardless)")
    p.add_argument("--force", action="store_true",
                    help="Bypass the cache-first event-skip and re-fetch "
                         "every event in the window. Default behavior is "
                         "cache-first: events already in sgo_player_stats "
                         "are skipped and no API call is made.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
