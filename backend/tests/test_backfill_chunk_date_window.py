"""
Unit tests for _chunk_date_window — the date-chunking primitive used to
keep SGO bulk score-pulls under the transport timeout.

Locks the contract:
  • 30-day default chunking on a 16-month window produces ~17 chunks
  • final chunk closes ON the end date (inclusive)
  • adjacent chunks are contiguous (no gaps, no overlaps that double-count)
  • degenerate inputs (None, single-day, malformed) → single passthrough chunk
  • chunk_days <= 0 → single passthrough chunk
  • --chunk-days=7 splits a 30-day window into ~5 chunks
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.backfill_team_matchup_scores import _chunk_date_window


def test_chunk_30_day_default_on_16_month_window():
    """The exact scenario that triggered the VPS timeout: NFL season +
    next season → 521 days → must split into multiple chunks."""
    chunks = _chunk_date_window("2024-09-06", "2026-02-08", 30)
    # 521 days / 30 ≈ 18 chunks (rounded up)
    assert 15 <= len(chunks) <= 20, (
        f"Expected ~17-18 chunks for 521-day window @ 30 days, got "
        f"{len(chunks)}")
    # First chunk begins on starts_after
    assert chunks[0][0] == "2024-09-06"
    # Last chunk's end is at least the end date (+1 day for "exclusive
    # right" so SGO returns the final-day games)
    assert chunks[-1][1] >= "2026-02-08"
    # Adjacent chunks are contiguous
    for (s1, e1), (s2, _e2) in zip(chunks, chunks[1:]):
        assert e1 == s2, (
            f"Chunks must be contiguous: chunk ending {e1} ≠ next chunk "
            f"starting {s2}")


def test_chunk_seven_day_on_30_day_window():
    """Smaller chunks for resumable backfill."""
    chunks = _chunk_date_window("2024-09-01", "2024-09-30", 7)
    # 30 days / 7 ≈ 5 chunks
    assert 4 <= len(chunks) <= 6
    assert chunks[0][0] == "2024-09-01"
    assert chunks[-1][1] >= "2024-09-30"


def test_chunk_single_day_window():
    """Same-day window should produce exactly 1 chunk."""
    chunks = _chunk_date_window("2024-09-07", "2024-09-07", 30)
    assert len(chunks) == 1
    assert chunks[0][0] == "2024-09-07"
    # right side is inclusive of the day (we add +1 to close)
    assert chunks[0][1] >= "2024-09-07"


def test_chunk_passthrough_on_none_bounds():
    """Without both bounds, chunking is a no-op."""
    assert _chunk_date_window(None, "2024-12-31", 30) == [(None, "2024-12-31")]
    assert _chunk_date_window("2024-09-01", None, 30) == [("2024-09-01", None)]
    assert _chunk_date_window(None, None, 30) == [(None, None)]


def test_chunk_passthrough_on_zero_or_negative_chunk_days():
    """Defensive: caller passes 0 → don't infinite-loop, just pass through."""
    assert _chunk_date_window(
        "2024-09-01", "2024-12-31", 0) == [("2024-09-01", "2024-12-31")]
    assert _chunk_date_window(
        "2024-09-01", "2024-12-31", -5) == [("2024-09-01", "2024-12-31")]


def test_chunk_passthrough_on_malformed_dates():
    """Malformed ISO dates fall back to single passthrough chunk."""
    chunks = _chunk_date_window("not-a-date", "2024-12-31", 30)
    assert chunks == [("not-a-date", "2024-12-31")]


def test_chunk_passthrough_when_end_before_start():
    """Inverted window → single chunk (caller's mistake; don't loop)."""
    chunks = _chunk_date_window("2025-01-01", "2024-12-01", 30)
    assert chunks == [("2025-01-01", "2024-12-01")]


def test_chunk_no_gaps_full_coverage():
    """End-to-end coverage check: the union of chunks must span
    [starts_after, starts_before]. Validate by walking dates."""
    from datetime import date, timedelta
    chunks = _chunk_date_window("2024-09-06", "2024-11-15", 14)
    # Build the set of covered (start, end_exclusive) days
    covered = set()
    for s, e in chunks:
        cur = date.fromisoformat(s)
        end = date.fromisoformat(e)
        while cur < end:
            covered.add(cur.isoformat())
            cur += timedelta(days=1)
    # Verify EVERY day in [2024-09-06, 2024-11-15] is covered
    cur = date(2024, 9, 6)
    end = date(2024, 11, 15)
    missing = []
    while cur <= end:
        if cur.isoformat() not in covered:
            missing.append(cur.isoformat())
        cur += timedelta(days=1)
    assert missing == [], (
        f"Date coverage gap: these days are not in any chunk: {missing}")


def test_chunk_no_overlaps_between_adjacent_chunks():
    chunks = _chunk_date_window("2024-09-01", "2025-03-01", 30)
    for (s1, e1), (s2, _) in zip(chunks, chunks[1:]):
        # Adjacent chunks meet exactly: chunk1's exclusive-end ==
        # chunk2's inclusive-start. NO overlap.
        assert e1 == s2, (
            f"Chunks overlap or have gap: chunk1=({s1},{e1}) "
            f"chunk2 starts {s2}")
