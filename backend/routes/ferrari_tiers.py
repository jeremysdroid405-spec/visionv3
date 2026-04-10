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
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    legacy: bool = Query(False, description="Use legacy Safe Haven logic instead of stored data")
):
    """
    FERRARI SAFE HAVEN - Returns stored picks with Vision Intel data.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Picks are populated by the rebuild endpoint which runs:
    1. Oracle Apex 3-Gate qualification
    2. Vision Intel (Gemini) analysis and gating
    3. Composite scoring and final selection
    
    Use ?legacy=true to bypass stored data and run live Oracle Apex scan.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
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
                "sport": sport,
                "picks": picks,
                "count": len(picks),
                "note": "Live scan - Vision Intel not applied. Use rebuild for full analysis."
            }
        except Exception as e:
            logger.error(f"[SAFE_HAVEN] Legacy scan error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # DEFAULT: Read from sport-specific stored collection
    collection_name = get_collection_name("safe_haven", sport)
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # MLB Fallback: If no picks in tier collection, get from cached_board
    if not picks and sport == "mlb":
        cached_board_name = get_collection_name("cached_board", sport)
        cached_board = _db[cached_board_name]
        
        # Get top players from cached board with best hit rates
        players = await cached_board.find({}, {"_id": 0}).to_list(length=50)
        
        # Flatten props and sort by CV (lower = more consistent = safe haven)
        all_props = []
        for player in players:
            for prop in player.get("props", []):
                prop["player_name"] = player.get("player_name")
                prop["team"] = player.get("team")
                prop["position"] = player.get("position")
                all_props.append(prop)
        
        # Filter for safe haven criteria: high hit rate + low CV
        safe_picks = [
            p for p in all_props
            if p.get("hit_rate_l10") and p.get("hit_rate_l10") >= 60
            and (p.get("cv") is None or p.get("cv") <= 50)
        ]
        
        # Sort by hit rate descending
        safe_picks.sort(key=lambda x: x.get("hit_rate_l10", 0), reverse=True)
        picks = safe_picks[:limit]
        
        return {
            "tier": "safe_haven",
            "tier_label": f"Safe Haven ({sport.upper()})",
            "logic": "mlb_cached_board_fallback",
            "sport": sport,
            "collection": cached_board_name,
            "picks": picks,
            "count": len(picks),
            "note": "MLB picks from cached board (tier routing pending)"
        }
    
    return {
        "tier": "safe_haven",
        "tier_label": f"Safe Haven ({sport.upper()})",
        "logic": "stored_with_vision_intel",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/ferrari/front-lines")
async def get_ferrari_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    FERRARI FRONT LINES - Returns stored picks with Vision Intel data.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Read from sport-specific collection
    collection_name = get_collection_name("front_lines", sport)
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # MLB Fallback: If no picks in tier collection, get from cached_board
    if not picks and sport == "mlb":
        cached_board_name = get_collection_name("cached_board", sport)
        cached_board = _db[cached_board_name]
        
        players = await cached_board.find({}, {"_id": 0}).to_list(length=50)
        
        all_props = []
        for player in players:
            for prop in player.get("props", []):
                prop["player_name"] = player.get("player_name")
                prop["team"] = player.get("team")
                prop["position"] = player.get("position")
                all_props.append(prop)
        
        # Front lines: moderate hit rate + moderate CV
        front_picks = [
            p for p in all_props
            if p.get("hit_rate_l10") and 40 <= p.get("hit_rate_l10", 0) < 60
        ]
        
        front_picks.sort(key=lambda x: x.get("hit_rate_l10", 0), reverse=True)
        picks = front_picks[:limit]
        
        return {
            "tier": "front_lines",
            "tier_label": f"Front Lines ({sport.upper()})",
            "logic": "mlb_cached_board_fallback",
            "sport": sport,
            "collection": cached_board_name,
            "picks": picks,
            "count": len(picks),
            "note": "MLB picks from cached board (tier routing pending)"
        }
    
    return {
        "tier": "front_lines",
        "tier_label": f"Front Lines ({sport.upper()})",
        "logic": "stored_with_vision_intel",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/ferrari/war-zone")
async def get_ferrari_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    FERRARI WAR ZONE - Returns stored high-risk/high-reward picks with Vision Intel.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Read from sport-specific collection
    collection_name = get_collection_name("war_zone", sport)
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # MLB Fallback: If no picks in tier collection, get from cached_board
    if not picks and sport == "mlb":
        cached_board_name = get_collection_name("cached_board", sport)
        cached_board = _db[cached_board_name]
        
        players = await cached_board.find({}, {"_id": 0}).to_list(length=50)
        
        all_props = []
        for player in players:
            for prop in player.get("props", []):
                prop["player_name"] = player.get("player_name")
                prop["team"] = player.get("team")
                prop["position"] = player.get("position")
                all_props.append(prop)
        
        # War zone: lower hit rate OR high CV (risky plays)
        war_picks = [
            p for p in all_props
            if p.get("hit_rate_l10") and p.get("hit_rate_l10", 0) < 40
            or (p.get("cv") and p.get("cv") > 50)
        ]
        
        war_picks.sort(key=lambda x: x.get("edge", 0) or 0, reverse=True)
        picks = war_picks[:limit]
        
        return {
            "tier": "war_zone",
            "tier_label": f"War Zone ({sport.upper()})",
            "logic": "mlb_cached_board_fallback",
            "sport": sport,
            "collection": cached_board_name,
            "picks": picks,
            "count": len(picks),
            "note": "MLB picks from cached board (tier routing pending)"
        }
    
    return {
        "tier": "war_zone",
        "tier_label": f"War Zone ({sport.upper()})",
        "logic": "stored_with_vision_intel",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/ferrari/discarded")
async def get_ferrari_discarded(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    FERRARI DISCARDED - Props killed by the 15% separation filter.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Shows what was filtered out for being "mid" plays.
    Useful for debugging and transparency.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Read from sport-specific collection
    collection_name = get_collection_name("discarded", sport)
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    return {
        "tier": "discarded",
        "tier_label": f"Discarded ({sport.upper()})",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks)
    }


@router.post("/v3/ferrari/rebuild")
async def rebuild_ferrari_tiers(
    use_optimized: bool = True,
    sport: str = Query("nba", description="Target sport to sync (nba or mlb)")
):
    """
    Manually trigger a rebuild of all Ferrari tiers.
    
    **SPORT-EXCLUSIVE**: Syncs only the specified sport's data.
    - sport=nba: Syncs NBA collections (dg_cached_board, ferrari_* tiers)
    - sport=mlb: Syncs MLB collections (mlb_cached_board, mlb_ferrari_* tiers)
    
    With use_optimized=True (default):
    1. Fetches ALL global data in parallel (standings, refs, momentum, vacuums)
    2. Runs Ferrari pipeline with power score calculation
    3. Enriches all picks with cached data
    4. Generates AI summaries in batches (rate-limited)
    5. Persists enriched data to sport-specific cached_board
    
    Target: Complete sync in under 5 seconds (excluding AI summaries)
    
    With use_optimized=False:
    - Falls back to legacy sequential pipeline (NBA only)
    """
    from datetime import datetime, timezone
    
    # Normalize sport parameter
    target_sport = (sport or "nba").lower()
    if target_sport not in ["nba", "mlb"]:
        raise HTTPException(status_code=400, detail=f"Invalid sport '{sport}'. Must be 'nba' or 'mlb'.")
    
    if use_optimized:
        # Use the new optimized sync engine with sport isolation
        from services.optimized_sync_engine import run_optimized_sync
        result = await run_optimized_sync(_db, target_sport=target_sport)
        return result
    else:
        # Legacy path (NBA only for backwards compatibility)
        if target_sport != "nba":
            raise HTTPException(status_code=400, detail="Legacy sync only supports NBA. Use use_optimized=true for MLB.")
        service = get_service()
        result = await service.build_ferrari_tiers(datetime.now(timezone.utc), target_sport=target_sport)
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



@router.post("/v3/odds/sync")
async def sync_odds_universal(
    sport: str = Query("nba", description="Sport to sync (nba or mlb)"),
    bookmakers: str = Query(
        "prizepicks,draftkings,fanduel,pinnacle",
        description="Comma-separated bookmakers to fetch"
    ),
    include_sharp: bool = Query(True, description="Include sharp books (Pinnacle, Circa, BetCRIS)")
):
    """
    Universal Multi-Bookmaker Odds Sync.
    
    Fetches props from multiple bookmakers for cross-market comparison.
    
    **Bookmakers Supported:**
    - DFS: prizepicks, underdog
    - US Books: draftkings, fanduel, betmgm
    - Sharp Books: pinnacle, circa, betcris
    
    **NBA** (basketball_nba):
    - Markets: player_points, player_rebounds, player_assists, PRA
    - Saves to: dg_live_props
    
    **MLB** (baseball_mlb):
    - Markets: pitcher_strikeouts, pitcher_walks, pitcher_hits_allowed,
               batter_hits, batter_total_bases, batter_rbis, batter_runs_scored, batter_stolen_bases
    - Saves to: mlb_live_props
    
    **Output includes:**
    - all_lines: Lines from each bookmaker
    - sharp_line: Line from sharp book (Pinnacle)
    - sharp_edge: Percentage difference between DFS line and sharp line
    
    Returns sync summary with event count, prop count, bookmaker breakdown.
    """
    from config.db_config import validate_sport
    from services.universal_odds_sync import get_universal_odds_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Parse bookmakers
    bookmaker_list = [b.strip().lower() for b in bookmakers.split(",") if b.strip()]
    
    # Run the sync
    service = get_universal_odds_service(_db)
    result = await service.sync_sport_props(sport, bookmakers=bookmaker_list, include_sharp=include_sharp)
    
    return result


@router.get("/v3/odds/props")
async def get_live_props(
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    limit: int = Query(100, ge=1, le=500),
    stat_type: str = Query(None, description="Filter by stat type (e.g., PTS, Strikeouts)")
):
    """
    Get live props from the sport-specific collection.
    
    **NBA**: Returns props from dg_live_props
    **MLB**: Returns props from mlb_live_props
    
    Optional filtering by stat_type.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("live_props", sport)
    collection = _db[collection_name]
    
    # Build query
    query = {}
    if stat_type:
        query["stat_type"] = stat_type
    
    # Fetch props
    cursor = collection.find(query, {"_id": 0}).limit(limit)
    props = await cursor.to_list(length=limit)
    
    # Get unique stat types for reference
    stat_types = await collection.distinct("stat_type")
    
    return {
        "sport": sport,
        "collection": collection_name,
        "props": props,
        "count": len(props),
        "available_stat_types": stat_types
    }



