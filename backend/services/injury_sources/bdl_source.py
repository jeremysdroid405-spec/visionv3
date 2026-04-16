"""
BDL Injury Source Adapter
==========================
Structured source for both NBA and MLB.
Provides: player IDs, return dates, detailed status, team info.

Trust role: STRUCTURAL AUTHORITY
  - Best for normalized fields, player ID joins, return dates
  - Slower on breaking timing vs ESPN
"""

import os
import logging
from typing import Dict, List

import httpx

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "nba": "https://api.balldontlie.io/nba/v1/player_injuries",
    "mlb": "https://api.balldontlie.io/mlb/v1/player_injuries",
}

TEAM_ENDPOINTS = {
    "nba": "https://api.balldontlie.io/nba/v1/teams",
    "mlb": "https://api.balldontlie.io/mlb/v1/teams",
}

SOURCE_ID = "bdl"

_team_cache: Dict[str, Dict[int, str]] = {}


async def _get_team_map(sport: str, api_key: str) -> Dict[int, str]:
    if sport in _team_cache and _team_cache[sport]:
        return _team_cache[sport]
    endpoint = TEAM_ENDPOINTS.get(sport)
    if not endpoint:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(endpoint, headers={"Authorization": api_key})
            resp.raise_for_status()
            teams = resp.json().get("data", [])
            _team_cache[sport] = {t["id"]: t["abbreviation"] for t in teams if "id" in t}
            return _team_cache[sport]
    except Exception as e:
        logger.warning(f"[BDL_SOURCE] Team map fetch failed for {sport}: {e}")
        return _team_cache.get(sport, {})


class BDLInjurySource:
    """Fetch raw injury records from BDL for one sport."""

    SOURCE_ID = SOURCE_ID

    def __init__(self):
        self.api_key = os.environ.get("BDL_API_KEY", "")
        self._stats = {"polls": 0, "records_fetched": 0, "errors": 0}

    async def fetch(self, sport: str) -> List[dict]:
        """
        Returns list of raw intermediate records:
        {source, sport, player_name, bdl_id, team, position, raw_status,
         return_date, injury_date, description, injury_type, injury_detail, injury_side}
        """
        endpoint = ENDPOINTS.get(sport)
        if not endpoint or not self.api_key:
            return []

        self._stats["polls"] += 1
        team_map = await _get_team_map(sport, self.api_key)
        records = []
        cursor = None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    url = f"{endpoint}?per_page=100"
                    if cursor:
                        url += f"&cursor={cursor}"
                    resp = await client.get(url, headers={"Authorization": self.api_key})
                    resp.raise_for_status()
                    data = resp.json()

                    for entry in data.get("data", []):
                        player = entry.get("player", {})
                        team_obj = player.get("team", {}) if isinstance(player.get("team"), dict) else {}
                        team_abbr = team_obj.get("abbreviation", "")
                        if not team_abbr:
                            team_abbr = team_map.get(player.get("team_id"), "")

                        rec = {
                            "source": SOURCE_ID,
                            "sport": sport,
                            "player_name": (player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}").strip(),
                            "bdl_id": player.get("id"),
                            "team": team_abbr,
                            "team_id": team_obj.get("id") or player.get("team_id"),
                            "position": player.get("position", ""),
                            "raw_status": entry.get("status", "Unknown"),
                            "return_date": entry.get("return_date"),
                            "injury_date": entry.get("date"),
                            "description": entry.get("long_comment") or entry.get("description") or "",
                            "short_comment": entry.get("short_comment") or "",
                            "injury_type": entry.get("type"),
                            "injury_detail": entry.get("detail"),
                            "injury_side": entry.get("side"),
                        }
                        records.append(rec)

                    next_cursor = data.get("meta", {}).get("next_cursor")
                    if not next_cursor:
                        break
                    cursor = next_cursor

            self._stats["records_fetched"] += len(records)
            logger.info(f"[BDL_SOURCE] {sport.upper()}: {len(records)} injuries fetched")

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"[BDL_SOURCE] {sport.upper()} fetch failed: {e}")

        return records

    def get_stats(self) -> dict:
        return {**self._stats, "source": SOURCE_ID}
