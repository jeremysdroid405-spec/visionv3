"""
Phase 1.A.3.1 — TeamOddsIngestWorker.run_pass tests.

Tier 1 (unit, pure): normalize_sgo_payload + _apply_book_policy
Tier 2 (Mongo, synthetic): end-to-end run_pass against a real local
Motor DB, no network. The fail-closed dispatch guard is exercised
via monkeypatch.

NO real SGO call is made in any test in this file.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from services.team_master_hub.ingest_policy import (  # noqa: E402
    TeamIngestPolicy,
)
from workers.team._normalize import normalize_sgo_payload  # noqa: E402
from workers.team.team_odds_ingest import (  # noqa: E402
    INGEST_RUNS_COLL,
    LIVE_PROPS_COLL,
    MASTER_HUB_COLL,
    TeamOddsIngestWorker,
    _apply_book_policy,
)


# ── Fixtures ─────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    name = f"team_odds_ingest_test_{uuid.uuid4().hex[:10]}"
    client = AsyncIOMotorClient(mongo_url)
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


@pytest.fixture
def guard_closed(monkeypatch):
    monkeypatch.delenv("SGO_API_KEY",         raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("TEAM_INGEST_LIVE",    raising=False)


@pytest.fixture
def guard_open_with_live(monkeypatch):
    monkeypatch.setenv("SGO_API_KEY", "test_key")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")


@pytest.fixture
def synthetic_payload() -> dict:
    """One event, two teams, three books (one DFS, one blocked, one
    book), single market with OVER + UNDER outcomes.
    """
    return {
        "events": [
            {
                "event_id":      "evt_test_001",
                "commence_time": "2026-06-02T22:00:00Z",
                "home_team":     "New York Yankees",
                "away_team":     "Boston Red Sox",
                "bookmakers": [
                    {"key": "draftkings",
                      "markets": [{
                         "key": "team_total_runs",
                         "team": "New York Yankees",
                         "outcomes": [
                             {"name": "Over",  "point": 4.5,
                              "price": -110},
                             {"name": "Under", "point": 4.5,
                              "price": -110},
                         ]}]},
                    {"key": "prizepicks",   # reference-only
                      "markets": [{
                         "key": "team_total_runs",
                         "team": "New York Yankees",
                         "outcomes": [
                             {"name": "More", "point": 4.5,
                              "price": -119},
                         ]}]},
                    {"key": "fliff",   # BLOCKED
                      "markets": [{
                         "key": "team_total_runs",
                         "team": "New York Yankees",
                         "outcomes": [
                             {"name": "Over", "point": 4.5,
                              "price": -110},
                         ]}]},
                ],
            },
        ],
    }


async def _seed_master_hub(db) -> None:
    await db[MASTER_HUB_COLL].insert_one({
        "team_id": "mlb_nyy", "sport": "mlb",
        "display_names": {"full":   "New York Yankees",
                            "short":  "Yankees",
                            "abbrev": "NYY",
                            "market": "New York"},
    })


# ── Tier 1 — pure normalize ──────────────────────────────────────────
def test_normalize_emits_rows_for_each_outcome(
    synthetic_payload,
) -> None:
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="2026-06-02T22:00:00Z",
        ingested_at=datetime.now(timezone.utc),
    )
    # 3 books × outcomes (DK=2, PP=1, Fliff=1) → 4 rows pre-policy
    assert len(rows) == 4
    assert c["sgo_events"] == 1
    assert c["sgo_outcomes"] == 4
    assert c["rows_emitted"] == 4
    # All rows tagged with the inputs
    for r in rows:
        assert r["sport"] == "mlb"
        assert r["snapshot_iso"] == "2026-06-02T22:00:00Z"
        assert r["home_away"] is None        # 1.A.4 backfill
        assert r["game_date"] == "2026-06-02"
        assert r["market"]    == "team_total_runs"
        assert r["line"]      == 4.5
        assert r["side"] in ("OVER", "UNDER")
        assert r["book"] in ("draftkings", "prizepicks", "fliff")
        assert r["_team_name"] == "New York Yankees"


def test_normalize_drops_market_without_team(synthetic_payload) -> None:
    # Strip the `team` key from one market
    bad_payload = dict(synthetic_payload)
    bad_payload["events"][0]["bookmakers"][0]["markets"][0].pop("team")
    rows, c = normalize_sgo_payload(
        bad_payload,
        sport="mlb",
        snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    # DK book lost both outcomes (no team)
    assert c["dropped_no_team"] == 2
    # PP + Fliff still ingested
    books = {r["book"] for r in rows}
    assert "draftkings" not in books
    assert "prizepicks" in books
    assert "fliff" in books


def test_normalize_drops_bad_side(synthetic_payload) -> None:
    synthetic_payload["events"][0]["bookmakers"][0]["markets"][0][
        "outcomes"][0]["name"] = "Vibes"
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    assert c["dropped_bad_side"] == 1
    # 4 candidate outcomes minus the bad-side one = 3
    assert len(rows) == 3


def test_normalize_drops_bad_line(synthetic_payload) -> None:
    synthetic_payload["events"][0]["bookmakers"][0]["markets"][0][
        "outcomes"][0]["point"] = "not-a-number"
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    assert c["dropped_bad_line"] == 1
    assert len(rows) == 3


def test_normalize_empty_payload() -> None:
    rows, c = normalize_sgo_payload(
        {}, sport="mlb", snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc))
    assert rows == []
    assert c["sgo_events"] == 0


# ── Tier 1 — _apply_book_policy ──────────────────────────────────────
def test_apply_book_policy_drops_blocked_tags_refs(
    synthetic_payload,
) -> None:
    rows, _ = normalize_sgo_payload(
        synthetic_payload, sport="mlb", snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    assert len(rows) == 4
    counters = _apply_book_policy(rows)
    # 1 blocked (fliff), 1 reference_only (prizepicks)
    assert counters["n_blocked"] == 1
    assert counters["n_refs"] == 1
    # Survived books: draftkings (×2 — OVER+UNDER) + prizepicks (×1)
    assert len(rows) == 3
    books = sorted(r["book"] for r in rows)
    assert books == ["draftkings", "draftkings", "prizepicks"]
    # reference_only tagged correctly
    by_book = {r["book"]: r for r in rows}
    assert by_book["prizepicks"]["reference_only"] is True
    # DK row also has reference_only=False explicitly
    dk_rows = [r for r in rows if r["book"] == "draftkings"]
    assert all(r["reference_only"] is False for r in dk_rows)


# ── Tier 2 — run_pass against real Mongo (synthetic payload) ─────────
@pytest.mark.asyncio
async def test_run_pass_dry_run_writes_no_rows_but_writes_audit(
    db, synthetic_payload, guard_closed,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="dry_run",
    )
    assert result["mode_effective"] == "dry_run"
    assert result["dry_run"] is True
    assert result["live_write_allowed"] is False
    assert result["status"] == "dry_run"
    assert result["n_writes"] == 0
    # Normalize + policy stats still surfaced
    assert result["n_rows_normalized"] == 4
    assert result["n_blocked"] == 1   # fliff
    assert result["n_refs"]    == 1   # prizepicks

    # No team_live_props rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # One audit row
    n_audit = await db[INGEST_RUNS_COLL].count_documents({})
    assert n_audit == 1
    audit = await db[INGEST_RUNS_COLL].find_one(
        {"run_id": result["run_id"]}, {"_id": 0})
    assert audit is not None
    assert audit["sport"] == "mlb"
    assert audit["status"] == "dry_run"


@pytest.mark.asyncio
async def test_run_pass_live_mode_with_guard_closed_aborts(
    db, synthetic_payload, guard_closed,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, mode="live")
    assert result["status"] == "guard_closed"
    assert result["mode_effective"] == "dry_run"
    assert result["n_writes"] == 0
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # Audit row still written
    assert await db[INGEST_RUNS_COLL].count_documents({}) == 1


@pytest.mark.asyncio
async def test_run_pass_live_mode_with_guard_open_writes_rows(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    assert result["status"] == "succeeded"
    assert result["live_write_allowed"] is True
    # 4 normalized → 1 blocked → 3 written (2 DK + 1 PP)
    assert result["n_writes"]    == 3
    assert result["n_upserted"]  == 3
    assert result["n_blocked"]   == 1
    assert result["n_refs"]      == 1

    rows = await db[LIVE_PROPS_COLL].find(
        {}, {"_id": 0}).to_list(length=None)
    assert len(rows) == 3
    # Multi-book preservation
    books = sorted(r["book"] for r in rows)
    assert books == ["draftkings", "draftkings", "prizepicks"]
    # fliff was hard-dropped
    assert all(r["book"] != "fliff" for r in rows)
    # reference_only on prizepicks
    pp = next(r for r in rows if r["book"] == "prizepicks")
    assert pp["reference_only"] is True
    # team_id resolved
    assert all(r["team_id"] == "mlb_nyy" for r in rows)
    # home_away null (1.A.4 pre-condition)
    assert all(r["home_away"] is None for r in rows)
    # Sport tag preserved
    assert all(r["sport"] == "mlb" for r in rows)


@pytest.mark.asyncio
async def test_run_pass_idempotent_when_repeated(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    first = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    second = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    assert first["n_upserted"] == 3
    # Re-run with SAME snapshot_iso → all matched, none modified
    # (ingested_at lives under $setOnInsert per design §5)
    assert second["n_writes"]   == 3
    assert second["n_upserted"] == 0
    assert second["n_matched"]  == 3
    assert second["n_modified"] == 0
    # Two distinct audit rows
    assert await db[INGEST_RUNS_COLL].count_documents({}) == 2
    # Still exactly 3 team_live_props rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 3


@pytest.mark.asyncio
async def test_run_pass_different_snapshot_iso_creates_new_rows(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    """Same payload, different snapshot_iso ⇒ new historical row per
    book (intended multi-snapshot preservation).
    """
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t1", mode="live")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t2", mode="live")
    # 3 rows per snapshot × 2 snapshots = 6 rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 6


@pytest.mark.asyncio
async def test_run_pass_unresolved_team_skips_row(
    db, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    # Do NOT seed master_hub → every row unresolved
    payload = {
        "events": [{
            "event_id": "e1", "commence_time": "2026-06-02T22:00:00Z",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [{
                    "key": "team_total_runs",
                    "team": "Unknown Team Name",
                    "outcomes": [
                        {"name": "Over",  "point": 5.5, "price": -110}
                    ]}]}]}],
    }
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, payload, snapshot_iso="t", mode="live")
    assert result["n_unresolved"] == 1
    assert result["n_writes"]     == 0
    assert result["status"] in ("succeeded_empty", "succeeded")
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0


@pytest.mark.asyncio
async def test_run_pass_market_explosion_aborts_before_write(
    db, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    # MLB expected markets = 4. Force ≥ 12 distinct markets (3×).
    bms = []
    fake_markets = [f"team_synth_market_{i}" for i in range(15)]
    for m in fake_markets:
        bms.append({
            "key": "draftkings",
            "markets": [{
                "key": m, "team": "New York Yankees",
                "outcomes": [{"name": "Over", "point": 1.5,
                               "price": -110}]}]})
    payload = {"events": [{
        "event_id": "e_explode",
        "commence_time": "2026-06-02T22:00:00Z",
        "bookmakers": bms,
    }]}
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, payload, snapshot_iso="t", mode="live")
    assert result["status"] == "aborted_explosion"
    assert result["explosion_abort"] is True
    assert result["n_writes"] == 0
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # Audit row still recorded
    n_audit = await db[INGEST_RUNS_COLL].count_documents({})
    assert n_audit == 1


@pytest.mark.asyncio
async def test_run_pass_bad_mode_raises(db, synthetic_payload) -> None:
    worker = TeamOddsIngestWorker("mlb")
    with pytest.raises(ValueError, match="mode"):
        await worker.run_pass(db, synthetic_payload, mode="nope")


@pytest.mark.asyncio
async def test_run_pass_persists_full_policy_snapshot_audit_fields(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    res = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live")
    audit = await db[INGEST_RUNS_COLL].find_one(
        {"run_id": res["run_id"]}, {"_id": 0})
    # Every audit field from the design doc §7 is present
    expected_fields = {
        "run_id", "sport", "worker", "mode_requested",
        "mode_effective", "dry_run", "live_write_allowed",
        "guard_reasons", "started_at", "finished_at", "duration_ms",
        "snapshot_iso", "status", "diagnosis", "n_sgo_events",
        "n_sgo_outcomes", "n_rows_normalized", "n_blocked", "n_refs",
        "n_unresolved", "n_writes", "n_upserted", "n_modified",
        "n_matched", "observed_markets", "expected_markets",
        "explosion_abort", "per_market_counts",
    }
    assert expected_fields.issubset(set(audit.keys())), (
        f"missing audit fields: {expected_fields - set(audit.keys())}"
    )
    assert audit["per_market_counts"]["team_total_runs"] == 3


# ── Player-side isolation ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_pass_never_touches_player_collections(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    # Pre-seed sentinels in two player collections
    await db["sgo_propvision_full_pipeline_replay"].insert_one(
        {"_sentinel": "x"})
    await db["sgo_player_stats"].insert_one({"_sentinel": "y"})

    worker = TeamOddsIngestWorker("mlb")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t", mode="live")

    assert await db["sgo_propvision_full_pipeline_replay"].count_documents({}) == 1
    assert await db["sgo_player_stats"].count_documents({}) == 1
