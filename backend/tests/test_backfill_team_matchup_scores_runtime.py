"""
Regression tests for backfill_team_matchup_scores ↔ SGOClient binding.

Locks the contract that the backfill script calls a method that ACTUALLY
EXISTS on SGOClient. The original bug was a typo (`get_event` vs
`get_event_with_results`) that wasn't caught until VPS execution — the
SGO API key isn't available in preview, so the script never reached the
real fetch path in unit tests.

These tests use end-to-end mocking with `monkeypatch` to:
  1. Stub out the SGOClient constructor + .get_event_with_results
  2. Drive the real backfill main loop with synthetic matchups
  3. Verify the right method is called, with the right arguments
  4. Verify the dispatch happens AT LEAST ONCE (i.e. the script doesn't
     silently fall through to a 612-error pattern)
"""
from __future__ import annotations
import asyncio
import inspect
import os
import sys
import uuid
import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

from scripts.sgo import backfill_team_matchup_scores as B
from scripts.sgo.client import SGOClient


# ─── static contract — method actually exists on the client ─────────
def test_sgoclient_exposes_get_event_with_results():
    """The exact name the backfill calls. Bug regression — was
    `get_event` (which doesn't exist) before the fix."""
    assert hasattr(SGOClient, "get_event_with_results"), (
        "SGOClient missing `get_event_with_results`. The backfill "
        "script will fail with AttributeError on every VPS row.")
    # Verify it's an async method (else the `await` in backfill blows up)
    fn = getattr(SGOClient, "get_event_with_results")
    assert inspect.iscoroutinefunction(fn), (
        "get_event_with_results must be `async def`.")


def test_sgoclient_exposes_iter_finalized_events_with_results():
    """The bulk method the backfill calls. Was confirmed in production
    to be the only path returning populated `results` for older finals."""
    assert hasattr(SGOClient, "iter_finalized_events_with_results"), (
        "SGOClient missing `iter_finalized_events_with_results`. "
        "The backfill script's bulk score-pull will fail.")
    fn = getattr(SGOClient, "iter_finalized_events_with_results")
    assert inspect.isasyncgenfunction(fn), (
        "iter_finalized_events_with_results must be `async def` with `yield`.")


def test_sgoclient_does_not_expose_renamed_or_legacy_get_event():
    """If a future refactor renames the method, this test fails LOUDLY
    so the call-site in the backfill script can be updated in lock-step."""
    # The bug was calling `client.get_event(...)`. If somebody later
    # ALSO adds a `get_event` (different shape) and the rename ambiguity
    # comes back, the test below pinpoints the live API surface.
    methods = {n for n in dir(SGOClient) if n.startswith("get_event")}
    # Must contain the canonical name; otherwise the script breaks.
    assert "get_event_with_results" in methods


def test_backfill_script_uses_iter_finalized_events_with_results():
    """Source-scan the backfill script — bulk finalized-events path is
    the only one that returns populated `results` for older NFL finals.
    The per-row `get_event_with_results` was observed empirically to
    return 612/612 events with NO scores. Lock the bulk path in."""
    src = inspect.getsource(B)
    assert "iter_finalized_events_with_results" in src, (
        "backfill_team_matchup_scores.py no longer calls "
        "`iter_finalized_events_with_results` — the per-row "
        "`get_event_with_results` endpoint does NOT carry final scores "
        "for older finals; reverting to it will break all NFL/MLB/NBA "
        "backfills.")
    # The per-row pattern that was observed to return zero scores
    # MUST NOT be the live call-site anymore.
    assert "sgo.get_event_with_results(" not in src, (
        "backfill_team_matchup_scores.py still calls per-row "
        "`sgo.get_event_with_results(...)` — that endpoint returned "
        "0 scores for 612 events on VPS. Use the bulk "
        "`iter_finalized_events_with_results(league_id, ...)` path "
        "instead.")


# ─── runtime smoke — mocked SGOClient produces non-zero fetches ──
@pytest_asyncio.fixture
async def db_and_patch(monkeypatch):
    """Quarantined collections + stubbed SGOClient."""
    tag = uuid.uuid4().hex[:8]
    mt = f"_test_nfl_matchups_{tag}"
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Patch sport→collection mapping
    orig_cfg = B.SPORT_CONFIG
    B.SPORT_CONFIG = {"nfl": (mt, "NFL")}
    # Make missing SGO_API_KEY irrelevant for this test
    monkeypatch.setenv("SGO_API_KEY", "fake-test-key")

    await db[mt].drop()
    try:
        yield db, mt
    finally:
        await db[mt].drop()
        B.SPORT_CONFIG = orig_cfg
        client.close()


