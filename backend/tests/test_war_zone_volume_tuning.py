"""War Zone volume tuning lockdown (2026-05-01).

Locks in the two changes applied to `_NBA_WAR_ZONE_BASE`:

  1. Direction gate: was `min_projection_to_line_ratio: 1.05` →
     loosened to `1.00`, then fully retired by the strict-only
     direction refactor (2026-05-15) and stripped from config
     (2026-05-17 cleanup). The gate is now sign-only — passes iff
     `projection > line` strict. The two `_wz_direction_*` tests
     below assert the cleaned post-cleanup config state and the
     behavioural pass on `projection > line` slumps.

  2. `hit_rate_gate.enforce_l5_subgate`: False
     (WZ opts out of the universal L5 sub-gate. L5 drawdowns ARE the
      high-variance shots WZ exists to take; enforcing L5 ≥ L20 floor
      killed supply to 1 pick across 920 rejects. SH + FL still
      enforce it.)

Any mutation to either constant breaks exactly one test here.
"""
from services.scoring.gates.engine import UniversalGateEngine
from services.scoring.gates.schema import NormalizedMetrics
from services.scoring.gates.thresholds import resolve_thresholds


def _wz_cfg():
    return resolve_thresholds("nba", "war_zone", "pts", side="OVER")


def test_wz_direction_gate_is_strict_sign_only():
    """Post-2026-05-17 cleanup: WZ direction_gate carries ONLY
    `applies_to_sides=['OVER']`. No legacy cushion keys."""
    cfg = _wz_cfg()
    dg = cfg["direction_gate"]
    assert dg.get("applies_to_sides") == ["OVER"]
    # Legacy keys must be GONE from config (strict engine ignores them
    # at runtime, but we also clean them out of the declarative config
    # so audits don't surface misleading thresholds).
    assert "min_projection_to_line_ratio" not in dg
    assert "min_projection_minus_line" not in dg
    assert "max_projection_minus_line" not in dg
    assert "min_line_minus_projection_ratio" not in dg


def test_wz_direction_gate_keys_count():
    """Negative lockdown — guard against silent re-introduction of a
    cushion key. The clean WZ direction config carries exactly one
    behavioural key: `applies_to_sides`."""
    cfg = _wz_cfg()
    dg = cfg["direction_gate"]
    # Only sign-only direction lives here now.
    assert set(dg.keys()) == {"applies_to_sides"}


def test_wz_disables_l5_subgate():
    cfg = _wz_cfg()
    assert cfg["hit_rate_gate"].get("enforce_l5_subgate") is False


def test_wz_sh_fl_still_enforce_l5_subgate():
    """SH + FL must KEEP the L5 sub-gate ON. Only WZ opts out."""
    sh = resolve_thresholds("nba", "safe_haven", "pts", side="OVER")
    fl = resolve_thresholds("nba", "front_lines", "pts", side="OVER")
    assert sh["hit_rate_gate"].get("enforce_l5_subgate", True) is True
    assert fl["hit_rate_gate"].get("enforce_l5_subgate", True) is True


# ── Behavioural: the slumping-form case MUST now pass WZ ──────────────
def _wz_metrics(hr_l20=60.0, hr_l5=0.0, proj=21.0, line=19.5):
    return NormalizedMetrics(
        sport="nba", tier="war_zone", stat_family="pts", side="OVER",
        reference_book="dk", reference_odds=-300, book_count=4,
        tp=60.0,
        hit_rate=hr_l20, hit_rate_l20=hr_l20,
        hit_rate_l5=hr_l5, hit_rate_l10=hr_l20,
        hit_rate_sample_size=20,
        cv=0.4, edge_pct=10.0, vision_score=80.0,
        line=line, p_model_pct=60.0,
        extras={"projection": proj, "mu_recency_blend_l20": proj,
                "tp_source": "devig"},
    )


def test_wz_slumping_form_still_passes():
    """The Duren case: L20=60, L5=0, proj 22.2 vs line 19.5. With
    L5 sub-gate disabled for WZ, this MUST pass (it's exactly the
    high-variance shot WZ is meant to take)."""
    r = UniversalGateEngine().evaluate(_wz_metrics(hr_l20=60.0, hr_l5=0.0,
                                                    proj=22.2, line=19.5))
    assert r.passed, f"Should pass WZ; failed={r.failed_gates}"


def test_wz_still_rejects_below_l20_floor():
    """L20 must still back the gate — 45% is below the 55% floor and
    must fail even with L5 sub-gate disabled."""
    r = UniversalGateEngine().evaluate(_wz_metrics(hr_l20=45.0, hr_l5=90.0,
                                                    proj=22.0, line=19.5))
    assert not r.passed
    assert "hit_rate_gate" in r.failed_gates


def test_wz_1_00_direction_recovers_near_boundary_pick():
    """proj/line = 1.02 (would have failed old 1.05 floor) now passes."""
    # proj=20.0, line=19.5 → ratio = 1.0256
    r = UniversalGateEngine().evaluate(_wz_metrics(
        hr_l20=60.0, hr_l5=30.0, proj=20.0, line=19.5,
    ))
    assert r.passed, f"Near-boundary WZ should pass; failed={r.failed_gates}"
