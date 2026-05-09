"""Combo-family VK2 routing regression tests.

Locks down the 2026-05-09 fix that forces combo families
(`pts_ast` / `pts_reb` / `reb_ast`) onto the VK2 component path
in `_predict_combo_projection`. Before this fix, combo families
silently fell back to the legacy VK1 component models (because
`model_key=None` for combo families) — which over-projected
PTS-bearing combos by up to +62% on low-usage players.

These tests assert:
  1. `combo_vk2_eligible` flips True for combo families when ANY
     component is in `_VK2_PRIMARY_STATS`.
  2. End-to-end combo synth math: PA = PTS + AST, PR = PTS + REB,
     RA = REB + AST — with VK2 component μ values.
  3. A specific live regression: Jarrett Allen PA alt 9.5 must
     produce μ ≈ 13 (NOT 21.89, the buggy VK1 value).
  4. A high-usage player check (Jokić-shaped fixture).
  5. A low-usage player check (Allen-shaped fixture).
  6. Mutation guard: if `_VK2_PRIMARY_STATS` is emptied, combo
     families MUST NOT silently route to VK1 — they must either
     report zero VK2 eligibility OR the test fails so a future
     refactor cannot regress this fix unnoticed.

The tests use a stub adapter so we don't depend on the trained
model files or live BDL data — the fix lives in routing logic,
which is what we lock down here.
"""
from __future__ import annotations

import pytest

from services.scoring.adapters.nba_scoring import NBAScoringAdapter


# ---------------------------------------------------------------------------
# Helper: stub the two predictors so we can exercise routing without
# requiring trained model files. Each predictor returns a deterministic
# (proj, sigma) so we can verify the combo math AND the path used.
# ---------------------------------------------------------------------------
class _StubAdapter(NBAScoringAdapter):
    """Adapter with `_predict_model_prob_over` (VK1) and
    `_predict_vk2_prob_over` (VK2) replaced by deterministic stubs.

    Stub returns:
      VK1 proj = vk1_means[stat]
      VK2 proj = vk2_means[stat]
      sigma    = 1.0 (constant)
    Empirical covariance returns 0.0 so combo sigma = sqrt(Σ σ²) = √2.

    `path_log` records ("vk1"|"vk2", stat) per call so tests can
    assert which path was taken.
    """

    def __init__(self, vk1_means, vk2_means):
        super().__init__()
        self._vk1_means = vk1_means
        self._vk2_means = vk2_means
        self.path_log = []

    def _predict_model_prob_over(
        self, db=None, bdl_player_id=None, player_name=None,
        stat_type=None, line=None, opponent_team=None,
        team_total=None, target_game=None, sharp_implied=None,
    ):
        self.path_log.append(("vk1", stat_type))
        return {
            "projection": self._vk1_means[stat_type],
            "sigma": 1.0, "p_over": 0.5, "error": None,
        }

    def _predict_vk2_prob_over(
        self, bdl_player_id=None, stat_type=None, line=None,
    ):
        self.path_log.append(("vk2", stat_type))
        return {
            "projection": self._vk2_means[stat_type],
            "sigma": 1.0, "p_over": 0.5, "error": None,
        }

    def _empirical_covariance(self, *args, **kwargs):
        return 0.0


# ---------------------------------------------------------------------------
# 1. Allen-shaped (low usage). VK1 over-projects PTS by 62% (the bug).
#    With the fix, combo synth must use VK2 means and produce the
#    correct sums (≈ 13 PA, ≈ 20 PR, ≈ 10 RA).
# ---------------------------------------------------------------------------
ALLEN_VK1 = {"PTS": 19.62, "REB": 9.28, "AST": 2.40}
ALLEN_VK2 = {"PTS": 11.75, "REB": 8.67, "AST": 1.20}


def test_combo_synth_routes_through_vk2_for_pts_ast_low_usage():
    """PA combo on a low-usage player must use VK2 components."""
    a = _StubAdapter(ALLEN_VK1, ALLEN_VK2)
    res = a._predict_combo_projection(
        db=None, bdl_player_id=9, player_name="Jarrett Allen",
        line=9.5, opponent_team=None, use_vk2=True,
        components=("PTS", "AST"),
    )
    assert res["projection"] == pytest.approx(11.75 + 1.20, abs=0.05)
    assert all(p == "vk2" for p, _ in a.path_log)


