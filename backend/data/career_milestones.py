"""
NBA Career Milestones & All-Time Rankings
==========================================
Tracks active players' career stats and all-time rankings.

Data sourced from ESPN, Basketball Reference (as of March 2026).

MILESTONE BADGE TRIGGERS:
1. Player is in TOP 25 all-time for any category
2. Player is within 500 of passing someone on the all-time list
3. Player recently achieved a major milestone (10K, 20K, 30K, etc.)
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# ALL-TIME LEADERS (as of March 2026)
# =============================================================================

# Points - Top 30 All-Time
ALL_TIME_POINTS = [
    (1, "LeBron James", 43180, True),      # Active
    (2, "Kareem Abdul-Jabbar", 38387, False),
    (3, "Karl Malone", 36928, False),
    (4, "Kobe Bryant", 33643, False),
    (5, "Michael Jordan", 32292, False),
    (6, "Dirk Nowitzki", 31560, False),
    (7, "Wilt Chamberlain", 31419, False),
    (8, "Kevin Durant", 30902, True),       # Active
    (9, "Shaquille O'Neal", 28596, False),
    (10, "Carmelo Anthony", 28289, False),
    (11, "James Harden", 28027, True),      # Active
    (12, "Moses Malone", 27409, False),
    (13, "Elvin Hayes", 27313, False),
    (14, "Hakeem Olajuwon", 26946, False),
    (15, "Oscar Robertson", 26710, False),
    (16, "Dominique Wilkins", 26668, False),
    (17, "Tim Duncan", 26496, False),
    (18, "Russell Westbrook", 26413, True), # Active
    (19, "Paul Pierce", 26397, False),
    (20, "John Havlicek", 26395, False),
    (21, "Kevin Garnett", 26071, False),
    (22, "Stephen Curry", 25749, True),     # Active
    (23, "Vince Carter", 25728, False),
    (24, "Alex English", 25613, False),
    (25, "DeMar DeRozan", 25572, True),     # Active
    (26, "Reggie Miller", 25279, False),
    (27, "Jerry West", 25192, False),
    (28, "Patrick Ewing", 24815, False),
    (29, "Ray Allen", 24505, False),
    (30, "Allen Iverson", 24368, False),
]

# Assists - Top 20 All-Time
ALL_TIME_ASSISTS = [
    (1, "John Stockton", 15806, False),
    (2, "Chris Paul", 12552, True),         # Active
    (3, "Jason Kidd", 12091, False),
    (4, "LeBron James", 11909, True),       # Active
    (5, "Steve Nash", 10335, False),
    (6, "Mark Jackson", 10334, False),
    (7, "Magic Johnson", 10141, False),
    (8, "Russell Westbrook", 10021, True),  # Active
    (9, "Oscar Robertson", 9887, False),
    (10, "Isiah Thomas", 9061, False),
    (11, "Gary Payton", 8966, False),
    (12, "Andre Miller", 8524, False),
    (13, "James Harden", 7560, True),       # Active
    (14, "Rod Strickland", 7489, False),
    (15, "Rajon Rondo", 7431, False),
    (16, "Maurice Cheeks", 7392, False),
    (17, "Lenny Wilkens", 7211, False),
    (18, "Terry Porter", 7160, False),
    (19, "Tim Hardaway", 7095, False),
    (20, "Kyle Lowry", 7050, True),         # Active
]

# 3-Pointers Made - Top 20 All-Time
ALL_TIME_3PM = [
    (1, "Stephen Curry", 4233, True),       # Active - Record holder
    (2, "James Harden", 3353, True),        # Active
    (3, "Ray Allen", 2973, False),
    (4, "Klay Thompson", 2869, True),       # Active
    (5, "Damian Lillard", 2804, True),      # Active
    (6, "Reggie Miller", 2560, False),
    (7, "Kyle Korver", 2450, False),
    (8, "Vince Carter", 2290, False),
    (9, "Jason Terry", 2282, False),
    (10, "Jamal Crawford", 2221, False),
    (11, "Paul George", 2180, True),        # Active
    (12, "Buddy Hield", 2050, True),        # Active
    (13, "J.J. Redick", 1950, False),
    (14, "Paul Pierce", 1823, False),
    (15, "Jason Kidd", 1988, False),
    (16, "CJ McCollum", 1820, True),        # Active
    (17, "Kyrie Irving", 1780, True),       # Active
    (18, "Joe Johnson", 1778, False),
    (19, "Kemba Walker", 1750, False),
    (20, "Dirk Nowitzki", 1982, False),
]

# Rebounds - Top 15 All-Time
ALL_TIME_REBOUNDS = [
    (1, "Wilt Chamberlain", 23924, False),
    (2, "Bill Russell", 21620, False),
    (3, "Moses Malone", 17834, False),
    (4, "Kareem Abdul-Jabbar", 17440, False),
    (5, "Artis Gilmore", 16330, False),
    (6, "Elvin Hayes", 16279, False),
    (7, "Tim Duncan", 15091, False),
    (8, "Karl Malone", 14968, False),
    (9, "Robert Parish", 14715, False),
    (10, "Kevin Garnett", 14662, False),
    (11, "Nate Thurmond", 14464, False),
    (12, "Walt Bellamy", 14241, False),
    (13, "Wes Unseld", 13769, False),
    (14, "Dwight Howard", 14623, True),     # Active
    (15, "Buck Williams", 13017, False),
]

# Steals - Top 15 All-Time
ALL_TIME_STEALS = [
    (1, "John Stockton", 3265, False),
    (2, "Jason Kidd", 2684, False),
    (3, "Michael Jordan", 2514, False),
    (4, "Gary Payton", 2445, False),
    (5, "Maurice Cheeks", 2310, False),
    (6, "Scottie Pippen", 2307, False),
    (7, "Chris Paul", 2612, True),          # Active - Top 5!
    (8, "Clyde Drexler", 2207, False),
    (9, "Hakeem Olajuwon", 2162, False),
    (10, "Alvin Robertson", 2112, False),
    (11, "Karl Malone", 2085, False),
    (12, "Mookie Blaylock", 2075, False),
    (13, "Julius Erving", 2272, False),
    (14, "Derek Harper", 1957, False),
    (15, "Russell Westbrook", 1946, True),  # Active
]

# Blocks - Top 15 All-Time
ALL_TIME_BLOCKS = [
    (1, "Hakeem Olajuwon", 3830, False),
    (2, "Dikembe Mutombo", 3289, False),
    (3, "Kareem Abdul-Jabbar", 3189, False),
    (4, "Mark Eaton", 3064, False),
    (5, "Tim Duncan", 3020, False),
    (6, "David Robinson", 2954, False),
    (7, "Patrick Ewing", 2894, False),
    (8, "Shaquille O'Neal", 2732, False),
    (9, "Tree Rollins", 2542, False),
    (10, "Robert Parish", 2361, False),
    (11, "Alonzo Mourning", 2356, False),
    (12, "Caldwell Jones", 2297, False),
    (13, "Marcus Camby", 2331, False),
    (14, "Dwight Howard", 2201, True),      # Active
    (15, "Anthony Davis", 1750, True),      # Active
]

# =============================================================================
# ACTIVE PLAYERS CAREER STATS (Updated March 2026)
# =============================================================================

ACTIVE_CAREER_STATS = {
    "LeBron James": {
        "pts": 43180, "reb": 11120, "ast": 11909, "stl": 2290, "blk": 1120, "3pm": 2510,
        "ranking": {"pts": 1, "ast": 4},
        "years": 23
    },
    "Kevin Durant": {
        "pts": 30902, "reb": 7650, "ast": 5920, "stl": 1200, "blk": 1250, "3pm": 1890,
        "ranking": {"pts": 8},
        "years": 18
    },
    "Stephen Curry": {
        "pts": 25749, "reb": 4950, "ast": 6250, "stl": 1550, "blk": 240, "3pm": 4233,
        "ranking": {"pts": 22, "3pm": 1},
        "years": 16
    },
    "James Harden": {
        "pts": 28027, "reb": 6280, "ast": 7560, "stl": 1710, "blk": 680, "3pm": 3353,
        "ranking": {"pts": 11, "3pm": 2},
        "years": 16
    },
    "Chris Paul": {
        "pts": 22150, "reb": 5620, "ast": 12552, "stl": 2612, "blk": 135, "3pm": 2250,
        "ranking": {"ast": 2, "stl": 5},
        "years": 21
    },
    "Russell Westbrook": {
        "pts": 26413, "reb": 8550, "ast": 10021, "stl": 1946, "blk": 360, "3pm": 1250,
        "ranking": {"pts": 18, "ast": 8},
        "years": 17
    },
    "Damian Lillard": {
        "pts": 20580, "reb": 3720, "ast": 6350, "stl": 745, "blk": 220, "3pm": 2804,
        "ranking": {"3pm": 5},
        "years": 14
    },
    "Kyrie Irving": {
        "pts": 18820, "reb": 3320, "ast": 5120, "stl": 860, "blk": 310, "3pm": 1780,
        "ranking": {"3pm": 17},
        "years": 14
    },
    "Anthony Davis": {
        "pts": 16250, "reb": 7250, "ast": 2320, "stl": 920, "blk": 1750, "blk_rank": 15,
        "ranking": {"blk": 15},
        "years": 13
    },
    "Nikola Jokic": {
        "pts": 14320, "reb": 7820, "ast": 6120, "stl": 850, "blk": 530, "3pm": 560,
        "ranking": {},
        "years": 10
    },
    "Giannis Antetokounmpo": {
        "pts": 17650, "reb": 7620, "ast": 4280, "stl": 850, "blk": 1210, "3pm": 410,
        "ranking": {},
        "years": 12
    },
    "Jayson Tatum": {
        "pts": 13120, "reb": 4050, "ast": 2620, "stl": 680, "blk": 510, "3pm": 1350,
        "ranking": {},
        "years": 8
    },
    "Luka Doncic": {
        "pts": 11580, "reb": 3620, "ast": 4320, "stl": 650, "blk": 210, "3pm": 1120,
        "ranking": {},
        "years": 7
    },
    "Trae Young": {
        "pts": 11120, "reb": 1920, "ast": 5180, "stl": 510, "blk": 95, "3pm": 1210,
        "ranking": {},
        "years": 7
    },
    "Devin Booker": {
        "pts": 14250, "reb": 2920, "ast": 3820, "stl": 540, "blk": 190, "3pm": 1150,
        "ranking": {},
        "years": 10
    },
    "Donovan Mitchell": {
        "pts": 11520, "reb": 2680, "ast": 3020, "stl": 760, "blk": 230, "3pm": 1320,
        "ranking": {},
        "years": 8
    },
    "Paul George": {
        "pts": 17620, "reb": 5520, "ast": 3820, "stl": 1380, "blk": 490, "3pm": 2180,
        "ranking": {"3pm": 11},
        "years": 15
    },
    "Jimmy Butler": {
        "pts": 15820, "reb": 4820, "ast": 3680, "stl": 1180, "blk": 440, "3pm": 590,
        "ranking": {},
        "years": 14
    },
    "Kawhi Leonard": {
        "pts": 13420, "reb": 4620, "ast": 2520, "stl": 1210, "blk": 480, "3pm": 820,
        "ranking": {},
        "years": 14
    },
    "DeMar DeRozan": {
        "pts": 25572, "reb": 4520, "ast": 5420, "stl": 910, "blk": 310, "3pm": 420,
        "ranking": {"pts": 25},
        "years": 16
    },
    "Klay Thompson": {
        "pts": 15620, "reb": 2820, "ast": 2120, "stl": 620, "blk": 420, "3pm": 2869,
        "ranking": {"3pm": 4},
        "years": 14
    },
    "Buddy Hield": {
        "pts": 9820, "reb": 2420, "ast": 1620, "stl": 580, "blk": 220, "3pm": 2050,
        "ranking": {"3pm": 12},
        "years": 9
    },
}

# =============================================================================
# MAJOR MILESTONE THRESHOLDS
# =============================================================================

MILESTONE_THRESHOLDS = {
    "pts": [10000, 15000, 20000, 25000, 30000, 35000, 40000],
    "reb": [5000, 7500, 10000, 12500, 15000],
    "ast": [3000, 5000, 7500, 10000, 12000, 15000],
    "stl": [1000, 1500, 2000, 2500, 3000],
    "blk": [1000, 1500, 2000, 2500, 3000],
    "3pm": [1000, 1500, 2000, 2500, 3000, 3500, 4000],
}

STAT_DISPLAY_NAMES = {
    "pts": "career points",
    "reb": "career rebounds",
    "ast": "career assists",
    "stl": "career steals",
    "blk": "career blocks",
    "3pm": "career 3-pointers",
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_all_time_list(stat: str) -> List[Tuple]:
    """Get the all-time leaders list for a stat category."""
    lists = {
        "pts": ALL_TIME_POINTS,
        "reb": ALL_TIME_REBOUNDS,
        "ast": ALL_TIME_ASSISTS,
        "stl": ALL_TIME_STEALS,
        "blk": ALL_TIME_BLOCKS,
        "3pm": ALL_TIME_3PM,
    }
    return lists.get(stat, [])


def find_player_ranking(player_name: str, stat: str) -> Optional[Tuple[int, int, str]]:
    """
    Find a player's ranking in an all-time list.
    
    Returns: (rank, total, next_player_name) or None
    """
    all_time = get_all_time_list(stat)
    
    for i, (rank, name, total, is_active) in enumerate(all_time):
        if name.lower() == player_name.lower():
            # Find next player to pass (player above them)
            next_player = all_time[i-1][1] if i > 0 else None
            next_total = all_time[i-1][2] if i > 0 else None
            return (rank, total, next_player, next_total)
    
    return None


def get_milestone_info(player_name: str) -> List[Dict]:
    """
    Get all milestone information for a player.
    
    Returns list of milestone dicts:
    - All-time rankings they hold
    - Players they're close to passing
    - Round number milestones they're approaching
    """
    # Normalize name
    normalized = player_name.strip()
    
    # Get career stats
    career = ACTIVE_CAREER_STATS.get(normalized)
    if not career:
        return []
    
    milestones = []
    
    # Check each stat category
    for stat in ["pts", "ast", "3pm", "reb", "stl", "blk"]:
        current = career.get(stat, 0)
        if not current:
            continue
        
        all_time = get_all_time_list(stat)
        stat_name = STAT_DISPLAY_NAMES.get(stat, stat)
        
        # 1. Check if in TOP 25 all-time
        for rank, name, total, is_active in all_time[:25]:
            if name.lower() == normalized.lower():
                # Special case for #1
                if rank == 1:
                    milestones.append({
                        "type": "record_holder",
                        "stat": stat,
                        "stat_display": stat_name,
                        "rank": 1,
                        "total": current,
                        "headline": f"NBA ALL-TIME LEADER",
                        "description": f"#{rank} all-time in {stat_name} ({current:,})",
                        "severity": 10
                    })
                else:
                    milestones.append({
                        "type": "all_time_ranking",
                        "stat": stat,
                        "stat_display": stat_name,
                        "rank": rank,
                        "total": current,
                        "headline": f"TOP {rank} ALL-TIME",
                        "description": f"#{rank} all-time in {stat_name} ({current:,})",
                        "severity": 9 if rank <= 10 else 7
                    })
                break
        
        # 2. Check if close to passing someone
        for i, (rank, name, total, is_active) in enumerate(all_time):
            if name.lower() == normalized.lower():
                # Found player - check distance to next rank
                if i > 0:
                    next_rank, next_name, next_total, _ = all_time[i-1]
                    distance = next_total - current
                    
                    if 0 < distance <= 500:
                        milestones.append({
                            "type": "chasing",
                            "stat": stat,
                            "stat_display": stat_name,
                            "current_rank": rank,
                            "target_rank": next_rank,
                            "target_name": next_name,
                            "distance": distance,
                            "headline": f"CLOSING IN",
                            "description": f"{distance:,} {stat_name.split()[-1]} from passing {next_name} for #{next_rank}",
                            "severity": 8
                        })
                break
        
        # 3. Check for round number milestones approaching
        thresholds = MILESTONE_THRESHOLDS.get(stat, [])
        for threshold in thresholds:
            distance = threshold - current
            if 0 < distance <= 300:
                milestones.append({
                    "type": "round_milestone",
                    "stat": stat,
                    "stat_display": stat_name,
                    "current": current,
                    "target": threshold,
                    "distance": distance,
                    "headline": f"{threshold//1000}K WATCH",
                    "description": f"{distance:,} away from {threshold:,} {stat_name}",
                    "severity": 7
                })
                break  # Only show next milestone per category
    
    return milestones


def get_best_milestone(player_name: str) -> Optional[Dict]:
    """Get the single most significant milestone for a player."""
    milestones = get_milestone_info(player_name)
    
    if not milestones:
        return None
    
    # Sort by severity (highest first), then by type priority
    type_priority = {"record_holder": 0, "all_time_ranking": 1, "chasing": 2, "round_milestone": 3}
    
    sorted_milestones = sorted(
        milestones,
        key=lambda x: (-x.get("severity", 0), type_priority.get(x.get("type"), 99))
    )
    
    return sorted_milestones[0] if sorted_milestones else None


def get_career_stats(player_name: str) -> Dict:
    """Get full career stats for a player."""
    return ACTIVE_CAREER_STATS.get(player_name.strip(), {})
