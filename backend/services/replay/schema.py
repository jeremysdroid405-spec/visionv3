"""
Replay collection schema + index specifications.

Phase 0: declarative only. `ensure_indexes(db)` is callable but not invoked
during package import. No collections are created until something explicitly
calls `ensure_indexes`.

All replay collections are isolated from live data (`replay_*` prefix).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)


# Sentinel value that every replay output document must carry.
# Forward-testing lineage filter ignores anything not in
# {"legacy_vk", "modern_ssot"} — this keeps replay outputs
# permanently quarantined from official forward-test reporting.
DATASET_LINEAGE_VALUE = "historical_replay"


# Ordered list (kept stable for fixtures / tests / readability).
REPLAY_COLLECTIONS: List[str] = [
    "replay_events",
    "replay_odds_snapshots",
    "replay_props_normalized",
    "replay_results",
    "replay_runs",
    "replay_evaluations",
    "replay_outcomes",
    "replay_feature_cache",
    "replay_gate_sweeps",
    "replay_market_movements",
    "replay_calibration_reports",
]


# Index spec format:
#   { collection_name: [
#         { "name": str,
#           "keys": [(field, ASCENDING|DESCENDING), ...],
#           "unique": bool (optional),
#         },
#         ...
#     ],
#     ...
#   }
INDEX_SPECS: Dict[str, List[Dict[str, Any]]] = {
    "replay_events": [
        {"name": "sport_game_date",
         "keys": [("sport_key", ASCENDING), ("game_date", ASCENDING)]},
        {"name": "sport_commence_time",
         "keys": [("sport_key", ASCENDING), ("commence_time", ASCENDING)]},
        {"name": "uniq_sport_event_id",
         "keys": [("sport_key", ASCENDING), ("event_id", ASCENDING)],
         "unique": True},
    ],
    "replay_odds_snapshots": [
        {"name": "uniq_event_market_label",
         "keys": [("event_id", ASCENDING),
                  ("market_key", ASCENDING),
                  ("snapshot_label", ASCENDING)],
         "unique": True},
        {"name": "sport_label",
         "keys": [("sport_key", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
        {"name": "payload_hash",
         "keys": [("payload_hash", ASCENDING)]},
    ],
    "replay_props_normalized": [
        {"name": "uniq_event_label_book_market_player_line_side",
         "keys": [("event_id", ASCENDING),
                  ("snapshot_label", ASCENDING),
                  ("bookmaker", ASCENDING),
                  ("market_key", ASCENDING),
                  ("player", ASCENDING),
                  ("line", ASCENDING),
                  ("side", ASCENDING)],
         "unique": True},
        {"name": "canonical_key_label",
         "keys": [("canonical_key", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
        {"name": "player_label",
         "keys": [("player", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
        {"name": "sport_label_book",
         "keys": [("sport_key", ASCENDING),
                  ("snapshot_label", ASCENDING),
                  ("bookmaker", ASCENDING)]},
    ],
    "replay_results": [
        {"name": "uniq_event_player",
         "keys": [("event_id", ASCENDING), ("player", ASCENDING)],
         "unique": True},
        {"name": "sport_game_date",
         "keys": [("sport_key", ASCENDING), ("game_date", ASCENDING)]},
        {"name": "sport_player",
         "keys": [("sport_key", ASCENDING), ("player", ASCENDING)]},
    ],
    "replay_runs": [
        {"name": "created_at_desc",
         "keys": [("created_at", DESCENDING)]},
        {"name": "run_name",
         "keys": [("run_name", ASCENDING)]},
        {"name": "git_commit",
         "keys": [("git_commit", ASCENDING)]},
    ],
    "replay_evaluations": [
        {"name": "run_tier",
         "keys": [("replay_run_id", ASCENDING),
                  ("tier", ASCENDING)]},
        {"name": "run_canonical_label",
         "keys": [("replay_run_id", ASCENDING),
                  ("canonical_key", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
        {"name": "run_label",
         "keys": [("replay_run_id", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
        {"name": "run_event",
         "keys": [("replay_run_id", ASCENDING),
                  ("event_id", ASCENDING)]},
    ],
    "replay_outcomes": [
        {"name": "run_outcome",
         "keys": [("replay_run_id", ASCENDING),
                  ("outcome", ASCENDING)]},
        {"name": "run_tier",
         "keys": [("replay_run_id", ASCENDING),
                  ("tier_at_eval", ASCENDING)]},
        {"name": "run_canonical_label",
         "keys": [("replay_run_id", ASCENDING),
                  ("canonical_key", ASCENDING),
                  ("snapshot_label", ASCENDING)]},
    ],
    "replay_feature_cache": [
        {"name": "uniq_player_asof",
         "keys": [("player", ASCENDING),
                  ("as_of_minute", ASCENDING),
                  ("feature_set_version", ASCENDING)],
         "unique": True},
    ],
    "replay_gate_sweeps": [
        {"name": "run_gate_param",
         "keys": [("replay_run_id", ASCENDING),
                  ("gate_name", ASCENDING),
                  ("param_value", ASCENDING)]},
    ],
    "replay_market_movements": [
        {"name": "run_canonical",
         "keys": [("replay_run_id", ASCENDING),
                  ("canonical_key", ASCENDING)]},
    ],
    "replay_calibration_reports": [
        {"name": "uniq_run",
         "keys": [("replay_run_id", ASCENDING)],
         "unique": True},
    ],
}


# Sanity: every declared collection has at least one index spec.
assert set(INDEX_SPECS.keys()) == set(REPLAY_COLLECTIONS), (
    "INDEX_SPECS keys must match REPLAY_COLLECTIONS"
)


async def ensure_indexes(db) -> Dict[str, List[str]]:
    """Create every replay collection index. Idempotent. Safe to call repeatedly.

    Args:
        db: an `AsyncIOMotorDatabase` (Motor 3+) handle. Synchronous
            pymongo `Database` is also accepted — this function awaits
            create_index calls but Motor returns awaitables; pymongo
            returns plain values that we coerce with `inspect.isawaitable`.

    Returns:
        Mapping of collection_name → list of created/ensured index names.
    """
    import inspect

    created: Dict[str, List[str]] = {}
    for coll_name, specs in INDEX_SPECS.items():
        coll = db[coll_name]
        names: List[str] = []
        for spec in specs:
            kwargs = {"name": spec["name"]}
            if spec.get("unique"):
                kwargs["unique"] = True
            res = coll.create_index(spec["keys"], **kwargs)
            if inspect.isawaitable(res):
                res = await res
            names.append(spec["name"])
        created[coll_name] = names
    logger.info(f"[replay.schema] ensured indexes on {len(created)} collections")
    return created


__all__ = [
    "DATASET_LINEAGE_VALUE",
    "REPLAY_COLLECTIONS",
    "INDEX_SPECS",
    "ensure_indexes",
]
