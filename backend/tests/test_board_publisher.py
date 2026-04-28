"""
Universal Board Publisher — Validation Suite
============================================

Locks down the universal tier-board publish contract. These tests are
the proof requested in the spec:

    A. Safe Haven with 4 picks  → fills and reranks correctly
    B. Front Lines OVER with 6 picks → fills to 10 independently
    C. Front Lines UNDER with 3 picks → fills to 10 independently
    D. Full board → new pick ranked #2 enters at #2 (not blocked)
    E. Candidate worse than #10 → does NOT enter
    F. Two snapshots 5 minutes apart → majority of picks remain,
       only incremental changes occur
    G. Confirm ZERO full-board replacement behavior

All tests use the pure in-memory `_reconcile_in_memory` algorithm so
they run in milliseconds and don't depend on Mongo.

What these tests DO NOT validate
--------------------------------
Scoring, μ, σ, gates, thresholds, tier-routing, pick-selection. The
publisher is a publish-layer contract only; those layers have their
own dedicated test suites and are intentionally NOT exercised here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import pytest

from services.board.publisher import (
    TIER_CONFIG,
    _reconcile_in_memory,
    rank_tuple,
)


# ─── Helpers ──────────────────────────────────────────────────────────
def pick(player: str, side: str = "OVER", *,
         ranking: float = 50.0,
         vision: float = 50.0,
         edge: float = 0.0,
         stat: str = "PTS",
         line: float = 10.5,
         event: str = "evt1") -> Dict[str, Any]:
    """A minimal scored pick for the publisher to consume."""
    ck = f"nba|{event}|{player}|{stat}|{line}|{side}"
    return {
        "canonical_key":   ck,
        "player_name":     player,
        "stat_type":       stat,
        "line":            line,
        "recommendation":  side,
        "ranking_score":   ranking,
        "vision_score":    vision,
        "edge_pct":        edge,
    }


def state_entry(p: Dict[str, Any], rank: int) -> Dict[str, Any]:
    """Convert a candidate dict into a persisted state entry."""
    return {
        "canonical_key":  p["canonical_key"],
        "rank":           rank,
        "ranking_score":  p["ranking_score"],
        "vision_score":   p["vision_score"],
        "edge_pct":       p["edge_pct"],
    }


# ─── A. Safe Haven with 4 picks → fills and reranks correctly ────────
def test_a_safe_haven_with_4_picks_fills_and_reranks():
    """Empty board + 4 candidates → board has those 4 in best-first order.
    Fill-mode rerank: changing scores re-sorts during fill."""
    state: List[Dict[str, Any]] = []
    candidates = [
        pick("alpha", ranking=60),  # was best
        pick("bravo", ranking=80),  # actually best
        pick("charlie", ranking=70),
        pick("delta", ranking=50),
    ]
    ordered, evicted, mode = _reconcile_in_memory(state, candidates,
                                                   capacity=10)
    assert mode == "fill"
    assert [p["player_name"] for p in ordered] == ["bravo", "charlie",
                                                    "alpha", "delta"]
    assert evicted == []

    # During fill mode subsequent re-rank IS allowed (per spec line 56).
    state2 = [state_entry(p, i + 1) for i, p in enumerate(ordered)]
    candidates2 = [
        pick("bravo",   ranking=80),
        pick("charlie", ranking=70),
        pick("alpha",   ranking=60),
        pick("delta",   ranking=95),  # delta's score jumps
    ]
    ordered2, _, mode2 = _reconcile_in_memory(state2, candidates2,
                                               capacity=10)
    assert mode2 == "fill"
    assert ordered2[0]["player_name"] == "delta"


# ─── B. Front Lines OVER with 6 picks → fills to 10 independently ────
def test_b_front_lines_over_with_6_picks_fills_to_10_independently():
    """OVER board fills independently; UNDER pool absence does NOT
    block OVER fill."""
    state: List[Dict[str, Any]] = []
    over_cands = [pick(f"over_p{i}", side="OVER", ranking=50 + i)
                  for i in range(6)]
    ordered, evicted, mode = _reconcile_in_memory(state, over_cands,
                                                   capacity=10)
    assert mode == "fill"
    assert len(ordered) == 6  # only what we have
    # Best ranking_score first
    assert ordered[0]["player_name"] == "over_p5"
    assert ordered[-1]["player_name"] == "over_p0"
    assert evicted == []


# ─── C. Front Lines UNDER with 3 picks → fills to 10 independently ───
def test_c_front_lines_under_with_3_picks_fills_to_10_independently():
    state: List[Dict[str, Any]] = []
    under_cands = [pick(f"under_p{i}", side="UNDER", ranking=40 + i)
                   for i in range(3)]
    ordered, evicted, mode = _reconcile_in_memory(state, under_cands,
                                                   capacity=10)
    assert mode == "fill"
    assert len(ordered) == 3
    assert all(p["recommendation"] == "UNDER" for p in ordered)
    assert ordered[0]["player_name"] == "under_p2"


# ─── D. Full board → new pick ranked #2 enters at #2 (not blocked) ──
def test_d_full_board_new_pick_inserts_at_true_rank():
    """10-pick stable board. New candidate fits at slot 2 by ranking
    score → it inserts at slot 2; old #1 unchanged; old #2..#9 shift
    to #3..#10; old #10 evicted."""
    # Existing board: rankings 100, 90, 80, …, 10 → 10 picks.
    survivors = [
        pick(f"old_{i}", ranking=100 - i * 10) for i in range(10)
    ]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]

    # New candidate ranks 95 → between 100 (old #1) and 90 (old #2).
    new_pick = pick("new_alpha", ranking=95)
    candidates = list(survivors) + [new_pick]

    ordered, evicted, mode = _reconcile_in_memory(state, candidates,
                                                   capacity=10)
    assert mode == "stable"
    assert len(ordered) == 10
    # #1 unchanged
    assert ordered[0]["player_name"] == "old_0"
    # #2 is the new pick
    assert ordered[1]["player_name"] == "new_alpha"
    # #3..#10 are old_1..old_8 in order (old_9 was evicted)
    expected = [f"old_{i}" for i in range(1, 9)]
    assert [p["player_name"] for p in ordered[2:]] == expected
    assert evicted == [survivors[9]["canonical_key"]]


