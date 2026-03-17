"""
Anchor-Based Tier Classification Service
=========================================
PRIZEPICKS ANCHOR-BASED CLASSIFICATION

Uses the PrizePicks "Standard Line" as the baseline anchor.
All alternate lines are classified relative to this anchor:

- Alternate Line > Standard Line -> DEMON (Red) - Hard over
- Alternate Line < Standard Line -> GOBLIN (Green) - Easy over  
- Alternate Line == Standard Line -> STANDARD (Gray)

This logic OVERRIDES any is_demon/is_goblin flags from the Odds API.
"""
import logging
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize player name for matching."""
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "")
    for suffix in [" jr", " sr", " ii", " iii", " iv", " v"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
    return normalized


def classify_props_by_anchor(props: List[Dict]) -> List[Dict]:
    """
    Apply anchor-based classification to all props.
    
    For each player/stat combination:
    1. Find the Standard Line (is_alternate_market=False)
    2. Compare all alternates to this standard
    3. Set is_demon/is_goblin based on comparison
    
    Args:
        props: List of prop dictionaries
        
    Returns:
        Props list with updated tier classifications
    """
    if not props:
        return props
    
    # Group props by player + stat type
    groups = defaultdict(list)
    
    for prop in props:
        player_name = prop.get("player_name", "")
        stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").replace("_alternate", "")
        
        # Normalize stat type
        stat_key = stat_type.upper() if stat_type else "UNKNOWN"
        
        # Create group key
        key = f"{_normalize_name(player_name)}|{stat_key}"
        groups[key].append(prop)
    
    # Process each group
    classified_props = []
    
    for key, group_props in groups.items():
        # Find the standard line (anchor)
        standard_lines = [p for p in group_props if not p.get("is_alternate_market", True)]
        
        if not standard_lines:
            # No standard line found - use lowest line as proxy anchor
            # Sort by line value
            sorted_props = sorted(group_props, key=lambda x: x.get("line", 0) or 0)
            if sorted_props:
                anchor_line = sorted_props[0].get("line")
                logger.debug(f"[ANCHOR] No standard line for {key}, using lowest: {anchor_line}")
            else:
                anchor_line = None
        else:
            # Use the standard line as anchor
            # If multiple standards (Over/Under), use the Over line
            over_standards = [p for p in standard_lines if p.get("direction", "").lower() == "over"]
            anchor_line = (over_standards[0] if over_standards else standard_lines[0]).get("line")
        
        # Classify each prop relative to anchor
        for prop in group_props:
            prop_line = prop.get("line")
            is_alternate = prop.get("is_alternate_market", False)
            
            if anchor_line is None or prop_line is None:
                # No anchor - keep as standard
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "no_anchor"
                prop["anchor_line"] = None
            elif not is_alternate:
                # This IS the standard line
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "anchor"
                prop["anchor_line"] = anchor_line
            elif prop_line > anchor_line:
                # Alternate ABOVE standard = DEMON (harder over)
                prop["is_demon"] = True
                prop["is_goblin"] = False
                prop["tier_label"] = "DEMON"
                prop["tier_source"] = "anchor_classification"
                prop["anchor_line"] = anchor_line
                prop["diff_from_anchor"] = round(((prop_line - anchor_line) / anchor_line) * 100, 1) if anchor_line > 0 else 0
            elif prop_line < anchor_line:
                # Alternate BELOW standard = GOBLIN (easier over)
                prop["is_demon"] = False
                prop["is_goblin"] = True
                prop["tier_label"] = "GOBLIN"
                prop["tier_source"] = "anchor_classification"
                prop["anchor_line"] = anchor_line
                prop["diff_from_anchor"] = round(((prop_line - anchor_line) / anchor_line) * 100, 1) if anchor_line > 0 else 0
            else:
                # Equal to anchor
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "anchor_equal"
                prop["anchor_line"] = anchor_line
            
            classified_props.append(prop)
    
    # Log summary
    demons = sum(1 for p in classified_props if p.get("is_demon"))
    goblins = sum(1 for p in classified_props if p.get("is_goblin"))
    standards = len(classified_props) - demons - goblins
    
    logger.info(f"[ANCHOR_CLASSIFY] {len(classified_props)} props: {demons} demons, {goblins} goblins, {standards} standard")
    
    return classified_props


def group_props_by_player(props: List[Dict]) -> Dict[str, Dict]:
    """
    Group props by player for Universal Card display.
    
    Each player gets ONE card with:
    - Player identity (name, team, headshot from Vault)
    - All their props (standard + demons + goblins from Odds)
    
    Args:
        props: List of classified props
        
    Returns:
        Dict mapping player_name -> player data with grouped props
    """
    players = {}
    
    for prop in props:
        player_name = prop.get("player_name", "Unknown")
        
        if player_name not in players:
            players[player_name] = {
                "player_name": player_name,
                "team": prop.get("home_team") if prop.get("home_team") else prop.get("away_team"),
                "opponent": None,
                "props": [],
                "demons": [],
                "goblins": [],
                "standards": []
            }
        
        player = players[player_name]
        player["props"].append(prop)
        
        if prop.get("is_demon"):
            player["demons"].append(prop)
        elif prop.get("is_goblin"):
            player["goblins"].append(prop)
        else:
            player["standards"].append(prop)
        
        # Set opponent
        if not player["opponent"]:
            home = prop.get("home_team")
            away = prop.get("away_team")
            team = player["team"]
            if team == home:
                player["opponent"] = away
            elif team == away:
                player["opponent"] = home
    
    return players


def get_anchor_line_for_stat(props: List[Dict], player_name: str, stat_type: str) -> Optional[float]:
    """
    Get the standard (anchor) line for a specific player/stat combination.
    
    Args:
        props: List of all props
        player_name: Player name to find
        stat_type: Stat type (PTS, AST, etc.)
        
    Returns:
        The anchor line value or None if not found
    """
    normalized_player = _normalize_name(player_name)
    stat_upper = stat_type.upper() if stat_type else ""
    
    for prop in props:
        prop_player = _normalize_name(prop.get("player_name", ""))
        prop_stat = (prop.get("stat_type_extracted") or "").upper()
        
        if prop_player == normalized_player and prop_stat == stat_upper:
            if not prop.get("is_alternate_market", True):
                return prop.get("line")
    
    return None
