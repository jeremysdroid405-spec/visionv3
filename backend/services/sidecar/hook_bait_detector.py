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
from typing import Dict, Any, List, Optional, Tuple
from statistics import median, mode, StatisticsError
from motor.motor_asyncio import AsyncIOMotorDatabase
import os

logger = logging.getLogger(__name__)

# Feature Flag - can be toggled via environment variable
ENABLE_HOOK_BAIT_DETECTOR = os.environ.get("ENABLE_HOOK_BAIT_DETECTOR", "true").lower() == "true"


class HookBaitDetector:
    """
    Sidecar module for detecting:
    1. Hook Risk: Lines dangerously close to a highly frequent Mode
    2. Suspect Line Bait: Lines that are statistical anomalies (1.5 SD below Median)
    
    REFINED THRESHOLDS (2026-04-01):
    - Hook: Mode must occur 25%+ of L20, line exactly ±0.5 from Mode
    - Bait: Median >= 10, line <= Median - 1.5*SD, AND 20%+ drop with min 3.0 pts
    """
    
    # HOOK PROTECTOR THRESHOLDS
    HOOK_LINE_TOLERANCE = 0.5  # Line must be within ±0.5 of Mode
    HOOK_MODE_FREQUENCY_MIN = 0.25  # Mode must occur in 25%+ of games (5/20)
    
    # BAIT DETECTOR THRESHOLDS (Branched by Volume)
    # Branch 1: High Volume (Median >= 10.0)
    BAIT_HIGH_VOLUME_FLOOR = 10.0
    BAIT_HIGH_SD_MULTIPLIER = 1.5  # Line must be 1.5 SD below median
    BAIT_HIGH_ABSOLUTE_DROP = 3.0  # Line must be at least 3.0 points below
    
    # Branch 2: Mid Volume (Median 4.0 - 9.5)
    BAIT_MID_VOLUME_FLOOR = 4.0
    BAIT_MID_VOLUME_CEILING = 9.5
    BAIT_MID_ABSOLUTE_DROP = 1.5  # Line must be 1.5+ points below median
    
    # Branch 3: Micro Volume (Median < 4.0)
    BAIT_MICRO_ABSOLUTE_DROP = 1.0  # Line must be 1.0+ points below median
    
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
    def calculate_std_dev(values: List[float]) -> Optional[float]:
        """Calculate standard deviation of a list of values"""
        if len(values) < 2:
            return None
        try:
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            return round(variance ** 0.5, 2)
        except Exception:
            return None
    
    @staticmethod
    def calculate_mode_with_frequency(values: List[float]) -> Tuple[Optional[float], int, int]:
        """
        Calculate mode (most frequent value) and its frequency.
        Rounds values to nearest 0.5 for mode calculation (common betting increments).
        
        Returns:
            Tuple of (mode_value, frequency_count, total_games)
        """
        if not values:
            return None, 0, 0
        
        try:
            # Round values to nearest 0.5 for mode calculation
            rounded_values = [round(v * 2) / 2 for v in values]
            
            # Count frequencies
            from collections import Counter
            frequency = Counter(rounded_values)
            
            # Find most common
            most_common = frequency.most_common(1)
            if most_common:
                mode_val, count = most_common[0]
                return mode_val, count, len(values)
            
            return None, 0, len(values)
        except Exception:
            return None, 0, len(values)
    
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
                except (ValueError, TypeError, AttributeError):
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
        Calculate median, mode (with frequency), and standard deviation from game values.
        Uses L20 as primary source for more stable statistics.
        """
        l10 = game_values[:10] if len(game_values) >= 10 else game_values
        l20 = game_values[:20] if len(game_values) >= 20 else game_values
        
        # Calculate mode with frequency for L20
        l20_mode, l20_mode_count, l20_total = self.calculate_mode_with_frequency(l20)
        l10_mode, l10_mode_count, l10_total = self.calculate_mode_with_frequency(l10)
        
        # Calculate standard deviation
        l20_std_dev = self.calculate_std_dev(l20)
        l10_std_dev = self.calculate_std_dev(l10)
        
        return {
            "l10_median": self.calculate_median(l10),
            "l10_mode": l10_mode,
            "l10_mode_count": l10_mode_count,
            "l10_std_dev": l10_std_dev,
            "l20_median": self.calculate_median(l20),
            "l20_mode": l20_mode,
            "l20_mode_count": l20_mode_count,
            "l20_mode_frequency_pct": round(l20_mode_count / l20_total * 100, 1) if l20_total > 0 else 0,
            "l20_std_dev": l20_std_dev,
            "sample_size_l10": len(l10),
            "sample_size_l20": len(l20)
        }
    
    def detect_hook_risk(
        self, 
        line: float, 
        mode_value: Optional[float],
        mode_count: int,
        sample_size: int
    ) -> Tuple[bool, Optional[str]]:
        """
        REFINED Hook Protector Logic:
        
        Threshold A: Mode must occur in >= 25% of L20 games (5+ times in 20 games)
        Threshold B: DFS line is exactly ±0.5 points from this highly frequent Mode
        
        Returns:
            Tuple of (is_hook_risk, reason_string)
        """
        if mode_value is None or line is None or sample_size == 0:
            return False, None
        
        # Threshold A: Mode frequency must be >= 25%
        mode_frequency = mode_count / sample_size
        if mode_frequency < self.HOOK_MODE_FREQUENCY_MIN:
            return False, None
        
        # Threshold B: Line must be within ±0.5 of Mode
        diff = abs(line - mode_value)
        if diff > self.HOOK_LINE_TOLERANCE:
            return False, None
        
        # Both thresholds met - this is a Hook Risk
        freq_pct = round(mode_frequency * 100, 1)
        reason = f"Mode {mode_value} occurs {mode_count}/{sample_size} ({freq_pct}%), line is {diff} away"
        return True, reason
    
    def detect_suspect_bait(
        self, 
        line: float, 
        median_value: Optional[float],
        std_dev: Optional[float]
    ) -> Tuple[bool, Optional[str]]:
        """
        BRANCHED Bait Detector Logic based on stat volume.
        
        Branch 1: HIGH VOLUME (Median >= 10.0)
            - Stats: Points, PRA, P+R, P+A
            - Rule: Line <= (Median - 1.5*SD) AND absolute drop >= 3.0
        
        Branch 2: MID VOLUME (Median 4.0 - 9.5)
            - Stats: Assists, Rebounds
            - Rule: Line is 1.5+ points below Median (no SD/percentage)
        
        Branch 3: MICRO VOLUME (Median < 4.0)
            - Stats: Blocks, Steals, 3PM
            - Rule: Line is 1.0+ points below Median
        
        Returns:
            Tuple of (is_suspect_bait, reason_string)
        """
        if median_value is None or line is None:
            return False, None
        
        absolute_drop = median_value - line
        
        # ========== BRANCH 1: HIGH VOLUME (Median >= 10.0) ==========
        if median_value >= self.BAIT_HIGH_VOLUME_FLOOR:
            # Requires SD calculation
            if std_dev is None or std_dev <= 0:
                return False, None
            
            # Must be 1.5 SD below median
            sd_threshold = median_value - (self.BAIT_HIGH_SD_MULTIPLIER * std_dev)
            if line > sd_threshold:
                return False, None
            
            # Must have absolute drop of 3.0+ points
            if absolute_drop < self.BAIT_HIGH_ABSOLUTE_DROP:
                return False, None
            
            # HIGH VOLUME BAIT DETECTED
            sd_below = round(absolute_drop / std_dev, 2)
            reason = (
                f"HIGH VOL: Line {line} is {sd_below} SD below Median {median_value} "
                f"(drop: {round(absolute_drop, 1)} pts)"
            )
            return True, reason
        
        # ========== BRANCH 2: MID VOLUME (Median 4.0 - 9.5) ==========
        elif median_value >= self.BAIT_MID_VOLUME_FLOOR:
            # Simple rule: Line must be 1.5+ points below Median
            if absolute_drop < self.BAIT_MID_ABSOLUTE_DROP:
                return False, None
            
            # MID VOLUME BAIT DETECTED
            reason = (
                f"MID VOL: Line {line} is {round(absolute_drop, 1)} pts below Median {median_value}"
            )
            return True, reason
        
        # ========== BRANCH 3: MICRO VOLUME (Median < 4.0) ==========
        else:
            # Simple rule: Line must be 1.0+ points below Median
            if absolute_drop < self.BAIT_MICRO_ABSOLUTE_DROP:
                return False, None
            
            # MICRO VOLUME BAIT DETECTED
            reason = (
                f"MICRO VOL: Line {line} is {round(absolute_drop, 1)} pts below Median {median_value}"
            )
            return True, reason
    
    async def analyze_prop(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float
    ) -> Dict[str, Any]:
        """
        Analyze a single prop for hook risk and bait detection.
        Uses REFINED thresholds for extreme anomaly detection only.
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
        
        # Calculate advanced stats (using L20 for refined detection)
        advanced = self.calculate_advanced_stats(stat_values)
        
        # Use L20 stats for detection (more stable sample)
        l20_median = advanced.get("l20_median")
        l20_mode = advanced.get("l20_mode")
        l20_mode_count = advanced.get("l20_mode_count", 0)
        l20_sample = advanced.get("sample_size_l20", 0)
        l20_std_dev = advanced.get("l20_std_dev")
        
        # Run REFINED detectors
        hook_risk, hook_reason = self.detect_hook_risk(
            line, l20_mode, l20_mode_count, l20_sample
        )
        suspect_bait, bait_reason = self.detect_suspect_bait(
            line, l20_median, l20_std_dev
        )
        
        result = {
            "sidecar_enabled": True,
            "hook_risk": hook_risk,
            "suspect_line_bait": suspect_bait,
            "advanced_stats": {
                "median": l20_median,
                "mode": l20_mode,
                "mode_frequency_pct": advanced.get("l20_mode_frequency_pct"),
                "std_dev": l20_std_dev,
                **advanced
            }
        }
        
        # Add warning reasons
        if hook_risk and hook_reason:
            result["hook_warning"] = hook_reason
        
        if suspect_bait and bait_reason:
            result["bait_warning"] = bait_reason
        
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
            
            # Calculate advanced stats with REFINED thresholds
            if stat_values:
                advanced = self.calculate_advanced_stats(stat_values)
                
                # Use L20 for detection (more stable)
                l20_median = advanced.get("l20_median")
                l20_mode = advanced.get("l20_mode")
                l20_mode_count = advanced.get("l20_mode_count", 0)
                l20_sample = advanced.get("sample_size_l20", 0)
                l20_std_dev = advanced.get("l20_std_dev")
                
                # Detect risks with REFINED thresholds
                hook_risk, hook_reason = self.detect_hook_risk(
                    line, l20_mode, l20_mode_count, l20_sample
                )
                suspect_bait, bait_reason = self.detect_suspect_bait(
                    line, l20_median, l20_std_dev
                )
                
                # Add sidecar fields
                prop["sidecar"] = {
                    "enabled": True,
                    "hook_risk": hook_risk,
                    "suspect_line_bait": suspect_bait,
                    "median": l20_median,
                    "mode": l20_mode,
                    "mode_frequency_pct": advanced.get("l20_mode_frequency_pct"),
                    "std_dev": l20_std_dev,
                    "l10_median": advanced.get("l10_median"),
                    "l20_median": l20_median,
                }
                
                if hook_risk and hook_reason:
                    prop["sidecar"]["hook_warning"] = hook_reason
                
                if suspect_bait and bait_reason:
                    prop["sidecar"]["bait_warning"] = bait_reason
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
        Enrich a list of board picks with REFINED hook/bait detection.
        Uses strict thresholds to only flag extreme anomalies.
        """
        if not self.enabled:
            return picks
        
        enriched = []
        for pick in picks:
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            board = pick.get("board", "")
            
            # Analyze this prop with refined thresholds
            analysis = await self.analyze_prop(player_name, stat_type, line)
            
            # Add sidecar data
            pick["sidecar"] = {
                "enabled": True,
                "hook_risk": analysis.get("hook_risk", False),
                "suspect_line_bait": analysis.get("suspect_line_bait", False),
                "median": analysis.get("advanced_stats", {}).get("median"),
                "mode": analysis.get("advanced_stats", {}).get("mode"),
                "mode_frequency_pct": analysis.get("advanced_stats", {}).get("mode_frequency_pct"),
                "std_dev": analysis.get("advanced_stats", {}).get("std_dev"),
            }
            
            # Add warnings (only for true anomalies)
            if analysis.get("hook_risk"):
                pick["sidecar"]["hook_warning"] = analysis.get("hook_warning", "⚠️ Hook Risk")
            
            if analysis.get("suspect_line_bait"):
                pick["sidecar"]["bait_warning"] = analysis.get("bait_warning", "🚨 Vegas Bait")
                # Override Safe Haven status for true bait
                if board == "safe_haven":
                    pick["sidecar"]["override_board"] = True
                    pick["sidecar"]["override_reason"] = "SUSPECT LINE: Extreme Vegas Bait"
            
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
