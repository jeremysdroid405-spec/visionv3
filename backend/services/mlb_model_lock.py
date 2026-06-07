"""
MLB Model Lock Guard
====================
Single source of truth for "is the MLB HF v2.0 model frozen?". When the
lock file `/app/backend/models/mlb_hf/.LOCKED` exists, ANY attempt to
retrain, overwrite, or otherwise mutate the model artifacts must raise
`MLBModelLockedError`.

Use:
    from services.mlb_model_lock import enforce_lock
    enforce_lock()                      # raises if locked
    enforce_lock(check_integrity=True)  # also verifies sha256 matches manifest

Bypassing the lock requires a deliberate human action: delete `.LOCKED`
file (root permission). Programmatic bypass is intentionally not
supported.
"""
from __future__ import annotations
import hashlib
import json
import os
from typing import List, Optional

LOCK_DIR = "/var/www/app/backend/models/mlb_hf"
LOCK_FILE = os.path.join(LOCK_DIR, ".LOCKED")


class MLBModelLockedError(RuntimeError):
    """Raised when code attempts to retrain or overwrite a locked model."""


def is_locked() -> bool:
    return os.path.exists(LOCK_FILE)


def load_manifest() -> Optional[dict]:
    if not is_locked():
        return None
    with open(LOCK_FILE, "r") as fh:
        return json.load(fh)


def enforce_lock(*, action: str = "modify",
                  check_integrity: bool = False) -> None:
    """Raise if the MLB model is locked.

    Args:
        action: human-readable description of what the caller wanted to do
            (used in the error message).
        check_integrity: if True, also recompute SHA256 of every artifact
            and compare against the manifest. Raises on mismatch.
    """
    if not is_locked():
        return
    manifest = load_manifest() or {}
    msg = (
        "MLB MODEL IS LOCKED — "
        f"action '{action}' is not permitted while .LOCKED is present.\n"
        f"  locked_at: {manifest.get('locked_at')}\n"
        f"  version: {manifest.get('version')}\n"
        f"  feature_count: {manifest.get('feature_count')}\n"
        f"  total_training_samples: {manifest.get('total_training_samples')}\n"
        f"To unlock, manually delete: {LOCK_FILE}\n"
        "(intentional human-only step)."
    )
    if check_integrity:
        # Best-effort: verify the disk sha256 of each artifact vs manifest.
        bad = _verify_integrity(manifest)
        if bad:
            msg += f"\nINTEGRITY MISMATCH on: {bad}"
    raise MLBModelLockedError(msg)


def _verify_integrity(manifest: dict) -> List[str]:
    bad: List[str] = []
    files = (manifest or {}).get("files") or {}
    for fn, meta in files.items():
        path = os.path.join(LOCK_DIR, fn)
        if not os.path.exists(path):
            bad.append(f"{fn} (missing)")
            continue
        with open(path, "rb") as fh: raw = fh.read()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != meta.get("sha256"):
            bad.append(f"{fn} (sha mismatch)")
    return bad


def assert_load_ok() -> None:
    """Light check used at model-load time. Logs a warning when the
    lock manifest doesn't match disk content but does NOT raise — the
    model still loads fine, the operator is just informed."""
    if not is_locked():
        return
    bad = _verify_integrity(load_manifest() or {})
    if bad:
        import logging
        logging.getLogger(__name__).warning(
            f"[MLB_MODEL_LOCK] integrity mismatch on load: {bad}")
