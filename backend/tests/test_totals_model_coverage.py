"""
Regression test — Totals XGB model coverage.

User directive 2026-06-03:
  "Train a totals-side XGB model so the 18 OVER/UNDER game-total /
   team-total picks currently passing through (no model coverage)
   also go through the symmetric gate."

The trainer already produces game_total.pkl + team_total.pkl
artifacts (12 total: 4 markets × 3 sports). The live scorer was
silently SKIPPING totals because `classify_market_category` only
handled long-format `market_key` strings, not the short aliases
(`totals`, `team_totals`) the live ingest writes.

This test pins:
  1. Classifier handles BOTH short aliases AND long format.
  2. Totals rows ARE scored by the live pipeline (no longer all
     `model_probability=None`).
  3. The symmetric gate IS applied to totals (loser OVERs end up
     demoted just like loser AWAYs).
"""
import os
import pytest


def test_classify_market_category_handles_short_aliases():
    from services.team_live_xgb_scorer import classify_market_category
    assert classify_market_category("h2h") == "h2h"
    assert classify_market_category("spreads") == "spread"
    assert classify_market_category("spread") == "spread"
    assert classify_market_category("totals") == "game_total"
    assert classify_market_category("total") == "game_total"
    assert classify_market_category("team_totals") == "team_total"
    assert classify_market_category("team_total") == "team_total"


def test_classify_market_category_handles_long_format():
    from services.team_live_xgb_scorer import classify_market_category
    assert classify_market_category("points-home-game-ml-home") == "h2h"
    assert classify_market_category("points-home-game-sp-home") == "spread"
    assert classify_market_category("points-all-game-ou-over") == "game_total"
    assert classify_market_category("points-home-game-ou-over") == "team_total"
    assert classify_market_category("points-away-game-ou-under") == "team_total"


def test_totals_artifacts_exist():
    """The training pipeline must produce game_total + team_total
    artifacts for both NBA and MLB. Without these, totals scoring
    silently returns None and the symmetric gate can't fire."""
    from pathlib import Path
    root = Path("/app/backend/models/team_xgb")
    for sport in ("nba", "mlb"):
        for market in ("h2h", "spread", "game_total", "team_total"):
            p = root / sport / f"{market}.pkl"
            assert p.exists(), (
                f"missing trained artifact: {p} — totals coverage "
                f"broken for {sport}/{market}"
            )


@pytest.mark.asyncio
async def test_totals_rows_get_model_coverage():
    """No team_total or game_total row in `team_prop_scores` should
    sit in an active tier WITHOUT a model_probability — that would
    mean it bypassed scoring."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        unscored_in_active_tier = await db["team_prop_scores"].count_documents({
            "market_category": {"$in": ["team_total", "game_total"]},
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": None,
        })
        assert unscored_in_active_tier == 0, (
            f"{unscored_in_active_tier} totals picks are in an active "
            f"tier without model coverage — symmetric gate bypassed"
        )
    finally:
        client.close()


@pytest.mark.asyncio
async def test_totals_loser_overs_get_demoted():
    """OVER picks with model_probability < 0.50 must be demoted to
    tier=None — exactly the same threshold UNDER/AWAY picks face."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        # No OVER pick in active tier may have p<0.50.
        bad_over = await db["team_prop_scores"].count_documents({
            "market_category": {"$in": ["team_total", "game_total"]},
            "side": "OVER",
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": {"$ne": None, "$lt": 0.50},
        })
        # Mirror check for UNDER.
        bad_under = await db["team_prop_scores"].count_documents({
            "market_category": {"$in": ["team_total", "game_total"]},
            "side": "UNDER",
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": {"$ne": None, "$lt": 0.50},
        })
        assert bad_over == 0, (
            f"{bad_over} OVER losers still in active tier (p<0.50)"
        )
        assert bad_under == 0, (
            f"{bad_under} UNDER losers still in active tier (p<0.50)"
        )
    finally:
        client.close()
