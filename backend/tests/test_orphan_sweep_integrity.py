"""Regression test — the 9 orphan collections must stay dropped.

WHY THIS EXISTS
---------------
On 2026-04-30 the P0 #6 sweep dropped 9 orphan/archive/backup
collections (861,813 docs, 189MB). None were referenced by runtime
code — they were 8 months of rename-residue.

This test fails if any of those 9 names reappear in:
  (a) the live database as collections with data, OR
  (b) `services/config/collection_names.py` as a `_SPORT_COLLECTIONS`
      key or value (the entry was removed; its reintroduction is
      likely an accidental rollback).

If you have a legitimate new need for one of these names, rename it
— do not reuse the orphan name. That's the cleanest way to prevent
confusion with the pre-sweep residue.

SEE ALSO
--------
- `scripts/sweep_orphan_collections.py` — the one-shot dropper
- `/app/backend/data/snapshots/archives/` — per-collection manifests
- `/app/memory/SYSTEMS_orphan_sweep.md` — design doc
- `/app/memory/CHANGELOG.md` — migration entry
"""
from __future__ import annotations

import os
import pathlib

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


ORPHAN_NAMES = {
    "dg_cached_board_backup",
    "dg_events_cache_backup",
    "dg_live_props_backup",
    "dg_master_roster_backup",
    "dg_odds_cache_backup",
    "line_history_backup",
    "mlb_prop_scores_archive_stale_tags",
    "nba_prop_scores_archive_stale_tags",
    "referee_assignments_backup",
}

ROOT = pathlib.Path(__file__).resolve().parents[1]  # /app/backend


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_orphan_collections_stay_dropped(db):
    """None of the 9 swept collections may reappear in the database
    with ANY documents. An empty collection stub (e.g. an index-only
    shell left by an exploratory script) is also failed — if it's
    unused it should be dropped."""
    present = set(await db.list_collection_names())
    reappeared = ORPHAN_NAMES & present
    if not reappeared:
        return
    # If the name reappeared, let us at least see whether it's been
    # repopulated or is an empty shell.
    detail = []
    for name in sorted(reappeared):
        n = await db[name].estimated_document_count()
        detail.append(f"{name} ({n} docs)")
    pytest.fail(
        "Swept orphan collection(s) reappeared in the database: "
        + ", ".join(detail)
        + ". Either rename to a distinct name, or re-run "
        "`scripts/sweep_orphan_collections.py` to drop again."
    )


def test_orphan_names_not_reintroduced_in_collection_config():
    """`services/config/collection_names.py` must not reintroduce the
    dropped orphan names as `_SPORT_COLLECTIONS` keys or values."""
    from services.config.collection_names import _SPORT_COLLECTIONS

    offenders = []
    for concept, sport_map in _SPORT_COLLECTIONS.items():
        if concept in ORPHAN_NAMES:
            offenders.append(f"concept={concept!r}")
        if isinstance(sport_map, dict):
            for sport, name in sport_map.items():
                if name in ORPHAN_NAMES:
                    offenders.append(f"{concept}[{sport}]={name!r}")
    assert not offenders, (
        "Orphan collection names reintroduced in "
        "services/config/collection_names.py: " + ", ".join(offenders)
    )


def test_manifest_directory_exists():
    """The manifests written by `sweep_orphan_collections.py` are the
    audit trail for the 2026-04-30 drops. Delete the directory and
    you've lost the forensic record.

    This test asserts the directory is present and contains the 9
    expected manifests (at least one per orphan name)."""
    manifest_dir = pathlib.Path(
        "/app/backend/data/snapshots/archives"
    )
    assert manifest_dir.is_dir(), (
        f"Manifest directory missing: {manifest_dir}. "
        f"Restore from git or re-run sweep if recovery needed."
    )
    files = [p.name for p in manifest_dir.glob("*_manifest_*.json")]
    for orphan in ORPHAN_NAMES:
        matching = [f for f in files if f.startswith(f"{orphan}_manifest_")]
        assert matching, (
            f"No manifest file found for swept collection {orphan!r} "
            f"in {manifest_dir}. Audit trail broken."
        )
