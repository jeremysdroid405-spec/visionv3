"""
Universal SSOT Overwrite Firewall — contract tests
==================================================

Five contracts (matches the 5 user-supplied requirements):

  1. cached_board cannot overwrite owned prop fields
  2. route merge cannot overwrite owned fields
  3. frontend does not prefer legacy aliases
  4. preserve-on-replace fields survive full recompute
  5. unknown aliases are rejected

These tests pin the firewall ON. They run on the registry surface
directly so they cannot be silenced by patching one specific overlay
site.
"""
from __future__ import annotations

import sys
import pytest

sys.path.insert(0, "/app/backend")


# ── Contract 1 ───────────────────────────────────────────────────────
def test_cached_board_cannot_overwrite_owned_prop_fields():
    """`safe_overlay()` must block any owned-field write from a
    non-owner source. Daniss-Jenkins-style cross-line leak: a
    cached_board entry at line=9.5 carries `hit_rate_l10=60`, the
    score doc at line=14.5 has the canonical `hit_rate_l10=20`. The
    firewall must keep the canonical value."""
    from services.field_ownership.firewall import safe_overlay

    # Canonical SSOT values (score-doc origin).
    target = {
        "player_name":  "Daniss Jenkins",
        "line":         14.5,
        "stat_type":    "P+A",
        "recommendation": "OVER",
        "hit_rate_l5":  20.0,
        "hit_rate_l10": 20.0,
        "hit_rate_l20": 60.0,
        "tier":         "war_zone",
        "p_true_active": 0.62,
    }
    # Cross-line cached_board source (line=9.5 entry).
    cached = {
        "hit_rate_l5":   None,        # would be no-op anyway
        "hit_rate_l10":  60,          # ← would clobber canonical
        "hit_rate_l20":  None,
        "tier":          "front_lines",  # ← would mutate tier
        "h10_rate":      60,          # legacy alias (also protected)
        "season_avg":    11.5,        # NOT owned → allowed (sticky-write applies)
    }

    metrics = safe_overlay(target, cached)

    assert target["hit_rate_l10"] == 20.0, "owned hit_rate_l10 was overwritten"
    assert target["hit_rate_l20"] == 60.0, "owned hit_rate_l20 was overwritten"
    assert target["tier"] == "war_zone", "owned tier was overwritten"
    assert metrics["blocked"] >= 2, f"firewall did not block owned writes: {metrics}"


# ── Contract 2 ───────────────────────────────────────────────────────
def test_route_merge_cannot_overwrite_owned_fields_assertion():
    """`assert_no_owned_overwrite()` must raise when a non-owner
    layer mutates an owned field between two snapshots."""
    from services.field_ownership.firewall import (
        assert_no_owned_overwrite,
        OwnedFieldOverwriteError,
    )

    before = {
        "player_name":  "Daniss Jenkins",
        "hit_rate_l10": 20.0,
        "tier":         "war_zone",
    }
    after_clean = {**before, "vision_intel": "..."}        # only enrichment
    after_dirty = {**before, "hit_rate_l10": 60, "tier": "war_zone"}

    # Clean diff → no raise.
    assert_no_owned_overwrite(before, after_clean, context="clean")

    # Dirty diff → must raise with the offending field surfaced.
    with pytest.raises(OwnedFieldOverwriteError) as excinfo:
        assert_no_owned_overwrite(before, after_dirty, context="dirty")
    assert "hit_rate_l10" in str(excinfo.value)
    assert "20.0 -> 60" in str(excinfo.value)


# ── Contract 3 ───────────────────────────────────────────────────────
def test_frontend_does_not_prefer_legacy_aliases_over_canonical():
    """Static guard: every UserVisibleCard surface that reads a
    side-aware hit-rate window must read the canonical `hit_rate_l*`
    field FIRST. Implementation: parse the UniversalPlayerCard chip
    sites and assert canonical comes before legacy in the JS
    nullish-coalescing chain."""
    from pathlib import Path

    card = Path("/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx").read_text()

    # Every chip site must consult `hit_rate_l10` BEFORE `h10_rate`.
    # We assert a substring shape that pins the preference order.
    expected_orderings = (
        "prop.hit_rate_l10 ?? prop.h10_rate",
        "hit_rate_l10 ?? _legacy_h10_rate",
    )
    for ordering in expected_orderings:
        assert ordering in card, (
            f"Frontend chip is reading legacy alias before canonical. "
            f"Expected `{ordering}` in UniversalPlayerCard.jsx but did "
            f"not find it. SSOT contract requires canonical first."
        )

    # Strictly: no remaining bare `prop.h10_rate` reads in display
    # contexts (legacy fallback only allowed inside the
    # nullish-coalescing chain we just asserted).
    bad_reads = [
        ln for ln in card.splitlines()
        if "prop.h10_rate" in ln
        and "hit_rate_l10" not in ln
        and "data-testid" not in ln
        and "//" not in ln.split("prop.h10_rate")[0][-3:]
    ]
    assert not bad_reads, (
        f"Bare `prop.h10_rate` reads found (must combine with canonical "
        f"`prop.hit_rate_l10`):\n  " + "\n  ".join(bad_reads[:5])
    )


