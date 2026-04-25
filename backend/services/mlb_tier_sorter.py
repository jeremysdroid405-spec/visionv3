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

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# =============================================================================
# MLB gate constants & tables — DELETED 2026-04-22.
# All MLB gate thresholds (SAFE_HAVEN_GATES, FRONT_LINES_GATES,
# WAR_ZONE_GATES) plus the DK odds-bucket constants live exclusively in
# `services/scoring/gates/thresholds.py`. This file now carries only
# metric-normalization helpers consumed by the MLB scoring adapter.
# =============================================================================


def war_zone_cv_modifier(cv: Optional[float]) -> float:
    """CV → War-Zone ranking score modifier.

    CV is a scoring signal, not a gate, so this helper survived the
    gate-engine consolidation. Lower CV is slightly positive; higher CV
    is neutral/negative. Missing CV returns 0.

    Mapping (piece-wise linear):
        cv <= 0.40   →  +0.10
        cv <= 0.60   →  +0.05
        cv <= 0.80   →   0.00
        cv <= 1.00   →  -0.02
        cv >  1.00   →  -0.05
    """
    if cv is None:
        return 0.0
    try:
        cv_f = float(cv)
    except (TypeError, ValueError):
        return 0.0
    if cv_f <= 0.40:
        return 0.10
    if cv_f <= 0.60:
        return 0.05
    if cv_f <= 0.80:
        return 0.0
    if cv_f <= 1.00:
        return -0.02
    return -0.05


