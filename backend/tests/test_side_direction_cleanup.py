"""Regression tests for 2026-05-17 cleanup:

  1. Top-level `side` / `direction` fields populated on every score
     doc from the canonical SSOT (`recommendation`, with fallback to
     the `canonical_key` `|OVER`/`|UNDER` suffix).

  2. Legacy direction-cushion keys stripped from threshold config:
       • max_projection_minus_line
       • min_line_minus_projection_ratio
       • min_projection_minus_line
       • min_projection_to_line_ratio
     The strict engine ignored them at runtime — this cleanup removes
     them from the declarative config so audits don't show misleading
     thresholds.

These tests pin the contract. They DO NOT modify gate behaviour.
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/app/backend")

import pytest

from services.scoring.prop_scores_store import _project_score_doc
from services.scoring.gates.thresholds import resolve_thresholds


# ─────────────────────────────────────────────────────────────────────
# (1) Top-level side/direction populated by writer
# ─────────────────────────────────────────────────────────────────────
def _ctx(**overrides):
    base = {
        "canonical_key": "mlb|abc|Tester|Hits|1.5|OVER",
        "sport": "mlb",
        "event_id": "abc",
        "player_name": "Tester",
        "stat_type": "Hits",
        "line": 1.5,
        "recommendation": "OVER",
    }
    base.update(overrides)
    return base


def test_side_direction_populated_from_recommendation():
    doc = _project_score_doc(_ctx(recommendation="OVER"),
                             version_tag="t", computed_at="ts")
    assert doc.get("side") == "OVER"
    assert doc.get("direction") == "OVER"
    assert doc.get("recommendation") == "OVER"


def test_side_direction_under_passthrough():
    doc = _project_score_doc(
        _ctx(recommendation="UNDER",
             canonical_key="mlb|abc|Tester|Hits|1.5|UNDER"),
        version_tag="t", computed_at="ts",
    )
    assert doc.get("side") == "UNDER"
    assert doc.get("direction") == "UNDER"


def test_side_direction_normalised_uppercase():
    """Adapters that emit lowercase still produce upper-cased fields."""
    doc = _project_score_doc(_ctx(recommendation="over"),
                             version_tag="t", computed_at="ts")
    assert doc.get("side") == "OVER"
    assert doc.get("direction") == "OVER"


def test_side_direction_fallback_to_canonical_key_suffix():
    """If `recommendation` is missing (legacy adapter row), parse the
    canonical_key suffix as the SSOT fallback."""
    ctx = _ctx()
    del ctx["recommendation"]
    ctx["canonical_key"] = "mlb|abc|Tester|Hits|1.5|UNDER"
    doc = _project_score_doc(ctx, version_tag="t", computed_at="ts")
    assert doc.get("side") == "UNDER"
    assert doc.get("direction") == "UNDER"


def test_side_direction_absent_when_no_signal():
    """Neither `recommendation` nor a parseable canonical_key suffix
    available → don't fabricate a side."""
    ctx = _ctx()
    del ctx["recommendation"]
    ctx["canonical_key"] = "mlb|abc|Tester|Hits|1.5"  # no |OVER suffix
    doc = _project_score_doc(ctx, version_tag="t", computed_at="ts")
    assert doc.get("side") is None
    assert doc.get("direction") is None


def test_canonical_key_suffix_ignored_when_recommendation_present():
    """`recommendation` takes priority over canonical_key suffix —
    if they disagree (data drift) the adapter value wins.
    """
    doc = _project_score_doc(
        _ctx(recommendation="OVER",
             canonical_key="mlb|abc|Tester|Hits|1.5|UNDER"),
        version_tag="t", computed_at="ts",
    )
    assert doc.get("side") == "OVER"
    assert doc.get("direction") == "OVER"


# ─────────────────────────────────────────────────────────────────────
# (2) Legacy cushion keys stripped from threshold config
# ─────────────────────────────────────────────────────────────────────
LEGACY_KEYS = (
    "max_projection_minus_line",
    "min_line_minus_projection_ratio",
    "min_projection_minus_line",
    "min_projection_to_line_ratio",
)


@pytest.fixture(params=[
    ("nba", "safe_haven",  "pts",  "OVER"),
    ("nba", "front_lines", "pts",  "OVER"),
    ("nba", "front_lines", "pts",  "UNDER"),
    ("nba", "war_zone",    "pts",  "OVER"),
    ("nba", "safe_haven",  "pts",  "UNDER"),
    ("mlb", "front_lines", "hits", "OVER"),
    ("mlb", "front_lines", "hits", "UNDER"),
])
def cfg(request):
    sport, tier, stat, side = request.param
    return resolve_thresholds(sport, tier, stat, side=side)


def test_direction_gate_carries_only_sign_only_keys(cfg):
    dg = cfg.get("direction_gate")
    if dg is None:
        # Some tier+side combos legitimately have no direction gate.
        return
    for legacy in LEGACY_KEYS:
        assert legacy not in dg, (
            f"Legacy cushion key {legacy!r} must be absent from "
            f"direction_gate after the 2026-05-17 cleanup. Found: {dg}"
        )
    # The strict engine reads only `applies_to_sides`. Must still be
    # present so the gate routes side correctly.
    assert "applies_to_sides" in dg


# pytest is imported at the top of the file alongside the other imports.
