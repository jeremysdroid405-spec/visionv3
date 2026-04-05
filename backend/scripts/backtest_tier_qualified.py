"""
Vegas Killer Backtest - TIER QUALIFIED ONLY
============================================
Backtests only props that would qualify for Safe Haven, Front Lines, or War Zone.

Tier Criteria:
- Safe Haven: True Probability >= 72% (Goblins only)
- Front Lines: True Probability >= 62% OR Safe Demons (PP Edge >= 15% or HitAvg >= 65%)
- War Zone: Extreme Demons (PP Edge < 10%, L10 <= 60%)

Additional Hard Kills:
- L3 < 33% (cold streak)
- L5 <= 40% (confirmed cold)
"""

import os
import sys
import logging
import json
from typing import Dict, List, Any, Optional

sys.path.insert(0, '/app/backend')

import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

# Tier Thresholds (from propvision_v7_engine.py)
TIER_SAFE_HAVEN_MIN = 72.0
TIER_FRONT_LINES_MIN = 62.0
WAR_ZONE_L10_MIN = 50.0
HARD_KILL_L3_MIN = 33.0
HARD_KILL_L5_MIN = 40.0


class TierQualifiedBacktester:
    """
    Backtests only props that qualify for tier boards.
    """
    
    BREAK_EVEN_RATE = 0.524
    
    def __init__(self, db):
        self.db = db
        self.historical_odds = db['historical_odds']
        self.historical_game_logs = db['bdl_historical_game_logs']
        self.advanced_stats = db['bdl_advanced_stats']
        
        from services.vegas_killer_model import VegasKillerModel, VegasFeatureEngineer
        self.model = VegasKillerModel(db)
        self.feature_engineer = VegasFeatureEngineer(db)
        self.model.load_models()
    
    def _get_stat_value(self, game: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from game log."""
        stat_map = {
            'PTS': ['pts', 'points'],
            'REB': ['reb', 'rebounds'],
            'AST': ['ast', 'assists'],
            '3PM': ['fg3m', 'three_pointers_made'],
            'PRA': None,
        }
        
        if stat_type == 'PRA':
            pts = self._get_stat_value(game, 'PTS')
            reb = self._get_stat_value(game, 'REB')
            ast = self._get_stat_value(game, 'AST')
            if all(v is not None for v in [pts, reb, ast]):
                return pts + reb + ast
            return None
        
        keys = stat_map.get(stat_type, [])
        for key in keys:
            if key in game and game[key] is not None:
                try:
                    return float(game[key])
                except (ValueError, TypeError):
                    continue
        return None
    
    def calculate_hit_rates(self, games: List[Dict], stat_type: str, line: float) -> Dict:
        """Calculate L3, L5, L10 hit rates."""
        values = []
        for g in games[:10]:
            val = self._get_stat_value(g, stat_type)
            if val is not None:
                values.append(val)
        
        if not values:
            return {"l3_rate": 0, "l5_rate": 0, "l10_rate": 0, "avg": 0}
        
        l3_vals = values[:3]
        l5_vals = values[:5]
        l10_vals = values[:10]
        
        l3_hits = sum(1 for v in l3_vals if v > line)
        l5_hits = sum(1 for v in l5_vals if v > line)
        l10_hits = sum(1 for v in l10_vals if v > line)
        
        return {
            "l3_rate": (l3_hits / len(l3_vals) * 100) if l3_vals else 0,
            "l5_rate": (l5_hits / len(l5_vals) * 100) if l5_vals else 0,
            "l10_rate": (l10_hits / len(l10_vals) * 100) if l10_vals else 0,
            "avg": np.mean(values),
            "l5_avg": np.mean(l5_vals) if l5_vals else 0,
        }
    
    def qualifies_for_tier(
        self, 
        vk_prob_over: float, 
        hit_rates: Dict, 
        line: float,
        is_demon: bool = False
    ) -> tuple:
        """
        Check if prop qualifies for any tier.
        Returns (qualifies, tier_name, reason)
        """
        l3_rate = hit_rates.get("l3_rate", 0)
        l5_rate = hit_rates.get("l5_rate", 0)
        l10_rate = hit_rates.get("l10_rate", 0)
        l5_avg = hit_rates.get("l5_avg", 0)
        
        # Hard kills first
        if l3_rate < HARD_KILL_L3_MIN and not is_demon:
            return False, None, f"HARD_KILL: L3 {l3_rate:.0f}% < {HARD_KILL_L3_MIN}%"
        
        if l5_rate <= HARD_KILL_L5_MIN:
            return False, None, f"HARD_KILL: L5 {l5_rate:.0f}% <= {HARD_KILL_L5_MIN}%"
        
        # Calculate edge vs line
        edge = ((l5_avg - line) / max(line, 1)) * 100 if l5_avg else 0
        hit_avg = (l5_rate + l10_rate) / 2
        
        # Determine if this would be a "goblin" (favorable odds) or "demon" (even odds)
        # For backtest, we approximate: line significantly below average = goblin
        is_goblin = l5_avg > line * 1.15  # 15% cushion
        
        # Safe Haven: High probability goblins
        if vk_prob_over >= TIER_SAFE_HAVEN_MIN and is_goblin:
            return True, "safe_haven", f"VK Prob {vk_prob_over:.0f}% >= {TIER_SAFE_HAVEN_MIN}%"
        
        # Front Lines: Medium probability OR safe demons
        if vk_prob_over >= TIER_FRONT_LINES_MIN:
            return True, "front_lines", f"VK Prob {vk_prob_over:.0f}% >= {TIER_FRONT_LINES_MIN}%"
        
        # Check for demon classification
        if is_demon or not is_goblin:
            meets_basic = l10_rate >= WAR_ZONE_L10_MIN and l5_rate > 40
            
            if meets_basic:
                # Safe demon -> Front Lines
                if edge >= 15 or hit_avg >= 65:
                    return True, "front_lines", f"Safe demon: Edge {edge:.1f}% or HitAvg {hit_avg:.0f}%"
                
                # Extreme demon -> War Zone
                if edge < 10 and l10_rate <= 60:
                    return True, "war_zone", f"Extreme demon: Edge {edge:.1f}%, L10 {l10_rate:.0f}%"
                
                # Moderate demon -> Front Lines
                return True, "front_lines", f"Moderate demon: L10 {l10_rate:.0f}%"
        
        return False, None, f"Below threshold: VK {vk_prob_over:.0f}%, L10 {l10_rate:.0f}%"
    
    def run_backtest(self, stat_type: str, confidence_threshold: float = 55.0) -> Dict:
        """Run backtest for a stat type, filtering for tier-qualified props only."""
        logger.info(f"\n{'='*60}")
        logger.info(f"TIER-QUALIFIED BACKTEST: {stat_type}")
        logger.info(f"{'='*60}")
        
        # Get historical odds
        odds_data = list(self.historical_odds.find({"stat_type": stat_type}))
        logger.info(f"Found {len(odds_data)} historical lines for {stat_type}")
        
        if not odds_data:
            return {"error": f"No historical odds for {stat_type}"}
        
        results = []
        tier_counts = {"safe_haven": 0, "front_lines": 0, "war_zone": 0, "disqualified": 0}
        disqualify_reasons = {}
        
        # Group by player
        by_player = {}
        for od in odds_data:
            player = od.get("player_name", "").strip()
            if player:
                if player not in by_player:
                    by_player[player] = []
                by_player[player].append(od)
        
        logger.info(f"Processing {len(by_player)} unique players...")
        
        processed = 0
        for player_name, player_odds in by_player.items():
            # Get player's game logs
            player_games = list(self.historical_game_logs.find(
                {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}}
            ).sort("date", -1).limit(100))
            
            if len(player_games) < 10:
                continue
            
            for od in player_odds:
                line = od.get("line")
                # Use game_date field (not date)
                game_date = od.get("game_date")
                if game_date:
                    date = game_date.strftime("%Y-%m-%d") if hasattr(game_date, 'strftime') else str(game_date)[:10]
                else:
                    continue
                
                if not line or not date:
                    continue
                
                # Get games BEFORE this date
                prior_games = [g for g in player_games if g.get("date", "") < date][:20]
                
                if len(prior_games) < 5:
                    continue
                
                # Get the actual game on this date
                actual_game = next((g for g in player_games if g.get("date", "")[:10] == date[:10]), None)
                if not actual_game:
                    continue
                
                actual_value = self._get_stat_value(actual_game, stat_type)
                if actual_value is None:
                    continue
                
                # Calculate hit rates
                hit_rates = self.calculate_hit_rates(prior_games, stat_type, line)
                
                # Get VK prediction
                try:
                    prediction = self.model.predict(
                        player_name=player_name,
                        stat_type=stat_type,
                        line=float(line)
                    )
                    
                    if not prediction or prediction.get("error"):
                        continue
                    
                    prob_over = prediction.get("prob_over", 50)
                    prob_under = prediction.get("prob_under", 50)
                    predicted = prediction.get("predicted", line)
                    
                except Exception as e:
                    continue
                
                # Check tier qualification
                qualifies, tier, reason = self.qualifies_for_tier(
                    vk_prob_over=prob_over,
                    hit_rates=hit_rates,
                    line=line
                )
                
                if not qualifies:
                    tier_counts["disqualified"] += 1
                    reason_key = reason.split(":")[0] if ":" in reason else reason
                    disqualify_reasons[reason_key] = disqualify_reasons.get(reason_key, 0) + 1
                    continue
                
                tier_counts[tier] += 1
                
                # Determine bet direction
                if prob_over >= confidence_threshold:
                    bet_over = True
                    confidence = prob_over
                elif prob_under >= confidence_threshold:
                    bet_over = False
                    confidence = prob_under
                else:
                    continue
                
                # Check if bet won
                if bet_over:
                    won = actual_value > line
                else:
                    won = actual_value < line
                
                results.append({
                    "player": player_name,
                    "date": date,
                    "line": line,
                    "actual": actual_value,
                    "predicted": predicted,
                    "prob_over": prob_over,
                    "bet_over": bet_over,
                    "won": won,
                    "tier": tier,
                    "l5_rate": hit_rates["l5_rate"],
                    "l10_rate": hit_rates["l10_rate"],
                })
                
                processed += 1
                if processed % 500 == 0:
                    logger.info(f"  Processed {processed} tier-qualified bets...")
        
        # Calculate results
        if not results:
            return {"error": "No tier-qualified bets found"}
        
        df = pd.DataFrame(results)
        total_bets = len(df)
        wins = df["won"].sum()
        win_rate = (wins / total_bets * 100) if total_bets > 0 else 0
        roi = ((win_rate / 100) - self.BREAK_EVEN_RATE) / self.BREAK_EVEN_RATE * 100
        
        # Results by tier
        tier_results = {}
        for tier in ["safe_haven", "front_lines", "war_zone"]:
            tier_df = df[df["tier"] == tier]
            if len(tier_df) > 0:
                t_wins = tier_df["won"].sum()
                t_total = len(tier_df)
                t_wr = (t_wins / t_total * 100) if t_total > 0 else 0
                t_roi = ((t_wr / 100) - self.BREAK_EVEN_RATE) / self.BREAK_EVEN_RATE * 100
                tier_results[tier] = {
                    "bets": t_total,
                    "wins": int(t_wins),
                    "win_rate": round(t_wr, 2),
                    "roi": round(t_roi, 2),
                    "profitable": t_wr > 52.4
                }
        
        logger.info(f"\n{stat_type} TIER-QUALIFIED Results:")
        logger.info(f"  Total Qualified Bets: {total_bets}")
        logger.info(f"  Win Rate: {win_rate:.2f}%")
        logger.info(f"  ROI: {roi:.2f}%")
        logger.info(f"  PROFITABLE: {win_rate > 52.4}")
        
        logger.info(f"\n  By Tier:")
        for tier, data in tier_results.items():
            logger.info(f"    {tier.upper()}: {data['bets']} bets, {data['win_rate']}% WR, {data['roi']}% ROI")
        
        logger.info(f"\n  Tier Distribution:")
        for tier, count in tier_counts.items():
            logger.info(f"    {tier}: {count}")
        
        logger.info(f"\n  Top Disqualify Reasons:")
        for reason, count in sorted(disqualify_reasons.items(), key=lambda x: -x[1])[:5]:
            logger.info(f"    {reason}: {count}")
        
        return {
            "stat_type": stat_type,
            "total_bets": total_bets,
            "wins": int(wins),
            "win_rate": round(win_rate, 2),
            "roi_percent": round(roi, 2),
            "profitable": win_rate > 52.4,
            "tier_counts": tier_counts,
            "tier_results": tier_results,
            "disqualify_reasons": dict(sorted(disqualify_reasons.items(), key=lambda x: -x[1])[:10])
        }


def main():
    logger.info("=" * 70)
    logger.info("VEGAS KILLER BACKTEST - TIER QUALIFIED PROPS ONLY")
    logger.info("=" * 70)
    
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    backtester = TierQualifiedBacktester(db)
    
    stat_types = ['PTS', 'REB', 'AST', '3PM']
    all_results = {}
    
    for stat_type in stat_types:
        result = backtester.run_backtest(stat_type, confidence_threshold=55.0)
        all_results[stat_type] = result
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("TIER-QUALIFIED BACKTEST SUMMARY")
    logger.info("=" * 70)
    
    total_bets = sum(r.get("total_bets", 0) for r in all_results.values() if isinstance(r, dict))
    total_wins = sum(r.get("wins", 0) for r in all_results.values() if isinstance(r, dict))
    overall_wr = (total_wins / total_bets * 100) if total_bets > 0 else 0
    overall_roi = ((overall_wr / 100) - 0.524) / 0.524 * 100
    
    print(f"\n{'='*70}")
    print(f"TIER-QUALIFIED ONLY - OVERALL RESULTS")
    print(f"{'='*70}")
    print(f"Total Qualified Bets: {total_bets:,}")
    print(f"Total Wins: {total_wins:,}")
    print(f"Overall Win Rate: {overall_wr:.2f}%")
    print(f"Overall ROI: {overall_roi:+.2f}%")
    print(f"\nBy Stat Type:")
    print(f"{'Stat':<6} {'Bets':<8} {'Wins':<8} {'Win%':<10} {'ROI':<10}")
    print("-" * 50)
    
    for stat, data in all_results.items():
        if isinstance(data, dict) and "total_bets" in data:
            print(f"{stat:<6} {data['total_bets']:<8} {data['wins']:<8} {data['win_rate']:<10.1f}% {data['roi_percent']:+.1f}%")
    
    print(f"\nBy Tier (All Stats Combined):")
    tier_totals = {"safe_haven": {"bets": 0, "wins": 0}, "front_lines": {"bets": 0, "wins": 0}, "war_zone": {"bets": 0, "wins": 0}}
    for stat, data in all_results.items():
        if isinstance(data, dict) and "tier_results" in data:
            for tier, t_data in data["tier_results"].items():
                tier_totals[tier]["bets"] += t_data.get("bets", 0)
                tier_totals[tier]["wins"] += t_data.get("wins", 0)
    
    print(f"{'Tier':<15} {'Bets':<8} {'Wins':<8} {'Win%':<10} {'ROI':<10}")
    print("-" * 55)
    for tier, data in tier_totals.items():
        if data["bets"] > 0:
            wr = data["wins"] / data["bets"] * 100
            roi = ((wr / 100) - 0.524) / 0.524 * 100
            print(f"{tier.upper():<15} {data['bets']:<8} {data['wins']:<8} {wr:<10.1f}% {roi:+.1f}%")
    
    # Save results
    with open('/app/backend/backtest_tier_qualified.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    logger.info("\nResults saved to /app/backend/backtest_tier_qualified.json")


if __name__ == "__main__":
    main()
