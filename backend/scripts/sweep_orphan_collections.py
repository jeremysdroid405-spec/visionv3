"""One-shot sweep: drop 9 orphan/archive/backup collections.

WHY
---
As of 2026-04-30, Mongo contained 9 collections totaling **861,813 docs
and 188.9MB** that were not written to, not read from, not referenced by
any code path in services/ or routes/. They are the residue of 8+ months
of refactors where collections were renamed but the old ones were never
dropped.

These orphans cause concrete harm:
- Confusion for new agents reading the DB (which collection is live?)
- Database-wide queries scan them needlessly
- Atlas tier storage cost
- Index memory footprint

The ONLY references in code are in docstring comments in
`services/config/collection_names.py`, plus one dead entry
(`prop_scores_archive` mapping) that's not imported anywhere.

WHAT THIS DOES
--------------
For each of the 9 orphan collections:
  1. Writes a manifest file to
     `/app/backend/data/snapshots/archives/` capturing:
       - doc count, size in bytes
       - all field names seen across a sample
       - oldest + newest doc `_id`-derived timestamps
       - representative sample (first + last 10 docs)
  2. Drops the collection.

The script is IDEMPOTENT. Running it after the sweep does nothing
(all 9 collections are gone).

SAFETY
------
- Explicit allowlist below — only these 9 collection names are ever
  touched. Modifying it requires a code change.
- Manifest write happens BEFORE drop. If manifest write fails, drop
  is skipped.
- Requires MANIFEST_DIR to be writable.

RECOVERY
--------
If you need to restore any of these collections:
- Mongo Atlas: use the daily snapshot backup (free tier has 2 retained).
- Local: `mongorestore --drop --nsInclude='<db>.<coll>' <snapshot_dir>`
- Manifest files preserve schema + sample for forensic reference.

Re-run: `cd /app/backend && python scripts/sweep_orphan_collections.py`
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorClient


# ── Allowlist: the 9 orphan collections identified on 2026-04-30. ────
ORPHAN_COLLECTIONS: List[str] = [
    "dg_cached_board_backup",
    "dg_events_cache_backup",
    "dg_live_props_backup",
    "dg_master_roster_backup",
    "dg_odds_cache_backup",
    "line_history_backup",
    "mlb_prop_scores_archive_stale_tags",
    "nba_prop_scores_archive_stale_tags",
    "referee_assignments_backup",
]

MANIFEST_DIR = pathlib.Path(
    "/app/backend/data/snapshots/archives"
)
SAMPLE_SIZE = 10


def _stringify(v: Any) -> Any:
    """Coerce a sampled doc to JSON-safe form."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_stringify(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _stringify(val) for k, val in v.items()}
    # datetime, ObjectId, etc.
    try:
        return str(v)
    except Exception:
        return repr(v)


async def _write_manifest(db, coll_name: str) -> pathlib.Path:
    coll = db[coll_name]
    stats = await db.command("collStats", coll_name)

    # First + last docs by _id order (insertion order on ObjectId).
    first_docs: List[Dict[str, Any]] = []
    last_docs: List[Dict[str, Any]] = []
    async for d in coll.find({}).sort("_id", 1).limit(SAMPLE_SIZE):
        first_docs.append(_stringify(d))
    async for d in coll.find({}).sort("_id", -1).limit(SAMPLE_SIZE):
        last_docs.append(_stringify(d))

    # Collect all field names seen across those samples.
    fields = set()
    for d in first_docs + last_docs:
        if isinstance(d, dict):
            fields.update(d.keys())

    # Derive oldest + newest _id timestamp (ObjectId → timestamp).
    first_ts = None
    last_ts = None
    if first_docs:
        try:
            _id = first_docs[0].get("_id")
            if _id and len(str(_id)) >= 8:
                ts = int(str(_id)[:8], 16)
                first_ts = datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).isoformat()
        except Exception:
            pass
    if last_docs:
        try:
            _id = last_docs[0].get("_id")
            if _id and len(str(_id)) >= 8:
                ts = int(str(_id)[:8], 16)
                last_ts = datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).isoformat()
        except Exception:
            pass

    manifest = {
        "collection": coll_name,
        "dropped_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "doc_count": stats.get("count", 0),
            "size_bytes": stats.get("size", 0),
            "avg_obj_size": stats.get("avgObjSize", 0),
            "storage_size": stats.get("storageSize", 0),
            "n_indexes": stats.get("nindexes", 0),
        },
        "field_schema": sorted(fields),
        "approx_oldest_doc_ts": first_ts,
        "approx_newest_doc_ts": last_ts,
        "sample_first_docs": first_docs,
        "sample_last_docs": last_docs,
    }

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    manifest_path = MANIFEST_DIR / f"{coll_name}_manifest_{ts}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    return manifest_path


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    present = set(await db.list_collection_names())

    total_dropped_rows = 0
    total_dropped_bytes = 0
    dropped: List[str] = []
    skipped: List[str] = []

    for coll_name in ORPHAN_COLLECTIONS:
        if coll_name not in present:
            skipped.append(f"{coll_name} (already absent)")
            continue

        try:
            manifest_path = await _write_manifest(db, coll_name)
        except Exception as e:
            print(f"  ! {coll_name}: manifest failed — {e}. SKIP drop.")
            skipped.append(f"{coll_name} (manifest failed)")
            continue

        stats = await db.command("collStats", coll_name)
        n = stats.get("count", 0)
        size = stats.get("size", 0)
        total_dropped_rows += n
        total_dropped_bytes += size

        await db.drop_collection(coll_name)
        dropped.append(coll_name)
        print(
            f"  ✓ {coll_name}: {n:,} docs, "
            f"{size / 1024 / 1024:.1f}MB → dropped. "
            f"Manifest: {manifest_path.name}"
        )

    print()
    print(f"Dropped: {len(dropped)} collections, "
          f"{total_dropped_rows:,} docs, "
          f"{total_dropped_bytes / 1024 / 1024:.1f}MB")
    if skipped:
        print(f"Skipped: {len(skipped)}")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    asyncio.run(main())
