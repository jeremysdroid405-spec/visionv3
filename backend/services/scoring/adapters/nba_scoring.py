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
from typing import Any, Dict, List, Optional

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
    # VK v2 model file paths (new 5-season weighted models w/ advanced stats)
    _VK2_DIR = "/app/backend/models"
    _VK2_FILE_MAP = {
        "PTS": "vk2_pts.pkl",
        "REB": "vk2_reb.pkl",
        "AST": "vk2_ast.pkl",
        "3PM": "vk2_3pm.pkl",
        "PRA": "vk2_pra.pkl",
    }

    def __init__(self):
        self._cv_cache: dict = {}
        self._logs_cache: dict = {}
        self._logs_loaded = False
        self._vk = None         # lazy-init legacy VegasKillerModel
        self._vk_sigmas: dict = {}   # stat_type -> residual SD (empirical, from test RMSE)
        self._vk2_loaded: bool = False
        self._vk2_models: dict = {}  # stat -> {model, scaler, features, sigma}
        self._vk2_adv_map: dict = {}      # (player_id, game_id) -> adv doc
        self._vk2_adv_loaded: bool = False

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
        self, db, player_name: str, stat_type: str,
        line: float, opponent_team: Optional[str],
    ) -> Dict[str, Optional[float]]:
        """Run VegasKiller projection + convert to prob_over via empirical
        residual calibration.

        Returns {projection, sigma, p_over, error?}."""
        if stat_type not in self._MODEL_STATS:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": f"no_model_for_{stat_type}"}
        vk = self._get_vk(db)
        if not vk.models:
            return {"projection": None, "sigma": None, "p_over": None,
                    "error": "vk_not_loaded"}
        try:
            r = vk.predict(
                player_name=player_name, stat_type=stat_type,
                line=line, opponent_team=opponent_team,
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
        # Normal CDF: P(stat > line) = 1 - Phi((line - mu) / sigma) = Phi((mu - line) / sigma)
        from math import erf, sqrt
        z = (float(projection) - float(line)) / float(sigma)
        p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))
        return {
            "projection": round(float(projection), 3),
            "sigma": round(float(sigma), 3),
            "p_over": round(float(p_over), 4),
            "error": None,
        }

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

    def _get_vk2_history_logs(self, player_name: str, window: int = 20) -> List[Dict[str, Any]]:
        """Pull newest-first historical logs for a player from the master hub
        cache and normalize keys for VK2 feature builder.
        Master hub stores `bdl_player_id` (== historical `player_id`) so we
        remap it to `player_id` for adv_map matching."""
        raw = self._logs_cache.get((player_name or "").lower()) or []
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
        self, player_name: str, stat_type: str, line: float,
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
        history = self._get_vk2_history_logs(player_name, window=20)
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
        sigma = float(m["sigma"])
        if sigma <= 0:
            return {"projection": round(projection, 3), "sigma": sigma,
                    "p_over": None, "error": "sigma_invalid"}
        from math import erf, sqrt
        z = (projection - float(line)) / sigma
        p_over = 0.5 * (1.0 + erf(z / sqrt(2.0)))
        return {
            "projection": round(projection, 3),
            "sigma": round(sigma, 3),
            "p_over": round(p_over, 4),
            "error": None,
        }

    async def _preload_game_logs(self, db) -> None:
        """Pull NBA game logs from master hub once per recompute."""
        if self._logs_loaded:
            return
        hub = db[COLL("master_hub", "nba")]
        cursor = hub.find(
            {"bdl_game_logs_count": {"$gt": 0}},
            {"display_name": 1, "bdl_game_logs": 1, "_id": 0},
        )
        count = 0
        async for doc in cursor:
            name = (doc.get("display_name") or "").strip()
            if not name:
                continue
            self._logs_cache[name.lower()] = doc.get("bdl_game_logs") or []
            count += 1
        self._logs_loaded = True
        logger.info(f"[NBA_SCORING] Cached game logs for {count} players")

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
        self, player_name: str, stat_type: str, line: float,
        direction: str = "OVER", window: int = 20,
    ):
        """Compute line-independent CV and line-aware hit_rate / ceiling.

        CV is derived from the player's underlying stat-family
        distribution and is IDENTICAL across every line / alt-line for
        the same (player, family). Hit-rate and ceiling-rate still
        depend on the line / side (they count games relative to the
        threshold).

        Returns:
          (cv, cv_status, hit_rate, ceiling_rate, hit_rate_over, hit_rate_under)

        cv_status values:
          * "computed"                    – cv is a real stddev/mean
          * "unavailable_stat_family"     – we have no family spec yet
          * "missing_source_distribution" – fewer than 5 games, or
                                            degenerate zero-mean
        """
        family = self._resolve_family(stat_type)
        if family is None:
            return None, "unavailable_stat_family", None, None, None, None
        fields = self._FAMILY_SPEC.get(family)
        if not fields:
            return None, "unavailable_stat_family", None, None, None, None

        logs = self._logs_cache.get((player_name or "").lower()) or []
        if not logs:
            return None, "missing_source_distribution", None, None, None, None

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
            return None, "missing_source_distribution", None, None, None, None

        arr = np.array(vals)
        mean = float(arr.mean())
        if mean <= 0:
            cv = None
            cv_status = "missing_source_distribution"
        else:
            cv = round(float(arr.std(ddof=1) / mean), 4)
            cv_status = "computed"

        # Cache CV per (player, family) so downstream code paths that
        # re-query the same family on a different line can read a
        # consistent value without re-traversing logs.
        self._cv_cache[((player_name or "").lower(), family)] = (cv, cv_status)

        # Side-aware hit rate (line-dependent)
        over_hits = int(sum(1 for v in vals if v > line))
        under_hits = int(sum(1 for v in vals if v <= line))
        hit_rate_over = round((over_hits / len(vals)) * 100.0, 1)
        hit_rate_under = round((under_hits / len(vals)) * 100.0, 1)

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

        return cv, cv_status, hit_rate, ceiling_rate, hit_rate_over, hit_rate_under

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

        # CV + SIDE-AWARE hit_rate + ceiling_rate from master-hub game logs.
        # Prefer computed over embedded; fall back to embedded if logs missing.
        cv, cv_status, computed_hit_rate, ceiling_rate, hit_rate_over, hit_rate_under = \
            self._compute_cv_and_hit_rate(
                player_name, stat_type, float(line),
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
        if stat_type in self._MODEL_STATS:
            opponent_team = prop.get("opponent") or prop.get("away_team")
            if active_method_early != "vk2":
                mres = self._predict_model_prob_over(
                    db=db, player_name=player_name, stat_type=stat_type,
                    line=float(line), opponent_team=opponent_team,
                )
                p_over = mres.get("p_over")
                if p_over is not None:
                    p_true_model = round(
                        (1.0 - p_over) if side == "UNDER" else p_over, 4
                    )
                model_projection = mres.get("projection")
                model_sigma = mres.get("sigma")
            else:
                v2res = self._predict_vk2_prob_over(
                    player_name=player_name, stat_type=stat_type, line=float(line),
                )
                p_over_v2 = v2res.get("p_over")
                if p_over_v2 is not None:
                    p_true_vk2 = round(
                        (1.0 - p_over_v2) if side == "UNDER" else p_over_v2, 4
                    )
                vk2_projection = v2res.get("projection")
                vk2_sigma = v2res.get("sigma")
                vk2_error = v2res.get("error")

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
            p_model=p_model, cv=cv, cv_status=cv_status, hit_rate=hit_rate, edge_pct=edge_pct,
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
        )
