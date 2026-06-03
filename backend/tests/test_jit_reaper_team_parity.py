"""
Regression test — JIT Vision Intel reaper must drive BOTH player and
team enrichment on every pass.

Before 2026-06-03 the reaper only called the player enricher, so team
picks only received Gemini narratives on the 60-min master_sync cycle.
Newly-surfaced team picks went 0–60 min with the deterministic
fallback sentence. This test pins the team enrichment to the reaper.
"""
import os
import pytest


@pytest.mark.asyncio
async def test_jit_reaper_calls_team_enrichment():
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        metrics = await run_jit_vision_intel_reaper_for_sport(db, "mlb")
        # The team enrichment block must always run regardless of player
        # uncovered count.
        assert "team_enrichment_metrics" in metrics, (
            "JIT reaper must include team_enrichment_metrics in its "
            "return dict (P1: team Vision Intel parity)"
        )
        team = metrics["team_enrichment_metrics"]
        # Required shape so downstream observability can report it.
        for k in (
            "total_visible_picks", "cache_hits", "to_call",
            "gemini_calls", "gemini_returned", "score_docs_written",
        ):
            assert k in team, (
                f"team_enrichment_metrics must contain `{k}`"
            )
    finally:
        client.close()
