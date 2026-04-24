"""
TP Engine — Multi-Book De-Vigged True Probability (2026-04-22)
===============================================================

Replaces the legacy single/dual-book raw-implied-prob TP calculation
with a mathematically correct, multi-book, per-book de-vigged TP.

Pipeline per prop
-----------------
For every sportsbook we have BOTH over and under quotes for:

    1. American → implied prob (both sides)
    2. De-vig per book:       p_true = p_raw / (p_over_raw + p_under_raw)
    3. Pick the side we're modelling
    4. Average the per-book ``p_true`` across all valid books

If zero books are pair-complete, ``tp`` is ``None``. There is
**no 50% fallback** — the caller is responsible for handling the
"missing TP" case explicitly.

Output contract
---------------
    {
        "tp": float | None,          # 0..100 scale
        "tp_books_used": int,        # count of books with both sides
        "tp_books_list": List[str],  # ["DK", "FD", "MGM", "BOL"]
        "tp_method": "multi_book_devig_v1",
    }
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TP_METHOD = "multi_book_devig_v1"

# tp_source enumeration per 2026-04-24 one-sided alt-market recovery:
#   "devig"       — both sides present on at least one book, standard
#                   per-book de-vig average (preserves rigor)
#   "one_sided"   — no book had both sides, but at least one book quoted
#                   the picked side. Raw implied probability averaged
#                   across the quoting books; carries the full vig so
#                   edge estimates are CONSERVATIVE (market_probability
#                   is over-estimated by ~2–4pp) — NOT fabricated.
TP_SOURCE_DEVIG = "devig"
TP_SOURCE_ONE_SIDED = "one_sided"

# Canonical (legacy_price_field, universal_odds_field, short_code) per book.
# Either field, if not None, yields the book's American odds.
_BOOKS = (
    ("draftkings_price", "dk_odds",  "DK"),
    ("fanduel_price",    "fd_odds",  "FD"),
    ("betmgm_price",     "mgm_odds", "MGM"),
    ("betonline_price",  "bol_odds", "BOL"),
)


def _amer_to_prob(odds: Any) -> Optional[float]:
    """American odds → implied probability (0..1). Returns None for
    missing / zero / non-numeric inputs."""
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


def _get_price(prop: Optional[Dict], legacy_key: str, universal_key: str) -> Optional[float]:
    if not prop:
        return None
    for key in (legacy_key, universal_key):
        v = prop.get(key)
        if v is not None:
            return v
    # Nested `sharp_market` fallback for NBA demon_goblin path.
    sm = prop.get("sharp_market")
    if isinstance(sm, dict):
        return sm.get(legacy_key)
    return None


def compute_tp(
    *,
    over_prop: Optional[Dict] = None,
    under_prop: Optional[Dict] = None,
    side: str = "OVER",
    prop: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Compute multi-book de-vigged TP for the picked side.

    Two calling conventions are supported:

      1. **Single-prop** (preferred, used by the scoring adapters after
         the 2026-04-22 universal_odds_sync extract upgrade):
         pass ``prop=<the picked prop>`` and ``side``. The function
         reads ``{book}_odds`` (this side's price) and
         ``{book}_odds_opp`` (opposite side's price) directly.

      2. **Two-prop** (legacy / test harness): pass ``over_prop`` and
         ``under_prop`` separately, each containing book prices for
         that one side.

    Parameters
    ----------
    prop : Dict | None
        The picked prop doc. Expected to carry ``{book}_odds`` AND
        ``{book}_odds_opp`` for each book that quoted the line.
    over_prop, under_prop : Dict | None
        Legacy per-side dicts (used by the unit tests + any caller
        that manages its own pairing).
    side : str
        "OVER" or "UNDER" — the side we are modelling / picking.
    """
    side_norm = (side or "OVER").upper().strip()
    if side_norm not in ("OVER", "UNDER"):
        side_norm = "OVER"

    books_used: List[str] = []
    p_true_values: List[float] = []

    # ---- Path 1: single-prop with `{book}_odds` + `{book}_odds_opp` --
    if prop is not None:
        opp_fields = {
            "DK":  ("dk_odds",  "dk_odds_opp",  "draftkings_price"),
            "FD":  ("fd_odds",  "fd_odds_opp",  "fanduel_price"),
            "MGM": ("mgm_odds", "mgm_odds_opp", "betmgm_price"),
            "BOL": ("bol_odds", "bol_odds_opp", "betonline_price"),
        }
        for code, (self_key, opp_key, legacy_key) in opp_fields.items():
            this_odds = prop.get(self_key)
            if this_odds is None:
                this_odds = prop.get(legacy_key)
            opp_odds = prop.get(opp_key)
            p_this_raw = _amer_to_prob(this_odds)
            p_opp_raw = _amer_to_prob(opp_odds)
            if p_this_raw is None or p_opp_raw is None:
                continue
            total = p_this_raw + p_opp_raw
            if total <= 0:
                continue
            p_true_values.append(p_this_raw / total)
            books_used.append(code)
    else:
        # ---- Path 2: legacy companion-based pairing --------------------
        for legacy_key, universal_key, code in _BOOKS:
            over_odds = _get_price(over_prop, legacy_key, universal_key)
            under_odds = _get_price(under_prop, legacy_key, universal_key)
            p_over_raw = _amer_to_prob(over_odds)
            p_under_raw = _amer_to_prob(under_odds)
            if p_over_raw is None or p_under_raw is None:
                continue
            total = p_over_raw + p_under_raw
            if total <= 0:
                continue
            p_over_true = p_over_raw / total
            p_under_true = p_under_raw / total
            p_true = p_over_true if side_norm == "OVER" else p_under_true
            p_true_values.append(p_true)
            books_used.append(code)

    if not p_true_values:
        # ---- One-sided fallback (2026-04-24) --------------------------
        # When NO book quotes BOTH sides but at least one book quotes
        # the picked side, use the raw (non-devigged) implied probability
        # averaged across those books. Flag via `tp_source=one_sided`
        # so the caller can distinguish from a rigorous de-vig TP.
        # Carries the full vig → market_probability is systematically
        # over-estimated by ~2–4pp, so edge = p_true - market is
        # CONSERVATIVE (pessimistic for the picked side). We do NOT
        # fabricate the missing side.
        raw_values: List[float] = []
        raw_books: List[str] = []

        if prop is not None:
            for code, (self_key, _opp_key, legacy_key) in {
                "DK":  ("dk_odds",  "dk_odds_opp",  "draftkings_price"),
                "FD":  ("fd_odds",  "fd_odds_opp",  "fanduel_price"),
                "MGM": ("mgm_odds", "mgm_odds_opp", "betmgm_price"),
                "BOL": ("bol_odds", "bol_odds_opp", "betonline_price"),
            }.items():
                this_odds = prop.get(self_key)
                if this_odds is None:
                    this_odds = prop.get(legacy_key)
                p_raw = _amer_to_prob(this_odds)
                if p_raw is None:
                    continue
                raw_values.append(p_raw)
                raw_books.append(code)
        else:
            target_prop = over_prop if side_norm == "OVER" else under_prop
            for legacy_key, universal_key, code in _BOOKS:
                odds = _get_price(target_prop, legacy_key, universal_key)
                p_raw = _amer_to_prob(odds)
                if p_raw is None:
                    continue
                raw_values.append(p_raw)
                raw_books.append(code)

        if raw_values:
            tp_raw = sum(raw_values) / len(raw_values)
            return {
                "tp": round(tp_raw * 100.0, 1),
                "tp_books_used": len(raw_values),
                "tp_books_list": raw_books,
                "tp_method": TP_METHOD,
                "tp_source": TP_SOURCE_ONE_SIDED,
                "market_probability": round(tp_raw, 4),
            }

        return {
            "tp": None,
            "tp_books_used": 0,
            "tp_books_list": [],
            "tp_method": TP_METHOD,
            "tp_source": None,
            "market_probability": None,
        }

    tp_float = sum(p_true_values) / len(p_true_values)
    return {
        "tp": round(tp_float * 100.0, 1),
        "tp_books_used": len(p_true_values),
        "tp_books_list": books_used,
        "tp_method": TP_METHOD,
        "tp_source": TP_SOURCE_DEVIG,
        "market_probability": round(tp_float, 4),
    }


