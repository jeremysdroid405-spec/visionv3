"""Safe-Haven Conditional Override Layer (universal, config-driven).

Runs AFTER the universal gate engine evaluates a pick's gates. If, and
only if, a `__safe_haven_overrides__` block is present in the active
threshold config, the failed-gate list is examined and a single rescue
rule may be applied.

Spec (NBA Safe Haven, 2026-04-29):

    Rule 1 — Elite Vision        : VS >= 90 AND CV <= 0.35
                                   → relax hit_rate_gate to >= 75
    Rule 2 — REB / 3PM CV relax  : stat_family ∈ {reb, threes}
                                   AND HR >= 85
                                   → cv cap raised to 0.60
    Rule 3 — AST CV relax        : stat_family == ast AND HR >= 85
                                   → cv cap raised to 0.50
    Rule 4 — PTS dominance CV    : stat_family == pts AND HR >= 90
        bypass (CV-only)           AND L20_avg >= line × 1.75
                                   → CV failure IGNORED (no new cap)

Hard rules (per spec):

    • NEVER overrides `market_structure_gate`, `tp_gate`, `edge_gate`.
    • Overrides activate ONLY when the corresponding gate has FAILED.
    • At most ONE rule fires per pick (vision path OR stat-structure
      path OR dominance path — never stacked).

The module is sport-agnostic: any tier may opt-in by adding a
`__safe_haven_overrides__` config block with the same rule keys. NBA
Safe Haven is the only configured caller today; MLB / NHL / NFL get
zero behaviour change unless they declare their own block.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .schema import GateDetail, NormalizedMetrics, ReasonCode


# Only HR + CV failures may be rescued. Anything else (TP / edge /
# market_structure / vision_score / coverage / context) is a hard
# fail per spec and survives the override pass untouched.
_OVERRIDABLE_GATES = frozenset({"hit_rate_gate", "cv_gate"})


def _hr(m: NormalizedMetrics) -> Optional[float]:
    return m.hit_rate if m.hit_rate is not None else m.hit_rate_l20


def _l20_avg(m: NormalizedMetrics) -> Optional[float]:
    """Resolve the L20 average from `extras`. Adapters pipe
    `mu_recency_blend_l20` (or any equivalent L20 mean) through here.
    Falling back to `mu_recency_blend_l20` directly if present. None
    if neither exists — Rule 4 fails closed in that case.
    """
    extras = m.extras or {}
    for k in ("l20_avg", "mu_recency_blend_l20"):
        v = extras.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _mark_passed(details: Dict[str, GateDetail],
                 passed: List[str], failed: List[str],
                 gate_type: str, note: str) -> None:
    """Flip a failed gate into a passed gate WITHOUT mutating its
    threshold/actual values — the audit trail keeps the original
    threshold so we can see "this would have failed at the base rule
    but rule X rescued it."""
    detail = details.get(gate_type)
    if detail is None:
        return
    detail.passed = True
    detail.reason_code = None
    detail.note = note
    if gate_type in failed:
        failed.remove(gate_type)
    if gate_type not in passed:
        passed.append(gate_type)


def apply_safe_haven_overrides(
    metrics: NormalizedMetrics,
    details: Dict[str, GateDetail],
    passed: List[str],
    failed: List[str],
    cfg: Dict[str, Any],
) -> Tuple[Dict[str, GateDetail], List[str], List[str], bool, Optional[str]]:
    """Apply the Safe-Haven override pass. Returns possibly-rewritten
    details / passed / failed lists, plus the new overall_passed flag
    and the rule applied (if any)."""
    if not failed:
        return details, passed, failed, True, None

    # If anything outside the overridable set failed, the pick is a
    # hard reject — bail without touching the result.
    if not set(failed).issubset(_OVERRIDABLE_GATES):
        return details, passed, failed, False, None

    family = (metrics.stat_family or "").strip().lower()
    hr   = _hr(metrics)
    cv   = metrics.cv
    vs   = metrics.vision_score
    line = metrics.line
    l20  = _l20_avg(metrics)

    rule_cfg = cfg or {}

    # ── Rule 1 — Elite Vision ────────────────────────────────────────
    elite = rule_cfg.get("elite_vision") or {}
    if "hit_rate_gate" in failed and elite.get("enabled", True):
        min_vs   = float(elite.get("min_vision_score", 90.0))
        max_cv   = float(elite.get("max_cv", 0.35))
        relax_hr = float(elite.get("relax_hit_rate_to", 75.0))
        if (vs is not None and vs >= min_vs and
                cv is not None and cv <= max_cv and
                hr is not None and hr >= relax_hr):
            _mark_passed(details, passed, failed,
                         "hit_rate_gate",
                         f"safe_haven_override:elite_vision "
                         f"(vs>={min_vs},cv<={max_cv},hr>={relax_hr})")
            return details, passed, failed, len(failed) == 0, "elite_vision"

    # ── Rule 2 — REB / 3PM CV relax ──────────────────────────────────
    stat_relax = (rule_cfg.get("stat_family_cv_relax") or {})
    if "cv_gate" in failed and (hr is not None and hr >= 85.0):
        family_cap = stat_relax.get(family)
        if isinstance(family_cap, (int, float)) and cv is not None and cv <= family_cap:
            _mark_passed(details, passed, failed,
                         "cv_gate",
                         f"safe_haven_override:stat_structure "
                         f"(family={family},cap={family_cap})")
            return details, passed, failed, len(failed) == 0, f"stat_structure:{family}"

    # ── Rule 3 (PTS dominance) — CV bypass ───────────────────────────
    pts_dom = rule_cfg.get("pts_dominance") or {}
    if "cv_gate" in failed and pts_dom.get("enabled", True):
        target_family = (pts_dom.get("stat_family") or "pts").lower()
        min_hr        = float(pts_dom.get("min_hit_rate", 90.0))
        ratio         = float(pts_dom.get("min_l20_avg_to_line_ratio", 1.75))
        if (family == target_family and
                hr is not None and hr >= min_hr and
                line is not None and line > 0 and
                l20 is not None and l20 >= line * ratio):
            _mark_passed(details, passed, failed,
                         "cv_gate",
                         f"safe_haven_override:pts_dominance "
                         f"(hr>={min_hr},l20_avg/line>={ratio})")
            return details, passed, failed, len(failed) == 0, "pts_dominance"

    # No rescue rule matched.
    return details, passed, failed, len(failed) == 0, None


__all__ = [
    "apply_safe_haven_overrides",
]
