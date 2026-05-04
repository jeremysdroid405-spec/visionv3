"""Field Ownership — SSOT enforcement layer for PropVision.

Public surface:
    from services.field_ownership import (
        get_owned_field,
        has_owned_field,
        FieldOwnershipError,
        FIELD_REGISTRY,
        validate_score_doc,
        ContractViolation,
    )

See `/app/memory/FIELD_OWNERSHIP.md` for the declarative contract and
`/app/memory/SSOT_ENFORCEMENT_REPORT.md` for migration status.
"""
from .accessors import get_owned_field, has_owned_field
from .registry import (
    FIELD_REGISTRY,
    FieldOwnershipError,
    FieldSpec,
    get_spec,
    list_fields_by_status,
)
from .validators import (
    ContractViolation,
    REQUIRED_SCORE_FIELDS,
    check_contract_opponent,
    check_contract_scored_at,
    validate_score_doc,
)

__all__ = [
    "get_owned_field",
    "has_owned_field",
    "FIELD_REGISTRY",
    "FieldSpec",
    "FieldOwnershipError",
    "get_spec",
    "list_fields_by_status",
    "ContractViolation",
    "REQUIRED_SCORE_FIELDS",
    "validate_score_doc",
    "check_contract_opponent",
    "check_contract_scored_at",
]
