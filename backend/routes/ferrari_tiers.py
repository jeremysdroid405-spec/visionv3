"""
Ferrari Tier Routes
===================
API endpoints for the "Best of the Best" Ferrari-filtered picks.

Uses Bovada separation as the primary sharp benchmark.
Global 15% kill-switch ensures only elite plays are visible.
Whistle Matrix applies referee-based modifiers to power scores.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Dict, Any, List
import logging
import os

from services.ferrari_tier_service import get_ferrari_tier_service
from services.referee_scraper_service import get_referee_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Ferrari Tiers"])

# Engine reference for DB access
_db = None
_vegas_killer_model = None
_sync_db = None


def set_ferrari_db(db):
    """Set the database reference for Ferrari service."""
    global _db
    _db = db


def get_service():
    """Get the Ferrari tier service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Ferrari service not initialized")
    return get_ferrari_tier_service(_db)


def get_vegas_killer():
    """Get or initialize Vegas Killer model instance using sync PyMongo."""
    global _vegas_killer_model, _sync_db
    if _vegas_killer_model is None:
        try:
            from services.vegas_killer_model import VegasKillerModel
            from pymongo import MongoClient
            
            # Create sync MongoDB connection for VK model
            if _sync_db is None:
                mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
                db_name = os.environ.get("DB_NAME", "pick_vision")
                client = MongoClient(mongo_url)
                _sync_db = client[db_name]
            
            _vegas_killer_model = VegasKillerModel(_sync_db)
            _vegas_killer_model.load_models()
            logger.info("[VK-Ferrari] Vegas Killer model loaded for Ferrari tier enrichment")
        except Exception as e:
            logger.warning(f"[VK-Ferrari] Failed to load Vegas Killer model: {e}")
    return _vegas_killer_model


