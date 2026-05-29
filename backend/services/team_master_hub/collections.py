"""
Phase 1.A.2 — Team collection bootstrap.

`ensure_team_collections(db)` creates the ten team-side collections
declared in §1.1 of `/app/memory/TEAM_PROPS_ARCHITECTURE.md` with the
exact compound indexes from §1.2. No documents are inserted.

Idempotent: re-running creates zero new indexes.

The collection spec is the single source of truth for index shape;
both the admin endpoint and the CLI read from `TEAM_COLLECTIONS`.

Hard limits (preview-only):
  - No SGO API
  - No prod
  - No UI
  - No player-side mutation (player `sgo_*` / `mlb_*` collections
    are NEVER named here)
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pymongo import ASCENDING, IndexModel


# ── §1.2 index spec ──────────────────────────────────────────────────
# Each entry: (collection_name, [IndexModel, …]).
# `unique=True` and `sparse=True` are explicit per the architecture
# doc. The compound unique key on the two odds collections includes
# `book` — the multi-book invariant from `test_mirror_multi_book.py`.
TEAM_COLLECTIONS: List[Tuple[str, List[IndexModel]]] = [
    # ── team_live_props (§1.2) ──
    ("team_live_props", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING), ("line",    ASCENDING),
              ("side",     ASCENDING), ("book",    ASCENDING),
              ("snapshot_iso", ASCENDING)],
            unique=True, name="ix_live_prop_compound_unique",
        ),
        IndexModel(
            [("game_date", ASCENDING), ("sport", ASCENDING)],
            name="ix_live_prop_date_sport",
        ),
    ]),

    # ── team_historical_props (§1.2) ──
    ("team_historical_props", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING), ("line",    ASCENDING),
              ("side",     ASCENDING), ("book",    ASCENDING),
              ("snapshot_iso", ASCENDING)],
            unique=True, name="ix_hist_prop_compound_unique",
        ),
        IndexModel(
            [("team_id", ASCENDING), ("market", ASCENDING),
              ("game_date", ASCENDING)],
            name="ix_hist_prop_team_market_date",
        ),
    ]),

    # ── team_prop_outcomes (§1.2) ──
    ("team_prop_outcomes", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING), ("line",    ASCENDING),
              ("side",     ASCENDING)],
            unique=True, name="ix_outcome_compound_unique",
        ),
        IndexModel(
            [("game_date", ASCENDING), ("sport", ASCENDING)],
            name="ix_outcome_date_sport",
        ),
    ]),

    # ── team_matchups (§1.2) ──
    ("team_matchups", [
        IndexModel([("event_id", ASCENDING)],
                    unique=True, name="ix_matchup_event_id_unique"),
        IndexModel([("game_date", ASCENDING), ("sport", ASCENDING)],
                    name="ix_matchup_date_sport"),
    ]),

    # ── team_injuries (§1.2) ──
    ("team_injuries", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("player_id", ASCENDING), ("reported_at", ASCENDING)],
            unique=True, name="ix_injury_compound_unique",
        ),
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING)],
            name="ix_injury_event_team",
        ),
    ]),

    # ── team_context (§1.2) ──
    ("team_context", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING)],
            unique=True, name="ix_context_compound_unique",
        ),
        IndexModel(
            [("game_date", ASCENDING), ("sport", ASCENDING)],
            name="ix_context_date_sport",
        ),
    ]),

    # ── team_features (§1.2) ──
    ("team_features", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING),
              ("feature_set_version", ASCENDING)],
            unique=True, name="ix_features_compound_unique",
        ),
    ]),

    # ── team_projections (§1.2) — model_version included so A/B
    # ── runs can coexist for the same (event, team, market). ──
    ("team_projections", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING),
              ("model_version", ASCENDING)],
            unique=True, name="ix_projection_compound_unique",
        ),
    ]),

    # ── team_prop_scores (§1.2) ──
    ("team_prop_scores", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING), ("line",    ASCENDING),
              ("side",     ASCENDING), ("book",    ASCENDING),
              ("snapshot_iso", ASCENDING),
              ("model_version", ASCENDING),
              ("gate_config_version", ASCENDING)],
            unique=True, name="ix_score_compound_unique",
        ),
    ]),

    # ── team_replay_outputs (§1.2) — same shape as team_prop_scores ──
    ("team_replay_outputs", [
        IndexModel(
            [("event_id", ASCENDING), ("team_id", ASCENDING),
              ("market",   ASCENDING), ("line",    ASCENDING),
              ("side",     ASCENDING), ("book",    ASCENDING),
              ("snapshot_iso", ASCENDING),
              ("model_version", ASCENDING),
              ("gate_config_version", ASCENDING)],
            unique=True, name="ix_replay_compound_unique",
        ),
    ]),
]

# Compound-unique-key column ordering keyed by collection name —
# used by the multi-book regression test to assert `book` is in the
# unique key wherever multi-book preservation matters.
COMPOUND_UNIQUE_KEYS: Dict[str, Tuple[str, ...]] = {
    "team_live_props": (
        "event_id", "team_id", "market", "line", "side",
        "book", "snapshot_iso",
    ),
    "team_historical_props": (
        "event_id", "team_id", "market", "line", "side",
        "book", "snapshot_iso",
    ),
    "team_prop_outcomes": (
        "event_id", "team_id", "market", "line", "side",
    ),
    "team_matchups": ("event_id",),
    "team_injuries": (
        "event_id", "team_id", "player_id", "reported_at",
    ),
    "team_context": ("event_id", "team_id"),
    "team_features": (
        "event_id", "team_id", "market", "feature_set_version",
    ),
    "team_projections": (
        "event_id", "team_id", "market", "model_version",
    ),
    "team_prop_scores": (
        "event_id", "team_id", "market", "line", "side", "book",
        "snapshot_iso", "model_version", "gate_config_version",
    ),
    "team_replay_outputs": (
        "event_id", "team_id", "market", "line", "side", "book",
        "snapshot_iso", "model_version", "gate_config_version",
    ),
}


async def ensure_team_collections(db) -> Dict[str, Any]:
    """Create + index every team-side collection. Idempotent.

    Returns a per-collection summary:
        {
          ok: True,
          n_collections: 10,
          collections: [
            {name, doc_count, is_new, indexes_before, indexes_after,
             indexes_created},
            ...
          ],
        }

    The `is_new` flag is True when the collection didn't exist
    before the call (we infer this by checking if the only
    pre-existing index is `_id_`, i.e. the implicit one Mongo
    creates on first insert). Conservative — a manually-created
    empty collection with only `_id_` will also report `is_new=True`,
    which is harmless.
    """
    existing_names = set(await db.list_collection_names())
    summary: List[Dict[str, Any]] = []

    for coll_name, models in TEAM_COLLECTIONS:
        coll = db[coll_name]
        was_present = coll_name in existing_names

        indexes_before: List[str] = []
        if was_present:
            try:
                idx_info = await coll.index_information()
                indexes_before = sorted(idx_info.keys())
            except Exception:  # noqa: BLE001
                indexes_before = []

        # `create_indexes` is idempotent and Motor-aware
        created = await coll.create_indexes(models)

        idx_after = await coll.index_information()
        indexes_after = sorted(idx_after.keys())
        indexes_created = sorted(set(indexes_after) - set(indexes_before))
        doc_count = await coll.count_documents({})

        summary.append({
            "name":              coll_name,
            "doc_count":         int(doc_count),
            "is_new":            (not was_present)
                                   or indexes_before == ["_id_"],
            "indexes_before":    indexes_before,
            "indexes_after":     indexes_after,
            "indexes_created":   indexes_created,
            "create_indexes_returned": list(created),
        })

    return {
        "ok": True,
        "n_collections": len(TEAM_COLLECTIONS),
        "collections":   summary,
    }


async def collections_status(db) -> Dict[str, Any]:
    """Read-only status — never writes, never creates indexes.

    Used by `GET /team-master-hub/collections-status` to verify
    `ensure_team_collections` is in-sync with the live DB.
    """
    existing = set(await db.list_collection_names())
    out: List[Dict[str, Any]] = []
    for coll_name, _ in TEAM_COLLECTIONS:
        coll = db[coll_name]
        present = coll_name in existing
        indexes: List[str] = []
        doc_count = 0
        if present:
            try:
                idx_info = await coll.index_information()
                indexes = sorted(idx_info.keys())
            except Exception:  # noqa: BLE001
                indexes = []
            doc_count = await coll.count_documents({})
        out.append({
            "name":         coll_name,
            "present":      present,
            "doc_count":    int(doc_count),
            "indexes":      indexes,
        })
    return {"ok": True,
            "n_collections": len(TEAM_COLLECTIONS),
            "collections":   out}
