"""
Universal Odds Sync Service
============================
Multi-sport odds synchronization using The Odds API.

Supports:
- NBA (basketball_nba): Points, Rebounds, Assists, PRA
- MLB (baseball_mlb): Pitcher strikeouts, walks, hits allowed; 
                      Batter hits, total bases, RBIs, runs, stolen bases

Bookmakers Supported:
- PrizePicks (DFS)
- DraftKings (DK)
- FanDuel (FD)
- Sharp Books: Pinnacle, Circa, BetCRIS

Each sport saves to its own collection:
- NBA: dg_live_props (legacy name)
- MLB: mlb_live_props
"""
import os
import time
import uuid
import logging
import asyncio
from typing import Dict, List, Any, Optional, Set
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

# Budget guard — every odds-api call funnels through here.
from services.odds_api_budget import hourly_count as _budget_hourly_count

def hourly_count_safe() -> int:
    try:
        return _budget_hourly_count()
    except Exception:
        return -1


from config.db_config import get_collection_name, validate_sport, SPORT_CONFIG
from services.config.collection_names import COLL
from services.market_catalog import MarketCatalog

logger = logging.getLogger(__name__)

# =============================================================================
# GLOBAL IDENTITY RESOLVER (2026-04-23)
# =============================================================================
# Identity resolution happens ONCE at ingest time (the system boundary).
# Every downstream scoring / game-log / projection join uses the stamped
# `bdl_player_id` exclusively — no name-based joins.
#
# Failures are flagged (`identity_status="missing_bdl_id"`), not rejected,
# so props without a resolvable ID still land in the live collection where
# observability dashboards can surface the gap.

def _normalize_player_name_for_ingest(name: Optional[str]) -> str:
    """Identity-boundary name normalizer (INGEST ONLY).

    The master hub's `normalized_name` column follows the convention:
      lowercase, periods/apostrophes stripped (so "C.J." → "cj"),
      hyphens/commas replaced with spaces, suffixes (Jr/Sr/II/III/IV/V)
      dropped, whitespace collapsed. This function produces the same
      shape so ingest-time ID resolution is deterministic.

    This function is ONLY for identity resolution at ingest. Scoring
    pipelines must NOT import it — they join on `bdl_player_id`.
    """
    if not name:
        return ""
    import re
    s = str(name).lower().strip()
    # Periods + apostrophes → removed (no whitespace inserted) so initials
    # like "C.J." collapse to "cj" matching the hub format.
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[,\-]", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def _build_nba_identity_map(db) -> Dict[str, int]:
    """Build normalized_name → bdl_player_id map from the NBA master hub.
    The hub's `bdl_id` is the canonical identity (100% populated on
    hub rows); `bdl_player_id` is an alias present on newer rows.
    Prefer whichever is available."""
    out: Dict[str, int] = {}
    hub = db[COLL("master_hub", "nba")]
    cursor = hub.find(
        {},
        {
            "_id": 0,
            "bdl_id": 1, "bdl_player_id": 1,
            "normalized_name": 1, "display_name": 1,
            "player_name": 1, "first_name": 1,
        },
    )
    async for doc in cursor:
        pid = doc.get("bdl_player_id") or doc.get("bdl_id")
        if pid is None:
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        aliases = set()
        for raw in (
            doc.get("normalized_name"),
            doc.get("display_name"),
            doc.get("player_name"),
        ):
            norm = _normalize_player_name_for_ingest(raw)
            if norm:
                aliases.add(norm)
        fn = doc.get("first_name")
        display = doc.get("display_name") or doc.get("player_name")
        if fn and display:
            combo = _normalize_player_name_for_ingest(f"{fn} {display}")
            if combo:
                aliases.add(combo)
        for alias in aliases:
            # First writer wins — keeps behavior deterministic on the
            # rare alias collision.
            if alias not in out:
                out[alias] = pid_int
    return out


async def _build_mlb_identity_map(db) -> Dict[str, int]:
    """Build normalized_name → bdl_player_id map from the MLB master hub."""
    out: Dict[str, int] = {}
    try:
        hub = db[COLL("master_hub", "mlb")]
    except Exception:
        return out
    cursor = hub.find(
        {},
        {
            "_id": 0,
            "bdl_id": 1, "bdl_player_id": 1,
            "normalized_name": 1, "display_name": 1,
            "player_name": 1, "first_name": 1,
        },
    )
    async for doc in cursor:
        pid = doc.get("bdl_player_id") or doc.get("bdl_id")
        if pid is None:
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        aliases = set()
        for raw in (
            doc.get("normalized_name"),
            doc.get("display_name"),
            doc.get("player_name"),
        ):
            norm = _normalize_player_name_for_ingest(raw)
            if norm:
                aliases.add(norm)
        for alias in aliases:
            if alias not in out:
                out[alias] = pid_int
    return out


async def _stamp_identity_on_props(
    db, sport: str, props: List[Dict[str, Any]]
) -> tuple:
    """Stamp `bdl_player_id` + `identity_status` on every prop in place.

    Returns (resolved_count, missing_count).
    """
    if sport == "nba":
        id_map = await _build_nba_identity_map(db)
    elif sport == "mlb":
        id_map = await _build_mlb_identity_map(db)
    else:
        id_map = {}
    resolved = 0
    missing = 0
    for p in props:
        norm = _normalize_player_name_for_ingest(p.get("player_name"))
        pid = id_map.get(norm) if norm else None
        if pid is not None:
            p["bdl_player_id"] = pid
            p["identity_status"] = "resolved"
            resolved += 1
        else:
            p["bdl_player_id"] = None
            p["identity_status"] = "missing_bdl_id"
            missing += 1
    logger.info(
        f"[IDENTITY:{sport}] Stamped {resolved} resolved / "
        f"{missing} missing bdl_player_id on {len(props)} props "
        f"(hub aliases indexed: {len(id_map)})"
    )
    return resolved, missing

# API Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# =============================================================================
# BOOKMAKER CONFIGURATION
# =============================================================================

# DFS Region (PrizePicks, Underdog, etc.)
DFS_REGION = "us_dfs"
# US sportsbooks region (DraftKings, FanDuel, etc.)
US_REGION = "us"
# EU region (Pinnacle, Bet365)
EU_REGION = "eu"

# Bookmaker categories
BOOKMAKER_CONFIG = {
    # DFS platforms
    "prizepicks": {
        "region": "us_dfs",
        "display_name": "PrizePicks",
        "is_dfs": True,
        "is_sharp": False,
        "priority": 1,  # Primary source
    },
    "underdog": {
        "region": "us_dfs",
        "display_name": "Underdog Fantasy",
        "is_dfs": True,
        "is_sharp": False,
        "priority": 2,
    },
    # US Sportsbooks
    "draftkings": {
        "region": "us",
        "display_name": "DraftKings",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 3,
    },
    "fanduel": {
        "region": "us",
        "display_name": "FanDuel",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 4,
    },
    "betonlineag": {
        # Odds API key is "betonlineag" (BetOnline.ag).
        "region": "us2",
        "display_name": "BetOnline",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 4,
    },
    "betmgm": {
        "region": "us",
        "display_name": "BetMGM",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 5,
    },
    "williamhill_us": {
        # Odds API key is "williamhill_us" — this is CAESARS Sportsbook
        # post-rebrand (Caesars acquired William Hill US in 2021 and
        # rebranded the operation but the Odds API kept the legacy
        # key for backward-compat). Live API titles this bookmaker
        # exactly as "Caesars". Added 2026-05-11 to extend the
        # universal book pool from 4 sportsbooks → 5 (DK/FD/MGM/BOL +
        # CSR) at zero additional Odds API credit cost — region=us is
        # already in the active regions set, so adding the bookmaker
        # to the request just expands which books are returned, not
        # which markets are billed.
        "region": "us",
        "display_name": "Caesars",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 5,
    },
    # ── 2026-05-13: 6 additional US sportsbooks (all in `regions=us`,
    # FREE under existing credit accounting — same region, same
    # markets, just more books in the response payload).
    # Probed against a live MLB event and returned non-trivial
    # coverage (>=80 outcomes each):
    "espnbet": {
        "region": "us",
        "display_name": "ESPN BET",
        "is_dfs": False, "is_sharp": False, "priority": 5,
    },
    "hardrockbet": {
        "region": "us",
        "display_name": "Hard Rock Bet",
        "is_dfs": False, "is_sharp": False, "priority": 5,
    },
    "betrivers": {
        "region": "us",
        "display_name": "BetRivers",
        "is_dfs": False, "is_sharp": False, "priority": 5,
    },
    "betparx": {
        "region": "us",
        "display_name": "betPARX",
        "is_dfs": False, "is_sharp": False, "priority": 5,
    },
    "ballybet": {
        "region": "us",
        "display_name": "Bally Bet",
        "is_dfs": False, "is_sharp": False, "priority": 5,
    },
    "fliff": {
        "region": "us",
        "display_name": "Fliff",
        "is_dfs": False, "is_sharp": False, "priority": 6,
    },
    # Sharp Books (lower limits, sharper lines)
    "pinnacle": {
        "region": "eu",
        "display_name": "Pinnacle",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 10,  # Sharp reference
    },
    "circa": {
        "region": "us",
        "display_name": "Circa",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 11,
    },
    "betcris": {
        "region": "eu",
        "display_name": "BetCRIS",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 12,
    },
}

# Default bookmakers to fetch (prioritized list)
DEFAULT_BOOKMAKERS = ["prizepicks", "draftkings", "fanduel", "betonlineag", "pinnacle"]
SHARP_BOOKMAKERS = ["pinnacle", "circa", "betcris"]

# Sports-book quintet the user asked us to pull "all markets" from.
# Applied to both NBA and MLB sharp-enrichment paths.
#   - BetMGM added 2026-04-22 after the "what about BetMGM?" follow-up.
#   - Caesars (williamhill_us) added 2026-05-11 after the live-probe
#     audit confirmed Caesars is included free in the us-region pull
#     and was the only book quoting ~9% of standard NBA props on the
#     Jan-15-2025 probe slate.
#   - 2026-05-13: ESPN BET / Hard Rock / BetRivers / BetParx / BallyBet
#     / Fliff added after "pull from all books" directive — all share
#     the same `us` region so they're free credit-wise.
USER_SHARP_BOOKMAKERS = [
    "draftkings", "fanduel", "betonlineag", "betmgm", "williamhill_us",
    "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
]

