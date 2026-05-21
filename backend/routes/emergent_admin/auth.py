"""
Token auth + audit-log helpers for the Emergent Admin API.

Tokens are constant-time-compared against EMERGENT_ADMIN_TOKEN from .env.
If the env var is missing, the API refuses ALL requests (fail-closed).

Every protected request is logged into `emergent_admin_audit_log`:
    {
      ts, ip, route, method, action, status_code,
      params_redacted, response_summary, agent_id (from header), token_hash
    }
"""
from __future__ import annotations
import hmac
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

AUDIT_COLL = "emergent_admin_audit_log"
_client: Optional[AsyncIOMotorClient] = None


def _get_db() -> AsyncIOMotorDatabase:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client[os.environ["DB_NAME"]]


def _token_hash(tok: str) -> str:
    """Short irreversible fingerprint for audit logs (never store raw token)."""
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16]


async def require_admin_token(
    request: Request,
    x_admin_token: Optional[str] = Header(default=None,
                                            alias="X-Admin-Token"),
    x_agent_id: Optional[str] = Header(default=None,
                                          alias="X-Agent-Id"),
) -> Dict[str, Any]:
    expected = os.environ.get("EMERGENT_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergent admin API disabled: EMERGENT_ADMIN_TOKEN unset")
    if not x_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Admin-Token header")
    if not hmac.compare_digest(x_admin_token, expected):
        # Audit the failure
        try:
            await audit_log(request, action="auth_fail",
                              params={"reason": "token_mismatch"},
                              status_code=401,
                              token_hash="(invalid)",
                              agent_id=x_agent_id or "")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid X-Admin-Token")
    return {
        "agent_id":  x_agent_id or "anonymous",
        "token_hash": _token_hash(expected),
    }


def _redact(params: Dict[str, Any]) -> Dict[str, Any]:
    """Strip obvious secrets before persisting."""
    if not isinstance(params, dict):
        return {}
    out = {}
    for k, v in params.items():
        if any(s in k.lower() for s in
                ("token", "secret", "password", "api_key", "apikey")):
            out[k] = "***redacted***"
        else:
            out[k] = v
    return out


async def audit_log(
    request: Request, *,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
    token_hash: str = "",
    agent_id: str = "",
    response_summary: Optional[Dict[str, Any]] = None,
) -> None:
    db = _get_db()
    doc = {
        "ts":            datetime.now(timezone.utc),
        "ip":            (request.client.host if request.client else None),
        "route":         str(request.url.path),
        "method":        request.method,
        "action":        action,
        "status_code":   status_code,
        "params_redacted": _redact(params or {}),
        "response_summary": response_summary or {},
        "agent_id":      agent_id,
        "token_hash":    token_hash,
    }
    try:
        await db[AUDIT_COLL].insert_one(doc)
    except Exception:
        # Audit failure must NOT block API actions
        pass


# ── public auth router ────────────────────────────────────────────────────
router = APIRouter()


@router.get("/whoami")
async def whoami(request: Request,
                  auth: Dict[str, Any] = Depends(require_admin_token)):
    await audit_log(request, action="auth_whoami",
                      status_code=200, **auth)
    return {"ok": True, "agent_id": auth["agent_id"],
            "token_hash": auth["token_hash"]}
