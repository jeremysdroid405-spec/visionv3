"""
Phase 1.A.2 — Team collections + worker skeletons tests.

Pins the §1.2 index spec (especially the multi-book invariant in the
unique key — lesson from `test_mirror_multi_book.py`), the worker
skeleton contracts, the fail-closed dispatch guard, and the
"no player-side mutation" cross-pipeline invariant.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    COMPOUND_UNIQUE_KEYS,
    TEAM_COLLECTIONS,
    collections_status,
    ensure_team_collections,
)
from workers.team import (  # noqa: E402
    SUPPORTED_SPORTS,
    TeamIngestDisabled,
    TeamMatchupsIngestWorker,
    TeamOddsIngestWorker,
    TeamOutcomesGrader,
    dispatch_guard_ok,
    requires_sgo_key,
)
from workers.team.base import TeamWorkerBase  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    """Throw-away Motor DB per test."""
    mongo_url = os.environ["MONGO_URL"]
    db_name = f"team_collections_test_{uuid.uuid4().hex[:10]}"
    client = AsyncIOMotorClient(mongo_url)
    try:
        yield client[db_name]
    finally:
        await client.drop_database(db_name)
        client.close()


@pytest.fixture
def guard_closed(monkeypatch):
    """Force the dispatch guard fail-closed for every worker test."""
    monkeypatch.delenv("SGO_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)


@pytest.fixture
def guard_open(monkeypatch):
    """Force the dispatch guard open (both env vars set)."""
    monkeypatch.setenv("SGO_API_KEY", "test_key_not_for_real_dispatch")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")


# ── §1.2 INDEX SPEC TESTS ────────────────────────────────────────────
def test_ten_team_collections_declared() -> None:
    names = [n for n, _ in TEAM_COLLECTIONS]
    assert set(names) == {
        "team_live_props", "team_historical_props",
        "team_prop_outcomes", "team_matchups", "team_injuries",
        "team_context", "team_features", "team_projections",
        "team_prop_scores", "team_replay_outputs",
    }
    assert len(names) == 10, "exactly 10 team-side collections (§1.1)"


def test_no_player_side_collection_named() -> None:
    """Defence-in-depth: the bootstrap must never name a player-side
    `sgo_*` / `mlb_*` / `nba_*` / `nfl_*` / `pp_*` collection.
    """
    forbidden = ("sgo_", "mlb_", "nba_", "nfl_", "pp_")
    for name, _ in TEAM_COLLECTIONS:
        assert not name.startswith(forbidden), (
            f"forbidden collection name in team bootstrap: {name}"
        )


@pytest.mark.parametrize("coll", [
    "team_live_props", "team_historical_props",
    "team_prop_scores", "team_replay_outputs",
])
def test_multi_book_invariant_book_in_unique_key(coll: str) -> None:
    """§14.5 + multi-book lesson: every multi-book-aware collection
    MUST carry `book` in the compound unique key, else the historical
    mirror collapse from the player side recurs.
    """
    key = COMPOUND_UNIQUE_KEYS[coll]
    assert "book" in key, (
        f"{coll}: 'book' MUST appear in the compound unique key — "
        "lesson from test_mirror_multi_book.py."
    )


@pytest.mark.parametrize("coll", [
    "team_features", "team_projections",
])
def test_engineered_collection_unique_key_includes_version(coll) -> None:
    """A/B model versions must be allowed to coexist for the same
    (event, team, market) — hence the version field is part of the
    unique key."""
    key = COMPOUND_UNIQUE_KEYS[coll]
    version_field = (
        "feature_set_version" if coll == "team_features"
        else "model_version"
    )
    assert version_field in key, (
        f"{coll}: '{version_field}' must be in unique key to allow "
        "concurrent model/feature versions"
    )


# ── Live-DB index creation ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_ensure_team_collections_creates_all(db) -> None:
    result = await ensure_team_collections(db)
    assert result["ok"] is True
    assert result["n_collections"] == 10

    for entry in result["collections"]:
        name = entry["name"]
        # Every declared index name must be present
        spec_names = {idx.document["name"] for _, models in TEAM_COLLECTIONS
                       for idx in models
                       if dict(TEAM_COLLECTIONS)[name] is models}
        # Simpler: re-derive from TEAM_COLLECTIONS directly
        expected = {idx.document["name"]
                     for n, models in TEAM_COLLECTIONS for idx in models
                     if n == name}
        for ix in expected:
            assert ix in entry["indexes_after"], (
                f"{name}: missing expected index {ix} "
                f"(have {entry['indexes_after']})"
            )
        # Empty: no documents at all in this slice
        assert entry["doc_count"] == 0


@pytest.mark.asyncio
async def test_ensure_team_collections_is_idempotent(db) -> None:
    first = await ensure_team_collections(db)
    second = await ensure_team_collections(db)

    # Second run created NO new indexes
    for entry in second["collections"]:
        assert entry["indexes_created"] == [], (
            f"{entry['name']}: re-run created indexes "
            f"{entry['indexes_created']} — must be idempotent"
        )
    # And the per-collection index lists match between runs
    a = {c["name"]: c["indexes_after"] for c in first["collections"]}
    b = {c["name"]: c["indexes_after"] for c in second["collections"]}
    assert a == b


@pytest.mark.asyncio
async def test_unique_index_blocks_duplicate_multi_book_row(db) -> None:
    """End-to-end pin: inserting two rows that disagree ONLY on `book`
    must both succeed; inserting two rows IDENTICAL on the full unique
    key must fail with DuplicateKeyError.
    """
    from pymongo.errors import DuplicateKeyError

    await ensure_team_collections(db)
    base = {
        "event_id":     "evt1", "team_id": "mlb_nyy",
        "market":       "team_total_runs", "line": 4.5, "side": "OVER",
        "snapshot_iso": "2026-06-02T12:00:00Z",
        "sport":        "mlb",  "game_date": "2026-06-02",
    }
    await db["team_live_props"].insert_one(
        {**base, "book": "draftkings", "odds": -110})
    # Different book → allowed (multi-book preservation)
    await db["team_live_props"].insert_one(
        {**base, "book": "fanduel",    "odds": -105})
    # Same book → duplicate
    with pytest.raises(DuplicateKeyError):
        await db["team_live_props"].insert_one(
            {**base, "book": "draftkings", "odds": -115})


@pytest.mark.asyncio
async def test_collections_status_reflects_reality(db) -> None:
    # Before ensure: none present
    pre = await collections_status(db)
    assert pre["n_collections"] == 10
    assert all(c["present"] is False for c in pre["collections"])

    await ensure_team_collections(db)
    post = await collections_status(db)
    assert all(c["present"] for c in post["collections"])


# ── WORKER SKELETON TESTS ────────────────────────────────────────────
@pytest.mark.parametrize("cls", [TeamOddsIngestWorker,
                                  TeamOutcomesGrader,
                                  TeamMatchupsIngestWorker])
def test_worker_requires_sgo_key_is_true(cls) -> None:
    assert cls.requires_sgo_key() is True
    assert requires_sgo_key() is True


@pytest.mark.parametrize("cls", [TeamOddsIngestWorker,
                                  TeamOutcomesGrader,
                                  TeamMatchupsIngestWorker])
def test_worker_rejects_unknown_sport(cls) -> None:
    with pytest.raises(ValueError, match="unsupported sport"):
        cls("formula1")


def test_supported_sports_set() -> None:
    assert SUPPORTED_SPORTS == frozenset({"mlb", "nba", "nfl"})


def test_dispatch_guard_closed_when_both_vars_missing(guard_closed) -> None:
    ok, reasons = dispatch_guard_ok()
    assert ok is False
    assert any("SGO_API_KEY" in r for r in reasons)
    assert any("TEAM_INGEST_ENABLED" in r for r in reasons)


def test_dispatch_guard_closed_when_only_key_present(monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "x")
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    ok, reasons = dispatch_guard_ok()
    assert ok is False
    assert any("TEAM_INGEST_ENABLED" in r for r in reasons)
    assert not any("SGO_API_KEY" in r for r in reasons)


def test_dispatch_guard_closed_when_only_flag_present(monkeypatch) -> None:
    monkeypatch.delenv("SGO_API_KEY", raising=False)
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    ok, reasons = dispatch_guard_ok()
    assert ok is False
    assert any("SGO_API_KEY" in r for r in reasons)


def test_dispatch_guard_open_when_both_vars_set(guard_open) -> None:
    ok, reasons = dispatch_guard_ok()
    assert ok is True
    assert reasons == []


def test_worker_base_require_dispatch_raises_when_closed(
    guard_closed,
) -> None:
    class _ConcreteWorker(TeamWorkerBase):
        WORKER_KEY = "test_concrete"
    inst = _ConcreteWorker("mlb")
    with pytest.raises(TeamIngestDisabled,
                        match="team ingest is disabled"):
        inst._require_dispatch()


def test_worker_base_require_dispatch_passes_when_open(
    guard_open,
) -> None:
    class _ConcreteWorker(TeamWorkerBase):
        WORKER_KEY = "test_concrete"
    inst = _ConcreteWorker("mlb")
    inst._require_dispatch()  # must not raise


# ── PROBE / DRY-RUN: ZERO NETWORK CALLS ──────────────────────────────
def _block_network(monkeypatch):
    """Aggressively replace every common HTTP entrypoint with a
    raising stub. If a probe / dry-run accidentally tries to hit
    the network the test fails loudly.
    """
    import httpx
    import urllib.request

    def _raise(*a, **kw):  # noqa: ARG001
        raise AssertionError(
            "Phase 1.A.2 probe/dry-run made an UNEXPECTED network call"
        )

    monkeypatch.setattr(httpx, "get",  _raise, raising=False)
    monkeypatch.setattr(httpx, "post", _raise, raising=False)
    monkeypatch.setattr(httpx.Client, "request", _raise, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "request",
                          _raise, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _raise, raising=False)


@pytest.mark.parametrize("worker_cls,sport,extra", [
    (TeamOddsIngestWorker,    "mlb", {}),
    (TeamOddsIngestWorker,    "nba", {}),
    (TeamOddsIngestWorker,    "nfl", {}),
    (TeamOutcomesGrader,      "mlb", {}),
    (TeamOutcomesGrader,      "nba", {}),
    (TeamOutcomesGrader,      "nfl", {}),
    (TeamMatchupsIngestWorker, "mlb", {}),
    (TeamMatchupsIngestWorker, "nba", {}),
    (TeamMatchupsIngestWorker, "nfl", {}),
])
def test_probe_makes_zero_network_calls(
    monkeypatch, guard_closed, worker_cls, sport, extra,
) -> None:
    _block_network(monkeypatch)
    inst = worker_cls(sport)
    out = inst.probe()
    assert out["worker"] == worker_cls.WORKER_KEY
    assert out["sport"] == sport
    assert out["requires_sgo_key"] is True
    assert out["dispatch_allowed"] is False  # guard closed


def test_odds_dry_run_promote_no_writes(monkeypatch, guard_closed) -> None:
    _block_network(monkeypatch)
    inst = TeamOddsIngestWorker("mlb")
    out = inst.dry_run_promote(["evt_a", "evt_b"])
    assert out["mode"] == "dry_run"
    assert out["would_read"]  == "team_live_props"
    assert out["would_write"] == "team_historical_props"
    assert out["n_candidate_events"] == 2


def test_outcomes_dry_run_grade_no_writes(monkeypatch, guard_closed) -> None:
    _block_network(monkeypatch)
    inst = TeamOutcomesGrader("nba")
    out = inst.dry_run_grade(["evt_x"])
    assert out["mode"] == "dry_run"
    assert out["would_write"] == "team_prop_outcomes"


def test_matchups_dry_run_ingest_no_writes(
    monkeypatch, guard_closed,
) -> None:
    _block_network(monkeypatch)
    inst = TeamMatchupsIngestWorker("nfl")
    out = inst.dry_run_ingest("2026-09-01", "2026-09-07")
    assert out["mode"] == "dry_run"
    assert out["would_write"] == "team_matchups"


# ── _resolve_team_id read-only correctness ───────────────────────────
@pytest.mark.asyncio
async def test_outcomes_grader_resolve_team_id_uses_master_hub(
    db, monkeypatch, guard_closed,
) -> None:
    """The grader's `_resolve_team_id` lookup must:
      1. Read from team_master_hub only (no writes, no SGO call).
      2. Return the canonical team_id when display_names matches.
      3. Return None when no match.
    """
    _block_network(monkeypatch)
    # Seed a single team
    await db["team_master_hub"].insert_one({
        "team_id": "mlb_nyy", "sport": "mlb",
        "display_names": {"full":   "New York Yankees",
                            "short":  "Yankees",
                            "abbrev": "NYY",
                            "market": "New York"},
    })

    grader = TeamOutcomesGrader("mlb")
    assert await grader._resolve_team_id(db,
        team_name="New York Yankees") == "mlb_nyy"
    assert await grader._resolve_team_id(db,
        team_name="Yankees") == "mlb_nyy"
    assert await grader._resolve_team_id(db,
        team_name="NYY") == "mlb_nyy"
    # Different sport — same name pattern doesn't leak
    assert await grader._resolve_team_id(db,
        team_name="Knicks") is None
    # Empty string
    assert await grader._resolve_team_id(db, team_name="") is None


# ── NO PLAYER-SIDE MUTATION ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_ensure_team_collections_never_touches_player_collections(
    db,
) -> None:
    """Seed a player-style collection with a single doc and run the
    team bootstrap. Player collection must be unchanged afterwards.
    """
    await db["sgo_propvision_full_pipeline_replay"].insert_one(
        {"_sentinel": "do_not_touch", "player_name": "Aaron Judge"})
    await db["sgo_player_stats"].insert_one(
        {"_sentinel": "do_not_touch", "stat": "HR"})

    pre_a = await db["sgo_propvision_full_pipeline_replay"].find_one(
        {"_sentinel": "do_not_touch"})
    pre_b = await db["sgo_player_stats"].find_one(
        {"_sentinel": "do_not_touch"})

    await ensure_team_collections(db)

    post_a = await db["sgo_propvision_full_pipeline_replay"].find_one(
        {"_sentinel": "do_not_touch"})
    post_b = await db["sgo_player_stats"].find_one(
        {"_sentinel": "do_not_touch"})

    assert post_a == pre_a, (
        "ensure_team_collections must NOT modify player-side rows"
    )
    assert post_b == pre_b
    # And those collections must still have their original count
    assert await db["sgo_propvision_full_pipeline_replay"].count_documents({}) == 1
    assert await db["sgo_player_stats"].count_documents({}) == 1
