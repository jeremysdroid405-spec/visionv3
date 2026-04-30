"""
Hit Rate Calculator
===================
Stateless service for calculating hit rates from game logs.
Extracted from picks_getter_service.py for modularity.
"""

from typing import Dict, List, Optional, Any
from .game_utils import did_play, normalize_stat_key
from services.observability import log_silent_failure


class HitRateCalculator:
    """
    Calculates hit rates and averages from game logs.
    
    All methods are stateless and can be called directly.
    """
    
    @staticmethod
    def get_stat_value(game: Dict, stat_type: str) -> float:
        """
        Get stat value from game log, handling composite stats.
        
        Args:
            game: Single game log dictionary
            stat_type: Stat type (PTS, REB, AST, PRA, etc.)
            
        Returns:
            Stat value as float
        """
        stat_upper = stat_type.upper()
        
        # Handle composite stats
        if stat_upper == 'PRA':
            return game.get('pts', 0) + game.get('reb', 0) + game.get('ast', 0)
        elif stat_upper == 'PR':
            return game.get('pts', 0) + game.get('reb', 0)
        elif stat_upper == 'PA':
            return game.get('pts', 0) + game.get('ast', 0)
        elif stat_upper == 'RA':
            return game.get('reb', 0) + game.get('ast', 0)
        elif stat_upper == '3PM':
            return game.get('fg3m', 0)
        elif stat_upper == 'TOV':
            return game.get('turnover', 0)
        else:
            # Direct stat lookup
            key = normalize_stat_key(stat_type)
            return game.get(key, game.get(stat_type.lower(), 0))
    
    @staticmethod
    def calculate_l5_avg(game_logs: List[Dict], stat_type: str) -> Dict[str, Any]:
        """
        Calculate last 5 games average for a stat.
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to calculate
            
        Returns:
            {
                "l5_avg": float,
                "l5_games": int,
                "l5_total": float
            }
        """
        # Filter to games where player actually played
        played_games = [g for g in game_logs if did_play(g)][:5]
        
        if not played_games:
            return {"l5_avg": None, "l5_games": 0, "l5_total": 0}
        
        total = sum(HitRateCalculator.get_stat_value(g, stat_type) for g in played_games)
        avg = total / len(played_games)
        
        return {
            "l5_avg": round(avg, 1),
            "l5_games": len(played_games),
            "l5_total": round(total, 1)
        }
    
    @staticmethod
    def calculate_l10_avg(game_logs: List[Dict], stat_type: str) -> Dict[str, Any]:
        """
        Calculate last 10 games average for a stat.
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to calculate
            
        Returns:
            {
                "l10_avg": float,
                "l10_games": int,
                "l10_total": float,
                "std_dev": float
            }
        """
        import statistics
        
        played_games = [g for g in game_logs if did_play(g)][:10]
        
        if not played_games:
            return {"l10_avg": None, "l10_games": 0, "l10_total": 0, "std_dev": None}
        
        values = [HitRateCalculator.get_stat_value(g, stat_type) for g in played_games]
        total = sum(values)
        avg = total / len(values)
        
        # Calculate standard deviation if we have enough games
        std_dev = None
        if len(values) >= 3:
            try:
                std_dev = round(statistics.stdev(values), 2)
            except Exception as _swept_exc:
                log_silent_failure("services.picks.hit_rate_service.calculate_l10_avg", _swept_exc)  # sweep-auto-converted
        
        return {
            "l10_avg": round(avg, 1),
            "l10_games": len(played_games),
            "l10_total": round(total, 1),
            "std_dev": std_dev
        }
    
    @staticmethod
    def calculate_h5_hit_rate(game_logs: List[Dict], stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate hit rate for last 5 games.
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to check
            line: The betting line to check against
            
        Returns:
            {
                "h5_rate": float (0-100),
                "h5_hits": int,
                "h5_games": int,
                "h5_values": list
            }
        """
        played_games = [g for g in game_logs if did_play(g)][:5]
        
        if not played_games:
            return {"h5_rate": None, "h5_hits": 0, "h5_games": 0, "h5_values": []}
        
        values = [HitRateCalculator.get_stat_value(g, stat_type) for g in played_games]
        hits = sum(1 for v in values if v >= line)
        rate = (hits / len(values)) * 100
        
        return {
            "h5_rate": round(rate, 1),
            "h5_hits": hits,
            "h5_games": len(played_games),
            "h5_values": [round(v, 1) for v in values]
        }
    
    @staticmethod
    def calculate_h10_hit_rate(game_logs: List[Dict], stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate hit rate for last 10 games.
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to check
            line: The betting line to check against
            
        Returns:
            {
                "h10_rate": float (0-100),
                "h10_hits": int,
                "h10_games": int,
                "h10_values": list
            }
        """
        played_games = [g for g in game_logs if did_play(g)][:10]
        
        if not played_games:
            return {"h10_rate": None, "h10_hits": 0, "h10_games": 0, "h10_values": []}
        
        values = [HitRateCalculator.get_stat_value(g, stat_type) for g in played_games]
        hits = sum(1 for v in values if v >= line)
        rate = (hits / len(values)) * 100
        
        return {
            "h10_rate": round(rate, 1),
            "h10_hits": hits,
            "h10_games": len(played_games),
            "h10_values": [round(v, 1) for v in values]
        }
    
    @staticmethod
    def calculate_l25_hit_rate(game_logs: List[Dict], stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate hit rate for last 25 games (season sample).
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to check
            line: The betting line to check against
            
        Returns:
            {
                "l25_rate": float (0-100),
                "l25_hits": int,
                "l25_games": int
            }
        """
        played_games = [g for g in game_logs if did_play(g)][:25]
        
        if not played_games:
            return {"l25_rate": None, "l25_hits": 0, "l25_games": 0}
        
        values = [HitRateCalculator.get_stat_value(g, stat_type) for g in played_games]
        hits = sum(1 for v in values if v >= line)
        rate = (hits / len(values)) * 100
        
        return {
            "l25_rate": round(rate, 1),
            "l25_hits": hits,
            "l25_games": len(played_games)
        }
    
    @staticmethod
    def calculate_full_stats(
        game_logs: List[Dict], 
        stat_type: str, 
        line: float,
        season_avg: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate all stats for a prop line.
        
        Args:
            game_logs: List of game logs (newest first)
            stat_type: Stat type to analyze
            line: The betting line
            season_avg: Optional season average to include
            
        Returns:
            Complete stats dictionary with all hit rates and averages
        """
        l5_avg = HitRateCalculator.calculate_l5_avg(game_logs, stat_type)
        l10_avg = HitRateCalculator.calculate_l10_avg(game_logs, stat_type)
        h5 = HitRateCalculator.calculate_h5_hit_rate(game_logs, stat_type, line)
        h10 = HitRateCalculator.calculate_h10_hit_rate(game_logs, stat_type, line)
        l25 = HitRateCalculator.calculate_l25_hit_rate(game_logs, stat_type, line)
        
        # Calculate margin vs line
        margin_l5 = l5_avg.get('l5_avg', 0) - line if l5_avg.get('l5_avg') else None
        margin_l10 = l10_avg.get('l10_avg', 0) - line if l10_avg.get('l10_avg') else None
        margin_season = season_avg - line if season_avg else None
        
        return {
            # Averages
            "l5_avg": l5_avg.get("l5_avg"),
            "l10_avg": l10_avg.get("l10_avg"),
            "season_avg": season_avg,
            "std_dev_l10": l10_avg.get("std_dev"),
            
            # Hit rates
            "h5_rate": h5.get("h5_rate"),
            "h10_rate": h10.get("h10_rate"),
            "l25_rate": l25.get("l25_rate"),
            
            # Hit counts
            "h5_hits": h5.get("h5_hits"),
            "h10_hits": h10.get("h10_hits"),
            "l25_hits": l25.get("l25_hits"),
            
            # Games analyzed
            "h5_games": h5.get("h5_games"),
            "h10_games": h10.get("h10_games"),
            "l25_games": l25.get("l25_games"),
            
            # Margins
            "margin_l5": round(margin_l5, 1) if margin_l5 else None,
            "margin_l10": round(margin_l10, 1) if margin_l10 else None,
            "margin_season": round(margin_season, 1) if margin_season else None,
            
            # Line reference
            "line": line
        }
