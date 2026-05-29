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
      - `active: True` — default for fresh seeds.
      - `seed_version` / `seeded_at` — provenance.

    `team_id` is the upsert key. Re-running with the same seed
    version produces no-op updates (Mongo writes a doc identical to
    the existing one) thanks to deterministic JSON ordering.
    """
    seed_version = seed_doc.get("seed_version", "unknown")
    now_iso = datetime.now(timezone.utc).isoformat()
    ops: List[UpdateOne] = []
    for team in seed_doc.get("teams", []):
        doc = dict(team)  # shallow copy — never mutate the input
        doc["active"] = True
        doc["seed_version"] = seed_version
        doc["seeded_at"] = now_iso
        ops.append(UpdateOne({"team_id": doc["team_id"]},
                             {"$set": doc},
                             upsert=True))
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
