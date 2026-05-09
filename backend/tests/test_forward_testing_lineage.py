"""Regression tests for the 2026-05-09 forward-testing lineage filter.

Locks down the modern-SSOT cutoff boundary so future changes can't
silently re-introduce pre-2026-04-25 legacy `vk_*` ranker data into
official ROI / calibration / performance dashboards.
"""
from __future__ import annotations

import pytest

from services.forward_testing_lineage import (
    LEGACY_GENERATION,
    MIXED_GENERATION,
    MIXED_GENERATION_WARNING,
    MODERN_GENERATION,
    MODERN_SSOT_CUTOFF,
    MODERN_SSOT_CUTOFF_ISO,
    lineage_filter,
    merge_filter,
)


# --------------------------------------------------------------------------
# 1. Cutoff constants are pinned (anti-regression)
# --------------------------------------------------------------------------
def test_modern_ssot_cutoff_is_2026_04_25():
    assert MODERN_SSOT_CUTOFF == "2026-04-25"
    assert MODERN_SSOT_CUTOFF_ISO == "2026-04-25T00:00:00Z"


def test_generation_labels_are_pinned():
    assert LEGACY_GENERATION == "legacy_vk"
    assert MODERN_GENERATION == "modern_ssot"
    assert MIXED_GENERATION == "mixed"


def test_mixed_warning_is_present_and_explicit():
    assert "legacy_vk" in MIXED_GENERATION_WARNING
    assert "modern_ssot" in MIXED_GENERATION_WARNING
    assert "not statistically comparable" in MIXED_GENERATION_WARNING


# --------------------------------------------------------------------------
# 2. lineage_filter — default & override
# --------------------------------------------------------------------------
def test_default_filter_excludes_legacy():
    f = lineage_filter()
    assert f == {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}}


def test_default_filter_with_explicit_false():
    f = lineage_filter(include_legacy=False)
    assert f == {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}}


def test_include_legacy_returns_empty_filter():
    f = lineage_filter(include_legacy=True)
    assert f == {}


# --------------------------------------------------------------------------
# 3. merge_filter — composes with caller's existing match clauses
# --------------------------------------------------------------------------
def test_merge_filter_adds_cutoff_to_empty_base():
    out = merge_filter(None, include_legacy=False)
    assert out == {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}}


def test_merge_filter_preserves_unrelated_keys():
    out = merge_filter({"sport": "nba", "tier": "front_lines"}, include_legacy=False)
    assert out == {
        "sport": "nba",
        "tier": "front_lines",
        "capture_date": {"$gte": MODERN_SSOT_CUTOFF},
    }


def test_merge_filter_respects_caller_more_restrictive_lower_bound():
    """If caller already passed `capture_date >= 2026-05-01` and the
    cutoff is `2026-04-25`, the merged lower bound must stay at
    `2026-05-01` (the more restrictive of the two)."""
    out = merge_filter({"capture_date": {"$gte": "2026-05-01"}}, include_legacy=False)
    assert out == {"capture_date": {"$gte": "2026-05-01"}}


def test_merge_filter_lifts_caller_lower_bound_when_below_cutoff():
    """If caller passed `capture_date >= 2026-04-01` (pre-cutoff), the
    merged result must lift the lower bound to the cutoff."""
    out = merge_filter({"capture_date": {"$gte": "2026-04-01"}}, include_legacy=False)
    assert out == {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}}


def test_merge_filter_preserves_upper_bound():
    """If caller already passed an upper bound, the cutoff must not
    overwrite it."""
    out = merge_filter(
        {"capture_date": {"$lt": "2026-05-08"}},
        include_legacy=False,
    )
    assert out == {
        "capture_date": {
            "$lt": "2026-05-08",
            "$gte": MODERN_SSOT_CUTOFF,
        }
    }


