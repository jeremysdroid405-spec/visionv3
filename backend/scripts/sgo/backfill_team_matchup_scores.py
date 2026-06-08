"""
backfill_team_matchup_scores.py — Backfill null home_score/away_score values
in team_matchups using BDL game data fetched by date.

Fetches BDL games per game_date, matches to our records via the canonical
team_id → BDL team name mapping, and writes scores only for final games.

USAGE
    python -m scripts.sgo.backfill_team_matchup_scores --sport mlb \\
        --start 2025-04-01 --end 2025-06-01
    python -m scripts.sgo.backfill_team_matchup_scores --sport nba \\
        --start 2025-01-01 --end 2025-04-15
"""
from __future__ import annotations
import argparse
import asyncio
import os
import re
import sys
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
from motor.motor_asyncio import AsyncIOMotorClient

# ─── Team ID → BDL name maps ───────────────────────────────────────────────────

_MLB_TEAM_IDS: Dict[str, str] = {
    "mlb_ari": "arizona diamondbacks",
    "mlb_atl": "atlanta braves",
    "mlb_bal": "baltimore orioles",
    "mlb_bos": "boston red sox",
    "mlb_chc": "chicago cubs",
    "mlb_chw": "chicago white sox",
    "mlb_cin": "cincinnati reds",
    "mlb_cle": "cleveland guardians",
    "mlb_col": "colorado rockies",
    "mlb_det": "detroit tigers",
    "mlb_hou": "houston astros",
    "mlb_kcr": "kansas city royals",
    "mlb_laa": "los angeles angels",
    "mlb_lad": "los angeles dodgers",
    "mlb_mia": "miami marlins",
    "mlb_mil": "milwaukee brewers",
    "mlb_min": "minnesota twins",
    "mlb_nym": "new york mets",
    "mlb_nyy": "new york yankees",
    "mlb_oak": "oakland athletics",
    "mlb_phi": "philadelphia phillies",
    "mlb_pit": "pittsburgh pirates",
    "mlb_sdp": "san diego padres",
    "mlb_sea": "seattle mariners",
    "mlb_sfg": "san francisco giants",
    "mlb_stl": "st. louis cardinals",
    "mlb_tbr": "tampa bay rays",
    "mlb_tex": "texas rangers",
    "mlb_tor": "toronto blue jays",
    "mlb_wsn": "washington nationals",
}

_NBA_TEAM_IDS: Dict[str, str] = {
    "nba_atl": "Atlanta Hawks",
    "nba_bkn": "Brooklyn Nets",
    "nba_bos": "Boston Celtics",
    "nba_cha": "Charlotte Hornets",
    "nba_chi": "Chicago Bulls",
    "nba_cle": "Cleveland Cavaliers",
    "nba_dal": "Dallas Mavericks",
    "nba_den": "Denver Nuggets",
    "nba_det": "Detroit Pistons",
    "nba_gsw": "Golden State Warriors",
    "nba_hou": "Houston Rockets",
    "nba_ind": "Indiana Pacers",
    "nba_lac": "LA Clippers",
    "nba_lal": "Los Angeles Lakers",
    "nba_mem": "Memphis Grizzlies",
    "nba_mia": "Miami Heat",
    "nba_mil": "Milwaukee Bucks",
    "nba_min": "Minnesota Timberwolves",
    "nba_nop": "New Orleans Pelicans",
    "nba_nyk": "New York Knicks",
    "nba_okc": "Oklahoma City Thunder",
    "nba_orl": "Orlando Magic",
    "nba_phi": "Philadelphia 76ers",
    "nba_phx": "Phoenix Suns",
    "nba_por": "Portland Trail Blazers",
    "nba_sac": "Sacramento Kings",
    "nba_sas": "San Antonio Spurs",
    "nba_tor": "Toronto Raptors",
    "nba_uta": "Utah Jazz",
    "nba_was": "Washington Wizards",
}

TEAM_IDS_BY_SPORT: Dict[str, Dict[str, str]] = {
    "mlb": _MLB_TEAM_IDS,
    "nba": _NBA_TEAM_IDS,
}

# ─── Normalization ─────────────────────────────────────────────────────────────

_NOISE = re.compile(r"[^a-z0-9]+")


def _norm(s: Optional[str]) -> str:
    if not isinstance(s, str):
        return ""
    return _NOISE.sub("", s.lower())


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── Per-sport BDL shape ───────────────────────────────────────────────────────

def _mlb_is_final(g: Dict[str, Any]) -> bool:
    return "FINAL" in str(g.get("status", "")).upper()


def _nba_is_final(g: Dict[str, Any]) -> bool:
    return str(g.get("status", "")).startswith("Final")


