"""
MLB Matchup Math Service
========================
Calculates split matchup analysis for MLB props:

1. HITTER PROPS (The Pitching Gauntlet):
   - vs. Starting Pitcher: xFIP-based rank (1-30)
   - vs. Bullpen: ERA-based rank (1-30)
   - Overall Edge calculation

2. PITCHER PROPS (The Discipline Check):
   - Lineup K-Rate: How easy to strike out (rank 1=hardest, 30=easiest)
   - Lineup wRC+: Offensive threat level
   - Overall Edge calculation

Rank Labels:
- 1-10: "Brutal" (tough matchup)
- 11-20: "Medium" (neutral)
- 21-30: "Easy" (favorable matchup)
"""

import logging
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

# ==================== TEAM PITCHING RANKINGS (xFIP-based) ====================
# Lower xFIP = Better pitching = Harder for hitters
# Rankings: 1 = Best pitching (hardest), 30 = Worst pitching (easiest)
TEAM_SP_XFIP_RANK = {
    # Elite (1-10) - Brutal
    "ATL": 2, "LAD": 1, "PHI": 4, "TEX": 5, "BAL": 3,
    "MIL": 6, "TB": 7, "MIN": 8, "SEA": 9, "HOU": 10,
    # Average (11-20) - Medium
    "ARI": 11, "CLE": 12, "NYY": 13, "DET": 14, "SD": 15,
    "SF": 16, "BOS": 17, "KC": 18, "CIN": 19, "STL": 20,
    # Weak (21-30) - Easy
    "TOR": 21, "NYM": 22, "CHC": 23, "PIT": 24, "LAA": 25,
    "COL": 26, "OAK": 27, "MIA": 28, "CWS": 29, "WAS": 30
}

TEAM_BULLPEN_ERA_RANK = {
    # Elite (1-10) - Brutal
    "LAD": 1, "ATL": 2, "PHI": 3, "NYY": 4, "CLE": 5,
    "MIL": 6, "BAL": 7, "TB": 8, "HOU": 9, "SD": 10,
    # Average (11-20) - Medium
    "SEA": 11, "MIN": 12, "TEX": 13, "ARI": 14, "SF": 15,
    "KC": 16, "BOS": 17, "DET": 18, "STL": 19, "CIN": 20,
    # Weak (21-30) - Easy
    "TOR": 21, "CHC": 22, "NYM": 23, "PIT": 24, "MIA": 25,
    "LAA": 26, "OAK": 27, "COL": 28, "WAS": 29, "CWS": 30
}

# ==================== TEAM LINEUP RANKINGS ====================
# Lineup K-Rate: 1 = Hardest to strike out, 30 = Easiest to strike out (swings freely)
TEAM_LINEUP_K_RATE_RANK = {
    # Hardest to K (1-10) - Brutal for pitcher K props
    "LAD": 1, "NYY": 2, "ATL": 3, "SD": 4, "CLE": 5,
    "HOU": 6, "PHI": 7, "ARI": 8, "MIN": 9, "TB": 10,
    # Average (11-20) - Medium
    "TEX": 11, "MIL": 12, "SF": 13, "BOS": 14, "BAL": 15,
    "SEA": 16, "KC": 17, "STL": 18, "DET": 19, "CIN": 20,
    # Easiest to K (21-30) - Easy for pitcher K props
    "TOR": 21, "NYM": 22, "CHC": 23, "PIT": 24, "MIA": 25,
    "LAA": 26, "COL": 27, "OAK": 28, "WAS": 29, "CWS": 30
}

# Lineup wRC+: 1 = Best offense, 30 = Worst offense
TEAM_LINEUP_WRC_RANK = {
    # Elite Offense (1-10) - Dangerous for pitchers
    "LAD": 1, "NYY": 2, "ATL": 3, "PHI": 4, "BAL": 5,
    "ARI": 6, "TEX": 7, "SD": 8, "HOU": 9, "CLE": 10,
    # Average (11-20) - Medium
    "MIL": 11, "SEA": 12, "MIN": 13, "TB": 14, "SF": 15,
    "BOS": 16, "KC": 17, "DET": 18, "STL": 19, "CIN": 20,
    # Weak Offense (21-30) - Favorable for pitchers
    "TOR": 21, "NYM": 22, "CHC": 23, "PIT": 24, "MIA": 25,
    "LAA": 26, "COL": 27, "OAK": 28, "WAS": 29, "CWS": 30
}

# ==================== HELPER FUNCTIONS ====================

def get_rank_label(rank: int) -> str:
    """Convert rank to human-readable label."""
    if rank <= 10:
        return "Brutal"
    elif rank <= 20:
        return "Medium"
    else:
        return "Easy"

