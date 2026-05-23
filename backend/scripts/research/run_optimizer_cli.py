"""
run_optimizer_cli — out-of-process executor for /api/emergent-admin/optimizer/run.

Reads the pre-persisted request from `optimizer_runs.{run_id}.request`
and runs the same scoring/ranking logic that previously lived inline in
routes.emergent_admin.optimizer. By running here, the heavy CPU/IO
sits outside the FastAPI event loop and inherits the worker's resource
caps (nice +10, RLIMIT_AS, OOM bias, hard timeout).

CLI:
    python -m scripts.research.run_optimizer_cli --run-id <opt_xxxxx>
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
for env_path in ("/app/backend/.env", "/var/www/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient


async def _main(run_id: str) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    doc = await db["optimizer_runs"].find_one({"run_id": run_id}, {"_id": 0})
    if not doc:
        print(f"[optimizer_cli] run_id not found: {run_id}", file=sys.stderr)
        return 2
    req = doc.get("request") or {}
    if not req:
        print(f"[optimizer_cli] no request payload on run {run_id}", file=sys.stderr)
        return 3

    # Import lazily so this script can be exercised in isolation.
    # NOTE: this is the SAME code path the API used to run inline.
    from routes.emergent_admin.optimizer import (
        OptimizerRunBody, _RUNS, _run_optimizer,
    )

    # Re-hydrate the in-process state slot so progress + best-so-far is
    # written back to optimizer_runs by the original implementation.
    _RUNS[run_id] = {
        "run_id": run_id, "status": "queued",
        "combos_tested": 0, "combos_skipped_low_sample": 0,
        "cells_skipped_empty": 0, "cells_done": 0,
        "best": None, "failures": [], "cancelled": False,
        "results": [],
        "agent_id": doc.get("agent_id", "worker"),
    }
    body = OptimizerRunBody(**req)
    print(f"[optimizer_cli] starting run {run_id} "
            f"sport={body.sport} window={body.start}..{body.end}")
    await _run_optimizer(run_id, body)
    state = _RUNS.get(run_id) or {}
    rc = 0 if state.get("status") in ("succeeded", "cancelled") else 1
    print(f"[optimizer_cli] finished status={state.get('status')} rc={rc}")
    return rc


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run an optimizer sweep from "
                                                  "an enqueued optimizer_runs doc.")
    p.add_argument("--run-id", required=True)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse()
    sys.exit(asyncio.run(_main(args.run_id)))
