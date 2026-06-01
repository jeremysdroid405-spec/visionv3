"""
/api/emergent-admin/export — one-shot file transfer endpoint.

Lets a prod operator pull a pre-staged export file from the preview
pod via:

    curl -H "X-Admin-Token: $TOK" \
      "$PREVIEW_URL/api/emergent-admin/export/file?name=team_model_features.json.gz" \
      -o /tmp/team_import/team_model_features.json.gz

Files MUST live under `/tmp/team_export/` (operator stages them there
via `mongoexport`). The basename is sanitized — no path traversal.

Gated by `X-Admin-Token` like every other admin route. Intended for
manual ops only; safe to remove or ignore in production.
"""
from __future__ import annotations
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from .auth import require_admin_token

router = APIRouter(tags=["emergent-admin"])
EXPORT_DIR = Path("/tmp/team_export")


@router.get("/list")
async def list_exports(auth=Depends(require_admin_token)):
    """List files currently available for download."""
    if not EXPORT_DIR.is_dir():
        return {"ok": True, "dir": str(EXPORT_DIR), "files": []}
    out = []
    for p in sorted(EXPORT_DIR.iterdir()):
        if p.is_file():
            out.append({
                "name":  p.name,
                "bytes": p.stat().st_size,
            })
    return {"ok": True, "dir": str(EXPORT_DIR), "files": out}


@router.get("/file")
async def download_export(
    name: str = Query(..., min_length=1, max_length=200),
    auth=Depends(require_admin_token),
):
    """Stream a single file from `/tmp/team_export/`."""
    # Sanitize: drop any path components, allow only the basename.
    safe = os.path.basename(name)
    if not safe or safe.startswith(".") or "/" in safe or "\\" in safe:
        raise HTTPException(status_code=400, detail="invalid name")
    target = EXPORT_DIR / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"not found: {safe}")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=safe,
    )
