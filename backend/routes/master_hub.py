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
    Fetch player intel from Master Hub by bdl_id.
    
    Args:
        player_id: Player's bdl_id (integer) - STRICTLY NUMBER BASED
        
    Returns:
        Complete player object with all fields
    """
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    # Try as bdl_id first (number)
    try:
        bdl_id = int(player_id)
        player = await _hub_functions["fetchPlayerIntel"](bdl_id)
        if player:
            return player
    except (ValueError, TypeError):
        pass
    
    # Fall back to name lookup
    player = await _hub_functions["fetchPlayerIntelByName"](player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")
    return player


@router.get("/player/name/{display_name:path}")
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
    Trigger comprehensive BDL sync for ALL active NBA players (~530 players).
    
    Uses GOAT tier BATCH API calls for maximum efficiency:
    1. Fetches all active players from /players/active
    2. BATCH call to /season_averages/general for official season stats
    3. BATCH call to /stats for game logs (L5/L10 calculation)
    
    This ensures ALL players have fresh stats in nba_master_hub_2026,
    not just those with props today. Should complete in ~15-20 seconds.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    result = await service.sync_all_active_players()
    return result


@router.post("/sync-bdl-prizepicks")
async def trigger_bdl_prizepicks_sync():
    """
    DEPRECATED - Use /sync-bdl-all instead.
    
    This endpoint is kept for backwards compatibility but now calls
    sync_all_active_players() which syncs ALL active NBA players.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    result = await service.sync_all_active_players()
    return result


# ==================== NBA.COM L5/L10 ENRICHMENT ====================

@router.post("/enrich-nba-stats/{bdl_id}")
async def enrich_player_nba_stats(bdl_id: int):
    """
    Enrich a single player with official NBA.com L5/L10 stats.
    
    Uses the player's nba_id (stored in master hub) to fetch pre-calculated
    last N games averages from NBA.com's playerdashboardbylastngames endpoint.
    
    Args:
        bdl_id: Player's BDL ID
        
    Returns:
        Updated player with nba_stats_raw and enriched baseline_stats
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    
    success = await service.enrich_baseline_with_nba_stats(bdl_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Could not enrich player {bdl_id} - check nba_id exists")
    
    # Return updated player
    player = await _db.nba_master_hub_2026.find_one({"bdl_id": bdl_id}, {"_id": 0})
    return player


@router.get("/nba-stats/{bdl_id}")
async def get_nba_last_n_stats(bdl_id: int):
    """
    Fetch L5/L10/L15/L20 stats from NBA.com for a player.
    
    Does NOT update the database - just fetches and returns the stats.
    Use /enrich-nba-stats/{bdl_id} to persist the data.
    
    Args:
        bdl_id: Player's BDL ID
        
    Returns:
        Dict with overall, last5, last10, last15, last20 stats
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Get player's nba_id
    player = await _db.nba_master_hub_2026.find_one({"bdl_id": bdl_id}, {"nba_id": 1, "display_name": 1})
    if not player:
        raise HTTPException(status_code=404, detail=f"Player {bdl_id} not found")
    
    nba_id = player.get("nba_id")
    if not nba_id:
        raise HTTPException(status_code=400, detail=f"No nba_id for {player.get('display_name')}")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    
    nba_stats = await service.fetch_nba_last_n_games(nba_id)
    if not nba_stats:
        raise HTTPException(status_code=500, detail=f"Failed to fetch NBA.com stats for nba_id={nba_id}")
    
    return {
        "bdl_id": bdl_id,
        "nba_id": nba_id,
        "display_name": player.get("display_name"),
        "stats": nba_stats
    }


