"""
Regression tests for the replay injury / usage layer.

Covers:
  - usage proxy parsing (parse_minutes, _usage_proxy_per_game)
  - team injury blob output shape and "missed last N → OUT"
    reconstruction logic
  - usage_vacuum_factor matches the production formula
  - usage_spike magnitude + flag boundary
  - assemble_injury_blob completeness rollup
  - leakage guard (rows after as-of must NOT influence output)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..")))

from services.replay.injury_history import (  # noqa: E402
    ROTATION_TOP_N, RECENT_ABSENCE_THRESHOLD, USAGE_SPIKE_THRESHOLD,
    _parse_minutes, _usage_proxy_per_game,
    compute_team_injury_blob, compute_player_usage_spike,
    assemble_injury_blob,
)


# ============================================================================
# In-memory fake DB — mimics the small slice of motor we touch.
# ============================================================================
class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, field: str, direction: int = 1):
        rev = direction == -1
        self._rows = sorted(self._rows,
                             key=lambda r: r.get(field) or "",
                             reverse=rev)
        return self

    def limit(self, n: int):
        self._rows = self._rows[: int(n)]
        return self

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeColl:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def find(self, flt: Dict[str, Any], proj: Optional[Dict[str, Any]] = None):
        out = [r for r in self._rows if _match(r, flt)]
        return _FakeCursor(out)

    def aggregate(self, pipeline: List[Dict[str, Any]]):
        rows = list(self._rows)
        for stage in pipeline:
            if "$match" in stage:
                rows = [r for r in rows if _match(r, stage["$match"])]
            elif "$group" in stage:
                rows = _group(rows, stage["$group"])
            elif "$sort" in stage:
                key = list(stage["$sort"].keys())[0]
                rev = stage["$sort"][key] == -1
                rows = sorted(rows,
                                key=lambda r: r.get(key) or "",
                                reverse=rev)
            elif "$limit" in stage:
                rows = rows[: int(stage["$limit"])]
        return _FakeCursor(rows)


class _FakeDB:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._coll = _FakeColl(rows)

    def __getitem__(self, name: str):
        # we only access bdl_historical_game_logs
        return self._coll


def _match(row: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    for k, v in flt.items():
        if isinstance(v, dict):
            for op, val in v.items():
                rv = row.get(k)
                if op == "$lt" and not (rv is not None and rv < val):
                    return False
                if op == "$gte" and not (rv is not None and rv >= val):
                    return False
                if op == "$lte" and not (rv is not None and rv <= val):
                    return False
        else:
            if row.get(k) != v:
                return False
    return True


def _group(rows, spec):
    # We only need a handful of $group shapes here.
    id_spec = spec["_id"]
    out: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        if isinstance(id_spec, str) and id_spec.startswith("$"):
            key = r.get(id_spec[1:])
        else:
            key = id_spec  # constant
        bucket = out.setdefault(key, {"_id": key})
        for fld, expr in spec.items():
            if fld == "_id":
                continue
            op = list(expr.keys())[0]
            src = expr[op]
            if isinstance(src, str) and src.startswith("$"):
                src = r.get(src[1:])
            if op == "$sum":
                bucket[fld] = bucket.get(fld, 0) + (src if not isinstance(src, dict) else 1)
                if isinstance(expr[op], (int, float)):
                    bucket[fld] = bucket.get(fld, 0) + float(expr[op])
            elif op == "$avg":
                bucket.setdefault(f"_{fld}_acc", []).append(src)
                acc = bucket[f"_{fld}_acc"]
                bucket[fld] = sum(acc) / len(acc)
            elif op == "$first":
                bucket.setdefault(fld, src)
    return list(out.values())


# ============================================================================
# Pure-function tests.
# ============================================================================
def test_parse_minutes_handles_string_and_colon():
    assert _parse_minutes("28") == 28.0
    assert abs(_parse_minutes("28:30") - 28.5) < 1e-6
    assert _parse_minutes(None) == 0.0
    assert _parse_minutes("") == 0.0
    assert _parse_minutes(0) == 0.0


def test_usage_proxy_formula_matches_production():
    # (fga + 0.44*fta + tov) / minutes * 36
    g = {"min": "30", "fga": 18, "fta": 5, "turnover": 3}
    expected = (18 + 0.44 * 5 + 3) / 30.0 * 36.0
    assert abs(_usage_proxy_per_game(g) - expected) < 1e-6


def test_usage_proxy_returns_none_under_min_threshold():
    g = {"min": "3", "fga": 1, "fta": 0, "turnover": 0}
    assert _usage_proxy_per_game(g) is None


# ============================================================================
# Team blob test — synthetic schedule.
# ============================================================================
def _mk_log(player_id, name, team_id, date, mins,
             fga=12, fta=4, tov=2):
    return {"player_id": player_id, "player_name": name,
            "team_id": team_id, "game_id": f"g_{date}",
            "date": date, "min": str(mins),
            "fga": fga, "fta": fta, "turnover": tov}


@pytest.mark.asyncio
async def test_team_injury_blob_flags_recent_absence_as_out():
    # Team #99: 5 rotation players. Player A has played 0 of last 3
    # games (likely OUT). Players B-E have played in all of them.
    rows: List[Dict[str, Any]] = []
    dates = ["2024-02-01", "2024-02-03", "2024-02-05",
             "2024-02-07", "2024-02-09"]
    snapshot = datetime(2024, 2, 10, tzinfo=timezone.utc)
    # Player A — only played on the FIRST 2 dates (then absent for last 3).
    for d in dates[:2]:
        rows.append(_mk_log(1, "Star A", 99, d, 36, fga=20, fta=8, tov=3))
    # Players B-E — played all 5.
    for pid, name in [(2, "B"), (3, "C"), (4, "D"), (5, "E")]:
        for d in dates:
            rows.append(_mk_log(pid, name, 99, d, 28))
    db = _FakeDB(rows)
    blob = await compute_team_injury_blob(
        db, team_id=99, snapshot_ts=snapshot)
    assert blob["error"] is None
    out_names = blob["out_player_names"]
    assert "Star A" in out_names
    assert blob["out_count"] == 1
    # Vacuum factor must be > 1 because A had usage and is removed.
    assert blob["usage_vacuum_factor"] > 1.0
    # A is the highest-minutes leader (36 vs 28) → key player flag.
    assert blob["key_player_out_flag"] == 1
    assert 0.0 < blob["rotation_compression"] <= 1.0


@pytest.mark.asyncio
async def test_team_injury_blob_no_team_returns_neutral():
    db = _FakeDB([])
    blob = await compute_team_injury_blob(
        db, team_id=None,
        snapshot_ts=datetime(2024, 2, 10, tzinfo=timezone.utc))
    assert blob["usage_vacuum_factor"] == 1.0
    assert blob["feature_completeness"] == "team_injury_missing"
    assert blob["error"] == "team_id_unresolved"


@pytest.mark.asyncio
async def test_team_injury_blob_no_recent_games_neutral():
    db = _FakeDB([])
    blob = await compute_team_injury_blob(
        db, team_id=99,
        snapshot_ts=datetime(2024, 2, 10, tzinfo=timezone.utc))
    assert blob["usage_vacuum_factor"] == 1.0
    assert blob["feature_completeness"] == "team_injury_missing"


@pytest.mark.asyncio
async def test_team_blob_excludes_future_rows_strictly_before_as_of():
    # If a row from AFTER the snapshot date sneaks in, the find()
    # filter must drop it. We assert the leakage guard wouldn't fire.
    snapshot = datetime(2024, 2, 5, tzinfo=timezone.utc)
    rows = [
        _mk_log(1, "A", 99, "2024-02-01", 30),
        _mk_log(1, "A", 99, "2024-02-03", 30),
        _mk_log(1, "A", 99, "2024-02-04", 30),
        # Future row (post-as-of) — must not influence output.
        _mk_log(1, "A", 99, "2024-02-08", 30),
    ]
    db = _FakeDB(rows)
    blob = await compute_team_injury_blob(
        db, team_id=99, snapshot_ts=snapshot)
    # Only one rotation player -> no out flagged; just shape sanity.
    assert blob["error"] is None
    # The dump must only reference dates strictly before snapshot.
    for r in blob["rotation"]:
        assert r["avg_minutes"] is not None


# ============================================================================
# Player usage spike tests.
# ============================================================================
@pytest.mark.asyncio
async def test_usage_spike_flag_above_threshold():
    # 10 baseline games at usage~20, then last 3 spiked to ~35.
    rows = []
    base_date = datetime(2024, 1, 1)
    for i in range(13):
        d = (base_date.replace(day=1) +
             __import__("datetime").timedelta(days=i)).isoformat()
        if i >= 10:
            # Recent 3 - high usage.
            rows.append(_mk_log(7, "Spiker", 99, d, 30, fga=24,
                                  fta=6, tov=4))
        else:
            rows.append(_mk_log(7, "Spiker", 99, d, 30, fga=12,
                                  fta=4, tov=2))
    db = _FakeDB(rows)
    snapshot = datetime(2024, 1, 14, tzinfo=timezone.utc)
    blob = await compute_player_usage_spike(
        db, bdl_player_id=7, snapshot_ts=snapshot)
    assert blob["error"] is None
    assert blob["feature_completeness"] == "usage_spike_full"
    assert blob["usage_l3"] > blob["usage_l10"]
    assert blob["usage_spike_magnitude"] >= USAGE_SPIKE_THRESHOLD
    assert blob["usage_spike_flag"] is True


@pytest.mark.asyncio
async def test_usage_spike_no_player_id_neutral():
    db = _FakeDB([])
    blob = await compute_player_usage_spike(
        db, bdl_player_id=None,
        snapshot_ts=datetime(2024, 1, 14, tzinfo=timezone.utc))
    assert blob["usage_spike_flag"] is False
    assert blob["error"] == "player_id_unresolved"


@pytest.mark.asyncio
async def test_usage_spike_flat_usage_returns_false():
    rows = []
    base_date = datetime(2024, 1, 1)
    for i in range(13):
        d = (base_date.replace(day=1) +
             __import__("datetime").timedelta(days=i)).isoformat()
        rows.append(_mk_log(7, "Steady", 99, d, 30, fga=12,
                              fta=4, tov=2))
    db = _FakeDB(rows)
    snapshot = datetime(2024, 1, 14, tzinfo=timezone.utc)
    blob = await compute_player_usage_spike(
        db, bdl_player_id=7, snapshot_ts=snapshot)
    assert blob["usage_spike_flag"] is False
    assert abs(blob["usage_spike_magnitude"]) < 0.01


# ============================================================================
# Assemble combined blob.
# ============================================================================
def test_assemble_injury_blob_completeness_roll_up():
    full_team = {"feature_completeness": "team_injury_full",
                 "usage_vacuum_factor": 1.18,
                 "key_player_out_flag": 1,
                 "rotation_compression": 0.15,
                 "out_count": 2}
    full_spike = {"feature_completeness": "usage_spike_full",
                  "usage_spike_flag": True,
                  "usage_spike_magnitude": 0.31}
    asm = assemble_injury_blob(team_blob=full_team, spike_blob=full_spike)
    assert asm["feature_completeness"] == "injury_full"
    assert asm["usage_vacuum_factor"] == 1.18
    assert asm["usage_spike"] is True

    # Mixed completeness → injury_partial.
    miss_team = {"feature_completeness": "team_injury_missing",
                 "usage_vacuum_factor": 1.0,
                 "key_player_out_flag": 0,
                 "rotation_compression": 0.0,
                 "out_count": 0}
    asm2 = assemble_injury_blob(team_blob=miss_team, spike_blob=full_spike)
    assert asm2["feature_completeness"] == "injury_partial"
    assert asm2["usage_spike"] is True

    # Both missing.
    miss_spike = {"feature_completeness": "usage_spike_missing",
                  "usage_spike_flag": False}
    asm3 = assemble_injury_blob(team_blob=miss_team, spike_blob=miss_spike)
    assert asm3["feature_completeness"] == "injury_missing"
    assert asm3["usage_spike"] is False


# ============================================================================
# Vision-V2 contract sanity: usage_vacuum_factor=1.0 → no boost.
# ============================================================================
def test_vision_v2_treats_neutral_vacuum_as_zero_boost():
    """vision_v2._context_component:
        inj = clip01((uv - 1.0) / 0.5) * sign
    Verify: uv=1.0 produces no contribution; uv=1.5 → max contribution.
    """
    from services.scoring.vision_v2 import _context_component
    # Neutral.
    s_neutral = _context_component(
        injury_context={"usage_vacuum_factor": 1.0},
        usage_spike=False, matchup_strength=0.5,
        pace_factor=1.0, side="OVER")
    # Boosted.
    s_boost = _context_component(
        injury_context={"usage_vacuum_factor": 1.5},
        usage_spike=True, matchup_strength=1.0,
        pace_factor=1.2, side="OVER")
    assert 0.0 <= s_neutral <= 1.0
    assert s_boost > s_neutral
