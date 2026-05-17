"""Phase 4b — NBA parity + universal odds-bucket router tests.

Validates:
  (1) NBA `resolve_target_tier` behaviour is unchanged at every
      requested boundary (-300, -250, -200, -150, -149, -110, +100,
      +149, +150, +200).
  (2) The new universal `get_odds_bucket` returns IDENTICAL routing
      to the live `resolve_target_tier`.
  (3) Boundary inclusivity matches the documented contract:
      `<= -300` and `>= +150` are inclusive.
  (4) None / missing odds → None (no silent default).
  (5) Sport-agnosticism: NBA, MLB, NFL all produce the same routing
      for the same odds.
  (6) `tier_allows_odds` predicate matches `get_odds_bucket`.
  (7) The `TIER_ODDS_BUCKET_FAIL` reason code constant exists and is
      stable.
"""
from __future__ import annotations
import pytest

from services.scoring.gates.thresholds import (
    resolve_target_tier,
    UNIVERSAL_SAFE_HAVEN_MAX,
    UNIVERSAL_WAR_ZONE_MIN,
    ODDS_BUCKETS,
)
from services.scoring.odds_bucket_router import (
    get_odds_bucket,
    tier_allows_odds,
    explain_odds_bucket,
    get_tier_odds_contract,
    TIER_ODDS_BUCKET_FAIL,
    VALID_TIERS,
)


# ── (1) Live NBA contract — boundary values, unchanged ────────────────
@pytest.mark.parametrize("odds,expected", [
    (-1000, "safe_haven"),
    (-301, "safe_haven"),
    (-300, "safe_haven"),     # inclusive on chalk side
    (-299, "front_lines"),    # exclusive crossover
    (-250, "front_lines"),
    (-200, "front_lines"),
    (-150, "front_lines"),
    (-149, "front_lines"),
    (-110, "front_lines"),
    (+100, "front_lines"),
    (+149, "front_lines"),
    (+150, "war_zone"),       # inclusive on longshot side
    (+200, "war_zone"),
])
def test_nba_live_routing_unchanged(odds, expected):
    """NBA's `resolve_target_tier` must keep returning these mappings."""
    assert resolve_target_tier("nba", odds) == expected


# ── (2) Universal router parity vs live NBA router ────────────────────
@pytest.mark.parametrize("odds", [
    -1000, -500, -301, -300, -299, -250, -200, -150, -149, -110,
    -100, +100, +101, +149, +150, +200, +500, +1000,
])
def test_universal_matches_live(odds):
    """`get_odds_bucket` must equal `resolve_target_tier('nba',...)`."""
    assert get_odds_bucket(odds) == resolve_target_tier("nba", odds)
    # And must match every other sport identically (universal contract).
    for sport in ("nba", "mlb", "nfl"):
        assert get_odds_bucket(odds) == resolve_target_tier(sport, odds)


# ── (3) Boundary inclusivity ──────────────────────────────────────────
def test_safe_haven_max_is_inclusive():
    """`UNIVERSAL_SAFE_HAVEN_MAX` (-300) is INSIDE Safe Haven, -299 is FL."""
    assert get_odds_bucket(UNIVERSAL_SAFE_HAVEN_MAX) == "safe_haven"
    assert get_odds_bucket(UNIVERSAL_SAFE_HAVEN_MAX + 1) == "front_lines"


def test_war_zone_min_is_inclusive():
    """`UNIVERSAL_WAR_ZONE_MIN` (+150) is INSIDE War Zone, +149 is FL."""
    assert get_odds_bucket(UNIVERSAL_WAR_ZONE_MIN) == "war_zone"
    assert get_odds_bucket(UNIVERSAL_WAR_ZONE_MIN - 1) == "front_lines"


# ── (4) None / missing odds → None (no silent default) ───────────────
def test_missing_odds_returns_none():
    assert get_odds_bucket(None) is None


def test_tier_allows_odds_rejects_none():
    for t in VALID_TIERS:
        assert tier_allows_odds(t, None) is False


def test_tier_allows_odds_rejects_invalid_tier():
    assert tier_allows_odds("not_a_tier", -250) is False
    assert tier_allows_odds("", -250) is False


# ── (5) Sport-agnosticism ─────────────────────────────────────────────
def test_sport_buckets_all_equal_universal():
    for sport in ("nba", "mlb", "nfl"):
        block = ODDS_BUCKETS[sport]
        assert block["safe_haven_max"] == UNIVERSAL_SAFE_HAVEN_MAX
        assert block["war_zone_min"] == UNIVERSAL_WAR_ZONE_MIN


# ── (6) `tier_allows_odds` predicate matches `get_odds_bucket` ───────
@pytest.mark.parametrize("odds,tier,expected", [
    (-400, "safe_haven", True),
    (-400, "front_lines", False),
    (-400, "war_zone", False),
    (-299, "safe_haven", False),
    (-299, "front_lines", True),
    (-299, "war_zone", False),
    (+149, "front_lines", True),
    (+150, "war_zone", True),
    (+150, "front_lines", False),
])
def test_tier_allows_odds_matches_router(odds, tier, expected):
    assert tier_allows_odds(tier, odds) is expected


# ── (7) Reason code constant + contract API ─────────────────────────
def test_tier_odds_bucket_fail_constant():
    assert TIER_ODDS_BUCKET_FAIL == "tier_odds_bucket_fail"


def test_get_tier_odds_contract_shape():
    c = get_tier_odds_contract()
    assert c["universal_safe_haven_max"] == UNIVERSAL_SAFE_HAVEN_MAX
    assert c["universal_war_zone_min"] == UNIVERSAL_WAR_ZONE_MIN
    assert set(c["valid_tiers"]) == set(VALID_TIERS)
    assert c["fail_reason_code"] == TIER_ODDS_BUCKET_FAIL


def test_explain_odds_bucket_human_readable():
    # Sanity — strings reference the actual constants.
    assert "safe_haven" in explain_odds_bucket(-500)
    assert "front_lines" in explain_odds_bucket(-200)
    assert "war_zone" in explain_odds_bucket(+200)
    assert "no_reference_odds" in explain_odds_bucket(None)


# ── Future-sport compatibility (NFL or any new sport added) ──────────
def test_future_sport_compatibility():
    """Any new sport added to ODDS_BUCKETS must inherit the universal
    constants (cross-sport consistency is the entire point)."""
    for sport_key, block in ODDS_BUCKETS.items():
        assert block["safe_haven_max"] == UNIVERSAL_SAFE_HAVEN_MAX, \
            f"{sport_key} drifted from universal SH max"
        assert block["war_zone_min"] == UNIVERSAL_WAR_ZONE_MIN, \
            f"{sport_key} drifted from universal WZ min"
