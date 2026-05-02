"""
Delta Engine — Sport-Agnostic Change Detector
=============================================
Phase D1 (2026-04-21): read-only detection of prop-level changes since the
last watermark. No writes. No upstream API calls.

Three detection signals, per the architecture plan §5:

  1. UPDATED  — rows in `{sport}_live_props` with `updated_at > watermark`
                (primary signal: line moves, odds moves, new listings by
                the odds-sync).
  2. NEW      — canonical_keys present in live_props but absent from
                `{sport}_prop_scores@final-{sport}-rt` (secondary signal:
                props that upstream just surfaced and have never been
                scored at the RT tag).
  3. RETIRED  — canonical_keys present in the RT score set but whose
                live_props row is now `active=False` (tertiary signal:
                game-start scanner flipped the prop; tier ladder drops
                them on next rebalance).

Sport is passed in. Zero per-sport branching in this module — all sport-
specific details come from `services.scoring.adapters.get_scoring_adapter`
(for `cached_board_collection` / rt tag) and `services.config.collection_names.COLL`
(for live_props + prop_scores collection resolution).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from services.config.collection_names import COLL
from services.delta_watermarks import get_watermark_with_grace
from services.scoring.adapters import get_scoring_adapter

logger = logging.getLogger(__name__)

# Max sample size returned by the inspect endpoint for each bucket.
MAX_SAMPLE_KEYS = 10


def _rt_version_tag(sport: str) -> str:
    """The version_tag the live UI reads from (carbon-copy: final-{sport}-rt)."""
    return f"final-{sport}-rt"


@dataclass
class DeltaDetectionResult:
    """Result of a single detection pass. Read-only — no writes performed."""
    sport: str
    watermark_utc: Optional[datetime]
    # Canonical-key sets
    dirty_keys: Set[str] = field(default_factory=set)   # union of updated + new + retired
    updated_keys: Set[str] = field(default_factory=set) # §5.1 primary signal
    new_keys: Set[str] = field(default_factory=set)     # §5.2 secondary signal
    retired_keys: Set[str] = field(default_factory=set) # §5.3 tertiary signal
    # Totals for quick display
    live_props_count: int = 0
    active_live_props_count: int = 0
    scored_rt_count: int = 0
    # Samples for the inspect endpoint (capped at MAX_SAMPLE_KEYS each)
    sample_updated: List[str] = field(default_factory=list)
    sample_new: List[str] = field(default_factory=list)
    sample_retired: List[str] = field(default_factory=list)
    # How many live_props rows lacked an `updated_at` field (stamping
    # gap observability — should trend to 0 after the universal_odds_sync
    # instrumentation has run once).
    missing_updated_at: int = 0

    def to_summary(self) -> Dict[str, Any]:
        return {
            "sport": self.sport,
            "watermark_utc": (
                self.watermark_utc.isoformat() if self.watermark_utc else None
            ),
            "dirty_count": len(self.dirty_keys),
            "updated_count": len(self.updated_keys),
            "new_count": len(self.new_keys),
            "retired_count": len(self.retired_keys),
            "live_props_count": self.live_props_count,
            "active_live_props_count": self.active_live_props_count,
            "scored_rt_count": self.scored_rt_count,
            "missing_updated_at": self.missing_updated_at,
            "sample_updated_keys": self.sample_updated,
            "sample_new_keys": self.sample_new,
            "sample_retired_keys": self.sample_retired,
        }


async def detect_changed_props(db, sport: str) -> DeltaDetectionResult:
    """Detect which props have changed since the watermark for `sport`.

    Read-only: this function performs only `find` queries. It never writes
    to `live_props`, `prop_scores`, or any other collection.
    """
    sport = (sport or "").lower()
    result = DeltaDetectionResult(
        sport=sport,
        watermark_utc=await get_watermark_with_grace(db, sport),
    )

    live_coll = db[COLL("live_props", sport)]
    scored_coll = db[COLL("prop_scores", sport)]
    rt_tag = _rt_version_tag(sport)

    # Sport-agnostic canonical-key resolver — some sports persist
    # `canonical_key` on the raw live prop (MLB, via universal_odds_sync),
    # others compute it from raw fields (NBA). The adapter handles both.
    adapter = get_scoring_adapter(sport)
    resolve_key = adapter.canonical_key_from_raw

    # Projection-friendly view for canonical_key derivation. Pulls ONLY
    # the fields any adapter's `canonical_key_from_raw` needs, which is a
    # strict subset of `build_context` inputs — no CPU-heavy doc loads.
    _KEY_PROJECTION = {
        "_id": 0, "canonical_key": 1, "player_name": 1, "line": 1,
        "market": 1, "stat_type": 1, "stat_type_extracted": 1,
        "event_id": 1, "direction": 1, "recommendation": 1, "active": 1,
        "updated_at": 1,
    }

    # --- totals (cheap indexed counts) ---
    result.live_props_count = await live_coll.count_documents({})
    result.active_live_props_count = await live_coll.count_documents(
        {"$or": [{"active": {"$ne": False}}, {"active": {"$exists": False}}]}
    )
    result.scored_rt_count = await scored_coll.count_documents({"version_tag": rt_tag})

    # --- signal 1: updated_at > watermark (primary) ---
    updated_keys: Set[str] = set()
    if result.watermark_utc is not None:
        query = {"updated_at": {"$gt": result.watermark_utc}}
        async for doc in live_coll.find(query, _KEY_PROJECTION):
            ck = resolve_key(doc)
            if ck:
                updated_keys.add(ck)
    # Count rows missing the updated_at stamp (observability for the
    # odds-sync instrumentation rollout).
    result.missing_updated_at = await live_coll.count_documents(
        {"updated_at": {"$exists": False}}
    )

    # --- signal 2: new_keys = active live keys − scored RT keys ---
    # 2026-05-02 FIX: pre-filter active_live docs through the SAME
    # coverage filters the scorer applies (priceable + pp_playable).
    # Without this, the detector reported ~5.6k "new" keys that the
    # scorer immediately dropped at load_live_props → 0 rescored per
    # tick → board froze (real bug hit on NBA + MLB live pods).
    # Resolving adapter's load_live_props would re-query the DB; we
    # already have the docs here, so run the filters in-process.
    raw_active_docs: List[Dict[str, Any]] = []
    async for doc in live_coll.find(
        {"$or": [{"active": {"$ne": False}}, {"active": {"$exists": False}}]},
        _KEY_PROJECTION,
    ):
        raw_active_docs.append(doc)

    try:
        from services.scoring.coverage_filter import (
            filter_priceable, filter_pp_playable,
        )
        # The coverage filters need book-odds fields that aren't in
        # _KEY_PROJECTION. Re-query the full docs for ONLY the active
        # set so we can filter correctly. Small cost: this runs once
        # per tick and we already know exactly which ids to pull.
        full_docs_cursor = live_coll.find(
            {"$or": [{"active": {"$ne": False}}, {"active": {"$exists": False}}]},
            {"_id": 0},
        )
        full_docs = await full_docs_cursor.to_list(length=None)
        priceable, _cov = filter_priceable(full_docs, sport=sport)
        playable,  _pp  = filter_pp_playable(priceable, sport=sport)
        scorable_keys: Set[str] = set()
        for p in playable:
            k = resolve_key(p)
            if k:
                scorable_keys.add(k)
        active_live_keys = scorable_keys
    except Exception as _filter_exc:
        logger.warning(
            f"[DELTA:{sport}] coverage-filter pre-pass FAILED: "
            f"{_filter_exc!r} — falling back to unfiltered active set "
            "(may cause rescore-cap thrash)."
        )
        active_live_keys = set()
        for doc in raw_active_docs:
            ck = resolve_key(doc)
            if ck:
                active_live_keys.add(ck)

    scored_rt_keys: Set[str] = set()
    async for doc in scored_coll.find(
        {"version_tag": rt_tag}, {"_id": 0, "canonical_key": 1}
    ):
        ck = doc.get("canonical_key")
        if ck:
            scored_rt_keys.add(ck)

    new_keys = active_live_keys - scored_rt_keys

    # --- signal 3: retired = scored RT keys whose live row is inactive ---
    inactive_live_keys: Set[str] = set()
    async for doc in live_coll.find({"active": False}, _KEY_PROJECTION):
        ck = resolve_key(doc)
        if ck:
            inactive_live_keys.add(ck)
    retired_keys = scored_rt_keys & inactive_live_keys

    result.updated_keys = updated_keys
    result.new_keys = new_keys
    result.retired_keys = retired_keys
    result.dirty_keys = updated_keys | new_keys | retired_keys

    # Samples — deterministically ordered (sorted) so repeated inspect
    # calls on identical state yield identical samples.
    result.sample_updated = sorted(updated_keys)[:MAX_SAMPLE_KEYS]
    result.sample_new = sorted(new_keys)[:MAX_SAMPLE_KEYS]
    result.sample_retired = sorted(retired_keys)[:MAX_SAMPLE_KEYS]

    logger.info(
        f"[DELTA:{sport}] detect: watermark={result.watermark_utc} "
        f"updated={len(updated_keys)} new={len(new_keys)} "
        f"retired={len(retired_keys)} dirty={len(result.dirty_keys)}"
    )
    return result
