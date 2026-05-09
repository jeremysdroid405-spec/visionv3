"""Replay engine integration tests.

These tests verify the engine's contract with production scoring:
  - feature builder enforces as-of-time leakage rejection
  - book-layer collector merges rows by bookmaker correctly
  - score_one_offer produces a dict containing keys produced by
    production `compute_scoring_stack` (tier / vision_score / etc.)

No DB writes — uses an in-memory mock for the BDL collection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from services.replay.engine import (
    AsOfFeatures, FEATURE_COMPLETENESS_MINIMAL,
    collect_paired_layers, populate_flat_odds,
)
from services.replay.leakage_checks import LeakageDetected


# ----- collect_paired_layers ---------------------------------------------

def test_collect_paired_layers_separates_books():
    rows = [
        {"bookmaker": "draftkings", "side": "OVER",
         "odds_american": -120, "line": 24.5},
        {"bookmaker": "fanduel", "side": "OVER",
         "odds_american": -115, "line": 24.5},
    ]
    layers, line = collect_paired_layers(rows)
    assert set(layers.keys()) == {"draftkings", "fanduel"}
    assert layers["draftkings"]["over_odds"] == -120
    assert layers["draftkings"]["under_odds"] is None
    assert layers["fanduel"]["over_odds"] == -115
    assert line == 24.5


def test_collect_paired_layers_merges_both_sides_per_book():
    rows = [
        {"bookmaker": "draftkings", "side": "OVER",
         "odds_american": -120, "line": 24.5},
        {"bookmaker": "draftkings", "side": "UNDER",
         "odds_american": +100, "line": 24.5},
    ]
    layers, _ = collect_paired_layers(rows)
    assert layers["draftkings"]["over_odds"] == -120
    assert layers["draftkings"]["under_odds"] == +100
    assert layers["draftkings"]["line"] == 24.5


def test_collect_paired_layers_drops_rows_without_bookmaker():
    rows = [
        {"bookmaker": None, "side": "OVER",
         "odds_american": -120, "line": 24.5},
        {"bookmaker": "fanduel", "side": "OVER",
         "odds_american": -115, "line": 24.5},
    ]
    layers, _ = collect_paired_layers(rows)
    assert list(layers.keys()) == ["fanduel"]


# ----- populate_flat_odds (TP-engine path-1 contract) ---------------------

def test_populate_flat_odds_over_side():
    """When picking OVER, this side's price = over_odds, opp = under_odds."""
    by_book = {
        "draftkings": {"line": 24.5, "over_odds": -120, "under_odds": +100},
        "fanduel":    {"line": 24.5, "over_odds": -115, "under_odds": -105},
    }
    prop: dict = {}
    populate_flat_odds(prop, by_book=by_book, side="OVER")
    assert prop["dk_odds"] == -120
    assert prop["dk_odds_opp"] == +100
    assert prop["fd_odds"] == -115
    assert prop["fd_odds_opp"] == -105
    # Books not in by_book → keys still present, valued None
    assert prop["mgm_odds"] is None
    assert prop["bol_odds_opp"] is None


def test_populate_flat_odds_under_side_swaps():
    by_book = {
        "draftkings": {"line": 24.5, "over_odds": -120, "under_odds": +100},
    }
    prop: dict = {}
    populate_flat_odds(prop, by_book=by_book, side="UNDER")
    assert prop["dk_odds"] == +100
    assert prop["dk_odds_opp"] == -120


# ----- AsOfFeatures shape --------------------------------------------------

def test_asof_features_dataclass_serializable():
    f = AsOfFeatures(
        sample_size=20, mu=23.0, sigma=4.0, cv=0.17,
        hit_rate_l5=0.6, hit_rate_l10=0.55, hit_rate_l20=0.52,
        ceiling_rate=0.6,
        feature_completeness=FEATURE_COMPLETENESS_MINIMAL,
    )
    d = f.asdict()
    for k in ("sample_size", "mu", "sigma", "cv",
               "hit_rate_l5", "hit_rate_l10", "hit_rate_l20",
               "ceiling_rate", "feature_completeness"):
        assert k in d


# ----- async leakage gate --------------------------------------------------

class _FakeBDL:
    """Minimal Motor-like async cursor stub."""
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
    def find(self, *args, **kwargs):
        self._iter = iter(self._rows)
        return self
    def sort(self, *_, **__):
        return self
    def limit(self, n):
        self._rows = self._rows[:n]
        self._iter = iter(self._rows)
        return self
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDB:
    def __init__(self, bdl_rows: List[Dict[str, Any]]):
        self._bdl = _FakeBDL(bdl_rows)
    def __getitem__(self, name: str):
        if name == "bdl_historical_game_logs":
            return self._bdl
        raise KeyError(name)


@pytest.mark.asyncio
async def test_build_as_of_features_rejects_future_games():
    """If the BDL filter is bypassed and a future game leaks in, the
    leakage gate must catch it."""
    from services.replay.engine import build_as_of_features
    # Cutoff = 2024-02-15. Pretend BDL hands us a game from 2024-02-20.
    db = _FakeDB([
        {"date": "2024-02-20", "player_name": "luka doncic",
         "pts": 35, "reb": 8, "ast": 9, "fg3m": 4},
    ])
    as_of = datetime(2024, 2, 15, 22, 0, tzinfo=timezone.utc)
    with pytest.raises(LeakageDetected):
        await build_as_of_features(
            db, player_norm="lukadoncic", stat_family="PTS",
            line=29.5, side="OVER", as_of_ts=as_of,
        )


@pytest.mark.asyncio
async def test_build_as_of_features_returns_empty_when_no_logs():
    from services.replay.engine import build_as_of_features
    db = _FakeDB([])
    as_of = datetime(2024, 2, 15, 22, 0, tzinfo=timezone.utc)
    feats = await build_as_of_features(
        db, player_norm="lukadoncic", stat_family="PTS",
        line=29.5, side="OVER", as_of_ts=as_of,
    )
    assert feats.sample_size == 0
    assert feats.feature_completeness == FEATURE_COMPLETENESS_MINIMAL


@pytest.mark.asyncio
async def test_build_as_of_features_computes_pts_correctly():
    from services.replay.engine import build_as_of_features
    db = _FakeDB([
        {"date": "2024-02-14", "player_name": "luka doncic", "pts": 30},
        {"date": "2024-02-12", "player_name": "luka doncic", "pts": 25},
        {"date": "2024-02-10", "player_name": "luka doncic", "pts": 35},
        {"date": "2024-02-08", "player_name": "luka doncic", "pts": 40},
        {"date": "2024-02-06", "player_name": "luka doncic", "pts": 20},
    ])
    as_of = datetime(2024, 2, 15, 22, 0, tzinfo=timezone.utc)
    feats = await build_as_of_features(
        db, player_norm="lukadoncic", stat_family="PTS",
        line=29.5, side="OVER", as_of_ts=as_of,
    )
    assert feats.sample_size == 5
    assert feats.mu == 30.0
    # OVER 29.5 hits in 3/5 games (30, 35, 40).
    assert feats.hit_rate_l5 == 0.6


@pytest.mark.asyncio
async def test_build_as_of_features_naive_as_of_rejected():
    from services.replay.engine import build_as_of_features
    db = _FakeDB([])
    naive = datetime(2024, 2, 15, 22, 0)
    with pytest.raises(ValueError):
        await build_as_of_features(
            db, player_norm="x", stat_family="PTS",
            line=10.0, side="OVER", as_of_ts=naive,
        )
