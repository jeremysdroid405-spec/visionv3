"""
Spotrac Contract Data Service
============================
Scrapes player contract data from Spotrac.com for the pay_day badge.
Caches results in MongoDB with 24h TTL to avoid excessive scraping.
"""
import os
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import requests
from bs4 import BeautifulSoup
import time

logger = logging.getLogger(__name__)

# ========== EXCLUSION LIST ==========
# Players who are confirmed NOT in contract years (signed extensions, etc.)
# This overrides any Spotrac data that may be stale/incorrect
CONTRACT_YEAR_EXCLUSIONS = {
    # Star max extensions signed
    "shai gilgeous-alexander",  # Signed 5-year $207M extension in 2024
    "jayson tatum",             # Signed supermax extension
    "luka doncic",              # Signed supermax extension  
    "anthony edwards",          # Signed max extension
    "evan mobley",              # Signed max extension
    "scottie barnes",           # Signed max extension
    "cade cunningham",          # Signed max extension
    "franz wagner",             # Signed max extension
    "tyrese haliburton",        # Signed max extension
    "paolo banchero",           # Signed extension
    "jalen brunson",            # Signed extension with Knicks
    "chet holmgren",            # Signed extension with Thunder
}

# Spotrac URLs
SPOTRAC_NBA_CONTRACTS_URL = "https://www.spotrac.com/nba/contracts"
SPOTRAC_FA_URL = "https://www.spotrac.com/nba/free-agents"

# User agent to avoid being blocked
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Cache TTL - 24 hours
CACHE_TTL_HOURS = 24


