"""
Unit tests for `_tier_odds_filter` — the optimizer's tier router.

The optimizer routes rows into tiers BY ODDS RANGE, matching the
live runner's `resolve_target_tier`. This test pins the boundaries
and the null-odds exclusion to lock the behavior the user
explicitly asked for: tiers are buckets, not rejects.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import (
    _tier_odds_filter,
    UNIVERSAL_SAFE_HAVEN_MAX,
    UNIVERSAL_WAR_ZONE_MIN,
)


def _matches(filt: dict, value):
    """Apply a Mongo-ish range filter dict to a single odds value."""
    inner = filt["odds"]
    if value is None:
        return inner.get("$ne") is not None and False  # excluded
    ok = True
    if "$lte" in inner:
        ok = ok and value <= inner["$lte"]
    if "$lt" in inner:
        ok = ok and value < inner["$lt"]
    if "$gte" in inner:
        ok = ok and value >= inner["$gte"]
    if "$gt" in inner:
        ok = ok and value > inner["$gt"]
    return ok


def test_safe_haven_routes_heavy_chalk():
    f = _tier_odds_filter("safe_haven")
    assert _matches(f, -400) is True
    assert _matches(f, UNIVERSAL_SAFE_HAVEN_MAX) is True
    assert _matches(f, UNIVERSAL_SAFE_HAVEN_MAX + 1) is False
    assert _matches(f, -150) is False
    assert _matches(f, +200) is False


def test_war_zone_routes_longshots():
    f = _tier_odds_filter("war_zone")
    assert _matches(f, +500) is True
    assert _matches(f, UNIVERSAL_WAR_ZONE_MIN) is True
    assert _matches(f, UNIVERSAL_WAR_ZONE_MIN - 1) is False
    assert _matches(f, -110) is False
    assert _matches(f, -400) is False


def test_front_lines_routes_mid_range():
    f = _tier_odds_filter("front_lines")
    assert _matches(f, -110) is True
    assert _matches(f, +149) is True
    assert _matches(f, -299) is True
    # Boundary exclusions — front_lines is strictly between safe_haven
    # and war_zone
    assert _matches(f, UNIVERSAL_SAFE_HAVEN_MAX) is False
    assert _matches(f, UNIVERSAL_WAR_ZONE_MIN) is False
    assert _matches(f, -400) is False
    assert _matches(f, +200) is False


def test_null_odds_excluded_from_every_tier():
    """odds=None means the row cannot be routed to any tier (no
    reference market). Every tier filter must reject it."""
    for tier in ("safe_haven", "front_lines", "war_zone"):
        f = _tier_odds_filter(tier)
        assert f["odds"].get("$ne") is None, (
            f"tier {tier} must explicitly exclude null odds")


def test_tier_buckets_are_disjoint_and_cover_all_real_odds():
    """No odds value can match more than one tier; together they
    cover every non-null odds. Sweep -1000 to +1000 to verify."""
    sh = _tier_odds_filter("safe_haven")
    fl = _tier_odds_filter("front_lines")
    wz = _tier_odds_filter("war_zone")
    for odds in range(-1000, 1001, 5):
        matches = [_matches(sh, odds), _matches(fl, odds), _matches(wz, odds)]
        assert sum(matches) == 1, (
            f"odds={odds} matched {matches} (must be exactly one tier)")


def test_unknown_tier_returns_never_matches_clause():
    """Unknown tier strings must not silently route to safe_haven /
    front_lines / war_zone. They must return a clause that no real
    row will satisfy."""
    f = _tier_odds_filter("totally_made_up_tier")
    assert "odds" not in f
    # Should be a no-match shape
    assert "_unknown_tier_" in f
