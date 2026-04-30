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
import os
from typing import Any, Dict, List, Optional, Sequence

from services.scoring.adapters.base import ScoringAdapter, ScoringContext
from services.config.collection_names import COLL
from services.observability import log_silent_failure

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
    # 2026-04-28 — Stats where VK2 is the PRIMARY model path even when no
    # `override_config.vision_score.p_true_method` is supplied.
    # Promotion log:
    #   - 2026-04-28: AST  (after Jokic AST trace + dual-model audit)
    #   - 2026-04-28: REB, 3PM (per full-model-audit /tmp/nba_full_model_audit_REPORT.md)
    # PTS is intentionally NOT promoted yet — it touches recency blend,
    # rate × minutes, PRA synth, and shadow E. PTS runs as a SHADOW
    # column (`mu_pts_vk2`) for 7 days before any cutover decision.
    _VK2_PRIMARY_STATS = {"AST", "REB", "3PM"}
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
        from services.scoring.coverage_filter import (
            filter_priceable, filter_pp_playable,
        )
        priceable, coverage_stats = filter_priceable(props, sport="nba")
        self.last_coverage_stats = coverage_stats

        # Multi-book de-vig TP engine companion map (2026-04-22).
        # Built over the full props list so UNDER-side TP still has an
        # OVER companion even when the OVER was pp_only-filtered.
        from services.scoring.tp_engine import build_companion_map
        self._companion_map = build_companion_map(props)

        # PP Side-Aware Playability Filter (2026-05). Universal across
        # NBA / MLB / NFL — see coverage_filter.filter_pp_playable for
        # contract. Drops every sportsbook-fallback row whose exact
        # side PrizePicks did not list. Identical to the MLB
        # adapter's chokepoint so cross-sport rejection sets stay
        # symmetric.
        pp_playable, pp_stats = filter_pp_playable(priceable, sport="nba")
        self.last_pp_playable_stats = pp_stats
        return pp_playable

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

    # =========================================================================
    # 2026-04-27 — Recency-weighted μ blend for NBA PTS / PRA only.
    # =========================================================================
    # Forward-test audit (272 settled OVER picks) showed:
    #   • 100% of misses had μ > actual (one-tailed bias)
    #   • PRA + PTS account for 93% of misses
    #   • avg PE on misses: +8.56 pts
    #   • Shaedon Sharpe / Quentin Grimes / Harrison Barnes appeared 22x
    #     combined with μ ≈ L20 ceiling vs L5 ≈ 50% lower
    # Root cause: VK / VK2 weight L20-style features too heavily; recent
    # regression is ignored.
    #
    # Surgical override (PTS / PRA only — REB and AST are well-calibrated
    # and untouched):
    #     μ_new = 0.35 × L3
    #           + 0.30 × L10 median
    #           + 0.20 × L20 (or season_avg fallback)
    #           + 0.15 × μ_model
    #
    # MEDIAN, not mean, of L10 to suppress outliers.
    #
    # Minutes regression guard: when L3 minutes < 0.85 × L10 minutes,
    # blend further toward L5 (60% L5, 40% blended).  This is a soft
    # blend, not a hard override — preserves model authority on
    # confirmed full-minutes nights.
    # =========================================================================
    _RECENCY_BLEND_WEIGHTS = {
        "L3":     0.35,
        "L10MED": 0.30,
        "L20":    0.20,
        "MODEL":  0.15,
    }
    _RECENCY_TARGET_STATS = {"PTS", "PRA"}
    _MINUTES_REGRESSION_THRESHOLD = 0.85
    _MINUTES_REGRESSION_L5_WEIGHT = 0.60

    @classmethod
    def _stat_value_from_log(cls, log: Dict[str, Any], stat_type: str) -> Optional[float]:
        """Compute per-game value of a stat from one game log."""
        if stat_type == "PTS":
            v = log.get("pts")
            return float(v) if v is not None else None
        if stat_type == "PRA":
            try:
                p = log.get("pts"); r = log.get("reb"); a = log.get("ast")
                if p is None or r is None or a is None:
                    return None
                return float(p) + float(r) + float(a)
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def _compute_recency_baselines(
        cls,
        logs: List[Dict[str, Any]],
        stat_type: str,
        before_date: Optional[str] = None,
    ) -> Dict[str, Optional[float]]:
        """Compute L3 / L5 / L10 mean / L10 median / L20 from game logs,
        plus minutes baselines. Logs are filtered to strictly BEFORE
        `before_date` (ISO yyyy-mm-dd) when supplied so historical-replay
        callers don't leak the very game we're projecting."""
        if not logs:
            return {}
        if before_date:
            logs = [l for l in logs if (l.get("date") or "") < before_date]
        # Most recent first.
        logs = sorted(logs, key=lambda l: l.get("date") or "", reverse=True)
        vals = [cls._stat_value_from_log(l, stat_type) for l in logs]
        mins = [
            (float(l["min"]) if l.get("min") not in (None, "") else None)
            for l in logs
        ]

        def _avg(arr, n):
            s = [x for x in arr[:n] if x is not None]
            return (sum(s) / len(s)) if s else None

        def _median(arr, n):
            s = sorted([x for x in arr[:n] if x is not None])
            if not s:
                return None
            m = len(s)
            return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0

        return {
            "l3":         _avg(vals, 3),
            "l5":         _avg(vals, 5),
            "l10":        _avg(vals, 10),
            "l10_median": _median(vals, 10),
            "l20":        _avg(vals, 20),
            "min_l3":     _avg(mins, 3),
            "min_l5":     _avg(mins, 5),
            "min_l10":    _avg(mins, 10),
            "n_logs":     sum(1 for v in vals if v is not None),
        }

    @classmethod
    def _apply_recency_blend(
        cls,
        stat_type: str,
        mu_model: Optional[float],
        baselines: Dict[str, Optional[float]],
        season_avg: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Returns the blended μ + an audit dict, or `None` when the
        baselines aren't sufficient (need at least L3 + L10 median).

        Output keys:
          mu_blended, mu_raw_model, components_used, weights_applied,
          minutes_regression_applied, minutes_regression_factor, l5_used.
        """
        if stat_type not in cls._RECENCY_TARGET_STATS:
            return None
        if mu_model is None:
            return None

        l3 = baselines.get("l3")
        l10m = baselines.get("l10_median")
        l20 = baselines.get("l20")
        # L20 may be missing for early-season players; fall back to season_avg.
        l20_used = l20 if l20 is not None else season_avg
        # Need at least L3 and L10 median to apply the blend.
        if l3 is None or l10m is None:
            return None

        w = dict(cls._RECENCY_BLEND_WEIGHTS)
        # If L20 is unavailable, redistribute its weight onto L10 median
        # (the closest stable proxy) rather than the raw model.
        if l20_used is None:
            w["L10MED"] += w["L20"]
            w["L20"] = 0.0

        components = {
            "L3":     l3,
            "L10MED": l10m,
            "L20":    l20_used if l20_used is not None else 0.0,
            "MODEL":  float(mu_model),
        }
        wsum = sum(w[k] for k in components if components[k] is not None or k == "MODEL")
        # Normalize weights so they sum to 1.0 even when L20 was dropped.
        mu_blended = sum(components[k] * w[k] for k in components) / wsum

        # Minutes regression guard: shrink toward L5 when L3 minutes
        # are well below L10 minutes.
        min_l3 = baselines.get("min_l3")
        min_l10 = baselines.get("min_l10")
        l5 = baselines.get("l5")
        minutes_regressed = False
        minutes_factor = None
        if (
            min_l3 is not None and min_l10 is not None and l5 is not None
            and min_l10 >= 20  # only guard against meaningful minutes baselines
            and min_l3 < cls._MINUTES_REGRESSION_THRESHOLD * min_l10
        ):
            minutes_factor = min_l3 / min_l10 if min_l10 > 0 else None
            wL5 = cls._MINUTES_REGRESSION_L5_WEIGHT
            mu_blended = wL5 * l5 + (1.0 - wL5) * mu_blended
            minutes_regressed = True

        return {
            "mu_blended": round(mu_blended, 4),
            "mu_raw_model": round(float(mu_model), 4),
            "recency_l3":     l3,
            "recency_l10_median": l10m,
            "recency_l20":    l20_used,
            "recency_l5":     l5,
            "weights_applied": w,
            "minutes_regression_applied": minutes_regressed,
            "minutes_regression_factor":  minutes_factor,
            "minutes_l3":  min_l3,
            "minutes_l10": min_l10,
        }

    def _maybe_blend_recency(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_model: Optional[float],
    ) -> Optional[float]:
        """Apply the recency blend in-line during the score loop.
        Returns the new μ (or `mu_model` unchanged when the blend
        does not fire) and stamps audit fields onto `prop`."""
        if stat_type not in self._RECENCY_TARGET_STATS or mu_model is None:
            return mu_model
        if bdl_player_id is None:
            return mu_model
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_model
        # Cached per-player log slice.
        logs = (self._logs_by_id or {}).get(pid)
        if not logs:
            return mu_model
        # Use prop's commence_time as the "do not peek" cutoff so
        # historical replays don't include the resolved game.
        before_date = (prop.get("commence_time") or "")[:10] or None
        baselines = self._compute_recency_baselines(
            logs, stat_type, before_date=before_date,
        )
        season_avg = prop.get("season_avg") or prop.get("season_average")
        result = self._apply_recency_blend(
            stat_type=stat_type, mu_model=mu_model,
            baselines=baselines, season_avg=season_avg,
        )
        if result is None:
            return mu_model
        # Stamp audit fields on the raw prop so recompute mirrors them.
        prop["mu_raw_model_projection"] = result["mu_raw_model"]
        prop["mu_recency_blended"] = True
        prop["mu_recency_blend_l3"]         = result["recency_l3"]
        prop["mu_recency_blend_l10_median"] = result["recency_l10_median"]
        prop["mu_recency_blend_l20"]        = result["recency_l20"]
        prop["mu_recency_blend_l5"]         = result["recency_l5"]
        prop["mu_recency_blend_weights"]    = result["weights_applied"]
        prop["mu_minutes_regression_applied"] = result["minutes_regression_applied"]
        prop["mu_minutes_regression_factor"]  = result["minutes_regression_factor"]
        prop["mu_minutes_l3"]   = result["minutes_l3"]
        prop["mu_minutes_l10"]  = result["minutes_l10"]
        return result["mu_blended"]


    # =========================================================================
    # 2026-04-27 — Unified availability guard.
    # =========================================================================
    # Runs at the projection layer AFTER the recency blend, BEFORE the
    # universal probability engine.  Adjusts μ for *availability /
    # role / minutes restriction* — independent of the model's recency
    # weighting.
    #
    # μ_final = μ_recency × restriction_factor
    #
    # Status classification (priority order):
    #   1. DNP_RISK              factor 0.50–0.65
    #      • last game had 0 minutes
    #      • 2+ games with <5 min in last 5 (unstable role / coach DNPs)
    #   2. RETURNING_FROM_ABSENCE factor 0.70–0.90 by return_game_number
    #      • a gap of ≥ 5 days exists in the last 5 logs
    #      • return_game_number = games since the gap
    #   3. MINUTES_RESTRICTION    factor clamp(L3/L10, 0.55–0.90)
    #      • L3 minutes < 0.85 × L10 minutes (with L10 ≥ 20)
    #   4. FULL_GO                factor 1.0
    #
    # OUT/INACTIVE and QUESTIONABLE/GTD require a real-time injury feed
    # we don't have today; classification leaves them as FULL_GO until
    # the feed is wired in.  The score-doc carries `availability_status`
    # so an upstream feed adapter can override (`availability_status_override`
    # on the raw prop) without touching this code.
    # =========================================================================
    _AVAIL_GUARD_TARGET_STATS = {
        "PTS", "PRA", "REB", "AST",
        "P+R", "P+A", "R+A",
        "pts_reb", "pts_ast", "reb_ast",
    }
    # Sub-status factors for RETURNING (split 2026-04-27 refactor):
    #   • RESTRICTED — minutes still <80% of L10  → moderate penalty
    #   • SOFT       — minutes between 80% and 90%  → light penalty
    #   • FULL_GO    — minutes ≥ 90% of L10        → minimal/no penalty
    _AVAIL_RETURN_RESTRICTED_FACTORS = {1: 0.75, 2: 0.80, 3: 0.85}
    _AVAIL_RETURN_SOFT_FACTORS       = {1: 0.90, 2: 0.95, 3: 0.95}
    _AVAIL_RETURN_FULL_GO_FACTORS    = {1: 0.97, 2: 0.99, 3: 1.00}
    _AVAIL_MIN_RECOVERY_RESTRICTED = 0.80   # < this  → RETURNING_RESTRICTED
    _AVAIL_MIN_RECOVERY_FULL_GO    = 0.90   # ≥ this → RETURNING_FULL_GO
    _AVAIL_MIN_L10_FOR_RETURN_CLASSIFY = 18.0  # skip return logic on tiny samples
    # Hard universal clamp (per spec)
    _AVAIL_FACTOR_CLAMP_LO = 0.50
    _AVAIL_FACTOR_CLAMP_HI = 1.00
    # Legacy reference — left for back-compat of any external caller
    # peeking at the dict; the active path uses the split factors above.
    _AVAIL_RETURN_FACTORS = {1: 0.80, 2: 0.85, 3: 0.95}
    _AVAIL_DNP_FACTOR_LAST_GAME = 0.50    # 0 min last game
    _AVAIL_DNP_FACTOR_LOW_PATTERN = 0.65  # 2+ <5min in last 5
    _AVAIL_MIN_RESTRICT_FLOOR = 0.55
    _AVAIL_MIN_RESTRICT_CEIL = 0.90
    _AVAIL_RETURN_GAP_DAYS = 5
    _AVAIL_GAP_LOOKBACK = 5
    _AVAIL_DNP_LOWMIN_THRESHOLD = 5.0
    _AVAIL_DNP_LOWMIN_LAST_N = 5

    @classmethod
    def _classify_availability(
        cls,
        logs: List[Dict[str, Any]],
        before_date: Optional[str],
    ) -> Dict[str, Any]:
        """Classify a player's availability state from `bdl_game_logs`."""
        if not logs:
            return {
                "status": "UNKNOWN", "restriction_factor": 1.0,
                "dnp_risk_flag": False, "injury_return_flag": False,
                "minutes_restriction_flag": False, "return_game_number": None,
                "normal_minutes": None, "expected_minutes": None,
                "minutes_l3": None, "minutes_l10": None,
                "games_missed_recently": None, "reason": "no_logs",
            }
        if before_date:
            logs = [l for l in logs if (l.get("date") or "") < before_date]
        logs = sorted(logs, key=lambda l: (l.get("date") or ""), reverse=True)
        if not logs:
            return {
                "status": "UNKNOWN", "restriction_factor": 1.0,
                "dnp_risk_flag": False, "injury_return_flag": False,
                "minutes_restriction_flag": False, "return_game_number": None,
                "normal_minutes": None, "expected_minutes": None,
                "minutes_l3": None, "minutes_l10": None,
                "games_missed_recently": None, "reason": "no_pre_game_logs",
            }

        def _minutes(l):
            v = l.get("min")
            if v in (None, ""):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        mins_all = [_minutes(l) for l in logs]
        last_min = mins_all[0] if mins_all else None

        def _avg(arr, n):
            s = [x for x in arr[:n] if x is not None]
            return sum(s) / len(s) if s else None

        min_l3 = _avg(mins_all, 3)
        min_l10 = _avg(mins_all, 10)

        # Detect a recent absence gap.
        from datetime import datetime
        return_game_number: Optional[int] = None
        games_missed_recently: Optional[int] = None
        try:
            n_check = min(cls._AVAIL_GAP_LOOKBACK, len(logs) - 1)
            for i in range(n_check):
                d1 = datetime.fromisoformat((logs[i].get("date") or "")[:10])
                d2 = datetime.fromisoformat((logs[i + 1].get("date") or "")[:10])
                gap_days = (d1 - d2).days
                if gap_days >= cls._AVAIL_RETURN_GAP_DAYS:
                    return_game_number = i + 1
                    games_missed_recently = max(1, gap_days // 2 - 1)
                    break
        except (TypeError, ValueError) as _swept_exc:
            log_silent_failure("services.scoring.adapters.nba_scoring._classify_availability", _swept_exc)  # sweep-auto-converted

        # DNP-risk patterns.
        recent_n = logs[: cls._AVAIL_DNP_LOWMIN_LAST_N]
        recent_mins = [_minutes(l) for l in recent_n]
        low_n = sum(1 for m in recent_mins
                    if m is not None and m < cls._AVAIL_DNP_LOWMIN_THRESHOLD)
        last_was_dnp = (last_min is not None and last_min == 0)

        normal_minutes = min_l10
        # ---- Edge case: blowout sit. If last_min is 0 but the prior
        # two games show normal minutes (≥ 85% of L10), skip the
        # DNP_RISK branch and let the rest of the classifier run.
        # This prevents penalising stars who got pulled in a blowout.
        prev_two = [m for m in mins_all[1:3] if m is not None]
        prev_two_normal = (
            min_l10 is not None and min_l10 > 0
            and len(prev_two) == 2
            and all(m >= 0.85 * min_l10 for m in prev_two)
        )

        if last_was_dnp and not prev_two_normal:
            f = cls._AVAIL_DNP_FACTOR_LAST_GAME
            return cls._clamp_avail_result({
                "status": "DNP_RISK", "restriction_factor": f,
                "dnp_risk_flag": True, "injury_return_flag": False,
                "minutes_restriction_flag": False, "return_game_number": None,
                "normal_minutes": normal_minutes,
                "expected_minutes": (normal_minutes * f) if normal_minutes else None,
                "minutes_l3": min_l3, "minutes_l10": min_l10,
                "games_missed_recently": 0,
                "reason": "last_game_0_minutes",
            })
        if low_n >= 2:
            f = cls._AVAIL_DNP_FACTOR_LOW_PATTERN
            return cls._clamp_avail_result({
                "status": "DNP_RISK", "restriction_factor": f,
                "dnp_risk_flag": True, "injury_return_flag": False,
                "minutes_restriction_flag": False, "return_game_number": None,
                "normal_minutes": normal_minutes,
                "expected_minutes": (normal_minutes * f) if normal_minutes else None,
                "minutes_l3": min_l3, "minutes_l10": min_l10,
                "games_missed_recently": 0,
                "reason": f"{low_n}_low_min_games_in_last_5",
            })
        # ----- Returning-from-absence (split into RESTRICTED / SOFT /
        # FULL_GO based on minutes recovery ratio L3/L10).
        if (
            return_game_number is not None and return_game_number <= 3
            and min_l10 is not None and min_l10 >= cls._AVAIL_MIN_L10_FOR_RETURN_CLASSIFY
        ):
            recovery = (min_l3 / min_l10) if (min_l3 is not None and min_l10 > 0) else None
            rgn = return_game_number
            rgn_key = min(rgn, 3)
            sub_status = None
            f = 1.0
            if recovery is not None and recovery >= cls._AVAIL_MIN_RECOVERY_FULL_GO:
                # Minutes back to ≥90% of L10 — return is real but
                # workload restored. Apply only the cosmetic factor.
                sub_status = "RETURNING_FULL_GO"
                f = cls._AVAIL_RETURN_FULL_GO_FACTORS.get(rgn_key, 1.0)
            elif recovery is not None and recovery < cls._AVAIL_MIN_RECOVERY_RESTRICTED:
                sub_status = "RETURNING_RESTRICTED"
                f = cls._AVAIL_RETURN_RESTRICTED_FACTORS.get(rgn_key, 0.85)
            else:
                # Recovery in the 80%-90% band, OR L3 minutes missing.
                sub_status = "RETURNING_SOFT"
                f = cls._AVAIL_RETURN_SOFT_FACTORS.get(rgn_key, 0.95)

            return cls._clamp_avail_result({
                "status": "RETURNING_FROM_ABSENCE",
                "availability_sub_status": sub_status,
                "restriction_factor": f,
                "dnp_risk_flag": False, "injury_return_flag": True,
                "minutes_restriction_flag": False,
                "return_game_number": return_game_number,
                "normal_minutes": normal_minutes,
                "expected_minutes": (normal_minutes * f) if normal_minutes else None,
                "minutes_l3": min_l3, "minutes_l10": min_l10,
                "minutes_recovery_ratio": (round(recovery, 4) if recovery is not None else None),
                "games_missed_recently": games_missed_recently,
                "reason": (
                    f"{sub_status}_g{rgn}_after_{games_missed_recently}_missed"
                    + (f"_recovery={recovery:.2f}" if recovery is not None else "_recovery=none")
                ),
            })
        if (
            min_l3 is not None and min_l10 is not None
            and min_l10 >= 20 and min_l3 < 0.85 * min_l10
        ):
            ratio = (min_l3 / min_l10) if min_l10 > 0 else 1.0
            f = max(cls._AVAIL_MIN_RESTRICT_FLOOR,
                    min(cls._AVAIL_MIN_RESTRICT_CEIL, ratio))
            return cls._clamp_avail_result({
                "status": "MINUTES_RESTRICTION", "restriction_factor": f,
                "dnp_risk_flag": False, "injury_return_flag": False,
                "minutes_restriction_flag": True, "return_game_number": None,
                "normal_minutes": normal_minutes,
                "expected_minutes": (normal_minutes * f) if normal_minutes else None,
                "minutes_l3": min_l3, "minutes_l10": min_l10,
                "games_missed_recently": 0,
                "reason": f"L3_min_{min_l3:.1f}_below_85pct_L10_{min_l10:.1f}",
            })
        return cls._clamp_avail_result({
            "status": "FULL_GO", "restriction_factor": 1.0,
            "dnp_risk_flag": False, "injury_return_flag": False,
            "minutes_restriction_flag": False, "return_game_number": None,
            "normal_minutes": normal_minutes, "expected_minutes": normal_minutes,
            "minutes_l3": min_l3, "minutes_l10": min_l10,
            "games_missed_recently": 0, "reason": "no_availability_signal",
        })

    @classmethod
    def _clamp_avail_result(cls, info: Dict[str, Any]) -> Dict[str, Any]:
        """Universal clamp on `restriction_factor` to [0.50, 1.00]
        per spec, applied at every return point of the classifier."""
        f = info.get("restriction_factor")
        if f is not None:
            f = max(cls._AVAIL_FACTOR_CLAMP_LO,
                    min(cls._AVAIL_FACTOR_CLAMP_HI, float(f)))
            info["restriction_factor"] = f
            # Re-derive expected_minutes after clamp.
            nm = info.get("normal_minutes")
            if nm is not None:
                info["expected_minutes"] = nm * f
        return info

    def _maybe_apply_availability_guard(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_in: Optional[float],
    ) -> Optional[float]:
        """Score-loop hook for the availability guard.

        Returns the adjusted μ.  Only acts on `_AVAIL_GUARD_TARGET_STATS`
        (volume stats).  Stamps audit fields onto `prop`.  An external
        injury feed can set `prop["availability_status_override"]` to a
        dict to override the heuristic — useful when the feed has hard
        OUT / GTD data the heuristic cannot derive from logs alone.
        """
        if mu_in is None or stat_type not in self._AVAIL_GUARD_TARGET_STATS:
            return mu_in
        if bdl_player_id is None:
            return mu_in
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_in
        logs = (self._logs_by_id or {}).get(pid)
        if not logs:
            return mu_in
        before_date = (prop.get("commence_time") or "")[:10] or None
        info = self._classify_availability(logs, before_date)

        override = prop.get("availability_status_override")
        if override and isinstance(override, dict):
            info = {**info, **override}

        # OUT / INACTIVE — flag the prop unqualified by returning None μ.
        if info.get("status") == "OUT":
            prop["availability_guard_applied"] = True
            prop["availability_status"] = "OUT"
            prop["availability_guard_reason"] = info.get("reason") or "out_or_inactive"
            prop["mu_before_availability_guard"] = round(float(mu_in), 4)
            prop["mu_after_availability_guard"] = None
            return None

        factor = float(info.get("restriction_factor") or 1.0)
        prop["availability_guard_applied"] = True
        prop["availability_status"] = info.get("status")
        prop["availability_sub_status"] = info.get("availability_sub_status")
        prop["minutes_recovery_ratio"] = info.get("minutes_recovery_ratio")
        prop["availability_guard_reason"] = info.get("reason")
        prop["dnp_risk_flag"] = info.get("dnp_risk_flag", False)
        prop["injury_return_flag"] = info.get("injury_return_flag", False)
        prop["minutes_restriction_flag"] = info.get("minutes_restriction_flag", False)
        prop["games_missed_recently"] = info.get("games_missed_recently")
        prop["return_game_number"] = info.get("return_game_number")
        prop["normal_minutes"] = info.get("normal_minutes")
        prop["expected_minutes"] = info.get("expected_minutes")
        prop["minutes_restriction_factor"] = factor
        prop["mu_before_availability_guard"] = round(float(mu_in), 4)

        if factor >= 0.999:
            prop["mu_after_availability_guard"] = round(float(mu_in), 4)
            return mu_in
        mu_out = float(mu_in) * factor
        prop["mu_after_availability_guard"] = round(mu_out, 4)
        return mu_out


    # =========================================================================
    # 2026-04-28 — Rate × Minutes projection layer (PTS / PRA).
    # =========================================================================
    # Runs AFTER the recency blend AND the availability guard, BEFORE
    # the universal probability engine. Replaces μ in-place with a 60/40
    # blend of:
    #     μ_rate  = (per-minute rate) × (expected minutes after restriction)
    #     μ_model = current μ (already recency-blended + availability-guarded)
    #
    # Eligibility (BOTH must hold):
    #   1. stat_type ∈ {PTS, PRA}.
    #   2. minutes are dynamic — either L3_min < L10_min (load decreasing) OR
    #      the availability guard fired non-trivially (status != FULL_GO).
    #      A plain FULL_GO PTS/PRA prop with stable minutes keeps μ_model
    #      unchanged so we don't disturb the existing model on healthy nights.
    #
    # Rate computation (volume-stat per minute):
    #   r_x = 0.7 × L5_avg(x_per_min) + 0.3 × L10_median(x_per_min)
    #   (L5 = recency, L10_median = stability anchor; median, not mean,
    #    to suppress outliers.)
    #
    # Expected minutes:
    #   exp_min_raw   = 0.4 × L3_min + 0.3 × L5_min + 0.3 × L10_min
    #   exp_min_final = exp_min_raw × restriction_factor   (from guard)
    #
    # Blend:
    #   μ_pts_final = 0.6 × (r_pts × exp_min_final) + 0.4 × μ_pts_model
    #   μ_pra_final = 0.6 × ((r_pts + r_reb + r_ast) × exp_min_final)
    #               + 0.4 × μ_pra_model
    #
    # Constraints / rules:
    #   - DOES NOT modify probability engine, σ/CV, recency-blend weights,
    #     availability-guard logic, or gate thresholds.
    #   - DOES NOT apply a global multiplier; the rate-model is the
    #     blended majority but the existing model intelligence is kept.
    # =========================================================================
    _RATE_TARGET_STATS = {"PTS", "PRA"}
    # ---------------------------------------------------------------------
    # 2026-04-29 — Rate-blend feature flag.
    # Promotes the 60/40 → 100/0 (pure rate × minutes) weighting validated
    # by `/tmp/nba_rate_100_0_production_sim.py` (272 settled picks, +13.2
    # pts hit rate on ALL, +36.4 on War Zone, 0 hit-rate change on Safe
    # Haven / Front Lines, 39 misses avoided vs 3 hits lost).
    #
    # Controlled via env `NBA_RATE_BLEND_MODE`:
    #     "100_0" (default, 2026-04-29 promotion)
    #     "60_40" (legacy — instant revert path)
    # The eligibility gate is UNCHANGED in either mode: rate fires only on
    # PTS/PRA when L3_min < L10_min OR availability_status is non-trivial.
    # Safe Haven and Front Lines tier behavior is unaffected (zero flips
    # in the simulation); War Zone is rescued from 22.2% → 58.6% hit rate.
    # ---------------------------------------------------------------------
    _RATE_BLEND_MODE = (os.environ.get("NBA_RATE_BLEND_MODE") or "100_0").strip()
    _RATE_BLEND_RATE_LEGACY  = 0.60   # legacy weight on rate × minutes
    _RATE_BLEND_MODEL_LEGACY = 0.40   # legacy weight on existing μ
    if _RATE_BLEND_MODE == "60_40":
        _RATE_BLEND_RATE  = _RATE_BLEND_RATE_LEGACY
        _RATE_BLEND_MODEL = _RATE_BLEND_MODEL_LEGACY
    else:                              # "100_0" (default) or any unknown value
        _RATE_BLEND_RATE  = 1.00       # weight on rate × minutes
        _RATE_BLEND_MODEL = 0.00       # weight on existing μ
    _RATE_EXP_MIN_W = {"L3": 0.40, "L5": 0.30, "L10": 0.30}
    _RATE_RECENCY_W = {"L5": 0.70, "L10_MEDIAN": 0.30}
    _RATE_MIN_LOGS = 3   # need at least 3 game logs to compute rates
    # ---------------------------------------------------------------------
    # 2026-04-29 — RFA-only minutes penalty.
    # Validated by `/tmp/nba_rfa_minutes_penalty_sim.py` (272 picks, tighter
    # sweep around 0.85): RFA-only hit rate 63.1% → 85.1% (+22 pts), PTS
    # 56.8% → 81.1%, PRA 52.8% → 71.7%, only 4 hits broken on 168 RFA picks
    # (10:1 win ratio). Applies AFTER the availability guard's
    # restriction_factor, ONLY when status == "RETURNING_FROM_ABSENCE".
    # FULL_GO, MINUTES_RESTRICTION, MINUTES_VOLATILITY, DNP_RISK paths
    # are unaffected.
    #
    # Controlled via env `NBA_RFA_MINUTES_PENALTY` (float in (0, 1.0]):
    #   1.0  → disabled (default — no penalty applied)
    #   0.85 → recommended production setting (post-2026-04-29 promotion)
    # Out-of-range values are clamped to (0.50, 1.00) for safety.
    # ---------------------------------------------------------------------
    try:
        _RFA_MINUTES_PENALTY = float(
            os.environ.get("NBA_RFA_MINUTES_PENALTY") or "1.0"
        )
    except (TypeError, ValueError):
        _RFA_MINUTES_PENALTY = 1.0
    _RFA_MINUTES_PENALTY = max(0.50, min(1.00, _RFA_MINUTES_PENALTY))

    @classmethod
    def _apply_rfa_minutes_penalty(
        cls, prop: Dict[str, Any], exp_min_pre: float,
        avail_status: Optional[str],
    ) -> float:
        """Apply the RFA-only minutes penalty to `exp_min_pre` and stamp
        audit fields onto `prop`. Used by the production rate × minutes
        layer AND every other path that consumes `expected_minutes`
        (REB/AST shadow, future minutes-driven projections).

        Returns the post-penalty expected_minutes value. Stamps:
          • rfa_minutes_penalty_applied  (bool)
          • rfa_minutes_penalty_factor   (float in [0.50, 1.00])
          • expected_minutes_before_rfa_penalty  (raw × restriction_factor)
          • expected_minutes_after_rfa_penalty   (post-penalty value)

        Penalty fires ONLY when `avail_status == "RETURNING_FROM_ABSENCE"`
        AND the configured factor is < 1.0. All other statuses
        (FULL_GO, MINUTES_RESTRICTION, MINUTES_VOLATILITY, DNP_RISK,
        UNKNOWN, None) pass through unchanged.
        """
        applied = False
        factor  = 1.0
        if (avail_status == "RETURNING_FROM_ABSENCE"
            and cls._RFA_MINUTES_PENALTY < 1.0):
            factor  = float(cls._RFA_MINUTES_PENALTY)
            applied = True
        exp_min_post = exp_min_pre * factor
        prop["rfa_minutes_penalty_applied"]         = applied
        prop["rfa_minutes_penalty_factor"]          = factor
        prop["expected_minutes_before_rfa_penalty"] = round(exp_min_pre, 4)
        prop["expected_minutes_after_rfa_penalty"]  = round(exp_min_post, 4)
        return exp_min_post

    @classmethod
    def _compute_rate_components(
        cls,
        logs: List[Dict[str, Any]],
        before_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Compute per-minute rates (pts, reb, ast) plus minute baselines
        from `bdl_game_logs`. Returns None when fewer than `_RATE_MIN_LOGS`
        games carry a usable minute count (filters out DNPs)."""
        if not logs:
            return None
        if before_date:
            logs = [l for l in logs if (l.get("date") or "") < before_date]
        if not logs:
            return None
        logs = sorted(logs, key=lambda l: (l.get("date") or ""), reverse=True)

        def _f(v):
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        # Per-game per-minute rates — only for games with min > 0 so DNPs
        # don't divide by zero or skew the rate.
        per_min_rows: List[Dict[str, Optional[float]]] = []
        mins_all: List[Optional[float]] = []
        for l in logs:
            m = _f(l.get("min"))
            mins_all.append(m)
            if m is None or m <= 0:
                per_min_rows.append({"pts": None, "reb": None, "ast": None})
                continue
            per_min_rows.append({
                "pts": (_f(l.get("pts")) / m) if l.get("pts") is not None else None,
                "reb": (_f(l.get("reb")) / m) if l.get("reb") is not None else None,
                "ast": (_f(l.get("ast")) / m) if l.get("ast") is not None else None,
            })

        def _avg(arr, n):
            s = [x for x in arr[:n] if x is not None]
            return (sum(s) / len(s)) if s else None

        def _median(arr, n):
            s = sorted([x for x in arr[:n] if x is not None])
            if not s:
                return None
            m = len(s)
            return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0

        # L5 mean of per-min rates (recency).
        l5_pts = _avg([r["pts"] for r in per_min_rows], 5)
        l5_reb = _avg([r["reb"] for r in per_min_rows], 5)
        l5_ast = _avg([r["ast"] for r in per_min_rows], 5)
        # L10 median (stability anchor).
        l10m_pts = _median([r["pts"] for r in per_min_rows], 10)
        l10m_reb = _median([r["reb"] for r in per_min_rows], 10)
        l10m_ast = _median([r["ast"] for r in per_min_rows], 10)

        def _blend(l5, l10m):
            wL5  = cls._RATE_RECENCY_W["L5"]
            wL10 = cls._RATE_RECENCY_W["L10_MEDIAN"]
            if l5 is None and l10m is None:
                return None
            if l5 is None:
                return l10m
            if l10m is None:
                return l5
            return wL5 * l5 + wL10 * l10m

        r_pts = _blend(l5_pts, l10m_pts)
        r_reb = _blend(l5_reb, l10m_reb)
        r_ast = _blend(l5_ast, l10m_ast)

        # Need at least L3+L10 minutes to build expected_minutes.
        min_l3  = _avg(mins_all, 3)
        min_l5  = _avg(mins_all, 5)
        min_l10 = _avg(mins_all, 10)
        n_with_min = sum(1 for m in mins_all if m is not None and m > 0)
        if n_with_min < cls._RATE_MIN_LOGS:
            return None
        if min_l3 is None or min_l10 is None:
            return None

        w = cls._RATE_EXP_MIN_W
        # If L5 is missing, redistribute its weight onto L10.
        if min_l5 is None:
            exp_min_raw = w["L3"] * min_l3 + (w["L5"] + w["L10"]) * min_l10
        else:
            exp_min_raw = w["L3"] * min_l3 + w["L5"] * min_l5 + w["L10"] * min_l10

        return {
            "rate_pts_per_min": r_pts,
            "rate_reb_per_min": r_reb,
            "rate_ast_per_min": r_ast,
            "expected_minutes_raw": round(exp_min_raw, 4),
            "minutes_l3":  min_l3,
            "minutes_l5":  min_l5,
            "minutes_l10": min_l10,
            "n_games_with_min": n_with_min,
        }

    def _maybe_apply_rate_model(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_model: Optional[float],
    ) -> Optional[float]:
        """Apply the rate × minutes projection blend.

        Returns the blended μ when the layer fires, otherwise returns
        `mu_model` unchanged. Always stamps `rate_model_applied` on the
        prop (True or False) so downstream observability can partition.
        """
        # Default audit stamp — overridden if the layer activates.
        prop.setdefault("rate_model_applied", False)

        if stat_type not in self._RATE_TARGET_STATS or mu_model is None:
            return mu_model
        if bdl_player_id is None:
            return mu_model
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_model
        logs = (self._logs_by_id or {}).get(pid)
        if not logs:
            return mu_model

        before_date = (prop.get("commence_time") or "")[:10] or None
        comps = self._compute_rate_components(logs, before_date=before_date)
        if comps is None:
            return mu_model

        # Eligibility gate — minutes must be dynamic OR availability guard
        # fired non-trivially. Plain FULL_GO + stable minutes → keep μ_model.
        avail_status = prop.get("availability_status")
        non_trivial_avail = (
            avail_status not in (None, "UNKNOWN", "FULL_GO")
        )
        min_l3, min_l10 = comps["minutes_l3"], comps["minutes_l10"]
        l3_below_l10 = (
            min_l3 is not None and min_l10 is not None
            and min_l3 < min_l10
        )
        if not (l3_below_l10 or non_trivial_avail):
            return mu_model

        # Restriction factor was stamped by the availability guard.
        # When the guard didn't fire (e.g. classifier returned UNKNOWN
        # or stat wasn't in its target set), default to 1.0.
        rf = prop.get("minutes_restriction_factor")
        try:
            rf = float(rf) if rf is not None else 1.0
        except (TypeError, ValueError):
            rf = 1.0
        rf = max(0.50, min(1.00, rf))  # mirror the guard's universal clamp

        exp_min_raw     = comps["expected_minutes_raw"]
        exp_min_pre_rfa = exp_min_raw * rf

        # Shared RFA penalty helper — also stamps audit fields.
        exp_min_final = self._apply_rfa_minutes_penalty(
            prop, exp_min_pre_rfa, avail_status,
        )

        r_pts = comps["rate_pts_per_min"]
        r_reb = comps["rate_reb_per_min"]
        r_ast = comps["rate_ast_per_min"]

        if stat_type == "PTS":
            if r_pts is None:
                return mu_model
            mu_rate = r_pts * exp_min_final
        else:  # PRA
            if r_pts is None or r_reb is None or r_ast is None:
                return mu_model
            mu_rate = (r_pts + r_reb + r_ast) * exp_min_final

        wR = self._RATE_BLEND_RATE
        wM = self._RATE_BLEND_MODEL
        mu_final = wR * mu_rate + wM * float(mu_model)

        # Stamp audit fields.
        prop["rate_model_applied"]   = True
        prop["rate_pts_per_min"]     = (round(r_pts, 6) if r_pts is not None else None)
        prop["rate_reb_per_min"]     = (round(r_reb, 6) if r_reb is not None else None)
        prop["rate_ast_per_min"]     = (round(r_ast, 6) if r_ast is not None else None)
        prop["expected_minutes_raw"] = round(exp_min_raw, 4)
        prop["expected_minutes"]     = round(exp_min_final, 4)
        prop["mu_rate_projection"]   = round(mu_rate, 4)
        prop["mu_model_projection"]  = round(float(mu_model), 4)
        prop["mu_final_projection"]  = round(mu_final, 4)
        prop["rate_model_blend_weights"] = {"rate": wR, "model": wM}
        prop["rate_model_blend_mode"]    = self._RATE_BLEND_MODE  # "100_0" / "60_40"
        prop["rate_model_trigger"] = (
            "L3_below_L10" if l3_below_l10 and not non_trivial_avail
            else ("availability_guard" if non_trivial_avail and not l3_below_l10
                  else "both")
        )
        return mu_final





    # =========================================================================
    # 2026-04-28 — Shadow Recipe E (heavy-recency) projection.
    # =========================================================================
    # AUDIT-ONLY layer that runs at the very end of the projection
    # pipeline (AFTER recency blend, availability guard, and rate × minutes).
    # NEVER replaces μ_current — only stamps shadow fields so we can
    # forward-test Recipe E against the live blend over the next 7 days.
    #
    # Recipe E (validated 2026-04-28 on 272 settled NBA picks):
    #     μ_E = 0.50·L3_avg + 0.20·L10_median + 0.10·L10_avg + 0.10·μ_model
    # Weights renormalised to sum to 1.0 at evaluation time so they are
    # applied identically to how the offline replay measured them.
    #
    # Stamp fields (all audit-only):
    #   • mu_recency_E              — Recipe-E blended μ
    #   • mu_recency_E_applied      — always False (production untouched)
    #   • delta_mu_E_vs_A           — mu_recency_E − mu_current
    #   • mu_recency_E_l3, _l10med, _l10  — input baselines
    # =========================================================================
    _SHADOW_E_TARGET_STATS = {
        "PTS", "PRA", "REB", "AST",
        "P+R", "P+A", "R+A",
        "pts_reb", "pts_ast", "reb_ast",
    }
    _SHADOW_E_WEIGHTS = {"L3": 0.50, "L10MED": 0.20, "L10": 0.10, "MODEL": 0.10}
    _SHADOW_E_MIN_SAMPLES = 3

    @classmethod
    def _shadow_E_stat_value(
        cls, log: Dict[str, Any], stat_type: str
    ) -> Optional[float]:
        """Per-game value extractor covering volume stats. Independent
        of `_stat_value_from_log` so the production recency blend's
        scope (PTS/PRA only) stays intact."""
        s = (stat_type or "").upper()
        try:
            if s == "PTS":
                v = log.get("pts")
                return float(v) if v is not None else None
            if s == "REB":
                v = log.get("reb")
                return float(v) if v is not None else None
            if s == "AST":
                v = log.get("ast")
                return float(v) if v is not None else None
            if s == "PRA":
                p, r, a = log.get("pts"), log.get("reb"), log.get("ast")
                if p is None or r is None or a is None:
                    return None
                return float(p) + float(r) + float(a)
            if s in {"P+R", "PTS_REB"}:
                p, r = log.get("pts"), log.get("reb")
                if p is None or r is None:
                    return None
                return float(p) + float(r)
            if s in {"P+A", "PTS_AST"}:
                p, a = log.get("pts"), log.get("ast")
                if p is None or a is None:
                    return None
                return float(p) + float(a)
            if s in {"R+A", "REB_AST"}:
                r, a = log.get("reb"), log.get("ast")
                if r is None or a is None:
                    return None
                return float(r) + float(a)
        except (TypeError, ValueError):
            return None
        return None

    @classmethod
    def _compute_shadow_recency_E(
        cls,
        logs: List[Dict[str, Any]],
        stat_type: str,
        mu_model: Optional[float],
        before_date: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Compute Recipe-E shadow μ from raw logs. Returns None when
        the baselines are too thin or μ_model is missing."""
        if mu_model is None or not logs:
            return None
        stat_up = (stat_type or "").upper()
        if stat_up not in cls._SHADOW_E_TARGET_STATS:
            return None
        if before_date:
            logs = [l for l in logs if (l.get("date") or "") < before_date]
        if not logs:
            return None
        logs = sorted(logs, key=lambda log: (log.get("date") or ""), reverse=True)
        vals = [cls._shadow_E_stat_value(log, stat_up) for log in logs]
        if sum(1 for v in vals if v is not None) < cls._SHADOW_E_MIN_SAMPLES:
            return None

        def _avg(arr, n):
            s = [x for x in arr[:n] if x is not None]
            return (sum(s) / len(s)) if s else None

        def _median(arr, n):
            s = sorted([x for x in arr[:n] if x is not None])
            if not s:
                return None
            m = len(s)
            return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0

        l3       = _avg(vals, 3)
        l10_avg  = _avg(vals, 10)
        l10_med  = _median(vals, 10)
        if l3 is None or l10_med is None:
            return None
        if l10_avg is None:
            l10_avg = l10_med   # graceful fallback for thin samples

        comps = {
            "L3":     l3,
            "L10MED": l10_med,
            "L10":    l10_avg,
            "MODEL":  float(mu_model),
        }
        # Renormalise weights to sum=1 so we match the offline replay.
        wsum = sum(cls._SHADOW_E_WEIGHTS[k] for k in comps)
        mu_E = sum(comps[k] * (cls._SHADOW_E_WEIGHTS[k] / wsum) for k in comps)
        return {
            "mu_E": round(mu_E, 4),
            "L3":     l3,
            "L10MED": l10_med,
            "L10":    l10_avg,
            "MODEL":  round(float(mu_model), 4),
        }

    def _maybe_apply_shadow_recency_E(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_current: Optional[float],
    ) -> Optional[float]:
        """Score-loop hook for the Recipe-E shadow projection.

        ALWAYS returns `mu_current` unchanged. Stamps audit fields when
        the layer can compute a shadow μ; otherwise leaves the prop's
        shadow fields at their default (`mu_recency_E_applied = False`).
        """
        prop.setdefault("mu_recency_E_applied", False)
        if mu_current is None or bdl_player_id is None:
            return mu_current
        if (stat_type or "").upper() not in self._SHADOW_E_TARGET_STATS:
            return mu_current
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_current
        logs = (self._logs_by_id or {}).get(pid)
        if not logs:
            return mu_current

        before_date = (prop.get("commence_time") or "")[:10] or None
        result = self._compute_shadow_recency_E(
            logs=logs, stat_type=stat_type,
            mu_model=mu_current, before_date=before_date,
        )
        if result is None:
            return mu_current

        mu_E = result["mu_E"]
        prop["mu_recency_E"]         = mu_E
        prop["mu_recency_E_applied"] = False  # shadow only
        prop["delta_mu_E_vs_A"]      = round(mu_E - float(mu_current), 4)
        prop["mu_recency_E_l3"]      = result["L3"]
        prop["mu_recency_E_l10med"]  = result["L10MED"]
        prop["mu_recency_E_l10"]     = result["L10"]
        # μ_current stays the production projection — DO NOT touch it.
        return mu_current


    # =========================================================================
    # 2026-04-28 — Shadow VK2 PTS projection (audit-only).
    # =========================================================================
    # AUDIT-ONLY layer that runs the VK2 PTS predictor in parallel with
    # the production VK1-PTS μ. NEVER replaces μ_current. Stamps
    # `mu_pts_vk2` and `delta_mu_pts_vk2_vs_vk1` so the 7-day
    # forward-test eval can decide whether to promote PTS to
    # VK2-primary.
    #
    # Why shadow first (instead of straight cutover): PTS μ is the
    # input for recency blend, minutes-regression guard, rate × minutes
    # layer, PRA synth, and the existing shadow Recipe E. A direct
    # source switch would simultaneously change all five downstream
    # consumers; shadowing isolates the model-source change from the
    # downstream-pipeline impact.
    #
    # Eligibility: `stat_type == "PTS"` AND VK2 PTS model loaded AND
    # >= 5 pre-game logs available. Skipped silently otherwise.
    # =========================================================================
    def _maybe_apply_shadow_pts_vk2(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_current: Optional[float],
    ) -> Optional[float]:
        """Run the VK2 PTS predictor in shadow mode. Returns μ_current
        unchanged. Only stamps audit fields when VK2 produces a clean
        projection."""
        prop.setdefault("mu_pts_vk2_applied", False)
        if (stat_type or "").upper() != "PTS":
            return mu_current
        if mu_current is None or bdl_player_id is None:
            return mu_current
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_current
        m = (self._vk2_models or {}).get("PTS")
        if m is None:
            return mu_current

        # Reuse the existing VK2 history slice + feature builder so
        # this layer doesn't duplicate filtering logic.
        try:
            history = self._get_vk2_history_logs(pid, window=20)
        except Exception:
            return mu_current
        if not history or len(history) < 5:
            return mu_current

        # Lazy-import to keep top-level imports tight.
        from services.scoring.nba_vk2_features import build_features as _bf
        try:
            feats = _bf(
                history_logs=history,
                target_game=history[0],
                adv_map=self._vk2_adv_map or None,
            )
        except Exception:
            return mu_current
        if feats is None:
            return mu_current

        try:
            import numpy as np
            row = np.asarray(
                [[feats.get(c, 0.0) for c in m["features"]]],
                dtype=np.float32,
            )
            row_s = m["scaler"].transform(row)
            mu_vk2 = float(m["model"].predict(row_s)[0])
        except Exception:
            return mu_current

        prop["mu_pts_vk2"]            = round(mu_vk2, 4)
        prop["mu_pts_vk2_applied"]    = False  # shadow only
        prop["delta_mu_pts_vk2_vs_vk1"] = round(mu_vk2 - float(mu_current), 4)
        # μ_current stays the production projection — DO NOT touch it.
        return mu_current


    # =========================================================================
    # 2026-04-29 — Shadow REB / AST rate × minutes (audit-only).
    # =========================================================================
    # The rate × minutes layer (`_maybe_apply_rate_model`) only fires for
    # PTS and PRA in production. The 2026-04-29 weight-sweep audit on 272
    # settled NBA outcomes showed that pure rate × minutes (100/0) ties
    # production hit rate on REB (75.9%) and AST (95.2%) while cutting
    # absolute error by 16% (REB: 2.65 → 2.22) and 20% (AST: 2.06 → 1.64).
    # Sample sizes are too small (n=29 / n=21) to flip a single pick, so
    # this layer is added as SHADOW ONLY — NEVER replaces μ_current.
    # Stamps:
    #     mu_rate_reb_shadow   — pure rate × minutes for REB
    #     mu_rate_ast_shadow   — pure rate × minutes for AST
    #     mu_rate_<s>_shadow_applied  — always False (shadow only)
    #     delta_mu_rate_<s>_shadow_vs_current  — μ_shadow − μ_current
    #     rate_<s>_per_min_shadow      — blended rate that produced μ_shadow
    #     expected_minutes_shadow      — exp_min × restriction_factor
    # Eligibility: stat ∈ {REB, AST}, ≥ _RATE_MIN_LOGS pre-game logs,
    # restriction_factor available from availability guard (defaults to 1.0).
    # =========================================================================
    _RATE_SHADOW_REB_AST_STATS = {"REB", "AST"}

    def _maybe_apply_shadow_rate_reb_ast(
        self,
        stat_type: str,
        bdl_player_id: Optional[int],
        prop: Dict[str, Any],
        mu_current: Optional[float],
    ) -> Optional[float]:
        """Compute pure rate × minutes shadow μ for REB / AST. Returns
        `mu_current` unchanged in all cases — shadow only."""
        stat_up = (stat_type or "").upper()
        if stat_up not in self._RATE_SHADOW_REB_AST_STATS:
            return mu_current
        # Default audit stamp so absence is observable.
        prop.setdefault(f"mu_rate_{stat_up.lower()}_shadow_applied", False)
        if mu_current is None or bdl_player_id is None:
            return mu_current
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return mu_current
        logs = (self._logs_by_id or {}).get(pid)
        if not logs:
            return mu_current

        before_date = (prop.get("commence_time") or "")[:10] or None
        comps = self._compute_rate_components(logs, before_date=before_date)
        if comps is None:
            return mu_current

        rf = prop.get("minutes_restriction_factor")
        try:
            rf = float(rf) if rf is not None else 1.0
        except (TypeError, ValueError):
            rf = 1.0
        rf = max(0.50, min(1.00, rf))

        # Compute pre-penalty expected_minutes, then apply the shared
        # RFA penalty so the REB/AST shadow stays consistent with the
        # production rate × minutes layer.
        exp_min_raw     = comps["expected_minutes_raw"]
        exp_min_pre_rfa = exp_min_raw * rf
        avail_status_shadow = prop.get("availability_status")
        exp_min_final = self._apply_rfa_minutes_penalty(
            prop, exp_min_pre_rfa, avail_status_shadow,
        )

        rate = (comps["rate_reb_per_min"]
                if stat_up == "REB" else comps["rate_ast_per_min"])
        if rate is None:
            return mu_current

        mu_shadow = float(rate) * float(exp_min_final)
        key = stat_up.lower()
        prop[f"mu_rate_{key}_shadow"]                = round(mu_shadow, 4)
        prop[f"mu_rate_{key}_shadow_applied"]        = False  # shadow only
        prop[f"delta_mu_rate_{key}_shadow_vs_current"] = round(
            mu_shadow - float(mu_current), 4
        )
        prop[f"rate_{key}_per_min_shadow"]           = round(float(rate), 6)
        prop["expected_minutes_shadow"]              = round(exp_min_final, 4)
        # μ_current stays the production projection — DO NOT touch it.
        return mu_current







    # =========================================================================
    # 2026-04-27 — Universal probability engine bridge.
    # =========================================================================
    # NBA projections (μ) and empirical residual σ continue to come from
    # the VK / VK2 / combo paths. The probability conversion is replaced
    # by the sport-agnostic engine so audit fields (`distribution_kind`,
    # `distribution_p_over`, etc.) are populated identically to MLB.
    #
    # Stat-type tokens in NBA scoring use compact form (PTS/REB/AST/...)
    # which the registry's canonicaliser handles; STL/BLK route to
    # Poisson at 0.5-line via the selector in calibration/nba.py.
    # =========================================================================
    def _engine_p_over(
        self,
        stat_type: str,
        line: float,
        projection: Optional[float],
        sigma: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """Compute p_over via the universal probability engine.

        Returns a small dict with `p_over` plus the audit fields the
        score loop needs to persist; or `None` if inputs are missing.
        """
        if projection is None or sigma is None or sigma <= 0:
            return None
        try:
            from services.probability.distribution import compute_probability
            res = compute_probability(
                sport="nba",
                stat_family=stat_type,
                mu=float(projection),
                line=float(line),
                sigma=float(sigma),
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"[NBA_SCORING] engine p_over failed: {e}")
            return None
        if res is None:
            return None
        return {
            "p_over": res.p_over,
            "distribution_p_over": round(res.p_over, 4),
            "distribution_p_under": round(res.p_under, 4),
            "distribution_kind": res.distribution,
            "distribution_selector_reason": res.selector_reason,
            "distribution_sigma": res.sigma,
            "distribution_sigma_source": res.sigma_source,
            "distribution_clamped": res.clamped,
            "distribution_effective_mu": res.effective_mu,
            "distribution_mu_floor_applied": res.mu_floor_applied,
            "distribution_mu_floor_capped": res.mu_floor_capped,
            "distribution_cv_floor_applied": res.cv_floor_applied,
            "distribution_lambda": res.lambda_,
            "distribution_threshold": res.threshold,
            "distribution_dispersion_r": res.dispersion_r,
            "distribution_p_param": res.p_param,
        }



    def _predict_model_prob_over(
        self, db, bdl_player_id: Optional[int], player_name: Optional[str],
        stat_type: str, line: float, opponent_team: Optional[str],
        # 2026-05 feature-activation: pipe live game context so VK's
        # trained features hydrate to real values instead of silent
        # defaults (`team_total=115`, `sharp_implied=50`, `rest_days=1`,
        # `is_b2b=0`, `is_home=0`).
        team_total: Optional[float] = None,
        target_game: Optional[Dict[str, Any]] = None,
        sharp_implied: Optional[float] = None,
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
                team_total=team_total,
                target_game=target_game,
                sharp_implied=sharp_implied,
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
            "feature_health": r.get("feature_health"),
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

        # --- Projection intercept calibration (2026-04-23) -----------
        # Audit-derived per-stat additive shift (PTS -0.094, PRA -0.103,
        # others 0). Applied AFTER minutes composition so the final
        # projection hitting prob_over carries the correction regardless
        # of the composition path. Sigma / gates are untouched. Toggle
        # via env var VK2_CALIBRATION_ENABLED.
        from services.scoring.calibration import (
            apply_projection_intercept, intercept_for,
            apply_probability_calibration,
            prob_calibrator_available,
            apply_empirical_cdf_probability,
            ecdf_available, ecdf_flag_enabled,
            calibration_flag_enabled,
            prob_calibration_flag_enabled,
        )
        pre_intercept_projection = projection
        projection = apply_projection_intercept(stat_type, projection)
        calibration_meta: Dict[str, Any] = {}
        if calibration_flag_enabled() and intercept_for(stat_type):
            calibration_meta = {
                "projection_intercept_applied": True,
                "projection_intercept_delta": round(intercept_for(stat_type), 4),
                "pre_intercept_projection": round(
                    float(pre_intercept_projection), 3,
                ),
            }

        from math import erf, sqrt
        z = (projection - float(line)) / sigma
        raw_p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))

        # Always persist the raw Gaussian value so observability /
        # shadow eval can A/B without rescoring.
        calibration_meta["raw_gaussian_p_over"] = round(float(raw_p_over), 4)

        # --- Fallback chain (2026-04-23 distribution-audit order) -----
        # 1. Empirical CDF (winner on every stat: 91-99% weighted-|gap|
        #    improvement vs Gaussian). Strictly non-parametric per-stat
        #    lookup; handles skew + heavy tails natively.
        # 2. Isotonic global (prior layer; kept for fallback when ECDF
        #    pkl missing).
        # 3. Raw Gaussian CDF.
        # All three values are persisted for audit when computed.
        probability_method = "gaussian"
        p_over: float = float(raw_p_over)

        ecdf_out = apply_empirical_cdf_probability(
            stat_type, projection, float(line),
        )
        if ecdf_out is not None:
            p_over = float(ecdf_out["p_over"])
            probability_method = "ecdf"
            calibration_meta["ecdf_p_over"] = round(p_over, 4)
            calibration_meta["ecdf_bucket"] = int(ecdf_out["bucket"])
            calibration_meta["ecdf_bucket_n"] = int(ecdf_out["bucket_n"])
            calibration_meta["ecdf_version"] = ecdf_out["version"]

        iso_out = apply_probability_calibration(stat_type, float(raw_p_over))
        if (iso_out is not None and raw_p_over is not None
                and abs(float(iso_out) - float(raw_p_over)) > 1e-9):
            calibration_meta["isotonic_p_over"] = round(float(iso_out), 4)

        if probability_method == "gaussian":
            # ECDF missing/skipped — try isotonic fallback before
            # returning raw Gaussian.
            if (prob_calibration_flag_enabled()
                    and prob_calibrator_available(stat_type)
                    and iso_out is not None
                    and abs(float(iso_out) - float(raw_p_over)) > 1e-9):
                p_over = float(iso_out)
                probability_method = "isotonic"

        calibration_meta["probability_method"] = probability_method
        if probability_method == "isotonic":
            calibration_meta["probability_calibration_applied"] = True
            calibration_meta["raw_p_over"] = round(float(raw_p_over), 4)

        out = {
            "projection": round(projection, 3),
            "sigma": round(sigma, 3),
            "p_over": round(float(p_over), 4) if p_over is not None else None,
            "error": None,
        }
        if composition_meta:
            out.update(composition_meta)
        if calibration_meta:
            out.update(calibration_meta)
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
        # 2026-05 feature-activation: forward game context so the
        # underlying VK predictions for each component receive real
        # `team_total`, `target_game`, `sharp_implied`.
        team_total: Optional[float] = None,
        target_game: Optional[Dict[str, Any]] = None,
        sharp_implied: Optional[float] = None,
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
                    team_total=team_total, target_game=target_game,
                    sharp_implied=sharp_implied,
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

        # Lazy-preload VK2 adv_map when model path requested OR when
        # the prop's stat is in `_VK2_PRIMARY_STATS` (default-VK2 stats).
        active_method_early = (
            ((config or {}).get("override_config") or {})
            .get("vision_score", {})
            .get("p_true_method")
        ) or "model"
        # Per-stat VK2 promotion: if no explicit override picked legacy,
        # default to VK2 for stats in `_VK2_PRIMARY_STATS`. The check
        # uses the raw prop stat_type because `model_key` is computed
        # later in this function.
        prop_stat_for_routing = (prop.get("stat_type") or "").upper()
        wants_vk2 = (
            active_method_early == "vk2"
            or prop_stat_for_routing in self._VK2_PRIMARY_STATS
        )
        if wants_vk2 and not self._vk2_adv_loaded:
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
        # 2026-05 feature-activation: prefer the hydrated 3-letter abbr
        # (`opponent_team`) written by `feature_hydration.py`. Fall back
        # to legacy fields when hydration didn't run / failed.
        opponent_team = (
            prop.get("opponent_team")
            or prop.get("opponent")
            or prop.get("away_team")
        )
        # Build live game-context for the VK feature engineer.
        # `team_total`: hydrated from `dg_raw_odds_markets.team_totals`.
        # `target_game`: minimal shape `{date, home_game}` so VK can
        # derive `is_home`, `is_b2b`, `rest_days`. The `date` matches
        # the prop's commence_time so VK's date-diff against the most
        # recent game log is accurate.
        # `sharp_implied`: devigged sharp probability from `sharp_layer`
        # if available; otherwise None (VK falls back + flags imputed).
        live_team_total = prop.get("team_total")
        try:
            live_team_total = float(live_team_total) if live_team_total is not None else None
        except (TypeError, ValueError):
            live_team_total = None
        live_target_game: Optional[Dict[str, Any]] = None
        commence_iso = prop.get("commence_time")
        is_home_flag = prop.get("is_home_team")
        if commence_iso:
            live_target_game = {
                "date": commence_iso,
                "home_game": bool(is_home_flag) if is_home_flag is not None else None,
            }
        live_sharp_implied: Optional[float] = None
        sharp_layer = prop.get("sharp_layer") or {}
        sharp_odds_raw = sharp_layer.get("odds") if isinstance(sharp_layer, dict) else None
        if sharp_odds_raw is None:
            sharp_odds_raw = prop.get("sharp_odds")
        # Fallback: devig DK over price when no sharp book is available.
        # This is closer to the sharp truth than the silent default of
        # 50 and removes 100% of `sharp_implied` dead-feature hits.
        if sharp_odds_raw is None:
            dk_layer = prop.get("dk_layer") or {}
            sharp_odds_raw = dk_layer.get("odds") if isinstance(dk_layer, dict) else None
            if sharp_odds_raw is None:
                sharp_odds_raw = prop.get("dk_odds")
        if sharp_odds_raw is not None:
            try:
                so = int(sharp_odds_raw)
                # American odds → implied probability (no de-vig pair
                # available here; we use the raw side prob, which is
                # an upper bound for the "no-vig" prob and is closer
                # to truth than the silent default of 50).
                if so > 0:
                    live_sharp_implied = 100.0 / (so + 100.0) * 100.0
                elif so < 0:
                    live_sharp_implied = (-so) / ((-so) + 100.0) * 100.0
            except (TypeError, ValueError):
                live_sharp_implied = None
        # 2026-04-28 — VK2 is now the primary path for stats in
        # `_VK2_PRIMARY_STATS` even when no override was passed.
        # Explicit `p_true_method == "model"` is still honored as a
        # legacy escape hatch.
        explicit_legacy = (
            ((config or {}).get("override_config") or {})
            .get("vision_score", {})
            .get("p_true_method")
        ) == "model"
        use_vk2_path = (active_method_early == "vk2") or (
            model_key in self._VK2_PRIMARY_STATS and not explicit_legacy
        )
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
        # 2026-05 missing-value policy — populated from `mres.feature_health`
        # when the VK predict path runs. Persisted on the ScoringContext.
        vk_feature_health: Optional[Dict[str, Any]] = None
        # Model predictions only when identity resolved. Propagate the
        # ID through every downstream scoring call.
        if model_key in self._MODEL_STATS and bdl_player_id is not None:
            if not use_vk2_path:
                mres = self._predict_model_prob_over(
                    db=db, bdl_player_id=bdl_player_id,
                    player_name=player_name, stat_type=model_key,
                    line=float(line), opponent_team=opponent_team,
                    team_total=live_team_total,
                    target_game=live_target_game,
                    sharp_implied=live_sharp_implied,
                )
                p_over = mres.get("p_over")
                model_projection = mres.get("projection")
                model_sigma = mres.get("sigma")
                # 2026-04-27 — Recency blend (PTS/PRA only). Replaces μ
                # in-place with weighted L3/L10med/L20/model blend before
                # the engine sees it. REB/AST and other families pass
                # through unchanged.
                model_projection = self._maybe_blend_recency(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_model=model_projection,
                )
                # 2026-04-27 — Unified availability guard (DNP / injury
                # return / minutes restriction). Runs AFTER recency blend,
                # BEFORE engine. Adjusts μ multiplicatively by a
                # restriction_factor in [0.5, 1.0] derived from game-log
                # patterns. Returns None for OUT-classified props so the
                # gate skips them.
                model_projection = self._maybe_apply_availability_guard(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_in=model_projection,
                )
                # 2026-04-28 — Rate × minutes projection layer (PTS/PRA).
                # Runs AFTER availability guard, BEFORE engine. Conditionally
                # blends μ with rate_per_min × expected_minutes when the
                # player has dynamic minutes or a non-trivial availability
                # flag; otherwise leaves μ_model untouched.
                model_projection = self._maybe_apply_rate_model(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_model=model_projection,
                )
                # 2026-04-28 — Shadow Recipe E (audit-only). Stamps
                # `mu_recency_E` / `delta_mu_E_vs_A` for forward-test
                # validation. Returns μ unchanged.
                model_projection = self._maybe_apply_shadow_recency_E(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=model_projection,
                )
                # 2026-04-28 — Shadow VK2 PTS (audit-only). PTS only.
                model_projection = self._maybe_apply_shadow_pts_vk2(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=model_projection,
                )
                # 2026-04-29 — Shadow REB/AST rate × minutes (audit-only).
                # Stamps `mu_rate_reb_shadow` / `mu_rate_ast_shadow` for
                # forward-test validation. Returns μ unchanged.
                model_projection = self._maybe_apply_shadow_rate_reb_ast(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=model_projection,
                )
                # Universal probability engine override
                # for the legacy VK path. Engine receives empirical
                # (μ, σ) and re-derives p_over via Normal CDF (or
                # Poisson for STL/BLK 0.5). Audit fields land on `prop`
                # so recompute mirrors them onto the score doc.
                _eng = self._engine_p_over(
                    stat_type=model_key, line=float(line),
                    projection=model_projection, sigma=model_sigma,
                )
                if _eng is not None:
                    p_over = _eng["p_over"]
                    for k, v in _eng.items():
                        if k != "p_over":
                            prop[k] = v
                if p_over is not None:
                    p_true_model = round(
                        (1.0 - p_over) if side == "UNDER" else p_over, 4
                    )
                # 2026-05 missing-value policy — preserve VK's
                # feature_health summary so the score doc captures
                # which features were silent defaults.
                vk_feature_health = mres.get("feature_health")
                # 2026-04-26 NBA LOM (Phase 4) — DISABLED 2026-04-26.
                # The LOM artifacts at /app/backend/models/probability/lom/nba/
                # are kept on disk for research, but the v1 model regressed
                # REB Brier and produced ambiguous PRA improvements (see
                # diagnostic in CHANGELOG). Live NBA scoring uses the
                # Gaussian path until a v1.2 ships uniform per-stat gains.
                # MLB LOM remains active.
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
                vk2_projection = v2res.get("projection")
                vk2_sigma = v2res.get("sigma")
                vk2_error = v2res.get("error")
                # 2026-04-27 — Recency blend (PTS/PRA only) on VK2 path.
                vk2_projection = self._maybe_blend_recency(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_model=vk2_projection,
                )
                # 2026-04-27 — Availability guard on VK2 path.
                vk2_projection = self._maybe_apply_availability_guard(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_in=vk2_projection,
                )
                # 2026-04-28 — Rate × minutes layer (PTS/PRA only) on VK2 path.
                vk2_projection = self._maybe_apply_rate_model(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_model=vk2_projection,
                )
                # 2026-04-28 — Shadow Recipe E on VK2 path (audit-only).
                vk2_projection = self._maybe_apply_shadow_recency_E(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=vk2_projection,
                )
                # 2026-04-28 — Shadow VK2 PTS (audit-only). PTS only.
                # (Currently a no-op on VK2 path because PTS isn't in
                # `_VK2_PRIMARY_STATS` so this branch isn't taken for
                # PTS — kept for parity if PTS is later promoted.)
                vk2_projection = self._maybe_apply_shadow_pts_vk2(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=vk2_projection,
                )
                # 2026-04-29 — Shadow REB/AST rate × minutes (audit-only)
                # on the VK2 path. REB/AST already route through VK2 in
                # production so this is the primary trip-point for the
                # shadow stamps.
                vk2_projection = self._maybe_apply_shadow_rate_reb_ast(
                    stat_type=model_key, bdl_player_id=bdl_player_id,
                    prop=prop, mu_current=vk2_projection,
                )
                # 2026-04-27 — universal-engine override on the VK2 path.
                _eng_v2 = self._engine_p_over(
                    stat_type=model_key, line=float(line),
                    projection=vk2_projection, sigma=vk2_sigma,
                )
                if _eng_v2 is not None:
                    p_over_v2 = _eng_v2["p_over"]
                    for k, v in _eng_v2.items():
                        if k != "p_over":
                            prop[k] = v
                if p_over_v2 is not None:
                    p_true_vk2 = round(
                        (1.0 - p_over_v2) if side == "UNDER" else p_over_v2, 4
                    )
                # NBA LOM disabled on the VK2 path as well — see comment
                # in the legacy-VK branch above.
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
                    team_total=live_team_total,
                    target_game=live_target_game,
                    sharp_implied=live_sharp_implied,
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
                    # 2026-04-27 — engine override on synth fallback
                    _eng_c = self._engine_p_over(
                        stat_type=model_key, line=float(line),
                        projection=model_projection_synth,
                        sigma=model_sigma_synth,
                    )
                    if _eng_c is not None:
                        p_over_c = _eng_c["p_over"]
                        for k, v in _eng_c.items():
                            if k != "p_over":
                                prop[k] = v
                    if p_over_c is not None:
                        p_true_model = round(
                            (1.0 - p_over_c) if side == "UNDER" else p_over_c, 4
                        )
                    model_projection = model_projection_synth
                    model_sigma = model_sigma_synth
                    projection_method = "combo_synth"
                # 2026-04-26 PRA SYNTH-PREFERRED (Phase 5):
                # backtest on 436 settled props showed Brier 0.2157→0.2125
                # (-0.0032 improvement, 62.8% per-row win rate) with
                # high-confidence calibration gap closing from -0.049 to
                # -0.002 at p≥0.80. When BOTH direct and synth are
                # available, prefer synth — it removes the -0.41 median
                # bias of the direct VK PRA regressor and uses the
                # covariance-aware sigma from `_predict_combo_projection`.
                # Direct VK PRA remains the strict fallback when any
                # component projection is missing. Affects only the
                # "pra" family; `_COMBO_COMPONENTS` synth path for
                # pts_reb / pts_ast / reb_ast is unchanged.
                elif (
                    resolved_family == "pra"
                    and direct_proj is not None
                    and model_projection_synth is not None
                    and model_sigma_synth is not None
                    and float(model_sigma_synth) > 0
                ):
                    p_over_c = cres_audit.get("p_over")
                    # 2026-04-27 — Recency blend (PRA-eligible synth path).
                    model_projection_synth = self._maybe_blend_recency(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_model=model_projection_synth,
                    )
                    # 2026-04-27 — Availability guard on PRA synth path.
                    model_projection_synth = self._maybe_apply_availability_guard(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_in=model_projection_synth,
                    )
                    # 2026-04-28 — Rate × minutes layer on PRA synth path.
                    model_projection_synth = self._maybe_apply_rate_model(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_model=model_projection_synth,
                    )
                    # 2026-04-28 — Shadow Recipe E on PRA synth path.
                    model_projection_synth = self._maybe_apply_shadow_recency_E(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_current=model_projection_synth,
                    )
                    # 2026-04-28 — Shadow VK2 PTS (audit-only). No-op
                    # on the PRA synth branch because synth is for PRA
                    # and combos, not PTS — kept for symmetry.
                    model_projection_synth = self._maybe_apply_shadow_pts_vk2(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_current=model_projection_synth,
                    )
                    # 2026-04-29 — Shadow REB/AST rate × minutes on PRA
                    # synth path. Synth fires for PRA / combo families
                    # only, so this is a no-op for REB/AST — kept for
                    # symmetry across all three projection paths.
                    model_projection_synth = self._maybe_apply_shadow_rate_reb_ast(
                        stat_type=model_key, bdl_player_id=bdl_player_id,
                        prop=prop, mu_current=model_projection_synth,
                    )
                    # 2026-04-27 — engine override on PRA synth-preferred
                    _eng_pra = self._engine_p_over(
                        stat_type=model_key, line=float(line),
                        projection=model_projection_synth,
                        sigma=model_sigma_synth,
                    )
                    if _eng_pra is not None:
                        p_over_c = _eng_pra["p_over"]
                        for k, v in _eng_pra.items():
                            if k != "p_over":
                                prop[k] = v
                    if p_over_c is not None:
                        p_true_model = round(
                            (1.0 - p_over_c) if side == "UNDER" else p_over_c, 4
                        )
                    model_projection = float(model_projection_synth)
                    model_sigma = float(model_sigma_synth)
                    if use_vk2_path:
                        vk2_projection = model_projection
                        vk2_sigma = model_sigma
                        if p_over_c is not None:
                            p_true_vk2 = round(
                                (1.0 - p_over_c) if side == "UNDER" else p_over_c,
                                4,
                            )
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
                team_total=live_team_total,
                target_game=live_target_game,
                sharp_implied=live_sharp_implied,
            )
            if cres.get("projection") is not None:
                p_over_c = cres.get("p_over")
                model_projection = cres.get("projection")
                model_sigma = cres.get("sigma")
                # 2026-04-27 — Availability guard on primary combo synth
                # path (covers pts_reb / pts_ast / reb_ast). Recency blend
                # does NOT run here (those families aren't in
                # _RECENCY_TARGET_STATS), but availability does.
                model_projection = self._maybe_apply_availability_guard(
                    stat_type=model_key or resolved_family,
                    bdl_player_id=bdl_player_id,
                    prop=prop, mu_in=model_projection,
                )
                # 2026-04-27 — engine override on primary combo synth.
                # `model_key` may be None for combo families that don't
                # have a registered VK model (e.g. pts_reb). Pass the
                # canonical family token so the registry resolves it.
                _eng_combo = self._engine_p_over(
                    stat_type=model_key or resolved_family,
                    line=float(line),
                    projection=model_projection, sigma=model_sigma,
                )
                if _eng_combo is not None:
                    p_over_c = _eng_combo["p_over"]
                    for k, v in _eng_combo.items():
                        if k != "p_over":
                            prop[k] = v
                if p_over_c is not None:
                    p_true_model = round(
                        (1.0 - p_over_c) if side == "UNDER" else p_over_c, 4
                    )
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
        tp_source = tp_result.get("tp_source")
        market_probability = tp_result.get("market_probability")
        tp_unavailable = tp is None
        prop["tp"] = tp
        prop["tp_books_used"] = tp_books_used
        prop["tp_books_list"] = tp_books_list
        prop["tp_method"] = tp_method
        prop["tp_source"] = tp_source
        prop["market_probability"] = market_probability
        prop["tp_unavailable"] = tp_unavailable

        # Spec step 4 (2026-04-24): explicit reason code for tp=None.
        # Replaces the catch-all `tp_unavailable=True` with a typed
        # reason so audits can distinguish genuine downstream bugs
        # (missing alias, unsupported stat, no live_props row) from
        # inherent sportsbook behaviour (alt-line one-sided quote).
        tp_reason = None
        if tp_unavailable:
            from services.scoring.gates.thresholds import (
                resolve_stat_family, STAT_FAMILY_ALIASES,
            )
            nba_aliases = STAT_FAMILY_ALIASES.get("nba", {}) or {}
            resolved_family = resolve_stat_family("nba", stat_type)
            has_explicit_alias = stat_type in nba_aliases
            market_key = (prop.get("market_key") or "").lower()
            is_alt = bool(prop.get("is_alternate_market")) or market_key.endswith(
                "_alternate"
            )
            has_any_side = any(
                prop.get(f"{b}_odds") is not None
                for b in ("dk", "fd", "mgm", "bol")
            ) or prop.get("draftkings_price") is not None

            if not has_explicit_alias and resolved_family == stat_type.strip().lower().replace(" ", "_"):
                # resolve_stat_family fell back to the raw lowercase key
                # — means we have no canonical family for this stat_type
                # and scoring cannot resolve a projection distribution.
                tp_reason = "unsupported_stat_family"
            elif not has_any_side:
                tp_reason = "no_live_props_quote"
            elif is_alt:
                # This side has a price on at least one book but no book
                # returned the opposite side — inherent alt-line one-
                # sided pattern (DK/FD alt markets typically publish a
                # single boosted price per point value).
                tp_reason = "alt_line_one_sided"
            else:
                # Standard market that should have paired — upstream
                # odds-extract gap.
                tp_reason = "standard_line_missing_opp"
        prop["tp_unavailable_reason"] = tp_reason

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
            # 2026-05 missing-value policy.
            feature_health=vk_feature_health,
            # 2026-05 injury context — team + opp aggregates (counts,
            # missing minutes/usage, key-player-out, vacuum factor).
            # Populated by `feature_hydration.py` upstream. NOT fed
            # into VK's trained 105-feature input — carried here for
            # observability and downstream consumption.
            injury_context=({
                "team": prop.get("team_injury_context"),
                "opp": prop.get("opp_injury_context"),
                "team_injury_count": prop.get("team_injury_count"),
                "team_out_count": prop.get("team_out_count"),
                "missing_usage_estimate": prop.get("missing_usage_estimate"),
                "missing_minutes_estimate": prop.get("missing_minutes_estimate"),
                "usage_vacuum_factor": prop.get("usage_vacuum_factor"),
                "key_player_out_flag": prop.get("key_player_out_flag"),
                "injury_data_is_imputed": (
                    (prop.get("team_injury_context") or {})
                    .get("injury_data_is_imputed", 1)
                ),
            } if prop.get("team_injury_context") is not None else None),
        )
