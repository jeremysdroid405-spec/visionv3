"""
Tier Routes
===========
War Zone, Safe Haven (Goblin Vault), and Front Lines tier endpoints.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Tiers"])

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


@router.get("/v3/war-zone")
async def get_war_zone(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE WAR ZONE - High-risk Demon picks
    
    STATIC ROUTE: Simple MongoDB read, no JIT calculations.
    All tier calculations done during background sync.
    """
    # Prevent browser caching to ensure fresh photo data
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    result = await engine.get_war_zone_static()
    
    # Apply sidecar enrichment (hook/bait detection) - only if picks exist
    sidecar = get_sidecar()
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
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
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE SAFE HAVEN (Goblin Vault) - Safer Goblin picks (80%+ hit rate)
    
    STATIC ROUTE: Simple MongoDB read, no JIT calculations.
    All tier calculations done during background sync.
    """
    # Prevent browser caching to ensure fresh photo data
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    result = await engine.get_goblin_vault_static()
    
    # Apply sidecar enrichment (hook/bait detection) - only if picks exist
    sidecar = get_sidecar()
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
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
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    Alias for /v3/goblin-vault (user-friendly name).
    """
    return await get_goblin_vault(response=response, limit=limit, include_vision=include_vision)


@router.get("/v3/front-lines")
async def get_front_lines(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE FRONT LINES - Goblin picks with 60-79% hit rate
    
    STATIC ROUTE: Simple MongoDB read, no JIT calculations.
    All tier calculations done during background sync.
    """
    # Prevent browser caching to ensure fresh photo data
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    engine = get_engine()
    result = await engine.get_front_lines_static()
    
    # Apply sidecar enrichment (hook/bait detection) - only if picks exist
    sidecar = get_sidecar()
    if sidecar and sidecar.is_enabled() and result and result.get("picks"):
        try:
            result["picks"] = await sidecar.enrich_board_picks(result["picks"])
            result["sidecar_enabled"] = True
        except Exception as e:
            logger.warning(f"[SIDECAR] Front Lines enrichment failed: {e}")
            result["sidecar_enabled"] = False
    else:
        if result:
            result["sidecar_enabled"] = False
    
    return result


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
