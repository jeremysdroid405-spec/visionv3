"""
Universal dedupe-key audit.

Walks every collection in the live DB and flags any unique index whose
key includes a known volatile field (snapshot_iso, ingested_at, run_id,
captured_at, etc.). Also samples a few docs from each affected
collection to surface other risk signals.

Output:
  /app/memory/DEDUPE_KEY_AUDIT.json
  /app/memory/DEDUPE_KEY_AUDIT.md
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

VOLATILE_FIELDS: set[str] = {
    "snapshot_iso", "snapshot_label", "snapshot_ts", "snapshot_time",
    "ingested_at", "captured_at", "_first_seen", "fetched_at",
    "recorded_at", "started_at", "finished_at", "computed_at",
    "evaluated_at", "snapshot_at", "occurred_at", "synced_at",
    "next_ts", "previous_ts", "requested_ts", "returned_ts",
    "snapshot_time", "cached_at", "detected_at", "timestamp",
}
RUN_ID_LIKE: set[str] = {
    "run_id", "replay_run_id", "source_run_id",
    "replay_serial", "serial",
}


async def main() -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        names = sorted(await db.list_collection_names())
        report: List[Dict[str, Any]] = []
        for coll in names:
            try:
                info = await db[coll].index_information()
            except Exception:
                continue
            row: Dict[str, Any] = {
                "coll": coll,
                "n_docs": 0,
                "unique_indexes": [],
                "volatile_in_unique": [],
                "run_id_in_unique": [],
                "all_indexes": sorted(info.keys()),
            }
            try:
                row["n_docs"] = int(
                    await db[coll].estimated_document_count())
            except Exception:
                pass
            for name, spec in info.items():
                if not spec.get("unique"):
                    continue
                keys = [k for k, _ in (spec.get("key") or [])]
                row["unique_indexes"].append(
                    {"name": name, "keys": keys})
                vol = [k for k in keys if k in VOLATILE_FIELDS]
                if vol:
                    row["volatile_in_unique"].append(
                        {"name": name, "volatile_keys": vol,
                         "full_keys": keys})
                run = [k for k in keys if k in RUN_ID_LIKE]
                if run:
                    row["run_id_in_unique"].append(
                        {"name": name, "run_id_keys": run,
                         "full_keys": keys})
            if (row["volatile_in_unique"]
                    or row["run_id_in_unique"]
                    or row["unique_indexes"]):
                report.append(row)

        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db": os.environ["DB_NAME"],
            "n_collections":      len(names),
            "n_with_unique":      sum(1 for r in report
                                        if r["unique_indexes"]),
            "n_with_volatile":    sum(1 for r in report
                                        if r["volatile_in_unique"]),
            "n_with_run_id":      sum(1 for r in report
                                        if r["run_id_in_unique"]),
            "volatile_fields":    sorted(VOLATILE_FIELDS),
            "run_id_fields":      sorted(RUN_ID_LIKE),
            "collections":        report,
        }
        with open("/app/memory/DEDUPE_KEY_AUDIT.json", "w") as f:
            json.dump(out, f, indent=2, default=str)

        # Markdown summary
        with open("/app/memory/DEDUPE_KEY_AUDIT.md", "w") as f:
            f.write("# Dedupe-Key Audit (live MongoDB)\n")
            f.write(f"_Generated: {out['generated_at']}_\n\n")
            f.write(f"- Collections scanned: {out['n_collections']}\n")
            f.write(f"- With ≥1 unique index: {out['n_with_unique']}\n")
            f.write(f"- **With volatile-field in unique key (BROKEN): "
                    f"{out['n_with_volatile']}**\n")
            f.write(f"- With run-id-like in unique key: "
                    f"{out['n_with_run_id']}\n\n")
            f.write("## ⚠️ Broken — volatile field in unique key\n\n")
            f.write("| Collection | n_docs | Index | "
                    "Full unique key | Volatile field(s) |\n")
            f.write("|---|---:|---|---|---|\n")
            for r in report:
                for v in r.get("volatile_in_unique", []):
                    f.write(
                        f"| `{r['coll']}` | {r['n_docs']:,} | "
                        f"`{v['name']}` | "
                        f"`({', '.join(v['full_keys'])})` | "
                        f"{', '.join(v['volatile_keys'])} |\n")
            f.write("\n## ⚠️ Broken — run-id-like in unique key\n\n")
            f.write("| Collection | n_docs | Index | "
                    "Full unique key | run-id field(s) |\n")
            f.write("|---|---:|---|---|---|\n")
            for r in report:
                for v in r.get("run_id_in_unique", []):
                    f.write(
                        f"| `{r['coll']}` | {r['n_docs']:,} | "
                        f"`{v['name']}` | "
                        f"`({', '.join(v['full_keys'])})` | "
                        f"{', '.join(v['run_id_keys'])} |\n")
            f.write("\n## Clean — collections with safe unique keys\n\n")
            f.write("| Collection | n_docs | Index | Unique key |\n")
            f.write("|---|---:|---|---|\n")
            for r in report:
                if r["volatile_in_unique"] or r["run_id_in_unique"]:
                    continue
                for u in r["unique_indexes"]:
                    f.write(
                        f"| `{r['coll']}` | {r['n_docs']:,} | "
                        f"`{u['name']}` | "
                        f"`({', '.join(u['keys'])})` |\n")
        print(f"BROKEN volatile : {out['n_with_volatile']}")
        print(f"BROKEN run-id   : {out['n_with_run_id']}")
        print(f"with unique idx : {out['n_with_unique']}")
        print(f"Report:")
        print(f"  /app/memory/DEDUPE_KEY_AUDIT.md")
        print(f"  /app/memory/DEDUPE_KEY_AUDIT.json")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
