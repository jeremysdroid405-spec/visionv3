"""
JIT Vision Intel reaper tests.
==============================

Pinned contract (2026-05-08, JIT VI option B):

  * Reaper inspects ONLY visible board cards (via `services.board.reader.get_board`).
  * Eligible-for-Gemini set = visible cks whose LIVE-tag score doc has
    `vision_intel` null / empty / missing.
  * If 0 visible cks are uncovered → cheap no-op (no Gemini call).
  * If ≥1 uncovered → delegates to existing `_enrich_*_board_vision_intel`,
    which content-hash-filters and writes to BOTH live tags.
  * Hidden / non-visible siblings are NEVER touched.
  * Baseline-only rows are NEVER touched (only LIVE tag is queried).

Tests use an in-process fake mongo + monkeypatched get_board so no live
DB or Gemini calls happen.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Backend root on path so the helper module imports work.
_BACKEND_ROOT = Path("/app/backend")
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ─── Tiny async-mongo fake — enough surface for find/count_documents ─
class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def __aiter__(self):
        async def _gen():
            for r in self._rows:
                yield r
        return _gen()


class _FakeColl:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    async def count_documents(self, query):
        return sum(1 for r in self._rows if _matches(r, query))

    def find(self, query, projection=None):
        return _FakeCursor([r for r in self._rows if _matches(r, query)])


def _matches(row: Dict, query: Dict) -> bool:
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(row, sub) for sub in cond):
                return False
            continue
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
        return self._mapping.get(name, _FakeColl([]))


# ─── Stub `services.board.reader.get_board` ──────────────────────────
def _install_stub_get_board(picks_by_sport_tier: Dict[str, Dict[str, List[Dict]]]):
    import services.board.reader as reader_mod

    async def _stub(db, *, sport: str, tier: str, limit=None):
        return picks_by_sport_tier.get(sport, {}).get(tier, [])

    reader_mod.get_board = _stub  # type: ignore[assignment]


# ─── Stub the master_sync enrichers ──────────────────────────────────
class _Spy:
    def __init__(self):
        self.nba_calls = 0
        self.mlb_calls = 0

    async def nba_enrich(self, db):
        self.nba_calls += 1
        return {"to_call": 5, "cache_hits": 15, "gemini_calls": 1,
                "gemini_returned": 5, "gemini_empty_or_failed": 0,
                "score_docs_written": 5, "cached_board_writes": 5}

    async def mlb_enrich(self, db):
        self.mlb_calls += 1
        return {"to_call": 0, "cache_hits": 20, "gemini_calls": 0,
                "gemini_returned": 0, "gemini_empty_or_failed": 0,
                "score_docs_written": 0, "cached_board_writes": 0}


def _install_spy_enrichers(spy: _Spy):
    import services.master_sync as ms
    ms._enrich_nba_board_vision_intel = spy.nba_enrich  # type: ignore[assignment]
    ms._enrich_mlb_board_vision_intel = spy.mlb_enrich  # type: ignore[assignment]


# ─── Common fixture ──────────────────────────────────────────────────
def _build_db(rows_by_coll):
    return _FakeDB({k: _FakeColl(v) for k, v in rows_by_coll.items()})


# ─────────────────────────────────────────────────────────────────────
# 1. Empty visible board → no enrichment call
# ─────────────────────────────────────────────────────────────────────
def test_reaper_no_op_on_empty_board():
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )
    _install_stub_get_board({"nba": {"safe_haven": [], "front_lines": [], "war_zone": []}})
    spy = _Spy()
    _install_spy_enrichers(spy)
    db = _build_db({"nba_prop_scores": []})
    metrics = asyncio.run(run_jit_vision_intel_reaper_for_sport(db, "nba"))
    assert metrics["uncovered_visible"] == 0
    assert metrics["would_call_enrichment"] is False
    assert metrics["skipped_reason"] == "no_uncovered_visible_picks"
    assert spy.nba_calls == 0


# ─────────────────────────────────────────────────────────────────────
# 2. All visible already covered → no enrichment call
# ─────────────────────────────────────────────────────────────────────
def test_reaper_no_op_when_all_visible_covered():
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )
    _install_stub_get_board({
        "nba": {
            "safe_haven": [{"canonical_key": "nba|c1"}],
            "front_lines": [{"canonical_key": "nba|c2"}],
            "war_zone": [],
        }
    })
    spy = _Spy()
    _install_spy_enrichers(spy)
    rows = [
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c1", "vision_intel": "x"},
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c2", "vision_intel": "y"},
    ]
    db = _build_db({"nba_prop_scores": rows})
    metrics = asyncio.run(run_jit_vision_intel_reaper_for_sport(db, "nba"))
    assert metrics["uncovered_visible"] == 0
    assert metrics["would_call_enrichment"] is False
    assert spy.nba_calls == 0


# ─────────────────────────────────────────────────────────────────────
# 3. One visible uncovered → enrichment called once
# ─────────────────────────────────────────────────────────────────────
def test_reaper_calls_enrichment_when_visible_uncovered():
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )
    _install_stub_get_board({
        "nba": {
            "safe_haven": [{"canonical_key": "nba|c1"}],
            "front_lines": [{"canonical_key": "nba|c2"}],
            "war_zone": [],
        }
    })
    spy = _Spy()
    _install_spy_enrichers(spy)
    rows = [
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c1", "vision_intel": "x"},
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c2", "vision_intel": None},
    ]
    db = _build_db({"nba_prop_scores": rows})
    metrics = asyncio.run(run_jit_vision_intel_reaper_for_sport(db, "nba"))
    assert metrics["uncovered_visible"] == 1
    assert metrics["would_call_enrichment"] is True
    assert spy.nba_calls == 1


# ─────────────────────────────────────────────────────────────────────
# 4. Hidden uncovered prop_score rows do NOT trigger enrichment
# ─────────────────────────────────────────────────────────────────────
def test_reaper_ignores_hidden_uncovered_rows():
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )
    _install_stub_get_board({
        "nba": {
            "safe_haven": [{"canonical_key": "nba|c1"}],
            "front_lines": [],
            "war_zone": [],
        }
    })
    spy = _Spy()
    _install_spy_enrichers(spy)
    rows = [
        # Visible, covered.
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c1", "vision_intel": "x"},
        # Hidden, uncovered — must NOT trigger reaper.
        {"version_tag": "final-nba-rt", "canonical_key": "nba|hidden_a", "vision_intel": None},
        {"version_tag": "final-nba-rt", "canonical_key": "nba|hidden_b"},  # missing field
    ]
    db = _build_db({"nba_prop_scores": rows})
    metrics = asyncio.run(run_jit_vision_intel_reaper_for_sport(db, "nba"))
    assert metrics["uncovered_visible"] == 0
    assert spy.nba_calls == 0


# ─────────────────────────────────────────────────────────────────────
# 5. Baseline-only uncovered rows do NOT trigger enrichment
# ─────────────────────────────────────────────────────────────────────
def test_reaper_ignores_baseline_only_rows():
    from services.jit_vision_intel_reaper import (
        run_jit_vision_intel_reaper_for_sport,
    )
    _install_stub_get_board({
        "nba": {"safe_haven": [{"canonical_key": "nba|c1"}], "front_lines": [], "war_zone": []}
    })
    spy = _Spy()
    _install_spy_enrichers(spy)
    rows = [
        # LIVE: covered.
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c1", "vision_intel": "x"},
        # BASELINE: uncovered — but the discovery query filters by LIVE tag only.
        {"version_tag": "final-nba", "canonical_key": "nba|c1", "vision_intel": None},
    ]
    db = _build_db({"nba_prop_scores": rows})
    metrics = asyncio.run(run_jit_vision_intel_reaper_for_sport(db, "nba"))
    assert metrics["uncovered_visible"] == 0
    assert spy.nba_calls == 0


# ─────────────────────────────────────────────────────────────────────
# 6. find_visible_uncovered_cks — pure helper, isolated
# ─────────────────────────────────────────────────────────────────────
def test_find_visible_uncovered_cks_filters_live_only():
    from services.jit_vision_intel_reaper import find_visible_uncovered_cks
    _install_stub_get_board({
        "mlb": {
            "safe_haven": [{"canonical_key": "mlb|a"}],
            "front_lines": [{"canonical_key": "mlb|b"}],
            "war_zone": [],
        }
    })
    rows = [
        # LIVE:
        {"version_tag": "final-mlb-rt", "canonical_key": "mlb|a", "vision_intel": "covered"},
        {"version_tag": "final-mlb-rt", "canonical_key": "mlb|b"},  # missing → uncovered
        # BASELINE: should NOT count
        {"version_tag": "final-mlb", "canonical_key": "mlb|a"},  # baseline missing
    ]
    db = _build_db({"mlb_prop_scores": rows})
    out = asyncio.run(find_visible_uncovered_cks(db, "mlb"))
    assert out == ["mlb|b"]


# ─────────────────────────────────────────────────────────────────────
# 7. all-sports dispatcher delegates to per-sport runner
# ─────────────────────────────────────────────────────────────────────
def test_all_sports_dispatcher_runs_each_sport():
    from services.jit_vision_intel_reaper import run_jit_vision_intel_reaper_all_sports
    _install_stub_get_board({
        "nba": {"safe_haven": [{"canonical_key": "nba|c1"}], "front_lines": [], "war_zone": []},
        "mlb": {"safe_haven": [{"canonical_key": "mlb|c1"}], "front_lines": [], "war_zone": []},
    })
    spy = _Spy()
    _install_spy_enrichers(spy)
    rows = [
        {"version_tag": "final-nba-rt", "canonical_key": "nba|c1", "vision_intel": None},
        {"version_tag": "final-mlb-rt", "canonical_key": "mlb|c1", "vision_intel": "x"},
    ]
    db = _build_db({"nba_prop_scores": rows, "mlb_prop_scores": rows})
    out = asyncio.run(run_jit_vision_intel_reaper_all_sports(db))
    assert "nba" in out["per_sport"] and "mlb" in out["per_sport"]
    assert out["per_sport"]["nba"]["uncovered_visible"] == 1
    assert out["per_sport"]["mlb"]["uncovered_visible"] == 0
    assert spy.nba_calls == 1
    assert spy.mlb_calls == 0
