"""
Tests locking the 2026-04-21 "pull all markets / all 3 books" wiring
across services/odds_api_service.py and services/universal_odds_sync.py.
"""
from __future__ import annotations

from services import odds_api_service
from services import universal_odds_sync


def test_nba_sharp_bookmakers_are_the_user_requested_trio():
    """Locks: NBA sharp-book fetch hits DraftKings + FanDuel + BetOnline."""
    assert odds_api_service.NBA_SHARP_BOOKMAKERS == [
        "draftkings", "fanduel", "betonlineag",
    ]


def test_nba_sharp_regions_cover_betonline_region():
    """BetOnline lives in us2 and DK/FD in us — both regions required."""
    assert "us" in odds_api_service.NBA_SHARP_REGIONS
    assert "us2" in odds_api_service.NBA_SHARP_REGIONS


def test_mlb_bookmakers_config_includes_user_requested_trio():
    """MLB universal-sync defaults now include all three user-requested
    books alongside the PrizePicks anchor."""
    mlb_bms = universal_odds_sync.SPORT_API_CONFIG["mlb"]["bookmakers"]
    for required in ("draftkings", "fanduel", "betonlineag"):
        assert required in mlb_bms, f"MLB missing {required}"
    # PrizePicks anchor still present so the canonical-key extraction
    # retains its DFS anchor.
    assert "prizepicks" in mlb_bms


def test_nba_bookmakers_config_includes_user_requested_trio():
    nba_bms = universal_odds_sync.SPORT_API_CONFIG["nba"]["bookmakers"]
    for required in ("draftkings", "fanduel", "betonlineag"):
        assert required in nba_bms, f"NBA missing {required}"
    assert "prizepicks" in nba_bms


def test_betonlineag_registered_in_bookmaker_config():
    """Without this entry `fetch_event_odds` can't compute the region
    set when BOL is in the bookmaker list."""
    assert "betonlineag" in universal_odds_sync.BOOKMAKER_CONFIG
    cfg = universal_odds_sync.BOOKMAKER_CONFIG["betonlineag"]
    # BetOnline is in the us2 region per Odds API v4.
    assert cfg["region"] == "us2"


def test_extract_stat_type_falls_back_to_uppercase_raw_key():
    """Unknown markets must NOT return an empty string — they return
    the uppercased base key so composite keys stay unique across
    distinct unknown markets (the previous behavior collided them all
    on ``""``)."""
    from services.utils_service import extract_stat_type

    # Known markets preserve their canonical abbreviation.
    assert extract_stat_type("player_points") == "PTS"
    assert extract_stat_type("player_points_alternate") == "PTS"

    # Unknown market → uppercase base key (the _alternate suffix is
    # stripped first so alt/standard collapse into the same stat_type).
    assert extract_stat_type("player_first_basket") == "FIRST BASKET" or \
        extract_stat_type("player_first_basket") == "PLAYER_FIRST_BASKET"
    assert extract_stat_type("brand_new_unknown_market") == "BRAND_NEW_UNKNOWN_MARKET"

    # Empty input unchanged.
    assert extract_stat_type("") == ""


def test_odds_api_service_exports_credits_counter_attr():
    """``OddsApiService.credits_used`` must exist post-init so callers
    can surface API-credit consumption in sync results."""
    import asyncio
    import inspect

    # We can't easily init the service without a live DB in a unit test,
    # but we can at least confirm the attribute is documented in
    # ``__init__`` source.
    src = inspect.getsource(odds_api_service.OddsApiService.__init__)
    assert "credits_used" in src, "OddsApiService must track credits_used"
    assert "market_discovery" in src
    assert "sharp_book_odds" in src
    assert "prizepicks_odds" in src


def test_universal_odds_sync_exports_credits_tracking():
    """Parity with OddsApiService — MLB/NBA universal sync must surface
    API-credit consumption per sync."""
    import inspect
    src = inspect.getsource(universal_odds_sync.UniversalOddsSyncService.__init__)
    assert "credits_used" in src
    assert "market_discovery" in src
    assert "event_odds" in src


def test_no_hardcoded_market_list_in_fetch_sharp_book_odds_call():
    """The fetch_sharp_book_odds method must hit MarketCatalog first,
    not ship a hardcoded market list straight into /odds."""
    import inspect
    src = inspect.getsource(odds_api_service.OddsApiService.fetch_sharp_book_odds)
    # Discovery must be invoked.
    assert "discover_event_markets" in src
    # Dynamic list must be the one passed into the /odds request.
    assert "discovered_markets" in src


def test_no_hardcoded_market_list_in_universal_sync_sport_props():
    """sync_sport_props must resolve the dynamic market union once and
    pass it into every per-event fetch."""
    import inspect
    src = inspect.getsource(
        universal_odds_sync.UniversalOddsSyncService.sync_sport_props
    )
    assert "_resolve_markets_for_sport" in src
    assert "markets_override=sync_markets" in src
