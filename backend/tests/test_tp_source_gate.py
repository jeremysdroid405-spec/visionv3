"""Regression — 2026-05-13 `tp_source_gate` for MLB Safe Haven.

User audit (5 HRR 0.5 OVER rejects) revealed every close-edge SH-routed
MLB prop was `tp_source=one_sided`: DK/FD/MGM quoted only the heavy-
chalk OVER side (-300..-500) and the Odds API returned no UNDER
companion, so the de-vig fallback inflated `edge_vs_fair` by 4-8 pp.

`tp_source_gate` rejects these props from SH only — FL/WZ continue
to accept one_sided picks (lower supply, larger edge/HR/CV floors).
"""
from __future__ import annotations

from services.scoring.gates.engine import UniversalGateEngine as GateEngine
from services.scoring.gates.schema import NormalizedMetrics, ReasonCode
from services.scoring.gates.thresholds import THRESHOLDS


def _base_metrics(**overrides):
    base = dict(
        sport="mlb", tier="safe_haven", stat_family="hits_runs_rbis",
        side="OVER",
        line=0.5, hit_rate_l20=85.0, hit_rate_l5=80.0, hit_rate=85.0,
        cv=0.50, edge_pct=8.0, tp=82.0, book_count=3,
        ceiling_rate=70.0, vision_score=85.0,
        avg_hit_margin=2.5, avg_miss_margin=0.5,
        reference_book="consensus", reference_odds=-300,
        is_alt=False,
    )
    base.update(overrides)
    return NormalizedMetrics(**base)


def test_mlb_sh_rejects_one_sided_tp_source():
    """Core requirement: an otherwise-passing SH HRR 0.5 OVER pick
    must be REJECTED when tp_source == 'one_sided'."""
    m = _base_metrics(tp_source="one_sided")
    res = GateEngine().evaluate(m)
    assert res.passed is False
    assert "tp_source_gate" in res.failed_gates
    failed_detail = next(d for d in res.gate_details.values()
                          if d.gate_type == "tp_source_gate")
    assert failed_detail.actual == "one_sided"
    assert failed_detail.threshold == "devig"
    assert failed_detail.reason_code == ReasonCode.TP_SOURCE_FAIL


def test_mlb_sh_accepts_devig_tp_source():
    """tp_source=='devig' must pass tp_source_gate."""
    m = _base_metrics(tp_source="devig")
    res = GateEngine().evaluate(m)
    assert "tp_source_gate" in res.passed_gates
    detail = next(d for d in res.gate_details.values()
                    if d.gate_type == "tp_source_gate")
    assert detail.passed is True


def test_mlb_fl_keeps_one_sided_tp_source():
    """Front Lines must NOT have tp_source_gate — one_sided picks
    must still tier when other gates clear."""
    cfg = THRESHOLDS["mlb"]["front_lines"]
    assert "tp_source_gate" not in cfg["hits_runs_rbis"]
    assert "tp_source_gate" not in cfg["hits"]
    assert "tp_source_gate" not in cfg["_default"]


def test_mlb_wz_keeps_one_sided_tp_source():
    """War Zone must NOT have tp_source_gate — ceiling-chase picks
    are often one-sided by structure."""
    cfg = THRESHOLDS["mlb"]["war_zone"]
    assert "tp_source_gate" not in cfg["hits_runs_rbis"]
    assert "tp_source_gate" not in cfg["_default"]


def test_mlb_wz_has_no_ceiling_gate():
    """2026-05-13 — ceiling_gate explicitly removed from MLB WZ per
    user spec. War Zone reduces to coverage + direction (OVER) + edge."""
    cfg = THRESHOLDS["mlb"]["war_zone"]
    for family, block in cfg.items():
        assert "ceiling_gate" not in block, (
            f"MLB WZ/{family} still has ceiling_gate (must be removed)"
        )
        # And that the remaining structure is intact
        assert "coverage_gate" in block
        assert "direction_gate" in block
        assert "edge_gate" in block


def test_mlb_sh_all_stat_families_have_tp_source_gate():
    """Every stat family in MLB SH must enforce tp_source_gate."""
    cfg = THRESHOLDS["mlb"]["safe_haven"]
    for family, block in cfg.items():
        assert "tp_source_gate" in block, (
            f"MLB SH/{family} missing tp_source_gate"
        )
        assert block["tp_source_gate"]["required_source"] == "devig"


def test_tp_source_gate_null_source_default_rejects():
    """tp_source=None defaults to fail (the safer side — unknown is
    treated as potentially one-sided)."""
    m = _base_metrics(tp_source=None)
    res = GateEngine().evaluate(m)
    assert res.passed is False
    assert "tp_source_gate" in res.failed_gates


def test_tp_source_gate_allow_unknown_opts_in():
    """When `allow_unknown: True` is set on the config, tp_source=None passes."""
    from services.scoring.gates.engine import UniversalGateEngine
    m = _base_metrics(tp_source=None)
    eng = UniversalGateEngine()
    # Manually invoke the gate handler with allow_unknown
    detail = eng._eval_tp_source(
        {"required_source": "devig", "allow_unknown": True}, m
    )
    assert detail.passed is True


# ────────────────────────────────────────────────────────────────────
# 2026-05-13 — One-sided Safe Haven override regressions
# ────────────────────────────────────────────────────────────────────

_OVERRIDE_CFG = {
    "required_source": "devig",
    "one_sided_override": {
        "allowed_stat_families": [
            "hits", "hits_runs_rbis", "runs", "rbis",
            "batter_strikeouts", "stolen_bases", "batter_walks",
        ],
        "hr_l20_min": 90.0,
        "hr_l5_min":  80.0,
        "min_edge_pp": 5.0,
        "cv_max":      0.70,
    },
}


