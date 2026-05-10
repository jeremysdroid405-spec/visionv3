"""
Regression test for the WZ FanDuel-anchor coverage decoration bypass
(2026-05-10 fix).

Bug
---
`board/engine.py::on_new_props` (real-time scoped ingest) loads raw
props from `{sport}_live_props` and passes them DIRECTLY to
`recompute_sport(props=matched)`. The function's docstring states the
caller is responsible for filtering, but no caller actually decorated
its props with `filter_priceable` / `filter_pp_playable` /
`build_companion_map`. As a result every real-time-ingested prop
landed in `{sport}_prop_scores` with:

    book_count        = None
    coverage_class    = None
    books_anchored    = None
    tp_source         = "one_sided"  (no devig companion)

Downstream `coverage_gate` then evaluated `actual=None vs threshold=1`
and fail-closed, rejecting every WZ-routed FD-anchor row with
`gate_coverage_fail`. Pre-fix audit:
`/app/audit_reports/fd_anchor_p1_fix.md` (same bug pattern in the
injury rescore monkey-patch).

Fix
---
`recompute_sport` now applies the canonical 3-step decoration to
caller-supplied props before scoring:

    filter_priceable      → stamps book_count / coverage_class
    build_companion_map   → over the FULL live pool (db query)
    filter_pp_playable    → drops non-PP-playable rows

These tests pin that contract so a future regression (e.g., somebody
short-circuits the decoration in a hot-path "optimisation") fails CI
loudly instead of silently knocking the WZ tier to zero again.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

import services.scoring.recompute as recompute_mod


class _StubCursor:
    """Minimal async-iterable Mongo cursor that satisfies
    `to_list(length=...)`. The `find(query, projection)` call returns
    `self`; `to_list` returns the stored docs.
    """
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = list(docs)

    async def to_list(self, length: Optional[int] = None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])

    def limit(self, n: int):
        self._docs = self._docs[:int(n)]
        return self


class _StubCollection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs
        # Captured during recompute_sport — used by tests to detect that
        # we read the live-props collection for the companion map.
        self.find_call_count = 0

    def find(self, query=None, projection=None):
        self.find_call_count += 1
        return _StubCursor(self._docs)

    async def count_documents(self, q):
        return 0

    async def find_one(self, q, projection=None):
        return None


class _StubDB:
    def __init__(self, live_docs: List[Dict[str, Any]]):
        # All collection names the recompute path touches resolve to
        # the same stub. The live_props collection holds the canonical
        # live pool; cached_board / scores never get hit because we
        # short-circuit before write.
        self._live = _StubCollection(live_docs)

    def __getitem__(self, name):
        return self._live


def _make_prop(*, player, line, side, dk=None, fd=None, mgm=None, bol=None,
               playable_on_pp=True, event_id="evt_1", stat="PTS"):
    """Build a minimal live-props doc with the price-field shape both
    `filter_priceable` and `filter_pp_playable` consume."""
    return {
        "canonical_key": f"nba|{event_id}|{player}|{stat}|{line}|{side}",
        "player_name": player,
        "stat_type": stat,
        "line": line,
        "recommendation": side,
        "event_id": event_id,
        # Universal price keys (the legacy ones aren't on these rows —
        # filter_priceable falls back to `dk_odds` etc.).
        "dk_odds": dk,
        "fd_odds": fd,
        "mgm_odds": mgm,
        "bol_odds": bol,
        "playable_on_pp": playable_on_pp,
    }


@pytest.fixture
def patch_compute_and_write(monkeypatch):
    """Stub out the heavy compute + write paths inside `recompute_sport`
    so the test isolates the decoration step. We only need the function
    to RUN end-to-end through the decoration block.
    """
    monkeypatch.setattr(
        recompute_mod, "compute_scoring_stack",
        lambda **kwargs: {"tier": "front_lines", "tier_reason": "ok"},
    )

    async def _fake_write(*args, **kwargs):
        # Recover the score_docs the caller built so we can assert
        # the decoration mutated them in place. Capture the kwargs that
        # `write_versioned_scores` is called with.
        captured["score_docs"] = kwargs.get("score_docs") or (
            args[2] if len(args) >= 3 else []
        )
        return {"written": len(captured["score_docs"] or []),
                "replaced": 0,
                "collection": "stub_prop_scores"}

    captured: Dict[str, Any] = {}
    monkeypatch.setattr(
        recompute_mod, "write_versioned_scores", _fake_write,
    )

    # Stub adapter.build_context so we don't run the real heavy NBA
    # context builder. Just return None for every prop — recompute
    # treats `ctx=None` as a skip.
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter

    async def _noop_build_context(self, db, prop, override_config):
        # We don't need a real ctx — the test only validates that
        # decoration ran BEFORE this point and the input list to
        # build_context is the decorated subset.
        captured.setdefault("seen_props", []).append(prop)
        return None
    monkeypatch.setattr(NBAScoringAdapter, "build_context", _noop_build_context)
    return captured


@pytest.mark.asyncio
async def test_caller_supplied_props_get_book_count_stamped(
    patch_compute_and_write,
):
    """When the caller supplies undecorated props, recompute_sport must
    stamp `book_count` / `coverage_class` / `books_anchored` before
    handing them to build_context. Without this the coverage_gate sees
    `book_count=None` and fail-closes — the exact WZ FD-anchor bug.
    """
    captured = patch_compute_and_write

    props = [
        _make_prop(player="Test Player", line=20.5, side="OVER",
                   fd=190, dk=200, playable_on_pp=True),
    ]
    db = _StubDB(live_docs=props)

    await recompute_mod.recompute_sport(
        db=db, sport="nba", version_tag="final-nba-rt",
        write_mode="upsert", props=props,
    )

    # Decoration must have stamped the universal coverage fields BEFORE
    # the prop reached build_context (the FD-anchor coverage_gate bug
    # was exactly this field being None).
    seen = captured.get("seen_props") or []
    assert len(seen) == 1, f"expected 1 prop seen, got {len(seen)}"
    p = seen[0]
    assert "book_count" in p, "book_count NOT stamped on caller-supplied prop"
    assert "coverage_class" in p, "coverage_class NOT stamped"
    assert "books_anchored" in p, "books_anchored NOT stamped"
    assert p["book_count"] == 2, f"expected book_count=2 (FD+DK), got {p['book_count']}"
    assert p["coverage_class"] == "multi_book"
    assert set(p["books_anchored"]) == {"fanduel", "draftkings"}


@pytest.mark.asyncio
async def test_caller_supplied_props_drop_non_pp_playable(
    patch_compute_and_write,
):
    """`filter_pp_playable` must run on caller-supplied props so
    sportsbook-fallback rows (no exact PP side) are dropped, matching
    the universal contract enforced inside `load_live_props`. Without
    this, the bypass path also bypassed the PP-side contract.
    """
    captured = patch_compute_and_write

    props = [
        _make_prop(player="PP Playable", line=10.5, side="OVER",
                   dk=200, playable_on_pp=True),
        _make_prop(player="No PP Side", line=10.5, side="OVER",
                   dk=200, playable_on_pp=False),
    ]
    db = _StubDB(live_docs=props)

    await recompute_mod.recompute_sport(
        db=db, sport="nba", version_tag="final-nba-rt",
        write_mode="upsert", props=props,
    )

    seen_names = [p["player_name"] for p in (captured.get("seen_props") or [])]
    assert "PP Playable" in seen_names
    assert "No PP Side" not in seen_names, (
        "filter_pp_playable did NOT drop non-PP-playable prop from "
        "caller-supplied batch — universal PP contract bypassed."
    )


@pytest.mark.asyncio
async def test_caller_supplied_props_drop_zero_book_rows(
    patch_compute_and_write,
):
    """`filter_priceable` must drop rows with no sportsbook anchor
    (book_count == 0) so PP-only rows never enter scoring. Mirrors the
    `load_live_props` 0-book exclusion guarantee.
    """
    captured = patch_compute_and_write

    props = [
        # 0-book row: no DK/FD/MGM/BOL price. Should be dropped.
        _make_prop(player="PP Only", line=10.5, side="OVER",
                   playable_on_pp=True),
        _make_prop(player="DK Anchored", line=10.5, side="OVER",
                   dk=110, playable_on_pp=True),
    ]
    db = _StubDB(live_docs=props)

    await recompute_mod.recompute_sport(
        db=db, sport="nba", version_tag="final-nba-rt",
        write_mode="upsert", props=props,
    )

    seen_names = [p["player_name"] for p in (captured.get("seen_props") or [])]
    assert "DK Anchored" in seen_names
    assert "PP Only" not in seen_names, (
        "filter_priceable did NOT drop 0-book PP-only row from "
        "caller-supplied batch."
    )


@pytest.mark.asyncio
async def test_no_caller_supplied_uses_adapter_load_live_props(
    patch_compute_and_write, monkeypatch,
):
    """Sanity: when `props` is None, the decoration block must NOT run —
    the adapter's `load_live_props` already does it. This pins the
    "decorate only on caller-supplied" branching condition.
    """
    captured = patch_compute_and_write
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter

    called = {"load_live_props": 0}

    async def _stub_load(self, db, limit=None):
        called["load_live_props"] += 1
        # Return one already-decorated prop.
        return [{
            **_make_prop(player="From Adapter", line=10.5, side="OVER",
                         dk=110, playable_on_pp=True),
            # Pretend `filter_priceable` already ran:
            "book_count": 1, "coverage_class": "single_book",
            "books_anchored": ["draftkings"],
        }]
    monkeypatch.setattr(NBAScoringAdapter, "load_live_props", _stub_load)

    db = _StubDB(live_docs=[])

    await recompute_mod.recompute_sport(
        db=db, sport="nba", version_tag="final-nba-rt",
        write_mode="upsert",
        # props=None → adapter.load_live_props path
    )

    assert called["load_live_props"] == 1, (
        "adapter.load_live_props was NOT called when caller didn't "
        "supply props — branching condition broken."
    )
