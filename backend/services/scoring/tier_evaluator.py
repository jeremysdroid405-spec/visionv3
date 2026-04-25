"""Single tier-evaluation entry point.

Both the first-pass tiering (`scoring_stack.compute_tier`) and the
post-vision re-evaluation (`recompute._reevaluate_tiers_post_vision`)
must apply the SAME post-engine overrides to a given prop.

Today the only override is the MLB Safe Haven goblin-line override
(line < 1.0 → relaxed thresholds). This module owns it so the
override exists in exactly one place. Adding a new override (e.g. an
NBA cv_cap_override exposed via extras) means editing one function.

Sport-agnostic at the call site. No threshold tuning. No new gates.
"""
from __future__ import annotations

from typing import Dict, Any, List

from services.scoring.gates import (
    NormalizedMetrics, ReasonCode, get_engine,
)
from services.scoring.gates.engine import UniversalGateEngine
from services.scoring.gates.schema import GateDetail, GateEvalResult
from services.scoring.gates.thresholds import resolve_thresholds


def _is_mlb_sh_goblin(metrics: NormalizedMetrics) -> bool:
    return bool(
        (metrics.sport or "").lower() == "mlb"
        and metrics.tier == "safe_haven"
        and metrics.extras
        and metrics.extras.get("mlb_goblin_override")
    )


def _apply_mlb_sh_goblin_override(
    metrics: NormalizedMetrics, result: GateEvalResult,
) -> GateEvalResult:
    """Re-run every dispatched gate with the MLB SH goblin thresholds.

    This is the SAME inline patched-thresholds block that previously
    lived in `scoring_stack.compute_tier` (pre-PR-1 lines 391-422).
    Mutates and returns the supplied `result` (caller passes a result
    we own; mirroring the prior in-place behaviour for full parity).
    """
    override = metrics.extras["mlb_goblin_override"]
    thresholds = dict(resolve_thresholds(metrics.sport, metrics.tier, metrics.stat_family))
    if not thresholds:
        return result
    thresholds["cv_gate"]       = {"max":  override["cv_max"]}
    thresholds["hit_rate_gate"] = {"min":  override["hr_min"], "window": "default"}
    thresholds["tp_gate"]       = {"min":  override["tp_min"]}
    thresholds["edge_gate"]     = {"min":  override["edge_min"]}

    details: Dict[str, GateDetail] = {}
    passed_gates: List[str] = []
    failed_gates: List[str] = []
    engine = UniversalGateEngine()
    for gate_type, gate_cfg in thresholds.items():
        fn = engine._GATE_DISPATCH.get(gate_type)
        if fn is None:
            continue
        detail = (
            fn.__func__(gate_cfg, metrics) if hasattr(fn, "__func__")
            else fn(gate_cfg, metrics)
        )
        details[gate_type] = detail
        (passed_gates if detail.passed else failed_gates).append(gate_type)

    overall_passed = len(failed_gates) == 0
    result.gate_details = details
    result.passed_gates = passed_gates
    result.failed_gates = failed_gates
    result.passed = overall_passed
    result.gate_summary = "PASS" if overall_passed else "FAIL"
    result.reason_code = (
        ReasonCode.GATES_PASSED if overall_passed
        else (
            details[failed_gates[0]].reason_code
            or ReasonCode.for_gate(failed_gates[0])
        )
    )
    return result


def evaluate_tier_with_overrides(metrics: NormalizedMetrics) -> GateEvalResult:
    """Single tier-evaluator. Runs the UniversalGateEngine, then
    applies any sport-specific post-engine overrides registered for
    the (sport, tier, extras) combination. Returns a canonical
    `GateEvalResult` that callers can serialize directly.
    """
    result = get_engine().evaluate(metrics)
    if _is_mlb_sh_goblin(metrics) and result.gate_summary == "FAIL":
        result = _apply_mlb_sh_goblin_override(metrics, result)
    return result


__all__ = ["evaluate_tier_with_overrides"]
