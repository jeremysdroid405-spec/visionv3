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
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tuple of (legacy_name, universal_name) per book. Either key counts as
# an anchor as long as its value is not None.
_BOOK_FIELDS: Tuple[Tuple[str, str, str], ...] = (
    ("draftkings", "draftkings_price", "dk_odds"),
    ("fanduel",    "fanduel_price",    "fd_odds"),
    ("betonlineag","betonline_price",  "bol_odds"),
    ("betmgm",     "betmgm_price",     "mgm_odds"),
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
        ``book_count`` (int 0..4)
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
