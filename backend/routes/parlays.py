"""
Parlay Routes - Parlay Builder and Validation
==============================================
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["parlays"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


class ValidateParlayRequest(BaseModel):
    picks: List[Dict[str, Any]]
    parlay_type: Optional[str] = "power_play"


@router.get("/parlay-builder")
async def get_parlay_builder() -> Dict[str, Any]:
    """Get pre-built parlay tickets for War Zone"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_parlay_builder()
        return result
    except Exception as e:
        logger.error(f"Error getting parlay builder: {e}")
        return {"success": False, "error": str(e), "parlays": {}}


@router.get("/goblin-recon")
async def get_goblin_recon() -> Dict[str, Any]:
    """Get pre-built parlay tickets for Safe Haven"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_goblin_recon()
        return result
    except Exception as e:
        logger.error(f"Error getting goblin recon: {e}")
        return {"success": False, "error": str(e), "parlays": {}}


@router.post("/validate-parlay")
async def validate_parlay(request: ValidateParlayRequest) -> Dict[str, Any]:
    """Validate a custom parlay against DFS rules"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.validate_parlay(request.picks, request.parlay_type)
        return result
    except Exception as e:
        logger.error(f"Error validating parlay: {e}")
        return {"success": False, "error": str(e), "valid": False}
