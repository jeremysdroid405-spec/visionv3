"""
Regression tests for scripts/sgo/build_pp_research_core — schema contracts
for the per-month result dict returned by `build_month()`.

The original bug:
    `build_month()` had two return paths with DIFFERENT schemas:
      • Empty-month early-return (no events in window) → dict missing
        `avg_books_per_anchor`, `consensus_rate`, `sample_docs`.
      • Populated path → dict with all fields.

    The caller in `amain()` logged `r['avg_books_per_anchor']`
    unconditionally and crashed with `KeyError` whenever a season's
    first month had zero anchors (e.g. NCAAF August). This silently
    aborted the whole multi-month run mid-way.

These tests lock in:
  1. Empty-month path returns the SAME keys as the populated path.
  2. Values are zero / sensible defaults (no None for numeric counters).
  3. Caller's logging accumulator paths use `.get()` defaults (light
     contract test — ensures every field the caller touches has a
     safe default).

Pure schema tests — no Mongo I/O required.
"""
from __future__ import annotations
import os
import sys
import inspect

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

import pytest

from scripts.sgo import build_pp_research_core as bppc


# Keys the populated path returns AND the caller (`amain` summary loop)
# touches. If the empty-month path drops any of these, the multi-month
# run crashes mid-stream.
_REQUIRED_RESULT_KEYS = {
    "month", "events", "anchors", "books_attached", "with_consensus",
    "avg_books_per_anchor", "consensus_rate",
    "upserts", "skipped_dry_run", "sample_docs",
}


# ───────────────────────── schema regression ─────────────────────────
class _FakeCursor:
    """Mimics motor's async cursor for `.find(...)`."""
    def __init__(self, rows):
        self._rows = list(rows)
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = rows or []
    def find(self, *_a, **_kw):
        return _FakeCursor(self._rows)


class _FakeDB:
    """Minimal stand-in for a Motor DB used in the empty-month path.
    Returns zero events for any query, so `build_month` always hits
    the early-return."""
    def __init__(self):
        self.sgo_events = _FakeColl(rows=[])
    def __getitem__(self, _name):
        return _FakeColl(rows=[])


@pytest.mark.asyncio
async def test_build_month_empty_returns_full_schema():
    """The empty-month early-return must carry every key the populated
    return carries — otherwise the caller's logging crashes."""
    db = _FakeDB()
    r = await bppc.build_month(
        db,
        league="NCAAF",
        month="2024-08",
        start_iso="2024-08-01",
        end_iso="2024-08-31",
        dry_run=True,
        player_name={},
        out_coll="sgo_ncaaf_research_core")
    missing = _REQUIRED_RESULT_KEYS - set(r.keys())
    assert not missing, (
        f"build_month empty-path is missing required keys: {missing}\n"
        f"This will crash the multi-month summary log in amain().")


@pytest.mark.asyncio
async def test_build_month_empty_returns_numeric_zero_not_none():
    """Counters must be 0 (numeric), not None — accumulators in
    amain() do `total[k] += r[k]`. None + int raises TypeError."""
    db = _FakeDB()
    r = await bppc.build_month(
        db,
        league="NCAAF", month="2024-08",
        start_iso="2024-08-01", end_iso="2024-08-31",
        dry_run=True, player_name={},
        out_coll="sgo_ncaaf_research_core")
    for k in ("events", "anchors", "books_attached",
              "with_consensus", "upserts"):
        v = r.get(k)
        assert isinstance(v, (int, float)) and v == 0, (
            f"build_month empty-path returned non-numeric/non-zero "
            f"{k}={v!r} — would crash accumulator")
    # sample_docs must be a list (caller iterates it)
    assert isinstance(r.get("sample_docs"), list)


@pytest.mark.asyncio
async def test_build_month_empty_avg_books_per_anchor_is_safe():
    """The exact field that broke the original NCAAF run."""
    db = _FakeDB()
    r = await bppc.build_month(
        db,
        league="NCAAF", month="2024-08",
        start_iso="2024-08-01", end_iso="2024-08-31",
        dry_run=True, player_name={},
        out_coll="sgo_ncaaf_research_core")
    assert "avg_books_per_anchor" in r
    assert r["avg_books_per_anchor"] == 0 or r["avg_books_per_anchor"] == 0.0


# ───────────────────────── caller defensiveness ─────────────────────────
def test_amain_logging_uses_get_with_defaults():
    """Belt-and-braces: scan the source of `amain()` and verify the
    monthly logging block uses `.get()` for every field that could be
    missing on a partial result dict. This locks in the post-fix
    behaviour so a future refactor can't reintroduce the bug.
    """
    src = inspect.getsource(bppc.amain)
    # The bug-triggering line was `f"avg_books/anchor={r['avg_books_per_anchor']}"`.
    # After the fix, no `r[` direct subscripts remain in the monthly
    # logging / accumulator block.
    forbidden = [
        "r['events']", 'r["events"]',
        "r['anchors']", 'r["anchors"]',
        "r['books_attached']", 'r["books_attached"]',
        "r['avg_books_per_anchor']", 'r["avg_books_per_anchor"]',
        "r['with_consensus']", 'r["with_consensus"]',
        "r['upserts']", 'r["upserts"]',
        "r['sample_docs']", 'r["sample_docs"]',
    ]
    found = [pat for pat in forbidden if pat in src]
    assert not found, (
        f"amain() still uses direct subscripts {found} on monthly "
        f"result dict — these will KeyError on empty-month results. "
        f"Switch to r.get(key, default).")
