"""
Master Hub Routes Module
========================
NBA Master Hub - Single Source of Truth for all player data

DATA SOURCE: BallDontLie API (BDL) - ONLY source
BDL has been REMOVED from this application.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/master-hub", tags=["master-hub"])

# Service references (set by main app)
_db = None
_hub_functions = None  # Dict of functions: fetchPlayerIntel, hubSearchPlayers, etc.


def set_master_hub_deps(db, hub_functions: dict):
    """Set the db and hub function references."""
    global _db, _hub_functions
    _db = db
    _hub_functions = hub_functions


@router.get("/player/{player_id}")
async def get_player_intel(player_id: str):
    """
    THE VALET FUNCTION - Fetch player intel from Master Hub.
    
    This is the ONLY way to access player data from NBA_MASTER_HUB_2026.
    
    Args:
        player_id: Player ID (bdl_id, nba_id, or display_name)
        
    Returns:
        Complete player object with all fields
    """
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    player = await _hub_functions["fetchPlayerIntel"](player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")
    return player


@router.get("/player/name/{display_name}")
async def get_player_by_name(display_name: str):
    """Fetch player by display name."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    player = await _hub_functions["fetchPlayerIntelByName"](display_name)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {display_name}")
    return player


@router.get("/search")
async def search_hub_players(q: str, limit: int = 10):
    """Search players in Master Hub."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    players = await _hub_functions["hubSearchPlayers"](q, limit)
    return {"players": players, "count": len(players)}


@router.get("/stats")
async def get_hub_statistics():
    """Get Master Hub statistics."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    return await _hub_functions["getHubStats"]()


@router.post("/sync")
async def trigger_hub_sync():
    """
    Manually trigger Master Hub daily sync using BDL.
    Normally runs at 4:00 AM ET automatically.
    """
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    result = await _hub_functions["runHubSync"]()
    return result


@router.post("/start-scheduler")
async def start_hub_scheduler():
    """Start the 4:00 AM ET daily sync scheduler."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    hub = _hub_functions["get_master_hub"]()
    await hub.startDailyScheduler()
    return {"status": "started", "schedule": "4:00 AM ET daily"}


# ==================== BDL SYNC ENDPOINTS (PRIMARY) ====================

@router.post("/sync-bdl-all")
async def trigger_bdl_full_sync():
    """
    Trigger comprehensive BDL sync for ALL active players.
    
    This pulls COMPLETE data from all BDL endpoints:
    - /players: Profile metadata (height, weight, college, draft info)
    - /season_averages: Full season stats (pts, reb, ast, fg_pct, fg3_pct, etc.)
    - /stats: Last 15 game logs with full box scores
    
    WARNING: This syncs 500+ players and may take several minutes.
    For faster sync, use /sync-bdl-prizepicks instead.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    result = await service.sync_active_players()
    return result


@router.post("/sync-bdl-prizepicks")
async def trigger_bdl_prizepicks_sync():
    """
    Sync BDL data for players currently on the PrizePicks board.
    
    More efficient than full sync - only syncs players with active lines.
    Pulls complete data including:
    - Player profile (height, weight, college, draft info)
    - Season averages (all stats: pts, reb, ast, fg_pct, fg3_pct, ft_pct, etc.)
    - Last 15 game logs with full box scores
    
    Data is stored EXACTLY as received from BDL - no field renaming.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    result = await service.sync_prizepicks_players()
    return result


# ==================== CAREER STATS ENDPOINTS ====================

@router.post("/sync-career-stats")
async def sync_career_stats():
    """
    Sync career stats from NBA.com for all tracked players.
    
    Uses the nba_api library to fetch real career totals:
    - Points, Rebounds, Assists, Steals, Blocks, 3PM
    - Games played, Minutes
    
    Data is cached for 24 hours to avoid rate limiting.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.nba_career_service import sync_career_stats_for_players, TRACKED_PLAYERS
    
    result = await sync_career_stats_for_players(_db, TRACKED_PLAYERS)
    return {
        "success": True,
        "synced": result["synced"],
        "failed": result["failed"],
        "message": f"Synced career stats for {result['synced']} players"
    }


@router.get("/career-stats/{player_name}")
async def get_player_career_stats(player_name: str):
    """
    Get career stats for a specific player.
    
    Returns cached stats if available (< 24h old), otherwise fetches fresh data.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.nba_career_service import get_career_stats, get_milestone_for_player
    
    stats = await get_career_stats(_db, player_name)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Career stats not found for: {player_name}")
    
    milestone = await get_milestone_for_player(_db, player_name)
    
    return {
        "player_name": stats.get("player_name"),
        "career_stats": {
            "points": stats.get("career_pts", 0),
            "rebounds": stats.get("career_reb", 0),
            "assists": stats.get("career_ast", 0),
            "steals": stats.get("career_stl", 0),
            "blocks": stats.get("career_blk", 0),
            "three_pointers": stats.get("career_3pm", 0),
            "games_played": stats.get("games_played", 0),
        },
        "milestone": milestone,
        "fetched_at": stats.get("fetched_at"),
        "is_active": stats.get("is_active", False)
    }




@router.post("/sync-bdl-player/{player_name}")
async def sync_single_player_bdl(player_name: str):
    """
    Sync BDL data for a single player by name.
    
    Searches BDL for the player and syncs all available data.
    Uses normalized name matching (Jr, Sr, III suffixes handled).
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    
    # Search for player
    player = await service.search_player(player_name)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found in BDL: {player_name}")
    
    # Sync complete data
    player_id = player.get("id")
    success = await service.sync_player_to_master_hub(player_id)
    
    if success:
        # Fetch the synced document
        doc = await _db.nba_master_hub_2026.find_one(
            {"bdl_id": player_id},
            {"_id": 0}
        )
        return {
            "success": True,
            "player_name": player_name,
            "bdl_id": player_id,
            "data": doc
        }
    else:
        raise HTTPException(status_code=500, detail=f"Failed to sync player: {player_name}")


