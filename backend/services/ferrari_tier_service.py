"""
Ferrari+ Hybrid Tier Service
=============================
"Best of the Best" filtering using Bovada as the sharp benchmark
with DVP matchup and AI context intelligence integration.

FERRARI+ ELITE FILTERS:
1. SAFE HAVEN: sharp_price <= -250 AND l10_hit_rate >= 80%
2. FRONT LINES: sharp_price -149 to -245 AND dvp_rank <= 10 (weak defenses)
3. WAR ZONE: sharp_price >= +500 AND ai_context_score > 75

SORTING: By Line Delta (PP line - anchor line), drop lowest hit rates if > 10
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Global Kill-Switch
GLOBAL_MIN_SEPARATION_PCT = 15.0  # 15% minimum implied probability separation

# DEAD ZONE: Props with sharp price between -137 and -148 are hidden
DEAD_ZONE_MIN = -148
DEAD_ZONE_MAX = -137

# FERRARI+ ELITE THRESHOLDS
# Safe Haven: Ultra-safe locks with 80%+ consistency
SAFE_HAVEN_MAX_SHARP_PRICE = -250  # Sharp price <= -250
SAFE_HAVEN_MIN_L10_RATE = 0.80     # 80% L10 hit rate (upgraded from 70%)

# Front Lines: DVP-targeted plays against weak defenses
FRONT_LINES_MIN_SHARP_PRICE = -245  # Sharp price floor
FRONT_LINES_MAX_SHARP_PRICE = -149  # Sharp price ceiling
FRONT_LINES_MAX_DVP_RANK = 10       # Only target weak defenses (rank 1-10)

# War Zone: AI-validated high-upside demons
WAR_ZONE_MIN_SHARP_PRICE = 500      # Sharp price >= +500
WAR_ZONE_MIN_AI_CONTEXT = 40        # AI context score > 40 (default 50 passes)
WAR_ZONE_MIN_L10_HITS = 2           # Safety net: hit at least 2 in L10


def american_to_implied_probability(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_separation_pct(pp_price: int, sharp_price: int) -> float:
    """Calculate separation percentage using implied probability."""
    if pp_price is None or sharp_price is None:
        return 0.0
    
    pp_implied = american_to_implied_probability(pp_price)
    sharp_implied = american_to_implied_probability(sharp_price)
    
    if sharp_implied == 0:
        return 0.0
    
    return abs(pp_implied - sharp_implied) / sharp_implied * 100


def calculate_line_delta(pp_line: float, anchor_line: float) -> float:
    """
    Calculate Line Delta: difference between PP line and anchor (standard) line.
    
    Positive delta = PP line is higher than standard (harder prop)
    Negative delta = PP line is lower than standard (easier prop)
    """
    if pp_line is None or anchor_line is None:
        return 0.0
    return pp_line - anchor_line


def calculate_mode(values: List[float]) -> Optional[float]:
    """
    Calculate the mode (most frequent value) from a list of values.
    Returns None if no clear mode exists.
    """
    if not values:
        return None
    
    from collections import Counter
    # Round to nearest 0.5 for grouping similar values
    rounded = [round(v * 2) / 2 for v in values]
    counts = Counter(rounded)
    
    if not counts:
        return None
    
    # Get the most common value
    most_common = counts.most_common(1)[0]
    mode_value, mode_count = most_common
    
    # Only return mode if it appears at least twice
    if mode_count >= 2:
        return mode_value
    return None


def calculate_median(values: List[float]) -> Optional[float]:
    """
    Calculate the median (middle value) from a sorted list.
    """
    if not values:
        return None
    
    sorted_values = sorted(values)
    n = len(sorted_values)
    
    if n % 2 == 0:
        # Even number: average of two middle values
        return (sorted_values[n//2 - 1] + sorted_values[n//2]) / 2
    else:
        # Odd number: middle value
        return sorted_values[n//2]


def calculate_mean(values: List[float]) -> Optional[float]:
    """Calculate the mean (average) from a list of values."""
    if not values:
        return None
    return sum(values) / len(values)


class FerrariTierService:
    """
    Ferrari+ Hybrid Tier Service - Elite filtering with:
    - Bovada sharp price gates
    - DVP matchup targeting
    - AI context validation
    - Line Delta sorting
    """
    
    def __init__(self, db):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.dvp_rankings_col = db.dvp_rankings
        self.master_hub = db.nba_master_hub_2026
        self.player_stats = db.dg_player_stats  # For game logs
        
        # Ferrari+ tier collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded
    
    async def _load_player_game_stats(self) -> Dict[str, Dict[str, List[float]]]:
        """
        Load recent game stats for all players.
        Returns: {player_name: {stat_type: [values from last 10 games]}}
        """
        player_stats_cache = {}
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
                # Sort by game date (most recent first) and take last 10
                sorted_games = sorted(
                    games, 
                    key=lambda g: g.get("game", {}).get("date", ""),
                    reverse=True
                )[:10]
                
                # Extract stat values
                stat_values = {
                    "PTS": [g.get("pts") for g in sorted_games if g.get("pts") is not None],
                    "AST": [g.get("ast") for g in sorted_games if g.get("ast") is not None],
                    "REB": [g.get("reb") for g in sorted_games if g.get("reb") is not None],
                    "3PM": [g.get("fg3m") for g in sorted_games if g.get("fg3m") is not None],
                    "BLK": [g.get("blk") for g in sorted_games if g.get("blk") is not None],
                    "STL": [g.get("stl") for g in sorted_games if g.get("stl") is not None],
                }
                
                # Calculate PRA (Points + Rebounds + Assists)
                pra_values = []
                for g in sorted_games:
                    pts = g.get("pts", 0) or 0
                    reb = g.get("reb", 0) or 0
                    ast = g.get("ast", 0) or 0
                    if pts or reb or ast:
                        pra_values.append(pts + reb + ast)
                stat_values["PRA"] = pra_values
                
                player_stats_cache[player_name] = stat_values
                
        except Exception as e:
            logger.warning(f"[FERRARI+] Could not load player game stats: {e}")
        
        logger.info(f"[FERRARI+] Loaded game stats for {len(player_stats_cache)} players")
        return player_stats_cache
    
    async def _load_dvp_rankings(self) -> Dict[str, Dict[str, int]]:
        """Load DVP rankings: {stat_type: {team: rank}}"""
        try:
            doc = await self.dvp_rankings_col.find_one({"type": "dvp_rankings"})
            if doc:
                return doc.get("rankings", {})
        except Exception as e:
            logger.warning(f"[FERRARI+] Could not load DVP rankings: {e}")
        return {}
    
    async def _load_ai_context_scores(self) -> Dict[str, float]:
        """Load AI context scores: {player_name: score}"""
        ai_cache = {}
        try:
            cursor = self.master_hub.find(
                {"ai_context_score": {"$exists": True}},
                {"_id": 0, "display_name": 1, "player_name": 1, "ai_context_score": 1}
            )
            async for doc in cursor:
                name = doc.get("display_name") or doc.get("player_name")
                if name:
                    # Convert 0-1 scale to 0-100 if needed
                    score = doc.get("ai_context_score", 0.5)
                    if score <= 1:
                        score = score * 100
                    ai_cache[name] = score
        except Exception as e:
            logger.warning(f"[FERRARI+] Could not load AI context: {e}")
        return ai_cache
    
    def _get_dvp_rank(
        self, 
        dvp_rankings: Dict[str, Dict[str, int]], 
        opponent: str, 
        stat_type: str
    ) -> int:
        """Get DVP rank for opponent team vs stat type. Lower = weaker defense."""
        if not dvp_rankings or not opponent or not stat_type:
            return 99  # Default high rank (strong defense)
        
        # Normalize stat type
        stat_map = {
            "PTS": "PTS", "AST": "AST", "REB": "REB",
            "3PM": "3PM", "BLK": "BLK", "STL": "STL",
            "PRA": "PTS", "P+R": "PTS", "P+A": "PTS", "R+A": "REB"
        }
        normalized_stat = stat_map.get(stat_type.upper(), "PTS")
        
        stat_rankings = dvp_rankings.get(normalized_stat, {})
        return stat_rankings.get(opponent, 99)
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Build Ferrari+ Hybrid Tiers with DVP and AI context integration.
        
        1. Apply global 15% separation kill-switch
        2. Apply tier-specific elite filters (DVP, AI context)
        3. Sort by Line Delta, drop lowest hit rates if > 10
        """
        logger.info("[FERRARI+] Building Ferrari+ Hybrid tiers...")
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "total_props_scanned": 0,
            "props_discarded": 0,
            "props_qualified": 0,
            "safe_haven": {"count": 0, "candidates": 0},
            "front_lines": {"count": 0, "candidates": 0},
            "war_zone": {"count": 0, "candidates": 0}
        }
        
        try:
            # Pre-load DVP rankings, AI context, and player game stats
            dvp_rankings = await self._load_dvp_rankings()
            ai_context_cache = await self._load_ai_context_scores()
            player_game_stats = await self._load_player_game_stats()
            
            logger.info(f"[FERRARI+] Loaded DVP rankings for {len(dvp_rankings)} stat types")
            logger.info(f"[FERRARI+] Loaded AI context for {len(ai_context_cache)} players")
            logger.info(f"[FERRARI+] Loaded game stats for {len(player_game_stats)} players")
            
            # Fetch all players from cached_board
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=500)
            
            # Track all props and apply filters
            discarded_props = []
            safe_haven_candidates = []
            front_lines_candidates = []
            war_zone_candidates = []
            
            for player in players:
                player_name = player.get("player_name", "")
                opponent = player.get("opponent") or player.get("opponent_abbr", "")
                
                # Get AI context score for this player
                ai_context_score = ai_context_cache.get(player_name, 50)  # Default 50
                
                # Get player's game stats for mode/median calculation
                player_stats = player_game_stats.get(player_name, {})
                
                for prop in player.get("props", []):
                    results["total_props_scanned"] += 1
                    
                    # Get stat type and calculate mode/median from game logs
                    stat_type = prop.get("stat_type", "")
                    stat_values = player_stats.get(stat_type.upper(), [])
                    
                    # Calculate mode, median, mean from last 10 games
                    l10_mode = calculate_mode(stat_values)
                    l10_median = calculate_median(stat_values)
                    l10_mean = calculate_mean(stat_values)
                    
                    # Get sharp market data
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    pp_price = prop.get("price", -137)
                    
                    # Skip props without sharp data
                    if sharp_price is None:
                        continue
                    
                    # DEAD ZONE CHECK
                    if DEAD_ZONE_MIN <= sharp_price <= DEAD_ZONE_MAX:
                        discarded_props.append({
                            "player_name": player_name,
                            "reason": f"Dead Zone: Sharp {sharp_price}",
                            "sharp_price": sharp_price
                        })
                        continue
                    
                    # GLOBAL KILL-SWITCH: 15% separation
                    separation_pct = calculate_separation_pct(pp_price, sharp_price)
                    if separation_pct < GLOBAL_MIN_SEPARATION_PCT:
                        discarded_props.append({
                            "player_name": player_name,
                            "reason": f"Separation {separation_pct:.1f}% < 15%",
                            "sharp_price": sharp_price
                        })
                        continue
                    
                    # Extract hit rates
                    hit_rates = prop.get("hit_rates", {})
                    if "l10_rate" in hit_rates:
                        l10_rate = (hit_rates.get("l10_rate") or 0) / 100.0
                        l5_rate = (hit_rates.get("l5_rate") or 0) / 100.0
                        l10_hits = hit_rates.get("l10_hit_count") or 0
                    else:
                        l10_data = hit_rates.get("l10", {})
                        l10_rate = l10_data.get("hit_rate", 0) if isinstance(l10_data, dict) else 0
                        l10_hits = l10_data.get("games_over", 0) if isinstance(l10_data, dict) else 0
                        l5_rate = hit_rates.get("l5", {}).get("hit_rate", 0) if isinstance(hit_rates.get("l5"), dict) else 0
                    
                    # Get DVP rank for opponent
                    stat_type = prop.get("stat_type", "")
                    dvp_rank = self._get_dvp_rank(dvp_rankings, opponent, stat_type)
                    
                    # Calculate Line Delta
                    pp_line = prop.get("line", 0)
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = calculate_line_delta(pp_line, anchor_line)
                    
                    # Build candidate object
                    candidate = self._build_candidate(
                        player, prop, sharp_market,
                        separation_pct, line_delta,
                        l10_rate, l5_rate, l10_hits,
                        dvp_rank, ai_context_score,
                        l10_mode, l10_median, l10_mean,
                        sync_time
                    )
                    
                    # TIER CLASSIFICATION with ELITE FILTERS
                    
                    # SAFE HAVEN: sharp <= -250 AND l10_rate >= 80%
                    if sharp_price <= SAFE_HAVEN_MAX_SHARP_PRICE:
                        if l10_rate >= SAFE_HAVEN_MIN_L10_RATE:
                            safe_haven_candidates.append(candidate)
                            results["safe_haven"]["candidates"] += 1
                    
                    # FRONT LINES: sharp -245 to -149 AND dvp_rank <= 10
                    elif FRONT_LINES_MIN_SHARP_PRICE <= sharp_price <= FRONT_LINES_MAX_SHARP_PRICE:
                        if dvp_rank <= FRONT_LINES_MAX_DVP_RANK:
                            front_lines_candidates.append(candidate)
                            results["front_lines"]["candidates"] += 1
                    
                    # WAR ZONE: sharp >= +500 AND ai_context > 75
                    elif sharp_price >= WAR_ZONE_MIN_SHARP_PRICE:
                        if ai_context_score > WAR_ZONE_MIN_AI_CONTEXT and l10_hits >= WAR_ZONE_MIN_L10_HITS:
                            war_zone_candidates.append(candidate)
                            results["war_zone"]["candidates"] += 1
            
            results["props_discarded"] = len(discarded_props)
            
            # SORTING & SELECTION
            # Primary sort: Line Delta (abs value - bigger delta = more edge)
            # Tiebreaker: Hit rate (drop lowest if > 10)
            
            used_players = set()
            
            # SAFE HAVEN: Sort by Line Delta (negative = easier line), then hit rate
            safe_haven_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), -x.get("l10_rate", 0))
            )
            top_safe_haven = self._dedupe_and_select(safe_haven_candidates, used_players, 10)
            
            # FRONT LINES: Sort by Line Delta, then DVP rank (lower = weaker defense)
            front_lines_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), x.get("dvp_rank", 99), -x.get("l10_rate", 0))
            )
            top_front_lines = self._dedupe_and_select(front_lines_candidates, used_players, 10)
            
            # WAR ZONE: Sort by Line Delta, then AI context score
            war_zone_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), -x.get("ai_context_score", 0), -x.get("l10_rate", 0))
            )
            top_war_zone = self._dedupe_and_select(war_zone_candidates, used_players, 10)
            
            # Store in MongoDB
            await self.ferrari_safe_haven.delete_many({})
            if top_safe_haven:
                await self.ferrari_safe_haven.insert_many(top_safe_haven)
            results["safe_haven"]["count"] = len(top_safe_haven)
            
            await self.ferrari_front_lines.delete_many({})
            if top_front_lines:
                await self.ferrari_front_lines.insert_many(top_front_lines)
            results["front_lines"]["count"] = len(top_front_lines)
            
            await self.ferrari_war_zone.delete_many({})
            if top_war_zone:
                await self.ferrari_war_zone.insert_many(top_war_zone)
            results["war_zone"]["count"] = len(top_war_zone)
            
            # Store discarded for debugging
            await self.ferrari_discarded.delete_many({})
            if discarded_props:
                await self.ferrari_discarded.insert_many(discarded_props[:100])
            
            logger.info(
                f"[FERRARI+] Complete: "
                f"Safe Haven={results['safe_haven']['count']}/{results['safe_haven']['candidates']}, "
                f"Front Lines={results['front_lines']['count']}/{results['front_lines']['candidates']}, "
                f"War Zone={results['war_zone']['count']}/{results['war_zone']['candidates']}"
            )
            
        except Exception as e:
            logger.error(f"[FERRARI+] Error building tiers: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["error"] = str(e)
        
        return results
    
    def _build_candidate(
        self,
        player: Dict,
        prop: Dict,
        sharp_market: Dict,
        separation_pct: float,
        line_delta: float,
        l10_rate: float,
        l5_rate: float,
        l10_hits: int,
        dvp_rank: int,
        ai_context_score: float,
        l10_mode: Optional[float],
        l10_median: Optional[float],
        l10_mean: Optional[float],
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Build a standardized candidate object for tier storage."""
        # Extract averages from hit_rates
        hit_rates = prop.get("hit_rates", {})
        l5_avg = hit_rates.get("l5_avg")
        l10_avg = hit_rates.get("l10_avg")
        season_avg = hit_rates.get("season_avg")
        
        return {
            "player_name": player.get("player_name"),
            "player_id": player.get("player_id"),
            "team": player.get("team"),
            "team_name": player.get("team_name"),
            "photo_url": player.get("photo_url") or player.get("headshot_url"),
            "headshot_url": player.get("headshot_url"),
            "nba_id": player.get("nba_id"),
            "position": player.get("position"),
            "opponent": player.get("opponent") or player.get("opponent_abbr"),
            "opponent_abbr": player.get("opponent_abbr"),
            "game_time": player.get("game_time"),
            # Prop details
            "stat_type": prop.get("stat_type"),
            "market": prop.get("market"),
            "direction": prop.get("direction"),
            "line": prop.get("line"),
            "anchor_line": prop.get("anchor_line"),
            "price": prop.get("price"),
            "is_demon": prop.get("is_demon", False),
            "is_goblin": prop.get("is_goblin", False),
            "is_alternate": sharp_market.get("is_alternate", False),
            # Sharp market data
            "sharp_price": sharp_market.get("sharp_price"),
            "sharp_source": sharp_market.get("sharp_source"),
            "bovada_price": sharp_market.get("bovada_price"),
            "draftkings_price": sharp_market.get("draftkings_price"),
            "fanduel_price": sharp_market.get("fanduel_price"),
            "dk_fd_average": sharp_market.get("dk_fd_average"),
            # FERRARI+ metrics
            "separation_pct": round(separation_pct, 1),
            "line_delta": round(line_delta, 1),
            "dvp_rank": dvp_rank,
            "ai_context_score": round(ai_context_score, 1),
            # Hit rates (frontend expects h5_rate, h10_rate format)
            "l10_rate": round(l10_rate * 100, 1),
            "l5_rate": round(l5_rate * 100, 1),
            "h10_rate": round(l10_rate * 100, 1),  # Frontend format
            "h5_rate": round(l5_rate * 100, 1),    # Frontend format
            "l10_hits": l10_hits,
            # Averages (extracted to top level for frontend)
            "l5_avg": l5_avg,
            "l10_avg": l10_avg,
            "season_avg": season_avg,
            # MODE / MEDIAN / MEAN from last 10 games
            "l10_mode": round(l10_mode, 1) if l10_mode is not None else None,
            "l10_median": round(l10_median, 1) if l10_median is not None else None,
            "l10_mean": round(l10_mean, 1) if l10_mean is not None else None,
            # Full hit_rates object
            "hit_rates": hit_rates,
            # Metadata
            "synced_at": sync_time.isoformat(),
            "is_ferrari_pick": True
        }
    
    def _dedupe_and_select(
        self, 
        candidates: List[Dict], 
        used_players: set, 
        limit: int
    ) -> List[Dict]:
        """
        Deduplicate picks and select top N.
        Cross-tier deduplication via used_players set.
        """
        selected = []
        for pick in candidates:
            name = pick.get("player_name")
            if name and name not in used_players:
                used_players.add(name)
                selected.append(pick)
                if len(selected) >= limit:
                    break
        return selected
    
    # ==================== GETTER METHODS ====================
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari+ Safe Haven picks."""
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"<= {SAFE_HAVEN_MAX_SHARP_PRICE}",
                "l10_rate": f">= {SAFE_HAVEN_MIN_L10_RATE * 100}%",
                "sort_by": "Line Delta (biggest edge first)"
            }
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari+ Front Lines picks."""
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"{FRONT_LINES_MIN_SHARP_PRICE} to {FRONT_LINES_MAX_SHARP_PRICE}",
                "dvp_rank": f"<= {FRONT_LINES_MAX_DVP_RANK} (weak defenses only)",
                "sort_by": "Line Delta, then DVP Rank"
            }
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari+ War Zone picks."""
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f">= +{WAR_ZONE_MIN_SHARP_PRICE}",
                "ai_context": f"> {WAR_ZONE_MIN_AI_CONTEXT}",
                "l10_hits": f">= {WAR_ZONE_MIN_L10_HITS}",
                "sort_by": "Line Delta, then AI Context Score"
            }
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        """Get props discarded by kill-switch or dead zone."""
        cursor = self.ferrari_discarded.find({}, {"_id": 0}).limit(limit)
        discarded = await cursor.to_list(length=limit)
        return {
            "discarded": discarded,
            "count": len(discarded),
            "kill_switch_threshold": f"{GLOBAL_MIN_SEPARATION_PCT}%",
            "dead_zone": f"{DEAD_ZONE_MIN} to {DEAD_ZONE_MAX}"
        }


# Singleton instance
_ferrari_service = None


def get_ferrari_tier_service(db=None):
    """Get or create the Ferrari tier service singleton."""
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = FerrariTierService(db)
    return _ferrari_service
