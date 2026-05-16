"""
PEER-DISAGREEMENT BOOK QUOTE INTEGRITY FILTER (2026-05-17)
=========================================================
Replaces the old flat ``american_odds >= +500`` cutoff with a
peer-relative outlier rule that survives single-book divergence
without rejecting legitimate executable odds.

Rule (initial MLB alternate-markets-only scope)
-----------------------------------------------
For each prop, considering ONLY real sportsbook quotes in the
``all_odds_alternate`` container (PrizePicks is **never** allowed
into the peer calculation — its ``+100`` payout multiplier is not
true sportsbook American odds), eject an individual book quote
when ALL of the following hold:

1. There are ≥2 non-PP sportsbook quotes on the prop.
2. ``peer_median`` = median(American odds of EVERY OTHER real
   sportsbook quote on the same canonical row, i.e. same side /
   line / stat / market_class). Candidate is excluded from its own
   peer set.
3. ``delta = book_odds - peer_median  >=  +200``.

The rule is sport-scoped to MLB and class-scoped to alternate
markets for the initial rollout. Standard markets, gates, TP
formulas, devig math, consensus formulas, and canonical keys are
NEVER touched. The prop itself is **never** dropped — only the
specific bad quote is ejected from aggregation.

Forensic persistence (per ejected quote)
----------------------------------------
``excluded_book_quotes`` carries one record per ejection:

    {
        "book":               <book_key>,
        "odds":               <american_odds>,
        "line":               <line>,
        "market_class":       "alternate",
        "reason":             "peer_disagreement_plus_200",
        "peer_median_odds":   <median across peers>,
        "book_odds_delta":    <book_odds - peer_median>,
        "peer_book_count":    <# peers used in median>,
    }

``integrity_filter_applied=True`` is stamped on the prop only when
≥1 quote was ejected. Eligible props with no ejections do NOT get
the flag, so downstream readers can distinguish "filter ran but
found nothing" from "filter ran and ejected".

Config flag
-----------
``ENABLE_PEER_DISAGREEMENT_FILTER`` (default TRUE). When false the
recompute path skips the filter entirely — fully reversible. The
older ``ENABLE_BOOK_QUOTE_INTEGRITY_FILTER`` flag continues to
gate the legacy ``+500`` cutoff and remains disabled by default.
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# +N American-odds threshold (book - peer_median). Boundary is
# INCLUSIVE: a delta of exactly +200 ejects.
_DELTA_THRESHOLD: int = 200
_RULE_TAG: str = "peer_disagreement_plus_200"

# Books explicitly forbidden from the peer-set. PrizePicks ships
# fixed-payout multipliers (American-equivalent +100) — these are
# not real sportsbook quotes and would systematically pull the
# peer median toward +100, corrupting every comparison.
_REFERENCE_ONLY_BOOKS = frozenset({"prizepicks"})

# Book key ↔ per-book layer slot (mirrors universal_odds_sync slot
# map and the integrity_filter sibling module). When an alt-class
# layer is cleared, the parallel standard layer (if any) is left
# untouched: the filter is class-pure.
_BOOK_TO_LAYER: Dict[str, str] = {
    "draftkings":     "dk_layer",
    "fanduel":        "fd_layer",
    "prizepicks":     "pp_layer",   # never reached — PP filtered out upfront
    "betonlineag":    "bol_layer",
    "betmgm":         "mgm_layer",
    "williamhill_us": "csr_layer",
    "espnbet":        "eb_layer",
    "hardrockbet":    "hrb_layer",
    "betrivers":      "brv_layer",
    "betparx":        "prx_layer",
    "ballybet":       "bly_layer",
    "fliff":          "flf_layer",
}

# Flat per-book prefix for {prefix}_line / {prefix}_odds /
# {prefix}_odds_opp triples on the canonical row.
_BOOK_FLAT_PREFIX: Dict[str, str] = {
    "draftkings":     "dk",
    "fanduel":        "fd",
    "prizepicks":     "pp",
    "betonlineag":    "bol",
    "betmgm":         "mgm",
    "williamhill_us": "csr",
    "espnbet":        "eb",
    "hardrockbet":    "hrb",
    "betrivers":      "brv",
    "betparx":        "prx",
    "ballybet":       "bly",
    "fliff":          "flf",
}


def _is_eligible(prop: Dict[str, Any]) -> bool:
    """Sport + market-class gate. Pure, no I/O.

    Accepts BOTH the new ``market_class=='alternate'`` field (from
    the 2026-05 odds-pipeline hardening) AND the legacy
    ``is_alternate_market=True`` flag — `mlb_live_props` rows are
    still ingested with the legacy flag today, so the filter must
    recognise both to fire on the live cohort.
    """
    if (prop.get("sport") or "").lower() != "mlb":
        return False
    mc = (prop.get("market_class") or "").lower()
    if mc == "alternate":
        return True
    if prop.get("is_alternate_market") is True:
        return True
    return False


def _resolve_alt_odds_source(
    prop: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(container_key, dict)`` of the alt-bucket odds for
    this prop.

    Prefers ``all_odds_alternate`` (post-hardening shape). Falls
    back to the legacy combined ``all_odds`` dict for rows that
    pre-date the hardening but carry ``is_alternate_market=True``.
    Returns ``('', {})`` when nothing usable is present.
    """
    aoa = prop.get("all_odds_alternate")
    if isinstance(aoa, dict) and aoa:
        return "all_odds_alternate", aoa
    legacy = prop.get("all_odds")
    if isinstance(legacy, dict) and legacy and prop.get("is_alternate_market") is True:
        return "all_odds", legacy
    return "", {}


