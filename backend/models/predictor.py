"""
Lasso-Weighted Prediction Engine
=================================
Loads AutoFE survivor coefficients from /app/backend/data/autofe_*.json
Builds dynamic feature vectors from live game logs.
Produces dot-product projections with confidence scoring.
"""

import os
import json
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from itertools import combinations

logger = logging.getLogger(__name__)

DATA_DIR = "/app/backend/data"

# R² thresholds for confidence tiers
CONFIDENCE_TIERS = {
    "HIGH_FIDELITY": 0.35,
    "MODERATE": 0.15,
    "HIGH_VARIANCE": 0.0,
}

# Model registry: (sport, player_type, target_stat) -> filename
MODEL_REGISTRY = {
    ("mlb", "batter", "hits"): "autofe_mlb_batter_hits.json",
    ("mlb", "batter", "total_bases"): "autofe_mlb_batter_total_bases.json",
    ("nba", "all", "pts"): "autofe_nba_all_pts.json",
}


class LassoPredictor:
    """Single-target Lasso prediction model loaded from AutoFE JSON."""

    def __init__(self, model_path: str):
        with open(model_path) as f:
            data = json.load(f)

        self.sport = data["sport"]
        self.player_type = data["player_type"]
        self.target_stat = data["target_stat"]
        self.alpha = data["lasso_alpha"]
        self.intercept = data.get("lasso_intercept", 0.0)
        self.r_squared = data.get("r_squared", self._fallback_r2(data))
        self.raw_keys = sorted(data.get("raw_keys_found", []))

        # Scaler parameters for StandardScaler normalization
        self.scaler_means = data.get("scaler_means", {})
        self.scaler_scales = data.get("scaler_scales", {})

        # Build coefficient map: feature_name -> coefficient
        self.survivor_names = []
        self.survivor_coefs = []
        for name, abs_coef, raw_coef in data["survivor_list"]:
            self.survivor_names.append(name)
            self.survivor_coefs.append(raw_coef)
        self.coef_map = dict(zip(self.survivor_names, self.survivor_coefs))

        self.confidence_tier = self._compute_confidence()

        logger.info(
            f"[LASSO] Loaded {self.sport}/{self.target_stat}: "
            f"{len(self.survivor_names)} survivors, α={self.alpha:.6f}, "
            f"R²={self.r_squared:.4f}, intercept={self.intercept:.4f}, "
            f"scaler={'YES' if self.scaler_means else 'NO'}, tier={self.confidence_tier}"
        )

    def _fallback_r2(self, data):
        if self.sport == "nba" and self.target_stat == "pts":
            return 0.4863
        elif self.sport == "mlb" and self.target_stat == "hits":
            return 0.0355
        elif self.sport == "mlb" and self.target_stat == "total_bases":
            return 0.0301
        return 0.01

    def _compute_confidence(self) -> str:
        for tier, threshold in sorted(CONFIDENCE_TIERS.items(), key=lambda x: -x[1]):
            if self.r_squared >= threshold:
                return tier
        return "HIGH_VARIANCE"

    def predict(self, feature_vector: Dict[str, float]) -> Dict:
        """Run dot product prediction from feature vector.
        Applies StandardScaler normalization if scaler params available."""
        projection = self.intercept
        contributions = []

        for name, coef in zip(self.survivor_names, self.survivor_coefs):
            raw_val = feature_vector.get(name, 0.0)

            # Apply StandardScaler: (x - mean) / scale
            mean = self.scaler_means.get(name, 0.0)
            scale = self.scaler_scales.get(name, 1.0)
            if scale == 0:
                scale = 1.0
            scaled_val = (raw_val - mean) / scale if self.scaler_means else raw_val

            contrib = scaled_val * coef
            projection += contrib
            contributions.append({
                "feature": name,
                "raw_value": round(raw_val, 4),
                "scaled_value": round(scaled_val, 4),
                "coefficient": round(coef, 4),
                "contribution": round(contrib, 4),
            })

        # Sort by absolute contribution descending
        contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)

        return {
            "projection": round(projection, 3),
            "confidence_tier": self.confidence_tier,
            "r_squared": self.r_squared,
            "lasso_alpha": self.alpha,
            "intercept": round(self.intercept, 4),
            "survivors_used": len(self.survivor_names),
            "top_contributors": contributions[:10],
            "all_contributions": contributions,
        }


