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
