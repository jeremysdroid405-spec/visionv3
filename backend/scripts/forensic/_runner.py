"""
scripts/forensic/_runner.py — Forensic Audit runner core.

Provides a small, dependency-light harness for cataloging system tests
as TAMPER-EVIDENT proof-of-use artifacts for investor / customer /
forensic review.

DESIGN GOALS
    • Fool-proof: every test produces a deterministic JSON record.
      Records are signed by sha256 over `{test_id, inputs, actual,
      pass, started_at, completed_at}` so any post-hoc edit breaks the
      signature.
    • Verifiable: artifacts land in TWO places (filesystem +
      Mongo) so cross-checking is trivial.
    • Cataloged: each run produces an INDEX.json catalog +
      tests.jsonl (one JSON object per line) + a manifest sha256
      over both.
    • Re-runnable: each run gets a fresh UTC-stamped subdirectory
      under `/app/memory/forensic_audit/`. Past runs are never
      mutated.

ARTIFACT LAYOUT
    /app/memory/forensic_audit/
        2026-06-01T12-34-56Z_a1b2c3def0/
            INDEX.json         — run metadata + per-test summary
            tests.jsonl        — one JSON line per test (full detail)
            manifest.sha256    — sha256 over INDEX.json + tests.jsonl

MONGO MIRROR
    `forensic_test_runs`     — one doc per run (with summary + sha256)
    `forensic_test_records`  — one doc per individual test (full detail)
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

ARTIFACT_ROOT = Path("/app/memory/forensic_audit")
RUNS_COLL = "forensic_test_runs"
RECORDS_COLL = "forensic_test_records"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_stamp(dt: Optional[datetime] = None) -> str:
    dt = dt or _utc_now()
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


def _git_commit() -> Optional[str]:
    """Best-effort git short SHA. Never raises."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/app", stderr=subprocess.DEVNULL, timeout=2)
        return out.decode().strip()
    except Exception:
        return None


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _sha256_json(payload: Dict[str, Any]) -> str:
    """Canonical sha256 over a JSON-serializable dict. Sorted keys +
    no whitespace so the same logical value always hashes identically."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                        default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _json_safe(v: Any) -> Any:
    """Recursively coerce to JSON-/BSON-safe types. Sets → sorted lists,
    tuples → lists, anything else with a non-trivial repr → str.
    Pure function, used before Mongo writes."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, set):
        try:
            return sorted([_json_safe(x) for x in v], key=lambda x: str(x))
        except TypeError:
            return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(val) for k, val in v.items()}
    # datetime + ObjectId-ish stuff → str
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    return str(v)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class TestRecord:
    test_id:       str
    category:      str
    description:   str
    inputs:        Dict[str, Any]
    expected:      Any
    actual:        Any
    passed:        bool
    failure_reason: Optional[str] = None
    latency_ms:    int = 0
    started_at:    str = ""
    completed_at:  str = ""
    sha256:        str = ""

    def compute_sha256(self) -> str:
        signed = {
            "test_id":      self.test_id,
            "inputs":       self.inputs,
            "actual":       self.actual,
            "passed":       self.passed,
            "started_at":   self.started_at,
            "completed_at": self.completed_at,
        }
        return _sha256_json(signed)


@dataclass
class ForensicAuditRun:
    run_id:        str
    started_at:    str
    completed_at:  str = ""
    git_commit:    Optional[str] = None
    pod_hostname:  str = ""
    mongo_db:      str = ""
    artifact_dir:  str = ""
    n_tests:       int = 0
    n_passed:      int = 0
    n_failed:      int = 0
    duration_ms:   int = 0
    manifest_sha256: str = ""
    records:       List[TestRecord] = field(default_factory=list)


# ───── public TestCase API ─────
# A test case is a coroutine returning a dict shaped:
#   { "expected": <any>, "actual": <any>, "passed": bool,
#     "failure_reason": Optional[str] }
# Plus the static metadata supplied at registration time.
TestFn = Callable[["ForensicContext"], Awaitable[Dict[str, Any]]]


@dataclass
class TestCase:
    test_id:     str
    category:    str
    description: str
    inputs:      Dict[str, Any]
    fn:          TestFn


