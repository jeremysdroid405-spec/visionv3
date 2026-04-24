"""Tests for services/probability/ecdf.py — the UniversalECDFProbability
layer (2026-04-24)."""
from __future__ import annotations

import os
import pickle
import pytest
import numpy as np

from services.probability.ecdf import (
    UniversalECDFProbability,
    ECDFPrediction,
    VERSION,
    DEFAULT_MIN_BUCKET_N,
)


def _records(n: int = 400, mean: float = 0.0, std: float = 1.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    projs = np.linspace(5, 30, n)   # spread projections across bucket range
    noise = rng.normal(mean, std, n)
    actuals = projs + noise
    return [
        {"projection": float(p), "actual": float(a)}
        for p, a in zip(projs, actuals)
    ]


# ---------------------------------------------------------------------
# Basic fit / save / load
# ---------------------------------------------------------------------
def test_fit_persists_artifact_and_metadata(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    art = ecdf.fit("nba", "pts", _records(500), source_model_version="vtest")
    assert art["sport"] == "nba"
    assert art["stat_family"] == "pts"
    assert art["version"] == VERSION
    assert art["source_model_version"] == "vtest"
    assert art["sample_count"] == 500
    assert art["n_buckets"] == 10
    assert art["projection_bucket_edges"].shape == (11,)
    assert np.isinf(art["projection_bucket_edges"][0])
    assert np.isinf(art["projection_bucket_edges"][-1])
    assert os.path.exists(ecdf.artifact_path("nba", "pts"))


def test_fit_too_few_records_raises(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    with pytest.raises(ValueError):
        ecdf.fit("nba", "pts", _records(n=50))  # < 10 * 20 floor


def test_save_load_roundtrip(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    art = ecdf.fit("nba", "reb", _records(400, mean=0.5, std=0.8))
    ecdf.invalidate("nba", "reb")   # drop cache
    loaded = ecdf.load("nba", "reb")
    assert loaded is not None
    assert loaded["stat_family"] == "reb"
    assert loaded["sample_count"] == 400
    np.testing.assert_array_equal(
        art["projection_bucket_edges"],
        loaded["projection_bucket_edges"],
    )


def test_load_missing_returns_none_and_caches(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    assert ecdf.load("mlb", "hits") is None
    # Second call hits the _load_attempted cache (no IO attempted)
    assert ecdf.load("mlb", "hits") is None
    assert not ecdf.is_available("mlb", "hits")


# ---------------------------------------------------------------------
# predict_over_probability — success + edge cases
# ---------------------------------------------------------------------
def test_predict_returns_prediction_object(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("nba", "pts", _records(1000))
    pred = ecdf.predict_over_probability("nba", "pts", 15.0, 14.5)
    assert isinstance(pred, ECDFPrediction)
    assert 0.0 <= pred.p_over <= 1.0
    assert 0.0 <= pred.p_under <= 1.0
    assert pred.p_over + pred.p_under == pytest.approx(1.0, abs=1e-9)
    assert pred.version == VERSION
    assert pred.bucket_n >= DEFAULT_MIN_BUCKET_N


def test_predict_none_inputs_return_none(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("nba", "pts", _records(500))
    assert ecdf.predict_over_probability("nba", "pts", None, 10.0) is None
    assert ecdf.predict_over_probability("nba", "pts", 10.0, None) is None


def test_predict_missing_artifact_returns_none(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    assert ecdf.predict_over_probability("nfl", "rushing_yards", 60, 55) is None


def test_predict_reproduces_empirical_tail(tmp_path):
    """The whole point of ECDF: output must match the empirical tail
    of the residual distribution in the sample's bucket."""
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    records = _records(2000, mean=0.0, std=1.0, seed=42)
    ecdf.fit("nba", "pts", records)
    # projection=15, line=16 → P(actual > line) = P(ε > 1)
    pred = ecdf.predict_over_probability("nba", "pts", 15.0, 16.0)
    # Compute expected empirical tail for the bucket containing proj=15
    art = ecdf.load("nba", "pts")
    inner = art["projection_bucket_edges"][1:-1]
    bucket = int(np.digitize(15.0, inner))
    r = art["sorted_residuals_by_bucket"][bucket]
    expected = float(np.mean(r > 1.0))
    assert pred.p_over == pytest.approx(expected, abs=1e-9)


def test_extreme_lines_clamp_to_unit_interval(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("nba", "pts", _records(500))
    far_below = ecdf.predict_over_probability("nba", "pts", 15.0, -100.0)
    far_above = ecdf.predict_over_probability("nba", "pts", 15.0, 100.0)
    assert far_below.p_over > 0.99
    assert far_above.p_over < 0.01


def test_context_param_is_silently_ignored_today(tmp_path):
    """Context is reserved for a future 2-D lookup. Today passing
    arbitrary context must not raise."""
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("nba", "pts", _records(500))
    with_ctx = ecdf.predict_over_probability(
        "nba", "pts", 15.0, 14.5,
        context={"minutes_bucket": "starter", "odds_bucket": "even"},
    )
    without_ctx = ecdf.predict_over_probability("nba", "pts", 15.0, 14.5)
    assert with_ctx.p_over == without_ctx.p_over


# ---------------------------------------------------------------------
# Sport / stat-family routing
# ---------------------------------------------------------------------
def test_sports_are_isolated(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("nba", "pts", _records(500, mean=0.0))
    # MLB artifact doesn't exist → returns None
    assert ecdf.predict_over_probability("mlb", "hits", 15.0, 14.5) is None
    # Write an MLB artifact and confirm it's served separately
    ecdf.fit("mlb", "hits", _records(500, mean=0.5))
    pred_nba = ecdf.predict_over_probability("nba", "pts", 15.0, 14.5)
    pred_mlb = ecdf.predict_over_probability("mlb", "hits", 15.0, 14.5)
    assert pred_nba is not None and pred_mlb is not None


def test_stat_family_case_insensitive(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    ecdf.fit("NBA", "PTS", _records(500))
    assert ecdf.is_available("nba", "pts")
    assert ecdf.is_available("Nba", "Pts")


def test_artifact_path_layout(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    assert ecdf.artifact_path("nba", "pts").endswith("/nba/pts.pkl")
    assert ecdf.artifact_path("MLB", "HITS").endswith("/mlb/hits.pkl")


# ---------------------------------------------------------------------
# Accept multiple record shapes
# ---------------------------------------------------------------------
def test_fit_accepts_tuple_records(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    tuples = [(float(p), float(a)) for p, a in zip(
        np.linspace(5, 25, 400),
        np.linspace(5, 25, 400) + np.random.default_rng(0).normal(0, 1, 400),
    )]
    ecdf.fit("nba", "ast", tuples)
    assert ecdf.is_available("nba", "ast")


def test_fit_accepts_dict_of_arrays(tmp_path):
    ecdf = UniversalECDFProbability(root=str(tmp_path))
    projs = np.linspace(5, 25, 400)
    acts = projs + np.random.default_rng(1).normal(0, 1, 400)
    ecdf.fit("nba", "3pm", {"projection": projs, "actual": acts})
    assert ecdf.is_available("nba", "3pm")


# ---------------------------------------------------------------------
# Parity with the migrated NBA artifacts (real pkls under default root)
# ---------------------------------------------------------------------
def test_real_nba_artifacts_parity_with_legacy_prob_ecdf_pkls():
    """The migration script must have produced universal artifacts
    that are *structurally equivalent* to the legacy flat pkls."""
    ecdf = UniversalECDFProbability()  # default root
    for stat in ("pts", "reb", "ast", "3pm", "pra"):
        uni = ecdf.load("nba", stat)
        if uni is None:
            pytest.skip(f"universal artifact missing for {stat}; skip")
        legacy_path = f"/app/backend/models/prob_ecdf_{stat}.pkl"
        if not os.path.exists(legacy_path):
            continue
        with open(legacy_path, "rb") as f:
            legacy = pickle.load(f)
        # Same bucket edges
        np.testing.assert_array_equal(
            uni["projection_bucket_edges"], legacy["bucket_edges"],
        )
        # Same sorted residuals per bucket
        for b in legacy["sorted_residuals_by_bucket"].keys():
            np.testing.assert_array_equal(
                uni["sorted_residuals_by_bucket"][int(b)],
                legacy["sorted_residuals_by_bucket"][int(b)],
            )


def test_real_nba_predict_parity_with_inline_legacy_logic():
    """Calling the universal predict must reproduce the numerical
    output of the inline legacy logic (np.digitize + searchsorted)
    bit-for-bit."""
    ecdf = UniversalECDFProbability()
    for stat in ("pts", "reb", "ast", "3pm", "pra"):
        art = ecdf.load("nba", stat)
        if art is None:
            pytest.skip(f"universal artifact missing for {stat}; skip")
        # Sample a few (proj, line) combos within the typical range
        test_cases = [
            (10.0, 9.5),
            (15.0, 16.5),
            (20.0, 18.5),
            (2.0, 2.5),
        ]
        edges = art["projection_bucket_edges"]
        inner = edges[1:-1]
        for proj, line in test_cases:
            pred = ecdf.predict_over_probability("nba", stat, proj, line)
            # Reproduce inline legacy logic
            bucket = int(np.digitize(proj, inner))
            r = art["sorted_residuals_by_bucket"].get(bucket)
            if r is None or len(r) < DEFAULT_MIN_BUCKET_N:
                assert pred is None
                continue
            needed = line - proj
            pos = int(np.searchsorted(r, needed, side="right"))
            expected = max(0.0, min(1.0, 1.0 - pos / len(r)))
            assert pred.p_over == pytest.approx(expected, abs=1e-9)
