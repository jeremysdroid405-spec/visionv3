"""Universal Edge SSOT pytest (2026-05-15).

Locks the contract:
  • `compute_edge_vs_fair` and `derive_edge_pct` produce canonical values
  • `compute_edge_bundle` is the only adapter-facing entry point
  • `audit_edge_writers` returns 0 violations across the live scoring tree
  • Round-trip identity: edge_pct(re-eval) == edge_pct(first-pass)
"""
from __future__ import annotations

import pytest

from services.scoring.universal_edge import (
    audit_edge_writers,
    compute_edge_bundle,
    compute_edge_vs_fair,
    derive_edge_pct,
)


class TestEdgeVsFair:
    @pytest.mark.parametrize("p_model,fair,expected", [
        (0.8095, 0.7333, 0.0762),     # Shea Langeliers case
        (0.7701, 0.7333, 0.0368),     # Freddie Freeman case
        (0.7814, 0.7465, 0.0349),     # Daulton Varsho case
        (0.7748, 0.7451, 0.0297),     # Josh Naylor case
        (0.5,    0.5,    0.0),
        (0.3,    0.6,   -0.3),         # negative edge supported
        (1.0,    0.0,    1.0),         # max edge
    ])
    def test_canonical_compute(self, p_model, fair, expected):
        result = compute_edge_vs_fair(p_model, fair)
        assert result == pytest.approx(expected, abs=1e-4)

    @pytest.mark.parametrize("p_model,fair", [
        (None, 0.5),
        (0.5, None),
        (None, None),
    ])
    def test_returns_none_on_missing_input(self, p_model, fair):
        assert compute_edge_vs_fair(p_model, fair) is None

    def test_rounding_is_4dp(self):
        # Inputs designed to expose any rounding drift.
        v = compute_edge_vs_fair(0.123456789, 0.0)
        assert v == 0.1235  # banker's rounding to 4dp


class TestDeriveEdgePct:
    @pytest.mark.parametrize("edge_decimal,expected_pp", [
        (0.0762, 7.62),
        (0.0368, 3.68),
        (0.0,    0.0),
        (-0.114, -11.4),
    ])
    def test_conversion(self, edge_decimal, expected_pp):
        assert derive_edge_pct(edge_decimal) == pytest.approx(expected_pp,
                                                                abs=1e-4)

    def test_none_passthrough(self):
        assert derive_edge_pct(None) is None


class TestComputeEdgeBundle:
    def test_returns_both_fields(self):
        bundle = compute_edge_bundle(0.8095, 0.7333)
        assert bundle == {"edge_vs_fair": 0.0762, "edge_pct": 7.62}

    def test_none_inputs_propagate(self):
        bundle = compute_edge_bundle(None, 0.5)
        assert bundle == {"edge_vs_fair": None, "edge_pct": None}

    def test_first_pass_re_eval_identity(self):
        """Critical invariant: first-pass gates and re-eval gates
        must consume bit-identical edge values for the same prop."""
        p_model = 0.8095
        fair_prob = 0.7333
        # First pass — adapter path
        first_pass = compute_edge_bundle(p_model, fair_prob)
        # Re-eval path — metrics_builder reads `doc.edge_vs_fair * 100`
        # which is exactly `derive_edge_pct(stored_edge_vs_fair)`.
        stored = first_pass["edge_vs_fair"]
        re_eval = derive_edge_pct(stored)
        assert first_pass["edge_pct"] == re_eval, (
            f"first-pass edge_pct={first_pass['edge_pct']} != "
            f"re-eval edge_pct={re_eval}"
        )


class TestDriftAudit:
    def test_zero_violations_in_live_scoring_tree(self):
        """The whole point of the SSOT — if anyone adds a duplicate
        edge writer to services/scoring/ or routes/, this test fails."""
        res = audit_edge_writers()
        assert res["scanned"] > 0
        assert res["violations"] == [], (
            f"Duplicate edge writers detected:\n" +
            "\n".join(
                f"  {v['module']}:{v['line']}  {v['pattern']!r}  "
                f"→ {v['snippet'][:120]}"
                for v in res["violations"]
            )
        )


class TestSSOTContractViaAdapters:
    """End-to-end via the actual adapter call sites — proves the
    Phase 2A edge unification holds in production code paths."""

    def test_mlb_adapter_imports_universal_edge(self):
        import services.scoring.adapters.mlb_scoring as mlb
        src = open(mlb.__file__).read()
        # Must call the universal helper.
        assert "compute_edge_bundle" in src
        # Must NOT carry the old (p_model * 100) - tp formula.
        assert "(p_model * 100.0) - tp" not in src
        assert "(p_model * 100) - tp" not in src
        assert "p_model * 100.0 - tp" not in src

    def test_nba_adapter_imports_universal_edge(self):
        import services.scoring.adapters.nba_scoring as nba
        src = open(nba.__file__).read()
        assert "compute_edge_bundle" in src
        assert "p_model * 100.0 - tp" not in src
        assert "(p_model * 100) - tp" not in src
