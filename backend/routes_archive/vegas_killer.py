"""
Vegas Killer Model API Routes
==============================
Process-based prediction model endpoints.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import os
from pymongo import MongoClient

from services.vegas_killer_model import VegasKillerModel, FEATURE_CATEGORIES

router = APIRouter(prefix="/api/v3/vegas-killer", tags=["Vegas Killer Model"])

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]

_model: Optional[VegasKillerModel] = None


def get_model() -> VegasKillerModel:
    global _model
    if _model is None:
        _model = VegasKillerModel(db)
        try:
            _model.load_models()
        except:
            pass
    return _model


class PredictRequest(BaseModel):
    player_name: str
    stat_type: str
    line: Optional[float] = None
    opponent_team: Optional[str] = None
    team_total: Optional[float] = None


class TrainRequest(BaseModel):
    stat_types: Optional[List[str]] = None
    model_type: str = "ensemble"


@router.get("/status")
async def model_status():
    """Check Vegas Killer model status."""
    model = get_model()
    
    return {
        "models_loaded": list(model.models.keys()),
        "feature_categories": list(FEATURE_CATEGORIES.keys()),
        "total_features": sum(len(v) for v in FEATURE_CATEGORIES.values()),
        "metrics": {
            stat: {
                "samples": m.get("n_samples"),
                "features": m.get("n_features"),
                "test_mae": m.get("test", {}).get("mae"),
                "test_r2": m.get("test", {}).get("r2"),
            }
            for stat, m in model.metrics.items()
        }
    }


@router.post("/train")
async def train_models(request: TrainRequest, background_tasks: BackgroundTasks):
    """Train Vegas Killer models with process-based features."""
    model = get_model()
    stat_types = request.stat_types or ['PTS', 'REB', 'AST', '3PM', 'PRA']
    
    results = {}
    for stat_type in stat_types:
        try:
            metrics = model.train(stat_type, model_type=request.model_type)
            results[stat_type] = {
                "success": True,
                "samples": metrics.get("n_samples"),
                "features": metrics.get("n_features"),
                "test_mae": metrics.get("test", {}).get("mae"),
                "test_r2": metrics.get("test", {}).get("r2"),
            }
        except Exception as e:
            results[stat_type] = {"error": str(e)}
    
    background_tasks.add_task(model.save_models)
    
    return {"success": True, "results": results}


@router.post("/analyze-features/{stat_type}")
async def analyze_features(stat_type: str):
    """Analyze feature significance for a stat type."""
    model = get_model()
    
    try:
        analysis = model.analyze_features(stat_type)
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict")
async def predict_single(request: PredictRequest):
    """Get Vegas Killer prediction for a player prop."""
    model = get_model()
    
    if request.stat_type not in model.models:
        raise HTTPException(
            status_code=400,
            detail=f"No model for {request.stat_type}. POST /train first."
        )
    
    try:
        result = model.predict(
            player_name=request.player_name,
            stat_type=request.stat_type,
            line=request.line,
            opponent_team=request.opponent_team,
            team_total=request.team_total,
        )
        return {"success": True, "prediction": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/predict-tier/{tier}")
async def predict_tier(tier: str):
    """Run Vegas Killer predictions for all picks in a tier."""
    collection_map = {
        "safe_haven": "ferrari_safe_haven",
        "front_lines": "ferrari_front_lines",
        "war_zone": "ferrari_war_zone"
    }
    
    if tier not in collection_map:
        raise HTTPException(status_code=400, detail="Invalid tier")
    
    model = get_model()
    picks = list(db[collection_map[tier]].find({}, {"_id": 0}))
    
    predictions = []
    for pick in picks:
        player = pick.get('player_name')
        stat = pick.get('stat_type')
        line = pick.get('line')
        
        if stat not in model.models:
            predictions.append({"player_name": player, "error": f"No model for {stat}"})
            continue
        
        result = model.predict(player, stat, line)
        
        predictions.append({
            "player_name": player,
            "stat_type": stat,
            "line": line,
            "board_score": pick.get('board_score'),
            "l10_rate": pick.get('l10_rate'),
            "vk_predicted": result.get('predicted'),
            "vk_edge": result.get('edge'),
            "vk_prob_over": result.get('prob_over'),
            "vk_recommendation": result.get('recommendation'),
            "features": result.get('features'),
        })
    
    # Sort by probability
    predictions.sort(key=lambda x: x.get('vk_prob_over') or 0, reverse=True)
    
    return {
        "success": True,
        "tier": tier,
        "total_picks": len(picks),
        "predictions": predictions
    }


@router.get("/feature-breakdown")
async def feature_breakdown():
    """Show all feature categories used in Vegas Killer model."""
    return {
        "categories": FEATURE_CATEGORIES,
        "total_features": sum(len(v) for v in FEATURE_CATEGORIES.values()),
        "description": {
            "opportunity": "Volume stats - Usage Rate, Minutes, FGA, Free Throw Rate",
            "efficiency": "Quality stats - eFG%, TS%, 3PT%, Shooting Trends",
            "matchup": "Opponent friction - Def Rating, Pace, Points Allowed",
            "environment": "Fatigue factors - Rest, Home/Away, Schedule Density",
            "baseline": "Rolling averages - L3, L5, L10, Season, Volatility",
            "market": "Betting data - Line, Sharp Implied, Team Total",
        }
    }


@router.get("/compare-all")
async def compare_all():
    """Compare Vegas Killer predictions across all tiers."""
    model = get_model()
    
    tiers = ["safe_haven", "front_lines", "war_zone"]
    results = {
        "summary": {
            "total": 0,
            "strong_over": 0,
            "lean_over": 0,
            "neutral": 0,
            "lean_under": 0,
            "strong_under": 0,
        },
        "tiers": {}
    }
    
    for tier in tiers:
        collection_name = f"ferrari_{tier}"
        picks = list(db[collection_name].find({}, {"_id": 0}))
        
        tier_data = {"picks": len(picks), "breakdown": {}, "details": []}
        
        for rec in ["STRONG_OVER", "LEAN_OVER", "NEUTRAL", "LEAN_UNDER", "STRONG_UNDER"]:
            tier_data["breakdown"][rec] = 0
        
        for pick in picks:
            player = pick.get('player_name')
            stat = pick.get('stat_type')
            line = pick.get('line')
            
            if stat not in model.models:
                continue
            
            result = model.predict(player, stat, line)
            rec = result.get('recommendation', 'NEUTRAL')
            
            tier_data["breakdown"][rec] = tier_data["breakdown"].get(rec, 0) + 1
            results["summary"][rec.lower().replace("_", "_")] = results["summary"].get(rec.lower().replace("_", "_"), 0) + 1
            results["summary"]["total"] += 1
            
            tier_data["details"].append({
                "player": player,
                "prop": f"{stat} @ {line}",
                "predicted": result.get('predicted'),
                "prob_over": result.get('prob_over'),
                "recommendation": rec,
                "usg_rate": result.get('features', {}).get('usg_rate'),
                "ts_pct": result.get('features', {}).get('ts_pct'),
            })
        
        results["tiers"][tier] = tier_data
    
    return results



# =============================================================================
# BACKTESTING ENDPOINT
# =============================================================================

@router.get("/backtest/results")
async def get_backtest_results():
    """Get the latest backtest results."""
    import json
    
    try:
        with open('/app/backend/backtest_results.json', 'r') as f:
            results = json.load(f)
        
        # Calculate overall summary
        total_bets = sum(r.get('total_bets', 0) for r in results.values())
        total_wins = sum(r.get('wins', 0) for r in results.values())
        total_losses = sum(r.get('losses', 0) for r in results.values())
        
        overall_win_rate = total_wins / (total_wins + total_losses) * 100 if (total_wins + total_losses) > 0 else 0
        overall_roi = sum(r.get('roi_percent', 0) * r.get('total_bets', 0) for r in results.values()) / total_bets if total_bets > 0 else 0
        
        return {
            "success": True,
            "summary": {
                "total_bets": total_bets,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "overall_win_rate": round(overall_win_rate, 2),
                "overall_roi": round(overall_roi, 2),
                "break_even": 52.4,
                "edge_vs_break_even": round(overall_win_rate - 52.4, 2),
                "all_profitable": all(r.get('profitable', False) for r in results.values())
            },
            "by_stat": results
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "No backtest results found. Run the backtest first."
        }



# =============================================================================
# BOARD ENRICHMENT - Add Vegas Killer predictions to all props
# =============================================================================

@router.get("/enrich-board")
async def enrich_board_with_predictions():
    """
    Get all current props from the board and enrich with Vegas Killer predictions.
    
    This is the forward-looking integration - what should we bet TODAY.
    """
    model = get_model()
    
    # Get current props from the cached board
    board = db['dg_cached_board']
    players = list(board.find({}, {"_id": 0}).limit(200))
    
    if not players:
        return {
            "success": False,
            "error": "No props found in board. Run sync first."
        }
    
    enriched = []
    stat_map = {
        'points': 'PTS',
        'rebounds': 'REB',
        'assists': 'AST',
        'threes': '3PM',
        'pts+reb+ast': 'PRA',
        'pra': 'PRA',
        'three_pointers_made': '3PM',
    }
    
    for player in players:
        player_name = player.get('player_name')
        team = player.get('team')
        opponent = player.get('opponent')
        
        # Get props array
        props = player.get('props', [])
        
        for prop in props:
            stat_type_raw = prop.get('stat_type', '').lower()
            line = prop.get('line')
            
            if not line:
                continue
            
            # Map stat type
            stat_type = stat_map.get(stat_type_raw)
            if not stat_type:
                continue
            
            # Get prediction
            try:
                result = model.predict(
                    player_name=player_name,
                    stat_type=stat_type,
                    line=float(line),
                    opponent_team=opponent,
                )
                
                if 'error' not in result and result.get('predicted') is not None:
                    # Result is flat, not nested in 'prediction'
                    enriched_prop = {
                        "player_name": player_name,
                        "team": team,
                        "opponent": opponent,
                        "stat_type": stat_type,
                        "line": line,
                        "board_tier": prop.get('tier'),
                        "board_direction": prop.get('direction'),
                        "board_score": prop.get('board_score'),
                        # Vegas Killer predictions
                        "vk_predicted": float(result.get('predicted')),
                        "vk_edge": float(result.get('edge', 0)),
                        "vk_prob_over": float(result.get('prob_over', 50)),
                        "vk_prob_under": float(result.get('prob_under', 50)),
                        "vk_recommendation": result.get('recommendation'),
                        "vk_data_source": result.get('data_source'),
                        # Key features
                        "l5_avg": result.get('features', {}).get('l5_avg'),
                        "usage_rate": result.get('v2_advanced_stats', {}).get('usage_rate'),
                    }
                    
                    # Flag disagreements (traps)
                    board_dir = prop.get('direction', '').upper()
                    prob_over = result.get('prob_over', 50)
                    prob_under = result.get('prob_under', 50)
                    vk_dir = 'OVER' if prob_over > 55 else 'UNDER' if prob_under > 55 else 'NEUTRAL'
                    
                    if board_dir and vk_dir != 'NEUTRAL' and board_dir != vk_dir:
                        enriched_prop['trap_alert'] = True
                        enriched_prop['trap_reason'] = f"Board says {board_dir} but VK predicts {result.get('predicted')} ({vk_dir} {prob_over if vk_dir == 'OVER' else prob_under}%)"
                    else:
                        enriched_prop['trap_alert'] = False
                    
                    enriched.append(enriched_prop)
                    
            except Exception as e:
                logger.error(f"Error predicting {player_name} {stat_type}: {e}")
                continue
    
    # Sort by confidence
    enriched.sort(key=lambda x: max(x.get('vk_prob_over', 50), x.get('vk_prob_under', 50)), reverse=True)
    
    # Summary
    strong_overs = [p for p in enriched if p.get('vk_prob_over', 0) >= 65]
    strong_unders = [p for p in enriched if p.get('vk_prob_under', 0) >= 65]
    traps = [p for p in enriched if p.get('trap_alert')]
    
    return {
        "success": True,
        "total_props": len(enriched),
        "summary": {
            "strong_overs": len(strong_overs),
            "strong_unders": len(strong_unders),
            "traps_detected": len(traps),
        },
        "top_plays": enriched[:20],
        "traps": traps[:10],
        "all_predictions": enriched,
    }


@router.post("/predict-slate")
async def predict_full_slate(player_lines: List[dict]):
    """
    Predict a full slate of player props.
    
    Input: [{"player_name": "LeBron James", "stat_type": "PTS", "line": 25.5}, ...]
    """
    model = get_model()
    
    results = []
    
    for item in player_lines:
        player_name = item.get('player_name')
        stat_type = item.get('stat_type')
        line = item.get('line')
        
        if not all([player_name, stat_type, line]):
            continue
        
        try:
            result = model.predict(
                player_name=player_name,
                stat_type=stat_type,
                line=float(line),
            )
            
            if 'error' not in result:
                pred = result.get('prediction', {})
                results.append({
                    "player_name": player_name,
                    "stat_type": stat_type,
                    "line": line,
                    "predicted": pred.get('predicted'),
                    "edge": pred.get('edge'),
                    "prob_over": pred.get('prob_over'),
                    "prob_under": pred.get('prob_under'),
                    "recommendation": pred.get('recommendation'),
                })
        except:
            continue
    
    # Sort by edge
    results.sort(key=lambda x: abs(x.get('edge', 0)), reverse=True)
    
    return {
        "success": True,
        "predictions": results,
        "best_overs": [r for r in results if r.get('prob_over', 0) >= 60][:5],
        "best_unders": [r for r in results if r.get('prob_under', 0) >= 60][:5],
    }