class DynamicFeatureBuilder:
    """Builds the 40 survivor features from raw game log history."""

    def __init__(self, sport: str):
        self.sport = sport
        if sport == "mlb":
            self.interaction_keys = [
                "at_bats", "hits", "hr", "bb", "k", "avg", "obp", "slg",
                "total_bases", "rbi", "runs", "plate_appearances",
                "fly_outs", "ground_outs", "stolen_bases",
            ]
        else:
            self.interaction_keys = [
                "pts", "reb", "ast", "fga", "fgm", "fg3a", "fg3m",
                "fta", "ftm", "turnover", "usage_pct", "pace",
                "true_shooting_pct", "off_rating", "def_rating",
            ]

    def build_features(self, game_logs: List[Dict], raw_keys: List[str]) -> Dict[str, float]:
        """Build all feature types from chronological game logs.
        Expects logs sorted oldest→newest, uses the tail for recent windows."""
        if len(game_logs) < 11:
            return {}

        n = len(game_logs)

        # Build numeric matrix
        matrix = []
        for log in game_logs:
            row = []
            for k in raw_keys:
                val = log.get(k)
                row.append(float(val) if val is not None else 0.0)
            matrix.append(row)
        matrix = np.array(matrix)

        features = {}
        key_idx = {k: i for i, k in enumerate(raw_keys)}

        # 1. Raw features from previous (last) game
        for j, k in enumerate(raw_keys):
            features[f"prev_{k}"] = matrix[-1, j]

        # 2. Rolling averages + volatility (L3, L5, L10)
        for window, label in [(3, "L3"), (5, "L5"), (10, "L10")]:
            if n >= window:
                window_data = matrix[-window:]
                for j, k in enumerate(raw_keys):
                    col = window_data[:, j]
                    features[f"{label}_avg_{k}"] = float(np.mean(col))
                    features[f"{label}_std_{k}"] = float(np.std(col))

        # 3. Time-derivatives (change in L3 avg vs prior L3 avg)
        if n >= 6:
            recent_3 = matrix[-3:]
            prior_3 = matrix[-6:-3]
            for j, k in enumerate(raw_keys):
                features[f"delta3_{k}"] = float(np.mean(recent_3[:, j]) - np.mean(prior_3[:, j]))

        # 4. Interaction features
        for k1, k2 in combinations(self.interaction_keys, 2):
            if k1 in key_idx and k2 in key_idx:
                features[f"ix_{k1}_x_{k2}"] = matrix[-1, key_idx[k1]] * matrix[-1, key_idx[k2]]

        # 5. Momentum / Meta features
        if n >= 10:
            # Need target stat values — use common targets
            for target_key in ["hits", "total_bases", "pts", "reb", "ast"]:
                if target_key in key_idx:
                    target_col = matrix[:, key_idx[target_key]]
                    l10_avg = float(np.mean(target_col[-10:]))
                    l5_vals = target_col[-5:]
                    features["momentum_hit_rate_L5"] = float(np.mean(l5_vals > l10_avg)) if l10_avg > 0 else 0.0
                    features["target_L10_avg"] = l10_avg
                    features["target_L5_avg"] = float(np.mean(l5_vals))
                    features["target_L3_avg"] = float(np.mean(target_col[-3:]))
                    features["target_cv_L10"] = float(np.std(target_col[-10:]) / (l10_avg + 1e-9))
                    break

        return features


class PropVisionLassoEngine:
    """Top-level engine that manages all Lasso models and runs predictions."""

    def __init__(self):
        self.models: Dict[str, LassoPredictor] = {}
        self.builders: Dict[str, DynamicFeatureBuilder] = {}
        self._load_all_models()

    def _load_all_models(self):
        for key, filename in MODEL_REGISTRY.items():
            path = os.path.join(DATA_DIR, filename)
            if os.path.exists(path):
                sport, player_type, target = key
                model_key = f"{sport}_{target}"
                self.models[model_key] = LassoPredictor(path)
                if sport not in self.builders:
                    self.builders[sport] = DynamicFeatureBuilder(sport)
                logger.info(f"[ENGINE] Loaded model: {model_key}")

    def get_available_models(self) -> List[str]:
        return list(self.models.keys())

    def predict_player(
        self,
        sport: str,
        target_stat: str,
        game_logs: List[Dict],
        player_name: str = "",
    ) -> Optional[Dict]:
        """Run prediction for a player given their game log history."""
        model_key = f"{sport}_{target_stat}"
        model = self.models.get(model_key)
        if not model:
            return {"error": f"No model for {model_key}"}

        builder = self.builders.get(sport)
        if not builder:
            return {"error": f"No feature builder for {sport}"}

        # Build feature vector from game logs
        features = builder.build_features(game_logs, model.raw_keys)
        if not features:
            return {"error": "Insufficient game log history (need 11+ games)"}

        # Run prediction
        result = model.predict(features)
        result["player_name"] = player_name
        result["sport"] = sport
        result["target_stat"] = target_stat
        result["games_analyzed"] = len(game_logs)

        return result


# Singleton
_engine: Optional[PropVisionLassoEngine] = None


def get_lasso_engine() -> PropVisionLassoEngine:
    global _engine
    if _engine is None:
        _engine = PropVisionLassoEngine()
    return _engine
