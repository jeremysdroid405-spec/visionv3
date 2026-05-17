"""Production eligibility SSOT (Phase A).

`apply_production_eligibility(props, sport, ...)` is the single
function every code path — live, historical, replay, test — MUST
call to gate which props enter scoring. It encapsulates the exact
3-step decoration chain that previously lived (duplicated) in:

  • `services/scoring/adapters/mlb_scoring.py::load_live_props`
  • `services/scoring/adapters/nba_scoring.py::load_live_props`
  • `services/scoring/recompute.py::recompute_sport` (caller-supplied)

Behavioural contract — bit-identical to the inline chain it replaces:

  1. **`filter_priceable`** (0-Book Exclusion Rule, 2026-04-22)
     Stamps `book_count` / `coverage_class` / `books_anchored` on
     every input prop in place. Drops `book_count == 0` (pp_only)
     rows.
  2. **`build_companion_map`** (Multi-book TP de-vig, 2026-04-22)
     Built over the FULL pre-filter prop list so OVER-side TP
     pairing survives the eventual UNDER drop.
  3. **`filter_pp_playable`** (Side-aware PP filter, 2026-05)
     Drops every row where `playable_on_pp != True`.

Fallback path (`use_pp_registry_fallback=True`, default False):
When a prop carries NO `playable_on_pp` field AND no `pp_layer`
field, the prop is considered to come from a historical / test
input. The hardcoded `SPORT_PP_SIDE_REGISTRY` is consulted to
fail-closed decide whether the `(stat_family, side)` is
PrizePicks-playable structurally. The function NEVER invents
playability for live inputs — live props that hit
`load_live_props` already carry `playable_on_pp` set by
`universal_odds_sync._normalize_market_data`.

Phase A goal: live behaviour byte-identical. The fallback path is
only consumed by Phase B+ historical/test entrypoints (NOT wired
yet).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.scoring.coverage_filter import (
    filter_priceable, filter_pp_playable,
)
from services.scoring.tp_engine import build_companion_map
from services.pipeline.pp_playability_registry import is_pp_playable_side

logger = logging.getLogger(__name__)


@dataclass
class EligibilityResult:
    """Structured return of `apply_production_eligibility`.

    Carries every artifact the prior inline chain stashed on the
    adapter instance (`last_coverage_stats`, `_companion_map`,
    `last_pp_playable_stats`) so the caller can persist them
    unchanged. The decision NOT to mutate adapter state here keeps
    this module purely functional and importable from contexts that
    don't have an adapter (recompute caller-supplied branch, future
    historical providers).
    """
    props: List[Dict[str, Any]]
    coverage_stats: Dict[str, Any] = field(default_factory=dict)
    pp_playable_stats: Dict[str, Any] = field(default_factory=dict)
    companion_map: Dict[Any, Any] = field(default_factory=dict)
    pp_registry_fallback_applied: int = 0


def _apply_pp_registry_fallback(
    props: List[Dict[str, Any]], *, sport: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Stamp `playable_on_pp` (bool) on every prop that lacks it,
    using the hardcoded registry. NEVER overrides an existing value
    — live props that carry `playable_on_pp` set by the sync are
    trusted as-is.

    Returns `(props, n_stamped)` — same list, mutated in place.
    """
    n_stamped = 0
    for p in props:
        if p.get("playable_on_pp") is not None:
            continue
        if p.get("pp_layer") is not None:
            # PP-layer present → trust the sync. Stamp accordingly.
            p["playable_on_pp"] = True
            continue
        # No PP signal at all → consult registry fail-closed.
        stat_family = (
            p.get("stat_family") or p.get("stat") or p.get("stat_type")
        )
        side = (p.get("side") or p.get("recommendation") or "").upper() or None
        p["playable_on_pp"] = is_pp_playable_side(sport, stat_family, side)
        n_stamped += 1
    return props, n_stamped


def apply_production_eligibility(
    props: List[Dict[str, Any]], *,
    sport: str,
    run_id: Optional[str] = None,
    use_pp_registry_fallback: bool = False,
) -> EligibilityResult:
    """Phase A SSOT entry point.

    Args:
        props: raw prop dicts straight from the input source
            (`{sport}_live_props` for live, normalized historical
            rows for test mode — both must already conform to the
            scoring-time prop shape).
        sport: lowercase sport key (`"mlb"`, `"nba"`, `"nfl"`).
            Required so the inner filters tag their log lines
            uniformly and the registry fallback can sport-route.
        run_id: optional tag for greppable pipeline logs.
        use_pp_registry_fallback: when True, props that lack BOTH
            `playable_on_pp` and `pp_layer` are stamped via the
            hardcoded registry BEFORE `filter_pp_playable` runs.
            Default False — live path MUST never need this; it's
            for historical / test providers in Phase B.

    Returns: `EligibilityResult` (see dataclass).

    Behaviour parity:
      live caller (sport adapter) calls with
      `use_pp_registry_fallback=False` and gets a byte-identical
      filtered list + companion map + stats dicts vs the previous
      inline chain.
    """
    # ── Step 0: optional fail-closed PP registry stamping ────────
    n_registry = 0
    if use_pp_registry_fallback:
        props, n_registry = _apply_pp_registry_fallback(
            props, sport=sport,
        )

    # ── Step 1: filter_priceable (0-Book Exclusion Rule) ─────────
    # Mutates props in place with book_count / coverage_class /
    # books_anchored. Drops 0-book rows.
    priceable, coverage_stats = filter_priceable(
        props, sport=sport, run_id=run_id,
    )

    # ── Step 2: build_companion_map over the FULL pre-filter pool
    # NOT just the priceable subset — UNDER-side TP de-vig must
    # still find its OVER companion when the OVER is sportsbook-
    # only and got pp-filtered.
    companion_map = build_companion_map(props)

    # ── Step 3: filter_pp_playable (side-aware PP filter) ────────
    pp_playable, pp_stats = filter_pp_playable(priceable, sport=sport)

    if use_pp_registry_fallback and n_registry > 0:
        logger.info(
            "[ELIGIBILITY:%s%s] pp_registry_fallback stamped %d rows "
            "(live-path: 0 expected; historical/test path: explicit)",
            sport.upper(),
            f":{run_id}" if run_id else "",
            n_registry,
        )

    return EligibilityResult(
        props=pp_playable,
        coverage_stats=coverage_stats,
        pp_playable_stats=pp_stats,
        companion_map=companion_map,
        pp_registry_fallback_applied=n_registry,
    )


__all__ = ["apply_production_eligibility", "EligibilityResult"]
