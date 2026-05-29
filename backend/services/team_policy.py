"""
Team-side book policy — re-exports the canonical BLOCKED_BOOKS and
REFERENCE_ONLY_BOOKS sets from the existing player-pipeline
authoring location.

§14.5 invariant: team and player code paths MUST reference the SAME
Python object so policy can never drift between them. The test
`test_team_phase_1_a_0_skeleton.py::test_blocked_books_identity`
verifies this with `is` (and `id()`) checks.

Phase 1.A.0 does NOT modify the player-side authoring location. We
chose this seam so player code is not touched in this slice; a
later refactoring slice can promote the policy into a neutral
`services/policy/book_policy.py` if/when we unify the player and
optimizer duplicates.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §14.5.
"""
from __future__ import annotations

# Import the canonical objects defined in the existing reshape
# script. These are intentionally NOT redefined here — see invariant
# above. This is also where REFERENCE_ONLY_BOOKS now lives;
# `optimizer.py` has a duplicate `REFERENCE_ONLY_BOOKS` we will
# unify in a later slice (tracked in CHANGELOG follow-ups).
from scripts.sgo.reshape_sgo_to_replay_odds import (
    BLOCKED_BOOKS,
    REFERENCE_ONLY_BOOKS,
)

__all__ = ["BLOCKED_BOOKS", "REFERENCE_ONLY_BOOKS"]