@router.post("/sync-player-logs/{player_name}")
async def sync_player_game_logs(player_name: str):
    """
    Sync game logs for a single player using BDL API.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    
    # Search for player in BDL
    player = await service.search_player(player_name)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found in BDL: {player_name}")
    
    # Sync the player (includes game logs)
    player_id = player.get("id")
    success = await service.sync_player_to_master_hub(player_id)
    
    if success:
        doc = await _db.nba_master_hub_2026.find_one(
            {"bdl_id": player_id},
            {"_id": 0, "display_name": 1, "game_logs": 1}
        )
        game_logs = doc.get("game_logs", []) if doc else []
        return {
            "success": True,
            "player": player_name,
            "games_count": len(game_logs),
            "sample_game": game_logs[0] if game_logs else None
        }
    else:
        raise HTTPException(status_code=500, detail=f"Failed to sync player: {player_name}")


@router.get("/bdl-sample/{player_name}")
async def get_bdl_sample(player_name: str):
    """
    Get sample BDL data for a player (for verification).
    
    Returns the complete baseline_stats object as stored in nba_master_hub_2026.
    Shows exactly what BDL API fields are available.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import _normalize_name
    normalized = _normalize_name(player_name)
    
    doc = await _db.nba_master_hub_2026.find_one(
        {"normalized_name": normalized},
        {"_id": 0}
    )
    
    if not doc:
        # Try display_name match
        doc = await _db.nba_master_hub_2026.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
    
    if not doc:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_name}")
    
    return doc


@router.post("/sync-roster")
async def sync_roster_from_board():
    """
    ROSTER SYNC: Update team and season stats for all active players.
    
    This endpoint:
    1. Gets all unique players from the cached board
    2. Fetches their current team and season stats from BDL
    3. Updates the master hub with correct team assignments
    
    Use this to fix trade-related issues (e.g., player moved to new team).
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.roster_sync_service import get_roster_sync_service
    service = get_roster_sync_service(_db)
    result = await service.sync_all_from_cached_board()
    return result


@router.post("/sync-roster-player/{player_name}")
async def sync_roster_single_player(player_name: str):
    """
    Sync roster data for a single player.
    
    Args:
        player_name: Player name (e.g., "John Collins")
        
    Updates team assignment and season stats from BDL.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.roster_sync_service import get_roster_sync_service
    service = get_roster_sync_service(_db)
    result = await service.sync_player_from_bdl(player_name)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Could not sync player: {player_name}")
    
    return {
        "success": True,
        "player": player_name,
        "team": result.get("team"),
        "baseline_stats": result.get("baseline_stats")
    }



@router.post("/sync-contracts")
async def sync_contract_data_endpoint():
    """
    Sync contract data from Spotrac.
    
    Scrapes Spotrac.com for contract year players (UFAs, RFAs, player options).
    Results are cached in MongoDB with 24h TTL.
    
    Used to populate the pay_day badge with live contract data.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.spotrac_contract_service import sync_contract_data
    result = await sync_contract_data(_db)
    return result


@router.get("/contract-year-players")
async def get_contract_year_players():
    """
    Get list of all players in contract years.
    
    Returns players who are UFAs, RFAs, or have player options expiring.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    cache_doc = await _db.spotrac_contracts_cache.find_one(
        {"type": "contracts_cache"},
        {"_id": 0}
    )
    
    if not cache_doc:
        # Trigger sync if no cache
        from services.spotrac_contract_service import sync_contract_data
        await sync_contract_data(_db)
        cache_doc = await _db.spotrac_contracts_cache.find_one(
            {"type": "contracts_cache"},
            {"_id": 0}
        )
    
    contracts = cache_doc.get("contracts", {}) if cache_doc else {}
    
    # Convert to list and sort by salary
    players_list = []
    for name, data in contracts.items():
        players_list.append({
            "player_name": data.get("player_name"),
            "team": data.get("team"),
            "salary": data.get("salary", 0),
            "salary_display": f"${data.get('salary', 0) / 1e6:.1f}M" if data.get("salary", 0) >= 1e6 else "N/A",
            "type": data.get("type"),
            "expires": data.get("expires")
        })
    
    # Sort by salary descending
    players_list.sort(key=lambda x: x.get("salary", 0), reverse=True)
    
    return {
        "success": True,
        "count": len(players_list),
        "cached_at": cache_doc.get("cached_at") if cache_doc else None,
        "players": players_list
    }


@router.get("/contract/{player_name}")
async def get_player_contract(player_name: str):
    """
    Get contract info for a specific player.
    
    Returns contract year status and details if player is in a contract year.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.spotrac_contract_service import get_contract_year_info
    info = await get_contract_year_info(player_name, _db)
    
    if not info:
        return {
            "success": True,
            "player_name": player_name,
            "in_contract_year": False,
            "message": "Player is not in a contract year"
        }
    
    return {
        "success": True,
        "player_name": player_name,
        "in_contract_year": True,
        "contract_info": info
    }
