"""
Team Master Hub seeder.

Pure transformation + DB writer for the team_master_hub collection.
Reads the deterministic JSON seed at
`backend/data/team_master_hub_seed.json` and upserts each team keyed
by `team_id`.

Hard limits (Phase 1.A.1):
  - PREVIEW POD ONLY.
  - No SGO API calls, no production touch, no historical ingest.
  - Idempotent: re-running the seeder produces zero net changes
    when input is unchanged.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §1.2.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pymongo import UpdateOne

SEED_PATH = Path("/app/backend/data/team_master_hub_seed.json")
COLLECTION_NAME = "team_master_hub"


def load_seed_doc(path: Path = SEED_PATH) -> Dict[str, Any]:
    """Read + parse the seed JSON. Pure I/O, no DB."""
    with path.open() as f:
        return json.load(f)


def build_upsert_ops(seed_doc: Dict[str, Any]) -> List[UpdateOne]:
    """Transform the seed doc into a list of `UpdateOne` upserts.

    Operational fields injected here (not in the JSON):
      - `active: True`        — default for fresh seeds (via $set).
      - `seed_version: …`     — provenance, refreshed each run (via $set).
      - `seeded_at: <iso utc>`— set ONLY on first insert
                                  (via $setOnInsert) so re-runs against
                                  unchanged content produce a true no-op
                                  (`modified_count == 0`).

    `team_id` is the upsert key.
    """
    seed_version = seed_doc.get("seed_version", "unknown")
    now_iso = datetime.now(timezone.utc).isoformat()
    ops: List[UpdateOne] = []
    for team in seed_doc.get("teams", []):
        doc = dict(team)  # shallow copy — never mutate the input
        doc["active"] = True
        doc["seed_version"] = seed_version
        ops.append(UpdateOne(
            {"team_id": doc["team_id"]},
            {"$set": doc, "$setOnInsert": {"seeded_at": now_iso}},
            upsert=True,
        ))
    return ops


async def ensure_indexes(db) -> List[str]:
    """Create the four indexes required by Phase 1.A.1 spec.

    - `team_id` UNIQUE (canonical SSOT identity)
    - `sport`   (filter by league)
    - `active`  (hide retired franchises)
    - `external_ids.sgo` SPARSE (some teams have no SGO mapping yet)

    Returns the list of index names created (or already present).
    """
    coll = db[COLLECTION_NAME]
    names: List[str] = []
    names.append(await coll.create_index("team_id", unique=True,
                                          name="ix_team_id_unique"))
    names.append(await coll.create_index("sport", name="ix_sport"))
    names.append(await coll.create_index("active", name="ix_active"))
    names.append(await coll.create_index("external_ids.sgo",
                                          sparse=True,
                                          name="ix_external_ids_sgo_sparse"))
    return names


async def seed_team_master_hub(db, seed_path: Path = SEED_PATH) -> Dict[str, Any]:
    """Run the full seed flow: indexes + bulk upserts.

    Returns a summary doc consumable by the admin UI / CLI.
    """
    seed_doc = load_seed_doc(seed_path)
    indexes = await ensure_indexes(db)
    ops = build_upsert_ops(seed_doc)
    if not ops:
        return {
            "ok": True, "n_upserts": 0, "matched": 0, "modified": 0,
            "upserted": 0, "indexes": indexes,
            "seed_version": seed_doc.get("seed_version"),
            "counts": seed_doc.get("counts"),
            "note": "seed was empty",
        }
    result = await db[COLLECTION_NAME].bulk_write(ops, ordered=False)
    return {
        "ok": True,
        "n_upserts": len(ops),
        "matched":   result.matched_count,
        "modified":  result.modified_count,
        "upserted":  len(result.upserted_ids or {}),
        "indexes":   indexes,
        "seed_version": seed_doc.get("seed_version"),
        "counts":    seed_doc.get("counts"),
    }


def diff_seed_vs_collection_docs(
    seed_doc: Dict[str, Any],
    existing_docs: List[Dict[str, Any]],
) -> Tuple[List[str], List[str], List[str]]:
    """Pure compare between a seed and a snapshot of the collection.

    Returns (in_seed_only, in_db_only, in_both). Used by the audit
    layer to spot drift between the seed and the live collection.
    """
    seed_ids = {t["team_id"] for t in seed_doc.get("teams", [])}
    db_ids = {d["team_id"] for d in existing_docs if "team_id" in d}
    return (
        sorted(seed_ids - db_ids),
        sorted(db_ids - seed_ids),
        sorted(seed_ids & db_ids),
    )


# ─────────────────────────────────────────────────────────────────────
# Audit layer — coverage report for the live `team_master_hub` coll.
# Pure read path: never writes, never calls SGO. Surfaces the four
# health signals required by Phase 1.A.1 spec:
#   total, by_sport, missing_sgo, duplicates, inactive
# Plus `indexes_present` so the operator can verify the index spec
# matches what `ensure_indexes` claims to create.
# ─────────────────────────────────────────────────────────────────────
async def audit_team_master_hub(db) -> Dict[str, Any]:
    """Run the coverage audit. Pure read, no mutations.

    Returns a structured report; the seed CLI + admin endpoint both
    surface this same dict verbatim.
    """
    coll = db[COLLECTION_NAME]

    total = await coll.count_documents({})

    # ── by_sport: { sport: count } ────────────────────────────────
    by_sport: Dict[str, int] = {}
    async for d in coll.aggregate(
        [{"$group": {"_id": "$sport", "n": {"$sum": 1}}}]
    ):
        sport = d.get("_id") or "(none)"
        by_sport[str(sport)] = int(d.get("n", 0))

    # ── missing_sgo: teams without an SGO external_id (sorted) ────
    missing_sgo: List[str] = []
    async for d in coll.find(
        {"$or": [{"external_ids.sgo": {"$exists": False}},
                  {"external_ids.sgo": None}]},
        {"_id": 0, "team_id": 1},
    ).sort("team_id", 1):
        if "team_id" in d:
            missing_sgo.append(d["team_id"])

    # ── duplicates: team_ids appearing more than once ─────────────
    duplicates: List[Dict[str, Any]] = []
    async for d in coll.aggregate([
        {"$group": {"_id": "$team_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"_id": 1}},
    ]):
        duplicates.append({"team_id": d["_id"], "count": int(d["n"])})

    # ── inactive: explicitly active=False (sorted) ────────────────
    inactive: List[str] = []
    async for d in coll.find(
        {"active": False},
        {"_id": 0, "team_id": 1},
    ).sort("team_id", 1):
        if "team_id" in d:
            inactive.append(d["team_id"])

    # ── indexes_present: report what's actually on the collection ─
    indexes_present: List[str] = []
    try:
        idx_info = await coll.index_information()
        indexes_present = sorted(idx_info.keys())
    except Exception:  # noqa: BLE001
        # Collection may not exist yet — report empty list.
        indexes_present = []

    return {
        "ok": True,
        "collection": COLLECTION_NAME,
        "total": int(total),
        "by_sport": dict(sorted(by_sport.items())),
        "missing_sgo": missing_sgo,
        "missing_sgo_count": len(missing_sgo),
        "duplicates": duplicates,
        "duplicates_count": len(duplicates),
        "inactive": inactive,
        "inactive_count": len(inactive),
        "indexes_present": indexes_present,
    }


async def seed_and_audit(
    db,
    seed_path: Path = SEED_PATH,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run the seeder (or simulate it) and return both seed + audit
    payloads in a single dict.

    `dry_run=True` skips index creation and bulk_write. The function
    still loads the seed JSON and returns the audit of the CURRENT
    collection state (which the operator can compare against the
    `seed_preview` block to anticipate what a real run would do).
    """
    seed_doc = load_seed_doc(seed_path)
    if dry_run:
        audit = await audit_team_master_hub(db)
        return {
            "ok": True,
            "dry_run": True,
            "seed_preview": {
                "seed_version": seed_doc.get("seed_version"),
                "counts": seed_doc.get("counts"),
                "n_ops_would_run": len(seed_doc.get("teams", [])),
            },
            "audit": audit,
        }
    seed_result = await seed_team_master_hub(db, seed_path)
    audit = await audit_team_master_hub(db)
    return {
        "ok": True,
        "dry_run": False,
        "seed": seed_result,
        "audit": audit,
    }
