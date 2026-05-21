"""
Scoped service restart.

Allows /api/emergent-admin/services/restart for a hardcoded allowlist
(default: just "backend"). Uses supervisorctl directly with a fixed argv
— no shell. If supervisorctl is not available, returns 501.

Intentionally does NOT call sudo. The server process must be configured
to either:
  (a) run supervisor as the same user, OR
  (b) have a NOPASSWD sudoers entry for that specific supervisorctl command
      (operator's responsibility — NOT requested by this code).
"""
from __future__ import annotations
import asyncio
import os
import shutil

_FALLBACK_BIN_PATHS = ["/usr/bin/supervisorctl", "/usr/local/bin/supervisorctl",
                          "/sbin/supervisorctl", "/usr/sbin/supervisorctl"]


def _which_supervisorctl() -> str | None:
    """which() honors PATH; if PATH lacks /usr/bin (venv-launched), fall back
    to the conventional install paths so the API still functions after a
    fresh openssh/supervisor install."""
    p = shutil.which("supervisorctl")
    if p:
        return p
    for cand in _FALLBACK_BIN_PATHS:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import audit_log, require_admin_token
from .policy import ALLOWED_SERVICES

router = APIRouter()


class RestartBody(BaseModel):
    service: str
    confirm: bool = False


@router.get("/status/{service}")
async def status(service: str, request: Request,
                    auth=Depends(require_admin_token)):
    if service not in ALLOWED_SERVICES:
        raise HTTPException(403,
            f"service '{service}' not in allowlist {sorted(ALLOWED_SERVICES)}")
    sctl = _which_supervisorctl()
    if not sctl:
        raise HTTPException(501, "supervisorctl not available on this host")
    proc = await asyncio.create_subprocess_exec(
        sctl, "status", service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await proc.communicate()
    await audit_log(request, action="service_status",
                      params={"service": service}, **auth)
    return {"ok": True, "service": service,
             "exit_code": proc.returncode,
             "output": (stdout or b"").decode("utf-8", "replace")[-4000:]}


@router.post("/restart")
async def restart(body: RestartBody, request: Request,
                     auth=Depends(require_admin_token)):
    if body.service not in ALLOWED_SERVICES:
        raise HTTPException(403,
            f"service '{body.service}' not in allowlist "
            f"{sorted(ALLOWED_SERVICES)}")
    if not body.confirm:
        raise HTTPException(400, "set confirm=true to restart")
    sctl = _which_supervisorctl()
    if not sctl:
        raise HTTPException(501, "supervisorctl not available on this host")
    started = datetime.now(timezone.utc)
    proc = await asyncio.create_subprocess_exec(
        sctl, "restart", body.service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await proc.communicate()
    out = (stdout or b"").decode("utf-8", "replace")
    summary = {"exit_code": proc.returncode,
                "output_tail": out[-4000:],
                "duration_s":
                  (datetime.now(timezone.utc) - started).total_seconds()}
    await audit_log(request, action="service_restart",
                      params={"service": body.service},
                      response_summary=summary, **auth)
    return {"ok": proc.returncode == 0, "service": body.service, **summary}
