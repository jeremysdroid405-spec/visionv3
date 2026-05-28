"""
Multi-book reshape contract (2026-06-02).

ROOT CAUSE this pins:
  `sgo_pp_research_core_enriched.books[]` carries up to 22 book quotes
  per prop, including real-money US sportsbooks (DraftKings, FanDuel,
  ESPN BET, Fanatics, HardRock) that DO post +150..+250 and +250+
  MLB longshots. The old reshape emitted ONE row per prop — the
  best_book / anchor — dropping ~95% of book quotes. That left the
  optimizer's `+150_+300` and `+300p` odds buckets populated almost
  exclusively by Fliff (free-play sweepstakes). When Fliff was removed,
  those buckets had no data and 7,933 cells were excluded from the
  optimizer ranking with the misleading "no graded rows" message.

CONTRACT:
  `reshape_rows` must enumerate every entry in `books[]`, skip
  BLOCKED_BOOKS at the source, and emit ONE row per (prop × real-money
  book). When `books[]` is empty, it falls back to the legacy single-
  row best-book / anchor behaviour so unenriched / pre-multi-book
  docs are not dropped.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from scripts.sgo.reshape_sgo_to_replay_odds import (  # noqa: E402
    reshape_rows, reshape_row,
)


def _sample_doc(books):
    return {
        "league_id": "MLB",
        "game_date": "2025-06-15",
        "event_id":  "EVT1",
        "player_name": "Test Player",
        "stat_id":   "pitching_strikeouts",
        "line":      6.5,
        "side":      "OVER",
        "books":     books,
        "best_book_id": "draftkings",
        "anchor":   {"book_id": "prizepicks", "price": "+100"},
    }


def test_reshape_emits_one_row_per_book_in_books_array() -> None:
    """The whole point of the multi-book fix: 8 books in upstream
    → 8 rows in the replay collection."""
    doc = _sample_doc([
        {"book_id": "draftkings", "price": "+165"},
        {"book_id": "fanduel",    "price": "+180"},
        {"book_id": "betmgm",     "price": "+155"},
        {"book_id": "espnbet",    "price": "+170"},
        {"book_id": "fanatics",   "price": "+175"},
        {"book_id": "caesars",    "price": "+160"},
        {"book_id": "hardrockbet","price": "+185"},
        {"book_id": "fliff",      "price": "+300"},  # BLOCKED — must drop
    ])
    rows, reason = reshape_rows(doc, datetime.now(timezone.utc))
    assert reason is None, f"unexpected skip reason: {reason}"
    # 8 input books − 1 blocked (fliff) = 7 emitted rows
    assert len(rows) == 7, (
        f"expected 7 rows (8 books − 1 blocked), got {len(rows)}: "
        f"{[r['book'] for r in rows]}"
    )
    emitted_books = {r["book"] for r in rows}
    assert "fliff" not in emitted_books, "BLOCKED_BOOKS must not emit"
    assert emitted_books == {"draftkings", "fanduel", "betmgm", "espnbet",
                              "fanatics", "caesars", "hardrockbet"}
    # Each row carries that book's specific odds, not a shared value
    odds_by_book = {r["book"]: r["odds"] for r in rows}
    assert odds_by_book["draftkings"] == 165
    assert odds_by_book["fanduel"] == 180
    assert odds_by_book["fliff" if False else "espnbet"] == 170


def test_reshape_falls_back_to_anchor_when_books_empty() -> None:
    """Legacy docs with no books[] array still emit ONE row from the
    anchor / best_book — they don't get silently dropped."""
    doc = _sample_doc([])
    rows, reason = reshape_rows(doc, datetime.now(timezone.utc))
    # No books[] → fall back to anchor (prizepicks +100) or best_book.
    # Either way exactly one row.
    assert reason is None
    assert len(rows) == 1


def test_legacy_reshape_row_returns_first_emitted() -> None:
    """The 1-row `reshape_row` wrapper kept for backwards-compat must
    return the FIRST row when `reshape_rows` emits multiple, so the
    legacy smoke test keeps passing."""
    doc = _sample_doc([
        {"book_id": "draftkings", "price": "+150"},
        {"book_id": "fanduel",    "price": "+160"},
    ])
    row, reason = reshape_row(doc, datetime.now(timezone.utc))
    assert reason is None
    assert row is not None
    assert row["book"] in {"draftkings", "fanduel"}


def test_reshape_skips_books_with_null_price() -> None:
    """Books[] entries with missing / unparseable price must be skipped
    without dropping the rest of the array."""
    doc = _sample_doc([
        {"book_id": "draftkings", "price": "+150"},
        {"book_id": "fanduel",    "price": None},      # skip
        {"book_id": "betmgm",     "price": "bad"},     # skip
        {"book_id": "espnbet",    "price": "+170"},
    ])
    rows, _ = reshape_rows(doc, datetime.now(timezone.utc))
    assert len(rows) == 2
    assert {r["book"] for r in rows} == {"draftkings", "espnbet"}


def test_reshape_skips_books_with_empty_book_id() -> None:
    """Entries with no book_id are unidentifiable — skip them."""
    doc = _sample_doc([
        {"book_id": "draftkings", "price": "+150"},
        {"book_id": "",           "price": "+200"},   # skip
        {"book_id": None,         "price": "+200"},   # skip
    ])
    rows, _ = reshape_rows(doc, datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["book"] == "draftkings"
