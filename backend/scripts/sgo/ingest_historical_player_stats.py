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
    if not d: return None
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
    hits      = _num(_g(raw, "hits", "H"))
    singles   = _num(_g(raw, "singles", "1B"))
    doubles   = _num(_g(raw, "doubles", "2B"))
    triples   = _num(_g(raw, "triples", "3B"))
    hr        = _num(_g(raw, "homeRuns", "home_runs", "HR"))
    tb        = _num(_g(raw, "totalBases", "total_bases", "TB"))
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
        "runs":                  _num(_g(raw, "runs", "R")),
        "rbi":                   _num(_g(raw, "rbi", "RBI")),
        "walks":                 _num(_g(raw, "walks", "baseOnBalls",
                                          "base_on_balls", "BB")),
        "strikeouts":            _num(_g(raw, "strikeouts", "K",
                                          "batting_strikeouts", "SO")),
        "stolen_bases":          _num(_g(raw, "stolenBases", "stolen_bases",
                                          "SB")),
        "pitcher_strikeouts":    _num(_g(raw, "pitcher_strikeouts",
                                          "pitching_strikeouts",
                                          "strikeOutsPitched",
                                          "strikeoutsPitched")),
        "pitching_outs":         _num(_g(raw, "pitching_outs", "outs",
                                          "outsPitched")),
        "pitching_hits_allowed": _num(_g(raw, "hitsAllowed", "hits_allowed",
                                          "pitching_hits_allowed")),
        "pitching_earned_runs":  _num(_g(raw, "earnedRuns", "earned_runs",
                                          "ER", "pitching_earned_runs")),
        "pitching_walks":        _num(_g(raw, "pitcher_walks", "walksAllowed",
                                          "walks_allowed", "pitching_walks")),
        "pitches_thrown":        _num(_g(raw, "pitchesThrown", "pitches_thrown",
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
    gap_dates_mlb: Optional[List[str]] = None
    gap_dates_nba: Optional[List[str]] = None

    source = args.source
    if source == "both":
        source = "auto"  # backwards-compat alias

    if source in ("sgo", "auto"):
        r = await ingest_from_sgo(
            db, league=args.league, start=args.start, end=args.end,
            dry_run=args.dry_run, resume=args.resume)
        results.append(r)
        print(f"  [sgo] events_scanned={r['events_scanned']:,}  "
              f"events_with_zero_playerStats={r['events_with_zero']:,}  "
              f"rows_emitted={r['rows_emitted']:,}")

        # For auto: identify dates that still produced no rows per league
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
                async for d in db[OUT_COLL].aggregate(
                    [{"$match": {"league_id": lg,
                                  **({"game_date": match.get("game_date")}
                                     if "game_date" in match else {})}},
                      {"$group": {"_id": "$game_date"}}], allowDiskUse=True):
                    if d.get("_id"): stats_d.add(d["_id"])
                return sorted(all_d - stats_d)

            if args.league in (None, "MLB"):
                gap_dates_mlb = await _gap_dates("MLB")
                if gap_dates_mlb:
                    print(f"  [gap-detect/MLB] {len(gap_dates_mlb)} dates "
                          f"need mlbstatsapi backfill")
            if args.league in (None, "NBA"):
                gap_dates_nba = await _gap_dates("NBA")
                if gap_dates_nba:
                    print(f"  [gap-detect/NBA] {len(gap_dates_nba)} dates "
                          f"need bdl backfill")

    run_mlb_api = (source == "mlbstatsapi" or
                    (source == "auto" and args.league in (None, "MLB")
                     and (gap_dates_mlb is None or len(gap_dates_mlb) > 0)))
    run_bdl     = (source == "bdl" or
                    (source == "auto" and args.league in (None, "NBA")
                     and (gap_dates_nba is None or len(gap_dates_nba) > 0)))

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
                    choices=["sgo", "mlbstatsapi", "bdl", "auto", "both"],
                    help="sgo | mlbstatsapi (MLB) | bdl (NBA) | "
                         "auto = sgo + league-appropriate fallback (default) | "
                         "both = legacy alias for auto")
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
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
