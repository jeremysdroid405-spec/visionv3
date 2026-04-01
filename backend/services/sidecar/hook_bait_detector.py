"""
Hook Protector & Bait Detector Sidecar Module
==============================================
Decoupled module that applies final-stage UI warning flags to existing data.
Does NOT modify core projection engine - only adds warning metadata.

Features:
- Hook Protector: Flags lines near the statistical Mode (trap lines)
- Bait Detector: Flags suspiciously low lines (Vegas bait)

Feature Flag: ENABLE_HOOK_BAIT_DETECTOR (defaults to True)
"""

import logging
from typing import Dict, Any, List, Optional
from statistics import median, mode, StatisticsError
from motor.motor_asyncio import AsyncIOMotorDatabase
import os

logger = logging.getLogger(__name__)

# Feature Flag - can be toggled via environment variable
ENABLE_HOOK_BAIT_DETECTOR = os.environ.get("ENABLE_HOOK_BAIT_DETECTOR", "true").lower() == "true"


class HookBaitDetector:
    """
    Sidecar module for detecting:
    1. Hook Risk: Lines dangerously close to the Mode (most frequent outcome)
    2. Suspect Line Bait: Lines suspiciously below the Median
    """
    
    HOOK_THRESHOLD = 0.5  # Line within 0.5 of Mode triggers hook_risk
    BAIT_THRESHOLD = 0.25  # Line 25%+ below Median triggers suspect_line_bait
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.game_logs = db.nba_game_logs_2026
        self.enabled = ENABLE_HOOK_BAIT_DETECTOR
        
    def is_enabled(self) -> bool:
        """Check if feature is enabled"""
        return self.enabled
    
    def toggle(self, enabled: bool):
        """Toggle feature on/off"""
        self.enabled = enabled
        logger.info(f"[SIDECAR] Hook/Bait Detector {'ENABLED' if enabled else 'DISABLED'}")
    
    @staticmethod
    def calculate_median(values: List[float]) -> Optional[float]:
        """Calculate median of a list of values"""
        if not values:
            return None
        try:
            return round(median(values), 1)
        except Exception:
            return None
    
    @staticmethod
    def calculate_mode(values: List[float]) -> Optional[float]:
        """Calculate mode (most frequent value) - rounds to nearest 0.5"""
        if not values:
            return None
        try:
            # Round values to nearest 0.5 for mode calculation (common betting increments)
            rounded_values = [round(v * 2) / 2 for v in values]
            return mode(rounded_values)
        except StatisticsError:
            # No unique mode - return None
            return None
        except Exception:
            return None
    
    async def get_player_game_logs(
        self, 
        player_name: str, 
        limit: int = 20
    ) -> Dict[str, List[float]]:
        """
        Fetch raw game-by-game data for a player from bdl_game_logs.
        Returns dict with PTS, REB, AST arrays from actual game data.
        
        NO ESTIMATION OR PROXYING - uses only raw game data.
        """
        try:
            # Find player in master hub with their raw game logs
            player = await self.master_hub.find_one(
                {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                {"_id": 0, "display_name": 1, "bdl_game_logs": {"$slice": limit}}
            )
            
            if not player:
                logger.debug(f"[SIDECAR] Player not found: {player_name}")
                return {"PTS": [], "REB": [], "AST": [], "BLK": [], "STL": [], "THREES": []}
            
            bdl_logs = player.get("bdl_game_logs", [])
            
            if not bdl_logs:
                logger.debug(f"[SIDECAR] No game logs for: {player_name}")
                return {"PTS": [], "REB": [], "AST": [], "BLK": [], "STL": [], "THREES": []}
            
            # Extract raw stat values from each game
            result = {
                "PTS": [],
                "REB": [],
                "AST": [],
                "BLK": [],
                "STL": [],
                "THREES": []
            }
            
            for game in bdl_logs:
                # Only include games where player actually played
                min_str = game.get("min", "0")
                try:
                    mins = int(min_str.split(":")[0]) if ":" in str(min_str) else int(min_str) if min_str else 0
                except:
                    mins = 0
                
                if mins > 0:  # Player actually played
                    result["PTS"].append(float(game.get("pts", 0)))
                    result["REB"].append(float(game.get("reb", 0)))
                    result["AST"].append(float(game.get("ast", 0)))
                    result["BLK"].append(float(game.get("blk", 0)))
                    result["STL"].append(float(game.get("stl", 0)))
                    result["THREES"].append(float(game.get("fg3m", 0)))
            
            logger.debug(f"[SIDECAR] {player_name}: {len(result['PTS'])} valid games loaded")
            return result
            
        except Exception as e:
            logger.error(f"[SIDECAR] Error fetching game logs for {player_name}: {e}")
            return {"PTS": [], "REB": [], "AST": [], "BLK": [], "STL": [], "THREES": []}
    
    def calculate_advanced_stats(
        self, 
        game_values: List[float]
    ) -> Dict[str, Any]:
        """
        Calculate median and mode from game values.
        Returns stats for L10 and L20.
        """
        l10 = game_values[:10] if len(game_values) >= 10 else game_values
        l20 = game_values[:20] if len(game_values) >= 20 else game_values
        
        return {
            "l10_median": self.calculate_median(l10),
            "l10_mode": self.calculate_mode(l10),
            "l20_median": self.calculate_median(l20),
            "l20_mode": self.calculate_mode(l20),
            "sample_size_l10": len(l10),
            "sample_size_l20": len(l20)
        }
    
    def detect_hook_risk(
        self, 
        line: float, 
        mode_value: Optional[float]
    ) -> bool:
        """
        Hook Protector Logic:
        If line is within 0.5 of the Mode, it's a hook risk.
        Books often set lines at the most common outcome to maximize house edge.
        """
        if mode_value is None or line is None:
            return False
        
        diff = abs(line - mode_value)
        return diff <= self.HOOK_THRESHOLD
    
    def detect_suspect_bait(
        self, 
        line: float, 
        median_value: Optional[float]
    ) -> bool:
        """
        Bait Detector Logic:
        If line is 25%+ below the Median, it's suspect bait.
        Vegas may be baiting the public with a "too good to be true" line.
        """
        if median_value is None or line is None or median_value == 0:
            return False
        
        # Calculate how far below median the line is
        percent_below = (median_value - line) / median_value
        return percent_below >= self.BAIT_THRESHOLD
    
    async def analyze_prop(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float
    ) -> Dict[str, Any]:
        """
        Analyze a single prop for hook risk and bait detection.
        Returns warning flags and advanced stats.
        """
        if not self.enabled:
            return {
                "sidecar_enabled": False,
                "hook_risk": False,
                "suspect_line_bait": False
            }
        
        # Normalize stat type
        stat_key = stat_type.upper()
        if stat_key in ["3PM", "3PT", "THREE", "3P"]:
            stat_key = "THREES"
        elif stat_key in ["REBOUNDS", "TRB"]:
            stat_key = "REB"
        elif stat_key in ["ASSISTS"]:
            stat_key = "AST"
        elif stat_key in ["POINTS"]:
            stat_key = "PTS"
        elif stat_key in ["BLOCKS"]:
            stat_key = "BLK"
        elif stat_key in ["STEALS"]:
            stat_key = "STL"
        
        # Get game logs
        game_logs = await self.get_player_game_logs(player_name, limit=20)
        stat_values = game_logs.get(stat_key, [])
        
        if not stat_values:
            return {
                "sidecar_enabled": True,
                "hook_risk": False,
                "suspect_line_bait": False,
                "advanced_stats": None,
                "reason": "Insufficient game data"
            }
        
        # Calculate advanced stats
        advanced = self.calculate_advanced_stats(stat_values)
        
        # Prefer L10 for detection (more recent = more relevant)
        use_median = advanced.get("l10_median") or advanced.get("l20_median")
        use_mode = advanced.get("l10_mode") or advanced.get("l20_mode")
        
        # Run detectors
        hook_risk = self.detect_hook_risk(line, use_mode)
        suspect_bait = self.detect_suspect_bait(line, use_median)
        
        result = {
            "sidecar_enabled": True,
            "hook_risk": hook_risk,
            "suspect_line_bait": suspect_bait,
            "advanced_stats": {
                "median": use_median,
                "mode": use_mode,
                **advanced
            }
        }
        
        # Add warning reasons
        if hook_risk:
            result["hook_warning"] = f"Line {line} is within 0.5 of Mode ({use_mode})"
        
        if suspect_bait:
            pct_below = round((use_median - line) / use_median * 100, 1) if use_median else 0
            result["bait_warning"] = f"Line {line} is {pct_below}% below Median ({use_median})"
        
        return result
    
    async def enrich_player_props(
        self, 
        player_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enrich a player's props with hook/bait detection.
        Non-destructive: adds new fields without modifying existing data.
        """
        if not self.enabled:
            return player_data
        
        player_name = player_data.get("player_name", "")
        props = player_data.get("props", [])
        
        if not props:
            return player_data
        
        # Cache game logs for this player
        game_logs = await self.get_player_game_logs(player_name, limit=20)
        
        enriched_props = []
        for prop in props:
            stat_type = prop.get("stat_type_extracted") or prop.get("stat_type", "")
            line = prop.get("line", 0)
            
            # Normalize stat type
            stat_key = stat_type.upper()
            if stat_key in ["3PM", "3PT", "THREE", "3P"]:
                stat_key = "THREES"
            elif stat_key in ["REBOUNDS", "TRB"]:
                stat_key = "REB"
            elif stat_key in ["ASSISTS"]:
                stat_key = "AST"
            elif stat_key in ["POINTS"]:
                stat_key = "PTS"
            
            stat_values = game_logs.get(stat_key, [])
            
            # Calculate advanced stats
            if stat_values:
                advanced = self.calculate_advanced_stats(stat_values)
                use_median = advanced.get("l10_median") or advanced.get("l20_median")
                use_mode = advanced.get("l10_mode") or advanced.get("l20_mode")
                
                # Detect risks
                hook_risk = self.detect_hook_risk(line, use_mode)
                suspect_bait = self.detect_suspect_bait(line, use_median)
                
                # Add sidecar fields
                prop["sidecar"] = {
                    "enabled": True,
                    "hook_risk": hook_risk,
                    "suspect_line_bait": suspect_bait,
                    "median": use_median,
                    "mode": use_mode,
                    "l10_median": advanced.get("l10_median"),
                    "l20_median": advanced.get("l20_median"),
                }
                
                if hook_risk:
                    prop["sidecar"]["hook_warning"] = f"⚠️ Line near Mode ({use_mode})"
                
                if suspect_bait:
                    pct = round((use_median - line) / use_median * 100, 1) if use_median else 0
                    prop["sidecar"]["bait_warning"] = f"🚨 {pct}% below Median"
            else:
                prop["sidecar"] = {
                    "enabled": True,
                    "hook_risk": False,
                    "suspect_line_bait": False,
                    "median": None,
                    "mode": None
                }
            
            enriched_props.append(prop)
        
        player_data["props"] = enriched_props
        return player_data
    
    async def enrich_board_picks(
        self, 
        picks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich a list of board picks with hook/bait detection.
        For Safe Haven picks: if suspect_bait is true, mark for override.
        """
        if not self.enabled:
            return picks
        
        enriched = []
        for pick in picks:
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            board = pick.get("board", "")
            
            # Analyze this prop
            analysis = await self.analyze_prop(player_name, stat_type, line)
            
            # Add sidecar data
            pick["sidecar"] = {
                "enabled": True,
                "hook_risk": analysis.get("hook_risk", False),
                "suspect_line_bait": analysis.get("suspect_line_bait", False),
                "median": analysis.get("advanced_stats", {}).get("median"),
                "mode": analysis.get("advanced_stats", {}).get("mode"),
            }
            
            # Add warnings
            if analysis.get("hook_risk"):
                pick["sidecar"]["hook_warning"] = analysis.get("hook_warning", "⚠️ Hook Risk")
            
            if analysis.get("suspect_line_bait"):
                pick["sidecar"]["bait_warning"] = analysis.get("bait_warning", "🚨 Vegas Bait")
                # Override Safe Haven status
                if board == "safe_haven":
                    pick["sidecar"]["override_board"] = True
                    pick["sidecar"]["override_reason"] = "SUSPECT LINE: Vegas Bait detected"
            
            enriched.append(pick)
        
        return enriched


# Singleton instance
_detector_instance: Optional[HookBaitDetector] = None


def get_hook_bait_detector(db: AsyncIOMotorDatabase) -> HookBaitDetector:
    """Get or create the singleton detector instance"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = HookBaitDetector(db)
    return _detector_instance