# ---------------------------------------------------------------------------
# Companion-side lookup
# ---------------------------------------------------------------------------
def build_companion_map(props: List[Dict]) -> Dict[tuple, Dict[str, Dict]]:
    """Build a ``(player, stat, line) -> {"OVER": doc, "UNDER": doc}`` map
    so scoring adapters can find the opposite-side doc in O(1) when
    computing TP.

    ``stat`` is resolved from ``stat_type_extracted`` / ``stat_type``
    and ``side`` from ``recommendation`` / ``direction``.
    """
    companion: Dict[tuple, Dict[str, Dict]] = {}
    for p in props:
        player = p.get("player_name")
        stat = p.get("stat_type_extracted") or p.get("stat_type")
        line = p.get("line")
        if player is None or stat is None or line is None:
            continue
        rec = (p.get("recommendation") or "").upper().strip()
        if not rec:
            d = (p.get("direction") or "").lower()
            rec = "OVER" if "over" in d else "UNDER"
        key = (player, stat, float(line))
        companion.setdefault(key, {})[rec] = p
    return companion


def lookup_companion_sides(
    prop: Dict,
    companion_map: Dict[tuple, Dict[str, Dict]],
) -> tuple:
    """Return ``(over_prop, under_prop)`` for the given prop's line
    using the pre-built companion map. Either slot may be None if
    that side isn't in live_props.
    """
    player = prop.get("player_name")
    stat = prop.get("stat_type_extracted") or prop.get("stat_type")
    line = prop.get("line")
    if player is None or stat is None or line is None:
        return None, None
    key = (player, stat, float(line))
    sides = companion_map.get(key) or {}
    return sides.get("OVER"), sides.get("UNDER")


__all__ = [
    "TP_METHOD",
    "TP_SOURCE_DEVIG",
    "TP_SOURCE_ONE_SIDED",
    "compute_tp",
    "build_companion_map",
    "lookup_companion_sides",
]
