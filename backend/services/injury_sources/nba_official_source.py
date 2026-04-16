"""
NBA Official Injury Report Source Adapter
==========================================
Timing authority for NBA injuries.

The NBA publishes timestamped PDF injury reports every ~15 minutes at:
  https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM.pdf

Trust role: TIMING AUTHORITY
  - Detects status changes from official league submissions
  - Has structured fields: player, team, status, reason, game matchup
  - Published every 15 min on game days (teams submit closer to tip)
  - Faster than BDL for official status changes (Out → Probable, etc.)

Limitations:
  - No player IDs (name-based matching)
  - No return dates
  - PDF parsing required
  - "NOT YET SUBMITTED" before teams file

BDL remains structural authority for: player IDs, return dates, detailed injury fields.
"""

import io
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

CDN_BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report"
SOURCE_ID = "nba_official"

# Status mapping from official report text to normalized raw status
_STATUS_MAP = {
    "out": "Out",
    "doubtful": "Doubtful",
    "questionable": "Questionable",
    "probable": "Probable",
    "available": "Available",
    "not yet submitted": "Not Yet Submitted",
}

# Team display name → abbreviation
_TEAM_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


def _generate_report_url(report_time: datetime) -> str:
    """Generate the CDN URL for a specific report timestamp."""
    # Format: Injury-Report_2026-04-16_03_30PM.pdf
    date_str = report_time.strftime("%Y-%m-%d")
    hour = report_time.strftime("%I").lstrip("0")  # 1-12, no leading zero
    if len(hour) == 1:
        hour = "0" + hour
    minute = report_time.strftime("%M")
    ampm = report_time.strftime("%p")
    return f"{CDN_BASE}_{date_str}_{hour}_{minute}{ampm}.pdf"