def get_rank_color(rank: int) -> str:
    """Get color class for rank display."""
    if rank <= 10:
        return "red"  # Brutal
    elif rank <= 20:
        return "amber"  # Medium
    else:
        return "green"  # Easy

def calculate_edge_from_ranks(ranks: list, weights: list = None) -> float:
    """
    Calculate overall edge from multiple ranks.
    Higher positive = advantage for the bettor's side
    """
    if not ranks:
        return 0.0
    
    if weights is None:
        weights = [1.0 / len(ranks)] * len(ranks)
    
    # Normalize weights
    total_weight = sum(weights)
    weights = [w / total_weight for w in weights]
    
    # Calculate weighted average rank
    weighted_rank = sum(r * w for r, w in zip(ranks, weights))
    
    # Convert to edge: rank 15 = 0%, rank 1 = -20%, rank 30 = +20%
    edge = (weighted_rank - 15.5) * (40 / 29)  # Scale from -20% to +20%
    return round(edge, 1)


# ==================== HITTER MATCHUP (vs Pitching Gauntlet) ====================

def get_hitter_matchup_analysis(
    opponent_team: str,
    starting_pitcher_name: Optional[str] = None,
    sp_override_rank: Optional[int] = None
) -> Dict[str, Any]:
    """
    Analyze hitter's matchup against opposing pitching.
    
    Returns:
        {
            "prop_type": "hitter",
            "sp_matchup": {
                "pitcher_name": "Skenes",
                "rank": 2,
                "label": "Brutal",
                "color": "red"
            },
            "bullpen_matchup": {
                "team": "PIT",
                "rank": 24,
                "label": "Easy",
                "color": "green"
            },
            "overall_edge": -5.0,
            "edge_label": "Slight Disadvantage"
        }
    """
    # Get SP rank (from override or team average)
    sp_rank = sp_override_rank or TEAM_SP_XFIP_RANK.get(opponent_team, 15)
    
    # Get bullpen rank
    bullpen_rank = TEAM_BULLPEN_ERA_RANK.get(opponent_team, 15)
    
    # Calculate overall edge (SP weighted 60%, Bullpen 40%)
    overall_edge = calculate_edge_from_ranks([sp_rank, bullpen_rank], [0.6, 0.4])
    
    # Determine edge label
    if overall_edge <= -10:
        edge_label = "Significant Disadvantage"
    elif overall_edge < 0:
        edge_label = "Slight Disadvantage"
    elif overall_edge == 0:
        edge_label = "Neutral"
    elif overall_edge <= 10:
        edge_label = "Slight Advantage"
    else:
        edge_label = "Strong Advantage"
    
    return {
        "prop_type": "hitter",
        "sp_matchup": {
            "pitcher_name": starting_pitcher_name or "Unknown",
            "rank": sp_rank,
            "label": get_rank_label(sp_rank),
            "color": get_rank_color(sp_rank)
        },
        "bullpen_matchup": {
            "team": opponent_team,
            "rank": bullpen_rank,
            "label": get_rank_label(bullpen_rank),
            "color": get_rank_color(bullpen_rank)
        },
        "overall_edge": overall_edge,
        "edge_label": edge_label
    }


# ==================== PITCHER MATCHUP (vs Lineup Discipline) ====================

