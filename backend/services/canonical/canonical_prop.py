"""Universal Canonical Prop Engine — sport-agnostic.

Architecture (per 2026-05-17 directive):

    raw_book_rows  →  canonical_prop_engine  →  tp/edge/routing/gates/cards

ONE canonical prop per `(event_id, player_id, stat_family, canonical_line)`.
Collapses fragmented book rows. Aggregates per-book OVER / UNDER
inventory. Computes consensus + cross-book devig probabilities.

This module does NOT:
  - run gates
  - run routing
  - compute model probabilities (`projection_mu`, `model_probability`)
  - write to the DB
  - modify any existing code path

It produces a list of `CanonicalProp` objects. Callers decide what to
do next.

Key design rule (per directive):
  Cross-book devig is SUPPORTED. A prop with DK-OVER and FD-UNDER
  at the same canonical_line is a valid devig input. The legacy
  "same-book pair required" assumption is dropped here.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.canonical.market_normalizer import normalize_market


# ── Canonical model ────────────────────────────────────────────────
@dataclass(frozen=False)
class CanonicalProp:
    """ONE playable market. Not one sportsbook row.

    Identity is `(sport, event_id, player_id, stat_family, canonical_line)`.
    Two raw rows from different books at the same identity are the
    same `CanonicalProp` — they populate `over_prices` / `under_prices`.
    """
    # Identity
    sport: str
    event_id: str
    player_id: Optional[str]
    player_name: str
    player_name_normalized: str
    stat_family: str
    canonical_line: float
    # Reference market identity (preserves the canonical Odds-API key
    # AFTER stripping `_alternate`, e.g. "batter_hits" for both std
    # and alt rows).
    canonical_market_key: str
    # Per-book inventory
    over_prices: Dict[str, int] = field(default_factory=dict)   # book → american odds
    under_prices: Dict[str, int] = field(default_factory=dict)
    available_books: List[str] = field(default_factory=list)
    # Raw source row count and original market keys observed
    source_rows_count: int = 0
    source_market_keys: List[str] = field(default_factory=list)
    # Computed (filled by `finalize`)
    best_over_price: Optional[int] = None
    best_under_price: Optional[int] = None
    best_over_book: Optional[str] = None
    best_under_book: Optional[str] = None
    consensus_over_price: Optional[float] = None
    consensus_under_price: Optional[float] = None
    devig_over_probability: Optional[float] = None
    devig_under_probability: Optional[float] = None
    # Coverage metrics
    book_count_over: int = 0
    book_count_under: int = 0
    book_count_both_sides_same_book: int = 0   # the legacy "paired" count
    book_count_either_side_any_book: int = 0   # union of OVER ∪ UNDER books
    has_cross_book_devig: bool = False
    has_same_book_devig: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── American odds ↔ implied probability helpers ────────────────────
def _american_to_prob(odds: int) -> float:
    o = int(odds)
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def _prob_to_american(p: float) -> int:
    """Inverse of `_american_to_prob`. Returns nearest int."""
    p = max(min(p, 0.9999), 0.0001)
    if p >= 0.5:
        return int(round(-(p * 100.0) / (1.0 - p)))
    return int(round(((1.0 - p) * 100.0) / p))


def _best_chalk(prices: Dict[str, int]) -> Tuple[Optional[int], Optional[str]]:
    """Best (most favourable to bettor) odds in an American-odds map.

    For OVER/UNDER alike, "best" means the LEAST negative or MOST
    positive American value (i.e. the highest implied-prob price for
    the bettor). Returns (best_odds, best_book) or (None, None).
    """
    if not prices: return None, None
    items = list(prices.items())
    # Higher American value = better payout for bettor.
    items.sort(key=lambda kv: kv[1], reverse=True)
    return items[0][1], items[0][0]


def _mean_implied(prices: Dict[str, int]) -> Optional[float]:
    if not prices: return None
    probs = [_american_to_prob(o) for o in prices.values()]
    return sum(probs) / len(probs)


def _devig_two_side(prob_a: Optional[float],
                     prob_b: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """Standard 2-side devig: p_a / (p_a + p_b), p_b / (p_a + p_b)."""
    if prob_a is None or prob_b is None: return None, None
    s = prob_a + prob_b
    if s <= 0: return None, None
    return prob_a / s, prob_b / s


# ── Builder ────────────────────────────────────────────────────────
def _normalize_player_name(name: str) -> str:
    return (name or "").strip().lower()


def build_canonical_props(
    raw_rows: Iterable[Dict[str, Any]],
    *,
    sport: str,
    line_tolerance: float = 0.0,
) -> List[CanonicalProp]:
    """Collapse a stream of raw book rows into canonical props.

    Each raw row must carry the minimal fields:
       `event_id`, `player_name`, `player_name_normalized`, `market`,
       `line`, `side` ("OVER"/"UNDER"), `book`, `odds` (American int).

    Optional: `player_id`. If absent we use `player_name_normalized`
    as the identity (works for MLB/NBA/NFL since name is unique within
    a slate).

    Sport-agnostic. The `sport` arg is used only by the market
    normalizer; no business logic is sport-specific.

    `line_tolerance` is reserved for future alt-ladder collapse
    (e.g. treat 0.5 and 1 as equivalent). For now, identity uses
    exact `line` after rounding to 2 decimals.
    """
    sport_lc = (sport or "").lower()
    bucket: Dict[Tuple, CanonicalProp] = {}
    for r in raw_rows:
        market_raw = r.get("market")
        family, canonical_market, is_alt = normalize_market(sport_lc, market_raw)
        if family is None:
            # Unknown market — caller's responsibility. Skip silently
            # would violate "do not silently default". We surface by
            # NOT including it; the audit script reports the unknown count.
            continue
        line = r.get("line")
        if line is None: continue
        try: line = round(float(line), 2)
        except Exception: continue
        side = (r.get("side") or "").upper()
        if side not in ("OVER", "UNDER"): continue
        try: odds = int(r["odds"])
        except (TypeError, KeyError, ValueError): continue
        book = (r.get("book") or "").strip().lower()
        if not book: continue
        event_id = str(r.get("event_id") or "")
        if not event_id: continue
        pn_norm = _normalize_player_name(r.get("player_name_normalized")
                                          or r.get("player_name") or "")
        if not pn_norm: continue
        player_id = r.get("player_id") or pn_norm
        key = (sport_lc, event_id, str(player_id), family,
                canonical_market, float(line))
        cp = bucket.get(key)
        if cp is None:
            cp = CanonicalProp(
                sport=sport_lc, event_id=event_id,
                player_id=str(player_id),
                player_name=r.get("player_name") or pn_norm,
                player_name_normalized=pn_norm,
                stat_family=family,
                canonical_line=float(line),
                canonical_market_key=canonical_market,
            )
            bucket[key] = cp
        cp.source_rows_count += 1
        if market_raw and market_raw not in cp.source_market_keys:
            cp.source_market_keys.append(market_raw)
        if side == "OVER":
            # Keep the most-favourable American value if multiple rows
            # exist for this (book, side, line) — same-book duplicates
            # (e.g. std + alt at the same line) collapse here.
            if book not in cp.over_prices or odds > cp.over_prices[book]:
                cp.over_prices[book] = odds
        else:
            if book not in cp.under_prices or odds > cp.under_prices[book]:
                cp.under_prices[book] = odds
    # Finalize each prop
    out: List[CanonicalProp] = []
    for cp in bucket.values():
        finalize_canonical_prop(cp)
        out.append(cp)
    return out


def finalize_canonical_prop(cp: CanonicalProp) -> None:
    """Compute derived fields after raw-row aggregation."""
    cp.book_count_over = len(cp.over_prices)
    cp.book_count_under = len(cp.under_prices)
    same_book_pairs = set(cp.over_prices) & set(cp.under_prices)
    cp.book_count_both_sides_same_book = len(same_book_pairs)
    union = set(cp.over_prices) | set(cp.under_prices)
    cp.available_books = sorted(union)
    cp.book_count_either_side_any_book = len(union)
    # Devig flags
    cp.has_same_book_devig = cp.book_count_both_sides_same_book > 0
    cp.has_cross_book_devig = (
        cp.book_count_over >= 1 and cp.book_count_under >= 1
    )
    # Best prices
    cp.best_over_price, cp.best_over_book = _best_chalk(cp.over_prices)
    cp.best_under_price, cp.best_under_book = _best_chalk(cp.under_prices)
    # Consensus (mean implied prob → American)
    mean_over = _mean_implied(cp.over_prices)
    mean_under = _mean_implied(cp.under_prices)
    cp.consensus_over_price = (
        float(_prob_to_american(mean_over)) if mean_over is not None else None
    )
    cp.consensus_under_price = (
        float(_prob_to_american(mean_under)) if mean_under is not None else None
    )
    # Devig probabilities — CROSS-BOOK supported. We devig consensus
    # implied probabilities; this is the same math the live path uses
    # via `_pick_reference_odds`'s DK+FD-mean consensus, generalised
    # to all REF books and either-side cross-book.
    cp.devig_over_probability, cp.devig_under_probability = (
        _devig_two_side(mean_over, mean_under)
    )


__all__ = [
    "CanonicalProp",
    "build_canonical_props",
    "finalize_canonical_prop",
]
