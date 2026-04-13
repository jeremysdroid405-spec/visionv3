"""
BDL MLB Splits Service
======================
Fetches player splits and matchup data from BallDontLie MLB API
for calculating Matchup_Modifier and Tempo_Modifier.

API Rules (STRICT):
- Base URL: https://api.balldontlie.io/mlb/v1
- Auth: {"Authorization": "API_KEY"} (NOT Bearer)

Features:
1. Matchup Modifier (L/R Splits) - GET /players/splits
2. Matchup Modifier (Batter vs Pitcher) - GET /players/versus  
3. Tempo Modifier (Batting Order) - GET /players/splits

Author: PropVision AI
Version: 1.0.0
"""

import os
import logging
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# BDL MLB API Configuration (STRICT)
BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")

# Current season
CURRENT_SEASON = 2026

# Modifier thresholds
OPS_SPLIT_BOOST_THRESHOLD = 0.850  # +5% if split OPS > .850
OPS_VERSUS_BOOST_THRESHOLD = 0.900  # +5% if historical OPS > .900
MIN_AT_BATS_FOR_VERSUS = 10  # Minimum ABs for batter vs pitcher data


def get_bdl_headers() -> Dict[str, str]:
    """
    Get BDL API headers with correct auth format.
    
    CRITICAL: BDL uses {"Authorization": "KEY"} NOT "Bearer KEY"
    """
    return {
        "Authorization": BDL_API_KEY,
        "Content-Type": "application/json"
    }


async def fetch_player_splits(
    player_id: int,
    season: int = CURRENT_SEASON
) -> Optional[Dict[str, Any]]:
    """
    Fetch player splits from BDL MLB API.
    
    Endpoint: GET /players/splits?player_id={id}&season={season}
    
    Returns:
        {
            "byBreakdown": [...],  # L/R splits
            "byBattingOrder": [...]  # Batting order splits
        }
    """
    if not BDL_API_KEY:
        logger.warning("[BDL_SPLITS] No API key configured")
        return None
    
    url = f"{BDL_MLB_BASE_URL}/players/splits"
    params = {
        "player_id": player_id,
        "season": season
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers=get_bdl_headers(),
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"[BDL_SPLITS] Got splits for player {player_id}")
                return data.get("data", data)
            elif response.status_code == 404:
                logger.debug(f"[BDL_SPLITS] No splits found for player {player_id}")
                return None
            else:
                logger.warning(f"[BDL_SPLITS] API error {response.status_code}: {response.text[:200]}")
                return None
                
    except Exception as e:
        logger.error(f"[BDL_SPLITS] Request failed for player {player_id}: {e}")
        return None


