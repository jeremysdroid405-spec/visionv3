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
        # 2026-04-29 — surgical addition: when cfg["source"] == "model",
        # the OVER tp_gate evaluates the model-derived distribution
        # probability (p_model_pct / p_distribution), bringing it to
        # parity with the UNDER branch. Default behaviour (market
        # devig'd `tp`) is preserved for all callers that don't set
        # `source`. Used by MLB Safe Haven only (per user spec).
        source = (cfg.get("source") or "market").lower()
        if source == "model":
            actual = _py(m.p_model_pct)
            note = "model_confidence_over"
        else:
            actual = _py(m.tp)
            note = "market_implied_over"
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
            reason_code=None if passed else ReasonCode.TP_FAIL,
            note=note,
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

        # Stat-family-keyed caps (2026-04-24). A `caps` dict keyed by
        # stat_family overrides the scalar `max` for this eval.
        # Family lookup is strict — an unknown family AND no default
        # `max` means the CV gate fails fail-closed.
        caps: Optional[Dict[str, float]] = cfg.get("caps")
        note: Optional[str] = None
        if caps:
            family_cap = caps.get((m.stat_family or "").strip().lower())
            if family_cap is None and "default" in caps:
                family_cap = caps["default"]
                note = "cv_gate_caps_default"
            if family_cap is None and "max" not in cfg:
                return GateDetail(
                    gate_type="cv_gate",
                    threshold={"caps": caps, "family": m.stat_family},
                    actual=_py(m.cv), passed=False, comparator="<=",
                    reason_code=ReasonCode.CV_FAIL,
                    note="cv_gate_no_cap_for_stat_family",
                )
            cap = float(family_cap) if family_cap is not None else float(cfg["max"])
        else:
            override = m.extras.get("cv_cap_override") if m.extras else None
            cap = float(override) if override is not None else float(
                cfg.get("max", 9999.0))
            if override is not None:
                note = "cv_cap_override_from_adapter"

        actual = _py(m.cv)
        if actual is None:
            return GateDetail(
                gate_type="cv_gate", threshold=cap, actual=None,
                passed=True, comparator="<=", reason_code=None,
                note="cv_missing_skipped",
            )

        # ── HR-conditional CV relaxation (NBA UNDER) ─────────────────
        # `hr_relax`: list of stepwise rules, evaluated in declared
        # order, that may either ADD slack to the cap or DISABLE the
        # gate entirely when HR is high enough. Side-agnostic in the
        # evaluator — gating to UNDER side is done by living inside
        # the side-specific `_default_under` config block, so OVER
        # picks never see this branch.
        #
        # Schema:
        #   "hr_relax": [
        #       {"min_hr": 75.0, "absolute_add": 0.10},
        #       {"min_hr": 80.0, "disable_gate": True},
        #   ]
        relax_rules = cfg.get("hr_relax")
        relax_note: Optional[str] = None
        if relax_rules:
            hr = _py(m.hit_rate)
            if hr is not None:
                for rule in relax_rules:
                    if hr >= float(rule.get("min_hr", float("inf"))):
                        if rule.get("disable_gate"):
                            return GateDetail(
                                gate_type="cv_gate",
                                threshold={"cap": cap, "hr_relax": rule},
                                actual=actual, passed=True, comparator="<=",
                                reason_code=None,
                                note=f"cv_disabled_hr>={rule['min_hr']}",
                            )
                        add = rule.get("absolute_add")
                        if add is not None:
                            cap = cap + float(add)
                            relax_note = (
                                f"cv_cap_relaxed_hr>={rule['min_hr']}_+{add}"
                            )

        passed = bool(actual <= cap)
        return GateDetail(
            gate_type="cv_gate", threshold=cap, actual=actual,
            passed=passed, comparator="<=",
            reason_code=None if passed else ReasonCode.CV_FAIL,
            note=relax_note or note,
        )

    @staticmethod
    def _eval_margin(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        """Margin-based stability gate (2026-05) — replaces `cv_gate` for
        binary 0.5-line MLB props. CV is meaningless on a binary outcome
        because exceeding the line by a large amount inflates variance
        without making the prop riskier. We score stability by the
        average margin over hit games instead.

        Threshold key:
            ``min`` — minimum required `avg_hit_margin` (default 0.75).
        """
        threshold = float(cfg.get("min", 0.75))
        actual = _py(m.avg_hit_margin)
        if actual is None:
            # No hit games or HR pipeline didn't populate margins.
            # Treat as fail-closed to mirror cv_gate semantics
            # (`cv_gate` fails when cv is required-but-missing).
            return GateDetail(
                gate_type="margin_gate", threshold=threshold, actual=None,
                passed=False, comparator=">=",
                reason_code=ReasonCode.MARGIN_FAIL,
                note="margin_missing",
            )
        passed = bool(actual >= threshold)
        return GateDetail(
            gate_type="margin_gate", threshold=threshold, actual=actual,
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.MARGIN_FAIL,
            note="binary_0.5_line",
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

    @staticmethod
    def _eval_vision_score(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        """Vision-score floor with optional per-tp_source branching.

        Config shape:
          {"min": 85.0}                          # flat floor (v1)
          {"min": 60.0, "use_v2": True}          # flat floor read from v2
          {"by_tp_source": {
              "devig":     {"min_vs": 85.0},
              "one_sided": {"min_vs": 90.0, "or_min_hr": 60.0},
           }}                                    # branch on tp_source (v1 only)

        `or_min_hr` makes the branch pass if EITHER vs >= min_vs OR
        hr >= or_min_hr (single-gate OR semantics per spec).
        Missing tp_source / vision_score fails closed.

        `use_v2`: when True, the gate reads `extras['vision_score_v2']`
        instead of the v1 percentile field. v2 is computed in
        `services/scoring/vision_v2.py` and piped through extras
        by `metrics_builder`. This is opt-in per tier-config block;
        every other caller continues to read the v1 value.
        """
        if cfg.get("use_v2"):
            v2 = None
            if m.extras and isinstance(m.extras.get("vision_score_v2"),
                                       (int, float)):
                v2 = float(m.extras["vision_score_v2"])
            min_vs = float(cfg.get("min", 0.0))
            if v2 is None:
                return GateDetail(
                    gate_type="vision_score_gate",
                    threshold={"min": min_vs, "use_v2": True},
                    actual=None, passed=False, comparator=">=",
                    reason_code=ReasonCode.VISION_SCORE_FAIL,
                    note="vision_v2_unavailable",
                )
            passed = bool(v2 >= min_vs)
            return GateDetail(
                gate_type="vision_score_gate",
                threshold={"min": min_vs, "use_v2": True},
                actual=v2, passed=passed, comparator=">=",
                reason_code=None if passed else ReasonCode.VISION_SCORE_FAIL,
                note="vision_v2_floor",
            )

        vs = _py(m.vision_score)
        tp_source = (m.tp_source or "").lower() or None

        # First-pass deferral: vision_score is a slate-percentile field
        # populated AFTER per-prop scoring. When it's missing we defer
        # to the slate-level re-eval pass in recompute.py. Skipped =
        # passes with a diagnostic note, never fails closed.
        if vs is None:
            return GateDetail(
                gate_type="vision_score_gate",
                threshold=cfg, actual=None,
                passed=True, comparator=">=",
                reason_code=None,
                note="vision_score_deferred_to_slate_pass",
            )

        by_src = cfg.get("by_tp_source")
        if by_src:
            if tp_source is None or tp_source not in by_src:
                return GateDetail(
                    gate_type="vision_score_gate",
                    threshold={"by_tp_source": by_src},
                    actual={"vision_score": vs, "tp_source": tp_source},
                    passed=False, comparator=">=",
                    reason_code=ReasonCode.VISION_SCORE_FAIL,
                    note="no_tp_source_branch",
                )
            branch = by_src[tp_source]
            min_vs = float(branch.get("min_vs", 0.0))
            or_min_hr = branch.get("or_min_hr")
            hr = _py(m.hit_rate if m.hit_rate is not None else m.hit_rate_l20)
            vs_ok = vs is not None and vs >= min_vs
            hr_ok = (
                or_min_hr is not None
                and hr is not None
                and hr >= float(or_min_hr)
            )
            passed = bool(vs_ok or hr_ok)
            return GateDetail(
                gate_type="vision_score_gate",
                threshold={"tp_source": tp_source, **branch},
                actual={"vision_score": vs, "hit_rate": hr},
                passed=passed, comparator=">=",
                reason_code=None if passed else ReasonCode.VISION_SCORE_FAIL,
                note=f"tp_source_branch={tp_source}",
            )

        min_vs = float(cfg.get("min", 0.0))
        passed = bool(vs is not None and vs >= min_vs)
        return GateDetail(
            gate_type="vision_score_gate",
            threshold={"min": min_vs}, actual=vs,
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.VISION_SCORE_FAIL,
        )

    @staticmethod
    def _eval_market_trap(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        """Reject mid-odds weak-signal props (pricing trap).

        FAIL when ALL of:
          reference_odds in [odds_low, odds_high]
          AND hit_rate < hr_max
          AND vision_score < vs_max

        Any missing input → pass (nothing to trap on).
        """
        odds_low = int(cfg.get("odds_low", 0))
        odds_high = int(cfg.get("odds_high", 0))
        hr_max = float(cfg.get("hr_max", 0.0))
        vs_max = float(cfg.get("vs_max", 0.0))

        odds = _py(m.reference_odds)
        hr = _py(m.hit_rate if m.hit_rate is not None else m.hit_rate_l20)
        vs = _py(m.vision_score)

        if odds is None or hr is None or vs is None:
            return GateDetail(
                gate_type="market_trap_gate",
                threshold=cfg,
                actual={"odds": odds, "hit_rate": hr, "vision_score": vs},
                passed=True, comparator="!=",
                reason_code=None, note="market_trap_missing_inputs_skipped",
            )

        in_band = odds_low <= int(odds) <= odds_high
        trap = in_band and hr < hr_max and vs < vs_max
        passed = not trap
        return GateDetail(
            gate_type="market_trap_gate",
            threshold=cfg,
            actual={"odds": int(odds), "hit_rate": hr, "vision_score": vs},
            passed=passed, comparator="!=",
            reason_code=None if passed else ReasonCode.MARKET_TRAP_FAIL,
        )

    @staticmethod
    def _eval_market_structure(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        """Generic structural reject rule. Config shape:

            {"reject_when": {"is_alt": True, "tp_source": "one_sided"}}

        Rejects ONLY when EVERY listed key/value pair matches the
        metrics. Any listed key missing from metrics (None) blocks
        the match — rule fails open rather than rejecting on absent
        data. Supported keys: any scalar field on NormalizedMetrics
        (e.g. `is_alt`, `tp_source`, `side`, `stat_family`, `sport`).
        """
        rules = cfg.get("reject_when") or {}
        if not rules:
            return GateDetail(
                gate_type="market_structure_gate", threshold=cfg,
                actual=None, passed=True, comparator="!=",
                reason_code=None, note="no_reject_rules_configured",
            )
        actuals: Dict[str, Any] = {}
        for key in rules:
            raw = getattr(m, key, None)
            if isinstance(raw, str):
                actuals[key] = raw.lower() or None
            else:
                actuals[key] = _py(raw)
        normalized_rules = {
            k: (v.lower() if isinstance(v, str) else v) for k, v in rules.items()
        }
        match = all(
            actuals.get(k) is not None and actuals.get(k) == v
            for k, v in normalized_rules.items()
        )
        passed = not match
        return GateDetail(
            gate_type="market_structure_gate",
            threshold={"reject_when": normalized_rules},
            actual=actuals,
            passed=passed, comparator="!=",
            reason_code=None if passed else ReasonCode.MARKET_STRUCTURE_FAIL,
        )

    @staticmethod
    def _eval_direction(cfg: Dict[str, Any], m: NormalizedMetrics) -> GateDetail:
        """Direction-consistency gate (configurable per tier and side).

        OVER-side config (existing):
            {"applies_to_sides": ["OVER"],
             "min_projection_minus_line": 0.0,        # FL: proj >= line
             "min_projection_to_line_ratio": 1.0}     # WZ: proj >= line * 1.05

        UNDER-side config (NBA UNDER tuning, 2026-04-29):
            {"applies_to_sides": ["UNDER"],
             "max_projection_minus_line": 0.0,        # proj <= line + X
             "min_line_minus_projection_ratio": 0.15} # (line - proj) / line >= X

        Both directional thresholds may be set; ALL configured thresholds
        must hold. Sides not in `applies_to_sides` auto-pass.
        """
        applies = {s.upper() for s in cfg.get("applies_to_sides", ["OVER"])}
        side_uc = (m.side or "").upper()
        if side_uc not in applies:
            return GateDetail(
                gate_type="direction_gate", threshold=cfg,
                actual={"side": side_uc}, passed=True, comparator="==",
                reason_code=None,
                note="direction_gate_skipped_side_out_of_scope",
            )
        line = _py(m.line)
        proj = None
        if m.extras and isinstance(m.extras.get("projection"), (int, float)):
            proj = float(m.extras["projection"])
        if line is None or proj is None:
            return GateDetail(
                gate_type="direction_gate", threshold=cfg,
                actual={"projection": proj, "line": line},
                passed=False, comparator=">=",
                reason_code=ReasonCode.DIRECTION_FAIL,
                note="direction_gate_missing_inputs",
            )
        diff = proj - line                                  # OVER-flavoured
        line_minus_proj = (line - proj)                     # UNDER-flavoured
        ratio_proj_over_line = (proj / line) if line not in (0, 0.0) else None
        ratio_line_minus_proj = (line_minus_proj / line) if line not in (0, 0.0) else None

        passed = True
        # OVER-side checks (proj >= line + X / proj >= line * X)
        if "min_projection_minus_line" in cfg:
            if diff < float(cfg["min_projection_minus_line"]):
                passed = False
        if "min_projection_to_line_ratio" in cfg:
            if (ratio_proj_over_line is None or
                    ratio_proj_over_line < float(cfg["min_projection_to_line_ratio"])):
                passed = False

        # UNDER-side checks (proj <= line - X / (line-proj)/line >= X)
        if "max_projection_minus_line" in cfg:
            if diff > float(cfg["max_projection_minus_line"]):
                passed = False
        if "min_line_minus_projection_ratio" in cfg:
            if (ratio_line_minus_proj is None or
                    ratio_line_minus_proj < float(cfg["min_line_minus_projection_ratio"])):
                passed = False

        return GateDetail(
            gate_type="direction_gate", threshold=cfg,
            actual={"projection": round(proj, 4), "line": line,
                    "diff": round(diff, 4),
                    "ratio_proj/line": round(ratio_proj_over_line, 4)
                                          if ratio_proj_over_line is not None else None,
                    "ratio_(line-proj)/line": round(ratio_line_minus_proj, 4)
                                          if ratio_line_minus_proj is not None else None},
            passed=passed, comparator=">=",
            reason_code=None if passed else ReasonCode.DIRECTION_FAIL,
            note=f"direction_check_{side_uc}",
        )

    _GATE_DISPATCH = {
        "coverage_gate":         _eval_coverage,
        "hit_rate_gate":         _eval_hit_rate,
        "tp_gate":               _eval_tp,
        "cv_gate":               _eval_cv,
        "margin_gate":           _eval_margin,
        "edge_gate":             _eval_edge,
        "ceiling_gate":          _eval_ceiling,
        "context_gate":          _eval_context,
        "vision_score_gate":     _eval_vision_score,
        "market_trap_gate":      _eval_market_trap,
        "market_structure_gate": _eval_market_structure,
        "direction_gate":        _eval_direction,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(self, metrics: NormalizedMetrics) -> GateEvalResult:
        cfg = resolve_thresholds(
            metrics.sport, metrics.tier, metrics.stat_family,
            side=metrics.side,
        )

        # NOTE: Gates that depend on slate-percentile fields (e.g.
        # `vision_score_gate`, `market_trap_gate`) require a second
        # evaluation pass AFTER `_apply_vision_score_normalization` has
        # populated `NormalizedMetrics.vision_score`. The driver
        # (`recompute.py`) handles this by re-evaluating war_zone docs
        # post-normalization. First-pass callers may see those gates
        # fail on missing vision_score — the re-eval is authoritative.

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

        # 2026-05 — Binary-line stability swap (MLB only).
        # For 0.5-line MLB props the raw-value CV is misleading: a
        # batter who hits the line in 8/10 games and goes 0/2 will
        # have legitimately high CV simply because non-zero values
        # are large compared to the line. Swap `cv_gate` for the
        # margin-based equivalent here so stat-family threshold
        # tables stay declarative (no per-line conditionals in
        # thresholds.py). NBA / NFL untouched.
        if (
            metrics.sport == "mlb"
            and metrics.line is not None
            and float(metrics.line) == 0.5
            and "cv_gate" in cfg
        ):
            cv_cfg = cfg["cv_gate"]
            min_margin = (
                cv_cfg.get("min_margin")
                if isinstance(cv_cfg, dict) else None
            )
            cfg = {k: v for k, v in cfg.items() if k != "cv_gate"}
            cfg["margin_gate"] = {"min": min_margin if min_margin is not None else 0.75}

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
        applied_override: Optional[str] = None

        # ── Safe-Haven Override Pass (universal, opt-in) ─────────────
        # Runs ONLY when the active config includes a
        # `__safe_haven_overrides__` block AND the gate eval failed.
        # The override module may flip `hit_rate_gate` / `cv_gate`
        # results to passed if the configured rescue rules apply. It
        # NEVER touches market_structure_gate / tp_gate / edge_gate.
        # NBA Safe Haven is the only configured caller today; other
        # tiers/sports get zero behaviour change unless they declare
        # their own block.
        if not overall_passed:
            override_cfg = cfg.get("__safe_haven_overrides__")
            if override_cfg:
                from .overrides import apply_safe_haven_overrides
                (details, passed_gates, failed_gates,
                 overall_passed, applied_override) = apply_safe_haven_overrides(
                    metrics, details, passed_gates, failed_gates, override_cfg,
                )

        # ── Front-Lines OVER Override Pass (NBA-only opt-in) ─────────
        # Runs ONLY when the active config includes a
        # `__front_lines_over_overrides__` block, the gate eval
        # failed, and metrics.side == "OVER". Rescues SPECIFIC
        # tp_gate / cv_gate failures per the 2026-04-29 spec.
        # NEVER touches market_structure_gate / direction_gate /
        # hit_rate_gate / vision_score_gate / coverage_gate /
        # edge_gate.
        if not overall_passed and (metrics.side or "").upper() == "OVER":
            fl_over_cfg = cfg.get("__front_lines_over_overrides__")
            if fl_over_cfg:
                from .overrides import apply_front_lines_over_overrides
                (details, passed_gates, failed_gates,
                 overall_passed, fl_applied) = apply_front_lines_over_overrides(
                    metrics, details, passed_gates, failed_gates, fl_over_cfg,
                )
                if fl_applied is not None:
                    applied_override = fl_applied

        # ── War Zone Override Pass (NBA-only opt-in) ─────────────────
        # Runs ONLY when the active config includes a
        # `__war_zone_overrides__` block AND the gate eval failed.
        # Rescues `cv_gate` failures when HR > 70 and CV <= 1.00.
        # NEVER touches any other gate.
        if not overall_passed:
            wz_cfg = cfg.get("__war_zone_overrides__")
            if wz_cfg:
                from .overrides import apply_war_zone_overrides
                (details, passed_gates, failed_gates,
                 overall_passed, wz_applied) = apply_war_zone_overrides(
                    metrics, details, passed_gates, failed_gates, wz_cfg,
                )
                if wz_applied is not None:
                    applied_override = wz_applied

        if overall_passed:
            primary_reason = ReasonCode.GATES_PASSED
        else:
            primary_detail = details[failed_gates[0]]
            primary_reason = primary_detail.reason_code or ReasonCode.for_gate(failed_gates[0])

        result = GateEvalResult(
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
        # Stamp the override audit trail (None if no rule matched).
        if applied_override is not None:
            # `applied_override` is the rule name. Safe-Haven rules
            # are returned bare (e.g. "elite_vision"); FL-OVER rules
            # are namespaced ("fl_over:..."); WZ rules are namespaced
            # ("war_zone:..."). We surface all shapes verbatim so
            # existing safe-haven tests keep their bare-name contract.
            if str(applied_override).startswith("fl_over:"):
                note_prefix = "front_lines_over_override"
            elif str(applied_override).startswith("war_zone:"):
                note_prefix = "war_zone_override"
            else:
                note_prefix = "safe_haven_override"
            result.gate_details["__override_applied__"] = GateDetail(
                gate_type="__override_applied__",
                threshold={"name": applied_override},
                actual=None, passed=True, comparator="==",
                reason_code=None,
                note=f"{note_prefix}:{applied_override}",
            )
        return result


# Module-level singleton.
_ENGINE = UniversalGateEngine()


def get_engine() -> UniversalGateEngine:
    return _ENGINE
