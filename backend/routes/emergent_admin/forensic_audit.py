"""
routes/emergent_admin/forensic_audit.py — Forensic audit API endpoints.

POST /api/emergent-admin/forensic-audit/run
        Trigger a fresh audit run. Returns the run summary, sha256
        manifest, and artifact path.

GET  /api/emergent-admin/forensic-audit/runs
        List historic runs (newest first). Useful for investor/auditor
        UIs that want a catalog of all proof-of-use runs.

GET  /api/emergent-admin/forensic-audit/{run_id}
        Fetch one run + all its test records. Returns the full forensic
        record including per-test sha256 signatures.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db

router = APIRouter()


class RunAuditBody(BaseModel):
    base_url: Optional[str] = Field(
        default=None,
        description="Override base URL the audit uses for HTTP probes. "
                       "Defaults to FORENSIC_BASE_URL env or localhost:8001.")
    category: Optional[str] = Field(
        default=None,
        description="Run only tests in this category.")
    dry_run: bool = Field(
        default=False,
        description="Compute & write filesystem artifacts but skip "
                       "the Mongo mirror.")


@router.post("/run")
async def run_forensic_audit(body: RunAuditBody, request: Request,
                                  auth=Depends(require_admin_token)):
    """Trigger a fresh forensic audit run. Returns the summary + sha256."""
    # Imports kept inside the handler so the audit code is lazy-loaded
    # (FastAPI startup never blocks on it even if collections are large).
    from scripts.forensic._runner import execute_run
    from scripts.forensic.tests import ALL_TESTS

    base_url = (body.base_url
                  or os.environ.get("FORENSIC_BASE_URL")
                  or "http://localhost:8001")
    admin_token = os.environ.get("EMERGENT_ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(503, "EMERGENT_ADMIN_TOKEN unset")
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    tests = ALL_TESTS
    if body.category:
        tests = [t for t in ALL_TESTS if t.category == body.category]
        if not tests:
            raise HTTPException(
                400, f"unknown category {body.category!r}; "
                       f"valid: {sorted({t.category for t in ALL_TESTS})}")

    run = await execute_run(
        tests=tests, db_name=db_name, base_url=base_url,
        admin_token=admin_token, mongo_url=mongo_url,
        dry_run=body.dry_run,
    )
    summary = {
        "ok":              run.n_failed == 0,
        "run_id":          run.run_id,
        "started_at":      run.started_at,
        "completed_at":    run.completed_at,
        "duration_ms":     run.duration_ms,
        "git_commit":      run.git_commit,
        "pod_hostname":    run.pod_hostname,
        "mongo_db":        run.mongo_db,
        "artifact_dir":    run.artifact_dir,
        "n_tests":         run.n_tests,
        "n_passed":        run.n_passed,
        "n_failed":        run.n_failed,
        "manifest_sha256": run.manifest_sha256,
        "category_filter": body.category,
        "dry_run":         body.dry_run,
    }
    await audit_log(
        request,
        action="forensic_audit_run",
        params={"category": body.category, "dry_run": body.dry_run},
        response_summary={"run_id": run.run_id, "n_passed": run.n_passed,
                              "n_failed": run.n_failed,
                              "manifest_sha256": run.manifest_sha256},
        **auth,
    )
    return summary


@router.get("/runs")
async def list_forensic_audit_runs(request: Request,
                                          limit: int = Query(50, ge=1, le=500),
                                          auth=Depends(require_admin_token)):
    """Catalog of past audit runs, newest first."""
    db = _get_db()
    cursor = db["forensic_test_runs"].find(
        {}, {"_id": 0}
    ).sort("started_at", -1).limit(int(limit))
    runs: List[Dict[str, Any]] = [d async for d in cursor]
    await audit_log(
        request, action="forensic_audit_list",
        params={"limit": limit},
        response_summary={"n_runs": len(runs)}, **auth)
    return {"ok": True, "n_runs": len(runs), "runs": runs}


@router.get("/{run_id}")
async def get_forensic_audit_run(run_id: str, request: Request,
                                       auth=Depends(require_admin_token)):
    """Full detail of one audit run + every test record."""
    db = _get_db()
    run = await db["forensic_test_runs"].find_one(
        {"run_id": run_id}, {"_id": 0})
    if run is None:
        raise HTTPException(404, f"run_id not found: {run_id}")
    records = [d async for d in db["forensic_test_records"].find(
        {"run_id": run_id}, {"_id": 0}).sort("test_id", 1)]
    await audit_log(
        request, action="forensic_audit_get",
        params={"run_id": run_id},
        response_summary={"n_records": len(records)}, **auth)
    return {"ok": True, "run": run, "records": records}
