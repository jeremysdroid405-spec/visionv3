"""Unit tests for build_team_prop_features (Phase 2B)."""
from __future__ import annotations
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")

import pytest
from pymongo import UpdateOne

from scripts.sgo.build_team_prop_features import (
    stable_key,
    assemble_prop_features_doc,
    build_prop_features_for_sport,
    _PriorsCache,
    BUILDER_VERSION,
    DST_COLL,
)


# ───── stable_key ─────
def test_stable_key_picks_six_fields():
    row = {
        "event_id": "E1", "team_id": "mlb_bos",
        "market_key": "spread", "side": "OVER",
        "line": -1.5, "book": "pinnacle",
        "_extra": "ignored",
    }
    k = stable_key(row)
    assert k == {
        "event_id": "E1", "team_id": "mlb_bos",
        "market_key": "spread", "side": "OVER",
        "line": -1.5, "book": "pinnacle",
    }


def test_stable_key_idempotent_for_same_input():
    row = {"event_id": "E1", "team_id": "x", "market_key": "h2h",
           "side": "HOME", "line": None, "book": "pinnacle"}
    assert stable_key(row) == stable_key(row.copy())


# ───── assemble_prop_features_doc ─────
def test_assemble_lifts_outcome_fields_and_embeds_priors():
    outcome = {
        "event_id": "E1", "team_id": "mlb_bos",
        "opponent_team_id": "mlb_nyy", "sport": "mlb",
        "game_date": "2024-07-05", "commence_time": "2024-07-05T17:05:00Z",
        "home_away": "home",
        "market_key": "spread-home-game-bos", "market_category": "spread",
        "market_name": "Run Line", "side": "HOME", "line": -1.5,
        "book": "pinnacle", "odds": -110, "is_alternate": False,
        "periodID": "game", "betTypeID": "sp",
        "outcome": "WIN", "hit": True, "push": False,
        "outcome_resolved": True, "outcome_numeric": 1,
        "margin_vs_line": 1.5, "home_score_used": 7.0,
        "away_score_used": 4.0, "actual_value": 3.0,
        "_internal_field": "should_be_dropped",
    }
    team_pri = {"sample_size": 21, "win_rate_l10": 0.6,
                  "feature_completeness": "team_v1_priors"}
    opp_pri = {"sample_size": 19, "win_rate_l10": 0.4,
                "feature_completeness": "team_v1_priors"}
    doc = assemble_prop_features_doc(
        outcome, team_features=team_pri, opponent_features=opp_pri)
    # All outcome whitelist fields present
    assert doc["event_id"] == "E1"
    assert doc["outcome"] == "WIN"
    assert doc["hit"] is True
    assert doc["line"] == -1.5
    # Non-whitelisted internal field dropped
    assert "_internal_field" not in doc
    # Priors embedded
    assert doc["team_features"] is team_pri
    assert doc["opponent_features"] is opp_pri
    assert doc["team_features_completeness"] == "team_v1_priors"
    assert doc["opponent_features_completeness"] == "team_v1_priors"
    assert doc["builder_version"] == BUILDER_VERSION
    # Timestamp is tz-aware UTC
    assert doc["computed_at"].tzinfo is not None


def test_assemble_with_null_priors():
    outcome = {"event_id": "E1", "team_id": "x", "sport": "nba",
                "outcome": "LOSS", "hit": False}
    doc = assemble_prop_features_doc(
        outcome, team_features=None, opponent_features=None)
    assert doc["team_features"] is None
    assert doc["opponent_features"] is None
    assert doc["team_features_completeness"] is None
    assert doc["opponent_features_completeness"] is None


# ───── _PriorsCache ─────
class _FakeCursor:
    def __init__(self, docs): self._docs = docs
    def batch_size(self, _n): return self
    def __aiter__(self):
        async def gen():
            for d in self._docs: yield d
        return gen()


class _FakeColl:
    def __init__(self):
        self._docs: List[Dict[str, Any]] = []
        self._bulk_calls: List[List[Any]] = []
        self.find_one_calls = 0
    def set_docs(self, docs): self._docs = list(docs)
    def find(self, _match, projection=None): return _FakeCursor(self._docs)
    async def find_one(self, match, projection=None):
        self.find_one_calls += 1
        for d in self._docs:
            if all(d.get(k) == v for k, v in match.items()):
                return d
        return None
    async def count_documents(self, _m):
        return len(self._docs)
    async def create_index(self, *a, **kw): return None
    async def bulk_write(self, ops, ordered=False):
        self._bulk_calls.append(list(ops))
        class R:
            upserted_count = sum(1 for o in ops if isinstance(o, UpdateOne))
            modified_count = 0
        return R()


class _FakeDB:
    def __init__(self): self._colls: Dict[str, _FakeColl] = {}
    def __getitem__(self, n):
        return self._colls.setdefault(n, _FakeColl())


@pytest.mark.asyncio
async def test_priors_cache_collapses_repeats():
    """Many outcomes share the same (sport, team_id, game_date), so a
    cache should drastically reduce the find_one count."""
    db = _FakeDB()
    db["team_model_features"].set_docs([
        {"sport": "nba", "team_id": "nba_bos", "as_of_date": "2024-12-01",
         "sample_size": 10, "win_rate_l10": 0.7,
         "feature_completeness": "team_v1_priors"},
    ])
    cache = _PriorsCache(db)
    for _ in range(50):
        d = await cache.get(sport="nba", team_id="nba_bos",
                              as_of_date="2024-12-01")
        assert d is not None
    assert db["team_model_features"].find_one_calls == 1
    assert cache.hits == 49
    assert cache.misses == 1


