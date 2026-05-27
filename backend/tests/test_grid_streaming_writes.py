"""
Streaming-writes contract test (2026-05-26).

Pins: the two grid-sweep scripts MUST NOT accumulate all cells in a
single in-memory `bulk` list. With ~3,000 combos × ~66 tier-fam groups
(or ~22 stat-family slices) that list is 100-600 MB resident — which
combined with the row dataset and motor's buffers blew past the 4 GiB
worker rlimit and got the script SIGKILL'd by the kernel. Symptom for
the operator: "optimizer 504'd and killed the OOM."

Fix: write cells to Mongo in batches of FLUSH_EVERY=1000 as they're
computed, then drop the buffer. Track running winners (best by HR,
best by Δ) on the fly so the post-loop report doesn't need the full
bulk list. Peak memory drops 100×.

Verified by source inspection. A behavioural test would require
seeding a real Mongo with ~30k replay rows and running the actual
script — slow & flaky in CI.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

BACKEND = Path("/app/backend")

STREAMING_SCRIPTS = [
    "scripts/sgo/historical_gate_replay_grid.py",
    "scripts/research/grid_sweep.py",
]


@pytest.mark.parametrize("rel_path", STREAMING_SCRIPTS)
def test_no_bulk_list_accumulator(rel_path: str):
    """The script MUST NOT append every cell to a single `bulk` list
    that persists past the sweep loop. Lookup for the explicit
    anti-pattern: `bulk: List[...]` declared at sweep scope + a
    post-loop `for i in range(0, len(bulk), 1000)` slice-write."""
    src = (BACKEND / rel_path).read_text()
    # Strip comment lines so the test doesn't false-positive on the
    # doc comment explaining the OLD pattern that was removed.
    src_no_comments = "\n".join(
        ln for ln in src.splitlines()
        if not re.match(r"\s*#", ln) and "# " not in ln[:80]
    )
    # Old anti-pattern: declaring a `bulk: List[Dict...]` before the
    # sweep loop then slice-writing it after. Allow `write_buffer`
    # (the new streaming buffer, which is bounded by FLUSH_EVERY).
    assert not re.search(r"\bbulk\s*:\s*List\[", src_no_comments), (
        f"{rel_path} still has a top-level `bulk: List[...]` "
        f"accumulator. Switch to streaming writes (FLUSH_EVERY=1000) "
        f"so peak memory doesn't OOM the worker.")
    # And there must be no `len(bulk)` reference left (would prove
    # the bulk list survived past the loop).
    leftover_len_bulk = re.findall(r"len\(\s*bulk\s*\)", src_no_comments)
    assert not leftover_len_bulk, (
        f"{rel_path} still references len(bulk) — proves the bulk "
        f"accumulator is in use. Switch to streaming writes.")


@pytest.mark.parametrize("rel_path", STREAMING_SCRIPTS)
def test_has_streaming_writes(rel_path: str):
    """The script MUST flush to Mongo in batches inside the sweep
    loop, not after."""
    src = (BACKEND / rel_path).read_text()
    assert "FLUSH_EVERY" in src, (
        f"{rel_path} missing FLUSH_EVERY constant — should batch "
        f"writes to bound peak memory")
    assert "write_buffer" in src, (
        f"{rel_path} missing write_buffer streaming buffer")
    # The flush must happen INSIDE the loop, not just at the end.
    assert re.search(r"if\s+len\(\s*write_buffer\s*\)\s*>=", src), (
        f"{rel_path}: must check `len(write_buffer) >= FLUSH_EVERY` "
        f"inside the sweep loop and flush. Otherwise the buffer "
        f"grows without bound and we're back to the OOM scenario.")
    # And a final flush after the loop.
    assert re.search(r"await\s+_flush\s*\(\s*\)", src), (
        f"{rel_path}: must call `await _flush()` after the sweep "
        f"loop to drain the partial last batch")


@pytest.mark.parametrize("rel_path", STREAMING_SCRIPTS)
def test_best_per_group_tracked_on_the_fly(rel_path: str):
    """The post-loop "best per (tier, family)" / "best per side" /
    "best per stat_family" reports MUST be tracked on the fly during
    the sweep — NOT computed by re-scanning a stored cell list at
    end-of-run (which would force us to keep the list around)."""
    src = (BACKEND / rel_path).read_text()
    # Look for the in-loop winner-tracking pattern.
    assert ("best_per_pair_dl" in src or "fam_best" in src), (
        f"{rel_path}: must track best-cell-per-group on the fly")
    # And: NO scan of `fam_cells` or `side_cells` lists post-loop.
    bad_patterns = [
        r"for\s+c\s+in\s+fam_cells\s*:",
        r"for\s+c\s+in\s+side_cells\s*:",
        r"for\s+c\s+in\s+bulk\s*:",
    ]
    for pat in bad_patterns:
        assert not re.search(pat, src), (
            f"{rel_path} still scans a stored cell list (pattern: "
            f"{pat}). Track winners on the fly inside the sweep "
            f"loop and discard each cell after it's written.")
