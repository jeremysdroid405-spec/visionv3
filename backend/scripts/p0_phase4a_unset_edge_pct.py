#!/usr/bin/env python3
"""
P0 Phase 4A — `$unset edge_pct` migration.

This script is the SSOT cleanup for the persisted-legacy-field
violation surfaced by the writer-discovery audit. It:
  1. Captures BEFORE counts of `edge_pct` presence per collection.
  2. Runs `$unset` on `nba_prop_scores` and `mlb_prop_scores`
     (the two collections that were carrying it; cached_board
     collections never persisted the alias per the same audit).
  3. Captures AFTER counts to prove zero leakage.

Idempotent: rerunning produces 0 modified docs and 0 remaining
edge_pct presence. Safe to re-execute as part of CI / smoke checks.

Outputs JSON for log-friendly downstream parsing.
"""
import os
import asyncio
import datetime as dt
import json
from motor.motor_asyncio import AsyncIOMotorClient


COLLS = ("nba_prop_scores", "mlb_prop_scores")


async def main() -> int:
    # Load .env if MONGO_URL not already exported
    if "MONGO_URL" not in os.environ:
        env_path = "/app/backend/.env"
        try:
            for ln in open(env_path):
                if "=" in ln and not ln.strip().startswith("#"):
                    k, _, v = ln.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        except FileNotFoundError:
            pass

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    out = {
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "before": {},
        "modifications": {},
        "after": {},
        "ok": True,
    }
    for coll in COLLS:
        c = await db[coll].count_documents({"edge_pct": {"$exists": True}})
        out["before"][coll] = c

    for coll in COLLS:
        res = await db[coll].update_many(
            {"edge_pct": {"$exists": True}},
            {"$unset": {"edge_pct": ""}},
        )
        out["modifications"][coll] = {
            "matched": res.matched_count,
            "modified": res.modified_count,
        }

    for coll in COLLS:
        c = await db[coll].count_documents({"edge_pct": {"$exists": True}})
        out["after"][coll] = c
        if c != 0:
            out["ok"] = False

    out["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
