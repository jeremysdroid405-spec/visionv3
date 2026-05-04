"""Validators — runtime contract checks for field ownership.

Two surfaces:

1. `validate_score_doc(doc)` — called from `write_versioned_scores`
   before DB insert. Catches missing required fields and unknown
   fields (silent-drop prevention). Replaces the allowlist's
   silent-filter behavior.

2. `check_contract(field, source_doc)` — used by contract tests to
   assert that a sample of live data still honors ownership.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .registry import FIELD_REGISTRY, FieldOwnershipError


class ContractViolation(RuntimeError):
    """Raised by contract tests when live data violates ownership."""


# Fields that are REQUIRED on every persisted score doc. Derived from
# registry entries with null_policy=fail_loud (i.e. they must exist).
REQUIRED_SCORE_FIELDS: List[str] = sorted(
    f for f, s in FIELD_REGISTRY.items()
    if s.null_policy == "fail_loud" and s.owner_collection == "prop_scores"
)


def validate_score_doc(doc: Dict[str, Any], allowlist: set) -> List[str]:
    """Validate a score doc before DB write.

    Returns a list of violation strings (empty when valid). Callers
    decide whether to raise or log based on deploy phase:

    - Phase 1 (now): log warnings so existing writes aren't blocked.
    - Phase 2 (after full migration): raise `ContractViolation` on
      any non-empty list.

    Rules enforced:
    - Every required field (per registry) has a non-None value.
    - Every key in `doc` is either in `allowlist` OR in the registry's
      storage-field set — unknown keys are dropped today but MUST be
      flagged so we know about them.
    """
    violations: List[str] = []

    # Required-field presence
    for fname in REQUIRED_SCORE_FIELDS:
        spec = FIELD_REGISTRY[fname]
        storage = spec.owner_field
        if storage not in doc or doc[storage] is None:
            violations.append(
                f"REQUIRED field missing: {fname} (storage: {storage})"
            )

    # Unknown-field detection
    for key in doc.keys():
        if key not in allowlist:
            violations.append(
                f"UNKNOWN field being written (NOT in allowlist): {key}"
            )

    return violations


def check_contract_opponent(api_pick: Dict[str, Any]) -> None:
    """Contract test helper: a pick returned by /api/v3/ferrari/* must
    have opponent sourced from live_props (verified by presence of
    opponent_abbr which is set only by the live_props override path)."""
    opp = api_pick.get("opponent")
    if opp is None:
        return  # null is allowed under return_null policy
    # If opponent_abbr is missing but opponent is set, we're reading
    # from a legacy path (cached_board etc.) that doesn't populate
    # opponent_abbr. This is the smoking-gun signal.
    if "opponent_abbr" not in api_pick:
        raise ContractViolation(
            f"Pick {api_pick.get('player_name')} has opponent={opp!r} "
            f"but no opponent_abbr — indicates legacy cached_board "
            f"source, not live_props."
        )


def check_contract_scored_at(score_doc: Dict[str, Any]) -> None:
    """Contract test helper: every active score doc must carry
    scored_at (populated at write time via write_versioned_scores)."""
    if score_doc.get("scored_at") is None:
        raise ContractViolation(
            f"Score doc for {score_doc.get('canonical_key')} missing "
            f"scored_at — write_versioned_scores is not populating it."
        )


__all__ = [
    "ContractViolation",
    "REQUIRED_SCORE_FIELDS",
    "validate_score_doc",
    "check_contract_opponent",
    "check_contract_scored_at",
]
