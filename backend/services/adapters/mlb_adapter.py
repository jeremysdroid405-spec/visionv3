"""
MLB Sport Adapter
==================
Delegates to existing mlb_tier_sorter scoring and gate logic.
Wraps all MLB-specific logic: BDL game logs, Lasso models, SP matchup,
tempo calculations, stat-specific gate thresholds.

This adapter plugs into UnifiedPipeline without changing any scoring math.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from services.unified_pipeline import SportAdapter

logger = logging.getLogger(__name__)


class MLBAdapter(SportAdapter):
    """MLB-specific pipeline adapter."""

    def __init__(self, tier_sorter=None):
        self._sorter = tier_sorter

    @property
    def sport(self) -> str:
        return "mlb"

    @property
    def tier_collections(self) -> Dict[str, str]:
        return {
            "safe_haven": "mlb_safe_haven",
            "front_lines": "mlb_front_lines",
            "war_zone": "mlb_war_zone",
        }

    async def _get_sorter(self, db):
        """Lazy-init the MLBTierSorter."""
        if self._sorter is None:
            from services.mlb_tier_sorter import MLBTierSorter
            self._sorter = MLBTierSorter(db)
            await self._sorter._load_caches()
        return self._sorter

    async def load_board(self, db) -> List[Dict]:
        """Load MLB props from mlb_cached_board, flatten props+goblins+demons, deduplicate."""
        board = db["mlb_cached_board"]
        cursor = board.find({}, {"_id": 0})
        players = await cursor.to_list(length=None)

        all_props = []
        seen = set()
        for player in players:
            combined = player.get("props", []) + player.get("goblins", []) + player.get("demons", [])
            for prop in combined:
                key = f"{player.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}"
                if key in seen:
                    continue
                seen.add(key)
                prop["player_name"] = player.get("player_name")
                prop["team"] = player.get("team")
                prop["position"] = player.get("position")
                all_props.append(prop)

        logger.info(f"[MLB_ADAPTER] Loaded {len(all_props)} unique props from {len(players)} players")
        return all_props

    async def enrich_and_score(self, props: List[Dict], db) -> List[Dict]:
        """
        Enrich and score MLB props.
        
        Runs the MLBHighFrictionModel (XGBoost) LIVE for each prop to get
        model-driven predictions and probability-based edge.
        
        Edge priority:
          1. Model prob_over - DK implied probability (post-model edge)
          2. Fallback: hit_rate - tp (pre-model edge) if model fails
        """
        sorter = await self._get_sorter(db)

        # Load the XGBoost VK model
        from services.mlb_high_friction_model import get_mlb_high_friction_model
        import pymongo as _pymongo
        _sync_client = _pymongo.MongoClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
        _sync_db = _sync_client[os.environ.get('DB_NAME', 'pick_vision')]
        hf_model = get_mlb_high_friction_model(_sync_db)
        if hf_model and not hf_model.models:
            hf_model.load_models()

        scored = []
        model_stats = {'total': len(props), 'no_odds': 0, 'scored': 0, 'model_hit': 0, 'model_miss': 0}

        for prop in props:
            player_name = prop.get("player_name", "?")
            stat_type = prop.get("stat_type", "?")
            line = prop.get("line") or 0

            # DK odds
            all_odds = prop.get("all_odds") or {}
            dk_odds = all_odds.get("draftkings") or prop.get("dk_odds")
            mgm_odds = all_odds.get("betmgm") or prop.get("mgm_odds")
            if dk_odds is None and mgm_odds is None:
                model_stats['no_odds'] += 1

            # -------------------------------------------------------
            # PROBABILITY CONVERSION (Section 2)
            # -------------------------------------------------------
            def _odds_to_prob(odds):
                if odds is None:
                    return None
                if odds < 0:
                    return round(abs(odds) / (abs(odds) + 100), 4)
                else:
                    return round(100 / (odds + 100), 4)

            p_dk = _odds_to_prob(dk_odds)
            p_mgm = _odds_to_prob(mgm_odds)

            # -------------------------------------------------------
            # CONSENSUS MARKET PROBABILITY (Section 3)
            # -------------------------------------------------------
            if p_dk is not None and p_mgm is not None:
                p_market_consensus = round((p_dk + p_mgm) / 2, 4)
                books_available_count = 2
            elif p_dk is not None:
                p_market_consensus = p_dk
                books_available_count = 1
            elif p_mgm is not None:
                p_market_consensus = p_mgm
                books_available_count = 1
            else:
                p_market_consensus = 0.50
                books_available_count = 0

            # -------------------------------------------------------
            # DISAGREEMENT METRIC (Section 4)
            # -------------------------------------------------------
            if p_dk is not None and p_mgm is not None:
                disagreement = round(abs(p_dk - p_mgm), 4)
                low_confidence = False
            else:
                disagreement = 0.0
                low_confidence = True

            # -------------------------------------------------------
            # AGREEMENT FACTOR (Section 5)
            # -------------------------------------------------------
            agreement_factor = max(0.7, round(1.0 - disagreement, 4))

            # -------------------------------------------------------
            # TRUE PROBABILITY from consensus (Section 6)
            # -------------------------------------------------------
            tp = round(p_market_consensus * 100, 1)  # as percentage

            # -------------------------------------------------------
            # STATS FROM HUB
            # -------------------------------------------------------
            cv = sorter._calculate_cv(player_name, stat_type)
            hit_rate, avg = sorter._calculate_hit_rate(player_name, stat_type, line, 20)
            h5_rate, h5_avg = sorter._calculate_hit_rate(player_name, stat_type, line, 5)
            h10_rate, h10_avg = sorter._calculate_hit_rate(player_name, stat_type, line, 10)
            ceiling_rate = sorter._calculate_ceiling_hit_rate(player_name, stat_type, line)

            # --- RUN MODEL LIVE ---
            vk_predicted = None
            vk_prob_over = None
            vk_z_score = None
            model_edge = None
            edge_source = 'pre_model'

            if hf_model:
                opponent = prop.get('away_team') if not prop.get('is_away_team') else prop.get('home_team')
                park_team = prop.get('home_team') if prop.get('is_away_team') else prop.get('team')
                result = hf_model.predict(
                    player_name=player_name,
                    stat_type=stat_type,
                    line=line,
                    opponent_team=opponent,
                    park_team=park_team,
                    dk_odds=int(dk_odds) if dk_odds else None,
                )
                if not result.get('error'):
                    vk_predicted = result.get('predicted')
                    vk_prob_over = result.get('prob_over')
                    vk_z_score = result.get('z_score')
                    if vk_prob_over is not None:
                        # Model edge = model probability - CONSENSUS market probability
                        model_edge = round(vk_prob_over - tp, 1)
                        edge_source = 'post_model'
                        model_stats['model_hit'] += 1
                    else:
                        model_stats['model_miss'] += 1
                else:
                    model_stats['model_miss'] += 1

            # Pre-model fallback
            pre_model_edge = round((hit_rate or 0) - tp, 1) if hit_rate is not None else 0

            # FINAL edge: model wins if available (Section 6)
            edge_pct = model_edge if model_edge is not None else pre_model_edge

            # -------------------------------------------------------
            # p_true: model probability or hit-rate based
            # -------------------------------------------------------
            p_true = (vk_prob_over / 100.0) if vk_prob_over is not None else ((hit_rate or 50) / 100.0)

            # -------------------------------------------------------
            # STABILITY from CV
            # -------------------------------------------------------
            if cv is not None and cv > 0:
                stability = round(max(0.3, min(1.0, 1.0 - (cv / 3.0))), 4)
            else:
                stability = 0.5

            # -------------------------------------------------------
            # CONFIDENCE from validation signals
            # -------------------------------------------------------
            confidence_signals = [
                1.0 if vk_predicted is not None else 0.0,      # has model
                1.0 if (hit_rate or 0) > 0 else 0.0,           # has hit rate
                1.0 if books_available_count >= 2 else 0.5,     # multi-book
                1.0 if not low_confidence else 0.5,             # not single-book fallback
            ]
            confidence = round(sum(confidence_signals) / len(confidence_signals), 4)

            # -------------------------------------------------------
            # VISION SCORE (Section 7)
            # -------------------------------------------------------
            edge_decimal = edge_pct / 100.0
            vision_score = round(edge_decimal * p_true * stability * confidence * agreement_factor, 6)
            vision_score_100 = round(min(100, max(0, vision_score * 1000)), 1)

            # -------------------------------------------------------
            # ANOMALY DETECTION FLAGS (Section 8)
            # -------------------------------------------------------
            dk_outlier = False
            mgm_outlier = False
            market_disagreement_high = False
            consensus_strong = False

            if p_dk is not None and p_mgm is not None:
                dk_outlier = abs(p_dk - p_mgm) > 0.10 and p_dk > p_mgm
                mgm_outlier = abs(p_mgm - p_dk) > 0.10 and p_mgm > p_dk
                market_disagreement_high = disagreement > 0.08
                consensus_strong = disagreement < 0.03

            board_score = tp + edge_pct + ((hit_rate or 0) / 10)

            scored_prop = {
                **prop,
                'player_name': player_name,
                'stat_type': stat_type,
                'line': line,
                # --- Book odds (Section 1, 9) ---
                'dk_odds': dk_odds,
                'mgm_odds': mgm_odds,
                'all_odds': all_odds,
                # --- Implied probabilities (Section 2) ---
                'p_dk': p_dk,
                'p_mgm': p_mgm,
                # --- Consensus (Section 3) ---
                'p_market_consensus': p_market_consensus,
                'books_available_count': books_available_count,
                # --- Disagreement (Section 4) ---
                'disagreement': disagreement,
                'low_confidence': low_confidence,
                # --- Agreement factor (Section 5) ---
                'agreement_factor': agreement_factor,
                # --- Edge (Section 6) ---
                'true_probability': tp,
                'edge_pct': edge_pct,
                'pre_model_edge': pre_model_edge,
                'post_model_edge': model_edge,
                'edge_source': edge_source,
                # --- Vision score (Section 7) ---
                'vision_score': vision_score,
                'vision_score_100': vision_score_100,
                'p_true': round(p_true, 4),
                'stability': stability,
                'confidence': confidence,
                # --- Anomaly flags (Section 8) ---
                'dk_outlier': dk_outlier,
                'mgm_outlier': mgm_outlier,
                'market_disagreement_high': market_disagreement_high,
                'consensus_strong': consensus_strong,
                # --- Stats ---
                'cv': cv,
                'hit_rate': hit_rate,
                'h5_rate': h5_rate or 0,
                'h10_rate': h10_rate or 0,
                'h20_rate': hit_rate or 0,
                'l10_avg': h10_avg,
                'l5_avg': h5_avg,
                'season_avg': avg,
                'ceiling_rate': ceiling_rate,
                # --- Model ---
                'vk_predicted': vk_predicted,
                'vk_prob_over': vk_prob_over,
                'vk_z_score': vk_z_score,
                'vk_edge': model_edge if model_edge is not None else pre_model_edge,
                'board_score': round(board_score, 2),
                'synced_at': datetime.now(timezone.utc).isoformat(),
                'validation': {
                    'has_market_data': books_available_count > 0,
                    'has_hit_rates': (hit_rate or 0) > 0,
                    'has_context': bool(prop.get('matchup_analysis')) or bool(all_odds),
                    'has_mlr': vk_predicted is not None,
                    'has_gemini': bool(prop.get('vision_intel')),
                    'has_consensus': books_available_count >= 2,
                    'is_fully_validated': False,
                },
            }

            v = scored_prop['validation']
            v['is_fully_validated'] = all([
                v['has_market_data'],
                v['has_hit_rates'],
            ])

            scored.append(scored_prop)
            model_stats['scored'] += 1

        logger.info(
            f"[MLB_ADAPTER] Scored {model_stats['scored']}/{model_stats['total']} props "
            f"(model_hit={model_stats['model_hit']}, model_miss={model_stats['model_miss']}, no_odds={model_stats['no_odds']})"
        )
        return scored

    TIER_CAPACITY = 10

    def select_tiers(self, scored_props: List[Dict], previous_tiers: Optional[Dict[str, List[Dict]]] = None) -> Dict[str, List[Dict]]:
        """
        MLB tier selection using the INTENDED stat-specific gate system.
        
        Delegates to MLBTierSorter.check_*_gates() methods which enforce:
          - SAFE_HAVEN_GATES: max_cv, min_hit_rate, min_edge, min_tp per stat
          - FRONT_LINES_GATES: max_cv, min_hit_rate, min_edge, min_tp per stat
          - WAR_ZONE_GATES: min_cv, min_ceiling_rate, min_edge per stat
        
        DK odds thresholds determine which gate set is tested:
          - dk_odds <= -240  → test Safe Haven gates
          - dk_odds >= +150  → test War Zone gates
          - else / None      → test Front Lines gates
        """
        from services.mlb_tier_sorter import (
            DK_SAFE_HAVEN_MAX, DK_WAR_ZONE_MIN,
        )

        sorter = self._sorter

        prev_keys = {}
        if previous_tiers:
            for tier_name, picks in previous_tiers.items():
                prev_keys[tier_name] = {
                    f"{p.get('player_name', '')}|{p.get('stat_type', '')}|{p.get('line', '')}" for p in picks
                }

        safe_haven, front_lines, war_zone = [], [], []

        for prop in scored_props:
            dk_odds = prop.get('dk_odds')
            cv = prop.get('cv')
            hit_rate = prop.get('hit_rate') or prop.get('h20_rate')
            edge_pct = prop.get('edge_pct') or 0
            tp = prop.get('true_probability') or 50
            ceiling_rate = prop.get('ceiling_rate')

            # HARD GUARD: dk_odds must come from a real DK offering.
            bookmakers_available = prop.get('bookmakers_available') or []
            if dk_odds is not None and 'draftkings' not in bookmakers_available:
                dk_odds = None
                prop['dk_odds'] = None
                prop['dk_odds_disqualified'] = True

            # TIER 1: Safe Haven — dk_odds <= -240, then stat-specific gates
            if dk_odds is not None and dk_odds <= DK_SAFE_HAVEN_MAX:
                passed, reason, gate_results = sorter.check_safe_haven_gates(
                    prop, cv, hit_rate, edge_pct, tp
                )
                prop['safe_haven_gate_results'] = gate_results
                prop['safe_haven_reason'] = reason
                if passed:
                    prop['ferrari_tier'] = 'safe_haven'
                    prop['tier_label'] = 'Safe Haven'
                    safe_haven.append(prop)
                    continue

            # TIER 3: War Zone — dk_odds >= +150, then stat-specific gates
            if dk_odds is not None and dk_odds >= DK_WAR_ZONE_MIN:
                passed, reason, gate_results = sorter.check_war_zone_gates(
                    prop, cv, ceiling_rate, edge_pct
                )
                prop['war_zone_gate_results'] = gate_results
                prop['war_zone_reason'] = reason
                if passed:
                    prop['ferrari_tier'] = 'war_zone'
                    prop['tier_label'] = 'War Zone'
                    war_zone.append(prop)
                    continue

            # TIER 2: Front Lines — everything else, then stat-specific gates
            if dk_odds is None or (dk_odds > DK_SAFE_HAVEN_MAX and dk_odds < DK_WAR_ZONE_MIN):
                passed, reason, gate_results = sorter.check_front_lines_gates(
                    prop, cv, hit_rate, edge_pct, tp
                )
                prop['front_lines_gate_results'] = gate_results
                prop['front_lines_reason'] = reason
                if passed:
                    prop['ferrari_tier'] = 'front_lines'
                    prop['tier_label'] = 'Front Lines'
                    front_lines.append(prop)
                    continue

        # Apply retention + capped set logic per tier
        safe_haven = self._apply_retention_cap(safe_haven, prev_keys.get('safe_haven', set()), 'board_score')
        front_lines = self._apply_retention_cap(front_lines, prev_keys.get('front_lines', set()), 'edge_pct')
        war_zone = self._apply_retention_cap(war_zone, prev_keys.get('war_zone', set()), 'ceiling_rate')

        logger.info(f"[MLB_ADAPTER] Tier selection: SH={len(safe_haven)} FL={len(front_lines)} WZ={len(war_zone)}")
        return {"safe_haven": safe_haven, "front_lines": front_lines, "war_zone": war_zone}

    def _apply_retention_cap(self, candidates: List[Dict], prev_keys: set, sort_key: str) -> List[Dict]:
        """Qualified capped set: keep all if underfilled, displace only at capacity."""
        seen = set()
        unique = []
        for p in candidates:
            key = f"{p.get('player_name', '')}|{p.get('stat_type', '')}|{p.get('line', '')}"
            if key not in seen:
                seen.add(key)
                unique.append(p)

        if len(unique) <= self.TIER_CAPACITY:
            unique.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            return unique
        else:
            unique.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            return unique[:self.TIER_CAPACITY]

    async def enrich_intel(self, tiers: Dict[str, List[Dict]], db) -> Dict[str, List[Dict]]:
        """Pre-publish enrichment for MLB: overlay cache, averages, tempo, intel_suite.
        Gemini batch enrichment is handled by the shared pipeline Phase 7."""
        from routes.ferrari_tiers import (
            overlay_enrichment_cache,
            enrich_mlb_prop_with_averages,
            enrich_mlb_prop_with_tempo,
            enrich_mlb_intel_suite,
        )

        for tier_name, picks in tiers.items():
            picks = overlay_enrichment_cache(picks, "mlb")
            tiers[tier_name] = picks

            for pick in picks:
                try:
                    enrich_mlb_prop_with_averages(pick)
                    enrich_mlb_prop_with_tempo(pick)
                except Exception:
                    pass
                enrich_mlb_intel_suite(pick)

        return tiers