def get_pitcher_matchup_analysis(opponent_team: str) -> Dict[str, Any]:
    """
    Analyze pitcher's matchup against opposing lineup.
    
    For K-props: Rank 1 = Hardest to K (bad), Rank 30 = Easiest (good)
    For Outs/ER: Consider overall offensive threat
    
    Returns:
        {
            "prop_type": "pitcher",
            "k_rate_matchup": {
                "rank": 4,
                "label": "Brutal",  # For K props this means hard to strike out
                "note": "Swings Freely",
                "color": "red"
            },
            "wrc_matchup": {
                "rank": 14,
                "label": "Medium",
                "color": "amber"
            },
            "overall_edge": 12.0,
            "edge_label": "Advantage"
        }
    """
    # Get lineup K-rate rank (flipped for K props - high rank = easy to K = good)
    k_rate_rank = TEAM_LINEUP_K_RATE_RANK.get(opponent_team, 15)
    
    # Get lineup offensive threat (wRC+)
    wrc_rank = TEAM_LINEUP_WRC_RANK.get(opponent_team, 15)
    
    # For pitcher props, we FLIP the K-rate interpretation
    # K-props: Low rank (hard to K) = bad, High rank (easy to K) = good
    # So for edge calculation, we flip it: rank 1 -> 30, rank 30 -> 1
    flipped_k_rank = 31 - k_rate_rank
    
    # Calculate overall edge (K-rate weighted 50%, wRC+ weighted 50% for strikeout props)
    overall_edge = calculate_edge_from_ranks([flipped_k_rank, wrc_rank], [0.5, 0.5])
    
    # Determine edge label
    if overall_edge <= -10:
        edge_label = "Significant Disadvantage"
    elif overall_edge < 0:
        edge_label = "Slight Disadvantage"
    elif overall_edge == 0:
        edge_label = "Neutral"
    elif overall_edge <= 10:
        edge_label = "Slight Advantage"
    else:
        edge_label = "Strong Advantage"
    
    # K-rate note (what it means for K props)
    if k_rate_rank <= 10:
        k_note = "Rarely Whiffs"  # Hard to strike out
    elif k_rate_rank <= 20:
        k_note = "Average Discipline"
    else:
        k_note = "Swings Freely"  # Easy to strike out
    
    return {
        "prop_type": "pitcher",
        "k_rate_matchup": {
            "rank": k_rate_rank,
            "label": get_rank_label(k_rate_rank),
            "note": k_note,
            "color": get_rank_color(k_rate_rank)
        },
        "wrc_matchup": {
            "rank": wrc_rank,
            "label": get_rank_label(wrc_rank),
            "color": get_rank_color(wrc_rank)
        },
        "overall_edge": overall_edge,
        "edge_label": edge_label
    }


# ==================== MAIN DISPATCHER ====================

def get_mlb_matchup_analysis(
    stat_type: str,
    opponent_team: str,
    starting_pitcher_name: Optional[str] = None,
    sp_xfip_rank: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main entry point for MLB matchup analysis.
    
    Automatically detects if prop is for a hitter or pitcher based on stat_type.
    
    Args:
        stat_type: The stat type (HITS, K, TB, ER, OUTS, etc.)
        opponent_team: 3-letter team code (PIT, LAD, etc.)
        starting_pitcher_name: For hitter props, the opposing SP name
        sp_xfip_rank: Optional override for SP rank
    
    Returns:
        Dict with matchup analysis (varies by prop type)
    """
    # Normalize stat type - convert spaces to underscores and uppercase
    stat_normalized = (stat_type or "").upper().replace(" ", "_").replace("-", "_")
    
    # Pitcher stats (from the pitcher's perspective)
    PITCHER_STATS = {"K", "OUTS", "ER", "PITCHER_STRIKEOUTS", "EARNED_RUNS", "PITCHING_OUTS",
                     "STRIKEOUTS", "WALKS_ALLOWED", "HITS_ALLOWED"}
    
    if stat_normalized in PITCHER_STATS:
        return get_pitcher_matchup_analysis(opponent_team)
    else:
        return get_hitter_matchup_analysis(
            opponent_team,
            starting_pitcher_name=starting_pitcher_name,
            sp_override_rank=sp_xfip_rank
        )


# ==================== BATCH ENRICHMENT ====================

def enrich_props_with_matchup(props: list, opponent_team: str = None) -> list:
    """
    Enrich a list of props with matchup analysis.
    
    Args:
        props: List of prop dicts with stat_type field
        opponent_team: Default opponent team if not in prop
    
    Returns:
        Props with 'matchup_analysis' field added
    """
    enriched = []
    for prop in props:
        try:
            stat_type = prop.get("stat_type", "")
            opp = prop.get("opponent_team") or prop.get("opponent") or opponent_team
            sp_name = prop.get("opposing_pitcher") or prop.get("sp_name")
            
            if opp:
                matchup = get_mlb_matchup_analysis(
                    stat_type=stat_type,
                    opponent_team=opp,
                    starting_pitcher_name=sp_name
                )
                prop["matchup_analysis"] = matchup
            else:
                prop["matchup_analysis"] = None
        except Exception as e:
            logger.warning(f"Failed to enrich prop with matchup: {e}")
            prop["matchup_analysis"] = None
        
        enriched.append(prop)
    
    return enriched


# ==================== SINGLETON INSTANCE ====================

_mlb_matchup_instance = None

def get_mlb_matchup_service():
    """Get singleton instance of MLB matchup service."""
    global _mlb_matchup_instance
    if _mlb_matchup_instance is None:
        _mlb_matchup_instance = {
            "get_analysis": get_mlb_matchup_analysis,
            "enrich_props": enrich_props_with_matchup,
            "get_hitter_matchup": get_hitter_matchup_analysis,
            "get_pitcher_matchup": get_pitcher_matchup_analysis
        }
    return _mlb_matchup_instance
