"""
Player Context Data for Badges
==============================
Data for context badges: contract year (pay_day), trade rumors (distraction), 
and deep water (low minutes lately).

Updated March 2026.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# CONTRACT YEAR PLAYERS (PAY_DAY BADGE)
# Players in final year of their contract - extra motivation
# =============================================================================

CONTRACT_YEAR_PLAYERS = {
    # High-profile UFAs 2026
    "Collin Sexton": {"salary": 17737500, "team": "CHI", "type": "UFA"},
    "Jusuf Nurkic": {"salary": 17500000, "team": "UTA", "type": "UFA"},
    "Zach Collins": {"salary": 17410848, "team": "CHI", "type": "UFA"},
    "Bogdan Bogdanovic": {"salary": 17000000, "team": "LAC", "type": "UFA"},
    "De'Anthony Melton": {"salary": 3266350, "team": "GSW", "type": "UFA"},
    "Precious Achiuwa": {"salary": 2453285, "team": "SAC", "type": "UFA"},
    "Thomas Bryant": {"salary": 3287409, "team": "CLE", "type": "UFA"},
    "Jock Landale": {"salary": 2461463, "team": "ATL", "type": "UFA"},
    
    # RFAs - teams have matching rights
    "Ochai Agbaji": {"salary": 4681591, "team": "TOR", "type": "RFA"},
    "Mark Williams": {"salary": 4500253, "team": "PHX", "type": "RFA"},
    "Tari Eason": {"salary": 4064312, "team": "HOU", "type": "RFA"},
    "David Roddy": {"salary": 3246472, "team": "DEN", "type": "RFA"},
    
    # Player options (could opt out)
    "Jonathan Kuminga": {"salary": 6000000, "team": "ATL", "type": "Player Option"},
    "Josh Giddey": {"salary": 8500000, "team": "CHI", "type": "RFA"},
    
    # Stars on expiring deals
    "Kristaps Porzingis": {"salary": 29000000, "team": "ATL", "type": "UFA"},
}

# =============================================================================
# TRADE RUMOR PLAYERS (DISTRACTION BADGE)
# Players with active trade rumors - potential distraction
# =============================================================================

TRADE_RUMOR_PLAYERS = {
    # Post-deadline but still rumored for offseason
    "Giannis Antetokounmpo": {
        "rumor_level": "high",  # high, medium, low
        "reason": "Bucks reportedly open to listening to offers",
        "destinations": ["Warriors", "Heat", "Knicks"],
        "since": "2026-02"
    },
    "Ja Morant": {
        "rumor_level": "medium",
        "reason": "Grizzlies in rebuild mode after JJJ trade",
        "destinations": ["Bulls", "Pistons", "Rockets"],
        "since": "2026-02"
    },
    "Zion Williamson": {
        "rumor_level": "medium",
        "reason": "Pelicans frustrated with injuries, looking to move on",
        "destinations": ["Knicks", "Heat", "Suns"],
        "since": "2025-12"
    },
    "Trae Young": {
        "rumor_level": "low",
        "reason": "Hawks exploring options to reshape roster",
        "destinations": ["Lakers", "Spurs"],
        "since": "2026-01"
    },
    "Brandon Ingram": {
        "rumor_level": "low",
        "reason": "Pelicans seeking trade partner",
        "destinations": ["Kings", "Heat", "Warriors"],
        "since": "2026-01"
    },
    "Scoot Henderson": {
        "rumor_level": "low",
        "reason": "Development concerns, Blazers listening",
        "destinations": ["Spurs", "Hornets"],
        "since": "2026-03"
    },
}

# =============================================================================
# RECENT MAJOR TRADES (Also affects distraction for newly traded players)
# =============================================================================

RECENTLY_TRADED_PLAYERS = {
    # February 2026 deadline trades
    "Anthony Davis": {"from": "LAL", "to": "WAS", "date": "2026-02-05"},
    "James Harden": {"from": "LAC", "to": "CLE", "date": "2026-02-05"},
    "Jaren Jackson Jr.": {"from": "MEM", "to": "UTA", "date": "2026-02-05"},
    "Chris Paul": {"from": "LAC", "to": "TOR", "date": "2026-02-05"},
    "Ivica Zubac": {"from": "LAC", "to": "IND", "date": "2026-02-05"},
    "Jonathan Kuminga": {"from": "GSW", "to": "ATL", "date": "2026-02-04"},
    "Nikola Vucevic": {"from": "CHI", "to": "BOS", "date": "2026-02-03"},
    "Bennedict Mathurin": {"from": "IND", "to": "LAC", "date": "2026-02-05"},
    "Darius Garland": {"from": "CLE", "to": "LAC", "date": "2026-02-05"},
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def is_contract_year(player_name: str) -> Optional[Dict]:
    """
    Check if player is in a contract year.
    
    Returns contract info dict or None.
    """
    return CONTRACT_YEAR_PLAYERS.get(player_name.strip())


def get_trade_rumor(player_name: str) -> Optional[Dict]:
    """
    Check if player has active trade rumors.
    
    Returns rumor info dict or None.
    """
    return TRADE_RUMOR_PLAYERS.get(player_name.strip())


def was_recently_traded(player_name: str) -> Optional[Dict]:
    """
    Check if player was traded in last 30 days.
    
    Returns trade info dict or None.
    """
    trade = RECENTLY_TRADED_PLAYERS.get(player_name.strip())
    if not trade:
        return None
    
    # Check if trade was within last 30 days
    try:
        trade_date = datetime.strptime(trade["date"], "%Y-%m-%d")
        days_since = (datetime.now() - trade_date).days
        if days_since <= 45:  # Extended window for adjustment period
            return {**trade, "days_since": days_since}
    except:
        pass
    
    return None


def get_distraction_info(player_name: str) -> Optional[Dict]:
    """
    Get any distraction factors for a player (trade rumors, recent trade).
    
    Returns dict with type and details, or None.
    """
    name = player_name.strip()
    
    # Check trade rumors first (higher priority)
    rumor = TRADE_RUMOR_PLAYERS.get(name)
    if rumor and rumor.get("rumor_level") in ["high", "medium"]:
        return {
            "type": "trade_rumor",
            "level": rumor["rumor_level"],
            "reason": rumor["reason"],
            "destinations": rumor.get("destinations", [])
        }
    
    # Check recent trades
    trade = was_recently_traded(name)
    if trade:
        return {
            "type": "recently_traded",
            "from_team": trade["from"],
            "to_team": trade["to"],
            "days_since": trade["days_since"],
            "reason": f"Traded from {trade['from']} to {trade['to']} {trade['days_since']} days ago"
        }
    
    # Low-level rumors don't trigger badge
    return None


def get_pay_day_info(player_name: str) -> Optional[Dict]:
    """
    Get contract year info for a player.
    
    Returns dict with contract details, or None.
    """
    contract = CONTRACT_YEAR_PLAYERS.get(player_name.strip())
    if not contract:
        return None
    
    salary = contract.get("salary", 0)
    salary_str = f"${salary / 1000000:.1f}M" if salary >= 1000000 else f"${salary:,}"
    
    return {
        "type": contract.get("type", "UFA"),
        "salary": salary,
        "salary_display": salary_str,
        "team": contract.get("team"),
        "description": f"Contract year ({contract.get('type', 'UFA')}) - {salary_str}"
    }
