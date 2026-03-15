"""
Payout Routes
=============
Endpoints for payout calculations and estimates.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Dict, List, Any
import logging

from payout_engine import (
    calculate_payout_from_picks,
    estimate_payout,
    BASE_MULTIPLIERS
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Payouts"])


class PayoutRequest(BaseModel):
    picks: List[Dict[str, Any]] = Field(
        ..., 
        description="List of picks with player_name, stat_type, line, direction, is_demon, is_goblin"
    )


@router.post("/v3/calculate-payout")
async def calculate_payout(request: PayoutRequest):
    """
    Calculate live estimated payout for a slip.
    
    Each pick should have:
    - player_name: str
    - stat_type: str (PTS, REB, AST, etc.)
    - line: float (the line being played)
    - direction: str (over/under)
    - is_demon: bool (optional)
    - is_goblin: bool (optional)
    - standard_line: float (optional, for modifier calculation)
    
    Returns:
    - estimated_payout: The cumulative payout multiplier
    - legs: Details for each pick including asset_type and modifier
    - asset_breakdown: Count of demons, goblins, standards
    """
    try:
        result = calculate_payout_from_picks(request.picks)
        return result
    except Exception as e:
        logger.error(f"[PAYOUT] Error calculating payout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/payout-estimate")
async def get_payout_estimate(
    num_picks: int = Query(..., ge=2, le=6, description="Number of picks (2-6)"),
    demon_count: int = Query(0, ge=0, description="Number of demon picks"),
    goblin_count: int = Query(0, ge=0, description="Number of goblin picks")
):
    """
    Quick payout estimate based on slip composition.
    
    Use this for real-time UI updates as picks are added.
    
    Example:
    - /v3/payout-estimate?num_picks=3&demon_count=2&goblin_count=0 → ~9.8x
    - /v3/payout-estimate?num_picks=2&demon_count=1&goblin_count=1 → ~3.0x
    """
    if demon_count + goblin_count > num_picks:
        raise HTTPException(
            status_code=400, 
            detail="demon_count + goblin_count cannot exceed num_picks"
        )
    
    payout = estimate_payout(num_picks, demon_count, goblin_count)
    standard_count = num_picks - demon_count - goblin_count
    
    return {
        "num_picks": num_picks,
        "asset_breakdown": {
            "demons": demon_count,
            "goblins": goblin_count,
            "standards": standard_count
        },
        "base_multiplier": BASE_MULTIPLIERS.get(num_picks, 3.0),
        "estimated_payout": payout,
        "payout_display": f"{payout:.1f}x"
    }


@router.get("/v3/payout-table")
async def get_payout_table():
    """
    Get the full payout reference table.
    
    Shows base multipliers and example payouts for different compositions.
    """
    table = {
        "base_multipliers": BASE_MULTIPLIERS,
        "modifier_ranges": {
            "demon": {"min": 1.10, "max": 1.50, "average": 1.25},
            "standard": {"min": 0.95, "max": 1.05, "average": 1.00},
            "goblin": {"min": 0.70, "max": 0.90, "average": 0.80}
        },
        "examples": {
            "2_pick": {
                "all_standard": estimate_payout(2, 0, 0),
                "all_demons": estimate_payout(2, 2, 0),
                "all_goblins": estimate_payout(2, 0, 2),
                "mixed": estimate_payout(2, 1, 1)
            },
            "3_pick": {
                "all_standard": estimate_payout(3, 0, 0),
                "all_demons": estimate_payout(3, 3, 0),
                "all_goblins": estimate_payout(3, 0, 3),
                "2_demons_1_standard": estimate_payout(3, 2, 0)
            },
            "4_pick": {
                "all_standard": estimate_payout(4, 0, 0),
                "all_demons": estimate_payout(4, 4, 0),
                "3_demons_1_goblin": estimate_payout(4, 3, 1)
            },
            "6_pick": {
                "all_standard": estimate_payout(6, 0, 0),
                "all_demons": estimate_payout(6, 6, 0),
                "all_goblins": estimate_payout(6, 0, 6)
            }
        },
        "formula": "Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)"
    }
    return table
