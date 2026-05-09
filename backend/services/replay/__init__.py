"""
PropVision Historical Replay Test Suite — service package.

Phase 0 scaffolding only. Contains:
  - schema.py        : collection names, index specifications, ensure_indexes()
  - snapshot_plan.py : 8-window pregame ladder + per-tier canonical mapping
  - markets.py       : market + book whitelists for replay (NBA Phase 1)
  - run_header.py    : version-fingerprint helpers for reproducible runs

Nothing in this package executes DB writes or API calls on import.
"""
from .snapshot_plan import (
    REPLAY_WINDOWS,
    REPLAY_WINDOW_LABELS,
    PER_TIER_CANONICAL_SNAPSHOT,
    snapshot_for,
    minutes_before_start,
)
from .schema import (
    REPLAY_COLLECTIONS,
    INDEX_SPECS,
    DATASET_LINEAGE_VALUE,
    ensure_indexes,
)
from .run_header import (
    SCORING_FILES,
    GATE_FILES,
    compute_run_fingerprint,
    new_run_id,
)
from .markets import (
    REPLAY_NBA_MARKETS,
    REPLAY_BOOK_WHITELIST_PHASE1,
    REPLAY_REGIONS_PHASE1,
)

__all__ = [
    "REPLAY_WINDOWS",
    "REPLAY_WINDOW_LABELS",
    "PER_TIER_CANONICAL_SNAPSHOT",
    "snapshot_for",
    "minutes_before_start",
    "REPLAY_COLLECTIONS",
    "INDEX_SPECS",
    "DATASET_LINEAGE_VALUE",
    "ensure_indexes",
    "SCORING_FILES",
    "GATE_FILES",
    "compute_run_fingerprint",
    "new_run_id",
    "REPLAY_NBA_MARKETS",
    "REPLAY_BOOK_WHITELIST_PHASE1",
    "REPLAY_REGIONS_PHASE1",
]