def get_db() -> AsyncIOMotorDatabase:
    """Get MongoDB database connection."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    return client[os.environ.get("DB_NAME", "nba_props")]


async def get_cached_contracts(db: AsyncIOMotorDatabase) -> Optional[Dict]:
    """
    Get cached contract data if it exists and is fresh.
    Returns None if cache is stale or doesn't exist.
    """
    cache_collection = db["spotrac_contracts_cache"]
    cache_doc = await cache_collection.find_one(
        {"type": "contracts_cache"},
        {"_id": 0}
    )
    
    if not cache_doc:
        return None
    
    cached_at = cache_doc.get("cached_at")
    if not cached_at:
        return None
    
    # Check if cache is still fresh
    now = datetime.now(timezone.utc)
    if isinstance(cached_at, str):
        cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    
    age_hours = (now - cached_at).total_seconds() / 3600
    if age_hours > CACHE_TTL_HOURS:
        logger.info(f"Contract cache expired (age: {age_hours:.1f}h)")
        return None
    
    logger.info(f"Using cached contract data (age: {age_hours:.1f}h)")
    return cache_doc


def scrape_free_agents_page() -> List[Dict]:
    """
    Scrape the Spotrac free agents page to get players with expiring contracts.
    Returns list of contract year players.
    """
    contract_year_players = []
    
    try:
        # Scrape 2026 UFAs and RFAs
        for fa_type in ["ufa", "rfa"]:
            url = f"{SPOTRAC_FA_URL}/{fa_type}/2026"
            logger.info(f"Scraping {url}")
            
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {url}: {response.status_code}")
                continue
            
            soup = BeautifulSoup(response.content, "lxml")
            
            # Find all tables - the main FA table is usually the larger one
            tables = soup.find_all("table")
            
            # Find the table with most rows (the main free agents table)
            main_table = None
            max_rows = 0
            for table in tables:
                tbody = table.find("tbody")
                if tbody:
                    rows = tbody.find_all("tr")
                    if len(rows) > max_rows:
                        max_rows = len(rows)
                        main_table = table
            
            if not main_table:
                logger.warning(f"No main table found on {url}")
                continue
            
            tbody = main_table.find("tbody")
            if not tbody:
                continue
            
            rows = tbody.find_all("tr")
            logger.info(f"Found {len(rows)} {fa_type.upper()} rows")
            
            for row in rows:
                try:
                    cells = row.find_all("td")
                    if len(cells) < 5:
                        continue
                    
                    # Structure based on analysis:
                    # 0: Player name, 1: Position, 2: Age, 3: YOE, 4: Prev Team, 5: Prev AAV, 6: Type
                    
                    # Player name (cell 0)
                    name_cell = cells[0]
                    name_link = name_cell.find("a")
                    if name_link:
                        player_name = name_link.get_text(strip=True)
                    else:
                        player_name = name_cell.get_text(strip=True)
                    
                    # Clean player name
                    player_name = re.sub(r'\s+', ' ', player_name).strip()
                    
                    # Skip empty names
                    if not player_name or len(player_name) < 3:
                        continue
                    
                    # Team (cell 4 - "Prev Team")
                    team = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    
                    # Salary (cell 5 - "Prev AAV")
                    salary = 0
                    if len(cells) > 5:
                        salary_text = cells[5].get_text(strip=True)
                        salary = parse_salary(salary_text)
                    
                    # Contract type (cell 6 - "Type")
                    contract_type = fa_type.upper()
                    if len(cells) > 6:
                        type_text = cells[6].get_text(strip=True).upper()
                        if "PLAYER" in type_text:
                            contract_type = "Player Option"
                        elif "TEAM" in type_text:
                            contract_type = "Team Option"
                        elif "UFA" in type_text or "BIRD" in type_text:
                            contract_type = "UFA"
                        elif "RFA" in type_text:
                            contract_type = "RFA"
                    
                    if player_name:
                        contract_year_players.append({
                            "player_name": player_name,
                            "team": team,
                            "salary": salary,
                            "type": contract_type,
                            "contract_year": True,
                            "expires": 2026
                        })
                
                except Exception as e:
                    logger.debug(f"Error parsing row: {e}")
                    continue
            
            # Rate limit between pages
            time.sleep(1)
    
    except Exception as e:
        logger.error(f"Error scraping free agents: {e}")
    
    return contract_year_players


def scrape_player_options_page() -> List[Dict]:
    """
    Scrape players with player options who could opt out.
    """
    players = []
    
    try:
        url = "https://www.spotrac.com/nba/contracts/player-option"
        logger.info(f"Scraping {url}")
        
        response = requests.get(url, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch player options: {response.status_code}")
            return players
        
        soup = BeautifulSoup(response.content, "lxml")
        
        # Find the table with most rows
        tables = soup.find_all("table")
        main_table = None
        max_rows = 0
        for table in tables:
            tbody = table.find("tbody")
            if tbody:
                rows = tbody.find_all("tr")
                if len(rows) > max_rows:
                    max_rows = len(rows)
                    main_table = table
        
        if not main_table:
            return players
        
        tbody = main_table.find("tbody")
        if not tbody:
            return players
        
        rows = tbody.find_all("tr")
        logger.info(f"Found {len(rows)} player option rows")
        
        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                
                # Player name
                name_cell = cells[0]
                name_link = name_cell.find("a")
                player_name = name_link.get_text(strip=True) if name_link else name_cell.get_text(strip=True)
                player_name = re.sub(r'\s+', ' ', player_name).strip()
                
                if not player_name or len(player_name) < 3:
                    continue
                
                # Look for team abbreviation and year in cells
                team = ""
                year_text = ""
                salary = 0
                
                for cell in cells[1:]:
                    cell_text = cell.get_text(strip=True)
                    # Team abbrev
                    if re.match(r'^[A-Z]{2,3}$', cell_text) and not team:
                        team = cell_text
                    # Year pattern like 2025-26
                    elif re.search(r'202[4-6]', cell_text):
                        year_text = cell_text
                    # Salary
                    elif '$' in cell_text:
                        parsed = parse_salary(cell_text)
                        if parsed > salary:
                            salary = parsed
                
                # Only include 2025-26 or 2026 options
                if "2025-26" in year_text or "2026" in year_text or not year_text:
                    players.append({
                        "player_name": player_name,
                        "team": team,
                        "salary": salary,
                        "type": "Player Option",
                        "contract_year": True,
                        "expires": 2026,
                        "option_year": year_text if year_text else "2025-26"
                    })
            
            except Exception as e:
                logger.debug(f"Error parsing player option row: {e}")
                continue
    
    except Exception as e:
        logger.error(f"Error scraping player options: {e}")
    
    return players


def parse_salary(salary_text: str) -> int:
    """Parse salary string to integer."""
    if not salary_text:
        return 0
    
    # Remove non-numeric chars except decimal
    cleaned = re.sub(r'[^\d.]', '', salary_text)
    if not cleaned:
        return 0
    
    try:
        value = float(cleaned)
        # If the number is small, assume it's in millions
        if value < 1000:
            value *= 1_000_000
        return int(value)
    except ValueError:
        return 0


def scrape_all_contracts() -> Dict[str, Dict]:
    """
    Main scraping function - gets all contract year players.
    Returns dict keyed by normalized player name.
    """
    logger.info("Starting Spotrac contract scrape...")
    all_players = {}
    
    # Get free agents (UFAs and RFAs)
    fa_players = scrape_free_agents_page()
    logger.info(f"Found {len(fa_players)} free agent contracts")
    
    for p in fa_players:
        name = normalize_name(p["player_name"])
        all_players[name] = p
    
    # Get player options
    time.sleep(1)  # Rate limit
    option_players = scrape_player_options_page()
    logger.info(f"Found {len(option_players)} player options")
    
    for p in option_players:
        name = normalize_name(p["player_name"])
        if name not in all_players:  # Don't overwrite FA data
            all_players[name] = p
    
    logger.info(f"Total contract year players found: {len(all_players)}")
    return all_players


def normalize_name(name: str) -> str:
    """Normalize player name for matching."""
    # Remove Jr., III, etc.
    name = re.sub(r'\s+(Jr\.?|Sr\.?|III|II|IV)$', '', name, flags=re.IGNORECASE)
    # Lowercase and strip
    return name.lower().strip()


async def sync_contract_data(db: Optional[AsyncIOMotorDatabase] = None) -> Dict:
    """
    Sync contract data from Spotrac to MongoDB.
    Returns summary of sync operation.
    """
    if db is None:
        db = get_db()
    
    cache_collection = db["spotrac_contracts_cache"]
    
    # Check if we already have fresh cache
    existing = await get_cached_contracts(db)
    if existing:
        return {
            "success": True,
            "message": "Using cached contract data",
            "players_count": len(existing.get("contracts", {})),
            "cached_at": existing.get("cached_at"),
            "from_cache": True
        }
    
    # Scrape fresh data (run in thread to avoid blocking)
    loop = asyncio.get_event_loop()
    contracts = await loop.run_in_executor(None, scrape_all_contracts)
    
    if not contracts:
        return {
            "success": False,
            "message": "Failed to scrape contract data",
            "players_count": 0
        }
    
    # Save to cache
    cache_doc = {
        "type": "contracts_cache",
        "contracts": contracts,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "players_count": len(contracts)
    }
    
    await cache_collection.replace_one(
        {"type": "contracts_cache"},
        cache_doc,
        upsert=True
    )
    
    logger.info(f"Cached {len(contracts)} contract year players")
    
    return {
        "success": True,
        "message": "Contract data synced from Spotrac",
        "players_count": len(contracts),
        "cached_at": cache_doc["cached_at"],
        "from_cache": False
    }


async def get_contract_year_info(player_name: str, db: Optional[AsyncIOMotorDatabase] = None) -> Optional[Dict]:
    """
    Get contract year info for a specific player.
    
    Args:
        player_name: The player's display name
        db: Optional database connection
    
    Returns:
        Dict with contract info or None if not in contract year
    """
    # ========== CHECK EXCLUSION LIST FIRST ==========
    # Players confirmed to have signed extensions should NOT get pay_day badge
    normalized_check = player_name.lower().strip()
    if normalized_check in CONTRACT_YEAR_EXCLUSIONS:
        logger.debug(f"[PAY_DAY] {player_name} excluded (signed extension)")
        return None
    
    if db is None:
        db = get_db()
    
    cache_collection = db["spotrac_contracts_cache"]
    
    # Get cached data
    cache_doc = await cache_collection.find_one(
        {"type": "contracts_cache"},
        {"_id": 0, "contracts": 1}
    )
    
    if not cache_doc or not cache_doc.get("contracts"):
        # Try to sync if no cache
        logger.info("No contract cache found, attempting sync...")
        sync_result = await sync_contract_data(db)
        if not sync_result.get("success"):
            return None
        
        cache_doc = await cache_collection.find_one(
            {"type": "contracts_cache"},
            {"_id": 0, "contracts": 1}
        )
    
    contracts = cache_doc.get("contracts", {}) if cache_doc else {}
    
    # Normalize player name for lookup
    normalized = normalize_name(player_name)
    
    # Double-check exclusion with normalized name
    if normalized in CONTRACT_YEAR_EXCLUSIONS:
        logger.debug(f"[PAY_DAY] {player_name} excluded (normalized match)")
        return None
    
    contract = contracts.get(normalized)
    if not contract:
        return None
    
    # Format for badge display
    salary = contract.get("salary", 0)
    salary_str = f"${salary / 1_000_000:.1f}M" if salary >= 1_000_000 else f"${salary:,}"
    
    return {
        "type": contract.get("type", "UFA"),
        "salary": salary,
        "salary_display": salary_str,
        "team": contract.get("team"),
        "expires": contract.get("expires", 2026),
        "description": f"Contract year ({contract.get('type', 'UFA')}) - {salary_str}",
        "source": "spotrac"
    }


async def get_all_contract_year_players(db: Optional[AsyncIOMotorDatabase] = None) -> List[str]:
    """
    Get list of all players in contract years.
    Useful for batch operations.
    """
    if db is None:
        db = get_db()
    
    cache_collection = db["spotrac_contracts_cache"]
    cache_doc = await cache_collection.find_one(
        {"type": "contracts_cache"},
        {"_id": 0, "contracts": 1}
    )
    
    if not cache_doc:
        return []
    
    contracts = cache_doc.get("contracts", {})
    return [c.get("player_name") for c in contracts.values() if c.get("player_name")]


# ============================================================================
# TEST FUNCTION
# ============================================================================

async def test_scraper():
    """Test the scraper functionality."""
    print("Testing Spotrac scraper...")
    
    # Test sync
    db = get_db()
    result = await sync_contract_data(db)
    print(f"Sync result: {result}")
    
    if result.get("success"):
        # Test lookup
        test_players = ["Collin Sexton", "Jonathan Kuminga", "LeBron James", "Trae Young"]
        for player in test_players:
            info = await get_contract_year_info(player, db)
            if info:
                print(f"  {player}: {info.get('type')} - {info.get('salary_display')}")
            else:
                print(f"  {player}: Not in contract year")


if __name__ == "__main__":
    asyncio.run(test_scraper())