def enrich_picks_with_vk(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich picks with Vegas Killer ML predictions."""
    vk_model = get_vegas_killer()
    if not vk_model:
        return picks
    
    for pick in picks:
        try:
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "")
            line = pick.get("line")
            opponent = pick.get("opponent") or pick.get("opponent_abbr")
            
            if not player_name or not stat_type or line is None:
                continue
            
            result = vk_model.predict(
                player_name=player_name,
                stat_type=stat_type,
                line=float(line),
                opponent_team=opponent
            )
            
            if result and not result.get("error") and result.get("predicted") is not None:
                predicted = result.get("predicted")
                edge = result.get("edge")
                prob_over = result.get("prob_over", 50)
                prob_under = result.get("prob_under", 50)
                
                # Recommendation logic
                if prob_over >= 70:
                    recommendation = "STRONG_OVER"
                elif prob_over >= 55:
                    recommendation = "LEAN_OVER"
                elif prob_under >= 70:
                    recommendation = "STRONG_UNDER"
                elif prob_under >= 55:
                    recommendation = "LEAN_UNDER"
                else:
                    recommendation = "NEUTRAL"
                
                pick["vk_predicted"] = float(predicted) if predicted else None
                pick["vk_edge"] = float(edge) if edge else None
                pick["vk_prob_over"] = float(prob_over)
                pick["vk_prob_under"] = float(prob_under)
                pick["vk_recommendation"] = recommendation
                pick["vk_data_source"] = result.get("data_source", "PROXY")
                
                # Include FULL feature breakdown for deep intel
                if result.get("full_features"):
                    pick["vk_full_features"] = result["full_features"]
                if result.get("v2_advanced_stats"):
                    pick["vk_v2_stats"] = result["v2_advanced_stats"]
        except Exception as e:
            logger.warning(f"[VK-Ferrari] Failed to enrich {pick.get('player_name')}: {e}")
    
    return picks


@router.get("/v3/ferrari/oracle-apex")
async def get_oracle_apex_picks(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    ORACLE APEX - ML-powered Safe Haven picks.
    
    NEW TIER LOGIC using Vegas Killer predictions with stat-specific gates:
    
    | Stat | Max CV | Hit Rate | Min Edge |
    |------|--------|----------|----------|
    | PTS  | 0.22   | 18/20    | 2.0      |
    | REB  | 0.35   | 16/20*   | 1.5      |
    | AST  | 0.35   | 15/20    | 2.0      |
    | PRA  | 0.20   | 18/20    | 2.0      |
    
    *REB: 14/20 OK if L20 Mean >= Line + 2.5
    
    Additional filters:
    - Minutes >= 22
    - Dedupe: Lowest line per player+stat (best goblin)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        from services.oracle_apex_service import get_oracle_apex_service
        
        vk_model = get_vegas_killer()
        if not vk_model:
            raise HTTPException(status_code=500, detail="Vegas Killer model not available")
        
        oracle_apex = get_oracle_apex_service(_db, vk_model)
        result = await oracle_apex.scan_all_props()
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
        
        picks = result.get('apex_picks', [])[:limit]
        
        return {
            "tier": "oracle_apex",
            "tier_label": "Oracle Apex (Safe Haven)",
            "description": "ML-powered mathematically-proven safe plays",
            "picks": picks,
            "count": len(picks),
            "total_scanned": result.get('total_scanned', 0),
            "gate_stats": result.get('gate_stats', {}),
            "config": {
                "PTS": {"max_cv": 0.22, "hit_rate": "18/20", "min_edge": 2.0},
                "REB": {"max_cv": 0.35, "hit_rate": "16/20 (14/20 w/ buffer)", "min_edge": 1.5},
                "AST": {"max_cv": 0.35, "hit_rate": "15/20", "min_edge": 2.0},
                "PRA": {"max_cv": 0.20, "hit_rate": "18/20", "min_edge": 2.0},
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORACLE_APEX] Endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/ferrari/safe-haven")
async def get_ferrari_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    legacy: bool = Query(False, description="Use legacy Safe Haven logic instead of stored data")
):
    """
    FERRARI SAFE HAVEN - Returns stored picks with Vision Intel data.
    
    Picks are populated by the rebuild endpoint which runs:
    1. Oracle Apex 3-Gate qualification
    2. Vision Intel (Gemini) analysis and gating
    3. Composite scoring and final selection
    
    Use ?legacy=true to bypass stored data and run live Oracle Apex scan.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    if legacy:
        # Legacy behavior - run live Oracle Apex scan (no Vision Intel)
        try:
            from services.oracle_apex_service import get_oracle_apex_service
            vk_model = get_vegas_killer()
            if not vk_model:
                raise HTTPException(status_code=500, detail="Vegas Killer model not available")
            
            oracle_apex = get_oracle_apex_service(_db, vk_model)
            result = await oracle_apex.scan_all_props()
            
            if not result.get('success'):
                raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
            
            picks = result.get('apex_picks', [])[:limit]
            return {
                "tier": "safe_haven",
                "tier_label": "Safe Haven (Live Scan)",
                "logic": "oracle_apex_live",
                "picks": picks,
                "count": len(picks),
                "note": "Live scan - Vision Intel not applied. Use rebuild for full analysis."
            }
        except Exception as e:
            logger.error(f"[SAFE_HAVEN] Legacy scan error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # DEFAULT: Read from stored collection (includes Vision Intel data)
    service = get_service()
    result = await service.get_safe_haven(limit)
    
    # Add tier metadata
    result["tier_label"] = "Safe Haven (Oracle Apex + Vision Intel)"
    result["logic"] = "stored_with_vision_intel"
    
    return result


@router.get("/v3/ferrari/front-lines")
async def get_ferrari_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    FERRARI FRONT LINES - Returns stored picks with Vision Intel data.
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    result = await service.get_front_lines(limit)
    
    result["tier_label"] = "Front Lines (Vision Intel)"
    result["logic"] = "stored_with_vision_intel"
    
    return result


@router.get("/v3/ferrari/war-zone")
async def get_ferrari_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    FERRARI WAR ZONE - Returns stored high-risk/high-reward picks with Vision Intel.
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    result = await service.get_war_zone(limit)
    
    result["tier_label"] = "War Zone (Vision Intel)"
    result["logic"] = "stored_with_vision_intel"
    
    return result


@router.get("/v3/ferrari/discarded")
async def get_ferrari_discarded(
    response: Response,
    limit: int = Query(50, ge=1, le=100)
):
    """
    FERRARI DISCARDED - Props killed by the 15% separation filter.
    
    Shows what was filtered out for being "mid" plays.
    Useful for debugging and transparency.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_discarded(limit)


@router.post("/v3/ferrari/rebuild")
async def rebuild_ferrari_tiers(use_optimized: bool = True):
    """
    Manually trigger a rebuild of all Ferrari tiers.
    
    With use_optimized=True (default):
    1. Fetches ALL global data in parallel (standings, refs, momentum, vacuums)
    2. Runs Ferrari pipeline with power score calculation
    3. Enriches all picks with cached data
    4. Generates AI summaries in batches (rate-limited)
    5. Persists enriched data to dg_cached_board
    
    Target: Complete sync in under 5 seconds (excluding AI summaries)
    
    With use_optimized=False:
    - Falls back to legacy sequential pipeline
    """
    from datetime import datetime, timezone
    
    if use_optimized:
        # Use the new optimized sync engine
        from services.optimized_sync_engine import run_optimized_sync
        result = await run_optimized_sync(_db)
        return result
    else:
        # Legacy path
        service = get_service()
        result = await service.build_ferrari_tiers(datetime.now(timezone.utc))
        return result


@router.post("/v3/ferrari/sync-refs")
async def sync_referee_data():
    """
    Manually sync referee assignments and stats.
    
    Fetches:
    - Daily assignments from official.nba.com
    - Referee O/U and PPG stats from Covers.com
    
    Returns whistle classifications for today's crews.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    result = await ref_service.sync_all()
    return result


@router.get("/v3/ferrari/refs")
async def get_todays_refs(response: Response):
    """
    Get today's referee assignments with whistle classifications.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    
    # Return cached assignments - convert dict_values to list explicitly
    assignments = list(ref_service.daily_assignments_cache.values()) if ref_service.daily_assignments_cache else []
    
    # Dedupe (same game appears for both teams)
    seen_games = set()
    unique_assignments = []
    for a in assignments:
        # Ensure a is a dict
        if not isinstance(a, dict):
            continue
        game = a.get("game", "")
        if game not in seen_games:
            seen_games.add(game)
            # Enrich with stats
            crew_chief = a.get("crew_chief", "")
            normalized = ref_service._normalize_ref_name(crew_chief)
            stats = ref_service.referee_stats_cache.get(normalized, {})
            # Build a clean dict without any non-serializable objects
            unique_assignments.append({
                "game": a.get("game"),
                "away_team": a.get("away_team"),
                "home_team": a.get("home_team"),
                "crew_chief": a.get("crew_chief"),
                "referee": a.get("referee"),
                "umpire": a.get("umpire"),
                "date": a.get("date"),
                "ppg": stats.get("ppg"),
                "ou_pct": stats.get("ou_pct"),
                "whistle_class": stats.get("whistle_class", "neutral")
            })
    
    # Get date safely
    date_str = None
    if ref_service.last_assignments_fetch:
        try:
            date_str = ref_service.last_assignments_fetch.strftime("%Y-%m-%d")
        except Exception:
            date_str = None
    
    return {
        "date": date_str,
        "assignments": unique_assignments,
        "total_refs_in_cache": len(ref_service.referee_stats_cache) if ref_service.referee_stats_cache else 0,
        "total_games": len(unique_assignments)
    }


@router.get("/v3/ferrari/all")
async def get_all_ferrari_tiers(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get all Ferrari tiers in a single response.
    
    Returns:
    - safe_haven: Top 10 elite goblins
    - front_lines: Top 10 battleground picks
    - war_zone: Top 10 elite demons
    - verification: Market Intel stats
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    safe_haven = await service.get_safe_haven(limit)
    front_lines = await service.get_front_lines(limit)
    war_zone = await service.get_war_zone(limit)
    
    # Get verification stats from any tier (they all share the same stats)
    verification = safe_haven.get("verification", {})
    active_props = verification.get("active_props_verified", 0)
    output_total = safe_haven.get("count", 0) + front_lines.get("count", 0) + war_zone.get("count", 0)
    
    return {
        "safe_haven": safe_haven,
        "front_lines": front_lines,
        "war_zone": war_zone,
        "verification": {
            "active_props_verified": active_props,
            "elite_opportunities": output_total,
            "safe_haven_pool": verification.get("safe_haven_pool", 0),
            "front_lines_pool": verification.get("front_lines_pool", 0),
            "war_zone_pool": verification.get("war_zone_pool", 0),
            "message": f"Verified {active_props} active props to identify these {output_total} Elite opportunities."
        }
    }


@router.get("/v3/ferrari/parlays")
async def get_ferrari_parlays(
    response: Response,
    tier: str = Query(None, description="Filter by tier: safe_haven, front_lines, war_zone")
):
    """
    Get PropVision v7 Diversified Parlays.
    
    Returns optimized, EV-positive parlays with diversification constraints:
    - Max 2 appearances per player per tier
    - Max 2 picks from same team per parlay  
    - Max 3 picks from same stat type per parlay
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    query = {}
    if tier:
        if tier not in ["safe_haven", "front_lines", "war_zone"]:
            raise HTTPException(status_code=400, detail="Invalid tier. Use: safe_haven, front_lines, war_zone")
        query["tier"] = tier
    
    cursor = _db.ferrari_parlays.find(query, {"_id": 0})
    parlays = await cursor.to_list(length=None)
    
    # Group by tier
    by_tier = {
        "safe_haven": [],
        "front_lines": [],
        "war_zone": []
    }
    
    for p in parlays:
        t = p.get("tier", "unknown")
        if t in by_tier:
            by_tier[t].append(p)
    
    return {
        "total_parlays": len(parlays),
        "parlays_by_tier": {
            "safe_haven": len(by_tier["safe_haven"]),
            "front_lines": len(by_tier["front_lines"]),
            "war_zone": len(by_tier["war_zone"])
        },
        "safe_haven_parlays": by_tier["safe_haven"],
        "front_lines_parlays": by_tier["front_lines"],
        "war_zone_parlays": by_tier["war_zone"],
        "diversification_rules": {
            "max_player_appearances_per_tier": 2,
            "max_team_per_parlay": 2,
            "max_stat_type_per_parlay": 3
        }
    }