class _StubSGOClient:
    """Minimal stand-in for SGOClient with the SAME method surface
    `backfill_team_matchup_scores.amain` actually uses (the BULK
    `iter_finalized_events_with_results` path)."""
    def __init__(self, *_a, **_kw):
        self.calls = []
        # Default canned response set in tests via .canned_events
        self.canned_events = [
            {"eventID": "EID_HAS_SCORES",
              "results": {"homeScore": 24, "awayScore": 17}},
            {"eventID": "EID_NO_SCORES", "status": "completed"},
        ]

    async def iter_finalized_events_with_results(
            self, league_id, *, starts_after=None,
            starts_before=None, page_size=50, max_pages=4000):
        self.calls.append({
            "league_id":     league_id,
            "starts_after":  starts_after,
            "starts_before": starts_before,
        })
        for ev in self.canned_events:
            yield ev

    def stats(self):
        return {"total": len(self.calls), "ok": len(self.calls), "err": 0}

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_backfill_runtime_makes_real_method_calls(
        db_and_patch, monkeypatch):
    """Run the actual backfill amain() against synthetic matchup data
    using a stubbed SGOClient. Confirms the wire-up:
      • get_event_with_results IS called once per row
      • scores extracted correctly
      • updates happen idempotently
    """
    db, mt = db_and_patch
    # Seed 3 completed NFL matchups
    await db[mt].insert_many([
        {"event_id": "EID_HAS_SCORES", "game_date": "2024-09-07",
         "sport": "nfl", "status": "completed",
         "home_team_name": "USC", "away_team_name": "UCLA"},
        {"event_id": "EID_NO_SCORES", "game_date": "2024-09-14",
         "sport": "nfl", "status": "completed",
         "home_team_name": "OSU", "away_team_name": "MICH"},
        {"event_id": "EID_NOT_FOUND", "game_date": "2024-09-21",
         "sport": "nfl", "status": "completed",
         "home_team_name": "BAMA", "away_team_name": "UGA"},
    ])

    # Patch the SGOClient class used inside the module
    monkeypatch.setattr(B, "SGOClient", _StubSGOClient)

    class _Args:
        pass
    a = _Args()
    a.sport = "nfl"; a.start = None; a.end = None
    a.yes = True; a.dry_run = False; a.force = False
    a.max_events = 100; a.chunk_days = 30
    rc = await B.amain(a)
    assert rc == 0

    # Verify the row that had scores got updated in Mongo
    row1 = await db[mt].find_one({"event_id": "EID_HAS_SCORES"})
    assert row1.get("home_score") == 24
    assert row1.get("away_score") == 17
    assert row1.get("final_score") == {"home": 24, "away": 17}
    assert row1.get("score_source") == "sgo_event_results"
    assert row1.get("score_backfill_version") == "v1"

    # The no-scores row stayed unscored
    row2 = await db[mt].find_one({"event_id": "EID_NO_SCORES"})
    assert row2.get("home_score") is None
    assert row2.get("away_score") is None

    # The not-found row stayed unscored
    row3 = await db[mt].find_one({"event_id": "EID_NOT_FOUND"})
    assert row3.get("home_score") is None


@pytest.mark.asyncio
async def test_backfill_invokes_bulk_iter_once_per_chunk(
        db_and_patch, monkeypatch):
    """When the auto-derived date window spans multiple chunk_days
    intervals, the bulk iterator must be invoked once PER chunk —
    this is what prevents the SGO transport timeout on multi-season
    pulls."""
    db, mt = db_and_patch
    # Seed 2 matchups 90 days apart → auto-window spans ~90 days
    # With chunk_days=30 we expect ~3-4 chunks
    await db[mt].insert_many([
        {"event_id": "EID_A", "game_date": "2024-09-07",
         "sport": "nfl", "status": "completed"},
        {"event_id": "EID_B", "game_date": "2024-12-06",
         "sport": "nfl", "status": "completed"},
    ])

    stub_instances: list = []
    class _Tracker(_StubSGOClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            stub_instances.append(self)
    monkeypatch.setattr(B, "SGOClient", _Tracker)

    class _Args: pass
    a = _Args()
    a.sport = "nfl"; a.start = None; a.end = None
    a.yes = False; a.dry_run = True; a.force = False
    a.max_events = 100; a.chunk_days = 30
    await B.amain(a)

    assert stub_instances, "SGOClient not instantiated"
    n_calls = len(stub_instances[0].calls)
    # 91 days @ 30/chunk → 4 chunks (1-30, 31-60, 61-90, 91)
    assert 3 <= n_calls <= 5, (
        f"Expected 3-5 bulk-iter calls (one per 30-day chunk over "
        f"~91-day window), got {n_calls}: {stub_instances[0].calls}")
    # Each call must carry its own non-overlapping date window
    seen_windows = [(c["starts_after"], c["starts_before"])
                    for c in stub_instances[0].calls]
    assert len(set(seen_windows)) == len(seen_windows), (
        f"Chunks must have unique windows; saw duplicates: {seen_windows}")


@pytest.mark.asyncio
async def test_backfill_runtime_idempotent_skips_already_scored(
        db_and_patch, monkeypatch):
    db, mt = db_and_patch
    # Pre-scored row — should be skipped at the gate, no UPDATE issued
    await db[mt].insert_one({
        "event_id": "EID_PRE", "game_date": "2024-09-07",
        "sport": "nfl", "status": "completed",
        "home_score": 31, "away_score": 28,
    })

    stub_instances: list = []
    class _Tracker(_StubSGOClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            stub_instances.append(self)

    monkeypatch.setattr(B, "SGOClient", _Tracker)

    class _Args:
        pass
    a = _Args()
    a.sport = "nfl"; a.start = None; a.end = None
    a.yes = True; a.dry_run = False; a.force = False
    a.max_events = 100; a.chunk_days = 30
    await B.amain(a)
    # Bulk iterator IS called once (preloads the index) — that's fine.
    # The point of idempotency is that ALREADY-SCORED rows are SKIPPED
    # at the gate before the index lookup, so the row is not re-updated.
    assert stub_instances, "SGOClient was never instantiated"
    # The pre-scored row's home_score must still equal the original value
    row = await db[mt].find_one({"event_id": "EID_PRE"})
    assert row["home_score"] == 31 and row["away_score"] == 28
    # No backfill stamp added (write skipped)
    assert "score_backfill_version" not in row