def _real_sportsbook_quotes(alt_odds: Dict[str, Any]) -> Dict[str, float]:
    """Return ``{book: american_odds_float}`` for real sportsbooks
    only. PrizePicks (and any other reference-only books) are
    excluded — never participate in peer calculations.
    Non-numeric values are dropped silently."""
    out: Dict[str, float] = {}
    for book, raw in alt_odds.items():
        if book in _REFERENCE_ONLY_BOOKS:
            continue
        try:
            out[book] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def apply_peer_disagreement_filter(
    prop: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Mutate ``prop`` in-place; eject peer-disconnected alt-bucket
    book quotes per the rule above.

    Returns
    -------
    ``(prop, excluded)``
        prop      — same dict, mutated when quotes were ejected.
        excluded  — list of ejection records (empty when none
                    ejected). The prop itself is **never** dropped
                    by this rule, regardless of how many quotes
                    are ejected.
    """
    excluded: List[Dict[str, Any]] = []
    if not _is_eligible(prop):
        return prop, excluded

    container_key, alt_odds_raw = _resolve_alt_odds_source(prop)
    if not container_key:
        return prop, excluded

    alt_odds: Dict[str, Any] = dict(alt_odds_raw)
    # Lines container parallels the chosen odds container.
    alt_lines_raw = prop.get(
        "all_lines_alternate" if container_key == "all_odds_alternate"
        else "all_lines"
    )
    alt_lines: Dict[str, Any] = dict(alt_lines_raw) if isinstance(alt_lines_raw, dict) else {}

    real = _real_sportsbook_quotes(alt_odds)
    # Need ≥2 non-PP sportsbook quotes to fire (per spec).
    if len(real) < 2:
        return prop, excluded

    bad_books: List[str] = []
    for book, book_odds in real.items():
        # Peers = every OTHER real sportsbook quote (candidate
        # excluded from its own peer set).
        peer_vals = [v for b, v in real.items() if b != book]
        if not peer_vals:
            continue
        peer_med = float(median(peer_vals))
        delta = book_odds - peer_med
        if delta >= _DELTA_THRESHOLD:
            bad_books.append(book)
            excluded.append({
                "book": book,
                "odds": book_odds,
                "line": prop.get("line"),
                "market_class": "alternate",
                "reason": _RULE_TAG,
                "peer_median_odds": peer_med,
                "book_odds_delta": delta,
                "peer_book_count": len(peer_vals),
            })

    if not bad_books:
        return prop, excluded

    # Eject from the chosen alternate-odds container (post- or
    # pre-hardening) plus its lines sibling.
    for b in bad_books:
        alt_odds.pop(b, None)
        alt_lines.pop(b, None)
    prop[container_key] = alt_odds
    if isinstance(alt_lines_raw, dict):
        prop[
            "all_lines_alternate" if container_key == "all_odds_alternate"
            else "all_lines"
        ] = alt_lines

    # Eject per-book *_layer slot IFF the layer itself is
    # alternate-class. A parallel standard layer for the same book
    # MUST survive (class-pure invariant). Clear flat per-book
    # line/odds/odds_opp triples for ejected books on this
    # canonical row only.
    for b in bad_books:
        layer_key = _BOOK_TO_LAYER.get(b)
        if layer_key:
            layer = prop.get(layer_key)
            if isinstance(layer, dict) and (layer.get("market_class") == "alternate"):
                prop[layer_key] = None
        prefix = _BOOK_FLAT_PREFIX.get(b)
        if prefix:
            for suf in ("_line", "_odds", "_odds_opp"):
                prop.pop(f"{prefix}{suf}", None)

    prop["integrity_filter_applied"] = True
    existing = prop.get("excluded_book_quotes")
    if not isinstance(existing, list):
        existing = []
    existing.extend(excluded)
    prop["excluded_book_quotes"] = existing

    logger.info(
        "[PEER_DISAGREEMENT_FILTER] canonical_key=%s line=%s "
        "ejected_books=%s remaining_alt_books=%d (rule=%s, "
        "threshold=+%d)",
        prop.get("canonical_key"),
        prop.get("line"),
        bad_books,
        len(alt_odds),
        _RULE_TAG,
        _DELTA_THRESHOLD,
    )
    return prop, excluded


def apply_to_prop_list(
    props: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run the peer-disagreement rule over a batch.

    Returns
    -------
    ``(props, stats)``
        props — same list (no prop is ever dropped by this rule).
        stats — diagnostic dict: scanned / eligible / mutated /
                total_quotes_ejected / rule / threshold.
    """
    eligible = 0
    mutated = 0
    total_ejected = 0
    for p in props:
        is_elig = _is_eligible(p)
        if is_elig:
            eligible += 1
        _, excluded = apply_peer_disagreement_filter(p)
        if excluded:
            mutated += 1
            total_ejected += len(excluded)
    stats = {
        "scanned": len(props),
        "eligible": eligible,
        "mutated": mutated,
        "total_quotes_ejected": total_ejected,
        "rule": _RULE_TAG,
        "threshold_plus": _DELTA_THRESHOLD,
    }
    logger.info(
        "[PEER_DISAGREEMENT_FILTER] batch summary scanned=%d "
        "eligible=%d mutated=%d total_quotes_ejected=%d",
        stats["scanned"], stats["eligible"], stats["mutated"],
        stats["total_quotes_ejected"],
    )
    return props, stats


__all__ = [
    "apply_peer_disagreement_filter",
    "apply_to_prop_list",
]
