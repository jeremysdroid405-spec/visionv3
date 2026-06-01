"""
scripts/forensic/run_audit.py — CLI entrypoint for the forensic audit.

USAGE
    python -m scripts.forensic.run_audit
    python -m scripts.forensic.run_audit --base-url https://propvision.preview.emergentagent.com
    python -m scripts.forensic.run_audit --dry-run   # no Mongo mirror

Output:
    /app/memory/forensic_audit/<UTC-stamp>_<uuid8>/
        INDEX.json
        tests.jsonl
        manifest.sha256

Mongo mirror (unless --dry-run):
    db.forensic_test_runs    (one doc per run)
    db.forensic_test_records (one doc per individual test)
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from scripts.forensic._runner import execute_run
from scripts.forensic.tests import ALL_TESTS


async def amain(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.environ.get(
        "FORENSIC_BASE_URL", "http://localhost:8001")
    admin_token = os.environ["EMERGENT_ADMIN_TOKEN"]
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    print()
    print("=" * 72)
    print("  PropVision Forensic Audit")
    print("=" * 72)
    print(f"  tests:        {len(ALL_TESTS)}")
    print(f"  base_url:     {base_url}")
    print(f"  mongo_db:     {db_name}")
    print(f"  dry_run:      {args.dry_run}")
    print(f"  filter cat:   {args.category or '(none)'}")
    print()

    tests = ALL_TESTS
    if args.category:
        tests = [t for t in ALL_TESTS if t.category == args.category]
        if not tests:
            print(f"  ERROR: no tests in category {args.category!r}")
            return 2

    run = await execute_run(
        tests=tests,
        db_name=db_name,
        base_url=base_url,
        admin_token=admin_token,
        mongo_url=mongo_url,
        dry_run=args.dry_run,
    )
    print()
    print("=" * 72)
    print(f"  Run: {run.run_id}")
    print(f"  Result: {run.n_passed}/{run.n_tests} passed  "
          f"({run.n_failed} failed)")
    print(f"  Duration: {run.duration_ms}ms")
    print(f"  Artifact dir: {run.artifact_dir}")
    print(f"  Manifest sha256: {run.manifest_sha256}")
    print(f"  Mongo mirror: "
          f"{'SKIPPED (dry-run)' if args.dry_run else 'forensic_test_runs / forensic_test_records'}")
    print("=" * 72)
    return 0 if run.n_failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=None,
                    help="API base URL (default localhost:8001)")
    p.add_argument("--dry-run", action="store_true",
                    help="Compute & write filesystem artifacts but "
                         "do NOT mirror to Mongo")
    p.add_argument("--category", default=None,
                    help="Run only tests in this category")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
