"""
Regression test — team detail endpoint must reuse the SAME SSOT
calculations the board pipeline uses (league_ranks via
`_compute_league_ranks`, rich `_build_team_scout_badges`, rich
`_build_team_intel_suite`). User feedback 2026-06-03:

    "we already do these calculations, dvp matchups, bullpen matchups
     etc. they are calculated in player vision intel and can be
     easily mirrored"

Pinning this so a future refactor can't quietly re-fork the team
detail into a thin local-only builder.
"""
import os
import pytest


@pytest.mark.asyncio
async def test_team_detail_uses_rich_builders_and_league_ranks():
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.team_with_badges as twb

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    twb.set_team_with_badges_db(db)
    try:
        res = await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        props = (res.get("player") or {}).get("props") or []
        if not props:
            pytest.skip("nba_bos has no detail props")

        # Rich intel_suite must carry the full board-parity tile set,
        # not just the thin {lasso, scout_badges, context_badges}.
        REQUIRED_TILES = {
            "usage_ripple", "pace_delta", "tempo", "blowout_risk",
            "matchup_dvp", "momentum_data", "variance", "lasso",
        }
        for p in props:
            tiles = set((p.get("intel_suite") or {}).keys())
            missing = REQUIRED_TILES - tiles
            assert not missing, (
                f"intel_suite missing rich tiles {missing} — "
                f"team_with_badges fell back to thin builder?"
            )

        # At least one prop must carry a rank-based team badge
        # (Brick Wall / Fast Lane / Fortress / Jet Fuel / etc.) — these
        # only fire when `league_ranks` is populated. Empty league_ranks
        # regression caught here.
        RANK_BADGES = {
            "brick_wall", "fast_lane", "deadeye", "fortress",
            "jet_fuel", "barrel_club", "icebox", "high-powered",
            "stout_defense", "high_powered",
        }
        seen_rank_badge = False
        for p in props:
            for b in (p.get("scout_badges") or []):
                key = (b.get("badge_key") or "").lower()
                if key in RANK_BADGES:
                    seen_rank_badge = True
                    break
            if seen_rank_badge:
                break
        assert seen_rank_badge, (
            "no rank-based team badge fired across any prop — "
            "league_ranks was probably empty {} (regression)"
        )

        # matchup_dvp must read the opponent's defensive rank from
        # league_ranks (not None) when an opponent exists.
        for p in props:
            md = (p.get("intel_suite") or {}).get("matchup_dvp")
            if md and md.get("opponent"):
                assert md.get("rank") is not None, (
                    "matchup_dvp.rank is None despite opponent set — "
                    "league_ranks not threaded into intel_suite"
                )
                assert md.get("label") in ("Elite", "Tough", "Neutral", "Soft")
    finally:
        client.close()
