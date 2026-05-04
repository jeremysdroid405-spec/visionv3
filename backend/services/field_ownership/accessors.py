"""Canonical accessors for owned fields.

Every read of a field registered in `registry.FIELD_REGISTRY` MUST go
through `get_owned_field()`. Direct `prop.get("x")` on owned fields
bypasses the ownership contract and will be flagged by the contract
scanner (future).

Usage:
    from services.field_ownership.accessors import get_owned_field

    opp = get_owned_field(prop, "opponent")      # None or "MIN"
    p   = get_owned_field(prop, "p_true")        # raises on None (fail_loud)

Implementation notes:
- The accessor is SCHEMA-aware, not source-aware — it reads from the
  passed-in dict. The dict is expected to already have been populated
  from the owner collection at query time.
- For fields where the storage field name differs from the public
  name (e.g. `edge` stored as `edge_vs_fair`), the accessor handles
  the mapping.
- We explicitly do NOT chain `.get("x") or .get("y") or .get("z")`.
  If the owner doc doesn't have the owner field, we return None
  (or raise, per null_policy). That is the entire point of SSOT.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .registry import FIELD_REGISTRY, FieldOwnershipError


# Map from public field name → storage field name when they differ.
# (When they're the same, absence from this map means "use name as-is".)
_STORAGE_FIELD_MAP: Dict[str, str] = {
    "opponent":         "opponent",       # stored as "opponent" on live_props read by ferrari_tiers
    "p_true":           "p_true_active",
    "edge":             "edge_vs_fair",
    "hit_rate_l5":      "l5_rate",
    "hit_rate_l10":     "l10_rate",
    "hit_rate_l20":     "hit_rate_over",  # legacy name; migration will rename
    "side":             "recommendation",
    "player_name":      "display_name",   # master_hub canonical
    "pp_projection_id": "projection_id",
}


def get_owned_field(doc: Dict[str, Any], field_name: str) -> Optional[Any]:
    """Canonical read. Enforces the field's null_policy.

    Returns:
        The owner-collection value, or None if null_policy=return_null
        and the field is missing/None.

    Raises:
        FieldOwnershipError: if null_policy=fail_loud and the field is
            missing/None on `doc`. Callers MUST NOT catch this to
            substitute a fallback value.
        KeyError: if `field_name` is not registered (unregistered
            fields have no ownership — they should be registered
            before use).
    """
    spec = FIELD_REGISTRY[field_name]
    storage_name = _STORAGE_FIELD_MAP.get(field_name, field_name)
    value = doc.get(storage_name)

    if value is None or (isinstance(value, str) and value == ""):
        if spec.null_policy == "fail_loud":
            raise FieldOwnershipError(
                f"Field '{field_name}' (storage: {storage_name}) is "
                f"missing from doc but null_policy=fail_loud. "
                f"Owner: {spec.owner_collection}.{spec.owner_field}. "
                f"Fix the source — do NOT substitute a fallback."
            )
        return None

    return value


def has_owned_field(doc: Dict[str, Any], field_name: str) -> bool:
    """Non-raising presence check. Safe to use for branching."""
    spec = FIELD_REGISTRY[field_name]
    storage_name = _STORAGE_FIELD_MAP.get(field_name, field_name)
    value = doc.get(storage_name)
    return value is not None and not (isinstance(value, str) and value == "")


__all__ = ["get_owned_field", "has_owned_field"]
