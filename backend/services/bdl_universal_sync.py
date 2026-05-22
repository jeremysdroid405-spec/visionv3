"""
BDL Universal Stats Sync Service (2026 AI-Native)
==================================================
Multi-sport stats synchronization using BallDontLie v1 API.

Endpoints:
- NBA: https://api.balldontlie.io/nba/v1/stats
- MLB: https://api.balldontlie.io/mlb/v1/stats

STRICT REQUIREMENT: Uses cursor-based pagination (next_cursor from meta object).
NO PAGE-BASED PAGINATION - This causes data loss.

Features:
- Sport-specific endpoints (nba/v1 vs mlb/v1)
- Batched player ID requests for efficiency
- Cursor-based pagination with circuit breakers
- Saves to sport-specific master_hub collections
"""

import os
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx

from config.db_config import get_collection_name, validate_sport, SPORT_CONFIG

logger = logging.getLogger(__name__)

# BDL API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")

# Sport-specific base URLs (2026 AI-Native endpoints)
BDL_BASE_URLS = {
    "nba": "https://api.balldontlie.io/nba/v1",
    "mlb": "https://api.balldontlie.io/mlb/v1"
}

# Season configuration
SEASONS = {
    "nba": 2025,  # 2025-26 season
    "mlb": 2026   # 2026 MLB season
}

# Batching configuration
BATCH_SIZE = 25  # Players per API request
PARALLEL_BATCHES = 2  # Concurrent batch groups
RATE_LIMIT_DELAY = 1.0  # Seconds between batch groups

# Circuit breaker - abort if too few results
MIN_EXPECTED_RESULTS = 5

# Persistent raw MLB historical archive (in addition to the hub array
# mirror). Idempotent upsert keyed by (bdl_player_id, game_id).
RAW_MLB_HISTORICAL_COLL = "bdl_mlb_historical_game_logs"