@router.post("/v3/bdl/sync")
async def sync_bdl_universal(
    sport: str = Query("nba", description="Sport to sync (nba or mlb)"),
    include_players: bool = Query(True, description="Sync player roster"),
    include_stats: bool = Query(True, description="Sync game logs/stats")
):
    """
    BDL Universal Sync - Fetch stats from BallDontLie v1 API.
    
    **Endpoints:**
    - NBA: https://api.balldontlie.io/nba/v1/stats
    - MLB: https://api.balldontlie.io/mlb/v1/stats
    
    **STRICT cursor-based pagination** using next_cursor from meta object.
    
    Saves to sport-specific master_hub collection:
    - NBA: nba_master_hub_2026
    - MLB: mlb_master_hub_2026
    
    Returns sync summary with player count, game logs count, and errors.
    """
    from config.db_config import validate_sport
    from services.bdl_universal_sync import run_bdl_universal_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Run the sync
    result = await run_bdl_universal_sync(
        _db,
        sport=sport,
        include_players=include_players,
        include_stats=include_stats
    )
    
    return result


@router.get("/v3/bdl/players")
async def get_bdl_players(
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    limit: int = Query(50, ge=1, le=500),
    team: str = Query(None, description="Filter by team abbreviation")
):
    """
    Get players from sport-specific master_hub collection.
    
    Returns player profiles synced from BDL.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("master_hub", sport)
    collection = _db[collection_name]
    
    # Build query
    query = {"bdl_id": {"$exists": True}}
    if team:
        query["team_abbr"] = team.upper()
    
    # Fetch players
    cursor = collection.find(query, {"_id": 0}).limit(limit)
    players = await cursor.to_list(length=limit)
    
    # Get unique teams for reference
    teams = await collection.distinct("team_abbr")
    
    return {
        "sport": sport,
        "collection": collection_name,
        "players": players,
        "count": len(players),
        "available_teams": sorted([t for t in teams if t])
    }


@router.get("/v3/bdl/stats/{player_name}")
async def get_bdl_player_stats(
    player_name: str,
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    Get game logs for a specific player from master_hub.
    
    Returns BDL game logs with full box score data.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("master_hub", sport)
    collection = _db[collection_name]
    
    # Search for player (case-insensitive)
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player:
        # Try partial match
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return {
        "sport": sport,
        "player": player.get("display_name"),
        "team": player.get("team_abbr"),
        "bdl_id": player.get("bdl_id"),
        "game_logs_count": player.get("bdl_game_logs_count", 0),
        "game_logs": player.get("bdl_game_logs", [])[:20],  # Limit to recent 20
        "last_sync": player.get("bdl_last_sync")
    }



@router.post("/v3/mlb/build-board")
async def build_mlb_cached_board():
    """
    Build the MLB Cached Board (Enrichment Pipeline).
    
    Process:
    1. Fetches all props from mlb_live_props
    2. Matches each prop to mlb_master_hub_2026 by player_name
    3. Enriches with:
       - Last 10 game logs
       - Season average
       - CV (Coefficient of Variation)
       - Hit rates (L10, L5)
    4. Saves to mlb_cached_board
    
    **CIRCUIT BREAKER**: If 0 props found, preserves existing board.
    
    Returns build summary with counts.
    """
    from services.mlb_cached_board_builder import run_mlb_board_build
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    result = await run_mlb_board_build(_db)
    return result


@router.get("/v3/mlb/cached-board")
async def get_mlb_cached_board(
    response: Response,
    limit: int = Query(100, ge=1, le=500)
):
    """
    Get the MLB Cached Board with enriched props.
    
    Returns players with their enriched props including:
    - Season averages
    - CV scores
    - Hit rates
    - Last 10 game logs
    """
    from services.mlb_cached_board_builder import get_mlb_board_builder
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    builder = get_mlb_board_builder(_db)
    result = await builder.get_cached_board(limit)
    return result


@router.get("/v3/mlb/player/{player_name}")
async def get_mlb_player_props(
    player_name: str,
    response: Response
):
    """
    Get a specific MLB player's enriched props from the cached board.
    
    Returns:
    - Player info
    - All props with enrichment data (CV, hit rates, averages)
    - Last 10 game logs
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection_name = get_collection_name("cached_board", "mlb")
    collection = _db[collection_name]
    
    # Search for player (case-insensitive)
    player = await collection.find_one(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player:
        # Try partial match
        player = await collection.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found in MLB board")
    
    # Add stat_type_extracted to each prop for frontend compatibility
    # Also check if prop is in goblins/demons collections
    goblins_coll = _db["mlb_goblins"]
    demons_coll = _db["mlb_demons"]
    
    # Get all goblins and demons for this player
    player_goblins = await goblins_coll.find(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "stat_type": 1, "line": 1}
    ).to_list(length=100)
    
    player_demons = await demons_coll.find(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "stat_type": 1, "line": 1}
    ).to_list(length=100)
    
    # Create lookup sets
    goblin_keys = {f"{g['stat_type']}|{g['line']}" for g in player_goblins}
    demon_keys = {f"{d['stat_type']}|{d['line']}" for d in player_demons}
    
    if player.get("props"):
        for prop in player["props"]:
            # Add stat_type_extracted (copy of stat_type for frontend compatibility)
            prop["stat_type_extracted"] = prop.get("stat_type")
            
            # Add direction field (copy of recommendation)
            if not prop.get("direction"):
                prop["direction"] = prop.get("recommendation", "Over")
            
            # Add market field
            if not prop.get("market"):
                prop["market"] = prop.get("market_key") or prop.get("stat_type")
            
            # Check if this prop is a goblin or demon
            prop_key = f"{prop.get('stat_type')}|{prop.get('line')}"
            prop["is_goblin"] = prop_key in goblin_keys
            prop["is_demon"] = prop_key in demon_keys
    
    return {
        "success": True,
        "player": player
    }


