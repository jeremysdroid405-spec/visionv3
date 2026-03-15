"""
Demon Tracker Routes Module
===========================
Demon Tracker V2 - Deep ingestion of player props with color-coded cards
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demon-tracker", tags=["demon-tracker"])

# Service reference (set by main app)
_demon_tracker = None


def set_demon_tracker(tracker):
    """Set the demon tracker reference."""
    global _demon_tracker
    _demon_tracker = tracker


@router.get("/status")
async def get_demon_tracker_status():
    """Get current Demon Tracker sync status"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    status = await _demon_tracker.get_sync_status()
    return {"success": True, "data": status}


@router.post("/sync")
async def trigger_demon_tracker_sync():
    """Manually trigger deep ingestion sync"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    result = await _demon_tracker.run_deep_ingestion()
    return {"success": True, "result": result}


@router.get("/events")
async def get_todays_events():
    """Get today's NBA events from The Odds API"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    events = await _demon_tracker.fetch_todays_events()
    return {
        "success": True,
        "count": len(events),
        "events": [
            {
                "id": e.get("id"),
                "home_team": e.get("home_team"),
                "away_team": e.get("away_team"),
                "commence_time": e.get("commence_time")
            }
            for e in events
        ]
    }


@router.get("/event/{event_id}/odds")
async def get_event_odds(event_id: str):
    """Get all odds for a specific event including player props"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    odds = await _demon_tracker.fetch_event_odds(event_id)
    if not odds:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    
    props = _demon_tracker.extract_player_props_from_odds(odds)
    
    return {
        "success": True,
        "event": {
            "id": odds.get("id"),
            "home_team": odds.get("home_team"),
            "away_team": odds.get("away_team"),
            "commence_time": odds.get("commence_time")
        },
        "bookmakers_count": len(odds.get("bookmakers", [])),
        "player_props_count": len(props),
        "player_props": props[:50]
    }


@router.get("/props")
async def get_processed_props(
    color: Optional[str] = Query(None, description="Filter by card color: green, yellow, red, standard"),
    bookmaker: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    demons_only: bool = Query(False)
):
    """Get processed demon cards with filters"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(
        color=color,
        bookmaker=bookmaker,
        market=market,
        demons_only=demons_only
    )
    
    return {
        "success": True,
        "count": len(cards),
        "cards": cards
    }


@router.get("/demons")
async def get_demon_lines():
    """Get all qualified Demon lines (L10 hit rate >= 40%)"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(demons_only=True)
    
    return {
        "success": True,
        "count": len(cards),
        "demons": cards
    }


@router.get("/cards/green")
async def get_green_cards():
    """Get all GREEN demon cards (high hit rate >= 50%)"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(color="green")
    return {"success": True, "count": len(cards), "cards": cards}


@router.get("/cards/yellow")
async def get_yellow_cards():
    """Get all YELLOW demon cards (injury/news warnings)"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(color="yellow")
    return {"success": True, "count": len(cards), "cards": cards}


@router.get("/cards/red")
async def get_red_cards():
    """Get all RED demon cards (low hit rate < 30% or injured)"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(color="red")
    return {"success": True, "count": len(cards), "cards": cards}


@router.get("/player/{player_name}")
async def get_player_analysis(
    player_name: str, 
    line: float = Query(20.0), 
    market: str = Query("player_points")
):
    """Get full analysis for a specific player"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    bdl_player = await _demon_tracker.search_bdl_player(player_name)
    if not bdl_player:
        raise HTTPException(status_code=404, detail=f"Player {player_name} not found")
    
    games = await _demon_tracker.fetch_player_season_stats(bdl_player.get("id"))
    hit_rates = _demon_tracker.calculate_hit_rates(games, market, line)
    injury_info = _demon_tracker.check_player_injury_and_news(player_name)
    
    return {
        "success": True,
        "player": {
            "id": bdl_player.get("id"),
            "name": f"{bdl_player.get('first_name', '')} {bdl_player.get('last_name', '')}".strip(),
            "team": bdl_player.get("team", {}).get("full_name", ""),
            "position": bdl_player.get("position", "")
        },
        "market": market,
        "line": line,
        "hit_rates": hit_rates,
        "injury_info": injury_info,
        "games_analyzed": len(games),
        "last_5_games": [
            {
                "date": g.get("game", {}).get("date", "")[:10],
                "pts": g.get("pts", 0),
                "reb": g.get("reb", 0),
                "ast": g.get("ast", 0),
                "fg3m": g.get("fg3m", 0),
                "min": g.get("min", 0)
            }
            for g in games[:5]
        ]
    }


@router.get("/search")
async def search_player_cards(
    q: str = Query(..., description="Player name to search"),
    market: Optional[str] = Query(None)
):
    """Search for a player's cards"""
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await _demon_tracker.get_demon_cards(player_name=q, market=market)
    
    return {
        "success": True,
        "query": q,
        "count": len(cards),
        "cards": cards
    }


@router.get("/board")
async def get_full_demon_board():
    """
    Get the full Demon Board with color-coded cards
    Green: High hit rate (>=50%)
    Yellow: Injury/news warning
    Red: Low hit rate (<30%) or injured OUT
    """
    if _demon_tracker is None:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    all_cards = await _demon_tracker.get_demon_cards()
    all_cards = [c for c in all_cards if c is not None]
    
    color_counts = {
        "green": sum(1 for c in all_cards if c and c.get("card_color") == "green"),
        "yellow": sum(1 for c in all_cards if c and c.get("card_color") == "yellow"),
        "red": sum(1 for c in all_cards if c and c.get("card_color") == "red"),
        "standard": sum(1 for c in all_cards if c and c.get("card_color") == "standard")
    }
    
    events_map = {}
    for card in all_cards:
        if not card:
            continue
        event_id = card.get("event_id", "unknown")
        if event_id not in events_map:
            events_map[event_id] = {
                "event_id": event_id,
                "home_team": card.get("home_team"),
                "away_team": card.get("away_team"),
                "commence_time": card.get("commence_time"),
                "cards": []
            }
        events_map[event_id]["cards"].append(card)
    
    events_list = sorted(
        events_map.values(),
        key=lambda x: x.get("commence_time") or ""
    )
    
    total_demons = sum(
        1 for c in all_cards 
        if c and c.get("hit_rates") and c.get("hit_rates", {}).get("is_demon")
    )
    
    return {
        "success": True,
        "sync_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "events_count": len(events_list),
        "total_cards": len(all_cards),
        "total_demons": total_demons,
        "card_colors": color_counts,
        "board": events_list
    }