class ForensicContext:
    """Passed to each test fn. Carries the live Mongo db handle, a
    shared aiohttp session for HTTP probes, the admin token (read from
    env), and the API base URL."""
    def __init__(self, *, db, base_url: str, admin_token: str,
                  session):
        self.db = db
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.session = session

    async def http(self, method: str, path: str, *,
                    json_body: Optional[Dict[str, Any]] = None,
                    expect_status: Optional[int] = None,
                    timeout_s: float = 30.0) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"X-Admin-Token": self.admin_token}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        import aiohttp
        async with self.session.request(
            method, url, headers=headers,
            json=json_body,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as r:
            text = await r.text()
            try:
                body = json.loads(text)
            except Exception:
                body = {"raw": text[:500]}
            return {"status": r.status, "body": body, "url": url}


# ───── runner ─────
async def execute_run(
    *, tests: List[TestCase], db_name: str, base_url: str,
    admin_token: str, mongo_url: str, dry_run: bool = False,
) -> ForensicAuditRun:
    """Execute every test case, persist all artifacts, return the run."""
    import aiohttp
    from motor.motor_asyncio import AsyncIOMotorClient

    started = _utc_now()
    run_id = f"audit_{_utc_stamp(started)}_{uuid.uuid4().hex[:10]}"
    artifact_dir = ARTIFACT_ROOT / run_id.replace("audit_", "")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    mongo = AsyncIOMotorClient(mongo_url)
    db = mongo[db_name]
    session = aiohttp.ClientSession()

    run = ForensicAuditRun(
        run_id=run_id,
        started_at=started.isoformat(),
        git_commit=_git_commit(),
        pod_hostname=_hostname(),
        mongo_db=db_name,
        artifact_dir=str(artifact_dir),
    )

    ctx = ForensicContext(db=db, base_url=base_url,
                            admin_token=admin_token, session=session)

    try:
        for tc in tests:
            t0 = time.monotonic()
            t_start = _utc_now()
            rec_actual: Any = None
            rec_expected: Any = None
            passed = False
            failure_reason: Optional[str] = None
            try:
                result = await tc.fn(ctx)
                rec_expected = result.get("expected")
                rec_actual = result.get("actual")
                passed = bool(result.get("passed"))
                failure_reason = result.get("failure_reason")
            except Exception as e:
                passed = False
                failure_reason = f"{type(e).__name__}: {e}"
                rec_actual = {"exception": failure_reason}
            t_end = _utc_now()
            latency_ms = int((time.monotonic() - t0) * 1000)
            # Normalize to JSON-safe types BEFORE signing — same shape
            # goes into the sha256, the JSONL artifact, AND Mongo. This
            # is critical for tamper-evidence: an auditor recomputing
            # the hash must see the exact bytes we wrote.
            rec_expected = _json_safe(rec_expected)
            rec_actual = _json_safe(rec_actual)
            safe_inputs = _json_safe(tc.inputs)
            rec = TestRecord(
                test_id=tc.test_id, category=tc.category,
                description=tc.description, inputs=safe_inputs,
                expected=rec_expected, actual=rec_actual,
                passed=passed, failure_reason=failure_reason,
                latency_ms=latency_ms,
                started_at=t_start.isoformat(),
                completed_at=t_end.isoformat(),
            )
            rec.sha256 = rec.compute_sha256()
            run.records.append(rec)
            run.n_tests += 1
            if passed:
                run.n_passed += 1
            else:
                run.n_failed += 1
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {tc.test_id:<10s} {tc.category:<22s}  "
                  f"{tc.description[:60]:<60s}  "
                  f"({latency_ms:>5d}ms)")
            if failure_reason:
                print(f"          ↳ {failure_reason}")
    finally:
        await session.close()
        mongo_client_for_writes = AsyncIOMotorClient(mongo_url)

    run.completed_at = _utc_now().isoformat()
    run.duration_ms = sum(r.latency_ms for r in run.records)

    # Write per-test JSONL
    jsonl_path = artifact_dir / "tests.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in run.records:
            f.write(json.dumps(asdict(r), default=str) + "\n")

    # Write INDEX.json (summary + per-test minimal view)
    index = {
        "run_id":       run.run_id,
        "started_at":   run.started_at,
        "completed_at": run.completed_at,
        "duration_ms":  run.duration_ms,
        "git_commit":   run.git_commit,
        "pod_hostname": run.pod_hostname,
        "mongo_db":     run.mongo_db,
        "artifact_dir": run.artifact_dir,
        "n_tests":      run.n_tests,
        "n_passed":     run.n_passed,
        "n_failed":     run.n_failed,
        "tests": [
            {"test_id": r.test_id, "category": r.category,
              "description": r.description,
              "passed": r.passed, "latency_ms": r.latency_ms,
              "sha256": r.sha256,
              "failure_reason": r.failure_reason}
            for r in run.records
        ],
    }
    index_path = artifact_dir / "INDEX.json"
    with index_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, default=str)

    # Compute manifest: sha256 over INDEX.json + tests.jsonl bytes.
    manifest_h = hashlib.sha256()
    manifest_h.update(index_path.read_bytes())
    manifest_h.update(jsonl_path.read_bytes())
    run.manifest_sha256 = manifest_h.hexdigest()
    (artifact_dir / "manifest.sha256").write_text(
        f"{run.manifest_sha256}  INDEX.json+tests.jsonl\n"
        f"run_id={run.run_id}\n"
        f"completed_at={run.completed_at}\n"
        f"git_commit={run.git_commit}\n",
        encoding="utf-8",
    )

    # Mirror to Mongo unless dry_run
    if not dry_run:
        try:
            wdb = mongo_client_for_writes[db_name]
            await wdb[RUNS_COLL].update_one(
                {"run_id": run.run_id},
                {"$set": {
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
                    "ingested_at":     _utc_now(),
                }},
                upsert=True,
            )
            if run.records:
                from pymongo import UpdateOne
                ops = [
                    UpdateOne(
                        {"run_id": run.run_id, "test_id": r.test_id},
                        {"$set": {"run_id": run.run_id, **asdict(r)}},
                        upsert=True,
                    )
                    for r in run.records
                ]
                await wdb[RECORDS_COLL].bulk_write(ops, ordered=False)
        finally:
            mongo_client_for_writes.close()

    return run
