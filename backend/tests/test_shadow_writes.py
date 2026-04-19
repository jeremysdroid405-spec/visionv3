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
    # `live_props` is not shadow-mapped in the Wave 1 pilot.
    assert COLL.writes_to("live_props", "nba") == [COLL("live_props", "nba")]


def test_writes_to_dual_for_events_cache_pilot():
    names = COLL.writes_to("events_cache", "nba")
    assert len(names) == 2
    assert names[0] == COLL("events_cache", "nba")        # primary
    assert names[1] == "nba_events_cache"                 # shadow
    assert names[0] != names[1]


def test_active_shadows_contains_pilot():
    shadows = COLL.active_shadows()
    assert ("events_cache", "nba") in shadows
    assert shadows[("events_cache", "nba")] == "nba_events_cache"


def test_handle_returns_raw_collection_when_no_shadow():
    class _StubDb(dict):
        def __getitem__(self, k):
            self.setdefault(k, _StubColl(k))
            return super().__getitem__(k)

    db = _StubDb()
    h = COLL.handle(db, "live_props", "nba")
    assert isinstance(h, _StubColl)
    assert h.name == COLL("live_props", "nba")


def test_handle_returns_shadow_writer_for_pilot():
    class _StubDb(dict):
        def __getitem__(self, k):
            self.setdefault(k, _StubColl(k))
            return super().__getitem__(k)

    db = _StubDb()
    h = COLL.handle(db, "events_cache", "nba")
    assert isinstance(h, ShadowWriter)
    assert h.primary.name == COLL("events_cache", "nba")
    assert [s.name for s in h.shadows] == ["nba_events_cache"]


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
