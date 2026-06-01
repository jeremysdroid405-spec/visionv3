"""
Live Scores Routes Module
=========================
Handles live scores and command center ticker endpoints
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/live-scores", tags=["live-scores"])
command_center_router = APIRouter(prefix="/v3/command-center", tags=["command-center"])

# Service reference (set by main app)
_live_scores_engine = None


def set_live_scores_engine(engine):
    """Set the live scores engine reference."""
    global _live_scores_engine
    _live_scores_engine = engine
    logger.info(f"[LIVE_SCORES] Engine set: {type(engine).__name__}")


@router.get("")
async def get_live_scores(refresh: bool = False):
    """
    Get live NBA scores from The Odds API.
    
    Args:
        refresh: Force refresh from API (otherwise uses cache)
    
    Returns:
        - games: List of games with scores and status
        - live_count: Number of games currently in play
        - upcoming_count: Number of games not yet started
    """
    if _live_scores_engine is None:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    if refresh:
        # Tag the call so the budget log shows manual_refresh, not unknown.
        from services.odds_api_budget import CallerTag
        with CallerTag("manual_refresh"):
            result = await _live_scores_engine.fetch_live_scores()
    else:
        result = await _live_scores_engine.get_cached_scores()
        # 2026-06-01 — only fall through to a live API call when the
        # cache is BOTH missing/unsuccessful AND not fresh. Previously
        # `not result.get("games")` was true on every empty-slate day
        # (e.g., off-season) and triggered an Odds API call on EVERY
        # frontend hit. Now the empty cache result is honored for the
        # configured TTL window (default 300s).
        cache_fresh = bool(result.get("cache_fresh"))
        needs_refetch = (not result.get("success")) and (not cache_fresh)
        if needs_refetch:
            from services.odds_api_budget import CallerTag
            with CallerTag("frontend_live_scores_route"):
                result = await _live_scores_engine.fetch_live_scores()
    
    return result


@router.post("/refresh")
async def refresh_live_scores():
    """Force refresh live scores from The Odds API."""
    if _live_scores_engine is None:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    result = await _live_scores_engine.fetch_live_scores()
    return result


@command_center_router.get("/news")
async def get_command_center_news(custom_headlines: Optional[str] = None):
    """
    Get breaking news for the Command Center ticker.
    
    Combines RSS feeds from Rotoworld and ESPN with optional custom headlines.
    
    Args:
        custom_headlines: Pipe-separated list of custom headlines to include
    """
    if _live_scores_engine is None:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    headlines = None
    if custom_headlines:
        headlines = [h.strip() for h in custom_headlines.split("|") if h.strip()]
    
    result = await _live_scores_engine.fetch_breaking_news(custom_headlines=headlines)
    return result


@command_center_router.get("/ticker")
async def get_ticker_data():
    """
    Get combined data for the Command Center tickers.
    
    Returns both live scores and breaking news in a single call,
    optimized for the frontend ticker display.
    """
    if _live_scores_engine is None:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    # Get scores (from cache).
    # 2026-06-01 — same fix as `/v3/live-scores`: don't refetch on
    # every frontend tick during empty-slate windows. Cache freshness
    # is honored even when the games list is empty.
    scores_result = await _live_scores_engine.get_cached_scores()
    scores_fresh = bool(scores_result.get("cache_fresh"))
    if (not scores_result.get("success")) and (not scores_fresh):
        from services.odds_api_budget import CallerTag
        with CallerTag("frontend_ticker_route"):
            scores_result = await _live_scores_engine.fetch_live_scores()
    
    # Get news (from cache)
    news_result = await _live_scores_engine.get_cached_news()
    if not news_result.get("success"):
        news_result = await _live_scores_engine.fetch_breaking_news()
    
    # Format for ticker display
    ticker_items = []
    
    # Add live scores first
    for game in scores_result.get("games", []):
        if game["status"] == "in_play":
            ticker_items.append({
                "type": "live_score",
                "text": f"{game['away_team']} {game['away_score']} @ {game['home_team']} {game['home_score']} - {game['status_display']}",
                "priority": 1,
                "category": "live"
            })
        elif game["status"] == "upcoming":
            ticker_items.append({
                "type": "upcoming",
                "text": f"{game['away_team']} @ {game['home_team']} - {game['status_display']}",
                "priority": 2,
                "category": "upcoming"
            })
    
    # Add breaking news (max 8 — controlled by backend freshness lifecycle)
    for news in news_result.get("news", [])[:8]:
        ticker_items.append({
            "type": "news",
            "text": news["title"],
            "source": news.get("source", ""),
            "priority": 3 if news.get("is_custom") else 4,
            "category": news.get("category", "news")
        })
    
    return {
        "success": True,
        "ticker_items": ticker_items,
        "live_games": scores_result.get("live_count", 0),
        "upcoming_games": scores_result.get("upcoming_count", 0),
        "news_count": len(news_result.get("news", []))
    }
