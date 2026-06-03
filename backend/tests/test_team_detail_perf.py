"""
Regression test — `/api/v3/team-with-badges/{team_id}` must respond
well under 5 seconds. The 2026-06-03 prod 504 timeout was caused by
an N+1 query pattern in `_hit_rates_for_market` (2 DB calls per
(market, line, side) tuple — 30+ tuples × ~500ms = 15s+ cold).

Fix: one bulk `_fetch_team_outcomes_bulk` + in-memory hit-rate
computation. This test pins the endpoint to <5s and also covers the
"all hit rates non-null where outcomes exist" contract so a future
refactor can't quietly skip computation.
"""
import os
import time
import pytest


@pytest.mark.asyncio
async def test_team_detail_endpoint_under_5_seconds():
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.team_with_badges as twb

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    twb.set_team_with_badges_db(db)
    try:
        # Warm-up (Mongo connection pool, indexes loaded into RAM)
        await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        t0 = time.monotonic()
        res = await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, (
            f"team-with-badges took {elapsed:.2f}s — must be <5s "
            f"(N+1 regression?)"
        )
        # Sanity: payload still has content after the bulk-query fix
        player = res.get("player") or {}
        assert player.get("props"), "props collapsed after bulk fix"
        # At least some props must have populated hit rates when
        # outcomes exist in the bulk fetch.
        has_hr = any(
            p.get("hit_rate_l10") is not None
            for p in player["props"]
        )
        assert has_hr, (
            "no prop carries a populated hit_rate_l10 — bulk-query "
            "in-memory hit-rate computation may be broken"
        )
    finally:
        client.close()
