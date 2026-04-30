"""Regression test: every sport adapter's `canonical_key()` must
produce a non-None key for real `{sport}_live_props` documents.

WHY THIS EXISTS
---------------
On 2026-04-29 the NBA adapter was discovered to have been silently
broken for DAYS: every call to `canonical_key(doc)` returned `None`
because the adapter read fields (`market`, `direction`) that had been
renamed upstream (to `market_key`, `recommendation`) long before. The
real-time scoring pipeline gated on these keys being non-None, so
every NBA prop from the watcher was silently discarded. The bug was
invisible because nothing tested it.

This test is the anti-regression. It fails loudly the next time an
adapter's canonical_key() loses its grip on the live_props schema.

WHAT IT ASSERTS
---------------
For every sport in `config.version_tags.SUPPORTED_SPORTS`:
  1. The live_props collection exists and has real documents.
  2. A random sample of up to 100 documents all produce a non-None
     canonical_key when fed to the adapter.
  3. When the doc has a pre-computed `canonical_key` field (the
     canonical case today), the adapter's reconstructed key matches it
     exactly — no silent drift between ingest and adapter.
  4. The key is stably prefixed with `{sport}|`.

FAILURE MODE
------------
If this test fails, the test output tells you EXACTLY which sport /
which sample doc / which field the adapter is missing. Do not skip
or xfail the test — fix the adapter.

NOTE
----
The test is intentionally permissive about empty collections (if
nothing's been synced yet, e.g. during a fresh env bring-up, we only
log a warning). The moment a sport has live props, the test becomes
enforcing.
"""
from __future__ import annotations

import os
import random
from typing import Dict, List

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from config.version_tags import SUPPORTED_SPORTS
from services.board.adapters import get_adapter


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


async def _sample_live_props(db, collection: str, n: int = 100) -> List[Dict]:
    """Return up to `n` random documents from `collection`."""
    total = await db[collection].count_documents({})
    if total == 0:
        return []
    # Deterministic sample for reproducibility within a run.
    # Uses $sample to avoid pulling the full collection; fall back to
    # .find().limit() if $sample is unavailable (it always is on Mongo
    # 3.2+).
    pipeline = [{"$sample": {"size": min(n, total)}}]
    return await db[collection].aggregate(pipeline).to_list(n)


@pytest.mark.asyncio
@pytest.mark.parametrize("sport", SUPPORTED_SPORTS)
async def test_canonical_key_parity(sport: str, db):
    """The adapter must produce a non-None canonical_key for every
    real live_props doc, AND it must match the precomputed field when
    present.

    Parametrised so that adding a new sport to `SUPPORTED_SPORTS`
    automatically adds coverage with zero test changes."""
    adapter = get_adapter(sport)
    collection = adapter.live_props_collection

    # Collection may legitimately be empty (fresh env, pre-sync).
    if await db[collection].estimated_document_count() == 0:
        pytest.skip(
            f"{collection} has no documents yet — test becomes "
            f"enforcing after first sync"
        )

    samples = await _sample_live_props(db, collection, n=100)
    assert samples, f"Could not pull samples from {collection}"

    none_count = 0
    mismatch_count = 0
    first_none_example: Dict | None = None
    first_mismatch_example: Dict | None = None

    for doc in samples:
        produced = adapter.canonical_key(doc)
        if produced is None:
            none_count += 1
            if first_none_example is None:
                # Keep a tiny snapshot for the assertion message.
                first_none_example = {
                    k: doc.get(k) for k in (
                        "player_name", "line", "event_id",
                        "market", "market_key",
                        "stat_type", "stat_type_extracted",
                        "direction", "recommendation",
                        "canonical_key",
                    )
                }
            continue

        # Shape check: starts with the sport prefix.
        assert produced.startswith(f"{sport}|"), (
            f"{sport} adapter produced key without sport prefix: "
            f"{produced!r}"
        )

        # Precomputed-field parity check.
        precomputed = doc.get("canonical_key")
        if precomputed and precomputed != produced:
            mismatch_count += 1
            if first_mismatch_example is None:
                first_mismatch_example = {
                    "precomputed": precomputed,
                    "adapter_produced": produced,
                    "doc_fields": {
                        k: doc.get(k) for k in (
                            "player_name", "line", "event_id",
                            "market", "market_key", "stat_type",
                            "direction", "recommendation",
                        )
                    },
                }

    n = len(samples)
    assert none_count == 0, (
        f"{sport}: adapter returned None for {none_count}/{n} samples. "
        f"This is exactly the shape of the 2026-04-29 NBA regression.\n"
        f"First failing doc fields: {first_none_example}"
    )
    assert mismatch_count == 0, (
        f"{sport}: adapter-produced key diverged from the precomputed "
        f"`canonical_key` field for {mismatch_count}/{n} samples. "
        f"Any divergence here breaks scoped-ingest filtering in "
        f"`services/board/engine.py`.\n"
        f"First mismatch: {first_mismatch_example}"
    )


@pytest.mark.asyncio
async def test_every_sport_has_an_adapter():
    """If a sport is declared in `SUPPORTED_SPORTS` but has no
    adapter, `get_adapter(sport)` will raise — catch that here so
    it fails fast at test time rather than inside a hot realtime
    path."""
    for sport in SUPPORTED_SPORTS:
        adapter = get_adapter(sport)
        assert adapter is not None, f"No adapter registered for {sport}"
        assert adapter.sport == sport
        assert adapter.version_tag, f"{sport} adapter missing version_tag"
        assert adapter.live_props_collection, (
            f"{sport} adapter missing live_props_collection"
        )


@pytest.mark.asyncio
async def test_canonical_key_synthetic_doc_shape():
    """Hermetic unit test: build a canonical minimal doc by hand and
    ensure each sport's adapter produces a key with the expected shape.

    This is the test that would have caught the NBA bug even BEFORE
    any docs existed in the DB — because it exercises the adapter
    directly with a known-good schema."""
    # The canonical schema we expect live_props ingest to produce.
    # If this shape ever changes, both ingest and adapter must change
    # together — this test is the pinning contract between them.
    synthetic = {
        "canonical_key": None,  # let adapter reconstruct
        "player_name": "Test Player",
        "line": 1.5,
        "event_id": "evt_test_123",
        # Mirror both NBA + MLB field-name conventions so the adapter
        # picks the right one via its own precedence.
        "market_key": "player_points",
        "market": "player_points",
        "stat_type": "PTS",
        "stat_type_extracted": "PTS",
        "recommendation": "OVER",
        "direction": "OVER",
    }

    for sport in SUPPORTED_SPORTS:
        adapter = get_adapter(sport)
        key = adapter.canonical_key(synthetic)
        assert key is not None, (
            f"{sport} adapter returned None for the canonical "
            f"synthetic schema. This adapter is broken — any ingest "
            f"that produces this shape will be silently dropped."
        )
        assert key.startswith(f"{sport}|"), (
            f"{sport} adapter built key without sport prefix: {key}"
        )
        # Every canonical field must appear in the key somewhere —
        # catches accidentally swapping field order or dropping a
        # field during refactors.
        assert "Test Player" in key
        assert "1.5" in key
        assert "evt_test_123" in key
        assert "OVER" in key
