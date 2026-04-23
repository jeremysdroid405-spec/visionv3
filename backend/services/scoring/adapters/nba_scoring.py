"""
NBA Scoring Adapter — reads dg_live_props (NBA's canonical layered-equivalent
collection) and produces a ScoringContext.

NOTE: NBA has not yet migrated to the exact-match canonical layered schema
used by MLB. Instead, dg_live_props stores PP as the primary row with a
`sharp_market` sub-document carrying dk/fd/betonline prices. This adapter
normalizes that shape into the standard ScoringContext.

NBA has a real `multiplier` field + `is_demon`/`is_goblin` flags sourced
from PP, so pp_utility gets actual multiplier-source data.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence

from services.scoring.adapters.base import ScoringAdapter, ScoringContext
from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


# `_NBAGateSorter` / `get_sorter` / `_build_sorter` were DELETED
# 2026-04-22 in the Universal Gate Engine cleanup pass. NBA scoring no
# longer maintains any sport-specific gate evaluator — gate decisions
# run through `services.scoring.gates.UniversalGateEngine` driven by
# config in `services.scoring.gates.thresholds.THRESHOLDS['nba']`.


class NBAScoringAdapter(ScoringAdapter):
    # Canonical stat-family → list of bdl_game_logs fields that sum to
    # produce the per-game value of that family. CV is computed from the
    # resulting distribution, so combo families (PRA, PTS_REB, etc.)
    # naturally inherit the variance of the COMBINED stat.
    _FAMILY_SPEC = {
        "pts":       ["pts"],
        "reb":       ["reb"],
        "ast":       ["ast"],
        "pra":       ["pts", "reb", "ast"],
        "threes":    ["fg3m"],
        "stl":       ["stl"],
        "blk":       ["blk"],
        "pts_reb":   ["pts", "reb"],
        "pts_ast":   ["pts", "ast"],
        "reb_ast":   ["reb", "ast"],
        "turnovers": ["turnover"],
    }

    # Legacy alias — kept for any direct callers still passing canonical
    # short stat types (PTS/REB/...). New code should go through
    # `_resolve_family` → `_FAMILY_SPEC`.
    _STAT_FIELD_MAP = {
        "PTS": "pts",
        "REB": "reb",
        "AST": "ast",
        "PRA": "pra",  # synthesized
        "3PM": "fg3m",
        "STL": "stl",
        "BLK": "blk",
        "TO": "turnover",
    }

    # Which stat_types have a Vegas Killer model on disk
    _MODEL_STATS = {"PTS", "REB", "AST", "3PM", "PRA"}
    # Canonical family (lowercase, from resolve_stat_family) → model key
    # (the UPPERCASE stat_type the VK / VK2 predictors internally expect).
    # Every raw market name — standard or alternate — routes to a family
    # by `_resolve_family`; families listed here inherit the trained
    # model without needing a new model file.
    _FAMILY_TO_MODEL_KEY = {
        "pts":    "PTS",
        "reb":    "REB",
        "ast":    "AST",
        "pra":    "PRA",
        "threes": "3PM",
    }
    # Combo-projection synthesis (2026-04-23). Each combo family is a
    # sum of N component families that DO have trained models. We
    # synthesize `proj_combo = Σ proj_i` with sigma from
    # `var_combo = Σ var_i + 2·Σ_{i<j} cov(i, j)`. No new training.
    # 2-way families are primary users of synth (no direct model
    # exists). 3-way (PRA) has a direct model — synth runs as a
    # fallback when the direct model fails (see `_SYNTH_FALLBACK_COMPONENTS`).
    _COMBO_COMPONENTS = {
        "pts_reb": ("PTS", "REB"),
        "pts_ast": ("PTS", "AST"),
        "reb_ast": ("REB", "AST"),
    }
    # Fallback synth spec: direct-model family → components to synth
    # from when the direct model returns no projection (missing
    # features, opponent-free rows, player not in model training set,
    # etc.). Ensures a useful projection reaches the score doc.
    _SYNTH_FALLBACK_COMPONENTS = {
        "pra": ("PTS", "REB", "AST"),
    }
    # Fallback correlation coefficient for combo sigma when we can't
    # compute empirical covariance from game logs (too few paired rows
    # or degenerate variance). Empirically, same-game NBA box-score
    # stats for the same player correlate mildly-positively; 0.25 is
    # deliberately conservative — it WIDENS sigma vs independence (0.0)
    # so the resulting p_over isn't overconfident.
    _COMBO_FALLBACK_RHO = 0.25
    # VK v2 model file paths (new 5-season weighted models w/ advanced stats)
    _VK2_DIR = "/app/backend/models"
    _VK2_FILE_MAP = {
        "PTS": "vk2_pts.pkl",
        "REB": "vk2_reb.pkl",
        "AST": "vk2_ast.pkl",
        "3PM": "vk2_3pm.pkl",
        "PRA": "vk2_pra.pkl",
    }

    # ---------------------------------------------------------------
    # Expected-minutes composition (2026-04-23).
    # Narrow rollout: apply to PTS / PRA ONLY. The segmented evaluation
    # (reports/vk2_expected_minutes_segmented.json) showed `blend_bench`
    # reduces low-line (<10) bias by ~14% on PTS and PRA with no RMSE
    # penalty (PRA RMSE also improves 7.33 → 7.22). REB / AST / 3PM
    # baseline is already near-zero bias so composition is intentionally
    # off for them.
    #
    # Strategy: when `min_played_L10_mean < _MIN_BENCH_THRESHOLD`, the
    # composed projection replaces the baseline VK2 projection as:
    #     composed = predicted_minutes * (stat_L5_mean / min_played_L5_mean)
    # Sigma is preserved (per-stat empirical residual SD). Gate/TP
    # logic untouched.
    # ---------------------------------------------------------------
    _EXPECTED_MINUTES_PATH = "/app/backend/models/nba_expected_minutes.pkl"
    _MIN_COMPOSITION_STATS = {"PTS", "PRA"}
    _MIN_BENCH_THRESHOLD = 20.0   # min_played_L10_mean cutoff
    _MIN_PER_MIN_RATE_CAP = 5.0   # sanity-clamp on historical per-min rate

    def __init__(self):
        self._cv_cache: dict = {}
        # ID-identity lookup (2026-04-23, Global Identity Rule).
        # Names are display-only; IDs are identity. `_logs_by_id` is
        # the ONLY game-log cache. There is no name-keyed fallback.
        self._logs_by_id: dict = {}      # bdl_player_id -> [logs]
        self._logs_loaded = False
        self._vk = None         # lazy-init legacy VegasKillerModel
        self._vk_sigmas: dict = {}   # stat_type -> residual SD (empirical, from test RMSE)
        self._vk2_loaded: bool = False
        self._vk2_models: dict = {}  # stat -> {model, scaler, features, sigma}
        self._vk2_adv_map: dict = {}      # (player_id, game_id) -> adv doc
        self._vk2_adv_loaded: bool = False
        # Combo-projection synthesis diagnostics (2026-04-23).
        # Incremented whenever `_predict_combo_projection` has to fall
        # back to the correlation-based sigma because empirical
        # covariance couldn't be computed. Inspected after each
        # recompute to monitor data-quality.
        self._combo_fallback_count: int = 0
        self._combo_success_count: int = 0
        # Expected-minutes model (loaded lazily, shared across predicts).
        self._min_model_loaded: bool = False
        self._min_model_payload: dict = {}
        # Observability counters for the blend_bench composition.
        self._min_composition_applied: int = 0
        self._min_composition_skipped_not_bench: int = 0
        self._min_composition_skipped_no_rate: int = 0
        self._min_composition_errors: int = 0

    @property
    def sport(self) -> str:
        return "nba"

    # ------------------------------------------------------------------
    # Delta Engine (Phase D1, 2026-04-21) — canonical_key_from_raw
    # ------------------------------------------------------------------
    # NBA's ingest path writes `_composite_key` on raw live_props rows
    # but does NOT persist the scoring-layer `canonical_key`. This
    # override derives the same key shape that `build_context` uses
    # (see line ~520) directly from the raw prop fields, so the delta
    # detector can set-diff live_props vs prop_scores without requiring
    # an ingest-side change. MUST stay in sync with the canon_key
    # formula in `build_context`.
    # ------------------------------------------------------------------
    _MARKET_TO_STAT = {
        "player_points": "PTS", "player_rebounds": "REB",
        "player_assists": "AST", "player_points_rebounds_assists": "PRA",
        "player_points_alternate": "PTS", "player_rebounds_alternate": "REB",
        "player_assists_alternate": "AST",
        "player_points_rebounds_assists_alternate": "PRA",
    }

    def canonical_key_from_raw(self, raw_prop):
        ck = raw_prop.get("canonical_key")
        if isinstance(ck, str) and ck:
            return ck
        player_name = raw_prop.get("player_name")
        line = raw_prop.get("line")
        market = raw_prop.get("market", "")
        stat_type = self._MARKET_TO_STAT.get(
            market, raw_prop.get("stat_type_extracted") or market
        )
        event_id = raw_prop.get("event_id", "?")
        direction = (raw_prop.get("direction") or "OVER").upper()
        side = "OVER" if "OVER" in direction else "UNDER"
        if player_name is None or line is None or not stat_type:
            return None
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            return None
        return f"nba|{event_id}|{player_name}|{stat_type}|{line_f}|{side}"

    @property
    def live_props_collection(self) -> str:
        return COLL("live_props", "nba")

    @property
    def scores_collection(self) -> str:
        return COLL("prop_scores", "nba")

    @property
    def cached_board_collection(self) -> str:
        return COLL("board_cache", "nba")

    async def load_live_props(self, db, limit: Optional[int] = None):
        cursor = db[self.live_props_collection].find({}, {"_id": 0})
        if limit:
            cursor = cursor.limit(int(limit))
        props = await cursor.to_list(length=None)
        logger.info(f"[NBA_SCORING] Loaded {len(props)} props from {self.live_props_collection}")

        # 0-Book Exclusion Rule (2026-04-22): any prop with no exact-line
        # anchor from DraftKings / FanDuel / BetMGM / BetOnline is marked
        # pp_only and MUST NOT enter scoring, tiering, or the cached
        # board. Applied here so every NBA scoring run — delta, master
        # sync, recompute — goes through the same filter.
        from services.scoring.coverage_filter import filter_priceable
        priceable, coverage_stats = filter_priceable(props, sport="nba")
        self.last_coverage_stats = coverage_stats

        # Multi-book de-vig TP engine companion map (2026-04-22).
        # Built over the full props list so UNDER-side TP still has an
        # OVER companion even when the OVER was pp_only-filtered.
        from services.scoring.tp_engine import build_companion_map
        self._companion_map = build_companion_map(props)
        return priceable

    def _get_vk(self, db):
        """Lazy-load the legacy VegasKillerModel + cache stat-specific residual SDs."""
        if self._vk is not None:
            return self._vk
        import os, pymongo
        from services.vegas_killer_model import VegasKillerModel
        sync_client = pymongo.MongoClient(os.environ.get("MONGO_URL"))
        sync_db = sync_client[os.environ.get("DB_NAME", "pick_vision")]
        vk = VegasKillerModel(sync_db)
        try:
            vk.load_models()
        except Exception as e:
            logger.warning(f"[NBA_SCORING] VegasKiller load failed: {e}")
            self._vk = vk
            return vk
        # Pull residual SDs from stored metadata. test_rmse is an empirical
        # estimate of model-residual SD on held-out data.
        for st in self._MODEL_STATS:
            m = (vk.metrics or {}).get(st, {})
            rmse = (m.get("test") or {}).get("rmse") or (m.get("train") or {}).get("rmse")
            if rmse and rmse > 0:
                self._vk_sigmas[st] = float(rmse)
        self._vk = vk
        logger.info(f"[NBA_SCORING] VegasKiller loaded. stat-sigmas={self._vk_sigmas}")
        return vk

    def _predict_model_prob_over(
        self, db, bdl_player_id: Optional[int], player_name: Optional[str],
        stat_type: str, line: float, opponent_team: Optional[str],
    ) -> Dict[str, Optional[float]]:
        """Run VegasKiller projection + convert to prob_over via empirical
        residual calibration.

        `bdl_player_id` is the canonical identity; `player_name` is
        passed through to VK's legacy name-based resolver pending a
        follow-up refactor to VK itself. When a prop has no
        `bdl_player_id`, caller must skip this path.

        Returns {projection, sigma, p_over, error?}."""
        if stat_type not in self._MODEL_STATS:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": f"no_model_for_{stat_type}"}
        if bdl_player_id is None:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "missing_bdl_player_id"}
        vk = self._get_vk(db)
        if not vk.models:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "vk_not_loaded"}
        try:
            r = vk.predict(
                player_name=player_name, stat_type=stat_type,
                line=line, opponent_team=opponent_team,
                bdl_player_id=bdl_player_id,
            )
        except Exception as e:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": f"predict_failed:{e}"}
        if r.get("error"):
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": r["error"]}
        projection = r.get("predicted")
        if projection is None:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "no_projection"}
        sigma = self._vk_sigmas.get(stat_type)
        if sigma is None or sigma <= 0:
            # Fallback: widen slightly to avoid overconfidence
            sigma = max(1.0, float(r.get("full_features", {}).get(
                "baseline", {}).get("std_dev_l10", 5) or 5))
        # ---- Expected-minutes composition (PTS / PRA only) --------
        # Path-agnostic: when the user-selected p_true_method is
        # "model" (legacy VK), we still want the bench-regime
        # composition from reports/vk2_expected_minutes_segmented.json
        # to apply. Features are rebuilt via the shared VK2 builder
        # so the bench detection and per-min rate match VK2 exactly.
        composition_meta: Dict[str, Any] = {}
        if stat_type in self._MIN_COMPOSITION_STATS:
            try:
                history = self._get_vk2_history_logs(bdl_player_id, window=20)
                if len(history) >= 5:
                    from services.scoring.nba_vk2_features import build_features
                    feats = build_features(
                        history_logs=history, target_game=history[0],
                        adv_map=self._vk2_adv_map or None,
                    )
                    if feats is not None:
                        composed = self._compose_minutes_adjusted_projection(
                            stat_type=stat_type,
                            baseline_projection=float(projection),
                            feats=feats,
                        )
                        if composed.get("composition_applied"):
                            composition_meta = {
                                "minutes_composition_applied": True,
                                "baseline_projection": round(float(projection), 3),
                                "composed_from_minutes": composed["composed_from_minutes"],
                                "per_min_rate": composed["per_min_rate"],
                                "min_played_L10_mean": round(
                                    composed["min_played_L10_mean"], 3,
                                ),
                            }
                            projection = composed["projection"]
            except Exception as e:
                logger.warning(
                    f"[NBA_SCORING] legacy-VK composition failed {stat_type}: {e}"
                )
        # Normal CDF: P(stat > line) = 1 - Phi((line - mu) / sigma) = Phi((mu - line) / sigma)
        from math import erf, sqrt
        z = (float(projection) - float(line)) / float(sigma)
        p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))
        out = {
            "projection": round(float(projection), 3),
            "sigma": round(float(sigma), 3),
            "p_over": round(float(p_over), 4),
            "error": None,
        }
        if composition_meta:
            out.update(composition_meta)
        return out

    # -----------------------------------------------------------------
    # VK2 (5-year, adv-stat-enriched) models — parallel to legacy VK
    # -----------------------------------------------------------------
    def _load_vk2_models(self) -> None:
        if self._vk2_loaded:
            return
        import os, pickle
        for stat, fn in self._VK2_FILE_MAP.items():
            p = os.path.join(self._VK2_DIR, fn)
            if not os.path.exists(p):
                logger.warning(f"[NBA_SCORING] VK2 model missing: {p}")
                continue
            with open(p, "rb") as f:
                payload = pickle.load(f)
            self._vk2_models[stat] = {
                "model": payload["model"],
                "scaler": payload["scaler"],
                "features": list(payload["features"]),
                "sigma": float(payload["residual_sigma_empirical"]),
                "version": payload.get("version"),
                "feature_count": payload.get("feature_count"),
            }
        self._vk2_loaded = True
        logger.info(
            "[NBA_SCORING] VK2 loaded: "
            f"{[(s, self._vk2_models[s]['sigma'], self._vk2_models[s]['version']) for s in self._vk2_models]}"
        )

    # -----------------------------------------------------------------
    # Expected-minutes model (2026-04-23) — used only for PTS / PRA
    # when `min_played_L10_mean < _MIN_BENCH_THRESHOLD`.
    # -----------------------------------------------------------------
    def _load_min_model(self) -> None:
        if self._min_model_loaded:
            return
        import os
        import pickle
        path = self._EXPECTED_MINUTES_PATH
        if not os.path.exists(path):
            logger.warning(f"[NBA_SCORING] expected-minutes model missing: {path}")
            self._min_model_loaded = True  # don't retry every call
            return
        with open(path, "rb") as f:
            payload = pickle.load(f)
        self._min_model_payload = {
            "model": payload["model"],
            "scaler": payload["scaler"],
            "features": list(payload["features"]),
            "version": payload.get("version"),
        }
        self._min_model_loaded = True
        logger.info(
            f"[NBA_SCORING] expected-minutes model loaded: "
            f"version={self._min_model_payload['version']} "
            f"features={len(self._min_model_payload['features'])}"
        )

    def _build_minutes_feature_vector(self, feats: Dict[str, float]) -> Optional[list]:
        """Derive the 15-feat minutes-model input row from the shared VK2
        feature dict. Any missing moment aborts composition — we never
        feed zero-filled rows through the minutes model."""
        schema = self._min_model_payload.get("features") or []
        if not schema:
            return None
        row: List[float] = []
        m_mean_L5 = feats.get("min_played_L5_mean", 0.0)
        m_mean_L10 = feats.get("min_played_L10_mean", 0.0)
        m_mean_L20 = feats.get("min_played_L20_mean", 0.0)
        m_std_L5 = feats.get("min_played_L5_std", 0.0)
        m_std_L10 = feats.get("min_played_L10_std", 0.0)
        m_std_L20 = feats.get("min_played_L20_std", 0.0)
        # L3 not in VK2 schema; fallback to L5 as the closest recency signal.
        m_mean_L3 = feats.get("min_played_L3_mean", m_mean_L5)
        m_std_L3 = feats.get("min_played_L3_std", m_std_L5)
        # Approximate floor/ceiling from mean ± 2σ (clamped at 0) since we
        # don't have the raw minutes series at live-scoring time.
        floor_L20 = max(0.0, m_mean_L20 - 2.0 * m_std_L20)
        ceiling_L20 = m_mean_L20 + 2.0 * m_std_L20
        # Rate approximations via a normal-CDF using erf.
        from math import erf, sqrt
        def _norm_cdf(x, mean, std):
            std = max(std, 0.5)
            return 0.5 * (1.0 + erf((x - mean) / (std * sqrt(2.0))))
        dnp_rate_L20 = _norm_cdf(5.0, m_mean_L20, m_std_L20)
        low_rate_L10 = _norm_cdf(15.0, m_mean_L10, m_std_L10)
        appearances_L20 = 20.0 if m_mean_L20 > 0 else 0.0
        vals = {
            "min_L3_mean": m_mean_L3,
            "min_L5_mean": m_mean_L5,
            "min_L10_mean": m_mean_L10,
            "min_L20_mean": m_mean_L20,
            "min_L3_std": m_std_L3,
            "min_L5_std": m_std_L5,
            "min_L10_std": m_std_L10,
            "min_L20_std": m_std_L20,
            "min_L3_L10_diff": m_mean_L3 - m_mean_L10,
            "min_L5_L20_diff": m_mean_L5 - m_mean_L20,
            "min_floor_L20": floor_L20,
            "min_ceiling_L20": ceiling_L20,
            "min_dnp_rate_L20": dnp_rate_L20,
            "min_low_rate_L10": low_rate_L10,
            "appearances_L20": appearances_L20,
        }
        for f in schema:
            if f not in vals:
                return None
            row.append(float(vals[f]))
        return row

    def _predict_expected_minutes(self, feats: Dict[str, float]) -> Optional[float]:
        """Returns predicted next-game minutes, or None if model unavailable
        or features insufficient."""
        if not self._min_model_loaded:
            self._load_min_model()
        if not self._min_model_payload:
            return None
        row_list = self._build_minutes_feature_vector(feats)
        if row_list is None:
            return None
        import numpy as np
        row = np.asarray([row_list], dtype=np.float32)
        try:
            row_s = self._min_model_payload["scaler"].transform(row)
            pred = float(self._min_model_payload["model"].predict(row_s)[0])
        except Exception as e:
            logger.warning(f"[NBA_SCORING] minutes-model predict failed: {e}")
            return None
        # Clamp to a sane NBA range [0, 48]; models can occasionally
        # extrapolate slightly outside on tail inputs.
        return max(0.0, min(48.0, pred))

    def _compose_minutes_adjusted_projection(
        self, stat_type: str, baseline_projection: float,
        feats: Dict[str, float],
    ) -> Dict[str, Any]:
        """Apply the blend_bench strategy for PTS / PRA.

        When the player is in the bench regime (min_played_L10_mean <
        _MIN_BENCH_THRESHOLD), replace the baseline projection with
        `predicted_minutes * historical_per_min_rate`. Otherwise return
        the baseline unchanged. Sigma is never mutated here.

        Returns:
          {projection, composition_applied, composed_from_minutes,
           per_min_rate, min_played_L10_mean, error}
        """
        result = {
            "projection": baseline_projection,
            "composition_applied": False,
            "composed_from_minutes": None,
            "per_min_rate": None,
            "min_played_L10_mean": float(feats.get("min_played_L10_mean", 0.0)),
            "error": None,
        }
        if stat_type not in self._MIN_COMPOSITION_STATS:
            return result
        min_L10 = float(feats.get("min_played_L10_mean", 0.0))
        if min_L10 >= self._MIN_BENCH_THRESHOLD:
            self._min_composition_skipped_not_bench += 1
            return result
        # Bench regime — compute historical per-min rate from L5 means.
        if stat_type == "PTS":
            stat_L5 = float(feats.get("pts_L5_mean", 0.0))
        elif stat_type == "PRA":
            stat_L5 = float(feats.get("pra_L5_mean", 0.0))
            if stat_L5 <= 0:
                # Fallback: rebuild from component means.
                stat_L5 = (
                    float(feats.get("pts_L5_mean", 0.0))
                    + float(feats.get("reb_L5_mean", 0.0))
                    + float(feats.get("ast_L5_mean", 0.0))
                )
        else:
            return result
        min_L5 = float(feats.get("min_played_L5_mean", 0.0))
        if min_L5 <= 1.0 or stat_L5 <= 0.0:
            # No usable historical minutes → keep baseline.
            self._min_composition_skipped_no_rate += 1
            result["error"] = "insufficient_per_min_rate_inputs"
            return result
        per_min_rate = stat_L5 / min_L5
        per_min_rate = max(0.0, min(self._MIN_PER_MIN_RATE_CAP, per_min_rate))
        pred_minutes = self._predict_expected_minutes(feats)
        if pred_minutes is None:
            self._min_composition_errors += 1
            result["error"] = "minutes_model_unavailable"
            return result
        composed = pred_minutes * per_min_rate
        self._min_composition_applied += 1
        result.update({
            "projection": float(composed),
            "composition_applied": True,
            "composed_from_minutes": round(pred_minutes, 3),
            "per_min_rate": round(per_min_rate, 4),
        })
        return result

    async def _preload_vk2_adv_map(self, db) -> None:
        """Build {(player_id, game_id): adv_doc} for VK2 feature lookup.
        One-shot per recompute; mirrors retrain_nba_vk2.preload_advanced_stats."""
        if self._vk2_adv_loaded:
            return
        from services.scoring.nba_vk2_features import ADV_FIELDS
        proj = {"_id": 0, "player_id": 1, "game_id": 1}
        for f in ADV_FIELDS:
            proj[f] = 1
        cursor = db["bdl_advanced_stats"].find({}, proj)
        async for doc in cursor:
            pid = doc.get("player_id"); gid = doc.get("game_id")
            if pid is None or gid is None:
                continue
            self._vk2_adv_map[(pid, gid)] = doc
        self._vk2_adv_loaded = True
        logger.info(f"[NBA_SCORING] VK2 adv_map loaded rows={len(self._vk2_adv_map):,}")

    def _get_vk2_history_logs(
        self, bdl_player_id: Optional[int], window: int = 20
    ) -> List[Dict[str, Any]]:
        """Pull newest-first historical logs for a player from the ID-keyed
        master hub cache, ready for the VK2 feature builder.
        Master hub stores `bdl_player_id` (== historical `player_id`) so we
        remap it to `player_id` for adv_map matching."""
        raw = self._get_logs_by_id(bdl_player_id)
        if not raw:
            return []
        # Sort newest-first by date
        try:
            raw_sorted = sorted(raw, key=lambda g: str(g.get("date") or ""), reverse=True)
        except Exception:
            raw_sorted = list(raw)
        out = []
        for g in raw_sorted[:window]:
            g2 = dict(g)
            if g2.get("player_id") is None and g2.get("bdl_player_id") is not None:
                g2["player_id"] = g2["bdl_player_id"]
            out.append(g2)
        return out

    def _predict_vk2_prob_over(
        self, bdl_player_id: Optional[int], stat_type: str, line: float,
    ) -> Dict[str, Optional[float]]:
        """VK2 predict + erf-based p_over calibration using per-stat empirical sigma.
        Returns {projection, sigma, p_over, error?}."""
        if stat_type not in self._VK2_FILE_MAP:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": f"no_vk2_model_for_{stat_type}"}
        if not self._vk2_loaded:
            self._load_vk2_models()
        m = self._vk2_models.get(stat_type)
        if not m:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "vk2_not_loaded"}
        history = self._get_vk2_history_logs(bdl_player_id, window=20)
        if len(history) < 5:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "insufficient_history"}
        from services.scoring.nba_vk2_features import build_features
        feats = build_features(history_logs=history, target_game=history[0],
                               adv_map=self._vk2_adv_map or None)
        if feats is None:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "feature_build_failed"}
        import numpy as np
        row = np.asarray(
            [[feats.get(c, 0.0) for c in m["features"]]], dtype=np.float32,
        )
        try:
            row_s = m["scaler"].transform(row)
            projection = float(m["model"].predict(row_s)[0])
        except Exception as e:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": f"predict_failed:{e}"}
        # ---- Expected-minutes composition (PTS / PRA only) --------
        # Narrow rollout per reports/vk2_expected_minutes_segmented.json.
        composition_meta: Dict[str, Any] = {}
        try:
            composed = self._compose_minutes_adjusted_projection(
                stat_type=stat_type, baseline_projection=projection,
                feats=feats,
            )
            if composed.get("composition_applied"):
                composition_meta = {
                    "minutes_composition_applied": True,
                    "baseline_projection": round(projection, 3),
                    "composed_from_minutes": composed["composed_from_minutes"],
                    "per_min_rate": composed["per_min_rate"],
                    "min_played_L10_mean": round(
                        composed["min_played_L10_mean"], 3
                    ),
                }
                projection = composed["projection"]
        except Exception as e:
            logger.warning(
                f"[NBA_SCORING] minutes composition failed for {stat_type}: {e}"
            )
        sigma = float(m["sigma"])
        if sigma <= 0:
            return {"projection": round(projection, 3), "sigma": sigma,
                    "p_over": None, "error": "sigma_invalid"}
        from math import erf, sqrt
        z = (projection - float(line)) / sigma
        p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))
        out = {
            "projection": round(projection, 3),
            "sigma": round(sigma, 3),
            "p_over": round(p_over, 4),
            "error": None,
        }
        if composition_meta:
            out.update(composition_meta)
        return out

    # -----------------------------------------------------------------
    # Combo-family projection synthesis (2026-04-23)
    # -----------------------------------------------------------------
    # Builds a projection + sigma for combo stat families (pts_reb /
    # pts_ast / reb_ast) by summing the two component models' mean
    # predictions and widening sigma via empirical covariance drawn
    # from the player's recent game logs. No new model is trained;
    # we reuse the existing VK / VK2 predictors for each component.
    # Mathematically:
    #     proj_combo = proj_a + proj_b
    #     var_combo  = var_a + var_b + 2 * cov(a, b)
    #     sigma_combo = sqrt(var_combo)
    # -----------------------------------------------------------------
    _STAT_KEY_TO_LOG_FIELD = {
        "PTS": "pts", "REB": "reb", "AST": "ast", "3PM": "fg3m", "PRA": "pra",
    }

    def _empirical_covariance(
        self, bdl_player_id: Optional[int], key_a: str, key_b: str,
        window: int = 20,
    ) -> Optional[float]:
        """Sample covariance of two stats from the player's last-N
        game logs. Returns None if fewer than 5 paired observations
        or either series is degenerate (near-zero variance)."""
        field_a = self._STAT_KEY_TO_LOG_FIELD.get(key_a)
        field_b = self._STAT_KEY_TO_LOG_FIELD.get(key_b)
        if field_a is None or field_b is None:
            return None
        logs = self._get_logs_by_id(bdl_player_id)
        try:
            logs_sorted = sorted(
                logs, key=lambda g: str(g.get("date") or ""), reverse=True,
            )
        except Exception:
            logs_sorted = logs
        window_logs = logs_sorted[:window]
        paired: list = []
        for g in window_logs:
            a = g.get(field_a)
            b = g.get(field_b)
            if a is None or b is None:
                continue
            try:
                paired.append((float(a), float(b)))
            except (TypeError, ValueError):
                continue
        if len(paired) < 5:
            return None
        import numpy as np
        xs = np.array([p[0] for p in paired])
        ys = np.array([p[1] for p in paired])
        if xs.std(ddof=1) < 1e-6 or ys.std(ddof=1) < 1e-6:
            return None
        cov = float(np.cov(xs, ys, ddof=1)[0, 1])
        return cov

    def _predict_combo_projection(
        self, db, bdl_player_id: Optional[int], player_name: Optional[str],
        line: float, opponent_team: Optional[str], use_vk2: bool,
        components: Sequence[str],
    ) -> Dict[str, Any]:
        """Synthesize an N-way combo projection from component models.

        For arbitrary N ≥ 2 components:
          proj_combo = Σ proj_i
          var_combo  = Σ var_i + 2·Σ_{i<j} cov(i, j)

        Returns {projection, sigma, p_over, error, covariance_source,
        components, pairwise_covariances}. Supports both primary combo
        families (no direct model) and fallback synthesis for families
        whose direct model returned None (e.g., PRA → PTS+REB+AST).
        Empirical covariances are drawn from L20 game logs; any pair
        that can't be computed falls back to ρ·σ_i·σ_j (ρ=0.25) and
        the whole row is labelled `fallback_rho`.

        `bdl_player_id` is the identity key used for empirical
        covariance / VK2 history lookups. `player_name` is passed
        through to the legacy VK model (which still resolves by name
        internally — flagged for follow-up refactor).
        """
        if not components or len(components) < 2:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "too_few_components", "covariance_source": None}

        comp_results: List[Dict[str, Any]] = []
        for key in components:
            if use_vk2:
                r = self._predict_vk2_prob_over(
                    bdl_player_id=bdl_player_id, stat_type=key,
                    line=float(line),
                )
            else:
                r = self._predict_model_prob_over(
                    db=db, bdl_player_id=bdl_player_id,
                    player_name=player_name, stat_type=key,
                    line=float(line), opponent_team=opponent_team,
                )
            comp_results.append({"key": key, **r})

        if any(c.get("projection") is None or c.get("sigma") is None
               for c in comp_results):
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "component_prediction_failed",
                    "covariance_source": None,
                    "components": comp_results}

        pairwise: Dict[str, float] = {}
        any_fallback = False
        for i in range(len(comp_results)):
            for j in range(i + 1, len(comp_results)):
                ki, kj = comp_results[i]["key"], comp_results[j]["key"]
                si, sj = float(comp_results[i]["sigma"]), float(comp_results[j]["sigma"])
                cov = self._empirical_covariance(
                    bdl_player_id, ki, kj, window=20,
                )
                if cov is None:
                    cov = self._COMBO_FALLBACK_RHO * si * sj
                    any_fallback = True
                pairwise[f"{ki}_{kj}"] = round(float(cov), 4)

        covariance_source = "fallback_rho" if any_fallback else "empirical"
        if any_fallback:
            self._combo_fallback_count += 1
        else:
            self._combo_success_count += 1

        projection = sum(float(c["projection"]) for c in comp_results)
        var_combo = sum(float(c["sigma"]) ** 2 for c in comp_results)
        var_combo += 2.0 * sum(pairwise.values())

        if var_combo <= 0:
            var_combo = max(
                1.0, sum(float(c["sigma"]) ** 2 for c in comp_results),
            )
            covariance_source = "fallback_nonpositive_variance"
            self._combo_fallback_count += 1

        from math import erf, sqrt
        sigma = sqrt(var_combo)
        z = (projection - float(line)) / sigma
        p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))

        return {
            "projection": round(projection, 3),
            "sigma": round(sigma, 3),
            "p_over": round(p_over, 4),
            "error": None,
            "covariance_source": covariance_source,
            "components": [
                {"key": c["key"], "proj": c.get("projection"), "sigma": c.get("sigma")}
                for c in comp_results
            ],
            "pairwise_covariances": pairwise,
        }



    async def _preload_game_logs(self, db) -> None:
        """Pull NBA game logs from master hub once per recompute.

        ID-mapping refactor (2026-04-23, Global Identity Rule):
        Identity is strictly `bdl_player_id`. This preloader builds
        ONLY an ID-keyed store (`_logs_by_id`). No name indexes, no
        aliases, no fuzzy matching. The master hub's `bdl_id` is the
        canonical identity (fully populated across every hub row).
        Callers downstream receive `bdl_player_id` from the live-prop
        doc (stamped at ingest) and look up logs by ID.
        """
        if self._logs_loaded:
            return
        hub = db[COLL("master_hub", "nba")]
        cursor = hub.find(
            {"bdl_game_logs_count": {"$gt": 0}},
            {"bdl_id": 1, "bdl_player_id": 1, "bdl_game_logs": 1, "_id": 0},
        )
        count = 0
        async for doc in cursor:
            # Accept either field name — `bdl_id` is the canonical hub
            # column, `bdl_player_id` is the alias some rows carry.
            pid = doc.get("bdl_player_id") or doc.get("bdl_id")
            if pid is None:
                continue
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                continue
            self._logs_by_id[pid_int] = doc.get("bdl_game_logs") or []
            count += 1
        self._logs_loaded = True
        logger.info(
            f"[NBA_SCORING] Cached game logs by bdl_player_id: "
            f"{count} players indexed"
        )

    def _get_logs_by_id(
        self, bdl_player_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        """Canonical game-log lookup by `bdl_player_id`.

        GLOBAL IDENTITY RULE: no name-based fallback. If the prop has
        no `bdl_player_id`, the caller must mark
        `identity_status="missing_bdl_id"` and skip metric computation.
        Returns [] when the ID is None or unknown to the hub.
        """
        if bdl_player_id is None:
            return []
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return []
        return self._logs_by_id.get(pid) or []

    def _resolve_family(self, stat_type: Optional[str]) -> Optional[str]:
        """Canonical NBA stat-family for either a short stat_type (PTS)
        or a raw odds-market name (player_points_alternate).

        Uses the SHARED `resolve_stat_family` helper so the family
        mapping has one source of truth across CV + gate-threshold
        routing. Returns None when the stat has no canonical family
        yet (in which case CV is flagged unavailable_stat_family)."""
        from services.scoring.gates.thresholds import resolve_stat_family
        family = resolve_stat_family("nba", stat_type)
        # resolve_stat_family never returns None, so filter out the
        # non-family fallbacks explicitly.
        if not family or family in ("_default", ""):
            return None
        return family

    def _compute_cv_and_hit_rate(
        self, bdl_player_id: Optional[int], stat_type: str, line: float,
        direction: str = "OVER", window: int = 20,
    ):
        """Compute line-independent CV and line-aware hit_rate / ceiling.

        CV is derived from the player's underlying stat-family
        distribution and is IDENTICAL across every line / alt-line for
        the same (player, family). Hit-rate and ceiling-rate still
        depend on the line / side (they count games relative to the
        threshold).

        Returns:
          (cv, cv_status, hit_rate, ceiling_rate,
           hit_rate_over, hit_rate_under, hit_rate_status)

        `cv_status` and `hit_rate_status` share a state machine:
          * "computed"                    – real values produced
          * "unavailable_stat_family"     – no family spec yet
          * "missing_bdl_id"              – prop has no bdl_player_id
          * "missing_source_distribution" – fewer than 5 L20 games
                                            (or, for CV only, a
                                            degenerate zero-mean).
        HR and CV can disagree on one edge case: when mean is 0 across
        the window (e.g. a specialist with zero steals in L20), CV
        becomes None (degenerate) but HR is a legitimate 0% (the
        player never hit the line). In that case
        `hit_rate_status="computed"`, `cv_status="missing_source_distribution"`.
        """
        # Global Identity Rule (2026-04-23): no name-based fallback.
        # If the prop has no canonical ID, we cannot compute metrics.
        if bdl_player_id is None:
            return (None, "missing_bdl_id",
                    None, None, None, None,
                    "missing_bdl_id")
        family = self._resolve_family(stat_type)
        if family is None:
            return (None, "unavailable_stat_family",
                    None, None, None, None,
                    "unavailable_stat_family")
        fields = self._FAMILY_SPEC.get(family)
        if not fields:
            return (None, "unavailable_stat_family",
                    None, None, None, None,
                    "unavailable_stat_family")

        logs = self._get_logs_by_id(bdl_player_id)
        if not logs:
            return (None, "missing_source_distribution",
                    None, None, None, None,
                    "missing_source_distribution")

        try:
            logs_sorted = sorted(
                logs,
                key=lambda g: str(g.get("date") or ""),
                reverse=True,
            )
        except Exception:
            logs_sorted = logs
        window_logs = logs_sorted[:window]

        import numpy as np

        # Build per-game value as sum of family component fields.
        vals: List[float] = []
        for g in window_logs:
            per_field = [g.get(f) for f in fields]
            if any(v is None for v in per_field):
                continue
            vals.append(float(sum(per_field)))

        if len(vals) < 5:
            return (None, "missing_source_distribution",
                    None, None, None, None,
                    "missing_source_distribution")

        arr = np.array(vals)
        mean = float(arr.mean())
        if mean <= 0:
            cv = None
            cv_status = "missing_source_distribution"
        else:
            cv = round(float(arr.std(ddof=1) / mean), 4)
            cv_status = "computed"

        # Cache CV per (bdl_player_id, family) so downstream code paths
        # that re-query the same family on a different line read a
        # consistent value without re-traversing logs.
        self._cv_cache[(int(bdl_player_id), family)] = (cv, cv_status)

        # Side-aware hit rate (line-dependent). HR is a legitimate
        # value even when mean==0 — zero hit rate IS information.
        over_hits = int(sum(1 for v in vals if v > line))
        under_hits = int(sum(1 for v in vals if v <= line))
        hit_rate_over = round((over_hits / len(vals)) * 100.0, 1)
        hit_rate_under = round((under_hits / len(vals)) * 100.0, 1)
        hit_rate_status = "computed"

        side = (direction or "OVER").upper()
        hit_rate = hit_rate_under if "UNDER" in side else hit_rate_over

        # Side-aware ceiling: tail probability in the direction of the bet
        if "UNDER" in side:
            floor_thresh = min(line * 0.5, line - 0.5)
            tail_hits = int(sum(1 for v in vals if v <= floor_thresh))
        else:
            ceiling_thresh = max(line * 1.5, line + 0.5)
            tail_hits = int(sum(1 for v in vals if v >= ceiling_thresh))
        ceiling_rate = round((tail_hits / len(vals)) * 100.0, 1)

        return (cv, cv_status,
                hit_rate, ceiling_rate,
                hit_rate_over, hit_rate_under,
                hit_rate_status)

    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        # Ensure game logs loaded once
        await self._preload_game_logs(db)

        # Lazy-preload VK2 adv_map when model path requested
        active_method_early = (
            ((config or {}).get("override_config") or {})
            .get("vision_score", {})
            .get("p_true_method")
        ) or "model"
        if active_method_early == "vk2" and not self._vk2_adv_loaded:
            await self._preload_vk2_adv_map(db)

        player_name = prop.get("player_name")
        # GLOBAL IDENTITY RULE (2026-04-23): bdl_player_id is the sole
        # identity key for joins. `player_name` remains display-only.
        # Stamped at ingest time by `universal_odds_sync`; absence is
        # reported as `identity_status="missing_bdl_id"` on the score
        # doc and skips all ID-based metric computation.
        bdl_player_id_raw = prop.get("bdl_player_id")
        bdl_player_id: Optional[int] = None
        if bdl_player_id_raw is not None:
            try:
                bdl_player_id = int(bdl_player_id_raw)
            except (TypeError, ValueError):
                bdl_player_id = None
        identity_status = "resolved" if bdl_player_id is not None else "missing_bdl_id"
        # Hard Consolidation (2026-04-22): universal_odds_sync writes
        # `stat_type` (PTS/REB/AST/PRA) and `market_key` directly.
        # Prefer the persisted stat_type first; fall back to market-
        # name mapping for any legacy rows still carrying `market`.
        stat_type = prop.get("stat_type")
        if not stat_type:
            market = prop.get("market") or prop.get("market_key") or ""
            stat_type = {
                "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
                "player_points_rebounds_assists": "PRA",
                "player_points_alternate": "PTS", "player_rebounds_alternate": "REB",
                "player_assists_alternate": "AST",
                "player_points_rebounds_assists_alternate": "PRA",
            }.get(market, prop.get("stat_type_extracted") or market)

        line = prop.get("line")
        if player_name is None or line is None or not stat_type:
            return None

        direction = (prop.get("recommendation") or prop.get("direction") or "OVER").upper()
        side = "OVER" if "OVER" in direction else "UNDER"
        event_id = prop.get("event_id", "?")

        canon_key = (
            f"nba|{event_id}|{player_name}|{stat_type}|{float(line)}|{side}"
        )

        # PP layer (primary) — prefer pre-built `pp_layer` from universal
        # odds sync; fall back to the legacy flat `price` field.
        pp_layer = prop.get("pp_layer") or {
            "book": "prizepicks",
            "line": float(line),
            "odds": prop.get("pp_odds") or prop.get("price"),
        }

        # Book layers — prefer nested layers from universal_odds_sync,
        # fall back to flat odds fields or sharp_market (legacy shape).
        sm = prop.get("sharp_market") or {}
        dk_price = (prop.get("dk_layer") or {}).get("odds") or prop.get("dk_odds") or sm.get("draftkings_price")
        dk_layer = (
            {"book": "draftkings", "line": float(line), "odds": dk_price}
            if dk_price is not None else None
        )
        fd_price = (prop.get("fd_layer") or {}).get("odds") or prop.get("fd_odds") or sm.get("fanduel_price")
        mgm_layer = (
            {"book": "fanduel", "line": float(line), "odds": fd_price}
            if fd_price is not None else None
        )
        bo_price = (prop.get("bol_layer") or {}).get("odds") or prop.get("bol_odds") or sm.get("betonline_price")
        sharp_layer = (
            {"book": "betonline", "line": float(line), "odds": bo_price}
            if bo_price is not None else None
        )

        # Hit rates (embedded in prop as fallback)
        # NOTE: embedded upstream values are assumed to represent the SIDE of
        # the prop row (PP ships side-specific hit_rates). If that ever
        # changes, revisit here.
        hr = (prop.get("hit_rates") or {})
        season = hr.get("season") or {}
        l10 = hr.get("l10") or {}
        season_rate = season.get("hit_rate")
        l10_rate = l10.get("hit_rate")
        embedded_hit_rate = round(l10_rate * 100.0, 1) if l10_rate is not None else (
            round(season_rate * 100.0, 1) if season_rate is not None else None
        )

        # CV + SIDE-AWARE hit_rate + ceiling_rate — ID-based join only.
        (cv, cv_status, computed_hit_rate, ceiling_rate,
         hit_rate_over, hit_rate_under, hit_rate_status) = \
            self._compute_cv_and_hit_rate(
                bdl_player_id, stat_type, float(line),
                direction=side, window=20,
            )
        hit_rate = computed_hit_rate if computed_hit_rate is not None else embedded_hit_rate

        # -----------------------------------------------------------
        # p_true candidates
        #  1. p_true_hit_rate  = raw L20 rolling count (legacy default)
        #  2. p_true_model     = legacy VegasKiller regressor + empirical residual CDF
        #  3. p_true_vk2       = 5-year adv-stat VK2 regressor + per-stat sigma CDF
        # `p_true_method` from override_config selects the active p_model.
        # -----------------------------------------------------------
        p_true_hit_rate = (hit_rate / 100.0) if hit_rate is not None else None

        # Legacy VK path (always computed when a model stat, unless vk2 is active)
        p_true_model = None
        model_projection = None
        model_sigma = None
        # VK2 path (always computed when a model stat & method is vk2)
        p_true_vk2 = None
        vk2_projection = None
        vk2_sigma = None
        vk2_error = None
        # Family-based model routing (2026-04-23). Alternates and aliased
        # market names (e.g. `player_points_alternate`, `player_threes`)
        # inherit the projection model of their canonical family. The
        # line differs per prop; the model / sigma do not. The underlying
        # predictors (`_predict_model_prob_over`, `_predict_vk2_prob_over`)
        # still reject anything outside _MODEL_STATS / _VK2_FILE_MAP, so
        # we pass the RESOLVED uppercase model key, not the raw stat_type.
        resolved_family = self._resolve_family(stat_type) or ""
        model_key = self._FAMILY_TO_MODEL_KEY.get(resolved_family)
        projection_method: Optional[str] = None
        opponent_team = prop.get("opponent") or prop.get("away_team")
        use_vk2_path = (active_method_early == "vk2")
        # PRA dual-projection audit (2026-04-23): populate these when
        # both direct and synth come back with a projection so we can
        # evaluate them against actuals later. Live pipeline behaviour
        # is unchanged — `model_projection` / `model_sigma` still carry
        # whatever the live primary path chose.
        model_projection_direct: Optional[float] = None
        model_sigma_direct: Optional[float] = None
        model_projection_synth: Optional[float] = None
        model_sigma_synth: Optional[float] = None
        # Expected-minutes composition audit (2026-04-23). Populated
        # only when `_predict_vk2_prob_over` returned
        # `minutes_composition_applied=True` (i.e. PTS / PRA in the
        # bench regime). Stamped on the ScoringContext so the score
        # doc persists the baseline vs composed trail.
        minutes_composition_applied: Optional[bool] = None
        minutes_composition_baseline: Optional[float] = None
        minutes_composition_predicted_minutes: Optional[float] = None
        minutes_composition_per_min_rate: Optional[float] = None
        # Model predictions only when identity resolved. Propagate the
        # ID through every downstream scoring call.
        if model_key in self._MODEL_STATS and bdl_player_id is not None:
            if not use_vk2_path:
                mres = self._predict_model_prob_over(
                    db=db, bdl_player_id=bdl_player_id,
                    player_name=player_name, stat_type=model_key,
                    line=float(line), opponent_team=opponent_team,
                )
                p_over = mres.get("p_over")
                if p_over is not None:
                    p_true_model = round(
                        (1.0 - p_over) if side == "UNDER" else p_over, 4
                    )
                model_projection = mres.get("projection")
                model_sigma = mres.get("sigma")
                if mres.get("minutes_composition_applied"):
                    minutes_composition_applied = True
                    minutes_composition_baseline = mres.get("baseline_projection")
                    minutes_composition_predicted_minutes = mres.get("composed_from_minutes")
                    minutes_composition_per_min_rate = mres.get("per_min_rate")
                if model_projection is not None:
                    projection_method = "model"
                    model_projection_direct = model_projection
                    model_sigma_direct = model_sigma
            else:
                v2res = self._predict_vk2_prob_over(
                    bdl_player_id=bdl_player_id, stat_type=model_key,
                    line=float(line),
                )
                p_over_v2 = v2res.get("p_over")
                if p_over_v2 is not None:
                    p_true_vk2 = round(
                        (1.0 - p_over_v2) if side == "UNDER" else p_over_v2, 4
                    )
                vk2_projection = v2res.get("projection")
                vk2_sigma = v2res.get("sigma")
                vk2_error = v2res.get("error")
                if v2res.get("minutes_composition_applied"):
                    minutes_composition_applied = True
                    minutes_composition_baseline = v2res.get(
                        "baseline_projection"
                    )
                    minutes_composition_predicted_minutes = v2res.get(
                        "composed_from_minutes"
                    )
                    minutes_composition_per_min_rate = v2res.get(
                        "per_min_rate"
                    )
                if vk2_projection is not None:
                    projection_method = "model"
                    model_projection_direct = vk2_projection
                    model_sigma_direct = vk2_sigma

            synth_components = self._SYNTH_FALLBACK_COMPONENTS.get(resolved_family)
            if synth_components:
                # PRA audit: always run synth in parallel so we can
                # compare against the direct model row-by-row.
                cres_audit = self._predict_combo_projection(
                    db=db, bdl_player_id=bdl_player_id,
                    player_name=player_name, line=float(line),
                    opponent_team=opponent_team, use_vk2=use_vk2_path,
                    components=synth_components,
                )
                if cres_audit.get("projection") is not None:
                    model_projection_synth = cres_audit.get("projection")
                    model_sigma_synth = cres_audit.get("sigma")

                # Fallback wiring (unchanged): when direct missed,
                # promote synth to the LIVE projection so ranking /
                # p_true_model still have a real value.
                direct_proj = (
                    vk2_projection if use_vk2_path else model_projection
                )
                if direct_proj is None and model_projection_synth is not None:
                    p_over_c = cres_audit.get("p_over")
                    if p_over_c is not None:
                        p_true_model = round(
                            (1.0 - p_over_c) if side == "UNDER" else p_over_c, 4
                        )
                    model_projection = model_projection_synth
                    model_sigma = model_sigma_synth
                    projection_method = "combo_synth"
        elif resolved_family in self._COMBO_COMPONENTS and bdl_player_id is not None:
            # Primary combo synthesis (2026-04-23): pts_reb / pts_ast /
            # reb_ast — no direct trained model exists, synth is the
            # only path. Gate config is unchanged.
            cres = self._predict_combo_projection(
                db=db, bdl_player_id=bdl_player_id,
                player_name=player_name, line=float(line),
                opponent_team=opponent_team, use_vk2=use_vk2_path,
                components=self._COMBO_COMPONENTS[resolved_family],
            )
            if cres.get("projection") is not None:
                p_over_c = cres.get("p_over")
                if p_over_c is not None:
                    p_true_model = round(
                        (1.0 - p_over_c) if side == "UNDER" else p_over_c, 4
                    )
                model_projection = cres.get("projection")
                model_sigma = cres.get("sigma")
                projection_method = "combo_synth"

        # tp from reference-market implied prob (dk preferred, else fanduel).
        # Prices on dg_live_props are OVER-side American odds, so convert and
        # flip for UNDER picks to keep the gate mathematically side-aware
        # (mirrors the existing side-aware fix applied to gate_hit_rate).
        # ---- Multi-book de-vigged TP (2026-04-22) ----------------------
        # Single-prop path using `{book}_odds_opp` captured at extract
        # time. Per-book de-vigged average across every book that
        # quotes BOTH sides of the exact line. No 50% fallback.
        from services.scoring.tp_engine import compute_tp
        tp_result = compute_tp(prop=prop, side=side)
        tp = tp_result["tp"]
        tp_books_used = tp_result["tp_books_used"]
        tp_books_list = tp_result["tp_books_list"]
        tp_method = tp_result["tp_method"]
        tp_unavailable = tp is None
        prop["tp"] = tp
        prop["tp_books_used"] = tp_books_used
        prop["tp_books_list"] = tp_books_list
        prop["tp_method"] = tp_method
        prop["tp_unavailable"] = tp_unavailable

        # Select active method via the shared p_true ladder helper.
        # Canonical order: model → hit_rate → vk2 → fair.
        # `preferred_method` lets override_config.vision_score.p_true_method
        # jump any rung to the front (e.g. the legacy NBA "vk2" opt-in or
        # "hit_rate" A/B harness), preserving historical behaviour.
        # The "fair" rung uses market-implied tp so p_true_method is never
        # "none" when a reference market exists (Stage 2 invariant).
        # When only the fair rung fires, p_model = tp/100 → edge_pct = 0
        # (mathematically identical to the legacy "no signal" fallback).
        from services.scoring.scoring_stack import resolve_p_true_ladder
        p_model, p_true_method_used = resolve_p_true_ladder(
            p_true_model=p_true_model,
            p_true_hit_rate=p_true_hit_rate,
            p_true_vk2=p_true_vk2,
            tp=tp,
            preferred_method=active_method_early,
        )

        # Multi-book de-vig TP contract (2026-04-22): no 50% fallback.
        # When TP is unavailable:
        #   * edge_pct = None (not computable without a reference market)
        #   * gates receive tp=None, causing the TP gate to fail hard
        #     (see tiering/gate logic).
        if tp is None or p_model is None:
            edge_pct = None
        else:
            edge_pct = round(p_model * 100.0 - tp, 1)

        books = 1 + int(dk_layer is not None) + int(mgm_layer is not None) + int(sharp_layer is not None)

        # PP multiplier (REAL source for NBA)
        pp_multiplier = prop.get("multiplier")
        is_demon = bool(prop.get("is_demon"))
        is_goblin = bool(prop.get("is_goblin"))
        pp_label = (
            "demon" if is_demon else "goblin" if is_goblin
            else ("standard" if prop.get("prop_type") == "standard" else None)
        )

        return ScoringContext(
            canonical_key=canon_key, sport="nba", event_id=event_id,
            player_name=player_name, stat_type=stat_type, line=float(line),
            recommendation=side,
            pp_layer=pp_layer, dk_layer=dk_layer, mgm_layer=mgm_layer,
            sharp_layer=sharp_layer,
            p_model=p_model, cv=cv, cv_status=cv_status,
            hit_rate=hit_rate, hit_rate_status=hit_rate_status,
            edge_pct=edge_pct,
            tp=tp, ceiling_rate=ceiling_rate,
            books_available_count=books,
            raw_prop=prop,
            pp_combo_multiplier=pp_multiplier,
            pp_label=pp_label, pp_multiplier_model=None,
            p_true_hit_rate=p_true_hit_rate,
            p_true_model=p_true_model,
            p_true_method=p_true_method_used,
            model_projection=model_projection,
            model_sigma=model_sigma,
            p_true_vk2=p_true_vk2,
            vk2_projection=vk2_projection,
            vk2_sigma=vk2_sigma,
            vk2_error=vk2_error,
            hit_rate_over=hit_rate_over,
            hit_rate_under=hit_rate_under,
            projection_method=projection_method,
            # Global Identity Rule (2026-04-23): stamp both on the
            # scoring context so the score doc persists the identity
            # decision for downstream observability.
            bdl_player_id=bdl_player_id,
            identity_status=identity_status,
            # PRA dual-projection audit — both versions side by side
            # when the family has a synth recipe; None otherwise.
            model_projection_direct=model_projection_direct,
            model_sigma_direct=model_sigma_direct,
            model_projection_synth=model_projection_synth,
            model_sigma_synth=model_sigma_synth,
            projection_delta_abs=(
                round(abs(model_projection_direct - model_projection_synth), 3)
                if (model_projection_direct is not None
                    and model_projection_synth is not None) else None
            ),
            projection_delta_pct=(
                round(
                    abs(model_projection_direct - model_projection_synth)
                    / max(abs(model_projection_direct), 1e-6) * 100.0, 2,
                )
                if (model_projection_direct is not None
                    and model_projection_synth is not None) else None
            ),
            projection_compare_status=(
                "both_available"
                if (model_projection_direct is not None and model_projection_synth is not None)
                else "direct_only" if model_projection_direct is not None
                else "synth_only" if model_projection_synth is not None
                else "neither"
            ),
            projection_primary_method=projection_method,
            # Expected-minutes composition audit (2026-04-23).
            minutes_composition_applied=minutes_composition_applied,
            minutes_composition_baseline_projection=minutes_composition_baseline,
            minutes_composition_predicted_minutes=minutes_composition_predicted_minutes,
            minutes_composition_per_min_rate=minutes_composition_per_min_rate,
        )
