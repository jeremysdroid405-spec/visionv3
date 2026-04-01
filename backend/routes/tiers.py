"""
Tier Routes
===========
War Zone, Safe Haven (Goblin Vault), and Front Lines tier endpoints.
VIP Room Logic: Filters out flagged picks (hook_risk, suspect_line_bait) from main feeds.
Cross-Board Deduplication: Ensures each player+stat+line combo appears in only ONE section.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Set
import logging
import time

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Tiers"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None

# Sidecar detector instance
_sidecar_detector = None

# Cross-board deduplication cache (reset every 60 seconds)
_served_picks_cache: Set[str] = set()
_cache_timestamp: float = 0
CACHE_TTL = 60  # seconds


def _get_pick_key(pick: Dict[str, Any]) -> str:
    """Generate unique key for a pick: player_name|stat_type|line"""
    return f"{pick.get('player_name', '')}|{pick.get('stat_type', '')}|{pick.get('line', '')}"


def _reset_cache_if_stale():
    """Reset the served picks cache if it's older than TTL"""
    global _served_picks_cache, _cache_timestamp
    if time.time() - _cache_timestamp > CACHE_TTL:
        _served_picks_cache = set()
        _cache_timestamp = time.time()


def _mark_picks_as_served(picks: List[Dict[str, Any]]):
    """Add picks to the served cache"""
    global _served_picks_cache
    for pick in picks:
        _served_picks_cache.add(_get_pick_key(pick))


