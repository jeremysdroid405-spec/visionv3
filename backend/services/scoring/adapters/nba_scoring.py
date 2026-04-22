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


class _NBAGateSorter:
    """
    Minimal NBA gate sorter satisfying the MLBTierSorter-compatible contract.
    Uses hit-rate + edge + tp + CV thresholds scaled for NBA's stat types.

    These thresholds are intentionally conservative placeholders — tuned
    by NBA analytics can be passed in via config.override_config.tier.
    """
    SAFE_HAVEN = {"max_cv": 0.50, "min_hit_rate": 75, "min_edge": 8, "min_tp": 70}
    FRONT_LINES = {"max_cv": 0.75, "min_hit_rate": 60, "min_edge": 5, "min_tp": 55}
    # NBA war_zone defaults (varA, locked 2026-04-17 after tuning validation).
    # MLB-scale thresholds (cv=0.80, ceil=30, edge=15) were unreachable on NBA.
    # varA produces ~1.5% slate-share legitimate moonshots with zero cannibalization
    # of safe_haven/front_lines; all entrants migrate from unqualified only.
    WAR_ZONE = {"min_cv": 0.45, "min_ceiling_rate": 20, "min_edge": 10}
    # WAR_ZONE gate review (2026-04-19): these three gates are intentionally
    # market-facing only (CV, ceiling_rate, market edge). Model-side veto
    # (negative vk_edge + weak hit_rate_over + wrong-side l10_avg) is a
    # SEPARATE concern handled by scoring_stack._model_contradicts_anchor,
    # which runs BEFORE tier dispatch and can eject a pick from any tier.
    # Duplicating it here would weaken legitimate long-odds moonshots whose
    # ceiling/CV profile qualifies even when short-term VK trails. The two
    # layers compose cleanly: gates decide bucket, veto decides side.

    # Path (c) — side-aware tp gate: UNDER picks replace market-implied tp with
    # a model-confidence floor (p_model * 100) at BALANCED thresholds locked
    # 2026-04-18 after slate audit. OVER path is unchanged. `war_zone_under`
    # is recorded for future use; war_zone has no gate_tp today (ceiling-only).
    UNDER_TP_FLOORS = {
        "safe_haven": 75,
        "front_lines": 65,
        "war_zone": 60,  # not yet wired — awaits floor_rate side-aware gate
    }

    def __init__(self, overrides: Optional[Dict[str, Any]] = None):
        o = overrides or {}
        self.SAFE_HAVEN = {**self.SAFE_HAVEN, **(o.get("safe_haven") or {})}
        self.FRONT_LINES = {**self.FRONT_LINES, **(o.get("front_lines") or {})}
        self.WAR_ZONE = {**self.WAR_ZONE, **(o.get("war_zone") or {})}
        self.UNDER_TP_FLOORS = {**self.UNDER_TP_FLOORS, **(o.get("under_tp_floors") or {})}

    def _check(self, gates_def, *, cv=None, hit_rate=None, edge_pct=None,
               tp=None, ceiling_rate=None,
               side: str = "OVER", p_model_pct: Optional[float] = None,
               tier_name: Optional[str] = None):
        results = {}
        if "max_cv" in gates_def:
            results["gate_cv"] = {
                "threshold": gates_def["max_cv"], "value": cv,
                "passed": cv is not None and cv <= gates_def["max_cv"],
            }
        if "min_cv" in gates_def:
            results["gate_cv"] = {
                "threshold": gates_def["min_cv"], "value": cv,
                "passed": cv is not None and cv >= gates_def["min_cv"],
            }
        if "min_hit_rate" in gates_def:
            results["gate_hit_rate"] = {
                "threshold": gates_def["min_hit_rate"], "value": hit_rate,
                "passed": hit_rate is not None and hit_rate >= gates_def["min_hit_rate"],
            }
        if "min_ceiling_rate" in gates_def:
            results["gate_ceiling"] = {
                "threshold": gates_def["min_ceiling_rate"], "value": ceiling_rate,
                "passed": ceiling_rate is not None and ceiling_rate >= gates_def["min_ceiling_rate"],
            }
        if "min_edge" in gates_def:
            results["gate_edge"] = {
                "threshold": gates_def["min_edge"], "value": edge_pct,
                "passed": edge_pct is not None and edge_pct >= gates_def["min_edge"],
            }
        if "min_tp" in gates_def:
            if side == "UNDER" and tier_name in self.UNDER_TP_FLOORS:
                # Side-aware path: replace market-implied tp with model
                # confidence (p_model * 100) against UNDER-specific threshold.
                under_floor = self.UNDER_TP_FLOORS[tier_name]
                results["gate_tp"] = {
                    "threshold": under_floor, "value": p_model_pct,
                    "passed": p_model_pct is not None and p_model_pct >= under_floor,
                    "source": "model_confidence_under",
                }
            else:
                results["gate_tp"] = {
                    "threshold": gates_def["min_tp"], "value": tp,
                    "passed": tp is not None and tp >= gates_def["min_tp"],
                    "source": "market_implied_over" if side == "OVER" else "market_implied",
                }
        failed = [k for k, v in results.items() if not v["passed"]]
        return (len(failed) == 0), (",".join(failed) or "ok"), results

    def check_safe_haven_gates(self, prop, cv, hit_rate, edge_pct, tp,
                               side: str = "OVER", p_model_pct: Optional[float] = None):
        # Stat-aware CV cap (2026-04-21): structurally higher CV on
        # small-mean stats (AST/REB/STL/BLK) was rejecting high-HitR picks.
        # Resolve the cap from prop["stat_type"] and override the default
        # Safe Haven max_cv for this single check.  PTS/PRA still use 0.50.
        from services.scoring.cv_caps import resolve_cv_cap
        stat_type = (prop or {}).get("stat_type") if isinstance(prop, dict) else None
        gates_def = {**self.SAFE_HAVEN, "max_cv": resolve_cv_cap(stat_type)}
        return self._check(gates_def, cv=cv, hit_rate=hit_rate,
                           edge_pct=edge_pct, tp=tp,
                           side=side, p_model_pct=p_model_pct, tier_name="safe_haven")

    def check_front_lines_gates(self, prop, cv, hit_rate, edge_pct, tp,
                                side: str = "OVER", p_model_pct: Optional[float] = None):
        return self._check(self.FRONT_LINES, cv=cv, hit_rate=hit_rate,
                           edge_pct=edge_pct, tp=tp,
                           side=side, p_model_pct=p_model_pct, tier_name="front_lines")

    def check_war_zone_gates(self, prop, cv, ceiling_rate, edge_pct):
        # war_zone has no tp gate — UNDER qualification here awaits the
        # separate floor_rate work. OVER behaviour fully preserved.
        return self._check(self.WAR_ZONE, cv=cv, ceiling_rate=ceiling_rate,
                           edge_pct=edge_pct)


