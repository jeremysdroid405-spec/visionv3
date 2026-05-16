"""
PRE-SCORING MLB BOOK QUOTE INTEGRITY FILTER (2026-05-17)
========================================================
Strips structurally absurd individual BOOK QUOTES out of a raw prop
BEFORE edge / fair_prob / market_probability / best_book / tp /
consensus / book_count are derived.

Rule
----
    IF sport == "mlb"
    AND market_class == "alternate"
    AND line == 0.5
    AND american_odds >= +500
    THEN eject ONLY that specific book quote.

Contract
--------
  * The prop itself stays alive with whichever books remain.
  * If every alternate-bucket book quote is ejected the filter sets
    ``rejected=True`` and the caller MUST drop the prop entirely
    (NO scoring, NO write to ``{sport}_prop_scores``).
  * ``excluded_book_quotes`` carries one dict per ejected quote with
    ``{book, odds, line, market_class, reason}`` so audit endpoints
    and forensic tests can reconstruct the decision deterministically.
  * ``integrity_filter_applied=True`` is stamped on the prop whenever
    at least one book quote was ejected. Eligible props with zero
    bad quotes keep the field absent so downstream readers can
    distinguish "filter ran but found nothing" from "filter ran and
    removed quotes".
  * Sport-scoped (MLB only) and class-scoped (``all_odds_alternate``
    only). Standard-market prices, gates, TP logic, and the existence
    of the alternate market itself are NEVER touched.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# American odds at or above this value on an alt 0.5 line are
# structurally absurd longshots (real prices observed in upstream
# DK / FD payloads). Spec value — boundary is INCLUSIVE.
_ABSURD_ODDS_THRESHOLD: int = 500
_FILTER_RULE: str = "mlb_alt_05line_long_odds"

# Book key (universal_odds_sync ALLOWED_BOOKS) ↔ per-book layer slot.
# Mirrors the slot map in `universal_odds_sync._normalize_market_data`.
_BOOK_TO_LAYER: Dict[str, str] = {
    "draftkings":     "dk_layer",
    "fanduel":        "fd_layer",
    "prizepicks":     "pp_layer",
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

# Book key ↔ flat per-book prefix (e.g. "dk" → dk_line/dk_odds/dk_odds_opp).
# Matches `prop_scores_store._BOOK_LAYER_FIELDS`.
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
    """Sport + market_class + line gate. Pure, no I/O."""
    if (prop.get("sport") or "").lower() != "mlb":
        return False
    if (prop.get("market_class") or "").lower() != "alternate":
        return False
    line = prop.get("line")
    try:
        return float(line) == 0.5
    except (TypeError, ValueError):
        return False


def _is_absurd(odds: Any) -> bool:
    """Inclusive >= +500 American-odds threshold."""
    try:
        return int(odds) >= _ABSURD_ODDS_THRESHOLD
    except (TypeError, ValueError):
        return False


def apply_book_quote_integrity_filter(
    prop: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    """Mutate ``prop`` in-place; eject absurd alt-bucket book quotes.

    Returns
    -------
    ``(prop, excluded, rejected)``
        prop      — same dict, mutated when quotes were ejected.
        excluded  — list of ejection records (empty when none ejected).
        rejected  — ``True`` ONLY when the filter was eligible AND every
                    alt-bucket book quote was ejected. Caller MUST drop
                    the prop entirely (do not score, do not write).
    """
    excluded: List[Dict[str, Any]] = []
    if not _is_eligible(prop):
        return prop, excluded, False

    alt_odds_raw = prop.get("all_odds_alternate")
    alt_lines_raw = prop.get("all_lines_alternate")
    alt_odds: Dict[str, Any] = dict(alt_odds_raw) if isinstance(alt_odds_raw, dict) else {}
    alt_lines: Dict[str, Any] = dict(alt_lines_raw) if isinstance(alt_lines_raw, dict) else {}

    pre_filter_count = len(alt_odds)
    if pre_filter_count == 0:
        return prop, excluded, False

    bad_books: List[str] = []
    for book, odds in alt_odds.items():
        if _is_absurd(odds):
            bad_books.append(book)
            excluded.append({
                "book": book,
                "odds": odds,
                "line": prop.get("line"),
                "market_class": "alternate",
                "reason": _FILTER_RULE,
            })

    if not bad_books:
        return prop, excluded, False

    # Eject from the canonical alternate odds/lines containers.
    for b in bad_books:
        alt_odds.pop(b, None)
        alt_lines.pop(b, None)
    prop["all_odds_alternate"] = alt_odds
    prop["all_lines_alternate"] = alt_lines

    # Eject from the per-book *_layer slot IFF the layer itself is an
    # alternate-class quote. Standard-class layers are NEVER touched
    # (cross-class safety: a book may have a sane standard quote
    # parallel to a bad alt quote — the standard one must survive).
    # Eject flat per-book line/odds/odds_opp triples too; on an alt
    # canonical they were sourced from the ejected alt attach.
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

    # Persist bookkeeping fields the score doc + audit endpoints surface.
    prop["integrity_filter_applied"] = True
    existing = prop.get("excluded_book_quotes")
    if not isinstance(existing, list):
        existing = []
    existing.extend(excluded)
    prop["excluded_book_quotes"] = existing

    rejected = len(alt_odds) == 0

    logger.info(
        "[INTEGRITY_FILTER] canonical_key=%s line=%s ejected_books=%s "
        "remaining_alt_books=%d rejected_prop=%s",
        prop.get("canonical_key"),
        prop.get("line"),
        bad_books,
        len(alt_odds),
        rejected,
    )
    return prop, excluded, rejected


def apply_to_prop_list(
    props: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run the filter over a batch; drop rejected props.

    Returns
    -------
    ``(surviving_props, stats)``
        surviving_props — same list minus any prop where every alt
                          book quote was ejected (``rejected=True``).
        stats           — diagnostic dict: scanned / eligible / mutated
                          / rejected / total_quotes_ejected.
    """
    surviving: List[Dict[str, Any]] = []
    eligible = 0
    mutated = 0
    rejected = 0
    total_ejected = 0

    for p in props:
        _, excluded, was_rejected = apply_book_quote_integrity_filter(p)
        if _is_eligible(p):
            eligible += 1
        if excluded:
            mutated += 1
            total_ejected += len(excluded)
        if was_rejected:
            rejected += 1
            logger.info(
                "[INTEGRITY_FILTER] rejected_prop canonical_key=%s "
                "line=%s reason=all_alt_book_quotes_ejected "
                "ejected_count=%d",
                p.get("canonical_key"), p.get("line"), len(excluded),
            )
            continue
        surviving.append(p)

    stats = {
        "scanned": len(props),
        "eligible": eligible,
        "mutated": mutated,
        "rejected": rejected,
        "total_quotes_ejected": total_ejected,
        "rule": _FILTER_RULE,
    }
    logger.info(
        "[INTEGRITY_FILTER] batch summary scanned=%d eligible=%d "
        "mutated=%d rejected=%d total_quotes_ejected=%d",
        stats["scanned"], stats["eligible"], stats["mutated"],
        stats["rejected"], stats["total_quotes_ejected"],
    )
    return surviving, stats


__all__ = [
    "apply_book_quote_integrity_filter",
    "apply_to_prop_list",
]
