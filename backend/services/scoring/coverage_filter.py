"""
Coverage Filter — 0-Book Exclusion Rule (2026-04-22)
=====================================================

Enforces the "no sportsbook anchor → no scoring" pricing-integrity rule:

    book_count == 0  →  coverage_class = "pp_only"  →  EXCLUDED from
                         ranking_score_v2, implied-prob, edge, tier
                         builders, parlay builder, cached board.

    book_count == 1  →  coverage_class = "single_book" → scored normally.
    book_count >= 2  →  coverage_class = "multi_book"  → scored normally.

A prop's book anchor is only counted when the book quotes the **exact**
line PrizePicks anchors on. Nearest-line / fuzzy matching is intentionally
NOT done here — that is the design invariant.

Recognised book-price fields (both NBA and MLB naming conventions):
    DraftKings → ``draftkings_price`` or ``dk_odds``
    FanDuel    → ``fanduel_price``    or ``fd_odds``
    BetOnline  → ``betonline_price``  or ``bol_odds``
    BetMGM     → ``betmgm_price``     or ``mgm_odds``
    Caesars    → ``caesars_price``    or ``csr_odds``     (williamhill_us)

Usage
-----
    from services.scoring.coverage_filter import (
        classify_coverage, filter_priceable
    )

    # Single prop classification (mutates prop in place):
    book_count, coverage_class = classify_coverage(prop)

    # Batch filter (preferred — logs stats once per run):
    kept, stats = filter_priceable(props, sport="nba", run_id=run_id)

Returned stats dict shape
-------------------------
    {
        "total_props_seen": int,
        "total_props_excluded_pp_only": int,
        "total_props_remaining": int,
        "coverage_rate": float,        # remaining / seen (0..1)
        "multi_book": int,
        "single_book": int,
        "pp_only": int,
    }
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tuple of (legacy_name, universal_name) per book. Either key counts as
# an anchor as long as its value is not None.
# 2026-05-13: Added williamhill_us (Caesars). csr_layer/csr_odds was
# wired up by universal_odds_sync on 2026-05-11 but never added to the
# anchor count — leaving 4,379 MLB props (30.2%) with Caesars data
# silently uncounted in `book_count` and `books_anchored`.
# 2026-05-13: "Pull from all books" expansion adds 6 more US books
# (espnbet, hardrockbet, betrivers, betparx, ballybet, fliff). Short
# codes match `universal_odds_sync` flat-field naming.
_BOOK_FIELDS: Tuple[Tuple[str, str, str], ...] = (
    ("draftkings",     "draftkings_price", "dk_odds"),
    ("fanduel",        "fanduel_price",    "fd_odds"),
    ("betonlineag",    "betonline_price",  "bol_odds"),
    ("betmgm",         "betmgm_price",     "mgm_odds"),
    ("williamhill_us", "caesars_price",    "csr_odds"),
    ("espnbet",        "espnbet_price",    "eb_odds"),
    ("hardrockbet",    "hardrockbet_price","hrb_odds"),
    ("betrivers",      "betrivers_price",  "brv_odds"),
    ("betparx",        "betparx_price",    "prx_odds"),
    ("ballybet",       "ballybet_price",   "bly_odds"),
    ("fliff",          "fliff_price",      "flf_odds"),
)


def _is_present(value) -> bool:
    """A price is "present" if it's a concrete number (not None / not 0).
    0 American odds is not a real quote so we reject it too.
    """
    if value is None:
        return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def _extract_price(prop: Dict, legacy_key: str, universal_key: str):
    """Look at both the flat prop and a nested ``sharp_market`` subdoc
    (the NBA demon_goblin path stores prices there in addition to flat)."""
    val = prop.get(legacy_key)
    if _is_present(val):
        return val
    val = prop.get(universal_key)
    if _is_present(val):
        return val
    sharp = prop.get("sharp_market") or {}
    if isinstance(sharp, dict):
        val = sharp.get(legacy_key)
        if _is_present(val):
            return val
    return None


def classify_coverage(prop: Dict) -> Tuple[int, str]:
    """Compute ``(book_count, coverage_class)`` for a single prop and
    stamp the values onto the prop dict in place.

    The prop dict will gain:
        ``book_count`` (int 0..5)
        ``coverage_class`` (``"pp_only"`` | ``"single_book"`` | ``"multi_book"``)
        ``books_anchored`` (list[str]) — which books had an exact-line anchor
    """
    anchored: List[str] = []
    for book_key, legacy_key, universal_key in _BOOK_FIELDS:
        if _extract_price(prop, legacy_key, universal_key) is not None:
            anchored.append(book_key)

    book_count = len(anchored)
    if book_count == 0:
        coverage_class = "pp_only"
    elif book_count == 1:
        coverage_class = "single_book"
    else:
        coverage_class = "multi_book"

    prop["book_count"] = book_count
    prop["coverage_class"] = coverage_class
    prop["books_anchored"] = anchored
    return book_count, coverage_class


def filter_priceable(
    props: List[Dict],
    *,
    sport: Optional[str] = None,
    run_id: Optional[str] = None,
    log_level: int = logging.INFO,
) -> Tuple[List[Dict], Dict[str, float]]:
    """Apply the 0-Book Exclusion Rule to a batch of props.

    Mutates every prop in-place with `book_count`/`coverage_class`.
    Returns only the subset where ``book_count >= 1``.

    Logs a single ``[Coverage Filter]`` line at the configured level, with
    the prefix labelled by sport + run_id when provided so pipeline logs
    stay greppable across multi-sport runs.
    """
    total_seen = 0
    total_excluded = 0
    stat_multi = 0
    stat_single = 0

    kept: List[Dict] = []
    for prop in props:
        total_seen += 1
        book_count, coverage_class = classify_coverage(prop)
        if book_count == 0:
            total_excluded += 1
            continue
        if coverage_class == "multi_book":
            stat_multi += 1
        else:
            stat_single += 1
        kept.append(prop)

    total_remaining = len(kept)
    coverage_rate = (total_remaining / total_seen) if total_seen else 0.0

    tag_parts = ["COVERAGE_FILTER"]
    if run_id:
        tag_parts.append(str(run_id))
    if sport:
        tag_parts.append(sport.upper())
    tag = "[" + "] [".join(tag_parts) + "]"

    logger.log(
        log_level,
        f"{tag} total={total_seen} excluded_pp_only={total_excluded} "
        f"remaining={total_remaining} coverage_rate={coverage_rate:.3f} "
        f"(multi_book={stat_multi} single_book={stat_single})"
    )

    return kept, {
        "total_props_seen": total_seen,
        "total_props_excluded_pp_only": total_excluded,
        "total_props_remaining": total_remaining,
        "coverage_rate": coverage_rate,
        "multi_book": stat_multi,
        "single_book": stat_single,
        "pp_only": total_excluded,
    }


# ============================================================================
# PP Playability Filter (2026-05) — side-aware PrizePicks contract
# ============================================================================
#
# Universal rule, identical across NBA / MLB / NFL:
#
#     A prop is eligible for scoring, tiering, and the cached board ONLY
#     if PrizePicks itself listed THAT EXACT player + stat + line + side.
#
# This is enforced as a hard filter at every adapter's `load_live_props`
# entry point (the single chokepoint every scoring run funnels through).
#
# Source of truth:
#     prop["playable_on_pp"] is True  ↔  prop["pp_layer"] is not None
#
# `pp_layer` is set by `universal_odds_sync._normalize_market_data` only
# when PrizePicks quoted the canonical_key
# (sport|event|player|stat|line|side). Sportsbook fallbacks
# (`source_anchor == "sportsbook_fallback"`) carry `playable_on_pp=False`
# and are dropped here.
#
# Why filter at the scoring boundary (vs inside normalization):
#   - The full canonical pool is still useful for research / alt-line
#     tracking / future non-PP-playable products. We keep the pool intact
#     in `{sport}_live_props` and gate it once at the scoring entrypoint.
#   - The TP-engine companion map is intentionally built over the FULL
#     pool BEFORE this filter so OVER-side de-vig TP can still pair its
#     same-book UNDER companion when the UNDER is sportsbook-only.
# ============================================================================


def filter_pp_playable(
    props: List[Dict],
    *,
    sport: Optional[str] = None,
    run_id: Optional[str] = None,
    log_level: int = logging.INFO,
) -> Tuple[List[Dict], Dict[str, int]]:
    """Drop every prop where `playable_on_pp != True`.

    Universal — applied identically for NBA, MLB, and any future sport.
    Pairs with `filter_priceable` (0-book exclusion) at every adapter's
    `load_live_props` step.

    Returns the kept subset and a stats dict for sync-history logging.
    """
    total_seen = 0
    dropped_no_pp = 0
    dropped_by_side: Dict[str, int] = {}
    dropped_by_stat_side_line: Dict[str, int] = {}

    kept: List[Dict] = []
    for prop in props:
        total_seen += 1
        if prop.get("playable_on_pp") is True:
            kept.append(prop)
            continue
        dropped_no_pp += 1
        side = (prop.get("recommendation") or "?").upper()
        dropped_by_side[side] = dropped_by_side.get(side, 0) + 1
        key = (
            f"{prop.get('stat_type','?')}|{side}|{prop.get('line','?')}"
        )
        dropped_by_stat_side_line[key] = (
            dropped_by_stat_side_line.get(key, 0) + 1
        )

    tag_parts = ["PP_PLAYABLE_FILTER"]
    if run_id:
        tag_parts.append(str(run_id))
    if sport:
        tag_parts.append(sport.upper())
    tag = "[" + "] [".join(tag_parts) + "]"

    logger.log(
        log_level,
        f"{tag} total={total_seen} dropped_no_pp_side={dropped_no_pp} "
        f"remaining={len(kept)} "
        f"dropped_by_side={dropped_by_side}"
    )

    return kept, {
        "total_props_seen": total_seen,
        "dropped_no_pp_side": dropped_no_pp,
        "remaining": len(kept),
        "dropped_by_side": dropped_by_side,
        "dropped_by_stat_side_line": dropped_by_stat_side_line,
    }


def audit_pp_side_legality(
    props: List[Dict], *, sport: Optional[str] = None,
) -> Dict[str, Any]:
    """Post-sync assertion: every `playable_on_pp=True` prop MUST have a
    non-null `pp_layer` (PrizePicks quoted that exact side). Returns a
    structured violation report — empty `violations` list = contract
    holds.

    Tests these specific PP-illegal patterns the user flagged:
        MLB stolen_bases UNDER 0.5
        MLB doubles UNDER 0.5
        MLB home_runs UNDER 0.5
        NBA extreme alternate UNDERs
    """
    violations: List[Dict] = []
    flag_counts = {
        "playable_on_pp_with_no_pp_layer": 0,
        "pp_available_with_no_pp_layer": 0,
    }
    for p in props:
        if p.get("playable_on_pp") is True and p.get("pp_layer") is None:
            flag_counts["playable_on_pp_with_no_pp_layer"] += 1
            if len(violations) < 50:
                violations.append({
                    "player": p.get("player_name"),
                    "stat": p.get("stat_type"),
                    "line": p.get("line"),
                    "side": p.get("recommendation"),
                    "source_anchor": p.get("source_anchor"),
                    "anchor_book": p.get("anchor_book"),
                })
        if p.get("pp_available") is True and p.get("pp_layer") is None:
            flag_counts["pp_available_with_no_pp_layer"] += 1

    return {
        "sport": sport,
        "total_checked": len(props),
        "flag_counts": flag_counts,
        "violations_sample": violations,
        "contract_holds": (
            flag_counts["playable_on_pp_with_no_pp_layer"] == 0
            and flag_counts["pp_available_with_no_pp_layer"] == 0
        ),
    }

