"""
NBA Sport Adapter
==================
Delegates to existing ferrari_tier_service + oracle_apex_service scoring.
Wraps all NBA-specific logic: BDL stats, MLR/VK model, blowout risk,
vacuum/momentum, referee whistle data.

This adapter plugs into UnifiedPipeline without changing any scoring math.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services.unified_pipeline import SportAdapter

logger = logging.getLogger(__name__)


class NBAAdapter(SportAdapter):
    """NBA-specific pipeline adapter."""

    def __init__(self, ferrari_service=None, oracle_service=None):
        self._ferrari = ferrari_service
        self._oracle = oracle_service

    @property
    def sport(self) -> str:
        return "nba"

    @property
    def tier_collections(self) -> Dict[str, str]:
        return {
            "safe_haven": "elite_safe_haven",
            "front_lines": "elite_front_lines",
            "war_zone": "elite_war_zone",
        }

    async def load_board(self, db) -> List[Dict]:
        """
        Load NBA props from ferrari_scored (populated by Phase 1-6).
        
        NOTE: The raw dg_cached_board is processed by ferrari_tier_service
        during Phases 1-6 of nba_master_sync, which writes scored output
        to ferrari_scored. This adapter reads from that output.
        """
        cursor = db["ferrari_scored"].find({}, {"_id": 0})
        props = await cursor.to_list(length=None)
        logger.info(f"[NBA_ADAPTER] Loaded {len(props)} props from ferrari_scored")
        return props

    async def enrich_and_score(self, props: List[Dict], db) -> List[Dict]:
        """
        Phase 2+3: Run safety filters + MLR model on ferrari_scored props.
        
        NOTE: The Ferrari scoring pipeline (BDL stats, V7, context layers) runs
        separately in nba_master_sync Phases 1-6 BEFORE this adapter is invoked.
        Props passed in are already from ferrari_scored (via load_board).
        """
        if self._oracle is None:
            from services.oracle_apex_service import get_oracle_apex_service
            self._oracle = get_oracle_apex_service(db)

        if not props:
            logger.warning("[NBA_ADAPTER] No props from ferrari_scored — Phase 1-6 may have failed")
            return []

        # Run through oracle safety filters + MLR
        qualified = await self._run_safety_filters_and_mlr(props)

        logger.info(f"[NBA_ADAPTER] {len(qualified)} props passed safety filters + MLR")
        return qualified

    async def _run_safety_filters_and_mlr(self, all_picks: List[Dict]) -> List[Dict]:
        """
        Run the oracle apex safety filters and MLR model.
        Returns qualified props with validation metadata.
        Extracted from oracle_apex_service.build_elite_top_10_tiers.
        """
        # Delegate directly to oracle service — it already builds qualified pool
        # We intercept the qualified_pool before tier selection
        oracle = self._oracle

        import numpy as np
        from services.anchor_classification_service import get_tier_from_odds
        from services.oracle_apex_service import (
            calculate_nba_master_probability,
            get_nba_pp_required_win_rate,
            calculate_vk_model,
            MIN_MINUTES,
            MARKET_FIRST_REQUIRED,
        )

        qualified_pool = []
        gate_stats = {
            'total_input': len(all_picks),
            'fail_blowout': 0, 'fail_hit_rate': 0, 'fail_cv': 0,
            'fail_actuary_gate': 0, 'fail_minutes': 0,
            'fail_market_first': 0, 'fail_mlr_model': 0, 'fail_vk_model': 0,
            'qualified_pool': 0,
        }

        for prop in all_picks:
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or prop.get("stat_type_extracted") or "").upper()
            if not stat_type:
                market = prop.get("market", "")
                market_to_stat = {
                    "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
                    "player_threes": "3PM", "player_steals": "STL", "player_blocks": "BLK",
                    "player_turnovers": "TO", "player_points_rebounds_assists": "PRA",
                    "player_points_rebounds": "PR", "player_points_assists": "PA",
                    "player_rebounds_assists": "RA"
                }
                stat_type = market_to_stat.get(market, "")

            line = prop.get("line") or 0

            # DK odds
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                sharp_market = prop.get("sharp_market") or {}
                dk_odds = (
                    sharp_market.get("draftkings_price") or
                    prop.get("draftkings_price") or
                    sharp_market.get("sort_price") or
                    prop.get("sort_price") or
                    prop.get("price")
                )

            # Classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            if not is_goblin and not is_demon and dk_odds is not None:
                odds_tier = get_tier_from_odds(dk_odds)
                is_goblin = odds_tier == "SAFE_HAVEN"
                is_demon = odds_tier == "WAR_ZONE"
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")

            # Market gate
            if MARKET_FIRST_REQUIRED and (dk_odds is None or dk_odds == 0):
                gate_stats['fail_market_first'] += 1
                continue

            # Blowout gate
            blowout_risk = prop.get("blowout_risk") or (prop.get("intel_suite") or {}).get("blowout_risk", {}).get("risk_level", "UNKNOWN")
            if blowout_risk == "HIGH":
                gate_stats['fail_blowout'] += 1
                continue

            # Minutes gate
            avg_mins = prop.get("avg_mins") or 0
            if avg_mins > 0 and avg_mins < MIN_MINUTES:
                gate_stats['fail_minutes'] += 1
                continue

            # Hit rate extraction
            l10_rate = prop.get("l10_rate") or prop.get("h10_rate") or 0
            l5_rate = prop.get("l5_rate") or prop.get("h5_rate") or 0
            if l10_rate == 0 and l5_rate == 0:
                hit_rates = prop.get("hit_rates") or {}
                if hit_rates:
                    l10_data = hit_rates.get("l10") or {}
                    l5_data = hit_rates.get("l5") or {}
                    l10_raw = l10_data.get("hit_rate", 0) if isinstance(l10_data, dict) else 0
                    l5_raw = l5_data.get("hit_rate", 0) if isinstance(l5_data, dict) else 0
                    l10_rate = l10_raw * 100 if 0 < l10_raw <= 1 else l10_raw
                    l5_rate = l5_raw * 100 if 0 < l5_raw <= 1 else l5_raw

            true_hit_rate = l10_rate if l10_rate > 0 else l5_rate
            if true_hit_rate == 0:
                gate_stats['fail_hit_rate'] += 1
                continue
            if true_hit_rate < 40.0:
                gate_stats['fail_hit_rate'] += 1
                continue

            # CV — use shared volatility profile
            cv = prop.get("cv")
            if cv is None or cv == 0:
                l10_std = prop.get("l10_std_dev") or 0
                l10_avg = prop.get("l10_avg") or prop.get("l10_mean") or 1
                cv = l10_std / l10_avg if l10_avg > 0 and l10_std > 0 else 0.5
            from services.volatility_profile import get_volatility_profile
            vol = get_volatility_profile(cv, prop_type, line)
            cv = vol.cv_raw
            if vol.label == "extreme":
                gate_stats['fail_cv'] += 1
                continue

            # True probability + edge
            ferrari_true_prob = prop.get("true_probability") or 0
            if ferrari_true_prob > 0:
                propvision_true_prob = ferrari_true_prob
                market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100 if dk_odds and dk_odds < 0 else 50.0
            else:
                prob_data = calculate_nba_master_probability(dk_odds, true_hit_rate, prop_type)
                market_prob = prob_data['market_prob']
                propvision_true_prob = prob_data['propvision_true_prob']

            casino_req_rate = get_nba_pp_required_win_rate(dk_odds, prop_type)
            true_edge = propvision_true_prob - casino_req_rate
            ferrari_pp_edge = prop.get("pp_edge") or 0
            if ferrari_pp_edge > true_edge:
                true_edge = ferrari_pp_edge

            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                continue

            gate_stats['qualified_pool'] += 1

            # Board scores
            sh_board_score = (true_edge * 3.0) - (cv * 15)
            fl_board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)
            wz_board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)

            qualified_prop = {
                'player_name': player_name,
                'player_id': prop.get('player_id'),
                'team': prop.get('team'),
                'opponent': prop.get('opponent') or prop.get('opponent_abbr'),
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                'position': prop.get('position'),
                'nba_id': prop.get('nba_id'),
                'stat_type': stat_type,
                'line': line,
                'dk_odds': dk_odds,
                'price': prop.get('price'),
                'direction': prop.get('direction', 'Over'),
                'market': prop.get('market'),
                'prop_type': prop_type,
                'is_goblin': is_goblin,
                'is_demon': is_demon,
                'is_standard': not is_goblin and not is_demon,
                'l10_rate': round(l10_rate, 1),
                'l5_rate': round(l5_rate, 1),
                'h10_rate': prop.get('h10_rate') or round(l10_rate, 1),
                'h5_rate': prop.get('h5_rate') or round(l5_rate, 1),
                'true_hit_rate': round(true_hit_rate, 1),
                'cv': round(cv, 3),
                'l10_std_dev': prop.get('l10_std_dev'),
                'l10_avg': prop.get('l10_avg'),
                'avg_mins': round(avg_mins, 1) if avg_mins else None,
                'market_prob': round(market_prob, 1),
                'propvision_true_prob': round(propvision_true_prob, 1),
                'true_probability': round(propvision_true_prob, 1),
                'casino_req_rate': round(casino_req_rate, 1),
                'true_edge': round(true_edge, 1),
                'pp_edge': prop.get('pp_edge') or round(true_edge, 1),
                'sh_board_score': round(sh_board_score, 1),
                'fl_board_score': round(fl_board_score, 1),
                'wz_board_score': round(wz_board_score, 1),
                'board_score': round(prop.get('board_score') or fl_board_score, 1),
                'ferrari_power_score': prop.get('ferrari_power_score'),
                'blowout_risk': blowout_risk,
                'intel_suite': prop.get('intel_suite') or {},
                'active_badges': prop.get('active_badges') or [],
                'momentum_data': prop.get('momentum_data'),
                'vacuum_data': prop.get('vacuum_data'),
                'whistle_data': prop.get('whistle_data') or (prop.get('intel_suite') or {}).get('whistle_data'),
                'whistle_class': prop.get('whistle_class'),
                'whistle_modifier': prop.get('whistle_modifier'),
                'momentum_modifier': prop.get('momentum_modifier'),
                'vacuum_modifier': prop.get('vacuum_modifier'),
                'v7_components': prop.get('v7_components') or prop.get('components'),
                'v7_confidence': prop.get('v7_confidence'),
                'season_avg': prop.get('season_avg'),
                'l5_avg': prop.get('l5_avg'),
                'l10_median': prop.get('l10_median'),
                'vk_predicted': prop.get('vk_predicted'),
                'vk_edge': prop.get('vk_edge'),
                'vk_prob_over': prop.get('vk_prob_over'),
                'is_vision_enriched': prop.get('is_vision_enriched'),
                'vision_intel': prop.get('vision_intel'),
                'draftkings_price': prop.get('draftkings_price'),
                'fanduel_price': prop.get('fanduel_price'),
                'sort_price': prop.get('sort_price'),
                'hook_risk': prop.get('hook_risk'),
                'trap_risk': prop.get('trap_risk'),
                'dk_tier': prop.get('dk_tier'),
                'tier_label': prop.get('tier_label'),
                'synced_at': datetime.now(timezone.utc).isoformat(),
                'validation': {
                    'has_market_data': dk_odds is not None and dk_odds != 0,
                    'has_hit_rates': true_hit_rate > 0,
                    'has_context': bool(prop.get('intel_suite')) or bool(prop.get('blowout_risk')),
                    'has_mlr': False,
                    'has_gemini': bool(prop.get('vision_intel') or prop.get('is_vision_enriched')),
                    'is_fully_validated': False,
                },
            }

            # MLR model
            mlr_success = False
            if oracle.vegas_killer_model:
                try:
                    opponent = prop.get('opponent') or prop.get('away_team') or prop.get('home_team', '')
                    vk_raw = oracle.vegas_killer_model.predict(player_name, stat_type, line=line, opponent_team=opponent)
                    if vk_raw and not vk_raw.get('error'):
                        mlr_pred = vk_raw.get('predicted')
                        mlr_std = (vk_raw.get('full_features') or {}).get('baseline', {}).get('std_dev_l10')
                        if mlr_pred is not None and not (isinstance(mlr_pred, float) and np.isnan(mlr_pred)):
                            mlr_success = True
                            qualified_prop['mlr_matchup'] = (vk_raw.get('full_features') or {}).get('matchup', {})
                            qualified_prop['mlr_friction'] = (vk_raw.get('full_features') or {}).get('friction', {})
                            qualified_prop['mlr_features_used'] = True
                            qualified_prop['vk_predicted'] = round(mlr_pred, 2)
                            qualified_prop['mlr_raw_prediction'] = mlr_pred

                            vk_result = calculate_vk_model(
                                predicted_value=mlr_pred, line=line, dk_odds=dk_odds,
                                season_avg=prop.get('season_avg') or prop.get('l10_avg'),
                                require_market=True, std_dev=mlr_std,
                                player_name=player_name, stat_type=stat_type, sport="NBA"
                            )
                            if vk_result.is_valid:
                                qualified_prop['vk_prob_over'] = vk_result.vk_prob_over
                                qualified_prop['vk_prob_under'] = vk_result.vk_prob_under
                                qualified_prop['vk_verdict'] = vk_result.vk_verdict
                                qualified_prop['vk_edge'] = vk_result.vk_edge
                                qualified_prop['vk_confidence'] = vk_result.confidence_score
                            else:
                                gate_stats['fail_vk_model'] = gate_stats.get('fail_vk_model', 0) + 1
                                continue
                except Exception as e:
                    logger.warning(f"[NBA_ADAPTER] MLR fail {player_name} {stat_type}: {e}")

            if not mlr_success:
                gate_stats['fail_mlr_model'] += 1
                qualified_prop['validation']['has_mlr'] = False
                continue

            qualified_prop['validation']['has_mlr'] = True
            qualified_prop['validation']['is_fully_validated'] = all([
                qualified_prop['validation']['has_market_data'],
                qualified_prop['validation']['has_hit_rates'],
                qualified_prop['validation']['has_mlr'],
            ])
            qualified_pool.append(qualified_prop)

        # Log gate stats
        logger.info("[NBA_ADAPTER] Safety Filter Results:")
        for k, v in gate_stats.items():
            logger.info(f"  {k}: {v}")

        return qualified_pool

    TIER_CAPACITY = 10

    def select_tiers(self, scored_props: List[Dict], previous_tiers: Optional[Dict[str, List[Dict]]] = None) -> Dict[str, List[Dict]]:
        """NBA tier selection with retention: qualified capped set.

        Rules:
          1. Props from previous board that still pass gates → RETAINED first
          2. New qualified props fill remaining capacity, sorted by score
          3. Displacement only when retained + new > TIER_CAPACITY
          4. Score used for ordering within the tier and for displacement tie-breaking
        """
        prev_keys = {}  # tier_name -> set of "player|stat" keys on previous board
        if previous_tiers:
            for tier_name, picks in previous_tiers.items():
                prev_keys[tier_name] = {
                    f"{p.get('player_name', '')}|{p.get('stat_type', '')}" for p in picks
                }

        # WAR ZONE claims first
        wz_cands = [
            p for p in scored_props
            if p['prop_type'] in ('DEMON', 'STANDARD') and (p.get('dk_odds') or 0) > 100
            and (p.get('true_edge') or 0) >= 8.0
        ]
        wz_picks = self._select_with_retention(
            wz_cands, prev_keys.get('war_zone', set()),
            sort_key='true_edge', tier_name='war_zone', tier_label='War Zone',
            score_field='wz_board_score',
        )

        claimed = {f"{p['player_name']}|{p['stat_type']}" for p in wz_picks}
        remaining = [p for p in scored_props if f"{p['player_name']}|{p['stat_type']}" not in claimed]

        # SAFE HAVEN claims second
        sh_cands = [
            p for p in remaining
            if p['prop_type'] == 'GOBLIN'
            and (p.get('true_hit_rate') or 0) >= 60.0
            and (p.get('h5_rate') or 0) >= 70.0
            and (p.get('cv') or 1) <= 0.35
            and (p.get('vk_prob_over') or 0) >= 70.0
        ]
        sh_picks = self._select_with_retention(
            sh_cands, prev_keys.get('safe_haven', set()),
            sort_key='vk_prob_over', tier_name='safe_haven', tier_label='Safe Haven',
            score_field='vk_prob_over',
        )

        claimed.update(f"{p['player_name']}|{p['stat_type']}" for p in sh_picks)
        remaining = [p for p in remaining if f"{p['player_name']}|{p['stat_type']}" not in claimed]

        # FRONT LINES claims last
        fl_cands = [
            p for p in remaining
            if (p.get('true_hit_rate') or 0) >= 50.0 and (p.get('cv') or 1) <= 0.50
        ]
        fl_picks = self._select_with_retention(
            fl_cands, prev_keys.get('front_lines', set()),
            sort_key='vk_edge', tier_name='front_lines', tier_label='Front Lines',
            score_field='vk_edge',
        )

        logger.info(f"[NBA_ADAPTER] Tier selection: SH={len(sh_picks)} FL={len(fl_picks)} WZ={len(wz_picks)}")
        return {"safe_haven": sh_picks, "front_lines": fl_picks, "war_zone": wz_picks}

    def _select_with_retention(
        self,
        candidates: List[Dict],
        prev_keys: set,
        sort_key: str,
        tier_name: str,
        tier_label: str,
        score_field: str,
    ) -> List[Dict]:
        """
        Qualified Capped Set selection with retention.

        1. Separate candidates into retained (were on previous board) and new
        2. If total qualified <= capacity, keep ALL
        3. If total qualified > capacity, sort by score, keep top capacity
           (retained picks don't get preferential treatment at capacity —
            pure score ranking decides displacement)
        4. Deduplicate by player|stat
        """
        # Deduplicate candidates by player|stat
        seen = set()
        unique = []
        for p in candidates:
            key = f"{p['player_name']}|{p['stat_type']}"
            if key not in seen:
                seen.add(key)
                unique.append(p)

        # Tag retained vs new
        retained = [p for p in unique if f"{p['player_name']}|{p['stat_type']}" in prev_keys]
        new = [p for p in unique if f"{p['player_name']}|{p['stat_type']}" not in prev_keys]

        if len(unique) <= self.TIER_CAPACITY:
            # Underfilled: keep ALL qualified picks, sort for display order
            picks = unique
            picks.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
        else:
            # Overfilled: pure score ranking decides who stays
            unique.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            picks = unique[:self.TIER_CAPACITY]

        # Tag tier metadata
        for p in picks:
            p['tier'] = tier_name
            p['tier_label'] = tier_label
            p['board_score'] = p.get(score_field, 0)

        if retained:
            logger.debug(f"[NBA_ADAPTER] {tier_name}: {len(retained)} retained, {len(new)} new, {len(picks)} final")

        return picks

    async def enrich_intel(self, tiers: Dict[str, List[Dict]], db) -> Dict[str, List[Dict]]:
        """Non-blocking Gemini enrichment for NBA. Marks has_gemini on each prop."""
        # For now, Gemini runs in the background rolling cache.
        # This phase just ensures the validation flag is accurate.
        for tier_picks in tiers.values():
            for p in tier_picks:
                has_gemini = bool(p.get('vision_intel') or p.get('is_vision_enriched'))
                if 'validation' in p:
                    p['validation']['has_gemini'] = has_gemini
        return tiers
