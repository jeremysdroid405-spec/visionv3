"""
emergent_admin/ops.py — Operator-driven service restart.

Single-click reboot of the prod stack:
  - sudo systemctl restart vision-backend.service
  - sudo supervisorctl restart research_worker
  - sudo systemctl reload nginx

SECURITY:
  - Admin-token gated.
  - Commands are HARDCODED — no input from the request body is ever
    spliced into the shell. The endpoint takes ZERO user input apart
    from `services` (an enum from an explicit allowlist).
  - Each command is run via `subprocess.run(argv_list, shell=False)`
    so there is no shell-interpolation surface.
  - Audit-logged on every call.

PROD PREREQUISITE (operator one-time setup):
  Add to /etc/sudoers.d/propvision-admin (chmod 440):
      vision_user ALL=(root) NOPASSWD: /bin/systemctl restart vision-backend.service
      vision_user ALL=(root) NOPASSWD: /bin/supervisorctl restart research_worker
      vision_user ALL=(root) NOPASSWD: /bin/systemctl reload nginx

  Without those passwordless sudo lines, the endpoint will return
  the subprocess error verbatim so the operator can fix the
  sudoers config.
"""
from __future__ import annotations
import asyncio
import logging
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token


logger = logging.getLogger(__name__)
router = APIRouter()


# Strict allowlist — services the operator can reboot via this endpoint.
# Each entry is a tuple of (label, argv list, expected zero-exit semantics).
# NO user input EVER reaches the argv. The request body picks one of
# these keys; nothing else.
ALLOWED_RESTART_COMMANDS: Dict[str, Dict[str, Any]] = {
    "backend": {
        "label": "FastAPI backend (vision-backend.service)",
        "argv":  ["sudo", "-n", "/bin/systemctl", "restart",
                      "vision-backend.service"],
        "timeout_s": 30,
    },
    "worker": {
        "label": "Research worker (supervisorctl)",
        "argv":  ["sudo", "-n", "/usr/bin/supervisorctl", "restart",
                      "research_worker"],
        "timeout_s": 30,
    },
    "nginx": {
        "label": "Nginx reload (no downtime)",
        "argv":  ["sudo", "-n", "/bin/systemctl", "reload", "nginx"],
        "timeout_s": 15,
    },
}


class RebootBody(BaseModel):
    services: List[str] = Field(
        default_factory=lambda: ["backend", "worker", "nginx"],
        description=("Which services to restart. Each must be one of "
                          "'backend' / 'worker' / 'nginx'. Default = all three."))


def _run_cmd_sync(argv: List[str], timeout_s: int) -> Dict[str, Any]:
    """Synchronous wrapper for `subprocess.run`. We run it in a
    thread executor below (no event-loop blocking)."""
    started = datetime.now(timezone.utc)
    try:
        cp = subprocess.run(argv, capture_output=True, text=True,
                                 timeout=timeout_s, check=False)
        return {
            "cmd":       " ".join(shlex.quote(a) for a in argv),
            "rc":        cp.returncode,
            "stdout":    (cp.stdout or "")[-2000:],
            "stderr":    (cp.stderr or "")[-2000:],
            "ok":        cp.returncode == 0,
            "started":   started.isoformat(),
            "duration_s": (datetime.now(timezone.utc)
                                  - started).total_seconds(),
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd":       " ".join(shlex.quote(a) for a in argv),
            "rc":        None,
            "stdout":    "",
            "stderr":    f"command timed out after {timeout_s}s",
            "ok":        False,
            "started":   started.isoformat(),
            "duration_s": float(timeout_s),
        }
    except FileNotFoundError as e:
        return {
            "cmd":       " ".join(shlex.quote(a) for a in argv),
            "rc":        None,
            "stdout":    "",
            "stderr":    f"binary not found: {e}",
            "ok":        False,
            "started":   started.isoformat(),
            "duration_s": 0.0,
        }


@router.post("/reboot")
async def reboot_services(request: Request,
                                  body: RebootBody,
                                  auth=Depends(require_admin_token)):
    """Restart selected operator-allowlisted services.

    Body: `{"services": ["backend", "worker", "nginx"]}`. Default =
    all three (the typical full-stack reboot the operator runs after
    a deploy or when the worker dies repeatedly).

    Returns a per-service result with rc/stdout/stderr/duration so the
    operator can see which step failed and why.
    """
    if not body.services:
        raise HTTPException(400, "services must be a non-empty list")
    invalid = [s for s in body.services
                  if s not in ALLOWED_RESTART_COMMANDS]
    if invalid:
        raise HTTPException(
            400,
            f"unknown services: {invalid}. "
            f"Allowed: {sorted(ALLOWED_RESTART_COMMANDS)}")

    # Execute in the requested order (backend → worker → nginx is
    # the canonical sequence so the worker reconnects to the freshly-
    # restarted backend, and nginx reloads only after both are up).
    canonical_order = ["backend", "worker", "nginx"]
    ordered = [s for s in canonical_order if s in body.services]

    loop = asyncio.get_running_loop()
    results: List[Dict[str, Any]] = []
    for svc in ordered:
        cmd = ALLOWED_RESTART_COMMANDS[svc]
        logger.info("[ops_reboot] running %s (%s)", svc, cmd["argv"])
        result = await loop.run_in_executor(
            None, _run_cmd_sync, cmd["argv"], cmd["timeout_s"])
        result["service"] = svc
        result["label"]   = cmd["label"]
        results.append(result)

    all_ok = all(r.get("ok") for r in results)
    await audit_log(request, action="ops_reboot_services",
                       params={"services": ordered},
                       response_summary={"all_ok": all_ok,
                                              "n": len(results)},
                       **auth)
    return {
        "ok":              all_ok,
        "services_run":    ordered,
        "all_succeeded":   all_ok,
        "results":         results,
        "completed_at":    datetime.now(timezone.utc).isoformat(),
    }


@router.get("/reboot/_meta")
async def reboot_meta(request: Request,
                            auth=Depends(require_admin_token)):
    """Returns the static catalog of allowed services for the UI."""
    return {
        "ok": True,
        "services": [
            {"key": k, "label": v["label"],
              "argv_preview": " ".join(v["argv"])}
            for k, v in ALLOWED_RESTART_COMMANDS.items()
        ],
    }
