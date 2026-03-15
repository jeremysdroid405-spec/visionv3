"""
Prop Processor Service
======================
Extracted from demon_goblin_engine.py for modularity.

Handles the core prop processing logic including:
- BallDontLie stats lookup
- NAJI Safeguard verification
- Hit rate calculation
- Injury status enrichment
"""
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from datetime import datetime, timezone
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

if TYPE_CHECKING:
    from demon_goblin_engine import DemonGoblinEngine

logger = logging.getLogger(__name__)

# Constants
GOBLIN_HIT_RATE_WARNING = 0.90


class PropProcessorService:
    """
    Service for processing individual props through the verification pipeline.
    
    Requires engine reference to be set via set_engine() after initialization.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._engine = None
    
    def set_engine(self, engine: "DemonGoblinEngine"):
        """Set engine reference for method delegation."""
        self._engine = engine
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single prop through all three pillars with V3.1 "Truth Engine" verification.
        
        V3.1 NAJI SAFEGUARD:
        - Verify playerID from game logs matches playerID from active daily roster
        - Discard data if mismatch (prevents wrong player stats)
        - Log all discrepancies for audit
        """
        if not self._engine:
            raise RuntimeError("Engine not set. Call set_engine() first.")
        
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "bdl_team": None,
            "position": None,
            "hit_rates": None,
            "injury_info": {"warning_level": "none"},
            "has_goblin_warning": False,
            "source_verified": False,
            "verification_status": "unverified",
            "verification_details": {},
            "naji_safeguard_passed": None,
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Pillar 2: BallDontLie stats
        bdl_player = await self._engine.search_bdl_player(player_name)
        if bdl_player:
            bdl_player_id = bdl_player.get("id")
            result["bdl_player_id"] = bdl_player_id
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            # Convert market name for stats lookup
            stat_market = market.replace("_alternate", "")
            
            games = await self._engine.fetch_player_season_stats(bdl_player_id)
            if games:
                # NAJI SAFEGUARD - Verify player IDs match
                naji_result = self._verify_naji_safeguard(games, bdl_player_id)
                result["naji_safeguard_passed"] = naji_result["passed"]
                
                if not naji_result["passed"]:
                    result["source_verified"] = False
                    result["verification_status"] = "NAJI_SAFEGUARD_FAILED"
                    result["verification_details"] = {
                        "reason": "Player ID mismatch in game logs",
                        "expected_player_id": bdl_player_id,
                        "mismatched_games": naji_result["mismatched_games"][:5]
                    }
                    logger.error(
                        f"[NAJI SAFEGUARD] FAILED for {player_name}: "
                        f"Expected ID {bdl_player_id}, found mismatched games: {len(naji_result['mismatched_games'])}"
                    )
                    await self._engine._log_verification_failure(
                        player_name, "naji_safeguard", result["verification_details"]
                    )
                else:
                    # Naji Safeguard passed - proceed with hit rate calculation
                    hit_rates = self._engine.calculate_hit_rates(games, stat_market, line)
                    result["hit_rates"] = hit_rates
                    
                    # Triple-check verification
                    verification_result = self._verify_hit_rates(
                        games, stat_market, line, hit_rates, player_name
                    )
                    result["source_verified"] = verification_result["verified"]
                    result["verification_status"] = verification_result["status"]
                    result["verification_details"] = verification_result["details"]
                    
                    if not verification_result["verified"] and verification_result["status"] != "verified":
                        await self._engine._log_verification_failure(
                            player_name, result["verification_status"], result["verification_details"]
                        )
            else:
                result["verification_status"] = "no_games_found"
        
        # Pillar 3: Injury check
        injury_info = self._engine.get_player_injury_status(player_name)
        result["injury_info"] = injury_info
        
        # Special warning: Goblin with high hit rate but Questionable
        if prop.get("is_goblin") and result.get("hit_rates"):
            l10_hit_rate = result["hit_rates"].get("l10", {}).get("hit_rate", 0)
            if l10_hit_rate >= GOBLIN_HIT_RATE_WARNING and injury_info["warning_level"] == "questionable":
                result["has_goblin_warning"] = True
        
        return result
    
    def _verify_naji_safeguard(
        self, games: List[Dict], expected_player_id: int
    ) -> Dict[str, Any]:
        """Verify that game log player IDs match the expected player ID."""
        mismatched_games = []
        
        for game in games:
            game_player = game.get("player", {})
            game_player_id = game_player.get("id") if isinstance(game_player, dict) else None
            
            if game_player_id is not None and game_player_id != expected_player_id:
                mismatched_games.append({
                    "expected_id": expected_player_id,
                    "found_id": game_player_id,
                    "game_date": game.get("game", {}).get("date", "unknown")
                })
        
        return {
            "passed": len(mismatched_games) == 0,
            "mismatched_games": mismatched_games
        }
    
    def _verify_hit_rates(
        self,
        games: List[Dict],
        stat_market: str,
        line: float,
        hit_rates: Dict,
        player_name: str
    ) -> Dict[str, Any]:
        """Triple-check verification of hit rates."""
        l10_data = self._engine._extract_l10_values(games[:10], stat_market)
        
        if not l10_data:
            return {
                "verified": False,
                "status": "no_game_data",
                "details": {}
            }
        
        calculated_hits = sum(1 for v in l10_data if v > line)
        calculated_rate = (calculated_hits / len(l10_data) * 100) if l10_data else 0
        claimed_rate = hit_rates.get("l10", {}).get("hit_rate", 0) * 100
        raw_avg = sum(l10_data) / len(l10_data) if l10_data else 0
        
        details = {
            "calculated_hits": calculated_hits,
            "calculated_rate": round(calculated_rate, 2),
            "claimed_rate": round(claimed_rate, 2),
            "raw_avg": round(raw_avg, 2),
            "line": line,
            "games_analyzed": len(l10_data)
        }
        
        # Detect hallucinations
        is_hallucinated = (
            claimed_rate > 80 and 
            raw_avg < line and 
            calculated_rate < 50
        )
        
        major_discrepancy = abs(claimed_rate - calculated_rate) > 20
        
        if is_hallucinated or major_discrepancy:
            status = "HALLUCINATION_DETECTED" if is_hallucinated else "DISCREPANCY"
            logger.warning(
                f"[VERIFY FAIL] {player_name} {stat_market}: "
                f"Claimed {claimed_rate:.1f}% vs Calculated {calculated_rate:.1f}% "
                f"(avg {raw_avg:.1f} vs line {line})"
            )
            return {
                "verified": False,
                "status": status,
                "details": details
            }
        
        return {
            "verified": True,
            "status": "verified",
            "details": details
        }
