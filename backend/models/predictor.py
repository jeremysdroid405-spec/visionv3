"""
Lasso-Weighted Prediction Engine v2
====================================
Standardized Linear Equation:
  Projection = Intercept + SUM( beta_i * (Feature_i - Mean_i) / Std_i )

Only computes the 40 survivor features per model. Zero wasted CPU.
Includes Vision Score = (Projection - Line) with High Edge flagging.
"""

import os
import json
import logging
import numpy as np
from typing import Dict, List, Optional
from itertools import combinations

logger = logging.getLogger(__name__)

DATA_DIR = "/app/backend/data"

CONFIDENCE_TIERS = [
    ("HIGH_FIDELITY", 0.35),
    ("MODERATE", 0.15),
    ("HIGH_VARIANCE", 0.0),
]

# (sport, player_type, target_stat) -> filename
MODEL_REGISTRY = {
    ("mlb", "batter", "hits"): "autofe_mlb_batter_hits.json",
    ("mlb", "batter", "total_bases"): "autofe_mlb_batter_total_bases.json",
    ("mlb", "batter", "rbi"): "autofe_mlb_batter_rbi.json",
    ("mlb", "batter", "runs"): "autofe_mlb_batter_runs.json",
    ("mlb", "pitcher", "p_k"): "autofe_mlb_pitcher_p_k.json",
    ("nba", "all", "pts"): "autofe_nba_all_pts.json",
    ("nba", "all", "reb"): "autofe_nba_all_reb.json",
    ("nba", "all", "ast"): "autofe_nba_all_ast.json",
    ("nba", "all", "fg3m"): "autofe_nba_all_fg3m.json",
    ("nba", "all", "pra"): "autofe_nba_all_pra.json",
}

# Stat type aliases: what PrizePicks calls it -> model target key
STAT_ALIASES = {
    "hits": "hits", "Hits": "hits",
    "total_bases": "total_bases", "Total Bases": "total_bases",
    "rbi": "rbi", "rbis": "rbi", "RBIs": "rbi",
    "runs": "runs", "Runs": "runs",
    "pitcher_strikeouts": "p_k", "Pitcher Strikeouts": "p_k", "p_k": "p_k",
    "pts": "pts", "points": "pts", "Points": "pts",
    "reb": "reb", "rebounds": "reb", "Rebounds": "reb",
    "ast": "ast", "assists": "ast", "Assists": "ast",
    "fg3m": "fg3m", "3pm": "fg3m", "Three Pointers Made": "fg3m", "3PM": "fg3m",
    "pra": "pra", "Pts+Rebs+Asts": "pra", "PRA": "pra",
}

HIGH_EDGE_THRESHOLD = 0.15  # 15% of line


class LassoPredictor:
    """Single-target Lasso model with StandardScaler normalization."""

    def __init__(self, model_path: str):
        with open(model_path) as f:
            data = json.load(f)

        self.sport = data["sport"]
        self.player_type = data["player_type"]
        self.target_stat = data["target_stat"]
        self.alpha = data["lasso_alpha"]
        self.intercept = data.get("lasso_intercept", 0.0)
        self.r_squared = data.get("r_squared", 0.01)
        self.raw_keys = sorted(data.get("raw_keys_found", []))

        self.scaler_means = data.get("scaler_means", {})
        self.scaler_scales = data.get("scaler_scales", {})

        self.survivor_names = []
        self.survivor_coefs = []
        for name, _, raw_coef in data["survivor_list"]:
            self.survivor_names.append(name)
            self.survivor_coefs.append(raw_coef)
        self.survivor_set = set(self.survivor_names)

        for tier_name, threshold in CONFIDENCE_TIERS:
            if self.r_squared >= threshold:
                self.confidence_tier = tier_name
                break
        else:
            self.confidence_tier = "HIGH_VARIANCE"

        logger.info(
            f"[LASSO] {self.sport}/{self.target_stat}: "
            f"{len(self.survivor_names)} survivors, R²={self.r_squared:.4f}, "
            f"intercept={self.intercept:.4f}, tier={self.confidence_tier}"
        )

    def predict(self, feature_vector: Dict[str, float]) -> Dict:
        """
        Projection = Intercept + SUM( beta_i * (Feature_i - Mean_i) / Std_i )
        """
        projection = self.intercept
        contributions = []

        for name, coef in zip(self.survivor_names, self.survivor_coefs):
            raw_val = feature_vector.get(name, 0.0)
            mean = self.scaler_means.get(name, 0.0)
            scale = self.scaler_scales.get(name, 1.0) or 1.0
            scaled_val = (raw_val - mean) / scale
            contrib = scaled_val * coef
            projection += contrib
            contributions.append({
                "feature": name,
                "raw_value": round(raw_val, 4),
                "scaled_value": round(scaled_val, 4),
                "coefficient": round(coef, 4),
                "contribution": round(contrib, 4),
            })

        contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)

        return {
            "projection": round(projection, 3),
            "confidence_tier": self.confidence_tier,
            "r_squared": round(self.r_squared, 4),
            "intercept": round(self.intercept, 4),
            "survivors_used": len(self.survivor_names),
            "top_contributors": contributions[:10],
        }


