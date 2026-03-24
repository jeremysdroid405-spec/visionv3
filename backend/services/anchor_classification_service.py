"""
Anchor-Based Tier Classification Service
=========================================
PRIZEPICKS ANCHOR-BASED CLASSIFICATION

Uses the PrizePicks "Standard Line" as the baseline anchor.
All alternate lines are classified relative to this anchor:

- Alternate Line > Standard Line -> DEMON (Red)
- Alternate Line < Standard Line -> GOBLIN (Green)  
- Standard Line itself -> STANDARD (Gray)

FALLBACK FOR PLAYERS WITHOUT MAIN LINE:
When a player only has alternate markets (no standard line),
use the player's L5 average as the anchor:

- Line > L5 Avg -> DEMON (Red)
- Line < L5 Avg -> GOBLIN (Green)
- Line == L5 Avg -> STANDARD (Gray)

ALL bets (over AND under) are classified this way.
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


def classify_props_by_anchor(props: List[Dict], player_stats: Dict[str, Dict] = None) -> List[Dict]:
    """
    Apply anchor-based classification to all props.
    
    For each player/stat combination:
    1. Find the Standard Line (is_alternate_market=False) - this is the ANCHOR
    2. If no standard line exists, use L5 average as the fallback anchor
    3. Compare all lines to the anchor
    4. Set is_demon/is_goblin based on comparison
    
    Args:
        props: List of prop dictionaries
        player_stats: Optional dict mapping "player_name|STAT" -> {"l5_avg": X, "season_avg": Y}
                     Used as fallback anchor when no main line exists
        
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
    l5_fallback_count = 0
    no_anchor_count = 0
    
    for key, group_props in groups.items():
        # Find the standard line (anchor) - non-alternate market
        standard_lines = [p for p in group_props if not p.get("is_alternate_market", True)]
        
        anchor_line = None
        anchor_source = None
        main_line_value = None
        
        if standard_lines:
            # Get the odds provider's main line (for reference)
            over_standards = [p for p in standard_lines if p.get("direction", "").lower() == "over"]
            main_line_value = (over_standards[0] if over_standards else standard_lines[0]).get("line")
        
        # Get player's averages (PREFERRED anchor for betting relevance)
        stats = player_stats.get(key, {})
        l5_avg = stats.get("l5_avg")
        l10_avg = stats.get("l10_avg")
        season_avg = stats.get("season_avg")
        
        # =================================================================
        # ANCHOR SELECTION LOGIC (PERMANENT FIX - March 2026)
        # =================================================================
        # For BETTING purposes, what matters is: can the player exceed this line?
        # The odds provider's main line is their opinion, but it may be set low
        # to entice bets. We use the PLAYER'S ACTUAL AVERAGE as the anchor.
        #
        # Priority:
        # 1. L10 average (used for War Zone filtering, most representative)
        # 2. L5 average (if no L10)
        # 3. Season average (if no L5/L10)
        # 4. Main line (ONLY if no player stats available)
        #
        # This ensures that a "demon" is truly ABOVE the player's average,
        # and a "goblin" is truly BELOW the player's average.
        # =================================================================
        
        if l10_avg and l10_avg > 0:
            anchor_line = l10_avg
            anchor_source = "l10_avg"
            l5_fallback_count += 1
            logger.debug(f"[ANCHOR] {key}: using L10 avg = {anchor_line} (main_line was {main_line_value})")
        elif l5_avg and l5_avg > 0:
            anchor_line = l5_avg
            anchor_source = "l5_avg"
            l5_fallback_count += 1
            logger.debug(f"[ANCHOR] {key}: using L5 avg = {anchor_line} (main_line was {main_line_value})")
        elif season_avg and season_avg > 0:
            anchor_line = season_avg
            anchor_source = "season_avg"
            l5_fallback_count += 1
            logger.debug(f"[ANCHOR] {key}: using season avg = {anchor_line} (main_line was {main_line_value})")
        elif main_line_value:
            # Last resort: use main line if no player stats
            anchor_line = main_line_value
            anchor_source = "main_line"
            logger.debug(f"[ANCHOR] {key}: no player stats, using main_line = {anchor_line}")
        else:
            # No anchor available at all
            anchor_line = None
            anchor_source = "none"
            no_anchor_count += 1
            logger.debug(f"[ANCHOR] {key}: NO ANCHOR AVAILABLE")
        
        # Classify each prop relative to anchor
        for prop in group_props:
            prop_line = prop.get("line")
            is_alternate = prop.get("is_alternate_market", False)
            
            if anchor_line is None or prop_line is None:
                # No anchor - keep as standard (unclassified)
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "no_anchor"
                prop["anchor_line"] = None
                prop["anchor_source"] = "none"
            elif not is_alternate and anchor_source == "main_line":
                # This IS the standard line (main line anchor)
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = "is_main_line"
                prop["anchor_line"] = anchor_line
                prop["anchor_source"] = anchor_source
            elif prop_line > anchor_line:
                # Line ABOVE anchor = DEMON (harder to hit)
                prop["is_demon"] = True
                prop["is_goblin"] = False
                prop["tier_label"] = "DEMON"
                prop["tier_source"] = f"above_{anchor_source}"
                prop["anchor_line"] = anchor_line
                prop["anchor_source"] = anchor_source
                prop["diff_from_anchor"] = round(((prop_line - anchor_line) / anchor_line) * 100, 1) if anchor_line > 0 else 0
            elif prop_line < anchor_line:
                # Line BELOW anchor = GOBLIN (easier to hit)
                prop["is_demon"] = False
                prop["is_goblin"] = True
                prop["tier_label"] = "GOBLIN"
                prop["tier_source"] = f"below_{anchor_source}"
                prop["anchor_line"] = anchor_line
                prop["anchor_source"] = anchor_source
                prop["diff_from_anchor"] = round(((prop_line - anchor_line) / anchor_line) * 100, 1) if anchor_line > 0 else 0
            else:
                # Equal to anchor = STANDARD
                prop["is_demon"] = False
                prop["is_goblin"] = False
                prop["tier_label"] = "STANDARD"
                prop["tier_source"] = f"equal_{anchor_source}"
                prop["anchor_line"] = anchor_line
                prop["anchor_source"] = anchor_source
            
            classified_props.append(prop)
    
    # Log summary
    demons = sum(1 for p in classified_props if p.get("is_demon"))
    goblins = sum(1 for p in classified_props if p.get("is_goblin"))
    standards = len(classified_props) - demons - goblins
    
    logger.info(f"[ANCHOR_CLASSIFY] {len(classified_props)} props: {demons} demons, {goblins} goblins, {standards} standard")
    if l5_fallback_count > 0:
        logger.info(f"[ANCHOR_CLASSIFY] Used L5/season avg as fallback anchor for {l5_fallback_count} player/stat groups")
    if no_anchor_count > 0:
        logger.warning(f"[ANCHOR_CLASSIFY] {no_anchor_count} player/stat groups had NO anchor available")
    
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
