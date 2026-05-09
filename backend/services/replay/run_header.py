"""
Replay run versioning helpers.

Captures git commit + scoring/gate config hashes so two replay runs are
provably comparable iff the relevant config files differ in *exactly*
the way the experiment intends.

Phase 0: pure helpers, no DB writes.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from pathlib import Path
from typing import Dict, List, Optional


# Files that, when changed, invalidate replay-vs-replay comparability of
# the *scoring* dimension. Listed by repo-relative path.
SCORING_FILES: List[str] = [
    "backend/services/scoring/scoring_stack.py",
    "backend/services/scoring/adapters/nba_scoring.py",
    "backend/services/scoring/adapters/mlb_scoring.py",
    "backend/services/scoring/tp_engine.py",
    "backend/services/scoring/calibration.py",
    "backend/services/scoring/vision_v2.py",
    "backend/services/scoring/cv_caps.py",
    "backend/services/scoring/coverage_filter.py",
    "backend/services/scoring/stat_family.py",
    "backend/services/scoring/tiering.py",
]

# Files that, when changed, invalidate replay-vs-replay comparability of
# the *gate* dimension.
GATE_FILES: List[str] = [
    "backend/services/scoring/gates/thresholds.py",
    "backend/services/scoring/gates/overrides.py",
    "backend/services/scoring/gates/engine.py",
    "backend/services/scoring/gates/schema.py",
]


def _sha256_files(repo_root: Path, rel_paths: List[str]) -> str:
    """Hash a stable, ordered concatenation of file contents.

    Missing files contribute the literal token `<MISSING:{path}>` so we
    can detect missing-file regressions.
    """
    h = hashlib.sha256()
    for rel in sorted(rel_paths):
        p = repo_root / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except FileNotFoundError:
            h.update(f"<MISSING:{rel}>".encode("utf-8"))
        h.update(b"\0\0")
    return h.hexdigest()


def _git(cmd: List[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.check_output(
            cmd, cwd=cwd, stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def compute_run_fingerprint(
    repo_root: Optional[Path] = None,
    *,
    scoring_files: Optional[List[str]] = None,
    gate_files: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Return a versioning fingerprint dict suitable for `replay_runs`.

    Args:
        repo_root: project root. Defaults to /app.
        scoring_files / gate_files: override file lists for testing.

    Returns:
        dict with git_commit, git_dirty, scoring_config_hash, gate_config_hash.
    """
    if repo_root is None:
        repo_root = Path("/app")
    if scoring_files is None:
        scoring_files = SCORING_FILES
    if gate_files is None:
        gate_files = GATE_FILES

    git_commit = _git(["git", "rev-parse", "HEAD"], repo_root)
    porcelain = _git(["git", "status", "--porcelain"], repo_root)
    git_dirty = bool(porcelain) if porcelain is not None else None

    return {
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "scoring_config_hash": _sha256_files(repo_root, scoring_files),
        "gate_config_hash":    _sha256_files(repo_root, gate_files),
        "fingerprint_version": 1,
    }


def new_run_id() -> str:
    """Stable UUID4 hex string for a new replay run."""
    return uuid.uuid4().hex


__all__ = [
    "SCORING_FILES",
    "GATE_FILES",
    "compute_run_fingerprint",
    "new_run_id",
]
