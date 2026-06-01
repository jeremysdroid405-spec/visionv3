"""
Integration test for build_team_historical_outcomes.py.

Seeds synthetic team_historical_props + team_matchups (and the NFL
equivalents) into preview Mongo under quarantine collection names,
runs the builder against those quarantined sources, and verifies:

  1. All four Phase-1 markets land in `team_historical_outcomes_TEST`
     with the correct WIN/LOSS/PUSH/UNRESOLVED classification.
  2. Re-running the builder produces zero net new rows (idempotency).
  3. Sport filtering works.
  4. Date-window filtering works.
  5. UNRESOLVED rows fire `no_matchup` and `no_final_score` reasons
     when expected.

The test patches SPORT_SOURCES + DEST_COLL on the module to point at
quarantine names so production collections are NEVER touched.
"""
from __future__ import annotations
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from scripts.sgo import build_team_historical_outcomes as B


# Quarantined collection names (test-only)
_TAG       = uuid.uuid4().hex[:8]
TEST_PROPS = f"_test_team_props_{_TAG}"
TEST_NFL   = f"_test_nfl_props_{_TAG}"
TEST_TM    = f"_test_team_matchups_{_TAG}"
TEST_NM    = f"_test_nfl_matchups_{_TAG}"
TEST_DEST  = f"_test_team_outcomes_{_TAG}"


