"""
Scoring Stack — Three Independent Scoring Dimensions
=====================================================

Each canonical prop emits three DECOUPLED scores:

  1. vision_score  — Platform-agnostic pick quality (0–100 or null)
  2. tier          — Risk bucket (safe_haven | front_lines | war_zone | unqualified)
  3. pp_utility    — PP-specific usefulness (0–100 + category)

Contract: no function here reads or writes fields belonging to another
scoring dimension. They are composed by `compute_scoring_stack()`.

LOCKED SPEC (user 2026-04-17):

  vision_score:
    - Sharp-first fair probability: pinnacle > consensus(dk,mgm) > dk > mgm
    - Never reads PP data or multipliers
    - null if no valid reference market (quality_source="insufficient_market")

  tier:
    - Driven by reference odds (dk → mgm fallback) + Universal Gate Engine
      (`services.scoring.gates.UniversalGateEngine`)
    - "unqualified" if no reference market (tier_reason="no_reference_market")

  pp_utility:
    - Measures PP-leg usefulness for parlay construction
    - MUST NOT treat PP American odds (-137, +100, etc.) as a payout multiplier
    - Real multiplier source: PP combo multiplier if available, else a learned
      multiplier model (future). Until provided, `pp_multiplier` is null and
      the multiplier component is weighted 0.
    - Categories: pp_fair | pp_exclusive | pp_scam
      (pp_premium and pp_discount are RESERVED until a real multiplier source
       is wired in; do not emit them from odds heuristics.)
"""
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Shared p_true ladder (canonical scoring-framework primitive)
# -----------------------------------------------------------------------------
# Stage 2 (2026-04-20, MLB↔NBA carbon-copy enforcement):
# Both sport scoring adapters delegate p_true selection to this helper.
# Canonical ladder order (applies to every sport):
#
#     model → hit_rate → vk2 → fair
#
# Each rung is evaluated in order; the first non-None candidate becomes
# `p_true_active` and its name becomes `p_true_method`. If a preferred
# method is supplied via override_config (NBA keeps the legacy "vk2"
# opt-in path) and its candidate is available, that rung jumps to the
# front without changing the rest of the ladder order.
#
# Invariant: `p_true_method` is non-None for every scored row as long as
# any rung has data. The "fair" rung (market-implied `tp`) guarantees
# coverage whenever reference odds exist.
# -----------------------------------------------------------------------------

_LADDER_ORDER: Tuple[str, ...] = ("model", "hit_rate", "vk2", "fair")