# MLB-specific: PrizePicks anchor + DK/FD/BOL/MGM/CSR + 6 new books
# (2026-05-13 — "pull from all books" expansion).
MLB_BOOKMAKERS = [
    "prizepicks",
    "draftkings", "fanduel", "betonlineag", "betmgm", "williamhill_us",
    "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
]

# =============================================================================
# SPORT-SPECIFIC CONFIGURATION
# =============================================================================

SPORT_API_CONFIG = {
    "nba": {
        "sport_key": "basketball_nba",
        "display_name": "NBA",
        # NBA Markets — 2026-04-24: expanded market list so universal-odds-sync
        # pulls BOTH sides (and thus `{book}_odds_opp`) for every stat_type
        # the NBA adapter scores. Previously only PTS/REB/AST/PRA (standard
        # + alternate) were pulled, which caused 888 NBA scored props — on
        # threes/steals/blocks, two-stat combos, and turnovers — to enter
        # the board via the PrizePicks-anchored path without any paired
        # DK/FD quotes. Those rows were routed to `gate_tp_unavailable`.
        # Adding these markets closes the market-coverage half of the
        # TP-anchor gap (the other half, alt-line one-sided quotes, is
        # inherent to the US sportsbook alt-market API shape).
        "markets": [
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_points_rebounds_assists",          # PRA combo
            "player_points_alternate",
            "player_rebounds_alternate",
            "player_assists_alternate",
            "player_points_rebounds_assists_alternate",
            # 2026-04-24 additions — every stat_type scored by NBA adapter
            "player_threes",
            "player_threes_alternate",
            "player_steals",
            "player_steals_alternate",
            "player_blocks",
            "player_blocks_alternate",
            "player_turnovers",
            "player_turnovers_alternate",
            # Two-stat combos — heavily subscribed by PrizePicks.
            "player_points_rebounds",
            "player_points_rebounds_alternate",
            "player_points_assists",
            "player_points_assists_alternate",
            "player_rebounds_assists",
            "player_rebounds_assists_alternate",
        ],
        # Map Odds API market names to our stat types
        "stat_type_map": {
            "player_points": "PTS",
            "player_points_alternate": "PTS",
            "player_rebounds": "REB",
            "player_rebounds_alternate": "REB",
            "player_assists": "AST",
            "player_assists_alternate": "AST",
            "player_points_rebounds_assists": "PRA",
            "player_points_rebounds_assists_alternate": "PRA",
            # 2026-04-24 additions
            "player_threes": "3PM",
            "player_threes_alternate": "3PM",
            "player_steals": "STL",
            "player_steals_alternate": "STL",
            "player_blocks": "BLK",
            "player_blocks_alternate": "BLK",
            "player_turnovers": "TO",
            "player_turnovers_alternate": "TO",
            # Preserve raw combo keys — scoring adapter / gate aliases
            # already handle them downstream.
            #
            # 2026-05-13 SSOT FIX — combos NOW collapse to short codes
            # (PR / PA / RA), matching the canonical pattern already used
            # for `PTS / REB / AST / PRA / 3PM / STL / BLK / TO`.
            # Previously these were left as raw market keys, which:
            #   • produced two stat_type representations in the SAME
            #     `nba_prop_scores` collection (PRA short + PR long form)
            #   • caused GameLogBarChart to render "No game data" for
            #     every alt-combo prop because its STAT_FIELD_MAP only
            #     keyed short codes (Ayo Dosunmu P+R 19.5 repro).
            # Single source of truth: ONE token per stat family,
            # owned here at the ingest boundary. See
            # `services/scoring/stat_family.py` for downstream alias
            # canonicalization.
            "player_points_rebounds":           "PR",
            "player_points_rebounds_alternate": "PR",
            "player_points_assists":            "PA",
            "player_points_assists_alternate":  "PA",
            "player_rebounds_assists":          "RA",
            "player_rebounds_assists_alternate":"RA",
        },
        # PrizePicks anchor + DK + FD + BetOnline + BetMGM + Caesars
        # + ESPN BET + Hard Rock + BetRivers + BetParx + Bally Bet + Fliff
        # (2026-05-13 "pull from all books" expansion — every new book
        # is in regions=us so it's free credit-wise).
        "bookmakers": [
            "prizepicks",
            "draftkings", "fanduel", "betonlineag", "betmgm", "williamhill_us",
            "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
        ],
    },
    "mlb": {
        "sport_key": "baseball_mlb",
        "display_name": "MLB",
        # MLB Markets - ALL available markets from PrizePicks (verified via API)
        "markets": [
            # Batter props - Standard
            "batter_home_runs",
            "batter_hits",
            "batter_total_bases",
            "batter_rbis",
            "batter_runs_scored",
            "batter_stolen_bases",
            "batter_walks",
            "batter_strikeouts",
            "batter_singles",
            "batter_doubles",
            "batter_triples",
            # Batter combo props
            "batter_hits_runs_rbis",
            "batter_first_home_run",
            # Batter props - Alternate lines (PrizePicks verified)
            "batter_home_runs_alternate",
            "batter_hits_alternate",
            "batter_total_bases_alternate",
            "batter_rbis_alternate",
            "batter_runs_scored_alternate",
            "batter_stolen_bases_alternate",
            "batter_walks_alternate",
            "batter_strikeouts_alternate",
            "batter_singles_alternate",
            "batter_doubles_alternate",
            "batter_triples_alternate",
            # Pitcher props - Standard
            "pitcher_strikeouts",
            "pitcher_hits_allowed",
            "pitcher_walks",
            "pitcher_earned_runs",
            "pitcher_outs",
            "pitcher_record_a_win",
            # Pitcher props - Alternate lines (PrizePicks verified)
            "pitcher_strikeouts_alternate",
            "pitcher_hits_allowed_alternate",
            "pitcher_walks_alternate",
            "pitcher_earned_runs_alternate",
            "pitcher_outs_alternate",
        ],
        # Map Odds API market names to our stat types
        "stat_type_map": {
            # Pitcher stats
            "pitcher_strikeouts": "Pitcher Strikeouts",
            "pitcher_strikeouts_alternate": "Pitcher Strikeouts",
            "pitcher_walks": "Walks Allowed",
            "pitcher_walks_alternate": "Walks Allowed",
            "pitcher_hits_allowed": "Hits Allowed",
            "pitcher_hits_allowed_alternate": "Hits Allowed",
            "pitcher_earned_runs": "Earned Runs",
            "pitcher_earned_runs_alternate": "Earned Runs",
            "pitcher_outs": "Pitcher Outs",
            "pitcher_outs_alternate": "Pitcher Outs",
            "pitcher_record_a_win": "Pitcher Win",
            # Batter stats
            "batter_home_runs": "Home Runs",
            "batter_home_runs_alternate": "Home Runs",
            "batter_hits": "Hits",
            "batter_hits_alternate": "Hits",
            "batter_total_bases": "Total Bases",
            "batter_total_bases_alternate": "Total Bases",
            "batter_rbis": "RBIs",
            "batter_rbis_alternate": "RBIs",
            "batter_runs_scored": "Runs",
            "batter_runs_scored_alternate": "Runs",
            "batter_stolen_bases": "Stolen Bases",
            "batter_stolen_bases_alternate": "Stolen Bases",
            "batter_walks": "Batter Walks",
            "batter_walks_alternate": "Batter Walks",
            "batter_strikeouts": "Batter Strikeouts",
            "batter_strikeouts_alternate": "Batter Strikeouts",
            "batter_singles": "Singles",
            "batter_singles_alternate": "Singles",
            "batter_doubles": "Doubles",
            "batter_doubles_alternate": "Doubles",
            "batter_triples": "Triples",
            "batter_triples_alternate": "Triples",
            # Combo stats
            "batter_hits_runs_rbis": "Hits+Runs+RBIs",
            "batter_hits_runs_rbis_alternate": "Hits+Runs+RBIs",
            "batter_total_bases_runs_rbis": "Total Bases+Runs+RBIs",
            "batter_total_bases_runs_rbis_alternate": "Total Bases+Runs+RBIs",
            "batter_hits_runs": "Hits+Runs",
            "batter_hits_runs_alternate": "Hits+Runs",
        },
        # PrizePicks anchor + DK + FD + BetOnline + BetMGM + Caesars
        # + ESPN BET + Hard Rock + BetRivers + BetParx + Bally Bet + Fliff
        # (2026-05-13 — "pull from all books" expansion; all extra
        # books are in regions=us so they're free credit-wise).
        "bookmakers": [
            "prizepicks",
            "draftkings", "fanduel", "betonlineag", "betmgm", "williamhill_us",
            "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
        ],
    }
}


