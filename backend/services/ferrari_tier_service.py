"""
Ferrari v4 Pipeline - FINAL ARCHITECTURAL LOCK
===============================================
"Math so tight you can copy-paste with 100% confidence."

PROBABILITY STANDARD:
- Kill Switch: |Sharp_Implied - 57.8%| >= 5% absolute edge
- All calculations use implied probability

MEDIAN ANCHOR:
- Season Median required for each player/stat
- PP Line must be <= Season Median to qualify

TIER WINDOWS:
- Safe Haven: Sharp <= -250
- Front Lines: Sharp -245 to -115
- War Zone: Sharp -114 to +500

OUTPUT:
- 30 total plays (10 per tier)
- One player per tier
- Sorted by Line Delta, then L10 Hit Rate
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# FERRARI v4 CONSTANTS
# =============================================================================

# PROBABILITY STANDARD
PP_IMPLIED_PROBABILITY = 0.578  # -137 = 57.8%
MIN_ABSOLUTE_EDGE = 0.05        # 5% absolute edge required

# TIER WINDOWS (Sharp Price)
SAFE_HAVEN_MAX = -250           # <= -250
FRONT_LINES_MIN = -245          # -245 to -115
FRONT_LINES_MAX = -115
WAR_ZONE_MIN = -114             # -114 to +500
WAR_ZONE_MAX = 500

# OUTPUT CAPS
MAX_PICKS_PER_TIER = 10         # 30 total plays


# =============================================================================
# MATHEMATICAL FUNCTIONS
# =============================================================================

def american_to_implied(odds: int) -> float:
    """
    Convert American odds to implied probability.
    
    -200 → 66.7%
    -137 → 57.8%
    +200 → 33.3%
    """
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_absolute_edge(sharp_price: int) -> float:
    """
    FERRARI v4 KILL SWITCH FORMULA:
    
    Absolute Edge = |Sharp_Implied - PP_Implied|
    
    Where PP_Implied = 57.8% (standard -137 line)
    
    Example:
        Sharp: -250 → 71.4%
        Edge = |0.714 - 0.578| = 0.136 = 13.6%
    """
    if sharp_price is None:
        return 0.0
    
    sharp_implied = american_to_implied(sharp_price)
    return abs(sharp_implied - PP_IMPLIED_PROBABILITY)


def calculate_line_delta(pp_line: float, anchor_line: float) -> float:
    """
    Line Delta = PP Line - Anchor Line
    
    Negative = PP line is BELOW anchor (easier)
    Positive = PP line is ABOVE anchor (harder)
    """
    if pp_line is None or anchor_line is None:
        return 0.0
    return pp_line - anchor_line


def calculate_median(values: List[float]) -> Optional[float]:
    """Calculate median from a list of values."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    return sorted_vals[n//2]


def calculate_mode(values: List[float]) -> Optional[float]:
    """Calculate mode (most frequent value, rounded to 0.5)."""
    if not values:
        return None
    rounded = [round(v * 2) / 2 for v in values]
    counts = Counter(rounded)
    if not counts:
        return None
    mode_val, mode_count = counts.most_common(1)[0]
    return mode_val if mode_count >= 2 else None


def calculate_mean(values: List[float]) -> Optional[float]:
    """Calculate mean from a list of values."""
    if not values:
        return None
    return sum(values) / len(values)


# =============================================================================
# FERRARI v4 PIPELINE SERVICE
# =============================================================================

