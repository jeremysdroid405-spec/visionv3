"""Test the /api/v3/team-with-badges/{team_id} endpoint contract.

Locks in the player-shaped JSON contract `TeamDetailPage` →
`PlayerDetailPage` clone depends on. If any field shifts shape, the
detail page will silently render broken cells — these tests catch the
drift at CI time.
"""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _get_db():
    url = os.environ["MONGO_URL"]
    name = os.environ["DB_NAME"]
    return AsyncIOMotorClient(url)[name]


# ---- Unit-style tests for the deterministic builders ------------------------
def test_build_vision_intel_returns_one_sentence_per_signal():
    from routes.team_with_badges import _build_vision_intel
    text = _build_vision_intel(
        "TEAM_TOTAL", "OVER", 110.5,
        hit_l5=80.0, hit_l10=70.0, hit_l20=65.0, season_hr=58.0,
        opp_abbr="LAL", opp_hit_pct=75.0, opp_sample=4,
    )
    assert text is not None
    assert "Hit OVER 110.5 in 7 of last 10" in text
    assert "trending over" in text.lower()
    assert "vs LAL: 3/4 hits" in text


def test_build_vision_intel_returns_none_when_no_history():
    from routes.team_with_badges import _build_vision_intel
    out = _build_vision_intel(
        "TEAM_TOTAL", "OVER", 110.5,
        hit_l5=None, hit_l10=None, hit_l20=None, season_hr=None,
        opp_abbr=None, opp_hit_pct=None, opp_sample=0,
    )
    assert out is None


def test_build_scout_badges_hot_streak_above_threshold():
    from routes.team_with_badges import _build_scout_badges
    b = _build_scout_badges(hit_l5=80.0, hit_l10=70.0, hit_l20=65.0)
    keys = [x["badge_key"] for x in b]
    assert "hot_streak" in keys


def test_build_scout_badges_floor_lock_above_threshold():
    from routes.team_with_badges import _build_scout_badges
    b = _build_scout_badges(hit_l5=60.0, hit_l10=60.0, hit_l20=80.0)
    keys = [x["badge_key"] for x in b]
    assert "floor_lock" in keys


def test_build_scout_badges_empty_when_below_thresholds():
    from routes.team_with_badges import _build_scout_badges
    b = _build_scout_badges(hit_l5=50.0, hit_l10=55.0, hit_l20=60.0)
    assert b == []


def test_split_team_id_strips_sport_prefix():
    from routes.team_with_badges import _split_team_id
    assert _split_team_id("nba_bos", "nba") == ("bos", "BOS")
    assert _split_team_id("NBA_BOS", "nba") == ("bos", "BOS")
    assert _split_team_id("bos", "nba") == ("bos", "BOS")


def test_team_display_name_resolves_known_abbr():
    from routes.team_with_badges import _team_display_name
    assert _team_display_name("nba_bos", "nba") == "Boston Celtics"
    assert _team_display_name("mlb_cle", "mlb") == "Cleveland Guardians"
    # Unknown abbr falls back to uppercase token.
    assert _team_display_name("nba_xyz", "nba") == "XYZ"


# ---- Integration tests against the real DB --------------------------------
@pytest.mark.asyncio
async def test_endpoint_returns_player_shaped_payload():
    """Smoke test against real team_historical_outcomes rows. Verifies
    the response carries every field PlayerDetailPage reads."""
    from routes.team_with_badges import (
        get_team_with_badges, set_team_with_badges_db,
    )
    set_team_with_badges_db(_get_db())

    # NBA bos — 413k historical rows, 0 live props expected today.
    out = await get_team_with_badges("nba_bos", sport="nba")
    assert out["success"] is True
    p = out["player"]
    # Identity slot — must match PlayerDetailPage field reads.
    assert p["player_name"] == "Boston Celtics"
    assert p["team"] == "BOS"
    assert p["sport"] == "nba"
    assert p["is_team_prop"] is True
    assert isinstance(p["props"], list)
    # baseline_stats keys consumed by PlayerDetailPage header strip.
    assert "PTS" in p["baseline_stats"]
    pts = p["baseline_stats"]["PTS"]
    assert "season_avg" in pts
    assert "l5_avg" in pts
    assert "l10_avg" in pts
    assert "l20_avg" in pts


@pytest.mark.asyncio
async def test_endpoint_rejects_unsupported_sport():
    from fastapi import HTTPException
    from routes.team_with_badges import (
        get_team_with_badges, set_team_with_badges_db,
    )
    set_team_with_badges_db(_get_db())
    with pytest.raises(HTTPException) as exc:
        await get_team_with_badges("nba_bos", sport="ncaaf")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_endpoint_rejects_empty_team_id():
    from fastapi import HTTPException
    from routes.team_with_badges import (
        get_team_with_badges, set_team_with_badges_db,
    )
    set_team_with_badges_db(_get_db())
    with pytest.raises(HTTPException) as exc:
        await get_team_with_badges("", sport="nba")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_props_carry_player_shaped_fields():
    """When live props exist (MLB has data), every prop must carry the
    canonical fields PropRow consumes."""
    from routes.team_with_badges import (
        get_team_with_badges, set_team_with_badges_db,
    )
    set_team_with_badges_db(_get_db())
    out = await get_team_with_badges("mlb_cle", sport="mlb")
    props = out["player"]["props"]
    if not props:
        pytest.skip("No live MLB team props for mlb_cle right now")
    REQUIRED = {
        "stat_type", "stat_type_extracted", "market", "line", "direction",
        "hit_rate_l5", "hit_rate_l10", "hit_rate_l20",
        "l5_avg", "l10_avg", "l20_avg", "season_avg",
        "vk_predicted", "edge_vs_fair",
        "best_book", "best_book_odds",
        "vision_intel", "scout_badges", "intel_suite",
        "game_logs", "is_team_prop", "prop_type",
    }
    for p in props:
        missing = REQUIRED - set(p.keys())
        assert not missing, f"prop missing fields: {missing}"
        assert p["prop_type"] == "team"
        assert p["is_team_prop"] is True
