"""
SLO §3 (`check_tier_freshness`) unit tests + writer tests.
==========================================================

Pinned contract (2026-05-07 P0 §3 fix):

    1. EXISTENCE   - every cached_board doc carries the canonical
                     freshness fields:
                         updated_at
                         last_publish_ts
                         source_score_max_scored_at
                         sport
                         version_tag

    2. INVARIANT   - updated_at >= source_score_max_scored_at
                     (the writer cannot stamp a board claiming a
                      source score newer than its own publish time)

    3. RECENCY     - now - max(updated_at) <= CACHED_BOARD_MAX_AGE_S
                     (master_sync hourly cadence + 15-min grace)

These tests use an in-process fake mongo collection so they run in
CI without a live database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ─── Load the SLO module by file path ─────────────────────────────────
_SCRIPT_PATH = Path("/app/backend/scripts/production_readiness_slo_check.py")
_spec = importlib.util.spec_from_file_location("_slo_check_for_tier_test", _SCRIPT_PATH)
slo = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["_slo_check_for_tier_test"] = slo
_spec.loader.exec_module(slo)  # type: ignore[union-attr]


# ─── Tiny async-mongo fake — only what §3 actually calls ────────────
class _FakeCB:
    """Stand-in for `db['<sport>_cached_board']` and
    `db['<sport>_prop_scores']`. Backed by an in-memory list of dicts."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    async def count_documents(self, query: Dict[str, Any]) -> int:
        return sum(1 for r in self._rows if _matches(r, query))

    async def find_one(self, query, projection=None, sort=None):
        rows = [r for r in self._rows if _matches(r, query)]
        if sort:
            for field, direction in reversed(sort):
                rows.sort(
                    key=lambda r: r.get(field) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=(direction == -1),
                )
        return rows[0] if rows else None


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        if key == "$expr":
            # Support `{"$lt": ["$updated_at", "$source_score_max_scored_at"]}`
            cmp = list(cond.keys())[0]
            a_path, b_path = cond[cmp]
            a = row.get(a_path.lstrip("$"))
            b = row.get(b_path.lstrip("$"))
            if a is None or b is None:
                return False
            if cmp == "$lt" and not (a < b):
                return False
            continue
        val = row.get(key)
        if isinstance(cond, dict):
            for op, opv in cond.items():
                if op == "$exists":
                    present = key in row
                    if bool(opv) != present:
                        return False
                elif op == "$ne":
                    if val == opv:
                        return False
                elif op == "$in":
                    if val not in opv:
                        return False
                else:
                    raise NotImplementedError(f"_matches: op {op!r}")
        else:
            if val != cond:
                return False
    return True


class _FakeDB:
    def __init__(self, mapping: Dict[str, _FakeCB]):
        self._mapping = mapping

    def __getitem__(self, name: str) -> _FakeCB:
        if name not in self._mapping:
            self._mapping[name] = _FakeCB([])
        return self._mapping[name]


def _now() -> datetime:
    return datetime(2026, 5, 7, 23, 30, tzinfo=timezone.utc)


def _stamped_doc(now: datetime, source_offset_s: int = 60) -> Dict[str, Any]:
    """One canonically-stamped cached_board doc."""
    src = now - timedelta(seconds=source_offset_s)
    return {
        "sport": "nba",
        "version_tag": "nba-cb-v1",
        "updated_at": now,
        "last_publish_ts": now,
        "source_score_max_scored_at": src,
        "props": [],
    }


def _build_db_for(
    nba_rows: List[Dict[str, Any]],
    mlb_rows: List[Dict[str, Any]],
    *,
    nba_score_max: Optional[datetime] = None,
    mlb_score_max: Optional[datetime] = None,
) -> _FakeDB:
    return _FakeDB({
        "nba_cached_board": _FakeCB(nba_rows),
        "mlb_cached_board": _FakeCB(mlb_rows),
        "nba_prop_scores":  _FakeCB(
            [{"scored_at": nba_score_max}] if nba_score_max else []
        ),
        "mlb_prop_scores":  _FakeCB(
            [{"scored_at": mlb_score_max}] if mlb_score_max else []
        ),
    })


def _run(db: _FakeDB):
    return asyncio.run(slo.check_tier_freshness(db, _now()))


