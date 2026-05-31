"""
_index_utils.py — shared tolerant index helpers for the SGO historical pipeline.

PURPOSE
    The SGO archive collections (`sgo_events`, `sgo_props_raw`,
    `sgo_players`, `sgo_book_consensus`, `sgo_pp_research_core*`, the
    per-sport `sgo_{nfl,ncaaf}_research_*` variants, etc.) are written
    by multiple scripts and from multiple deploy generations. Different
    scripts auto-name their indexes differently — for example legacy
    `scripts/sgo/ingest.py::ensure_indexes()` calls
    `db.sgo_events.create_index([("event_id",1),("snapshot_time",1)])`
    with no `name=` kwarg, so Mongo auto-names it
    `event_id_1_snapshot_time_1`. Newer scripts pass an explicit
    `name="events_pk"`. Re-running the newer script on a collection
    already carrying the legacy auto-named index raises
    `OperationFailure: IndexOptionsConflict` (code 85).

    This module provides a single, tolerant, idempotent index-creation
    API for the entire historical pipeline. It is intentionally minimal
    and never mutates pre-existing indexes — touching them could ripple
    into other leagues' data (MLB/NBA/NFL).

CONTRACT
    1. Match by KEY PATTERN, not name.
       If an index with the same `[(field, direction), …]` already
       exists under any name, it is reused as-is (we return its
       existing name).
    2. NEVER drop existing indexes.
    3. NEVER mutate existing indexes — even if the existing index's
       `unique` flag differs from the requested one. (Changing
       uniqueness in place requires a drop+recreate; that is the
       caller's explicit decision to make.)
    4. Race-condition safety net: catch `OperationFailure` codes
       85 (IndexOptionsConflict) and 86 (IndexKeySpecsConflict) and
       treat them as non-fatal IF a same-pattern index now exists
       (i.e. another process beat us to it).
    5. Re-raise on all other errors — network, auth, parse failures,
       etc. surface loudly.

USAGE
    from scripts.sgo._index_utils import ensure_index, ensure_indexes

    # Single index
    await ensure_index(
        db.sgo_events,
        keys=[("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="events_pk")

    # Batch
    await ensure_indexes(db.sgo_props_raw, [
        {"keys": [("event_id", 1), ("odd_id", 1), ("book_id", 1),
                  ("side", 1), ("line", 1), ("snapshot_time", 1)],
         "unique": True, "name": "props_raw_pk"},
        {"keys": [("league_id", 1)], "name": "props_raw_league_id"},
        {"keys": [("player_id", 1)], "name": "props_raw_player_id"},
    ])
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pymongo.errors import OperationFailure

# A key spec is a sequence of (field_name, direction_int) tuples — exactly
# what pymongo's create_index accepts. Direction is typically 1 (ASCENDING)
# or -1 (DESCENDING). A bare string is allowed as shorthand for a single
# ascending key, mirroring pymongo's API surface.
KeyPattern = Union[str, Sequence[Tuple[str, int]]]


# ──────────────────────────────────────────────────────────────────────────
def _coerce_keys(keys: KeyPattern) -> List[Tuple[str, int]]:
    """Normalize `keys` into a list of (field, direction) tuples.

    Accepts:
      - a bare string  →  [(string, 1)]
      - a sequence of (field, direction) tuples (or lists)  →  list of tuples
    """
    if isinstance(keys, str):
        return [(keys, 1)]
    coerced: List[Tuple[str, int]] = []
    for k in keys:
        if isinstance(k, str):
            coerced.append((k, 1))
        elif isinstance(k, (tuple, list)) and len(k) == 2:
            coerced.append((str(k[0]), int(k[1])))
        else:
            raise TypeError(
                f"Unsupported key spec element: {k!r}. "
                f"Expected str or (field, direction) tuple.")
    return coerced


def _key_doc(keys: List[Tuple[str, int]]) -> Dict[str, int]:
    """Convert key tuples → comparable dict form for pattern matching.

    Pymongo's `index_information()` returns keys as a list of (field,
    direction) tuples. Two indexes have the same pattern iff their key
    dicts are equal.
    """
    return {field: direction for field, direction in keys}


# ──────────────────────────────────────────────────────────────────────────
async def ensure_index(
    coll,
    keys: KeyPattern,
    *,
    unique: bool = False,
    name: Optional[str] = None,
    **create_index_kwargs: Any,
) -> str:
    """Idempotently ensure an index with the requested key pattern exists
    on `coll`. See module docstring for the full contract.

    Args:
        coll: A Motor (or PyMongo) collection handle.
        keys: Key pattern — bare string or sequence of (field, direction).
        unique: Whether the index should be unique. Ignored if a
                same-pattern index already exists (we never mutate).
        name: Preferred index name. Only used if no same-pattern index
              exists yet. If None, Mongo's auto-name is used.
        **create_index_kwargs: Additional kwargs forwarded to
              `create_index()` (e.g. `sparse=True`, `partialFilterExpression`).
              These are also only applied on actual creation.

    Returns:
        The effective index name (existing or newly created).

    Raises:
        OperationFailure: any failure code other than 85/86, OR a
                          85/86 where no same-pattern index can be
                          found after the conflict (true conflict
                          on a different pattern).
    """
    target_keys = _coerce_keys(keys)
    target_pattern = _key_doc(target_keys)

    # 1. Inspect existing indexes — match by KEY PATTERN
    existing = await coll.index_information()
    for ex_name, ex_spec in existing.items():
        ex_pattern = _key_doc([
            (str(f), int(d)) for f, d in (ex_spec.get("key") or [])
        ])
        if ex_pattern == target_pattern:
            return ex_name   # reuse existing — never mutate

    # 2. No same-pattern index — safe to create
    create_kwargs = dict(create_index_kwargs)
    if unique:
        create_kwargs["unique"] = True
    if name is not None:
        create_kwargs["name"] = name
    try:
        return await coll.create_index(target_keys, **create_kwargs)
    except OperationFailure as e:
        # 85 = IndexOptionsConflict, 86 = IndexKeySpecsConflict.
        # Race-safety: another process may have created a same-pattern
        # index between our scan and our create call.
        if getattr(e, "code", None) in (85, 86):
            after = await coll.index_information()
            for ex_name, ex_spec in after.items():
                ex_pattern = _key_doc([
                    (str(f), int(d))
                    for f, d in (ex_spec.get("key") or [])
                ])
                if ex_pattern == target_pattern:
                    return ex_name
            # 85/86 raised, but the conflict is on a DIFFERENT pattern
            # we don't recognise → surface it. Caller bug, not ours.
        raise


async def ensure_indexes(
    coll,
    specs: Sequence[Dict[str, Any]],
) -> List[str]:
    """Batch wrapper around `ensure_index`. Returns the effective name
    for each spec, in input order.

    Each spec is a dict with keys:
        keys     (required)  — KeyPattern
        unique   (optional)  — bool, default False
        name     (optional)  — str
        any other kwarg accepted by `ensure_index` / `create_index`
    """
    names: List[str] = []
    for s in specs:
        if "keys" not in s:
            raise ValueError(f"index spec missing 'keys': {s!r}")
        spec = dict(s)
        keys = spec.pop("keys")
        names.append(await ensure_index(coll, keys, **spec))
    return names


__all__ = ["ensure_index", "ensure_indexes", "KeyPattern"]
