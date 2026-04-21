"""
Tests for services/defensive_rank_resolver.py — the canonical multi-sport
DvP rank contract. No static fallbacks, no sport-specific hacks, stable
(rank, source) tuple across NBA / MLB / future NFL.
"""
from __future__ import annotations

import pytest

from services import defensive_rank_resolver as dr


# ---------- Unknown sport / missing args -----------------------------------
def test_unknown_sport_returns_unavailable():
    assert dr.get_opponent_defensive_rank("nfl", "KC", "PTS") == (None, "unavailable")


def test_missing_args_return_unavailable():
    for args in [
        (None, "SAS", "PTS"),
        ("nba", None, "PTS"),
        ("nba", "SAS", None),
        ("", "", ""),
    ]:
        assert dr.get_opponent_defensive_rank(*args) == (None, "unavailable")


# ---------- MLB provider ---------------------------------------------------
def test_mlb_always_unavailable():
    # Current MLB pipeline doesn't use team-defense rank by stat type.
    assert dr.get_opponent_defensive_rank("mlb", "NYY", "H+R+RBI") == (None, "unavailable")


# ---------- NBA provider (strict: never static) ----------------------------
def test_nba_returns_unavailable_when_cache_empty(monkeypatch):
    import services.dvp_service as dvp
    monkeypatch.setattr(dvp, "_dvp_cache", None)
    assert dr.get_opponent_defensive_rank("nba", "SAS", "PTS") == (None, "unavailable")


def test_nba_returns_unavailable_when_cache_static(monkeypatch):
    import services.dvp_service as dvp
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(
        dvp, "_dvp_cache",
        dvp.DvPCacheEntry(
            rankings={"PTS": {"SAS": 24}},
            source=dvp.DvPDataSource.STATIC_FALLBACK,   # <- must be rejected
            fetched_at=datetime.now(timezone.utc),
            season="2024-25",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    rank, source = dr.get_opponent_defensive_rank("nba", "SAS", "PTS")
    assert rank is None
    assert source == "unavailable"


def test_nba_returns_live_rank_when_cache_is_live(monkeypatch):
    import services.dvp_service as dvp
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(
        dvp, "_dvp_cache",
        dvp.DvPCacheEntry(
            rankings={"PTS": {"SAS": 8}, "AST": {"SAS": 12}},
            source=dvp.DvPDataSource.DYNAMIC_LIVE,
            fetched_at=datetime.now(timezone.utc),
            season="2025-26",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    assert dr.get_opponent_defensive_rank("nba", "SAS", "PTS") == (8, "bdl_live")
    # Mapped stat types (player_points -> PTS) also work via STAT_TYPE_MAP.
    assert dr.get_opponent_defensive_rank("nba", "SAS", "player_points") == (8, "bdl_live")


def test_nba_returns_unavailable_for_unknown_team(monkeypatch):
    import services.dvp_service as dvp
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(
        dvp, "_dvp_cache",
        dvp.DvPCacheEntry(
            rankings={"PTS": {"SAS": 8}},
            source=dvp.DvPDataSource.DYNAMIC_LIVE,
            fetched_at=datetime.now(timezone.utc),
            season="2025-26",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    assert dr.get_opponent_defensive_rank("nba", "ZZZ", "PTS") == (None, "unavailable")


# ---------- annotate_defensive_rank (pipeline-time writer) ----------------
def test_annotate_writes_canonical_fields_on_every_pick(monkeypatch):
    import services.dvp_service as dvp
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(
        dvp, "_dvp_cache",
        dvp.DvPCacheEntry(
            rankings={"PTS": {"SAS": 8, "BOS": 2}},
            source=dvp.DvPDataSource.DYNAMIC_LIVE,
            fetched_at=datetime.now(timezone.utc),
            season="2025-26",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )

    picks = [
        {"opponent": "SAS", "stat_type": "PTS"},
        {"opponent_abbr": "BOS", "stat_type": "PTS"},
        {"opponent": "SAS", "stat_type": "REB"},          # stat not in cache
        {"opponent": None, "stat_type": "PTS"},           # missing opp
        "not-a-dict",
    ]
    count = dr.annotate_defensive_rank(picks, "nba")
    assert count == 2
    assert picks[0]["opponent_defensive_rank"] == 8
    assert picks[0]["opponent_defensive_source"] == "bdl_live"
    assert picks[0]["opponent_defensive_stat_type"] == "PTS"
    assert picks[1]["opponent_defensive_rank"] == 2
    assert picks[2]["opponent_defensive_rank"] is None
    assert picks[2]["opponent_defensive_source"] == "unavailable"
    assert picks[3]["opponent_defensive_rank"] is None
    assert picks[3]["opponent_defensive_source"] == "unavailable"


def test_annotate_mlb_writes_unavailable_contract_but_never_raises():
    picks = [{"opponent": "NYY", "stat_type": "H+R+RBI"}]
    count = dr.annotate_defensive_rank(picks, "mlb")
    assert count == 0
    assert picks[0]["opponent_defensive_rank"] is None
    assert picks[0]["opponent_defensive_source"] == "unavailable"
    assert picks[0]["opponent_defensive_stat_type"] == "H+R+RBI"


# ---------- Future-sport registration (NFL contract proof) -----------------
def test_future_sport_plugs_in_without_touching_callers():
    def fake_nfl_provider(opp, stat):
        return (5, "nfl_ngs_live")

    dr.register_provider("nfl", fake_nfl_provider)
    try:
        assert dr.get_opponent_defensive_rank("nfl", "KC", "passing_yards") == (5, "nfl_ngs_live")
    finally:
        # Restore default (unavailable)
        dr.register_provider("nfl", lambda o, s: (None, "unavailable"))
    # After teardown, back to unavailable — proving nothing is cached globally.
    assert dr.get_opponent_defensive_rank("nfl", "KC", "passing_yards") == (None, "unavailable")


# ---------- Static fallback leakage guard ---------------------------------
def test_static_DVP_RANKINGS_is_NEVER_a_source():
    """Guarantees `config.settings.DVP_RANKINGS` cannot leak a rank into
    the resolver output even if the dvp_service cache is empty/stale."""
    from config.settings import DVP_RANKINGS
    # Static table HAS a value for SAS PTS (=24, last season) — the bug value.
    assert DVP_RANKINGS["PTS"]["SAS"] == 24
    # Resolver must return None/unavailable when cache is not live, never 24.
    import services.dvp_service as dvp
    dvp._dvp_cache = None
    assert dr.get_opponent_defensive_rank("nba", "SAS", "PTS") == (None, "unavailable")


@pytest.mark.asyncio
async def test_ensure_provider_warm_is_sport_agnostic_and_safe():
    # Unknown sport -> no-op, no raise
    await dr.ensure_provider_warm("nfl")
    # MLB -> no-op, no raise
    await dr.ensure_provider_warm("mlb")
    # None/empty -> no raise
    await dr.ensure_provider_warm("")
    await dr.ensure_provider_warm(None)  # type: ignore[arg-type]