def resolve_p_true_ladder(
    *,
    p_true_model: Optional[float] = None,
    p_true_hit_rate: Optional[float] = None,
    p_true_vk2: Optional[float] = None,
    tp: Optional[float] = None,
    preferred_method: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """Canonical ladder resolver shared by every sport scoring adapter.

    Args:
        p_true_model:   Primary model prob_over for the picked side, 0-1.
        p_true_hit_rate: L20 rolling hit-rate prob, 0-1.
        p_true_vk2:     Alternate 5-year adv-stat VK2 prob_over, 0-1.
        tp:             Market-implied reference probability in 0-100 pp.
                        (Converted to 0-1 internally for the "fair" rung.)
        preferred_method: If supplied (e.g. "vk2") and that rung has a
                        value, it is used regardless of canonical order.

    Returns:
        (p_active, method) where method ∈ {"model","hit_rate","vk2",
        "fair","none"}. method == "none" ONLY if every rung is None.
    """
    fair_p = None
    if tp is not None:
        try:
            fair_p = float(tp) / 100.0
        except (TypeError, ValueError):
            fair_p = None

    candidates: Dict[str, Optional[float]] = {
        "model":    p_true_model,
        "hit_rate": p_true_hit_rate,
        "vk2":      p_true_vk2,
        "fair":     fair_p,
    }

    # Preferred rung takes priority when it has a value.
    if preferred_method and preferred_method in candidates:
        v = candidates[preferred_method]
        if v is not None:
            return v, preferred_method

    # Canonical ladder walk.
    for method in _LADDER_ORDER:
        v = candidates.get(method)
        if v is not None:
            return v, method

    return None, "none"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _american_to_prob(odds: Optional[float]) -> Optional[float]:
    """Convert American odds to implied probability [0,1]."""
    if odds is None:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o < 0:
        return round(abs(o) / (abs(o) + 100.0), 4)
    return round(100.0 / (o + 100.0), 4)


def _safe_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# =============================================================================
# 1. VISION SCORE — Platform-agnostic quality
# =============================================================================

def _pick_fair_probability(
    pp_layer: Optional[Dict],  # accepted but IGNORED per spec
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
    sharp_layer: Optional[Dict],
    fd_layer: Optional[Dict] = None,
    sport: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """
    Sharp-first fair probability source selection.
    Returns (fair_prob [0,1] or None, source_label).
    PP data is NEVER considered as a fair-price source (placeholder odds).

    Source-chain order (per sport):

      NBA chain (unchanged historical behaviour):
        sharp → DK+MGM consensus → DK → MGM → insufficient_market

      MLB chain (2026-04-27 expansion — FD has dominant MLB
      player-prop coverage; previously triggered `insufficient_market`
      on Runs / Stolen Bases / SP K alts where FD is the only quote):
        sharp → DK+FD consensus → DK → FD → MGM → insufficient_market

    Default chain (no sport hint) keeps the legacy NBA order to avoid
    silent behaviour changes for any caller that omits `sport`.

    The source_label reflects the actual book used:
      sharp_layer.book / consensus / dk / fd / mgm
    """
    sharp_odds = sharp_layer.get("odds") if sharp_layer else None
    dk_odds = dk_layer.get("odds") if dk_layer else None
    mgm_odds = mgm_layer.get("odds") if mgm_layer else None
    fd_odds = fd_layer.get("odds") if fd_layer else None

    sharp_p = _american_to_prob(sharp_odds)
    dk_p = _american_to_prob(dk_odds)
    mgm_p = _american_to_prob(mgm_odds)
    fd_p = _american_to_prob(fd_odds)

    if sharp_p is not None:
        book_name = (sharp_layer or {}).get("book") or "sharp"
        return sharp_p, str(book_name).lower()

    sport_lc = (sport or "").lower()
    if sport_lc == "mlb":
        # MLB: FD-aware chain.
        if dk_p is not None and fd_p is not None:
            return round((dk_p + fd_p) / 2.0, 4), "consensus"
        if dk_p is not None:
            return dk_p, "dk"
        if fd_p is not None:
            return fd_p, "fd"
        if mgm_p is not None:
            return mgm_p, "mgm"
        return None, "insufficient_market"

    # NBA / default: unchanged.
    if dk_p is not None and mgm_p is not None:
        return round((dk_p + mgm_p) / 2.0, 4), "consensus"
    if dk_p is not None:
        return dk_p, "dk"
    if mgm_p is not None:
        return mgm_p, "mgm"
    return None, "insufficient_market"


def compute_vision_score(
    p_model: Optional[float],
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
    sharp_layer: Optional[Dict],
    cv: Optional[float],
    hit_rate: Optional[float],
    books_available_count: int = 0,
    fd_layer: Optional[Dict] = None,
    sport: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Platform-agnostic pick quality.

    Returns:
      {
        "vision_score_raw": float or None,   # unnormalized; used for percentile pass
        "vision_score": float or None,       # populated later by percentile pass
        "quality_source": str,               # pinnacle | consensus | dk | fd | mgm | insufficient_market
        "fair_prob": float or None,
        "stability": float or None,
        "confidence": float or None,
        "edge_vs_fair": float or None,       # p_model - fair_prob  (can be negative)
      }

    `fd_layer` and `sport` are 2026-04-27 additions: when sport='mlb',
    FanDuel is consulted as a market source (`_pick_fair_probability`'s
    MLB chain). NBA behaviour is unchanged.
    """
    fair_prob, quality_source = _pick_fair_probability(
        None, dk_layer, mgm_layer, sharp_layer, fd_layer=fd_layer, sport=sport,
    )

    # No market → vision score is undefined (per spec, null).
    if fair_prob is None or p_model is None:
        return {
            "vision_score_raw": None,
            "vision_score": None,
            "quality_source": quality_source,
            "fair_prob": fair_prob,
            "stability": None,
            "confidence": None,
            "edge_vs_fair": None,
        }

    # Stability from CV: higher CV → less stable → lower score.
    if cv is not None and cv > 0:
        stability = max(0.3, min(1.0, 1.0 - (cv / 3.0)))
    else:
        stability = 0.5

    # Confidence: data-quality signals only (no PP data, no multipliers).
    conf_signals = [
        1.0 if p_model is not None else 0.0,
        1.0 if (hit_rate or 0) > 0 else 0.0,
        1.0 if books_available_count >= 2 else 0.5,
    ]
    confidence = round(sum(conf_signals) / len(conf_signals), 4)

    edge = p_model - fair_prob  # signed; positive = model sees more value than market

    # Raw vision score: magnitude of POSITIVE edge, scaled by p_model, stability, confidence.
    pos_edge = max(0.0, edge)
    vision_raw = round(pos_edge * p_model * stability * confidence, 6)

    return {
        "vision_score_raw": vision_raw,
        "vision_score": None,  # filled by percentile normalization downstream
        "quality_source": quality_source,
        "fair_prob": round(fair_prob, 4),
        "stability": round(stability, 4),
        "confidence": confidence,
        "edge_vs_fair": round(edge, 4),
    }


# =============================================================================
# 2. TIER — Risk bucket via the Universal Gate Engine
# =============================================================================
# Reference-odds buckets + per-sport/tier/stat thresholds live entirely in
# `services/scoring/gates/thresholds.py`. Hard-coded `_REF_SAFE_HAVEN_MAX`
# / `_REF_WAR_ZONE_MIN` constants were deleted 2026-04-22 (Universal Gate
# Engine cleanup pass).


def _prob_to_american(p: Optional[float]) -> Optional[float]:
    """Inverse of `_american_to_prob`. Returns American odds for a [0,1] prob.

    Used by `_pick_reference_odds` when forming a DK+FD MLB consensus —
    routing is bucketed on American-odds thresholds, so we must convert
    the averaged implied-prob back to American to keep the bucket math
    intact. Bounded clamp (0.001..0.999) prevents division blow-ups on
    edge-case prices.
    """
    if p is None:
        return None
    p = max(0.001, min(0.999, float(p)))
    if p >= 0.5:
        # Favorites
        return -round(100 * p / (1 - p), 1)
    # Underdogs
    return round(100 * (1 - p) / p, 1)


def _pick_reference_odds(
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
    fd_layer: Optional[Dict] = None,
    bol_layer: Optional[Dict] = None,
    sport: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """Reference odds used by the universal odds-bucket router
    (`route_by_reference_odds` in gates/thresholds.py).

    Source-chain order (per sport):

      NBA / default (unchanged historical behaviour):
        DK → MGM → none

      MLB (2026-04-27 expansion — DK is missing on FD-only families
      such as Runs / Stolen Bases / a long tail of pitcher props.
      Without this fallback, those props always resolve to
      routed_tier=None and never reach the gate stage.):
        DK + FD consensus (mean of implied probs, re-converted) →
        DK → FD → MGM → BOL → none

    PrizePicks is intentionally NEVER consulted as a reference book —
    PP odds are placeholder fixed-payout structure prices, not a real
    two-sided market. Sharp/Pinnacle is also excluded to preserve
    pre-existing behaviour where the sorter never read sharp for tier.

    Returns
    -------
    (reference_odds_in_american_format or None, book_label)

    book_label values: "dk" | "fd" | "mgm" | "bol" | "consensus" | "none"
    """
    dk_odds = dk_layer.get("odds") if dk_layer else None
    fd_odds = fd_layer.get("odds") if fd_layer else None
    mgm_odds = mgm_layer.get("odds") if mgm_layer else None
    bol_odds = bol_layer.get("odds") if bol_layer else None

    sport_lc = (sport or "").lower()
    if sport_lc == "mlb":
        if dk_odds is not None and fd_odds is not None:
            dk_p = _american_to_prob(dk_odds)
            fd_p = _american_to_prob(fd_odds)
            if dk_p is not None and fd_p is not None:
                consensus_prob = (dk_p + fd_p) / 2.0
                consensus_amer = _prob_to_american(consensus_prob)
                if consensus_amer is not None:
                    return consensus_amer, "consensus"
            # If for any reason consensus math fails, fall through to
            # the canonical DK price.
        if dk_odds is not None:
            return dk_odds, "dk"
        if fd_odds is not None:
            return fd_odds, "fd"
        if mgm_odds is not None:
            return mgm_odds, "mgm"
        if bol_odds is not None:
            return bol_odds, "bol"
        return None, "none"

    # NBA / default — unchanged.
    if dk_odds is not None:
        return dk_odds, "dk"
    if mgm_odds is not None:
        return mgm_odds, "mgm"
    return None, "none"


def compute_tier(
    prop: Dict,
    cv: Optional[float],
    hit_rate: Optional[float],
    edge_pct: Optional[float],
    tp: Optional[float],
    ceiling_rate: Optional[float],
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
    sport: str,
    p_model: Optional[float] = None,
    avg_hit_margin: Optional[float] = None,
    avg_miss_margin: Optional[float] = None,
    fd_layer: Optional[Dict] = None,
    bol_layer: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Assign risk bucket via the UNIVERSAL GATE ENGINE.

    Post 2026-04-22 HARD GATE CLEANUP:
      • the `sorter` parameter is gone — the engine is sport-agnostic
        and gates, thresholds, and reason codes all live in
        `services.scoring.gates`;
      • `sport` is now passed explicitly by the adapter driver
        (`recompute.compute_scoring_stack`);
      • the returned contract (`tier`, `tier_reason`,
        `tier_reference_book`, `tier_reference_odds`,
        `tier_gate_results`, `gate_eval`, optional
        `war_zone_cv_modifier`) is preserved.
    """
    from services.scoring.gates import ReasonCode
    from services.scoring.gates.thresholds import resolve_target_tier
    from services.scoring.metrics_builder import build_metrics_from_context
    from services.scoring.tier_evaluator import evaluate_tier_with_overrides

    ref_odds, ref_book = _pick_reference_odds(
        dk_layer, mgm_layer,
        fd_layer=fd_layer, bol_layer=bol_layer, sport=sport,
    )

    side = (prop.get("recommendation") or "OVER").upper()
    p_model_pct = round((p_model or 0.0) * 100.0, 1) if p_model is not None else None

    if ref_odds is None:
        return {
            "tier": "unqualified",
            "tier_reason": ReasonCode.NO_REFERENCE_MARKET,
            "tier_reference_book": "none",
            "tier_reference_odds": None,
            "routed_tier": None,
            "tier_gate_results": {},
        }

    # Pre-gate filters (direction veto, p_model<0.55 floor) were
    # removed 2026-04-23 per operator request: tier eligibility is now
    # determined strictly by the Universal Gate Engine configured in
    # `services.scoring.gates.thresholds`. `__pass_all__` tiers are now
    # genuinely filter-free end-to-end.

    # ----- FIRST-CLASS TIER ROUTING (2026-04-25) ------------------
    # Route by reference odds BEFORE any gate evaluation. The routed
    # tier defines which gate block this prop is allowed to evaluate
    # against — there is no cross-tier evaluation, no promotion path.
    # `tier_evaluator` only ever sees a prop tagged with one routed
    # bucket. Final assignment is constrained to {routed_tier,
    # "unqualified"} by the hard guard further down.
    sport = (sport or "nba").lower()
    routed_tier = resolve_target_tier(sport, ref_odds) or "front_lines"
    target_tier = routed_tier
    stat_raw = prop.get("stat_type")

    # Book count — the universal 0-Book Exclusion filter has already
    # trimmed pp_only rows before scoring; for any survivor this is
    # always >= 1. We still surface the real count so the coverage_gate
    # is informative and future per-tier min_books bumps become config
    # changes rather than code changes.
    book_count = prop.get("book_count")
    if book_count is None:
        books = [b for b in (prop.get("books_anchored") or []) if b and b != "prizepicks"]
        book_count = len(books) if books else None

    # Override resolution — delegated to the metrics_builder helpers
    # so the cv-cap predicate lives in exactly one place
    # (PR-2, 2026-04-25). Both paths (first pass + post-vision re-eval)
    # call the same private resolvers. The MLB goblin-line override
    # was removed 2026-05; MLB gates now use the visible threshold
    # table only with no hidden line-based patching.
    from services.scoring.metrics_builder import _resolve_cv_cap_override
    cv_cap_override = _resolve_cv_cap_override(sport, target_tier, stat_raw)

    # PR-1 (2026-04-25): NormalizedMetrics builder + tier evaluator
    # extracted to dedicated modules so the post-vision re-eval can
    # call the same code paths in PR-2. Behaviour-preserving.
    metrics = build_metrics_from_context(
        prop=prop,
        sport=sport,
        target_tier=target_tier,
        stat_raw=stat_raw,
        side=side,
        ref_book=ref_book,
        ref_odds=ref_odds,
        book_count=book_count,
        cv=cv,
        hit_rate=hit_rate,
        edge_pct=edge_pct,
        tp=tp,
        ceiling_rate=ceiling_rate,
        p_model_pct=p_model_pct,
        cv_cap_override=cv_cap_override,
        avg_hit_margin=avg_hit_margin,
        avg_miss_margin=avg_miss_margin,
    )

    eval_result = evaluate_tier_with_overrides(metrics)

    # War-Zone CV ranking modifier — INFORMATIONAL ONLY.
    # The CV floor on War Zone eligibility was removed 2026-04-23 (design
    # decision: War Zone must not penalize consistency). This modifier
    # still adjusts the RANKING score so higher-volatility props sort
    # above flatter ones within the tier, but it never affects pass/fail.
    war_zone_cv_mod = None
    if target_tier == "war_zone":
        try:
            from services.mlb_tier_sorter import war_zone_cv_modifier
            war_zone_cv_mod = war_zone_cv_modifier(cv)
        except Exception:
            war_zone_cv_mod = None

    # Build legacy-shaped return for recompute.py / UI.
    legacy_gate_results = {
        name: {
            "threshold": d.threshold,
            "value": d.actual,
            "passed": bool(d.passed),
            **({"tp_unavailable": True} if d.reason_code == ReasonCode.TP_UNAVAILABLE else {}),
            **({"note": d.note} if d.note else {}),
            "reason_code": d.reason_code,
        }
        for name, d in eval_result.gate_details.items()
    }

    if eval_result.passed:
        # Hard-guard (2026-04-25): final tier MUST equal routed_tier
        # for a passing prop. The current pipeline can't violate this
        # because gate thresholds are resolved per-target_tier — but
        # the assertion exists to catch any future regression
        # (e.g. someone wiring cross-tier gate evaluation) at the
        # exact moment the constraint would be broken.
        if target_tier != routed_tier:
            logger.error(
                "[ROUTING_GUARD] passed-prop tier mismatch: "
                f"target={target_tier} != routed={routed_tier} "
                f"sport={sport} ref_odds={ref_odds} — forcing unqualified"
            )
            out = {
                "tier": "unqualified",
                "tier_reason": "routing_guard_violation",
                "tier_reference_book": ref_book,
                "tier_reference_odds": ref_odds,
                "routed_tier": routed_tier,
                "tier_gate_results": legacy_gate_results,
                "gate_eval": eval_result.to_dict(),
            }
            if war_zone_cv_mod is not None:
                out["war_zone_cv_modifier"] = war_zone_cv_mod
            return out

        out = {
            "tier": routed_tier,
            "tier_reason": ReasonCode.GATES_PASSED,
            "tier_reference_book": ref_book,
            "tier_reference_odds": ref_odds,
            "routed_tier": routed_tier,
            "tier_gate_results": legacy_gate_results,
            "gate_eval": eval_result.to_dict(),
        }
        if war_zone_cv_mod is not None:
            out["war_zone_cv_modifier"] = war_zone_cv_mod
        return out

    out = {
        "tier": "unqualified",
        "tier_reason": f"{routed_tier}_failed: {eval_result.reason_code}",
        "tier_reference_book": ref_book,
        "tier_reference_odds": ref_odds,
        "routed_tier": routed_tier,
        "tier_gate_results": legacy_gate_results,
        "gate_eval": eval_result.to_dict(),
    }
    if war_zone_cv_mod is not None:
        out["war_zone_cv_modifier"] = war_zone_cv_mod
    return out


# `_infer_sport` was deleted 2026-04-22 along with the `sorter` parameter
# on compute_tier. The driver (recompute.compute_scoring_stack) passes
# `sport` explicitly now.


# =============================================================================
# 3. PP_UTILITY — PP-specific leg usefulness
# =============================================================================

# HARD RULE: pp_odds (American) is NOT a payout multiplier. Do not derive
# goblin/demon/standard labels from it. Use only real PP multiplier data.
#
# Multiplier source is looked up from the prop via these fields (in order):
#   1. prop["pp_combo_multiplier"]   — real PP combo multiplier (float)
#   2. prop["pp_label"]              — explicit "goblin" | "standard" | "demon"
#                                       sourced from a verified PP feed
#   3. prop["pp_multiplier_model"]   — learned predicted multiplier (future)
#
# If none exist, pp_multiplier = None and the multiplier component is
# weighted 0 in the composite. The category system also refuses to emit
# pp_premium / pp_discount without a real source.

def _extract_pp_multiplier(prop: Dict) -> Tuple[Optional[float], Optional[str], str]:
    """
    Returns (multiplier_value, label, source).
    source ∈ {'pp_combo', 'pp_label', 'model', 'none'}.
    """
    m = prop.get("pp_combo_multiplier")
    if m is not None:
        try:
            return float(m), None, "pp_combo"
        except (TypeError, ValueError):
            pass
    lab = prop.get("pp_label")
    if lab in ("goblin", "standard", "demon"):
        return None, lab, "pp_label"
    m2 = prop.get("pp_multiplier_model")
    if m2 is not None:
        try:
            return float(m2), None, "model"
        except (TypeError, ValueError):
            pass
    return None, None, "none"


def compute_pp_utility(
    p_model: Optional[float],
    prop: Dict,
    pp_layer: Optional[Dict],
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
) -> Dict[str, Any]:
    """
    PP-specific leg usefulness (0–100 + category).

    Components (each 0–1; weights auto-renormalize when multiplier missing):
      availability         : 1.0 if DK/MGM ref exists, 0.4 if PP-exclusive
      line_fairness        : 1.0 if PP line == ref line; degraded as diff grows
      model_alignment      : 1 - |p_model - p_ref_fair|  (ref-sourced, not PP)
      edge_confidence      : magnitude of positive p_model - p_ref_fair edge
      multiplier_value     : mapped from real multiplier source, else weighted 0

    Category (disjoint, precedence order):
      pp_scam       — ref exists AND line_fairness < 0.5 AND strong model disagreement
      pp_exclusive  — no DK/MGM match at this line|side (unverifiable)
      pp_premium    — real multiplier source indicates demon-class (ONLY with verified source)
      pp_discount   — real multiplier source indicates goblin-class (ONLY with verified source)
      pp_fair       — default
    """
    if pp_layer is None:
        return {
            "pp_utility": None,
            "pp_utility_category": "no_pp_layer",
            "pp_utility_components": {},
            "pp_multiplier": None,
            "pp_multiplier_source": "none",
            "pp_reference_source": None,
            "pp_playable": False,
            "pp_playability_reason": "no_pp_layer",
        }

    pp_line = _safe_float(pp_layer.get("line"))
    mult_value, mult_label, mult_source = _extract_pp_multiplier(prop)

    # -----------------------------------------------------------------
    # PP playability constraint (does NOT touch vision_score or tier)
    #
    # PrizePicks rule: UNDER selections are only playable on STANDARD
    # lines. UNDERs on demon / goblin alternate lines are NOT playable.
    # This is a PP-surface-only concern — core scoring (vision_score,
    # tier, p_true, edge) MUST remain populated regardless.
    # -----------------------------------------------------------------
    direction = (prop.get("direction") or prop.get("recommendation") or "").upper()
    is_under = "UNDER" in direction
    nonstandard_label = mult_label in ("demon", "goblin")
    if is_under and nonstandard_label:
        pp_playable = False
        pp_playability_reason = "under_nonstandard_not_playable"
    else:
        pp_playable = True
        pp_playability_reason = "playable"

    # Reference line / fair prob: DK first, then MGM.
    ref_line = None
    ref_source = None
    ref_odds = None
    if dk_layer and dk_layer.get("line") is not None:
        ref_line = _safe_float(dk_layer.get("line"))
        ref_odds = _safe_float(dk_layer.get("odds"))
        ref_source = "dk"
    elif mgm_layer and mgm_layer.get("line") is not None:
        ref_line = _safe_float(mgm_layer.get("line"))
        ref_odds = _safe_float(mgm_layer.get("odds"))
        ref_source = "mgm"

    p_ref = _american_to_prob(ref_odds)

    # --- Component: availability ---
    has_ref = ref_line is not None
    availability = 1.0 if has_ref else 0.4

    # --- Component: line_fairness ---
    if ref_line is None:
        line_fairness = 0.5  # unverifiable
    else:
        diff = abs((pp_line or 0.0) - ref_line)
        line_fairness = max(0.0, min(1.0, 1.0 - diff))

    # --- Component: model_alignment (model vs reference-market fair) ---
    if p_model is not None and p_ref is not None:
        model_alignment = max(0.0, 1.0 - abs(p_model - p_ref) * 2.0)  # 0.5 apart → 0
    else:
        model_alignment = 0.5

    # --- Component: edge_confidence (positive model edge vs ref) ---
    if p_model is not None and p_ref is not None:
        raw_edge = p_model - p_ref
        edge_confidence = max(0.0, min(1.0, raw_edge * 2.5 + 0.0))  # +0.4 edge → 1.0
    else:
        edge_confidence = 0.0

    # --- Component: multiplier_value ---
    # Only contributes when a real multiplier source exists.
    multiplier_value = None
    if mult_source == "pp_combo" and mult_value is not None:
        # Map multiplier directly: ~1.0x = fair, >1.5x = demon-ish, <1.0x = goblin-ish.
        # Scale to [0,1]: 0.5x → 0, 1.0x → 0.5, 2.0x → 1.0 (clipped).
        multiplier_value = max(0.0, min(1.0, (mult_value - 0.5) / 1.5))
    elif mult_source == "pp_label" and mult_label is not None:
        multiplier_value = {"goblin": 0.25, "standard": 0.5, "demon": 0.9}.get(mult_label, 0.5)
    elif mult_source == "model" and mult_value is not None:
        multiplier_value = max(0.0, min(1.0, (mult_value - 0.5) / 1.5))
    # else: multiplier_value stays None → weighted 0

    # --- Composite utility (renormalize weights when multiplier absent) ---
    weights_full = {
        "availability": 0.15,
        "line_fairness": 0.20,
        "model_alignment": 0.25,
        "edge_confidence": 0.20,
        "multiplier_value": 0.20,
    }
    if multiplier_value is None:
        # Drop multiplier weight, renormalize the remaining four to sum to 1.0
        weights = {k: v for k, v in weights_full.items() if k != "multiplier_value"}
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}
        composite = (
            availability * weights["availability"]
            + line_fairness * weights["line_fairness"]
            + model_alignment * weights["model_alignment"]
            + edge_confidence * weights["edge_confidence"]
        )
    else:
        weights = weights_full
        composite = (
            availability * weights["availability"]
            + line_fairness * weights["line_fairness"]
            + model_alignment * weights["model_alignment"]
            + edge_confidence * weights["edge_confidence"]
            + multiplier_value * weights["multiplier_value"]
        )
    pp_utility = round(composite * 100.0, 1)

    # --- Category ---
    strong_disagreement = (
        p_model is not None and p_ref is not None and abs(p_model - p_ref) > 0.15
    )
    if has_ref and line_fairness < 0.5 and strong_disagreement:
        category = "pp_scam"
    elif not has_ref:
        category = "pp_exclusive"
    elif mult_source in ("pp_combo", "pp_label", "model") and mult_label == "demon":
        category = "pp_premium"
    elif mult_source in ("pp_combo", "pp_label", "model") and mult_label == "goblin":
        category = "pp_discount"
    elif mult_source == "pp_combo" and mult_value is not None and mult_value >= 1.5:
        category = "pp_premium"
    elif mult_source == "pp_combo" and mult_value is not None and mult_value < 1.0:
        category = "pp_discount"
    else:
        # Default: fair. Do NOT guess premium/discount from odds.
        category = "pp_fair"

    # When the prop is not PP-playable, zero out PP-surface outputs but
    # keep the component diagnostics intact for audit / debugging.
    if not pp_playable:
        pp_utility_out = None
        category_out = "pp_not_playable"
    else:
        pp_utility_out = pp_utility
        category_out = category

    return {
        "pp_utility": pp_utility_out,
        "pp_utility_category": category_out,
        "pp_utility_components": {
            "availability": round(availability, 4),
            "line_fairness": round(line_fairness, 4),
            "model_alignment": round(model_alignment, 4),
            "edge_confidence": round(edge_confidence, 4),
            "multiplier_value": (
                round(multiplier_value, 4) if multiplier_value is not None else None
            ),
            "weights": weights,
            # Raw pp_utility before playability masking (for diagnostics)
            "pp_utility_raw": pp_utility,
            "category_raw": category,
        },
        "pp_multiplier": mult_value,
        "pp_multiplier_label": mult_label,
        "pp_multiplier_source": mult_source,
        "pp_reference_source": ref_source,
        "pp_playable": pp_playable,
        "pp_playability_reason": pp_playability_reason,
    }


# =============================================================================
# Composed entry point
# =============================================================================

def compute_scoring_stack(
    prop: Dict,
    p_model: Optional[float],
    cv: Optional[float],
    hit_rate: Optional[float],
    edge_pct: Optional[float],
    tp: Optional[float],
    ceiling_rate: Optional[float],
    books_available_count: int,
    sport: str,
    avg_hit_margin: Optional[float] = None,
    avg_miss_margin: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compose all three scoring dimensions for a canonical prop.
    Gate evaluation is delegated to the Universal Gate Engine via
    `compute_tier(..., sport=sport)`. No sport-specific gate code runs here.
    """
    pp_layer = prop.get("pp_layer")
    dk_layer = prop.get("dk_layer")
    mgm_layer = prop.get("mgm_layer")
    sharp_layer = prop.get("sharp_layer")
    fd_layer = prop.get("fd_layer")
    bol_layer = prop.get("bol_layer")

    vs = compute_vision_score(
        p_model=p_model,
        dk_layer=dk_layer, mgm_layer=mgm_layer, sharp_layer=sharp_layer,
        cv=cv, hit_rate=hit_rate, books_available_count=books_available_count,
        fd_layer=fd_layer, sport=sport,
    )
    t = compute_tier(
        prop=prop,
        cv=cv, hit_rate=hit_rate, edge_pct=edge_pct, tp=tp, ceiling_rate=ceiling_rate,
        dk_layer=dk_layer, mgm_layer=mgm_layer,
        sport=sport,
        p_model=p_model,
        avg_hit_margin=avg_hit_margin,
        avg_miss_margin=avg_miss_margin,
        fd_layer=fd_layer, bol_layer=bol_layer,
    )
    pp = compute_pp_utility(
        p_model=p_model, prop=prop,
        pp_layer=pp_layer, dk_layer=dk_layer, mgm_layer=mgm_layer,
    )
    return {**vs, **t, **pp}
