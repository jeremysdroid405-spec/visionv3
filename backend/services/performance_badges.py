"""
Universal Performance Badge Generator (SSOT)
============================================

Single source of truth for `scout_badges`. Replaces the three duplicate
generators that previously drifted out of sync:

    1. routes/ferrari_tiers.py (MLB inline block, ≈676–710)
    2. routes/ferrari_tiers.py::_apply_under_badge_rewire (≈813–827)
    3. services/intel_suite_calculator.py::_generate_scout_badges

All three locations now delegate here so a `floor_lock` rendered on the
tier endpoint matches what the player-detail endpoint emits, and so the
`lasso_high_edge` unit-mismatch bug (decimal `edge_vs_fair` compared to
the integer `15`) cannot recur.

Canonical input contract
------------------------
The function consumes a single dict that is either a score document or a
merged prop (post `_merge_score_with_board`). Only **canonical** SSOT
fields are read:

    recommendation / direction      → side (OVER/UNDER)
    hit_rate_l5 / l10 / l20         → side-aware hit-rate windows
    hit_rate_under                  → legacy fallback for UNDER L20
    edge_vs_fair                    → DECIMAL units (0.15 == 15%)
    p_true_active                   → calibrated true probability
    vision_score                    → vision score v1
    cv                              → coefficient of variation
    momentum_data                   → optional defensive momentum bundle
    usage_bump_percent              → numeric usage shift
    has_vacuum_modifier / vacuum_modifier
    dvp_rank or matchup_dvp.rank    → defensive matchup rank
    matchup_analysis.sp_matchup.rank → MLB starting-pitcher buzzsaw guard
    stat_type, line                 → forwarded to volatility_profile

Output shape
------------
List[Dict] with `{"badge_key": <str>, "id": <str>}` (id mirrors badge_key
to match the dict-form already used downstream by `_apply_under_badge_rewire`
and the MLB block).

This module deliberately does NOT mutate `context_badges` or
`active_badges`. Side-stripping of OVER-only badges remains in
`_apply_under_badge_rewire`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ── Threshold constants (canonical) ───────────────────────────────────
# `edge_vs_fair` is stored in DECIMAL units throughout the SSOT score
# document. 15% == 0.15. Comparing the decimal value against the integer
# `15` (the historical bug in routes/ferrari_tiers.py:688) caused
# `lasso_high_edge` to never fire on real picks. Regression test:
# /app/backend/tests/test_performance_badges.py.
EDGE_VS_FAIR_TRIGGER = 0.15            # decimal
FLOOR_LOCK_HIT_RATE_PCT = 90.0         # percent, side-aware L10
HOT_STREAK_HIT_RATE_PCT = 80.0         # percent, side-aware L5
HIGH_FIDELITY_P_TRUE = 0.65            # decimal probability
USAGE_SPIKE_BUMP_PCT = 3.0             # percent
SOFT_MATCHUP_RANK_MIN = 22             # higher == easier matchup
SOFT_MATCHUP_HIT_RATE_PCT = 60.0       # percent, side-aware L10
SP_BUZZSAW_RANK_MAX = 15               # MLB SP top-15 → block soft_matchup


def _badge(key: str) -> Dict[str, str]:
    """Return the canonical badge dict shape used downstream."""
    return {"badge_key": key, "id": key}


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resolve_side(doc: Dict[str, Any]) -> str:
    """Return 'OVER' or 'UNDER'. Defaults to OVER when unset."""
    raw = doc.get("recommendation") or doc.get("direction") or "OVER"
    side = str(raw).strip().upper()
    return "UNDER" if "UNDER" in side else "OVER"


def _side_hit_rate(doc: Dict[str, Any], window: str, side: str) -> Optional[float]:
    """
    Return the hit-rate for the active side over the requested window.

    `hit_rate_l5/l10/l20` on a prop_scores document are side-aware (the
    adapter computes them with the prop's direction), so reading them
    directly is correct for either OVER or UNDER picks. We fall back to
    `hit_rate_under` (L20 UNDER diagnostic) and `hit_rate_over` (legacy
    L20 OVER alias) when the canonical window field is missing on older
    documents.
    """
    canonical = doc.get(f"hit_rate_{window}")
    v = _coerce_float(canonical)
    if v is not None:
        return v
    if window == "l20":
        return _coerce_float(
            doc.get("hit_rate_under") if side == "UNDER" else doc.get("hit_rate_over")
        )
    return None


def _matchup_rank(doc: Dict[str, Any]) -> Optional[int]:
    """Pull defensive matchup rank from the canonical locations."""
    raw = doc.get("dvp_rank")
    if raw is None:
        intel = doc.get("intel_suite") or {}
        mdvp = intel.get("matchup_dvp") if isinstance(intel, dict) else None
        if isinstance(mdvp, dict):
            raw = mdvp.get("rank")
        if raw is None:
            mdvp_top = doc.get("matchup_dvp")
            if isinstance(mdvp_top, dict):
                raw = mdvp_top.get("rank")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sp_buzzsaw_rank(doc: Dict[str, Any]) -> Optional[int]:
    """MLB-only: starting-pitcher rank if present."""
    matchup = doc.get("matchup_analysis") or {}
    sp = matchup.get("sp_matchup") if isinstance(matchup, dict) else None
    if isinstance(sp, dict):
        try:
            return int(sp.get("rank")) if sp.get("rank") is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _is_volatility_extreme(doc: Dict[str, Any]) -> bool:
    """Delegate to `volatility_profile` SSOT (handles family + units)."""
    cv = doc.get("cv")
    if cv is None:
        return False
    try:
        from services.volatility_profile import get_volatility_profile
    except Exception:
        # If the module is unavailable in some isolated test context,
        # fall back to a coarse threshold (≥ 0.35).
        cv_f = _coerce_float(cv)
        return cv_f is not None and cv_f >= 0.35
    profile = get_volatility_profile(cv, doc.get("stat_type") or "", doc.get("line"))
    return bool(profile.is_extreme)


def generate_performance_badges(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Universal scout-badge generator.

    See module docstring for the canonical input contract. Deterministic,
    pure (no I/O), side-aware. Order-stable so downstream snapshot/diff
    tests stay deterministic.
    """
    if not isinstance(doc, dict):
        return []

    badges: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        if key not in seen:
            badges.append(_badge(key))
            seen.add(key)

    side = _resolve_side(doc)

    hit_l5 = _side_hit_rate(doc, "l5", side)
    hit_l10 = _side_hit_rate(doc, "l10", side)
    # Side-aware L10 fallback for UNDER docs that don't yet stamp
    # `hit_rate_l10` but do carry `hit_rate_under` (legacy L20 UNDER).
    if hit_l10 is None and side == "UNDER":
        hit_l10 = _coerce_float(doc.get("hit_rate_under"))

    edge = _coerce_float(doc.get("edge_vs_fair"))
    p_true = _coerce_float(doc.get("p_true_active"))
    vision = _coerce_float(doc.get("vision_score"))

    # 1. hot_streak — recent-form L5 hit rate on active side.
    if hit_l5 is not None and hit_l5 >= HOT_STREAK_HIT_RATE_PCT:
        _add("hot_streak")

    # 2. floor_lock — side-aware L10 hit rate at the 90% public tooltip.
    if hit_l10 is not None and hit_l10 >= FLOOR_LOCK_HIT_RATE_PCT:
        _add("floor_lock")

    # 3. lasso_high_edge — `edge_vs_fair` is DECIMAL. Threshold 0.15.
    if edge is not None and abs(edge) >= EDGE_VS_FAIR_TRIGGER:
        _add("lasso_high_edge")

    # 4. high_fidelity_model — calibrated p_true on the active side
    # combined with a populated vision_score (signal that the v1 stack
    # actually fired and produced a usable score).
    if p_true is not None and vision is not None and p_true >= HIGH_FIDELITY_P_TRUE:
        _add("high_fidelity_model")

    # 5. volatility_extreme — delegate to volatility_profile SSOT.
    if _is_volatility_extreme(doc):
        _add("volatility_extreme")

    # 6. usage_spike — usage shift bump or vacuum modifier flag.
    bump = _coerce_float(doc.get("usage_bump_percent"))
    if bump is not None and bump >= USAGE_SPIKE_BUMP_PCT:
        _add("usage_spike")
    elif doc.get("has_vacuum_modifier") or doc.get("vacuum_modifier"):
        _add("usage_spike")

    # 7. soft_matchup — defensive matchup rank in the easy half AND a
    # supportive recent hit rate on the active side. SP buzzsaw guard
    # (MLB) strips the badge when the opposing starter is top 15.
    rank = _matchup_rank(doc)
    if (
        rank is not None
        and rank >= SOFT_MATCHUP_RANK_MIN
        and hit_l10 is not None
        and hit_l10 >= SOFT_MATCHUP_HIT_RATE_PCT
    ):
        sp_rank = _sp_buzzsaw_rank(doc)
        if sp_rank is None or sp_rank > SP_BUZZSAW_RANK_MAX:
            _add("soft_matchup")

    return badges
