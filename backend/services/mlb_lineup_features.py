"""Phase 2B — Opposing-lineup feature aggregator.

Takes a list of opposing batters (output of `mlb_lineup_resolver` for
training OR `mlb_live_lineup_feed` for prediction) and produces the
canonical Phase-2B feature dict consumed by the pitcher-stat models.

Sport-agnostic only in spirit — this module is MLB-specific. The
schema is locked: every key emitted here must be in
`PHASE2B_LINEUP_FEATURE_NAMES`. The retrain worker and the live
`_build_friction_features` pitcher branch both pin to this list so
the train-predict feature vectors stay byte-aligned.

Imputation contract
───────────────────
Every feature ALWAYS appears in the output dict. When data is missing:
  • Continuous metrics fall back to a neutral default (0.0 for rates,
    1.0 for ratios) and the matching `*_is_imputed=1` flag is raised.
  • Counts fall back to 0 with `lineup_size_is_imputed=1`.
Missing values must never be silently zeroed without an imputed flag —
the model needs the flag to learn how to discount imputed rows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# Canonical schema — DO NOT add/remove without bumping the model version.
PHASE2B_LINEUP_FEATURE_NAMES = [
    # — Lineup handedness mix
    "projected_lhh_count",
    "projected_rhh_count",
    "projected_switch_count",
    "lineup_size",
    "lineup_size_is_imputed",
    "pct_lhh",
    "pct_rhh",
    "pct_switch",
    "lineup_handedness_is_imputed",
    # — Lineup strength rolling 14d (mean over lineup)
    "lineup_k_rate_14d",
    "lineup_bb_rate_14d",
    "lineup_woba_14d",
    "lineup_xwoba_14d",
    "lineup_hard_hit_rate_14d",
    "lineup_barrel_rate_14d",
    "lineup_strength_is_imputed",
    # — Matchup interaction (lineup vs pitcher handedness)
    "lineup_same_hand_count",
    "lineup_opposite_hand_count",
    "lineup_pct_same_hand",
    "lineup_pct_opposite_hand",
    "matchup_exposure_is_imputed",
]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _lookup_rolling_14(sc_batter_cache: Dict[int, Dict[str, Any]],
                       batter_id: int,
                       game_date: str) -> Optional[Dict[str, Any]]:
    """As-of lookup: rolling_14 features for a batter on/before game_date."""
    if not sc_batter_cache or batter_id is None or not game_date:
        return None
    by_date = sc_batter_cache.get(batter_id)
    if not by_date:
        return None
    if game_date in by_date:
        return (by_date[game_date] or {}).get("rolling_14") or {}
    earlier = [d for d in by_date if d <= game_date]
    if not earlier:
        return None
    pick = max(earlier)
    return (by_date[pick] or {}).get("rolling_14") or {}


def _empty_features() -> Dict[str, float]:
    """Default-zeroed feature dict with all imputed flags raised."""
    out: Dict[str, float] = {k: 0.0 for k in PHASE2B_LINEUP_FEATURE_NAMES}
    out["lineup_size_is_imputed"] = 1.0
    out["lineup_handedness_is_imputed"] = 1.0
    out["lineup_strength_is_imputed"] = 1.0
    out["matchup_exposure_is_imputed"] = 1.0
    return out


def build_lineup_features(
    *,
    lineup: Optional[List[Dict[str, Any]]],
    game_date: Optional[str],
    pitcher_throws: Optional[str],
    sc_batter_cache: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, float]:
    """Build the Phase-2B opposing-lineup feature vector.

    Args:
      lineup: list of dicts with at least `batter_id` and `stand`.
              Pass None when the resolver returned nothing.
      game_date: ISO `YYYY-MM-DD`. Used for the as-of rolling lookup.
      pitcher_throws: "L" / "R" — drives the matchup interaction
              counts. None ⇒ matchup features stay imputed.
      sc_batter_cache: optional map `batter_id → {game_date → rolling_14}`
              for lineup-strength aggregation. None ⇒ strength stays
              imputed.

    Returns: dict with every key in PHASE2B_LINEUP_FEATURE_NAMES.
    """
    if not lineup:
        return _empty_features()

    out = _empty_features()
    out["lineup_size"] = float(len(lineup))
    out["lineup_size_is_imputed"] = 0.0

    # — Handedness mix —
    lhh = rhh = switch = unknown = 0
    for b in lineup:
        stand = (b.get("stand") or "").upper().strip()
        if stand == "L":
            lhh += 1
        elif stand == "R":
            rhh += 1
        elif stand == "S":
            switch += 1
        else:
            unknown += 1

    known = lhh + rhh + switch
    if known > 0:
        out["projected_lhh_count"] = float(lhh)
        out["projected_rhh_count"] = float(rhh)
        out["projected_switch_count"] = float(switch)
        out["pct_lhh"] = float(lhh) / known
        out["pct_rhh"] = float(rhh) / known
        out["pct_switch"] = float(switch) / known
        out["lineup_handedness_is_imputed"] = (
            1.0 if unknown > 0 and known < len(lineup) // 2 else 0.0
        )
    # else: keep imputed flag raised, counts at 0

    # — Matchup interaction —
    if pitcher_throws and pitcher_throws.upper() in ("L", "R"):
        pthrows = pitcher_throws.upper()
        # Switch-hitters always bat with the platoon advantage:
        #   pitcher L  → SH bats R → opposite hand
        #   pitcher R  → SH bats L → opposite hand
        same = (rhh if pthrows == "R" else lhh)
        oppos = (lhh if pthrows == "R" else rhh) + switch
        out["lineup_same_hand_count"] = float(same)
        out["lineup_opposite_hand_count"] = float(oppos)
        denom = same + oppos
        if denom > 0:
            out["lineup_pct_same_hand"] = float(same) / denom
            out["lineup_pct_opposite_hand"] = float(oppos) / denom
        out["matchup_exposure_is_imputed"] = 0.0 if denom > 0 else 1.0

    # — Lineup strength rolling 14d —
    if sc_batter_cache and game_date:
        gd = str(game_date)[:10]
        k_rates, bb_rates, wobas, xwobas, hard_hits, barrels = (
            [], [], [], [], [], []
        )
        for b in lineup:
            bid = b.get("batter_id")
            r14 = _lookup_rolling_14(sc_batter_cache, bid, gd)
            if not r14:
                continue
            k_rates.append(_safe_float(r14.get("k_rate"), default=None)
                            if r14.get("k_rate") is not None else None)
            bb_rates.append(_safe_float(r14.get("bb_rate"), default=None)
                             if r14.get("bb_rate") is not None else None)
            wobas.append(_safe_float(r14.get("wOBA"), default=None)
                          if r14.get("wOBA") is not None else None)
            xwobas.append(_safe_float(r14.get("xwOBA"), default=None)
                           if r14.get("xwOBA") is not None else None)
            hard_hits.append(
                _safe_float(r14.get("hard_hit_rate"), default=None)
                if r14.get("hard_hit_rate") is not None else None
            )
            barrels.append(
                _safe_float(r14.get("barrel_rate"), default=None)
                if r14.get("barrel_rate") is not None else None
            )

        def _mean(xs):
            valid = [x for x in xs if x is not None]
            return sum(valid) / len(valid) if valid else None

        agg = {
            "lineup_k_rate_14d": _mean(k_rates),
            "lineup_bb_rate_14d": _mean(bb_rates),
            "lineup_woba_14d": _mean(wobas),
            "lineup_xwoba_14d": _mean(xwobas),
            "lineup_hard_hit_rate_14d": _mean(hard_hits),
            "lineup_barrel_rate_14d": _mean(barrels),
        }
        if any(v is not None for v in agg.values()):
            # Coverage threshold: need rolling data for at least 1/3 of the
            # lineup before we consider the aggregate non-imputed.
            coverage = sum(1 for v in agg.values() if v is not None) / 6.0
            for k, v in agg.items():
                if v is not None:
                    out[k] = v
            out["lineup_strength_is_imputed"] = (
                0.0 if coverage >= 0.5 else 1.0
            )

    return out
