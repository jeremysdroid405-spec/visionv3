"""
Universal SSOT Overwrite Firewall
==================================

Enforces the field-ownership contract at runtime: every field registered
in `registry.FIELD_REGISTRY` may be written by EXACTLY ONE writer
(declared in `FieldSpec.writers`). Every other layer — overlay, merge,
fallback, alias — is read-only.

This module is the enforcement boundary the previous infrastructure
was missing. `registry.py` declared ownership; `validators.py` checked
required-field presence; `accessors.py` enforced fail-loud reads.
NONE of those prevented a downstream layer from BLINDLY ASSIGNING to
an owned field via a spread / `update()` / per-key copy from a
non-owner source (the Daniss-Jenkins-style cross-line cached_board
leak — line=9.5 cached `hit_rates` clobbering a line=14.5 score doc).

Two callable surfaces:

    safe_overlay(target, source, *, owner_layer=False, exclude=())
        Merge `source` into `target` WITHOUT clobbering any owned
        field. Use at every cached_board / merge / overlay site.
        Pass `owner_layer=True` ONLY when the source IS the owning
        writer for those fields (escape hatch for legitimate writes).

    assert_no_owned_overwrite(before, after, *, allowed=())
        Test/audit helper. Raises `OwnedFieldOverwriteError` if any
        owned field changed value between `before` and `after`,
        unless that field is explicitly in `allowed`.

    protected_field_names() -> frozenset[str]
        Set of every storage-field name (the actual key on a
        document) currently protected by the firewall. Derived from
        the registry so adding/removing a registry entry
        automatically updates the firewall surface.

Rules enforced (matches the user-supplied 8-point contract):
  1. Every owned field has exactly one writer            (registry)
  2. Non-owner layers may read only                      (firewall)
  3. Overlay fields cannot overwrite owned fields        (safe_overlay)
  4. Fallbacks cannot source owned fields from aliases   (registry +
                                                          accessor)
  5. Frontend canonical-first preference                 (test)
  6. Line-specific fields cannot come from stat-level    (firewall +
                                                          test)
  7. Player-level fields cannot overwrite prop-level     (firewall)
  8. Enrichment-only fields preserve-on-replace          (PRESERVE
                                                          allowlist
                                                          in
                                                          prop_scores_store)
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional

from .registry import FIELD_REGISTRY


class OwnedFieldOverwriteError(RuntimeError):
    """Raised when a non-owner layer attempts to write an owned field.

    DO NOT CATCH this to substitute a different value — that defeats
    the firewall. Either:
      a. Confirm the layer SHOULD own the field and add it to the
         registry's `writers` list, OR
      b. Stop the offending overlay (the usual answer).
    """


# Storage-key map: registry uses public field NAMES; documents carry
# storage KEYS. The firewall protects the actual document key.
# Mirrors `accessors._STORAGE_FIELD_MAP`; kept as a separate mapping
# so this module is import-cycle-safe.
_PUBLIC_TO_STORAGE: Dict[str, str] = {
    "opponent":         "opponent",
    "p_true":           "p_true_active",
    "edge":             "edge_vs_fair",
    "hit_rate_l5":      "hit_rate_l5",
    "hit_rate_l10":     "hit_rate_l10",
    "hit_rate_l20":     "hit_rate_l20",
    "side":             "recommendation",
    "player_name":      "player_name",
    "pp_projection_id": "projection_id",
}


def protected_field_names() -> FrozenSet[str]:
    """Storage-key set of every owned field. Used by `safe_overlay`
    to decide which keys to never overwrite from a non-owner source.

    Derived from `FIELD_REGISTRY` at call time so registering a new
    field automatically expands the protected surface.
    """
    keys = set()
    for name, spec in FIELD_REGISTRY.items():
        # Public name → storage key (when they differ).
        keys.add(_PUBLIC_TO_STORAGE.get(name, name))
        # Also protect the registry's `owner_field` (storage path
        # under the owner collection — e.g. "selected_projections.odds_type"
        # collapses to "odds_type" at the document root for our
        # purposes, but we add the full path defensively).
        keys.add(spec.owner_field.split(".")[-1])
    # Aliases that historically caused silent overwrites.
    # `direction` is the legacy alias of `recommendation` — registry
    # already says `side` is owned, but old cached docs surface
    # `direction`. Block both spellings.
    keys.add("direction")
    return frozenset(keys)


def safe_overlay(
    target: Dict[str, Any],
    source: Mapping[str, Any],
    *,
    owner_layer: bool = False,
    exclude: Iterable[str] = (),
) -> Dict[str, int]:
    """Merge `source` → `target` while protecting owned fields.

    A non-owner overlay (the default) writes a key from `source` only
    when:
      * the key is NOT in `protected_field_names()`, AND
      * the key is NOT in the caller's `exclude` set, AND
      * `target.get(key)` is currently None / "" / [] (sticky-write —
        never replace existing values from a non-owner source).

    The sticky-write rule is what stops the Jenkins-style leak:
    `target["hit_rate_l10"] = 20.0` is already set by the score-doc
    SSOT, so the cached_board overlay's `hit_rate_l10 = 60` never
    lands.

    `owner_layer=True` skips the protection and performs an
    unconditional write. This is the escape hatch for the canonical
    writer and MUST only be passed when the source dict comes from
    that writer.

    Returns:
        {"applied": N, "blocked": M, "skipped_present": K}
        Useful for observability.
    """
    metrics = {"applied": 0, "blocked": 0, "skipped_present": 0}
    if not source:
        return metrics

    protected = protected_field_names()
    excl = set(exclude)

    for key, value in source.items():
        if key in excl:
            continue
        if not owner_layer and key in protected:
            metrics["blocked"] += 1
            continue
        # Sticky-write: never overwrite an existing non-empty value
        # from a non-owner source. The owner already wrote it.
        if not owner_layer and target.get(key) not in (None, "", []):
            metrics["skipped_present"] += 1
            continue
        target[key] = value
        metrics["applied"] += 1

    return metrics


def assert_no_owned_overwrite(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allowed: Iterable[str] = (),
    context: str = "",
) -> None:
    """Audit helper for tests / dev-mode runtime checks.

    Compares `before` to `after`; raises `OwnedFieldOverwriteError`
    if any protected key changed value (excluding keys passed in
    `allowed`, which the caller asserts are legitimate writes).

    Useful as a wrapping assertion around any code path that
    *shouldn't* mutate owned fields.
    """
    protected = protected_field_names()
    allowed_set = set(allowed)
    diffs = []
    for key in protected:
        if key in allowed_set:
            continue
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        # before-value present and changed → overwrite
        if b not in (None, "", []) and a != b:
            diffs.append(f"{key}: {b!r} -> {a!r}")
    if diffs:
        raise OwnedFieldOverwriteError(
            f"[FIREWALL{(' ' + context) if context else ''}] "
            f"non-owner layer mutated owned field(s): "
            f"{'; '.join(diffs)}"
        )


__all__ = [
    "OwnedFieldOverwriteError",
    "safe_overlay",
    "assert_no_owned_overwrite",
    "protected_field_names",
]
