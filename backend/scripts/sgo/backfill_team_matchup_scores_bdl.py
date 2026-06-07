"""
backfill_team_matchup_scores_bdl.py — populate final scores into NFL/team
matchups using the BALLDONTLIE NFL API.

WHY THIS SCRIPT (vs. the SGO sibling and the abandoned Odds-API attempt)
    • SGO bulk-finalized-events doesn't carry usable scores —
      `scores_found` stays 0 (operator confirmed).
    • The Odds API `/scores` endpoint is hard-capped at `daysFrom ≤ 3`
      and has no deep-historical scores endpoint.
    • BDL NFL `/games` covers 2002–current with final scores
      (`status="Final"`, `home_team_score`, `visitor_team_score`).
      This is the right data source.

ENDPOINT (BDL NFL)
    GET https://api.balldontlie.io/nfl/v1/games
    Auth:        Authorization: <BDL_API_KEY>
    Pagination:  cursor (next_cursor in `meta`); per_page ≤ 100.
    Filters:     seasons[]=YYYY (start-year), weeks[]=N, dates[]=YYYY-MM-DD,
                 team_ids[]=N, postseason=true|false.
    Rate limit:  Free=5/min, ALL-STAR=60/min, GOAT=600/min.

OPERATIONAL MODEL
    • Auto-derive the NFL season-year window from the matchups'
      `game_date` range. Anchor logic: NFL "season=Y" covers
      Sep-Y → Feb-(Y+1). The script handles year-rollover.
    • Pull all `seasons[]=Y` games (paged), filter to
      `status=="Final"`, build an in-memory index keyed by
        (commence_date_iso, norm_home, norm_away)
      AND a fallback (norm_home, norm_away) for UTC-drift edges.
    • For each matchup without scores, look it up and `$set`:
      `home_score`, `away_score`, `final_score`, `score_source`,
      `score_backfilled_at`, `score_backfill_version`. Idempotent.
    • `score_source="bdl_nfl_games"`.

SAFETY
    • Idempotent. Skips already-scored rows unless `--force`.
    • Per-row updates only — never bulk-replace.
    • Dry-run unless `--yes`.
    • Never touches player model, live routing, or NCAAF.
    • Rate-limit-aware: `--rate-sleep-ms` between paginated calls.

USAGE
    # NFL dry-run, auto-derive seasons from matchup data
    python -m scripts.sgo.backfill_team_matchup_scores_bdl --sport nfl --dry-run

    # NFL live
    python -m scripts.sgo.backfill_team_matchup_scores_bdl --sport nfl --yes

    # Specific seasons
    python -m scripts.sgo.backfill_team_matchup_scores_bdl --sport nfl \\
        --seasons 2024 2025 --yes
"""
from __future__ import annotations
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

BACKFILL_VERSION = "v2"
SCORE_SOURCE_BY_SPORT = {
    "nfl": "bdl_nfl_games",
    "mlb": "bdl_mlb_games",
    "nba": "bdl_nba_games",
}


