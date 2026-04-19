"""
Shadow Divergence Monitor — Wave 1 observability.

Runs every 60 seconds. For each concept registered in `_SHADOW_WRITES`
(via `services.config.collection_names.COLL.active_shadows()`), it:

1. Counts documents in the primary and shadow collections.
2. Computes a signed delta_pct = (shadow - primary) / max(primary, 1) * 100.
3. Samples up to SAMPLE_SIZE stable-identified docs from the primary and
   looks them up in the shadow, computing a structural hash of each pair
   (excluding volatile fields `_id` and `fetched_at`).
4. Writes a row to the shared `board_drift_ledger` collection with the
   resulting metrics.
5. Logs a WARNING if |delta_pct| > DELTA_PCT_ALERT or hash_match_rate <
   HASH_MATCH_ALERT.

This module is the ONLY source-of-truth for shadow health during Wave 1.
Operators gate Wave 2 (read-flip) on a clean window of ledger entries.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


# ----- Thresholds ----------------------------------------------------------
SAMPLE_SIZE = 50
DELTA_PCT_ALERT = 1.0        # absolute %
HASH_MATCH_ALERT = 0.99      # rate in [0, 1]
VOLATILE_FIELDS = {"_id", "fetched_at", "updated_at", "last_synced_at"}

# Concept → stable-identifier field used to pair primary docs to shadow
# docs during hash sampling. If a concept isn't listed, we fall back to
# `_id`.
_STABLE_KEY: Dict[str, str] = {
    # Odds-API event documents carry the sportsbook's event id under `id`.
    "events_cache": "id",
}


# ----- Helpers -------------------------------------------------------------
def _stable_hash(doc: Dict[str, Any]) -> str:
    """Return a SHA-256 hash of `doc` with volatile fields removed.

    The doc is JSON-serialised with sorted keys so logically-identical
    documents hash to identical digests irrespective of key order.
    """
    clean = {k: v for k, v in doc.items() if k not in VOLATILE_FIELDS}
    payload = json.dumps(clean, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _sample_hash_rate(
    primary_coll,
    shadow_coll,
    stable_key: str,
    sample_size: int,
) -> Tuple[int, int]:
    """Return (matched, sampled) counts comparing up to `sample_size`
    primary docs against their shadow counterparts.

    If the primary is empty OR a sampled doc has no stable key, the pair
    is skipped (not counted as a mismatch).
    """
    pipeline = [{"$sample": {"size": sample_size}}]
    samples = await primary_coll.aggregate(pipeline).to_list(length=sample_size)
    sampled = 0
    matched = 0
    for doc in samples:
        # Pick the lookup key/value. Prefer the configured stable key; if
        # that field is missing on the doc, we cannot pair it against the
        # shadow reliably (shadow docs have independent `_id`s), so skip.
        key_value = doc.get(stable_key) if stable_key != "_id" else doc.get("_id")
        if key_value is None:
            # Can't pair this doc; don't count against match rate.
            continue
        query = {stable_key: key_value}
        shadow_doc = await shadow_coll.find_one(query)
        sampled += 1
        if shadow_doc is None:
            continue
        if _stable_hash(doc) == _stable_hash(shadow_doc):
            matched += 1
    return matched, sampled


async def _snapshot_concept(
    db: AsyncIOMotorDatabase,
    concept: str,
    sport: str,
    shadow_name: str,
) -> Dict[str, Any]:
    """Compute one divergence snapshot for a single (concept, sport)."""
    primary_name = COLL(concept, sport)
    primary = db[primary_name]
    shadow = db[shadow_name]

    primary_count, shadow_count = await asyncio.gather(
        primary.count_documents({}),
        shadow.count_documents({}),
    )

    delta = shadow_count - primary_count
    denom = max(primary_count, 1)
    delta_pct = round(delta / denom * 100.0, 4)

    stable_key = _STABLE_KEY.get(concept, "_id")
    sample_to_use = min(SAMPLE_SIZE, primary_count)
    matched = sampled = 0
    if sample_to_use > 0:
        matched, sampled = await _sample_hash_rate(
            primary, shadow, stable_key, sample_to_use
        )

    hash_match_rate = round(matched / sampled, 4) if sampled else 1.0

    snapshot = {
        "observed_at": datetime.now(timezone.utc),
        "wave": 1,
        "phase": "shadow_write",
        "sport": sport,
        "concept": concept,
        "primary_collection": primary_name,
        "shadow_collection": shadow_name,
        "primary_count": primary_count,
        "shadow_count": shadow_count,
        "delta": delta,
        "delta_pct": delta_pct,
        "sampled": sampled,
        "hash_matched": matched,
        "hash_match_rate": hash_match_rate,
        "stable_key": stable_key,
    }

    alerts: List[str] = []
    if abs(delta_pct) > DELTA_PCT_ALERT:
        alerts.append(f"DELTA_PCT={delta_pct}%")
    if sampled and hash_match_rate < HASH_MATCH_ALERT:
        alerts.append(f"HASH_MATCH_RATE={hash_match_rate}")
    if alerts:
        snapshot["alerts"] = alerts
        logger.warning(
            "[SHADOW_DIVERGENCE] %s/%s primary=%s shadow=%s alerts=%s",
            sport, concept, primary_count, shadow_count, alerts,
        )

    return snapshot


async def _ensure_shadow_indexes(db: AsyncIOMotorDatabase) -> None:
    """On first tick, mirror the primary's indexes onto each shadow.

    Idempotent: `create_index` is a no-op if the index already exists with
    the same name + key spec. We skip the implicit `_id_` index.
    """
    for (concept, sport), shadow_name in COLL.active_shadows().items():
        primary_name = COLL(concept, sport)
        try:
            specs = await db[primary_name].index_information()
        except Exception as e:
            logger.error(
                "[SHADOW_INDEX_MIRROR] %s.index_information() failed: %r",
                primary_name, e,
            )
            continue
        for idx_name, spec in specs.items():
            if idx_name == "_id_":
                continue
            keys = spec.get("key")
            if not keys:
                continue
            try:
                await db[shadow_name].create_index(
                    list(keys),
                    name=idx_name,
                    unique=bool(spec.get("unique", False)),
                    sparse=bool(spec.get("sparse", False)),
                    background=True,
                )
            except Exception as e:
                logger.error(
                    "[SHADOW_INDEX_MIRROR] create_index %s on %s failed: %r",
                    idx_name, shadow_name, e,
                )


# ----- Public API ---------------------------------------------------------
_indexes_mirrored = False


async def run_shadow_divergence_check(db: AsyncIOMotorDatabase) -> Optional[Dict[str, Any]]:
    """One tick of the monitor. Intended to be called by APScheduler every
    60 seconds. Returns a summary dict (useful for tests)."""
    global _indexes_mirrored

    shadows = COLL.active_shadows()
    if not shadows:
        # No shadow concepts registered — nothing to do. This lets operators
        # leave the scheduler job permanently installed.
        return {"checked": 0, "alerts": 0}

    if not _indexes_mirrored:
        try:
            await _ensure_shadow_indexes(db)
        finally:
            # Set even on failure — we don't want to retry this on every
            # tick. A failed mirror shows up as index-less shadow in the
            # ledger via elevated latency/drift, which the operator will
            # see.
            _indexes_mirrored = True

    ledger_name = COLL.shared("board_drift_ledger")
    ledger = db[ledger_name]

    snapshots: List[Dict[str, Any]] = []
    alert_count = 0
    for (concept, sport), shadow_name in shadows.items():
        try:
            snap = await _snapshot_concept(db, concept, sport, shadow_name)
        except Exception as e:
            logger.exception(
                "[SHADOW_DIVERGENCE] snapshot failed for %s/%s: %r",
                sport, concept, e,
            )
            continue
        snapshots.append(snap)
        if snap.get("alerts"):
            alert_count += 1

    if snapshots:
        try:
            await ledger.insert_many(snapshots, ordered=False)
        except Exception as e:
            logger.error("[SHADOW_DIVERGENCE] ledger insert failed: %r", e)

    return {"checked": len(snapshots), "alerts": alert_count}
