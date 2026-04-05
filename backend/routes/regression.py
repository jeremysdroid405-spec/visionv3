"""
Vegas Regression API Routes
============================
Provides endpoints for the regression-based prediction model.

This is a PARALLEL system to the existing PropVision v7.2 Board Score.
Both approaches can be compared to find where they agree/disagree.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
from pymongo import MongoClient

from services.vegas_regression_model import VegasRegressionModel, calculate_vegas_edge

router = APIRouter(prefix="/api/v3/regression", tags=["Vegas Regression"])

# MongoDB connection
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]


class PredictionRequest(BaseModel):
    player_name: str
    stat_type: str
    line: float
    opponent: Optional[str] = None
    is_home: Optional[bool] = None
    rest_days: Optional[int] = 1


class BatchPredictionRequest(BaseModel):
    tier: Optional[str] = "all"  # "safe_haven", "front_lines", "war_zone", or "all"


@router.post("/predict")
async def predict_single(request: PredictionRequest):
    """
    Get regression prediction for a single player prop.
    
    Returns predicted stat value, edge vs line, and probability.
    """
    try:
        result = calculate_vegas_edge(
            db=db,
            player_name=request.player_name,
            stat_type=request.stat_type,
            line=request.line,
            opponent=request.opponent,
            is_home=request.is_home,
            rest_days=request.rest_days
        )
        
        return {
            "success": True,
            "player_name": request.player_name,
            "stat_type": request.stat_type,
            "line": request.line,
            "prediction": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/compare/all")
async def compare_all_tiers():
    """
    Compare all tiers at once.
    Returns summary of agreements/disagreements across all picks.
    """
    tiers = ["safe_haven", "front_lines", "war_zone"]
    all_results = {}
    total_agreements = 0
    total_disagreements = 0
    
    for tier in tiers:
        collection_name = f"ferrari_{tier}"
        picks = list(db[collection_name].find({}, {"_id": 0}))
        hub = db['nba_master_hub_2026']
        model = VegasRegressionModel(db)
        
        tier_comparisons = []
        tier_agreements = 0
        tier_disagreements = 0
        
        for pick in picks:
            player_name = pick.get('player_name')
            stat_type = pick.get('stat_type')
            line = pick.get('line')
            opponent = pick.get('opponent') or pick.get('opponent_abbr')
            board_score = pick.get('board_score', 0)
            
            player = hub.find_one({
                '$or': [
                    {'player_name': player_name},
                    {'display_name': player_name},
                ]
            })
            
            if player and player.get('bdl_game_logs'):
                logs = player['bdl_game_logs']
                result = model.predict(
                    game_logs=logs,
                    stat_type=stat_type,
                    opponent_team=opponent,
                    line=line
                )
                
                prob_over = result.get('prob_over', 50)
                
                if prob_over >= 60:
                    agreement = "AGREE"
                    tier_agreements += 1
                elif prob_over >= 45:
                    agreement = "NEUTRAL"
                else:
                    agreement = "DISAGREE"
                    tier_disagreements += 1
                
                tier_comparisons.append({
                    "player": player_name,
                    "prop": f"{stat_type} @ {line}",
                    "board_score": board_score,
                    "predicted": result.get('predicted'),
                    "prob_over": prob_over,
                    "agreement": agreement
                })
        
        all_results[tier] = {
            "picks": len(picks),
            "agreements": tier_agreements,
            "disagreements": tier_disagreements,
            "agreement_rate": round(tier_agreements / len(picks) * 100, 1) if picks else 0,
            "comparisons": tier_comparisons
        }
        
        total_agreements += tier_agreements
        total_disagreements += tier_disagreements
    
    total_picks = sum(r['picks'] for r in all_results.values())
    
    return {
        "success": True,
        "summary": {
            "total_picks": total_picks,
            "total_agreements": total_agreements,
            "total_disagreements": total_disagreements,
            "overall_agreement_rate": round(total_agreements / total_picks * 100, 1) if total_picks else 0
        },
        "tiers": all_results
    }


@router.get("/compare/{tier}")
async def compare_tier(tier: str):
    """
    Compare Board Score picks with Regression predictions.
    
    Shows where both systems agree (high confidence) and 
    where they disagree (potential traps or opportunities).
    
    Args:
        tier: "safe_haven", "front_lines", or "war_zone"
    """
    collection_map = {
        "safe_haven": "ferrari_safe_haven",
        "front_lines": "ferrari_front_lines",
        "war_zone": "ferrari_war_zone"
    }
    
    if tier not in collection_map:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Use: {list(collection_map.keys())}")
    
    try:
        picks = list(db[collection_map[tier]].find({}, {"_id": 0}))
        hub = db['nba_master_hub_2026']
        model = VegasRegressionModel(db)
        
        comparisons = []
        agreements = 0
        disagreements = 0
        
        for pick in picks:
            player_name = pick.get('player_name')
            stat_type = pick.get('stat_type')
            line = pick.get('line')
            opponent = pick.get('opponent') or pick.get('opponent_abbr')
            board_score = pick.get('board_score', 0)
            l10_rate = pick.get('l10_rate', 0)
            l5_rate = pick.get('l5_rate', 0)
            
            # Find player in hub
            player = hub.find_one({
                '$or': [
                    {'player_name': player_name},
                    {'display_name': player_name},
                ]
            })
            
            regression_result = {"error": "Player not found"}
            agreement = "unknown"
            
            if player and player.get('bdl_game_logs'):
                logs = player['bdl_game_logs']
                regression_result = model.predict(
                    game_logs=logs,
                    stat_type=stat_type,
                    opponent_team=opponent,
                    line=line
                )
                
                # Determine agreement
                prob_over = regression_result.get('prob_over', 50)
                
                # Board Score approach says this is a good pick (in Safe Haven = high confidence)
                # Regression confirms if P(Over) > 60%
                if prob_over >= 60:
                    agreement = "AGREE"
                    agreements += 1
                elif prob_over >= 45:
                    agreement = "NEUTRAL"
                else:
                    agreement = "DISAGREE"
                    disagreements += 1
            
            comparisons.append({
                "player_name": player_name,
                "stat_type": stat_type,
                "line": line,
                "opponent": opponent,
                # Board Score approach
                "board_score": board_score,
                "l10_rate": l10_rate,
                "l5_rate": l5_rate,
                # Regression approach
                "predicted": regression_result.get('predicted'),
                "edge": regression_result.get('edge'),
                "prob_over": regression_result.get('prob_over'),
                "recommendation": regression_result.get('recommendation'),
                "confidence": regression_result.get('confidence'),
                # Comparison
                "agreement": agreement
            })
        
        # Sort by agreement (DISAGREE first to highlight potential issues)
        comparisons.sort(key=lambda x: (
            0 if x['agreement'] == 'DISAGREE' else 1 if x['agreement'] == 'NEUTRAL' else 2
        ))
        
        return {
            "success": True,
            "tier": tier,
            "total_picks": len(picks),
            "agreements": agreements,
            "disagreements": disagreements,
            "comparisons": comparisons
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/flags")
async def get_regression_flags():
    """
    Get picks where Board Score and Regression strongly disagree.
    
    These are potential TRAPS (high Board Score but low P(Over))
    or OPPORTUNITIES (low Board Score but high P(Over)).
    """
    tiers = ["safe_haven", "front_lines", "war_zone"]
    hub = db['nba_master_hub_2026']
    model = VegasRegressionModel(db)
    
    traps = []  # High Board Score, Low P(Over)
    opportunities = []  # Low Board Score, High P(Over)
    
    for tier in tiers:
        collection_name = f"ferrari_{tier}"
        picks = list(db[collection_name].find({}, {"_id": 0}))
        
        for pick in picks:
            player_name = pick.get('player_name')
            stat_type = pick.get('stat_type')
            line = pick.get('line')
            opponent = pick.get('opponent') or pick.get('opponent_abbr')
            board_score = pick.get('board_score', 0)
            
            player = hub.find_one({
                '$or': [
                    {'player_name': player_name},
                    {'display_name': player_name},
                ]
            })
            
            if player and player.get('bdl_game_logs'):
                logs = player['bdl_game_logs']
                result = model.predict(
                    game_logs=logs,
                    stat_type=stat_type,
                    opponent_team=opponent,
                    line=line
                )
                
                prob_over = result.get('prob_over', 50)
                predicted = result.get('predicted', 0)
                
                flag_data = {
                    "player_name": player_name,
                    "stat_type": stat_type,
                    "line": line,
                    "opponent": opponent,
                    "tier": tier,
                    "board_score": board_score,
                    "predicted": predicted,
                    "prob_over": prob_over,
                    "edge": result.get('edge'),
                    "recommendation": result.get('recommendation')
                }
                
                # TRAP: Board says Safe Haven but Regression says < 50%
                if tier == "safe_haven" and prob_over < 50:
                    flag_data["flag_type"] = "POTENTIAL_TRAP"
                    flag_data["reason"] = f"Safe Haven pick but only {prob_over}% P(Over)"
                    traps.append(flag_data)
                
                # Also flag if strong disagreement in any tier
                elif board_score > 350 and prob_over < 45:
                    flag_data["flag_type"] = "BOARD_REGRESSION_DIVERGENCE"
                    flag_data["reason"] = f"Board Score {board_score} but {prob_over}% P(Over)"
                    traps.append(flag_data)
    
    return {
        "success": True,
        "potential_traps": traps,
        "potential_opportunities": opportunities,
        "total_flags": len(traps) + len(opportunities)
    }
