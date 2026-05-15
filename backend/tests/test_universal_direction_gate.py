"""Universal Direction Gate — strict side-lean semantics (2026-05-15).

Contract:
    OVER  passes iff  projection >  line   (strict)
    UNDER passes iff  projection <  line   (strict)
    Equality (projection == line) fails — there is no side-lean.

Other quality concerns (margin, CV, edge magnitude, hit-rate) live in
their OWN gates. The direction gate MUST NOT act as a hidden
confidence floor. Historical config keys (`min_projection_minus_line`,
`min_line_minus_projection_ratio`, `min_projection_to_line_ratio`,
`max_projection_minus_line`) are accepted for backwards compatibility
but are NOT honoured as positive cushions.

These tests are deliberately structural — they exercise the engine
itself with a synthetic NormalizedMetrics record so they catch any
future regression that re-introduces a hidden margin floor on the
direction check.
"""
from __future__ import annotations

import pytest

from services.scoring.gates.engine import UniversalGateEngine
from services.scoring.gates.schema import NormalizedMetrics


def _eval_direction(side: str, line: float, projection: float):
    """Invoke the direction-gate evaluator directly with the universal
    OVER+UNDER applies-to scope. Bypasses tier resolution so we test
    pure semantics, not threshold lookup."""
    cfg = {"applies_to_sides": ["OVER", "UNDER"]}
    m = NormalizedMetrics(
        sport="nba", tier="front_lines", stat_family="pts",
        side=side, line=line, extras={"projection": projection},
    )
    return UniversalGateEngine._eval_direction(cfg, m)


# ─── OVER side ────────────────────────────────────────────────
class TestOverDirectionStrict:
    def test_over_passes_when_projection_strictly_above_line(self):
        d = _eval_direction("OVER", line=4.5, projection=5.0)
        assert d.passed
        assert d.comparator == ">"
        assert d.actual == {"projection": 5.0, "line": 4.5, "diff": 0.5}

    def test_over_fails_on_equality(self):
        """Strict semantics — projection == line has no side-lean."""
        d = _eval_direction("OVER", line=4.5, projection=4.5)
        assert not d.passed
        assert d.reason_code == "gate_direction_fail"

    def test_over_fails_when_projection_below_line(self):
        d = _eval_direction("OVER", line=4.5, projection=4.0)
        assert not d.passed

    def test_over_passes_with_tiny_positive_margin(self):
        """No hidden positive cushion: even +0.01 is a valid OVER lean.
        Other gates (margin_gate, CV, edge, HR) enforce confidence —
        not the direction gate.
        """
        d = _eval_direction("OVER", line=0.5, projection=0.51)
        assert d.passed
        assert d.actual["diff"] == pytest.approx(0.01)


# ─── UNDER side ───────────────────────────────────────────────
class TestUnderDirectionStrict:
    def test_under_passes_when_projection_strictly_below_line(self):
        d = _eval_direction("UNDER", line=4.5, projection=4.0)
        assert d.passed
        assert d.comparator == "<"

    def test_under_fails_on_equality(self):
        d = _eval_direction("UNDER", line=4.5, projection=4.5)
        assert not d.passed

    def test_under_fails_when_projection_above_line(self):
        d = _eval_direction("UNDER", line=4.5, projection=5.0)
        assert not d.passed

    def test_under_passes_with_tiny_negative_margin(self):
        """No hidden minimum gap — any proj < line counts as side-lean."""
        d = _eval_direction("UNDER", line=1.5, projection=1.49)
        assert d.passed


# ─── Side scope ───────────────────────────────────────────────
class TestDirectionSideScope:
    def test_skipped_when_side_outside_applies_to(self):
        """Engine should skip the gate entirely when the side is not
        in `applies_to_sides` (returns a pass with diagnostic note)."""
        cfg = {"applies_to_sides": ["OVER"]}
        m = NormalizedMetrics(
            sport="nba", tier="front_lines", stat_family="pts",
            side="UNDER", line=4.5, extras={"projection": 5.0},
        )
        d = UniversalGateEngine._eval_direction(cfg, m)
        assert d.passed
        assert "skipped" in (d.note or "")

    def test_default_applies_to_over(self):
        """When `applies_to_sides` is absent, the engine defaults to
        OVER-only — preserves prior config semantics."""
        m = NormalizedMetrics(
            sport="nba", tier="front_lines", stat_family="pts",
            side="UNDER", line=4.5, extras={"projection": 5.0},
        )
        d = UniversalGateEngine._eval_direction({}, m)
        assert d.passed
        assert "skipped" in (d.note or "")


