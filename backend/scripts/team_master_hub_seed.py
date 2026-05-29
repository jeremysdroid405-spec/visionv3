"""
Team Master Hub seeder CLI (Phase 1.A.1).

Operator entrypoint for re-seeding the `team_master_hub` collection
WITHOUT going through HTTP. Uses the IDENTICAL service logic as the
admin endpoint — `services.team_master_hub.seed_and_audit` — so there
is exactly one code path for the seed flow.

Examples:
    # Dry-run (no writes, returns audit of current state + preview)
    python -m scripts.team_master_hub_seed --dry-run

    # Real run (idempotent — re-running with the same seed is a no-op)
    python -m scripts.team_master_hub_seed

    # Audit only (no seed flow at all)
    python -m scripts.team_master_hub_seed --audit-only

Output is JSON on stdout so it can be piped into `jq` or saved.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient

from services.team_master_hub import (
    audit_team_master_hub,
    seed_and_audit,
)


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    try:
        db = client[db_name]
        if args.audit_only:
            return await audit_team_master_hub(db)
        return await seed_and_audit(db, dry_run=args.dry_run)
    finally:
        client.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="team_master_hub_seed",
        description="Idempotent seeder for the team_master_hub collection.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                       help="parse seed + run audit but skip all writes")
    mode.add_argument("--audit-only", action="store_true",
                       help="skip seed entirely, just return the audit")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