# Per-sport BDL adapter spec — encodes the shape differences between
# BDL's NFL / MLB / NBA `/games` payloads in one place. The orchestrator
# never branches on sport; it dispatches through these specs.
class _SportSpec:
    """Holds the per-sport facts the orchestrator needs."""
    def __init__(self, *,
                  url: str,
                  matchups_coll: str,
                  away_team_key: str,
                  team_name_field: str,
                  score_extractor,
                  is_final,
                  season_deriver,
                  seasons_in_url_when_empty: bool = True,
                  matchups_sport_filter,
                  fetch_postseason: bool = False):
        self.url = url
        self.matchups_coll = matchups_coll
        self.away_team_key = away_team_key
        self.team_name_field = team_name_field
        self.score_extractor = score_extractor
        self.is_final = is_final
        self.season_deriver = season_deriver
        self.matchups_sport_filter = matchups_sport_filter
        self.fetch_postseason = fetch_postseason


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nfl_score_extractor(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    return _num(g.get("home_team_score")), _num(g.get("visitor_team_score"))


def _nba_score_extractor(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    return _num(g.get("home_team_score")), _num(g.get("visitor_team_score"))


def _mlb_score_extractor(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """MLB nests runs under `home_team_data.runs` / `away_team_data.runs`."""
    h = g.get("home_team_data") or {}
    a = g.get("away_team_data") or {}
    hs = _num(h.get("runs")) if isinstance(h, dict) else None
    as_ = _num(a.get("runs")) if isinstance(a, dict) else None
    return hs, as_


def _nfl_is_final(g: Dict[str, Any]) -> bool:
    s = g.get("status")
    return isinstance(s, str) and s.startswith("Final")


def _nba_is_final(g: Dict[str, Any]) -> bool:
    s = g.get("status")
    return isinstance(s, str) and s.startswith("Final")


def _mlb_is_final(g: Dict[str, Any]) -> bool:
    s = g.get("status")
    return isinstance(s, str) and "FINAL" in s.upper()


_TEAM_NAME_NOISE = re.compile(r"[^a-z0-9]+")
_NICKNAME_OVERRIDES = {
    # Franchise renames pinned for safety.
    "washingtonfootballteam":  "washingtoncommanders",
    "oaklandraiders":          "lasvegasraiders",
    "stlouisrams":             "losangelesrams",
    "sandiegochargers":        "losangeleschargers",
    "clevelandindians":        "clevelandguardians",
    # BDL uses abbreviated city names that differ from our DB's full names.
    "laclippers":              "losangelesclippers",
    "lalakers":                "losangeleslakers",
}


def normalize_team_name(name: Optional[str]) -> str:
    """Lowercase, strip non-alphanumeric, apply franchise-rename map."""
    if not isinstance(name, str):
        return ""
    flat = _TEAM_NAME_NOISE.sub("", name.lower())
    return _NICKNAME_OVERRIDES.get(flat, flat)


def commence_date_iso(commence_time: Optional[str]) -> Optional[str]:
    """Extract 'YYYY-MM-DD' from an ISO commence_time string."""
    if not isinstance(commence_time, str):
        return None
    if len(commence_time) < 10:
        return None
    return commence_time[:10]


def derive_nfl_seasons_from_game_dates(
    game_dates: List[str],
) -> List[int]:
    """Map 'YYYY-MM-DD' strings to BDL NFL season-start-years.

    Rule: Aug–Dec → season=Y; Jan–Jul → season=Y-1 (SB / playoffs of
    the prior season). Pure function."""
    seasons: Set[int] = set()
    for gd in game_dates:
        d = commence_date_iso(gd)
        if not d:
            continue
        try:
            y, m, _ = d.split("-")
            year, month = int(y), int(m)
        except (ValueError, IndexError):
            continue
        if month >= 8:
            seasons.add(year)
        else:
            seasons.add(year - 1)
    return sorted(seasons)


def derive_calendar_year_seasons(
    game_dates: List[str],
) -> List[int]:
    """Map 'YYYY-MM-DD' strings to BDL MLB/NBA seasons (calendar year =
    season start year). MLB season runs Feb–Nov, NBA Oct–Jun.
    For NBA, games in Jan–Jun belong to season=Y-1; Oct–Dec to season=Y.
    Common idiom: just send all calendar years that appear; BDL will
    correctly return only games it has, no extra cost when seasons[]
    duplicate.

    For BDL `season` semantics:
      - MLB: `season` = calendar year of the entire season
        (e.g., 2024 season = Feb–Nov 2024)
      - NBA: `season` = start-year (2024-25 season = `season=2024`)

    To stay tolerant, return BOTH (Y) and (Y-1) for every game date —
    BDL `seasons[]` is a whitelist, not an AND filter, so this just
    means we fetch a superset that we'll filter on the way in.
    Pure function."""
    seasons: Set[int] = set()
    for gd in game_dates:
        d = commence_date_iso(gd)
        if not d:
            continue
        try:
            year = int(d.split("-")[0])
        except (ValueError, IndexError):
            continue
        seasons.add(year)
        # For NBA: a Feb 2024 game is the 2023-24 season (season=2023)
        seasons.add(year - 1)
    return sorted(seasons)


# Legacy alias kept for the original NFL-only test
def derive_seasons_from_game_dates(game_dates: List[str]) -> List[int]:
    return derive_nfl_seasons_from_game_dates(game_dates)


def extract_scores_from_bdl_game(
    g: Dict[str, Any], *,
    home_team_norm: str, away_team_norm: str,
    away_team_key: str = "visitor_team",
    team_name_field: str = "full_name",
    score_extractor=_nfl_score_extractor,
) -> Tuple[Optional[float], Optional[float]]:
    """Pull (home_score, away_score) from a BDL `/games` entry.

    Sport-aware via three parameters:
      - `away_team_key`: BDL key for the away side
        ("visitor_team" for NFL/NBA, "away_team" for MLB).
      - `team_name_field`: nested field carrying the team name
        ("full_name" for NFL/NBA, "display_name" for MLB).
      - `score_extractor`: pure function returning (home, away) numbers
        from a top-level game dict (sport-specific because MLB nests
        scores under `home_team_data.runs`).

    Pure function. The caller passes pre-normalized team keys so this
    function is trivially testable."""
    if not isinstance(g, dict):
        return None, None
    h_team = g.get("home_team") or {}
    a_team = g.get(away_team_key) or {}
    h_norm = normalize_team_name(h_team.get(team_name_field)
                                  if isinstance(h_team, dict) else None)
    a_norm = normalize_team_name(a_team.get(team_name_field)
                                  if isinstance(a_team, dict) else None)
    if h_norm != home_team_norm or a_norm != away_team_norm:
        # Could legitimately be a reversed-home/away record; try the swap.
        if h_norm == away_team_norm and a_norm == home_team_norm:
            hs_raw, as_raw = score_extractor(g)
            return as_raw, hs_raw   # swap to our perspective
        return None, None
    return score_extractor(g)


def build_event_index(
    games: List[Dict[str, Any]], *,
    away_team_key: str = "visitor_team",
    team_name_field: str = "full_name",
) -> Tuple[Dict[Tuple[str, str, str], Dict[str, Any]],
            Dict[Tuple[str, str], List[Dict[str, Any]]]]:
    """Build the (date, home, away) primary and (home, away) fallback
    indices from a list of BDL game dicts. Sport-aware via
    `away_team_key` and `team_name_field`.

    The fallback maps to a LIST of games (not a single game) because
    a team pair can meet multiple times across the seasons window.
    The orchestrator picks the closest by date — see
    `pick_closest_game`. Pure function."""
    primary: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    fallback: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for g in games:
        if not isinstance(g, dict):
            continue
        h_team = g.get("home_team") or {}
        a_team = g.get(away_team_key) or {}
        if not (isinstance(h_team, dict) and isinstance(a_team, dict)):
            continue
        h = normalize_team_name(h_team.get(team_name_field))
        a = normalize_team_name(a_team.get(team_name_field))
        d = commence_date_iso(g.get("date"))
        if not h or not a:
            continue
        if d:
            primary[(d, h, a)] = g
        fallback.setdefault((h, a), []).append(g)
    return primary, fallback


def _date_distance_days(d1: Optional[str], d2: Optional[str]) -> Optional[int]:
    """Absolute distance in days between two 'YYYY-MM-DD' strings,
    or None if either is unparseable. Pure helper."""
    if not d1 or not d2:
        return None
    try:
        from datetime import date as _date
        a = _date.fromisoformat(d1[:10])
        b = _date.fromisoformat(d2[:10])
    except ValueError:
        return None
    return abs((a - b).days)


def pick_closest_game(
    candidates: List[Dict[str, Any]], *,
    matchup_date: Optional[str], max_days: int = 2,
) -> Optional[Dict[str, Any]]:
    """From a list of BDL games for the same team pair, pick the one
    whose `date` is closest to `matchup_date`, but ONLY if it falls
    within ±`max_days` of the matchup date. Returns None otherwise.

    Pure function. Prevents the previous fallback bug where a 2024
    preseason exhibition could match a 2025 rematch on team-name alone.
    """
    if not candidates:
        return None
    best: Optional[Dict[str, Any]] = None
    best_dist: Optional[int] = None
    for g in candidates:
        d = commence_date_iso(g.get("date"))
        dist = _date_distance_days(matchup_date, d)
        if dist is None or dist > max_days:
            continue
        if best is None or dist < (best_dist or 0):
            best, best_dist = g, dist
    return best


def _is_final(g: Dict[str, Any]) -> bool:
    """Default final-status check (NFL/NBA): `status` starts with "Final".
    MLB uses "STATUS_FINAL"; per-sport behavior is overridden via the
    `SportSpec`."""
    s = g.get("status")
    if not isinstance(s, str):
        return False
    return s.startswith("Final")


def _is_score_present(row: Dict[str, Any]) -> bool:
    return (row.get("home_score") is not None
            and row.get("away_score") is not None)


# ───── Per-sport BDL spec table ─────
_SPORT_SPECS: Dict[str, _SportSpec] = {
    "nfl": _SportSpec(
        url="https://api.balldontlie.io/nfl/v1/games",
        matchups_coll="nfl_matchups",
        away_team_key="visitor_team",
        team_name_field="full_name",
        score_extractor=_nfl_score_extractor,
        is_final=_nfl_is_final,
        season_deriver=derive_nfl_seasons_from_game_dates,
        matchups_sport_filter={"$or": [{"sport": "nfl"}, {"league": "NFL"}]},
    ),
    "nba": _SportSpec(
        url="https://api.balldontlie.io/nba/v1/games",
        matchups_coll="team_matchups",
        away_team_key="visitor_team",
        team_name_field="full_name",
        score_extractor=_nba_score_extractor,
        is_final=_nba_is_final,
        season_deriver=derive_calendar_year_seasons,
        matchups_sport_filter={"sport": "nba"},
        fetch_postseason=True,
    ),
    "mlb": _SportSpec(
        url="https://api.balldontlie.io/mlb/v1/games",
        matchups_coll="team_matchups",
        away_team_key="away_team",
        team_name_field="display_name",
        score_extractor=_mlb_score_extractor,
        is_final=_mlb_is_final,
        season_deriver=derive_calendar_year_seasons,
        matchups_sport_filter={"sport": "mlb"},
    ),
}

# Public alias for back-compat with tests that still import SPORT_CONFIG
SPORT_CONFIG: Dict[str, str] = {s: spec.matchups_coll
                                  for s, spec in _SPORT_SPECS.items()}


# ───── HTTP fetch (mockable in tests) ─────
async def fetch_bdl_games(
    api_key: str, *, url: str, seasons: List[int],
    per_page: int = 100, rate_sleep_ms: int = 250,
    timeout_s: float = 30.0, max_429_retries: int = 5,
    postseason: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Page through a BDL `/games` endpoint for the given seasons.

    `url` is the full BDL endpoint
    (e.g. "https://api.balldontlie.io/nfl/v1/games"). Returns a flat
    list of game dicts. Raises RuntimeError on non-2xx (except 429,
    which is retried with exponential backoff up to `max_429_retries`).

    When `postseason` is not None, adds `postseason=true/false` to the
    request params so BDL filters to only that phase.

    Kept thin so tests can monkeypatch this single function via the
    `fetcher=` kwarg on `backfill_sport`."""
    import aiohttp
    if not seasons:
        return []
    if per_page < 1 or per_page > 100:
        raise ValueError("per_page must be in [1, 100] (BDL contract)")
    params: List[Tuple[str, Any]] = [("per_page", per_page)]
    for s in seasons:
        params.append(("seasons[]", s))
    if postseason is not None:
        params.append(("postseason", "true" if postseason else "false"))
    headers = {"Authorization": api_key}
    out: List[Dict[str, Any]] = []
    page = 0
    next_cursor: Optional[int] = None
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    ) as sess:
        while True:
            page += 1
            req_params = list(params)
            if next_cursor is not None:
                req_params.append(("cursor", next_cursor))
            attempt = 0
            while True:
                async with sess.get(url, params=req_params,
                                      headers=headers) as resp:
                    body = await resp.text()
                    if resp.status == 429 and attempt < max_429_retries:
                        delay = min(60, 15 * (2 ** attempt))
                        print(f"    [bdl] 429 rate-limited on page {page} "
                              f"(attempt {attempt + 1}); sleeping {delay}s…")
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"BDL {url} returned HTTP {resp.status}: "
                            f"{body[:300]}")
                    import json as _json
                    payload = _json.loads(body)
                    break
            data = payload.get("data") or []
            meta = payload.get("meta") or {}
            out.extend([g for g in data if isinstance(g, dict)])
            nxt = meta.get("next_cursor")
            print(f"    [bdl] page {page}: rows={len(data)}  "
                  f"next_cursor={nxt}  total_so_far={len(out):,}  "
                  f"seasons={seasons}")
            if nxt is None or not data:
                break
            next_cursor = nxt
            if rate_sleep_ms > 0:
                await asyncio.sleep(rate_sleep_ms / 1000.0)
    return out


# Back-compat alias for the NFL-only tests
async def fetch_bdl_nfl_games(
    api_key: str, *, seasons: List[int],
    per_page: int = 100, rate_sleep_ms: int = 250,
    timeout_s: float = 30.0, max_429_retries: int = 5,
) -> List[Dict[str, Any]]:
    return await fetch_bdl_games(
        api_key, url=_SPORT_SPECS["nfl"].url, seasons=seasons,
        per_page=per_page, rate_sleep_ms=rate_sleep_ms,
        timeout_s=timeout_s, max_429_retries=max_429_retries)


# ───── core ─────
async def _derive_matchup_date_range(
    db: AsyncIOMotorDatabase, coll_name: str, sport_filter: Dict[str, Any],
    start: Optional[str], end: Optional[str],
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Returns (effective_start, effective_end, distinct_game_dates)."""
    base: Dict[str, Any] = {"status": "completed", **sport_filter}
    if start or end:
        gd: Dict[str, Any] = {}
        if start:
            gd["$gte"] = start
        if end:
            gd["$lte"] = end
        base["game_date"] = gd
    dates = await db[coll_name].distinct("game_date", base)
    dates = sorted(d for d in dates if isinstance(d, str))
    eff_start = start or (dates[0] if dates else None)
    eff_end = end or (dates[-1] if dates else None)
    return eff_start, eff_end, dates


async def backfill_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, api_key: str,
    start: Optional[str], end: Optional[str],
    seasons: Optional[List[int]],
    dry_run: bool, force: bool, max_events: int,
    rate_sleep_ms: int = 250,
    fetcher: Any = None,
    postseason: Optional[bool] = None,
) -> Dict[str, Any]:
    """Pull BDL games for the inferred (or user-supplied) seasons, then
    update matchups in-place. Dispatches all sport-specific shape
    differences through `_SPORT_SPECS[sport]`.

    For sports with `spec.fetch_postseason=True` (NBA), when `postseason`
    is None the fetch runs twice — once for regular season
    (postseason=false) and once for playoffs (postseason=true) — and the
    results are combined before indexing. Pass `postseason=True` or
    `postseason=False` to force a single phase.

    `fetcher` mirrors `fetch_bdl_games`'s signature, with the FIRST two
    positional args being `(api_key, url)`. Tests pass a stub that
    ignores `url`."""
    spec = _SPORT_SPECS[sport]
    coll_name = spec.matchups_coll
    score_source = SCORE_SOURCE_BY_SPORT[sport]
    fetcher = fetcher or fetch_bdl_games
    print(f"\n  [{sport.upper()}] backfilling final scores into {coll_name}")
    print(f"  [{sport.upper()}] source=BDL {spec.url}  start={start}  end={end}  "
          f"dry_run={dry_run}  force={force}")

    eff_start, eff_end, gdates = await _derive_matchup_date_range(
        db, coll_name, spec.matchups_sport_filter, start, end)
    if (eff_start, eff_end) != (start, end):
        print(f"  [{sport.upper()}] auto-derived game_date window: "
              f"{eff_start} → {eff_end}  ({len(gdates)} distinct dates)")

    effective_seasons = seasons or spec.season_deriver(gdates)
    if not effective_seasons:
        print(f"  [{sport.upper()}] no seasons resolved; aborting.")
        return {"sport": sport, "coll": coll_name,
                "counters": {"scanned": 0, "dry_run": dry_run},
                "sample_updates": []}
    print(f"  [{sport.upper()}] BDL seasons to pull: {effective_seasons}")

    if spec.fetch_postseason and postseason is None:
        print(f"  [{sport.upper()}] fetching regular season games…")
        reg_games = await fetcher(api_key, url=spec.url,
                                   seasons=effective_seasons,
                                   rate_sleep_ms=rate_sleep_ms,
                                   postseason=False)
        print(f"  [{sport.upper()}] fetching postseason games…")
        ps_games = await fetcher(api_key, url=spec.url,
                                  seasons=effective_seasons,
                                  rate_sleep_ms=rate_sleep_ms,
                                  postseason=True)
        games = reg_games + ps_games
    else:
        games = await fetcher(api_key, url=spec.url, seasons=effective_seasons,
                               rate_sleep_ms=rate_sleep_ms,
                               postseason=postseason)
    finals = [g for g in games if spec.is_final(g)]
    print(f"  [{sport.upper()}] received {len(games):,} game(s) from BDL; "
          f"{len(finals):,} are final.")
    primary_idx, fallback_idx = build_event_index(
        finals,
        away_team_key=spec.away_team_key,
        team_name_field=spec.team_name_field)
    print(f"  [{sport.upper()}] index sizes: "
          f"primary={len(primary_idx):,}  fallback={len(fallback_idx):,}")

    match: Dict[str, Any] = {"status": "completed", **spec.matchups_sport_filter}
    if eff_start or eff_end:
        gd: Dict[str, Any] = {}
        if eff_start:
            gd["$gte"] = eff_start
        if eff_end:
            gd["$lte"] = eff_end
        match["game_date"] = gd

    n_total = await db[coll_name].count_documents(match)
    print(f"  [{sport.upper()}] candidate completed matchups: {n_total:,}")

    counters = {
        "scanned":             0,
        "already_scored_skip": 0,
        "no_team_names":       0,
        "matched_primary":     0,
        "matched_fallback":    0,
        "not_in_bdl":          0,
        "missing_score_fields": 0,
        "scores_found":        0,
        "updated":             0,
        "dry_run":             dry_run,
    }
    sample_updates: List[Dict[str, Any]] = []

    cursor = db[coll_name].find(match, {
        "_id": 0, "event_id": 1, "home_team_name": 1, "away_team_name": 1,
        "commence_time": 1, "game_date": 1,
        "home_score": 1, "away_score": 1,
    }).batch_size(200)

    async for m in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_events:
            print(f"  [{sport.upper()}] hit --max-events={max_events}; "
                  f"stopping early.")
            break
        if not force and _is_score_present(m):
            counters["already_scored_skip"] += 1
            continue
        h_norm = normalize_team_name(m.get("home_team_name"))
        a_norm = normalize_team_name(m.get("away_team_name"))
        if not h_norm or not a_norm:
            counters["no_team_names"] += 1
            continue
        date_iso = (m.get("game_date") or
                      commence_date_iso(m.get("commence_time")))
        hit = None
        used_fallback = False
        if date_iso:
            hit = primary_idx.get((date_iso, h_norm, a_norm))
        if hit is None:
            candidates = fallback_idx.get((h_norm, a_norm)) or []
            hit = pick_closest_game(candidates, matchup_date=date_iso,
                                      max_days=2)
            used_fallback = hit is not None
        if hit is None:
            counters["not_in_bdl"] += 1
            continue
        hs, as_ = extract_scores_from_bdl_game(
            hit, home_team_norm=h_norm, away_team_norm=a_norm,
            away_team_key=spec.away_team_key,
            team_name_field=spec.team_name_field,
            score_extractor=spec.score_extractor)
        if hs is None or as_ is None:
            counters["missing_score_fields"] += 1
            continue
        counters["matched_fallback" if used_fallback else "matched_primary"] += 1
        counters["scores_found"] += 1
        if len(sample_updates) < 5:
            sample_updates.append({
                "event_id":   m.get("event_id"),
                "home_team":  m.get("home_team_name"),
                "away_team":  m.get("away_team_name"),
                "game_date":  m.get("game_date"),
                "home_score": hs, "away_score": as_,
                "via_fallback": used_fallback,
                "bdl_game_id": hit.get("id"),
                "bdl_week":    hit.get("week"),
                "bdl_season":  hit.get("season"),
            })
        if dry_run:
            continue
        update_doc = {
            "$set": {
                "home_score":             hs,
                "away_score":             as_,
                "final_score":            {"home": hs, "away": as_},
                "score_source":           score_source,
                "score_backfilled_at":    datetime.now(timezone.utc),
                "score_backfill_version": BACKFILL_VERSION,
                "bdl_game_id":            hit.get("id"),
            },
        }
        r = await db[coll_name].update_one(
            {"event_id": m.get("event_id")}, update_doc)
        counters["updated"] += (r.modified_count or 0)
        if counters["scanned"] % 200 == 0:
            print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                  f"updated={counters['updated']:,}  "
                  f"already={counters['already_scored_skip']:,}  "
                  f"not_in_bdl={counters['not_in_bdl']:,}")

    return {"sport": sport, "coll": coll_name, "counters": counters,
            "sample_updates": sample_updates}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} BDL BACKFILL SUMMARY "
          f"({r['coll']}) ──")
    print(f"     scanned:                  {c['scanned']:,}")
    print(f"     already scored (skip):    {c.get('already_scored_skip', 0):,}")
    print(f"     no team names on matchup: {c.get('no_team_names', 0):,}")
    print(f"     matched (primary date+nm):{c.get('matched_primary', 0):,}")
    print(f"     matched (fallback nm):    {c.get('matched_fallback', 0):,}")
    print(f"     not in BDL:               {c.get('not_in_bdl', 0):,}")
    print(f"     missing score fields:     {c.get('missing_score_fields', 0):,}")
    print(f"     scores found:             {c.get('scores_found', 0):,}")
    print(f"     rows updated:             {c.get('updated', 0):,}  "
          f"({'DRY-RUN' if c.get('dry_run') else 'live'})")
    if r["sample_updates"]:
        print("     sample updates (first 5):")
        for s in r["sample_updates"]:
            tag = " (fallback)" if s.get("via_fallback") else ""
            print(f"        eid={s['event_id']}  "
                  f"{s.get('away_team') or '?'} @ {s.get('home_team') or '?'}  "
                  f"{s.get('game_date') or '?'}  "
                  f"wk{s.get('bdl_week')}/{s.get('bdl_season')}  "
                  f"→ {s['away_score']}–{s['home_score']}{tag}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    api_key = os.environ.get("BDL_API_KEY")
    if not api_key:
        print("  ERROR: BDL_API_KEY missing from environment.")
        return 2

    sports = [args.sport] if args.sport != "all" else list(SPORT_CONFIG)
    for s in sports:
        if s not in SPORT_CONFIG:
            print(f"  ERROR: unsupported --sport {s!r} "
                  f"(supported: {sorted(SPORT_CONFIG)})")
            return 2

    dry_run = not args.yes
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"backfill_team_matchup_scores_bdl  version={BACKFILL_VERSION}")
    print(f"  sports={sports}  yes={args.yes}  dry_run={dry_run}  "
          f"force={args.force}  seasons={args.seasons}  "
          f"postseason={args.postseason}  "
          f"max_events_per_sport={args.max_events}")
    print("  CONTRACT: in-place $set updates to matchup docs; preserves "
          "all other fields; idempotent. score_source = "
          "'bdl_<sport>_games'.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        all_results: List[Dict[str, Any]] = []
        for sp in sports:
            r = await backfill_sport(
                db, sport=sp, api_key=api_key,
                start=args.start, end=args.end,
                seasons=args.seasons,
                dry_run=dry_run, force=args.force,
                max_events=args.max_events,
                rate_sleep_ms=args.rate_sleep_ms,
                postseason=args.postseason,
            )
            _print_summary(r)
            all_results.append(r)

        print()
        print("=" * 72)
        print("  GRAND TOTALS")
        print("=" * 72)
        tot = {"scanned": 0, "updated": 0, "scores_found": 0,
                "not_in_bdl": 0, "already_scored_skip": 0,
                "matched_primary": 0, "matched_fallback": 0}
        for r in all_results:
            for k in tot:
                tot[k] += r["counters"].get(k, 0)
        for k, v in tot.items():
            print(f"  {k:<22s} {v:,}")
        if dry_run:
            print("\n  DRY-RUN — no writes. Pass --yes to apply.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=["nfl", "mlb", "nba", "all"],
                    default="nfl",
                    help="Sport to backfill. NFL → nfl_matchups; "
                         "MLB/NBA → team_matchups (sport-filtered).")
    p.add_argument("--start", default=None,
                    help="Optional inclusive start game_date 'YYYY-MM-DD'.")
    p.add_argument("--end", default=None,
                    help="Optional inclusive end game_date 'YYYY-MM-DD'.")
    p.add_argument("--seasons", type=int, nargs="+", default=None,
                    help="Explicit BDL seasons[] to pull. NFL: start-year "
                         "(e.g. 2024 = 2024-25 season). MLB: calendar year. "
                         "NBA: start-year (2024 = 2024-25 season). "
                         "If omitted, derived from matchup game_date.")
    p.add_argument("--yes", action="store_true",
                    help="Actually write to Mongo (default is dry-run).")
    p.add_argument("--dry-run", action="store_true",
                    help="Explicit dry-run flag (default behaviour).")
    p.add_argument("--force", action="store_true",
                    help="Overwrite even when home/away_score already set.")
    p.add_argument("--rate-sleep-ms", type=int, default=250,
                    help="Sleep between BDL page calls (ms). Default 250 "
                         "(safe for ALL-STAR tier @ 60 req/min).")
    p.add_argument("--max-events", type=int, default=10_000,
                    help="Safety cap on matchups processed per sport.")
    ps_group = p.add_mutually_exclusive_group()
    ps_group.add_argument(
        "--postseason", dest="postseason", action="store_true", default=None,
        help="Fetch only postseason (playoff) games from BDL.")
    ps_group.add_argument(
        "--no-postseason", dest="postseason", action="store_false",
        help="Fetch only regular-season games from BDL.")
    p.set_defaults(postseason=None)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
