"""Regression tests for `services.scoring.prop_scores_store.write_versioned_scores`.

WHY THIS EXISTS
---------------
On 2026-04-30, sync_history showed 75 of 76 MLB sync failures (98.7%)
were one root cause: `E11000 duplicate key error` on
`(canonical_key, version_tag)` in `mlb_prop_scores`.

The bug was a race window in `mode=replace`:

    delete_many({"version_tag": tag})
    # ← realtime engine upserts here → violates uniq_canonical_version
    insert_many(new_docs, ordered=False)   # blows up with E11000

Any time the hourly master_sync rebuild overlapped with the realtime
engine's `on_new_props` upsert (which we enabled when we switched
realtime off the shadow tag), the sync failed catastrophically.

THE FIX
-------
Race-safe bulk replace:
  1. `bulk_write([ReplaceOne(..., upsert=True)])` for every prepared doc
     — idempotent under concurrent upserts.
  2. `delete_many({"version_tag": tag, "canonical_key": {"$nin": new_cks}})`
     sweeps stale rows in a single operation, no race window.

WHAT THIS SUITE LOCKS IN
------------------------
INV-1: `mode=replace` is idempotent under concurrent upsert pressure.
       Specifically, if a competing writer inserts a doc with the same
       (canonical_key, version_tag) BETWEEN our stale-sweep and our
       bulk replace, the second writer replaces — no E11000.

INV-2: Stale canonical_keys (in DB but not in the new batch) are
       removed. A reduced slate must shrink the collection.

INV-3: Empty new-batch (`score_docs == []`) wipes the whole tag.
       Preserves the old delete+insert contract.

INV-4: Result dict always has `mode`, `written`, `replaced`, `prepared`,
       `computed_at`. Callers (master_sync, board/engine) rely on
       these keys being present.

INV-5: `mode=upsert` is UNCHANGED — still per-doc `update_one(upsert=True)`.
       The race fix is strictly scoped to `mode=replace`.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from services.scoring.prop_scores_store import write_versioned_scores


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


def _make_score(tag: str, ck_suffix: str, edge: float = 10.0) -> Dict[str, Any]:
    return {
        "canonical_key": f"mlb|evt_{tag}|Test Player {ck_suffix}|Hits|0.5|OVER",
        "player_name": f"Test Player {ck_suffix}",
        "stat_type": "Hits",
        "line": 0.5,
        "recommendation": "OVER",
        "team": "TST",
        "edge_pct": edge,
        "tier": "front_lines",
        "_test_tag": tag,
    }


async def _cleanup(db, tag: str) -> None:
    await db["mlb_prop_scores"].delete_many({"_test_tag": tag})


@pytest.mark.asyncio
async def test_inv1_replace_is_idempotent_under_concurrent_upsert(db):
    """INV-1: a concurrent upsert landing mid-replace must not fail
    the replace with E11000.

    Reproduces the exact pattern that caused 75 of 76 MLB sync
    failures: hourly master_sync (mode=replace) overlapping with
    realtime engine (mode=upsert) on the same version_tag.
    """
    tag = f"test_race_{uuid.uuid4().hex[:8]}"
    version_tag = f"test-tag-{tag}"
    await _cleanup(db, tag)

    try:
        # Seed: 3 docs in the collection under our version_tag.
        initial = [_make_score(tag, f"init_{i}", edge=float(i))
                   for i in range(3)]
        r1 = await write_versioned_scores(
            db=db, sport="mlb", score_docs=initial,
            version_tag=version_tag, mode="replace",
        )
        assert r1["written"] == 3

        # Now simulate the race: fire a replace-rebuild AND a
        # concurrent upsert for an OVERLAPPING key at the same time.
        new_batch = [_make_score(tag, f"init_{i}", edge=float(10 + i))
                     for i in range(3)]
        # Plus one brand-new key the replace knows about.
        new_batch.append(_make_score(tag, "new_extra", edge=99.0))
        # The competing writer is "realtime engine" — upserts one of
        # the same canonical_keys the replace is about to write.
        overlap_doc = _make_score(tag, "init_1", edge=77.0)

        async def do_replace():
            return await write_versioned_scores(
                db=db, sport="mlb", score_docs=new_batch,
                version_tag=version_tag, mode="replace",
            )

        async def do_upsert():
            return await write_versioned_scores(
                db=db, sport="mlb", score_docs=[overlap_doc],
                version_tag=version_tag, mode="upsert",
            )

        # Run a few race permutations — we want to stress-test, not
        # just the happy case.
        for _ in range(5):
            results = await asyncio.gather(
                do_replace(), do_upsert(), return_exceptions=True,
            )
            # NEITHER task may have raised.
            for res in results:
                assert not isinstance(res, Exception), (
                    f"Concurrent replace+upsert raised {type(res).__name__}: "
                    f"{res}. INV-1 broken — E11000 race not fixed."
                )

        # After all races, the collection must be consistent:
        # exactly the 4 keys from the last replace (3 init + 1 extra).
        count = await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        )
        assert count == 4, (
            f"Expected 4 docs after race, got {count}. "
            f"Replace didn't converge — INV-1 broken."
        )
    finally:
        await _cleanup(db, tag)
        await db["mlb_prop_scores"].delete_many({"version_tag": version_tag})


@pytest.mark.asyncio
async def test_inv2_stale_keys_are_swept(db):
    """INV-2: a replace with a REDUCED set of canonical_keys must
    remove the stale ones from the previous write."""
    tag = f"test_sweep_{uuid.uuid4().hex[:8]}"
    version_tag = f"test-tag-{tag}"
    await _cleanup(db, tag)
    try:
        # First write: 5 docs.
        big = [_make_score(tag, f"k_{i}") for i in range(5)]
        await write_versioned_scores(
            db=db, sport="mlb", score_docs=big,
            version_tag=version_tag, mode="replace",
        )
        assert await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        ) == 5

        # Second write: only 2 docs (3 stale).
        small = [_make_score(tag, "k_0"), _make_score(tag, "k_1")]
        r = await write_versioned_scores(
            db=db, sport="mlb", score_docs=small,
            version_tag=version_tag, mode="replace",
        )
        # Result reports the stale sweep count.
        assert r["replaced"] >= 3, (
            f"Expected replaced≥3 stale-swept, got {r['replaced']}"
        )
        final = await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        )
        assert final == 2, (
            f"Expected 2 docs after sweep, got {final}"
        )
    finally:
        await _cleanup(db, tag)
        await db["mlb_prop_scores"].delete_many({"version_tag": version_tag})


@pytest.mark.asyncio
async def test_inv3_empty_batch_wipes_tag(db):
    """INV-3: empty score_docs list wipes everything under the tag.
    Preserves the pre-fix contract callers rely on."""
    tag = f"test_wipe_{uuid.uuid4().hex[:8]}"
    version_tag = f"test-tag-{tag}"
    await _cleanup(db, tag)
    try:
        # Seed then empty-replace.
        await write_versioned_scores(
            db=db, sport="mlb",
            score_docs=[_make_score(tag, "x"), _make_score(tag, "y")],
            version_tag=version_tag, mode="replace",
        )
        assert await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        ) == 2

        r = await write_versioned_scores(
            db=db, sport="mlb", score_docs=[],
            version_tag=version_tag, mode="replace",
        )
        assert r["written"] == 0
        assert await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        ) == 0
    finally:
        await _cleanup(db, tag)
        await db["mlb_prop_scores"].delete_many({"version_tag": version_tag})


@pytest.mark.asyncio
async def test_inv4_result_shape_is_stable(db):
    """INV-4: result dict always has the documented keys."""
    tag = f"test_shape_{uuid.uuid4().hex[:8]}"
    version_tag = f"test-tag-{tag}"
    await _cleanup(db, tag)
    try:
        r = await write_versioned_scores(
            db=db, sport="mlb",
            score_docs=[_make_score(tag, "z")],
            version_tag=version_tag, mode="replace",
        )
        for k in ("mode", "written", "replaced", "prepared",
                  "computed_at", "sport", "version_tag",
                  "collection", "dry_run"):
            assert k in r, f"write_versioned_scores result missing {k!r}"
        assert r["mode"] == "replace"
        assert r["prepared"] >= 1
    finally:
        await _cleanup(db, tag)
        await db["mlb_prop_scores"].delete_many({"version_tag": version_tag})


@pytest.mark.asyncio
async def test_inv5_upsert_mode_unchanged(db):
    """INV-5: mode=upsert still works — the race fix is scoped to
    mode=replace only."""
    tag = f"test_upsert_{uuid.uuid4().hex[:8]}"
    version_tag = f"test-tag-{tag}"
    await _cleanup(db, tag)
    try:
        # First upsert — creates.
        r1 = await write_versioned_scores(
            db=db, sport="mlb",
            score_docs=[_make_score(tag, "u")],
            version_tag=version_tag, mode="upsert",
        )
        assert r1["mode"] == "upsert"
        assert r1["written"] == 1
        # Second upsert of the same key — updates, doesn't duplicate.
        r2 = await write_versioned_scores(
            db=db, sport="mlb",
            score_docs=[_make_score(tag, "u", edge=99.0)],
            version_tag=version_tag, mode="upsert",
        )
        # Either upserted+0, or modified=1, or 0 (no change) — but
        # the total doc count stays at 1.
        count = await db["mlb_prop_scores"].count_documents(
            {"version_tag": version_tag}
        )
        assert count == 1, (
            f"Upsert produced {count} docs for same canonical_key — "
            f"INV-5 broken."
        )
    finally:
        await _cleanup(db, tag)
        await db["mlb_prop_scores"].delete_many({"version_tag": version_tag})