def test_merge_filter_include_legacy_returns_caller_unchanged():
    base = {"sport": "nba", "capture_date": {"$gte": "2026-04-01"}}
    out = merge_filter(base, include_legacy=True)
    assert out == base


def test_merge_filter_handles_scalar_capture_date_via_and():
    """Defensive: a caller passing `capture_date='2026-04-01'` (scalar
    rather than range) must not get overwritten — wrap in $and."""
    out = merge_filter({"capture_date": "2026-04-01"}, include_legacy=False)
    assert "$and" in out
    assert {"capture_date": "2026-04-01"} in out["$and"]
    assert {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}} in out["$and"]


# --------------------------------------------------------------------------
# 4. Mutation guard — flipping the cutoff date in-test must NOT silently
# disable the boundary.
# --------------------------------------------------------------------------
def test_filter_uses_module_constant_so_changes_propagate():
    """If a future refactor hard-codes the date elsewhere, this test
    will surface the divergence."""
    out = lineage_filter()
    cutoff_in_filter = out["capture_date"]["$gte"]
    assert cutoff_in_filter == MODERN_SSOT_CUTOFF, (
        f"lineage_filter is using a hard-coded date "
        f"({cutoff_in_filter!r}) instead of MODERN_SSOT_CUTOFF "
        f"({MODERN_SSOT_CUTOFF!r}). The cutoff has bifurcated."
    )


# --------------------------------------------------------------------------
# 5. Async lineage_metadata — uses an in-memory fake collection so the
# test is deterministic + no DB needed.
# --------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, total: int):
        self._total = total

    async def to_list(self, length=None):
        return []  # not used


class _FakeColl:
    """Counts documents using a Python predicate matched against the
    Mongo-style `$lt` / `$gte` clauses on `capture_date`."""

    def __init__(self, dates):
        self._dates = list(dates)

    async def count_documents(self, q):
        cd = q.get("capture_date") or {}
        out = 0
        for d in self._dates:
            ok = True
            if "$lt" in cd and not (d < cd["$lt"]):  ok = False
            if "$gte" in cd and not (d >= cd["$gte"]): ok = False
            if ok: out += 1
        return out


class _FakeDB:
    def __init__(self, dates):
        self._coll = _FakeColl(dates)

    def __getitem__(self, _name):
        return self._coll


@pytest.mark.asyncio
async def test_metadata_modern_only_when_legacy_excluded():
    from services.forward_testing_lineage import lineage_metadata
    db = _FakeDB(["2026-04-13", "2026-04-14", "2026-04-25", "2026-05-01"])
    meta = await lineage_metadata(db, "x", base_match=None, include_legacy=False)
    assert meta["dataset_generation"] == MODERN_GENERATION
    assert meta["row_counts"]["legacy_vk"] == 2
    assert meta["row_counts"]["modern_ssot"] == 2
    assert meta["row_counts"]["excluded_from_official_reporting"] == 2
    assert meta["warning"] is None


@pytest.mark.asyncio
async def test_metadata_mixed_generation_emits_warning():
    from services.forward_testing_lineage import lineage_metadata
    db = _FakeDB(["2026-04-13", "2026-04-25"])
    meta = await lineage_metadata(db, "x", base_match=None, include_legacy=True)
    assert meta["dataset_generation"] == MIXED_GENERATION
    assert meta["warning"] == MIXED_GENERATION_WARNING
    assert meta["row_counts"]["excluded_from_official_reporting"] == 0


@pytest.mark.asyncio
async def test_metadata_legacy_only_no_warning():
    """If a caller asks for legacy and there's no modern data, that's
    not 'mixed' — it's just legacy. No warning needed."""
    from services.forward_testing_lineage import lineage_metadata
    db = _FakeDB(["2026-04-13", "2026-04-14"])
    meta = await lineage_metadata(db, "x", base_match=None, include_legacy=True)
    assert meta["dataset_generation"] == LEGACY_GENERATION
    assert meta["warning"] is None