class MLBTierSorter:
    """
    MLB stat-utility carrier.

    Post 2026-04-22 Universal Gate Engine refactor this class no longer
    contains gate-evaluation methods; all gate logic runs through
    `services.scoring.gates.UniversalGateEngine`. It survives purely as
    a carrier for MLB-specific stat utilities consumed by
    `services.scoring.adapters.mlb_scoring` (CV, hit-rate, ceiling-rate,
    splits caches). The `sport` attribute is read by
    `scoring_stack.compute_tier._infer_sport`.
    """

    sport = "mlb"

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        # Global Identity Rule (2026-04-23): keyed on `bdl_player_id`
        # (canonical identity), not `player_name` (display-only).
        self._player_logs_cache: Dict[int, List[Dict]] = {}
        self._vk_projections_cache: Dict[str, Dict] = {}
    
    async def _load_caches(self):
        """Load player logs and VK projections into memory.

        Global Identity Rule (2026-04-23): logs are indexed by
        `bdl_player_id` (the hub's `bdl_id` column). Hub rows without
        an ID are skipped — they cannot be resolved unambiguously.
        """
        from datetime import datetime
        
        # Current season (2026)
        current_year = datetime.now().year
        current_season = current_year
        
        # Load player logs from master hub - FILTER TO CURRENT SEASON ONLY
        master_hub = self.db[COLL("master_hub", "mlb")]
        players = await master_hub.find(
            {"bdl_game_logs": {"$exists": True, "$ne": []}},
            {"_id": 0, "bdl_id": 1, "bdl_player_id": 1,
             "display_name": 1, "bdl_game_logs": 1, "vk_baselines": 1}
        ).to_list(length=None)
        
        for player in players:
            pid = player.get("bdl_player_id") or player.get("bdl_id")
            if pid is None:
                continue
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                continue
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
            
            self._player_logs_cache[pid_int] = current_season_logs
        
        logger.info(
            f"[TIER_SORTER] Loaded {len(self._player_logs_cache)} "
            f"player logs by bdl_player_id (filtered to {current_season} season)"
        )
        
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
    
    def _get_logs_by_id(
        self, bdl_player_id: Optional[int]
    ) -> List[Dict]:
        """Global Identity Rule (2026-04-23): game-log lookup by
        canonical `bdl_player_id`. No name-based fallback."""
        if bdl_player_id is None:
            return []
        try:
            pid = int(bdl_player_id)
        except (TypeError, ValueError):
            return []
        return self._player_logs_cache.get(pid) or []

    def _calculate_cv(
        self, bdl_player_id: Optional[int], stat_type: str
    ) -> Optional[float]:
        """Calculate Coefficient of Variation for player stat."""
        game_logs = self._get_logs_by_id(bdl_player_id)
        
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
        bdl_player_id: Optional[int],
        stat_type: str, 
        line: float, 
        num_games: int = 20
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Calculate hit rate and average for player stat.
        
        Returns:
            Tuple of (hit_rate_percentage, average)
        """
        game_logs = self._get_logs_by_id(bdl_player_id)
        
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

    def _calculate_hit_rate_sides(
        self,
        bdl_player_id: Optional[int],
        stat_type: str,
        line: float,
        num_games: int = 20,
        min_games: int = 10,
    ) -> "Tuple[Optional[float], Optional[float], Optional[float], Optional[int]]":
        """STRICT-WINDOW hit-rate with 20→10 fallback (2026-05 rewrite).

        WINDOW SELECTION — non-negotiable:
            - len(logs) >= 20 → use newest 20
            - len(logs) >= 10 → use newest 10
            - otherwise        → return (None, None, None, None)

        DENOMINATOR is FIXED to the selected window (20 or 10). Never
        `len(values)`. Every game in the window counts:
            - missing / None stat value → miss for OVER (hit for UNDER
              by complement)
            - never extend / walk deeper to find more "valid" games
            - never filter games out for at_bats, innings, etc.

        HIT RULE: `stat_value > line` (strict greater-than).
            HR_over  = (hits / window) * 100
            HR_under = 100 - HR_over

        OUTPUT increments:
            - 20-game window → multiples of 5
            - 10-game window → multiples of 10

        The `num_games` / `min_games` kwargs are kept for signature
        stability (callers from older paths pass them) but are
        intentionally ignored — the window contract is defined here.

        Returns:
            (hit_rate_over, hit_rate_under, average, sample_size)
            where `sample_size` is the chosen window (20 or 10).
        """
        game_logs = self._get_logs_by_id(bdl_player_id)
        if not game_logs:
            return None, None, None, None

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

        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get("date", "") or "",
            reverse=True,
        )

        # Strict window selection — no walking, no fallback to a
        # smaller-than-10 window. Either 20 or 10 or None.
        if len(sorted_logs) >= 20:
            window = 20
        elif len(sorted_logs) >= 10:
            window = 10
        else:
            return None, None, None, None

        selected = sorted_logs[:window]

        over_hits = 0
        sum_val = 0.0
        valid_count = 0  # only used for `avg` — never the HR denominator
        for game in selected:
            if isinstance(field, list):
                # Combo stats (HRR): None components count as 0 so the
                # combo value is well-defined as long as one component
                # is present.
                val = sum(game.get(f) or 0 for f in field)
            elif field == "innings_pitched":
                ip = game.get(field)
                val = (ip * 3) if ip is not None else None
            elif field == "singles":
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
            # Strict `>` per spec. None values → miss for OVER (hit for
            # UNDER by complement).
            if val is not None:
                if val > line:
                    over_hits += 1
                sum_val += val
                valid_count += 1

        # Fixed denominator. Result is integer-valued (multiples of 5
        # for window=20, multiples of 10 for window=10). We surface
        # them as floats only because the storage / API schema is
        # `Optional[float]` everywhere downstream.
        hit_rate_over = float(round((over_hits / window) * 100))
        hit_rate_under = 100.0 - hit_rate_over
        avg = round(sum_val / valid_count, 2) if valid_count else None
        return hit_rate_over, hit_rate_under, avg, window

    def _calculate_ceiling_hit_rate(
        self, 
        bdl_player_id: Optional[int],
        stat_type: str, 
        line: float
    ) -> Optional[float]:
        """Calculate ceiling hit rate (times player exceeded 2x line)."""
        game_logs = self._get_logs_by_id(bdl_player_id)
        
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
        bdl_player_id: Optional[int],
        stat_type: str, 
        num_games: int = 5
    ) -> List[Dict]:
        """Get formatted recent game logs for Oracle context."""
        game_logs = self._get_logs_by_id(bdl_player_id)
        
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

    
    # ------------------------------------------------------------------
    # check_safe_haven_gates / check_front_lines_gates / check_war_zone_gates
    # DELETED 2026-04-22 (Universal Gate Engine refactor).
    # All gate evaluation now runs through
    # services.scoring.gates.UniversalGateEngine driven by config
    # in services.scoring.gates.thresholds.THRESHOLDS.
    # ------------------------------------------------------------------