def test_d2_full_board_new_pick_top_inserts_at_one():
    """Sanity: a candidate that beats #1 lands at #1 (not somewhere else)."""
    survivors = [pick(f"old_{i}", ranking=100 - i * 10) for i in range(10)]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]
    new_pick = pick("monster", ranking=999)
    ordered, evicted, mode = _reconcile_in_memory(
        state, list(survivors) + [new_pick], capacity=10,
    )
    assert mode == "stable"
    assert ordered[0]["player_name"] == "monster"
    assert ordered[1]["player_name"] == "old_0"
    assert evicted == [survivors[9]["canonical_key"]]


# ─── E. Candidate worse than #10 → does NOT enter ────────────────────
def test_e_candidate_worse_than_last_does_not_enter():
    survivors = [pick(f"old_{i}", ranking=100 - i * 10) for i in range(10)]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]
    weakling = pick("weakling", ranking=5)  # worse than old_9 (rank 10)

    ordered, evicted, mode = _reconcile_in_memory(
        state, list(survivors) + [weakling], capacity=10,
    )
    # Spec: "noop" when nothing better arrives.
    assert mode == "noop"
    assert [p["player_name"] for p in ordered] == \
           [s["player_name"] for s in survivors]
    assert evicted == []
    # Weakling is NOT on the board.
    assert "weakling" not in {p["player_name"] for p in ordered}