# =============================================================================
# MLB VEGAS KILLER HISTORICAL BACKFILL
# =============================================================================

@router.post("/v3/mlb/vk-backfill")
async def run_mlb_vk_historical_backfill(
    seasons: str = Query("2021,2022,2023,2024,2025,2026", description="Comma-separated seasons to fetch"),
    save_to_db: bool = Query(True, description="Save results to database")
):
    """
    MLB Vegas Killer 5-Season Historical Backfill.
    
    Fetches historical stats (2021-2026) and calculates weighted baselines
    for the ML regression model.
    
    **Process:**
    1. Data Retrieval: Fetch BDL /mlb/v1/stats for each season
    2. Game Cache: Build game date caches for accurate timestamps
    3. Weighted Regression: Apply time-decaying weights
       - 2026: w=1.0 (most recent)
       - 2021: w=0.5 (oldest)
    4. Output: 5-Year Weighted Baseline vs L10 Average
    
    **Collections Updated:**
    - mlb_historical_logs: Raw game logs by player
    - mlb_master_hub_2026: Player baselines (vk_baselines field)
    
    **Warning:** This is a long-running operation (5-15 minutes).
    """
    from services.mlb_vk_historical_backfill import run_mlb_historical_backfill
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse seasons
    try:
        season_list = [int(s.strip()) for s in seasons.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid seasons format. Use comma-separated years.")
    
    # Validate seasons
    valid_seasons = [s for s in season_list if 2020 <= s <= 2026]
    if not valid_seasons:
        raise HTTPException(status_code=400, detail="No valid seasons provided (2020-2026)")
    
    result = await run_mlb_historical_backfill(_db, seasons=valid_seasons)
    return result


@router.post("/v3/mlb/advanced-stats-sync")
async def run_mlb_advanced_stats_sync_endpoint(
    seasons: str = Query("2024,2025,2026", description="Comma-separated seasons to fetch"),
    include_splits: bool = Query(True, description="Fetch vL/vR, home/away splits"),
    include_season_stats: bool = Query(True, description="Fetch WAR, OPS, WHIP, etc."),
    player_limit: int = Query(None, description="Limit players for testing (None = all)")
):
    """
    MLB Advanced Stats Sync.
    
    Fetches advanced stats from BDL for the VK Regression Model:
    
    **Splits Data (vL/vR, Park, Opponent):**
    - vs_left: Stats vs left-handed pitchers
    - vs_right: Stats vs right-handed pitchers
    - home/away: Home and away splits
    - day/night: Day and night game splits
    - by_park: Park-specific performance
    - by_opponent: Opponent-specific performance
    
    **Season Stats (Advanced Metrics):**
    - WAR: Wins Above Replacement
    - OPS: On-Base Plus Slugging
    - WHIP: Walks + Hits per Inning Pitched
    - K/9: Strikeouts per 9 innings
    - ERA: Earned Run Average
    - FIP: Fielding Independent Pitching
    
    **Derived Metrics:**
    - days_rest: Calculated from game log dates
    
    **Warning:** This is a long-running operation (5-30 minutes depending on player count).
    """
    from services.mlb_advanced_stats_sync import run_mlb_advanced_stats_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse seasons
    try:
        season_list = [int(s.strip()) for s in seasons.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid seasons format")
    
    valid_seasons = [s for s in season_list if 2020 <= s <= 2026]
    if not valid_seasons:
        raise HTTPException(status_code=400, detail="No valid seasons provided (2020-2026)")
    
    result = await run_mlb_advanced_stats_sync(
        _db,
        seasons=valid_seasons,
        include_splits=include_splits,
        include_season_stats=include_season_stats,
        player_limit=player_limit
    )
    return result


@router.get("/v3/mlb/advanced-stats/{player_name}")
async def get_mlb_player_advanced_stats(
    player_name: str,
    response: Response
):
    """
    Get a player's advanced stats.
    
    Returns:
    - vL/vR splits (batting stats vs left/right-handed pitchers)
    - Home/Away splits
    - Season stats (WAR, OPS, WHIP, K/9, ERA)
    - Days of rest data from game logs
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("master_hub", "mlb")]
    
    # Find player
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "display_name": 1, "vs_left": 1, "vs_right": 1, "home_splits": 1, 
         "away_splits": 1, "war": 1, "ops": 1, "whip": 1, "k_per_9": 1, "era": 1,
         "advanced_stats": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "display_name": 1, "vs_left": 1, "vs_right": 1, "home_splits": 1,
             "away_splits": 1, "war": 1, "ops": 1, "whip": 1, "k_per_9": 1, "era": 1,
             "advanced_stats": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return {
        "success": True,
        "player_name": player.get("display_name"),
        "quick_stats": {
            "war": player.get("war"),
            "ops": player.get("ops"),
            "whip": player.get("whip"),
            "k_per_9": player.get("k_per_9"),
            "era": player.get("era")
        },
        "vs_left": player.get("vs_left"),
        "vs_right": player.get("vs_right"),
        "home_splits": player.get("home_splits"),
        "away_splits": player.get("away_splits"),
        "advanced_stats": player.get("advanced_stats")
    }


@router.get("/v3/mlb/vk-baselines/{player_name}")
async def get_mlb_vk_baselines(
    player_name: str,
    response: Response
):
    """
    Get a player's VK weighted baselines.
    
    Returns the 5-year weighted baselines calculated during historical backfill:
    - weighted_baseline: Time-weighted average
    - l10_average: Recent 10-game average
    - baseline_vs_l10: Deviation percentage
    - weighted_cv: Consistency score
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("master_hub", "mlb")]
    
    # Find player
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "display_name": 1, "vk_baselines": 1, "vk_baseline_games": 1, "vk_baseline_updated": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "display_name": 1, "vk_baselines": 1, "vk_baseline_games": 1, "vk_baseline_updated": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    if not player.get("vk_baselines"):
        raise HTTPException(status_code=404, detail=f"No VK baselines found for '{player_name}'. Run historical backfill first.")
    
    return {
        "success": True,
        "player_name": player.get("display_name"),
        "baselines": player.get("vk_baselines"),
        "total_games": player.get("vk_baseline_games"),
        "updated_at": player.get("vk_baseline_updated")
    }


# =============================================================================
# MLB VK REGRESSION MODEL ENDPOINTS
# =============================================================================

@router.post("/v3/mlb/vk-regression")
async def run_mlb_vk_regression_analysis(
    save_to_db: bool = Query(True, description="Save results to Ferrari collections"),
    vision_intel: bool = Query(True, description="Run Vision Intel on Safe Haven picks")
):
    """
    MLB Vegas Killer Regression Analysis.
    
    Runs weighted linear regression on today's MLB slate:
    
    **Process:**
    1. Fetch all live props from mlb_live_props
    2. Calculate projections using weighted linear regression
    3. Calculate VK Edge: (Projected - Line) / Line
    4. Classify into Ferrari tiers:
       - Safe Haven: Edge > 20% + R² > 0.75 + L10 Hit Rate > 70%
       - Front Lines: Edge > 15% + R² > 0.60
       - War Zone: Edge > 25% + R² < 0.40 (High risk/reward)
    5. Run Vision Intel on Safe Haven picks (optional)
    6. Save to mlb_ferrari_* collections
    
    **Returns:** Tiered picks with projections and edges
    """
    from services.mlb_vk_regression import run_mlb_vk_slate_analysis
    from services.mlb_vision_intel_service import run_mlb_vision_intel_analysis
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Run regression analysis
    results = await run_mlb_vk_slate_analysis(_db, save_to_db=save_to_db)
    
    # Run Vision Intel on Safe Haven picks if requested
    if vision_intel and results.get("tiers", {}).get("safe_haven"):
        vision_results = await run_mlb_vision_intel_analysis(
            _db,
            results["tiers"]["safe_haven"],
            save_to_db=save_to_db
        )
        results["vision_intel"] = vision_results
    
    return results


@router.get("/v3/mlb/vk-projection/{player_name}")
async def get_mlb_vk_projection(
    player_name: str,
    stat_type: str = Query(..., description="Stat type (e.g., 'Total Bases', 'Strikeouts')"),
    line: float = Query(..., description="Sportsbook line to calculate edge against"),
    opponent: str = Query(None, description="Opponent team abbreviation"),
    venue: str = Query(None, description="Home team abbreviation for park factor"),
    response: Response = None
):
    """
    Get VK projection for a specific player and stat.
    
    Uses weighted linear regression on historical game logs.
    
    **Returns:**
    - projected_value: Model's prediction
    - r_squared: Confidence score (0-1)
    - edge: (Projected - Line) / Line
    - tier: Suggested tier classification
    """
    from services.mlb_vk_regression import get_mlb_vk_regression
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Find player
    collection = _db[get_collection_name("master_hub", "mlb")]
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "bdl_id": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "bdl_id": 1}
        )
    
    if not player or not player.get("bdl_id"):
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    # Get model and calculate projection
    model = get_mlb_vk_regression(_db)
    
    projection = await model.calculate_player_projection(
        player_id=player["bdl_id"],
        stat_type=stat_type,
        opponent_abbr=opponent,
        venue_team=venue
    )
    
    if not projection.get("valid"):
        raise HTTPException(
            status_code=400, 
            detail=f"Could not calculate projection: {projection.get('error', 'Unknown error')}"
        )
    
    # Calculate edge
    edge_data = model.calculate_edge(projection["projected_value"], line)
    
    # Calculate hit rate
    hit_rate = model.calculate_hit_rate(
        projection.get("l10_values", []),
        line,
        edge_data["direction"]
    )
    
    # Classify tier
    tier = model.classify_tier(
        edge_data["edge"],
        projection["r_squared"],
        hit_rate
    )
    
    return {
        "success": True,
        "player_name": projection["player_name"],
        "stat_type": stat_type,
        "line": line,
        "projection": {
            "projected_value": projection["projected_value"],
            "raw_projection": projection["raw_projection"],
            "r_squared": projection["r_squared"],
            "std_error": projection["std_error"],
            "slope": projection["slope"],
            "intercept": projection["intercept"],
            "sample_size": projection["sample_size"]
        },
        "edge": edge_data,
        "hit_rate_l10": hit_rate,
        "l10_avg": projection["l10_avg"],
        "tier": tier,
        "adjustments": projection["adjustments"]
    }