def _mlb_scores(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    h = g.get("home_team_data") or {}
    a = g.get("away_team_data") or {}
    return (
        _num(h.get("runs") if isinstance(h, dict) else None),
        _num(a.get("runs") if isinstance(a, dict) else None),
    )


def _nba_scores(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    return _num(g.get("home_team_score")), _num(g.get("visitor_team_score"))


_SPORT_SPECS: Dict[str, Dict[str, Any]] = {
    "mlb": {
        "url": "https://api.balldontlie.io/mlb/v1/games",
        "extra_params": {"season_type": "regular"},
        "home_team_key": "home_team",
        "away_team_key": "away_team",
        "team_name_field": "display_name",
        "is_final": _mlb_is_final,
        "score_extractor": _mlb_scores,
    },
    "nba": {
        "url": "https://api.balldontlie.io/v1/games",
        "extra_params": {},
        "home_team_key": "home_team",
        "away_team_key": "visitor_team",
        "team_name_field": "full_name",
        "is_final": _nba_is_final,
        "score_extractor": _nba_scores,
    },
}

# ─── BDL HTTP fetch ────────────────────────────────────────────────────────────

async def fetch_bdl_games_for_date(
    client: httpx.AsyncClient,
    api_key: str,
    sport: str,
    date: str,
) -> List[Dict[str, Any]]:
    """Fetch all BDL games for a single date, handling cursor pagination."""
    spec = _SPORT_SPECS[sport]
    url = spec["url"]
    base_params: Dict[str, Any] = {
        "dates[]": date,
        "per_page": 100,
        **spec["extra_params"],
    }
    headers = {"Authorization": api_key}
    games: List[Dict[str, Any]] = []
    next_cursor: Optional[int] = None

    while True:
        params = dict(base_params)
        if next_cursor is not None:
            params["cursor"] = next_cursor
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or []
        meta = payload.get("meta") or {}
        games.extend(g for g in data if isinstance(g, dict))
        next_cursor = meta.get("next_cursor")
        if not next_cursor or not data:
            break

    return games


# ─── Date-level game index ─────────────────────────────────────────────────────

def build_game_index(
    games: List[Dict[str, Any]],
    sport: str,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Return (norm_home, norm_away) → game dict for final games only."""
    spec = _SPORT_SPECS[sport]
    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for g in games:
        if not spec["is_final"](g):
            continue
        h_team = g.get(spec["home_team_key"]) or {}
        a_team = g.get(spec["away_team_key"]) or {}
        h_norm = _norm(
            h_team.get(spec["team_name_field"]) if isinstance(h_team, dict) else None
        )
        a_norm = _norm(
            a_team.get(spec["team_name_field"]) if isinstance(a_team, dict) else None
        )
        if h_norm and a_norm:
            idx[(h_norm, a_norm)] = g
    return idx


# ─── Core backfill ─────────────────────────────────────────────────────────────

async def backfill(
    db,
    api_key: str,
    sport: str,
    start: str,
    end: str,
) -> None:
    spec = _SPORT_SPECS[sport]
    team_id_map = TEAM_IDS_BY_SPORT[sport]

    docs: List[Dict[str, Any]] = await db["team_matchups"].find(
        {
            "sport": sport,
            "game_date": {"$gte": start, "$lte": end},
            "home_score": None,
        },
        {"_id": 1, "home_team_id": 1, "away_team_id": 1, "game_date": 1},
    ).to_list(length=None)

    print(
        f"[{sport.upper()}] found {len(docs):,} null-score matchups "
        f"({start} → {end})"
    )

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for doc in docs:
        gd = doc.get("game_date")
        if gd:
            by_date.setdefault(gd, []).append(doc)

    total_bdl_found = 0
    total_matched = 0
    total_updated = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for date in sorted(by_date):
            matchups_on_date = by_date[date]

            try:
                games = await fetch_bdl_games_for_date(client, api_key, sport, date)
            except httpx.HTTPStatusError as exc:
                print(
                    f"  [{date}] BDL error {exc.response.status_code}; skipping"
                )
                await asyncio.sleep(0.3)
                continue

            finals = [g for g in games if spec["is_final"](g)]
            game_idx = build_game_index(finals, sport)
            total_bdl_found += len(finals)

            matched = 0
            updated = 0
            for doc in matchups_on_date:
                h_id = doc.get("home_team_id", "")
                a_id = doc.get("away_team_id", "")
                h_norm = _norm(team_id_map.get(h_id, ""))
                a_norm = _norm(team_id_map.get(a_id, ""))
                if not h_norm or not a_norm:
                    continue
                hit = game_idx.get((h_norm, a_norm))
                flipped = False
                if hit is None:
                    hit = game_idx.get((a_norm, h_norm))
                    flipped = True
                if hit is None:
                    continue
                matched += 1
                hs, as_ = spec["score_extractor"](hit)
                if flipped:
                    hs, as_ = as_, hs
                if hs is None or as_ is None:
                    continue
                result = await db["team_matchups"].update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "home_score":     hs,
                        "away_score":     as_,
                        "updated_at":     datetime.now(timezone.utc),
                        "bdl_backfilled": True,
                    }},
                )
                updated += result.modified_count

            total_matched += matched
            total_updated += updated
            print(
                f"  {date}  bdl_games={len(finals)}  "
                f"matched={matched}  updated={updated}"
            )
            await asyncio.sleep(0.3)

    print(
        f"\n[{sport.upper()}] DONE  total_bdl_finals={total_bdl_found}  "
        f"matched={total_matched}  updated={total_updated}"
    )


# ─── Entry point ───────────────────────────────────────────────────────────────

async def amain(args: argparse.Namespace) -> int:
    api_key = os.environ.get("BDL_API_KEY")
    if not api_key:
        print("ERROR: BDL_API_KEY not set in environment.")
        return 2

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        await backfill(db, api_key, args.sport, args.start, args.end)
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", required=True, choices=["mlb", "nba"],
                   help="Sport to backfill.")
    p.add_argument("--start", required=True,
                   help="Inclusive start game_date YYYY-MM-DD.")
    p.add_argument("--end", required=True,
                   help="Inclusive end game_date YYYY-MM-DD.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
