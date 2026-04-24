"""Smoke test for MLB universal-ECDF artifacts (2026-04-24).

Confirms all major MLB stat families have a loaded ECDF artifact and
that .5-line lookups produce valid probabilities. No scoring adapter
wiring exercise — that's covered by the broader `test_universal_ecdf.py`
parity + shape tests."""
from __future__ import annotations

import pytest

from services.probability.ecdf import UniversalECDFProbability, VERSION

MLB_FAMILIES = [
    "hits", "total_bases", "strikeouts", "pitcher_strikeouts",
    "home_runs", "rbis", "runs", "hits_allowed", "walks", "singles",
]


@pytest.fixture(scope="module")
def ecdf():
    return UniversalECDFProbability()


def test_every_trained_mlb_family_loads(ecdf):
    for fam in MLB_FAMILIES:
        art = ecdf.load("mlb", fam)
        assert art is not None, f"MLB ECDF artifact missing for {fam}"
        assert art["version"] == VERSION
        assert art["sport"] == "mlb"
        assert art["stat_family"] == fam
        assert art["sample_count"] > 500


@pytest.mark.parametrize("fam,proj,line", [
    # .5 line sanity — user priority
    ("hits", 0.75, 0.5),
    ("total_bases", 1.0, 0.5),
    ("strikeouts", 0.8, 0.5),
    ("pitcher_strikeouts", 5.0, 4.5),
    ("home_runs", 0.15, 0.5),
    ("runs", 0.6, 0.5),
])
def test_half_line_probability_valid(ecdf, fam, proj, line):
    pred = ecdf.predict_over_probability("mlb", fam, proj, line)
    assert pred is not None, f"ECDF declined for mlb/{fam} @ line {line}"
    assert 0.0 <= pred.p_over <= 1.0
    assert pred.p_over + pred.p_under == pytest.approx(1.0, abs=1e-9)


def test_pitcher_outs_alias_resolves_to_pitcher_strikeouts(ecdf):
    """The hf model normalises `pitcher_outs` → `pitcher_strikeouts`.
    Since our ECDF artifacts use the canonical key, the scoring
    adapter looks them up post-normalisation. Confirm the canonical
    key has an artifact."""
    assert ecdf.is_available("mlb", "pitcher_strikeouts")


def test_missing_mlb_stat_family_returns_none(ecdf):
    # No artifact for these families — caller falls back to gaussian.
    assert ecdf.predict_over_probability(
        "mlb", "stolen_bases", 0.15, 0.5,
    ) is None
    assert ecdf.predict_over_probability(
        "mlb", "earned_runs", 2.5, 2.5,
    ) is None
