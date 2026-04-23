"""Universal Gate Engine — single evaluator for every sport/tier.

Given a `NormalizedMetrics` record the engine looks up the matching
threshold block in `THRESHOLDS`, runs each gate uniformly, and returns a
`GateEvalResult` describing the outcome in sport-agnostic terms.

The engine does not know about stat-specific CV caps, UNDER-side
floors, etc.; the adapter bakes those into the NormalizedMetrics or
passes the fully-resolved cv-cap via `metrics.extras['cv_cap_override']`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _py(v):
    """Coerce numpy scalars to native Python so MongoDB bson can encode them."""
    if v is None:
        return None
    try:
        import numpy as np  # optional — only if the adapter uses numpy
        if isinstance(v, np.generic):
            return v.item()
    except ImportError:
        pass
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float, str)):
        return v
    return v


from .schema import GateDetail, GateEvalResult, NormalizedMetrics, ReasonCode
from .thresholds import resolve_thresholds


class UniversalGateEngine:
    """Single gate evaluator. Stateless; safe to share as a singleton."""

    # ------------------------------------------------------------------
    # Internal — per-gate evaluators. Each returns a GateDetail.
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_coverage(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        min_books = int(cfg.get("min_books", 1))
        actual = _py(m.book_count)
        passed = bool(actual is not None and actual >= min_books)
        return GateDetail(
            gate_type="coverage_gate",
            threshold=min_books,
            actual=actual,
            passed=passed,
            comparator=">=",
            reason_code=None if passed else ReasonCode.COVERAGE_FAIL,
        )

    @staticmethod
    def _eval_hit_rate(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        min_val = float(cfg.get("min", 0.0))
        window = cfg.get("window", "default")
        if window == "l20":
            actual = _py(m.hit_rate_l20)
        elif window == "l10":
            actual = _py(m.hit_rate_l10)
        elif window == "l5":
            actual = _py(m.hit_rate_l5)
        else:
            actual = _py(m.hit_rate if m.hit_rate is not None else m.hit_rate_l20)
        passed = bool(actual is not None and actual >= min_val)
        return GateDetail(
            gate_type="hit_rate_gate",
            threshold={"min": min_val, "window": window},
            actual=actual,
            passed=passed,
            comparator=">=",
            reason_code=None if passed else ReasonCode.HIT_RATE_FAIL,
        )

    @staticmethod
    def _eval_tp(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        if m.side == "UNDER" and "under_floor" in cfg:
            threshold = float(cfg["under_floor"])
            actual = _py(m.p_model_pct)
            note = "model_confidence_under"
            if actual is None:
                return GateDetail(
                    gate_type="tp_gate", threshold=threshold, actual=None,
                    passed=False, comparator=">=",
                    reason_code=ReasonCode.TP_UNAVAILABLE, note=note,
                )
            passed = bool(actual >= threshold)
            return GateDetail(
                gate_type="tp_gate", threshold=threshold, actual=actual,
                passed=passed, comparator=">=",
                reason_code=None if passed else ReasonCode.TP_FAIL, note=note,
            )

        threshold = float(cfg.get("min", 0.0))
        actual = _py(m.tp)
        if actual is None:
            return GateDetail(
                gate_type="tp_gate", threshold=threshold, actual=None,
                passed=False, comparator=">=",
                reason_code=ReasonCode.TP_UNAVAILABLE, note="market_implied_over",
            )
        passed = bool(actual >= threshold)
        return GateDetail(
            gate_type="tp_gate", threshold=threshold, actual=actual,
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.TP_FAIL,
            note="market_implied_over",
        )

    @staticmethod
    def _eval_cv(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        if "min_cv_floor" in cfg:
            floor = float(cfg["min_cv_floor"])
            actual = _py(m.cv)
            passed = bool(actual is not None and actual >= floor)
            return GateDetail(
                gate_type="cv_gate",
                threshold={"min_cv_floor": floor}, actual=actual,
                passed=passed, comparator=">=",
                reason_code=None if passed else ReasonCode.CV_FAIL,
            )
        override = m.extras.get("cv_cap_override") if m.extras else None
        cap = float(override) if override is not None else float(cfg.get("max", 9999.0))
        note = None if override is None else "cv_cap_override_from_adapter"
        actual = _py(m.cv)
        if actual is None:
            return GateDetail(
                gate_type="cv_gate", threshold=cap, actual=None,
                passed=True, comparator="<=", reason_code=None,
                note="cv_missing_skipped",
            )
        passed = bool(actual <= cap)
        return GateDetail(
            gate_type="cv_gate", threshold=cap, actual=actual,
            passed=passed, comparator="<=",
            reason_code=None if passed else ReasonCode.CV_FAIL, note=note,
        )

    @staticmethod
    def _eval_edge(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        threshold = float(cfg.get("min", 0.0))
        actual = _py(m.edge_pct)
        if actual is None:
            return GateDetail(
                gate_type="edge_gate", threshold=threshold, actual=None,
                passed=True, comparator=">=", reason_code=None,
                note="edge_missing_skipped",
            )
        passed = bool(actual >= threshold)
        return GateDetail(
            gate_type="edge_gate", threshold=threshold, actual=actual,
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.EDGE_FAIL,
        )

    @staticmethod
    def _eval_ceiling(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        threshold = float(cfg.get("min", 0.0))
        actual = _py(m.ceiling_rate)
        if actual is None:
            return GateDetail(
                gate_type="ceiling_gate", threshold=threshold, actual=None,
                passed=True, comparator=">=", reason_code=None,
                note="ceiling_missing_skipped",
            )
        passed = bool(actual >= threshold)
        return GateDetail(
            gate_type="ceiling_gate", threshold=threshold, actual=actual,
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.CEILING_FAIL,
        )

    @staticmethod
    def _eval_context(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        vetoes = set(cfg.get("vetoes", []))
        triggered = [v for v in (m.context_vetoes or []) if v in vetoes]
        passed = bool(len(triggered) == 0)
        return GateDetail(
            gate_type="context_gate",
            threshold={"vetoes": sorted(vetoes)},
            actual={"triggered": triggered},
            passed=passed,
            comparator="!=",
            reason_code=None if passed else ReasonCode.CONTEXT_FAIL,
        )

    _GATE_DISPATCH = {
        "coverage_gate": _eval_coverage,
        "hit_rate_gate": _eval_hit_rate,
        "tp_gate":       _eval_tp,
        "cv_gate":       _eval_cv,
        "edge_gate":     _eval_edge,
        "ceiling_gate":  _eval_ceiling,
        "context_gate":  _eval_context,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(self, metrics: NormalizedMetrics) -> GateEvalResult:
        cfg = resolve_thresholds(metrics.sport, metrics.tier, metrics.stat_family)

        # Explicit "pass-all" opt-out (2026-04-23): a tier config may
        # declare `__pass_all__: True` to signal that the operator has
        # intentionally removed every gate. This bypasses the
        # fail-closed behaviour below and marks every prop eligible.
        # Distinguishable from an accidentally-missing config by the
        # presence of the sentinel key.
        if cfg.get("__pass_all__") is True:
            return GateEvalResult(
                sport=metrics.sport, tier=metrics.tier,
                stat_family=metrics.stat_family,
                gate_summary="PASS",
                passed=True,
                reason_code=ReasonCode.GATES_PASSED,
                reference_book=metrics.reference_book,
                reference_odds=metrics.reference_odds,
            )

        # Empty config ⇒ nothing to evaluate. Treat as "no gate framework
        # configured for this sport yet" — fail closed with a dedicated
        # reason so ops can see the missing config in the score doc.
        if not cfg:
            return GateEvalResult(
                sport=metrics.sport, tier=metrics.tier,
                stat_family=metrics.stat_family,
                gate_summary="FAIL",
                passed=False,
                reason_code="gate_config_missing",
                reference_book=metrics.reference_book,
                reference_odds=metrics.reference_odds,
            )

        details: Dict[str, GateDetail] = {}
        passed_gates: list = []
        failed_gates: list = []

        for gate_type, gate_cfg in cfg.items():
            fn = self._GATE_DISPATCH.get(gate_type)
            if fn is None:
                # Unknown gate type — ignore. Allows adding new gate keys
                # to thresholds without crashing older engine builds.
                continue
            detail = fn.__func__(gate_cfg, metrics) if hasattr(fn, "__func__") else fn(gate_cfg, metrics)
            details[gate_type] = detail
            (passed_gates if detail.passed else failed_gates).append(gate_type)

        overall_passed = len(failed_gates) == 0
        if overall_passed:
            primary_reason = ReasonCode.GATES_PASSED
        else:
            primary_detail = details[failed_gates[0]]
            primary_reason = primary_detail.reason_code or ReasonCode.for_gate(failed_gates[0])

        return GateEvalResult(
            sport=metrics.sport, tier=metrics.tier,
            stat_family=metrics.stat_family,
            gate_summary="PASS" if overall_passed else "FAIL",
            passed=overall_passed,
            reason_code=primary_reason,
            passed_gates=passed_gates,
            failed_gates=failed_gates,
            gate_details=details,
            reference_book=metrics.reference_book,
            reference_odds=metrics.reference_odds,
        )


# Module-level singleton.
_ENGINE = UniversalGateEngine()


def get_engine() -> UniversalGateEngine:
    return _ENGINE
