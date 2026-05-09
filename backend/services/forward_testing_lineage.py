"""Forward-Testing Lineage Boundary
================================

Read-only reporting filter that separates the **legacy `vk_*` ranker era**
from the **modern SSOT (sportsbook-anchored) era**.

Why this exists
---------------
Audits in `/app/audit_reports/fl_goblin_lineage_findings_2026-05-09.md`
proved that pre-2026-04-25 `forward_test_outcomes` were produced by a
retired ranker that did NOT require sportsbook reference odds. Mixing
those rows with post-cutover modern-SSOT data invalidates ROI /
calibration metrics because the two systems are not statistically
comparable (different scoring contracts, different gates, different
fixes landed since).

This module is **strictly a reporting boundary**:

  * NO mutation of historical rows.
  * NO deletion.
  * NO change to scoring / gates / tiers / settlement / odds routing.

It only:

  1. Defines the canonical cutoff date.
  2. Provides a `match_filter()` helper to inject `capture_date` clauses.
  3. Provides `lineage_metadata()` to enrich every reporting response with
     dataset-generation provenance + counts + warnings.

Endpoints integrate via:

    cutoff_filter = lineage_filter(include_legacy=False)
    match_query.update(cutoff_filter)
    metadata = await lineage_metadata(db, collection, match_base, include_legacy)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Canonical cutoff. Inclusive on the modern side.
# ---------------------------------------------------------------------------
MODERN_SSOT_CUTOFF: str = "2026-04-25"
MODERN_SSOT_CUTOFF_ISO: str = "2026-04-25T00:00:00Z"

LEGACY_GENERATION = "legacy_vk"
MODERN_GENERATION = "modern_ssot"
MIXED_GENERATION = "mixed"

MIXED_GENERATION_WARNING = (
    "Mixed legacy_vk and modern_ssot datasets are not statistically "
    "comparable due to architectural changes (reference-odds chain port, "
    "VK2 combo routing fix, no_reference_market enforcement, injury "
    "redistribution corrections, WZ ladder, ROI math). Treat combined "
    "metrics as informational only."
)


def lineage_filter(include_legacy: bool = False) -> Dict[str, Any]:
    """Return a Mongo match clause that respects the modern-SSOT cutoff.

    * `include_legacy=False` (default): only rows with
      `capture_date >= MODERN_SSOT_CUTOFF`.
    * `include_legacy=True`: empty filter — caller is expected to surface
      the mixed-generation warning explicitly.

    Returns
    -------
    dict
        A Mongo `$match`-compatible fragment. Empty dict when
        `include_legacy=True`.
    """
    if include_legacy:
        return {}
    return {"capture_date": {"$gte": MODERN_SSOT_CUTOFF}}


def merge_filter(base: Optional[Dict[str, Any]], include_legacy: bool) -> Dict[str, Any]:
    """Merge a caller's existing match query with the lineage filter.

    Caller's `capture_date` clause is preserved when present and the
    lineage cutoff is applied via an `$and` to avoid silent overwrites.
    """
    base = dict(base or {})
    cutoff = lineage_filter(include_legacy)
    if not cutoff:
        return base
    if "capture_date" in base:
        existing = base["capture_date"]
        # If existing is a dict (range), merge cutoff in via $and to keep
        # both bounds. Otherwise fall back to $and.
        if isinstance(existing, dict):
            merged = dict(existing)
            cutoff_inner = cutoff["capture_date"]
            for k, v in cutoff_inner.items():
                if k not in merged:
                    merged[k] = v
                else:
                    # Resolve overlap by taking the more restrictive bound.
                    if k == "$gte":
                        merged[k] = max(merged[k], v)
                    elif k == "$lt":
                        merged[k] = min(merged[k], v)
                    else:
                        merged[k] = v
            base["capture_date"] = merged
            return base
        # Scalar capture_date: AND in the cutoff.
        return {"$and": [base, cutoff]}
    base.update(cutoff)
    return base


async def lineage_metadata(
    db,
    collection_name: str,
    base_match: Optional[Dict[str, Any]] = None,
    include_legacy: bool = False,
) -> Dict[str, Any]:
    """Return a metadata block describing dataset generations covered.

    Reads counts from the target collection without modifying anything.

    Parameters
    ----------
    db : motor database handle
    collection_name : "forward_test_outcomes" or "forward_test_snapshots"
    base_match : the caller's match clause WITHOUT the lineage cutoff
        applied. Counts are computed against `base_match` so the metadata
        accurately reflects the caller's filter (sport, tier, days, ...).
    include_legacy : whether legacy rows are part of the response

    Returns
    -------
    {
      "dataset_generation": "modern_ssot" | "legacy_vk" | "mixed",
      "modern_ssot_cutoff": "2026-04-25",
      "include_legacy_flag": bool,
      "row_counts": {
          "legacy_vk": int,
          "modern_ssot": int,
          "total": int
      },
      "warning": str | None
    }
    """
    base_match = dict(base_match or {})
    # Strip any caller-level capture_date clause so counts reflect the
    # full universe under the OTHER filters (sport / tier / outcome).
    counting_match = {k: v for k, v in base_match.items() if k != "capture_date"}

    coll = db[collection_name]
    legacy_match = dict(counting_match)
    legacy_match["capture_date"] = {"$lt": MODERN_SSOT_CUTOFF}
    modern_match = dict(counting_match)
    modern_match["capture_date"] = {"$gte": MODERN_SSOT_CUTOFF}

    n_legacy = await coll.count_documents(legacy_match)
    n_modern = await coll.count_documents(modern_match)

    if include_legacy and n_legacy > 0 and n_modern > 0:
        generation = MIXED_GENERATION
        warning = MIXED_GENERATION_WARNING
    elif include_legacy and n_legacy > 0:
        generation = LEGACY_GENERATION
        warning = None
    else:
        generation = MODERN_GENERATION
        warning = None

    return {
        "dataset_generation": generation,
        "modern_ssot_cutoff": MODERN_SSOT_CUTOFF,
        "include_legacy_flag": bool(include_legacy),
        "row_counts": {
            "legacy_vk": n_legacy,
            "modern_ssot": n_modern,
            "total": n_legacy + n_modern,
            "excluded_from_official_reporting": (
                n_legacy if not include_legacy else 0
            ),
        },
        "warning": warning,
    }


__all__ = [
    "MODERN_SSOT_CUTOFF",
    "MODERN_SSOT_CUTOFF_ISO",
    "LEGACY_GENERATION",
    "MODERN_GENERATION",
    "MIXED_GENERATION",
    "MIXED_GENERATION_WARNING",
    "lineage_filter",
    "merge_filter",
    "lineage_metadata",
]