@router.post("/enrich-all-nba-stats")
async def enrich_all_players_nba_stats(limit: int = 100):
    """
    Batch enrich players with NBA.com L5/L10 stats.
    
    Finds players that have nba_id but missing l5_avg in baseline_stats,
    fetches their L5/L10 from NBA.com, and updates their records.
    
    Args:
        limit: Max number of players to enrich (default 100)
        
    Returns:
        Summary of enrichment results
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    service = get_bdl_sync_service(_db)
    
    # Find players needing enrichment
    players = await _db.nba_master_hub_2026.find({
        "nba_id": {"$exists": True, "$ne": None},
        "$or": [
            {"baseline_stats.PTS.l5_avg": {"$exists": False}},
            {"baseline_stats.PTS.l5_avg": None}
        ]
    }, {"bdl_id": 1, "display_name": 1, "nba_id": 1}).limit(limit).to_list(limit)
    
    logger.info(f"[NBA] Batch enrichment: {len(players)} players need L5/L10")
    
    success = 0
    failed = 0
    
    for player in players:
        try:
            result = await service.enrich_baseline_with_nba_stats(player["bdl_id"])
            if result:
                success += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"[NBA] Error enriching {player.get('display_name')}: {e}")
            failed += 1
    
    return {
        "total_processed": len(players),
        "success": success,
        "failed": failed,
        "remaining": await _db.nba_master_hub_2026.count_documents({
            "nba_id": {"$exists": True, "$ne": None},
            "$or": [
                {"baseline_stats.PTS.l5_avg": {"$exists": False}},
                {"baseline_stats.PTS.l5_avg": None}
            ]
        })
    }


@router.post("/sync-career-stats")
async def sync_career_stats():
    """
    Sync player badges from BDL Advanced Stats API.
    
    MIGRATED: Now uses BallDontLie API instead of stats.nba.com (which was
    failing due to bot protection / JSON decode errors).
    
    Uses BDL endpoints:
    - /season_averages/general?type=advanced -> Usage%, PIE, TS%
    - /season_averages/tracking -> Drives, Passing, Catch & Shoot
    - /season_averages/hustle -> Deflections, Contested Shots
    - /season_averages/playtype -> P&R, ISO, Post-Up efficiency
    
    Data is cached for 12 hours.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_player_badge_service import get_bdl_badge_service
    
    badge_service = get_bdl_badge_service(_db)
    result = await badge_service.sync_badges_for_players()
    
    return {
        "success": True,
        "synced": result["synced"],
        "failed": result["failed"],
        "total_badges": result["total_badges"],
        "message": f"Synced badges for {result['synced']} players ({result['total_badges']} badges generated)",
        "source": "bdl_advanced"
    }


@router.get("/career-stats/{player_name}")
async def get_player_career_stats(player_name: str):
    """
    Get player badges from BDL Advanced Stats.
    
    MIGRATED: Now returns performance badges based on BDL advanced stats
    instead of career totals from stats.nba.com.
    
    Returns badges like: Volume Scorer, Efficient, Playmaker, Motor, etc.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_player_badge_service import get_bdl_badge_service
    
    badge_service = get_bdl_badge_service(_db)
    result = await badge_service.get_player_badges(player_name=player_name)
    
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    
    return {
        "player_name": result.get("player_name"),
        "badges": result.get("badges", []),
        "badge_count": result.get("badge_count", 0),
        "stats": result.get("stats", {}),
        "fetched_at": result.get("fetched_at"),
        "source": "bdl_advanced"
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



@router.post("/enrich-game-values")
async def enrich_game_values(limit: int = 100):
    """
    Batch enrich players with l10_values from BDL game logs.
    
    This is CRITICAL for hit rate calculations. Without l10_values,
    hit rates cannot be calculated per betting line.
    
    Args:
        limit: Maximum number of players to enrich (default 100)
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    sync_service = get_bdl_sync_service(_db)
    
    result = await sync_service.batch_enrich_game_values(limit=limit)
    
    return {
        "success": True,
        "message": f"Enriched {result['enriched']} players with game values",
        "details": result
    }


@router.post("/enrich-player-game-values/{bdl_id}")
async def enrich_single_player_game_values(bdl_id: int):
    """
    Enrich a single player with l10_values from BDL game logs.
    
    Args:
        bdl_id: BallDontLie player ID
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    sync_service = get_bdl_sync_service(_db)
    
    success = await sync_service.enrich_player_with_game_values(bdl_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Failed to enrich player bdl_id={bdl_id}")
    
    return {
        "success": True,
        "message": f"Enriched player bdl_id={bdl_id} with game values"
    }



@router.post("/sync-badges")
async def sync_context_badges(limit: int = 500):
    """
    Sync context badges for all players with active props.
    
    Computes badges based on:
    - Home/Away game (home_cookin)
    - Travel distance (jet_lag)
    - Former team matchups (revenge)
    - Hot streaks (locked_in)
    
    Args:
        limit: Maximum number of players to process (default 500)
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.context_badge_service import get_badge_service
    badge_service = get_badge_service(_db)
    
    result = await badge_service.sync_badges_for_all_players(limit=limit)
    
    return {
        "success": True,
        "message": f"Synced badges for {result['updated']} players",
        "details": result
    }


@router.post("/add-badge/{player_name}")
async def add_manual_badge(player_name: str, badge_key: str, description: str = None):
    """
    Manually add a badge to a player.
    
    Useful for news-based badges like 'distraction', 'milestone', 'legal_noise'.
    
    Args:
        player_name: Player's display name
        badge_key: Badge key (milestone, distraction, legal_noise, pay_day, deep_water)
        description: Optional custom description
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.context_badge_service import get_badge_service
    badge_service = get_badge_service(_db)
    
    success = await badge_service.add_manual_badge(player_name, badge_key, description)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Failed to add badge for {player_name}")
    
    return {
        "success": True,
        "message": f"Added '{badge_key}' badge to {player_name}"
    }