# ─── Missing inputs ──────────────────────────────────────────
class TestDirectionMissingInputs:
    def test_fails_when_projection_missing(self):
        cfg = {"applies_to_sides": ["OVER"]}
        m = NormalizedMetrics(
            sport="nba", tier="front_lines", stat_family="pts",
            side="OVER", line=4.5, extras={},
        )
        d = UniversalGateEngine._eval_direction(cfg, m)
        assert not d.passed
        assert d.note == "direction_gate_missing_inputs"

    def test_fails_when_line_missing(self):
        cfg = {"applies_to_sides": ["OVER"]}
        m = NormalizedMetrics(
            sport="nba", tier="front_lines", stat_family="pts",
            side="OVER", line=None, extras={"projection": 5.0},
        )
        d = UniversalGateEngine._eval_direction(cfg, m)
        assert not d.passed


# ─── Mutation guard: no hidden margin floor ──────────────────
class TestNoHiddenMarginFloor:
    """Regression — protects against silent re-introduction of a
    positive cushion. If anyone re-adds a `min_projection_minus_line`
    > 0 enforcement, these tests fail immediately.
    """

    @pytest.mark.parametrize("legacy_key,legacy_val", [
        ("min_projection_minus_line", 0.50),   # old MLB FL hits gate
        ("min_projection_to_line_ratio", 1.05),  # old NBA WZ
        ("max_projection_minus_line", -0.10),    # old NBA UNDER
        ("min_line_minus_projection_ratio", 0.15),  # old NBA/MLB UNDER
    ])
    def test_legacy_cushion_keys_are_ignored(self, legacy_key, legacy_val):
        """Any positive cushion config must be ignored — proj=line+0.01
        passes OVER regardless of any legacy `min_projection_minus_line`
        floor that previously demanded e.g. 0.50.
        """
        cfg = {"applies_to_sides": ["OVER"], legacy_key: legacy_val}
        m = NormalizedMetrics(
            sport="mlb", tier="front_lines", stat_family="hits",
            side="OVER", line=0.5, extras={"projection": 0.51},
        )
        d = UniversalGateEngine._eval_direction(cfg, m)
        assert d.passed, (
            f"Legacy cushion key '{legacy_key}' is being honoured — "
            f"the direction gate must remain pure side-lean."
        )


# ─── Jorge Soler — production scenario reproduction ──────────
class TestJorgeSolerHits05Scenario:
    """User-cited audit example (2026-05-15):
        Jorge Soler · Hits · OVER · line=0.5
        Pre-refactor: failed direction at `margin_fail` because the
        binary-line cv→margin swap (in `_eval_margin`) AND a positive
        `min_projection_minus_line` cushion both contributed to a
        hidden confidence floor on the direction check.

    The new strict semantics: any projection > 0.5 passes direction.
    Confidence is gated independently by margin_gate / cv_gate / etc.
    """

    def test_soler_hits_over_passes_direction_with_modest_projection(self):
        """μ≈0.65 (typical Soler L20 hit rate × PA expectation).
        Direction strictly passes — quality concerns handled elsewhere."""
        d = _eval_direction("OVER", line=0.5, projection=0.65)
        assert d.passed
        assert d.actual["diff"] == pytest.approx(0.15)

    def test_soler_hits_over_passes_direction_with_thin_projection(self):
        """Even μ=0.55 — only +0.05 over the line — passes direction.
        Pre-refactor this hit `margin_fail` at min_margin=0.50.
        """
        d = _eval_direction("OVER", line=0.5, projection=0.55)
        assert d.passed

    def test_soler_hits_over_fails_direction_only_below_or_eq_line(self):
        """The only way an OVER Hits 0.5 pick can fail direction
        post-refactor is if the model genuinely projects <= 0.5.
        """
        assert not _eval_direction("OVER", line=0.5, projection=0.50).passed
        assert not _eval_direction("OVER", line=0.5, projection=0.49).passed