class SurvivorFeatureBuilder:
    """
    Optimized feature builder: only computes features in the survivor set.
    Stops processing anything outside the 40 required features.
    """

    MLB_IX_KEYS = [
        "at_bats", "hits", "hr", "bb", "k", "avg", "obp", "slg",
        "total_bases", "rbi", "runs", "plate_appearances",
        "fly_outs", "ground_outs", "stolen_bases",
    ]
    NBA_IX_KEYS = [
        "pts", "reb", "ast", "fga", "fgm", "fg3a", "fg3m",
        "fta", "ftm", "turnover", "usage_pct", "pace",
        "true_shooting_pct", "off_rating", "def_rating",
    ]

    def __init__(self, sport: str):
        self.sport = sport
        self.ix_keys = self.MLB_IX_KEYS if sport == "mlb" else self.NBA_IX_KEYS

    def build(
        self, game_logs: List[Dict], raw_keys: List[str], survivor_set: set
    ) -> Dict[str, float]:
        """Build ONLY the survivor features from chronological game logs.
        Filters out DNP games (min=='00' or fga==0 for NBA, at_bats==None for MLB)."""
        # DNP filter: remove games where player didn't actually play
        if self.sport == "nba":
            game_logs = [
                g for g in game_logs
                if g.get("min") not in ("00", "", "0", None)
                and (g.get("fga") or 0) > 0
            ]
        else:
            game_logs = [
                g for g in game_logs
                if g.get("at_bats") is not None or g.get("ip") is not None
            ]

        if len(game_logs) < 11:
            return {}

        n = len(game_logs)
        key_idx = {k: i for i, k in enumerate(raw_keys)}

        # Vectorize only the tail we need (max 10 games back + 6 for delta)
        tail = game_logs[-min(n, 12):]
        matrix = np.zeros((len(tail), len(raw_keys)))
        for i, log in enumerate(tail):
            for j, k in enumerate(raw_keys):
                val = log.get(k)
                matrix[i, j] = float(val) if val is not None else 0.0

        t = len(tail)
        features = {}

        # 1. prev_ features
        for j, k in enumerate(raw_keys):
            fname = f"prev_{k}"
            if fname in survivor_set:
                features[fname] = float(matrix[-1, j])

        # 2. Rolling L3/L5/L10 avg + std
        for window, label in [(3, "L3"), (5, "L5"), (10, "L10")]:
            if t >= window:
                w_data = matrix[-window:]
                for j, k in enumerate(raw_keys):
                    avg_name = f"{label}_avg_{k}"
                    std_name = f"{label}_std_{k}"
                    col = w_data[:, j]
                    if avg_name in survivor_set:
                        features[avg_name] = float(np.mean(col))
                    if std_name in survivor_set:
                        features[std_name] = float(np.std(col))

        # 3. delta3_ time-derivatives
        if t >= 6:
            recent_3 = matrix[-3:]
            prior_3 = matrix[-6:-3]
            for j, k in enumerate(raw_keys):
                dname = f"delta3_{k}"
                if dname in survivor_set:
                    features[dname] = float(
                        np.mean(recent_3[:, j]) - np.mean(prior_3[:, j])
                    )

        # 4. ix_ interactions (only survivor pairs)
        for k1, k2 in combinations(self.ix_keys, 2):
            iname = f"ix_{k1}_x_{k2}"
            if iname in survivor_set and k1 in key_idx and k2 in key_idx:
                features[iname] = float(
                    matrix[-1, key_idx[k1]] * matrix[-1, key_idx[k2]]
                )

        # 5. Momentum / Meta
        if t >= 10:
            for tgt in ["hits", "total_bases", "pts", "reb", "ast"]:
                if tgt in key_idx:
                    col = matrix[:, key_idx[tgt]]
                    l10 = col[-10:]
                    l10_avg = float(np.mean(l10))
                    l5 = col[-5:]
                    if "momentum_hit_rate_L5" in survivor_set:
                        features["momentum_hit_rate_L5"] = (
                            float(np.mean(l5 > l10_avg)) if l10_avg > 0 else 0.0
                        )
                    if "target_L10_avg" in survivor_set:
                        features["target_L10_avg"] = l10_avg
                    if "target_L5_avg" in survivor_set:
                        features["target_L5_avg"] = float(np.mean(l5))
                    if "target_L3_avg" in survivor_set:
                        features["target_L3_avg"] = float(np.mean(col[-3:]))
                    if "target_cv_L10" in survivor_set:
                        features["target_cv_L10"] = float(
                            np.std(l10) / (l10_avg + 1e-9)
                        )
                    break

        return features


