"""
Phase D2 — `only_canonical_keys` filter regression tests.

Verifies:
  1. Full-sync call (no filter) behaves identically to pre-D2 — backwards
     compatible.
  2. With a subset filter, exactly that subset is processed and written.
  3. Passing a filter with mode="replace" is auto-coerced to "upsert"
     (delta engine's "additive, not replacement" invariant).
  4. Response includes `only_canonical_keys_applied` / `_requested` /
     `_matched` diagnostics.

These tests patch the scoring adapter's `load_live_props` + the
`write_versioned_scores` persistence so the filter path is exercised
without touching live MongoDB.
"""
import asyncio
import types
from unittest.mock import patch, AsyncMock

import pytest

from services.scoring import recompute as recompute_mod


class _FakeCachedBoardColl:
    async def count_documents(self, *a, **kw):
        return 0

    async def find_one(self, *a, **kw):
        return None


class _FakeDB:
    def __getitem__(self, name):
        return _FakeCachedBoardColl()


def _sample_props():
    return [
        {"canonical_key": "nba|e1|Points|10.5|OVER", "player": "A"},
        {"canonical_key": "nba|e2|Points|20.5|OVER", "player": "B"},
        {"canonical_key": "nba|e3|Points|30.5|OVER", "player": "C"},
        {"canonical_key": "nba|e4|Points|40.5|OVER", "player": "D"},
    ]


class _StubAdapter:
    cached_board_collection = "stub_cached_board"

    async def load_live_props(self, db, limit=None):
        return _sample_props()

    def get_sorter(self, db):
        return None

    async def build_context(self, db, prop, config):
        # Return a context stub that recompute_sport treats as "skip".
        return None

    def enrich_score_doc(self, raw, ctx):
        return {}

    def canonical_key_from_raw(self, raw_prop):
        # D1 contract: return persisted canonical_key when present.
        ck = raw_prop.get("canonical_key")
        return ck if isinstance(ck, str) and ck else None


@pytest.fixture
def stub_env(monkeypatch):
    monkeypatch.setattr(
        recompute_mod, "get_scoring_adapter", lambda sport: _StubAdapter()
    )
    async def _fake_write(db, sport, score_docs, version_tag, dry_run, mode):
        return {
            "written": len(score_docs),
            "replaced": 0,
            "collection": f"{sport}_prop_scores",
            "mode": mode,
        }
    monkeypatch.setattr(recompute_mod, "write_versioned_scores", _fake_write)
    yield


@pytest.mark.asyncio
async def test_recompute_sport_no_filter_is_backwards_compatible(stub_env):
    """Without `only_canonical_keys`, behaviour is identical to pre-D2."""
    res = await recompute_mod.recompute_sport(
        db=_FakeDB(), sport="nba", version_tag="test-tag",
    )
    assert res["only_canonical_keys_applied"] is False
    assert res["only_canonical_keys_requested"] is None
    assert res["only_canonical_keys_matched"] is None
    # All 4 stub props were "processed" (skipped via ctx=None is fine —
    # the test only asserts the pre-scoring filter math, not the writes).
    assert res["processed"] == 4


@pytest.mark.asyncio
async def test_recompute_sport_filter_restricts_processed_set(stub_env):
    subset = {
        "nba|e1|Points|10.5|OVER",
        "nba|e3|Points|30.5|OVER",
    }
    res = await recompute_mod.recompute_sport(
        db=_FakeDB(), sport="nba", version_tag="test-tag",
        only_canonical_keys=subset,
        write_mode="upsert",
    )
    assert res["only_canonical_keys_applied"] is True
    assert res["only_canonical_keys_requested"] == 2
    assert res["only_canonical_keys_matched"] == 2
    assert res["processed"] == 2


@pytest.mark.asyncio
async def test_recompute_sport_filter_forces_upsert(stub_env, caplog):
    """Filter + write_mode='replace' must be coerced to 'upsert'."""
    subset = {"nba|e2|Points|20.5|OVER"}
    with caplog.at_level("WARNING"):
        res = await recompute_mod.recompute_sport(
            db=_FakeDB(), sport="nba", version_tag="test-tag",
            only_canonical_keys=subset,
            write_mode="replace",
        )
    assert res["only_canonical_keys_applied"] is True
    assert res["only_canonical_keys_matched"] == 1
    # Validate warning emitted about mode coercion.
    assert any("forcing write_mode='upsert'" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_recompute_sport_filter_empty_set(stub_env):
    """Empty filter set means zero props — NOT a bypass to full rescore."""
    res = await recompute_mod.recompute_sport(
        db=_FakeDB(), sport="nba", version_tag="test-tag",
        only_canonical_keys=set(),
        write_mode="upsert",
    )
    assert res["only_canonical_keys_applied"] is True
    assert res["only_canonical_keys_requested"] == 0
    assert res["only_canonical_keys_matched"] == 0
    assert res["processed"] == 0
