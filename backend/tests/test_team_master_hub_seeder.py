"""
Phase 1.A.1 — Team Master Hub seeder + audit tests.

Pins the contract on:
  - pure transform (`build_upsert_ops`)
  - real seeder run + idempotency
  - audit shape + content
  - index spec exists post-seed
  - dry-run never writes
  - missing-sgo / inactive surfaces
  - duplicate detection (informational — uniqueness index also catches it)

Tests use a real local MongoDB instance on a dedicated test DB name so
the pod's main `pick_vision` collections are never touched.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub import (  # noqa: E402
    COLLECTION_NAME,
    SEED_PATH,
    audit_team_master_hub,
    build_upsert_ops,
    ensure_indexes,
    load_seed_doc,
    seed_and_audit,
    seed_team_master_hub,
)


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    """Real Motor connection to a throw-away DB name, dropped after."""
    mongo_url = os.environ["MONGO_URL"]
    db_name = f"team_master_hub_test_{uuid.uuid4().hex[:10]}"
    client = AsyncIOMotorClient(mongo_url)
    try:
        yield client[db_name]
    finally:
        await client.drop_database(db_name)
        client.close()


# ── 1. Pure transform — no DB ────────────────────────────────────────
def test_build_upsert_ops_pure_transform() -> None:
    seed = load_seed_doc(SEED_PATH)
    ops = build_upsert_ops(seed)
    assert len(ops) == len(seed["teams"]) == 92

    # Every op is keyed by team_id and is an upsert
    seen_team_ids = set()
    for op in ops:
        # pymongo's UpdateOne stores the filter on `_filter`, the
        # update payload on `_doc`, and the upsert flag on `_upsert`.
        flt = op._filter
        update = op._doc
        upsert = op._upsert

        # Filter keyed by team_id, upsert=True
        assert "team_id" in flt
        assert upsert is True
        # Update doc carries the active + seed_version flags injected
        # by build_upsert_ops under `$set`, and `seeded_at` under
        # `$setOnInsert` (so re-runs don't drift the timestamp).
        payload = update.get("$set", {})
        provenance = update.get("$setOnInsert", {})
        assert payload["active"] is True
        assert payload["seed_version"] == seed["seed_version"]
        assert "seeded_at" in provenance
        assert "seeded_at" not in payload, (
            "seeded_at must live under $setOnInsert so idempotent "
            "re-runs produce modified_count=0"
        )
        # team_id matches between filter and payload
        assert payload["team_id"] == flt["team_id"]
        seen_team_ids.add(payload["team_id"])
    assert len(seen_team_ids) == 92, "team_ids must be unique in ops list"


def test_build_upsert_ops_does_not_mutate_input() -> None:
    seed = load_seed_doc(SEED_PATH)
    # Snapshot the first team before transform
    original_first = dict(seed["teams"][0])
    _ = build_upsert_ops(seed)
    assert seed["teams"][0] == original_first, (
        "build_upsert_ops must not mutate the input seed dict"
    )


# ── 2. Full seed flow against a real DB ───────────────────────────────
@pytest.mark.asyncio
async def test_seed_team_master_hub_writes_all_teams(db) -> None:
    result = await seed_team_master_hub(db)
    assert result["ok"] is True
    assert result["n_upserts"] == 92
    # First run: upserted == 92 (no docs existed)
    assert result["upserted"] == 92
    assert result["matched"] == 0

    n = await db[COLLECTION_NAME].count_documents({})
    assert n == 92


@pytest.mark.asyncio
async def test_seeder_is_idempotent(db) -> None:
    first = await seed_team_master_hub(db)
    assert first["upserted"] == 92

    # Second run with the same seed — every doc matches, none inserted,
    # and (because seeded_at lives under $setOnInsert) NONE modified.
    second = await seed_team_master_hub(db)
    assert second["n_upserts"] == 92
    assert second["upserted"] == 0
    assert second["matched"] == 92
    assert second["modified"] == 0, (
        "true idempotency: re-running an unchanged seed must produce "
        "modified_count=0 (seeded_at lives under $setOnInsert)"
    )

    n = await db[COLLECTION_NAME].count_documents({})
    assert n == 92, "idempotent re-run must not create duplicate rows"

    # seeded_at on docs is preserved from the FIRST run
    sample = await db[COLLECTION_NAME].find_one({"team_id": "mlb_nyy"})
    assert sample is not None
    assert "seeded_at" in sample


# ── 3. Indexes ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ensure_indexes_creates_the_four_required_indexes(db) -> None:
    await ensure_indexes(db)
    info = await db[COLLECTION_NAME].index_information()
    names = set(info.keys())
    for required in (
        "ix_team_id_unique",
        "ix_sport",
        "ix_active",
        "ix_external_ids_sgo_sparse",
    ):
        assert required in names, f"missing index: {required}"
    # team_id index must be unique
    assert info["ix_team_id_unique"].get("unique") is True
    # sgo index must be sparse
    assert info["ix_external_ids_sgo_sparse"].get("sparse") is True


@pytest.mark.asyncio
async def test_unique_index_blocks_duplicate_team_id(db) -> None:
    await seed_team_master_hub(db)
    # Direct insert with a colliding team_id must fail
    from pymongo.errors import DuplicateKeyError
    with pytest.raises(DuplicateKeyError):
        await db[COLLECTION_NAME].insert_one({"team_id": "mlb_nyy",
                                               "sport": "mlb"})


# ── 4. Audit shape + values ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_audit_against_empty_collection(db) -> None:
    report = await audit_team_master_hub(db)
    assert report["ok"] is True
    assert report["total"] == 0
    assert report["by_sport"] == {}
    assert report["missing_sgo"] == []
    assert report["duplicates"] == []
    assert report["inactive"] == []
    # No indexes yet (collection doesn't exist)
    assert report["indexes_present"] == []


@pytest.mark.asyncio
async def test_audit_after_fresh_seed(db) -> None:
    await seed_team_master_hub(db)
    report = await audit_team_master_hub(db)

    assert report["total"] == 92
    assert report["by_sport"] == {"mlb": 30, "nba": 30, "nfl": 32}
    # Every team currently has external_ids.sgo = None → all "missing"
    assert report["missing_sgo_count"] == 92
    # None inactive (active=True is the seeder default)
    assert report["inactive"] == []
    # No duplicates
    assert report["duplicates"] == []
    # All four indexes plus the implicit _id_ index
    for required in (
        "_id_",
        "ix_team_id_unique",
        "ix_sport",
        "ix_active",
        "ix_external_ids_sgo_sparse",
    ):
        assert required in report["indexes_present"]


@pytest.mark.asyncio
async def test_audit_picks_up_external_ids_sgo(db) -> None:
    await seed_team_master_hub(db)
    # Patch one team with a real SGO id
    await db[COLLECTION_NAME].update_one(
        {"team_id": "mlb_nyy"},
        {"$set": {"external_ids.sgo": "NYY"}},
    )
    report = await audit_team_master_hub(db)
    assert report["missing_sgo_count"] == 91
    assert "mlb_nyy" not in report["missing_sgo"]


@pytest.mark.asyncio
async def test_audit_picks_up_inactive(db) -> None:
    await seed_team_master_hub(db)
    await db[COLLECTION_NAME].update_one(
        {"team_id": "mlb_oak"},
        {"$set": {"active": False}},
    )
    report = await audit_team_master_hub(db)
    assert report["inactive"] == ["mlb_oak"]
    assert report["inactive_count"] == 1


# ── 5. Combined runner — used by the admin endpoint + CLI ────────────
@pytest.mark.asyncio
async def test_seed_and_audit_dry_run_writes_nothing(db) -> None:
    result = await seed_and_audit(db, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "seed_preview" in result
    assert result["seed_preview"]["n_ops_would_run"] == 92
    # Collection is still empty
    n = await db[COLLECTION_NAME].count_documents({})
    assert n == 0
    assert result["audit"]["total"] == 0


@pytest.mark.asyncio
async def test_seed_and_audit_real_run_returns_both_blocks(db) -> None:
    result = await seed_and_audit(db, dry_run=False)
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["seed"]["upserted"] == 92
    assert result["audit"]["total"] == 92
    assert result["audit"]["by_sport"] == {"mlb": 30, "nba": 30, "nfl": 32}
