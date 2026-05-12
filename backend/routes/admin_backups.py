"""
Admin-only backup file downloader.
================================================================
Exposes the three preview-DB backup files in /data/backups/ over
HTTPS so the user can pull them into their production server with
a single `curl` (or browser click).

Security model — single shared bearer token:
  • Token is regenerated on every backend boot and written to
    `/data/backups/.download_token` (chmod 600). The companion
    bash helper `print_download_links.sh` prints curl-ready URLs.
  • Token must be passed as `?token=<value>` (query) OR
    `Authorization: Bearer <value>` (header). Both work; pick
    whichever your client supports.
  • No token, no answer (401).

Filename whitelist
  Only files matching the exact pattern
      preview_dump_YYYYMMDD_HHMMSS(.manifest)?(.tar|.json|.txt)
  may be downloaded — no path traversal possible.

This is intentionally minimal — no session, no auth flow, no
cookies. Bearer-only.
"""
from __future__ import annotations

import os
import re
import secrets
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Header, status
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])

BACKUP_DIR = Path("/data/backups")

# Pattern matches the three artefacts we ship today AND any future
# `preview_dump_TIMESTAMP.*` sibling files. Anything else is 404.
_NAME_RE = re.compile(
    r"^preview_dump_\d{8}_\d{6}"
    r"(?:\.manifest)?"
    r"\.(?:tar|json|txt)$"
)

_TOKEN_FILE = BACKUP_DIR / ".download_token"


def _ensure_token() -> str:
    """Return the current bearer token. Regenerated only when the
    token file is missing (every boot if it was wiped). chmod 600 so
    other pod users can't read it."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if _TOKEN_FILE.exists():
        try:
            tok = _TOKEN_FILE.read_text().strip()
            if len(tok) >= 32:
                return tok
        except Exception:  # noqa: BLE001
            pass
    tok = secrets.token_urlsafe(32)
    _TOKEN_FILE.write_text(tok)
    try:
        _TOKEN_FILE.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass
    logger.warning(
        "[admin-backups] new download token generated → "
        "%s (read it with `cat %s`)",
        _TOKEN_FILE, _TOKEN_FILE,
    )
    return tok


_CURRENT_TOKEN = _ensure_token()


def _check_token(query_token: Optional[str],
                  auth_header: Optional[str]) -> None:
    bearer = None
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(None, 1)[1].strip()
    given = query_token or bearer
    if not given or not secrets.compare_digest(given, _CURRENT_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing download token",
        )


@router.get("/list")
def list_backups(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """List every downloadable backup artefact + its size + the full
    GET URL the caller can hit. Auth-gated (same token)."""
    _check_token(token, authorization)
    files = []
    for p in sorted(BACKUP_DIR.iterdir()):
        if p.is_file() and _NAME_RE.match(p.name):
            files.append({
                "name":        p.name,
                "size_bytes":  p.stat().st_size,
                "size_human":  _human(p.stat().st_size),
                "download_url": f"/api/admin/backups/download/{p.name}",
            })
    return {
        "backup_dir":   str(BACKUP_DIR),
        "file_count":   len(files),
        "files":        files,
    }


@router.get("/download/{filename}")
def download_backup(
    filename: str,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """Stream a single backup artefact. Auth-gated.

    `FileResponse` streams via sendfile / chunked transfer; the 925 MB
    tarball never touches application memory.
    """
    _check_token(token, authorization)
    if not _NAME_RE.match(filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filename does not match the whitelisted pattern",
        )
    path = BACKUP_DIR / filename
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{filename} not found in {BACKUP_DIR}",
        )
    # Media type guess — tar is application/x-tar; json + txt are obvious.
    if filename.endswith(".tar"):
        media_type = "application/x-tar"
    elif filename.endswith(".json"):
        media_type = "application/json"
    else:
        media_type = "text/plain"
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
    )


def _human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n/1024:.1f} KB"
    if n < 1024**3:
        return f"{n/1024**2:.1f} MB"
    return f"{n/1024**3:.2f} GB"


__all__ = ["router"]