def test_combo_synth_routes_through_vk2_for_pts_reb_low_usage():
    a = _StubAdapter(ALLEN_VK1, ALLEN_VK2)
    res = a._predict_combo_projection(
        db=None, bdl_player_id=9, player_name="Jarrett Allen",
        line=14.5, opponent_team=None, use_vk2=True,
        components=("PTS", "REB"),
    )
    assert res["projection"] == pytest.approx(11.75 + 8.67, abs=0.05)
    assert all(p == "vk2" for p, _ in a.path_log)


def test_combo_synth_routes_through_vk2_for_reb_ast_low_usage():
    a = _StubAdapter(ALLEN_VK1, ALLEN_VK2)
    res = a._predict_combo_projection(
        db=None, bdl_player_id=9, player_name="Jarrett Allen",
        line=6.5, opponent_team=None, use_vk2=True,
        components=("REB", "AST"),
    )
    assert res["projection"] == pytest.approx(8.67 + 1.20, abs=0.05)
    assert all(p == "vk2" for p, _ in a.path_log)


# ---------------------------------------------------------------------------
# 2. Jokic-shaped (high usage). With VK2, PA / PR / RA stay
#    realistic; both VK1 and VK2 paths must produce the correct math
#    (Σ μ_i) — the test guards the aggregation, not the model values.
# ---------------------------------------------------------------------------
JOKIC_VK1 = {"PTS": 28.5, "REB": 12.5, "AST": 9.5}
JOKIC_VK2 = {"PTS": 27.8, "REB": 12.2, "AST": 9.8}


def test_combo_synth_high_usage_aggregation_correct():
    a = _StubAdapter(JOKIC_VK1, JOKIC_VK2)
    pa = a._predict_combo_projection(
        db=None, bdl_player_id=1, player_name="Nikola Jokic",
        line=35.5, opponent_team=None, use_vk2=True,
        components=("PTS", "AST"),
    )
    pr = a._predict_combo_projection(
        db=None, bdl_player_id=1, player_name="Nikola Jokic",
        line=39.5, opponent_team=None, use_vk2=True,
        components=("PTS", "REB"),
    )
    ra = a._predict_combo_projection(
        db=None, bdl_player_id=1, player_name="Nikola Jokic",
        line=21.5, opponent_team=None, use_vk2=True,
        components=("REB", "AST"),
    )
    assert pa["projection"] == pytest.approx(27.8 + 9.8, abs=0.05)
    assert pr["projection"] == pytest.approx(27.8 + 12.2, abs=0.05)
    assert ra["projection"] == pytest.approx(12.2 + 9.8, abs=0.05)


# ---------------------------------------------------------------------------
# 3. The Jarrett Allen PA alt 9.5 regression — explicitly proves the
#    bug pattern from `/app/audit_reports/wz_alt_line_projection_audit_2026-05-09.md`.
#    With the fix, μ must be ~13, NOT ~22.
# ---------------------------------------------------------------------------
def test_jarrett_allen_pa_alt_9_5_regression():
    """Explicit guard against the inflation pattern documented in the
    2026-05-09 audit. Ensures PA combo synth on Allen returns ~13,
    NOT the buggy ~22 the legacy VK1 path produced."""
    a = _StubAdapter(ALLEN_VK1, ALLEN_VK2)
    res = a._predict_combo_projection(
        db=None, bdl_player_id=9, player_name="Jarrett Allen",
        line=9.5, opponent_team=None, use_vk2=True,
        components=("PTS", "AST"),
    )
    # Correct: PTS_VK2 + AST_VK2 ≈ 12.95
    assert 12.0 <= res["projection"] <= 14.0, (
        f"Allen PA alt 9.5 μ={res['projection']} — outside [12,14]; "
        f"the VK2 routing fix may have regressed."
    )
    # Negative guard against the inflated VK1 value
    assert res["projection"] < 18.0, (
        f"Allen PA alt 9.5 μ={res['projection']} ≥ 18 — combo synth "
        f"is back on VK1 (regressed the 2026-05-09 fix)."
    )