class NBAScoringAdapter(ScoringAdapter):
    # Map our stat_type to the bdl_game_logs field
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
        self._sorter = None
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
        return priceable

    def get_sorter(self, db):
        return self._sorter  # populated per-recompute with config

    def _build_sorter(self, config):
        overrides = ((config or {}).get("override_config") or {}).get("tier")
        self._sorter = _NBAGateSorter(overrides)
        return self._sorter

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

    def _compute_cv_and_hit_rate(
        self, player_name: str, stat_type: str, line: float,
        direction: str = "OVER", window: int = 20,
    ):
        """
        Compute (cv, hit_rate, ceiling_rate, hit_rate_over, hit_rate_under)
        from the player's last-N game logs.

        hit_rate is SIDE-AWARE:
          - direction == OVER  → % games where stat > line
          - direction == UNDER → % games where stat <= line  (i.e., 100 - over_rate)
        hit_rate_over / hit_rate_under are always returned for diagnostics.

        ceiling_rate is direction-aware too:
          - OVER:  % games where stat >= max(1.5*line, line+0.5)   (tail up)
          - UNDER: % games where stat <= max(0.5*line, line-0.5)   (tail down)

        Returns (None, None, None, None, None) if unavailable.
        """
        field = self._STAT_FIELD_MAP.get(stat_type)
        if field is None:
            return None, None, None, None, None
        logs = self._logs_cache.get((player_name or "").lower()) or []
        if not logs:
            return None, None, None, None, None

        # Newest-first order is NOT guaranteed; sort by date desc for the window.
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
        # PRA synthesized
        if stat_type == "PRA":
            vals = [
                (g.get("pts") or 0) + (g.get("reb") or 0) + (g.get("ast") or 0)
                for g in window_logs
                if g.get("pts") is not None
            ]
        else:
            vals = [g.get(field) for g in window_logs if g.get(field) is not None]
        vals = [float(v) for v in vals if v is not None]
        if len(vals) < 5:
            return None, None, None, None, None

        arr = np.array(vals)
        mean = float(arr.mean())
        if mean <= 0:
            cv = None
        else:
            cv = round(float(arr.std(ddof=1) / mean), 4)

        # Side-aware hit rate
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

        return cv, hit_rate, ceiling_rate, hit_rate_over, hit_rate_under

    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        if self._sorter is None:
            self._build_sorter(config)
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
        # NBA market/prop_type → stat_type
        market = prop.get("market", "")
        stat_type = {
            "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
            "player_points_rebounds_assists": "PRA",
            "player_points_alternate": "PTS", "player_rebounds_alternate": "REB",
            "player_assists_alternate": "AST",
            "player_points_rebounds_assists_alternate": "PRA",
        }.get(market, prop.get("stat_type_extracted") or market)

        line = prop.get("line")
        if player_name is None or line is None or stat_type is None:
            return None

        direction = (prop.get("direction") or "OVER").upper()
        side = "OVER" if "OVER" in direction else "UNDER"
        event_id = prop.get("event_id", "?")

        canon_key = (
            f"nba|{event_id}|{player_name}|{stat_type}|{float(line)}|{side}"
        )

        # PP layer (primary)
        pp_layer = {
            "book": "prizepicks",
            "line": float(line),
            "odds": prop.get("price"),
        }

        # Sharp market → dk/sharp layers (line is assumed to match PP since dg_live_props
        # stores one row per PP-anchored prop)
        sm = prop.get("sharp_market") or {}
        dk_price = sm.get("draftkings_price")
        dk_layer = (
            {"book": "draftkings", "line": float(line), "odds": dk_price}
            if dk_price is not None else None
        )
        fd_price = sm.get("fanduel_price")
        # NBA has no MGM in dg_live_props — use FanDuel as the second reference book
        # but label it in the layer as 'fanduel'. The scoring_stack only uses
        # dk/mgm/sharp; to keep compatibility, treat FanDuel as mgm_layer for NBA.
        mgm_layer = (
            {"book": "fanduel", "line": float(line), "odds": fd_price}
            if fd_price is not None else None
        )
        bo_price = sm.get("betonline_price")
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
        cv, computed_hit_rate, ceiling_rate, hit_rate_over, hit_rate_under = \
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
        # Computed BEFORE the p_true ladder so the "fair" rung has tp.
        def _amer(o):
            if o is None: return None
            try: o = float(o)
            except (TypeError, ValueError): return None
            return abs(o)/(abs(o)+100.0) if o < 0 else 100.0/(o+100.0)
        dk_p = _amer(dk_price)
        fd_p = _amer(fd_price)
        if dk_p is not None and fd_p is not None:
            tp_over = round(((dk_p + fd_p) / 2.0) * 100.0, 1)
        elif dk_p is not None:
            tp_over = round(dk_p * 100.0, 1)
        elif fd_p is not None:
            tp_over = round(fd_p * 100.0, 1)
        else:
            tp_over = None
        # Flip to the recommended side so gate_tp and edge_pct reflect
        # the market's implied probability for the SIDE we are picking.
        if tp_over is None:
            tp = None  # no reference market → fair rung disabled, tp default 50.0 for gates
        else:
            tp = round(100.0 - tp_over, 1) if side == "UNDER" else tp_over

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

        # Gates/edge keep the legacy tp default of 50.0 when no reference
        # market exists, preserving behaviour for downstream gate checks.
        tp_for_gates = tp if tp is not None else 50.0

        if p_model is not None:
            edge_pct = round(p_model * 100.0 - tp_for_gates, 1)
        else:
            edge_pct = 0.0

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
            p_model=p_model, cv=cv, hit_rate=hit_rate, edge_pct=edge_pct,
            tp=tp_for_gates, ceiling_rate=ceiling_rate,
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
