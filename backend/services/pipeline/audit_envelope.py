"""Audit envelope builder (Phase B).

Composes the structured audit dict stamped on every run doc
written by the universal pipeline runner. Caller passes it
through to `run_production_replay(audit_envelope=...)`.

Required fields per directive:
    test_id, sport, mode, snapshot_time, created_at, git_sha,
    pipeline_version, config_hash, input_snapshot_hash,
    source_collections, row_count, canonical_prop_count,
    gate_version, routing_version, eligibility_version
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


PIPELINE_VERSION = "universal_pipeline_v1_phase_b_2026_05_17"
ELIGIBILITY_VERSION = "apply_production_eligibility_v1_phase_a"
ROUTING_VERSION = "odds_bucket_router_universal_sh_le_neg300_wz_ge_pos150"
GATE_VERSION_FIRMWARE = "tier_evaluator_universal_v1"


def _config_hash(parts: List[str]) -> str:
    """Deterministic short hash of canonicalized config strings."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _git_sha() -> Optional[str]:
    try:
        from services.replay.providers.audit import git_commit_sha
        return git_commit_sha()
    except Exception:
        return None


def build_audit_envelope(*,
    test_id: str,
    sport: str,
    mode: str,
    snapshot_time: str,
    output_namespace: str,
    input_provider_describe: Dict[str, Any],
    output_writer_describe: Dict[str, Any],
    raw_row_count: Optional[int] = None,
    normalized_prop_count: Optional[int] = None,
    after_priceable_count: Optional[int] = None,
    after_pp_playable_count: Optional[int] = None,
    canonical_prop_count: Optional[int] = None,
    cards_written: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the structured run-doc envelope.

    Counts (raw/normalized/after_priceable/after_pp/canonical/cards)
    are populated by the runner AFTER each stage; this helper takes
    them all as kwargs so the runner is the only thing that knows
    the per-stage timings.
    """
    cfg_hash = _config_hash([
        PIPELINE_VERSION, ELIGIBILITY_VERSION,
        ROUTING_VERSION, GATE_VERSION_FIRMWARE,
        f"sport={sport}", f"mode={mode}",
        f"namespace={output_namespace}",
    ])
    env: Dict[str, Any] = {
        "test_id": test_id,
        "sport": sport,
        "mode": mode,
        "snapshot_time": snapshot_time,
        "output_namespace": output_namespace,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "pipeline_version": PIPELINE_VERSION,
        "eligibility_version": ELIGIBILITY_VERSION,
        "routing_version": ROUTING_VERSION,
        "gate_version_firmware": GATE_VERSION_FIRMWARE,
        "config_hash": cfg_hash,
        "input_provider": input_provider_describe,
        "output_writer": output_writer_describe,
        "input_snapshot_hash": input_provider_describe.get(
            "input_snapshot_hash"),
        "source_collections": input_provider_describe.get(
            "source_collections", []),
        "stage_counts": {
            "raw_input_rows": raw_row_count,
            "normalized_props": normalized_prop_count,
            "after_priceable_filter": after_priceable_count,
            "after_pp_playable_filter": after_pp_playable_count,
            "canonical_props": canonical_prop_count,
            "cards_written": cards_written,
        },
    }
    if extra:
        env["extra"] = extra
    return env


__all__ = [
    "build_audit_envelope",
    "PIPELINE_VERSION", "ELIGIBILITY_VERSION",
    "ROUTING_VERSION", "GATE_VERSION_FIRMWARE",
]