@router.get("/v3/mlb/ferrari/safe-haven")
async def get_mlb_safe_haven_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Safe Haven picks.
    
    Safe Haven criteria:
    - VK Edge > 20%
    - R-Squared > 0.75
    - L10 Hit Rate > 70%
    - Vision Intel: CONFIRMED (not TRAP)
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("safe_haven", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    # Filter out TRAP picks
    confirmed = [p for p in picks if p.get("vision_intel", {}).get("verdict") != "TRAP"]
    
    return {
        "success": True,
        "tier": "SAFE_HAVEN",
        "sport": "mlb",
        "picks": confirmed,
        "count": len(confirmed),
        "total_before_filter": len(picks)
    }


@router.get("/v3/mlb/ferrari/front-lines")
async def get_mlb_front_lines_picks(
    response: Response,
    limit: int = Query(30, ge=1, le=100)
):
    """
    Get MLB Front Lines picks.
    
    Front Lines criteria:
    - VK Edge > 15%
    - R-Squared > 0.60
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("front_lines", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    return {
        "success": True,
        "tier": "FRONT_LINES",
        "sport": "mlb",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/ferrari/war-zone")
async def get_mlb_war_zone_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB War Zone picks.
    
    War Zone criteria:
    - VK Edge > 25%
    - R-Squared < 0.40 (High variance = high risk/reward)
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("war_zone", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    return {
        "success": True,
        "tier": "WAR_ZONE",
        "sport": "mlb",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/ferrari/hrr-picks")
async def get_mlb_hrr_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    min_edge: float = Query(50.0, description="Minimum edge percentage"),
    min_hit_rate: float = Query(0.5, description="Minimum L10 hit rate")
):
    """
    Get MLB Hits+Runs+RBIs (HRR) combo picks.
    
    HRR props have inherently lower R² due to variance in combo stats.
    Uses adjusted criteria: High edge + High hit rate.
    
    **Adjusted Criteria for Combo Stats:**
    - Edge > 50% (combo lines are often set conservatively)
    - L10 Hit Rate > 50%
    - Sorted by balanced score (edge * hit_rate)
    
    **Returns:** HRR picks sorted by value score
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Query HRR props from war_zone (they end up there due to low R²)
    collection = _db[get_collection_name("war_zone", "mlb")]
    
    # Find HRR props with edge and hit rate filters
    query = {
        "stat_type": "Hits+Runs+RBIs",
        "edge_pct": {"$gte": min_edge},
        "hit_rate_l10": {"$gte": min_hit_rate}
    }
    
    picks = await collection.find(query, {"_id": 0}).to_list(length=None)
    
    # Calculate value score and sort
    for pick in picks:
        edge = abs(pick.get("edge_pct", 0))
        hr = pick.get("hit_rate_l10", 0) or 0
        # Score: edge weighted by hit rate
        pick["value_score"] = round(edge * hr, 1)
    
    # Sort by value_score descending
    picks.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    
    # Deduplicate (same player can appear twice for OVER/UNDER)
    seen = set()
    unique_picks = []
    for p in picks:
        key = f"{p.get('player_name')}|{p.get('line')}|{p.get('direction')}"
        if key not in seen:
            seen.add(key)
            unique_picks.append(p)
    
    return {
        "success": True,
        "stat_type": "Hits+Runs+RBIs",
        "sport": "mlb",
        "picks": unique_picks[:limit],
        "count": len(unique_picks[:limit]),
        "total_available": len(unique_picks),
        "filters": {
            "min_edge": min_edge,
            "min_hit_rate": min_hit_rate
        }
    }


# =============================================================================
# MLB SHARP SORTING & TIER DISTRIBUTION
# =============================================================================

@router.post("/v3/mlb/sharp-sort")
async def run_mlb_sharp_sorting_endpoint(
    stat_types: str = Query(
        None, 
        description="Comma-separated stat types to filter (e.g., 'Hits+Runs+RBIs,Total Bases')"
    ),
    save_to_db: bool = Query(True, description="Save results to collections")
):
    """
    MLB Sharp Sorting & Tier Distribution.
    
    Classifies props using sharp book analysis:
    
    **1. Pinnacle De-Vig Layer:**
    - Calculates fair value probability from Pinnacle odds
    - Removes ~4.5% vig to get true probability
    - Sharp Goblin: Fair value > 70% (odds ≤ -240)
    
    **2. DraftKings Market Depth:**
    - Compares DK alt-lines to PrizePicks
    - Identifies mispricing where DK is plus money but PP favors
    - Demon: DK +180 vs PP -110 equivalent = 12% edge
    
    **3. Ferrari Final Sort:**
    - mlb_goblins: Sharp odds ≤ -240 AND VK Projection > Line
    - mlb_demons: VK Slope trending + DK alt-line mispricing
    - mlb_standard: Sharp and public agree (-110 to -130)
    
    **Collections Created:**
    - mlb_goblins, mlb_demons, mlb_standard
    """
    from services.mlb_sharp_sorting_service import run_mlb_sharp_sorting
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse stat types
    stat_type_list = None
    if stat_types:
        stat_type_list = [s.strip() for s in stat_types.split(",") if s.strip()]
    
    results = await run_mlb_sharp_sorting(_db, stat_types=stat_type_list, save_to_db=save_to_db)
    
    # Return summary (don't return full lists to avoid serialization issues)
    return {
        "success": results.get("success"),
        "props_processed": results.get("props_processed"),
        "goblins_count": len(results.get("goblins", [])),
        "demons_count": len(results.get("demons", [])),
        "standard_count": len(results.get("standard", [])),
        "unclassified": results.get("unclassified"),
        "stats": results.get("stats"),
        "duration_seconds": results.get("duration_seconds"),
        "top_5_goblins": [
            {
                "player_name": g.get("player_name"),
                "stat_type": g.get("stat_type"),
                "line": g.get("line"),
                "projected_value": g.get("projected_value"),
                "direction": g.get("recommendation"),
                "sharp_odds": g.get("all_odds", {}).get("pinnacle"),
                "sharp_fair_value": g.get("sharp_fair_value"),
                "edge_pct": g.get("edge_pct"),
                "hit_rate_l10": g.get("hit_rate_l10")
            }
            for g in results.get("goblins", [])[:5]
        ]
    }


@router.get("/v3/mlb/sharp/goblins")
async def get_mlb_goblins(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Sharp Goblins.
    
    Criteria: Sharp odds ≤ -240 AND VK Projection > Line
    
    These are the highest-confidence plays backed by sharp money.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_goblins"]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    return {
        "success": True,
        "tier": "GOBLINS",
        "description": "Sharp odds ≤ -240 AND VK confirms",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/sharp/demons")
async def get_mlb_demons(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Demons.
    
    Criteria: DK mispricing detected + VK Slope trending
    
    These are mispriced props where DK alt-lines suggest PP is wrong.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_demons"]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    return {
        "success": True,
        "tier": "DEMONS",
        "description": "DK mispricing + VK slope confirms",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/sharp/standard")
async def get_mlb_standard(
    response: Response,
    limit: int = Query(30, ge=1, le=100)
):
    """
    Get MLB Standard Props.
    
    Criteria: Sharp and public books agree (-110 to -130 range)
    
    These are consensus plays where all books are aligned.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_standard"]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    return {
        "success": True,
        "tier": "STANDARD",
        "description": "Books agree (-110 to -130)",
        "picks": picks,
        "count": len(picks)
    }


# =============================================================================
# MLB HEADSHOT SYNC ENDPOINTS
# =============================================================================

@router.post("/v3/mlb/headshots/sync")
async def sync_mlb_headshots(
    limit: int = Query(None, description="Optional limit on players to process"),
    phase: str = Query("full", description="Phase to run: 'ids', 'headshots', or 'full'")
):
    """
    MLB Headshot Sync - Multi-step process.
    
    **Phase 1: ID Discovery**
    - Searches MLB API (https://statsapi.mlb.com/api/v1/people/search)
    - Extracts official 6-digit MLB ID
    - Saves to official_mlb_id field
    
    **Phase 2: Headshot Fetch**
    - Downloads from MLB CDN using official_mlb_id
    - Falls back to ESPN CDN if MLB CDN fails
    - Saves to /app/frontend/public/images/mlb_headshots/{id}.png
    
    **Options:**
    - phase='ids' - Only run ID discovery
    - phase='headshots' - Only fetch headshots (requires IDs)
    - phase='full' - Run both phases (default)
    """
    from services.mlb_headshot_sync import get_mlb_headshot_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_mlb_headshot_service(_db)
    
    if phase == "ids":
        result = await service.discover_mlb_ids(limit)
    elif phase == "headshots":
        result = await service.fetch_headshots(limit)
    else:  # full
        result = await service.run_full_sync(limit)
    
    return result


@router.get("/v3/mlb/headshots/status")
async def get_mlb_headshot_status(response: Response):
    """
    Get MLB headshot sync status.
    
    Returns counts of:
    - Total players
    - Players with official_mlb_id
    - Players with headshot path
    - Local headshot files
    - Coverage percentage
    """
    from services.mlb_headshot_sync import get_mlb_headshot_service
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_mlb_headshot_service(_db)
    status = await service.get_sync_status()
    
    return status


@router.get("/v3/mlb/headshots/errors")
async def get_mlb_mapping_errors(response: Response):
    """
    Get list of players that couldn't be mapped to MLB IDs.
    
    These players don't have official headshots available.
    """
    from pathlib import Path
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    error_log = Path("/app/backend/logs/mlb_mapping_errors.log")
    
    if not error_log.exists():
        return {"errors": [], "message": "No mapping errors logged yet"}
    
    with open(error_log, "r") as f:
        content = f.read()
    
    # Parse player names (skip comment lines)
    players = [
        line.strip()
        for line in content.split("\n")
        if line.strip() and not line.startswith("#")
    ]
    
    return {
        "unmapped_players": players,
        "count": len(players),
        "log_path": str(error_log)
    }
