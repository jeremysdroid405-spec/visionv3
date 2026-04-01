"""
Ferrari Tier Service
====================
"Best of the Best" filtering using Bovada as the sharp benchmark.

Global 15% Separation Kill-Switch:
- Uses implied probability formula to calculate separation
- Props with < 15% separation from sharp market are discarded entirely

Tiered Assignment:
1. SAFE HAVEN (Elite Goblins): Sharp price <= -250, line delta >= 1.5, L10 >= 70%
2. FRONT LINES (Battleground): Sharp price -149 to +110, 40-cent gap, L5 >= 60%
3. WAR ZONE (Elite Demons): Demons with sharp >= +500, 200pt separation, 2+ L10 hits
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Ferrari Tier Thresholds
GLOBAL_MIN_SEPARATION_PCT = 15.0  # 15% minimum implied probability separation

# DEAD ZONE: Props with sharp price between -137 and -148 are hidden from ALL dashboards
DEAD_ZONE_MIN = -148
DEAD_ZONE_MAX = -137

# Safe Haven Thresholds - "Too Safe" stuff lives here ONLY
SAFE_HAVEN_MAX_SHARP_PRICE = -250  # Sharp price must be <= -250
SAFE_HAVEN_MIN_L10_RATE = 0.70     # 70% L10 hit rate

# Front Lines Thresholds - EXCLUSIVE window: -149 to -245
FRONT_LINES_MIN_SHARP_PRICE = -245  # Sharp price floor (most negative allowed)
FRONT_LINES_MAX_SHARP_PRICE = -149  # Sharp price ceiling (least negative allowed)
FRONT_LINES_MIN_L10_RATE = 0.70     # 70%+ L10 hit rate preferred

# War Zone Thresholds - Elite Demons only
WAR_ZONE_MIN_SHARP_PRICE = 500      # Sharp price >= +500
WAR_ZONE_MIN_SEPARATION_PTS = 200   # Bovada 200+ points shorter
WAR_ZONE_MIN_L10_HITS = 2           # Hit at least 2 times in L10


def american_to_implied_probability(odds: int) -> float:
    """
    Convert American odds to implied probability.
    
    Examples:
        -200 → 66.67%
        +200 → 33.33%
        -137 → 57.8%
        +100 → 50%
    """
    if odds is None:
        return 0.0
    
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_separation_pct(pp_price: int, sharp_price: int) -> float:
    """
    Calculate the separation percentage using implied probability.
    
    Formula: abs(PP_implied - Sharp_implied) / Sharp_implied * 100
    
    Example:
        PP -137 (57.8%) vs Sharp -200 (66.7%)
        Separation = |57.8 - 66.7| / 66.7 * 100 = 13.3%
    """
    if pp_price is None or sharp_price is None:
        return 0.0
    
    pp_implied = american_to_implied_probability(pp_price)
    sharp_implied = american_to_implied_probability(sharp_price)
    
    if sharp_implied == 0:
        return 0.0
    
    return abs(pp_implied - sharp_implied) / sharp_implied * 100


def calculate_price_gap(pp_price: int, sharp_price: int) -> int:
    """
    Calculate the raw price gap in American odds points.
    
    Example: PP -137 vs Sharp -177 = 40 cent gap
    """
    if pp_price is None or sharp_price is None:
        return 0
    
    return abs(pp_price - sharp_price)


class FerrariTierService:
    """
    Ferrari Tier Service - Elite filtering with Bovada separation benchmarks.
    """
    
    def __init__(self, db):
        self.db = db
        self.cached_board = db.dg_cached_board
        
        # Ferrari-specific collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded  # Props that failed the kill-switch
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Main entry point: Build all Ferrari tiers from cached_board.
        
        1. Apply global 15% separation kill-switch
        2. Classify remaining props into tiers
        3. Store in tier-specific collections
        """
        logger.info("[FERRARI] Building Ferrari tiers with Bovada separation...")
        
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
            # Fetch all players from cached_board
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=500)
            
            # Track all props and apply global filter
            qualified_props = []
            discarded_props = []
            
            safe_haven_candidates = []
            front_lines_candidates = []
            war_zone_candidates = []
            
            for player in players:
                player_name = player.get("player_name", "")
                
                for prop in player.get("props", []):
                    results["total_props_scanned"] += 1
                    
                    # Get sharp market data
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    bovada_price = sharp_market.get("bovada_price")
                    dk_fd_avg = sharp_market.get("dk_fd_average")
                    
                    pp_price = prop.get("price", -137)
                    
                    # Skip props without sharp data
                    if sharp_price is None:
                        continue
                    
                    # DEAD ZONE CHECK: Props with sharp price -137 to -148 are HIDDEN
                    if DEAD_ZONE_MIN <= sharp_price <= DEAD_ZONE_MAX:
                        discarded_props.append({
                            "player_name": player_name,
                            "prop": prop,
                            "reason": f"Dead Zone: Sharp {sharp_price} is between {DEAD_ZONE_MIN} and {DEAD_ZONE_MAX}",
                            "separation_pct": 0,
                            "pp_price": pp_price,
                            "sharp_price": sharp_price
                        })
                        continue
                    
                    # GLOBAL KILL-SWITCH: 15% separation requirement
                    separation_pct = calculate_separation_pct(pp_price, sharp_price)
                    
                    if separation_pct < GLOBAL_MIN_SEPARATION_PCT:
                        discarded_props.append({
                            "player_name": player_name,
                            "prop": prop,
                            "reason": f"Separation {separation_pct:.1f}% < {GLOBAL_MIN_SEPARATION_PCT}%",
                            "separation_pct": round(separation_pct, 1),
                            "pp_price": pp_price,
                            "sharp_price": sharp_price
                        })
                        continue
                    
                    # Prop passed global filter
                    qualified_props.append((player, prop, sharp_market, separation_pct))
                    
                    # Extract hit rates - handle both formats
                    hit_rates = prop.get("hit_rates", {})
                    
                    # Format 1: Nested structure {l10: {hit_rate: 0.7}}
                    # Format 2: Flat structure {l10_rate: 70}
                    
                    # Check if it's flat format (has l10_rate key)
                    if "l10_rate" in hit_rates:
                        # Flat format - rate is already a percentage (70 means 70%)
                        l10_rate = (hit_rates.get("l10_rate") or 0) / 100.0
                        l5_rate = (hit_rates.get("l5_rate") or 0) / 100.0
                        l10_hits = hit_rates.get("l10_hit_count") or 0
                    else:
                        # Nested format
                        l10_data = hit_rates.get("l10", {})
                        l5_data = hit_rates.get("l5", {})
                        l10_rate = l10_data.get("hit_rate", 0) if isinstance(l10_data, dict) else 0
                        l5_rate = l5_data.get("hit_rate", 0) if isinstance(l5_data, dict) else 0
                        l10_hits = l10_data.get("games_over", 0) if isinstance(l10_data, dict) else 0
                    
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    is_alternate = sharp_market.get("is_alternate", False)
                    
                    line = prop.get("line", 0)
                    price_gap = calculate_price_gap(pp_price, sharp_price)
                    
                    # Build candidate object
                    candidate = self._build_candidate(
                        player, prop, sharp_market, 
                        separation_pct, price_gap,
                        l10_rate, l5_rate, l10_hits,
                        sync_time
                    )
                    
                    # TIER CLASSIFICATION
                    
                    # SAFE HAVEN: Elite Goblins
                    if self._qualifies_safe_haven(
                        sharp_price, bovada_price, line, l10_rate, is_goblin
                    ):
                        safe_haven_candidates.append(candidate)
                        results["safe_haven"]["candidates"] += 1
                    
                    # FRONT LINES: Battleground (exclusive -149 to -245 window)
                    elif self._qualifies_front_lines(
                        sharp_price, price_gap, l10_rate, pp_price
                    ):
                        front_lines_candidates.append(candidate)
                        results["front_lines"]["candidates"] += 1
                    
                    # WAR ZONE: Elite Demons
                    elif self._qualifies_war_zone(
                        sharp_price, pp_price, l10_hits, is_demon
                    ):
                        war_zone_candidates.append(candidate)
                        results["war_zone"]["candidates"] += 1
            
            results["props_discarded"] = len(discarded_props)
            results["props_qualified"] = len(qualified_props)
            
            # Sort and store each tier WITH CROSS-TIER DEDUPLICATION
            # A player can only appear in ONE tier (priority: Safe Haven > Front Lines > War Zone)
            
            used_players = set()
            
            # SAFE HAVEN: Sort by most negative sharp_price (strongest locks)
            safe_haven_candidates.sort(key=lambda x: x.get("sharp_price", 0))
            top_safe_haven = self._dedupe_picks(safe_haven_candidates)[:10]
            used_players.update(p.get("player_name") for p in top_safe_haven)
            
            # FRONT LINES: Sort by highest L10 hit rate (exclude Safe Haven players)
            front_lines_candidates.sort(key=lambda x: x.get("l10_rate", 0), reverse=True)
            front_lines_filtered = [p for p in front_lines_candidates if p.get("player_name") not in used_players]
            top_front_lines = self._dedupe_picks(front_lines_filtered)[:10]
            used_players.update(p.get("player_name") for p in top_front_lines)
            
            # WAR ZONE: Sort by highest sharp_price (exclude Safe Haven + Front Lines players)
            war_zone_candidates.sort(key=lambda x: x.get("sharp_price", 0), reverse=True)
            war_zone_filtered = [p for p in war_zone_candidates if p.get("player_name") not in used_players]
            top_war_zone = self._dedupe_picks(war_zone_filtered)[:10]
            
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
            
            # Store discarded props for debugging
            await self.ferrari_discarded.delete_many({})
            if discarded_props:
                await self.ferrari_discarded.insert_many(discarded_props[:100])  # Keep top 100
            
            logger.info(
                f"[FERRARI] Complete: "
                f"Scanned={results['total_props_scanned']}, "
                f"Discarded={results['props_discarded']}, "
                f"Safe Haven={results['safe_haven']['count']}, "
                f"Front Lines={results['front_lines']['count']}, "
                f"War Zone={results['war_zone']['count']}"
            )
            
        except Exception as e:
            logger.error(f"[FERRARI] Error building tiers: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["error"] = str(e)
        
        return results
    
    def _qualifies_safe_haven(
        self,
        sharp_price: Optional[int],
        bovada_price: Optional[int],
        line: float,
        l10_rate: float,
        is_goblin: bool
    ) -> bool:
        """
        SAFE HAVEN qualification - EXCLUSIVE WINDOW:
        - Sharp price <= -250 (the "too safe" stuff lives here ONLY)
        - L10 hit rate >= 70%
        
        IMPORTANT: Props with sharp -360 MUST go here, NOT Front Lines
        """
        if sharp_price is None:
            return False
        
        # Must have 70%+ L10 hit rate
        if l10_rate < SAFE_HAVEN_MIN_L10_RATE:
            return False
        
        # EXCLUSIVE: Sharp price <= -250 (heavy favorite on sharp book)
        # This BANS -360 from Front Lines
        if sharp_price <= SAFE_HAVEN_MAX_SHARP_PRICE:
            return True
        
        return False
    
    def _qualifies_front_lines(
        self,
        sharp_price: Optional[int],
        price_gap: int,
        l10_rate: float,
        pp_price: int
    ) -> bool:
        """
        FRONT LINES qualification - EXCLUSIVE WINDOW:
        - Sharp price between -149 and -245 (NO ultra-safe goblins!)
        - L10 hit rate >= 70% (consistency is king)
        
        BANNED: Props with sharp <= -250 (those go to Safe Haven)
        BANNED: Props with sharp > -149 (dead zone or positive)
        """
        if sharp_price is None:
            return False
        
        # EXCLUSIVE Sharp price window: -245 to -149
        # This ensures NO leakage from Safe Haven (which is <= -250)
        if not (FRONT_LINES_MIN_SHARP_PRICE <= sharp_price <= FRONT_LINES_MAX_SHARP_PRICE):
            return False
        
        # L10 consistency check (70%+ preferred)
        if l10_rate < FRONT_LINES_MIN_L10_RATE:
            return False
        
        return True
    
    def _qualifies_war_zone(
        self,
        sharp_price: Optional[int],
        pp_price: int,
        l10_hits: int,
        is_demon: bool
    ) -> bool:
        """
        WAR ZONE qualification:
        - Must be a demon (PP even odds / +100)
        - Sharp price >= +500
        - Bovada at least 200 points shorter than PP implied
        - Hit at least 2 times in L10
        """
        if not is_demon:
            return False
        
        if sharp_price is None:
            return False
        
        # Sharp price >= +500
        if sharp_price < WAR_ZONE_MIN_SHARP_PRICE:
            return False
        
        # Safety check: hit at least 2 in L10
        if l10_hits < WAR_ZONE_MIN_L10_HITS:
            return False
        
        # Separation check: Bovada should be 200+ pts shorter
        # If PP pays +1000 and Bovada says +800, that's 200pt edge
        # Sharp price being +500 when demon is +100 means huge edge
        price_diff = sharp_price - 100  # Demon is always +100
        if price_diff < WAR_ZONE_MIN_SEPARATION_PTS:
            return False
        
        return True
    
    def _build_candidate(
        self,
        player: Dict,
        prop: Dict,
        sharp_market: Dict,
        separation_pct: float,
        price_gap: int,
        l10_rate: float,
        l5_rate: float,
        l10_hits: int,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Build a standardized candidate object for tier storage."""
        return {
            "player_name": player.get("player_name"),
            "player_id": player.get("player_id"),
            "team": player.get("team"),
            "team_name": player.get("team_name"),
            "photo_url": player.get("photo_url") or player.get("headshot_url"),
            "headshot_url": player.get("headshot_url"),
            "nba_id": player.get("nba_id"),
            "position": player.get("position"),
            "opponent": player.get("opponent"),
            "opponent_abbr": player.get("opponent_abbr"),
            "game_time": player.get("game_time"),
            # Prop details
            "stat_type": prop.get("stat_type"),
            "market": prop.get("market"),
            "direction": prop.get("direction"),
            "line": prop.get("line"),
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
            # Separation metrics
            "separation_pct": round(separation_pct, 1),
            "price_gap": price_gap,
            # Hit rates
            "l10_rate": round(l10_rate * 100, 1),
            "l5_rate": round(l5_rate * 100, 1),
            "l10_hits": l10_hits,
            "hit_rates": prop.get("hit_rates", {}),
            # Metadata
            "synced_at": sync_time.isoformat(),
            "is_ferrari_pick": True
        }
    
    def _dedupe_picks(self, picks: List[Dict]) -> List[Dict]:
        """De-duplicate picks: one pick per player."""
        seen = set()
        unique = []
        for pick in picks:
            name = pick.get("player_name")
            if name and name not in seen:
                seen.add(name)
                unique.append(pick)
        return unique
    
    # ==================== GETTER METHODS ====================
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari Safe Haven picks."""
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"<= {SAFE_HAVEN_MAX_SHARP_PRICE}",
                "l10_rate": f">= {SAFE_HAVEN_MIN_L10_RATE * 100}%",
                "description": "Too Safe - Ultra-high probability locks"
            }
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari Front Lines picks."""
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f"{FRONT_LINES_MIN_SHARP_PRICE} to {FRONT_LINES_MAX_SHARP_PRICE}",
                "l10_rate": f">= {FRONT_LINES_MIN_L10_RATE * 100}%",
                "description": "Battleground - Consistent 70%+ picks with edge"
            }
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        """Get Ferrari War Zone picks."""
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "thresholds": {
                "sharp_price": f">= +{WAR_ZONE_MIN_SHARP_PRICE}",
                "separation": f">= {WAR_ZONE_MIN_SEPARATION_PTS} pts",
                "l10_hits": f">= {WAR_ZONE_MIN_L10_HITS}",
                "description": "Elite Demons - High payout longshots"
            }
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        """Get props that were discarded by the kill-switch or dead zone."""
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
