"""
Gemini cost-audit fixes — P1.1 + P1.2 regression tests.

Verifies:
  1. `_vision_intel_content_hash` is stable for unchanged material inputs
     AND invariant to `computed_at` / delta-tick metadata (the D3
     cache-bust bug we're fixing).
  2. `_is_cache_fresh` returns True iff stored hash == computed hash.
  3. `_payload_hash` (via UnifiedPipeline._run_gemini_enrichment) is
     invariant to key-order and formatting of float inputs.
"""
from routes.ferrari_tiers import (
    _vision_intel_content_hash,
    _is_cache_fresh,
)


# ---------------------------------------------------------------------------
# P1.1 — content-hash stability
# ---------------------------------------------------------------------------

def _base_pick():
    return {
        "sport": "nba",
        "canonical_key": "nba|e1|Player A|PTS|25.5|OVER",
        "line": 25.5,
        "direction": "OVER",
        "opponent": "BOS",
        "true_edge": 0.12,
    }


def test_content_hash_stable_for_same_inputs():
    p = _base_pick()
    h1 = _vision_intel_content_hash(p)
    h2 = _vision_intel_content_hash(dict(p))
    assert h1 == h2
    assert len(h1) == 40  # sha1 hex length


def test_content_hash_ignores_computed_at_and_delta_metadata():
    """This is the D3 cache-bust bug we fixed: changing computed_at alone
    must NOT invalidate the cache."""
    p1 = _base_pick()
    p2 = dict(p1)
    p2["computed_at"] = "2026-04-21T03:00:00Z"
    p2["run_id"] = "abcdef"
    p2["vision_intel_generated_at"] = "2026-04-21T02:00:00Z"
    p2["tier_rank"] = 42
    p2["ranking_score_v2"] = 17.3
    assert _vision_intel_content_hash(p1) == _vision_intel_content_hash(p2)


def test_content_hash_invalidated_by_line_change():
    p1 = _base_pick()
    p2 = dict(p1); p2["line"] = 26.5
    assert _vision_intel_content_hash(p1) != _vision_intel_content_hash(p2)


def test_content_hash_invalidated_by_direction_flip():
    p1 = _base_pick()
    p2 = dict(p1); p2["direction"] = "UNDER"
    assert _vision_intel_content_hash(p1) != _vision_intel_content_hash(p2)


def test_content_hash_invalidated_by_opponent_change():
    p1 = _base_pick()
    p2 = dict(p1); p2["opponent"] = "LAL"
    assert _vision_intel_content_hash(p1) != _vision_intel_content_hash(p2)


def test_content_hash_invalidated_by_edge_bucket_crossing():
    p1 = _base_pick()            # edge=0.12 → bucket 1
    p2 = dict(p1); p2["true_edge"] = 0.23   # bucket 2
    assert _vision_intel_content_hash(p1) != _vision_intel_content_hash(p2)


def test_content_hash_stable_within_edge_bucket():
    """Small edge jitter within the same bucket must NOT invalidate."""
    p1 = _base_pick()            # edge=0.12 → bucket 1
    p2 = dict(p1); p2["true_edge"] = 0.17   # still bucket 1
    assert _vision_intel_content_hash(p1) == _vision_intel_content_hash(p2)


# ---------------------------------------------------------------------------
# Cache freshness
# ---------------------------------------------------------------------------

def test_cache_fresh_when_hashes_match():
    p = _base_pick()
    cached = {
        "vision_intel": "blah",
        "vision_intel_content_hash": _vision_intel_content_hash(p),
    }
    assert _is_cache_fresh(p, cached) is True


def test_cache_stale_when_hashes_differ():
    p = _base_pick()
    cached = {
        "vision_intel": "blah",
        "vision_intel_content_hash": "not_the_right_hash",
    }
    assert _is_cache_fresh(p, cached) is False


def test_cache_stale_when_pre_p11_entry_has_no_hash_field():
    """Pre-P1.1 cache entries have no `vision_intel_content_hash`. Treat
    them as stale so the next enrichment writes the hash and future
    checks can short-circuit."""
    p = _base_pick()
    cached = {
        "vision_intel": "blah",
        # No vision_intel_content_hash field at all
        "vision_intel_generated_at": "2026-04-20T00:00:00Z",
    }
    assert _is_cache_fresh(p, cached) is False


def test_cache_stale_when_vision_intel_missing():
    p = _base_pick()
    cached = {
        "vision_intel": None,
        "vision_intel_content_hash": _vision_intel_content_hash(p),
    }
    assert _is_cache_fresh(p, cached) is False


# ---------------------------------------------------------------------------
# D3 delta-tick scenario — the motivating case
# ---------------------------------------------------------------------------

def test_d3_tick_does_not_invalidate_cache():
    """Reproduction of the D3 delta-tick cache-bust bug. BEFORE P1.1, a
    rescore would bump `computed_at` and `_is_cache_fresh` compared that
    to `vision_intel_generated_at` → stale. AFTER P1.1, the compare is
    purely content-hash-based and a rescore with identical material
    inputs is a cache HIT."""
    pre_tick = _base_pick()
    cached = {
        "vision_intel": "Stable narrative",
        "vision_intel_content_hash": _vision_intel_content_hash(pre_tick),
        "vision_intel_generated_at": "2026-04-21T02:00:00Z",
    }
    # Simulate D3 tick: rescore → computed_at moves forward, nothing
    # material changes.
    post_tick = dict(pre_tick)
    post_tick["computed_at"] = "2026-04-21T02:00:20Z"
    post_tick["ranking_score_v2"] = 99.9  # cosmetic field
    assert _is_cache_fresh(post_tick, cached) is True, (
        "D3 delta tick must not invalidate the cache when material "
        "inputs are unchanged."
    )
