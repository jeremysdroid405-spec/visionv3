"""
MLB Scoring Adapter — reads mlb_live_props, produces ScoringContext.

Reuses MLBHighFrictionModel (XGBoost) for p_true predictions; all gate
evaluation routes through the Universal Gate Engine.
No odds-sync, no mutation of live props or cached_board.
"""
import os
import logging
from typing import Any, Dict, List, Optional

from services.scoring.adapters.base import ScoringAdapter, ScoringContext

logger = logging.getLogger(__name__)


class MLBScoringAdapter(ScoringAdapter):
    def __init__(self):
        self._stats_cache = None
        self._hf_model = None

    @property
    def sport(self) -> str:
        return "mlb"

    @property
    def live_props_collection(self) -> str:
        return "mlb_live_props"

    @property
    def scores_collection(self) -> str:
        return "mlb_prop_scores"

    @property
    def cached_board_collection(self) -> str:
        return "mlb_cached_board"

    async def load_live_props(self, db, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        cursor = db[self.live_props_collection].find({}, {"_id": 0})
        if limit:
            cursor = cursor.limit(int(limit))
        props = await cursor.to_list(length=None)
        logger.info(f"[MLB_SCORING] Loaded {len(props)} live props from {self.live_props_collection}")

        # 0-Book Exclusion Rule (2026-04-22): any prop with no exact-line
        # anchor from DraftKings / FanDuel / BetMGM / BetOnline is marked
        # pp_only and MUST NOT enter scoring, tiering, or the cached board.
        # This is the single MLB chokepoint — every scoring run funnels
        # through `load_live_props`, so filtering here covers delta,
        # master-sync, and recompute paths uniformly.
        from services.scoring.coverage_filter import filter_priceable
        priceable, coverage_stats = filter_priceable(props, sport="mlb")
        # Attach stats for the caller (pipeline logs / sync-result JSON).
        self.last_coverage_stats = coverage_stats

        # Multi-book de-vig TP engine (2026-04-22). Build the companion
        # (OVER ↔ UNDER) map over the FULL props list — not just the
        # priceable subset — so UNDER picks whose OVER companion was
        # pp_only-dropped still get their UNDER-side reference. The
        # companion map is attached to the instance so build_context
        # can reuse it without re-scanning live_props per prop.
        from services.scoring.tp_engine import build_companion_map
        self._companion_map = build_companion_map(props)
        return priceable

    def _get_stats_cache(self, db):
        """Return the MLB stat-utility cache (hit-rate / CV / ceiling rate
        lookups backed by BDL splits + historical logs). POST 2026-04-22
        Universal Gate Engine cleanup this helper no longer holds any
        gate logic; every threshold / reason code lives in
        `services.scoring.gates`. The class is kept as a stats cache
        only."""
        if self._stats_cache is None:
            from services.mlb_tier_sorter import MLBTierSorter
            self._stats_cache = MLBTierSorter(db)
        return self._stats_cache

    def _get_hf_model(self, db):
        if self._hf_model is None:
            import pymongo as _pymongo
            sync_client = _pymongo.MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client[os.environ.get('DB_NAME', 'pick_vision')]
            from services.mlb_high_friction_model import get_mlb_high_friction_model
            self._hf_model = get_mlb_high_friction_model(sync_db)
            if self._hf_model and not self._hf_model.models:
                self._hf_model.load_models()
        return self._hf_model

    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        """Normalize an mlb_live_props doc into a ScoringContext."""
        # Stats cache (hit-rate / CV / ceiling lookups). Name kept
        # intentionally generic post Universal Gate Engine cleanup —
        # no gate logic lives here.
        stats = self._get_stats_cache(db)
        if not getattr(stats, "_player_logs_cache", None):
            await stats._load_caches()

        player_name = prop.get("player_name")
        stat_type = prop.get("stat_type")
        line = prop.get("line")
        if line is None or player_name is None or stat_type is None:
            return None

        canon_key = prop.get("canonical_key")
        if not canon_key:
            canon_key = (
                f"mlb|{prop.get('event_id','?')}|{player_name}|{stat_type}|"
                f"{float(line)}|{prop.get('recommendation','OVER')}"
            )

        # Layers: prefer nested layers (new schema), else hydrate from flat fields.
        def _hydrate(book, ln_key, od_key, layer_key):
            nested = prop.get(layer_key)
            if nested:
                return nested
            ln = prop.get(ln_key)
            od = prop.get(od_key)
            if ln is None and od is None:
                return None
            return {"book": book, "line": ln, "odds": od}

        pp_layer = _hydrate("prizepicks", "pp_line", "pp_odds", "pp_layer") or {
            "book": "prizepicks", "line": line, "odds": prop.get("odds")
        }
        dk_layer = _hydrate("draftkings", "dk_line", "dk_odds", "dk_layer")
        mgm_layer = _hydrate("betmgm", "mgm_line", "mgm_odds", "mgm_layer")
        sharp_layer = prop.get("sharp_layer") or (
            {"book": prop.get("sharp_book"), "line": prop.get("sharp_line"),
             "odds": prop.get("sharp_odds")}
            if prop.get("sharp_odds") is not None else None
        )

        # Stats from hub (pure metric normalization — no gate logic)
        cv = stats._calculate_cv(player_name, stat_type)
        hit_rate, _ = stats._calculate_hit_rate(player_name, stat_type, line, 20)
        ceiling_rate = stats._calculate_ceiling_hit_rate(player_name, stat_type, line)

        # Model
        hf_model = self._get_hf_model(db)
        p_true_model = None
        model_projection = None
        model_sigma = None
        if hf_model:
            opponent = prop.get('away_team') if not prop.get('is_away_team') else prop.get('home_team')
            park_team = prop.get('home_team') if prop.get('is_away_team') else prop.get('team')
            dk_odds_int = None
            if dk_layer and dk_layer.get("odds") is not None:
                try:
                    dk_odds_int = int(dk_layer["odds"])
                except (TypeError, ValueError):
                    dk_odds_int = None
            result = hf_model.predict(
                player_name=player_name, stat_type=stat_type, line=line,
                opponent_team=opponent, park_team=park_team, dk_odds=dk_odds_int,
            )
            if result and not result.get("error") and result.get("prob_over") is not None:
                prob_over_pct = result["prob_over"]
                # Side-aware flip: MLB stores over-prob; flip for UNDER picks
                # so p_true_model always reflects the side we're picking.
                side = (prop.get("recommendation") or "OVER").upper()
                if "UNDER" in side:
                    p_true_model = round((100.0 - prob_over_pct) / 100.0, 4)
                else:
                    p_true_model = round(prob_over_pct / 100.0, 4)
                # Preserve projection + sigma so recompute can populate
                # model_projection and ranking_score_v2 for MLB.
                model_projection = result.get("predicted")
                model_sigma = result.get("std_dev")

        # Books available
        books = 0
        if pp_layer: books += 1
        if dk_layer: books += 1
        if mgm_layer: books += 1
        if sharp_layer: books += 1

        # ---- Multi-book de-vigged TP (2026-04-22) ----------------------
        # Single-prop path using `{book}_odds_opp` captured at extract
        # time — no companion-row lookup needed. Mathematically correct
        # per-book de-vig average across every book (DK/FD/MGM/BOL)
        # that quotes BOTH sides of this exact line. No 50% fallback.
        from services.scoring.tp_engine import compute_tp
        side_for_tp = (prop.get("recommendation") or "OVER").upper()
        tp_result = compute_tp(prop=prop, side=side_for_tp)
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

        # ---- Shared p_true ladder (carbon-copy with NBA) ---------------
        # Ladder order: model → hit_rate → vk2 → fair
        # MLB currently has no vk2 model on disk, so that rung stays None
        # until a 5-year adv-stat MLB VK2 model is trained. The structure
        # is identical to NBA — only the available rungs differ.
        p_true_hit_rate = (hit_rate / 100.0) if hit_rate is not None else None
        p_true_vk2 = None  # reserved: future MLB VK2 model
        from services.scoring.scoring_stack import resolve_p_true_ladder
        p_model, p_true_method_used = resolve_p_true_ladder(
            p_true_model=p_true_model,
            p_true_hit_rate=p_true_hit_rate,
            p_true_vk2=p_true_vk2,
            tp=tp,  # enables the "fair" rung when a reference market exists
        )

        # Multi-book de-vig TP contract (2026-04-22): no 50% fallback.
        # edge_pct is None when tp or model-prob are both missing so
        # downstream gates receive an explicit "unknown" signal rather
        # than a fabricated 0.0.
        if tp is None or (p_model is None and hit_rate is None):
            edge_pct = None
        elif p_model is not None:
            edge_pct = round((p_model * 100.0) - tp, 1)
        else:
            edge_pct = round((hit_rate or 0) - tp, 1)

        # MLB has no verified PP multiplier data yet
        return ScoringContext(
            canonical_key=canon_key,
            sport="mlb",
            event_id=prop.get("event_id"),
            player_name=player_name,
            stat_type=stat_type,
            line=float(line),
            recommendation=prop.get("recommendation"),
            pp_layer=pp_layer,
            dk_layer=dk_layer,
            mgm_layer=mgm_layer,
            sharp_layer=sharp_layer,
            p_model=p_model,
            p_true_hit_rate=p_true_hit_rate,
            p_true_model=p_true_model,
            p_true_method=p_true_method_used,
            p_true_vk2=p_true_vk2,
            model_projection=model_projection,
            model_sigma=model_sigma,
            cv=cv,
            hit_rate=hit_rate,
            edge_pct=edge_pct,
            tp=tp,
            ceiling_rate=ceiling_rate,
            books_available_count=books,
            raw_prop=prop,
            pp_combo_multiplier=None,  # not yet available for MLB
            pp_label=None,
            pp_multiplier_model=None,
        )

    # ---------------------------------------------------------
    # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): persist tempo +
    # intel_suite at scoring-write time. Eliminates the route-time
    # enrichers `enrich_mlb_prop_with_tempo` and `enrich_mlb_intel_suite`
    # from the live board path (D11).
    # ---------------------------------------------------------
    def enrich_score_doc(
        self, raw_prop: Dict[str, Any], ctx: ScoringContext
    ) -> Dict[str, Any]:
        # Lazy import to avoid circular deps (routes → services).
        try:
            from routes.ferrari_tiers import (
                enrich_mlb_prop_with_tempo,
                enrich_mlb_intel_suite,
            )
        except Exception as e:
            logger.debug(f"[MLB_SCORING] enrichers unavailable: {e}")
            return {}

        # Work on a shallow copy so we never mutate the live_props source.
        working = dict(raw_prop) if raw_prop else {}
        try:
            enrich_mlb_prop_with_tempo(working)
        except Exception as e:
            logger.debug(f"[MLB_SCORING] tempo enrich skipped: {e}")
        try:
            enrich_mlb_intel_suite(working)
        except Exception as e:
            logger.debug(f"[MLB_SCORING] intel_suite enrich skipped: {e}")

        return {
            "tempo_modifier": working.get("tempo_modifier"),
            "intel_suite": working.get("intel_suite"),
        }
