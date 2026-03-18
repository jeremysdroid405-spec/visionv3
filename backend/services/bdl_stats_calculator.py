"""
BDL Stats Calculator
====================
Recalculates baseline_stats from bdl_game_logs stored in nba_master_hub_2026.

This ensures stats are always computed from the most accurate source (BDL game logs)
rather than relying on potentially stale or incomplete Tank01 data.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


def safe_float(val) -> float:
    """Safely convert value to float."""
    try:
        return float(val) if val else 0
    except (ValueError, TypeError):
        return 0


def calc_avg(game_list: List[Dict], stat_key: str) -> float:
    """Calculate average for a stat from game logs."""
    if not game_list:
        return 0
    values = [safe_float(g.get(stat_key, 0)) for g in game_list]
    return round(sum(values) / len(values), 1) if values else 0


def calc_std_dev(game_list: List[Dict], stat_key: str) -> float:
    """Calculate standard deviation for a stat from game logs."""
    if len(game_list) < 2:
        return 0
    values = [safe_float(g.get(stat_key, 0)) for g in game_list]
    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    return round(variance ** 0.5, 2)


async def recalculate_all_baseline_stats(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Recalculate baseline_stats for all players using BDL game logs as primary source.
    
    This function:
    1. Queries all players with bdl_game_logs
    2. Computes L5, L10, and season averages from the game logs
    3. Updates baseline_stats in nba_master_hub_2026
    
    Returns:
        Dict with update statistics
    """
    logger.info("[BDL_CALC] Starting baseline stats recalculation from BDL game logs...")
    
    # Get all players with BDL game logs
    players = await db.nba_master_hub_2026.find(
        {'bdl_game_logs': {'$exists': True, '$ne': []}},
        {'_id': 1, 'display_name': 1, 'bdl_game_logs': 1, 'baseline_stats': 1}
    ).to_list(2000)
    
    logger.info(f"[BDL_CALC] Found {len(players)} players with BDL game logs")
    updated = 0
    skipped = 0
    
    for player in players:
        bdl_logs = player.get('bdl_game_logs', [])
        if not bdl_logs:
            skipped += 1
            continue
        
        # Sort by date (most recent first)
        # BDL logs have 'date' field from the game object
        bdl_logs = sorted(
            bdl_logs, 
            key=lambda x: x.get('date') or x.get('game', {}).get('date', ''), 
            reverse=True
        )
        
        l5 = bdl_logs[:5]
        l10 = bdl_logs[:10]
        all_games = bdl_logs
        
        # Calculate baseline stats from BDL logs
        new_stats = {
            "PTS": {
                "l5_avg": calc_avg(l5, "pts"),
                "l10_avg": calc_avg(l10, "pts"),
                "season_avg": calc_avg(all_games, "pts"),
                "std_dev_l10": calc_std_dev(l10, "pts")
            },
            "REB": {
                "l5_avg": calc_avg(l5, "reb"),
                "l10_avg": calc_avg(l10, "reb"),
                "season_avg": calc_avg(all_games, "reb"),
                "std_dev_l10": calc_std_dev(l10, "reb")
            },
            "AST": {
                "l5_avg": calc_avg(l5, "ast"),
                "l10_avg": calc_avg(l10, "ast"),
                "season_avg": calc_avg(all_games, "ast"),
                "std_dev_l10": calc_std_dev(l10, "ast")
            },
            "3PM": {
                "l5_avg": calc_avg(l5, "fg3m"),
                "l10_avg": calc_avg(l10, "fg3m"),
                "season_avg": calc_avg(all_games, "fg3m"),
                "std_dev_l10": calc_std_dev(l10, "fg3m")
            },
            "STL": {
                "l5_avg": calc_avg(l5, "stl"),
                "l10_avg": calc_avg(l10, "stl"),
                "season_avg": calc_avg(all_games, "stl"),
                "std_dev_l10": calc_std_dev(l10, "stl")
            },
            "BLK": {
                "l5_avg": calc_avg(l5, "blk"),
                "l10_avg": calc_avg(l10, "blk"),
                "season_avg": calc_avg(all_games, "blk"),
                "std_dev_l10": calc_std_dev(l10, "blk")
            },
            "TO": {
                "l5_avg": calc_avg(l5, "turnover"),
                "l10_avg": calc_avg(l10, "turnover"),
                "season_avg": calc_avg(all_games, "turnover"),
                "std_dev_l10": calc_std_dev(l10, "turnover")
            },
            "games_played": len(all_games),
            "synced_from": "bdl_game_logs",
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Combined stats (PRA, PR, PA, RA)
        pts_season = new_stats["PTS"]["season_avg"]
        reb_season = new_stats["REB"]["season_avg"]
        ast_season = new_stats["AST"]["season_avg"]
        
        new_stats["PRA"] = {
            "l5_avg": round(new_stats["PTS"]["l5_avg"] + new_stats["REB"]["l5_avg"] + new_stats["AST"]["l5_avg"], 1),
            "l10_avg": round(new_stats["PTS"]["l10_avg"] + new_stats["REB"]["l10_avg"] + new_stats["AST"]["l10_avg"], 1),
            "season_avg": round(pts_season + reb_season + ast_season, 1)
        }
        new_stats["PR"] = {
            "l5_avg": round(new_stats["PTS"]["l5_avg"] + new_stats["REB"]["l5_avg"], 1),
            "l10_avg": round(new_stats["PTS"]["l10_avg"] + new_stats["REB"]["l10_avg"], 1),
            "season_avg": round(pts_season + reb_season, 1)
        }
        new_stats["PA"] = {
            "l5_avg": round(new_stats["PTS"]["l5_avg"] + new_stats["AST"]["l5_avg"], 1),
            "l10_avg": round(new_stats["PTS"]["l10_avg"] + new_stats["AST"]["l10_avg"], 1),
            "season_avg": round(pts_season + ast_season, 1)
        }
        new_stats["RA"] = {
            "l5_avg": round(new_stats["REB"]["l5_avg"] + new_stats["AST"]["l5_avg"], 1),
            "l10_avg": round(new_stats["REB"]["l10_avg"] + new_stats["AST"]["l10_avg"], 1),
            "season_avg": round(reb_season + ast_season, 1)
        }
        
        # Update the player
        await db.nba_master_hub_2026.update_one(
            {'_id': player['_id']},
            {'$set': {'baseline_stats': new_stats}}
        )
        updated += 1
    
    logger.info(f"[BDL_CALC] Recalculated baseline_stats for {updated} players from BDL game logs (skipped {skipped})")
    
    return {
        "success": True,
        "updated": updated,
        "skipped": skipped,
        "total_with_bdl_logs": len(players),
        "synced_at": datetime.now(timezone.utc).isoformat()
    }
