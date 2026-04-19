"""
BDL Stats Calculator
====================
Recalculates baseline_stats from bdl_game_logs stored in nba_master_hub_2026.

This ensures stats are always computed from the most accurate source (BDL game logs)
rather than relying on potentially stale or incomplete BDL data.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

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
    Recalculate baseline_stats for all players using game logs as primary source.
    
    This function:
    1. Queries all players with game_logs (or bdl_game_logs as fallback)
    2. Computes L5, L10, and season averages from the game logs
    3. Updates baseline_stats in nba_master_hub_2026
    
    Returns:
        Dict with update statistics
    """
    logger.info("[BDL_CALC] Starting baseline stats recalculation from game logs...")
    
    # Get all players with game logs (primary) or BDL game logs (fallback)
    players = await db[COLL("master_hub", "nba")].find(
        {'$or': [
            {'game_logs': {'$exists': True, '$ne': []}},
            {'bdl_game_logs': {'$exists': True, '$ne': []}}
        ]},
        {'_id': 1, 'display_name': 1, 'bdl_game_logs': 1, 'game_logs': 1, 'baseline_stats': 1}
    ).to_list(2000)
    
    logger.info(f"[BDL_CALC] Found {len(players)} players with game logs")
    updated = 0
    skipped = 0
    
    for player in players:
        # Prefer game_logs (more complete), fall back to bdl_game_logs
        game_logs = player.get('game_logs', [])
        bdl_logs = player.get('bdl_game_logs', [])
        
        # Use game_logs if it has more data, otherwise use bdl_game_logs
        if len(game_logs) >= len(bdl_logs):
            logs = game_logs
            log_source = "game_logs"
            # game_logs uses 'game_date' and different field names
            date_key = 'game_date'
            pts_key = 'pts'
            reb_key = 'reb'
            ast_key = 'ast'
            fg3m_key = 'fg3m'
            stl_key = 'stl'
            blk_key = 'blk'
            to_key = 'tov'
        else:
            logs = bdl_logs
            log_source = "bdl_game_logs"
            date_key = 'date'
            pts_key = 'pts'
            reb_key = 'reb'
            ast_key = 'ast'
            fg3m_key = 'fg3m'
            stl_key = 'stl'
            blk_key = 'blk'
            to_key = 'turnover'
        
        if not logs or len(logs) < 5:
            skipped += 1
            continue
        
        # Sort by date (most recent first)
        try:
            logs = sorted(
                logs, 
                key=lambda x: x.get(date_key) or x.get('game', {}).get('date', '') or '', 
                reverse=True
            )
        except:
            # If sorting fails, use as-is
            pass
        
        l5 = logs[:5]
        l10 = logs[:10]
        all_games = logs
        
        # Helper to calculate average with correct field key
        def calc_avg_with_key(game_list, key):
            if not game_list:
                return 0
            values = [safe_float(g.get(key, 0)) for g in game_list]
            return round(sum(values) / len(values), 1) if values else 0
        
        def calc_std_dev_with_key(game_list, key):
            if len(game_list) < 2:
                return 0
            values = [safe_float(g.get(key, 0)) for g in game_list]
            avg = sum(values) / len(values)
            variance = sum((v - avg) ** 2 for v in values) / len(values)
            return round(variance ** 0.5, 2)
        
        # Calculate baseline stats
        new_stats = {
            "PTS": {
                "l5_avg": calc_avg_with_key(l5, pts_key),
                "l10_avg": calc_avg_with_key(l10, pts_key),
                "season_avg": calc_avg_with_key(all_games, pts_key),
                "std_dev_l10": calc_std_dev_with_key(l10, pts_key)
            },
            "REB": {
                "l5_avg": calc_avg_with_key(l5, reb_key),
                "l10_avg": calc_avg_with_key(l10, reb_key),
                "season_avg": calc_avg_with_key(all_games, reb_key),
                "std_dev_l10": calc_std_dev_with_key(l10, reb_key)
            },
            "AST": {
                "l5_avg": calc_avg_with_key(l5, ast_key),
                "l10_avg": calc_avg_with_key(l10, ast_key),
                "season_avg": calc_avg_with_key(all_games, ast_key),
                "std_dev_l10": calc_std_dev_with_key(l10, ast_key)
            },
            "3PM": {
                "l5_avg": calc_avg_with_key(l5, fg3m_key),
                "l10_avg": calc_avg_with_key(l10, fg3m_key),
                "season_avg": calc_avg_with_key(all_games, fg3m_key),
                "std_dev_l10": calc_std_dev_with_key(l10, fg3m_key)
            },
            "STL": {
                "l5_avg": calc_avg_with_key(l5, stl_key),
                "l10_avg": calc_avg_with_key(l10, stl_key),
                "season_avg": calc_avg_with_key(all_games, stl_key),
                "std_dev_l10": calc_std_dev_with_key(l10, stl_key)
            },
            "BLK": {
                "l5_avg": calc_avg_with_key(l5, blk_key),
                "l10_avg": calc_avg_with_key(l10, blk_key),
                "season_avg": calc_avg_with_key(all_games, blk_key),
                "std_dev_l10": calc_std_dev_with_key(l10, blk_key)
            },
            "TO": {
                "l5_avg": calc_avg_with_key(l5, to_key),
                "l10_avg": calc_avg_with_key(l10, to_key),
                "season_avg": calc_avg_with_key(all_games, to_key),
                "std_dev_l10": calc_std_dev_with_key(l10, to_key)
            },
            "games_played": len(all_games),
            "synced_from": log_source,
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
        await db[COLL("master_hub", "nba")].update_one(
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