class UniversalOddsSyncService:
    """
    Universal odds sync service supporting multiple sports.
    
    Fetches props from The Odds API and saves to sport-specific collections.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None

        # Dynamic market discovery. Replaces hardcoded market whitelists
        # per the 2026-04-21 "pull all available markets" requirement.
        # Per-event market lists are cached in-instance so we don't pay
        # the discovery credit twice for the same event within a sync.
        self._market_catalog = MarketCatalog(ODDS_API_KEY)

        # Per-sport union-market memo. Reset every sync start.
        self._sport_market_union: Dict[str, List[str]] = {}

        # Approximate Odds API credit usage for the current sync
        # (cleared each call to sync_sport_props). Surfaced in the sync
        # results so operators can monitor spend.
        self.credits_used: Dict[str, int] = {
            "events": 0,
            "market_discovery": 0,
            "event_odds": 0,
        }
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create shared HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    def _get_sport_config(self, sport: str) -> Dict[str, Any]:
        """Get API configuration for a sport."""
        sport = validate_sport(sport)
        if sport not in SPORT_API_CONFIG:
            raise ValueError(f"No API config for sport: {sport}")
        return SPORT_API_CONFIG[sport]
    
    async def fetch_events(self, sport: str = "nba") -> List[Dict[str, Any]]:
        """
        Fetch all events for a sport from The Odds API.
        
        Args:
            sport: Sport to fetch ('nba' or 'mlb')
            
        Returns:
            List of events with game info
        """
        config = self._get_sport_config(sport)
        sport_key = config["sport_key"]
        display_name = config["display_name"]
        
        logger.info(f"[UNIVERSAL_ODDS] Fetching {display_name} events...")
        
        # ── Budget guard ──────────────────────────────────────────────
        from services.odds_api_budget import (
            check_and_increment, log_call_result, current_caller,
            OddsApiBudgetExceeded,
        )
        caller = current_caller()
        try:
            check_and_increment(
                caller=caller, sport=sport, endpoint="events")
        except OddsApiBudgetExceeded as exc:
            logger.error(f"[ODDS_BUDGET] fetch_events blocked: {exc}")
            return []

        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            logger.info(
                f"[ODDS_BUDGET] caller={caller} sport={sport} "
                f"endpoint=events status={response.status_code} "
                f"hour_count={hourly_count_safe()}"
            )
            await log_call_result(
                self.db, caller=caller, sport=sport, endpoint="events",
                url=url, status_code=response.status_code, sync_mode="full_or_delta",
            )

            if response.status_code == 200:
                events = response.json()
                logger.info(f"[UNIVERSAL_ODDS] Found {len(events)} {display_name} events")
                
                for e in events[:5]:
                    logger.info(f"  • {e.get('away_team')} @ {e.get('home_team')}")
                
                return events
            else:
                logger.warning(f"[UNIVERSAL_ODDS] {display_name} events fetch returned {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"[UNIVERSAL_ODDS] Event fetch error for {display_name}: {e}")
            return []
    
    async def _resolve_markets_for_sport(
        self,
        sport: str,
        sport_key: str,
        bookmakers: List[str],
        regions_str: str,
        sample_events: List[Dict[str, Any]],
    ) -> List[str]:
        """Discover the union of markets offered by ``bookmakers`` across
        a sample of upcoming events for this sport.

        The result is cached on the instance (``_sport_market_union``) so
        every event in the current sync reuses the same discovered list
        — we only pay the discovery credits once per sport per sync.

        **2026-04-24**: now honours ``NBA_PULL_ALL_MARKETS=true`` (default
        true) which flips discovery to ``include_all_markets=True`` —
        every market key the sportsbook exposes (player_*, game_*,
        team_totals, period/quarter/half, novelty) gets pulled. A
        persistent Mongo cache (``dg_market_catalog_cache``) with
        ``NBA_MARKETS_CACHE_TTL_SECONDS`` TTL (default 3600) is used
        before hitting the Odds API.

        Falls back to the sport's hardcoded ``SPORT_API_CONFIG[sport].markets``
        list if the catalog returns nothing (e.g. API glitch, pre-season
        window).
        """
        cached = self._sport_market_union.get(sport)
        if cached is not None:
            return cached

        # ---- 0. env flags -----------------------------------------------
        pull_all = os.environ.get(
            "NBA_PULL_ALL_MARKETS", "true"
        ).strip().lower() in ("1", "true", "yes", "on")
        ttl_seconds = int(os.environ.get(
            "NBA_MARKETS_CACHE_TTL_SECONDS", "3600") or 3600)

        # ---- 1. persistent mongo cache (1h by default) -------------------
        cache_coll = self.db["dg_market_catalog_cache"]
        cache_key = f"{sport}:all={pull_all}:books={','.join(sorted(bookmakers))}"
        try:
            cache_doc = await cache_coll.find_one({"cache_key": cache_key})
        except Exception:
            cache_doc = None
        now_ts = time.time()
        if cache_doc and (now_ts - float(cache_doc.get("cached_at_ts", 0))) < ttl_seconds:
            cached_markets = list(cache_doc.get("markets") or [])
            if cached_markets:
                logger.info(
                    f"[UNIVERSAL_ODDS] {sport}: market catalog cache HIT "
                    f"({len(cached_markets)} markets, "
                    f"age={int(now_ts - cache_doc['cached_at_ts'])}s, "
                    f"ttl={ttl_seconds}s, pull_all={pull_all})"
                )
                self._sport_market_union[sport] = cached_markets
                return cached_markets

        # ---- 2. live discovery ------------------------------------------
        event_ids = [e.get("id") for e in sample_events if e.get("id")]
        client = await self._get_client()

        discovered: List[str] = []
        if event_ids:
            discovered = await self._market_catalog.discover_union_across_events(
                client=client,
                sport_key=sport_key,
                event_ids=event_ids,
                regions=regions_str,
                bookmakers=bookmakers,
                include_game_markets=pull_all,      # h2h / spreads / totals etc
                include_all_markets=pull_all,       # + team_totals + period + novelty
                max_events=3,
            )
            # Each probe event = 1 credit.
            self.credits_used["market_discovery"] += min(3, len(event_ids))

        if not discovered:
            # Fall back to the sport-specific hardcoded list so we never
            # silently serve zero props when discovery fails. If a prior
            # cache entry exists (even stale) prefer it to the hardcoded
            # subset — matches the "do NOT silently fall back to a tiny
            # default list" requirement.
            if cache_doc and cache_doc.get("markets"):
                discovered = list(cache_doc["markets"])
                logger.warning(
                    f"[UNIVERSAL_ODDS] {sport}: discovery returned 0 markets; "
                    f"re-using STALE cache ({len(discovered)} markets, "
                    f"age={int(now_ts - cache_doc['cached_at_ts'])}s)"
                )
            else:
                config = self._get_sport_config(sport)
                discovered = list(config.get("markets", []))
                logger.warning(
                    f"[UNIVERSAL_ODDS] {sport}: dynamic market discovery returned "
                    f"no markets AND no cache; falling back to "
                    f"{len(discovered)} hardcoded markets"
                )

        # ---- 3. persist cache -------------------------------------------
        if discovered:
            try:
                await cache_coll.update_one(
                    {"cache_key": cache_key},
                    {"$set": {
                        "cache_key": cache_key,
                        "sport": sport,
                        "bookmakers": sorted(bookmakers),
                        "pull_all_markets": pull_all,
                        "markets": discovered,
                        "market_count": len(discovered),
                        "cached_at_ts": now_ts,
                        "cached_at_iso": datetime.now(timezone.utc).isoformat(),
                        "ttl_seconds": ttl_seconds,
                    }},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"[UNIVERSAL_ODDS] cache persist failed: {e}")

        self._sport_market_union[sport] = discovered
        logger.info(
            f"[UNIVERSAL_ODDS] {sport}: using {len(discovered)} markets for this "
            f"sync across books={bookmakers} (pull_all={pull_all}, "
            f"cache_ttl={ttl_seconds}s)"
        )
        return discovered

    async def _persist_raw_markets(
        self,
        odds_data: Dict[str, Any],
        event_info: Dict[str, Any],
        sport: str,
    ) -> Dict[str, Any]:
        """Write every outcome from every bookmaker × market into
        ``dg_raw_odds_markets`` — one row per (bookmaker, market, outcome).

        This is the durable record of ALL markets the sportsbook exposed,
        including markets we don't map into scoring yet. The record
        preserves the exact spec requirements:
          market_key, player_or_team, line/point, price, sportsbook,
          game_id, timestamp, mapped-vs-unmapped flag.

        The return summary lets the caller report mapped vs unmapped
        counts in the sync validation block.
        """
        # 2026-05-13 SSOT — stat_type_map is now derived from the
        # canonical-stats registry (services.scoring.canonical_stats)
        # instead of the embedded SPORT_API_CONFIG dict. The local
        # binding is preserved so the surrounding logic is unchanged;
        # only the source of truth has moved.
        from services.scoring.canonical_stats import market_to_stat_map
        from services.market_class import classify_market_key, build_canonical_v2
        stat_type_map = market_to_stat_map(sport)

        event_id = odds_data.get("event_id") or event_info.get("id") or ""
        home = event_info.get("home_team")
        away = event_info.get("away_team")
        commence = event_info.get("commence_time")
        now_iso = datetime.now(timezone.utc).isoformat()

        # 2026-05-17 hardening — every scrape gets a unique scrape_id
        # so the append-only snapshot collection can group rows that
        # belong to the same physical odds-API call. Survives across
        # retries (one fetch_event_odds → one scrape_id).
        import uuid as _uuid
        scrape_id = f"{sport}|{event_id}|{now_iso}|{_uuid.uuid4().hex[:8]}"

        mapped = 0
        unmapped = 0
        unmapped_keys: Set[str] = set()
        rows: List[Dict[str, Any]] = []
        # 2026-05-17 hardening — companion list of VERBATIM raw rows
        # for the append-only `dg_raw_odds_snapshots` collection.
        # Carries the raw market + outcome JSON so future replay /
        # forensic-trace work can reconstruct upstream intent
        # exactly. Never overwritten.
        snapshot_rows: List[Dict[str, Any]] = []

        for bm in odds_data.get("bookmakers") or []:
            bm_key = bm.get("key")
            if not bm_key:
                continue
            for market in bm.get("markets") or []:
                mkey = market.get("key")
                if not mkey:
                    continue
                is_mapped = mkey in stat_type_map
                if is_mapped:
                    mapped += 1
                else:
                    unmapped += 1
                    unmapped_keys.add(mkey)

                for outcome in market.get("outcomes") or []:
                    # `description` holds player name on player_* markets;
                    # team-level markets use `name` for the team / side.
                    player = outcome.get("description")
                    team_or_side = outcome.get("name")
                    # 2026-05-17 hardening — classify market class &
                    # carry the source market_key into observability.
                    market_class = classify_market_key(mkey)
                    rows.append({
                        "sport": sport,
                        "event_id": event_id,
                        "home_team": home,
                        "away_team": away,
                        "commence_time": commence,
                        "bookmaker": bm_key,
                        "market_key": mkey,
                        "mapped_stat_type": stat_type_map.get(mkey),
                        "is_mapped": is_mapped,
                        "player_name": player,
                        "team_or_side": team_or_side,
                        "line": outcome.get("point"),
                        "price": outcome.get("price"),
                        "fetched_at": now_iso,
                        # New observability fields. The legacy
                        # `dg_raw_odds_markets` collection is still
                        # overwrite-on-scrape (latest-state cache), so
                        # these aid the in-memory join — the durable
                        # forensic record lives in
                        # `dg_raw_odds_snapshots` below.
                        "market_class": market_class,
                        "source_market_key": mkey,
                        "is_alternate_market": market_class == "alternate",
                    })
                    # Append-only forensic record. We store the
                    # outcome dict VERBATIM (deep-copied via dict()
                    # so future mutations of `market` don't leak in)
                    # and the parent market metadata. A composite
                    # canonical_candidate is built so future replay
                    # can reverse-map the snapshot back to the
                    # canonical/v2 key without re-running
                    # classification logic.
                    side = "OVER" if str(team_or_side or "").lower() == "over" else (
                        "UNDER" if str(team_or_side or "").lower() == "under" else None
                    )
                    line_val = outcome.get("point")
                    if (player and stat_type_map.get(mkey)
                            and line_val is not None and side):
                        legacy = (
                            f"{sport}|{event_id}|{player}|"
                            f"{stat_type_map.get(mkey)}|{float(line_val)}|{side}"
                        )
                        canonical_candidate = legacy
                        canonical_v2_candidate = build_canonical_v2(
                            legacy, market_class)
                    else:
                        canonical_candidate = None
                        canonical_v2_candidate = None
                    snapshot_rows.append({
                        "scrape_id": scrape_id,
                        "fetched_at": now_iso,
                        "sport": sport,
                        "event_id": event_id,
                        "commence_time": commence,
                        "home_team": home,
                        "away_team": away,
                        "bookmaker": bm_key,
                        "market_key": mkey,
                        "market_class": market_class,
                        "raw_market_json": {
                            # Header-only — outcomes intentionally
                            # elided to avoid O(N²) duplication;
                            # they live one-per-row below.
                            "key": market.get("key"),
                            "last_update": market.get("last_update"),
                            "description": market.get("description"),
                        },
                        "raw_outcome_json": dict(outcome),
                        "outcome_name": team_or_side,
                        "outcome_description": player,
                        "outcome_point": outcome.get("point"),
                        "outcome_price": outcome.get("price"),
                        "canonical_candidate": canonical_candidate,
                        "canonical_v2_candidate": canonical_v2_candidate,
                        "source_file": "universal_odds_sync._persist_raw_markets",
                        "ingest_version": "v1.0_2026_05_17",
                    })

        coll = self.db["dg_raw_odds_markets"]
        # Replace every row for this (sport, event_id) each sync so the
        # collection stays fresh — dg_raw_odds_markets is an observability
        # table, not a history store. If ever we want history we'd
        # timestamp-partition.
        try:
            await coll.delete_many({"sport": sport, "event_id": event_id})
            if rows:
                await coll.insert_many(rows, ordered=False)
        except Exception as e:
            logger.warning(f"[UNIVERSAL_ODDS] raw-markets write error: {e}")

        # 2026-05-17 — writer GATED behind `DEBUG_RAW_ODDS=true`.
        # Originally introduced as an append-only forensic store; the
        # collection grew to 14.6 M docs / 13.7 GB BSON (~1.56 GB on
        # disk + 716 MB indexes) in ~36 hours of operation, blocking
        # /app partition writes. Per stabilization decision, the
        # collection was dropped 2026-05-17 and writes are now
        # disabled by default. Re-enable via `DEBUG_RAW_ODDS=true` ONLY
        # for short forensic windows. Long-term replacement: re-pull
        # via The Odds API historical endpoint.
        # Audit: `audits/DG_RAW_ODDS_SNAPSHOTS_DROP_2026_05_17.md`.
        if os.environ.get("DEBUG_RAW_ODDS", "").lower() == "true":
            snap_coll = self.db["dg_raw_odds_snapshots"]
            try:
                if snapshot_rows:
                    await snap_coll.insert_many(snapshot_rows, ordered=False)
            except Exception as e:
                logger.warning(f"[UNIVERSAL_ODDS] raw-snapshots write error: {e}")
        # else: writer disabled — admin audit endpoints will return
        # empty result sets until the writer is re-enabled.

        return {
            "written": len(rows),
            "mapped": mapped,
            "unmapped": unmapped,
            "unmapped_keys": sorted(unmapped_keys),
        }

    async def fetch_event_odds(
        self,
        event_id: str,
        event_info: Dict[str, Any],
        sport: str = "nba",
        bookmakers: List[str] = None,
        markets_override: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch odds for a single event from multiple bookmakers.

        Args:
            event_id: The Odds API event ID
            event_info: Event metadata (teams, time, etc.)
            sport: Sport to fetch ('nba' or 'mlb')
            bookmakers: List of bookmakers to fetch
            markets_override: Explicit markets list (typically the
                union discovered by ``_resolve_markets_for_sport``). If
                omitted we fall back to the sport-config hardcoded list
                (legacy behavior for unit tests).

        Returns:
            Odds data with all player props from all bookmakers
        """
        config = self._get_sport_config(sport)
        sport_key = config["sport_key"]

        if markets_override is not None and markets_override:
            markets_list = markets_override
        else:
            markets_list = list(config.get("markets", []))
        markets = ",".join(markets_list)

        # Default bookmakers if not specified - use sport-specific config
        if bookmakers is None:
            if "bookmakers" in config:
                bookmakers = config["bookmakers"]
            else:
                bookmakers = DEFAULT_BOOKMAKERS
        
        # Build regions list based on bookmakers
        regions = set()
        for bm in bookmakers:
            bm_config = BOOKMAKER_CONFIG.get(bm)
            if bm_config:
                regions.add(bm_config["region"])
        
        regions_str = ",".join(regions)
        bookmakers_str = ",".join(bookmakers)
        
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
            
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": regions_str,
                "markets": markets,
                "bookmakers": bookmakers_str,
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }
            
            # ── Budget guard ─────────────────────────────────────────
            from services.odds_api_budget import (
                check_and_increment, log_call_result, current_caller,
                OddsApiBudgetExceeded,
            )
            caller = current_caller()
            try:
                check_and_increment(
                    caller=caller, sport=sport, endpoint="event_odds")
            except OddsApiBudgetExceeded as exc:
                logger.error(
                    f"[ODDS_BUDGET] fetch_event_odds blocked: "
                    f"caller={caller} sport={sport} event={event_id[:8]} "
                    f"err={exc}")
                return {}

            # ================================================================
            # DIAGNOSTIC LOG 1: Raw Request Parameters
            # ================================================================
            logger.info(f"[ODDS_DIAG] REQUEST: url={url}")
            logger.info(f"[ODDS_DIAG] REQUEST: regions={regions_str}, bookmakers={bookmakers_str}")
            logger.info(f"[ODDS_DIAG] REQUEST: markets={markets[:100]}...")
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            logger.info(
                f"[ODDS_BUDGET] caller={caller} sport={sport} "
                f"endpoint=event_odds event={event_id[:8]} "
                f"status={response.status_code} "
                f"hour_count={hourly_count_safe()}"
            )
            await log_call_result(
                self.db, caller=caller, sport=sport, endpoint="event_odds",
                url=url, status_code=response.status_code,
                sync_mode="full_or_delta",
            )

            
            if response.status_code == 200:
                odds_data = response.json()
                
                # ================================================================
                # DIAGNOSTIC LOG 2: Raw JSON Payload for specific players
                # ================================================================
                raw_bookmakers = odds_data.get("bookmakers", [])
                logger.info(f"[ODDS_DIAG] RESPONSE: {len(raw_bookmakers)} bookmakers in payload")
                
                # Log bookmaker keys received
                bm_keys = [bm.get("key") for bm in raw_bookmakers]
                logger.info(f"[ODDS_DIAG] RESPONSE: Bookmaker keys = {bm_keys}")
                
                # Find Yordan Alvarez or Julio Rodriguez and log their data
                for bm in raw_bookmakers[:3]:  # First 3 bookmakers
                    bm_key = bm.get("key")
                    for market in bm.get("markets", []):
                        for outcome in market.get("outcomes", []):
                            player_name = outcome.get("description", "")
                            if any(name in player_name for name in ["Alvarez", "Rodriguez", "Olson"]):
                                logger.info(f"[ODDS_DIAG] PLAYER FOUND: {player_name}")
                                logger.info(f"[ODDS_DIAG]   Bookmaker: {bm_key}")
                                logger.info(f"[ODDS_DIAG]   Market: {market.get('key')}")
                                logger.info(f"[ODDS_DIAG]   Outcome: {outcome}")
                                break
                
                odds_data["event_id"] = event_id
                odds_data["sport"] = sport
                odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                odds_data["bookmakers_requested"] = bookmakers
                
                # Count outcomes per bookmaker
                total_outcomes = 0
                bookmaker_counts = {}
                for bm in odds_data.get("bookmakers", []):
                    bm_key = bm.get("key", "unknown")
                    bm_count = 0
                    for market in bm.get("markets", []):
                        bm_count += len(market.get("outcomes", []))
                    bookmaker_counts[bm_key] = bm_count
                    total_outcomes += bm_count
                
                odds_data["outcome_counts"] = bookmaker_counts
                
                logger.debug(
                    f"  [{config['display_name']}] {event_info.get('away_team')} @ "
                    f"{event_info.get('home_team')}: {total_outcomes} lines ({bookmaker_counts})"
                )
                
                return odds_data
            elif response.status_code == 404:
                logger.debug(f"  [ODDS] No props available for event {event_id}")
                return {}
            else:
                logger.warning(f"  [ODDS] Event odds returned {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"  [ODDS] Error fetching odds for {event_id}: {e}")
            return {}
    
    def extract_props_from_odds(
        self,
        odds_data: Dict[str, Any],
        event_info: Dict[str, Any],
        sport: str = "nba"
    ) -> List[Dict[str, Any]]:
        """
        Extract individual props from odds data with multi-bookmaker support.
        
        Groups props by player/stat and tracks lines from each bookmaker.
        Identifies sharp book lines for edge calculation.
        
        Args:
            odds_data: Raw odds data from API
            event_info: Event metadata
            sport: Sport for stat type mapping
            
        Returns:
            List of normalized prop dictionaries with multi-book data
        """
        config = self._get_sport_config(sport)
        # 2026-05-13 SSOT — registry-backed; see `_persist_raw_markets` note.
        from services.scoring.canonical_stats import market_to_stat_map
        stat_type_map = market_to_stat_map(sport)
        
        # =================================================================
        # UNIVERSAL CANONICAL POOL (SSOT, 2026-04-25)
        #
        # Architecture (replaces the legacy "PP-only anchor" model):
        #
        # The canonical prop pool is built from ANY of the allowed
        # books. PrizePicks is no longer an anchor; it is an overlay.
        # A canonical is created the FIRST time a (sport, event_id,
        # player, stat_type, line, side) tuple is seen across all
        # allowed books, in priority order:
        #
        #     prizepicks > draftkings > fanduel > betmgm > betonlineag
        #
        # When PP quotes the prop, the prior PP-anchored behaviour is
        # preserved exactly (same canonical_key, same layer fields,
        # same flat fields). When PP does NOT quote it, the canonical
        # is anchored on the next-priority book whose data is present,
        # `pp_layer = None`, `pp_available = False`, `playable_on_pp =
        # False`, and `source_anchor = "sportsbook_fallback"`.
        #
        # Canonical identity (UNCHANGED — downstream `canonical_key`
        # consumers in scoring / recompute / Ferrari readers continue
        # to work without modification):
        #     sport | event_id | player_name | stat_type | line | side
        #
        # Ferrari and PP-playable boards may filter on
        # `playable_on_pp == True`; backend keeps the full pool so
        # other surfaces (research, alt-line tracking, future
        # non-PP-playable products) have access to every market the
        # books actually published.
        #
        # See /app/memory/PRD.md "Universal SSOT canonical pool,
        # 2026-04-25" for the architecture rationale.
        # =================================================================

        # Allowed books (SSOT) — also defines anchor priority order.
        # 2026-05-11 — added "williamhill_us" (Caesars). Free under
        # regions=us; closes the gap where Caesars was the only book
        # quoting ~9% of the standard NBA prop pool on the probe slate.
        # 2026-05-13 — "Pull from all books" expansion: ESPN BET, Hard
        # Rock, BetRivers, BetParx, BallyBet, Fliff. All free under
        # regions=us. Sportsbook anchor priority below: existing primary
        # books retain their position (DK/FD/MGM/CSR/BOL), new books
        # added at the end so they NEVER seed a canonical that an
        # existing primary book would have anchored.
        ALLOWED_BOOKS = (
            "prizepicks", "draftkings", "fanduel", "betmgm",
            "williamhill_us", "betonlineag",
            "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
        )
        ANCHOR_PRIORITY = list(ALLOWED_BOOKS)

        canonical: Dict[str, Dict[str, Any]] = {}

        # --- Pass 1: union creation across all allowed books in
        # priority order. The first book to produce a (canon_key)
        # tuple seeds the canonical and stamps `source_anchor`
        # / `anchor_book`. Subsequent books in this same pass
        # (Pass 2 below) attach as layers without re-seeding.
        bookmakers_by_key = {
            (bm.get("key") or "unknown"): bm
            for bm in odds_data.get("bookmakers", [])
        }
        event_id = odds_data.get("event_id", "")

        for anchor_priority, bm_key in enumerate(ANCHOR_PRIORITY):
            bookmaker = bookmakers_by_key.get(bm_key)
            if not bookmaker:
                continue

            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_type = stat_type_map.get(market_key, market_key)

                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    if not player_name:
                        continue
                    line = outcome.get("point")
                    if line is None:
                        continue

                    outcome_name = outcome.get("name", "").lower()
                    side = "OVER" if "over" in outcome_name else "UNDER"
                    price = outcome.get("price", -110)

                    canon_key = (
                        f"{sport}|{event_id}|{player_name}|"
                        f"{stat_type}|{float(line)}|{side}"
                    )

                    if canon_key in canonical:
                        # Already seeded by a higher-priority book —
                        # this book will attach as a layer in Pass 2.
                        continue

                    is_pp_anchor = (bm_key == "prizepicks")
                    # 2026-05-17 hardening — market_class + v2 key SSOT.
                    # `market_class` and `canonical_key_v2` ride
                    # alongside the legacy fields so downstream
                    # consumers can choose either identity model.
                    # `source_market_key` is preserved verbatim from
                    # the upstream payload for forensic traceability.
                    from services.market_class import (
                        classify_market_key as _classify_mc,
                        build_canonical_v2 as _build_v2,
                    )
                    market_class = _classify_mc(market_key)
                    canonical_key_v2 = _build_v2(canon_key, market_class)
                    canonical[canon_key] = {
                        "canonical_key": canon_key,
                        # NOTE: legacy `canonical_key` is preserved
                        # unchanged. `canonical_key_v2` is the new
                        # market-class-aware identity. Adding a
                        # `market_class` segment to the legacy key
                        # would invalidate every existing join; we
                        # ship both forms so consumers can migrate
                        # at their own pace.
                        "canonical_key_v2": canonical_key_v2,
                        "market_class": market_class,
                        "source_market_key": market_key,
                        "sport": sport,
                        "event_id": event_id,
                        "home_team": event_info.get("home_team"),
                        "away_team": event_info.get("away_team"),
                        "commence_time": event_info.get("commence_time"),
                        "player_name": player_name,
                        "stat_type": stat_type,
                        "market_key": market_key,
                        "line": float(line),
                        "recommendation": side,
                        "is_alternate_market": market_class == "alternate",
                        # SSOT anchor metadata (2026-04-25).
                        "source_anchor":
                            "prizepicks" if is_pp_anchor
                            else "sportsbook_fallback",
                        "anchor_book": bm_key,
                        # Layer slots — populated in Pass 2 for every
                        # book that quoted this exact canonical_key,
                        # including the anchor book itself.
                        "pp_layer": None,
                        "dk_layer": None,
                        "fd_layer": None,
                        "bol_layer": None,
                        "mgm_layer": None,
                        # 2026-05-11 — Caesars (williamhill_us) layer slot.
                        "csr_layer": None,
                        # 2026-05-13 — additional US books (all free
                        # under regions=us, "pull from all books"
                        # expansion). Short codes: eb=ESPN BET,
                        # hrb=Hard Rock, brv=BetRivers, prx=BetParx,
                        # bly=BallyBet, flf=Fliff.
                        "eb_layer": None,
                        "hrb_layer": None,
                        "brv_layer": None,
                        "prx_layer": None,
                        "bly_layer": None,
                        "flf_layer": None,
                        "sharp_layer": None,
                        # 2026-05-17 hardening — split-class odds
                        # containers. The legacy `all_odds` dict
                        # mixes standard + alternate prices at the
                        # same (line, side); the v2 dicts keep them
                        # rigorously separated so scoring can opt
                        # into class-pure pricing. Filled in Pass 2.
                        "all_odds_standard": {},
                        "all_odds_alternate": {},
                        "all_lines_standard": {},
                        "all_lines_alternate": {},
                    }

        # --- Pass 2: attach every allowed book's price to its layer
        # slot on every canonical it matches (including the anchor
        # book that seeded the canonical in Pass 1). Also stamps
        # `*_odds_opp` for the opposite-side row so the multi-book
        # de-vig TP engine can pair both sides of the same book.
        for bm_key, bookmaker in bookmakers_by_key.items():
            if bm_key not in ALLOWED_BOOKS:
                continue
            bm_config = BOOKMAKER_CONFIG.get(bm_key, {})
            is_sharp = bm_config.get("is_sharp", False)

            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_type = stat_type_map.get(market_key, market_key)

                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    if not player_name:
                        continue
                    line = outcome.get("point")
                    if line is None:
                        continue

                    outcome_name = outcome.get("name", "").lower()
                    side = "OVER" if "over" in outcome_name else "UNDER"
                    price = outcome.get("price", -110)

                    canon_key = (
                        f"{sport}|{event_id}|{player_name}|"
                        f"{stat_type}|{float(line)}|{side}"
                    )
                    if canon_key in canonical:
                        # 2026-05-17 hardening — classify this
                        # attach's market_class. We keep the legacy
                        # layer-attach behaviour for backward
                        # compatibility, AND fill split containers
                        # so future scoring passes can isolate by
                        # class.
                        from services.market_class import (
                            classify_market_key as _classify_mc_p2,
                        )
                        attach_class = _classify_mc_p2(market_key)
                        layer = {
                            "book": bm_key,
                            "line": float(line),
                            "odds": price,
                            "fetched_at":
                                datetime.now(timezone.utc).isoformat(),
                            # New: per-attach market_class tag so the
                            # layer itself records which class it
                            # originated from. The canonical's own
                            # `market_class` field reflects the
                            # ANCHOR's class — individual book layers
                            # may differ.
                            "market_class": attach_class,
                            "source_market_key": market_key,
                        }
                        target = canonical[canon_key]
                        if bm_key == "prizepicks":
                            target["pp_layer"] = layer
                        elif bm_key == "draftkings":
                            target["dk_layer"] = layer
                        elif bm_key == "fanduel":
                            target["fd_layer"] = layer
                        elif bm_key == "betonlineag":
                            target["bol_layer"] = layer
                        elif bm_key == "betmgm":
                            target["mgm_layer"] = layer
                        elif bm_key == "williamhill_us":
                            # 2026-05-11 — Caesars layer (post-rebrand).
                            target["csr_layer"] = layer
                        elif bm_key == "espnbet":
                            target["eb_layer"] = layer
                        elif bm_key == "hardrockbet":
                            target["hrb_layer"] = layer
                        elif bm_key == "betrivers":
                            target["brv_layer"] = layer
                        elif bm_key == "betparx":
                            target["prx_layer"] = layer
                        elif bm_key == "ballybet":
                            target["bly_layer"] = layer
                        elif bm_key == "fliff":
                            target["flf_layer"] = layer
                        if is_sharp and target["sharp_layer"] is None:
                            target["sharp_layer"] = layer
                        # 2026-05-17 — split-class odds containers.
                        # Each book contributes ONLY to the bucket
                        # matching its OWN attach_class — so an
                        # alt-market price never lands in
                        # `all_odds_standard` and vice versa.
                        bucket_odds = (
                            target["all_odds_alternate"]
                            if attach_class == "alternate"
                            else target["all_odds_standard"]
                        )
                        bucket_lines = (
                            target["all_lines_alternate"]
                            if attach_class == "alternate"
                            else target["all_lines_standard"]
                        )
                        # Last-write-wins within the same class is
                        # acceptable; cross-class is what we're
                        # eliminating here.
                        bucket_odds[bm_key] = price
                        bucket_lines[bm_key] = float(line)

                    # Opposite-side stamp (preserves prior behaviour).
                    opp_side = "UNDER" if side == "OVER" else "OVER"
                    opp_key = (
                        f"{sport}|{event_id}|{player_name}|"
                        f"{stat_type}|{float(line)}|{opp_side}"
                    )
                    if opp_key in canonical:
                        opp_target = canonical[opp_key]
                        opp_field = {
                            "draftkings":      "dk_odds_opp",
                            "fanduel":         "fd_odds_opp",
                            "betonlineag":     "bol_odds_opp",
                            "betmgm":          "mgm_odds_opp",
                            "williamhill_us":  "csr_odds_opp",
                            "espnbet":         "eb_odds_opp",
                            "hardrockbet":     "hrb_odds_opp",
                            "betrivers":       "brv_odds_opp",
                            "betparx":         "prx_odds_opp",
                            "ballybet":        "bly_odds_opp",
                            "fliff":           "flf_odds_opp",
                        }.get(bm_key)
                        if opp_field:
                            opp_target[opp_field] = price
        
        # --- Pass 3: Flatten canonical records into prop documents ---
        props = []
        for canon_key, rec in canonical.items():
            pp = rec.get("pp_layer")
            dk = rec.get("dk_layer")
            fd = rec.get("fd_layer")
            bol = rec.get("bol_layer")
            mgm = rec.get("mgm_layer")
            # 2026-05-11 — Caesars (williamhill_us) layer.
            csr = rec.get("csr_layer")
            # 2026-05-13 — 6 new US sportsbooks (free credit-wise).
            eb  = rec.get("eb_layer")
            hrb = rec.get("hrb_layer")
            brv = rec.get("brv_layer")
            prx = rec.get("prx_layer")
            bly = rec.get("bly_layer")
            flf = rec.get("flf_layer")
            sharp = rec.get("sharp_layer")

            # SSOT anchor metadata (2026-04-25): when PP didn't quote
            # this exact canonical_key, the prop is anchored on the
            # next-priority sportsbook. UI surfaces filtering for
            # PrizePicks-playable products should gate on
            # `playable_on_pp`; backend keeps every prop in the pool.
            pp_available = pp is not None
            playable_on_pp = pp_available
            source_anchor = rec.get(
                "source_anchor",
                "prizepicks" if pp_available else "sportsbook_fallback",
            )
            anchor_book = rec.get(
                "anchor_book",
                "prizepicks" if pp_available else (
                    "draftkings" if dk else
                    "fanduel" if fd else
                    "betmgm" if mgm else
                    "williamhill_us" if csr else
                    "betonlineag" if bol else None
                ),
            )

            # Derive flat fields from layers (None when layer absent).
            pp_odds = pp["odds"] if pp else None
            dk_odds = dk["odds"] if dk else None
            fd_odds = fd["odds"] if fd else None
            bol_odds = bol["odds"] if bol else None
            mgm_odds = mgm["odds"] if mgm else None
            csr_odds = csr["odds"] if csr else None
            eb_odds  = eb["odds"]  if eb  else None
            hrb_odds = hrb["odds"] if hrb else None
            brv_odds = brv["odds"] if brv else None
            prx_odds = prx["odds"] if prx else None
            bly_odds = bly["odds"] if bly else None
            flf_odds = flf["odds"] if flf else None
            sharp_odds = sharp["odds"] if sharp else None

            # Demon/goblin from DK, then FD, then MGM, then Caesars,
            # then BOL, then nothing. Chain order mirrors the
            # reference-odds priority used by tier routing.
            is_demon = False
            is_goblin = False
            primary_ref_odds = next(
                (x for x in (dk_odds, fd_odds, mgm_odds, csr_odds, bol_odds)
                 if x is not None),
                None,
            )
            if primary_ref_odds is not None:
                is_demon = primary_ref_odds >= 100
                is_goblin = primary_ref_odds < 0

            # Build all_odds/all_lines from exact-match layers only.
            all_odds: Dict[str, Any] = {}
            all_lines: Dict[str, Any] = {}
            books_available: List[str] = []
            if pp:
                all_odds["prizepicks"] = pp_odds
                all_lines["prizepicks"] = pp["line"]
                books_available.append("prizepicks")
            if dk:
                all_odds["draftkings"] = dk_odds
                all_lines["draftkings"] = dk["line"]
                books_available.append("draftkings")
            if fd:
                all_odds["fanduel"] = fd_odds
                all_lines["fanduel"] = fd["line"]
                books_available.append("fanduel")
            if bol:
                all_odds["betonlineag"] = bol_odds
                all_lines["betonlineag"] = bol["line"]
                books_available.append("betonlineag")
            if mgm:
                all_odds["betmgm"] = mgm_odds
                all_lines["betmgm"] = mgm["line"]
                books_available.append("betmgm")
            if csr:
                all_odds["williamhill_us"] = csr_odds
                all_lines["williamhill_us"] = csr["line"]
                books_available.append("williamhill_us")
            # 2026-05-13 — 6 additional US books.
            for _bk_short, _bk_layer, _bk_full, _bk_odds in (
                ("eb",  eb,  "espnbet",     eb_odds),
                ("hrb", hrb, "hardrockbet", hrb_odds),
                ("brv", brv, "betrivers",   brv_odds),
                ("prx", prx, "betparx",     prx_odds),
                ("bly", bly, "ballybet",    bly_odds),
                ("flf", flf, "fliff",       flf_odds),
            ):
                if _bk_layer:
                    all_odds[_bk_full] = _bk_odds
                    all_lines[_bk_full] = _bk_layer["line"]
                    books_available.append(_bk_full)
            if sharp:
                all_odds[sharp["book"]] = sharp_odds
                all_lines[sharp["book"]] = sharp["line"]
                if sharp["book"] not in books_available:
                    books_available.append(sharp["book"])

            # Headline `odds` field — PP price when PP-playable, else
            # the anchor book's price. Preserves prior behaviour for
            # PP-anchored props (`odds == pp_odds`).
            headline_odds = pp_odds if pp_available else (
                dk_odds if dk_odds is not None else
                fd_odds if fd_odds is not None else
                mgm_odds if mgm_odds is not None else
                bol_odds
            )

            prop = {
                "canonical_key": canon_key,
                "player_name": rec["player_name"],
                "stat_type": rec["stat_type"],
                "line": rec["line"],
                "recommendation": rec["recommendation"],
                "odds": headline_odds,
                "market_key": rec["market_key"],
                # `bookmaker` reflects the SSOT anchor book — equal to
                # `prizepicks` for PP-playable props (back-compat),
                # otherwise the highest-priority sportsbook that
                # quoted this canonical_key.
                "bookmaker": anchor_book or "prizepicks",
                "event_id": rec["event_id"],
                "home_team": rec["home_team"],
                "away_team": rec["away_team"],
                "commence_time": rec["commence_time"],
                # Flat layer fields
                "pp_line": pp["line"] if pp else None,
                "pp_odds": pp_odds,
                "dk_line": dk["line"] if dk else None,
                "dk_odds": dk_odds,
                "fd_line": fd["line"] if fd else None,
                "fd_odds": fd_odds,
                "bol_line": bol["line"] if bol else None,
                "bol_odds": bol_odds,
                "mgm_line": mgm["line"] if mgm else None,
                "mgm_odds": mgm_odds,
                # 2026-05-11 — Caesars (williamhill_us) flat fields.
                "csr_line": csr["line"] if csr else None,
                "csr_odds": csr_odds,
                # 2026-05-13 — 6 new US sportsbooks (flat line/odds).
                "eb_line":  eb["line"]  if eb  else None,
                "eb_odds":  eb_odds,
                "hrb_line": hrb["line"] if hrb else None,
                "hrb_odds": hrb_odds,
                "brv_line": brv["line"] if brv else None,
                "brv_odds": brv_odds,
                "prx_line": prx["line"] if prx else None,
                "prx_odds": prx_odds,
                "bly_line": bly["line"] if bly else None,
                "bly_odds": bly_odds,
                "flf_line": flf["line"] if flf else None,
                "flf_odds": flf_odds,
                "sharp_line": sharp["line"] if sharp else None,
                "sharp_odds": sharp_odds,
                "sharp_book": sharp["book"] if sharp else None,
                # Opposite-side prices per book (2026-04-22). Captured
                # during Pass 2 so the multi-book de-vig TP engine can
                # pair both sides of the same book without needing a
                # separate companion row in live_props.
                "dk_odds_opp": rec.get("dk_odds_opp"),
                "fd_odds_opp": rec.get("fd_odds_opp"),
                "bol_odds_opp": rec.get("bol_odds_opp"),
                "mgm_odds_opp": rec.get("mgm_odds_opp"),
                "csr_odds_opp": rec.get("csr_odds_opp"),
                "eb_odds_opp":  rec.get("eb_odds_opp"),
                "hrb_odds_opp": rec.get("hrb_odds_opp"),
                "brv_odds_opp": rec.get("brv_odds_opp"),
                "prx_odds_opp": rec.get("prx_odds_opp"),
                "bly_odds_opp": rec.get("bly_odds_opp"),
                "flf_odds_opp": rec.get("flf_odds_opp"),
                # Structured layers (full objects)
                "pp_layer": pp,
                "dk_layer": dk,
                "fd_layer": fd,
                "bol_layer": bol,
                "mgm_layer": mgm,
                "csr_layer": csr,
                "eb_layer":  eb,
                "hrb_layer": hrb,
                "brv_layer": brv,
                "prx_layer": prx,
                "bly_layer": bly,
                "flf_layer": flf,
                "sharp_layer": sharp,
                # Aggregated (from exact matches only)
                "all_lines": all_lines,
                "all_odds": all_odds,
                "bookmakers_available": books_available,
                # Classification
                "is_goblin": is_goblin,
                "is_demon": is_demon,
                "is_alternate_market": rec.get("is_alternate_market", False),
                # SSOT anchor metadata (2026-04-25)
                "pp_available": pp_available,
                "playable_on_pp": playable_on_pp,
                "source_anchor": source_anchor,
                "anchor_book": anchor_book,
                # Metadata
                "sport": sport,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                # Delta engine (D1, 2026-04-21): datetime (not ISO string)
                # so indexed range queries work via `{$gt: watermark_utc}`.
                # Stamped at flatten-time; every prop in a full-sync batch
                # gets the batch completion timestamp (same cadence is fine
                # since detection runs between full syncs).
                "updated_at": datetime.now(timezone.utc),
                "source": source_anchor,
                "dfs_line": pp["line"] if pp else None,
                "dfs_book": "prizepicks" if pp else None,
                "team": None,
            }
            props.append(prop)

        return props
    
    async def sync_sport_props(
        self, 
        sport: str = "nba",
        bookmakers: List[str] = None,
        include_sharp: bool = True,
        enrich_features: bool = True,
        caller: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Full sync of props for a sport from multiple bookmakers.
        
        1. Fetch all events
        2. Fetch odds for each event from all specified bookmakers
        3. Extract and normalize props with multi-book comparison
        4. Save to sport-specific collection
        
        Args:
            sport: Sport to sync ('nba' or 'mlb')
            bookmakers: List of bookmakers (default: prizepicks, draftkings, fanduel, pinnacle)
            include_sharp: Include sharp books for edge calculation
            enrich_features: When True (default), run the NBA Tier-1/2/3
                context-feature enrichment (`enrich_slate`) after the
                STAGE-THEN-PRUNE write. The hourly APScheduler master_sync
                cron uses the default. The adaptive 5-min STANDBY callback
                passes `enrich_features=False` because enrichment takes
                ~6 min for NBA — running it every 5 min would push live_props
                freshness over the <300s SLO. Enrichment is idempotent and
                its outputs (Tier-1/2/3 context-feature docs) are read by
                downstream scoring, not by the adaptive ingestion path,
                so skipping it in the fast cadence is safe.
            caller: caller-id string used by the budget guard. MUST be
                one of `{"startup", "manual_admin", "scheduled_cron",
                "bootstrap_script"}` — any other value raises
                `FullSyncNotAllowed`. The watcher (Adaptive Sync Engine)
                does NOT have a full-sync entitlement; it uses
                `sync_sport_props_delta` instead.
            
        Returns:
            Sync results summary
        """
        # ── Hard guard: only allow-listed callers may full-sync ────
        from services.odds_api_budget import (
            assert_full_sync_allowed, CallerTag,
        )
        assert_full_sync_allowed(caller)

        # Tag the active caller for the rest of this call so downstream
        # `fetch_events` / `fetch_event_odds` / `discover_event_markets`
        # all record the correct caller in budget logs.
        with CallerTag(caller):
            return await self._sync_sport_props_inner(
                sport=sport, bookmakers=bookmakers,
                include_sharp=include_sharp,
                enrich_features=enrich_features,
            )

    async def _sync_sport_props_inner(
        self,
        sport: str,
        bookmakers: List[str],
        include_sharp: bool,
        enrich_features: bool,
    ) -> Dict[str, Any]:
        sport = validate_sport(sport)
        config = self._get_sport_config(sport)
        display_name = config["display_name"]
        
        # Build bookmaker list - use sport-specific config if available
        if bookmakers is None:
            # Check for sport-specific bookmaker config
            if "bookmakers" in config:
                bookmakers = config["bookmakers"].copy()
                # MLB uses ONLY PrizePicks, no sharp books
                include_sharp = False
            else:
                bookmakers = DEFAULT_BOOKMAKERS.copy()
        
        # Add sharp books if requested (not for MLB)
        if include_sharp:
            for sharp in SHARP_BOOKMAKERS:
                if sharp not in bookmakers:
                    bookmakers.append(sharp)
        
        sync_start = datetime.now(timezone.utc)
        
        logger.info("=" * 70)
        logger.info(f"[UNIVERSAL_ODDS] Starting {display_name} Props Sync")
        logger.info(f"[UNIVERSAL_ODDS] Bookmakers: {bookmakers}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sport": sport,
            "synced_at": sync_start.isoformat(),
            "bookmakers_requested": bookmakers,
            "events_count": 0,
            "total_props": 0,
            "unique_players": set(),
            "stat_types": {},
            "bookmaker_counts": {},
            "props_with_sharp_edge": 0,
            "api_calls": 0,
            "errors": []
        }
        
        try:
            # Reset per-sync credit counter so `results` reports only
            # this sync's spend.
            self.credits_used = {
                "events": 0,
                "market_discovery": 0,
                "event_odds": 0,
            }
            # Reset per-sport market-union memo so this sync always
            # rediscovers markets (catches newly-added markets by books).
            self._sport_market_union.pop(sport, None)

            # Step 1: Fetch events
            events = await self.fetch_events(sport)
            results["events_count"] = len(events)
            results["api_calls"] += 1
            self.credits_used["events"] += 1
            
            if not events:
                logger.warning(f"[UNIVERSAL_ODDS] No {display_name} events found")
                results["success"] = False
                results["errors"].append("No events found")
                return results

            # Step 1b: Discover ALL markets the selected bookmakers
            # currently offer — once per sync — and reuse the union
            # across every event. Replaces the hardcoded market lists
            # (2026-04-21 "pull all markets / all 3 books" request).
            # Build the regions string from our bookmaker config so the
            # discovery call sees every required region.
            regions_set = set()
            for bm in bookmakers:
                bm_cfg = BOOKMAKER_CONFIG.get(bm)
                if bm_cfg:
                    regions_set.add(bm_cfg["region"])
            regions_str_for_discovery = ",".join(sorted(regions_set))

            sync_markets = await self._resolve_markets_for_sport(
                sport=sport,
                sport_key=config["sport_key"],
                bookmakers=bookmakers,
                regions_str=regions_str_for_discovery,
                sample_events=events,
            )
            results["markets_discovered"] = sync_markets

            # Step 2: Fetch odds for each event (with rate limiting)
            all_props = []
            
            for event in events:
                event_id = event.get("id")
                if not event_id:
                    continue
                
                # Fetch odds from all bookmakers using the discovered
                # market union so every book's complete prop menu
                # surfaces in the response.
                odds_data = await self.fetch_event_odds(
                    event_id, event, sport, bookmakers,
                    markets_override=sync_markets,
                )
                results["api_calls"] += 1
                self.credits_used["event_odds"] += 1
                
                if odds_data:
                    # --- Persist raw odds for ALL markets (2026-04-24) ---
                    # When NBA_PULL_ALL_MARKETS=true we also pull game-level,
                    # team-totals, period/quarter/half, and novelty markets.
                    # None of those flow through scoring (which expects
                    # player_* mapped stat types), but we still want a
                    # durable record so future mapping work has a source.
                    # Writes happen once per event into
                    # ``dg_raw_odds_markets`` (flat one row per outcome).
                    try:
                        raw_stats = await self._persist_raw_markets(
                            odds_data, event, sport,
                        )
                        results["raw_markets_written"] = (
                            results.get("raw_markets_written", 0)
                            + raw_stats.get("written", 0)
                        )
                        results["mapped_markets_seen"] = (
                            results.get("mapped_markets_seen", 0)
                            + raw_stats.get("mapped", 0)
                        )
                        results["unmapped_markets_seen"] = (
                            results.get("unmapped_markets_seen", 0)
                            + raw_stats.get("unmapped", 0)
                        )
                        for mk in raw_stats.get("unmapped_keys", []):
                            results.setdefault("unmapped_market_keys", set()).add(mk)
                    except Exception as _pe:
                        logger.warning(f"[UNIVERSAL_ODDS] raw-markets persist failed: {_pe}")

                    # Extract props
                    props = self.extract_props_from_odds(odds_data, event, sport)
                    all_props.extend(props)
                    
                    # Track stats
                    for prop in props:
                        results["unique_players"].add(prop["player_name"])
                        stat_type = prop["stat_type"]
                        results["stat_types"][stat_type] = results["stat_types"].get(stat_type, 0) + 1
                        
                        # Track bookmaker availability
                        for bm in prop.get("bookmakers_available", []):
                            results["bookmaker_counts"][bm] = results["bookmaker_counts"].get(bm, 0) + 1
                        
                        # Track props with sharp edge
                        if prop.get("sharp_edge") is not None:
                            results["props_with_sharp_edge"] += 1
                
                # Rate limiting - The Odds API has limits
                await asyncio.sleep(0.1)
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(results["unique_players"])
            # JSON-safe: convert unmapped_market_keys set -> sorted list.
            if isinstance(results.get("unmapped_market_keys"), set):
                results["unmapped_market_keys"] = sorted(
                    results["unmapped_market_keys"])
            results["credits_used"] = dict(self.credits_used)
            
            # Step 3: Save to sport-specific collection
            # CRITICAL: Drop-and-replace to purge stale props from past events.
            # The old upsert-by-event_id approach left zombie records because
            # event_id changes every game day, so old records were never matched.
            if all_props:
                collection_name = get_collection_name("live_props", sport)
                # Wave 1 shadow-writes: COLL.handle returns a ShadowWriter
                # for (concept, sport) pairs registered in _SHADOW_WRITES
                # (currently: live_props·NBA → nba_live_props). For pairs
                # not registered it returns the raw Motor collection, so
                # MLB behavior is unchanged.
                collection = COLL.handle(self.db, "live_props", sport)
                
                # Phase 3 (2026-04-28) — STAGE-THEN-PRUNE write strategy.
                # ─────────────────────────────────────────────────────────
                # Old code did `delete_many({})` BEFORE `insert_many`, which
                # left the board EMPTY for seconds-to-minutes during every
                # sync. The board powers downstream readers (ferrari tiers,
                # odds props routes, MLB engine, cached-board builder) so
                # an empty window propagated to every consumer.
                #
                # New strategy:
                #   1. Stamp every new prop with a fresh `sync_batch_id`
                #      and `synced_at` timestamp.
                #   2. Bulk-insert the new batch FIRST. The collection now
                #      contains BOTH the previous batch and the new batch.
                #      Readers see at least one full slate at all times.
                #   3. Hydrate the new batch in place (existing path).
                #   4. After insert+hydrate succeed, atomically delete
                #      everything whose `sync_batch_id` is NOT the new id.
                #      Brief overlap window (~ms) replaces the previous
                #      multi-second empty window.
                #
                # If insert fails BEFORE the prune step, the old batch is
                # untouched and the board stays live.  This is the spec's
                # core invariant: "old board stays visible during failed sync."
                pre_keys: set = set()
                try:
                    from services.board.delta_publisher import capture_live_props_keys
                    pre_keys = await capture_live_props_keys(self.db, sport)
                except Exception as _e:
                    logger.warning(
                        f"[DELTA_PUB] {sport} pre-snapshot skipped: {_e}"
                    )

                stale_count = await collection.count_documents({})
                new_batch_id = uuid.uuid4().hex
                synced_at = datetime.now(timezone.utc)

                # Strip _id and stamp batch metadata before insert.
                clean_props = []
                for p in all_props:
                    d = {k: v for k, v in p.items() if k != "_id"}
                    d["sync_batch_id"] = new_batch_id
                    d["synced_at"] = synced_at
                    d["active"] = True   # active by default; readers
                                         # untouched, prune handles overlap
                    d["stale"] = False
                    clean_props.append(d)

                # Identity stamping (existing path, unchanged).
                identity_resolved, identity_missing = (
                    await _stamp_identity_on_props(self.db, sport, clean_props)
                )
                results["identity_resolved"] = identity_resolved
                results["identity_missing"] = identity_missing

                # Game-context hydration (existing path, unchanged).
                if sport in ("nba", "mlb"):
                    try:
                        from services.feature_hydration import (
                            hydrate_game_context_on_props,
                        )
                        hydration_report = await hydrate_game_context_on_props(
                            self.db, sport, clean_props,
                        )
                        results["context_hydration"] = hydration_report
                    except Exception as _ctx_err:
                        logger.warning(
                            f"[CTX_HYDRATE:{sport}] hydration skipped: "
                            f"{_ctx_err}"
                        )

                # Step A — bulk-insert the new batch (board now contains
                # both old + new; readers see a non-empty collection).
                if not clean_props:
                    logger.warning(
                        f"[ODDS_SYNC:{sport}] new batch is empty; "
                        "skipping prune to keep previous board live"
                    )
                    results["sync_batch_id"] = new_batch_id
                    results["new_batch_count"] = 0
                    results["pruned_count"] = 0
                    results["stale_count_before"] = stale_count
                else:
                    await collection.insert_many(clean_props)

                    # Step B — prune previous batches AFTER new batch is
                    # fully written.  A subsequent crash/retry leaves the
                    # NEW batch active and old rows pruned next cycle.
                    prune_res = await collection.delete_many(
                        {"sync_batch_id": {"$ne": new_batch_id}}
                    )
                    results["sync_batch_id"] = new_batch_id
                    results["new_batch_count"] = len(clean_props)
                    results["pruned_count"] = prune_res.deleted_count
                    results["stale_count_before"] = stale_count
                    logger.info(
                        f"[ODDS_SYNC:{sport}] STAGE-THEN-PRUNE: "
                        f"new_batch={len(clean_props)} pruned={prune_res.deleted_count} "
                        f"batch_id={new_batch_id[:8]}"
                    )

                    # 2026-05-07 dirty-queue ingestion hook (Step 3).
                    # Replaces the watermark-based detector. Every prop
                    # we just wrote is enqueued for the delta engine
                    # to rescore. Idempotent — failures here are
                    # logged but never block the sync.
                    try:
                        from services.delta.dirty_queue import enqueue_dirty
                        keys = [
                            p["canonical_key"]
                            for p in clean_props
                            if p.get("canonical_key")
                        ]
                        n_enq = await enqueue_dirty(
                            self.db, keys,
                            sport=sport,
                            reason="ingestion",
                            ingestion_batch=new_batch_id,
                        )
                        logger.info(
                            f"[ODDS_SYNC:{sport}] dirty_queue: "
                            f"enqueued {n_enq} canonical_keys "
                            f"(batch={new_batch_id[:8]})"
                        )
                        results["dirty_queue_enqueued"] = n_enq
                    except Exception as _dq_err:
                        logger.warning(
                            f"[ODDS_SYNC:{sport}] dirty_queue enqueue "
                            f"failed (non-fatal): {_dq_err}"
                        )

                # 2026-05 NBA feature-engine (Tier-1/2/3 context features
                # for upcoming VK retrain). Runs as an OPTIONAL,
                # non-breaking step after props are persisted. Failures
                # here never block the sync.
                #
                # 2026-05-07 P0-A callback split: the adaptive 5-min
                # STANDBY callback passes `enrich_features=False` so
                # the ~6-min enrich_slate work doesn't block the live
                # SLO. The hourly APScheduler master_sync still runs
                # this on its full cadence.
                if sport == "nba" and enrich_features:
                    try:
                        from services.features.nba_feature_engine import (
                            enrich_slate,
                        )
                        feat_report = await enrich_slate(self.db, sport)
                        results["nba_context_features"] = feat_report
                    except Exception as _fe_err:
                        logger.warning(
                            f"[NBA_FEATURES] enrich_slate skipped: {_fe_err}"
                        )
                
                inserted = len(clean_props)
                results["inserted"] = inserted
                results["updated"] = 0
                results["purged_stale"] = stale_count
                results["collection"] = collection_name
                
                logger.info(f"[UNIVERSAL_ODDS] Replaced {collection_name}: purged {stale_count} stale, inserted {inserted} fresh")

                # Phase 6 Step 5 — post-insert snapshot + delta publish
                try:
                    from services.board.delta_publisher import (
                        capture_live_props_keys, publish_new_props_delta,
                    )
                    post_keys = await capture_live_props_keys(self.db, sport)
                    emit_summary = await publish_new_props_delta(
                        sport=sport,
                        pre_keys=pre_keys,
                        post_keys=post_keys,
                        source="universal_odds_sync",
                    )
                    results["new_props_delta"] = emit_summary
                except Exception as _e:
                    logger.warning(
                        f"[DELTA_PUB] {sport} delta emit skipped: {_e}"
                    )
            
            # Log summary
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[UNIVERSAL_ODDS] {display_name} Sync Complete:")
            logger.info(f"  • Events: {results['events_count']}")
            logger.info(f"  • Props: {results['total_props']}")
            logger.info(f"  • Players: {results['unique_players']}")
            logger.info(f"  • API Calls: {results['api_calls']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            logger.info(f"  • Stat Types: {results['stat_types']}")
            
            results["duration_seconds"] = round(duration, 2)
            
        except Exception as e:
            logger.error(f"[UNIVERSAL_ODDS] Sync error for {display_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results

    # ============================================================
    # 2026-06-01 — Delta sync (watcher-safe).
    # ============================================================
    async def sync_sport_props_delta(
        self,
        sport: str,
        *,
        caller: str,
        ttl_seconds: int = 600,
        max_events_per_tick: int = 3,
        bookmakers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Watcher-safe delta sync.

        Behavior:
          1. Hits `/events` once for the sport (cheap — single call,
             returns the upcoming-game list).
          2. Reads per-event `last_synced_at` from the
             `odds_delta_state` collection.
          3. Filters to events whose `last_synced_at` is older than
             ``ttl_seconds`` (default 600s) or absent. Caps the result
             at ``max_events_per_tick`` (default 3) — overflow waits
             for the next tick.
          4. Calls `fetch_event_odds` only for the selected events,
             writes them through the existing extract+upsert path,
             updates `odds_delta_state` with the new timestamp.

        Returns a summary dict for logging.
        Does NOT call `sync_sport_props`, ever.
        """
        from services.odds_api_budget import CallerTag
        from services.config.collection_names import COLL
        from pymongo import UpdateOne

        sport = validate_sport(sport)
        config = self._get_sport_config(sport)
        if bookmakers is None:
            bookmakers = list(config.get("bookmakers", DEFAULT_BOOKMAKERS))
        now = datetime.now(timezone.utc)

        result: Dict[str, Any] = {
            "sync_mode":      "delta",
            "sport":          sport,
            "caller":         caller,
            "trigger_reason": "watcher_tick",
            "events_total":   0,
            "events_stale":   0,
            "events_fetched": 0,
            "api_calls_made": 0,
            "records_updated": 0,
            "ttl_seconds":    ttl_seconds,
            "max_events_per_tick": max_events_per_tick,
            "scope":          {"event_ids": []},
            "errors":         [],
        }

        with CallerTag(caller):
            try:
                events = await self.fetch_events(sport)
                result["api_calls_made"] += 1
                result["events_total"] = len(events)

                if not events:
                    logger.info(
                        f"[ODDS_DELTA] sport={sport} caller={caller} "
                        f"events=0 — nothing to refresh"
                    )
                    return result

                event_ids = [e.get("id") for e in events if e.get("id")]
                state_cur = self.db["odds_delta_state"].find(
                    {"sport": sport, "event_id": {"$in": event_ids}})
                last_seen: Dict[str, datetime] = {}
                async for s in state_cur:
                    ts = s.get("last_synced_at")
                    eid = s.get("event_id")
                    if eid and isinstance(ts, datetime):
                        last_seen[eid] = ts

                cutoff = now.timestamp() - ttl_seconds
                stale_events = []
                for ev in events:
                    eid = ev.get("id")
                    if not eid:
                        continue
                    ts = last_seen.get(eid)
                    if ts is None or ts.timestamp() < cutoff:
                        stale_events.append(ev)
                result["events_stale"] = len(stale_events)

                selected = stale_events[:max_events_per_tick]
                result["events_fetched"] = len(selected)
                result["scope"]["event_ids"] = [
                    (e.get("id") or "")[:12] for e in selected]

                if not selected:
                    logger.info(
                        f"[ODDS_DELTA] sport={sport} caller={caller} "
                        f"events_total={result['events_total']} "
                        f"events_stale=0 — nothing to refresh "
                        f"(all fresh within {ttl_seconds}s)"
                    )
                    return result

                markets_override = self._sport_market_union.get(sport)
                upserts: List[UpdateOne] = []
                for ev in selected:
                    eid = ev.get("id")
                    odds = await self.fetch_event_odds(
                        eid, ev, sport, bookmakers,
                        markets_override=markets_override,
                    )
                    result["api_calls_made"] += 1
                    if not odds:
                        continue
                    props = self.extract_props_from_odds(odds, ev, sport)
                    for prop in props:
                        ck = prop.get("canonical_key") or prop.get("_id")
                        if not ck:
                            continue
                        # The extracted prop dict already carries
                        # `updated_at`. Strip it so $currentDate owns
                        # the field and avoid the conflict-on-path
                        # bulk-write error.
                        prop.pop("updated_at", None)
                        upserts.append(UpdateOne(
                            {"canonical_key": ck},
                            {"$set": prop,
                              "$currentDate": {"updated_at": True}},
                            upsert=True,
                        ))
                    await self.db["odds_delta_state"].update_one(
                        {"sport": sport, "event_id": eid},
                        {"$set": {
                            "sport": sport, "event_id": eid,
                            "last_synced_at": now,
                            "commence_time": ev.get("commence_time"),
                            "home_team": ev.get("home_team"),
                            "away_team": ev.get("away_team"),
                        }},
                        upsert=True,
                    )

                if upserts:
                    coll = COLL.handle(self.db, "live_props", sport)
                    try:
                        wres = await coll.bulk_write(upserts, ordered=False)
                        result["records_updated"] = (
                            (wres.upserted_count or 0)
                            + (wres.modified_count or 0))
                    except Exception as we:  # noqa: BLE001
                        result["errors"].append(f"bulk_write: {we!r}")
                        logger.error(
                            f"[ODDS_DELTA] bulk_write failed: {we!r}")

                logger.info(
                    f"[ODDS_DELTA] sync_mode=delta sport={sport} "
                    f"caller={caller} trigger=watcher_tick "
                    f"events_total={result['events_total']} "
                    f"events_stale={result['events_stale']} "
                    f"events_fetched={result['events_fetched']} "
                    f"api_calls={result['api_calls_made']} "
                    f"records_updated={result['records_updated']} "
                    f"hour_count={hourly_count_safe()}"
                )
                return result
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    f"[ODDS_DELTA] sport={sport} caller={caller} FAILED")
                result["errors"].append(repr(exc))
                return result




# Singleton instance
_universal_odds_service: Optional[UniversalOddsSyncService] = None


def get_universal_odds_service(db: AsyncIOMotorDatabase) -> UniversalOddsSyncService:
    """Get or create the universal odds sync service."""
    global _universal_odds_service
    if _universal_odds_service is None:
        _universal_odds_service = UniversalOddsSyncService(db)
    return _universal_odds_service
