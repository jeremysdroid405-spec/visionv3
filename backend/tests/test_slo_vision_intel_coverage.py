"""
SLO §6 (`check_vision_intel_coverage`) unit tests — universe correction.
========================================================================

Pinned contract (2026-05-07 §6 fix-D):

  Universe = the visible board cards (canonical_keys returned by
             `services.board.reader.get_board()`) at the LIVE
             `version_tag` per sport.

  Threshold = 80% (UNCHANGED — fix is a universe correction, not a
              relaxation).

  Baseline-tag mirror coverage is reported for observability only;
  it does NOT gate the SLO.

These tests use a tiny in-process fake mongo + a stub get_board() so
they run in CI without a live database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


# ─── Load the SLO module by file path ─────────────────────────────────
_BACKEND_ROOT = Path("/app/backend")
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_SCRIPT_PATH = _BACKEND_ROOT / "scripts" / "production_readiness_slo_check.py"
_spec = importlib.util.spec_from_file_location("_slo_check_for_vi_test", _SCRIPT_PATH)
slo = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["_slo_check_for_vi_test"] = slo
_spec.loader.exec_module(slo)  # type: ignore[union-attr]


# ─── Stub get_board so tests never hit a real DB ─────────────────────
class _StubBoardReader:
    """Replaces `services.board.reader.get_board` for the duration of
    a single test. The slo check imports `from services.board.reader
    import get_board` at function scope, so we monkeypatch the
    `services.board.reader` module object directly."""

    def __init__(self, picks_by_sport_tier: Dict[str, Dict[str, List[Dict]]]):
        self._picks = picks_by_sport_tier

    async def __call__(self, db, *, sport: str, tier: str, limit=None):
        return self._picks.get(sport, {}).get(tier, [])


def _install_stub_get_board(stub: _StubBoardReader) -> None:
    import services.board.reader as reader_mod  # noqa: WPS433
    reader_mod.get_board = stub  # type: ignore[assignment]


# ─── Tiny async-mongo fake — only what §6 calls ──────────────────────
class _FakeColl:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    async def count_documents(self, query: Dict[str, Any]) -> int:
        return sum(1 for r in self._rows if _matches(r, query))


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        val = row.get(key)
        if isinstance(cond, dict):
            for op, opv in cond.items():
                if op == "$in":
                    if val not in opv:
                        return False
                elif op == "$nin":
                    if val in opv:
                        return False
                elif op == "$exists":
                    present = key in row
                    if bool(opv) != present:
                        return False
                elif op == "$ne":
                    if val == opv:
                        return False
                else:
                    raise NotImplementedError(f"_matches: op {op!r}")
        else:
            if val != cond:
                return False
    return True


class _FakeDB:
    def __init__(self, mapping: Dict[str, _FakeColl]):
        self._mapping = mapping

    def __getitem__(self, name: str) -> _FakeColl:
        if name not in self._mapping:
            self._mapping[name] = _FakeColl([])
        return self._mapping[name]


def _row(*, ck: str, tier: str, version_tag: str, vision_intel: Any = None) -> Dict[str, Any]:
    r = {
        "canonical_key": ck,
        "tier":          tier,
        "active":        True,
        "version_tag":   version_tag,
    }
    if vision_intel is not None:
        r["vision_intel"] = vision_intel
    return r


def _now() -> datetime:
    return datetime(2026, 5, 8, 0, 30, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# 1. Visible-card universe with full coverage on LIVE tag → PASS
#    (baseline-tag uncovered does NOT gate)
# ─────────────────────────────────────────────────────────────────────
def test_full_visible_coverage_passes_even_when_baseline_uncovered() -> None:
    nba_visible = [{"canonical_key": f"nba|ck{i}"} for i in range(5)]
    mlb_visible = [{"canonical_key": f"mlb|ck{i}"} for i in range(5)]
    _install_stub_get_board(_StubBoardReader({
        "nba": {"safe_haven": nba_visible[:2], "front_lines": nba_visible[2:], "war_zone": []},
        "mlb": {"safe_haven": mlb_visible[:1], "front_lines": mlb_visible[1:], "war_zone": []},
    }))

    nba_rows: List[Dict] = []
    mlb_rows: List[Dict] = []
    for i in range(5):
        # LIVE tag — covered.
        nba_rows.append(_row(ck=f"nba|ck{i}", tier="safe_haven" if i < 2 else "front_lines",
                             version_tag="final-nba-rt", vision_intel="x"))
        # BASELINE tag — uncovered.
        nba_rows.append(_row(ck=f"nba|ck{i}", tier="safe_haven" if i < 2 else "front_lines",
                             version_tag="final-nba"))
        mlb_rows.append(_row(ck=f"mlb|ck{i}", tier="safe_haven" if i < 1 else "front_lines",
                             version_tag="final-mlb-rt", vision_intel="x"))
        mlb_rows.append(_row(ck=f"mlb|ck{i}", tier="safe_haven" if i < 1 else "front_lines",
                             version_tag="final-mlb"))

    db = _FakeDB({"nba_prop_scores": _FakeColl(nba_rows), "mlb_prop_scores": _FakeColl(mlb_rows)})
    res = asyncio.run(slo.check_vision_intel_coverage(db, _now()))
    assert res.passed is True, res.failures
    assert res.evidence["nba"]["coverage_pct"] == 100.0
    assert res.evidence["nba"]["baseline_coverage_pct"] == 0.0   # observability
    assert res.evidence["mlb"]["coverage_pct"] == 100.0


# ─────────────────────────────────────────────────────────────────────
# 2. Hidden / non-visible siblings do NOT count against coverage
# ─────────────────────────────────────────────────────────────────────
def test_hidden_siblings_not_counted_in_universe() -> None:
    """Add 50 non-visible LIVE-tag rows, all uncovered. Must NOT
    drop coverage."""
    nba_visible = [{"canonical_key": f"nba|ck{i}"} for i in range(3)]
    _install_stub_get_board(_StubBoardReader({
        "nba": {"safe_haven": nba_visible, "front_lines": [], "war_zone": []},
        "mlb": {"safe_haven": [{"canonical_key": "mlb|c0"}], "front_lines": [], "war_zone": []},
    }))

    rows: List[Dict] = []
    for i in range(3):  # visible — covered
        rows.append(_row(ck=f"nba|ck{i}", tier="safe_haven",
                         version_tag="final-nba-rt", vision_intel="x"))
    for i in range(50):  # invisible — uncovered (DOES NOT COUNT)
        rows.append(_row(ck=f"nba|hidden{i}", tier="safe_haven",
                         version_tag="final-nba-rt"))
    mlb_rows = [_row(ck="mlb|c0", tier="safe_haven",
                     version_tag="final-mlb-rt", vision_intel="x")]

    db = _FakeDB({"nba_prop_scores": _FakeColl(rows),
                  "mlb_prop_scores": _FakeColl(mlb_rows)})
    res = asyncio.run(slo.check_vision_intel_coverage(db, _now()))
    assert res.passed is True, res.failures
    assert res.evidence["nba"]["total_visible"] == 3
    assert res.evidence["nba"]["coverage_pct"] == 100.0


# ─────────────────────────────────────────────────────────────────────
# 3. Below-threshold visible coverage → FAIL  (threshold UNCHANGED)
# ─────────────────────────────────────────────────────────────────────
def test_below_threshold_visible_coverage_fails() -> None:
    nba_visible = [{"canonical_key": f"nba|ck{i}"} for i in range(10)]
    _install_stub_get_board(_StubBoardReader({
        "nba": {"safe_haven": nba_visible, "front_lines": [], "war_zone": []},
        "mlb": {"safe_haven": [{"canonical_key": "mlb|c0"}], "front_lines": [], "war_zone": []},
    }))

    rows = []
    for i in range(10):
        rows.append(_row(
            ck=f"nba|ck{i}", tier="safe_haven", version_tag="final-nba-rt",
            vision_intel=("x" if i < 7 else None),   # 7 / 10 = 70 % < 80 %
        ))
    mlb_rows = [_row(ck="mlb|c0", tier="safe_haven",
                     version_tag="final-mlb-rt", vision_intel="x")]

    db = _FakeDB({"nba_prop_scores": _FakeColl(rows),
                  "mlb_prop_scores": _FakeColl(mlb_rows)})
    res = asyncio.run(slo.check_vision_intel_coverage(db, _now()))
    assert res.passed is False
    assert any("coverage 70.0%" in f for f in res.failures), res.failures
    # Threshold is preserved.
    assert slo.VISION_COVERAGE_MIN_PCT == 80.0


# ─────────────────────────────────────────────────────────────────────
# 4. Empty visible board → FAIL with clear message
# ─────────────────────────────────────────────────────────────────────
def test_empty_visible_board_fails() -> None:
    _install_stub_get_board(_StubBoardReader({
        "nba": {"safe_haven": [], "front_lines": [], "war_zone": []},
        "mlb": {"safe_haven": [{"canonical_key": "mlb|c0"}], "front_lines": [], "war_zone": []},
    }))
    mlb_rows = [_row(ck="mlb|c0", tier="safe_haven",
                     version_tag="final-mlb-rt", vision_intel="x")]
    db = _FakeDB({"nba_prop_scores": _FakeColl([]),
                  "mlb_prop_scores": _FakeColl(mlb_rows)})
    res = asyncio.run(slo.check_vision_intel_coverage(db, _now()))
    assert res.passed is False
    assert any("NO visible board picks" in f for f in res.failures), res.failures


# ─────────────────────────────────────────────────────────────────────
# 5. Baseline-tag mirror is OBSERVED but never blocks PASS
# ─────────────────────────────────────────────────────────────────────
def test_baseline_uncovered_never_blocks_pass() -> None:
    nba_visible = [{"canonical_key": f"nba|ck{i}"} for i in range(5)]
    _install_stub_get_board(_StubBoardReader({
        "nba": {"safe_haven": nba_visible, "front_lines": [], "war_zone": []},
        "mlb": {"safe_haven": [{"canonical_key": "mlb|c0"}], "front_lines": [], "war_zone": []},
    }))
    rows: List[Dict] = []
    for i in range(5):
        rows.append(_row(ck=f"nba|ck{i}", tier="safe_haven",
                         version_tag="final-nba-rt", vision_intel="x"))
        # baseline rows ZERO covered
        rows.append(_row(ck=f"nba|ck{i}", tier="safe_haven", version_tag="final-nba"))
    mlb_rows = [_row(ck="mlb|c0", tier="safe_haven",
                     version_tag="final-mlb-rt", vision_intel="x")]
    db = _FakeDB({"nba_prop_scores": _FakeColl(rows),
                  "mlb_prop_scores": _FakeColl(mlb_rows)})
    res = asyncio.run(slo.check_vision_intel_coverage(db, _now()))
    assert res.passed is True, res.failures
    assert res.evidence["nba"]["baseline_coverage_pct"] == 0.0
    assert res.evidence["nba"]["coverage_pct"] == 100.0
