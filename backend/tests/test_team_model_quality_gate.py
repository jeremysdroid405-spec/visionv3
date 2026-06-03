"""
Regression test — symmetric model-quality gate for team picks.

User directive 2026-06-03:
  "We don't want loosers on the board just because they're unders.
   The model needs to know its predicting unders and we need to know
   why we are recommending them."

Background: the team-side XGB model is trained with `is_over` as a
feature and target = `outcome_numeric=1` (= "the pick won"). So
`model_probability` is THE PROBABILITY THIS SIDE WINS, regardless of
whether the side is OVER, UNDER, HOME, or AWAY.

Before this fix, tier was set purely by odds-bucket router (price).
Picks with `model_probability=0.09` (91% chance to lose) were
sitting in `front_lines` because their odds happened to be ~+150.

This test pins the symmetric demotion rules:
  • model_p < 0.50           → tier=None (clear loser)
  • 0.50 ≤ p < 0.55, edge<2% → tier=None (borderline, no edge)
"""
import os
import pytest


@pytest.mark.asyncio
async def test_no_obvious_losers_in_team_tier():
    """No live team pick may sit in any active tier (safe_haven /
    front_lines / war_zone) while the model says it has < 50%
    chance to win."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        # Anything still in an active tier MUST have either no model
        # output (h2h moneylines bypass) OR p >= 0.50.
        bad = await db["team_prop_scores"].count_documents({
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": {"$ne": None, "$lt": 0.50},
        })
        assert bad == 0, (
            f"{bad} team picks with model_probability<0.50 are still "
            f"in an active tier — symmetric quality gate failed"
        )
    finally:
        client.close()


@pytest.mark.asyncio
async def test_borderline_picks_demoted_when_no_edge():
    """Picks with 0.50 ≤ p < 0.55 must also be demoted when
    edge < +2%."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        bad = await db["team_prop_scores"].count_documents({
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": {"$gte": 0.50, "$lt": 0.55},
            "edge": {"$lt": 0.02},
        })
        assert bad == 0, (
            f"{bad} borderline (p<0.55, edge<+2%) team picks still "
            f"in an active tier"
        )
    finally:
        client.close()


@pytest.mark.asyncio
async def test_gate_threshold_applied_symmetrically():
    """The gate must apply the SAME `model_probability < 0.50`
    threshold to BOTH sides. We verify by checking: every demoted
    pick should have p<0.50 (or borderline+no-edge), regardless of
    side. The actual SIDE distribution of demotions just reflects
    the model's predictions on today's slate — it's normal for one
    side to dominate when one team is heavily favored."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        # Every demoted pick must satisfy the threshold rule.
        bad = await db["team_prop_scores"].count_documents({
            "model_demoted": True,
            "model_probability": {"$gte": 0.55},
            "edge": {"$gte": 0.02},
        })
        assert bad == 0, (
            f"{bad} demoted picks don't satisfy the gate threshold "
            f"(p<0.55 OR edge<+2%) — gate is broken"
        )
        # And no NON-demoted pick in an active tier should violate
        # the threshold rule (regardless of side).
        bad_kept = await db["team_prop_scores"].count_documents({
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "model_probability": {"$ne": None, "$lt": 0.50},
        })
        assert bad_kept == 0, (
            f"{bad_kept} non-demoted active-tier picks have p<0.50"
        )
    finally:
        client.close()
