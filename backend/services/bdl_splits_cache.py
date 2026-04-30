"""
BDL Splits Cache - Single Pre-fetch, Zero Individual Calls
==========================================================
Pre-fetches ALL player splits data ONCE at rebuild start.
Tier processing uses ONLY cached data - no API calls.
"""

import os
import logging
import httpx
import asyncio
from typing import Dict, Any, Optional, Set
from datetime import datetime, timezone
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")

# Thresholds
OPS_SPLIT_BOOST = 0.850  # +5% if OPS > .850

# Global cache - populated once per rebuild
_splits_cache: Dict[int, Dict] = {}
_cache_populated = False


def get_current_season() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 4 else now.year - 1


async def _fetch_single_player_splits(player_id: int, season: int) -> Optional[Dict]:
    """Fetch splits for one player. Returns None on error."""
    if not BDL_API_KEY:
        return None
    
    url = f"{BDL_MLB_BASE_URL}/players/splits"
    params = {"player_id": player_id, "season": season}
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": BDL_API_KEY}, params=params)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data.get("byBreakdown") or data.get("byBattingOrder"):
                    return data
    except Exception as _swept_exc:
        log_silent_failure("services.bdl_splits_cache._fetch_single_player_splits", _swept_exc)  # sweep-auto-converted
    return None


async def prefetch_all_splits(player_ids: Set[int]) -> int:
    """
    Pre-fetch splits for ALL players in parallel batches.
    Called ONCE at rebuild start. Returns count of successful fetches.
    """
    global _splits_cache, _cache_populated
    
    if not player_ids:
        return 0
    
    season = get_current_season()
    fallback_season = season - 1
    
    logger.info(f"[BDL_CACHE] Pre-fetching splits for {len(player_ids)} players (season {season}/{fallback_season})...")
    
    player_list = list(player_ids)
    success_count = 0
    
    # Batch 25 concurrent requests
    batch_size = 25
    for i in range(0, len(player_list), batch_size):
        batch = player_list[i:i + batch_size]
        
        # Try current season first
        tasks = [_fetch_single_player_splits(pid, season) for pid in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # For failures, try fallback season
        retry_tasks = []
        retry_indices = []
        for idx, (pid, result) in enumerate(zip(batch, results)):
            if isinstance(result, Exception) or result is None:
                retry_tasks.append(_fetch_single_player_splits(pid, fallback_season))
                retry_indices.append(idx)
            else:
                _splits_cache[pid] = result
                success_count += 1
        
        if retry_tasks:
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            for idx, result in zip(retry_indices, retry_results):
                pid = batch[idx]
                if not isinstance(result, Exception) and result is not None:
                    _splits_cache[pid] = result
                    success_count += 1
                else:
                    _splits_cache[pid] = {}  # Empty cache to prevent re-fetch
        
        # Rate limit: 0.2s between batches
        if i + batch_size < len(player_list):
            await asyncio.sleep(0.2)
    
    _cache_populated = True
    logger.info(f"[BDL_CACHE] Complete: {success_count}/{len(player_ids)} players cached")
    return success_count


def get_cached_modifiers(player_id: int, pitcher_hand: str = "R") -> Dict[str, Any]:
    """
    Get modifiers from cache. NO API CALLS.
    Returns default 1.0 modifiers if not cached.
    """
    result = {
        "matchup_modifier": 1.0,
        "tempo_modifier": 1.0,
        "lr_split": None,
        "source": "bdl_cache"
    }
    
    splits = _splits_cache.get(player_id)
    if not splits:
        return result
    
    # Calculate L/R split modifier
    by_breakdown = splits.get("byBreakdown", [])
    target_split = f"vs. {'Left' if pitcher_hand == 'L' else 'Right'}"
    
    for split in by_breakdown:
        if split.get("split_name") == target_split:
            ops = split.get("ops")
            if ops and ops > OPS_SPLIT_BOOST:
                result["matchup_modifier"] = 1.05
                result["lr_split"] = {
                    "split_name": target_split,
                    "ops": ops,
                    "avg": split.get("avg"),
                    "at_bats": split.get("at_bats")
                }
            break
    
    return result


def clear_cache():
    """Clear cache for fresh rebuild."""
    global _splits_cache, _cache_populated
    _splits_cache = {}
    _cache_populated = False
