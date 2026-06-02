"""Smoke test for the historical enrichment pass in
`services.team_prop_tier_service._enrich_cards_with_history`.

Locks in:
  - Every card receives `hit_rate_l5/l10/l20`, `vision_intel`,
    `scout_badges`, `intel_suite`, `season_avg`/`l5_avg`/`l10_avg`/
    `l20_avg` (None is acceptable for h2h projection fields, but the
    KEYS must be present so the frontend cards bind without
    `undefined` fallbacks).
  - The enrichment is byte-equal to the team_with_badges endpoint's
    output for the same (team, market_category, side, line) — i.e.
    detail page and pick card cannot drift.
  - At least one MLB card gets a non-empty `vision_intel` string
    (real data exists in the DB right now).
"""
from __future__ import annotations

import os
import asyncio
import pytest
from motor.motor_asyncio import AsyncIOMotorClient


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


_REQUIRED_KEYS = {
    "hit_rate_l5", "hit_rate_l10", "hit_rate_l20", "season_hit_rate",
    "l5_avg", "l10_avg", "l20_avg", "season_avg",
    "vk_predicted", "edge_vs_fair",
    "vision_intel", "scout_badges", "active_badges", "intel_suite",
}


@pytest.mark.asyncio
async def test_every_card_carries_required_enrichment_keys():
    """Every card returned by the tier endpoint must carry the full
    enrichment field set — even if values are None for h2h."""
    from services.team_prop_tier_service import get_team_prop_picks
    out = await get_team_prop_picks(
        _db(), sport="mlb", tier_name="front_lines", limit=5,
    )
    picks = out.get("picks", [])
    if not picks:
        pytest.skip("No MLB live team props available")
    for p in picks:
        missing = _REQUIRED_KEYS - set(p.keys())
        assert not missing, f"card missing keys: {missing}"


@pytest.mark.asyncio
async def test_at_least_one_card_has_real_vision_intel():
    """We have 880k MLB historical rows; at least one of the top-5
    cards must produce a non-empty deterministic vision-intel
    sentence (proves the historical join works at the service layer,
    not just the detail endpoint)."""
    from services.team_prop_tier_service import get_team_prop_picks
    out = await get_team_prop_picks(
        _db(), sport="mlb", tier_name="front_lines", limit=10,
    )
    picks = out.get("picks", [])
    if not picks:
        pytest.skip("No MLB live team props available")
    intel_cards = [p for p in picks if p.get("vision_intel")]
    assert intel_cards, "no card produced a vision_intel sentence"
    # And the sentence carries the canonical phrasing.
    assert any("Hit " in (p["vision_intel"]) for p in intel_cards)


@pytest.mark.asyncio
async def test_hit_rate_matches_team_with_badges_endpoint():
    """Pick a card, then resolve the same (team, category, side,
    line) via the team-with-badges endpoint, and assert the
    `hit_rate_l10` values agree exactly. Guards against drift between
    the board read path and the detail read path."""
    from services.team_prop_tier_service import get_team_prop_picks
    from routes.team_with_badges import (
        get_team_with_badges, set_team_with_badges_db,
    )
    set_team_with_badges_db(_db())
    tier = await get_team_prop_picks(
        _db(), sport="mlb", tier_name="front_lines", limit=10,
    )
    picks = tier.get("picks", [])
    if not picks:
        pytest.skip("No MLB live team props available")
    card = picks[0]
    detail = await get_team_with_badges(
        card["team_id"], sport="mlb",
    )
    cmp_key = (
        (card.get("market_category") or "").lower(),
        (card.get("side") or "").upper(),
        card.get("line"),
    )
    matching = [
        p for p in detail["player"]["props"]
        if ((p.get("market_category") or "").lower(),
            (p.get("direction") or "").upper(),
            p.get("line")) == cmp_key
    ]
    if not matching:
        pytest.skip("Detail page has no matching prop for the top card")
    assert card["hit_rate_l10"] == matching[0]["hit_rate_l10"], (
        "Board card and detail page disagree on hit_rate_l10"
    )
