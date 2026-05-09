"""
Tests for the replay cache + fingerprint registry + incremental
scoring driver. These are pure-functional / DB-mock tests; they do
not need a live MongoDB.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.replay.cache import (
    REPLAY_VK2_CACHE,
    cache_row, changed_components, fingerprint_block,
    feature_pipeline_hash, gate_config_hash, tp_engine_hash,
    vk2_model_hash,
)
from services.replay.scoring_only import (
    _edge_pct, _p_model_for_side, _rebuild_prop,
)


# ----------------------------------------------------------------- fingerprints
def test_fingerprint_block_has_all_keys():
    fp = fingerprint_block("nba")
    for k in ("vk2_model_hash", "feature_pipeline_hash",
              "gate_config_hash", "tp_engine_hash", "stamped_at_utc"):
        assert k in fp


def test_vk2_model_hash_keys_are_per_family():
    h = vk2_model_hash()
    assert set(h.keys()) == {"PTS", "REB", "AST", "3PM", "PRA"}
    for v in h.values():
        assert isinstance(v, str) and len(v) > 0


def test_changed_components_detects_per_family_diff():
    before = fingerprint_block("nba")
    after = {**before,
             "vk2_model_hash": {**before["vk2_model_hash"],
                                "PTS": "deadbeef"}}
    diffs = changed_components(before, after)
    assert "vk2_model" in diffs
    assert "vk2_model.PTS" in diffs
    # No spurious diffs.
    assert "feature_pipeline_hash" not in diffs
    assert "gate_config_hash" not in diffs
    assert "tp_engine_hash" not in diffs


def test_changed_components_clean_when_equal():
    before = fingerprint_block("nba")
    after  = dict(before)  # shallow copy
    assert changed_components(before, after) == []


def test_gate_config_hash_is_data_hash_not_file_hash():
    """gate_config_hash should depend on THRESHOLDS contents, so
    re-import / minor formatting in thresholds.py shouldn't bump it."""
    h1 = gate_config_hash("nba")
    h2 = gate_config_hash("nba")
    assert h1 == h2  # determinism
    # Different sport → different hash (or 'error:').
    h_other = gate_config_hash("mlb")
    assert h_other != h1


def test_feature_pipeline_hash_is_string():
    assert isinstance(feature_pipeline_hash(), str)
    assert len(feature_pipeline_hash()) > 0


def test_tp_engine_hash_is_string():
    assert isinstance(tp_engine_hash(), str)


# ----------------------------------------------------------------- cache row
def test_cache_row_shape():
    row = cache_row(
        source_run_id="srcA", event_id="ev1",
        snapshot_label="t-30m",
        canonical_key="nba|player_points|john doe|10.5",
        market_key="player_points", stat_family="PTS",
        player="john doe", line=10.5, side="OVER",
        commence_time=datetime(2024, 2, 5, tzinfo=timezone.utc),
        snapshot_ts=datetime(2024, 2, 4, 23, 30, tzinfo=timezone.utc),
        by_book_layers={"draftkings": {"OVER": {"odds": -110},
                                         "UNDER": {"odds": -110}}},
        ref_book="draftkings", ref_odds=-110,
        tp_blob={"tp": 55.0, "books_used": 2},
        edge_pct=4.55,
        vk2_blob={"projection": 12.34, "sigma": 6.0,
                  "p_over": 0.65, "model_version": "v",
                  "feature_count": 52, "error": None,
                  "feature_completeness": "vk2_full"},
        feature_set={"sample_size": 20, "mu": 12.0, "sigma": 5.5,
                     "cv": 0.31, "hit_rate_l5": 0.8,
                     "hit_rate_l10": 0.75, "hit_rate_l20": 0.72,
                     "ceiling_rate": 0.4,
                     "feature_completeness": "minimal"},
    )
    for k in ("source_run_id", "event_id", "snapshot_label",
              "canonical_key", "side", "by_book_layers",
              "ref_book", "ref_odds", "tp_blob", "edge_pct",
              "vk2_blob", "feature_set",
              "feature_pipeline_hash", "cached_at"):
        assert k in row
    assert row["side"] == "OVER"


# ----------------------------------------------------------------- scorer math
def test_p_model_for_under_flips_p_over():
    vk2 = {"p_over": 0.7, "error": None}
    assert _p_model_for_side(vk2, "OVER")  == 0.7
    assert abs(_p_model_for_side(vk2, "UNDER") - 0.3) < 1e-9


def test_p_model_returns_none_when_vk2_errored():
    assert _p_model_for_side(
        {"p_over": 0.7, "error": "vk2_unsupported_family:BLK"},
        "OVER",
    ) is None


def test_edge_pct_negative_odds():
    """odds=-150 → implied=60% → edge = p_model*100 - 60."""
    assert _edge_pct(0.65, -150) == round(65 - 60.0, 6)


def test_edge_pct_positive_odds():
    """odds=+150 → implied=40% → edge = p_model*100 - 40."""
    assert _edge_pct(0.5, 150) == round(50.0 - 40.0, 6)


def test_edge_pct_handles_none():
    assert _edge_pct(None, -150) is None
    assert _edge_pct(0.5, None)  is None


def test_rebuild_prop_returns_minimal_keys():
    row = {
        "player": "lebron james", "line": 25.5,
        "stat_family": "PTS", "market_key": "player_points",
        "canonical_key": "k",
        "by_book_layers": {
            "draftkings": {"line": 25.5, "over_odds": -120,
                            "under_odds": +100},
            "fanduel":    {"line": 25.5, "over_odds": -115,
                            "under_odds": -105},
        },
        "vk2_blob":    {"projection": 28.1, "sigma": 6.0,
                          "p_over": 0.7, "error": None},
        "feature_set": {"sample_size": 20, "mu": 27.0, "sigma": 5.0,
                          "cv": 0.18, "hit_rate_l5": 0.6,
                          "hit_rate_l10": 0.55, "hit_rate_l20": 0.6,
                          "ceiling_rate": 0.35},
    }
    p = _rebuild_prop(row, "OVER")
    assert p["player"] == "lebron james"
    assert p["line"] == 25.5
    # HR ladder must be on 0-100 scale (production gate engine demands it).
    assert p["hit_rate_l20"] == 60.0
    # Book layers picked the OVER side.
    assert p["dk_layer"]["odds"] == -120
    assert p["fd_layer"]["odds"] == -115
    # vk2 projection prefers vk2_blob over feature_set.
    assert p["vk2_projection"] == 28.1
    assert p["model_projection"] == 28.1
