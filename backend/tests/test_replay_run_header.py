"""Unit tests for replay run-header versioning helpers (Phase 0).

No DB. No network. We use a temp directory as a fake repo root.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.replay.run_header import (
    SCORING_FILES,
    GATE_FILES,
    compute_run_fingerprint,
    new_run_id,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _seed_files(root: Path, scoring_text: str, gate_text: str) -> None:
    for rel in SCORING_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(scoring_text)
    for rel in GATE_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(gate_text)


# ----- new_run_id ----------------------------------------------------------

def test_new_run_id_is_32_hex_chars():
    rid = new_run_id()
    assert _HEX32.match(rid), rid


def test_new_run_id_is_unique_across_calls():
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100


# ----- compute_run_fingerprint --------------------------------------------

def test_fingerprint_returns_required_keys(tmp_path: Path):
    _seed_files(tmp_path, "scoring v1\n", "gate v1\n")
    fp = compute_run_fingerprint(repo_root=tmp_path)
    assert set(fp.keys()) == {
        "git_commit", "git_dirty",
        "scoring_config_hash", "gate_config_hash",
        "fingerprint_version",
    }
    assert _HEX64.match(fp["scoring_config_hash"])
    assert _HEX64.match(fp["gate_config_hash"])
    assert fp["fingerprint_version"] == 1


def test_fingerprint_is_deterministic_for_identical_inputs(tmp_path: Path):
    _seed_files(tmp_path, "scoring\n", "gate\n")
    a = compute_run_fingerprint(repo_root=tmp_path)
    b = compute_run_fingerprint(repo_root=tmp_path)
    assert a["scoring_config_hash"] == b["scoring_config_hash"]
    assert a["gate_config_hash"]    == b["gate_config_hash"]


def test_fingerprint_changes_when_scoring_file_changes(tmp_path: Path):
    _seed_files(tmp_path, "scoring v1\n", "gate v1\n")
    a = compute_run_fingerprint(repo_root=tmp_path)
    # Bump one scoring file.
    (tmp_path / SCORING_FILES[0]).write_text("scoring v2\n")
    b = compute_run_fingerprint(repo_root=tmp_path)
    assert a["scoring_config_hash"] != b["scoring_config_hash"]
    assert a["gate_config_hash"]    == b["gate_config_hash"]


def test_fingerprint_changes_when_gate_file_changes(tmp_path: Path):
    _seed_files(tmp_path, "scoring v1\n", "gate v1\n")
    a = compute_run_fingerprint(repo_root=tmp_path)
    (tmp_path / GATE_FILES[-1]).write_text("gate v2\n")
    b = compute_run_fingerprint(repo_root=tmp_path)
    assert a["gate_config_hash"]    != b["gate_config_hash"]
    assert a["scoring_config_hash"] == b["scoring_config_hash"]


def test_fingerprint_handles_missing_files_without_crashing(tmp_path: Path):
    """Empty repo should not raise; missing files contribute MISSING token."""
    fp = compute_run_fingerprint(repo_root=tmp_path)
    assert _HEX64.match(fp["scoring_config_hash"])
    assert _HEX64.match(fp["gate_config_hash"])


def test_fingerprint_is_order_independent_of_input_list(tmp_path: Path):
    """Reordering scoring_files / gate_files must not change the hash —
    the hasher sorts internally."""
    _seed_files(tmp_path, "x\n", "y\n")
    a = compute_run_fingerprint(repo_root=tmp_path)
    rev_scoring = list(reversed(SCORING_FILES))
    rev_gate = list(reversed(GATE_FILES))
    b = compute_run_fingerprint(
        repo_root=tmp_path,
        scoring_files=rev_scoring,
        gate_files=rev_gate,
    )
    assert a["scoring_config_hash"] == b["scoring_config_hash"]
    assert a["gate_config_hash"]    == b["gate_config_hash"]


def test_real_repo_fingerprint_completes(tmp_path: Path):  # noqa: ARG001
    """Smoke test against the actual /app tree — should not raise even
    on a non-git working dir."""
    fp = compute_run_fingerprint()  # default repo_root=/app
    assert _HEX64.match(fp["scoring_config_hash"])
    assert _HEX64.match(fp["gate_config_hash"])
    assert fp["fingerprint_version"] == 1
    # git_commit may or may not be set depending on env; just check shape.
    assert fp["git_commit"] is None or len(fp["git_commit"]) >= 7
