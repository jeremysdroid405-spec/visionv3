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


def better_american_odds(candidate: Optional[float], current_best: Optional[float]) -> Optional[float]:
    """Return whichever odds value is better FOR THE BETTOR (lower
    implied probability == higher payout). Side-agnostic — works for
    both OVER and UNDER because `{book}_odds` carries this-side prices.

    Examples (the bettor wants the smaller implied probability):
      better_american_odds(-300, -400) → -300   (-300 ⇒ 0.75 implied,
                                                 -400 ⇒ 0.80 implied)
      better_american_odds(+120, -110) → +120   (+120 ⇒ 0.45,
                                                  -110 ⇒ 0.524)
      better_american_odds(None, -110) → -110
      better_american_odds(-110, None) → -110
    """
    if candidate is None:
        return current_best
    if current_best is None:
        return candidate
    c_imp = american_to_implied(candidate)
    b_imp = american_to_implied(current_best)
    if c_imp is None:
        return current_best
    if b_imp is None:
        return candidate
    # Lower implied = better for bettor (more payout).
    return candidate if c_imp < b_imp else current_best


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
        # Pick best: lower implied = better for bettor.
        if best_book_odds is None or imp < (best_book_implied or 1.0):
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

    return {
        "best_book": best_book,
        "best_book_odds": best_book_odds,
        "best_book_implied_probability": (
            round(best_book_implied, 4) if best_book_implied is not None else None
        ),
        "best_book_edge": best_book_edge,
        "total_edge": total_edge,
        "market_spread": (
            round(market_spread, 4) if market_spread is not None else None
        ),
        "market_spread_label": _spread_label(market_spread),
        "books_available_count": len(books_available),
    }