# ── Contract 4 ───────────────────────────────────────────────────────
def test_preserve_on_replace_fields_survive_full_recompute():
    """`prop_scores_store` carries an enrichment-allowlist of fields
    that must NOT be wiped when `write_versioned_scores(mode="replace")`
    runs. Verify the canonical preserve-on-replace list is intact."""
    from services.scoring.prop_scores_store import _PRESERVE_ON_REPLACE

    # Hard floor: every enrichment-only field that has bitten us
    # historically must be on the list.
    required = {
        "vision_intel",
        "vision_intel_content_hash",
        "vision_intel_generated_at",
        "momentum_data",
    }
    missing = required - set(_PRESERVE_ON_REPLACE)
    assert not missing, (
        f"PRESERVE-ON-REPLACE allowlist missing required enrichment "
        f"fields: {missing}. A full recompute would wipe them."
    )


# ── Contract 5 ───────────────────────────────────────────────────────
def test_unknown_aliases_are_rejected():
    """`validate_score_doc()` must flag any key not in the registered
    allowlist. Catches silent drift when a new writer invents an alias."""
    from services.field_ownership.validators import validate_score_doc

    # A doc that satisfies all required fields but adds an unknown
    # alias `hit_rate_clutch` (made up). The allowlist passed to
    # `validate_score_doc` is the union of legitimate keys; anything
    # outside is reported.
    allowlist = {
        # Required fail-loud fields
        "active", "event_id", "line", "p_true_active", "tier",
        # Legitimate non-required fields
        "scored_at", "computed_at",
    }
    doc = {
        "active":         True,
        "event_id":       "evt_1",
        "line":           14.5,
        "p_true_active":  0.62,
        "tier":           "war_zone",
        "hit_rate_clutch": 0.99,  # ← unknown alias
    }
    violations = validate_score_doc(doc, allowlist=allowlist)
    flagged = [v for v in violations if "hit_rate_clutch" in v]
    assert flagged, (
        f"Unknown alias `hit_rate_clutch` not flagged by "
        f"validate_score_doc. Violations seen: {violations}"
    )


# ── Bonus: registry → firewall surface integrity ─────────────────────
def test_protected_field_names_includes_every_registered_owned_field():
    """The firewall surface must cover every registered field. Adding
    a new field to the registry MUST automatically enroll it in the
    firewall — no manual sync."""
    from services.field_ownership.firewall import protected_field_names
    from services.field_ownership.registry import FIELD_REGISTRY

    protected = protected_field_names()
    # Every public name OR its storage-key projection must be in
    # `protected`. We accept either form (storage map handles
    # rename cases).
    missing = []
    for fname, spec in FIELD_REGISTRY.items():
        owner_key = spec.owner_field.split(".")[-1]
        if fname not in protected and owner_key not in protected:
            missing.append((fname, owner_key))
    assert not missing, (
        f"Registry has fields the firewall is NOT protecting: {missing}. "
        f"Update _PUBLIC_TO_STORAGE in firewall.py."
    )


def test_safe_overlay_owner_layer_escape_hatch_writes_unconditionally():
    """`owner_layer=True` is the escape hatch for the canonical writer.
    It bypasses sticky-write AND the protected-field block."""
    from services.field_ownership.firewall import safe_overlay

    target = {"hit_rate_l10": 20.0, "tier": "war_zone"}
    canonical_source = {"hit_rate_l10": 25.0, "tier": "front_lines"}

    safe_overlay(target, canonical_source, owner_layer=True)
    assert target["hit_rate_l10"] == 25.0
    assert target["tier"] == "front_lines"
