"""Universal best-book / market-shopping edge engine (2026-05-13).

Computes per-prop:
    best_book                       — canonical book key (e.g. "draftkings")
    best_book_odds                  — American odds at best book for THIS side
    best_book_implied_probability   — implied prob from best_book_odds (0..1)
    best_book_edge                  — fair_prob - best_book_implied (0..1)
                                       [SHOPPING edge: market consensus fair
                                        vs cheapest book's implied prob]
    total_edge                      — p_model - best_book_implied (0..1)
                                       [TOTAL ROI edge: model probability
                                        vs cheapest book's implied prob;
                                        only populated when p_model is
                                        passed by caller]
    market_spread                   — max(implied) - min(implied) (0..1)
    market_spread_label             — "wide" | "moderate" | "tight"
    books_available_count           — count of books quoting THIS side

Design notes
------------
• Data-driven: enumerates every (book_short, odds_field) pair in
  `tp_engine._BOOKS`. Adding a new book to the system in one place
  (`tp_engine._BOOKS`) auto-extends best-book shopping.

• Sport- and tier-agnostic: takes only the prop dict + fair_prob.
  No NBA/MLB switches, no SH/FL/WZ logic.

• Side-aware: works for OVER + UNDER because `{book}_odds` is always
  "this side's price" by canonical-key contract (see universal_odds_sync
  Pass 2). Caller passes `fair_prob_this_side` so the engine doesn't
  need to know which side it's scoring.

• Additive — does NOT replace `tp_engine` / de-vig / `tp_source`
  semantics. This is an *exploitability* layer on top of truth-prob.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Canonical book list — re-uses `tp_engine._BOOKS` so adding a book
# in one place enables shopping everywhere. (short_code, odds_field).
from services.scoring.tp_engine import _BOOKS as _TP_BOOKS

# Resolve once at import time: (book_key_for_display, odds_field, legacy_field)
# `legacy_field` is the dotted-API name (e.g. `draftkings_price`); we
# probe it as a fallback for adapters that emit only the legacy field.
# `book_key_for_display` is the canonical sportsbook key surfaced to
# the UI (e.g. "draftkings"). Derived from the legacy field stem.
_BOOK_PROBES: List[Tuple[str, str, str]] = []
for _legacy, _universal, _short in _TP_BOOKS:
    # Strip "_price" → canonical book key (e.g. "draftkings_price" → "draftkings").
    _display = _legacy.replace("_price", "")
    _BOOK_PROBES.append((_display, _universal, _legacy))


def american_to_implied(odds: Any) -> Optional[float]:
    """American odds → implied probability (0..1). Returns None for
    missing / zero / non-numeric input."""
    if odds is None:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def is_better_american_odds(
    candidate: Optional[float], current_best: Optional[float]
) -> bool:
    """Boolean form of the better-odds predicate.

    Returns True iff `candidate` is strictly better for the bettor than
    `current_best` (lower implied probability, i.e. higher payout).

    Universal across NBA / MLB / NFL / future sports — odds semantics
    are sport-agnostic. Side-agnostic too: works for OVER + UNDER
    because the caller passes "this side's price" by convention.

    Examples
    --------
    >>> is_better_american_odds(+140, +110)      # True  (+140 = 41.7%, +110 = 47.6%)
    True
    >>> is_better_american_odds(-105, -130)      # True  (-105 = 51.2%, -130 = 56.5%)
    True
    >>> is_better_american_odds(-300, -400)      # True  (-300 = 75.0%, -400 = 80.0%)
    True
    >>> is_better_american_odds(+110, +140)      # False
    False
    >>> is_better_american_odds(None, -110)      # False (no candidate)
    False
    >>> is_better_american_odds(-110, None)      # True  (anything beats None)
    True
    >>> is_better_american_odds(0, -110)         # False (0 is invalid)
    False
    """
    c_imp = american_to_implied(candidate)
    if c_imp is None:
        return False
    b_imp = american_to_implied(current_best)
    if b_imp is None:
        return True
    return c_imp < b_imp


def better_american_odds(
    candidate: Optional[float], current_best: Optional[float]
) -> Optional[float]:
    """Return whichever odds value is better FOR THE BETTOR (lower
    implied probability == higher payout). Side-agnostic — works for
    both OVER and UNDER because `{book}_odds` carries this-side prices.

    Thin wrapper over `is_better_american_odds` that returns the winning
    odds value (or fallback) instead of a bool.
    """
    if candidate is None:
        return current_best
    if current_best is None:
        return candidate
    return candidate if is_better_american_odds(candidate, current_best) else current_best


def _spread_label(spread: Optional[float]) -> str:
    """Bucket the implied-prob spread into wide / moderate / tight.
    Buckets per product spec — never returns None so the UI badge
    is always renderable."""
    if spread is None:
        return "unknown"
    if spread >= 0.08:
        return "wide"
    if spread >= 0.04:
        return "moderate"
    return "tight"


def compute_best_book_metrics(
    prop: Dict[str, Any],
    *,
    fair_prob: Optional[float] = None,
    p_model: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute the universal best-book fields.

    Parameters
    ----------
    prop : dict
        Must carry `{book}_odds` flat fields for this side. (Either
        the legacy form like `draftkings_price` or the universal
        `dk_odds` is accepted — both probed.)
    fair_prob : float | None
        Devigged market-consensus fair probability (0..1) for THIS
        side (TP/100). Used to compute `best_book_edge` (the SHOPPING
        edge — market consensus fair vs best-book implied).
    p_model : float | None
        Model probability (0..1) for THIS side. When provided,
        `total_edge` = p_model - best_book_implied is also returned —
        this is the *true ROI edge* (model vs cheapest book).
        Left None when caller doesn't have a model probability.

    Returns
    -------
    dict spread directly onto a score doc. Field names follow the
    API-payload contract.
    """
    best_book: Optional[str] = None
    best_book_odds: Optional[float] = None
    best_book_implied: Optional[float] = None

    implied_probs: List[float] = []
    books_available: List[str] = []

    for display_key, universal_field, legacy_field in _BOOK_PROBES:
        odds = prop.get(universal_field)
        if odds is None:
            odds = prop.get(legacy_field)
        if odds is None:
            continue
        imp = american_to_implied(odds)
        if imp is None:
            continue
        books_available.append(display_key)
        implied_probs.append(imp)
        # Universal predicate: lower implied = better for bettor.
        # Works for OVER + UNDER + plus-money + minus-money + any sport.
        if is_better_american_odds(odds, best_book_odds):
            best_book = display_key
            best_book_odds = float(odds)
            best_book_implied = imp

    if implied_probs:
        market_spread = max(implied_probs) - min(implied_probs)
    else:
        market_spread = None

    best_book_edge: Optional[float] = None
    if fair_prob is not None and best_book_implied is not None:
        best_book_edge = round(float(fair_prob) - best_book_implied, 4)

    # Total ROI edge (2026-05-13): combines model alpha with shopping
    # alpha into a single actionable number. Independent of TP/devig —
    # measures model probability vs the cheapest market price.
    total_edge: Optional[float] = None
    if p_model is not None and best_book_implied is not None:
        total_edge = round(float(p_model) - best_book_implied, 4)

    best_book_implied_rounded = (
        round(best_book_implied, 4) if best_book_implied is not None else None
    )
    market_spread_rounded = (
        round(market_spread, 4) if market_spread is not None else None
    )
    spread_label = _spread_label(market_spread)

    return {
        "best_book": best_book,
        "best_book_odds": best_book_odds,
        "best_book_implied_probability": best_book_implied_rounded,
        "best_book_edge": best_book_edge,
        "total_edge": total_edge,
        "market_spread": market_spread_rounded,
        "market_spread_label": spread_label,
        "books_available_count": len(books_available),
    }