class BDLUniversalSyncService:
    """
    Universal BallDontLie sync service for multiple sports.
    
    Uses the v1 API endpoints with strict cursor-based pagination.
    
    MLB API STRUCTURE NOTES:
    - Stats endpoint returns flat structure with game_id (no nested game object)
    - Stats use short field names: rbi, k, hr, bb (not rbis, strikeouts, home_runs, walks)
    - team_name is at root level (no nested team object)
    - Must fetch games separately to get dates for game_id mapping
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
        self._mlb_game_cache: Dict[int, Dict] = {}  # Cache game_id -> game data (date, teams)
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with auth headers."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=45.0,
                headers={"Authorization": BDL_API_KEY},
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    def _get_base_url(self, sport: str) -> str:
        """Get sport-specific BDL base URL."""
        sport = validate_sport(sport)
        if sport not in BDL_BASE_URLS:
            raise ValueError(f"No BDL endpoint for sport: {sport}")
        return BDL_BASE_URLS[sport]
    
    def _get_season(self, sport: str) -> int:
        """Get current season for sport."""
        return SEASONS.get(sport, 2025)
    
    # =========================================================================
    # MLB GAME DATE CACHE (Required because MLB stats don't include game dates)
    # =========================================================================
    
    async def _build_mlb_game_cache(self, season: int) -> None:
        """
        Fetch MLB games for a season and cache game_id -> {date, home_team, away_team}.
        
        Required because MLB /stats endpoint only returns game_id, not game details.
        """
        logger.info(f"[BDL_MLB] Building game date cache for season {season}...")
        
        params = {
            "seasons[]": season,
            "per_page": 100
        }
        
        games = await self._fetch_with_cursor("/games", "mlb", params, max_pages=50)
        
        for game in games:
            game_id = game.get("id")
            if game_id:
                self._mlb_game_cache[game_id] = {
                    "date": game.get("date"),
                    "season": game.get("season"),
                    "home_team": game.get("home_team", {}),
                    "away_team": game.get("away_team", {}),
                    "home_team_name": game.get("home_team_name"),
                    "away_team_name": game.get("away_team_name"),
                    "venue": game.get("venue")
                }
        
        logger.info(f"[BDL_MLB] Cached {len(self._mlb_game_cache)} games for season {season}")
    
    def _get_mlb_game_date(self, game_id: int) -> Optional[str]:
        """Get game date from cache."""
        game_data = self._mlb_game_cache.get(game_id)
        return game_data.get("date") if game_data else None
    
    def _get_mlb_opponent(self, game_id: int, player_team_name: str) -> Optional[str]:
        """Get opponent team abbreviation from cache."""
        game_data = self._mlb_game_cache.get(game_id)
        if not game_data:
            return None
        
        # Determine opponent based on player's team
        if player_team_name == game_data.get("home_team_name"):
            return game_data.get("away_team", {}).get("abbreviation")
        else:
            return game_data.get("home_team", {}).get("abbreviation")
    
    # =========================================================================
    # CURSOR-BASED PAGINATION
    # =========================================================================
    
    async def _fetch_with_cursor(
        self,
        endpoint: str,
        sport: str,
        params: Dict[str, Any],
        max_pages: int = 100
    ) -> List[Dict]:
        """
        Fetch data using STRICT cursor-based pagination.
        
        CRITICAL: Uses next_cursor from meta object, NOT page numbers.
        
        Args:
            endpoint: API endpoint (e.g., '/stats', '/players')
            sport: Sport key ('nba' or 'mlb')
            params: Base query parameters
            max_pages: Maximum pages to fetch (circuit breaker)
            
        Returns:
            List of all data items across all pages
        """
        base_url = self._get_base_url(sport)
        url = f"{base_url}{endpoint}"
        client = await self._get_client()
        
        all_data = []
        cursor = None
        page_count = 0
        
        logger.info(f"[BDL_{sport.upper()}] Starting cursor-based fetch from {endpoint}")
        
        while page_count < max_pages:
            # Build request params
            request_params = {**params}
            if cursor:
                request_params["cursor"] = cursor
            
            try:
                response = await client.get(url, params=request_params)
                
                if response.status_code == 200:
                    result = response.json()
                    data = result.get("data", [])
                    all_data.extend(data)
                    page_count += 1
                    
                    # STRICT: Extract next_cursor from meta
                    meta = result.get("meta", {})
                    cursor = meta.get("next_cursor")
                    
                    logger.debug(
                        f"[BDL_{sport.upper()}] Page {page_count}: {len(data)} items, "
                        f"next_cursor={cursor is not None}"
                    )
                    
                    # Stop if no more pages
                    if not cursor:
                        logger.info(
                            f"[BDL_{sport.upper()}] Pagination complete: "
                            f"{page_count} pages, {len(all_data)} total items"
                        )
                        break
                    
                    # Rate limiting between pages
                    await asyncio.sleep(0.2)
                    
                elif response.status_code == 429:
                    # Rate limited - wait and retry
                    logger.warning(f"[BDL_{sport.upper()}] Rate limited, waiting 5s...")
                    await asyncio.sleep(5)
                    continue
                    
                elif response.status_code == 404:
                    logger.warning(f"[BDL_{sport.upper()}] Endpoint not found: {url}")
                    break
                    
                else:
                    logger.error(
                        f"[BDL_{sport.upper()}] API error {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                    break
                    
            except Exception as e:
                logger.error(f"[BDL_{sport.upper()}] Request error: {e}")
                await asyncio.sleep(1)
                break
        
        return all_data
    
    # =========================================================================
    # STATS SYNC (Game Logs)
    # =========================================================================
    
    async def fetch_player_stats(
        self,
        player_ids: List[int],
        sport: str = "nba",
        season: int = None
    ) -> List[Dict]:
        """
        Fetch stats for a batch of players using cursor pagination.
        
        Args:
            player_ids: List of BDL player IDs
            sport: Sport key ('nba' or 'mlb')
            season: Season year (defaults to current)
            
        Returns:
            List of stat records
        """
        if not player_ids:
            return []
        
        season = season or self._get_season(sport)
        
        # Build params with array format for player_ids
        params = {
            "seasons[]": season,
            "per_page": 100
        }
        
        # Add player_ids as array params
        # httpx handles this correctly when we pass a list
        params["player_ids[]"] = player_ids
        
        # Fetch with cursor pagination
        stats = await self._fetch_with_cursor("/stats", sport, params)
        
        return stats
    
    async def sync_stats_batched(
        self,
        sport: str = "nba",
        player_ids: List[int] = None,
        season: Optional[int] = None,
        merge_by_season: bool = False,
        also_save_raw: bool = False,
        skip_already_stored: bool = False,
    ) -> Dict[str, Any]:
        """
        Sync stats for all players in batches.
        
        If player_ids is None, fetches IDs from master_hub collection.
        
        Args:
            sport: Sport key ('nba' or 'mlb')
            player_ids: Optional list of specific player IDs
            season: Optional explicit season override (default uses
                    SEASONS[sport]). REQUIRED when backfilling historical
                    seasons — `_get_season(sport)` returns the *current*
                    season only.
            merge_by_season: When True, _save_stats_to_master_hub will
                    preserve existing `bdl_game_logs` for other seasons
                    instead of replacing the whole array. Required for
                    historical backfills into a hub that already carries
                    current-season logs.
            
        Returns:
            Sync summary with counts and errors
        """
        sport = validate_sport(sport)
        season = season if season is not None else self._get_season(sport)
        
        logger.info("=" * 70)
        logger.info(f"[BDL_SYNC] Starting {sport.upper()} Stats Sync")
        logger.info(f"[BDL_SYNC] Season: {season}")
        logger.info(f"[BDL_SYNC] Endpoint: {self._get_base_url(sport)}/stats")
        logger.info("=" * 70)
        
        sync_start = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "sport": sport,
            "season": season,
            "synced_at": sync_start.isoformat(),
            "players_requested": 0,
            "players_with_stats": 0,
            "total_game_logs": 0,
            "batches_processed": 0,
            "errors": []
        }
        
        try:
            # MLB SPECIAL: Build game date cache first (MLB stats don't include dates)
            if sport == "mlb":
                await self._build_mlb_game_cache(season)
            
            # Get player IDs if not provided
            if player_ids is None:
                master_hub_collection = get_collection_name("master_hub", sport)
                cursor = self.db[master_hub_collection].find(
                    {"bdl_id": {"$exists": True}},
                    {"bdl_id": 1}
                )
                docs = await cursor.to_list(length=None)
                player_ids = [d["bdl_id"] for d in docs if d.get("bdl_id")]
            
            results["players_requested"] = len(player_ids)
            
            if not player_ids:
                logger.warning(f"[BDL_SYNC] No player IDs found for {sport.upper()}")
                return results

            # ── Cache-first: skip players already fully covered ──────
            # Players with N>=1 row already stored for the requested
            # `season` are considered "in cache" and are skipped unless
            # the caller passed skip_already_stored=False (the default).
            n_skipped_cached = 0
            if (skip_already_stored and season is not None
                        and sport == "mlb" and player_ids):
                raw_coll = self.db[RAW_MLB_HISTORICAL_COLL]
                already_have = set()
                async for d in raw_coll.aggregate([
                    {"$match": {"season": season,
                                "bdl_player_id": {"$in": player_ids}}},
                    {"$group": {"_id": "$bdl_player_id"}},
                ]):
                    already_have.add(d["_id"])
                if already_have:
                    before = len(player_ids)
                    player_ids = [p for p in player_ids if p not in already_have]
                    n_skipped_cached = before - len(player_ids)
                    logger.info(
                        f"[BDL_SYNC] cache-first skipped {n_skipped_cached} "
                        f"players already with season-{season} rows. "
                        f"Remaining to fetch: {len(player_ids)}"
                    )
            results["players_skipped_cached"] = n_skipped_cached

            if not player_ids:
                logger.info(f"[BDL_SYNC] Nothing to fetch — all players "
                                f"already cached for season {season}.")
                return results
            
            logger.info(f"[BDL_SYNC] Processing {len(player_ids)} players in batches of {BATCH_SIZE}")
            
            # Process in batches
            all_stats = []
            players_with_stats = set()
            
            batches = [
                player_ids[i:i + BATCH_SIZE]
                for i in range(0, len(player_ids), BATCH_SIZE)
            ]
            
            for batch_idx, batch in enumerate(batches):
                logger.info(f"[BDL_SYNC] Processing batch {batch_idx + 1}/{len(batches)} ({len(batch)} players)")
                
                try:
                    # Fetch stats for batch
                    stats = await self.fetch_player_stats(batch, sport, season)
                    all_stats.extend(stats)
                    
                    # Track which players had stats
                    for stat in stats:
                        player = stat.get("player", {})
                        if player.get("id"):
                            players_with_stats.add(player["id"])
                    
                    results["batches_processed"] += 1
                    
                except Exception as e:
                    logger.error(f"[BDL_SYNC] Batch {batch_idx + 1} error: {e}")
                    results["errors"].append(f"Batch {batch_idx + 1}: {str(e)}")
                
                # Rate limiting between batches
                if batch_idx < len(batches) - 1:
                    await asyncio.sleep(RATE_LIMIT_DELAY)
            
            results["players_with_stats"] = len(players_with_stats)
            results["total_game_logs"] = len(all_stats)
            
            # Circuit breaker - don't wipe DB if suspiciously low results
            if len(all_stats) < MIN_EXPECTED_RESULTS and player_ids:
                logger.error(
                    f"[BDL_SYNC] CIRCUIT BREAKER: Only {len(all_stats)} stats returned "
                    f"for {len(player_ids)} players. Aborting save to prevent data wipe."
                )
                results["success"] = False
                results["errors"].append("Circuit breaker triggered - suspiciously low results")
                return results
            
            # Save to master hub
            if all_stats:
                await self._save_stats_to_master_hub(
                    all_stats, sport,
                    season=season,
                    merge_by_season=merge_by_season,
                    also_save_raw=also_save_raw,
                )
                logger.info(f"[BDL_SYNC] Saved {len(all_stats)} game logs to master hub")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            
            logger.info(f"[BDL_SYNC] {sport.upper()} Sync Complete:")
            logger.info(f"  • Players: {results['players_with_stats']}/{results['players_requested']}")
            logger.info(f"  • Game Logs: {results['total_game_logs']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"[BDL_SYNC] Sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results
    
    async def _save_stats_to_master_hub(
        self,
        stats: List[Dict],
        sport: str,
        season: Optional[int] = None,
        merge_by_season: bool = False,
        also_save_raw: bool = False,
    ) -> int:
        """
        Save game logs to sport-specific master_hub collection.
        
        Groups stats by player and updates the bdl_game_logs array.

        When `merge_by_season=True`, removes only the logs for the given
        `season` before pushing the new batch — preserving game logs from
        other seasons. This is the safe mode for historical backfills:
        without it, ingesting 2025 data would WIPE the 2026 logs already
        in the hub.

        When `also_save_raw=True` AND sport == "mlb", the raw transformed
        logs are also upserted into `bdl_mlb_historical_game_logs` keyed
        by `(bdl_player_id, game_id)` so the backfill remains
        reproducible / queryable without traversing the hub array.
        
        Args:
            stats: List of stat records
            sport: Sport key
            season: Season being written. Required when merge_by_season=True.
            merge_by_season: Per-season replacement mode (see above).
            also_save_raw: Mirror to `bdl_mlb_historical_game_logs`.
            
        Returns:
            Number of players updated
        """
        master_hub_collection = get_collection_name("master_hub", sport)
        collection = self.db[master_hub_collection]
        
        # Group stats by player
        player_stats: Dict[int, List[Dict]] = {}
        for stat in stats:
            player = stat.get("player", {})
            player_id = player.get("id")
            if player_id:
                if player_id not in player_stats:
                    player_stats[player_id] = []
                
                # Transform to game log format (caller-supplied season
                # gets stamped onto MLB logs since the API doesn't echo
                # one per-row).
                game_log = self._transform_stat_to_game_log(stat, sport,
                                                                  season=season)
                # Also-defence: if for any reason game_log["season"] is
                # missing/None, stamp it now. We always want the season
                # on every row for merge_by_season + raw indexing.
                if season is not None and not game_log.get("season"):
                    game_log["season"] = season
                player_stats[player_id].append(game_log)
        
        # Sort each player's logs by date (most recent first)
        for player_id in player_stats:
            player_stats[player_id].sort(
                key=lambda x: x.get("date") or "",
                reverse=True
            )

        # ── Optional raw-collection mirror (MLB only) ─────────────────
        # Idempotent upsert on (bdl_player_id, game_id). Indexes ensured
        # on first call.
        if also_save_raw and sport == "mlb" and player_stats:
            raw_coll = self.db[RAW_MLB_HISTORICAL_COLL]
            try:
                await raw_coll.create_index(
                    [("bdl_player_id", 1), ("game_id", 1)],
                    name="bdl_mlb_raw_pid_game_unique", unique=True,
                    background=True,
                )
                await raw_coll.create_index(
                    [("season", 1), ("date", 1)], background=True,
                )
            except Exception as ie:
                logger.warning(f"[BDL_RAW] index ensure soft-failed: {ie}")
            from pymongo import UpdateOne
            ops: List[UpdateOne] = []
            now_iso = datetime.now(timezone.utc).isoformat()
            for pid, logs in player_stats.items():
                for lg in logs:
                    gid = lg.get("game_id")
                    if not gid:
                        continue
                    raw_doc = {
                        **lg,
                        "bdl_player_id": pid,   # canonical key
                        "ingested_at": now_iso,
                    }
                    ops.append(UpdateOne(
                        {"bdl_player_id": pid, "game_id": gid},
                        {"$set": raw_doc}, upsert=True,
                    ))
            if ops:
                try:
                    r = await raw_coll.bulk_write(ops, ordered=False)
                    logger.info(
                        f"[BDL_RAW] {RAW_MLB_HISTORICAL_COLL} upsert: "
                        f"matched={r.matched_count} "
                        f"upserted={r.upserted_count} "
                        f"modified={r.modified_count} (ops={len(ops)})"
                    )
                except Exception as bwe:
                    logger.error(f"[BDL_RAW] bulk_write failed: {bwe}")
        
        # Update master hub
        updated_count = 0
        for player_id, logs in player_stats.items():
            if merge_by_season and season is not None:
                # Two-stage upsert: (1) pull existing rows for this
                # exact season, (2) push the new batch back in. This
                # keeps logs from OTHER seasons intact.
                await collection.update_one(
                    {"bdl_id": player_id},
                    {
                        "$pull": {"bdl_game_logs": {"season": season}},
                        "$set":  {"bdl_last_sync": datetime.now(timezone.utc).isoformat(),
                                  "sport": sport},
                    },
                    upsert=True,
                )
                result = await collection.update_one(
                    {"bdl_id": player_id},
                    {
                        "$push": {"bdl_game_logs": {"$each": logs}},
                        "$inc":  {"bdl_game_logs_count": len(logs)},
                    },
                )
                if result.matched_count or result.modified_count:
                    updated_count += 1
            else:
                # Legacy replace-all behaviour (kept for backwards-compat
                # with daily incremental refreshes of the current season).
                result = await collection.update_one(
                    {"bdl_id": player_id},
                    {
                        "$set": {
                            "bdl_game_logs": logs,
                            "bdl_game_logs_count": len(logs),
                            "bdl_last_sync": datetime.now(timezone.utc).isoformat(),
                            "sport": sport,
                        }
                    },
                    upsert=True,
                )
                if result.modified_count > 0 or result.upserted_id:
                    updated_count += 1
        
        return updated_count
    
    def _transform_stat_to_game_log(self, stat: Dict, sport: str,
                                          season: Optional[int] = None) -> Dict:
        """
        Transform BDL stat object to game_log format.
        
        Handles sport-specific stat fields and API structure differences.
        
        MLB API STRUCTURE:
        - game_id at root level (no nested game object)
        - team_name at root level (no nested team object)
        - Uses short field names: rbi, k, hr, bb, etc.
        - Requires game cache lookup for dates
        - DOES NOT carry per-row season — caller must supply it.
        
        NBA API STRUCTURE:
        - Nested game object with id, date
        - Nested team object with abbreviation
        """
        player = stat.get("player", {})
        
        if sport == "mlb":
            # MLB: Flat structure with game_id at root
            game_id = stat.get("game_id")
            team_name = stat.get("team_name", "")
            
            # Get date from game cache
            game_date = self._get_mlb_game_date(game_id)
            opponent_abbr = self._get_mlb_opponent(game_id, team_name)
            
            log = {
                "game_id": game_id,
                "date": game_date,  # From cache lookup
                # MLB stats don't include season — caller supplies it.
                # Default 2026 kept ONLY for backward-compat with the
                # legacy daily refresh path that doesn't pass season.
                "season": season if season is not None else 2026,
                "bdl_player_id": player.get("id"),
                "player_name": player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                "team_name": team_name,
                "opponent_abbr": opponent_abbr,
                "sport": sport,
                # MLB Batter stats (using actual BDL field names)
                "at_bats": stat.get("at_bats", 0),
                "hits": stat.get("hits", 0),
                "runs": stat.get("runs", 0),
                "rbis": stat.get("rbi", 0),  # BDL uses 'rbi' not 'rbis'
                "home_runs": stat.get("hr", 0),  # BDL uses 'hr' not 'home_runs'
                "stolen_bases": stat.get("stolen_bases", 0),
                "walks": stat.get("bb", 0),  # BDL uses 'bb' not 'walks'
                "strikeouts": stat.get("k", 0),  # BDL uses 'k' not 'strikeouts'
                "batting_avg": stat.get("avg", 0),
                "obp": stat.get("obp", 0),
                "slg": stat.get("slg", 0),
                "total_bases": stat.get("total_bases", 0),
                "doubles": stat.get("doubles", 0),
                "triples": stat.get("triples", 0),
                "plate_appearances": stat.get("plate_appearances", 0),
                # MLB Pitcher stats (BDL prefixes pitcher stats with p_)
                "innings_pitched": stat.get("ip"),  # ip = innings pitched
                "pitcher_strikeouts": stat.get("p_k"),  # p_k = pitcher strikeouts
                "pitcher_walks": stat.get("p_bb"),  # p_bb = pitcher walks
                "hits_allowed": stat.get("p_hits"),  # p_hits = hits allowed
                "earned_runs": stat.get("er"),  # er = earned runs
                "era": stat.get("era"),
                "pitch_count": stat.get("pitch_count"),
                "wins": stat.get("wins"),
                "losses": stat.get("losses"),
                "saves": stat.get("saves"),
            }
        else:
            # NBA: Nested structure with game and team objects
            game = stat.get("game", {})
            team = stat.get("team", {})
            
            log = {
                "game_id": game.get("id"),
                "date": game.get("date"),
                "season": game.get("season"),
                "bdl_player_id": player.get("id"),
                "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                "team_id": team.get("id"),
                "team_abbr": team.get("abbreviation"),
                "sport": sport,
                # NBA stats
                "pts": stat.get("pts", 0),
                "reb": stat.get("reb", 0),
                "ast": stat.get("ast", 0),
                "stl": stat.get("stl", 0),
                "blk": stat.get("blk", 0),
                "turnover": stat.get("turnover", 0),
                "min": stat.get("min", "0"),
                "fgm": stat.get("fgm", 0),
                "fga": stat.get("fga", 0),
                "fg_pct": stat.get("fg_pct", 0),
                "fg3m": stat.get("fg3m", 0),
                "fg3a": stat.get("fg3a", 0),
                "fg3_pct": stat.get("fg3_pct", 0),
                "ftm": stat.get("ftm", 0),
                "fta": stat.get("fta", 0),
                "ft_pct": stat.get("ft_pct", 0),
                "oreb": stat.get("oreb", 0),
                "dreb": stat.get("dreb", 0),
                "pf": stat.get("pf", 0),
                "plus_minus": stat.get("plus_minus", 0),
            }
        
        return log
    
    # =========================================================================
    # PLAYERS SYNC
    # =========================================================================
    
    async def sync_players(self, sport: str = "nba") -> Dict[str, Any]:
        """
        Sync all active players for a sport.
        
        Uses /players/active endpoint with cursor pagination.
        
        Args:
            sport: Sport key ('nba' or 'mlb')
            
        Returns:
            Sync summary
        """
        sport = validate_sport(sport)
        
        logger.info(f"[BDL_SYNC] Syncing {sport.upper()} players...")
        
        params = {"per_page": 100}
        
        # Use /players/active for current roster
        players = await self._fetch_with_cursor("/players/active", sport, params)
        
        if not players:
            logger.warning(f"[BDL_SYNC] No players returned for {sport.upper()}")
            return {"success": False, "players_count": 0}
        
        # Save to master hub
        master_hub_collection = get_collection_name("master_hub", sport)
        collection = self.db[master_hub_collection]
        
        saved_count = 0
        for player in players:
            player_id = player.get("id")
            if not player_id:
                continue
            
            # Build player document
            first_name = player.get("first_name", "")
            last_name = player.get("last_name", "")
            display_name = f"{first_name} {last_name}".strip()
            
            team = player.get("team", {})
            
            doc = {
                "bdl_id": player_id,
                "display_name": display_name,
                "first_name": first_name,
                "last_name": last_name,
                "position": player.get("position"),
                "team_id": team.get("id"),
                "team_abbr": team.get("abbreviation"),
                "team_name": team.get("full_name"),
                "sport": sport,
                "bdl_last_sync": datetime.now(timezone.utc).isoformat()
            }
            
            # Sport-specific fields
            if sport == "nba":
                doc.update({
                    "height": player.get("height"),
                    "weight": player.get("weight"),
                    "jersey_number": player.get("jersey_number"),
                    "college": player.get("college"),
                    "country": player.get("country"),
                    "draft_year": player.get("draft_year"),
                    "draft_round": player.get("draft_round"),
                    "draft_number": player.get("draft_number"),
                })
            elif sport == "mlb":
                doc.update({
                    "bats": player.get("bats"),
                    "throws": player.get("throws"),
                    "primary_position": player.get("primary_position"),
                    "jersey_number": player.get("jersey_number"),
                    "birth_date": player.get("birth_date"),
                })
            
            result = await collection.update_one(
                {"bdl_id": player_id},
                {"$set": doc},
                upsert=True
            )
            
            if result.modified_count > 0 or result.upserted_id:
                saved_count += 1
        
        logger.info(f"[BDL_SYNC] Saved {saved_count} {sport.upper()} players to master hub")
        
        return {
            "success": True,
            "sport": sport,
            "players_count": len(players),
            "saved_count": saved_count,
            "collection": master_hub_collection
        }


# Singleton instance
_bdl_universal_service: Optional[BDLUniversalSyncService] = None


def get_bdl_universal_service(db: AsyncIOMotorDatabase) -> BDLUniversalSyncService:
    """Get or create the universal BDL sync service."""
    global _bdl_universal_service
    if _bdl_universal_service is None:
        _bdl_universal_service = BDLUniversalSyncService(db)
    return _bdl_universal_service


async def run_bdl_universal_sync(
    db: AsyncIOMotorDatabase,
    sport: str = "nba",
    include_players: bool = True,
    include_stats: bool = True
) -> Dict[str, Any]:
    """
    Run complete BDL sync for a sport.
    
    Args:
        db: MongoDB database
        sport: Sport key ('nba' or 'mlb')
        include_players: Sync player roster
        include_stats: Sync game logs/stats
        
    Returns:
        Combined sync results
    """
    service = get_bdl_universal_service(db)
    
    results = {
        "sport": sport,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }
    
    if include_players:
        results["players"] = await service.sync_players(sport)
    
    if include_stats:
        results["stats"] = await service.sync_stats_batched(sport)
    
    return results