def _eval(metrics):
    from services.scoring.gates.engine import UniversalGateEngine
    return UniversalGateEngine()._eval_tp_source(_OVERRIDE_CFG, metrics)


def test_override_rescues_elite_one_sided_hrr():
    """Josh Jung profile: HR_L20=90, L5=80, edge 15.6pp, CV 0.66,
    stat_family=hits_runs_rbis, tp_source=one_sided → PASSES override."""
    m = _base_metrics(
        tp_source="one_sided", stat_family="hits_runs_rbis",
        hit_rate_l20=90.0, hit_rate_l5=80.0, edge_pct=15.6, cv=0.66,
    )
    d = _eval(m)
    assert d.passed is True
    assert d.note == "one_sided_override:override_pass"
    assert d.reason_code is None


def test_override_blocks_pitcher_strikeouts_family():
    """Pitcher Strikeouts is explicitly NOT in allowed_stat_families."""
    m = _base_metrics(
        tp_source="one_sided", stat_family="pitcher_strikeouts",
        hit_rate_l20=95.0, hit_rate_l5=100.0, edge_pct=20.0, cv=0.40,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_stat_family_fail"


def test_override_blocks_total_bases_family():
    """Total Bases is explicitly NOT in allowed_stat_families."""
    m = _base_metrics(
        tp_source="one_sided", stat_family="total_bases",
        hit_rate_l20=95.0, hit_rate_l5=100.0, edge_pct=20.0, cv=0.40,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_stat_family_fail"


def test_override_blocks_pitching_outs_family():
    m = _base_metrics(
        tp_source="one_sided", stat_family="pitcher_outs",
        hit_rate_l20=95.0, hit_rate_l5=100.0, edge_pct=20.0, cv=0.30,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_stat_family_fail"


def test_override_blocks_earned_runs_family():
    m = _base_metrics(
        tp_source="one_sided", stat_family="earned_runs",
        hit_rate_l20=95.0, hit_rate_l5=100.0, edge_pct=20.0, cv=0.40,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_stat_family_fail"


def test_override_fails_when_hr_l20_below_floor():
    m = _base_metrics(
        tp_source="one_sided", stat_family="hits",
        hit_rate_l20=85.0,   # < 90
        hit_rate_l5=80.0, edge_pct=10.0, cv=0.50,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_hr_fail"


def test_override_fails_when_hr_l5_below_floor():
    m = _base_metrics(
        tp_source="one_sided", stat_family="hits",
        hit_rate_l20=95.0,
        hit_rate_l5=60.0,    # < 80 — cold recent stretch
        edge_pct=10.0, cv=0.50,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_l5_fail"


def test_override_fails_when_edge_below_5pp():
    """fair_prob - book_implied < 0.05 → fake-edge, must die."""
    m = _base_metrics(
        tp_source="one_sided", stat_family="hits",
        hit_rate_l20=95.0, hit_rate_l5=80.0,
        edge_pct=3.5,        # < 5.0
        cv=0.50,
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_edge_fail"


def test_override_fails_when_cv_above_cap():
    """Even elite binary props need stable consistency."""
    m = _base_metrics(
        tp_source="one_sided", stat_family="hits",
        hit_rate_l20=95.0, hit_rate_l5=80.0, edge_pct=10.0,
        cv=0.85,             # > 0.70
    )
    d = _eval(m)
    assert d.passed is False
    assert d.note == "one_sided_override:override_cv_fail"


def test_override_does_not_affect_devig_props():
    """A devig prop must always pass without invoking the override path."""
    m = _base_metrics(
        tp_source="devig", stat_family="hits_runs_rbis",
        hit_rate_l20=70.0, hit_rate_l5=60.0,  # would fail override
        edge_pct=4.0, cv=0.75,
    )
    d = _eval(m)
    assert d.passed is True
    assert d.note is None  # devig path doesn't set override note


def test_override_does_not_affect_front_lines():
    """FL config must NOT carry one_sided_override (FL accepts
    one_sided picks by design — different supply/floor profile)."""
    from services.scoring.gates.thresholds import THRESHOLDS
    fl_cfg = THRESHOLDS["mlb"]["front_lines"]
    # FL doesn't have tp_source_gate at all
    assert "tp_source_gate" not in fl_cfg["hits_runs_rbis"]
    assert "tp_source_gate" not in fl_cfg["_default"]


def test_override_does_not_affect_war_zone():
    wz_cfg = THRESHOLDS["mlb"]["war_zone"]
    assert "tp_source_gate" not in wz_cfg["hits_runs_rbis"]
    assert "tp_source_gate" not in wz_cfg["_default"]


def test_safe_haven_config_includes_override_for_every_family():
    """Every stat family in MLB SH must carry the override block."""
    from services.scoring.gates.thresholds import THRESHOLDS
    cfg = THRESHOLDS["mlb"]["safe_haven"]
    for family, block in cfg.items():
        gate = block.get("tp_source_gate") or {}
        override = gate.get("one_sided_override") or {}
        assert override, f"MLB SH/{family} missing one_sided_override"
        # Verify allowed families are exactly the user-specified ones
        allowed = set(override.get("allowed_stat_families") or [])
        assert allowed == {
            "hits", "hits_runs_rbis", "runs", "rbis",
            "batter_strikeouts", "stolen_bases", "batter_walks",
        }
