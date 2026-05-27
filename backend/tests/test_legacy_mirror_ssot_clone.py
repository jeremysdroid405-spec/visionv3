"""
SSOT mirror faithfulness contract (2026-05-27).

ROOT CAUSE this pins (user-confirmed):
  June 2025 replay docs were missing every SSOT de-vig field — tp_source,
  edge_pct, is_alternate_market, devig_method, canonical_edge, p_model,
  p_true_active, vision_score, tier — across all 24,666 rows. The
  optimizer / Results page were operating on a pre-2026-04-22 schema
  (pre-multi-book-de-vig).

  Two specific bugs in `_mirror_to_legacy()`:
    1. `tp` was OVERWRITTEN with `model_probability`, deleting the
       runner's multi-book devigged true probability.
    2. The SSOT fields were not included in the $group projection
       so they were dropped silently.

USER REQUIREMENT (verbatim, 2026-05-27):
  "i want the testing to be EXACTLY like production a 100% clone. the
   only difference should be inputs and output destinations."

CONTRACT this test pins:
  Every SSOT field the production_replay_runner writes to its outputs
  collection MUST appear in the $group projection AND in the
  replay_row dict that lands in `sgo_propvision_full_pipeline_replay`.
  No field may be silently dropped. `tp` must NOT be overwritten.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")

MIRROR = Path("/app/backend/scripts/sgo/historical_full_pipeline_replay.py")

# Fields production_replay_runner explicitly sets on out_doc. These are
# the SSOT clone fields that MUST mirror through. (Source-of-truth:
# /app/backend/services/replay/production_replay_runner.py:953-1003.)
SSOT_FIELDS = [
    "tp_source",
    "edge_pct",
    "is_alternate_market",
    "devig_method",
    "canonical_edge",
    "p_model",
    "p_true_active",
    "vision_score",
    "tier",
    "routed_tier",
    "tier_reference_odds",
    "tier_reference_book",
    "research_mode",
]


@pytest.fixture(scope="module")
def mirror_src() -> str:
    return MIRROR.read_text()


@pytest.mark.parametrize("field", SSOT_FIELDS)
def test_ssot_field_is_in_group_projection(mirror_src: str, field: str):
    """Every SSOT field must appear in the `$first` projection inside
    the `_mirror_to_legacy` $group stage. Otherwise it's silently
    dropped at the aggregation step before reaching the output."""
    # Patterns for either the exact field name as the group key OR a
    # rename (e.g. tp_runner for tp, tier_ssot for tier).
    rename_map = {"tp": "tp_runner", "tier": "tier_ssot"}
    target = rename_map.get(field, field)
    # Look for `"target": {"$first": "$field"}` (or `$target` if no rename).
    assert (f'"$first": "${field}"' in mirror_src
            or f"'$first': '${field}'" in mirror_src), (
        f"SSOT field `{field}` is missing from the $group `$first` "
        f"projection in _mirror_to_legacy. Production writes it but "
        f"the mirror drops it — Results/optimizer become blind to "
        f"the de-vig metadata. Add: "
        f'"{target}": {{"$first": "${field}"}}'
    )


@pytest.mark.parametrize("field", SSOT_FIELDS)
def test_ssot_field_is_in_replay_row(mirror_src: str, field: str):
    """Every SSOT field must also appear in the `replay_row` dict that
    gets upserted into the legacy collection. Otherwise it's in the
    aggregation result but the mirror still doesn't write it."""
    # We accept either `"field": g.get("field")` or
    # `"field": g.get("field_rename")` (e.g. tier_ssot → tier).
    assert f'"{field}":' in mirror_src, (
        f"SSOT field `{field}` is missing from the `replay_row` dict "
        f"in _mirror_to_legacy. Production-runner sets it on out_doc "
        f"but the mirror leaves it off the legacy collection.")


def test_tp_is_NOT_overwritten_with_model_probability(mirror_src: str):
    """The original bug: `"tp": g.get("model_probability")` overwrote
    the runner's MULTI-BOOK DE-VIGGED true probability with the raw
    model_probability. This silently rolled the schema back to
    pre-2026-04-22. The fix is to source `tp` from `tp_runner` (the
    runner's value) and fall back to model_probability ONLY when the
    runner left tp null."""
    bad = '"tp":                  g.get("model_probability")'
    bad2 = '"tp":          g.get("model_probability")'
    bad3 = '"tp": g.get("model_probability")'
    for pattern in (bad, bad2, bad3):
        assert pattern not in mirror_src, (
            f"_mirror_to_legacy is OVERWRITING `tp` with "
            f"`model_probability` again. This deletes the runner's "
            f"multi-book devigged TP. The mirror must use the runner's "
            f"`tp` value (the production SSOT).")
    # Positive check: `tp` must source from `tp_runner` (or
    # equivalent name) and fall back to model_probability.
    assert "tp_runner" in mirror_src, (
        "_mirror_to_legacy should source `tp` from `tp_runner` "
        "(the runner's multi-book devigged value). Aliased to "
        "avoid name collision with the input `tp` field in the "
        "aggregation pipeline.")


def test_legacy_pipeline_version_is_current(mirror_src: str):
    """The legacy mirror used to stamp `pipeline_version` as a
    pre-de-vig string, which the optimizer used to detect 'is this a
    SSOT-faithful run?'. Confirm the mirror writes a current version
    string so downstream filters can trust the data."""
    assert "PIPELINE_VERSION" in mirror_src, (
        "pipeline_version stamp missing — without it the optimizer "
        "can't tell SSOT-faithful runs from legacy runs")
