"""
Universal Badge Enrichment Service
==================================

Single sport-routing entry point for board-pick badge enrichment. Owns the
contract for the four canonical badge fields:

    pick["scout_badges"]                 - performance / scout / situational
    pick["context_badges"]               - player-level context (home_cookin,
                                            revenge_game, ...)  ← NBA today
    pick["active_badges"]                - reserved (always present, default [])
    pick["intel_suite"]["context_badges"] - intel-suite mirror; written by
                                            existing enrich_*_intel_suite
                                            paths — this service ONLY ensures
                                            the field is well-formed.

Why this module exists
----------------------
Pre-2026-05-08 the route file (`routes/ferrari_tiers.py`) called the
performance-badge generator inline per pick. MLB-specific environmental
badges (`wind_boost`, `cold_zone`, `bvp_dominator`, `high_heat_trap`,
`hitters_haven`) were architecturally available via `services/mlb_badge_system.py`
but the only call sites passed `weather=None / park=None / umpire_data=None /
opponent_pitcher=None`, so every environmental gate failed silently.
Restoring the universal-badge architecture means routes call ONE service,
the service routes by sport, and sport-specific enrichers live in their own
modules.

Architectural rules
-------------------
* `routes/*` may import this service. This service MUST NOT import from
  `routes/*` (no backwards dependency, no circular import risk).
* All sport-specific enrichers live in dedicated service modules
  (`services/performance_badges.py`, `services/mlb_environmental_badges.py`).
* This service is a thin dispatcher + normalizer; it does not embed
  badge-generation logic of its own.
* Every step is failure-isolated. The board must always load even if every
  badge subsystem fails.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── canonical-field shape helpers ────────────────────────────────────
def _badge_key(badge: Any) -> Optional[str]:
    """Mirrors the de-dup key used in routes/ferrari_tiers.py. Accepts
    either dict-shaped badges (canonical) or raw strings (legacy)."""
    if isinstance(badge, dict):
        return badge.get("badge_key") or badge.get("id")
    if isinstance(badge, str):
        return badge
    return None


def _resolve_side(pick: Dict[str, Any]) -> str:
    """Identical contract to performance_badges._resolve_side and
    routes/ferrari_tiers._apply_universal_scout_badges. Kept inline
    here so this dispatcher does not depend on either file."""
    return (
        pick.get("recommendation")
        or pick.get("side")
        or pick.get("direction")
        or "OVER"
    ).strip().upper()


def _ensure_normalized_fields(pick: Dict[str, Any]) -> None:
    """Guarantee the four canonical badge fields exist with sane
    defaults. This is a SHAPE contract only — does NOT generate any
    badge content. NBA `context_badges` overlaid earlier by the reader
    (`routes/ferrari_tiers.py` :976) is preserved as-is."""
    if not isinstance(pick.get("scout_badges"), list):
        pick["scout_badges"] = list(pick.get("scout_badges") or [])
    if not isinstance(pick.get("context_badges"), list):
        # context_badges may legitimately be None from cached_board; coerce
        # to [] so the frontend can iterate without null-guards.
        pick["context_badges"] = list(pick.get("context_badges") or [])
    if not isinstance(pick.get("active_badges"), list):
        pick["active_badges"] = list(pick.get("active_badges") or [])
    intel = pick.get("intel_suite")
    if isinstance(intel, dict):
        sub = intel.get("context_badges")
        if not isinstance(sub, list):
            # Coerce None / missing to []. The frontend reads this
            # field and benefits from a stable list shape; existing
            # populated lists are preserved unchanged.
            intel["context_badges"] = list(sub or [])


def _merge_badges_dedup(
    existing: List[Any], incoming: List[Dict[str, Any]]
) -> List[Any]:
    """Append incoming badges that aren't already present (by id /
    badge_key). Preserves order of existing first, then incoming."""
    out = list(existing or [])
    seen = {k for k in (_badge_key(b) for b in out) if k}
    for b in incoming or []:
        if not isinstance(b, dict):
            continue
        key = _badge_key(b)
        if key and key not in seen:
            out.append(b)
            seen.add(key)
    return out


# ─── universal scout-badge step (sport-agnostic) ──────────────────────
def _apply_universal_scout_badges_inline(pick: Dict[str, Any]) -> None:
    """Inlined copy of `routes/ferrari_tiers.py::_apply_universal_scout_badges`,
    living in the service layer to avoid a service→route import. The
    route-level original is left in place untouched for any other
    callers (e.g. `_apply_under_badge_rewire`).

    Behaviour: skip UNDER picks (handled by `_apply_under_badge_rewire`
    in the route layer); for OVER picks, re-derive deterministic scout
    badges via `services/performance_badges.py` and merge into existing
    `pick["scout_badges"]` deduplicated by `badge_key` / `id`."""
    if "UNDER" in _resolve_side(pick):
        return
    from services.performance_badges import generate_performance_badges
    rederived = generate_performance_badges(pick) or []
    pick["scout_badges"] = _merge_badges_dedup(
        pick.get("scout_badges") or [], rederived
    )


# ─── public dispatcher ────────────────────────────────────────────────
async def enrich_pick_badges(
    pick: Dict[str, Any], *, sport: str, db: Any = None
) -> None:
    """Universal entry point. Mutates pick in place. NEVER raises.

    Sport routing
    -------------
    * **NBA** — runs the universal scout-badge step. Player-level
      `context_badges` are overlaid earlier by the cached_board reader
      (routes/ferrari_tiers.py :976) — this service preserves them.
    * **MLB** — runs the universal scout-badge step, then delegates to
      `services/mlb_environmental_badges.py` to add the environmental
      badges (`wind_boost`, `cold_zone`, `high_heat_trap`,
      `bvp_dominator`, `hitters_haven`, `pure_contact`, `barrel_master`,
      `whiff_wizard`, `volatility_extreme`) using the existing
      `MLBBadgeService` invoked with populated weather / park /
      umpire_data / opponent_pitcher context.

    Failure isolation
    -----------------
    Every step has its own try/except. A failure in any one step
    leaves the pick with whatever badge state was already populated;
    the board always loads.
    """
    if not isinstance(pick, dict):
        return

    sport_lower = (sport or "").lower()

    # Step 1: universal scout-badge enrichment (sport-agnostic).
    try:
        _apply_universal_scout_badges_inline(pick)
    except Exception:
        logger.exception(
            "[BADGE_ENRICH:%s] universal scout step failed", sport_lower
        )

    # Step 2: sport-specific enrichers.
    if sport_lower == "mlb":
        try:
            from services.mlb_environmental_badges import (
                apply_mlb_environmental_badges,
            )
            await apply_mlb_environmental_badges(pick, db)
        except Exception:
            logger.exception(
                "[BADGE_ENRICH:mlb] environmental step failed"
            )

    # NBA: no extra step today. The MLB context_badges hourly-job
    # symmetric writer is intentionally deferred (see audit note —
    # ContextBadgeService NBA-hardcoding fix is a separate patch).

    # Step 3: shape guarantee. Run last so all upstream writes settle
    # into normalized list types.
    try:
        _ensure_normalized_fields(pick)
    except Exception:
        logger.exception(
            "[BADGE_ENRICH:%s] field-normalization step failed",
            sport_lower,
        )


__all__ = [
    "enrich_pick_badges",
]
