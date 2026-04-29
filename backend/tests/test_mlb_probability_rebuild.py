"""MLB Probability Rebuild (2026-04-29).

Spec:
- Disable LOM as a live MLB probability source.
- Disable ECDF as a live MLB override (move to shadow).
- Use the distribution layer output as canonical p_model.
- probability_method = "distribution_mlb_v1" when distribution wins.
- NBA architecture is mirrored: projection → distribution → probability → edge.
- NBA scoring path must be byte-identical (no MLB change leaks into NBA).
"""
from __future__ import annotations

import importlib
from unittest.mock import patch


def test_mlb_lom_module_still_callable():
    """LOM module is kept as legacy reference. Importing it must not
    raise — it's only the live MLB scoring adapter that no longer
    USES its output."""
    mod = importlib.import_module("services.probability.line_outcome")
    assert hasattr(mod, "UniversalLineOutcomeModel")


def test_mlb_scoring_adapter_marks_lom_disabled():
    """Inspect the adapter source to confirm the LOM disablement
    comment block and the `lom_disabled` audit field are present."""
    import services.scoring.adapters.mlb_scoring as mod
    src = mod.__file__
    with open(src, "r") as f:
        text = f.read()
    # Documentation guarantees
    assert "MLB Probability Rebuild" in text
    assert "LOM is fully" in text
    assert 'prop["lom_disabled"] = True' in text
    # Live `probability_method` must be the distribution-based stamp
    assert 'prop["probability_method"] = "distribution_mlb_v1"' in text
    # Live LOM assignment to p_true_model must be ABSENT
    # (the only allowed assignment in this section is the SHADOW one).
    # We assert the live-path assignment is gone.
    assert "p_true_model = round(lom_p_over" not in text
    # Live ECDF assignment to p_true_model must also be ABSENT
    # (ECDF moved to shadow).
    assert "p_true_model = round(ecdf_pred.p_over" not in text
    # Shadow stamps must be present
    assert 'prop["p_lom_shadow"] = round(' in text
    assert 'prop["p_ecdf_shadow"] = round(' in text
    assert 'prop["probability_method_shadow"] = "lom_shadow"' in text
    assert 'prop["probability_method_shadow_ecdf"] = "ecdf_shadow"' in text


def test_mlb_score_doc_whitelist_includes_new_fields():
    from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS
    expected = {"p_distribution", "lom_disabled",
                "p_lom_shadow", "probability_method_shadow",
                "p_ecdf_shadow", "probability_method_shadow_ecdf"}
    for k in expected:
        assert k in _SCORE_OUTPUT_FIELDS, k


def test_mlb_recompute_propagates_new_fields():
    """Confirm the recompute mirror block enumerates the new audit
    fields so they actually persist on the score doc."""
    import services.scoring.recompute as mod
    with open(mod.__file__, "r") as f:
        text = f.read()
    for k in ("p_distribution", "lom_disabled", "p_lom_shadow",
              "probability_method_shadow",
              "p_ecdf_shadow", "probability_method_shadow_ecdf"):
        assert f'"{k}"' in text, k


def test_nba_scoring_adapter_unchanged_no_mlb_distribution_stamp():
    """The MLB-only `distribution_mlb_v1` stamp must NEVER appear in
    the NBA adapter — that would be a leakage of the rebuild into
    NBA. NBA continues to use `ecdf` / `gaussian` / etc."""
    import services.scoring.adapters.nba_scoring as mod
    with open(mod.__file__, "r") as f:
        text = f.read()
    assert "distribution_mlb_v1" not in text
    # NBA has no `lom_disabled` stamp either — flag is MLB-specific.
    assert 'prop["lom_disabled"] = True' not in text


def test_mlb_distribution_returns_high_probability_for_high_projection():
    """Mike Trout-style: μ=2.756, σ=1.5239, line=0.5, OVER. Even with
    cv-derived sigma floors, P(over) should be well above the typical
    ~0.78 market — the original LOM crushed it to 0.7566. The
    distribution layer (normal_cdf) returns ~0.83 here.
    """
    from services.probability.distribution import compute_probability
    res = compute_probability(
        sport="mlb",
        stat_family="Hits+Runs+RBIs",
        mu=2.756,
        line=0.5,
        cv=0.872,
    )
    assert res is not None
    # The exact value depends on the σ-floor in the calibration, but
    # we lock in: distribution P(over) MUST be > market TP (0.778) so
    # edge becomes positive.
    assert res.p_over > 0.80
    assert res.p_over < 1.0


def test_mlb_low_projection_distribution_returns_low_probability():
    """Sanity inverse: μ well below line → low p_over. Confirms the
    distribution function is direction-correct."""
    from services.probability.distribution import compute_probability
    res = compute_probability(
        sport="mlb",
        stat_family="Hits+Runs+RBIs",
        mu=0.2,
        line=0.5,
        cv=0.872,
    )
    assert res is not None
    assert res.p_over < 0.50


def test_lom_predicted_value_does_not_match_distribution_value():
    """Regression: we keep LOM available so it's persisted as shadow
    value, BUT for the canonical Mike Trout setup the LOM output and
    the distribution output diverge. This is the underlying reason
    the rebuild was needed.
    """
    from services.probability.distribution import compute_probability
    from services.probability.line_outcome import get_universal_lom

    dist = compute_probability(
        sport="mlb", stat_family="Hits+Runs+RBIs",
        mu=2.756, line=0.5, cv=0.872,
    )
    assert dist is not None
    lom_p = get_universal_lom().predict_proba_over(
        sport="mlb",
        stat_family="hits_runs_rbis",
        projection=2.756, line=0.5, sigma=1.5239,
        hit_rate_at_line=90.0, hit_rate_sample_size=20,
        cv=0.872, avg_hit_margin=2.611, avg_miss_margin=0.5,
    )
    # Either LOM artifact missing (lom_p is None) OR the two values
    # are clearly not the same — the rebuild prefers the higher
    # distribution-derived value.
    if lom_p is not None:
        assert abs(dist.p_over - lom_p) > 0.05
        assert dist.p_over > lom_p  # distribution rescues high-projection
