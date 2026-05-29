"""
Team collections bootstrap CLI (Phase 1.A.2).

Wraps `services.team_master_hub.collections.ensure_team_collections`
so the operator can create the ten team-side collections + their
§1.2 indexes without going through HTTP.

Modes:
    --apply        (default) — create collections + indexes (idempotent)
    --status-only  — read-only report; no creates, no writes

Output is JSON on stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient

from services.team_master_hub.collections import (
    collections_status,
    ensure_team_collections,
)


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        if args.status_only:
            return await collections_status(db)
        return await ensure_team_collections(db)
    finally:
        client.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="team_collections_ensure",
        description="Bootstrap team-side collections + §1.2 indexes.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true",
                    help="(default) create collections + indexes")
    g.add_argument("--status-only", action="store_true",
                    help="read-only report; no creates")
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
