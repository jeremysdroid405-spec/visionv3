"""
Collection-name indirection contract (Wave 0 gate).

The rebuild relies on every sport-specific collection name being resolvable
through `services.config.collection_names.COLL(concept, sport)`. These tests
are the stability gate that prevents a concept from being accidentally
removed, mistyped, or silently falling back to a wrong default.

Invariants enforced:
  - Every defined concept resolves for `nba`.
  - Every defined concept resolves for `mlb`.
  - Concepts not yet backed by a live collection (`board_active`,
    `board_injured`, `board_overlays`) MUST raise KeyError — fail-fast is
    the contract.
  - Unknown concepts raise KeyError.
  - Unknown sports raise KeyError.
  - Shared concepts resolve through `COLL.shared`.
"""
from __future__ import annotations

import pytest

from services.config.collection_names import (
    COLL,
    _SHARED_COLLECTIONS,
    _SPORT_COLLECTIONS,
)

# Concepts that are declared but not yet live (introduced in Wave 5).
# Dereferencing them today MUST raise to prevent accidental premature use.
NOT_YET_LIVE = {"board_active", "board_injured", "board_overlays"}


@pytest.mark.parametrize("concept", sorted(_SPORT_COLLECTIONS.keys()))
def test_every_concept_resolves_for_nba(concept: str) -> None:
    if concept in NOT_YET_LIVE:
        with pytest.raises(KeyError):
            COLL(concept, "nba")
        return
    resolved = COLL(concept, "nba")
    assert isinstance(resolved, str) and resolved, (
        f"concept={concept} resolved to empty/non-string for nba: {resolved!r}"
    )


@pytest.mark.parametrize("concept", sorted(_SPORT_COLLECTIONS.keys()))
def test_every_concept_resolves_for_mlb(concept: str) -> None:
    if concept in NOT_YET_LIVE:
        with pytest.raises(KeyError):
            COLL(concept, "mlb")
        return
    resolved = COLL(concept, "mlb")
    assert isinstance(resolved, str) and resolved, (
        f"concept={concept} resolved to empty/non-string for mlb: {resolved!r}"
    )


@pytest.mark.parametrize("concept", sorted(NOT_YET_LIVE))
def test_not_yet_live_concepts_raise(concept: str) -> None:
    with pytest.raises(KeyError):
        COLL(concept, "nba")
    with pytest.raises(KeyError):
        COLL(concept, "mlb")


def test_unknown_concept_raises() -> None:
    with pytest.raises(KeyError):
        COLL("definitely_not_a_concept", "nba")


def test_unknown_sport_raises() -> None:
    with pytest.raises(KeyError):
        COLL("live_props", "curling")


@pytest.mark.parametrize("concept", sorted(_SHARED_COLLECTIONS.keys()))
def test_every_shared_concept_resolves(concept: str) -> None:
    resolved = COLL.shared(concept)
    assert isinstance(resolved, str) and resolved


def test_unknown_shared_raises() -> None:
    with pytest.raises(KeyError):
        COLL.shared("not_a_shared_concept")


def test_all_mapping_snapshot_is_copy() -> None:
    """`all_mapping()` must return a defensive copy, not a mutable reference."""
    m = COLL.all_mapping()
    m["sport_specific"]["live_props"]["nba"] = "TAMPERED"
    # The registry must be unchanged.
    assert COLL("live_props", "nba") != "TAMPERED"
