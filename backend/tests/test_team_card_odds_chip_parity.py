"""
Regression test — team picks must expose `tier_reference_*` /
`display_reference_*` / `best_book_odds` so UniversalPlayerCard renders
the primary odds chip instead of `—`.

The card's `resolveDisplayOdds` reads, in order:
    display_reference_odds + display_reference_book
  → tier_reference_odds   + tier_reference_book
  → DK/FD/MGM/CSR/BOL per-book chain

For team picks we have ONE book (`book` + `odds`) that already won the
tier gate, so all three reference slots mirror that pair. Without this
parity the primary chip on every team card rendered as `—`.
"""
import asyncio
import os
import pytest


@pytest.mark.asyncio
async def test_team_tier_pick_carries_reference_odds():
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.team_prop_tier_service import get_team_prop_picks

    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    assert mongo_url and db_name, "MONGO_URL/DB_NAME required"

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        res = await get_team_prop_picks(
            db, sport="nba", tier_name="front_lines", limit=3)
        picks = res.get("picks") or []
        if not picks:
            pytest.skip("no nba front_lines team picks in DB")

        for p in picks:
            assert p.get("odds") is not None, \
                f"team pick missing raw odds: {p.get('team_id')}"
            # All three reference slots must be populated.
            assert p.get("tier_reference_odds") == p["odds"], \
                "tier_reference_odds must mirror odds"
            assert p.get("display_reference_odds") == p["odds"], \
                "display_reference_odds must mirror odds"
            assert p.get("best_book_odds") == p["odds"], \
                "best_book_odds must mirror odds"
            assert p.get("tier_reference_book"), \
                "tier_reference_book must be set"
            assert p.get("display_reference_book"), \
                "display_reference_book must be set"
    finally:
        client.close()


@pytest.mark.asyncio
async def test_team_with_badges_props_carry_reference_odds():
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.team_with_badges as twb

    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    twb.set_team_with_badges_db(db)
    try:
        # Call the route handler directly. nba_bos is the seeded
        # NBA Finals seed always present in the DB.
        res = await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        player = res.get("player") or {}
        props = player.get("props") or []
        if not props:
            pytest.skip("nba_bos has no team-detail props")
        for p in props:
            if p.get("odds") is None:
                # SKIP — moneyline-only rows or pre-line markets
                continue
            assert p.get("tier_reference_odds") == p["odds"]
            assert p.get("display_reference_odds") == p["odds"]
            assert p.get("best_book_odds") == p["odds"]
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(test_team_tier_pick_carries_reference_odds())
    asyncio.run(test_team_with_badges_props_carry_reference_odds())
    print("PASS")
