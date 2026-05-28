"""
Blocked-book ingest filter contract (2026-06-02).

ROOT CAUSE this pins:
  The replay/optimizer pool included rows from books that are not
  real-money regulated sportsbooks:
    • fliff    — Fliff Coins (free-play sweepstakes; +300 longshots
                  calibrated to fake currency, no skin in the game).
    • mybookie — tiny offshore book; lines often stale and never
                  agree with the market.
    • unknown  — book label missing or garbled (can't de-vig or grade
                  reliably).

  Including these in the optimizer pool produces:
    1. de-vig math that averages real prices with sweepstakes prices,
       biasing fair-probability estimates toward fake numbers.
    2. ROI inflation when Fliff's +300 longshots happen to hit.
    3. Sample-size inflation in optimizer cells that don't have real
       book coverage (the cell "wins" because Fliff was the only book
       quoting a degenerate longshot that happened to land).

  Hard removal from the warehouse was performed on 2026-06-02
  (see CHANGELOG). This contract ensures the books cannot be
  re-ingested by a future reshape run.

CONTRACT:
  `reshape_sgo_to_replay_odds.BLOCKED_BOOKS` must contain at minimum
  the three books removed in the 2026-06-02 cleanup, and the
  `_make_replay_row` reshape function must short-circuit any input
  doc whose `_resolve_book` falls in that set.
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")
from scripts.sgo import reshape_sgo_to_replay_odds as rsr  # noqa: E402


def test_blocked_books_set_includes_fliff() -> None:
    assert "fliff" in rsr.BLOCKED_BOOKS, (
        "fliff must remain blocked — it is a free-play sweepstakes "
        "platform, not a real-money sportsbook."
    )


def test_blocked_books_set_includes_mybookie() -> None:
    assert "mybookie" in rsr.BLOCKED_BOOKS


def test_blocked_books_set_includes_unknown_label() -> None:
    assert "unknown" in rsr.BLOCKED_BOOKS


def test_blocked_books_set_is_lowercase() -> None:
    """All entries must be lowercase so the case-insensitive check
    in `_make_replay_row` works regardless of upstream casing."""
    for b in rsr.BLOCKED_BOOKS:
        assert b == b.lower(), f"BLOCKED_BOOKS entry {b!r} must be lowercase"


def test_reshape_source_blocks_books_at_ingest() -> None:
    """The reshape function body must reject rows whose resolved book
    is in BLOCKED_BOOKS. Encoded as a source-level contract so we
    don't have to spin up MongoDB to validate it."""
    import inspect
    src = inspect.getsource(rsr)
    # The blocked-book guard must reference BLOCKED_BOOKS and short-
    # circuit before any row is appended to the destination buffer.
    assert "BLOCKED_BOOKS" in src
    assert "blocked_book" in src, (
        "Reshape must emit a `blocked_book` skip reason so it's "
        "auditable in the preflight/diagnostic stats."
    )
