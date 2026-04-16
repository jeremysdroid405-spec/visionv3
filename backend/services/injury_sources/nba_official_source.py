"""
NBA Official Injury Report Source Adapter
==========================================
Timing authority for NBA injuries.

The NBA publishes timestamped PDF injury reports at:
  https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM.pdf

Trust role: TIMING AUTHORITY ONLY
  - Detects status changes from official league submissions
  - Faster than BDL for official status changes (Out -> Probable, etc.)
  - NEVER a structural authority. No player IDs, no return dates.
  - Records from this source are used ONLY to annotate BDL records
    with timing disagreement signals. They are NEVER written to
    injuries_normalized directly.

BDL remains the sole structural authority for: player IDs, return dates,
injury detail fields. The Live Injury Advantage engine reads exclusively
from BDL-derived normalized data.
"""

import io
import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import httpx
import pdfplumber

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
}

# Team display name -> abbreviation
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

# Reverse lookup: abbreviation -> full name (for matching)
_ABBR_TO_TEAM = {v: k for k, v in _TEAM_ABBR.items()}


def _generate_report_url(report_time: datetime) -> str:
    """Generate the CDN URL for a specific report timestamp."""
    date_str = report_time.strftime("%Y-%m-%d")
    hour = report_time.strftime("%I")
    minute = report_time.strftime("%M")
    ampm = report_time.strftime("%p")
    return f"{CDN_BASE}_{date_str}_{hour}_{minute}{ampm}.pdf"


def _get_recent_report_urls(count: int = 6) -> List[Tuple[str, datetime]]:
    """Generate URLs for the most recent report timestamps (every 15 min)."""
    now = datetime.now(timezone(timedelta(hours=-4)))  # ET
    minute = (now.minute // 15) * 15
    base = now.replace(minute=minute, second=0, microsecond=0)

    urls = []
    for i in range(count):
        t = base - timedelta(minutes=15 * i)
        urls.append((_generate_report_url(t), t))
    return urls


def _parse_pdf_tables(pdf_bytes: bytes) -> List[dict]:
    """
    Extract injury records from the NBA official PDF using pdfplumber.

    The PDF uses a table layout with columns:
    GameTime | Matchup | Team | PlayerName | CurrentStatus | Reason

    Team names are concatenated (e.g., "OrlandoMagic"), player names use
    "Last,First" format, and reasons can span continuation rows.

    Returns list of raw records for the sensor's merge logic.
    """
    records = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                # Use text strategy — the default line/edge strategy misses data rows
                tables = page.extract_tables({
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                })
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find column indices from header
                    header = [(h or "").strip().lower() for h in table[0]]

                    col_map = {}
                    for idx, h in enumerate(header):
                        if "gametime" in h or "game time" in h:
                            col_map["time"] = idx
                        elif "matchup" in h:
                            col_map["matchup"] = idx
                        elif "team" in h:
                            col_map["team"] = idx
                        elif "player" in h:
                            col_map["player"] = idx
                        elif "status" in h:
                            col_map["status"] = idx
                        elif "reason" in h:
                            col_map["reason"] = idx

                    if "player" not in col_map or "status" not in col_map:
                        continue

                    current_time = ""
                    current_matchup = ""
                    current_team_raw = ""
                    # Track last player record for reason continuation rows
                    last_record = None

                    for row in table[1:]:
                        if not row or all(not (c or "").strip() for c in row):
                            continue

                        def cell(key):
                            idx = col_map.get(key)
                            if idx is not None and idx < len(row):
                                return (row[idx] or "").strip()
                            return ""

                        # Update game context from non-empty cells
                        t = cell("time")
                        if t:
                            current_time = t
                        m = cell("matchup")
                        if m:
                            current_matchup = m
                        tm = cell("team")
                        if tm:
                            current_team_raw = tm

                        player_raw = cell("player")
                        status_raw = cell("status")
                        reason_raw = cell("reason")

                        # Continuation row: no player but has reason -> append to last record
                        if not player_raw and not status_raw and reason_raw and last_record:
                            prev_reason = last_record.get("description", "")
                            last_record["description"] = (prev_reason + " " + reason_raw).strip()
                            last_record["short_comment"] = last_record["description"][:120]
                            last_record["injury_detail"] = last_record["description"]
                            continue

                        if not player_raw or not status_raw:
                            continue

                        # Skip NOT YET SUBMITTED
                        if "not yet submitted" in status_raw.lower():
                            continue

                        # Normalize status
                        normalized_status = _STATUS_MAP.get(status_raw.lower(), status_raw)

                        # Skip "Available" - not an injury
                        if normalized_status == "Available":
                            last_record = None
                            continue

                        # Resolve team abbreviation from concatenated name
                        team_abbr = _resolve_team_abbr(current_team_raw)

                        # Normalize player name: "Last,First" or "LastSuffix,First" -> "First Last"
                        player_name = _normalize_player_name(player_raw)

                        # Clean reason
                        reason = _clean_reason(reason_raw)

                        rec = {
                            "source": SOURCE_ID,
                            "sport": "nba",
                            "player_name": player_name,
                            "bdl_id": None,
                            "team": team_abbr,
                            "team_id": None,
                            "position": "",
                            "raw_status": normalized_status,
                            "return_date": None,
                            "injury_date": None,
                            "description": reason,
                            "short_comment": reason[:120],
                            "injury_type": None,
                            "injury_detail": reason,
                            "injury_side": None,
                            "game_time": current_time,
                            "matchup": current_matchup,
                        }
                        records.append(rec)
                        last_record = rec

    except Exception as e:
        logger.error(f"[NBA_OFFICIAL] pdfplumber extraction failed: {e}")

    return records


def _resolve_team_abbr(raw: str) -> str:
    """Resolve concatenated team names (e.g., 'OrlandoMagic') to abbreviation."""
    if not raw:
        return ""
    # Direct match
    if raw in _TEAM_ABBR:
        return _TEAM_ABBR[raw]
    if raw.upper() in _ABBR_TO_TEAM:
        return raw.upper()
    # Try splitting concatenated names (e.g., "OrlandoMagic" -> "Orlando Magic")
    for full_name, abbr in _TEAM_ABBR.items():
        # Remove spaces from full name and compare
        if raw.replace(" ", "") == full_name.replace(" ", ""):
            return abbr
    return ""


def _normalize_player_name(raw: str) -> str:
    """Convert 'Last,First' or 'LastJr.,First' or 'LastIII,First' to 'First Last' format."""
    if "," in raw:
        parts = raw.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else ""
        # Separate suffixes concatenated with last name: ButlerIII -> Butler III
        suffix_match = re.match(r"^(.+?)((?:Jr|Sr|II|III|IV|V)\.?)$", last)
        if suffix_match:
            last = suffix_match.group(1).strip() + " " + suffix_match.group(2).strip()
        return f"{first} {last}".strip()
    return raw.strip()


def _clean_reason(raw: str) -> str:
    """Clean up injury reason text from the PDF."""
    reason = (raw or "").strip()
    reason = reason.replace("Injury/Illness-", "").replace("Injury/Illness -", "")
    reason = reason.replace("Rest-", "Rest: ").replace("Rest -", "Rest: ")
    # Fix missing spaces around semicolons
    reason = re.sub(r";(\S)", r"; \1", reason)
    return reason.strip()


def _parse_text_fallback(text: str) -> List[dict]:
    """
    Fallback text parser when pdfplumber can't extract tables.
    Scans for player entries using pattern: "LastName, FirstName" followed by status.
    """
    records = []
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    current_team = ""
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for team names
        for full_name, abbr in _TEAM_ABBR.items():
            if line == full_name or line.startswith(full_name):
                current_team = abbr
                break

        # Skip NOT YET SUBMITTED blocks
        if "NOT YET SUBMITTED" in line.upper():
            i += 1
            continue

        # Player pattern: contains comma, not a header
        if "," in line and not any(line.startswith(h) for h in ("Game", "Injury", "Page", "Category")):
            parts = line.split(",", 1)
            last = parts[0].strip()
            first = parts[1].strip() if len(parts) > 1 else ""
            player_name = f"{first} {last}".strip()

            # Next line should be status
            status = ""
            reason = ""
            if i + 1 < len(lines):
                status_candidate = lines[i + 1].strip().lower()
                if status_candidate in _STATUS_MAP:
                    status = _STATUS_MAP[status_candidate]
                    i += 2
                    # Collect reason lines
                    while i < len(lines):
                        peek = lines[i]
                        if "," in peek or peek.lower() in _STATUS_MAP or re.match(r"\d{2}/\d{2}/\d{4}", peek):
                            break
                        reason += " " + peek
                        i += 1
                    reason = reason.strip().replace("Injury/Illness - ", "")
                else:
                    i += 1
                    continue
            else:
                i += 1
                continue

            if player_name and status and status != "Available":
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
                    "description": reason[:120],
                    "short_comment": reason[:120],
                    "injury_type": None,
                    "injury_detail": reason[:120],
                    "injury_side": None,
                })
            continue

        i += 1

    return records