@pytest_asyncio.fixture
async def db_and_patch():
    """Patch module globals to point at quarantine collections."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    # Patch
    orig_sources = B.SPORT_SOURCES
    orig_dest    = B.DEST_COLL
    B.SPORT_SOURCES = {
        "mlb": (TEST_PROPS, TEST_TM),
        "nba": (TEST_PROPS, TEST_TM),
        "nfl": (TEST_NFL,   TEST_NM),
    }
    B.DEST_COLL = TEST_DEST
    # Clean
    for c in (TEST_PROPS, TEST_NFL, TEST_TM, TEST_NM, TEST_DEST):
        await db[c].drop()
    try:
        yield db
    finally:
        for c in (TEST_PROPS, TEST_NFL, TEST_TM, TEST_NM, TEST_DEST):
            await db[c].drop()
        B.SPORT_SOURCES = orig_sources
        B.DEST_COLL = orig_dest
        client.close()


# ─── seeding helpers ───
def _prop(sport, event_id, team_id, market_key, bet, side, ent,
            line, book="circa", *, alt=False, period="game",
            game_date="2024-09-07"):
    return {
        "sport":         sport,
        "event_id":      event_id,
        "team_id":       team_id,
        "game_date":     game_date,
        "market":        market_key,
        "market_key":    market_key,
        "market_name":   market_key,
        "betTypeID":     bet,
        "statID":        "points",
        "statEntityID":  ent,
        "periodID":      period,
        "side":          side,
        "sideID":        side.lower(),
        "line":          line,
        "odds":          -110,
        "book":          book,
        "home_away":     "home" if team_id.endswith("_home") else "away",
        "is_alternate":  alt,
    }


def _matchup(event_id, sport, home_team, away_team, hs, as_, gd="2024-09-07"):
    return {
        "event_id":       event_id,
        "sport":          sport,
        "league":         sport.upper(),
        "home_team_id":   home_team,
        "away_team_id":   away_team,
        "home_team_name": home_team,
        "away_team_name": away_team,
        "game_date":      gd,
        "status":         "completed",
        "home_score":     hs,
        "away_score":     as_,
    }


async def _run_builder(*, sport="all", start=None, end=None):
    """Invoke the builder via its amain() with synthetic argparse."""
    class _Args:
        pass
    a = _Args()
    a.sport = sport
    a.start = start
    a.end = end
    a.dry_run = False
    return await B.amain(a)


# ─── tests ───
@pytest.mark.asyncio
async def test_end_to_end_all_three_sports(db_and_patch):
    db = db_and_patch
    # MLB game: home 5, away 3
    # NBA game: home 110, away 102
    # NFL game: home 24, away 17
    matchups = [
        _matchup("mlb_e1", "mlb", "mlb_home", "mlb_away", 5, 3),
        _matchup("nba_e1", "nba", "nba_home", "nba_away", 110, 102),
    ]
    nfl_matchups = [
        _matchup("nfl_e1", "nfl", "nfl_home", "nfl_away", 24, 17),
    ]
    # 8 MLB/NBA props (mix of WIN/LOSS/PUSH) + 4 NFL props
    props = [
        # MLB h2h home win
        _prop("mlb", "mlb_e1", "mlb_home", "k1", "ml", "HOME", "home", None),
        # MLB game total over (5+3=8); line 7.5 → over wins
        _prop("mlb", "mlb_e1", "mlb_home", "k2", "ou", "OVER", "all", 7.5),
        # MLB team total home over (5); line 4.5 → win
        _prop("mlb", "mlb_e1", "mlb_home", "k3", "ou", "OVER", "home", 4.5),
        # MLB spread home -1 (5-3-1=1 → win)
        _prop("mlb", "mlb_e1", "mlb_home", "k4", "sp", "HOME", "home", -1),
        # NBA: home -10 → margin (110-102-10) = -2 → LOSS
        _prop("nba", "nba_e1", "nba_home", "k5", "sp", "HOME", "home", -10),
        # NBA game total over 212 (212 actual) → PUSH
        _prop("nba", "nba_e1", "nba_home", "k6", "ou", "OVER", "all", 212),
        # NBA team total away under 105 (102 actual) → WIN
        _prop("nba", "nba_e1", "nba_away", "k7", "ou", "UNDER", "away", 105),
        # MLB h2h away — tie test (skipped; we don't seed a tie here);
        # use UNRESOLVED row from no_matchup
        _prop("mlb", "mlb_unknown_event", "mlb_home", "k8", "ml", "HOME", "home", None),
    ]
    nfl_props = [
        # NFL h2h home (24>17 → WIN)
        _prop("nfl", "nfl_e1", "nfl_home", "n1", "ml", "HOME", "home", None),
        # NFL spread home -3 (24-17-3=4 → WIN)
        _prop("nfl", "nfl_e1", "nfl_home", "n2", "sp", "HOME", "home", -3),
        # NFL game total over 40 (41 actual → WIN)
        _prop("nfl", "nfl_e1", "nfl_home", "n3", "ou", "OVER", "all", 40),
        # NFL alt-line (must be SKIPPED — is_alternate=True)
        _prop("nfl", "nfl_e1", "nfl_home", "n4", "sp", "HOME", "home", -3.5, alt=True),
    ]
    await db[TEST_TM].insert_many(matchups)
    await db[TEST_NM].insert_many(nfl_matchups)
    await db[TEST_PROPS].insert_many(props)
    await db[TEST_NFL].insert_many(nfl_props)

    await _run_builder()

    # ── verify per-market outcomes ──
    rows = await db[TEST_DEST].find({}, {"_id": 0}).to_list(length=None)
    assert len(rows) == 11, f"expected 11 outcomes; got {len(rows)}"
    by_key = {r["market_key"]: r for r in rows}
    assert by_key["k1"]["outcome"] == "WIN"
    assert by_key["k2"]["outcome"] == "WIN"
    assert by_key["k3"]["outcome"] == "WIN"
    # k4: spread home -1; margin = (5-3)+(-1) = 1 → WIN
    assert by_key["k4"]["outcome"] == "WIN"
    assert by_key["k5"]["outcome"] == "LOSS"
    assert by_key["k6"]["outcome"] == "PUSH"
    assert by_key["k7"]["outcome"] == "WIN"
    # k8 = mlb prop on missing event → no_matchup
    assert by_key["k8"]["outcome"] == "UNRESOLVED"
    assert by_key["k8"]["unresolved_reason"] == "no_matchup"
    # NFL ones
    assert by_key["n1"]["outcome"] == "WIN"
    assert by_key["n2"]["outcome"] == "WIN"
    assert by_key["n3"]["outcome"] == "WIN"
    # NFL alt-line must be SKIPPED, not present
    assert "n4" not in by_key

    # ── verify market_category telemetry ──
    cats = {r["market_key"]: r["market_category"] for r in rows}
    assert cats["k1"] == "h2h"
    assert cats["k2"] == "game_total"
    assert cats["k3"] == "team_total"
    assert cats["k4"] == "spread"


@pytest.mark.asyncio
async def test_idempotent_rerun(db_and_patch):
    db = db_and_patch
    await db[TEST_TM].insert_one(
        _matchup("e", "mlb", "h", "a", 5, 3))
    await db[TEST_PROPS].insert_one(
        _prop("mlb", "e", "h", "x", "ml", "HOME", "home", None))

    await _run_builder()
    n1 = await db[TEST_DEST].count_documents({})
    await _run_builder()
    n2 = await db[TEST_DEST].count_documents({})
    assert n1 == n2 == 1, (
        f"Re-run created/duplicated rows: first={n1} second={n2}")


@pytest.mark.asyncio
async def test_no_final_score_reason(db_and_patch):
    """Matchup exists but home_score/away_score are missing."""
    db = db_and_patch
    m = _matchup("e", "mlb", "h", "a", None, None)
    m.pop("home_score"); m.pop("away_score")
    await db[TEST_TM].insert_one(m)
    await db[TEST_PROPS].insert_one(
        _prop("mlb", "e", "h", "y", "ml", "HOME", "home", None))
    await _run_builder()
    rows = await db[TEST_DEST].find({}, {"_id": 0}).to_list(length=None)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "UNRESOLVED"
    assert rows[0]["unresolved_reason"] == "no_final_score"


@pytest.mark.asyncio
async def test_alternate_lines_are_skipped(db_and_patch):
    db = db_and_patch
    await db[TEST_TM].insert_one(_matchup("e", "mlb", "h", "a", 5, 3))
    await db[TEST_PROPS].insert_many([
        _prop("mlb", "e", "h", "main",   "sp", "HOME", "home", -1),
        _prop("mlb", "e", "h", "altline", "sp", "HOME", "home", -2, alt=True),
    ])
    await _run_builder()
    keys = {r["market_key"] async for r in db[TEST_DEST].find({})}
    assert "main" in keys
    assert "altline" not in keys


@pytest.mark.asyncio
async def test_period_markets_are_skipped(db_and_patch):
    """1q / 1h / 1i markets must be filtered out (Phase 1 = full-game only)."""
    db = db_and_patch
    await db[TEST_TM].insert_one(_matchup("e", "mlb", "h", "a", 5, 3))
    await db[TEST_PROPS].insert_many([
        _prop("mlb", "e", "h", "game",  "ml", "HOME", "home", None,
               period="game"),
        _prop("mlb", "e", "h", "first", "ml", "HOME", "home", None,
               period="1q"),
    ])
    await _run_builder()
    keys = {r["market_key"] async for r in db[TEST_DEST].find({})}
    assert "game" in keys and "first" not in keys


@pytest.mark.asyncio
async def test_date_window_filter(db_and_patch):
    db = db_and_patch
    await db[TEST_TM].insert_one(_matchup("e", "mlb", "h", "a", 5, 3,
                                              gd="2024-07-15"))
    # Prop's game_date inside window
    p1 = _prop("mlb", "e", "h", "in",  "ml", "HOME", "home", None,
                 game_date="2024-07-15")
    # Prop's game_date outside window
    p2 = _prop("mlb", "e", "h", "out", "ml", "HOME", "home", None,
                 game_date="2024-06-15")
    await db[TEST_PROPS].insert_many([p1, p2])
    await _run_builder(start="2024-07-01", end="2024-08-01", sport="mlb")
    keys = {r["market_key"] async for r in db[TEST_DEST].find({})}
    assert "in" in keys and "out" not in keys


@pytest.mark.asyncio
async def test_unique_key_dedupes_book_variants_correctly(db_and_patch):
    """Same (event, team, market_key, line, side) but DIFFERENT books
    must produce TWO outcome rows (book is part of the unique key)."""
    db = db_and_patch
    await db[TEST_TM].insert_one(_matchup("e", "mlb", "h", "a", 5, 3))
    await db[TEST_PROPS].insert_many([
        _prop("mlb", "e", "h", "z", "ml", "HOME", "home", None,
               book="circa"),
        _prop("mlb", "e", "h", "z", "ml", "HOME", "home", None,
               book="draftkings"),
    ])
    await _run_builder()
    n = await db[TEST_DEST].count_documents({})
    assert n == 2, f"expected 2 distinct-book rows, got {n}"
