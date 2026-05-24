"""
Pin the multi-book universe behaviour shipped 2026-05-24.

User brief:
  > "We are no longer treating PrizePicks as the sole anchor universe.
  >  Replace the single ANCHOR_BOOK='prizepicks' architecture with a
  >  multi-book market universe."

These tests lock the anchor-priority order, the per-row metadata
fields the downstream pipeline depends on, and the optimizer's
book_filter routing — so no future refactor can silently revert
PropVision to a PrizePicks-only platform.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from scripts.sgo.build_pp_research_core import (
    ANCHOR_PRIORITY,
    _PLAYABLE_FLAG_MAP,
    _pick_anchor,
)
from routes.emergent_admin.optimizer import (
    _book_filter_clause,
    _BOOK_FILTER_MAP,
    OptimizerRunBody,
)


def test_anchor_priority_starts_with_prizepicks():
    """PP stays first so existing PP-anchored rows are byte-identical
    to before the multi-book refactor."""
    assert ANCHOR_PRIORITY[0] == "prizepicks", (
        "PP must remain the first-priority anchor for backward "
        "compatibility with existing PP-anchored rows")


def test_anchor_priority_covers_required_books():
    """Brief mandates these six books as the initial multi-book set."""
    required = {"prizepicks", "draftkings", "fanduel", "betmgm",
                  "caesars", "betonlineag"}
    assert required.issubset(set(ANCHOR_PRIORITY)), (
        f"Missing required books: {required - set(ANCHOR_PRIORITY)}")


def test_pick_anchor_uses_prizepicks_when_present():
    """When PP has a quote, it MUST be the anchor (legacy parity)."""
    seen = {
        "prizepicks": {"price": -110},
        "draftkings": {"price": -115},
        "fanduel":    {"price": -108},
    }
    bid, anchor, src = _pick_anchor(seen)
    assert bid == "prizepicks"
    assert anchor["price"] == -110
    assert src == "priority"


def test_pick_anchor_falls_back_to_draftkings_when_pp_missing():
    """When PP is absent, DK is next in priority — this unblocks HR /
    SB / doubles / triples which PP doesn't carry but DK does."""
    seen = {
        "draftkings": {"price": +250},
        "fanduel":    {"price": +245},
    }
    bid, anchor, src = _pick_anchor(seen)
    assert bid == "draftkings"
    assert anchor["price"] == +250
    assert src == "priority"


def test_pick_anchor_full_priority_chain():
    """Walk every fallback step in the priority chain."""
    test_cases = [
        (["fanduel", "betmgm", "caesars", "betonlineag"], "fanduel"),
        (["betmgm", "caesars", "betonlineag"],            "betmgm"),
        (["caesars", "betonlineag"],                       "caesars"),
        (["betonlineag"],                                  "betonlineag"),
    ]
    for available, expected in test_cases:
        seen = {b: {"price": -100} for b in available}
        bid, _, src = _pick_anchor(seen)
        assert bid == expected, (
            f"with {available} expected {expected}, got {bid}")
        assert src == "priority"


def test_pick_anchor_deterministic_fallback_when_no_priority_book():
    """If NONE of the priority books offer a quote (rare edge case),
    pick the alphabetically-first book deterministically. Reproducible
    backtests require this; we must NEVER randomly choose."""
    seen = {
        "pinnacle":   {"price": -110},
        "bovada":     {"price": -108},
        "betparx":    {"price": -112},
    }
    bid, _, src = _pick_anchor(seen)
    assert bid == "betparx", (
        f"fallback must pick alphabetically-first ('betparx'), got {bid}")
    assert src == "fallback_first_available"


def test_pick_anchor_returns_none_for_empty_input():
    """A row with no books at all must be skippable, not crash."""
    bid, _, src = _pick_anchor({})
    assert bid is None
    assert src == "none"


def test_playable_flag_map_covers_all_priority_books():
    """Every book in `ANCHOR_PRIORITY` must have a `playable_on_*`
    flag mapping so the optimizer UI can filter on it."""
    # betonline / betonlineag share the same `playable_on_bol` flag
    expected_flags = {
        "playable_on_pp", "playable_on_dk", "playable_on_fd",
        "playable_on_mgm", "playable_on_caesars", "playable_on_bol",
    }
    assert set(_PLAYABLE_FLAG_MAP.values()) == expected_flags


def test_book_filter_clause_any_is_empty_dict():
    """Default `book_filter='any'` MUST be a no-op filter — the
    optimizer treats every row equally."""
    assert _book_filter_clause("any") == {}


def test_book_filter_clause_per_book_targets_playable_flag():
    """Each book-specific filter targets the corresponding
    `playable_on_*` field — NOT `anchor_book`. This is important:
    a row anchored on DK can still be playable on PP if PP offered
    the line too, and pp_only must surface it."""
    assert _book_filter_clause("pp_only")  == {"playable_on_pp":      True}
    assert _book_filter_clause("dk_only")  == {"playable_on_dk":      True}
    assert _book_filter_clause("fd_only")  == {"playable_on_fd":      True}
    assert _book_filter_clause("mgm_only") == {"playable_on_mgm":     True}
    assert _book_filter_clause("caesars_only") == {"playable_on_caesars": True}
    assert _book_filter_clause("bol_only") == {"playable_on_bol":     True}


def test_book_filter_multi_book_uses_array_size_expr():
    """`multi_book` filters rows where ≥ 2 books offer the line
    (consensus strength check). Implemented as a Mongo $expr because
    array-length filters need it."""
    clause = _book_filter_clause("multi_book")
    assert "$expr" in clause


def test_optimizer_default_book_filter_is_any():
    """The optimizer default must remain `any` — the WHOLE point of
    the multi-book refactor is to STOP narrowing the universe by
    default. PP-only is now an opt-in filter."""
    body = OptimizerRunBody(start="2025-05-01", end="2025-05-31")
    assert body.book_filter == "any"


def test_unknown_book_filter_defaults_to_no_filter():
    """Defensive — an unknown filter string shouldn't crash; it
    should fall back to 'no filter' rather than 'never match'."""
    assert _book_filter_clause("garbage_filter") == {}


def test_book_filter_map_size_lock():
    """Lock the set of recognized book filters so a future refactor
    can't quietly drop one."""
    expected_keys = {
        "any", "pp_only", "dk_only", "fd_only",
        "mgm_only", "caesars_only", "bol_only",
    }
    assert set(_BOOK_FILTER_MAP.keys()) == expected_keys
