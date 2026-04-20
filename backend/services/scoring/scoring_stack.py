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
    - Driven by reference odds (dk → mgm fallback) + existing MLBTierSorter gates
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
) -> Tuple[Optional[float], str]:
    """
    Sharp-first fair probability source selection.
    Returns (fair_prob [0,1] or None, source_label).
    PP data is NEVER considered as a fair-price source.

    The source_label reflects the actual book used:
      - If sharp_layer present, uses its ``book`` key (e.g., 'pinnacle', 'betonline')
      - consensus(dk,mgm) / dk / mgm otherwise
    """
    sharp_odds = sharp_layer.get("odds") if sharp_layer else None
    dk_odds = dk_layer.get("odds") if dk_layer else None
    mgm_odds = mgm_layer.get("odds") if mgm_layer else None

    sharp_p = _american_to_prob(sharp_odds)
    dk_p = _american_to_prob(dk_odds)
    mgm_p = _american_to_prob(mgm_odds)

    if sharp_p is not None:
        book_name = (sharp_layer or {}).get("book") or "sharp"
        return sharp_p, str(book_name).lower()
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
) -> Dict[str, Any]:
    """
    Platform-agnostic pick quality.

    Returns:
      {
        "vision_score_raw": float or None,   # unnormalized; used for percentile pass
        "vision_score": float or None,       # populated later by percentile pass
        "quality_source": str,               # pinnacle | consensus | dk | mgm | insufficient_market
        "fair_prob": float or None,
        "stability": float or None,
        "confidence": float or None,
        "edge_vs_fair": float or None,       # p_model - fair_prob  (can be negative)
      }
    """
    fair_prob, quality_source = _pick_fair_probability(
        None, dk_layer, mgm_layer, sharp_layer
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
# 2. TIER — Risk bucket via reference-market gates
# =============================================================================

# =============================================================================
# Reference-odds hard admission bands (LOCKED 2026-04-17)
# =============================================================================
# A prop must FIRST fall inside the correct band based on reference-market
# odds. Only then are quality gates (CV, hit_rate, edge, ceiling, vision_score)
# evaluated. If the prop is inside the band but fails quality gates, it goes
# to `unqualified` — never to a different tier.
#
#   Safe Haven    : ref_odds <= -240
#   Front Lines   : -239 <= ref_odds <= +149
#   War Zone      : ref_odds >= +150
#
# These constants are the SINGLE source of truth for tier admission and
# MUST NOT be overridden by sport adapters.
_REF_SAFE_HAVEN_MAX = -240
_REF_WAR_ZONE_MIN = 150


def _pick_reference_odds(
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
) -> Tuple[Optional[float], str]:
    """DK primary, MGM fallback. Pinnacle is NOT used for tier (per existing sorter)."""
    dk_odds = dk_layer.get("odds") if dk_layer else None
    mgm_odds = mgm_layer.get("odds") if mgm_layer else None
    if dk_odds is not None:
        return dk_odds, "dk"
    if mgm_odds is not None:
        return mgm_odds, "mgm"
    return None, "none"


def _model_contradicts_anchor(prop: Dict, side: str) -> Optional[str]:
    """PP-anchor direction veto (2026-04-19).

    PrizePicks demons / goblins force a fixed OVER/UNDER payout side that the
    scoring/selection layers otherwise honour. When every other evidence
    stream points the opposite way, that anchor becomes a trap — we would
    surface a bullish-looking card on a prop the core model doesn't believe in.

    Veto fires when ALL three contradict the anchor side for an OVER pick:
      - vk_edge (model_projection − line) is negative
      - hit_rate_over (or hit_rate_under for UNDER) is below 50
      - newest-L10 average is on the wrong side of the line

    (Symmetric on UNDER.)

    Returns a human-readable reason string when vetoed, else None.
    """
    side = (side or "OVER").upper()
    line = prop.get("line")
    if line is None:
        return None

    vk_edge = prop.get("vk_edge")
    if vk_edge is None:
        mp = prop.get("model_projection")
        if isinstance(mp, (int, float)):
            vk_edge = float(mp) - float(line)
    hit_rate_over = prop.get("hit_rate_over")
    hit_rate_under = prop.get("hit_rate_under")
    l10_avg = prop.get("l10_avg")

    if side == "OVER":
        # Require all three to exist and all three to contradict OVER.
        if not (isinstance(vk_edge, (int, float)) and vk_edge < 0):
            return None
        if not (isinstance(hit_rate_over, (int, float)) and hit_rate_over < 50):
            return None
        if not (isinstance(l10_avg, (int, float)) and l10_avg < line):
            return None
        return (
            f"model_contradicts_anchor: vk_edge={vk_edge:.2f}<0, "
            f"hit_rate_over={hit_rate_over}%<50, l10_avg={l10_avg}<line={line}"
        )
    # UNDER
    if not (isinstance(vk_edge, (int, float)) and vk_edge > 0):
        return None
    if not (isinstance(hit_rate_under, (int, float)) and hit_rate_under < 50):
        return None
    if not (isinstance(l10_avg, (int, float)) and l10_avg > line):
        return None
    return (
        f"model_contradicts_anchor: vk_edge={vk_edge:.2f}>0, "
        f"hit_rate_under={hit_rate_under}%<50, l10_avg={l10_avg}>line={line}"
    )


def compute_tier(
    sorter,
    prop: Dict,
    cv: Optional[float],
    hit_rate: Optional[float],
    edge_pct: Optional[float],
    tp: Optional[float],
    ceiling_rate: Optional[float],
    dk_layer: Optional[Dict],
    mgm_layer: Optional[Dict],
    p_model: Optional[float] = None,
) -> Dict[str, Any]:
    """Assign risk bucket using existing MLBTierSorter gates on a reference book."""
    ref_odds, ref_book = _pick_reference_odds(dk_layer, mgm_layer)

    # Side-aware gate inputs. For UNDER picks we pass `p_model_pct` through
    # so the sorter can replace the market-implied `gate_tp` with a
    # model-confidence floor (path (c): "UNDER tp = model confidence").
    # OVER behaviour is unchanged — sorter ignores side when it equals OVER.
    side = (prop.get("recommendation") or "OVER").upper()
    p_model_pct = round((p_model or 0.0) * 100.0, 1) if p_model is not None else None

    if ref_odds is None:
        return {
            "tier": "unqualified",
            "tier_reason": "no_reference_market",
            "tier_reference_book": "none",
            "tier_reference_odds": None,
            "tier_gate_results": {},
        }

    # Direction veto — PP demon/goblin anchor can't force a side against
    # converging negative evidence (model edge < 0, anchor-side hit rate < 50,
    # newest-L10 avg on wrong side). Fires BEFORE any tier gate so the pick
    # never reaches the board.
    veto_reason = _model_contradicts_anchor(prop, side)
    if veto_reason:
        return {
            "tier": "unqualified",
            "tier_reason": veto_reason,
            "tier_reference_book": ref_book,
            "tier_reference_odds": ref_odds,
            "tier_gate_results": {},
        }

    # Historical profitable-signal gate (2026-02-20): the VK1 backtest that
    # produced +19.26% real-odds AST ROI on 4,249 bets applied
    # `confidence_threshold = 55.0` on the Gaussian p_over. Every prop below
    # 0.55 model confidence is rejected to `unqualified` before any tier-
    # specific gate runs. This mirrors the single filter responsible for the
    # historical edge; no other gates are added.
    if p_model is not None and p_model < 0.55:
        return {
            "tier": "unqualified",
            "tier_reason": "p_model<0.55",
            "tier_reference_book": ref_book,
            "tier_reference_odds": ref_odds,
            "tier_gate_results": {},
        }

    # Safe Haven — short odds
    if ref_odds <= _REF_SAFE_HAVEN_MAX:
        passed, reason, gates = sorter.check_safe_haven_gates(
            prop, cv, hit_rate, edge_pct, tp,
            side=side, p_model_pct=p_model_pct,
        )
        if passed:
            return {
                "tier": "safe_haven", "tier_reason": "gates_passed",
                "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
                "tier_gate_results": gates,
            }
        return {
            "tier": "unqualified", "tier_reason": f"safe_haven_failed: {reason}",
            "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
            "tier_gate_results": gates,
        }

    # War Zone — long odds
    if ref_odds >= _REF_WAR_ZONE_MIN:
        passed, reason, gates = sorter.check_war_zone_gates(prop, cv, ceiling_rate, edge_pct)
        if passed:
            return {
                "tier": "war_zone", "tier_reason": "gates_passed",
                "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
                "tier_gate_results": gates,
            }
        return {
            "tier": "unqualified", "tier_reason": f"war_zone_failed: {reason}",
            "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
            "tier_gate_results": gates,
        }

    # Front Lines — middle band
    passed, reason, gates = sorter.check_front_lines_gates(
        prop, cv, hit_rate, edge_pct, tp,
        side=side, p_model_pct=p_model_pct,
    )
    if passed:
        return {
            "tier": "front_lines", "tier_reason": "gates_passed",
            "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
            "tier_gate_results": gates,
        }
    return {
        "tier": "unqualified", "tier_reason": f"front_lines_failed: {reason}",
        "tier_reference_book": ref_book, "tier_reference_odds": ref_odds,
        "tier_gate_results": gates,
    }


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
    sorter,
) -> Dict[str, Any]:
    """
    Compose all three scoring dimensions for a canonical prop.
    No field produced by one dimension is used as input to another.
    """
    pp_layer = prop.get("pp_layer")
    dk_layer = prop.get("dk_layer")
    mgm_layer = prop.get("mgm_layer")
    sharp_layer = prop.get("sharp_layer")

    vs = compute_vision_score(
        p_model=p_model,
        dk_layer=dk_layer, mgm_layer=mgm_layer, sharp_layer=sharp_layer,
        cv=cv, hit_rate=hit_rate, books_available_count=books_available_count,
    )
    t = compute_tier(
        sorter=sorter, prop=prop,
        cv=cv, hit_rate=hit_rate, edge_pct=edge_pct, tp=tp, ceiling_rate=ceiling_rate,
        dk_layer=dk_layer, mgm_layer=mgm_layer,
        p_model=p_model,
    )
    pp = compute_pp_utility(
        p_model=p_model, prop=prop,
        pp_layer=pp_layer, dk_layer=dk_layer, mgm_layer=mgm_layer,
    )
    return {**vs, **t, **pp}
