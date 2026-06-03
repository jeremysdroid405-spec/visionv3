"""
Regression test — Team detail page (`/api/v3/team-with-badges/{team_id}`)
must return a payload that powers ALL four user-visible behaviours:

  1. Open page → all team props rendered (grouped by category)
  2. Per-prop HR graph for the last N games (real team scores)
  3. Auto-scroll to the clicked pick (the "gold bet")
  4. Click the gold bet → Vision Intel Suite modal opens

The frontend's matching rules are strict, so this contract test pins:
  • stat_type is a canonical SSOT token (TEAM_TOTAL / SPREAD / etc.)
  • direction is normalised to OVER / UNDER (so the click-key from the
    board matches the detail-page prop key — without this the auto-
    scroll silently no-ops)
  • per-prop game_logs carry team_score / opp_score / margin /
    total_score (the chart's STAT_FIELD_MAP needs them)
  • intel_suite is present so the Vision Intel Suite modal opens
"""
import os
import pytest


@pytest.mark.asyncio
async def test_team_with_badges_full_parity_contract():
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.team_with_badges as twb

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    twb.set_team_with_badges_db(db)
    try:
        res = await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        player = res.get("player") or {}
        props = player.get("props") or []
        if not props:
            pytest.skip("nba_bos has no team-detail props")

        # 1) Categorisation — every prop must carry a canonical token
        #    that the frontend's CATEGORY_ORDER + STAT_FIELD_MAP know.
        valid_tokens = {"TEAM_TOTAL", "GAME_TOTAL", "SPREAD", "MONEYLINE"}
        for p in props:
            assert p.get("stat_type") in valid_tokens, (
                f"unknown stat_type token: {p.get('stat_type')} — "
                f"frontend chart + category grouping will break"
            )
            # `stat_type_extracted` must equal `stat_type` so the
            # detail page's `groupedProps` key resolves correctly.
            assert p.get("stat_type_extracted") == p.get("stat_type")

        # 2) Per-prop game_logs — chart needs >= 1 game with the
        #    correct numeric field for its category.
        for p in props:
            gls = p.get("game_logs") or []
            assert gls, "every prop must carry game_logs for chart"
            head = gls[0]
            for k in ("team_score", "opp_score", "margin", "total_score"):
                assert k in head, (
                    f"per-prop game_log missing `{k}` — chart needs it"
                )

        # 3) Direction must be normalised OVER / UNDER so the
        #    board-click highlightKey matches the detail-page row.
        for p in props:
            assert p.get("direction") in ("OVER", "UNDER"), (
                f"direction must be normalized OVER/UNDER for the "
                f"highlight match — got `{p.get('direction')}`"
            )

        # 4) Vision Intel Suite modal — intel_suite must exist.
        for p in props:
            assert p.get("intel_suite") is not None, (
                "intel_suite required for Vision Intel Suite modal"
            )
            assert "scout_badges" in p.get("intel_suite") or "lasso" in p.get("intel_suite"), (
                "intel_suite must carry scout_badges or lasso payload"
            )

        # 5) Reference odds carried (P0 fix — odds chip parity)
        for p in props:
            if p.get("odds") is not None:
                assert p.get("tier_reference_odds") == p["odds"]
    finally:
        client.close()
