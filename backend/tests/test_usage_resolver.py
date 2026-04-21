"""
Tests for services/usage_resolver.py — canonical multi-sport usage score
contract. Also asserts the Injury-Rank Phase 2 ordering rule: teammates
are ranked by usage-driven score, never by iteration / loop order.
"""
from __future__ import annotations

import pytest

from services import usage_resolver as ur


# ---------- Unit: blend function --------------------------------------------
def test_blend_none_when_no_inputs():
    assert ur._nba_blend(None, None) is None


def test_blend_pure_usage_when_no_minutes():
    # u=30, m=None -> 30 * 0.70 = 21.0
    assert ur._nba_blend(30.0, None) == 21.0


def test_blend_full_starter_minutes_restores_full_usage():
    # u=25, m=36 -> 25 * (0.70 + 0.30*1.0) = 25.0
    assert ur._nba_blend(25.0, 36.0) == 25.0


def test_blend_bench_minutes_downweights():
    # u=30, m=14 -> 30 * (0.70 + 0.30 * 14/36) = 30 * 0.8167
    got = ur._nba_blend(30.0, 14.0)
    assert got < 30.0
    assert abs(got - 30.0 * (0.70 + 0.30 * (14/36))) < 1e-3


def test_blend_caps_minutes_at_36():
    # 40 minutes should equal 36 minutes (capped)
    assert ur._nba_blend(25.0, 40.0) == ur._nba_blend(25.0, 36.0)


# ---------- Provider registry -----------------------------------------------
@pytest.mark.asyncio
async def test_unknown_sport_returns_unavailable():
    score, source = await ur.get_player_usage_score(None, "nfl", "Patrick Mahomes")
    assert score is None and source == "unavailable"


@pytest.mark.asyncio
async def test_missing_args_return_unavailable():
    for sport, player in [(None, "p"), ("nba", None), ("", ""), ("nba", " ")]:
        score, source = await ur.get_player_usage_score(None, sport, player)
        assert score is None and source == "unavailable"


@pytest.mark.asyncio
async def test_mlb_provider_is_unavailable_today():
    score, source = await ur.get_player_usage_score(None, "mlb", "Aaron Judge")
    assert score is None and source == "unavailable"


# ---------- Ranking semantics -----------------------------------------------
@pytest.mark.asyncio
async def test_rank_teammates_sorts_by_usage_desc_with_alpha_tiebreak():
    """Registers a fake NBA provider with deterministic scores so we can
    assert ordering without touching Mongo."""
    mock_scores = {
        "primary star":   (28.0, "nba_hub"),
        "role starter":   (18.0, "nba_hub"),
        "bench spark":    (15.0, "nba_hub"),
        "unknown":        (None, "unavailable"),
    }

    async def fake_provider(db, player_name):
        return mock_scores.get((player_name or "").lower(), (None, "unavailable"))

    ur.register_provider("testsport", fake_provider)
    try:
        teammates = [
            {"player_name": "Unknown"},       # no usage → should sink to bottom
            {"player_name": "Bench Spark"},
            {"player_name": "Primary Star"},
            {"player_name": "Role Starter"},
        ]
        ranked = await ur.rank_teammates_by_usage(None, "testsport", teammates)
        names = [t["player_name"] for t in ranked]
        assert names == ["Primary Star", "Role Starter", "Bench Spark", "Unknown"]
        # Ranks are 1-based, deterministic
        assert [t["usage_rank"] for t in ranked] == [1, 2, 3, 4]
        # Provenance preserved
        assert ranked[0]["usage_source"] == "nba_hub"
        assert ranked[-1]["usage_source"] == "unavailable"
    finally:
        # Restore fake provider to unavailable so other tests stay clean.
        async def gone(db, p): return (None, "unavailable")
        ur.register_provider("testsport", gone)


@pytest.mark.asyncio
async def test_rank_is_not_loop_order():
    """Critical regression: feeding teammates in REVERSE usage order must
    still produce the same usage-based ranking."""
    mock_scores = {
        "a": (10.0, "nba_hub"),
        "b": (25.0, "nba_hub"),
        "c": (18.0, "nba_hub"),
    }

    async def fp(db, p):
        return mock_scores.get((p or "").lower(), (None, "unavailable"))

    ur.register_provider("loop_test", fp)
    try:
        # Forward order
        forward = [{"player_name": "A"}, {"player_name": "B"}, {"player_name": "C"}]
        # Reverse order
        reverse = [{"player_name": "C"}, {"player_name": "B"}, {"player_name": "A"}]
        r1 = await ur.rank_teammates_by_usage(None, "loop_test", forward)
        r2 = await ur.rank_teammates_by_usage(None, "loop_test", reverse)
        assert [t["player_name"] for t in r1] == ["B", "C", "A"]
        assert [t["player_name"] for t in r2] == ["B", "C", "A"]
    finally:
        async def gone(db, p): return (None, "unavailable")
        ur.register_provider("loop_test", gone)


@pytest.mark.asyncio
async def test_deterministic_tiebreak_on_equal_usage():
    """Players with identical usage scores tiebreak alphabetically — so
    reruns always produce the same output. No loop-order dependence."""
    async def fp(db, p):
        return (20.0, "nba_hub")   # every player -> same score

    ur.register_provider("tie_test", fp)
    try:
        teammates = [
            {"player_name": "Zephyr"},
            {"player_name": "Alpha"},
            {"player_name": "Mid"},
        ]
        ranked = await ur.rank_teammates_by_usage(None, "tie_test", teammates)
        assert [t["player_name"] for t in ranked] == ["Alpha", "Mid", "Zephyr"]
    finally:
        async def gone(db, p): return (None, "unavailable")
        ur.register_provider("tie_test", gone)


@pytest.mark.asyncio
async def test_empty_and_malformed_lists():
    assert await ur.rank_teammates_by_usage(None, "nba", []) == []
    out = await ur.rank_teammates_by_usage(None, "nba", None)
    assert out is None  # passthrough


@pytest.mark.asyncio
async def test_nfl_future_sport_registration():
    """Registering future NFL provider requires NO caller changes."""
    async def fake_nfl(db, p):
        if p == "Travis Kelce":
            return (22.5, "nfl_rbs")
        return (None, "unavailable")

    ur.register_provider("nfl", fake_nfl)
    try:
        score, source = await ur.get_player_usage_score(None, "nfl", "Travis Kelce")
        assert score == 22.5 and source == "nfl_rbs"
    finally:
        async def gone(db, p): return (None, "unavailable")
        ur.register_provider("nfl", gone)
