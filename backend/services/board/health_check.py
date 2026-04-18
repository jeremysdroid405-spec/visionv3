"""
Canonical Collection Health Check — one-shot startup audit.

Compares the runtime `config/collections.py` resolution against the
actual MongoDB namespace and logs specific warnings when overrides
have gone stale during slow-roll Phase B/C/D migrations.

Pure log-only. Never raises, never blocks startup. Runs exactly once
at boot.

Warning classes (each emitted with a distinct prefix so ops can
grep + alert independently):

  OVERRIDE_MISSING  : config SPORT_OVERRIDES points to a legacy name
                      that no longer exists in the DB. Symptom: every
                      read returns 0 docs silently.
  CANONICAL_BLEED   : both the canonical collection AND the legacy
                      override collection exist and carry data. Readers
                      resolve to legacy; writes may be bleeding to
                      canonical from a partially-migrated writer.
  CANONICAL_READY   : canonical collection exists with data but reads
                      still resolve to legacy (override not retired).
                      Flip the SPORT_OVERRIDES line to complete the
                      migration.
  LEGACY_EMPTY      : legacy override resolves to an empty collection
                      while the canonical does NOT exist. Stale config —
                      override is pointing at nothing.

The function always returns a structured dict of findings so the caller
can log a final tally line. Never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from config.collections import (
    SUPPORTED_SPORTS,
    CANONICAL_CONCEPTS,
    resolve,
    canonical_name,
)

logger = logging.getLogger(__name__)


async def _safe_count(db, coll_name: str) -> int:
    """Estimated count; returns -1 if the collection is inaccessible."""
    try:
        return int(await db[coll_name].estimated_document_count())
    except Exception:
        return -1


async def run_canonical_collection_health_check(db) -> Dict[str, Any]:
    """One-shot audit. Logs warnings; never blocks startup."""
    try:
        actual_names = set(await db.list_collection_names())
    except Exception as e:
        logger.warning(f"[COLL_HEALTH] listCollections failed; audit skipped: {e}")
        return {"skipped": True, "reason": str(e)}

    warnings: List[str] = []
    aligned = 0
    findings: Dict[str, List[Dict[str, Any]]] = {
        "OVERRIDE_MISSING": [],
        "CANONICAL_BLEED": [],
        "CANONICAL_READY": [],
        "LEGACY_EMPTY": [],
    }

    for sport in SUPPORTED_SPORTS:
        for concept in CANONICAL_CONCEPTS:
            current = resolve(sport, concept)
            canon = canonical_name(sport, concept)

            if current == canon:
                aligned += 1
                continue  # no override — already canonical

            # Override is in force. Audit the two namespaces.
            current_exists = current in actual_names
            canon_exists = canon in actual_names

            if not current_exists and not canon_exists:
                # Both absent. Not necessarily an error (sport may not
                # ingest this concept yet), but worth flagging once.
                continue

            if not current_exists:
                # Legacy override points at a non-existent collection.
                msg = (
                    f"[COLL_HEALTH] OVERRIDE_MISSING {sport}.{concept}: "
                    f"override points to '{current}' which does NOT "
                    f"exist (canonical='{canon}', exists={canon_exists})"
                )
                logger.warning(msg)
                warnings.append(msg)
                findings["OVERRIDE_MISSING"].append({
                    "sport": sport, "concept": concept,
                    "current": current, "canonical": canon,
                    "canonical_exists": canon_exists,
                })
                continue

            legacy_count = await _safe_count(db, current)

            if canon_exists:
                canon_count = await _safe_count(db, canon)
                if canon_count > 0 and legacy_count > 0:
                    msg = (
                        f"[COLL_HEALTH] CANONICAL_BLEED {sport}.{concept}: "
                        f"legacy '{current}'={legacy_count} docs AND "
                        f"canonical '{canon}'={canon_count} docs both "
                        f"populated; reads resolve to legacy"
                    )
                    logger.warning(msg)
                    warnings.append(msg)
                    findings["CANONICAL_BLEED"].append({
                        "sport": sport, "concept": concept,
                        "current": current, "canonical": canon,
                        "legacy_count": legacy_count,
                        "canonical_count": canon_count,
                    })
                elif canon_count > 0:
                    msg = (
                        f"[COLL_HEALTH] CANONICAL_READY {sport}.{concept}: "
                        f"canonical '{canon}'={canon_count} docs exists "
                        f"but override still routes reads to "
                        f"'{current}'={legacy_count}. Migration ready to "
                        f"cut over (flip SPORT_OVERRIDES)."
                    )
                    logger.warning(msg)
                    warnings.append(msg)
                    findings["CANONICAL_READY"].append({
                        "sport": sport, "concept": concept,
                        "current": current, "canonical": canon,
                        "legacy_count": legacy_count,
                        "canonical_count": canon_count,
                    })
                continue

            # Canonical absent.
            if legacy_count == 0:
                msg = (
                    f"[COLL_HEALTH] LEGACY_EMPTY {sport}.{concept}: "
                    f"override points at '{current}' which is empty "
                    f"(canonical '{canon}' does not exist). Writer may "
                    f"have gone dead or never wrote this concept."
                )
                logger.warning(msg)
                warnings.append(msg)
                findings["LEGACY_EMPTY"].append({
                    "sport": sport, "concept": concept,
                    "current": current, "canonical": canon,
                })

    total_pairs = len(SUPPORTED_SPORTS) * len(CANONICAL_CONCEPTS)
    pending = total_pairs - aligned
    if warnings:
        logger.warning(
            f"[COLL_HEALTH] Audit complete — {len(warnings)} warning(s) "
            f"across {pending} pending (non-canonical) pair(s); "
            f"{aligned}/{total_pairs} pair(s) already canonical"
        )
    else:
        logger.info(
            f"[COLL_HEALTH] Audit complete — 0 warnings, "
            f"{aligned}/{total_pairs} pair(s) canonical, "
            f"{pending} pending (overrides align with existing data)"
        )
    return {
        "skipped": False,
        "total_pairs": total_pairs,
        "aligned": aligned,
        "pending": pending,
        "warning_count": len(warnings),
        "findings": findings,
    }