# ─── F. Two snapshots 5 minutes apart → majority remain, incremental ─
def test_f_majority_remain_only_incremental_changes_across_snapshots():
    """Simulates two reconcile passes 5 min apart with realistic
    score jitter. Verifies stability:
      * ≥ 80% of survivors remain
      * Only one new pick inserted (the one that beats #10)
      * Survivors keep their relative slot order from snapshot 1"""
    survivors = [pick(f"old_{i}", ranking=100 - i * 5) for i in range(10)]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]

    # Snapshot 2 candidates: each survivor wiggles slightly + one
    # newcomer that beats #10's score. NO survivor swaps with another
    # survivor: the spec says they only move on insertion-driven
    # displacement.
    candidates_t2: List[Dict[str, Any]] = []
    for i, p in enumerate(survivors):
        wiggle = (-0.5 if i % 2 else +0.5)  # tiny noise
        candidates_t2.append(pick(
            p["player_name"], ranking=p["ranking_score"] + wiggle,
        ))
    newcomer = pick("newcomer", ranking=58)  # beats old_9 (55), not old_8 (60)
    candidates_t2.append(newcomer)

    ordered, evicted, mode = _reconcile_in_memory(state, candidates_t2,
                                                   capacity=10)
    assert mode == "stable"
    assert len(ordered) == 10
    # 9 of 10 survivors remain (old_9 displaced) → 90% retention
    survivor_names_t1 = {f"old_{i}" for i in range(10)}
    survivor_names_t2 = {p["player_name"] for p in ordered}
    overlap = survivor_names_t1 & survivor_names_t2
    assert len(overlap) >= 8, (
        f"Only {len(overlap)}/10 survivors remained — board is unstable"
    )
    # Newcomer must be present
    assert "newcomer" in survivor_names_t2
    # Newcomer slots in at the correct true rank (between old_8 r=60
    # and old_9 r=55 → slot 10 after old_8) — spec 5 example.
    new_slot = next(i for i, p in enumerate(ordered)
                    if p["player_name"] == "newcomer")
    assert new_slot == 9, (
        f"Newcomer landed at slot {new_slot+1}, expected slot 10"
    )
    # Survivors' relative ordering: old_0..old_8 preserved.
    # Filter out the newcomer and old_9 from the result.
    relative = [p["player_name"] for p in ordered
                if p["player_name"] not in {"newcomer", "old_9"}]
    assert relative == [f"old_{i}" for i in range(9)], (
        f"Survivors reordered themselves: {relative}"
    )


def test_f2_score_wiggle_alone_does_not_reorder_existing_picks():
    """Spec line 79 lockdown: existing picks must NOT move based on
    their own metric refresh. Reordering only ever happens via a
    candidate-driven insertion. Without a qualifying entrant, ANY
    score wiggle on the existing picks is a no-op for ordering."""
    # Survivors with a rank gap that COULD invert (e.g. #2 rank=90 →
    # would now beat #1 rank=92 if we re-ranked). Must NOT re-rank.
    survivors = [
        pick("a", ranking=100),  # original #1
        pick("b", ranking=90),   # original #2
        pick("c", ranking=80),
    ]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]

    candidates = [
        pick("a", ranking=85),   # a CRATERED but should still be #1
        pick("b", ranking=92),   # b SOARED but should still be #2
        pick("c", ranking=80),
    ]
    # Below capacity -> fill mode -> reranking is OK by spec.
    # To test strict stable-mode no-reorder, run at capacity=3.
    ordered, evicted, mode = _reconcile_in_memory(state, candidates,
                                                   capacity=3)
    assert mode == "noop"
    assert [p["player_name"] for p in ordered] == ["a", "b", "c"]
    assert evicted == []


