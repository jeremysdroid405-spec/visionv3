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
        
        Delegates to MLBTierSorter for:
        - Hit rate calculation from mlb_master_hub_2026
        - CV calculation
        - DK odds extraction
        - True probability calculation
        - VK projection lookup
        
        Returns scored props with validation metadata.
        """
        sorter = await self._get_sorter(db)

        scored = []
        stats = {'total': len(props), 'no_odds': 0, 'scored': 0}

        for prop in props:
            player_name = prop.get("player_name", "?")
            stat_type = prop.get("stat_type", "?")
            line = prop.get("line") or 0

            # DK odds
            all_odds = prop.get("all_odds") or {}
            dk_odds = all_odds.get("draftkings") or prop.get("dk_odds")
            if dk_odds is None:
                stats['no_odds'] += 1

            # Stats from hub
            cv = sorter._calculate_cv(player_name, stat_type)
            hit_rate, avg = sorter._calculate_hit_rate(player_name, stat_type, line, 20)
            h5_rate, h5_avg = sorter._calculate_hit_rate(player_name, stat_type, line, 5)
            h10_rate, h10_avg = sorter._calculate_hit_rate(player_name, stat_type, line, 10)

            # True probability from odds
            tp = sorter._calculate_tp_odds(dk_odds) if dk_odds else 50.0

            # VK/Lasso projection
            vk = sorter._get_vk_projection(player_name, stat_type, line)
            vk_edge = vk.get('edge_pct') or prop.get('edge_pct')
            if vk_edge is None and hit_rate is not None:
                vk_edge = round(hit_rate - tp, 1)

            edge_pct = vk_edge or 0
            board_score = tp + edge_pct + ((hit_rate or 0) / 10)

            scored_prop = {
                **prop,
                'player_name': player_name,
                'stat_type': stat_type,
                'line': line,
                'dk_odds': dk_odds,
                'all_odds': all_odds,
                'cv': cv,
                'hit_rate': hit_rate,
                'h5_rate': h5_rate or 0,
                'h10_rate': h10_rate or 0,
                'h20_rate': hit_rate or 0,
                'l10_avg': h10_avg,
                'l5_avg': h5_avg,
                'season_avg': avg,
                'true_probability': tp,
                'edge_pct': edge_pct,
                'vk_predicted': vk.get('projection'),
                'vk_edge': vk_edge,
                'board_score': round(board_score, 2),
                'synced_at': datetime.now(timezone.utc).isoformat(),
                'validation': {
                    'has_market_data': dk_odds is not None and dk_odds != 0,
                    'has_hit_rates': (hit_rate or 0) > 0,
                    'has_context': bool(prop.get('matchup_analysis')) or bool(all_odds),
                    'has_mlr': bool(vk.get('projection')),
                    'has_gemini': bool(prop.get('vision_intel')),
                    'is_fully_validated': False,
                },
            }

            # Compute full validation
            v = scored_prop['validation']
            v['is_fully_validated'] = all([
                v['has_market_data'],
                v['has_hit_rates'],
            ])

            scored.append(scored_prop)
            stats['scored'] += 1

        logger.info(f"[MLB_ADAPTER] Scored {stats['scored']}/{stats['total']} props ({stats['no_odds']} missing odds)")
        return scored

    TIER_CAPACITY = 10

    def select_tiers(self, scored_props: List[Dict], previous_tiers: Optional[Dict[str, List[Dict]]] = None) -> Dict[str, List[Dict]]:
        """MLB tier selection with retention: qualified capped set."""
        from services.mlb_tier_sorter import (
            DK_SAFE_HAVEN_MAX, DK_WAR_ZONE_MIN, SAFE_HAVEN_GATES,
        )

        prev_keys = {}
        if previous_tiers:
            for tier_name, picks in previous_tiers.items():
                prev_keys[tier_name] = {
                    f"{p.get('player_name', '')}|{p.get('stat_type', '')}" for p in picks
                }

        safe_haven, front_lines, war_zone = [], [], []

        for prop in scored_props:
            dk_odds = prop.get('dk_odds')
            cv = prop.get('cv')
            hit_rate = prop.get('hit_rate') or prop.get('h20_rate')
            edge_pct = prop.get('edge_pct') or 0
            tp = prop.get('true_probability') or 50
            line = prop.get('line') or 0

            if dk_odds is not None and dk_odds <= DK_SAFE_HAVEN_MAX:
                if self._check_safe_haven_gates(prop, cv, hit_rate, edge_pct, tp, line):
                    prop['ferrari_tier'] = 'safe_haven'
                    prop['tier_label'] = 'Safe Haven'
                    safe_haven.append(prop)
                    continue

            if dk_odds is not None and dk_odds >= DK_WAR_ZONE_MIN:
                ceiling = prop.get('ceiling_rate') or hit_rate or 0
                if edge_pct >= 5 or ceiling >= 30:
                    prop['ferrari_tier'] = 'war_zone'
                    prop['tier_label'] = 'War Zone'
                    war_zone.append(prop)
                    continue

            if dk_odds is None or (dk_odds > DK_SAFE_HAVEN_MAX and dk_odds < DK_WAR_ZONE_MIN):
                if (hit_rate or 0) >= 50 and (cv or 1) <= 0.80:
                    prop['ferrari_tier'] = 'front_lines'
                    prop['tier_label'] = 'Front Lines'
                    front_lines.append(prop)
                    continue

        # Apply retention + capped set logic per tier
        safe_haven = self._apply_retention_cap(safe_haven, prev_keys.get('safe_haven', set()), 'board_score')
        front_lines = self._apply_retention_cap(front_lines, prev_keys.get('front_lines', set()), 'edge_pct')
        war_zone = self._apply_retention_cap(war_zone, prev_keys.get('war_zone', set()), 'edge_pct')

        logger.info(f"[MLB_ADAPTER] Tier selection: SH={len(safe_haven)} FL={len(front_lines)} WZ={len(war_zone)}")
        return {"safe_haven": safe_haven, "front_lines": front_lines, "war_zone": war_zone}

    def _apply_retention_cap(self, candidates: List[Dict], prev_keys: set, sort_key: str) -> List[Dict]:
        """Qualified capped set: keep all if underfilled, displace only at capacity."""
        # Deduplicate
        seen = set()
        unique = []
        for p in candidates:
            key = f"{p.get('player_name', '')}|{p.get('stat_type', '')}"
            if key not in seen:
                seen.add(key)
                unique.append(p)

        if len(unique) <= self.TIER_CAPACITY:
            unique.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            return unique
        else:
            unique.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            return unique[:self.TIER_CAPACITY]

    def _check_safe_haven_gates(self, prop, cv, hit_rate, edge_pct, tp, line) -> bool:
        """Check MLB Safe Haven gates using shared volatility profile."""
        from services.volatility_profile import get_volatility_profile
        from services.mlb_tier_sorter import SAFE_HAVEN_GATES

        stat_type = prop.get('stat_type', '')
        vol = get_volatility_profile(cv, stat_type, line)

        if line < 1.0:
            # Goblin-line override: binary/Bernoulli outcomes
            # Use volatility profile's extreme threshold instead of hardcoded 1.10
            if vol.is_extreme:
                return False
            if hit_rate is not None and hit_rate < 75:
                return False
            if tp < 60:
                return False
            return True

        # Standard gates by stat type
        sorter = self._sorter
        stat_key = sorter._normalize_stat_type(stat_type) if sorter else 'hits'
        gates = SAFE_HAVEN_GATES.get(stat_key, SAFE_HAVEN_GATES.get('hits'))

        # Use volatility profile for CV check instead of raw threshold
        if vol.label in ("extreme", "high"):
            return False
        if hit_rate is not None and hit_rate < gates['min_hit_rate']:
            return False
        if edge_pct < gates['min_edge']:
            return False
        if tp < gates['min_tp']:
            return False
        return True

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
