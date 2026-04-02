"""
Ferrari v5 Pipeline - Full-Field Ranking & Dynamic Top-K Selection
===================================================================
"Rank all 5,000 before showing the Top 30."

GLOBAL SCORING:
  ferrari_power_score = (Edge × 0.4) + (Cushion × 0.3) + (Form × 0.3)
  
  Where:
    Edge = Absolute edge percentage (0-100)
    Cushion = Line cushion below median (normalized 0-100)
    Form = L10 hit rate (0-100)

DYNAMIC RANKING:
  - No early exits or limits during scoring
  - Score ALL surviving props
  - Global sort by power score
  - Apply limit(10) only at final selection

SCALING:
  - Bulk writes for all database operations
  - Handles 5,000+ props efficiently
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# FERRARI v5 CONSTANTS
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

# POWER SCORE WEIGHTS
WEIGHT_EDGE = 0.4
WEIGHT_CUSHION = 0.3
WEIGHT_FORM = 0.3

# OUTPUT CAPS
MAX_PICKS_PER_TIER = 10         # 30 total plays


# =============================================================================
# MATHEMATICAL FUNCTIONS
# =============================================================================

def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_absolute_edge(sharp_price: int) -> float:
    """
    EDGE COMPONENT:
    Absolute Edge = |Sharp_Implied - PP_Implied|
    Returns as percentage (0-100)
    """
    if sharp_price is None:
        return 0.0
    sharp_implied = american_to_implied(sharp_price)
    return abs(sharp_implied - PP_IMPLIED_PROBABILITY) * 100


def calculate_cushion(pp_line: float, season_median: float) -> float:
    """
    CUSHION COMPONENT:
    How far below the median is the line?
    Normalized to 0-100 scale.
    
    Example: Line 29.5, Median 37.0
    Cushion = (37.0 - 29.5) / 37.0 * 100 = 20.3%
    """
    if pp_line is None or season_median is None or season_median == 0:
        return 0.0
    
    # Cushion = how much below median (as percentage of median)
    if pp_line <= season_median:
        cushion = ((season_median - pp_line) / season_median) * 100
        return min(cushion, 100)  # Cap at 100
    return 0.0  # Above median = no cushion


def calculate_form(l10_rate: float) -> float:
    """
    FORM COMPONENT:
    L10 hit rate as percentage (0-100)
    Already in correct format from hit_rates.
    """
    return l10_rate if l10_rate else 0.0


def calculate_power_score(edge: float, cushion: float, form: float) -> float:
    """
    FERRARI POWER SCORE:
    
    Score = (Edge × 0.4) + (Cushion × 0.3) + (Form × 0.3)
    
    All inputs should be 0-100 scale.
    Output is 0-100 scale.
    """
    return (edge * WEIGHT_EDGE) + (cushion * WEIGHT_CUSHION) + (form * WEIGHT_FORM)


def calculate_line_delta(pp_line: float, anchor_line: float) -> float:
    """Line Delta = PP Line - Anchor Line"""
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
# FERRARI v5 PIPELINE SERVICE
# =============================================================================

class FerrariTierService:
    """
    Ferrari v5 Pipeline - Full-Field Ranking
    
    Key Changes:
    - No early exits: Score ALL surviving props
    - Power score ranking: Global sort before selection
    - Bulk operations: Optimized for 5,000+ props
    - Full transparency: Return total_scanned counts
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
        self.ferrari_scored = db.ferrari_scored  # NEW: All scored props
    
    async def _load_season_medians(self) -> Dict[str, Dict[str, float]]:
        """Load SEASON MEDIAN for each player/stat combination."""
        medians = {}
        
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
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
                
                player_medians = {}
                for stat, values in stat_values.items():
                    if values:
                        player_medians[stat] = calculate_median(values)
                
                medians[player_name] = player_medians
                
        except Exception as e:
            logger.error(f"[v5] Season median load failed: {e}")
        
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
            logger.error(f"[v5] L10 stats load failed: {e}")
        
        return stats
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Execute Ferrari v5 Pipeline - Full-Field Ranking
        """
        logger.info("=" * 70)
        logger.info("[FERRARI v5] FULL-FIELD RANKING PIPELINE")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "pipeline": "Ferrari v5",
            "total_scanned": 0,
            "kill_switch": {
                "edge_too_small": 0,
                "above_median": 0,
                "no_sharp_data": 0,
                "total_killed": 0
            },
            "scored": {
                "total": 0,
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
            logger.info("[PHASE 1] Loading ALL data sources...")
            
            season_medians = await self._load_season_medians()
            l10_stats = await self._load_l10_stats()
            
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=None)  # NO LIMIT - get ALL
            
            logger.info(f"  Season Medians: {len(season_medians)} players")
            logger.info(f"  L10 Stats: {len(l10_stats)} players")
            logger.info(f"  Cached Board: {len(players)} players")
            
            # =================================================================
            # PHASE 2: GLOBAL SCORING (No Early Exits)
            # =================================================================
            logger.info("[PHASE 2] Global Scoring - Processing EVERY prop...")
            
            discarded = []
            all_scored_props = []  # ALL props that pass kill switch
            
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
                    edge = calculate_absolute_edge(sharp_price)
                    
                    if edge < (MIN_ABSOLUTE_EDGE * 100):
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"EDGE: {edge:.1f}% < 5%",
                            "sharp_price": sharp_price
                        })
                        results["kill_switch"]["edge_too_small"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # KILL SWITCH 2: Median Anchor
                    # ---------------------------------------------------------
                    stat_type = prop.get("stat_type", "").upper()
                    pp_line = prop.get("line", 0)
                    season_median = player_season_medians.get(stat_type)
                    
                    if season_median is not None and pp_line > season_median:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"MEDIAN: Line {pp_line} > Median {season_median}",
                            "sharp_price": sharp_price
                        })
                        results["kill_switch"]["above_median"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # CALCULATE POWER SCORE COMPONENTS
                    # ---------------------------------------------------------
                    hit_rates = prop.get("hit_rates", {})
                    
                    if "l10_rate" in hit_rates:
                        l10_rate = (hit_rates.get("l10_rate") or 0)
                        l5_rate = (hit_rates.get("l5_rate") or 0)
                        l10_hits = hit_rates.get("l10_hit_count") or 0
                    else:
                        l10_data = hit_rates.get("l10", {})
                        l10_rate = (l10_data.get("hit_rate", 0) * 100) if isinstance(l10_data, dict) else 0
                        l10_hits = l10_data.get("games_over", 0) if isinstance(l10_data, dict) else 0
                        l5_rate = 0
                    
                    # Calculate cushion (line below median)
                    cushion = calculate_cushion(pp_line, season_median) if season_median else 0
                    
                    # Calculate form (L10 hit rate)
                    form = calculate_form(l10_rate)
                    
                    # FERRARI POWER SCORE
                    power_score = calculate_power_score(edge, cushion, form)
                    
                    # Line delta
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = calculate_line_delta(pp_line, anchor_line)
                    
                    # L10 stats
                    stat_values = player_l10_stats.get(stat_type, [])
                    l10_mode = calculate_mode(stat_values)
                    l10_median = calculate_median(stat_values)
                    l10_mean = calculate_mean(stat_values)
                    
                    # Determine tier
                    if sharp_price <= SAFE_HAVEN_MAX:
                        tier = "safe_haven"
                        results["scored"]["safe_haven"] += 1
                    elif FRONT_LINES_MIN <= sharp_price <= FRONT_LINES_MAX:
                        tier = "front_lines"
                        results["scored"]["front_lines"] += 1
                    elif WAR_ZONE_MIN <= sharp_price <= WAR_ZONE_MAX:
                        tier = "war_zone"
                        results["scored"]["war_zone"] += 1
                    else:
                        tier = "unclassified"
                    
                    # Build scored prop
                    scored_prop = self._build_scored_prop(
                        player, prop, sharp_market,
                        edge, cushion, form, power_score,
                        line_delta, season_median,
                        l10_rate, l5_rate, l10_hits,
                        l10_mode, l10_median, l10_mean,
                        tier, sync_time
                    )
                    
                    all_scored_props.append(scored_prop)
            
            results["kill_switch"]["total_killed"] = (
                results["kill_switch"]["edge_too_small"] +
                results["kill_switch"]["above_median"] +
                results["kill_switch"]["no_sharp_data"]
            )
            results["scored"]["total"] = len(all_scored_props)
            
            logger.info(f"  Total Scanned: {results['total_scanned']}")
            logger.info(f"  Killed: {results['kill_switch']['total_killed']}")
            logger.info(f"  Scored: {results['scored']['total']}")
            
            # =================================================================
            # PHASE 3: BULK WRITE ALL SCORED PROPS
            # =================================================================
            logger.info("[PHASE 3] Bulk writing all scored props...")
            
            await self.ferrari_scored.delete_many({})
            if all_scored_props:
                # Bulk insert for scaling
                await self.ferrari_scored.insert_many(all_scored_props)
            
            # =================================================================
            # PHASE 4: DYNAMIC RANKING (Global Sort → Top-K Selection)
            # =================================================================
            logger.info("[PHASE 4] Dynamic Ranking - Global sort by power score...")
            
            used_players = set()
            
            # SAFE HAVEN: Query ALL, sort by power_score, then limit
            safe_haven_cursor = self.ferrari_scored.find(
                {"tier": "safe_haven"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            
            safe_haven_all = await safe_haven_cursor.to_list(length=None)
            top_safe_haven = self._dedupe_select(safe_haven_all, used_players, MAX_PICKS_PER_TIER)
            
            # FRONT LINES: Query ALL, sort by power_score, then limit
            front_lines_cursor = self.ferrari_scored.find(
                {"tier": "front_lines"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            
            front_lines_all = await front_lines_cursor.to_list(length=None)
            top_front_lines = self._dedupe_select(front_lines_all, used_players, MAX_PICKS_PER_TIER)
            
            # WAR ZONE: Query ALL, sort by power_score, then limit
            war_zone_cursor = self.ferrari_scored.find(
                {"tier": "war_zone"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            
            war_zone_all = await war_zone_cursor.to_list(length=None)
            top_war_zone = self._dedupe_select(war_zone_all, used_players, MAX_PICKS_PER_TIER)
            
            # =================================================================
            # PHASE 5: STORE FINAL SELECTIONS
            # =================================================================
            logger.info("[PHASE 5] Storing final Top-10 selections...")
            
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
                await self.ferrari_discarded.insert_many(discarded[:500])  # Keep more for debugging
            
            results["output"]["safe_haven"] = len(top_safe_haven)
            results["output"]["front_lines"] = len(top_front_lines)
            results["output"]["war_zone"] = len(top_war_zone)
            results["output"]["total"] = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            
            logger.info("=" * 70)
            logger.info("[FERRARI v5] PIPELINE COMPLETE")
            logger.info(f"  Scanned: {results['total_scanned']} props")
            logger.info(f"  Scored: {results['scored']['total']} props")
            logger.info(f"  Selected: {results['output']['total']}/30")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[v5] Pipeline error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["error"] = str(e)
        
        return results
    
    def _build_scored_prop(
        self,
        player: Dict,
        prop: Dict,
        sharp_market: Dict,
        edge: float,
        cushion: float,
        form: float,
        power_score: float,
        line_delta: float,
        season_median: Optional[float],
        l10_rate: float,
        l5_rate: float,
        l10_hits: int,
        l10_mode: Optional[float],
        l10_median: Optional[float],
        l10_mean: Optional[float],
        tier: str,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Build scored prop with all metrics."""
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
            # POWER SCORE COMPONENTS
            "edge": round(edge, 1),
            "cushion": round(cushion, 1),
            "form": round(form, 1),
            "ferrari_power_score": round(power_score, 2),
            # Other metrics
            "line_delta": round(line_delta, 1),
            "season_median": round(season_median, 1) if season_median else None,
            # Hit Rates
            "l10_rate": round(l10_rate, 1),
            "l5_rate": round(l5_rate, 1),
            "h10_rate": round(l10_rate, 1),
            "h5_rate": round(l5_rate, 1),
            "l10_hits": l10_hits,
            # Averages
            "l5_avg": hit_rates.get("l5_avg"),
            "l10_avg": hit_rates.get("l10_avg"),
            "season_avg": hit_rates.get("season_avg"),
            # L10 Stats
            "l10_mode": round(l10_mode, 1) if l10_mode else None,
            "l10_median": round(l10_median, 1) if l10_median else None,
            "l10_mean": round(l10_mean, 1) if l10_mean else None,
            # Classification
            "tier": tier,
            # Metadata
            "synced_at": sync_time.isoformat(),
            "pipeline": "ferrari_v5"
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
    # GETTER METHODS (Include total_scanned for verification)
    # =========================================================================
    
    async def _get_scan_stats(self) -> Dict[str, int]:
        """Get scanning statistics for verification display."""
        total_scored = await self.ferrari_scored.count_documents({})
        safe_haven_count = await self.ferrari_scored.count_documents({"tier": "safe_haven"})
        front_lines_count = await self.ferrari_scored.count_documents({"tier": "front_lines"})
        war_zone_count = await self.ferrari_scored.count_documents({"tier": "war_zone"})
        
        return {
            "total_scored": total_scored,
            "safe_haven_pool": safe_haven_count,
            "front_lines_pool": front_lines_count,
            "war_zone_pool": war_zone_count
        }
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_scan_stats()
        
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp <= {SAFE_HAVEN_MAX}",
            "pool_size": stats["safe_haven_pool"],
            "total_scored": stats["total_scored"],
            "formula": "Power Score = (Edge×0.4) + (Cushion×0.3) + (Form×0.3)"
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_scan_stats()
        
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {FRONT_LINES_MIN} to {FRONT_LINES_MAX}",
            "pool_size": stats["front_lines_pool"],
            "total_scored": stats["total_scored"],
            "formula": "Power Score = (Edge×0.4) + (Cushion×0.3) + (Form×0.3)"
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_scan_stats()
        
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {WAR_ZONE_MIN} to +{WAR_ZONE_MAX}",
            "pool_size": stats["war_zone_pool"],
            "total_scored": stats["total_scored"],
            "formula": "Power Score = (Edge×0.4) + (Cushion×0.3) + (Form×0.3)"
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        cursor = self.ferrari_discarded.find({}, {"_id": 0}).limit(limit)
        discarded = await cursor.to_list(length=limit)
        total_discarded = await self.ferrari_discarded.count_documents({})
        
        return {
            "discarded": discarded,
            "count": len(discarded),
            "total_discarded": total_discarded,
            "kill_switch": "5% absolute edge + Median Anchor"
        }


# Singleton
_ferrari_service = None

def get_ferrari_tier_service(db=None):
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = FerrariTierService(db)
    return _ferrari_service