class NBAOfficialInjurySource:
    """
    Fetch and parse the NBA official injury report PDFs.

    TIMING AUTHORITY ONLY. Records returned by this adapter are used
    exclusively for status-change timing signals. They are NEVER written
    to injuries_normalized. BDL remains the sole structural authority.
    """

    SOURCE_ID = SOURCE_ID

    def __init__(self):
        self._stats = {
            "polls": 0,
            "records_fetched": 0,
            "errors": 0,
            "pdfs_parsed": 0,
            "last_report_time": None,
            "skipped_no_new": 0,
        }
        self._last_report_hash: Optional[str] = None

    async def fetch(self, sport: str) -> List[dict]:
        """Only supports NBA. Returns empty for other sports."""
        if sport != "nba":
            return []

        self._stats["polls"] += 1

        urls = _get_recent_report_urls(count=6)

        for url, report_time in urls:
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    pdf_bytes = resp.content
                    if len(pdf_bytes) < 500:
                        continue  # Too small to be a real report

                    # Dedup: skip if same PDF content as last fetch
                    pdf_hash = hashlib.md5(pdf_bytes).hexdigest()
                    if pdf_hash == self._last_report_hash:
                        self._stats["skipped_no_new"] += 1
                        return []
                    self._last_report_hash = pdf_hash

                    # Parse with pdfplumber
                    records = _parse_pdf_tables(pdf_bytes)

                    if records:
                        self._stats["records_fetched"] += len(records)
                        self._stats["pdfs_parsed"] += 1
                        self._stats["last_report_time"] = report_time.isoformat()
                        logger.info(
                            f"[NBA_OFFICIAL] Parsed {len(records)} entries from "
                            f"{report_time.strftime('%I:%M %p')} report"
                        )
                        return records

            except Exception as e:
                self._stats["errors"] += 1
                logger.warning(f"[NBA_OFFICIAL] Failed to fetch/parse {url}: {e}")
                continue

        return []

    def get_stats(self) -> dict:
        return {**self._stats, "source": SOURCE_ID}
