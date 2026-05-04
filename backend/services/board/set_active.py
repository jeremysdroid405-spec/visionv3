"""
Universal `active` field writer for `{sport}_prop_scores` — SSOT enforcement.
============================================================================

This is the ONLY function allowed to write the `active` field on the
`{sport}_prop_scores` collection. Per `/app/memory/FIELD_OWNERSHIP.md`:
`active`, every flip (True → False or False → True) must go through
`set_active()` so that:

1. We have a single code path for lifecycle writes — no more divergent
   writers silently disagreeing about the three-tuple
   `(active, inactive_reason, active_changed_at)`.
2. Every transition is recorded in `active_transitions` (audit
   collection, TTL 30d) for post-mortem debugging of "why did this
   pick fall off / reappear".

Legitimate callers (migrated in this wave):
    - `services/scoring/tiering.py::mark_retired_inactive`
    - `services/board/scanner.py::scan_sport`

Initial `active=True` on first-time score doc persistence is handled
by `services/scoring/prop_scores_store.py::_project_score_doc` (it
sets the default on the doc dict pre-insert). That's not a transition
— it's an insert — so it does not route through this helper.

What this module explicitly does NOT do:
    - Write the `active` field on `board_state` (different concept —
      that's the UI-published-board lifecycle, not the prop-score
      lifecycle; its writers live in `services/board/publisher.py`).
    - Write the `active` field on `{sport}_context_engine` (badge
      flag lifecycle; writer is `services/badge_resolver.py`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Audit collection — one row per transition. TTL 30 days so the
# table stays small; anything older is off-the-board by then.
AUDIT_COLL = "active_transitions"
_AUDIT_TTL_SECONDS = 30 * 24 * 3600


async def ensure_indexes(db) -> None:
    """Idempotent — safe to call repeatedly at boot."""
    if db is None:
        return
    try:
        await db[AUDIT_COLL].create_index(
            "occurred_at",
            expireAfterSeconds=_AUDIT_TTL_SECONDS,
            name="active_transitions_ttl",
        )
        await db[AUDIT_COLL].create_index(
            [("sport", 1), ("canonical_key", 1), ("occurred_at", -1)],
            name="active_transitions_lookup_idx",
        )
    except Exception as e:  # pragma: no cover
        logger.warning("[SET_ACTIVE] index ensure failed: %s", e)


async def set_active(
    db,
    sport: str,
    canonical_keys: Iterable[str],
    active: bool,
    reason: str,
    version_tag: Optional[str] = None,
    extra_filter: Optional[Dict[str, Any]] = None,
    emit_audit: bool = True,
) -> Dict[str, Any]:
    """Flip the canonical `active` field on `{sport}_prop_scores`.

    Parameters
    ----------
    db
        Motor async DB handle. `None` becomes a no-op (returns zeros).
    sport
        Lowercase sport key — `"nba"` / `"mlb"` / etc.
    canonical_keys
        Iterable of canonical keys to flip. May be empty (no-op).
        If `None` is passed and `extra_filter` is provided, the filter
        alone drives the scope (used by the game-start scanner, which
        flips every active doc whose game has tipped off).
    active
        Target state — `True` (re-activate) or `False` (retire).
    reason
        Human-readable audit string. Examples: "game_started",
        "retired_by_delta_engine", "reactivated_by_rerun".
    version_tag
        Optional — scope the update to a single `version_tag`
        (typically `final-{sport}-rt` for the Ferrari RT path).
    extra_filter
        Optional — additional Mongo filter predicates AND-ed into the
        match clause. Used by the game-start scanner which wants
        `{"game_start_utc": {"$lte": now}}` on top of the active flip.
    emit_audit
        Write a row to `active_transitions` for each matched doc.
        Default True; set False only for high-volume tests.

    Returns
    -------
    dict
        `{matched, modified, keys_processed, to_state, reason,
          version_tag, sport}`.
    """
    keys_list: List[str] = list(canonical_keys) if canonical_keys is not None else []
    if db is None:
        return {
            "matched": 0, "modified": 0, "keys_processed": len(keys_list),
            "to_state": active, "reason": reason, "version_tag": version_tag,
            "sport": sport,
        }

    coll = db[COLL("prop_scores", sport)]
    now = datetime.now(timezone.utc)

    # Build the match filter. Scope narrows by keys (if provided) and
    # version_tag (if provided) AND extra_filter (if provided).
    match: Dict[str, Any] = {}
    if keys_list:
        match["canonical_key"] = {"$in": keys_list}
    elif not extra_filter:
        # No keys AND no filter would be an unbounded update — refuse.
        logger.warning(
            "[SET_ACTIVE:%s] refused unbounded update (no keys, no filter). "
            "reason=%r", sport, reason,
        )
        return {
            "matched": 0, "modified": 0, "keys_processed": 0,
            "to_state": active, "reason": reason, "version_tag": version_tag,
            "sport": sport,
        }
    if version_tag:
        match["version_tag"] = version_tag
    if extra_filter:
        match.update(extra_filter)

    # Only update rows actually transitioning (avoids redundant writes
    # and keeps `modified_count` honest). `{"$ne": active}` handles both
    # the missing-field case (legacy docs with no `active` field) and
    # the opposite-state case.
    if active is True:
        match.setdefault("$or", [
            {"active": {"$ne": True}},
            {"active": {"$exists": False}},
        ])
    else:
        match.setdefault("$or", [
            {"active": {"$ne": False}},
            {"active": {"$exists": False}},
        ])

    set_doc: Dict[str, Any] = {
        "active": active,
        "active_changed_at": now,
        "inactive_reason": reason if active is False else None,
    }

    result = await coll.update_many(match, {"$set": set_doc})
    matched = int(getattr(result, "matched_count", 0) or 0)
    modified = int(getattr(result, "modified_count", 0) or 0)

    # Audit emit — one row per matched doc. We read `canonical_key`
    # only (identity projection) so this stays cheap at scale.
    if emit_audit and matched > 0:
        try:
            cursor = coll.find(match if False else {  # match is mutated above — re-express scope
                **({"canonical_key": {"$in": keys_list}} if keys_list else {}),
                **({"version_tag": version_tag} if version_tag else {}),
                **(extra_filter or {}),
                "active": active,  # post-update state (the ones we just flipped)
                "active_changed_at": now,
            }, {"_id": 0, "canonical_key": 1})
            audit_rows: List[Dict[str, Any]] = []
            async for doc in cursor:
                ck = doc.get("canonical_key")
                if ck:
                    audit_rows.append({
                        "sport":         sport,
                        "canonical_key": ck,
                        "to_state":      active,
                        "reason":        reason,
                        "version_tag":   version_tag,
                        "occurred_at":   now,
                    })
            if audit_rows:
                await db[AUDIT_COLL].insert_many(audit_rows, ordered=False)
        except Exception as e:  # pragma: no cover
            logger.warning("[SET_ACTIVE:%s] audit emit failed: %s", sport, e)

    if modified:
        logger.info(
            "[SET_ACTIVE:%s] %s=%s reason=%r matched=%d modified=%d "
            "keys=%d version_tag=%r",
            sport, "active", active, reason, matched, modified,
            len(keys_list), version_tag,
        )
    return {
        "matched":         matched,
        "modified":        modified,
        "keys_processed":  len(keys_list),
        "to_state":        active,
        "reason":          reason,
        "version_tag":     version_tag,
        "sport":           sport,
    }


__all__ = ["set_active", "ensure_indexes", "AUDIT_COLL"]