async def fetch_player_versus(
    batter_id: int,
    opponent_team_id: int
) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch batter vs pitcher matchup history from BDL MLB API.
    
    Endpoint: GET /players/versus?player_id={batter_id}&opponent_team_id={team_id}
    
    Returns:
        List of matchup objects with opponent_player info and stats
    """
    if not BDL_API_KEY:
        logger.warning("[BDL_VERSUS] No API key configured")
        return None
    
    url = f"{BDL_MLB_BASE_URL}/players/versus"
    params = {
        "player_id": batter_id,
        "opponent_team_id": opponent_team_id
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers=get_bdl_headers(),
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"[BDL_VERSUS] Got versus data for batter {batter_id} vs team {opponent_team_id}")
                return data.get("data", data)
            elif response.status_code == 404:
                logger.debug(f"[BDL_VERSUS] No versus data for batter {batter_id}")
                return None
            else:
                logger.warning(f"[BDL_VERSUS] API error {response.status_code}: {response.text[:200]}")
                return None
                
    except Exception as e:
        logger.error(f"[BDL_VERSUS] Request failed: {e}")
        return None


def calculate_lr_split_modifier(
    splits_data: Dict[str, Any],
    pitcher_hand: str  # "L" or "R"
) -> Tuple[float, Optional[Dict[str, Any]]]:
    """
    Calculate Matchup Modifier from L/R splits.
    
    Parsing Logic:
    - Search byBreakdown array for split_name = "vs. Left" or "vs. Right"
    - Extract OPS value
    - If OPS > .850, apply +5% boost
    
    Args:
        splits_data: Response from /players/splits
        pitcher_hand: "L" for left-handed, "R" for right-handed pitcher
    
    Returns:
        (modifier, split_details) - modifier is 1.0 base, 1.05 if boosted
    """
    modifier = 1.0
    split_details = None
    
    if not splits_data:
        return modifier, split_details
    
    # Determine which split to look for
    target_split = "vs. Left" if pitcher_hand == "L" else "vs. Right"
    
    # Search byBreakdown array
    by_breakdown = splits_data.get("byBreakdown", [])
    
    for split in by_breakdown:
        split_name = split.get("split_name", "")
        
        if split_name == target_split:
            ops = split.get("ops")
            
            if ops is not None:
                try:
                    ops_float = float(ops)
                    split_details = {
                        "split_name": split_name,
                        "ops": ops_float,
                        "at_bats": split.get("at_bats"),
                        "avg": split.get("avg"),
                        "slg": split.get("slg"),
                        "obp": split.get("obp")
                    }
                    
                    # Apply +5% boost if OPS > .850
                    if ops_float > OPS_SPLIT_BOOST_THRESHOLD:
                        modifier = 1.05
                        logger.debug(f"[LR_SPLIT] {target_split} OPS {ops_float:.3f} > .850 → +5% boost")
                    else:
                        logger.debug(f"[LR_SPLIT] {target_split} OPS {ops_float:.3f} ≤ .850 → no boost")
                        
                except (ValueError, TypeError):
                    logger.warning(f"[LR_SPLIT] Invalid OPS value: {ops}")
            
            break
    
    return modifier, split_details


def calculate_versus_modifier(
    versus_data: List[Dict[str, Any]],
    opposing_pitcher_id: int
) -> Tuple[float, Optional[Dict[str, Any]]]:
    """
    Calculate Matchup Modifier from Batter vs Pitcher history.
    
    Parsing Logic:
    - Find object where opponent_player.id matches today's starting pitcher
    - Extract OPS from this matchup
    - If OPS > .900 (min 10 ABs), apply +5% boost
    
    Args:
        versus_data: Response from /players/versus
        opposing_pitcher_id: BDL player ID of today's starting pitcher
    
    Returns:
        (modifier, matchup_details) - modifier is 1.0 base, 1.05 if boosted
    """
    modifier = 1.0
    matchup_details = None
    
    if not versus_data or not opposing_pitcher_id:
        return modifier, matchup_details
    
    # Search for matchup against specific pitcher
    for matchup in versus_data:
        opponent_player = matchup.get("opponent_player", {})
        
        if opponent_player.get("id") == opposing_pitcher_id:
            ops = matchup.get("ops")
            at_bats = matchup.get("at_bats", 0)
            
            if ops is not None and at_bats is not None:
                try:
                    ops_float = float(ops)
                    at_bats_int = int(at_bats)
                    
                    matchup_details = {
                        "pitcher_id": opposing_pitcher_id,
                        "pitcher_name": opponent_player.get("name") or opponent_player.get("full_name"),
                        "ops": ops_float,
                        "at_bats": at_bats_int,
                        "avg": matchup.get("avg"),
                        "hits": matchup.get("hits"),
                        "home_runs": matchup.get("home_runs")
                    }
                    
                    # Apply +5% boost if OPS > .900 AND min 10 ABs
                    if ops_float > OPS_VERSUS_BOOST_THRESHOLD and at_bats_int >= MIN_AT_BATS_FOR_VERSUS:
                        modifier = 1.05
                        logger.debug(f"[BVP] vs pitcher {opposing_pitcher_id}: OPS {ops_float:.3f} ({at_bats_int} ABs) > .900 → +5% boost")
                    else:
                        if at_bats_int < MIN_AT_BATS_FOR_VERSUS:
                            logger.debug(f"[BVP] vs pitcher {opposing_pitcher_id}: Only {at_bats_int} ABs < {MIN_AT_BATS_FOR_VERSUS} min → no boost")
                        else:
                            logger.debug(f"[BVP] vs pitcher {opposing_pitcher_id}: OPS {ops_float:.3f} ≤ .900 → no boost")
                            
                except (ValueError, TypeError):
                    logger.warning(f"[BVP] Invalid data for matchup: ops={ops}, at_bats={at_bats}")
            
            break
    
    return modifier, matchup_details


def calculate_batting_order_tempo(
    splits_data: Dict[str, Any],
    lineup_position: int  # 1-9
) -> Tuple[float, Optional[Dict[str, Any]]]:
    """
    Calculate Tempo Modifier from batting order splits.
    
    Parsing Logic:
    - Look at byBattingOrder array in response
    - Find object matching player's lineup spot (e.g., "Batting #1")
    - Extract at_bats and games_played to calculate PA/Game volume
    
    Tempo adjustments:
    - Leadoff (1): +10% (most PAs)
    - 2-4: +5% (high volume)
    - 5-6: baseline (1.0)
    - 7-8: -5% (lower volume)
    - 9: -10% (pitcher spot in NL / lowest volume)
    
    Args:
        splits_data: Response from /players/splits
        lineup_position: Today's confirmed batting order position (1-9)
    
    Returns:
        (modifier, order_details) - modifier ranges from 0.90 to 1.10
    """
    modifier = 1.0
    order_details = None
    
    if not splits_data or not lineup_position:
        return modifier, order_details
    
    # Search byBattingOrder array
    by_batting_order = splits_data.get("byBattingOrder", [])
    target_split = f"Batting #{lineup_position}"
    
    for order_split in by_batting_order:
        split_name = order_split.get("split_name", "")
        
        if split_name == target_split:
            at_bats = order_split.get("at_bats", 0)
            games_played = order_split.get("games_played", 0)
            
            # Calculate PA per game from this batting order spot
            pa_per_game = (at_bats / games_played) if games_played > 0 else 0
            
            order_details = {
                "lineup_position": lineup_position,
                "split_name": split_name,
                "at_bats": at_bats,
                "games_played": games_played,
                "pa_per_game": round(pa_per_game, 2),
                "avg": order_split.get("avg"),
                "ops": order_split.get("ops")
            }
            
            break
    
    # Apply position-based tempo modifier
    if lineup_position == 1:
        modifier = 1.10  # Leadoff gets most PAs
    elif lineup_position <= 4:
        modifier = 1.05  # Heart of order
    elif lineup_position <= 6:
        modifier = 1.0   # Middle of order
    elif lineup_position <= 8:
        modifier = 0.95  # Bottom third
    else:  # 9
        modifier = 0.90  # 9-hole gets fewest PAs
    
    logger.debug(f"[TEMPO] Batting #{lineup_position} → {modifier:.2f}x modifier")
    
    return modifier, order_details


async def get_full_matchup_and_tempo_modifiers(
    batter_id: int,
    pitcher_hand: str,  # "L" or "R"
    opponent_team_id: int,
    opposing_pitcher_id: int,
    lineup_position: int
) -> Dict[str, Any]:
    """
    Calculate all modifiers for a batter using BDL MLB API.
    
    Combines:
    1. L/R Split Modifier (from /players/splits)
    2. Batter vs Pitcher Modifier (from /players/versus)
    3. Tempo Modifier (from /players/splits byBattingOrder)
    
    Returns:
        {
            "matchup_modifier": float,  # Combined L/R + BVP
            "tempo_modifier": float,
            "lr_split": {...},
            "batter_vs_pitcher": {...},
            "batting_order": {...},
            "source": "bdl_mlb_api"
        }
    """
    result = {
        "matchup_modifier": 1.0,
        "tempo_modifier": 1.0,
        "lr_split": None,
        "batter_vs_pitcher": None,
        "batting_order": None,
        "source": "bdl_mlb_api",
        "calculated_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Fetch splits data (used for both L/R and batting order)
    splits_data = await fetch_player_splits(batter_id)
    
    # 1. Calculate L/R Split Modifier
    lr_modifier, lr_details = calculate_lr_split_modifier(splits_data, pitcher_hand)
    result["lr_split"] = lr_details
    
    # 2. Calculate Batter vs Pitcher Modifier
    bvp_modifier = 1.0
    if opponent_team_id and opposing_pitcher_id:
        versus_data = await fetch_player_versus(batter_id, opponent_team_id)
        bvp_modifier, bvp_details = calculate_versus_modifier(versus_data, opposing_pitcher_id)
        result["batter_vs_pitcher"] = bvp_details
    
    # 3. Calculate Tempo Modifier from Batting Order
    tempo_modifier, order_details = calculate_batting_order_tempo(splits_data, lineup_position)
    result["tempo_modifier"] = tempo_modifier
    result["batting_order"] = order_details
    
    # Combine matchup modifiers (L/R + BVP are additive boosts)
    # If both trigger, max boost is +10%
    combined_boost = (lr_modifier - 1.0) + (bvp_modifier - 1.0)
    result["matchup_modifier"] = 1.0 + combined_boost
    
    logger.info(f"[BDL_MODIFIERS] Batter {batter_id}: "
                f"LR={lr_modifier:.2f}, BVP={bvp_modifier:.2f}, "
                f"Combined Matchup={result['matchup_modifier']:.2f}, "
                f"Tempo={tempo_modifier:.2f}")
    
    return result


async def batch_fetch_modifiers(
    batters: List[Dict[str, Any]]
) -> Dict[int, Dict[str, Any]]:
    """
    Batch fetch modifiers for multiple batters.
    
    Args:
        batters: List of batter dicts with:
            - player_id: BDL player ID
            - pitcher_hand: "L" or "R"
            - opponent_team_id: BDL team ID
            - opposing_pitcher_id: BDL pitcher ID
            - lineup_position: 1-9
    
    Returns:
        Dict mapping player_id to modifier results
    """
    import asyncio
    
    results = {}
    
    # Process in batches to avoid rate limiting
    batch_size = 5
    for i in range(0, len(batters), batch_size):
        batch = batters[i:i + batch_size]
        
        tasks = []
        for batter in batch:
            tasks.append(
                get_full_matchup_and_tempo_modifiers(
                    batter_id=batter.get("player_id"),
                    pitcher_hand=batter.get("pitcher_hand", "R"),
                    opponent_team_id=batter.get("opponent_team_id"),
                    opposing_pitcher_id=batter.get("opposing_pitcher_id"),
                    lineup_position=batter.get("lineup_position")
                )
            )
        
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for batter, result in zip(batch, batch_results):
            player_id = batter.get("player_id")
            if isinstance(result, Exception):
                logger.error(f"[BDL_BATCH] Error for player {player_id}: {result}")
                results[player_id] = {
                    "matchup_modifier": 1.0,
                    "tempo_modifier": 1.0,
                    "error": str(result)
                }
            else:
                results[player_id] = result
        
        # Small delay between batches to be nice to the API
        if i + batch_size < len(batters):
            await asyncio.sleep(0.5)
    
    return results