@pytest.mark.asyncio
async def test_priors_cache_returns_none_when_missing():
    db = _FakeDB()
    cache = _PriorsCache(db)
    d = await cache.get(sport="nba", team_id="nba_xyz",
                          as_of_date="2024-12-01")
    assert d is None
    # Missing keys are also cached to avoid hammering the DB
    d2 = await cache.get(sport="nba", team_id="nba_xyz",
                          as_of_date="2024-12-01")
    assert d2 is None
    assert db["team_model_features"].find_one_calls == 1


# ───── orchestrator ─────
def _outcome_row(**overrides):
    base = {
        "sport": "mlb", "event_id": "E1", "team_id": "mlb_bos",
        "opponent_team_id": "mlb_nyy",
        "game_date": "2024-07-05", "commence_time": "2024-07-05T17:00:00Z",
        "home_away": "home",
        "market_key": "spread-home-game-bos", "market_category": "spread",
        "market_name": "Run Line", "side": "HOME", "line": -1.5,
        "book": "pinnacle", "odds": -110, "is_alternate": False,
        "outcome": "WIN", "hit": True, "push": False,
        "outcome_resolved": True, "outcome_numeric": 1,
        "margin_vs_line": 1.5, "home_score_used": 7.0,
        "away_score_used": 4.0, "actual_value": 3.0,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_orchestrator_dry_run_emits_no_writes():
    db = _FakeDB()
    db["team_historical_outcomes"].set_docs([_outcome_row()])
    db["team_model_features"].set_docs([
        {"sport": "mlb", "team_id": "mlb_bos", "as_of_date": "2024-07-05",
         "sample_size": 20, "win_rate_l10": 0.7,
         "feature_completeness": "team_v1_priors"},
        {"sport": "mlb", "team_id": "mlb_nyy", "as_of_date": "2024-07-05",
         "sample_size": 18, "win_rate_l10": 0.5,
         "feature_completeness": "team_v1_priors"},
    ])
    r = await build_prop_features_for_sport(
        db, sport="mlb", dry_run=True, force=False,
        max_props=10, bulk_chunk=10)
    assert r["counters"]["scanned"] == 1
    assert r["counters"]["rows_emitted"] == 1
    assert r["counters"]["rows_written"] == 0    # dry-run
    assert db["team_model_prop_features"]._bulk_calls == []


@pytest.mark.asyncio
async def test_orchestrator_live_upserts_with_correct_key():
    db = _FakeDB()
    db["team_historical_outcomes"].set_docs([_outcome_row()])
    db["team_model_features"].set_docs([
        {"sport": "mlb", "team_id": "mlb_bos", "as_of_date": "2024-07-05",
         "sample_size": 20, "win_rate_l10": 0.7,
         "feature_completeness": "team_v1_priors"},
    ])
    r = await build_prop_features_for_sport(
        db, sport="mlb", dry_run=False, force=False,
        max_props=10, bulk_chunk=10)
    bulk = db["team_model_prop_features"]._bulk_calls
    assert len(bulk) == 1
    assert len(bulk[0]) == 1
    op = bulk[0][0]
    assert isinstance(op, UpdateOne)
    assert op._filter == {
        "event_id": "E1", "team_id": "mlb_bos",
        "market_key": "spread-home-game-bos",
        "side": "HOME", "line": -1.5, "book": "pinnacle",
    }
    set_doc = op._doc["$set"]
    assert set_doc["team_features"]["sample_size"] == 20
    assert set_doc["opponent_features"] is None   # not in features coll
    assert set_doc["builder_version"] == BUILDER_VERSION
    assert r["counters"]["rows_written"] == 1
    # Opponent prior was missing
    assert r["counters"]["opponent_priors_missing"] == 1


@pytest.mark.asyncio
async def test_orchestrator_counts_both_missing():
    db = _FakeDB()
    db["team_historical_outcomes"].set_docs([_outcome_row()])
    # No feature rows for either team
    r = await build_prop_features_for_sport(
        db, sport="mlb", dry_run=True, force=False,
        max_props=10, bulk_chunk=10)
    assert r["counters"]["both_priors_missing"] == 1
    assert r["counters"]["team_priors_missing"] == 0
    assert r["counters"]["opponent_priors_missing"] == 0


@pytest.mark.asyncio
async def test_orchestrator_bulk_chunking_flushes_correctly():
    db = _FakeDB()
    # 7 outcomes, chunk size 3 → expect 3 bulk_write calls (3+3+1)
    rows = [_outcome_row(event_id=f"E{i}", line=float(i)) for i in range(7)]
    db["team_historical_outcomes"].set_docs(rows)
    r = await build_prop_features_for_sport(
        db, sport="mlb", dry_run=False, force=False,
        max_props=1000, bulk_chunk=3)
    bulk = db["team_model_prop_features"]._bulk_calls
    sizes = [len(b) for b in bulk]
    assert sizes == [3, 3, 1]
    assert r["counters"]["rows_written"] == 7


@pytest.mark.asyncio
async def test_orchestrator_respects_max_props_cap():
    db = _FakeDB()
    rows = [_outcome_row(event_id=f"E{i}", line=float(i)) for i in range(10)]
    db["team_historical_outcomes"].set_docs(rows)
    r = await build_prop_features_for_sport(
        db, sport="mlb", dry_run=True, force=False,
        max_props=4, bulk_chunk=10)
    # The script breaks after scanning > max_props (5th iteration).
    assert r["counters"]["scanned"] == 5
    assert r["counters"]["rows_emitted"] == 4   # the 5th breaks before append
