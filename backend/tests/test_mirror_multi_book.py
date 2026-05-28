"""
Multi-book mirror contract (2026-06-02).

ROOT CAUSE this pins:
  The Layer-3 runner (`mlb_propvision_full_pipeline_outputs`) preserves
  ONE row per (prop × book) — verified: May 2025 has 208,630 rows
  across 21 books for 6,107 unique props (~34 books per prop on
  average, multi-book intact). But the mirror's `$group` stage in
  `_mirror_to_legacy` keyed only on (event, player, market, line,
  side), then took `$first` of `book` and `odds`, COLLAPSING 21
  books per prop into a single row. The resulting
  `sgo_propvision_full_pipeline_replay` collection had 6,107 rows —
  identical to the unique-prop count, with one arbitrary book per
  row. Every other book quote was thrown away.

  The optimizer reads from this mirror collection. With only one row
  per prop:
    1. The +150_+300 / +300p odds buckets stayed empty even after we
       fixed the upstream reshape (which now correctly emits 165K+
       multi-book rows to sgo_replay_alt_odds_raw).
    2. De-vigging math couldn't see multiple books for the same
       prop, so it operated on a single-book sample (no devig possible).
    3. ROI / HR estimates pulled from a randomly-picked book per prop,
       which is essentially noise.

CONTRACT:
  `_mirror_to_legacy` MUST include `book` in BOTH the `$group._id`
  key AND the upsert filter. Without both, multi-book data collapses
  at one of those two layers.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")

MIRROR_SRC = Path("/app/backend/scripts/sgo/historical_full_pipeline_replay.py")


@pytest.fixture(scope="module")
def src() -> str:
    return MIRROR_SRC.read_text()


def test_mirror_group_key_includes_book(src: str) -> None:
    """The $group._id dict MUST contain `book` so per-book quotes
    survive the group stage."""
    # The group _id block is multi-line. Search for the explicit key.
    assert '"event_id": "$event_id"' in src
    assert '"player_name_normalized": "$player_name_normalized"' in src
    assert '"book": "$book"' in src, (
        "Mirror $group._id must include `book` to preserve per-book "
        "rows. Without this, 21 books per prop collapse into 1 row "
        "and the optimizer loses the +150+ odds buckets."
    )


def test_mirror_upsert_filter_includes_book(src: str) -> None:
    """The upsert filter (`flt` dict) MUST include `book` so each
    per-book row gets its own document instead of overwriting the
    same slot."""
    # The upsert filter is the `flt = { ... }` block right before
    # the `UpdateOne(flt, ...)` call.
    assert "UpdateOne(flt" in src
    assert '"book": replay_row["book"]' in src, (
        "Mirror upsert filter must include `book` so the upsert is "
        "keyed per (prop × book). Without this, the upserts collapse "
        "every book row into the same document."
    )
