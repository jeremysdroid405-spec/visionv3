"""
Ferrari+ 5-Phase Pick Selection Pipeline
=========================================
"Best of the Best" filtering using the exact methodology:

PHASE 1: Data Sourcing (Odds API + BDL + DVP + AI Context)
PHASE 2: Global Kill Switch (15% Implied Probability Separation)
PHASE 3: Tier Classification (70-80-40 Thresholds)
PHASE 4: Sorting (Line Delta Primary)
PHASE 5: Deduplication (One Player Per Tier, 30 Total Plays)

Formula: Separation = (Implied_Sharp - Implied_PP) / Implied_PP
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# PHASE 2: GLOBAL KILL SWITCH THRESHOLDS
# =============================================================================
GLOBAL_MIN_SEPARATION_PCT = 15.0  # 15% minimum separation

# DEAD ZONE: No real market separation
DEAD_ZONE_MIN = -148
DEAD_ZONE_MAX = -137

# =============================================================================
# PHASE 3: TIER CLASSIFICATION THRESHOLDS (70-80-40)
# =============================================================================

# SAFE HAVEN: Elite Goblins
SAFE_HAVEN_MAX_SHARP_PRICE = -250   # Sharp price <= -250
SAFE_HAVEN_MIN_L10_RATE = 0.80      # 80% L10 hit rate

# FRONT LINES: Battleground (DVP-targeted)
FRONT_LINES_MIN_SHARP_PRICE = -245  # Sharp price floor
FRONT_LINES_MAX_SHARP_PRICE = -149  # Sharp price ceiling
FRONT_LINES_MAX_DVP_RANK = 10       # DVP Rank <= 10 (weak defenses)
FRONT_LINES_MIN_L10_RATE = 0.70     # 70% L10 hit rate

# WAR ZONE: Elite Demons
WAR_ZONE_MIN_SHARP_PRICE = 500      # Sharp price >= +500
WAR_ZONE_MIN_AI_CONTEXT = 40        # AI Context > 40
WAR_ZONE_MIN_L10_HITS = 2           # Safety: 2+ hits in L10

# =============================================================================
# PHASE 5: OUTPUT LIMITS
# =============================================================================
MAX_PICKS_PER_TIER = 10  # Top 10 per tier = 30 total plays


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def american_to_implied_probability(odds: int) -> float:
    """
    Convert American odds to implied probability.
    
    -200 → 0.667 (66.7%)
    +200 → 0.333 (33.3%)
    -137 → 0.578 (57.8%)
    """
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_separation_pct(pp_price: int, sharp_price: int) -> float:
    """
    PHASE 2 FORMULA: Separation = (Implied_Sharp - Implied_PP) / Implied_PP × 100
    
    Positive separation = Sharp book thinks it's MORE likely than PP suggests
    This indicates VALUE on the over.
    
    Example:
        PP -137 (57.8%) vs Sharp -250 (71.4%)
        Separation = (0.714 - 0.578) / 0.578 × 100 = 23.5%
    """
    if pp_price is None or sharp_price is None:
        return 0.0
    
    pp_implied = american_to_implied_probability(pp_price)
    sharp_implied = american_to_implied_probability(sharp_price)
    
    if pp_implied == 0:
        return 0.0
    
    # Exact formula as specified
    separation = (sharp_implied - pp_implied) / pp_implied * 100
    return abs(separation)  # Use absolute for kill switch


def calculate_line_delta(pp_line: float, anchor_line: float) -> float:
    """
    Line Delta = PP Line - Anchor Line (standard line)
    
    Negative delta = PP line is BELOW standard (easier prop)
    Positive delta = PP line is ABOVE standard (harder prop)
    """
    if pp_line is None or anchor_line is None:
        return 0.0
    return pp_line - anchor_line


def calculate_mode(values: List[float]) -> Optional[float]:
    """Calculate mode (most frequent value) from last 10 games."""
    if not values:
        return None
    
    # Round to nearest 0.5 for grouping
    rounded = [round(v * 2) / 2 for v in values]
    counts = Counter(rounded)
    
    if not counts:
        return None
    
    most_common = counts.most_common(1)[0]
    mode_value, mode_count = most_common
    
    # Only return if appears 2+ times
    return mode_value if mode_count >= 2 else None


def calculate_median(values: List[float]) -> Optional[float]:
    """Calculate median (middle value) from sorted list."""
    if not values:
        return None
    
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    if n % 2 == 0:
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    return sorted_vals[n//2]


def calculate_mean(values: List[float]) -> Optional[float]:
    """Calculate mean (average) from list."""
    if not values:
        return None
    return sum(values) / len(values)


# =============================================================================
# FERRARI+ 5-PHASE PIPELINE SERVICE
# =============================================================================

class FerrariTierService:
    """
    Ferrari+ 5-Phase Pick Selection Pipeline
    
    Phase 1: Data Sourcing
    Phase 2: Global Kill Switch (15% Separation)
    Phase 3: Tier Classification (70-80-40 Thresholds)
    Phase 4: Sorting (Line Delta Primary)
    Phase 5: Deduplication (One Player Per Tier)
    """
    
    def __init__(self, db):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.dvp_rankings_col = db.dvp_rankings
        self.master_hub = db.nba_master_hub_2026
        self.player_stats = db.dg_player_stats
        
        # Output collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded
    
    # =========================================================================
    # PHASE 1: DATA SOURCING
    # =========================================================================
    
    async def _load_dvp_rankings(self) -> Dict[str, Dict[str, int]]:
        """Load DVP rankings: {stat_type: {team: rank}}"""
        try:
            doc = await self.dvp_rankings_col.find_one({"type": "dvp_rankings"})
            if doc:
                return doc.get("rankings", {})
        except Exception as e:
            logger.warning(f"[PHASE1] DVP rankings load failed: {e}")
        return {}
    
    async def _load_ai_context_scores(self) -> Dict[str, float]:
        """Load AI context scores: {player_name: score (0-100)}"""
        ai_cache = {}
        try:
            cursor = self.master_hub.find(
                {"ai_context_score": {"$exists": True}},
                {"_id": 0, "display_name": 1, "player_name": 1, "ai_context_score": 1}
            )
            async for doc in cursor:
                name = doc.get("display_name") or doc.get("player_name")
                if name:
                    score = doc.get("ai_context_score", 0.5)
                    # Convert 0-1 scale to 0-100
                    ai_cache[name] = score * 100 if score <= 1 else score
        except Exception as e:
            logger.warning(f"[PHASE1] AI context load failed: {e}")
        return ai_cache
    
    async def _load_player_game_stats(self) -> Dict[str, Dict[str, List[float]]]:
        """Load game logs for mode/median/mean calculation."""
        stats_cache = {}
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
                
                # Extract stat values
                stats_cache[player_name] = {
                    "PTS": [g["pts"] for g in sorted_games if g.get("pts") is not None],
                    "AST": [g["ast"] for g in sorted_games if g.get("ast") is not None],
                    "REB": [g["reb"] for g in sorted_games if g.get("reb") is not None],
                    "3PM": [g["fg3m"] for g in sorted_games if g.get("fg3m") is not None],
                    "BLK": [g["blk"] for g in sorted_games if g.get("blk") is not None],
                    "STL": [g["stl"] for g in sorted_games if g.get("stl") is not None],
                    "PRA": [
                        (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                        for g in sorted_games
                        if g.get("pts") is not None or g.get("reb") is not None or g.get("ast") is not None
                    ]
                }
        except Exception as e:
            logger.warning(f"[PHASE1] Game stats load failed: {e}")
        return stats_cache
    
    def _get_dvp_rank(self, dvp_rankings: Dict, opponent: str, stat_type: str) -> int:
        """Get DVP rank for opponent vs stat type. Lower = weaker defense."""
        if not dvp_rankings or not opponent or not stat_type:
            return 99
        
        stat_map = {
            "PTS": "PTS", "AST": "AST", "REB": "REB",
            "3PM": "3PM", "BLK": "BLK", "STL": "STL",
            "PRA": "PTS", "P+R": "PTS", "P+A": "PTS", "R+A": "REB"
        }
        normalized = stat_map.get(stat_type.upper(), "PTS")
        return dvp_rankings.get(normalized, {}).get(opponent, 99)
    
    # =========================================================================
    # MAIN PIPELINE: BUILD FERRARI TIERS
    # =========================================================================
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Execute the 5-Phase Pick Selection Pipeline.
        """
        logger.info("=" * 70)
        logger.info("[FERRARI+] 5-PHASE PICK SELECTION PIPELINE")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "pipeline": "5-Phase Ferrari+",
            "total_props_scanned": 0,
            "phase2_killed": 0,
            "phase3_qualified": {
                "safe_haven": 0,
                "front_lines": 0,
                "war_zone": 0
            },
            "phase5_output": {
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
            
            dvp_rankings = await self._load_dvp_rankings()
            ai_context_cache = await self._load_ai_context_scores()
            player_game_stats = await self._load_player_game_stats()
            
            logger.info(f"  DVP Rankings: {len(dvp_rankings)} stat types")
            logger.info(f"  AI Context: {len(ai_context_cache)} players")
            logger.info(f"  Game Stats: {len(player_game_stats)} players")
            
            # Load cached board
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=500)
            
            # =================================================================
            # PHASE 2 & 3: FILTER AND CLASSIFY
            # =================================================================
            logger.info("[PHASE 2] Applying Kill Switch (15% Separation)...")
            logger.info("[PHASE 3] Classifying into tiers (70-80-40)...")
            
            discarded = []
            safe_haven_candidates = []
            front_lines_candidates = []
            war_zone_candidates = []
            
            for player in players:
                player_name = player.get("player_name", "")
                opponent = player.get("opponent") or player.get("opponent_abbr", "")
                ai_score = ai_context_cache.get(player_name, 50)
                player_stats = player_game_stats.get(player_name, {})
                
                for prop in player.get("props", []):
                    results["total_props_scanned"] += 1
                    
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    pp_price = prop.get("price", -137)
                    
                    # Skip if no sharp data
                    if sharp_price is None:
                        continue
                    
                    # ---------------------------------------------------------
                    # PHASE 2: KILL SWITCH
                    # ---------------------------------------------------------
                    
                    # Dead Zone Check
                    if DEAD_ZONE_MIN <= sharp_price <= DEAD_ZONE_MAX:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"DEAD_ZONE: Sharp {sharp_price}",
                            "sharp_price": sharp_price
                        })
                        results["phase2_killed"] += 1
                        continue
                    
                    # 15% Separation Check
                    separation = calculate_separation_pct(pp_price, sharp_price)
                    if separation < GLOBAL_MIN_SEPARATION_PCT:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"SEPARATION: {separation:.1f}% < 15%",
                            "sharp_price": sharp_price,
                            "separation_pct": round(separation, 1)
                        })
                        results["phase2_killed"] += 1
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
                        l5_rate = hit_rates.get("l5", {}).get("hit_rate", 0) if isinstance(hit_rates.get("l5"), dict) else 0
                    
                    stat_type = prop.get("stat_type", "")
                    dvp_rank = self._get_dvp_rank(dvp_rankings, opponent, stat_type)
                    
                    pp_line = prop.get("line", 0)
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = calculate_line_delta(pp_line, anchor_line)
                    
                    # Mode/Median/Mean
                    stat_values = player_stats.get(stat_type.upper(), [])
                    l10_mode = calculate_mode(stat_values)
                    l10_median = calculate_median(stat_values)
                    l10_mean = calculate_mean(stat_values)
                    
                    # Build candidate
                    candidate = self._build_candidate(
                        player, prop, sharp_market,
                        separation, line_delta,
                        l10_rate, l5_rate, l10_hits,
                        dvp_rank, ai_score,
                        l10_mode, l10_median, l10_mean,
                        sync_time
                    )
                    
                    # ---------------------------------------------------------
                    # PHASE 3: TIER CLASSIFICATION (70-80-40)
                    # ---------------------------------------------------------
                    
                    # SAFE HAVEN: Sharp <= -250 AND L10 >= 80%
                    if sharp_price <= SAFE_HAVEN_MAX_SHARP_PRICE:
                        if l10_rate >= SAFE_HAVEN_MIN_L10_RATE:
                            safe_haven_candidates.append(candidate)
                            results["phase3_qualified"]["safe_haven"] += 1
                    
                    # FRONT LINES: Sharp -245 to -149 AND DVP <= 10 AND L10 >= 70%
                    elif FRONT_LINES_MIN_SHARP_PRICE <= sharp_price <= FRONT_LINES_MAX_SHARP_PRICE:
                        if dvp_rank <= FRONT_LINES_MAX_DVP_RANK and l10_rate >= FRONT_LINES_MIN_L10_RATE:
                            front_lines_candidates.append(candidate)
                            results["phase3_qualified"]["front_lines"] += 1
                    
                    # WAR ZONE: Sharp >= +500 AND AI > 40 AND L10 Hits >= 2
                    elif sharp_price >= WAR_ZONE_MIN_SHARP_PRICE:
                        if ai_score > WAR_ZONE_MIN_AI_CONTEXT and l10_hits >= WAR_ZONE_MIN_L10_HITS:
                            war_zone_candidates.append(candidate)
                            results["phase3_qualified"]["war_zone"] += 1
            
            logger.info(f"  Phase 2 Killed: {results['phase2_killed']}")
            logger.info(f"  Phase 3 Qualified: SH={results['phase3_qualified']['safe_haven']}, "
                       f"FL={results['phase3_qualified']['front_lines']}, "
                       f"WZ={results['phase3_qualified']['war_zone']}")
            
            # =================================================================
            # PHASE 4: SORTING (Line Delta Primary)
            # =================================================================
            logger.info("[PHASE 4] Sorting by Line Delta...")
            
            # Sort each tier
            safe_haven_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), -x.get("l10_rate", 0))
            )
            front_lines_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), x.get("dvp_rank", 99), -x.get("l10_rate", 0))
            )
            war_zone_candidates.sort(
                key=lambda x: (-abs(x.get("line_delta", 0)), -x.get("ai_context_score", 0), -x.get("l10_rate", 0))
            )
            
            # =================================================================
            # PHASE 5: DEDUPLICATION (One Player Per Tier, 30 Total)
            # =================================================================
            logger.info("[PHASE 5] Deduplicating (One Player Per Tier)...")
            
            used_players = set()
            
            # Safe Haven: Top 10
            top_safe_haven = self._dedupe_and_select(
                safe_haven_candidates, used_players, MAX_PICKS_PER_TIER
            )
            
            # Front Lines: Top 10 (excluding Safe Haven players)
            top_front_lines = self._dedupe_and_select(
                front_lines_candidates, used_players, MAX_PICKS_PER_TIER
            )
            
            # War Zone: Top 10 (excluding Safe Haven + Front Lines players)
            top_war_zone = self._dedupe_and_select(
                war_zone_candidates, used_players, MAX_PICKS_PER_TIER
            )
            
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
                await self.ferrari_discarded.insert_many(discarded[:100])
            
            # Update results
            results["phase5_output"]["safe_haven"] = len(top_safe_haven)
            results["phase5_output"]["front_lines"] = len(top_front_lines)
            results["phase5_output"]["war_zone"] = len(top_war_zone)
            results["phase5_output"]["total"] = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            
            logger.info("=" * 70)
            logger.info(f"[FERRARI+] PIPELINE COMPLETE")
            logger.info(f"  Safe Haven: {len(top_safe_haven)}/{MAX_PICKS_PER_TIER}")
            logger.info(f"  Front Lines: {len(top_front_lines)}/{MAX_PICKS_PER_TIER}")
            logger.info(f"  War Zone: {len(top_war_zone)}/{MAX_PICKS_PER_TIER}")
            logger.info(f"  TOTAL: {results['phase5_output']['total']}/30")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[FERRARI+] Pipeline error: {e}")
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
        """Build standardized candidate object."""
        hit_rates = prop.get("hit_rates", {})
        
        return {
            # Player info
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
            # Sharp market
            "sharp_price": sharp_market.get("sharp_price"),
            "sharp_source": sharp_market.get("sharp_source"),
            "bovada_price": sharp_market.get("bovada_price"),
            "draftkings_price": sharp_market.get("draftkings_price"),
            "fanduel_price": sharp_market.get("fanduel_price"),
            "dk_fd_average": sharp_market.get("dk_fd_average"),
            # Ferrari+ metrics
            "separation_pct": round(separation_pct, 1),
            "line_delta": round(line_delta, 1),
            "dvp_rank": dvp_rank,
            "ai_context_score": round(ai_context_score, 1),
            # Hit rates
            "l10_rate": round(l10_rate * 100, 1),
            "l5_rate": round(l5_rate * 100, 1),
            "h10_rate": round(l10_rate * 100, 1),
            "h5_rate": round(l5_rate * 100, 1),
            "l10_hits": l10_hits,
            # Averages
            "l5_avg": hit_rates.get("l5_avg"),
            "l10_avg": hit_rates.get("l10_avg"),
            "season_avg": hit_rates.get("season_avg"),
            # Mode/Median/Mean
            "l10_mode": round(l10_mode, 1) if l10_mode is not None else None,
            "l10_median": round(l10_median, 1) if l10_median is not None else None,
            "l10_mean": round(l10_mean, 1) if l10_mean is not None else None,
            # Full hit_rates
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
        PHASE 5: One Player Per Tier deduplication.
        Players used in higher-priority tiers are excluded.
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
    
    # =========================================================================
    # GETTER METHODS
    # =========================================================================
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        """Get Safe Haven picks (Top 10)."""
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"<= {SAFE_HAVEN_MAX_SHARP_PRICE}",
                "l10_rate": f">= {int(SAFE_HAVEN_MIN_L10_RATE * 100)}%"
            }
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        """Get Front Lines picks (Top 10)."""
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"{FRONT_LINES_MIN_SHARP_PRICE} to {FRONT_LINES_MAX_SHARP_PRICE}",
                "dvp_rank": f"<= {FRONT_LINES_MAX_DVP_RANK}",
                "l10_rate": f">= {int(FRONT_LINES_MIN_L10_RATE * 100)}%"
            }
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        """Get War Zone picks (Top 10)."""
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f">= +{WAR_ZONE_MIN_SHARP_PRICE}",
                "ai_context": f"> {WAR_ZONE_MIN_AI_CONTEXT}",
                "l10_hits": f">= {WAR_ZONE_MIN_L10_HITS}"
            }
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        """Get props killed by Phase 2."""
        cursor = self.ferrari_discarded.find({}, {"_id": 0}).limit(limit)
        discarded = await cursor.to_list(length=limit)
        return {
            "discarded": discarded,
            "count": len(discarded),
            "kill_switch": f"{GLOBAL_MIN_SEPARATION_PCT}% separation",
            "dead_zone": f"{DEAD_ZONE_MIN} to {DEAD_ZONE_MAX}"
        }


# Singleton
_ferrari_service = None

def get_ferrari_tier_service(db=None):
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = FerrariTierService(db)
    return _ferrari_service
