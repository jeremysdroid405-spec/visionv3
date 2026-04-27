"""
Forward-Testing Infrastructure
==============================
Automated daily prop capture system for historical tracking and performance analysis.

This service captures:
1. Daily prop snapshots from all tiers (Safe Haven, Front Lines, War Zone)
2. Tracks outcomes (hit/miss) after games complete
3. Provides historical performance metrics by tier, stat type, and time period

Collections:
- forward_test_snapshots: Daily captured props with predictions
- forward_test_outcomes: Resolved props with actual results
- forward_test_metrics: Aggregated performance statistics

Scheduled Jobs:
- 10:00 AM ET: Capture MLB props for day games
- 6:00 PM ET: Capture NBA/MLB props for evening slate
- 2:00 AM ET: Resolve outcomes from previous day's games
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase
import asyncio

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Collection names
SNAPSHOTS_COLLECTION = "forward_test_snapshots"
OUTCOMES_COLLECTION = "forward_test_outcomes"
METRICS_COLLECTION = "forward_test_metrics"

# Tier list — the canonical `tier` field on `{sport}_prop_scores`.
# The legacy per-tier collection map (ferrari_*, mlb_*) was deleted in
# the 2026-04-22 HARD CONSOLIDATION.
CANONICAL_TIERS = ("safe_haven", "front_lines", "war_zone")


class ForwardTestingService:
    """
    Automated prop capture and tracking system for forward-testing.
    
    This service enables:
    - Historical analysis of tier performance
    - Calibration validation (are 80% predictions hitting 80%?)
    - Model drift detection
    - A/B testing of threshold changes
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.snapshots = db[SNAPSHOTS_COLLECTION]
        self.outcomes = db[OUTCOMES_COLLECTION]
        self.metrics = db[METRICS_COLLECTION]
    
    # =========================================================================
    # SNAPSHOT CAPTURE
    # =========================================================================
    
    async def capture_daily_snapshot(
        self,
        sport: str,
        capture_reason: str = "scheduled"
    ) -> Dict[str, Any]:
        """
        Capture all current tier props for forward-testing.
        
        Args:
            sport: 'nba' or 'mlb'
            capture_reason: 'scheduled', 'manual', 'pre_game', etc.
            
        Returns:
            Capture results with counts per tier
        """
        sport = sport.lower()
        if sport not in TIER_COLLECTIONS:
            return {"error": f"Unknown sport: {sport}"}
        
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        
        logger.info(f"[FORWARD_TEST] Capturing {sport.upper()} props for {today_str}")
        
        results = {
            "sport": sport,
            "capture_date": today_str,
            "captured_at": now.isoformat(),
            "capture_reason": capture_reason,
            "tiers": {},
            "total_props": 0
        }
        
        # Post Hard Consolidation (2026-04-22): read from the canonical
        # `{sport}_prop_scores` collection at `final-{sport}-rt`, filtered
        # by the scoring_stack `tier` field. No legacy tier collections.
        scores_collection = self.db[f"{sport}_prop_scores"]
        version_tag = f"final-{sport}-rt"

        for tier_name in ("safe_haven", "front_lines", "war_zone"):
            props = await scores_collection.find(
                {"version_tag": version_tag, "tier": tier_name},
                {"_id": 0},
            ).to_list(length=None)
            
            tier_count = len(props)
            results["tiers"][tier_name] = tier_count
            results["total_props"] += tier_count
            
            # Store each prop as a snapshot
            for prop in props:
                snapshot = {
                    "sport": sport,
                    "tier": tier_name,
                    "capture_date": today_str,
                    "captured_at": now,
                    "capture_reason": capture_reason,
                    
                    # Core prop data
                    "player_name": prop.get("player_name"),
                    "team": prop.get("team") or prop.get("team_abbr"),
                    "opponent": prop.get("opponent"),
                    "stat_type": prop.get("stat_type") or prop.get("stat_key"),
                    "line": prop.get("line"),
                    
                    # Predictions at capture time
                    "vk_predicted": prop.get("vk_predicted") or prop.get("adjusted_vk_projected"),
                    "vk_edge": prop.get("vk_edge") or prop.get("edge"),
                    "vk_prob": prop.get("vk_prob_over") or prop.get("pinnacle_tp"),
                    "hit_rate_l20": prop.get("h20_rate") or prop.get("hit_rate_l20"),
                    "hit_rate_l10": prop.get("h10_rate") or prop.get("hit_rate_l10"),
                    "cv": prop.get("cv"),
                    
                    # Classification
                    "is_goblin": prop.get("is_goblin", False),
                    "is_demon": prop.get("is_demon", False),
                    
                    # Market data
                    "dk_odds": prop.get("dk_odds"),
                    "pinnacle_tp": prop.get("pinnacle_tp"),
                    
                    # AI Analysis
                    "intel_score": prop.get("intel_score"),
                    "intel_verdict": prop.get("intel_verdict") or prop.get("verdict"),
                    "vision_intel": prop.get("vision_intel") or prop.get("ai_summary"),

                    # 2026-04-28 — Shadow Recipe E forward-test snapshot.
                    # Captured at top level so the 7-day evaluation can
                    # join `actual_value` against μ_recency_E without
                    # parsing `full_prop_data`.
                    "mu_current": (
                        prop.get("mu_final_projection")
                        or prop.get("mu_after_availability_guard")
                        or prop.get("vk_predicted")
                        or prop.get("model_projection")
                    ),
                    "mu_recency_E": prop.get("mu_recency_E"),
                    "delta_mu_E_vs_A": prop.get("delta_mu_E_vs_A"),
                    # 2026-04-28 — Shadow VK2 PTS (PTS-only; None for other stats).
                    "mu_pts_vk2": prop.get("mu_pts_vk2"),
                    "delta_mu_pts_vk2_vs_vk1": prop.get("delta_mu_pts_vk2_vs_vk1"),
                    
                    # Game info
                    "game_time": prop.get("game_time") or prop.get("commence_time"),
                    "game_id": prop.get("game_id"),
                    
                    # Outcome tracking (to be filled later)
                    "outcome": None,  # 'hit', 'miss', 'push', 'cancelled'
                    "actual_value": None,
                    "resolved_at": None,
                    
                    # Full prop for reference
                    "full_prop_data": prop
                }
                
                # Upsert to avoid duplicates
                await self.snapshots.update_one(
                    {
                        "sport": sport,
                        "capture_date": today_str,
                        "player_name": snapshot["player_name"],
                        "stat_type": snapshot["stat_type"],
                        "line": snapshot["line"]
                    },
                    {"$set": snapshot},
                    upsert=True
                )
        
        logger.info(f"[FORWARD_TEST] Captured {results['total_props']} {sport.upper()} props:")
        for tier, count in results["tiers"].items():
            logger.info(f"  • {tier}: {count} props")
        
        return results
    
    async def capture_all_sports(self, capture_reason: str = "scheduled") -> Dict[str, Any]:
        """Capture props for all sports."""
        results = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "capture_reason": capture_reason,
            "sports": {}
        }
        
        for sport in TIER_COLLECTIONS.keys():
            results["sports"][sport] = await self.capture_daily_snapshot(sport, capture_reason)
        
        return results
    
    # =========================================================================
    # OUTCOME RESOLUTION
    # =========================================================================
    
    async def resolve_outcomes(
        self,
        sport: str,
        date: str = None,
        game_results: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Resolve outcomes for captured props.

        Args:
            sport: 'nba' or 'mlb'
            date: Date to resolve (YYYY-MM-DD), defaults to yesterday
            game_results: Optional list of game results with player stats

        Returns:
            Resolution results with hit/miss counts

        2026-05 fix: capture timestamps and game timestamps don't always
        align (NBA captures at 1830 ET roll over the UTC date for late
        west-coast games). The resolver now searches a 3-day window
        around the capture_date for each player and matches the closest
        actual game.
        """
        sport = sport.lower()

        if date is None:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date = yesterday.strftime("%Y-%m-%d")

        logger.info(f"[FORWARD_TEST] Resolving {sport.upper()} outcomes for {date}")

        results = {
            "sport": sport,
            "date": date,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "total_resolved": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "unable_to_resolve": 0,
            "by_tier": {}
        }

        # Find unresolved snapshots for this date
        unresolved = await self.snapshots.find({
            "sport": sport,
            "capture_date": date,
            "outcome": None
        }).to_list(length=None)

        logger.info(f"[FORWARD_TEST] Found {len(unresolved)} unresolved props")

        if not game_results:
            # Build a dict of {date_str: [game_results]} covering ± 1 day
            # so we can match snapshots whose game crossed midnight UTC
            # vs the capture_date.
            results_by_date: Dict[str, List[Dict]] = {}
            target_dt = datetime.strptime(date, "%Y-%m-%d")
            for offset in (-1, 0, 1):
                d = (target_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                results_by_date[d] = await self._fetch_game_results(sport, d)
        else:
            results_by_date = {date: game_results}

        # Build per-date lookup dict by player name (normalized).
        stats_by_date: Dict[str, Dict[str, Dict]] = {
            d: {(r.get("player_name") or "").strip().lower(): r for r in lst}
            for d, lst in results_by_date.items()
        }

        for snapshot in unresolved:
            player_name = snapshot.get("player_name", "")
            stat_type = snapshot.get("stat_type", "")
            line = snapshot.get("line", 0)
            tier = snapshot.get("tier", "unknown")

            normalized_name = player_name.strip().lower()

            # Initialize tier stats
            if tier not in results["by_tier"]:
                results["by_tier"][tier] = {"hits": 0, "misses": 0, "pushes": 0}

            # Look up actual stats — prefer the date implied by
            # `game_time` (true game UTC), else fall back to the
            # capture_date and adjacent ±1 days.
            preferred_date = None
            gt_iso = snapshot.get("game_time")
            if gt_iso:
                try:
                    preferred_date = (
                        datetime.fromisoformat(
                            str(gt_iso).replace("Z", "+00:00")
                        ).date().isoformat()
                    )
                except (ValueError, TypeError):
                    preferred_date = None
            # If the preferred date is outside the pre-loaded window,
            # fetch it on demand (cached for subsequent snapshots).
            if preferred_date and preferred_date not in stats_by_date:
                fetched = await self._fetch_game_results(sport, preferred_date)
                stats_by_date[preferred_date] = {
                    (r.get("player_name") or "").strip().lower(): r
                    for r in fetched
                }
            actual_stats = None
            search_order = [preferred_date, date]
            search_order += [d for d in stats_by_date.keys()
                             if d not in search_order]
            for d in search_order:
                if d and d in stats_by_date:
                    actual_stats = stats_by_date[d].get(normalized_name)
                    if actual_stats:
                        break

            if not actual_stats:
                results["unable_to_resolve"] += 1
                continue

            # Get actual value for this stat type
            actual_value = self._get_stat_value(actual_stats, stat_type, sport)

            if actual_value is None:
                results["unable_to_resolve"] += 1
                continue

            # Determine outcome
            if actual_value > line:
                outcome = "hit"
                results["hits"] += 1
                results["by_tier"][tier]["hits"] += 1
            elif actual_value == line:
                outcome = "push"
                results["pushes"] += 1
                results["by_tier"][tier]["pushes"] += 1
            else:
                outcome = "miss"
                results["misses"] += 1
                results["by_tier"][tier]["misses"] += 1

            results["total_resolved"] += 1

            # Update snapshot with outcome
            await self.snapshots.update_one(
                {"_id": snapshot["_id"]},
                {
                    "$set": {
                        "outcome": outcome,
                        "actual_value": actual_value,
                        "resolved_at": datetime.now(timezone.utc)
                    }
                }
            )
            
            # Also store in outcomes collection for faster queries
            outcome_doc = {
                **snapshot,
                "outcome": outcome,
                "actual_value": actual_value,
                "resolved_at": datetime.now(timezone.utc),
                "margin": actual_value - line if actual_value else None
            }
            outcome_doc.pop("_id", None)
            
            await self.outcomes.update_one(
                {
                    "sport": sport,
                    "capture_date": date,
                    "player_name": player_name,
                    "stat_type": stat_type,
                    "line": line
                },
                {"$set": outcome_doc},
                upsert=True
            )
        
        # Log results
        logger.info(f"[FORWARD_TEST] Resolution complete for {date}:")
        logger.info(f"  • Total Resolved: {results['total_resolved']}")
        logger.info(f"  • Hits: {results['hits']}")
        logger.info(f"  • Misses: {results['misses']}")
        logger.info(f"  • Pushes: {results['pushes']}")
        logger.info(f"  • Unable to Resolve: {results['unable_to_resolve']}")
        
        for tier, stats in results["by_tier"].items():
            total = stats["hits"] + stats["misses"] + stats["pushes"]
            hit_rate = (stats["hits"] / total * 100) if total > 0 else 0
            logger.info(f"  • {tier}: {stats['hits']}/{total} ({hit_rate:.1f}%)")
        
        # Update aggregated metrics
        await self._update_metrics(sport, date, results)
        
        return results
    
    async def _fetch_game_results(self, sport: str, date: str) -> List[Dict]:
        """Fetch game results from BDL game logs (2026-05 — switched to
        master_hub which has full season coverage; cached_board only
        carries the current slate's 122 players).

        Note: NBA master-hub uses `display_name` as the canonical name
        field (`player_name` is None). MLB master-hub stores `player_name`
        on each game-log entry. We pull the right field per sport.
        """
        results = []
        hub_collection = (
            "nba_master_hub_2026" if sport == "nba" else "mlb_master_hub_2026"
        )
        async for player in self.db[hub_collection].find(
            {},
            {"player_name": 1, "display_name": 1, "bdl_game_logs": 1,
             "bdl_player_id": 1, "bdl_id": 1, "_id": 0}
        ):
            game_logs = player.get("bdl_game_logs") or []
            # Resolve canonical player name by sport.
            canonical_name = (
                player.get("player_name") or player.get("display_name")
            )
            for game in game_logs:
                game_date = (game.get("date") or "")[:10]
                if game_date == date:
                    # MLB game-log rows carry `player_name`; NBA do not.
                    name = (
                        game.get("player_name")
                        or canonical_name
                    )
                    results.append({
                        "player_name": name,
                        "bdl_player_id": (
                            player.get("bdl_player_id")
                            or player.get("bdl_id")
                        ),
                        **game,
                        "player_name_canonical": canonical_name,
                    })
                    # Don't break — for batters there's only one row
                    # per date, but allow multi-game days (rare).
                    break

        # Legacy fallback: cached_board (in case master_hub is mid-sync).
        if not results:
            from services.config.collection_names import COLL
            board_coll = (
                COLL("board_cache", "nba") if sport == "nba"
                else COLL("board_cache", "mlb")
            )
            async for player in self.db[board_coll].find(
                {}, {"player_name": 1, "game_logs": 1, "bdl_game_logs": 1,
                     "_id": 0}
            ):
                game_logs = player.get("game_logs") or player.get("bdl_game_logs") or []
                for game in game_logs:
                    game_date = (game.get("date") or "")[:10]
                    if game_date == date:
                        results.append({
                            "player_name": player.get("player_name"),
                            **game,
                        })
                        break
        return results
    
    def _get_stat_value(self, stats: Dict, stat_type: str, sport: str) -> Optional[float]:
        """Extract stat value from game stats."""
        # 2026-05 forward-test resolver fix:
        #   • Capture writes DK-style display names like
        #     "Batter Strikeouts" / "Hits+Runs+RBIs". Normalize to
        #     uppercase + underscores before mapping.
        stat_type_norm = (
            (stat_type or "")
            .upper()
            .replace(" ", "_")
            .replace("+", "_PLUS_")
            .replace("__", "_")
            .strip("_")
        )

        # NBA stat mappings
        nba_mappings = {
            "PTS": ["pts", "points"],
            "REB": ["reb", "rebounds", "total_rebounds"],
            "AST": ["ast", "assists"],
            "PRA": None,  # Calculated
            "3PM": ["fg3m", "three_pointers_made"],
            "STL": ["stl", "steals"],
            "BLK": ["blk", "blocks"],
            "TO": ["turnover", "turnovers"]
        }

        # MLB stat mappings (covers short codes + DK display names normalized)
        mlb_mappings = {
            # Batter
            "HITS": ["hits", "h"],
            "TB": ["total_bases", "tb"],
            "TOTAL_BASES": ["total_bases", "tb"],
            "HR": ["home_runs", "hr"],
            "HOME_RUNS": ["home_runs", "hr"],
            "RBI": ["rbis", "rbi"],
            "RBIS": ["rbis", "rbi"],
            "RUNS": ["runs", "r"],
            "SINGLES": None,  # Calculated: hits - doubles - triples - HR
            "DOUBLES": ["doubles"],
            "TRIPLES": ["triples"],
            "BATTER_STRIKEOUTS": ["strikeouts", "so"],
            "BATTER_WALKS": ["walks", "bb"],
            "BB": ["walks", "bb"],
            "WALKS": ["walks", "bb"],
            "STOLEN_BASES": ["stolen_bases", "sb"],
            "HRR": None,  # Calculated: H + R + RBI
            "HITS_PLUS_RUNS_PLUS_RBIS": None,  # Calculated alias
            # Pitcher
            "K": ["pitcher_strikeouts", "strikeouts_pitcher", "so"],
            "PITCHER_STRIKEOUTS": ["pitcher_strikeouts"],
            "PITCHER_WALKS": ["pitcher_walks"],
            "WALKS_ALLOWED": ["pitcher_walks"],
            "HITS_ALLOWED": ["hits_allowed"],
            "EARNED_RUNS": ["earned_runs"],
            "OUTS": ["outs_recorded", "outs"],
        }

        mappings = nba_mappings if sport == "nba" else mlb_mappings

        # Handle calculated stats
        if stat_type_norm == "PRA":
            pts = self._get_first_match(stats, ["pts", "points"]) or 0
            reb = self._get_first_match(stats, ["reb", "rebounds", "total_rebounds"]) or 0
            ast = self._get_first_match(stats, ["ast", "assists"]) or 0
            return pts + reb + ast

        if stat_type_norm in ("HRR", "HITS_PLUS_RUNS_PLUS_RBIS"):
            hits = self._get_first_match(stats, ["hits", "h"]) or 0
            runs = self._get_first_match(stats, ["runs", "r"]) or 0
            rbi = self._get_first_match(stats, ["rbis", "rbi"]) or 0
            return hits + runs + rbi

        if stat_type_norm == "SINGLES":
            hits = self._get_first_match(stats, ["hits", "h"]) or 0
            doubles = self._get_first_match(stats, ["doubles"]) or 0
            triples = self._get_first_match(stats, ["triples"]) or 0
            hr = self._get_first_match(stats, ["home_runs", "hr"]) or 0
            return max(0.0, hits - doubles - triples - hr)

        # Direct lookup
        keys = mappings.get(stat_type_norm)
        if keys:
            return self._get_first_match(stats, keys)

        # Fallback: try stat_type as key
        return stats.get(stat_type.lower()) or stats.get(stat_type)
    
    def _get_first_match(self, data: Dict, keys: List[str]) -> Optional[float]:
        """Get first matching key from dict."""
        for key in keys:
            if key in data and data[key] is not None:
                try:
                    return float(data[key])
                except (ValueError, TypeError):
                    pass
        return None
    
    # =========================================================================
    # METRICS & REPORTING
    # =========================================================================
    
    async def _update_metrics(self, sport: str, date: str, results: Dict):
        """Update aggregated metrics after resolution."""
        metrics_doc = {
            "sport": sport,
            "date": date,
            "updated_at": datetime.now(timezone.utc),
            "total_props": results["total_resolved"],
            "hits": results["hits"],
            "misses": results["misses"],
            "pushes": results["pushes"],
            "overall_hit_rate": (results["hits"] / results["total_resolved"] * 100) 
                if results["total_resolved"] > 0 else 0,
            "by_tier": {}
        }
        
        for tier, stats in results["by_tier"].items():
            total = stats["hits"] + stats["misses"] + stats["pushes"]
            metrics_doc["by_tier"][tier] = {
                "total": total,
                "hits": stats["hits"],
                "misses": stats["misses"],
                "pushes": stats["pushes"],
                "hit_rate": (stats["hits"] / total * 100) if total > 0 else 0
            }
        
        await self.metrics.update_one(
            {"sport": sport, "date": date},
            {"$set": metrics_doc},
            upsert=True
        )
    
    async def get_performance_summary(
        self,
        sport: str = None,
        days: int = 30,
        tier: str = None
    ) -> Dict[str, Any]:
        """
        Get aggregated performance metrics.
        
        Args:
            sport: Filter by sport (optional)
            days: Number of days to analyze
            tier: Filter by specific tier (optional)
            
        Returns:
            Performance summary with hit rates by tier
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        
        # Build query
        match_query = {"capture_date": {"$gte": cutoff_str}, "outcome": {"$ne": None}}
        if sport:
            match_query["sport"] = sport.lower()
        if tier:
            match_query["tier"] = tier.lower()
        
        # Aggregate by sport and tier
        pipeline = [
            {"$match": match_query},
            {
                "$group": {
                    "_id": {"sport": "$sport", "tier": "$tier"},
                    "total": {"$sum": 1},
                    "hits": {"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
                    "misses": {"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
                    "pushes": {"$sum": {"$cond": [{"$eq": ["$outcome", "push"]}, 1, 0]}},
                    "avg_vk_edge": {"$avg": "$vk_edge"},
                    "avg_intel_score": {"$avg": "$intel_score"}
                }
            },
            {"$sort": {"_id.sport": 1, "_id.tier": 1}}
        ]
        
        results = await self.outcomes.aggregate(pipeline).to_list(length=None)
        
        summary = {
            "period_days": days,
            "start_date": cutoff_str,
            "end_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "filters": {"sport": sport, "tier": tier},
            "overall": {"total": 0, "hits": 0, "misses": 0, "pushes": 0},
            "by_sport_tier": []
        }
        
        for r in results:
            sport_name = r["_id"]["sport"]
            tier_name = r["_id"]["tier"]
            total = r["total"]
            hits = r["hits"]
            
            entry = {
                "sport": sport_name,
                "tier": tier_name,
                "total": total,
                "hits": hits,
                "misses": r["misses"],
                "pushes": r["pushes"],
                "hit_rate": round((hits / total * 100), 1) if total > 0 else 0,
                "avg_vk_edge": round(r.get("avg_vk_edge") or 0, 2),
                "avg_intel_score": round(r.get("avg_intel_score") or 0, 1)
            }
            
            summary["by_sport_tier"].append(entry)
            summary["overall"]["total"] += total
            summary["overall"]["hits"] += hits
            summary["overall"]["misses"] += r["misses"]
            summary["overall"]["pushes"] += r["pushes"]
        
        if summary["overall"]["total"] > 0:
            summary["overall"]["hit_rate"] = round(
                summary["overall"]["hits"] / summary["overall"]["total"] * 100, 1
            )
        else:
            summary["overall"]["hit_rate"] = 0
        
        return summary
    
    async def get_daily_breakdown(
        self,
        sport: str,
        days: int = 14
    ) -> List[Dict]:
        """Get day-by-day performance breakdown."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        
        pipeline = [
            {
                "$match": {
                    "sport": sport.lower(),
                    "date": {"$gte": cutoff_str}
                }
            },
            {"$sort": {"date": -1}}
        ]
        
        return await self.metrics.aggregate(pipeline).to_list(length=None)
    
    async def get_calibration_report(self, sport: str = None) -> Dict[str, Any]:
        """
        Generate calibration report to validate model accuracy.
        
        Compares predicted hit rates vs actual hit rates across
        probability buckets to detect systematic over/under-prediction.
        """
        match_query = {"outcome": {"$ne": None}}
        if sport:
            match_query["sport"] = sport.lower()
        
        # Group by VK probability buckets
        pipeline = [
            {"$match": match_query},
            {
                "$bucket": {
                    "groupBy": "$vk_prob",
                    "boundaries": [0, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100],
                    "default": "other",
                    "output": {
                        "count": {"$sum": 1},
                        "hits": {"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
                        "avg_predicted": {"$avg": "$vk_prob"}
                    }
                }
            }
        ]
        
        results = await self.outcomes.aggregate(pipeline).to_list(length=None)
        
        calibration = {
            "sport": sport,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "buckets": []
        }
        
        for bucket in results:
            predicted = bucket.get("avg_predicted", 0)
            actual = (bucket["hits"] / bucket["count"] * 100) if bucket["count"] > 0 else 0
            
            calibration["buckets"].append({
                "probability_range": bucket["_id"],
                "sample_size": bucket["count"],
                "predicted_hit_rate": round(predicted, 1),
                "actual_hit_rate": round(actual, 1),
                "calibration_error": round(actual - predicted, 1)
            })
        
        return calibration
    
    async def get_snapshot_status(self) -> Dict[str, Any]:
        """Get current status of forward-testing system."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Count snapshots
        total_snapshots = await self.snapshots.count_documents({})
        today_snapshots = await self.snapshots.count_documents({"capture_date": today})
        unresolved = await self.snapshots.count_documents({"outcome": None})
        
        # Count outcomes
        total_outcomes = await self.outcomes.count_documents({})
        
        # Get date range
        oldest = await self.snapshots.find_one({}, sort=[("capture_date", 1)])
        newest = await self.snapshots.find_one({}, sort=[("capture_date", -1)])
        
        return {
            "total_snapshots": total_snapshots,
            "today_snapshots": today_snapshots,
            "unresolved_snapshots": unresolved,
            "total_outcomes": total_outcomes,
            "date_range": {
                "oldest": oldest.get("capture_date") if oldest else None,
                "newest": newest.get("capture_date") if newest else None
            },
            "checked_at": datetime.now(timezone.utc).isoformat()
        }


# Singleton instance
_forward_testing_service: Optional[ForwardTestingService] = None


def get_forward_testing_service(db: AsyncIOMotorDatabase) -> ForwardTestingService:
    """Get or create the forward-testing service."""
    global _forward_testing_service
    if _forward_testing_service is None:
        _forward_testing_service = ForwardTestingService(db)
    return _forward_testing_service
