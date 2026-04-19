"""
Wave 1 shadow-write regression tests.

Covers:
1. `COLL.writes_to(concept, sport)` returns [primary] for non-shadowed
   concepts and [primary, shadow] for shadow-mapped ones.
2. `COLL.handle(db, concept, sport)` returns a raw collection for
   non-shadowed concepts and a `ShadowWriter` for shadow-mapped ones.
3. `ShadowWriter` fans mutating methods out to every underlying
   collection, returns the primary's result, and pins reads to the
   primary.
4. Shadow failures do not break the primary's return contract.
"""
from __future__ import annotations

import asyncio
import pytest

from services.config.collection_names import COLL
from services.config.shadow_writer import ShadowWriter


class _StubColl:
    """Minimal Motor-collection stand-in for unit tests."""

    def __init__(self, name: str, fail_on: set | None = None):
        self.name = name
        self._fail_on = fail_on or set()
        self.ops: list[tuple] = []

    async def insert_one(self, doc):
        self.ops.append(("insert_one", doc))
        if "insert_one" in self._fail_on:
            raise RuntimeError(f"{self.name}.insert_one boom")
        return {"name": self.name, "inserted": True}

    async def delete_many(self, flt):
        self.ops.append(("delete_many", flt))
        return {"name": self.name, "deleted": 7}

    async def update_one(self, flt, update, **kw):
        self.ops.append(("update_one", flt, update))
        return {"name": self.name, "matched": 1}

    # Read method — should only ever be called against primary.
    async def count_documents(self, flt):
        self.ops.append(("count_documents", flt))
        return 99 if self.name == "primary" else -1


# -------------------------------------------------------------------------
# Registry helpers
# -------------------------------------------------------------------------
def test_writes_to_single_for_non_shadowed():
    # `live_props` is not shadow-mapped.
    assert COLL.writes_to("live_props", "nba") == [COLL("live_props", "nba")]


def test_writes_to_respects_active_shadows():
    """For any concept currently in `_SHADOW_WRITES`, `writes_to` must
    return [primary, shadow]. For everything else, a single-element list.

    This keeps the test suite correct across the lifecycle: it passes
    whether there is an active shadow pilot or not.
    """
    shadows = COLL.active_shadows()
    if shadows:
        (concept, sport), shadow_name = next(iter(shadows.items()))
        names = COLL.writes_to(concept, sport)
        assert names[0] == COLL(concept, sport)
        assert names[-1] == shadow_name
        assert len(names) == 2
    else:
        # No active shadows — pick a known sport-specific concept and
        # confirm single-name routing.
        assert COLL.writes_to("master_hub", "nba") == [COLL("master_hub", "nba")]


def test_handle_returns_raw_collection_when_no_shadow():
    class _StubDb(dict):
        def __getitem__(self, k):
            self.setdefault(k, _StubColl(k))
            return super().__getitem__(k)

    db = _StubDb()
    h = COLL.handle(db, "live_props", "nba")
    assert isinstance(h, _StubColl)
    assert h.name == COLL("live_props", "nba")


def test_handle_wraps_when_concept_is_shadow_mapped():
    """If ANY concept is currently shadow-mapped, its handle must be a
    ShadowWriter. Otherwise this test is a no-op (still passes)."""
    shadows = COLL.active_shadows()
    if not shadows:
        return
    (concept, sport), shadow_name = next(iter(shadows.items()))

    class _StubDb(dict):
        def __getitem__(self, k):
            self.setdefault(k, _StubColl(k))
            return super().__getitem__(k)

    db = _StubDb()
    h = COLL.handle(db, concept, sport)
    assert isinstance(h, ShadowWriter)
    assert h.primary.name == COLL(concept, sport)
    assert [s.name for s in h.shadows] == [shadow_name]


# -------------------------------------------------------------------------
# ShadowWriter behavior
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_shadow_writer_fans_out_mutations():
    p = _StubColl("primary")
    s = _StubColl("shadow")
    sw = ShadowWriter(p, [s])

    r = await sw.insert_one({"k": 1})
    assert r == {"name": "primary", "inserted": True}
    assert p.ops == [("insert_one", {"k": 1})]
    assert s.ops == [("insert_one", {"k": 1})]


@pytest.mark.asyncio
async def test_shadow_writer_pins_reads_to_primary():
    p = _StubColl("primary")
    s = _StubColl("shadow")
    sw = ShadowWriter(p, [s])

    assert await sw.count_documents({}) == 99
    assert p.ops == [("count_documents", {})]
    assert s.ops == []   # shadow was NOT read


@pytest.mark.asyncio
async def test_shadow_writer_isolates_shadow_failure():
    p = _StubColl("primary")
    s = _StubColl("shadow", fail_on={"insert_one"})
    sw = ShadowWriter(p, [s])

    # Primary succeeds; shadow raises — but caller sees primary's result.
    r = await sw.insert_one({"k": 2})
    assert r == {"name": "primary", "inserted": True}
    assert p.ops == [("insert_one", {"k": 2})]
    assert s.ops == [("insert_one", {"k": 2})]


@pytest.mark.asyncio
async def test_shadow_writer_propagates_primary_failure():
    p = _StubColl("primary", fail_on={"insert_one"})
    s = _StubColl("shadow")
    sw = ShadowWriter(p, [s])

    with pytest.raises(RuntimeError, match="primary.insert_one boom"):
        await sw.insert_one({"k": 3})


@pytest.mark.asyncio
async def test_shadow_writer_getitem_pins_to_primary():
    class _Sub:
        name = "sub"

    class _P:
        name = "primary"

        def __getitem__(self, k):
            return _Sub()

    p = _P()
    s = _StubColl("shadow")
    sw = ShadowWriter(p, [s])

    sub = sw["any"]
    assert getattr(sub, "name", None) == "sub"