def _filter_already_served(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove picks that have already been served in another section"""
    return [p for p in picks if _get_pick_key(p) not in _served_picks_cache]

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None

# Sidecar detector instance
_sidecar_detector = None


def set_tier_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine, _sidecar_detector
    _demon_goblin_engine = engine
    # Initialize sidecar detector
    from services.sidecar.hook_bait_detector import get_hook_bait_detector
    _sidecar_detector = get_hook_bait_detector(engine.db)


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


def get_sidecar():
    """Get the sidecar detector instance"""
    global _sidecar_detector
    if _sidecar_detector is None and _demon_goblin_engine is not None:
        from services.sidecar.hook_bait_detector import get_hook_bait_detector
        _sidecar_detector = get_hook_bait_detector(_demon_goblin_engine.db)
    return _sidecar_detector


def filter_clean_picks(
    picks: List[Dict[str, Any]], 
    limit: int = 10,
    exclude_keys: Set[str] = None
) -> tuple:
    """
    VIP Room Logic: Filter out flagged picks and return clean ones.
    
    Args:
        picks: List of picks (should be pre-fetched with buffer, e.g., 30 picks)
        limit: Target number of clean picks to return (default 10)
        exclude_keys: Set of pick keys to exclude (for cross-board deduplication)
    
    Returns:
        Tuple of (clean_picks[:limit], trapped_picks, served_keys)
    """
    if exclude_keys is None:
        exclude_keys = set()
    
    clean_picks = []
    trapped_picks = []
    served_keys = set()
    
    for pick in picks:
        pick_key = _get_pick_key(pick)
        
        # Skip if already served in a higher-priority board
        if pick_key in exclude_keys:
            continue
        
        sidecar = pick.get("sidecar", {})
        is_trapped = sidecar.get("hook_risk", False) or sidecar.get("suspect_line_bait", False)
        
        if is_trapped:
            trapped_picks.append(pick)
        else:
            clean_picks.append(pick)
            served_keys.add(pick_key)
    
    # Sort clean picks by vision_score (highest edge first) for backfill
    clean_picks.sort(key=lambda x: x.get("vision_score", 0) or 0, reverse=True)
    
    # Only return keys for picks we're actually serving (up to limit)
    final_picks = clean_picks[:limit]
    final_keys = {_get_pick_key(p) for p in final_picks}
    
    return final_picks, trapped_picks, final_keys


async def _get_board_pick_keys(engine, board_name: str, sidecar) -> Set[str]:
    """
    Get pick keys for a specific board (for cross-board exclusion).
    """
    try:
        if board_name == "safe_haven":
            result = await engine.get_goblin_vault_static()
        elif board_name == "front_lines":
            result = await engine.get_front_lines_static()
        elif board_name == "war_zone":
            result = await engine.get_war_zone_static()
        else:
            return set()
        
        picks = result.get("picks", [])
        
        # Apply sidecar enrichment to identify clean picks
        if sidecar and sidecar.is_enabled() and picks:
            picks = await sidecar.enrich_board_picks(picks)
        
        # Only include clean picks in exclusion set
        clean_keys = set()
        for pick in picks:
            sc = pick.get("sidecar", {})
            is_trapped = sc.get("hook_risk", False) or sc.get("suspect_line_bait", False)
            if not is_trapped:
                clean_keys.add(_get_pick_key(pick))
        
        return clean_keys
    except Exception as e:
        logger.warning(f"[DEDUP] Failed to get {board_name} keys: {e}")
        return set()


@router.get("/v3/war-zone")
async def get_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    include_vision: bool = Query(True),
    include_traps: bool = Query(False, description="Include trapped picks (for Trap Graveyard)")
):
    """
    THE WAR ZONE - High-risk Demon picks (VIP CLEAN FEED)
    PRIORITY: THIRD - Excludes picks already in Safe Haven and Front Lines.
    
    By default, returns only CLEAN picks (no hook_risk or suspect_line_bait).
    Set include_traps=true to get raw unfiltered data.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    sidecar = get_sidecar()
    
    # Get Safe Haven and Front Lines picks to exclude (higher priority boards)
    exclude_keys = set()
    if not include_traps:
        safe_haven_keys = await _get_board_pick_keys(engine, "safe_haven", sidecar)
        front_lines_keys = await _get_board_pick_keys(engine, "front_lines", sidecar)
        exclude_keys = safe_haven_keys | front_lines_keys
    
    result = await engine.get_war_zone_static()
    
    # Apply sidecar enrichment
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
            
            # VIP Room: Filter out traps and exclude Safe Haven/Front Lines duplicates
            if not include_traps:
                clean_picks, trapped_picks, _ = filter_clean_picks(
                    result["picks"], limit, exclude_keys=exclude_keys
                )
                result["picks"] = clean_picks
                result["trapped_count"] = len(trapped_picks)
                result["vip_filtered"] = True
                result["excluded_from_other_boards"] = len(exclude_keys)
            
        except Exception as e:
            logger.warning(f"[SIDECAR] War Zone enrichment failed: {e}")
            result["sidecar_enabled"] = False
    else:
        if result:
            result["sidecar_enabled"] = False
    
    return result


@router.get("/v3/goblin-vault")
async def get_goblin_vault(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    include_vision: bool = Query(True),
    include_traps: bool = Query(False, description="Include trapped picks (for Trap Graveyard)")
):
    """
    THE SAFE HAVEN (Goblin Vault) - Safer Goblin picks (VIP CLEAN FEED)
    PRIORITY: HIGHEST - No exclusions, this is the primary board.
    
    By default, returns only CLEAN picks (no hook_risk or suspect_line_bait).
    Set include_traps=true to get raw unfiltered data.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    result = await engine.get_goblin_vault_static()
    
    # Apply sidecar enrichment
    sidecar = get_sidecar()
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
            
            # VIP Room: Filter out traps unless explicitly requested
            # Safe Haven has HIGHEST priority - no exclusions
            if not include_traps:
                clean_picks, trapped_picks, _ = filter_clean_picks(result["picks"], limit)
                result["picks"] = clean_picks
                result["trapped_count"] = len(trapped_picks)
                result["vip_filtered"] = True
            
        except Exception as e:
            logger.warning(f"[SIDECAR] Goblin Vault enrichment failed: {e}")
            result["sidecar_enabled"] = False
    else:
        if result:
            result["sidecar_enabled"] = False
    
    return result


@router.get("/v3/safe-haven")
async def get_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    include_vision: bool = Query(True),
    include_traps: bool = Query(False)
):
    """
    Alias for /v3/goblin-vault (user-friendly name).
    """
    return await get_goblin_vault(response=response, limit=limit, include_vision=include_vision, include_traps=include_traps)


@router.get("/v3/front-lines")
async def get_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    include_vision: bool = Query(True),
    include_traps: bool = Query(False, description="Include trapped picks (for Trap Graveyard)")
):
    """
    THE FRONT LINES - Goblin picks with 60-79% hit rate (VIP CLEAN FEED)
    PRIORITY: SECOND - Excludes picks already in Safe Haven.
    
    By default, returns only CLEAN picks (no hook_risk or suspect_line_bait).
    Set include_traps=true to get raw unfiltered data.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    sidecar = get_sidecar()
    
    # Get Safe Haven picks to exclude (higher priority board)
    exclude_keys = set()
    if not include_traps:
        exclude_keys = await _get_board_pick_keys(engine, "safe_haven", sidecar)
    
    result = await engine.get_front_lines_static()
    
    # Apply sidecar enrichment
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
            
            # VIP Room: Filter out traps and exclude Safe Haven duplicates
            if not include_traps:
                clean_picks, trapped_picks, _ = filter_clean_picks(
                    result["picks"], limit, exclude_keys=exclude_keys
                )
                result["picks"] = clean_picks
                result["trapped_count"] = len(trapped_picks)
                result["vip_filtered"] = True
                result["excluded_from_safe_haven"] = len(exclude_keys)
            
        except Exception as e:
            logger.warning(f"[SIDECAR] Front Lines enrichment failed: {e}")
            result["sidecar_enabled"] = False
    else:
        if result:
            result["sidecar_enabled"] = False
    
    return result


# ==================== TRAP GRAVEYARD ====================

@router.get("/v3/trap-graveyard")
async def get_trap_graveyard(
    response: Response,
    limit: int = Query(50, ge=1, le=100)
):
    """
    THE TRAP GRAVEYARD - All flagged picks (hook_risk OR suspect_line_bait)
    
    This endpoint collects ALL trapped picks across all boards.
    Users can browse what the system saved them from.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    sidecar = get_sidecar()
    
    all_trapped = []
    board_stats = {
        "safe_haven": {"trapped": 0, "total": 0},
        "war_zone": {"trapped": 0, "total": 0},
        "front_lines": {"trapped": 0, "total": 0}
    }
    
    if not sidecar or not sidecar.is_enabled():
        return {
            "picks": [],
            "total_trapped": 0,
            "board_stats": board_stats,
            "sidecar_enabled": False
        }
    
    # Collect from all boards
    try:
        # Safe Haven / Goblin Vault
        goblin_result = await engine.get_goblin_vault_static()
        if goblin_result and goblin_result.get("picks"):
            enriched = await sidecar.enrich_board_picks(goblin_result["picks"])
            board_stats["safe_haven"]["total"] = len(enriched)
            for pick in enriched:
                pick["source_board"] = "safe_haven"
                sc = pick.get("sidecar", {})
                if sc.get("hook_risk") or sc.get("suspect_line_bait"):
                    all_trapped.append(pick)
                    board_stats["safe_haven"]["trapped"] += 1
        
        # War Zone
        war_result = await engine.get_war_zone_static()
        if war_result and war_result.get("picks"):
            enriched = await sidecar.enrich_board_picks(war_result["picks"])
            board_stats["war_zone"]["total"] = len(enriched)
            for pick in enriched:
                pick["source_board"] = "war_zone"
                sc = pick.get("sidecar", {})
                if sc.get("hook_risk") or sc.get("suspect_line_bait"):
                    all_trapped.append(pick)
                    board_stats["war_zone"]["trapped"] += 1
        
        # Front Lines
        front_result = await engine.get_front_lines_static()
        if front_result and front_result.get("picks"):
            enriched = await sidecar.enrich_board_picks(front_result["picks"])
            board_stats["front_lines"]["total"] = len(enriched)
            for pick in enriched:
                pick["source_board"] = "front_lines"
                sc = pick.get("sidecar", {})
                if sc.get("hook_risk") or sc.get("suspect_line_bait"):
                    all_trapped.append(pick)
                    board_stats["front_lines"]["trapped"] += 1
        
    except Exception as e:
        logger.error(f"[TRAP_GRAVEYARD] Error collecting trapped picks: {e}")
    
    # Sort by trap severity (bait first, then hook)
    all_trapped.sort(
        key=lambda x: (
            1 if x.get("sidecar", {}).get("suspect_line_bait") else 0,
            1 if x.get("sidecar", {}).get("hook_risk") else 0
        ),
        reverse=True
    )
    
    return {
        "picks": all_trapped[:limit],
        "total_trapped": len(all_trapped),
        "board_stats": board_stats,
        "sidecar_enabled": True
    }


@router.get("/v3/parlay-builder")
async def get_parlay_builder_data():
    """
    Get all data needed for the parlay builder interface.
    
    Returns:
    - All available players with props
    - Demon and goblin tags
    - Correlation data for same-game parlays
    - DFS compliance info
    """
    engine = get_engine()
    result = await engine.picks_getter_service.get_parlay_builder()
    return result


@router.get("/v3/goblin-recon")
async def get_goblin_recon():
    """
    GOBLIN RECON - Scouted parlay combinations
    
    Returns pre-built parlay suggestions based on:
    - High-correlation player pairs
    - Optimal risk/reward ratios
    - Team stacking opportunities
    """
    engine = get_engine()
    result = await engine.picks_getter_service.get_goblin_recon()
    return result


@router.get("/v3/goblin-vault-live")
async def get_goblin_vault_live(
    limit: int = Query(10, ge=1, le=50)
):
    """
    STATELESS Goblin Vault - True Open Door Policy
    
    Fetches LIVE data on every request:
    1. Live props from Odds API
    2. Live game logs from NBA API
    3. Calculates hit rates in-memory
    4. Returns unified props format
    
    No database caching. Always fresh data.
    """
    from services.stateless_tier_service import get_stateless_tier_service
    service = get_stateless_tier_service()
    return await service.get_goblin_vault_live(limit=limit)
