"""
Regression tests for Statcast ingest heartbeat (Operational PR C).

Verifies:
  1. scanned > 0 → status=ok (no spurious warnings)
  2. scanned == 0 IN-SEASON → status=warning (no longer silent)
  3. scanned == 0 OFF-SEASON → status=ok (off-day heuristic respected)
  4. scanned == 0 IN-SEASON with previous=warning → status=error
     (2-in-a-row escalation)
  5. heartbeat row + sync_history row are both written
  6. /api/admin/mlb/statcast-health returns the worst-of-last-3 status

These tests use an in-memory-style fake Mongo so they don't depend on
the live DB. No model / scoring / gate code is touched.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")

from services.scheduled.statcast_heartbeat import (  # noqa: E402
    _classify,
    _count_consecutive_failures,
    _is_in_mlb_season,
    record_heartbeat,
    get_health_summary,
    HEARTBEAT_COLL,
)


# ---------------------------------------------------------------------------
# Tiny async fake Mongo — just enough to back the heartbeat module.
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, docs, sort_key=None, sort_dir=-1, limit=None):
        if sort_key is not None:
            docs = sorted(
                docs, key=lambda d: d.get(sort_key) or "", reverse=(sort_dir < 0),
            )
        if limit is not None:
            docs = docs[:limit]
        self._docs = list(docs)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]; self._i += 1
        return d


class _FakeColl:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    def find(self, q=None, projection=None, sort=None, limit=None):
        docs = list(self.docs)
        if q:
            for k, v in q.items():
                docs = [d for d in docs if d.get(k) == v]
        sort_key, sort_dir = (sort[0] if sort else (None, -1))
        return _FakeCursor(docs, sort_key, sort_dir, limit)

    async def find_one(self, q=None, sort=None, projection=None):
        if sort is not None:
            sort_key, sort_dir = sort[0]
            docs = sorted(
                self.docs, key=lambda d: d.get(sort_key) or "",
                reverse=(sort_dir < 0),
            )
        else:
            docs = list(self.docs)
        for d in docs:
            return d
        return None


class _FakeDB:
    def __init__(self):
        self._colls: Dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())

    def __getattr__(self, name):
        return self.__getitem__(name)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------
def test_classify_ok_when_scanned_gt_zero():
    status, _ = _classify(scanned=1234, start="2026-05-15", end="2026-05-15", prev_status=None)
    assert status == "ok"


def test_classify_warning_when_zero_in_season():
    status, reason = _classify(scanned=0, start="2026-05-13", end="2026-05-15", prev_status="ok")
    assert status == "warning", f"expected warning, got {status} ({reason})"


def test_classify_ok_when_zero_off_season():
    status, _ = _classify(scanned=0, start="2026-01-10", end="2026-01-12", prev_status="ok")
    assert status == "ok"
    status, _ = _classify(scanned=0, start="2026-12-25", end="2026-12-25", prev_status="ok")
    assert status == "ok"


def test_classify_error_when_two_in_a_row():
    status, reason = _classify(
        scanned=0, start="2026-05-13", end="2026-05-15", prev_status="warning",
    )
    assert status == "error", f"expected error, got {status} ({reason})"
    status2, _ = _classify(
        scanned=0, start="2026-05-13", end="2026-05-15", prev_status="error",
    )
    assert status2 == "error"


def test_is_in_mlb_season_boundaries():
    from datetime import date
    assert _is_in_mlb_season(date(2026, 3, 25)) is True
    assert _is_in_mlb_season(date(2026, 3, 24)) is False
    assert _is_in_mlb_season(date(2026, 11, 5)) is True
    assert _is_in_mlb_season(date(2026, 11, 6)) is False
    assert _is_in_mlb_season(date(2026, 7, 1)) is True


def test_count_consecutive_failures():
    docs = [
        {"status": "warning"},
        {"status": "warning"},
        {"status": "ok"},
        {"status": "error"},
    ]
    assert _count_consecutive_failures(docs) == 2
    assert _count_consecutive_failures([{"status": "ok"}, {"status": "error"}]) == 0
    assert _count_consecutive_failures([]) == 0
    assert _count_consecutive_failures([{"status": "error"}, {"status": "error"}, {"status": "error"}]) == 3


# ---------------------------------------------------------------------------
# Integration test — full record_heartbeat path with fake DB
# ---------------------------------------------------------------------------
async def _scenario_two_in_a_row_escalation():
    db = _FakeDB()

    # First run: scanned=0 in-season → expect WARNING
    res1 = await record_heartbeat(
        db, start="2026-05-13", end="2026-05-15",
        scanned=0, inserted=0, updated=0, errors=0,
    )
    assert res1["status"] == "warning", (
        f"first zero-row run should warn, got {res1['status']}"
    )
    assert res1["previous_status"] is None

    # Heartbeat collection should have 1 row
    assert len(db[HEARTBEAT_COLL].docs) == 1
    # sync_history should also have 1 row
    assert len(db.sync_history.docs) == 1
    assert db.sync_history.docs[0]["status"] == "warning"

    # Second run: scanned=0 again in-season → expect ERROR
    res2 = await record_heartbeat(
        db, start="2026-05-14", end="2026-05-16",
        scanned=0, inserted=0, updated=0, errors=0,
    )
    assert res2["status"] == "error", (
        f"second zero-row run should escalate to error, got {res2['status']}"
    )
    assert res2["previous_status"] == "warning"

    # Third run: ingest recovers (3000 rows) → expect OK, but consecutive
    # failure count from health summary should be 0 since newest is OK.
    res3 = await record_heartbeat(
        db, start="2026-05-15", end="2026-05-17",
        scanned=3000, inserted=2500, updated=500, errors=0,
    )
    assert res3["status"] == "ok"

    # Health summary — worst of last 3 wins
    health = await get_health_summary(db)
    # Last 3 are [ok, error, warning] → worst = error
    assert health["overall_status"] == "error", (
        f"worst-of-3 should be error, got {health['overall_status']}"
    )
    assert health["consecutive_failures"] == 0  # newest is ok
    return res1, res2, res3, health


async def _scenario_in_season_recovers():
    db = _FakeDB()
    # Healthy run first
    await record_heartbeat(db, start="2026-05-10", end="2026-05-12",
                            scanned=5000, inserted=5000, updated=0, errors=0)
    # One bad run
    r2 = await record_heartbeat(db, start="2026-05-11", end="2026-05-13",
                                  scanned=0, inserted=0, updated=0, errors=0)
    assert r2["status"] == "warning"
    # Recovery
    r3 = await record_heartbeat(db, start="2026-05-12", end="2026-05-14",
                                  scanned=4500, inserted=4500, updated=0, errors=0)
    assert r3["status"] == "ok"
    health = await get_health_summary(db)
    # last 3 = [ok, warning, ok] → worst = warning
    assert health["overall_status"] == "warning"


async def _scenario_off_season_zero_is_silent():
    db = _FakeDB()
    r = await record_heartbeat(db, start="2026-12-20", end="2026-12-22",
                                 scanned=0, inserted=0, updated=0, errors=0)
    assert r["status"] == "ok", (
        f"off-season zero-row should be ok, got {r['status']}"
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    # Run all sync tests
    test_classify_ok_when_scanned_gt_zero()
    test_classify_warning_when_zero_in_season()
    test_classify_ok_when_zero_off_season()
    test_classify_error_when_two_in_a_row()
    test_is_in_mlb_season_boundaries()
    test_count_consecutive_failures()
    print("[OK] pure-function tests")

    # Run async scenarios
    r1, r2, r3, health = _run(_scenario_two_in_a_row_escalation())
    print("\n[OK] two-in-a-row escalation:")
    print(f"  run1: status={r1['status']} reason={r1['reason']}")
    print(f"  run2: status={r2['status']} reason={r2['reason']}")
    print(f"  run3: status={r3['status']} reason={r3['reason']}")
    print(f"  health.overall_status={health['overall_status']}  "
          f"consecutive_failures={health['consecutive_failures']}")

    _run(_scenario_in_season_recovers())
    print("[OK] in-season recovery scenario")

    _run(_scenario_off_season_zero_is_silent())
    print("[OK] off-season silence scenario")

    print("\nAll heartbeat tests passed.")
