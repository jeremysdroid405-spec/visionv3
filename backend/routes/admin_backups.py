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

from fastapi import APIRouter, HTTPException, Query, Header, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])

BACKUP_DIR = Path("/data/backups")

# Pattern matches the three artefacts we ship today AND any future
# `preview_dump_TIMESTAMP.*` sibling files. Anything else is 404.
_NAME_RE = re.compile(
    r"^preview_dump_\d{8}_\d{6}"
    r"(?:\.manifest)?"
    r"\.(?:tar|json|txt)$"
    r"|"
    # 2026-05-12 — also allow the split chunks `*.tar.part-aa`,
    # `*.tar.part-ab`, etc. Produced by `split -a 2` so each part is
    # ~55 MB. Concatenate on the production server with `cat
    # *.tar.part-* > full.tar`.
    r"^preview_dump_\d{8}_\d{6}\.tar\.part-[a-z]{2}$"
    r"|"
    # 2026-05-12 — also expose the per-part MD5 checksum file so
    # consumers can `md5sum -c parts.md5` before reassembly.
    r"^preview_dump_\d{8}_\d{6}\.parts\.md5$"
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
    request: Request,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    range_header: Optional[str] = Header(default=None, alias="Range"),
):
    """Stream a single backup artefact. Auth-gated. **Range-aware** so
    browsers + Cloudflare can resume / chunk huge files.

    Without Range support, a single 882 MB download streams as one
    200 OK over Cloudflare's preview ingress, which times the
    connection out after ~60 sec of buffering even if the origin
    is still streaming. Adding `Accept-Ranges: bytes` + a `Range:`
    handler lets the browser issue partial-content requests and
    Cloudflare keeps each chunk under its limits.

    Implements the minimum HTTP/1.1 Range subset:
      • single-range `Range: bytes=START-END`  → 206 Partial Content
      • `Range: bytes=START-`                   → 206, slice → EOF
      • `Range: bytes=-N`                       → 206, last N bytes
      • absent or malformed                     → 200, full body
    Anything more exotic (multi-range, units != bytes) falls back to
    200 OK full body — RFC-7233 says clients must accept that.
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

    file_size = path.stat().st_size
    if filename.endswith(".tar"):
        media_type = "application/x-tar"
    elif filename.endswith(".json"):
        media_type = "application/json"
    elif ".tar.part-" in filename:
        # Split tar chunk — `cat *.tar.part-* > full.tar` to reassemble.
        media_type = "application/octet-stream"
    else:
        media_type = "text/plain"

    # Common headers for every code path.
    base_headers = {
        "Accept-Ranges":        "bytes",
        "Content-Disposition":  f'attachment; filename="{filename}"',
        # Cache nothing — tokens shouldn't end up in CDN caches.
        "Cache-Control":        "no-store, no-cache, must-revalidate",
    }

    if not range_header or not range_header.lower().startswith("bytes="):
        # Full download path. Use FileResponse for sendfile-based
        # streaming when no Range was requested.
        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=filename,
            headers=base_headers,
        )

    # Parse a single-range spec.
    spec = range_header.split("=", 1)[1].strip()
    # Multi-range fallback — RFC-7233 allows server to return full body.
    if "," in spec:
        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=filename,
            headers=base_headers,
        )

    try:
        start_str, _, end_str = spec.partition("-")
        if not start_str and end_str:
            # `bytes=-N`  → last N bytes
            suffix = int(end_str)
            if suffix <= 0:
                raise ValueError
            start = max(0, file_size - suffix)
            end = file_size - 1
        elif start_str and not end_str:
            # `bytes=START-`  → start to EOF
            start = int(start_str)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str)
        if start < 0 or end < start or start >= file_size:
            raise ValueError
        end = min(end, file_size - 1)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={**base_headers, "Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024  # 1 MB
    length = end - start + 1

    def _iter():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                buf = fh.read(min(chunk_size, remaining))
                if not buf:
                    break
                remaining -= len(buf)
                yield buf

    headers = {
        **base_headers,
        "Content-Range":  f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        _iter(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
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