# ─────────────────────────────────────────────────────────────────────
# §3 SLO tests — read-side contract
# ─────────────────────────────────────────────────────────────────────
def test_state_fresh_canonical_stamp_passes() -> None:
    now = _now()
    nba_rows = [_stamped_doc(now - timedelta(minutes=5))   for _ in range(3)]
    mlb_rows = [_stamped_doc(now - timedelta(minutes=10))  for _ in range(3)]
    res = _run(_build_db_for(nba_rows, mlb_rows))
    assert res.passed is True, res.failures
    assert res.evidence["nba"]["invariant_violation_docs"] == 0
    assert res.evidence["mlb"]["invariant_violation_docs"] == 0
    assert res.evidence["nba"]["missing_updated_at_docs"] == 0


def test_empty_collection_fails() -> None:
    res = _run(_build_db_for([], [_stamped_doc(_now())]))
    assert res.passed is False
    assert any("nba_cached_board is EMPTY" in f for f in res.failures), res.failures


def test_missing_updated_at_fails() -> None:
    now = _now()
    bad = _stamped_doc(now)
    bad.pop("updated_at")
    res = _run(_build_db_for([bad], [_stamped_doc(now)]))
    assert res.passed is False
    assert any("missing canonical `updated_at`" in f for f in res.failures), res.failures


def test_missing_version_tag_fails() -> None:
    now = _now()
    bad = _stamped_doc(now)
    bad.pop("version_tag")
    res = _run(_build_db_for([bad], [_stamped_doc(now)]))
    assert res.passed is False
    assert any("missing canonical `version_tag`" in f for f in res.failures), res.failures


def test_invariant_violation_fails() -> None:
    """updated_at < source_score_max_scored_at → writer correctness bug."""
    now = _now()
    bad = _stamped_doc(now)
    bad["updated_at"] = now - timedelta(minutes=10)
    bad["last_publish_ts"] = now - timedelta(minutes=10)
    bad["source_score_max_scored_at"] = now   # newer than updated_at
    res = _run(_build_db_for([bad], [_stamped_doc(now)]))
    assert res.passed is False
    assert any("violating `updated_at >= source_score_max_scored_at`" in f
               for f in res.failures), res.failures


def test_recency_violation_fails() -> None:
    """max(updated_at) older than master_sync cadence + grace → stalled."""
    now = _now()
    stale = now - timedelta(seconds=slo.CACHED_BOARD_MAX_AGE_S + 60)
    res = _run(_build_db_for([_stamped_doc(stale)], [_stamped_doc(now)]))
    assert res.passed is False
    assert any("STALE" in f and "nba_cached_board" in f for f in res.failures), res.failures


# ─────────────────────────────────────────────────────────────────────
# Writer-side tests — `services.board_freshness.build_freshness_stamp`
# ─────────────────────────────────────────────────────────────────────
def test_build_freshness_stamp_canonical_shape():
    from services.board_freshness import build_freshness_stamp
    now = _now()
    src = now - timedelta(seconds=42)
    stamp = build_freshness_stamp("nba", now=now, source_score_max_scored_at=src)
    assert set(stamp) == {
        "sport", "version_tag",
        "updated_at", "last_publish_ts",
        "source_score_max_scored_at",
    }
    assert stamp["sport"] == "nba"
    assert stamp["version_tag"] == "nba-cb-v1"
    assert stamp["updated_at"] == now
    assert stamp["last_publish_ts"] == now
    assert stamp["source_score_max_scored_at"] == src


def test_build_freshness_stamp_invariant_holds():
    """updated_at >= source_score_max_scored_at by construction."""
    from services.board_freshness import build_freshness_stamp
    now = _now()
    src = now - timedelta(seconds=300)
    stamp = build_freshness_stamp("mlb", now=now, source_score_max_scored_at=src)
    assert stamp["updated_at"] >= stamp["source_score_max_scored_at"]


def test_build_freshness_stamp_handles_naive_now():
    """Defensive: naive `now` should be coerced to UTC, not crash."""
    from services.board_freshness import build_freshness_stamp
    naive = datetime(2026, 5, 7, 23, 30)
    stamp = build_freshness_stamp("nba", now=naive)
    assert stamp["updated_at"].tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_max_scored_at_parses_iso_string():
    """`{sport}_prop_scores.scored_at` is persisted as an ISO STRING.
    The helper must parse it back to an aware datetime."""
    from services.board_freshness import _max_scored_at

    class _FakeCol:
        async def find_one(self, *args, **kwargs):
            return {"scored_at": "2026-05-07T23:30:00+00:00"}

    class _FakeDB2:
        def __getitem__(self, _): return _FakeCol()

    ts = await _max_scored_at(_FakeDB2(), "nba")
    assert ts is not None
    assert ts.tzinfo is not None
    assert ts.year == 2026 and ts.month == 5 and ts.day == 7
