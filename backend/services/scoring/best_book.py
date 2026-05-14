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


def _book_devig_for_this_side(
    prop: Dict[str, Any],
    universal_field: str,
    legacy_field: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(raw_implied, devig_implied)`` for the candidate book's
    THIS-side price.

    Devig is per-book — uses the book's own opposite-side quote
    (`{book}_odds_opp`) to remove that book's vig. Returns
    ``devig_implied = None`` when the opposite side is missing
    (one-sided book → caller must use raw).

    Reuses the same opposite-side field map as `tp_engine` so adding
    a book in one place enables devigging everywhere.
    """
    this_odds = prop.get(universal_field)
    if this_odds is None:
        this_odds = prop.get(legacy_field)
    raw_imp = american_to_implied(this_odds)
    if raw_imp is None:
        return None, None
    opp_key = f"{universal_field}_opp"
    opp_odds = prop.get(opp_key)
    opp_imp = american_to_implied(opp_odds)
    if opp_imp is None or (raw_imp + opp_imp) <= 0:
        return raw_imp, None
    devig = raw_imp / (raw_imp + opp_imp)
    return raw_imp, devig


def compute_best_book_metrics(
    prop: Dict[str, Any],
    *,
    fair_prob: Optional[float] = None,
    p_model: Optional[float] = None,
    fair_prob_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the universal best-book + edge fields with consistent
    devigged probability bases (2026-05-14 rewrite).

    Pipeline
    --------
    1. For every book that quotes THIS side, compute raw implied prob
       (vigged). If the book also quotes the OPPOSITE side, devig per
       book → store `devig_implied`.
    2. Select the best book by **lowest raw implied probability** —
       that's the highest payout for the bettor.
    3. Compute edges on consistent probability bases:

         consensus_edge  = p_model     − consensus_devig_fair   (devig)
         best_bet_edge   = p_model     − best_book_devig        (devig)
         shopping_edge   = consensus_devig_fair − best_book_devig (devig vs devig)

       When the winning book is **one-sided** (no opposite-side quote
       available) the devig isn't possible at that book → fall back
       to raw implied AND set the corresponding `*_source` field to
       ``raw_one_sided`` / ``devig_vs_raw`` so callers can flag the
       comparison as basis-mismatched.

    4. Displayed sportsbook odds are NEVER altered — devigged values
       are internal comparison metrics only.

    Parameters
    ----------
    prop : dict
        Carries `{book}_odds` + `{book}_odds_opp` per book.
    fair_prob : float | None
        Consensus devigged fair probability for THIS side (TP/100).
    p_model : float | None
        Model probability for THIS side.
    fair_prob_source : str | None
        Echoed onto `consensus_edge_source`. Pass ``"devig"`` /
        ``"one_sided"`` / ``"raw_one_sided"`` from the caller (the
        scoring stack carries this as `tp_source`).
    """
    best_book: Optional[str] = None
    best_book_odds: Optional[float] = None
    best_book_raw_implied: Optional[float] = None
    best_book_devig_implied: Optional[float] = None

    implied_probs: List[float] = []
    books_available: List[str] = []

    for display_key, universal_field, legacy_field in _BOOK_PROBES:
        odds = prop.get(universal_field)
        if odds is None:
            odds = prop.get(legacy_field)
        if odds is None:
            continue
        raw_imp, devig_imp = _book_devig_for_this_side(prop, universal_field, legacy_field)
        if raw_imp is None:
            continue
        books_available.append(display_key)
        implied_probs.append(raw_imp)
        # Best = lowest raw implied (= highest bettor payout). Devig
        # is for edge math; selection is by the actual displayed price.
        if is_better_american_odds(odds, best_book_odds):
            best_book = display_key
            best_book_odds = float(odds)
            best_book_raw_implied = raw_imp
            best_book_devig_implied = devig_imp

    if implied_probs:
        market_spread = max(implied_probs) - min(implied_probs)
    else:
        market_spread = None

    # ── Source tags ────────────────────────────────────────────────────
    # The winner is pair-complete iff we successfully computed its
    # devig (both sides present at that one book).
    pair_complete = best_book_devig_implied is not None
    best_bet_edge_source = "devig" if pair_complete else (
        "raw_one_sided" if best_book_raw_implied is not None else None
    )
    shopping_edge_source = (
        "devig_vs_devig" if pair_complete and fair_prob is not None
        else "devig_vs_raw" if best_book_raw_implied is not None and fair_prob is not None
        else None
    )

    # ── Edge math on consistent probability bases ──────────────────────
    # Use devig when available; raw when one-sided. Caller can use the
    # `*_source` tags to flag mixed-basis comparisons.
    best_book_implied_for_edge = (
        best_book_devig_implied if pair_complete else best_book_raw_implied
    )

    best_book_edge: Optional[float] = None
    if fair_prob is not None and best_book_implied_for_edge is not None:
        best_book_edge = round(float(fair_prob) - best_book_implied_for_edge, 4)

    total_edge: Optional[float] = None
    if p_model is not None and best_book_implied_for_edge is not None:
        total_edge = round(float(p_model) - best_book_implied_for_edge, 4)

    best_book_raw_rounded = (
        round(best_book_raw_implied, 4) if best_book_raw_implied is not None else None
    )
    best_book_devig_rounded = (
        round(best_book_devig_implied, 4) if best_book_devig_implied is not None else None
    )
    market_spread_rounded = (
        round(market_spread, 4) if market_spread is not None else None
    )
    spread_label = _spread_label(market_spread)

    return {
        # Display-facing book + price (raw American odds unchanged).
        "best_book": best_book,
        "best_book_odds": best_book_odds,
        # 2026-05-14 — both probability bases surfaced explicitly so
        # callers can audit. Legacy field name `best_book_implied_probability`
        # now mirrors `best_book_raw_implied_probability` for back-compat.
        "best_book_raw_implied_probability":   best_book_raw_rounded,
        "best_book_devig_probability":         best_book_devig_rounded,
        "best_book_implied_probability":       best_book_raw_rounded,
        # ── Edges (now on consistent devig basis when pair-complete) ────
        "best_book_edge": best_book_edge,         # shopping edge (fair − best)
        "total_edge":     total_edge,             # best-bet edge (model − best)
        # ── Source tags so UI can flag basis-mismatch ──────────────────
        "consensus_edge_source": (fair_prob_source or "devig") if fair_prob is not None else None,
        "best_bet_edge_source":  best_bet_edge_source,
        "shopping_edge_source":  shopping_edge_source,
        # ── Coverage metadata ──────────────────────────────────────────
        "market_spread":         market_spread_rounded,
        "market_spread_label":   spread_label,
        "books_available_count": len(books_available),
    }