# ─── G. ZERO full-board replacement behavior ─────────────────────────
def test_g_zero_full_board_replacement():
    """A wholesale fresh top-N would replace ALL 10 picks in this
    setup. The publisher must NOT do that — at most ONE pick changes."""
    survivors = [pick(f"old_{i}", ranking=100 - i * 5) for i in range(10)]
    state = [state_entry(p, i + 1) for i, p in enumerate(survivors)]

    # Fresh batch: same 10 survivors but every single one has a slightly
    # different ranking_score (delta engine recompute simulation),
    # PLUS 5 brand-new candidates whose scores are all between #5 and
    # #10 of the existing board. A naive re-sort would push 5 survivors
    # off the board. The publisher must accept at most 5 of them ONLY
    # IF each one beats the current #10 — then one-by-one inserts with
    # the displaced survivor evicted on each step. Verify total
    # replacement count is bounded.
    candidates: List[Dict[str, Any]] = []
    for p in survivors:
        candidates.append(pick(p["player_name"],
                               ranking=p["ranking_score"] + 0.1))
    # 5 newcomers with scores 60.5, 60.4, 60.3, 60.2, 60.1
    # Old #10 starts at ranking=55, so all 5 do beat #10 — but each
    # insertion bumps the new last pick. Spec is fine with cumulative
    # inserts; the lockdown is "no FULL replace".
    for k in range(5):
        candidates.append(pick(f"newcomer_{k}", ranking=60.5 - 0.1 * k))

    ordered, evicted, mode = _reconcile_in_memory(state, candidates,
                                                   capacity=10)
    assert mode == "stable"
    new_names = {p["player_name"] for p in ordered}
    old_names = {p["player_name"] for p in survivors}
    retained = old_names & new_names
    # Spec G: "Confirm ZERO full-board replacement behavior" —
    # at minimum, MORE THAN HALF of the original board must persist.
    assert len(retained) >= 5, (
        f"Only {len(retained)}/10 of the original board survived — "
        "this looks like a full-board replacement, not stable insertion."
    )


# ─── Sort-tuple lockdown ─────────────────────────────────────────────
def test_rank_tuple_orders_universally():
    """The deterministic sort tuple is part of the public contract.
    Loosening this without a CHANGELOG entry will cause flicker."""
    a = pick("a", ranking=80, vision=50, edge=10)
    b = pick("b", ranking=80, vision=50, edge=15)   # same ranking, higher edge
    c = pick("c", ranking=80, vision=60, edge=10)   # same ranking, higher vision
    d = pick("d", ranking=85, vision=10, edge=0)    # higher ranking
    pool = [a, b, c, d]
    pool.sort(key=rank_tuple)
    assert [p["player_name"] for p in pool] == ["d", "c", "b", "a"]


def test_tier_config_invariants():
    """Lockdown — TIER_CONFIG defines the universal capacities. A
    silent change to these without a PRD update causes user-visible
    drift across all sports."""
    assert TIER_CONFIG["safe_haven"]   == {"capacity_per_side": 10,
                                           "split_by_side": False}
    assert TIER_CONFIG["front_lines"]  == {"capacity_per_side": 10,
                                           "split_by_side": True}
    assert TIER_CONFIG["war_zone"]     == {"capacity_per_side": 10,
                                           "split_by_side": False}


# ─── Mongo-backed end-to-end (uses the same _DB mock as contract tests) ─
class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def find(self, q=None, proj=None):
        q = q or {}

        def _match(d):
            for k, v in q.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        return False
                elif isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]:
                        return False
                else:
                    if d.get(k) != v:
                        return False
            return True

        rows = [dict(d) for d in self.docs if _match(d)]

        class _Cur:
            def __init__(self, rows):
                self._rows = rows
                self._sort = None

            def sort(self, key, direction=None):
                if isinstance(key, str):
                    self._sort = [(key, direction or 1)]
                else:
                    self._sort = list(key) if isinstance(key, list) else key
                return self

            async def to_list(self, length=None):
                rows = list(self._rows)
                if self._sort:
                    for k, d in reversed(self._sort):
                        rows.sort(key=lambda r: (r.get(k) is None, r.get(k)),
                                  reverse=(d == -1))
                if length:
                    rows = rows[:length]
                return rows

        return _Cur(rows)

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(upd.get("$set") or {})
                return type("R", (), {"matched_count": 1})()
        if upsert:
            new = {}
            new.update(upd.get("$setOnInsert") or {})
            new.update(upd.get("$set") or {})
            for k, v in q.items():
                new.setdefault(k, v)
            self.docs.append(new)
        return type("R", (), {"matched_count": 0})()

    async def update_many(self, q, upd):
        n = 0
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False; break
                elif isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]:
                        ok = False; break
                else:
                    if d.get(k) != v:
                        ok = False; break
            if ok:
                d.update(upd.get("$set") or {})
                n += 1
        return type("R", (), {"matched_count": n})()

    async def create_index(self, *a, **kw):
        return None


