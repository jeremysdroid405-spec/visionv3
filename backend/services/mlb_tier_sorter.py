"""
MLB Tier Sorter Service
========================
Quantitative gating system for MLB PropVision Ferrari Pipeline.

Implements strict stat-specific thresholds for:
- Tier 1: Safe Haven (The Locks)
- Tier 2: Front Lines (The Value Plays)  
- Tier 3: War Zone (The Moonshots)
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# =============================================================================
# TIER THRESHOLDS - STAT-SPECIFIC GATES
# =============================================================================

SAFE_HAVEN_GATES = {
    "hits": {"max_cv": 0.60, "min_hit_rate": 80, "min_edge": 15, "min_tp": 70},
    "total_bases": {"max_cv": 0.75, "min_hit_rate": 75, "min_edge": 20, "min_tp": 70},
    "hits+runs+rbis": {"max_cv": 0.55, "min_hit_rate": 80, "min_edge": 18, "min_tp": 70},
    "rbis": {"max_cv": 0.55, "min_hit_rate": 80, "min_edge": 18, "min_tp": 70},
    "runs": {"max_cv": 0.55, "min_hit_rate": 80, "min_edge": 18, "min_tp": 70},
    "pitching_outs": {"max_cv": 0.30, "min_hit_rate": 85, "min_edge": 8, "min_tp": 80},
    "pitcher_strikeouts": {"max_cv": 0.45, "min_hit_rate": 75, "min_edge": 12, "min_tp": 75},
    "earned_runs": {"max_cv": 0.40, "min_hit_rate": 75, "min_edge": 10, "min_tp": 75},
}

FRONT_LINES_GATES = {
    "hits": {"max_cv": 0.85, "min_hit_rate": 65, "min_edge": 10, "min_tp": 58},
    "total_bases": {"max_cv": 0.95, "min_hit_rate": 60, "min_edge": 15, "min_tp": 58},
    "hits+runs+rbis": {"max_cv": 0.75, "min_hit_rate": 65, "min_edge": 12, "min_tp": 58},
    "rbis": {"max_cv": 0.75, "min_hit_rate": 65, "min_edge": 12, "min_tp": 58},
    "runs": {"max_cv": 0.75, "min_hit_rate": 65, "min_edge": 12, "min_tp": 58},
    "pitching_outs": {"max_cv": 0.50, "min_hit_rate": 70, "min_edge": 6, "min_tp": 70},
    "pitcher_strikeouts": {"max_cv": 0.60, "min_hit_rate": 65, "min_edge": 10, "min_tp": 65},
    "earned_runs": {"max_cv": 0.55, "min_hit_rate": 65, "min_edge": 8, "min_tp": 65},
}

WAR_ZONE_GATES = {
    "hits": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
    "total_bases": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
    "hits+runs+rbis": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
    "rbis": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
    "runs": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
    "pitcher_strikeouts": {"min_cv": 0.8, "min_ceiling_rate": 30, "min_edge": 25},
}

# DraftKings odds thresholds
DK_SAFE_HAVEN_MAX = -240
DK_FRONT_LINES_MIN = -240
DK_FRONT_LINES_MAX = -145
DK_WAR_ZONE_MIN = 150


class MLBTierSorter:
    """
    MLB Tier Sorter - Quantitative Gating System.
    
    Sorts props from mlb_cached_board into Ferrari tiers using
    strict stat-specific thresholds.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._player_logs_cache: Dict[str, List[Dict]] = {}
        self._vk_projections_cache: Dict[str, Dict] = {}
    
    async def _load_caches(self):
        """Load player logs and VK projections into memory."""
        from datetime import datetime
        
        # Current season (2026)
        current_year = datetime.now().year
        current_season = current_year
        
        # Load player logs from master hub - FILTER TO CURRENT SEASON ONLY
        master_hub = self.db["mlb_master_hub_2026"]
        players = await master_hub.find(
            {"bdl_game_logs": {"$exists": True, "$ne": []}},
            {"_id": 0, "display_name": 1, "bdl_game_logs": 1, "vk_baselines": 1}
        ).to_list(length=None)
        
        for player in players:
            name = player.get("display_name", "").lower().strip()
            if name:
                all_logs = player.get("bdl_game_logs", [])
                
                # CRITICAL: Filter to ONLY current season (2026) games
                # Check both 'season' field and date year
                current_season_logs = []
                for log in all_logs:
                    season = log.get("season")
                    date_str = log.get("date", "")
                    
                    # Include if season matches OR date is in current year
                    if season == current_season:
                        current_season_logs.append(log)
                    elif date_str and str(current_season) in date_str[:4]:
                        current_season_logs.append(log)
                
                self._player_logs_cache[name] = current_season_logs
        
        logger.info(f"[TIER_SORTER] Loaded {len(self._player_logs_cache)} player logs (filtered to {current_season} season)")
        
        # Load VK projections
        vk_collection = self.db["mlb_vk_projections"]
        vk_docs = await vk_collection.find({}, {"_id": 0}).to_list(length=None)
        
        for vk in vk_docs:
            key = f"{vk.get('player_name', '').lower()}|{vk.get('stat_type', '').lower()}|{vk.get('line')}"
            self._vk_projections_cache[key] = vk
        
        logger.info(f"[TIER_SORTER] Loaded {len(self._vk_projections_cache)} VK projections")
    
    def _normalize_stat_type(self, stat_type: str) -> str:
        """Normalize stat type for gate lookup."""
        normalized = stat_type.lower().strip()
        
        # Map PrizePicks variations to canonical names
        mappings = {
            "tb": "total_bases",
            "hrr": "hits+runs+rbis",
            "batter_hits_runs_rbis": "hits+runs+rbis",
            "hits+runs+rbis": "hits+runs+rbis",
            # Pitcher stats
            "pitcher outs": "pitcher_outs",
            "pitcher strikeouts": "pitcher_strikeouts",
            "pitcher_strikeouts": "pitcher_strikeouts",
            "earned runs": "earned_runs",
            "earned runs allowed": "earned_runs",
            "hits allowed": "hits_allowed",
            "walks allowed": "walks_allowed",
            # Batter stats
            "batter walks": "batter_walks",
            "batter strikeouts": "batter_strikeouts",
            "home runs": "home_runs",
            "stolen bases": "stolen_bases",
            "total bases": "total_bases",
        }
        
        return mappings.get(normalized, normalized.replace(" ", "_"))
    
    def _calculate_cv(self, player_name: str, stat_type: str) -> Optional[float]:
        """Calculate Coefficient of Variation for player stat."""
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if len(game_logs) < 5:
            return None
        
        # Get stat field - comprehensive mapping for all PrizePicks stat types
        stat_map = {
            "hits": "hits",
            "total_bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home_runs": "home_runs",
            "stolen_bases": "stolen_bases",
            "singles": "singles",  # Calculated
            "doubles": "doubles",
            "triples": "triples",
            "batter_walks": "walks",
            "walks": "walks",
            "batter_strikeouts": "strikeouts",
            "strikeouts": "strikeouts",
            "hits+runs+rbis": ["hits", "runs", "rbis"],
            "pitcher_strikeouts": "pitcher_strikeouts",
            "pitcher_outs": "innings_pitched",
            "pitching_outs": "innings_pitched",
            "earned_runs": "earned_runs",
            "hits_allowed": "hits_allowed",
            "walks_allowed": "pitcher_walks",
        }
        
        stat_key = self._normalize_stat_type(stat_type)
        field = stat_map.get(stat_key, stat_key)
        
        # Get values from L20 games
        values = []
        for game in game_logs[:20]:
            if isinstance(field, list):
                # Combo stat
                val = sum(game.get(f) or 0 for f in field)
            elif field == "innings_pitched":
                ip = game.get(field)
                val = (ip * 3) if ip else None
            elif field == "singles":
                # Calculate singles = hits - doubles - triples - home_runs
                hits = game.get("hits")
                if hits is not None:
                    doubles = game.get("doubles") or 0
                    triples = game.get("triples") or 0
                    hr = game.get("home_runs") or 0
                    val = max(0, hits - doubles - triples - hr)
                else:
                    val = None
            else:
                val = game.get(field)
            
            if val is not None:
                values.append(val)
        
        if len(values) < 5:
            return None
        
        import statistics
        mean = statistics.mean(values)
        if mean == 0:
            return None
        
        std_dev = statistics.stdev(values) if len(values) > 1 else 0
        cv = std_dev / mean
        
        return round(cv, 3)
    
    def _calculate_hit_rate(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float, 
        num_games: int = 20
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Calculate hit rate and average for player stat.
        
        Returns:
            Tuple of (hit_rate_percentage, average)
        """
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if not game_logs:
            return None, None
        
        stat_map = {
            "hits": "hits",
            "total_bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home_runs": "home_runs",
            "stolen_bases": "stolen_bases",
            "singles": "singles",
            "doubles": "doubles",
            "triples": "triples",
            "batter_walks": "walks",
            "walks": "walks",
            "batter_strikeouts": "strikeouts",
            "strikeouts": "strikeouts",
            "hits+runs+rbis": ["hits", "runs", "rbis"],
            "pitcher_strikeouts": "pitcher_strikeouts",
            "pitcher_outs": "innings_pitched",
            "pitching_outs": "innings_pitched",
            "earned_runs": "earned_runs",
            "hits_allowed": "hits_allowed",
            "walks_allowed": "pitcher_walks",
        }
        
        stat_key = self._normalize_stat_type(stat_type)
        field = stat_map.get(stat_key, stat_key)
        
        # Sort by date descending
        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get("date", "") or "",
            reverse=True
        )[:num_games]
        
        values = []
        hits = 0
        
        for game in sorted_logs:
            if isinstance(field, list):
                val = sum(game.get(f) or 0 for f in field)
            elif field == "innings_pitched":
                ip = game.get(field)
                val = (ip * 3) if ip else None
            elif field == "singles":
                # Calculate singles = hits - doubles - triples - home_runs
                h = game.get("hits")
                if h is not None:
                    d = game.get("doubles") or 0
                    t = game.get("triples") or 0
                    hr = game.get("home_runs") or 0
                    val = max(0, h - d - t - hr)
                else:
                    val = None
            else:
                val = game.get(field)
            
            if val is None:
                continue
            
            values.append(val)
            if val >= line:
                hits += 1
        
        if not values:
            return None, None
        
        hit_rate = round((hits / len(values)) * 100, 1)
        avg = round(sum(values) / len(values), 2)
        
        return hit_rate, avg
    
    def _calculate_ceiling_hit_rate(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float
    ) -> Optional[float]:
        """Calculate ceiling hit rate (times player exceeded 2x line)."""
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if len(game_logs) < 10:
            return None
        
        stat_key = self._normalize_stat_type(stat_type)
        stat_map = {
            "hits": "hits",
            "total_bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home_runs": "home_runs",
            "stolen_bases": "stolen_bases",
            "singles": "singles",
            "doubles": "doubles",
            "triples": "triples",
            "batter_walks": "walks",
            "walks": "walks",
            "batter_strikeouts": "strikeouts",
            "strikeouts": "strikeouts",
            "hits+runs+rbis": ["hits", "runs", "rbis"],
            "pitcher_strikeouts": "pitcher_strikeouts",
            "pitcher_outs": "innings_pitched",
            "earned_runs": "earned_runs",
            "hits_allowed": "hits_allowed",
            "walks_allowed": "pitcher_walks",
        }
        
        field = stat_map.get(stat_key, stat_key)
        ceiling_threshold = line * 1.5  # 150% of line = ceiling
        
        ceiling_hits = 0
        valid_games = 0
        
        for game in game_logs[:20]:
            if isinstance(field, list):
                val = sum(game.get(f) or 0 for f in field)
            elif field == "singles":
                h = game.get("hits")
                if h is not None:
                    val = max(0, h - (game.get("doubles") or 0) - (game.get("triples") or 0) - (game.get("home_runs") or 0))
                else:
                    val = None
            elif field == "innings_pitched":
                ip = game.get(field)
                val = (ip * 3) if ip else None
            else:
                val = game.get(field)
            
            if val is None:
                continue
            
            valid_games += 1
            if val >= ceiling_threshold:
                ceiling_hits += 1
        
        if valid_games < 10:
            return None
        
        return round((ceiling_hits / valid_games) * 100, 1)
    
    def _get_vk_projection(self, player_name: str, stat_type: str, line: float) -> Dict:
        """Get VK projection for prop."""
        key = f"{player_name.lower()}|{stat_type.lower()}|{line}"
        return self._vk_projections_cache.get(key, {})
    
    def _calculate_tp_odds(self, sharp_odds: Optional[float]) -> float:
        """Calculate true probability from sharp odds."""
        if not sharp_odds:
            return 50.0
        
        if sharp_odds < 0:
            tp = abs(sharp_odds) / (abs(sharp_odds) + 100) * 100
        else:
            tp = 100 / (sharp_odds + 100) * 100
        
        return round(tp, 1)

    def _get_recent_game_logs(
        self, 
        player_name: str, 
        stat_type: str, 
        num_games: int = 5
    ) -> List[Dict]:
        """Get formatted recent game logs for Oracle context."""
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if not game_logs:
            return []
        
        stat_key = self._normalize_stat_type(stat_type)
        stat_map = {
            "hits": "hits",
            "total_bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home_runs": "home_runs",
            "stolen_bases": "stolen_bases",
            # Singles is calculated
            "singles": "singles",
            "doubles": "doubles",
            "triples": "triples",
            # Walks
            "walks": "walks",
            "batter_walks": "walks",
            # Strikeouts
            "strikeouts": "strikeouts",
            "batter_strikeouts": "strikeouts",
            # Combo
            "hits+runs+rbis": ["hits", "runs", "rbis"],
            # Pitcher
            "pitcher_strikeouts": "pitcher_strikeouts",
            "pitching_outs": "innings_pitched",
            "pitcher_outs": "innings_pitched",
            "earned_runs": "earned_runs",
            "hits_allowed": "hits_allowed",
            "walks_allowed": "pitcher_walks",
        }
        
        field = stat_map.get(stat_key, stat_key)
        
        # Sort by date descending
        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get("date", "") or "",
            reverse=True
        )[:num_games]
        
        formatted = []
        for game in sorted_logs:
            if isinstance(field, list):
                val = sum(game.get(f) or 0 for f in field)
            elif field == "innings_pitched":
                ip = game.get(field)
                val = round(ip * 3) if ip else None
            elif field == "singles":
                # Calculate singles = hits - doubles - triples - home_runs
                hits = game.get("hits")
                if hits is not None:
                    doubles = game.get("doubles") or 0
                    triples = game.get("triples") or 0
                    hr = game.get("home_runs") or 0
                    val = max(0, hits - doubles - triples - hr)
                else:
                    val = None
            else:
                val = game.get(field)
            
            if val is not None:
                formatted.append({
                    "date": game.get("date"),
                    "value": val,
                    "opponent": game.get("opponent_abbr") or game.get("opponent") or game.get("opp_team")
                })
        
        return formatted

    
    def check_safe_haven_gates(
        self,
        prop: Dict,
        cv: Optional[float],
        hit_rate: Optional[float],
        edge_pct: Optional[float],
        tp_odds: float
    ) -> Tuple[bool, str, Dict]:
        """
        Check if prop passes Safe Haven stat-specific gates.
        
        Returns:
            Tuple of (passed, reason, gate_results)
        """
        stat_key = self._normalize_stat_type(prop.get("stat_type", ""))
        gates = SAFE_HAVEN_GATES.get(stat_key, SAFE_HAVEN_GATES.get("hits"))
        
        gate_results = {
            "gate1_cv": {"threshold": gates["max_cv"], "value": cv, "passed": False},
            "gate2_hit_rate": {"threshold": gates["min_hit_rate"], "value": hit_rate, "passed": False},
            "gate3_edge": {"threshold": gates["min_edge"], "value": edge_pct, "passed": False},
            "gate4_tp": {"threshold": gates["min_tp"], "value": tp_odds, "passed": False},
        }
        
        passed_count = 0
        required_count = 0
        
        # Gate 1: CV Check (optional - pass if None)
        if cv is not None:
            required_count += 1
            if cv <= gates["max_cv"]:
                gate_results["gate1_cv"]["passed"] = True
                passed_count += 1
        else:
            gate_results["gate1_cv"]["passed"] = True  # Skip if no data
            gate_results["gate1_cv"]["skipped"] = True
        
        # Gate 2: Hit Rate Check (required)
        required_count += 1
        if hit_rate is not None and hit_rate >= gates["min_hit_rate"]:
            gate_results["gate2_hit_rate"]["passed"] = True
            passed_count += 1
        elif hit_rate is None:
            # No hit rate data - use 60% as minimum proxy
            gate_results["gate2_hit_rate"]["passed"] = True
            gate_results["gate2_hit_rate"]["skipped"] = True
            passed_count += 1
        
        # Gate 3: Edge Check (optional - pass if None)
        if edge_pct is not None:
            required_count += 1
            if edge_pct >= gates["min_edge"]:
                gate_results["gate3_edge"]["passed"] = True
                passed_count += 1
        else:
            gate_results["gate3_edge"]["passed"] = True
            gate_results["gate3_edge"]["skipped"] = True
        
        # Gate 4: TP Check (required)
        required_count += 1
        if tp_odds >= gates["min_tp"]:
            gate_results["gate4_tp"]["passed"] = True
            passed_count += 1
        
        # Must pass all non-skipped gates
        all_passed = all(g["passed"] for g in gate_results.values())
        
        if all_passed:
            reason = f"All gates passed for {stat_key.upper()}"
        else:
            failed = [k for k, v in gate_results.items() if not v["passed"]]
            reason = f"Failed gates: {', '.join(failed)}"
        
        return all_passed, reason, gate_results
    
    def check_front_lines_gates(
        self,
        prop: Dict,
        cv: Optional[float],
        hit_rate: Optional[float],
        edge_pct: Optional[float],
        tp_odds: float
    ) -> Tuple[bool, str, Dict]:
        """Check if prop passes Front Lines stat-specific gates."""
        stat_key = self._normalize_stat_type(prop.get("stat_type", ""))
        gates = FRONT_LINES_GATES.get(stat_key, FRONT_LINES_GATES.get("hits"))
        
        gate_results = {
            "gate1_cv": {"threshold": gates["max_cv"], "value": cv, "passed": False},
            "gate2_hit_rate": {"threshold": gates["min_hit_rate"], "value": hit_rate, "passed": False},
            "gate3_edge": {"threshold": gates["min_edge"], "value": edge_pct, "passed": False},
            "gate4_tp": {"threshold": gates["min_tp"], "value": tp_odds, "passed": False},
        }
        
        # Gate 1: CV Check
        if cv is not None and cv <= gates["max_cv"]:
            gate_results["gate1_cv"]["passed"] = True
        elif cv is None:
            gate_results["gate1_cv"]["passed"] = True
            gate_results["gate1_cv"]["skipped"] = True
        
        # Gate 2: Hit Rate Check
        if hit_rate is not None and hit_rate >= gates["min_hit_rate"]:
            gate_results["gate2_hit_rate"]["passed"] = True
        elif hit_rate is None:
            gate_results["gate2_hit_rate"]["passed"] = True
            gate_results["gate2_hit_rate"]["skipped"] = True
        
        # Gate 3: Edge Check  
        if edge_pct is not None and edge_pct >= gates["min_edge"]:
            gate_results["gate3_edge"]["passed"] = True
        elif edge_pct is None:
            gate_results["gate3_edge"]["passed"] = True
            gate_results["gate3_edge"]["skipped"] = True
        
        # Gate 4: TP Check
        if tp_odds >= gates["min_tp"]:
            gate_results["gate4_tp"]["passed"] = True
        
        all_passed = all(g["passed"] for g in gate_results.values())
        
        if all_passed:
            reason = f"All gates passed for {stat_key.upper()}"
        else:
            failed = [k for k, v in gate_results.items() if not v["passed"]]
            reason = f"Failed gates: {', '.join(failed)}"
        
        return all_passed, reason, gate_results
    
    def check_war_zone_gates(
        self,
        prop: Dict,
        cv: Optional[float],
        ceiling_rate: Optional[float],
        edge_pct: Optional[float]
    ) -> Tuple[bool, str, Dict]:
        """Check if prop passes War Zone gates (volatility required)."""
        stat_key = self._normalize_stat_type(prop.get("stat_type", ""))
        gates = WAR_ZONE_GATES.get(stat_key, WAR_ZONE_GATES.get("hits"))
        
        gate_results = {
            "gate1_cv": {"threshold": f">= {gates['min_cv']}", "value": cv, "passed": False},
            "gate2_ceiling": {"threshold": gates["min_ceiling_rate"], "value": ceiling_rate, "passed": False},
            "gate3_edge": {"threshold": gates["min_edge"], "value": edge_pct, "passed": False},
        }
        
        # War Zone REQUIRES high volatility (CV >= threshold)
        if cv is not None and cv >= gates["min_cv"]:
            gate_results["gate1_cv"]["passed"] = True
        elif cv is None:
            # For war zone, assume high volatility if no data
            gate_results["gate1_cv"]["passed"] = True
            gate_results["gate1_cv"]["skipped"] = True
        
        # Ceiling rate check
        if ceiling_rate is not None and ceiling_rate >= gates["min_ceiling_rate"]:
            gate_results["gate2_ceiling"]["passed"] = True
        elif ceiling_rate is None:
            gate_results["gate2_ceiling"]["passed"] = True
            gate_results["gate2_ceiling"]["skipped"] = True
        
        # Edge check
        if edge_pct is not None and edge_pct >= gates["min_edge"]:
            gate_results["gate3_edge"]["passed"] = True
        elif edge_pct is None:
            gate_results["gate3_edge"]["passed"] = True
            gate_results["gate3_edge"]["skipped"] = True
        
        all_passed = all(g["passed"] for g in gate_results.values())
        
        if all_passed:
            reason = f"Moonshot qualified: High volatility + ceiling upside"
        else:
            failed = [k for k, v in gate_results.items() if not v["passed"]]
            reason = f"Failed gates: {', '.join(failed)}"
        
        return all_passed, reason, gate_results
    
    async def sort_props(self, save_to_db: bool = True) -> Dict[str, Any]:
        """
        Sort all props from cached board into Ferrari tiers.
        
        Returns:
            Dict with sorted tiers and statistics
        """
        logger.info("[TIER_SORTER] ========================================")
        logger.info("[TIER_SORTER] Starting MLB Ferrari Pipeline - Phase 1")
        logger.info("[TIER_SORTER] ========================================")
        
        # Load caches
        await self._load_caches()
        
        # Load props from cached board
        cached_board = self.db["mlb_cached_board"]
        players = await cached_board.find({}, {"_id": 0}).to_list(length=None)
        
        # Flatten props - combine props, goblins, and demons arrays, then deduplicate
        all_props = []
        seen_keys = set()
        for player in players:
            combined = player.get("props", []) + player.get("goblins", []) + player.get("demons", [])
            for prop in combined:
                key = f"{player.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                prop["player_name"] = player.get("player_name")
                prop["team"] = player.get("team")
                prop["position"] = player.get("position")
                all_props.append(prop)
        
        logger.info(f"[TIER_SORTER] Processing {len(all_props)} props")
        
        results = {
            "safe_haven": [],
            "front_lines": [],
            "war_zone": [],
            "discarded": [],
            "stats": {
                "total_processed": len(all_props),
                "safe_haven_qualified": 0,
                "front_lines_qualified": 0,
                "war_zone_qualified": 0,
            }
        }
        
        for prop in all_props:
            player_name = prop.get("player_name", "")
            stat_type = prop.get("stat_type", "")
            line = prop.get("line", 0)
            
            # Get DK odds
            all_odds = prop.get("all_odds", {})
            dk_odds = all_odds.get("draftkings")
            sharp_odds = all_odds.get("pinnacle") or all_odds.get("prizepicks")
            
            # Calculate metrics - prefer cached values, fallback to prop data
            cv = self._calculate_cv(player_name, stat_type)
            if cv is None:
                # Get from prop, normalize if it's a percentage
                prop_cv = prop.get("cv")
                if prop_cv is not None:
                    # If CV > 5, it's likely a percentage - convert to decimal
                    cv = prop_cv / 100 if prop_cv > 5 else prop_cv
            
            hit_rate, avg = self._calculate_hit_rate(player_name, stat_type, line, 20)
            if hit_rate is None:
                # Use existing L10 hit rate from prop
                hit_rate = prop.get("hit_rate_l10") or prop.get("h10_rate")
                avg = prop.get("season_average")
            
            # Calculate L5 and L10 hit rates for frontend display
            h5_rate, l5_avg = self._calculate_hit_rate(player_name, stat_type, line, 5)
            h10_rate, l10_avg = self._calculate_hit_rate(player_name, stat_type, line, 10)
            
            # Fallback to prop data if calculated values are None
            if h5_rate is None:
                h5_rate = prop.get("h5_rate") or prop.get("hit_rate_l5")
                l5_avg = prop.get("l5_avg")
            if h10_rate is None:
                h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10")
                l10_avg = prop.get("l10_avg")
            
            ceiling_rate = self._calculate_ceiling_hit_rate(player_name, stat_type, line)
            
            # Get VK projection - try multiple sources
            vk = self._get_vk_projection(player_name, stat_type, line)
            vk_edge = vk.get("edge_pct") or prop.get("edge_pct") or prop.get("edge") or prop.get("sharp_edge")
            vk_predicted = vk.get("projected_value") or prop.get("vk_predicted") or prop.get("projected_value")
            
            # Calculate TP from DK odds (primary) or sharp odds (fallback)
            # The tier rules are based on DK odds, so use those for TP
            tp_odds = self._calculate_tp_odds(dk_odds or sharp_odds)
            
            # Calculate edge properly: Edge = Hit Rate - Implied Probability
            # If no VK edge available, calculate from hit rate and TP odds
            if vk_edge is not None and vk_edge != 0:
                edge_pct = vk_edge
            elif hit_rate is not None and tp_odds is not None:
                # Edge = (Hit Rate - True Probability) as percentage points
                edge_pct = round(hit_rate - tp_odds, 1)
            else:
                edge_pct = None
            
            # Get recent game logs for Oracle context
            recent_logs = self._get_recent_game_logs(player_name, stat_type, 5)
            
            # Enrich prop with all hit rate data
            prop["cv"] = cv
            prop["h5_rate"] = h5_rate
            prop["h10_rate"] = h10_rate
            prop["h20_rate"] = hit_rate
            prop["l5_avg"] = l5_avg
            prop["l10_avg"] = l10_avg
            prop["l20_avg"] = avg
            prop["ceiling_rate"] = ceiling_rate
            prop["vk_predicted"] = vk_predicted
            prop["edge_pct"] = edge_pct
            prop["tp_odds"] = tp_odds
            prop["game_logs"] = recent_logs  # For Oracle summaries
            
            # DK ODDS-BASED TIER CLASSIFICATION (Primary)
            # Safe Haven: DK <= -240
            # Front Lines: -240 < DK <= -145
            # War Zone: DK > +150
            # No DK odds: try stat gates for Front Lines
            
            # TIER 1: Safe Haven (The Locks)
            if dk_odds is not None and dk_odds <= DK_SAFE_HAVEN_MAX:
                passed, reason, gate_results = self.check_safe_haven_gates(
                    prop, cv, hit_rate, edge_pct, tp_odds
                )
                prop["safe_haven_gate_results"] = gate_results
                prop["safe_haven_reason"] = reason
                
                if passed:
                    prop["ferrari_tier"] = "safe_haven"
                    prop["tier_label"] = "Safe Haven"
                    results["safe_haven"].append(prop)
                    results["stats"]["safe_haven_qualified"] += 1
                    continue
            
            # TIER 3: War Zone (The Moonshots) — check before Front Lines
            if dk_odds is not None and dk_odds >= DK_WAR_ZONE_MIN:
                passed, reason, gate_results = self.check_war_zone_gates(
                    prop, cv, ceiling_rate, edge_pct
                )
                prop["war_zone_gate_results"] = gate_results
                prop["war_zone_reason"] = reason
                
                if passed:
                    prop["ferrari_tier"] = "war_zone"
                    prop["tier_label"] = "War Zone"
                    results["war_zone"].append(prop)
                    results["stats"]["war_zone_qualified"] += 1
                    continue
            
            # TIER 2: Front Lines (The Value Plays) — everything in between
            if dk_odds is None or (dk_odds > DK_SAFE_HAVEN_MAX and dk_odds < DK_WAR_ZONE_MIN):
                passed, reason, gate_results = self.check_front_lines_gates(
                    prop, cv, hit_rate, edge_pct, tp_odds
                )
                prop["front_lines_gate_results"] = gate_results
                prop["front_lines_reason"] = reason
                
                if passed:
                    prop["ferrari_tier"] = "front_lines"
                    prop["tier_label"] = "Front Lines"
                    results["front_lines"].append(prop)
                    results["stats"]["front_lines_qualified"] += 1
                    continue
            
            # Didn't qualify for any tier
            results["discarded"].append(prop)
        
        # Sort tiers
        # Safe Haven: Sort by board score (TP + Edge + Hit Rate)
        for prop in results["safe_haven"]:
            board_score = (prop.get("tp_odds") or 50) + (prop.get("edge_pct") or 0) + ((prop.get("h20_rate") or 0) / 10)
            prop["board_score"] = round(board_score, 1)
        results["safe_haven"].sort(key=lambda x: x.get("board_score", 0), reverse=True)
        
        # Front Lines: Sort by edge %
        results["front_lines"].sort(key=lambda x: x.get("edge_pct") or 0, reverse=True)
        
        # War Zone: Sort by ceiling rate
        results["war_zone"].sort(key=lambda x: x.get("ceiling_rate") or 0, reverse=True)
        
        logger.info(f"[TIER_SORTER] Phase 1 Complete:")
        logger.info(f"  Safe Haven: {len(results['safe_haven'])}")
        logger.info(f"  Front Lines: {len(results['front_lines'])}")
        logger.info(f"  War Zone: {len(results['war_zone'])}")
        logger.info(f"  Discarded: {len(results['discarded'])}")
        
        return results


# Singleton
_tier_sorter: Optional[MLBTierSorter] = None


def get_tier_sorter(db: AsyncIOMotorDatabase) -> MLBTierSorter:
    """Get or create Tier Sorter instance."""
    global _tier_sorter
    if _tier_sorter is None:
        _tier_sorter = MLBTierSorter(db)
    return _tier_sorter


async def run_mlb_tier_sorting(
    db: AsyncIOMotorDatabase,
    save_to_db: bool = True
) -> Dict[str, Any]:
    """Run MLB Tier Sorting pipeline."""
    sorter = get_tier_sorter(db)
    return await sorter.sort_props(save_to_db)
