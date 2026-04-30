"""Regression tests for `services.board.reader.get_board` gap-sort behavior.

WHY THIS EXISTS
---------------
On 2026-04-30 the production dashboard reported stale/divergent tier
counts: `/api/v3/ferrari/safe-haven?sport=nba&sort=gap` returned 2
picks while `/api/v3/ferrari/safe-haven?sport=nba` returned 4. Every
gap-sort read was silently evicting picks from the persisted
`board_state`, so subsequent non-gap reads saw the damaged board until
the next non-gap read re-healed it. The board was flapping on every
page load.

ROOT CAUSE
----------
`services/board/reader.py::get_board` added a hard filter when the
gap-sort key was active:

    if primary == "ranking_score_v2":
        query["ranking_score_v2"] = {"$ne": None}

Any scored pick whose `ranking_score_v2` happened to be null/missing
was DROPPED from the candidate pool. The pool was then fed into
`services.board.publisher.reconcile(...)`, which treats
"canonical_key not in candidate pool" as "pick no longer qualifying"
and evicts it from the persisted `board_state`. Valid picks lacking a
single optional score field were false-evicted on every page load.

THE FIX
-------
Drop the null filter. MongoDB sorts null/missing numeric values as
LOWEST in BSON type order, so in DESC they naturally fall to the end
— no special handling needed. Picks without `ranking_score_v2` flow
through the candidate pool and `publisher.rank_tuple` falls back
through the documented chain
(`ranking_score_v2 → ranking_score → vision_score`) when ranking them.

WHAT THIS SUITE LOCKS IN
------------------------
INV-G1: With `sort_key_override="ranking_score_v2"`, picks whose
        `ranking_score_v2` is null are NOT filtered out of the
        candidate pool. All qualifying picks surface.

INV-G2: Null-rsv2 picks sort LAST (after non-null picks) under
        DESC rsv2 + DESC pp_utility tiebreak, so ordering remains
        deterministic.

INV-G3: Gap-sort reads must not shrink the candidate pool relative
        to non-gap reads under identical tier/active/time filters.
        (Direct Mongo query parity check — no publisher involvement.)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


class _StubAdapter:
    """Minimal adapter double. Points `reader.get_board` at a
    dedicated test collection + unique version_tag so we never
    touch production data, and picks a tier name NOT registered in
    the publisher's `TIER_CONFIG` so the publisher's reconcile path
    is skipped (exercise reader read-path only)."""

    def __init__(self, scores_collection: str, version_tag: str):
        self.scores_collection = scores_collection
        self.version_tag = version_tag

    def sort_key_for_tier(self, tier: str) -> str:
        # Tier-default key (exercises the non-override branch when
        # the test doesn't pass sort_key_override).
        return "vision_score"

    def capacity_for_tier(self, tier: str) -> int:
        return 10


def _make_doc(
    *,
    version_tag: str,
    tier: str,
    player_name: str,
    ranking_score_v2: Optional[float],
    vision_score: float,
    pp_utility: float = 1.0,
    line: float = 9.5,
) -> Dict[str, Any]:
    ck = f"nba|evt_rsv2_test|{player_name}|PTS|{line}|OVER"
    return {
        "canonical_key": ck,
        "version_tag": version_tag,
        "tier": tier,
        "active": True,
        "game_start_utc": datetime.now(timezone.utc) + timedelta(hours=2),
        "player_name": player_name,
        "stat_type": "PTS",
        "line": line,
        "recommendation": "OVER",
        "direction": "OVER",
        "ranking_score_v2": ranking_score_v2,
        "vision_score": vision_score,
        "pp_utility": pp_utility,
        "edge_pct": 5.0,
        "_rsv2_null_filter_test": True,  # cleanup tag
    }


# ─── Tests ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_g1_null_rsv2_picks_are_not_filtered_out(
    db, monkeypatch,
):
    """INV-G1: With `sort_key_override='ranking_score_v2'`, picks whose
    `ranking_score_v2` is null must still appear in the result.

    Before the fix: reader added `ranking_score_v2: {"$ne": None}` to
    the query when the gap-sort key was active, silently dropping every
    null-rsv2 pick.
    """
    from services.board import reader as board_reader

    coll_suffix = uuid.uuid4().hex[:8]
    scores_coll = f"nba_prop_scores_test_rsv2_{coll_suffix}"
    version_tag = f"test-rsv2-{coll_suffix}"
    # Tier not registered in TIER_CONFIG → publisher path skipped,
    # reader returns the raw deduped candidate pool.
    tier = "_rsv2_null_filter_test_tier"

    stub = _StubAdapter(scores_coll, version_tag)
    monkeypatch.setattr(board_reader, "get_adapter", lambda _sport: stub)

    # Seed: 2 picks with rsv2 populated, 2 with rsv2 null.
    docs = [
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Alpha With Rsv2",
                  ranking_score_v2=90.0, vision_score=50.0),
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Bravo With Rsv2",
                  ranking_score_v2=80.0, vision_score=55.0),
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Charlie Null Rsv2",
                  ranking_score_v2=None, vision_score=95.0,
                  pp_utility=9.0),
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Delta Null Rsv2",
                  ranking_score_v2=None, vision_score=85.0,
                  pp_utility=7.0),
    ]
    try:
        await db[scores_coll].insert_many(docs)

        result = await board_reader.get_board(
            db, sport="nba", tier=tier, limit=10,
            sort_key_override="ranking_score_v2",
        )

        names = {r["player_name"] for r in result}
        assert names == {
            "Alpha With Rsv2", "Bravo With Rsv2",
            "Charlie Null Rsv2", "Delta Null Rsv2",
        }, (
            f"Null-rsv2 picks silently dropped. Got {len(result)} picks, "
            f"names={sorted(names)}. The gap-sort read MUST NOT filter on "
            f"ranking_score_v2 != None — it silently evicts valid picks from "
            f"the published board_state via reconcile."
        )
    finally:
        await db[scores_coll].drop()


@pytest.mark.asyncio
async def test_inv_g2_null_rsv2_picks_sort_last(db, monkeypatch):
    """INV-G2: Picks with null `ranking_score_v2` must sort AFTER
    picks with a populated value when sort_key_override is active.
    Guards against a future 'fix' that hoists nulls to the top of
    DESC order (which would demote real top-ranked picks)."""
    from services.board import reader as board_reader

    coll_suffix = uuid.uuid4().hex[:8]
    scores_coll = f"nba_prop_scores_test_rsv2_order_{coll_suffix}"
    version_tag = f"test-rsv2-order-{coll_suffix}"
    tier = "_rsv2_null_filter_test_tier"

    stub = _StubAdapter(scores_coll, version_tag)
    monkeypatch.setattr(board_reader, "get_adapter", lambda _sport: stub)

    docs = [
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Populated High",
                  ranking_score_v2=90.0, vision_score=50.0),
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name="Null Rsv2 High Vision",
                  ranking_score_v2=None, vision_score=99.0,
                  pp_utility=99.0),
    ]
    try:
        await db[scores_coll].insert_many(docs)

        result = await board_reader.get_board(
            db, sport="nba", tier=tier, limit=10,
            sort_key_override="ranking_score_v2",
        )
        names_in_order = [r["player_name"] for r in result]
        assert names_in_order == ["Populated High", "Null Rsv2 High Vision"], (
            f"Expected populated-rsv2 pick first, null-rsv2 pick last. "
            f"Got order: {names_in_order}"
        )
    finally:
        await db[scores_coll].drop()


@pytest.mark.asyncio
async def test_inv_g3_gap_sort_pool_matches_non_gap_pool(db, monkeypatch):
    """INV-G3: The candidate pool returned for a gap-sort read must be
    the SAME SET of picks as a non-gap read under identical
    tier/active/game_start filters. Only the *order* may differ.

    This is the core product invariant: sort is a presentation choice,
    NOT a filter. It also directly reproduces the user-visible bug
    (gap returned 2 picks, non-gap returned 4 picks).
    """
    from services.board import reader as board_reader

    coll_suffix = uuid.uuid4().hex[:8]
    scores_coll = f"nba_prop_scores_test_rsv2_parity_{coll_suffix}"
    version_tag = f"test-rsv2-parity-{coll_suffix}"
    tier = "_rsv2_null_filter_test_tier"

    stub = _StubAdapter(scores_coll, version_tag)
    monkeypatch.setattr(board_reader, "get_adapter", lambda _sport: stub)

    # Mirror production shape: some picks have rsv2, some don't.
    docs = [
        _make_doc(version_tag=version_tag, tier=tier,
                  player_name=f"Player {i}",
                  ranking_score_v2=(50.0 + i) if i % 2 == 0 else None,
                  vision_score=(60.0 + i),
                  pp_utility=float(i + 1),
                  line=9.5 + i)
        for i in range(6)
    ]
    try:
        await db[scores_coll].insert_many(docs)

        gap_picks = await board_reader.get_board(
            db, sport="nba", tier=tier, limit=10,
            sort_key_override="ranking_score_v2",
        )
        default_picks = await board_reader.get_board(
            db, sport="nba", tier=tier, limit=10,
            sort_key_override=None,
        )

        gap_cks = {p["canonical_key"] for p in gap_picks}
        default_cks = {p["canonical_key"] for p in default_picks}

        assert gap_cks == default_cks, (
            f"Gap-sort read returned a DIFFERENT set of picks than "
            f"non-gap read. Sort must not filter. "
            f"gap_only={gap_cks - default_cks}, "
            f"default_only={default_cks - gap_cks}"
        )
        assert len(gap_picks) == 6
    finally:
        await db[scores_coll].drop()
