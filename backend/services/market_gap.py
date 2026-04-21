"""
Market Gap (a.k.a. Book Spread) – sport-agnostic sportsbook disagreement signal.

Computes how much sportsbooks disagree on a single prop's price.
Designed as a pure, pipeline-free helper so any sport (NBA, MLB, NFL, …) can
opt-in by simply passing the pick through ``annotate_market_gap``.

Rules (kept deliberately strict so the UI only surfaces MEANINGFUL signal):
  * gap < MEDIUM_THRESHOLD      -> level = "none"  (UI renders nothing)
  * MEDIUM_THRESHOLD <= gap < HIGH_THRESHOLD -> level = "medium"
  * gap >= HIGH_THRESHOLD       -> level = "high"

Gap is measured as the American-odds point-spread between the highest-value
price (best for the bettor) and the lowest-value price across available books
for the pick's committed side.

No sport-specific branching is allowed in this module.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ----------------------------------------------------------------------------
# Thresholds (configurable via env; safe defaults per product spec)
# ----------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default

MEDIUM_THRESHOLD: int = _env_int("MARKET_GAP_MEDIUM", 50)
HIGH_THRESHOLD: int = _env_int("MARKET_GAP_HIGH", 100)

# Books checked, in preference order for best-book tie-breaking.
# Extend by env: MARKET_GAP_BOOKS="draftkings,fanduel,betonline,pinnacle,betmgm"
_DEFAULT_BOOKS = ("draftkings", "fanduel", "betonline")

def _configured_books() -> Tuple[str, ...]:
    raw = os.environ.get("MARKET_GAP_BOOKS")
    if not raw:
        return _DEFAULT_BOOKS
    books = tuple(b.strip().lower() for b in raw.split(",") if b.strip())
    return books or _DEFAULT_BOOKS


# Short labels used by the UI / logs.  Keep neutral — no hype.
BOOK_LABELS: Dict[str, str] = {
    "draftkings": "DK",
    "fanduel": "FD",
    "betonline": "BOL",
    "pinnacle": "PIN",
    "betmgm": "MGM",
    "caesars": "CZR",
}


# ----------------------------------------------------------------------------
# Core computation
# ----------------------------------------------------------------------------
def _extract_prices(pick: Dict[str, Any], books: Iterable[str]) -> Dict[str, int]:
    """Pull numeric American-odds prices from the pick across known books.

    Looks at top-level ``{book}_price`` fields and (as a fallback) the nested
    ``sharp_market.{book}_price`` field. Returns an ordered dict-like dict of
    ``{book: price}`` containing only numeric, non-null values.
    """
    sharp = pick.get("sharp_market") or {}
    out: Dict[str, int] = {}
    for book in books:
        key = f"{book}_price"
        val = pick.get(key)
        if val is None:
            val = sharp.get(key)
        if val is None:
            continue
        try:
            ival = int(val)
        except (TypeError, ValueError):
            continue
        out[book] = ival
    return out


def compute_market_gap(
    pick: Dict[str, Any],
    *,
    books: Optional[Iterable[str]] = None,
    medium_threshold: int = MEDIUM_THRESHOLD,
    high_threshold: int = HIGH_THRESHOLD,
) -> Dict[str, Any]:
    """Compute the market-gap / book-spread signal for a single pick.

    Returns a dict with the canonical, sport-agnostic fields consumed by the
    frontend.  Always returns a dict — callers should merge the result into
    the pick even when the level is ``"none"`` so the contract stays stable.
    """
    books_list = tuple(books) if books is not None else _configured_books()
    prices = _extract_prices(pick, books_list)

    # No disagreement possible with fewer than 2 books.
    if len(prices) < 2:
        if prices:
            only_book, only_price = next(iter(prices.items()))
            best_book_label = BOOK_LABELS.get(only_book, only_book.upper())
            price_map = {best_book_label: only_price}
        else:
            only_price = None
            best_book_label = None
            price_map = None
        return {
            "market_gap_points": 0,
            "market_books_compared": len(prices),
            "market_best_book": best_book_label,
            "market_best_price": only_price,
            "market_price_map": price_map,
            "market_gap_level": "none",
        }

    # American odds: higher numeric value = better price for the bettor.
    # (+150 > -100 > -200). Gap is absolute point distance between extremes.
    best_book, best_price = max(prices.items(), key=lambda kv: kv[1])
    _, worst_price = min(prices.items(), key=lambda kv: kv[1])
    gap = int(best_price - worst_price)

    if gap >= high_threshold:
        level = "high"
    elif gap >= medium_threshold:
        level = "medium"
    else:
        level = "none"

    return {
        "market_gap_points": gap,
        "market_books_compared": len(prices),
        "market_best_book": BOOK_LABELS.get(best_book, best_book.upper()),
        "market_best_price": int(best_price),
        "market_price_map": {BOOK_LABELS.get(b, b.upper()): p for b, p in prices.items()},
        "market_gap_level": level,
    }


def annotate_market_gap(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotate a list of picks in place (and return it) with market-gap fields.

    Pure, side-effect-limited helper: never raises on malformed picks — it
    simply skips fields it can't resolve and leaves ``market_gap_level='none'``.
    """
    if not picks:
        return picks
    for p in picks:
        if not isinstance(p, dict):
            continue
        try:
            p.update(compute_market_gap(p))
        except Exception:
            # Graceful degradation: never break the response on a bad pick.
            p.setdefault("market_gap_level", "none")
            p.setdefault("market_gap_points", 0)
            p.setdefault("market_books_compared", 0)
    return picks


__all__ = [
    "MEDIUM_THRESHOLD",
    "HIGH_THRESHOLD",
    "BOOK_LABELS",
    "compute_market_gap",
    "annotate_market_gap",
]
