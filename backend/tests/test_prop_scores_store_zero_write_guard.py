"""
0-write guard test for `services.scoring.prop_scores_store.write_versioned_scores`.

Pinned contract (2026-05-08 P0 fix):

    mode='replace' + empty score_docs → DO NOT delete existing rows
                                         → return early with
                                           skipped_stale_sweep=True

    mode='replace' + non-empty score_docs → existing stale-sweep
                                            behavior preserved (only
                                            keys NOT in the new set
                                            are deleted)

This guard protects production version tags (final-nba / final-nba-rt /
final-mlb / final-mlb-rt) from being wiped during upstream-odds
blackouts. Verified incident 2026-05-08T01:15:55Z: master_sync ran
during an upstream outage, scoring produced 0 docs, replace-mode
swept 4,431 NBA score docs.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

_BACKEND_ROOT = Path("/app/backend")
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ─── Minimal async fake mongo — just enough surface for the guard ───
class _FakeColl:
    """Captures the mutating calls write_versioned_scores would make."""

    def __init__(self, initial_docs: List[Dict[str, Any]] | None = None):
        self.docs: List[Dict[str, Any]] = list(initial_docs or [])
        self.calls: List[str] = []

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    async def delete_many(self, query):
        self.calls.append(f"delete_many:{query}")
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, query)]
        class _R:
            deleted_count = before - len(self.docs) if before else 0
        return _R()

    async def bulk_write(self, ops, ordered=False):
        self.calls.append(f"bulk_write:{len(ops)}")
        # Minimal handling — we only assert _whether_ it was called,
        # not what it inserted.
        class _R:
            upserted_count = len(ops)
            modified_count = 0
        return _R()

    async def create_index(self, *args, **kwargs):
        return None

    async def drop_index(self, *args, **kwargs):
        return None

    async def index_information(self):
        return {}


def _matches(row, query):
    for k, v in query.items():
        if isinstance(v, dict):
            for op, opv in v.items():
                if op == "$nin" and row.get(k) in opv:
                    return False
                if op == "$in" and row.get(k) not in opv:
                    return False
                if op == "$exists" and (k in row) != bool(opv):
                    return False
        else:
            if row.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self, mapping):
        self._m = mapping

    def __getitem__(self, name):
        return self._m.setdefault(name, _FakeColl())


def _seed_existing_docs(n: int, version_tag: str) -> List[Dict[str, Any]]:
    return [
        {
            "canonical_key": f"nba|seed|p{i}|PTS|10.5|OVER",
            "version_tag":   version_tag,
            "tier":          "front_lines",
            "active":        True,
            "vision_score":  70.0,
        }
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────
# 1. Empty replace batch + existing rows → existing rows PRESERVED
# ─────────────────────────────────────────────────────────────────────
def test_empty_replace_does_not_delete_existing_rows():
    from services.scoring.prop_scores_store import write_versioned_scores

    seeded = _seed_existing_docs(50, "final-nba-rt")
    coll = _FakeColl(initial_docs=list(seeded))
    db = _FakeDB({"nba_prop_scores": coll})

    result = asyncio.run(write_versioned_scores(
        db, sport="nba", score_docs=[], version_tag="final-nba-rt",
        mode="replace",
    ))

    # Critical: NO delete_many call was issued for the version_tag.
    delete_calls = [c for c in coll.calls if c.startswith("delete_many")]
    assert delete_calls == [], (
        f"replace with empty batch must not delete; observed: {delete_calls}"
    )
    # Original rows untouched.
    assert len(coll.docs) == 50
    # Result reports the guard fired.
    assert result["written"] == 0
    assert result["stale_swept"] == 0
    assert result.get("skipped_stale_sweep") is True
    assert result.get("skipped_reason") == "empty_batch_zero_write_guard"


# ─────────────────────────────────────────────────────────────────────
# 2. Non-empty replace batch → stale-sweep still runs (no regression)
# ─────────────────────────────────────────────────────────────────────
def test_nonempty_replace_still_sweeps_stale_keys():
    from services.scoring.prop_scores_store import write_versioned_scores

    # Seed with 5 existing docs, 2 of which the new batch will cover.
    seeded = _seed_existing_docs(5, "final-nba-rt")
    coll = _FakeColl(initial_docs=list(seeded))
    db = _FakeDB({"nba_prop_scores": coll})

    new_batch = [
        {
            "canonical_key": "nba|seed|p0|PTS|10.5|OVER",
            "version_tag":   "final-nba-rt",
            "vision_score":  72.0,
            "tier":          "front_lines",
            "active":        True,
            # Required identity fields the projector reads:
            "player_name":   "Player 0",
            "stat_type":     "PTS",
            "line":          10.5,
            "recommendation": "OVER",
            "sport":         "nba",
            "event_id":      "evt0",
        },
        {
            "canonical_key": "nba|seed|p1|PTS|10.5|OVER",
            "version_tag":   "final-nba-rt",
            "vision_score":  73.0,
            "tier":          "safe_haven",
            "active":        True,
            "player_name":   "Player 1",
            "stat_type":     "PTS",
            "line":          10.5,
            "recommendation": "OVER",
            "sport":         "nba",
            "event_id":      "evt0",
        },
    ]

    result = asyncio.run(write_versioned_scores(
        db, sport="nba", score_docs=new_batch, version_tag="final-nba-rt",
        mode="replace",
    ))

    # The stale sweep MUST have been issued (delete_many for keys
    # NOT in the new set).
    delete_calls = [c for c in coll.calls if c.startswith("delete_many")]
    assert len(delete_calls) >= 1, (
        f"non-empty replace must issue stale_sweep; observed: {coll.calls}"
    )
    # Guard flag NOT set (we want the sweep to run).
    assert result.get("skipped_stale_sweep") is not True
    # bulk_write was issued.
    bw_calls = [c for c in coll.calls if c.startswith("bulk_write")]
    assert len(bw_calls) == 1


# ─────────────────────────────────────────────────────────────────────
# 3. dry_run still wins — early return regardless of guard
# ─────────────────────────────────────────────────────────────────────
def test_dry_run_returns_early_unaffected_by_guard():
    from services.scoring.prop_scores_store import write_versioned_scores

    coll = _FakeColl(initial_docs=_seed_existing_docs(3, "final-nba-rt"))
    db = _FakeDB({"nba_prop_scores": coll})

    result = asyncio.run(write_versioned_scores(
        db, sport="nba", score_docs=[], version_tag="final-nba-rt",
        mode="replace", dry_run=True,
    ))
    assert result["dry_run"] is True
    assert coll.calls == []  # nothing called


# ─────────────────────────────────────────────────────────────────────
# 4. mode=upsert with empty batch is also a no-op (no regression)
# ─────────────────────────────────────────────────────────────────────
def test_upsert_with_empty_batch_is_noop():
    from services.scoring.prop_scores_store import write_versioned_scores

    coll = _FakeColl(initial_docs=_seed_existing_docs(7, "final-nba-rt"))
    db = _FakeDB({"nba_prop_scores": coll})
    result = asyncio.run(write_versioned_scores(
        db, sport="nba", score_docs=[], version_tag="final-nba-rt",
        mode="upsert",
    ))
    delete_calls = [c for c in coll.calls if c.startswith("delete_many")]
    assert delete_calls == []
    assert len(coll.docs) == 7
    assert result["written"] == 0
