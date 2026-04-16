"""
ESPN Injury Source Adapter
===========================
Timing canary for NBA injuries.
ESPN updates faster on breaking news/late scratches than BDL.

Trust role: TIMING AUTHORITY (NBA only)
  - Detects status changes first
  - Less structured than BDL (no player IDs, no return dates)
  - Used to trigger early "suspected change" signals
  - NOT used as structural source

MLB: Not used (ESPN NBA injuries endpoint only covers basketball).
"""

import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)

NBA_INJURIES_URL = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
SOURCE_ID = "espn"

# ESPN team name → abbreviation mapping (subset of common teams)
_TEAM_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


class ESPNInjurySource:
    """
    Fetch raw injury status from ESPN for NBA.
    Returns lightweight records — no player IDs, no return dates.
    Used as timing canary, not structural authority.
    """

    SOURCE_ID = SOURCE_ID

    def __init__(self):
        self._stats = {"polls": 0, "records_fetched": 0, "errors": 0}

    async def fetch(self, sport: str) -> List[dict]:
        """Only supports NBA. Returns empty for other sports."""
        if sport != "nba":
            return []

        self._stats["polls"] += 1

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(NBA_INJURIES_URL)
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") != "success":
                return []

            records = []
            for team_data in data.get("injuries", []):
                team_name = team_data.get("displayName", "")
                team_abbr = _TEAM_ABBR.get(team_name, "")

                for inj in team_data.get("injuries", []):
                    athlete = inj.get("athlete", {})
                    records.append({
                        "source": SOURCE_ID,
                        "sport": "nba",
                        "player_name": athlete.get("displayName", ""),
                        "bdl_id": None,  # ESPN has no BDL ID
                        "team": team_abbr,
                        "team_id": None,
                        "position": athlete.get("position", {}).get("abbreviation", ""),
                        "raw_status": inj.get("status", "Unknown"),
                        "return_date": None,  # ESPN doesn't provide
                        "injury_date": None,
                        "description": inj.get("shortComment", ""),
                        "short_comment": inj.get("shortComment", ""),
                        "injury_type": None,
                        "injury_detail": None,
                        "injury_side": None,
                    })

            self._stats["records_fetched"] += len(records)
            logger.info(f"[ESPN_SOURCE] NBA: {len(records)} injuries fetched")
            return records

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"[ESPN_SOURCE] NBA fetch failed: {e}")
            return []

    def get_stats(self) -> dict:
        return {**self._stats, "source": SOURCE_ID}
