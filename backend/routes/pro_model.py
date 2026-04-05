"""
Vegas Pro Model API Routes
===========================
Professional-grade regression model endpoints.

Uses scikit-learn for predictions and statsmodels for feature analysis.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import os
from pymongo import MongoClient

from services.vegas_pro_model import VegasProModel, BacktestEngine, STAT_TYPES

router = APIRouter(prefix="/api/v3/pro-model", tags=["Vegas Pro Model"])

# MongoDB connection
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Global model instance (load on startup)
_model: Optional[VegasProModel] = None


def get_model() -> VegasProModel:
    """Get or initialize the model."""
    global _model
    if _model is None:
        _model = VegasProModel(db)
        try:
            _model.load_models()
        except Exception as e:
            pass  # Models not trained yet
    return _model


class PredictRequest(BaseModel):
    player_name: str
    stat_type: str
    line: Optional[float] = None


class TrainRequest(BaseModel):
    stat_types: Optional[List[str]] = None
    model_type: str = "ridge"  # "linear", "ridge", "lasso"
    use_significant_only: bool = True


@router.get("/status")
async def model_status():
    """Check which models are trained and their metrics."""
    model = get_model()
    
    status = {
        "models_loaded": list(model.models.keys()),
        "metrics": {}
    }
    
    for stat_type in STAT_TYPES:
        if stat_type in model.metrics:
            m = model.metrics[stat_type]
            status["metrics"][stat_type] = {
                "samples": m.get("n_samples"),
                "features": m.get("n_features"),
                "test_mae": m.get("test", {}).get("mae"),
                "test_r2": m.get("test", {}).get("r2"),
                "cv_mae": m.get("cv_mae"),
            }
    
    return status


@router.post("/train")
async def train_models(request: TrainRequest, background_tasks: BackgroundTasks):
    """
    Train regression models for specified stat types.
    
    This analyzes feature significance using statsmodels,
    then trains scikit-learn models for prediction.
    """
    model = get_model()
    stat_types = request.stat_types or ['PTS', 'REB', 'AST', '3PM', 'PRA']
    
    results = {}
    
    for stat_type in stat_types:
        if stat_type not in STAT_TYPES:
            results[stat_type] = {"error": f"Invalid stat type. Use: {STAT_TYPES}"}
            continue
        
        try:
            metrics = model.train(
                stat_type,
                use_significant_only=request.use_significant_only,
                model_type=request.model_type
            )
            results[stat_type] = {
                "success": True,
                "samples": metrics.get("n_samples"),
                "features": metrics.get("features_used"),
                "test_mae": metrics.get("test", {}).get("mae"),
                "test_r2": metrics.get("test", {}).get("r2"),
                "cv_mae": metrics.get("cv_mae"),
                "feature_importance": metrics.get("feature_importance")
            }
        except Exception as e:
            results[stat_type] = {"error": str(e)}
    
    # Save models in background
    background_tasks.add_task(model.save_models)
    
    return {
        "success": True,
        "model_type": request.model_type,
        "results": results
    }


@router.post("/analyze-features/{stat_type}")
async def analyze_features(stat_type: str):
    """
    Analyze feature significance for a stat type using statsmodels.
    
    Returns P-values for each feature. Features with P > 0.05 
    are not statistically significant predictors.
    """
    if stat_type not in STAT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid stat type. Use: {STAT_TYPES}")
    
    model = get_model()
    
    try:
        analysis = model.analyze_features(stat_type)
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict")
async def predict_single(request: PredictRequest):
    """
    Get ML prediction for a single player prop.
    
    Uses trained scikit-learn model for the stat type.
    """
    model = get_model()
    
    if request.stat_type not in model.models:
        raise HTTPException(
            status_code=400, 
            detail=f"No trained model for {request.stat_type}. POST /train first."
        )
    
    try:
        result = model.predict(
            player_name=request.player_name,
            stat_type=request.stat_type,
            line=request.line
        )
        return {
            "success": True,
            "prediction": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/predict-tier/{tier}")
async def predict_tier(tier: str):
    """
    Run predictions for all picks in a tier.
    
    Compares ML predictions with the existing Board Score approach.
    """
    collection_map = {
        "safe_haven": "ferrari_safe_haven",
        "front_lines": "ferrari_front_lines",
        "war_zone": "ferrari_war_zone"
    }
    
    if tier not in collection_map:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Use: {list(collection_map.keys())}")
    
    model = get_model()
    picks = list(db[collection_map[tier]].find({}, {"_id": 0}))
    
    predictions = []
    
    for pick in picks:
        player = pick.get('player_name')
        stat = pick.get('stat_type')
        line = pick.get('line')
        board_score = pick.get('board_score', 0)
        l10_rate = pick.get('l10_rate', 0)
        
        if stat not in model.models:
            predictions.append({
                "player_name": player,
                "stat_type": stat,
                "error": f"No model for {stat}"
            })
            continue
        
        result = model.predict(player, stat, line)
        
        predictions.append({
            "player_name": player,
            "stat_type": stat,
            "line": line,
            # Board Score approach
            "board_score": board_score,
            "l10_rate": l10_rate,
            # ML approach
            "ml_predicted": result.get('predicted'),
            "ml_edge": result.get('edge'),
            "ml_prob_over": result.get('prob_over'),
            "ml_recommendation": result.get('recommendation'),
            # Model confidence
            "model_mae": result.get('model_metrics', {}).get('test_mae'),
        })
    
    # Sort by probability
    predictions.sort(key=lambda x: x.get('ml_prob_over', 0) or 0, reverse=True)
    
    return {
        "success": True,
        "tier": tier,
        "total_picks": len(picks),
        "predictions": predictions
    }


@router.get("/backtest/{stat_type}")
async def run_backtest(stat_type: str, n_games: int = 100):
    """
    Backtest model against historical games.
    
    Simulates predictions on past games where we know the outcome.
    """
    if stat_type not in STAT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid stat type. Use: {STAT_TYPES}")
    
    model = get_model()
    
    if stat_type not in model.models:
        raise HTTPException(
            status_code=400,
            detail=f"No trained model for {stat_type}. POST /train first."
        )
    
    try:
        engine = BacktestEngine(model)
        results = engine.run_backtest(stat_type, n_games)
        return {
            "success": True,
            "backtest": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feature-importance/{stat_type}")
async def get_feature_importance(stat_type: str):
    """
    Get feature importance (coefficients) for a trained model.
    """
    model = get_model()
    
    if stat_type not in model.feature_importance:
        raise HTTPException(
            status_code=400,
            detail=f"No trained model for {stat_type}. POST /train first."
        )
    
    return {
        "success": True,
        "stat_type": stat_type,
        "features": model.models.get(f"{stat_type}_features", []),
        "importance": model.feature_importance[stat_type],
        "metrics": model.metrics.get(stat_type, {})
    }


@router.get("/compare-approaches")
async def compare_approaches():
    """
    Compare Board Score vs ML Model predictions across all tiers.
    
    Shows where approaches agree (high confidence) and 
    where they disagree (potential opportunities or traps).
    """
    model = get_model()
    
    tiers = ["safe_haven", "front_lines", "war_zone"]
    comparison = {
        "summary": {
            "total_picks": 0,
            "ml_strong_over": 0,
            "ml_strong_under": 0,
            "ml_neutral": 0,
        },
        "tiers": {}
    }
    
    for tier in tiers:
        collection_name = f"ferrari_{tier}"
        picks = list(db[collection_name].find({}, {"_id": 0}))
        
        tier_data = {
            "picks": len(picks),
            "strong_over": 0,
            "lean_over": 0,
            "neutral": 0,
            "lean_under": 0,
            "strong_under": 0,
            "details": []
        }
        
        for pick in picks:
            player = pick.get('player_name')
            stat = pick.get('stat_type')
            line = pick.get('line')
            
            if stat not in model.models:
                continue
            
            result = model.predict(player, stat, line)
            rec = result.get('recommendation', 'NEUTRAL')
            
            if rec == 'STRONG_OVER':
                tier_data['strong_over'] += 1
                comparison['summary']['ml_strong_over'] += 1
            elif rec == 'LEAN_OVER':
                tier_data['lean_over'] += 1
            elif rec == 'STRONG_UNDER':
                tier_data['strong_under'] += 1
                comparison['summary']['ml_strong_under'] += 1
            elif rec == 'LEAN_UNDER':
                tier_data['lean_under'] += 1
            else:
                tier_data['neutral'] += 1
                comparison['summary']['ml_neutral'] += 1
            
            tier_data['details'].append({
                "player": player,
                "prop": f"{stat} @ {line}",
                "board_score": pick.get('board_score'),
                "l10_rate": pick.get('l10_rate'),
                "ml_predicted": result.get('predicted'),
                "ml_prob_over": result.get('prob_over'),
                "ml_recommendation": rec,
            })
        
        comparison['summary']['total_picks'] += len(picks)
        comparison['tiers'][tier] = tier_data
    
    return comparison
