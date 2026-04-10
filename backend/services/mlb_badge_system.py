"""
MLB Badge System & Vision Intel Suite
======================================
MLB-specific archetypes and scout insights for prop analysis.

BADGE SCHEMA:
🟢 PURE_CONTACT: Whiff Rate < 15% + xBA > .290 (Hits/TB target)
🔴 HIGH_HEAT_TRAP: Facing pitcher with 4-seam velo +1.5mph in 2026
🔵 WORKHORSE: Pitcher Outs 17.5+ with 80% L10 reaching 6th inning
🔥 BARREL_MASTER: Barrel % > 15% over last 25 PA

SITUATIONAL INTEL:
- Wind Blowing Out: +10% boost to Over TB/HRR
- Umpire Cold Zone: Strike Zone Ratio > 1.05 = pitcher friendly

ORACLE WEIGHTING:
- Priority 1: BvP (if sample > 15 PA)
- Priority 2: Split Dominance (LHB vs RHP, etc.)
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# =============================================================================
# MLB BADGE DEFINITIONS
# =============================================================================

class MLBBadge:
    """MLB Badge types with metadata."""
    
    # Badge definitions
    PURE_CONTACT = {
        "id": "pure_contact",
        "name": "Pure Contact",
        "icon": "🟢",
        "frontend_icon": "target",  # Lucide icon
        "color": "green",
        "description": "Elite contact hitter - Whiff Rate < 15% + xBA > .290",
        "target_props": ["Hits", "Total Bases"],
        "boost": 1.10  # 10% confidence boost
    }
    
    HIGH_HEAT_TRAP = {
        "id": "high_heat_trap",
        "name": "High-Heat Trap",
        "icon": "🔴",
        "frontend_icon": "flame",
        "color": "red",
        "description": "Facing pitcher with velocity spike +1.5mph in 2026",
        "target_props": ["Hits", "Total Bases", "Home Runs"],
        "boost": 0.85  # 15% confidence penalty (trap)
    }
    
    WORKHORSE = {
        "id": "workhorse",
        "name": "Workhorse",
        "icon": "🔵",
        "frontend_icon": "shield",
        "color": "blue",
        "description": "Reliable pitcher - 80% L10 reaching 6th inning",
        "target_props": ["Pitcher Strikeouts", "Pitcher Outs"],
        "boost": 1.15  # 15% confidence boost
    }
    
    BARREL_MASTER = {
        "id": "barrel_master",
        "name": "Barrel Master",
        "icon": "🔥",
        "frontend_icon": "zap",  # Lightning bolt for power
        "color": "orange",
        "description": "Elite power - Barrel % > 15% over last 25 PA",
        "target_props": ["Home Runs", "Total Bases", "RBIs"],
        "boost": 1.12  # 12% confidence boost
    }
    
    # Situational badges
    WIND_BOOST = {
        "id": "wind_boost",
        "name": "Wind Blowing Out",
        "icon": "💨",
        "frontend_icon": "wind",
        "color": "cyan",
        "description": "Wind blowing out - +10% boost to Over TB/HRR",
        "target_props": ["Total Bases", "Home Runs", "Hits+Runs+RBIs"],
        "boost": 1.10
    }
    
    COLD_ZONE = {
        "id": "cold_zone",
        "name": "Cold Zone",
        "icon": "❄️",
        "frontend_icon": "thermometer-snowflake",
        "color": "blue",
        "description": "Pitcher-friendly umpire - Strike Zone Ratio > 1.05",
        "target_props": ["Hits", "Total Bases", "Home Runs", "RBIs"],
        "boost": 0.90  # 10% penalty
    }
    
    BVP_DOMINATOR = {
        "id": "bvp_dominator",
        "name": "BvP Dominator",
        "icon": "⚔️",
        "frontend_icon": "swords",
        "color": "purple",
        "description": "Strong historical performance vs today's pitcher (15+ PA)",
        "target_props": ["Hits", "Total Bases", "Home Runs", "RBIs"],
        "boost": 1.15
    }
    
    SPLIT_ADVANTAGE = {
        "id": "split_advantage",
        "name": "Split Advantage",
        "icon": "📊",
        "frontend_icon": "bar-chart",
        "color": "teal",
        "description": "Favorable handedness matchup (e.g., LHB vs RHP)",
        "target_props": ["Hits", "Total Bases"],
        "boost": 1.08
    }
    
    WHIFF_WIZARD = {
        "id": "whiff_wizard",
        "name": "Whiff Wizard",
        "icon": "⚡",
        "frontend_icon": "zap",
        "color": "violet",
        "description": "Elite K pitcher - K% > 28% + SwStr% > 12%",
        "target_props": ["Pitcher Strikeouts"],
        "boost": 1.18  # 18% confidence boost for K overs
    }
    
    HITTERS_HAVEN = {
        "id": "hitters_haven",
        "name": "Hitter's Haven",
        "icon": "🏟️",
        "frontend_icon": "home",
        "color": "green",
        "description": "Playing in hitter-friendly park (Coors, GABP, Fenway)",
        "target_props": ["Hits", "Total Bases", "Home Runs", "RBIs", "Hits+Runs+RBIs"],
        "boost": 1.12  # 12% boost for hitting props
    }
    
    VOLATILITY_EXTREME = {
        "id": "volatility_extreme",
        "name": "Extreme Volatility",
        "icon": "📈",
        "frontend_icon": "bar-chart-3",
        "color": "red",
        "description": "AI-scored Volatility Index > 8/10 - True lottery ticket",
        "target_props": ["Total Bases", "Home Runs", "Hits+Runs+RBIs"],
        "boost": 1.0  # No boost - this is a warning/context badge
    }
    
    @classmethod
    def get_all_badges(cls) -> List[Dict]:
        """Get all badge definitions."""
        return [
            cls.PURE_CONTACT,
            cls.HIGH_HEAT_TRAP,
            cls.WORKHORSE,
            cls.BARREL_MASTER,
            cls.WIND_BOOST,
            cls.COLD_ZONE,
            cls.BVP_DOMINATOR,
            cls.SPLIT_ADVANTAGE,
            cls.WHIFF_WIZARD,
            cls.HITTERS_HAVEN,
            cls.VOLATILITY_EXTREME
        ]


# =============================================================================
# BADGE THRESHOLDS
# =============================================================================

BADGE_THRESHOLDS = {
    # Pure Contact thresholds
    "pure_contact_whiff_max": 0.15,      # Whiff Rate < 15%
    "pure_contact_xba_min": 0.290,       # xBA > .290
    
    # High-Heat Trap thresholds
    "high_heat_velo_increase": 1.5,      # Velocity increase > 1.5mph
    
    # Workhorse thresholds
    "workhorse_outs_min": 17.5,          # Outs line 17.5+
    "workhorse_6th_inning_pct": 0.80,    # 80% reaching 6th inning L10
    
    # Barrel Master thresholds
    "barrel_master_pct_min": 15.0,       # Barrel % > 15%
    "barrel_master_pa_min": 25,          # Over last 25 PA
    
    # Wind thresholds
    "wind_out_speed_min": 8,             # Wind speed > 8mph
    "wind_out_direction_range": (225, 315),  # SW to NW (blowing out)
    
    # Umpire thresholds
    "umpire_cold_zone_ratio": 1.05,      # Strike Zone Ratio > 1.05
    
    # BvP thresholds
    "bvp_min_pa": 15,                    # Minimum 15 PA for BvP
    "bvp_good_avg": 0.300,               # Good BvP average
    "bvp_bad_avg": 0.200,                # Bad BvP average
    
    # Whiff Wizard thresholds (Pitcher strikeout badge)
    "whiff_wizard_k_pct_min": 28.0,      # K% > 28%
    "whiff_wizard_swstr_pct_min": 12.0,  # Swinging Strike % > 12%
    
    # Hitter's Haven thresholds (Park factors)
    "hitters_haven_factor_min": 1.05,    # Park factor > 1.05
    
    # Volatility Index thresholds
    "volatility_extreme_min": 8,          # Volatility Index > 8/10
}


# =============================================================================
# MLB VISION INTEL BADGE SERVICE
# =============================================================================

class MLBBadgeService:
    """
    MLB Badge evaluation service.
    
    Evaluates players against badge criteria and returns earned badges.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.mlb_master_hub_2026
        self.historical_logs = db.mlb_historical_logs
    
    async def evaluate_pure_contact(self, player_name: str) -> Optional[Dict]:
        """
        Evaluate Pure Contact badge.
        
        Criteria: Whiff Rate < 15% + xBA > .290
        Uses real historical data from vk_baselines when available.
        """
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if not player:
            return None
        
        # Get real data from vk_baselines (historical backfill)
        baselines = player.get("vk_baselines", {})
        
        # Try to get real strikeout baseline
        k_baseline = baselines.get("strikeouts", {})
        hits_baseline = baselines.get("hits", {})
        at_bats_baseline = baselines.get("at_bats", {})
        
        # Get advanced stats as fallback
        advanced = player.get("advanced_stats", {})
        season_stats = advanced.get("season_stats", {}).get("2026", {})
        batting = season_stats.get("batting", {})
        
        # Calculate whiff rate proxy from K rate
        strikeouts = batting.get("strikeouts", 0) or k_baseline.get("weighted_baseline", 0) or 0
        at_bats = batting.get("at_bats", 0) or at_bats_baseline.get("weighted_baseline", 0) or 0
        avg = batting.get("avg", 0) or 0
        
        # Use historical baseline for better estimates
        if hits_baseline.get("weighted_baseline") and at_bats_baseline.get("weighted_baseline"):
            historical_avg = hits_baseline["weighted_baseline"] / at_bats_baseline["weighted_baseline"]
            avg = max(avg, historical_avg)  # Use higher of current or historical
        
        if at_bats < 20:  # Need minimum sample
            # Check historical data
            if at_bats_baseline.get("sample_size", 0) < 50:
                return None
        
        k_rate = strikeouts / at_bats if at_bats > 0 else 0
        
        # Use CV from baselines if available to estimate whiff rate more accurately
        k_cv = k_baseline.get("weighted_cv", 50)  # Default 50%
        # Lower CV = more consistent contact = lower whiff rate
        estimated_whiff = k_rate * (0.3 + (k_cv / 200))  # CV-adjusted whiff estimate
        
        # xBA estimate - use consistency metrics
        estimated_xba = avg + 0.015 if avg else 0
        if hits_baseline.get("weighted_cv"):
            # Lower CV in hits = more consistent hitter = higher xBA
            cv_boost = max(0, (50 - hits_baseline["weighted_cv"]) / 500)
            estimated_xba += cv_boost
        
        if estimated_whiff < BADGE_THRESHOLDS["pure_contact_whiff_max"] and estimated_xba > BADGE_THRESHOLDS["pure_contact_xba_min"]:
            return {
                **MLBBadge.PURE_CONTACT,
                "earned": True,
                "metrics": {
                    "estimated_whiff_rate": round(estimated_whiff, 3),
                    "estimated_xba": round(estimated_xba, 3),
                    "avg": round(avg, 3) if avg else None,
                    "k_rate": round(k_rate, 3),
                    "hits_baseline": hits_baseline.get("weighted_baseline"),
                    "seasons_data": hits_baseline.get("seasons_included", [])
                }
            }
        
        return None
    
    async def evaluate_high_heat_trap(
        self, 
        batter_name: str, 
        pitcher_name: str
    ) -> Optional[Dict]:
        """
        Evaluate High-Heat Trap badge.
        
        Criteria: Facing pitcher with 4-seam velocity +1.5mph in 2026
        """
        pitcher = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{pitcher_name}$", "$options": "i"}, "is_pitcher": True},
            {"_id": 0}
        )
        
        if not pitcher:
            return None
        
        # Check pitcher's 2025 vs 2026 velocity
        advanced = pitcher.get("advanced_stats", {})
        season_stats = advanced.get("season_stats", {})
        
        stats_2025 = season_stats.get("2025", {}).get("pitching", {})
        stats_2026 = season_stats.get("2026", {}).get("pitching", {})
        
        # Use K/9 as velocity proxy (higher velo = more Ks typically)
        k9_2025 = stats_2025.get("k_per_9", 0) or 0
        k9_2026 = stats_2026.get("k_per_9", 0) or 0
        
        # Significant K/9 increase suggests velocity increase
        # 1.5 K/9 increase ≈ 1.5mph velo increase (rough proxy)
        k9_increase = k9_2026 - k9_2025
        
        if k9_increase > 1.5:
            return {
                **MLBBadge.HIGH_HEAT_TRAP,
                "earned": True,
                "metrics": {
                    "pitcher": pitcher_name,
                    "k9_2025": round(k9_2025, 2),
                    "k9_2026": round(k9_2026, 2),
                    "k9_increase": round(k9_increase, 2),
                    "estimated_velo_increase": f"+{round(k9_increase, 1)}mph"
                }
            }
        
        return None
    
    async def evaluate_workhorse(self, pitcher_name: str, outs_line: float = 17.5) -> Optional[Dict]:
        """
        Evaluate Workhorse badge for pitchers.
        
        Criteria: Outs line 17.5+ with 80% L10 reaching 6th inning
        """
        pitcher = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{pitcher_name}$", "$options": "i"}, "is_pitcher": True},
            {"_id": 0}
        )
        
        if not pitcher:
            return None
        
        game_logs = pitcher.get("bdl_game_logs", [])[:10]  # L10 starts
        
        if len(game_logs) < 5:  # Need minimum sample
            return None
        
        # Count games reaching 6th inning (5+ IP = 15+ outs)
        games_6th_inning = sum(1 for g in game_logs if (g.get("innings_pitched") or 0) >= 5)
        pct_6th_inning = games_6th_inning / len(game_logs)
        
        avg_ip = sum(g.get("innings_pitched", 0) or 0 for g in game_logs) / len(game_logs)
        avg_outs = avg_ip * 3  # Outs = IP * 3
        
        if outs_line >= BADGE_THRESHOLDS["workhorse_outs_min"] and pct_6th_inning >= BADGE_THRESHOLDS["workhorse_6th_inning_pct"]:
            return {
                **MLBBadge.WORKHORSE,
                "earned": True,
                "metrics": {
                    "outs_line": outs_line,
                    "avg_ip_l10": round(avg_ip, 1),
                    "avg_outs_l10": round(avg_outs, 1),
                    "pct_6th_inning": round(pct_6th_inning * 100, 1),
                    "games_analyzed": len(game_logs)
                }
            }
        
        return None
    
    async def evaluate_barrel_master(self, player_name: str) -> Optional[Dict]:
        """
        Evaluate Barrel Master badge.
        
        Criteria: Barrel % > 15% over last 25 PA
        Uses real historical data from vk_baselines when available.
        """
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}, "is_batter": True},
            {"_id": 0}
        )
        
        if not player:
            return None
        
        # Get historical baselines for power metrics
        baselines = player.get("vk_baselines", {})
        hr_baseline = baselines.get("home_runs", {})
        tb_baseline = baselines.get("total_bases", {})
        
        game_logs = player.get("bdl_game_logs", [])
        
        # Accumulate last 25 PA worth of games
        total_pa = 0
        total_xbh = 0  # Extra base hits as barrel proxy
        total_hrs = 0
        games_used = 0
        
        for game in game_logs:
            abs = game.get("at_bats", 0) or 0
            walks = game.get("walks", 0) or 0
            pa = abs + walks
            
            if pa == 0:
                continue
            
            total_pa += pa
            games_used += 1
            
            # XBH as barrel proxy
            hits = game.get("hits", 0) or 0
            singles = hits - ((game.get("doubles", 0) or 0) + (game.get("triples", 0) or 0) + (game.get("home_runs", 0) or 0))
            xbh = hits - singles
            total_xbh += xbh
            total_hrs += game.get("home_runs", 0) or 0
            
            if total_pa >= 25:
                break
        
        # Use historical baseline if recent data is insufficient
        if total_pa < 20:
            if hr_baseline.get("sample_size", 0) < 30:
                return None
            # Use historical averages
            hr_avg = hr_baseline.get("weighted_baseline", 0)
            tb_avg = tb_baseline.get("weighted_baseline", 0)
            # Estimate barrel % from HR rate and TB
            if hr_avg > 0.15 or tb_avg > 2.0:  # Significant power
                return {
                    **MLBBadge.BARREL_MASTER,
                    "earned": True,
                    "metrics": {
                        "hr_per_game": round(hr_avg, 3),
                        "tb_per_game": round(tb_avg, 3),
                        "estimated_barrel_pct": round((hr_avg / 0.15) * 15, 1),  # Scale to barrel %
                        "sample_size": hr_baseline.get("sample_size", 0),
                        "seasons_data": hr_baseline.get("seasons_included", []),
                        "data_source": "historical_5yr"
                    }
                }
            return None
        
        # Calculate from recent games
        xbh_rate = total_xbh / total_pa if total_pa > 0 else 0
        hr_rate = total_hrs / total_pa if total_pa > 0 else 0
        
        # Use historical HR baseline to boost barrel estimate
        historical_hr_boost = 0
        if hr_baseline.get("weighted_baseline"):
            historical_hr_boost = hr_baseline["weighted_baseline"] * 5  # Scale historical HR to barrel boost
        
        # Estimate barrel % from XBH rate + HR rate + historical
        estimated_barrel_pct = (xbh_rate * 100) + (hr_rate * 200) + historical_hr_boost
        
        if estimated_barrel_pct > BADGE_THRESHOLDS["barrel_master_pct_min"]:
            return {
                **MLBBadge.BARREL_MASTER,
                "earned": True,
                "metrics": {
                    "plate_appearances": total_pa,
                    "xbh": total_xbh,
                    "home_runs": total_hrs,
                    "xbh_rate": round(xbh_rate, 3),
                    "hr_rate": round(hr_rate, 3),
                    "estimated_barrel_pct": round(estimated_barrel_pct, 1),
                    "games_analyzed": games_used,
                    "historical_hr_baseline": hr_baseline.get("weighted_baseline"),
                    "data_source": "recent_games"
                }
            }
        
        return None
    
    def evaluate_wind_boost(self, weather: Dict, park: Dict) -> Optional[Dict]:
        """
        Evaluate Wind Blowing Out badge.
        
        Criteria: Wind > 8mph blowing out (SW to NW)
        """
        if not weather or park.get("type") != "outdoor":
            return None
        
        wind_speed = weather.get("windspeed", 0)
        wind_dir = weather.get("winddirection", 0)
        
        # Wind direction 225-315 = SW to NW (blowing out in most parks)
        dir_min, dir_max = BADGE_THRESHOLDS["wind_out_direction_range"]
        
        if wind_speed >= BADGE_THRESHOLDS["wind_out_speed_min"] and dir_min <= wind_dir <= dir_max:
            return {
                **MLBBadge.WIND_BOOST,
                "earned": True,
                "metrics": {
                    "wind_speed": wind_speed,
                    "wind_direction": wind_dir,
                    "direction_desc": "Blowing Out",
                    "park": park.get("name")
                }
            }
        
        return None
    
    def evaluate_cold_zone(self, umpire_data: Optional[Dict]) -> Optional[Dict]:
        """
        Evaluate Cold Zone badge based on umpire.
        
        Criteria: Home plate umpire Strike Zone Ratio > 1.05
        """
        if not umpire_data:
            return None
        
        sz_ratio = umpire_data.get("strike_zone_ratio", 1.0)
        
        if sz_ratio > BADGE_THRESHOLDS["umpire_cold_zone_ratio"]:
            return {
                **MLBBadge.COLD_ZONE,
                "earned": True,
                "metrics": {
                    "umpire": umpire_data.get("name", "Unknown"),
                    "strike_zone_ratio": sz_ratio,
                    "tendency": "Pitcher Friendly"
                }
            }
        
        return None
    
    async def evaluate_bvp_dominator(
        self, 
        batter_name: str, 
        pitcher_name: str
    ) -> Optional[Dict]:
        """
        Evaluate BvP Dominator badge.
        
        Criteria: Strong historical performance vs pitcher (15+ PA, AVG > .300)
        """
        # Check BvP collection if available
        bvp_collection = self.db.get_collection("mlb_bvp_splits")
        
        bvp_data = await bvp_collection.find_one(
            {
                "batter_name": {"$regex": f"^{batter_name}$", "$options": "i"},
                "pitcher_name": {"$regex": f"^{pitcher_name}$", "$options": "i"}
            },
            {"_id": 0}
        ) if bvp_collection else None
        
        if bvp_data:
            pa = bvp_data.get("plate_appearances", 0)
            avg = bvp_data.get("avg", 0)
            
            if pa >= BADGE_THRESHOLDS["bvp_min_pa"] and avg >= BADGE_THRESHOLDS["bvp_good_avg"]:
                return {
                    **MLBBadge.BVP_DOMINATOR,
                    "earned": True,
                    "metrics": {
                        "vs_pitcher": pitcher_name,
                        "plate_appearances": pa,
                        "avg": avg,
                        "hits": bvp_data.get("hits", 0),
                        "home_runs": bvp_data.get("home_runs", 0)
                    }
                }
        
        return None
    
    async def evaluate_split_advantage(
        self, 
        batter_name: str, 
        pitcher_throws: str
    ) -> Optional[Dict]:
        """
        Evaluate Split Advantage badge.
        
        Criteria: Favorable handedness matchup
        """
        batter = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{batter_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if not batter:
            return None
        
        batter_bats = batter.get("bats", "R")  # Default right
        
        # Check platoon advantage
        # LHB vs RHP or RHB vs LHP = advantage
        has_advantage = (
            (batter_bats == "L" and pitcher_throws == "R") or
            (batter_bats == "R" and pitcher_throws == "L")
        )
        
        if has_advantage:
            return {
                **MLBBadge.SPLIT_ADVANTAGE,
                "earned": True,
                "metrics": {
                    "batter_bats": batter_bats,
                    "pitcher_throws": pitcher_throws,
                    "matchup": f"{batter_bats}HB vs {pitcher_throws}HP"
                }
            }
        
        return None
    
    async def evaluate_whiff_wizard(self, pitcher_name: str) -> Optional[Dict]:
        """
        Evaluate Whiff Wizard badge for pitchers.
        
        Criteria: K% > 28% + Swinging Strike % > 12%
        Uses real historical data from vk_baselines when available.
        """
        try:
            # Get pitcher from master hub
            master_hub = self.db["mlb_master_hub_2026"]
            pitcher = await master_hub.find_one(
                {"display_name": {"$regex": f"^{pitcher_name}$", "$options": "i"}},
                {"_id": 0, "k_per_9": 1, "whip": 1, "bdl_game_logs": 1, "vk_baselines": 1}
            )
            
            if not pitcher:
                return None
            
            # Get historical baselines for strikeout metrics
            baselines = pitcher.get("vk_baselines", {})
            k_baseline = baselines.get("pitcher_strikeouts", {})
            ip_baseline = baselines.get("innings_pitched", {})
            
            k_per_9 = pitcher.get("k_per_9") or 0
            
            # Calculate K/9 from historical baselines if available
            if k_baseline.get("weighted_baseline") and ip_baseline.get("weighted_baseline"):
                historical_k_per_9 = (k_baseline["weighted_baseline"] / ip_baseline["weighted_baseline"]) * 9
                # Use higher of current or historical
                k_per_9 = max(k_per_9, historical_k_per_9)
            
            # Check game logs for strikeout consistency
            game_logs = pitcher.get("bdl_game_logs", [])
            k_per_9_recent = 0
            if game_logs:
                recent_logs = sorted(game_logs, key=lambda x: x.get("date", ""), reverse=True)[:10]
                total_k = sum((g.get("pitcher_strikeouts") or 0) for g in recent_logs)
                total_ip = sum((g.get("innings_pitched") or 0) for g in recent_logs)
                if total_ip > 0:
                    k_per_9_recent = (total_k / total_ip) * 9
            
            # Use best K/9 from available sources
            best_k_per_9 = max(k_per_9, k_per_9_recent)
            
            # Estimate K% from K/9 (K% ≈ K/9 * 0.035 for rough conversion)
            estimated_k_pct = best_k_per_9 * 3.1
            
            # Use CV to estimate SwStr% - lower CV in Ks means more consistent swing-and-miss
            k_cv = k_baseline.get("weighted_cv", 50)  # Default 50%
            estimated_swstr_pct = 12 + ((50 - k_cv) / 10)  # Lower CV = higher SwStr%
            
            # Qualify based on K/9 > 10 OR estimated K% > threshold
            if best_k_per_9 > 10 or (estimated_k_pct > BADGE_THRESHOLDS["whiff_wizard_k_pct_min"] * 0.8 and estimated_swstr_pct > BADGE_THRESHOLDS["whiff_wizard_swstr_pct_min"] * 0.8):
                return {
                    **MLBBadge.WHIFF_WIZARD,
                    "earned": True,
                    "metrics": {
                        "k_per_9": round(best_k_per_9, 2),
                        "k_per_9_recent": round(k_per_9_recent, 2),
                        "estimated_k_pct": round(estimated_k_pct, 1),
                        "estimated_swstr_pct": round(estimated_swstr_pct, 1),
                        "whip": pitcher.get("whip"),
                        "k_baseline": k_baseline.get("weighted_baseline"),
                        "k_cv": round(k_cv, 1) if k_cv else None,
                        "seasons_data": k_baseline.get("seasons_included", []),
                        "data_source": "historical_5yr" if k_baseline.get("weighted_baseline") else "current_season"
                    }
                }
        except Exception as e:
            logger.warning(f"Whiff Wizard eval failed for {pitcher_name}: {e}")
        
        return None
    
    def evaluate_hitters_haven(self, park_info: Dict) -> Optional[Dict]:
        """
        Evaluate Hitter's Haven badge based on ballpark.
        
        Criteria: Park factor > 1.05 (Coors, GABP, Fenway, etc.)
        """
        if not park_info:
            return None
        
        park_name = park_info.get("name") or park_info.get("park_name")
        park_factor = park_info.get("factor") or park_info.get("park_factor") or 1.0
        team = park_info.get("team")
        
        # Check if it's a known hitter's haven
        HITTERS_HAVEN_PARKS = {
            "Coors Field": 1.28,
            "Great American Ball Park": 1.12,
            "Fenway Park": 1.08,
            "Globe Life Field": 1.07,
            "Citizens Bank Park": 1.06,
            "Yankee Stadium": 1.05,
        }
        
        # Also check by team
        HITTERS_HAVEN_TEAMS = {"COL", "CIN", "BOS", "TEX", "PHI", "NYY"}
        
        is_hitters_haven = False
        if park_name in HITTERS_HAVEN_PARKS:
            is_hitters_haven = True
            park_factor = HITTERS_HAVEN_PARKS[park_name]
        elif team in HITTERS_HAVEN_TEAMS:
            is_hitters_haven = True
        elif park_factor >= BADGE_THRESHOLDS["hitters_haven_factor_min"]:
            is_hitters_haven = True
        
        if is_hitters_haven:
            return {
                **MLBBadge.HITTERS_HAVEN,
                "earned": True,
                "metrics": {
                    "park_name": park_name or "Unknown",
                    "park_factor": park_factor,
                    "team": team
                }
            }
        
        return None
    
    def evaluate_volatility_extreme(self, cv: float, hit_rate: float, ceiling_stats: Dict, baselines: Dict = None) -> Optional[Dict]:
        """
        Evaluate Extreme Volatility badge based on AI scoring.
        
        Criteria: Volatility Index > 8/10
        Uses real CV data from vk_baselines when available.
        """
        # Calculate volatility index
        score = 0
        
        # Get real CV from baselines if available
        real_cv = cv
        if baselines:
            # Find the highest CV across relevant stats
            for stat_name, stat_data in baselines.items():
                if stat_data.get("weighted_cv"):
                    stat_cv = stat_data["weighted_cv"] / 100  # Convert from % to decimal
                    if stat_cv > real_cv:
                        real_cv = stat_cv
        
        # CV Score (0-4 points) - using real CV data
        if real_cv is not None:
            if real_cv > 1.2:
                score += 4
            elif real_cv > 1.0:
                score += 3
            elif real_cv > 0.8:
                score += 2
            elif real_cv > 0.6:
                score += 1
        
        # Hit Rate Variance Score (0-3 points)
        if hit_rate is not None:
            if hit_rate < 30:
                score += 3
            elif hit_rate < 50:
                score += 2
            elif hit_rate < 70:
                score += 1
        
        # Ceiling/Floor Spread Score (0-3 points)
        if ceiling_stats:
            max_val = ceiling_stats.get("max_value", 0) or 0
            values = ceiling_stats.get("values", [])
            if values:
                min_val = min(values) if values else 0
                spread = max_val - min_val
                if spread >= 5:
                    score += 3
                elif spread >= 3:
                    score += 2
                elif spread >= 2:
                    score += 1
        
        volatility_index = min(10, max(1, score))
        
        if volatility_index >= BADGE_THRESHOLDS["volatility_extreme_min"]:
            return {
                **MLBBadge.VOLATILITY_EXTREME,
                "earned": True,
                "metrics": {
                    "volatility_index": volatility_index,
                    "cv": round(cv, 3) if cv else None,
                    "real_cv": round(real_cv, 3) if real_cv else None,
                    "hit_rate": hit_rate,
                    "max_value": ceiling_stats.get("max_value") if ceiling_stats else None,
                    "data_source": "historical_5yr" if baselines else "current_season"
                }
            }
        
        return None
    
    async def evaluate_all_badges(
        self,
        player_name: str,
        stat_type: str,
        prop: Dict,
        weather: Optional[Dict] = None,
        park: Optional[Dict] = None,
        opponent_pitcher: Optional[str] = None,
        umpire_data: Optional[Dict] = None,
        cv: Optional[float] = None,
        hit_rate: Optional[float] = None,
        ceiling_stats: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Evaluate all applicable badges for a prop.
        
        Returns list of earned badges.
        Uses real historical data from vk_baselines when available.
        """
        badges = []
        is_pitcher_prop = "pitcher" in stat_type.lower() or "strikeout" in stat_type.lower() and "batter" not in stat_type.lower()
        
        # Fetch player baselines for enhanced badge evaluation
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "vk_baselines": 1}
        )
        baselines = player.get("vk_baselines", {}) if player else {}
        
        if is_pitcher_prop:
            # Pitcher badges
            workhorse = await self.evaluate_workhorse(player_name, prop.get("line", 0))
            if workhorse:
                badges.append(workhorse)
            
            # Whiff Wizard - for strikeout props
            if "strikeout" in stat_type.lower():
                whiff_wizard = await self.evaluate_whiff_wizard(player_name)
                if whiff_wizard:
                    badges.append(whiff_wizard)
        else:
            # Batter badges
            pure_contact = await self.evaluate_pure_contact(player_name)
            if pure_contact:
                badges.append(pure_contact)
            
            barrel_master = await self.evaluate_barrel_master(player_name)
            if barrel_master:
                badges.append(barrel_master)
            
            # Opponent-based badges
            if opponent_pitcher:
                high_heat = await self.evaluate_high_heat_trap(player_name, opponent_pitcher)
                if high_heat:
                    badges.append(high_heat)
                
                bvp = await self.evaluate_bvp_dominator(player_name, opponent_pitcher)
                if bvp:
                    badges.append(bvp)
        
        # Situational badges (apply to all)
        if weather and park:
            wind_boost = self.evaluate_wind_boost(weather, park)
            if wind_boost:
                badges.append(wind_boost)
        
        if umpire_data:
            cold_zone = self.evaluate_cold_zone(umpire_data)
            if cold_zone:
                badges.append(cold_zone)
        
        # Park factor badge - Hitter's Haven
        if park and not is_pitcher_prop:
            hitters_haven = self.evaluate_hitters_haven(park)
            if hitters_haven:
                badges.append(hitters_haven)
        
        # Volatility badge for War Zone candidates - now with real baselines
        if cv is not None and ceiling_stats:
            volatility = self.evaluate_volatility_extreme(cv, hit_rate, ceiling_stats, baselines)
            if volatility:
                badges.append(volatility)
        
        return badges


# =============================================================================
# ORACLE DECISION WEIGHTING
# =============================================================================

class MLBOracleWeighting:
    """
    MLB-specific Oracle decision weighting.
    
    Priority:
    1. BvP (if sample > 15 PA)
    2. Split Dominance (handedness)
    3. Standard factors (VK, Market, Scout)
    """
    
    WEIGHTS = {
        "bvp": 0.35,           # Highest weight if available
        "split": 0.20,         # Second priority
        "vk_projection": 0.20,
        "market_signal": 0.15,
        "badges": 0.10
    }
    
    @classmethod
    def calculate_weighted_score(
        cls,
        bvp_score: Optional[float],
        split_score: Optional[float],
        vk_score: float,
        market_score: float,
        badge_boost: float,
        has_bvp: bool = False
    ) -> Dict[str, Any]:
        """
        Calculate weighted Oracle score with MLB priorities.
        
        Args:
            bvp_score: BvP historical performance score (0-10)
            split_score: Handedness split score (0-10)
            vk_score: VK projection score (0-10)
            market_score: Market signal score (0-10)
            badge_boost: Badge multiplier (e.g., 1.10 for 10% boost)
            has_bvp: Whether BvP data is available (15+ PA)
            
        Returns:
            Weighted score and breakdown
        """
        if has_bvp and bvp_score is not None:
            # BvP available - use priority weighting
            weighted = (
                bvp_score * cls.WEIGHTS["bvp"] +
                (split_score or 5) * cls.WEIGHTS["split"] +
                vk_score * cls.WEIGHTS["vk_projection"] +
                market_score * cls.WEIGHTS["market_signal"] +
                5 * cls.WEIGHTS["badges"]  # Badge neutral, boost applied after
            )
        else:
            # No BvP - redistribute weight to splits and VK
            adjusted_split_weight = cls.WEIGHTS["bvp"] + cls.WEIGHTS["split"]
            weighted = (
                (split_score or 5) * adjusted_split_weight +
                vk_score * (cls.WEIGHTS["vk_projection"] + 0.10) +
                market_score * cls.WEIGHTS["market_signal"] +
                5 * cls.WEIGHTS["badges"]
            )
        
        # Apply badge boost
        final_score = weighted * badge_boost
        
        # Clamp to 1-10
        final_score = max(1, min(10, round(final_score)))
        
        return {
            "final_score": final_score,
            "base_score": round(weighted, 2),
            "badge_multiplier": badge_boost,
            "weights_used": {
                "bvp": cls.WEIGHTS["bvp"] if has_bvp else 0,
                "split": cls.WEIGHTS["split"] if has_bvp else cls.WEIGHTS["bvp"] + cls.WEIGHTS["split"],
                "vk": cls.WEIGHTS["vk_projection"],
                "market": cls.WEIGHTS["market_signal"],
                "badges": cls.WEIGHTS["badges"]
            },
            "priority": "BvP" if has_bvp else "Split Dominance"
        }


# =============================================================================
# FRONTEND BADGE ICON MAPPING
# =============================================================================

FRONTEND_BADGE_ICONS = {
    "pure_contact": {
        "icon": "Target",          # Lucide React icon
        "color": "#22c55e",        # Green
        "bgColor": "#dcfce7"
    },
    "high_heat_trap": {
        "icon": "Flame",
        "color": "#ef4444",        # Red
        "bgColor": "#fee2e2"
    },
    "workhorse": {
        "icon": "Shield",
        "color": "#3b82f6",        # Blue
        "bgColor": "#dbeafe"
    },
    "barrel_master": {
        "icon": "Zap",             # Lightning bolt for power
        "color": "#f97316",        # Orange
        "bgColor": "#ffedd5"
    },
    "wind_boost": {
        "icon": "Wind",
        "color": "#06b6d4",        # Cyan
        "bgColor": "#cffafe"
    },
    "cold_zone": {
        "icon": "Snowflake",
        "color": "#60a5fa",        # Light blue
        "bgColor": "#e0f2fe"
    },
    "bvp_dominator": {
        "icon": "Swords",
        "color": "#a855f7",        # Purple
        "bgColor": "#f3e8ff"
    },
    "split_advantage": {
        "icon": "BarChart3",
        "color": "#14b8a6",        # Teal
        "bgColor": "#ccfbf1"
    }
}


def get_badge_icon_config(badge_id: str) -> Dict:
    """Get frontend icon configuration for a badge."""
    return FRONTEND_BADGE_ICONS.get(badge_id, {
        "icon": "Badge",
        "color": "#6b7280",
        "bgColor": "#f3f4f6"
    })


# Singleton
_badge_service: Optional[MLBBadgeService] = None


def get_mlb_badge_service(db: AsyncIOMotorDatabase) -> MLBBadgeService:
    """Get or create MLB Badge service instance."""
    global _badge_service
    if _badge_service is None:
        _badge_service = MLBBadgeService(db)
    return _badge_service