def _get_recent_report_urls(count: int = 4) -> List[Tuple[str, datetime]]:
    """Generate URLs for the most recent report timestamps (every 15 min)."""
    now = datetime.now(timezone(timedelta(hours=-4)))  # ET
    # Round down to nearest 15 min
    minute = (now.minute // 15) * 15
    base = now.replace(minute=minute, second=0, microsecond=0)

    urls = []
    for i in range(count):
        t = base - timedelta(minutes=15 * i)
        urls.append((_generate_report_url(t), t))
    return urls


def _parse_report_text(text: str) -> List[dict]:
    """
    Parse the raw text extracted from the NBA official injury report PDF.

    The format is a flat stream of tokens from a table:
    Game Date, Game Time, Matchup, Team, Player Name, Current Status, Reason
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    records = []
    current_game = {"date": "", "time": "", "matchup": ""}
    current_team = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Detect game date pattern: MM/DD/YYYY
        if re.match(r"\d{2}/\d{2}/\d{4}", line):
            current_game["date"] = line
            i += 1
            continue

        # Detect game time: HH:MM
        if re.match(r"\d{1,2}:\d{2}$", line):
            # Next line should be (ET)
            time_str = line
            if i + 1 < len(lines) and "(ET)" in lines[i + 1]:
                time_str += " " + lines[i + 1]
                i += 2
            else:
                i += 1
            current_game["time"] = time_str
            continue

        # Detect matchup: XXX@YYY
        if re.match(r"[A-Z]{2,4}@[A-Z]{2,4}", line):
            current_game["matchup"] = line
            i += 1
            continue

        # Detect team names
        team_match = None
        for full_name in _TEAM_ABBR:
            # Team names may span 2-3 lines (e.g., "Golden", "State", "Warriors")
            words = full_name.split()
            if i + len(words) <= len(lines):
                candidate = " ".join(lines[i:i + len(words)])
                if candidate == full_name:
                    team_match = full_name
                    i += len(words)
                    break
            # Also check 2-word match
            if len(words) >= 2 and i + 2 <= len(lines):
                candidate = lines[i] + " " + lines[i + 1]
                if candidate == full_name:
                    team_match = full_name
                    i += 2
                    break

        if team_match:
            current_team = _TEAM_ABBR.get(team_match, "")
            # Check if next is NOT YET SUBMITTED
            if i < len(lines) and lines[i].upper() == "NOT":
                # Skip "NOT YET SUBMITTED"
                while i < len(lines) and lines[i].upper() in ("NOT", "YET", "SUBMITTED"):
                    i += 1
            continue

        # Detect player entry: "LastName," pattern (comma after last name)
        if "," in line and not line.startswith("Injury") and not line.startswith("Game") and not line.startswith("Page"):
            # Player name: "LastName, FirstName" may span 2 lines
            last_name = line.rstrip(",")
            first_name = ""
            if line.endswith(",") and i + 1 < len(lines):
                i += 1
                first_name = lines[i]
            elif "," in line:
                parts = line.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip() if len(parts) > 1 else ""

            player_name = f"{first_name} {last_name}".strip()
            i += 1

            # Next should be status
            status = ""
            if i < len(lines):
                status_text = lines[i].lower()
                if status_text in _STATUS_MAP:
                    status = _STATUS_MAP[status_text]
                    i += 1
                else:
                    status = lines[i]
                    i += 1

            # Next should be reason (may span multiple lines until next player/team)
            reason_parts = []
            while i < len(lines):
                peek = lines[i]
                # Stop if we hit another player (comma pattern), team, date, or status keyword
                if "," in peek and not peek.startswith("Injury") and not peek.startswith("-"):
                    break
                if any(peek == w for w in _TEAM_ABBR.keys()):
                    break
                if peek.split()[0] in [w.split()[0] for w in _TEAM_ABBR.keys()]:
                    # Could be start of a team name
                    break
                if re.match(r"\d{2}/\d{2}/\d{4}", peek):
                    break
                if re.match(r"\d{1,2}:\d{2}$", peek):
                    break
                if re.match(r"[A-Z]{2,4}@[A-Z]{2,4}", peek):
                    break
                reason_parts.append(peek)
                i += 1

            reason = " ".join(reason_parts).replace("Injury/Illness - ", "").strip()

            if player_name and status and status != "Not Yet Submitted":
                records.append({
                    "source": SOURCE_ID,
                    "sport": "nba",
                    "player_name": player_name,
                    "bdl_id": None,
                    "team": current_team,
                    "team_id": None,
                    "position": "",
                    "raw_status": status,
                    "return_date": None,
                    "injury_date": None,
                    "description": reason,
                    "short_comment": reason[:120],
                    "injury_type": None,
                    "injury_detail": reason,
                    "injury_side": None,
                    "game_date": current_game.get("date", ""),
                    "game_time": current_game.get("time", ""),
                    "matchup": current_game.get("matchup", ""),
                })
            continue

        i += 1

    return records


class NBAOfficialInjurySource:
    """
    Fetch and parse the NBA official injury report PDFs.
    Timing authority — detects status changes from league-mandated reports.
    """

    SOURCE_ID = SOURCE_ID

    def __init__(self):
        self._stats = {"polls": 0, "records_fetched": 0, "errors": 0, "pdfs_parsed": 0, "last_report_time": None}
        self._last_report_hash: Optional[str] = None

    async def fetch(self, sport: str) -> List[dict]:
        """Only supports NBA. Returns empty for other sports."""
        if sport != "nba":
            return []

        self._stats["polls"] += 1

        # Try recent report URLs (most recent first)
        urls = _get_recent_report_urls(count=4)

        for url, report_time in urls:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    pdf_bytes = resp.content

                    # Quick dedup: skip if same PDF as last time
                    import hashlib
                    pdf_hash = hashlib.md5(pdf_bytes).hexdigest()
                    if pdf_hash == self._last_report_hash:
                        return []  # No new report
                    self._last_report_hash = pdf_hash

                    # Parse PDF
                    from PyPDF2 import PdfReader
                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    text = ""
                    for page in reader.pages:
                        text += page.extract_text() or ""

                    if "NOT YET SUBMITTED" in text and text.count("NOT YET SUBMITTED") == text.count("Team"):
                        # All teams not yet submitted — no data
                        continue

                    records = _parse_report_text(text)
                    if records:
                        self._stats["records_fetched"] += len(records)
                        self._stats["pdfs_parsed"] += 1
                        self._stats["last_report_time"] = report_time.isoformat()
                        logger.info(f"[NBA_OFFICIAL] Parsed {len(records)} entries from {report_time.strftime('%I:%M %p')} report")
                        return records

            except Exception as e:
                self._stats["errors"] += 1
                logger.warning(f"[NBA_OFFICIAL] Failed to fetch/parse {url}: {e}")
                continue

        return []

    def get_stats(self) -> dict:
        return {**self._stats, "source": SOURCE_ID}