class PropVisionLassoEngine:
    """Top-level engine: model loading, prediction, and Vision Score."""

    def __init__(self):
        self.models: Dict[str, LassoPredictor] = {}
        self.builders: Dict[str, SurvivorFeatureBuilder] = {}
        self._load_all_models()

    def _load_all_models(self):
        for (sport, ptype, target), filename in MODEL_REGISTRY.items():
            path = os.path.join(DATA_DIR, filename)
            if os.path.exists(path):
                mkey = f"{sport}_{target}"
                self.models[mkey] = LassoPredictor(path)
                if sport not in self.builders:
                    self.builders[sport] = SurvivorFeatureBuilder(sport)

    def resolve_model_key(self, sport: str, stat_type: str) -> Optional[str]:
        """Resolve a stat_type string to a model key."""
        target = STAT_ALIASES.get(stat_type, stat_type.lower().replace(" ", "_"))
        mkey = f"{sport}_{target}"
        if mkey in self.models:
            return mkey
        return None

    def predict_player(
        self,
        sport: str,
        target_stat: str,
        game_logs: List[Dict],
        player_name: str = "",
        line: float = None,
        playoff_intensity: bool = False,
    ) -> Optional[Dict]:
        """Full prediction with Vision Score.
        playoff_intensity: Override prev_fga to L10 avg (assumes starter volume)."""
        mkey = self.resolve_model_key(sport, target_stat)
        if not mkey:
            return {"error": f"No model for {sport}/{target_stat}"}

        model = self.models[mkey]
        builder = self.builders.get(sport)
        if not builder:
            return {"error": f"No feature builder for {sport}"}

        features = builder.build(game_logs, model.raw_keys, model.survivor_set)
        if not features:
            return {"error": "Insufficient game log history (need 11+ games)"}

        # Playoff Intensity Override: replace prev_fga with L10 avg if last game
        # was a rest/cooldown outlier (prev_fga < L10_avg * 0.6)
        if playoff_intensity and sport == "nba":
            prev_fga = features.get("prev_fga", 0)
            l10_fga = features.get("L10_avg_fga", prev_fga)
            if prev_fga < l10_fga * 0.6 and l10_fga > 10:
                features["prev_fga"] = l10_fga
                features["_playoff_override"] = True
        if not features:
            return {"error": "Insufficient game log history (need 11+ games)"}

        result = model.predict(features)
        result["player_name"] = player_name
        result["sport"] = sport
        result["target_stat"] = target_stat
        result["games_analyzed"] = len(game_logs)
        if features.get("_playoff_override"):
            result["playoff_intensity_applied"] = True

        # Vision Score
        if line is not None and line > 0:
            projection = result["projection"]
            vision_score = projection - line
            vision_pct = (vision_score / line) * 100
            is_high_edge = abs(vision_score) > (line * HIGH_EDGE_THRESHOLD)
            direction = "OVER" if vision_score > 0 else "UNDER"

            result["vision_score"] = {
                "line": line,
                "projection": projection,
                "edge": round(vision_score, 3),
                "edge_pct": round(vision_pct, 2),
                "direction": direction,
                "high_edge": is_high_edge,
                "high_edge_threshold": f"{HIGH_EDGE_THRESHOLD*100:.0f}%",
            }

        return result


_engine: Optional[PropVisionLassoEngine] = None


def get_lasso_engine() -> PropVisionLassoEngine:
    global _engine
    if _engine is None:
        _engine = PropVisionLassoEngine()
    return _engine
