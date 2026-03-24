"""
Anchor-Based Tier Classification Service
=========================================
PRIZEPICKS DEMON/GOBLIN CLASSIFICATION

Uses PrizePicks' actual classification system based on:
1. The Standard Line as the anchor
2. The odds/price to determine tier

Classification Rules:
- DEMON (Red): Alternate line ABOVE standard + odds >= +100
- GOBLIN (Green): Alternate line BELOW standard + odds < 0 (-137)
- STANDARD (Gray): The main/standard line itself

FALLBACK (No Standard Line):
When no standard line exists:
1. Use player's L10/L5 average as anchor:
   - Line > player avg → DEMON
   - Line < player avg → GOBLIN
2. If no player stats, classify by odds alone:
   - odds >= +100 → DEMON (harder line, boosted)
   - odds < 0 → GOBLIN (easier line, discounted)

This matches how PrizePicks labels their picks while ensuring
betting relevance when standard lines are missing.
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


def classify_props_by_anchor(props: List[Dict], player_stats: Dict[str, Dict] = None) -> List[Dict]:
    """
    Apply PrizePicks-style classification to all props.
    
    Classification Logic:
    1. Find the Standard Line (is_alternate_market=False) - this is the ANCHOR
    2. For alternate lines:
       - Line > Standard AND odds >= +100 → DEMON
       - Line < Standard AND odds < 0 → GOBLIN
    3. If no standard line exists:
       - odds >= +100 → DEMON
       - odds < 0 → GOBLIN
    
    Args:
        props: List of prop dictionaries
        player_stats: Optional (not used in new PrizePicks-based classification)
        
    Returns:
        Props list with updated tier classifications
    """
    if not props:
        return props
    
    # Build player_stats lookup from props if not provided
    if player_stats is None:
        player_stats = {}
        for prop in props:
            player_name = prop.get("player_name", "")
            stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").replace("_alternate", "")
            stat_key = stat_type.upper() if stat_type else "UNKNOWN"
            key = f"{_normalize_name(player_name)}|{stat_key}"
            
            # Extract L5 avg from prop if available
            l5_avg = prop.get("l5_avg")
            season_avg = prop.get("season_avg")
            
            if key not in player_stats and (l5_avg or season_avg):
                player_stats[key] = {
                    "l5_avg": l5_avg,
                    "season_avg": season_avg
                }
    
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
    standard_count = 0
    odds_fallback_count = 0
    no_anchor_count = 0
    
    for key, group_props in groups.items():
        # Find the standard line (anchor) - non-alternate market
        standard_props = [p for p in group_props if not p.get("is_alternate_market", True)]
        alternate_props = [p for p in group_props if p.get("is_alternate_market", True)]
        
        anchor_line = None
        anchor_source = None
        
        if standard_props:
            # Get unique standard line values
            standard_line_values = list(set(p.get("line") for p in standard_props if p.get("line")))
            
            if len(standard_line_values) == 1:
                # Only one standard line - use it
                anchor_line = standard_line_values[0]
            elif len(standard_line_values) > 1:
                # Multiple "standard" lines - find the TRUE standard
                # The real standard should have alternate lines BOTH above AND below it
                # If not, pick the middle/highest value as the standard
                
                alternate_lines = [p.get("line") for p in alternate_props if p.get("line")]
                
                best_standard = None
                for std_line in sorted(standard_line_values, reverse=True):
                    has_above = any(alt > std_line for alt in alternate_lines)
                    has_below = any(alt < std_line for alt in alternate_lines)
                    
                    if has_above and has_below:
                        # This standard has alternates both above and below - likely the true standard
                        best_standard = std_line
                        break
                
                if best_standard:
                    anchor_line = best_standard
                    logger.debug(f"[ANCHOR] {key}: multiple standards {standard_line_values}, picked {anchor_line} (has alts above/below)")
                else:
                    # No standard has alternates both ways - use the highest standard
                    anchor_line = max(standard_line_values)
                    logger.debug(f"[ANCHOR] {key}: multiple standards {standard_line_values}, picked highest {anchor_line}")
            
            if anchor_line:
                anchor_source = "standard_line"
                standard_count += 1
                logger.debug(f"[ANCHOR] {key}: standard_line = {anchor_line}")
        
        if not anchor_line:
            # No standard line - will classify by odds alone
            anchor_source = "odds_only"
            odds_fallback_count += 1
            logger.debug(f"[ANCHOR] {key}: no standard line, using odds-based classification")
        
        # =================================================================
        # PRIZEPICKS CLASSIFICATION LOGIC
        # =================================================================
        # DEMON: Alternate line ABOVE standard + odds >= +100
        # GOBLIN: Alternate line BELOW standard + odds < 0 (like -137)
        # STANDARD: The main line itself (non-alternate)
        #
        # No standard line? Use odds alone:
        # - odds >= +100 → DEMON (boosted/harder)
        # - odds < 0 → GOBLIN (discounted/easier)
        # =================================================================
        
        for prop in group_props:
            prop_line = prop.get("line")
            is_alternate = prop.get("is_alternate_market", False)
            price = prop.get("price", 0)  # Odds like +100 or -137
            
            # Store anchor info
            prop["anchor_line"] = anchor_line
            prop["anchor_source"] = anchor_source
            
            if not is_alternate:
                # This IS the standard line
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "standard_line"
                
            elif anchor_line is not None and prop_line is not None:
                # We have a standard line to compare against
                # DEMON: Line > Standard AND odds >= +100
                # GOBLIN: Line < Standard AND odds < 0
                
                if prop_line > anchor_line and price >= 100:
                    prop["is_demon"] = True
                    prop["is_goblin"] = False
                    prop["tier_label"] = "DEMON"
                    prop["tier_source"] = "above_standard_boosted"
                elif prop_line < anchor_line and price < 0:
                    prop["is_demon"] = False
                    prop["is_goblin"] = True
                    prop["tier_label"] = "GOBLIN"
                    prop["tier_source"] = "below_standard_discounted"
                elif prop_line > anchor_line:
                    # Above standard but not +100 odds - still treat as demon
                    prop["is_demon"] = True
                    prop["is_goblin"] = False
                    prop["tier_label"] = "DEMON"
                    prop["tier_source"] = "above_standard"
                elif prop_line < anchor_line:
                    # Below standard but not -137 odds - still treat as goblin
                    prop["is_demon"] = False
                    prop["is_goblin"] = True
                    prop["tier_label"] = "GOBLIN"
                    prop["tier_source"] = "below_standard"
                else:
                    # Line equals standard
                    prop["is_demon"] = False
                    prop["is_goblin"] = False
                    prop["tier_label"] = "STANDARD"
                    prop["tier_source"] = "equals_standard"
                    
            else:
                # No standard line - classify by ODDS (PrizePicks' actual system)
                # +100 or higher = DEMON (boosted/harder line)
                # Negative odds = GOBLIN (discounted/easier line)
                
                if price >= 100:
                    prop["is_demon"] = True
                    prop["is_goblin"] = False
                    prop["tier_label"] = "DEMON"
                    prop["tier_source"] = "odds_boosted"
                elif price < 0:
                    prop["is_demon"] = False
                    prop["is_goblin"] = True
                    prop["tier_label"] = "GOBLIN"
                    prop["tier_source"] = "odds_discounted"
                else:
                    # Edge case: odds = 0 or missing
                    prop["is_demon"] = False
                    prop["is_goblin"] = False
                    prop["tier_label"] = "STANDARD"
                    prop["tier_source"] = "no_classification"
                    no_anchor_count += 1
            
            classified_props.append(prop)
    
    logger.info(f"[ANCHOR_CLASSIFY] Processed {len(classified_props)} props: "
                f"{standard_count} with standard lines, {odds_fallback_count} odds-only, "
                f"{no_anchor_count} unclassified")
    
    # Log summary
    demons = sum(1 for p in classified_props if p.get("is_demon"))
    goblins = sum(1 for p in classified_props if p.get("is_goblin"))
    standards = len(classified_props) - demons - goblins
    
    logger.info(f"[ANCHOR_CLASSIFY] Result: {demons} demons, {goblins} goblins, {standards} standard")
    
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