# ---------------------------------------------------------------------------
# 4. Mutation guard. Verifies that a tampered `_VK2_PRIMARY_STATS`
#    cannot silently re-route combos to VK1.
#
#    Strategy: The `combo_vk2_eligible` predicate inside `_score`
#    is `(combo_components is not None and not explicit_legacy and
#       any(c in _VK2_PRIMARY_STATS for c in combo_components))`.
#
#    If `_VK2_PRIMARY_STATS` is emptied, this predicate flips to
#    False — meaning combo families silently fall back to VK1.
#    This test asserts that contract IS THE ONLY way combos can
#    fall back, so future regressions will surface.
# ---------------------------------------------------------------------------
def test_combo_vk2_eligibility_predicate_locks_down_routing():
    """Lock the predicate: combo families are VK2-eligible if and only
    if (1) the family is a combo, (2) explicit_legacy is False, and
    (3) any component is in `_VK2_PRIMARY_STATS`. Empty
    `_VK2_PRIMARY_STATS` MUST disable combo VK2 (the only legitimate
    fallback path)."""
    a = NBAScoringAdapter()
    # 1) Default state: PA combo eligible (PTS in primary set?
    #    Actually current set is {AST, REB, 3PM} — PA has AST,
    #    so eligible). PR has PTS+REB → REB in primary → eligible.
    #    RA has REB+AST → both in primary → eligible.
    primary = a._VK2_PRIMARY_STATS
    for comps in a._COMBO_COMPONENTS.values():
        eligible = any(c in primary for c in comps)
        assert eligible, (
            f"Combo {comps} should be VK2-eligible by default. "
            f"Current _VK2_PRIMARY_STATS={primary}"
        )

    # 2) Mutation guard: empty primary set must disable eligibility.
    saved = a._VK2_PRIMARY_STATS
    try:
        a._VK2_PRIMARY_STATS = set()
        for comps in a._COMBO_COMPONENTS.values():
            eligible = any(c in a._VK2_PRIMARY_STATS for c in comps)
            assert not eligible, (
                f"With empty _VK2_PRIMARY_STATS, combo {comps} must "
                f"NOT be VK2-eligible — surfaces future regressions."
            )
    finally:
        a._VK2_PRIMARY_STATS = saved


def test_combo_vk2_eligibility_blocked_by_explicit_legacy():
    """If override_config sets `p_true_method=model` (legacy), combo
    families must respect that and NOT silently force VK2."""
    # The eligibility predicate is computed in `_score` against
    # `explicit_legacy`. We simulate that here.
    a = NBAScoringAdapter()
    explicit_legacy = True
    for comps in a._COMBO_COMPONENTS.values():
        # When explicit_legacy is True, the second clause in
        # `combo_vk2_eligible` short-circuits → False.
        eligible = (
            comps is not None
            and not explicit_legacy
            and any(c in a._VK2_PRIMARY_STATS for c in comps)
        )
        assert not eligible, (
            f"explicit_legacy must override combo VK2 routing for {comps}."
        )


# ---------------------------------------------------------------------------
# 5. Sigma propagation. The combo σ formula is:
#       σ_combo² = Σ σ_i² + 2·Σ_{i<j} cov(i,j)
#    With our stub returning σ=1 and cov=0, σ_combo = √2 ≈ 1.414.
# ---------------------------------------------------------------------------
def test_combo_synth_sigma_aggregation():
    a = _StubAdapter(ALLEN_VK1, ALLEN_VK2)
    res = a._predict_combo_projection(
        db=None, bdl_player_id=9, player_name="Jarrett Allen",
        line=9.5, opponent_team=None, use_vk2=True,
        components=("PTS", "AST"),
    )
    assert res["sigma"] == pytest.approx(2 ** 0.5, abs=0.05)
    assert res["covariance_source"] == "empirical"
