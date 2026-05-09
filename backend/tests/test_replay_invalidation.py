"""Tests for fingerprint hashes + invalidation rules covering the
matchup / injury layers added to the cache architecture."""
from __future__ import annotations

from services.replay.cache import (
    INVALIDATION_RULES,
    changed_components,
    fingerprint_block,
    injury_pipeline_hash,
    matchup_pipeline_hash,
    stale_cache_fields,
)


def test_fingerprint_block_includes_matchup_and_injury():
    fp = fingerprint_block("nba")
    assert "matchup_pipeline_hash" in fp
    assert "injury_pipeline_hash"  in fp
    assert isinstance(fp["matchup_pipeline_hash"], str)
    assert isinstance(fp["injury_pipeline_hash"],  str)


def test_matchup_pipeline_hash_is_stable():
    assert matchup_pipeline_hash() == matchup_pipeline_hash()


def test_injury_pipeline_hash_is_placeholder_when_not_implemented():
    """Spec: until injury layer ships, hash is a stable
    `not_implemented:vN` token (not 'missing:'). This lets the diff
    runner distinguish 'no injury wired yet' from 'pipeline changed'.
    """
    h = injury_pipeline_hash()
    # Either the placeholder token, OR a real sha1 if the file gets
    # added later. Both cases are valid; this test just enforces the
    # contract that the function never returns None / empty.
    assert h
    assert h.startswith("not_implemented:") or len(h) >= 32


def test_changed_components_detects_matchup_pipeline_change():
    before = fingerprint_block("nba")
    after  = {**before, "matchup_pipeline_hash": "deadbeef"}
    diffs = changed_components(before, after)
    assert "matchup_pipeline_hash" in diffs
    assert "vk2_model"       not in diffs
    assert "tp_engine_hash"  not in diffs


def test_changed_components_detects_injury_pipeline_change():
    before = fingerprint_block("nba")
    after  = {**before, "injury_pipeline_hash": "cafef00d"}
    assert "injury_pipeline_hash" in changed_components(before, after)


def test_invalidation_rules_gate_change_invalidates_nothing():
    """Hard architectural rule: changing gate thresholds reuses the
    cache 100%."""
    assert INVALIDATION_RULES["gate_config_hash"] == []
    assert stale_cache_fields(["gate_config_hash"]) == []


def test_invalidation_rules_matchup_change_invalidates_only_matchup():
    """Spec: matchup pipeline change → invalidate matchup cache only.
    Other cache fields stay reusable."""
    stale = stale_cache_fields(["matchup_pipeline_hash"])
    assert "matchup_blob" in stale
    assert "vk2_blob"     not in stale
    assert "tp_blob"      not in stale


def test_invalidation_rules_tp_change_invalidates_tp_blob_only():
    stale = stale_cache_fields(["tp_engine_hash"])
    assert set(stale) == {"tp_blob", "edge_pct"}


def test_invalidation_rules_per_family_vk2():
    stale = stale_cache_fields(["vk2_model.AST"])
    assert "vk2_blob" in stale
    assert "matchup_blob" not in stale
