"""
D3+D4 — DeltaEngine.tick end-to-end (unit-level).

Uses a mongomock / fakedb stub plus the real scoring adapter to verify:
  1. A tick with no dirty props completes cleanly (no-op).
  2. UpstreamLockGateStep short-circuits the chain when the lock is held.
  3. Retired keys are marked inactive on the RT scored doc.
  4. The watermark advances to the tick-start timestamp.

We deliberately skip the `RescoreDirtyPropsStep` scoring-heavy path
here — scoring is covered by the existing recompute tests. These tests
focus on orchestration semantics, lock gating, and retire-path writes.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from services.delta_engine import DeltaEngine
from services.upstream_sync_lock import UpstreamSyncLock


# ---------------------------------------------------------------------------
# Fake Mongo collection — supports the subset of ops our delta path uses.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs, projection=None):
        self._docs = docs
        self._proj = projection or {}

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        doc = dict(self._docs[self._i])
        self._i += 1
        return doc

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return [dict(d) for d in self._docs]


class _FakeColl:
    def __init__(self, initial: List[Dict[str, Any]] | None = None):
        self._docs = list(initial or [])

    # find ---------------------------------------------------------------
    def find(self, query=None, projection=None):
        q = query or {}
        filtered = [d for d in self._docs if _matches(d, q)]
        return _FakeCursor(filtered, projection)

    async def find_one(self, query=None, projection=None):
        q = query or {}
        for d in self._docs:
            if _matches(d, q):
                return dict(d)
        return None

    async def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self._docs if _matches(d, q))

    def aggregate(self, pipeline):
        # Motor returns a cursor synchronously; async-for over it is the
        # async part. We mirror that shape.
        match = next((s["$match"] for s in pipeline if "$match" in s), {})
        group = next((s["$group"] for s in pipeline if "$group" in s), None)
        rows = [d for d in self._docs if _matches(d, match)]
        if not group:
            return _FakeCursor(rows)
        field = group["_id"].lstrip("$")
        buckets: Dict[Any, int] = {}
        for r in rows:
            buckets[r.get(field)] = buckets.get(r.get(field), 0) + 1
        return _FakeCursor([{"_id": k, "n": v} for k, v in buckets.items()])

    async def update_many(self, query, update):
        q = query or {}
        set_doc = (update or {}).get("$set", {})
        matched, modified = 0, 0
        for d in self._docs:
            if _matches(d, q):
                matched += 1
                before = {k: d.get(k) for k in set_doc.keys()}
                d.update(set_doc)
                if any(before[k] != d[k] for k in set_doc.keys()):
                    modified += 1

        class _R:
            matched_count = matched
            modified_count = modified
        return _R()

    async def update_one(self, query, update, upsert=False):
        set_doc = (update or {}).get("$set", {})
        ins_doc = (update or {}).get("$setOnInsert", {})
        for d in self._docs:
            if _matches(d, query):
                d.update(set_doc)

                class _R:
                    matched_count = 1
                    modified_count = 1
                    upserted_id = None
                return _R()
        if upsert:
            new = {**query, **set_doc, **ins_doc}
            self._docs.append(new)

            class _R:
                matched_count = 0
                modified_count = 0
                upserted_id = "fake"
            return _R()

        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()


def _matches(doc, query):
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        if isinstance(v, dict):
            if "$gt" in v:
                if doc.get(k) is None or not (doc.get(k) > v["$gt"]):
                    return False
                continue
            if "$exists" in v:
                exists = k in doc
                if bool(v["$exists"]) != exists:
                    return False
                continue
            if "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
                continue
            if "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
                continue
            if "$nin" in v:
                if doc.get(k) in v["$nin"]:
                    return False
                continue
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self):
        self._colls: Dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeColl()
        return self._colls[name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_no_dirty_is_clean_noop(monkeypatch):
    db = _FakeDB()
    # Pre-populate a zero-row universe (no props, no scored docs).
    engine = DeltaEngine(db)
    result = await engine.tick("nba")
    assert result.success is True
    assert result.skipped is False
    assert "1_detect" in result.steps
    assert result.steps["1_detect"]["dirty_count"] == 0
    # With nothing dirty, rescore was skipped with no_dirty_props_to_rescore.
    assert result.steps["3_rescore_dirty"]["skipped"] is False
    assert result.steps["3_rescore_dirty"]["keys_requested"] == 0


@pytest.mark.asyncio
async def test_tick_aborts_when_upstream_lock_held():
    """UpstreamLockGateStep must short-circuit the chain."""
    from services import upstream_sync_lock as _usl

    fake_lock = UpstreamSyncLock()
    orig = _usl._singleton
    _usl._singleton = fake_lock
    try:
        db = _FakeDB()
        engine = DeltaEngine(db)

        # Acquire the lock in the background and hold it through the tick.
        holder_done = asyncio.Event()
        tick_done = asyncio.Event()

        async def holder():
            async with fake_lock.exclusive("nba", holder="master_sync:test"):
                await tick_done.wait()
            holder_done.set()

        holder_task = asyncio.create_task(holder())
        await asyncio.sleep(0.01)  # let holder acquire the lock
        assert fake_lock.is_held("nba")

        # Tick must see the lock held and abort.
        result = await engine.tick("nba")
        tick_done.set()
        await holder_done.wait()
        holder_task.cancel()

        assert result.skipped is True
        assert result.skipped_reason == "upstream_lock_held"
        # Downstream steps should report skipped=True.
        assert result.steps["3_rescore_dirty"]["skipped"] is True
        assert result.steps["4_rebalance_tiers"]["skipped"] is True
    finally:
        _usl._singleton = orig


@pytest.mark.asyncio
async def test_tick_marks_retired_keys_inactive(monkeypatch):
    """Retire signal → flip active=False on the matching RT scored doc."""
    db = _FakeDB()
    now = datetime.now(timezone.utc)

    # Seed: two MLB live_props — one active, one inactive (retired).
    key_active = "mlb|e1|Alvarez|Hits|1.5|OVER"
    key_retired = "mlb|e1|Judge|TotalBases|1.5|OVER"
    db["mlb_live_props"]._docs = [
        {"canonical_key": key_active, "active": True, "updated_at": now},
        {"canonical_key": key_retired, "active": False, "updated_at": now},
    ]
    # Seed: matching scored RT docs (both currently active).
    db["mlb_prop_scores"]._docs = [
        {
            "canonical_key": key_active, "version_tag": "final-mlb-rt",
            "active": True, "tier": "safe_haven",
        },
        {
            "canonical_key": key_retired, "version_tag": "final-mlb-rt",
            "active": True, "tier": "front_lines",
        },
    ]

    # Patch the scoring recompute so tests don't touch real adapters.
    async def _stub_recompute(db, sport, version_tag, **kw):
        return {
            "processed": 0, "written": 0, "skipped": 0, "replaced": 0,
            "collection": f"{sport}_prop_scores", "version_tag": version_tag,
            "only_canonical_keys_matched": 0,
        }
    import services.pipeline.delta_steps as ds_mod
    monkeypatch.setattr(ds_mod, "recompute_sport", _stub_recompute)

    engine = DeltaEngine(db)
    result = await engine.tick("mlb")
    assert result.success is True
    assert result.skipped is False
    # Detection should surface 1 retired and 0 new (retired live is already
    # present and scored; active live is also present and scored — no new keys).
    assert result.steps["1_detect"]["retired_count"] == 1
    # Retired scored doc flipped inactive.
    retired_doc = next(
        d for d in db["mlb_prop_scores"]._docs
        if d["canonical_key"] == key_retired
    )
    assert retired_doc["active"] is False
    assert retired_doc["inactive_reason"] == "retired_by_delta_engine"
    # Active scored doc untouched.
    active_doc = next(
        d for d in db["mlb_prop_scores"]._docs
        if d["canonical_key"] == key_active
    )
    assert active_doc["active"] is True


@pytest.mark.asyncio
async def test_tick_emits_observability_event(monkeypatch):
    """2026-05-07 P0-A: replaces the old `test_tick_advances_watermark`.
    The watermark step was deleted; the engine must still complete
    successfully and emit a delta_tick observability signal via the
    EmitDeltaTickStep at slot `5_emit`."""
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    db["mlb_live_props"]._docs = [
        {"canonical_key": "mlb|e1|A|Hits|1.5|OVER", "active": True, "updated_at": now}
    ]
    db["mlb_prop_scores"]._docs = [
        {"canonical_key": "mlb|e1|A|Hits|1.5|OVER",
         "version_tag": "final-mlb-rt", "active": True, "tier": "safe_haven"}
    ]

    async def _stub_recompute(db, sport, version_tag, **kw):
        return {"processed": 0, "written": 0, "skipped": 0, "replaced": 0,
                "collection": f"{sport}_prop_scores", "version_tag": version_tag,
                "only_canonical_keys_matched": 0}
    import services.pipeline.delta_steps as ds_mod
    monkeypatch.setattr(ds_mod, "recompute_sport", _stub_recompute)

    engine = DeltaEngine(db)
    result = await engine.tick("mlb")

    # The engine must NOT touch a (now-deleted) delta_watermarks
    # collection — emit step is now slot 6.
    assert "delta_watermarks" not in db._colls, (
        "DeltaEngine must not write to delta_watermarks anymore"
    )
    assert "5_advance_watermark" not in result.steps
    assert "6_emit" in result.steps
