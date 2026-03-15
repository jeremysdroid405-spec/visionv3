"""
Tier Routes
===========
War Zone, Safe Haven (Goblin Vault), and Front Lines tier endpoints.
"""
from fastapi import APIRouter, HTTPException, Query
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Tiers"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None


def set_tier_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.get("/v3/war-zone")
async def get_war_zone(
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE WAR ZONE - High-risk Demon picks
    
    Returns top demon picks sorted by Expected Value (EV).
    These are aggressive plays with lines above season average.
    
    Display with red/orange gradient cards in the UI.
    """
    engine = get_engine()
    result = await engine.get_war_zone()
    return result


@router.get("/v3/goblin-vault")
async def get_goblin_vault(
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE SAFE HAVEN (Goblin Vault) - Safer Goblin picks
    
    Returns top goblin picks with high hit rates.
    These are consistent plays for building parlays.
    
    Display with green gradient cards in the UI.
    """
    engine = get_engine()
    result = await engine.get_goblin_vault()
    return result


@router.get("/v3/safe-haven")
async def get_safe_haven(
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    Alias for /v3/goblin-vault (user-friendly name).
    """
    return await get_goblin_vault(limit=limit, include_vision=include_vision)


@router.get("/v3/front-lines")
async def get_front_lines(
    limit: int = Query(50, ge=1, le=200),
    include_vision: bool = Query(True)
):
    """
    THE FRONT LINES - Balanced mix (50% Demons, 50% Goblins)
    
    Returns a balanced mix of demon and goblin picks.
    Good for users who want variety in their selections.
    
    Display with mixed gradient cards in the UI.
    """
    engine = get_engine()
    result = await engine.get_front_lines()
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
