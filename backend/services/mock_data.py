"""
Mock Data for Sport-Exclusive Testing
======================================
Contains sample props for NBA and MLB to verify frontend sport switching.
"""

NBA_MOCK_PROPS = [
    {
        "player_name": "LeBron James",
        "team": "LAL",
        "opponent": "GSW",
        "stat_type": "PTS",
        "line": 27.5,
        "recommendation": "OVER",
        "hit_rate_l10": 80,
        "hit_rate_l5": 100,
        "edge": 12.5,
        "prob_over": 72,
        "dk_odds": -180,
        "tier": "safe_haven",
        "sport": "nba"
    },
    {
        "player_name": "Stephen Curry",
        "team": "GSW",
        "opponent": "LAL",
        "stat_type": "PTS",
        "line": 29.5,
        "recommendation": "OVER",
        "hit_rate_l10": 70,
        "hit_rate_l5": 80,
        "edge": 8.2,
        "prob_over": 65,
        "dk_odds": -145,
        "tier": "front_lines",
        "sport": "nba"
    },
    {
        "player_name": "Jayson Tatum",
        "team": "BOS",
        "opponent": "MIA",
        "stat_type": "REB",
        "line": 8.5,
        "recommendation": "OVER",
        "hit_rate_l10": 75,
        "hit_rate_l5": 80,
        "edge": 10.1,
        "prob_over": 68,
        "dk_odds": -160,
        "tier": "safe_haven",
        "sport": "nba"
    },
    {
        "player_name": "Nikola Jokic",
        "team": "DEN",
        "opponent": "PHX",
        "stat_type": "REB",
        "line": 12.5,
        "recommendation": "OVER",
        "hit_rate_l10": 90,
        "hit_rate_l5": 100,
        "edge": 15.3,
        "prob_over": 78,
        "dk_odds": -220,
        "tier": "safe_haven",
        "sport": "nba"
    },
    {
        "player_name": "Anthony Edwards",
        "team": "MIN",
        "opponent": "DAL",
        "stat_type": "PTS",
        "line": 25.5,
        "recommendation": "OVER",
        "hit_rate_l10": 60,
        "hit_rate_l5": 60,
        "edge": 5.5,
        "prob_over": 55,
        "dk_odds": +120,
        "tier": "war_zone",
        "sport": "nba"
    }
]

MLB_MOCK_PROPS = [
    {
        "player_name": "Shohei Ohtani",
        "team": "LAD",
        "opponent": "SF",
        "stat_type": "Strikeouts",
        "line": 8.5,
        "recommendation": "OVER",
        "hit_rate_l10": 85,
        "hit_rate_l5": 100,
        "edge": 14.2,
        "prob_over": 75,
        "dk_odds": -200,
        "tier": "safe_haven",
        "sport": "mlb"
    },
    {
        "player_name": "Gerrit Cole",
        "team": "NYY",
        "opponent": "BOS",
        "stat_type": "Strikeouts",
        "line": 7.5,
        "recommendation": "OVER",
        "hit_rate_l10": 70,
        "hit_rate_l5": 80,
        "edge": 9.8,
        "prob_over": 68,
        "dk_odds": -155,
        "tier": "front_lines",
        "sport": "mlb"
    },
    {
        "player_name": "Mookie Betts",
        "team": "LAD",
        "opponent": "SF",
        "stat_type": "Total Bases",
        "line": 2.5,
        "recommendation": "OVER",
        "hit_rate_l10": 75,
        "hit_rate_l5": 80,
        "edge": 11.5,
        "prob_over": 70,
        "dk_odds": -170,
        "tier": "safe_haven",
        "sport": "mlb"
    },
    {
        "player_name": "Aaron Judge",
        "team": "NYY",
        "opponent": "BOS",
        "stat_type": "Total Bases",
        "line": 2.5,
        "recommendation": "OVER",
        "hit_rate_l10": 80,
        "hit_rate_l5": 80,
        "edge": 12.0,
        "prob_over": 72,
        "dk_odds": -180,
        "tier": "safe_haven",
        "sport": "mlb"
    },
    {
        "player_name": "Corbin Burnes",
        "team": "BAL",
        "opponent": "TOR",
        "stat_type": "Strikeouts",
        "line": 6.5,
        "recommendation": "OVER",
        "hit_rate_l10": 65,
        "hit_rate_l5": 60,
        "edge": 6.2,
        "prob_over": 58,
        "dk_odds": +110,
        "tier": "war_zone",
        "sport": "mlb"
    },
    {
        "player_name": "Ronald Acuna Jr.",
        "team": "ATL",
        "opponent": "NYM",
        "stat_type": "Total Bases",
        "line": 1.5,
        "recommendation": "OVER",
        "hit_rate_l10": 60,
        "hit_rate_l5": 60,
        "edge": 4.5,
        "prob_over": 54,
        "dk_odds": +130,
        "tier": "war_zone",
        "sport": "mlb"
    }
]

# Tier-specific mock data
NBA_MOCK_TIERS = {
    "safe_haven": [p for p in NBA_MOCK_PROPS if p["tier"] == "safe_haven"],
    "front_lines": [p for p in NBA_MOCK_PROPS if p["tier"] == "front_lines"],
    "war_zone": [p for p in NBA_MOCK_PROPS if p["tier"] == "war_zone"]
}

MLB_MOCK_TIERS = {
    "safe_haven": [p for p in MLB_MOCK_PROPS if p["tier"] == "safe_haven"],
    "front_lines": [p for p in MLB_MOCK_PROPS if p["tier"] == "front_lines"],
    "war_zone": [p for p in MLB_MOCK_PROPS if p["tier"] == "war_zone"]
}

def get_mock_props(sport: str):
    """Get all mock props for a sport."""
    if sport == "mlb":
        return MLB_MOCK_PROPS
    return NBA_MOCK_PROPS

def get_mock_tier(sport: str, tier: str):
    """Get mock props for a specific tier and sport."""
    if sport == "mlb":
        return MLB_MOCK_TIERS.get(tier, [])
    return NBA_MOCK_TIERS.get(tier, [])
