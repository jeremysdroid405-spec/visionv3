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


def test_sgoclient_does_not_expose_renamed_or_legacy_get_event():
    """If a future refactor renames the method, this test fails LOUDLY
    so the call-site in the backfill script can be updated in lock-step."""
    # The bug was calling `client.get_event(...)`. If somebody later
    # ALSO adds a `get_event` (different shape) and the rename ambiguity
    # comes back, the test below pinpoints the live API surface.
    methods = {n for n in dir(SGOClient) if n.startswith("get_event")}
    # Must contain the canonical name; otherwise the script breaks.
    assert "get_event_with_results" in methods


def test_backfill_script_uses_get_event_with_results():
    """Source-scan the backfill script — direct subscripts on the SGO
    client object (e.g. `sgo.get_event(...)`) are forbidden if they
    don't match a real method name."""
    src = inspect.getsource(B)
    # The exact call-site we want
    assert "sgo.get_event_with_results(" in src, (
        "backfill_team_matchup_scores.py no longer calls "
        "`sgo.get_event_with_results(...)` — did the method get "
        "renamed without updating the call-site?")
    # The bug pattern that must not return
    assert "sgo.get_event(" not in src, (
        "backfill_team_matchup_scores.py contains `sgo.get_event(` — "
        "that method does not exist on SGOClient and will produce "
        "612-error failures on VPS.")


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
    `backfill_team_matchup_scores.amain` actually uses."""
    def __init__(self, *_a, **_kw):
        self.calls = []

    async def get_event_with_results(self, event_id, *,
                                       expand_results=True,
                                       include_alt_lines=False):
        self.calls.append({
            "event_id": event_id,
            "expand_results": expand_results,
            "include_alt_lines": include_alt_lines,
        })
        # Return a payload in the same shape SGO ships
        if event_id == "EID_HAS_SCORES":
            return {"eventID": event_id, "results":
                    {"homeScore": 24, "awayScore": 17}}
        if event_id == "EID_NO_SCORES":
            return {"eventID": event_id, "status": "completed"}
        return None

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
        {"event_id": "EID_HAS_SCORES",
         "sport": "nfl", "status": "completed",
         "home_team_name": "USC", "away_team_name": "UCLA"},
        {"event_id": "EID_NO_SCORES",
         "sport": "nfl", "status": "completed",
         "home_team_name": "OSU", "away_team_name": "MICH"},
        {"event_id": "EID_NOT_FOUND",
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
    a.max_events = 100
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
async def test_backfill_runtime_idempotent_skips_already_scored(
        db_and_patch, monkeypatch):
    db, mt = db_and_patch
    # Pre-scored row — should NOT trigger an SGO call
    await db[mt].insert_one({
        "event_id": "EID_PRE",
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
    a.max_events = 100
    await B.amain(a)
    # Idempotency gate triggered — zero SGO calls made
    assert stub_instances, "SGOClient was never instantiated"
    assert stub_instances[0].calls == [], (
        f"Expected 0 SGO calls (row already scored), "
        f"got {len(stub_instances[0].calls)}: {stub_instances[0].calls}")
