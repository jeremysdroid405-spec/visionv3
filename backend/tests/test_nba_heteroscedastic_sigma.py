"""Tests for NBA Phase 2 heteroscedastic sigma (2026-05-02).

Covers:
- Bucket classifier boundaries (minutes + line)
- Multiplier lookup wiring (known stat × bucket → known multiplier)
- Safety clamp ([0.4, 2.5] total multiplier window)
- Null-safety (missing stat / bucket → base σ unchanged)
- Real-data coverage floor (no hetero_sigma_base regression on recompute)
"""
from __future__ import annotations

import pytest

from config.nba_sigma_heteroscedastic import (
    BASE_SIGMAS,
    LINE_QUARTILES,
    MULTIPLIER_TABLES,
    line_bucket_for,
    minutes_bucket_for,
    sigma_for_prop,
)


class TestMinutesBucket:
    @pytest.mark.parametrize("mins,expected", [
        (None,    None),
        (15.0,    "0_22"),
        (21.9,    "0_22"),
        (22.0,    "22_28"),
        (27.9,    "22_28"),
        (28.0,    "28_34"),
        (33.9,    "28_34"),
        (34.0,    "34_plus"),
        (40.0,    "34_plus"),
        ("bad",   None),
    ])
    def test_classifier_boundaries(self, mins, expected):
        assert minutes_bucket_for(mins) == expected


class TestLineBucket:
    @pytest.mark.parametrize("stat,line,expected", [
        ("PTS", None,  None),
        ("PTS", 10.0,  "low"),          # < q25=15.5
        ("PTS", 15.5,  "low"),          # == q25 → inclusive low
        ("PTS", 16.0,  "mid_low"),      # > q25 <= q50
        ("PTS", 19.5,  "mid_low"),
        ("PTS", 20.0,  "mid_high"),     # > q50 <= q75
        ("PTS", 24.5,  "mid_high"),
        ("PTS", 25.0,  "high"),         # > q75
        ("PTS", 35.0,  "high"),
        ("UNKNOWN", 10.0, None),
        ("PTS", "bad", None),
    ])
    def test_pts_quartile_bins(self, stat, line, expected):
        assert line_bucket_for(stat, line) == expected

    def test_reb_boundaries_match_table(self):
        q = LINE_QUARTILES["REB"]
        assert line_bucket_for("REB", q["q25"]) == "low"
        assert line_bucket_for("REB", q["q50"]) == "mid_low"
        assert line_bucket_for("REB", q["q75"]) == "mid_high"
        assert line_bucket_for("REB", q["q75"] + 0.5) == "high"


class TestSigmaForProp:
    def test_no_features_returns_base(self):
        sig, mults = sigma_for_prop("PTS")
        assert sig == BASE_SIGMAS["PTS"]
        assert mults == {}

    def test_unknown_stat_returns_none(self):
        sig, mults = sigma_for_prop("UNKNOWN", minutes_bucket="34_plus")
        assert sig is None
        assert mults == {}

    def test_pts_mid_high_high_minutes_wider(self):
        # PTS mid_high=1.18; 28_34=1.19. Product ~1.404 clipped to [0.4,2.5].
        sig, mults = sigma_for_prop(
            "PTS", minutes_bucket="28_34", line_bucket="mid_high",
        )
        assert mults == {"minutes_bucket": 1.19, "line_bucket": 1.18}
        # sigma_for_prop returns BASE_SIGMAS[stat] × product
        assert sig == pytest.approx(BASE_SIGMAS["PTS"] * 1.19 * 1.18, rel=1e-3)

    def test_clamp_upper_bound(self, monkeypatch):
        # Monkeypatch PTS buckets to force product > 2.5, then verify
        # sigma_for_prop clamps to 2.5× base.
        fake_tables = {
            "PTS": {
                "minutes_bucket": {"28_34": 2.0},
                "line_bucket":    {"mid_high": 2.0},
            },
        }
        monkeypatch.setattr(
            "config.nba_sigma_heteroscedastic.MULTIPLIER_TABLES", fake_tables,
        )
        sig, mults = sigma_for_prop(
            "PTS", minutes_bucket="28_34", line_bucket="mid_high",
        )
        assert mults == {"minutes_bucket": 2.0, "line_bucket": 2.0}
        # 2.0 * 2.0 = 4.0 → clamped to 2.5
        assert sig == pytest.approx(BASE_SIGMAS["PTS"] * 2.5, rel=1e-3)

    def test_clamp_lower_bound(self, monkeypatch):
        fake_tables = {
            "PTS": {
                "minutes_bucket": {"0_22": 0.5},
                "line_bucket":    {"low":  0.5},
            },
        }
        monkeypatch.setattr(
            "config.nba_sigma_heteroscedastic.MULTIPLIER_TABLES", fake_tables,
        )
        sig, _ = sigma_for_prop(
            "PTS", minutes_bucket="0_22", line_bucket="low",
        )
        # 0.5 * 0.5 = 0.25 → clamped to 0.4
        assert sig == pytest.approx(BASE_SIGMAS["PTS"] * 0.4, rel=1e-3)


class TestTablesHaveRealData:
    """Guardrail: the lookup tables must contain bucket data for the
    four primary stats. Tables stripped to empty (accidental revert) is
    caught here."""

    @pytest.mark.parametrize("stat", ["PTS", "PRA", "REB", "AST"])
    def test_stat_has_at_least_one_bucket(self, stat):
        axes = MULTIPLIER_TABLES.get(stat) or {}
        bucket_count = sum(len(v) for v in axes.values())
        assert bucket_count >= 2, (
            f"{stat} has {bucket_count} populated buckets. "
            "Rebuild via scripts/build_nba_sigma_buckets.py."
        )

    def test_all_multipliers_in_safety_window(self):
        """Every persisted multiplier must be in [0.5, 2.0] per the
        build script's clip. This catches a manual edit that slipped a
        value outside the safety window."""
        for stat, axes in MULTIPLIER_TABLES.items():
            for axis, buckets in axes.items():
                for bname, mult in buckets.items():
                    assert 0.5 <= mult <= 2.0, (
                        f"{stat}.{axis}.{bname}={mult} outside [0.5, 2.0]"
                    )

    def test_line_quartiles_monotonic(self):
        """q25 <= q50 <= q75 for every stat or the bucket classifier
        will silently misroute lines."""
        for stat, q in LINE_QUARTILES.items():
            assert q["q25"] <= q["q50"] <= q["q75"], (
                f"{stat} quartiles non-monotonic: {q}"
            )
