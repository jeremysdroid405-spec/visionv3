"""
Probability Score Service
==========================
Calculates comprehensive probability scores for picks using:
- Hit Rate (L10, L5) - Primary factor
- DvP Matchup - Defense vs Position rankings
- Context Badges - Modifiers based on player situation
- Line Value - Gap between line and averages

The goal is to rank picks by their TRUE statistical probability of hitting.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Badge modifiers - positive boosts probability, negative hurts it
# Values are additive percentage points
BADGE_MODIFIERS = {
    # POSITIVE BADGES (increase probability)
    "home_cookin": +5.0,      # Home advantage
    "revenge": +4.0,          # Extra motivation vs former team
    "milestone": +3.0,        # Chasing records = higher effort
    "pay_day": +3.0,          # Contract year = motivated
    "locked_in": +4.0,        # High performance mode
    
    # NEGATIVE BADGES (decrease probability)
    "jet_lag": -4.0,          # Travel fatigue
    "gassed": -5.0,           # Back-to-back fatigue
    "legal_noise": -3.0,      # Off-court distractions
    "distraction": -3.0,      # Trade rumors/drama
    "deep_water": +2.0,       # Playoff pressure - usually positive for stars
}

# DvP rank thresholds and modifiers
# Rank 1-10 = Elite defense, Rank 21-30 = Poor defense
DVP_MODIFIERS = {
    "elite": -8.0,     # Ranks 1-5: Elite defense
    "strong": -4.0,    # Ranks 6-10: Strong defense  
    "above_avg": -2.0, # Ranks 11-15: Above average
    "below_avg": +2.0, # Ranks 16-20: Below average
    "weak": +4.0,      # Ranks 21-25: Weak defense
    "poor": +8.0,      # Ranks 26-30: Poor defense (favorable)
}


def get_dvp_modifier_from_rank(dvp_rank: Optional[int]) -> float:
    """
    Convert DvP rank to a probability modifier.
    
    Args:
        dvp_rank: 1-30 where 1 is best defense, 30 is worst
    
    Returns:
        Modifier in percentage points (-8 to +8)
    """
    if not dvp_rank or dvp_rank < 1 or dvp_rank > 30:
        return 0.0
    
    if dvp_rank <= 5:
        return DVP_MODIFIERS["elite"]
    elif dvp_rank <= 10:
        return DVP_MODIFIERS["strong"]
    elif dvp_rank <= 15:
        return DVP_MODIFIERS["above_avg"]
    elif dvp_rank <= 20:
        return DVP_MODIFIERS["below_avg"]
    elif dvp_rank <= 25:
        return DVP_MODIFIERS["weak"]
    else:
        return DVP_MODIFIERS["poor"]


def get_badge_modifier(badges: List[Dict[str, Any]]) -> float:
    """
    Calculate total badge modifier from list of badges.
    
    Args:
        badges: List of badge dictionaries with 'badge_key' field
    
    Returns:
        Total modifier in percentage points
    """
    if not badges:
        return 0.0
    
    total_modifier = 0.0
    for badge in badges:
        badge_key = badge.get("badge_key", "")
        modifier = BADGE_MODIFIERS.get(badge_key, 0.0)
        total_modifier += modifier
        
        if modifier != 0:
            logger.debug(f"[PROB_SCORE] Badge '{badge_key}' adds {modifier:+.1f}%")
    
    # Cap total badge modifier to prevent extreme swings
    total_modifier = max(-15.0, min(15.0, total_modifier))
    return total_modifier


def get_line_value_modifier(
    line: float,
    l10_avg: Optional[float],
    l5_avg: Optional[float],
    season_avg: Optional[float],
    direction: str = "over"
) -> float:
    """
    Calculate modifier based on how line compares to averages.
    
    For OVER bets:
    - Line well below average = positive modifier (easier to hit)
    - Line above average = negative modifier (harder to hit)
    
    Args:
        line: The betting line
        l10_avg: Last 10 games average
        l5_avg: Last 5 games average
        season_avg: Season average
        direction: "over" or "under"
    
    Returns:
        Modifier in percentage points
    """
    # Use L5 average as primary, fall back to L10, then season
    reference_avg = l5_avg or l10_avg or season_avg
    
    if not reference_avg or reference_avg == 0:
        return 0.0
    
    # Calculate gap as percentage of average
    gap_pct = ((reference_avg - line) / reference_avg) * 100
    
    # For OVER bets, positive gap (line below avg) is good
    # For UNDER bets, negative gap (line above avg) is good
    if direction.lower() == "under":
        gap_pct = -gap_pct
    
    # Convert gap to modifier
    # Gap of 10% below line = +5% to probability
    # Gap of 10% above line = -5% to probability
    modifier = gap_pct * 0.5
    
    # Cap the modifier
    modifier = max(-10.0, min(10.0, modifier))
    
    return round(modifier, 1)


def calculate_probability_score(
    hit_rate_l10: Optional[float],
    hit_rate_l5: Optional[float] = None,
    dvp_rank: Optional[int] = None,
    badges: Optional[List[Dict]] = None,
    line: Optional[float] = None,
    l5_avg: Optional[float] = None,
    l10_avg: Optional[float] = None,
    season_avg: Optional[float] = None,
    direction: str = "over"
) -> Dict[str, Any]:
    """
    Calculate comprehensive probability score for a pick.
    
    The score combines:
    1. Base Hit Rate (L10 weighted 60%, L5 weighted 40%)
    2. DvP Matchup modifier
    3. Badge modifiers
    4. Line value modifier
    
    Args:
        hit_rate_l10: L10 hit rate (0-100)
        hit_rate_l5: L5 hit rate (0-100)
        dvp_rank: Opponent's DvP rank for this stat (1-30)
        badges: List of active badges
        line: Betting line
        l5_avg: L5 average
        l10_avg: L10 average  
        season_avg: Season average
        direction: "over" or "under"
    
    Returns:
        Dict with:
        - probability_score: Final adjusted probability (0-100)
        - base_score: Raw hit rate based score
        - dvp_modifier: DvP adjustment
        - badge_modifier: Badge adjustment
        - line_modifier: Line value adjustment
        - breakdown: Detailed breakdown of scoring
    """
    # Start with base hit rate
    if hit_rate_l10 is None and hit_rate_l5 is None:
        return {
            "probability_score": 0,
            "base_score": 0,
            "dvp_modifier": 0,
            "badge_modifier": 0,
            "line_modifier": 0,
            "breakdown": {"error": "No hit rate data"}
        }
    
    # Weight L10 at 60%, L5 at 40% (L10 is more stable, L5 shows recent form)
    l10 = hit_rate_l10 or 0
    l5 = hit_rate_l5 or l10  # Fall back to L10 if no L5
    
    base_score = (l10 * 0.60) + (l5 * 0.40)
    
    # Calculate modifiers
    dvp_mod = get_dvp_modifier_from_rank(dvp_rank)
    badge_mod = get_badge_modifier(badges or [])
    line_mod = get_line_value_modifier(line, l10_avg, l5_avg, season_avg, direction) if line else 0
    
    # Apply modifiers to base score
    adjusted_score = base_score + dvp_mod + badge_mod + line_mod
    
    # Clamp to valid probability range
    final_score = max(0, min(100, adjusted_score))
    
    breakdown = {
        "base_hit_rate": {
            "l10": l10,
            "l5": l5,
            "weighted": round(base_score, 1)
        },
        "dvp_adjustment": {
            "rank": dvp_rank,
            "modifier": dvp_mod,
            "label": _get_dvp_label(dvp_rank)
        },
        "badge_adjustment": {
            "active_badges": [b.get("badge_key") for b in (badges or [])],
            "modifier": badge_mod
        },
        "line_adjustment": {
            "line": line,
            "reference_avg": l5_avg or l10_avg or season_avg,
            "modifier": line_mod
        },
        "final_formula": f"{base_score:.1f} + {dvp_mod:+.1f} (DvP) + {badge_mod:+.1f} (badges) + {line_mod:+.1f} (line) = {final_score:.1f}"
    }
    
    return {
        "probability_score": round(final_score, 1),
        "base_score": round(base_score, 1),
        "dvp_modifier": round(dvp_mod, 1),
        "badge_modifier": round(badge_mod, 1),
        "line_modifier": round(line_mod, 1),
        "breakdown": breakdown
    }


def _get_dvp_label(rank: Optional[int]) -> str:
    """Get human-readable DvP label from rank."""
    if not rank:
        return "Unknown"
    if rank <= 5:
        return "Elite Defense"
    elif rank <= 10:
        return "Strong Defense"
    elif rank <= 15:
        return "Above Average"
    elif rank <= 20:
        return "Below Average"
    elif rank <= 25:
        return "Weak Defense"
    else:
        return "Poor Defense"


class ProbabilityScoreService:
    """
    Service class for calculating probability scores with database access.
    Uses in-memory caching to avoid repeated DB queries.
    """
    
    # Class-level caches - shared across all instances
    _dvp_cache = {}  # team_stat -> rank
    _badges_cache = {}  # player_name -> badges
    _dvp_cache_loaded = False
    _badges_cache_loaded = False
    
    def __init__(self, db):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.dvp_rankings = db.dvp_rankings
    
    async def _preload_dvp_cache(self):
        """Pre-load all DvP rankings into memory."""
        if ProbabilityScoreService._dvp_cache_loaded:
            return
        
        cursor = self.dvp_rankings.find({}, {"_id": 0})
        docs = await cursor.to_list(50)
        
        for doc in docs:
            team = doc.get("team", "").upper()
            rankings = doc.get("rankings", {})
            for stat, rank in rankings.items():
                cache_key = f"{team}_{stat}"
                ProbabilityScoreService._dvp_cache[cache_key] = rank
        
        ProbabilityScoreService._dvp_cache_loaded = True
        logger.info(f"[PROB_CACHE] Loaded {len(ProbabilityScoreService._dvp_cache)} DvP rankings")
    
    async def _preload_badges_cache(self):
        """Pre-load all player badges into memory."""
        if ProbabilityScoreService._badges_cache_loaded:
            return
        
        cursor = self.master_hub.find(
            {"context_badges": {"$exists": True}},
            {"_id": 0, "display_name": 1, "context_badges": 1}
        )
        players = await cursor.to_list(6000)
        
        for player in players:
            name = player.get("display_name", "").lower().strip()
            badges = player.get("context_badges", [])
            if name and badges:
                ProbabilityScoreService._badges_cache[name] = badges
        
        ProbabilityScoreService._badges_cache_loaded = True
        logger.info(f"[PROB_CACHE] Loaded {len(ProbabilityScoreService._badges_cache)} player badges")
    
    async def get_dvp_rank(self, opponent: str, stat_type: str) -> Optional[int]:
        """
        Get DvP rank for opponent team and stat type.
        Uses in-memory cache for instant lookups.
        
        Args:
            opponent: Team abbreviation (e.g., "LAL")
            stat_type: Stat type (e.g., "PTS", "REB")
        
        Returns:
            Rank 1-30 or None if not found
        """
        if not opponent or not stat_type:
            return None
        
        # Ensure cache is loaded
        await self._preload_dvp_cache()
        
        # Normalize stat type
        stat_key = stat_type.upper().replace("PLAYER_", "").replace("_ALTERNATE", "")
        
        # Fast lookup from cache
        cache_key = f"{opponent.upper()}_{stat_key}"
        cached_rank = ProbabilityScoreService._dvp_cache.get(cache_key)
        if cached_rank is not None:
            return cached_rank
        
        # Fall back to hardcoded data (no DB query)
        from services.dvp_service import calculate_dvp_modifier
        modifier = calculate_dvp_modifier(opponent, stat_type)
        # Convert modifier (0-1) back to approximate rank
        rank = int(modifier * 29) + 1
        return rank
    
    async def get_player_badges(self, player_name: str) -> List[Dict[str, Any]]:
        """
        Get active badges for a player.
        Uses in-memory cache for instant lookups.
        
        Args:
            player_name: Player's display name
        
        Returns:
            List of badge dictionaries
        """
        if not player_name:
            return []
        
        # Ensure cache is loaded
        await self._preload_badges_cache()
        
        # Fast lookup from cache
        name_key = player_name.lower().strip()
        cached_badges = ProbabilityScoreService._badges_cache.get(name_key)
        if cached_badges is not None:
            return cached_badges
        
        # Return empty list if not in cache (no DB query)
        return []
    
    async def enrich_pick_with_probability(
        self,
        pick: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enrich a pick dictionary with probability score.
        
        Args:
            pick: Pick dictionary with player_name, stat_type, line, etc.
        
        Returns:
            Pick dictionary with added probability_score and prob_breakdown
        """
        player_name = pick.get("player_name")
        stat_type = pick.get("stat_type")
        opponent = pick.get("opponent")
        line = pick.get("line")
        direction = pick.get("direction", "over")
        
        # Get hit rates from pick
        hit_rate_l10 = pick.get("h10_rate") or pick.get("l10_hit_rate")
        hit_rate_l5 = pick.get("h5_rate") or pick.get("l5_hit_rate")
        l5_avg = pick.get("l5_avg")
        l10_avg = pick.get("l10_avg")
        season_avg = pick.get("season_avg")
        
        # Get DvP rank and badges
        dvp_rank = await self.get_dvp_rank(opponent, stat_type)
        badges = await self.get_player_badges(player_name)
        
        # Calculate probability score
        result = calculate_probability_score(
            hit_rate_l10=hit_rate_l10,
            hit_rate_l5=hit_rate_l5,
            dvp_rank=dvp_rank,
            badges=badges,
            line=line,
            l5_avg=l5_avg,
            l10_avg=l10_avg,
            season_avg=season_avg,
            direction=direction
        )
        
        # Add to pick
        pick["probability_score"] = result["probability_score"]
        pick["prob_breakdown"] = result["breakdown"]
        pick["dvp_rank"] = dvp_rank
        pick["dvp_label"] = _get_dvp_label(dvp_rank)
        pick["active_badges"] = [b.get("badge_key") for b in badges]
        
        return pick
    
    async def sort_picks_by_probability(
        self,
        picks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich and sort a list of picks by probability score.
        
        Args:
            picks: List of pick dictionaries
        
        Returns:
            Sorted list (highest probability first)
        """
        # Enrich each pick
        enriched = []
        for pick in picks:
            enriched_pick = await self.enrich_pick_with_probability(pick)
            enriched.append(enriched_pick)
        
        # Sort by probability score descending
        enriched.sort(key=lambda x: x.get("probability_score", 0), reverse=True)
        
        return enriched
    
    async def batch_enrich_picks(self, picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Batch enrich picks with probability scores.
        Pre-loads caches before processing for maximum speed.
        
        Args:
            picks: List of pick dictionaries
        
        Returns:
            List with probability scores added
        """
        if not picks:
            return picks
        
        # Pre-load caches once (instant if already loaded)
        await self._preload_dvp_cache()
        await self._preload_badges_cache()
        
        # Now enrich each pick (all cache lookups are instant)
        for pick in picks:
            player_name = pick.get("player_name")
            stat_type = pick.get("stat_type")
            opponent = pick.get("opponent")
            line = pick.get("line")
            direction = pick.get("direction", "over")
            
            # Get hit rates from pick
            hit_rate_l10 = pick.get("h10_rate") or pick.get("l10_hit_rate")
            hit_rate_l5 = pick.get("h5_rate") or pick.get("l5_hit_rate")
            l5_avg = pick.get("l5_avg")
            l10_avg = pick.get("l10_avg")
            season_avg = pick.get("season_avg")
            
            # Fast cache lookups (no DB queries)
            dvp_rank = await self.get_dvp_rank(opponent, stat_type)
            badges = await self.get_player_badges(player_name)
            
            # Calculate probability score
            result = calculate_probability_score(
                hit_rate_l10=hit_rate_l10,
                hit_rate_l5=hit_rate_l5,
                dvp_rank=dvp_rank,
                badges=badges,
                line=line,
                l5_avg=l5_avg,
                l10_avg=l10_avg,
                season_avg=season_avg,
                direction=direction
            )
            
            # Add to pick
            pick["probability_score"] = result["probability_score"]
            pick["prob_breakdown"] = result["breakdown"]
            pick["dvp_rank"] = dvp_rank
            pick["dvp_label"] = _get_dvp_label(dvp_rank)
            pick["active_badges"] = [b.get("badge_key") for b in badges]
        
        return picks
