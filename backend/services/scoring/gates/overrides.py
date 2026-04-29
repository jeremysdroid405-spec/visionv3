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
    "apply_front_lines_over_overrides",
]


# ============================================================================
# NBA Front Lines OVER Override Layer
# ============================================================================
# Spec (user, 2026-04-29):
#
#   Apply ONLY to:
#     • sport == "nba"
#     • tier  == "front_lines"
#     • side  == "OVER"
#
#   Rules (each rescues a specific gate failure; nothing else is touched):
#
#     2. 3PM TP override
#        IF stat_family == "threes" AND hit_rate > 75 AND projection >= line
#        → relax tp_gate floor to 45 (rescue if tp >= 45)
#
#     3. AST CV override
#        IF stat_family == "ast" AND hit_rate > 85 AND projection >= line
#        → relax cv_gate cap to 0.95 (rescue if cv <= 0.95)
#
#     4. PTS dominance
#        IF stat_family == "pts" AND hit_rate >= 75 AND
#           L20_avg >= line × 1.5 AND projection >= line
#        → bypass tp_gate / cv_gate failures only — every other gate
#          (market_structure / direction / hit_rate / vision_score /
#          coverage / edge) must still pass.
#
#   Hard rules:
#     • Direction rule (projection >= line) is enforced upstream by the
#       `direction_gate`. The override layer assumes that gate already
#       passed when it ran (since direction_gate failures are NEVER
#       overridable here).
#     • REB / PRA / P+A / STL / BLK / combos and any other family are
#       NOT touched.
#     • UNDER side, Safe Haven tier, War Zone tier are NOT touched.
#     • Market-structure failures still fail. Direction failures still
#       fail. Vision-score failures still fail. Hit-rate failures still
#       fail.
#     • Each rule fires AT MOST ONCE per pick.
# ============================================================================

# Only TP / CV failures are rescuable by the FL-OVER layer.
_FL_OVER_OVERRIDABLE_GATES = frozenset({"tp_gate", "cv_gate"})


def apply_front_lines_over_overrides(
    metrics: NormalizedMetrics,
    details: Dict[str, GateDetail],
    passed: List[str],
    failed: List[str],
    cfg: Dict[str, Any],
) -> Tuple[Dict[str, GateDetail], List[str], List[str], bool, Optional[str]]:
    """NBA Front Lines OVER conditional override pass.

    Returns possibly-rewritten details / passed / failed lists, plus
    the new overall_passed flag and the rule applied (if any).

    Pre-conditions (enforced by the caller in `engine.evaluate`):
      • metrics.side == "OVER"
      • cfg is the `__front_lines_over_overrides__` block
      • At least one gate failed.
    """
    if not failed:
        return details, passed, failed, True, None

    # If anything outside {tp_gate, cv_gate} failed, the pick is a hard
    # reject. The PTS dominance rule explicitly required "all other
    # Front Lines gates pass" — this is the universal predicate.
    if not set(failed).issubset(_FL_OVER_OVERRIDABLE_GATES):
        return details, passed, failed, False, None

    family = (metrics.stat_family or "").strip().lower()
    hr = _hr(metrics)
    cv = metrics.cv
    tp = metrics.tp
    line = metrics.line

    proj = None
    if metrics.extras and isinstance(metrics.extras.get("projection"),
                                     (int, float)):
        proj = float(metrics.extras["projection"])
    l20 = _l20_avg(metrics)

    # Hard direction precondition for ALL FL-OVER rescue rules. (The
    # direction_gate enforces this for the entire FL OVER tier, but we
    # re-check here so the override module is independently sound and
    # fail-closed if direction_gate is ever removed from the config.)
    if proj is None or line is None or proj < line:
        return details, passed, failed, False, None

    # ── Rule 2 — 3PM TP override ─────────────────────────────────────
    threes_cfg = cfg.get("threes_tp") or {}
    if (threes_cfg.get("enabled", True)
            and family == "threes"
            and "tp_gate" in failed
            and "cv_gate" not in failed):
        min_hr = float(threes_cfg.get("min_hit_rate", 75.0))
        relax_tp_to = float(threes_cfg.get("relax_tp_to", 45.0))
        if (hr is not None and hr > min_hr
                and tp is not None and tp >= relax_tp_to):
            _mark_passed(details, passed, failed, "tp_gate",
                         f"fl_over_override:threes_tp_relax "
                         f"(hr>{min_hr},proj>=line,tp>={relax_tp_to})")
            return details, passed, failed, len(failed) == 0, "fl_over:threes_tp_relax"

    # ── Rule 3 — AST CV override ─────────────────────────────────────
    ast_cfg = cfg.get("ast_cv") or {}
    if (ast_cfg.get("enabled", True)
            and family == "ast"
            and "cv_gate" in failed
            and "tp_gate" not in failed):
        min_hr = float(ast_cfg.get("min_hit_rate", 85.0))
        relax_cv_to = float(ast_cfg.get("relax_cv_to", 0.95))
        if (hr is not None and hr > min_hr
                and cv is not None and cv <= relax_cv_to):
            _mark_passed(details, passed, failed, "cv_gate",
                         f"fl_over_override:ast_cv_relax "
                         f"(hr>{min_hr},proj>=line,cv<={relax_cv_to})")
            return details, passed, failed, len(failed) == 0, "fl_over:ast_cv_relax"

    # ── Rule 4 — PTS dominance: bypass TP / CV failures ──────────────
    pts_cfg = cfg.get("pts_dominance") or {}
    if (pts_cfg.get("enabled", True)
            and family == "pts"):
        min_hr = float(pts_cfg.get("min_hit_rate", 75.0))
        ratio = float(pts_cfg.get("min_l20_avg_to_line_ratio", 1.5))
        if (hr is not None and hr >= min_hr
                and line is not None and line > 0
                and l20 is not None and l20 >= line * ratio):
            # Bypass any combination of {tp_gate, cv_gate} that failed.
            if "tp_gate" in failed:
                _mark_passed(details, passed, failed, "tp_gate",
                             f"fl_over_override:pts_dominance_bypass_tp "
                             f"(hr>={min_hr},l20/line>={ratio})")
            if "cv_gate" in failed:
                _mark_passed(details, passed, failed, "cv_gate",
                             f"fl_over_override:pts_dominance_bypass_cv "
                             f"(hr>={min_hr},l20/line>={ratio})")
            return details, passed, failed, len(failed) == 0, "fl_over:pts_dominance"

    return details, passed, failed, len(failed) == 0, None