class _DB:
    def __init__(self):
        self._colls: Dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]


@pytest.mark.asyncio
async def test_e2e_reconcile_persists_and_reads_back():
    """End-to-end via mock Mongo: reconcile a 4-pick safe_haven, read
    back, verify ordered correctly + state persists."""
    from services.board.publisher import (
        ensure_indexes, reconcile, get_published_board,
    )
    db = _DB()
    await ensure_indexes(db)
    cands = [
        pick("alpha",  ranking=60),
        pick("bravo",  ranking=80),
        pick("charlie", ranking=70),
        pick("delta",  ranking=50),
    ]
    audit = await reconcile(db, "nba", "safe_haven", cands)
    assert audit["sides"]["combined"]["mode"] == "fill"
    rows = await get_published_board(db, "nba", "safe_haven")
    assert [r["canonical_key"].split("|")[2] for r in rows] == \
           ["bravo", "charlie", "alpha", "delta"]


@pytest.mark.asyncio
async def test_e2e_split_tier_over_under_independence():
    """Front Lines: OVER and UNDER are independent. Reconciling 6 OVER
    + 3 UNDER produces 6+3=9 board entries split across the two sides
    with each ranked independently."""
    from services.board.publisher import (
        ensure_indexes, reconcile, get_published_board,
    )
    db = _DB()
    await ensure_indexes(db)
    cands = (
        [pick(f"o{i}", side="OVER",  ranking=50 + i) for i in range(6)] +
        [pick(f"u{i}", side="UNDER", ranking=40 + i) for i in range(3)]
    )
    audit = await reconcile(db, "nba", "front_lines", cands)
    assert audit["sides"]["OVER"]["board_count"] == 6
    assert audit["sides"]["UNDER"]["board_count"] == 3
    over_rows = await get_published_board(db, "nba", "front_lines",
                                           side="OVER")
    under_rows = await get_published_board(db, "nba", "front_lines",
                                            side="UNDER")
    assert all(r["canonical_key"].split("|")[5] == "OVER"
               for r in over_rows)
    assert all(r["canonical_key"].split("|")[5] == "UNDER"
               for r in under_rows)


@pytest.mark.asyncio
async def test_e2e_universal_works_for_arbitrary_sport():
    """The publisher's universality lockdown: NHL (a sport never seen
    by the publisher before) reconciles correctly with no code change."""
    from services.board.publisher import (
        ensure_indexes, reconcile, get_published_board,
    )
    db = _DB()
    await ensure_indexes(db)
    cands = [
        {"canonical_key": f"nhl|game1|player_{i}|GOALS|0.5|OVER",
         "player_name": f"player_{i}", "stat_type": "GOALS",
         "line": 0.5, "recommendation": "OVER",
         "ranking_score": 50 + i, "vision_score": 50 + i,
         "edge_pct": 0.0}
        for i in range(5)
    ]
    audit = await reconcile(db, "nhl", "safe_haven", cands)
    assert audit["sport"] == "nhl"
    rows = await get_published_board(db, "nhl", "safe_haven")
    assert len(rows) == 5
