"""
SLO §4 (`check_detection_source_freshness`) unit tests.
=======================================================

Phase 4B follow-up (2026-05-07): the original §4 check failed any time
the dirty queue was empty, which is the *steady state* for low-volume
sports (MLB drains its 5k-row queue in <60 s while the next ingest
cycle is still 5 min away). This module pins the two valid healthy
states and the four primary failure states defined by Phase 4B.

Valid healthy states (PASS):
  STATE 1 — queue has recent activity (last_enqueue lag < threshold)
            and bounded depth.
  STATE 2 — queue is empty (depth == 0, no last_enqueue) AND
            live_props.max(updated_at) is fresh AND
            prop_scores.max(scored_at) is fresh AND
            no watchdog FROZEN / RESTART_STORM events.

Invalid states (FAIL):
  * empty queue + stale live_props
  * empty queue + stale prop_scores
  * unbounded depth
  * watchdog/restart-storm events present
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─── Load the SLO module by file path (it lives under scripts/, not a
#     package, so import-by-name doesn't work) ─────────────────────────
_SCRIPT_PATH = Path("/app/backend/scripts/production_readiness_slo_check.py")
_spec = importlib.util.spec_from_file_location("_slo_check_mod", _SCRIPT_PATH)
slo = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["_slo_check_mod"] = slo
_spec.loader.exec_module(slo)  # type: ignore[union-attr]


# ─── Tiny async-mongo fake — only the surface §4 actually calls ──────
class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def sort(self, *_args, **_kwargs) -> "_FakeCursor":
        # Caller passes [("_id", -1)] — our seeds are already sorted
        # newest-first; honour that.
        return self

    def limit(self, n: int) -> "_FakeCursor":
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length: int) -> List[Dict[str, Any]]:
        return self._rows[:length]


class _FakeColl:
    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        max_ts_field: Optional[str] = None,
        max_ts_value: Optional[datetime] = None,
    ):
        self._rows = rows or []
        self._max_ts_field = max_ts_field
        self._max_ts_value = max_ts_value

    async def count_documents(self, query: Dict[str, Any]) -> int:
        sport = query.get("sport")
        if sport is None:
            return len(self._rows)
        return sum(1 for r in self._rows if r.get("sport") == sport)

    def find(self, query: Dict[str, Any], projection: Dict[str, Any] | None = None):
        sport = query.get("sport")
        rows = (
            [r for r in self._rows if r.get("sport") == sport]
            if sport
            else list(self._rows)
        )
        return _FakeCursor(rows)

    async def find_one(self, *args, **kwargs):
        # `_max_ts` invokes find_one(projection=..., sort=...). Honor
        # only the configured (field, value) pair.
        if self._max_ts_value is None or self._max_ts_field is None:
            return None
        return {self._max_ts_field: self._max_ts_value}


class _FakeDB:
    def __init__(self, mapping: Dict[str, _FakeColl]):
        self._mapping = mapping

    def __getitem__(self, name: str) -> _FakeColl:
        if name not in self._mapping:
            self._mapping[name] = _FakeColl()
        return self._mapping[name]


def _now() -> datetime:
    return datetime(2026, 5, 7, 22, 30, tzinfo=timezone.utc)


def _build_db(
    *,
    nba_queue_rows: List[Dict[str, Any]],
    mlb_queue_rows: List[Dict[str, Any]],
    nba_live_max: Optional[datetime],
    mlb_live_max: Optional[datetime],
    nba_score_max: Optional[datetime],
    mlb_score_max: Optional[datetime],
) -> _FakeDB:
    return _FakeDB({
        "delta_dirty_queue": _FakeColl(rows=nba_queue_rows + mlb_queue_rows),
        "nba_live_props":    _FakeColl(max_ts_field="updated_at", max_ts_value=nba_live_max),
        "mlb_live_props":    _FakeColl(max_ts_field="updated_at", max_ts_value=mlb_live_max),
        "nba_prop_scores":   _FakeColl(max_ts_field="scored_at",  max_ts_value=nba_score_max),
        "mlb_prop_scores":   _FakeColl(max_ts_field="scored_at",  max_ts_value=mlb_score_max),
    })


def _run(db: _FakeDB):
    # Always pin watchdog count to 0 unless the test overrides it.
    slo._count_watchdog_events = lambda *a, **kw: 0  # type: ignore[attr-defined]
    return asyncio.run(slo.check_detection_source_freshness(db, _now()))


# ─────────────────────────────────────────────────────────────────────
# STATE 1 — active queue, bounded depth, fresh live + scores → PASS
# ─────────────────────────────────────────────────────────────────────
def test_state1_active_queue_passes() -> None:
    now = _now()
    fresh = now - timedelta(seconds=30)
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh}],
        mlb_queue_rows=[{"sport": "mlb", "enqueued_at": fresh}],
        nba_live_max=fresh,
        mlb_live_max=fresh,
        nba_score_max=fresh,
        mlb_score_max=fresh,
    )
    res = _run(db)
    assert res.passed is True, res.failures
    assert res.evidence["nba"]["healthy_state"] == "STATE_1_active_queue"
    assert res.evidence["mlb"]["healthy_state"] == "STATE_1_active_queue"


# ─────────────────────────────────────────────────────────────────────
# STATE 2 — queue empty + fresh live_props + fresh scores → PASS
# (the case that motivated the fix)
# ─────────────────────────────────────────────────────────────────────
def test_state2_empty_queue_with_fresh_live_props_passes() -> None:
    now = _now()
    fresh = now - timedelta(seconds=45)
    fresh_nba = now - timedelta(seconds=15)
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh_nba}],
        mlb_queue_rows=[],   # MLB queue drained
        nba_live_max=fresh_nba,
        mlb_live_max=fresh,
        nba_score_max=fresh_nba,
        mlb_score_max=fresh,
    )
    res = _run(db)
    assert res.passed is True, res.failures
    assert res.evidence["mlb"]["queue_depth"] == 0
    assert res.evidence["mlb"]["last_enqueue_at"] is None
    assert res.evidence["mlb"]["healthy_state"] == "STATE_2_empty_queue_drained_clean"


# ─────────────────────────────────────────────────────────────────────
# Invalid: queue empty AND live_props stale → FAIL
# ─────────────────────────────────────────────────────────────────────
def test_empty_queue_with_stale_live_props_fails() -> None:
    now = _now()
    fresh = now - timedelta(seconds=30)
    stale = now - timedelta(minutes=12)   # > FRESHNESS_MAX_AGE_S (5 min)
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh}],
        mlb_queue_rows=[],
        nba_live_max=fresh,
        mlb_live_max=stale,
        nba_score_max=fresh,
        mlb_score_max=fresh,
    )
    res = _run(db)
    assert res.passed is False
    assert any("mlb_live_props STALE" in f for f in res.failures), res.failures
    assert res.evidence["mlb"]["healthy_state"] == "INVALID"


# ─────────────────────────────────────────────────────────────────────
# Invalid: queue empty AND scores stale → FAIL
# ─────────────────────────────────────────────────────────────────────
def test_empty_queue_with_stale_scores_fails() -> None:
    now = _now()
    fresh = now - timedelta(seconds=30)
    stale = now - timedelta(minutes=15)
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh}],
        mlb_queue_rows=[],
        nba_live_max=fresh,
        mlb_live_max=fresh,
        nba_score_max=fresh,
        mlb_score_max=stale,
    )
    res = _run(db)
    assert res.passed is False
    assert any("mlb_prop_scores STALE" in f for f in res.failures), res.failures


# ─────────────────────────────────────────────────────────────────────
# Invalid: queue depth unbounded → FAIL
# ─────────────────────────────────────────────────────────────────────
def test_unbounded_queue_depth_fails() -> None:
    now = _now()
    fresh = now - timedelta(seconds=30)
    overflow_count = slo.DIRTY_QUEUE_DEPTH_HARD_CAP + 5
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh}] * overflow_count,
        mlb_queue_rows=[{"sport": "mlb", "enqueued_at": fresh}],
        nba_live_max=fresh,
        mlb_live_max=fresh,
        nba_score_max=fresh,
        mlb_score_max=fresh,
    )
    res = _run(db)
    assert res.passed is False
    assert any("UNBOUNDED" in f and "nba" in f for f in res.failures), res.failures


# ─────────────────────────────────────────────────────────────────────
# Invalid: watchdog FROZEN / RESTART_STORM events present → FAIL
# (even when everything else is clean)
# ─────────────────────────────────────────────────────────────────────
def test_watchdog_events_force_fail() -> None:
    now = _now()
    fresh = now - timedelta(seconds=30)
    db = _build_db(
        nba_queue_rows=[{"sport": "nba", "enqueued_at": fresh}],
        mlb_queue_rows=[],
        nba_live_max=fresh,
        mlb_live_max=fresh,
        nba_score_max=fresh,
        mlb_score_max=fresh,
    )
    slo._count_watchdog_events = lambda *a, **kw: 3  # type: ignore[attr-defined]
    res = asyncio.run(slo.check_detection_source_freshness(db, now))
    assert res.passed is False
    assert any("watchdog" in f for f in res.failures), res.failures