class FerrariTierService:
    """
    Ferrari v4 Pipeline - Final Architectural Lock
    
    1. Kill Switch: 5% absolute edge minimum
    2. Median Anchor: PP Line <= Season Median
    3. Tier Classification: New windows
    4. Sorting: Line Delta → L10 Hit Rate
    5. Deduplication: One player per tier
    """
    
    def __init__(self, db):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.player_stats = db.dg_player_stats
        
        # Output collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded
    
    async def _load_season_medians(self) -> Dict[str, Dict[str, float]]:
        """
        Load SEASON MEDIAN for each player/stat combination.
        
        Returns: {player_name: {stat_type: season_median}}
        
        This is the MEDIAN ANCHOR - props must be <= this value.
        """
        medians = {}
        
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
                # Extract ALL season games (not just L10)
                stat_values = {
                    "PTS": [g["pts"] for g in games if g.get("pts") is not None],
                    "AST": [g["ast"] for g in games if g.get("ast") is not None],
                    "REB": [g["reb"] for g in games if g.get("reb") is not None],
                    "3PM": [g["fg3m"] for g in games if g.get("fg3m") is not None],
                    "BLK": [g["blk"] for g in games if g.get("blk") is not None],
                    "STL": [g["stl"] for g in games if g.get("stl") is not None],
                }
                
                # Calculate PRA
                pra_values = []
                for g in games:
                    pts = g.get("pts", 0) or 0
                    reb = g.get("reb", 0) or 0
                    ast = g.get("ast", 0) or 0
                    if pts or reb or ast:
                        pra_values.append(pts + reb + ast)
                stat_values["PRA"] = pra_values
                
                # Calculate season median for each stat
                player_medians = {}
                for stat, values in stat_values.items():
                    if values:
                        player_medians[stat] = calculate_median(values)
                
                medians[player_name] = player_medians
                
        except Exception as e:
            logger.error(f"[v4] Season median load failed: {e}")
        
        logger.info(f"[v4] Loaded season medians for {len(medians)} players")
        return medians
    
    async def _load_l10_stats(self) -> Dict[str, Dict[str, List[float]]]:
        """Load L10 game stats for mode/median/mean calculations."""
        stats = {}
        
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
                # Sort by date, take last 10
                sorted_games = sorted(
                    games,
                    key=lambda g: g.get("game", {}).get("date", ""),
                    reverse=True
                )[:10]
                
                stats[player_name] = {
                    "PTS": [g["pts"] for g in sorted_games if g.get("pts") is not None],
                    "AST": [g["ast"] for g in sorted_games if g.get("ast") is not None],
                    "REB": [g["reb"] for g in sorted_games if g.get("reb") is not None],
                    "3PM": [g["fg3m"] for g in sorted_games if g.get("fg3m") is not None],
                    "BLK": [g["blk"] for g in sorted_games if g.get("blk") is not None],
                    "STL": [g["stl"] for g in sorted_games if g.get("stl") is not None],
                    "PRA": [
                        (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                        for g in sorted_games
                    ]
                }
        except Exception as e:
            logger.error(f"[v4] L10 stats load failed: {e}")
        
        return stats
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Execute Ferrari v4 Pipeline.
        """
        logger.info("=" * 70)
        logger.info("[FERRARI v4] FINAL ARCHITECTURAL LOCK")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "pipeline": "Ferrari v4",
            "total_scanned": 0,
            "kill_switch": {
                "edge_too_small": 0,
                "above_median": 0,
                "no_sharp_data": 0
            },
            "tier_qualified": {
                "safe_haven": 0,
                "front_lines": 0,
                "war_zone": 0
            },
            "output": {
                "safe_haven": 0,
                "front_lines": 0,
                "war_zone": 0,
                "total": 0
            }
        }
        
        try:
            # =================================================================
            # PHASE 1: DATA SOURCING
            # =================================================================
            logger.info("[PHASE 1] Loading data sources...")
            
            season_medians = await self._load_season_medians()
            l10_stats = await self._load_l10_stats()
            
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=500)
            
            logger.info(f"  Season Medians: {len(season_medians)} players")
            logger.info(f"  L10 Stats: {len(l10_stats)} players")
            logger.info(f"  Cached Board: {len(players)} players")
            
            # =================================================================
            # PHASE 2 & 3: KILL SWITCH + TIER CLASSIFICATION
            # =================================================================
            logger.info("[PHASE 2] Kill Switch: 5% absolute edge + Median Anchor")
            logger.info("[PHASE 3] Tier Classification: New Windows")
            
            discarded = []
            safe_haven = []
            front_lines = []
            war_zone = []
            
            for player in players:
                player_name = player.get("player_name", "")
                player_season_medians = season_medians.get(player_name, {})
                player_l10_stats = l10_stats.get(player_name, {})
                
                for prop in player.get("props", []):
                    results["total_scanned"] += 1
                    
                    # Get sharp data
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    
                    # Skip if no sharp data
                    if sharp_price is None:
                        results["kill_switch"]["no_sharp_data"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # KILL SWITCH 1: 5% Absolute Edge
                    # ---------------------------------------------------------
                    absolute_edge = calculate_absolute_edge(sharp_price)
                    
                    if absolute_edge < MIN_ABSOLUTE_EDGE:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"EDGE: {absolute_edge*100:.1f}% < 5%",
                            "sharp_price": sharp_price,
                            "absolute_edge": round(absolute_edge * 100, 1)
                        })
                        results["kill_switch"]["edge_too_small"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # KILL SWITCH 2: Median Anchor
                    # PP Line must be <= Season Median
                    # ---------------------------------------------------------
                    stat_type = prop.get("stat_type", "").upper()
                    pp_line = prop.get("line", 0)
                    season_median = player_season_medians.get(stat_type)
                    
                    if season_median is not None and pp_line > season_median:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"MEDIAN: Line {pp_line} > Median {season_median}",
                            "sharp_price": sharp_price,
                            "pp_line": pp_line,
                            "season_median": season_median
                        })
                        results["kill_switch"]["above_median"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # EXTRACT STATS
                    # ---------------------------------------------------------
                    hit_rates = prop.get("hit_rates", {})
                    
                    if "l10_rate" in hit_rates:
                        l10_rate = (hit_rates.get("l10_rate") or 0) / 100.0
                        l5_rate = (hit_rates.get("l5_rate") or 0) / 100.0
                        l10_hits = hit_rates.get("l10_hit_count") or 0
                    else:
                        l10_data = hit_rates.get("l10", {})
                        l10_rate = l10_data.get("hit_rate", 0) if isinstance(l10_data, dict) else 0
                        l10_hits = l10_data.get("games_over", 0) if isinstance(l10_data, dict) else 0
                        l5_rate = 0
                    
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = calculate_line_delta(pp_line, anchor_line)
                    
                    # L10 stats
                    stat_values = player_l10_stats.get(stat_type, [])
                    l10_mode = calculate_mode(stat_values)
                    l10_median = calculate_median(stat_values)
                    l10_mean = calculate_mean(stat_values)
                    
                    # Build candidate
                    candidate = self._build_candidate(
                        player, prop, sharp_market,
                        absolute_edge, line_delta,
                        l10_rate, l5_rate, l10_hits,
                        season_median, l10_mode, l10_median, l10_mean,
                        sync_time
                    )
                    
                    # ---------------------------------------------------------
                    # TIER CLASSIFICATION (New Windows)
                    # ---------------------------------------------------------
                    
                    # SAFE HAVEN: Sharp <= -250
                    if sharp_price <= SAFE_HAVEN_MAX:
                        safe_haven.append(candidate)
                        results["tier_qualified"]["safe_haven"] += 1
                    
                    # FRONT LINES: Sharp -245 to -115
                    elif FRONT_LINES_MIN <= sharp_price <= FRONT_LINES_MAX:
                        front_lines.append(candidate)
                        results["tier_qualified"]["front_lines"] += 1
                    
                    # WAR ZONE: Sharp -114 to +500
                    elif WAR_ZONE_MIN <= sharp_price <= WAR_ZONE_MAX:
                        war_zone.append(candidate)
                        results["tier_qualified"]["war_zone"] += 1
            
            logger.info(f"  Kill Switch - Edge too small: {results['kill_switch']['edge_too_small']}")
            logger.info(f"  Kill Switch - Above median: {results['kill_switch']['above_median']}")
            logger.info(f"  Kill Switch - No sharp data: {results['kill_switch']['no_sharp_data']}")
            logger.info(f"  Qualified - SH: {len(safe_haven)}, FL: {len(front_lines)}, WZ: {len(war_zone)}")
            
            # =================================================================
            # PHASE 4: SORTING (Line Delta → L10 Hit Rate)
            # =================================================================
            logger.info("[PHASE 4] Sorting: Line Delta → L10 Hit Rate")
            
            # Sort each tier: Line Delta (biggest first), then L10 Rate (highest first)
            safe_haven.sort(key=lambda x: (-abs(x["line_delta"]), -x["l10_rate"]))
            front_lines.sort(key=lambda x: (-abs(x["line_delta"]), -x["l10_rate"]))
            war_zone.sort(key=lambda x: (-abs(x["line_delta"]), -x["l10_rate"]))
            
            # =================================================================
            # PHASE 5: DEDUPLICATION (One Player Per Tier)
            # =================================================================
            logger.info("[PHASE 5] Deduplication: One player per tier")
            
            used_players = set()
            
            top_safe_haven = self._dedupe_select(safe_haven, used_players, MAX_PICKS_PER_TIER)
            top_front_lines = self._dedupe_select(front_lines, used_players, MAX_PICKS_PER_TIER)
            top_war_zone = self._dedupe_select(war_zone, used_players, MAX_PICKS_PER_TIER)
            
            # =================================================================
            # STORE RESULTS
            # =================================================================
            await self.ferrari_safe_haven.delete_many({})
            if top_safe_haven:
                await self.ferrari_safe_haven.insert_many(top_safe_haven)
            
            await self.ferrari_front_lines.delete_many({})
            if top_front_lines:
                await self.ferrari_front_lines.insert_many(top_front_lines)
            
            await self.ferrari_war_zone.delete_many({})
            if top_war_zone:
                await self.ferrari_war_zone.insert_many(top_war_zone)
            
            await self.ferrari_discarded.delete_many({})
            if discarded:
                await self.ferrari_discarded.insert_many(discarded[:200])
            
            results["output"]["safe_haven"] = len(top_safe_haven)
            results["output"]["front_lines"] = len(top_front_lines)
            results["output"]["war_zone"] = len(top_war_zone)
            results["output"]["total"] = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            
            logger.info("=" * 70)
            logger.info("[FERRARI v4] PIPELINE COMPLETE")
            logger.info(f"  Safe Haven: {len(top_safe_haven)}/10")
            logger.info(f"  Front Lines: {len(top_front_lines)}/10")
            logger.info(f"  War Zone: {len(top_war_zone)}/10")
            logger.info(f"  TOTAL: {results['output']['total']}/30")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[v4] Pipeline error: {e}")
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
        absolute_edge: float,
        line_delta: float,
        l10_rate: float,
        l5_rate: float,
        l10_hits: int,
        season_median: Optional[float],
        l10_mode: Optional[float],
        l10_median: Optional[float],
        l10_mean: Optional[float],
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Build standardized candidate object."""
        hit_rates = prop.get("hit_rates", {})
        sharp_price = sharp_market.get("sharp_price")
        
        return {
            # Player
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
            # Prop
            "stat_type": prop.get("stat_type"),
            "market": prop.get("market"),
            "direction": prop.get("direction"),
            "line": prop.get("line"),
            "anchor_line": prop.get("anchor_line"),
            "price": prop.get("price"),
            "is_demon": prop.get("is_demon", False),
            "is_goblin": prop.get("is_goblin", False),
            "is_alternate": sharp_market.get("is_alternate", False),
            # Sharp Market
            "sharp_price": sharp_price,
            "sharp_implied": round(american_to_implied(sharp_price) * 100, 1) if sharp_price else None,
            "sharp_source": sharp_market.get("sharp_source"),
            "bovada_price": sharp_market.get("bovada_price"),
            "draftkings_price": sharp_market.get("draftkings_price"),
            "fanduel_price": sharp_market.get("fanduel_price"),
            # v4 Metrics
            "absolute_edge": round(absolute_edge * 100, 1),
            "line_delta": round(line_delta, 1),
            "season_median": round(season_median, 1) if season_median else None,
            # Hit Rates
            "l10_rate": round(l10_rate * 100, 1),
            "l5_rate": round(l5_rate * 100, 1),
            "h10_rate": round(l10_rate * 100, 1),
            "h5_rate": round(l5_rate * 100, 1),
            "l10_hits": l10_hits,
            # Averages
            "l5_avg": hit_rates.get("l5_avg"),
            "l10_avg": hit_rates.get("l10_avg"),
            "season_avg": hit_rates.get("season_avg"),
            # L10 Stats
            "l10_mode": round(l10_mode, 1) if l10_mode else None,
            "l10_median": round(l10_median, 1) if l10_median else None,
            "l10_mean": round(l10_mean, 1) if l10_mean else None,
            # Full data
            "hit_rates": hit_rates,
            # Metadata
            "synced_at": sync_time.isoformat(),
            "pipeline": "ferrari_v4"
        }
    
    def _dedupe_select(
        self,
        candidates: List[Dict],
        used_players: set,
        limit: int
    ) -> List[Dict]:
        """Select top N with one player per tier deduplication."""
        selected = []
        for pick in candidates:
            name = pick.get("player_name")
            if name and name not in used_players:
                used_players.add(name)
                selected.append(pick)
                if len(selected) >= limit:
                    break
        return selected
    
    # =========================================================================
    # GETTER METHODS
    # =========================================================================
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp <= {SAFE_HAVEN_MAX}",
            "filters": ["5% absolute edge", "Line <= Season Median"]
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {FRONT_LINES_MIN} to {FRONT_LINES_MAX}",
            "filters": ["5% absolute edge", "Line <= Season Median"]
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {WAR_ZONE_MIN} to +{WAR_ZONE_MAX}",
            "filters": ["5% absolute edge", "Line <= Season Median"]
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        cursor = self.ferrari_discarded.find({}, {"_id": 0}).limit(limit)
        discarded = await cursor.to_list(length=limit)
        return {
            "discarded": discarded,
            "count": len(discarded),
            "kill_switch": "5% absolute edge + Median Anchor"
        }


# Singleton
_ferrari_service = None

def get_ferrari_tier_service(db=None):
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = FerrariTierService(db)
    return _ferrari_service
