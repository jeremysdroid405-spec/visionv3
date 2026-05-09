"""
Replay cache & fingerprint registry — the substrate for fast iteration
on gate / threshold / TP changes without re-running the expensive
feature build + VK2 predict pipeline.

Three persistent layers (Stage A, Stage B, Stage C from the spec):

  Stage A — IMMUTABLE source data (already exists):
    replay_odds_snapshots, replay_props_normalized, replay_results,
    bdl_historical_game_logs, bdl_advanced_stats.

  Stage B — CACHED model + scoring INPUTS, this module:
    `replay_vk2_cache`. Per (event_id, snapshot_label, canonical_key, side):
      - vk2_blob (projection / sigma / p_over / model_version / feature_hash …)
      - feature_set (μ/σ/CV/HR ladder)
      - by_book layers
      - ref_book / ref_odds
      - tp_blob (de-vigged TP per book + chosen TP)
      - edge_pct
      - vk2_model_hash, feature_pipeline_hash

  Stage C — light-weight scoring (downstream, recompute-cheap):
    compute_scoring_stack → tier / gate_results / vision_score_v2.
    The incremental driver only touches Stage C.

Write path: invoked inside `services/replay/engine.py::run_replay_engine`
when `cache_outputs=True` (the default for VK2-enabled runs).

Read path: `services/replay/scoring_only.py::run_scoring_only` iterates
this cache and produces `replay_evaluations` rows under a fresh
`replay_run_id`.

Cache invalidation rules (deterministic, keyed on fingerprints stamped
on each cache row vs the current process):

  reuse-OK    : gate_config_hash and/or tp_engine_hash differ only.
                → use cache; write fresh `replay_evaluations`.
  partial     : feature_pipeline_hash differs but vk2_model_hash matches.
                → cache rows with the OLD pipeline hash are STALE; the
                incremental driver refuses to use them and prints a hint
                to re-run the full engine for those rows.
  partial-fam : vk2_model_hash differs only for SOME stat families.
                → cache rows for those families are STALE; rows for
                other families are reused.
  full-rebuild: anything else → re-run full engine.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import pickle
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

REPLAY_VK2_CACHE = "replay_vk2_cache"


# ============================================================================
# Fingerprint helpers — single source of truth for "what version of the
# pipeline produced this row".
# ============================================================================
def _sha1_of_obj(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def _sha1_of_file(path: str) -> str:
    if not os.path.exists(path):
        return f"missing:{os.path.basename(path)}"
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def vk2_model_hash() -> Dict[str, str]:
    """Per-stat content hash of every VK2 model pickle. Per-family
    keys let the diff runner say e.g. 'AST model changed; PTS rows
    reusable'."""
    out: Dict[str, str] = {}
    base = "/app/backend/models"
    for fn in ("vk2_pts.pkl", "vk2_reb.pkl", "vk2_ast.pkl",
              "vk2_3pm.pkl", "vk2_pra.pkl"):
        out[fn.replace("vk2_", "").replace(".pkl", "").upper()] = (
            _sha1_of_file(os.path.join(base, fn))
        )
    return out


def feature_pipeline_hash() -> str:
    """Hash of the feature-builder source (`nba_vk2_features.py`) +
    its declared ADV_FIELDS list. Feature schema changes will
    invalidate every cache row."""
    return _sha1_of_file(
        "/app/backend/services/scoring/nba_vk2_features.py"
    )


def gate_config_hash(sport: str = "nba") -> str:
    """Hash the THRESHOLDS dict for the sport (the value the gate
    engine looks up). This deliberately HASHES THE DATA, not the
    file, so a comment edit in `thresholds.py` won't bump the hash."""
    try:
        from services.scoring.gates.thresholds import THRESHOLDS
        return _sha1_of_obj(THRESHOLDS.get(sport, {}))
    except Exception as exc:  # noqa: BLE001
        return f"error:{exc}"


def tp_engine_hash() -> str:
    """Hash the TP engine source. Bumping any TP formula here will
    invalidate cached `tp_blob` outputs (the incremental driver
    refuses to reuse them)."""
    return _sha1_of_file(
        "/app/backend/services/scoring/tp_engine.py"
    )


def matchup_pipeline_hash() -> str:
    """Content hash of the matchup/pace pipeline source. When this
    bumps, every cache row's `matchup_blob` is stale and the cache
    must be rebuilt for the matchup family. The incremental scorer
    surfaces this as a `matchup_pipeline_hash` diff so callers know
    to invalidate."""
    return _sha1_of_file(
        "/app/backend/services/replay/matchup.py"
    )


def injury_pipeline_hash() -> str:
    """Content hash of the (future) historical injury pipeline.
    Returns the placeholder hash `not_implemented:vN` until the
    injury layer ships — this lets diff runners distinguish
    'no injury wired yet' from 'pipeline changed'."""
    path = "/app/backend/services/replay/injury_history.py"
    if not os.path.exists(path):
        return "not_implemented:v1"
    return _sha1_of_file(path)


def fingerprint_block(sport: str = "nba") -> Dict[str, Any]:
    """One-stop dict to stamp on every cache row + every replay run
    document. Comparing two of these tells the diff runner exactly
    what changed between executions."""
    return {
        "vk2_model_hash":         vk2_model_hash(),
        "feature_pipeline_hash":  feature_pipeline_hash(),
        "gate_config_hash":       gate_config_hash(sport),
        "tp_engine_hash":         tp_engine_hash(),
        "matchup_pipeline_hash":  matchup_pipeline_hash(),
        "injury_pipeline_hash":   injury_pipeline_hash(),
        "stamped_at_utc":         datetime.now(timezone.utc).isoformat(),
    }


def changed_components(before: Dict[str, Any],
                       after: Dict[str, Any]) -> List[str]:
    """Pretty list of which fingerprint components differ. Used by
    the diff runner."""
    diffs: List[str] = []
    if before.get("vk2_model_hash") != after.get("vk2_model_hash"):
        diffs.append("vk2_model")
        b = before.get("vk2_model_hash") or {}
        a = after.get("vk2_model_hash") or {}
        per = sorted(set(b) | set(a))
        for stat in per:
            if b.get(stat) != a.get(stat):
                diffs.append(f"vk2_model.{stat}")
    for k in ("feature_pipeline_hash", "gate_config_hash",
              "tp_engine_hash", "matchup_pipeline_hash",
              "injury_pipeline_hash"):
        if before.get(k) != after.get(k):
            diffs.append(k)
    return diffs


# ============================================================================
# Invalidation rules — what's reusable, what isn't.
# ============================================================================
# Each entry maps a component name (as returned by `changed_components`)
# to the cache fields that become STALE when that component bumps.
# Stage-C's job is to refuse to use a stale row.
INVALIDATION_RULES: Dict[str, List[str]] = {
    # Gate / threshold changes don't invalidate ANY cache fields —
    # Stage-C just reruns scoring on the same inputs.
    "gate_config_hash":       [],
    "tp_engine_hash":         ["tp_blob", "edge_pct"],
    # Feature pipeline change → VK2 inputs differ → vk2_blob stale.
    "feature_pipeline_hash":  ["vk2_blob", "feature_set"],
    # Per-family VK2 model retrained → only that family's vk2_blob.
    # The diff runner emits "vk2_model.PTS"; downstream filtering by
    # `stat_family` is the recommended workflow.
    "vk2_model":              ["vk2_blob"],
    # Matchup/pace pipeline edit → matchup_blob stale.
    "matchup_pipeline_hash":  ["matchup_blob"],
    "injury_pipeline_hash":   ["injury_blob"],
}


def stale_cache_fields(diffs: List[str]) -> List[str]:
    """Given a `changed_components` output, return the union of
    cache fields that callers must NOT trust on existing rows."""
    stale = []
    for d in diffs:
        # Per-family vk2 diffs (`vk2_model.PTS`) all map to the same
        # cache field, just scoped by stat_family — caller filters.
        key = d.split(".")[0]
        for f in INVALIDATION_RULES.get(key, []):
            if f not in stale:
                stale.append(f)
    return stale


# ============================================================================
# Cache row builder & I/O.
# ============================================================================
def cache_row(*,
              source_run_id: str,
              event_id: str,
              snapshot_label: str,
              canonical_key: str,
              market_key: Optional[str],
              stat_family: str,
              player: str,
              line: float,
              side: str,
              commence_time: Optional[datetime],
              snapshot_ts: Optional[datetime],
              by_book_layers: Dict[str, Any],
              ref_book: Optional[str],
              ref_odds: Optional[int],
              tp_blob: Dict[str, Any],
              edge_pct: Optional[float],
              vk2_blob: Dict[str, Any],
              feature_set: Optional[Dict[str, Any]],
              ) -> Dict[str, Any]:
    """Build a cache row. Everything expensive sits here; the
    incremental scorer reads exactly this and never touches BDL or
    The Odds API."""
    return {
        "source_run_id":   source_run_id,
        "event_id":        event_id,
        "snapshot_label":  snapshot_label,
        "canonical_key":   canonical_key,
        "market_key":      market_key,
        "stat_family":     stat_family,
        "player":          player,
        "line":            line,
        "side":            side,
        "commence_time":   commence_time,
        "snapshot_ts":     snapshot_ts,
        # The expensive payload.
        "by_book_layers":  by_book_layers,
        "ref_book":        ref_book,
        "ref_odds":        ref_odds,
        "tp_blob":         tp_blob,
        "edge_pct":        edge_pct,
        "vk2_blob":        vk2_blob,
        "feature_set":     feature_set,
        # Lineage.
        "vk2_model_hash":        vk2_model_hash().get(
            (vk2_blob or {}).get("model_version_family") or
            (stat_family or "").replace("THREES", "3PM").upper()
        ),
        "feature_pipeline_hash": feature_pipeline_hash(),
        "cached_at":             datetime.now(timezone.utc),
    }


async def ensure_cache_indexes(db) -> List[str]:
    """Idempotent index creation. Uniqueness on the cache key prevents
    duplicate rows when the engine is resumed."""
    coll = db[REPLAY_VK2_CACHE]
    out = []
    out.append(await coll.create_index(
        [("event_id", 1), ("snapshot_label", 1),
         ("canonical_key", 1), ("side", 1)],
        name="uniq_event_snap_can_side", unique=True))
    out.append(await coll.create_index(
        [("source_run_id", 1)], name="source_run_id"))
    out.append(await coll.create_index(
        [("stat_family", 1)], name="stat_family"))
    return out


__all__ = [
    "REPLAY_VK2_CACHE",
    "vk2_model_hash", "feature_pipeline_hash",
    "gate_config_hash", "tp_engine_hash",
    "matchup_pipeline_hash", "injury_pipeline_hash",
    "fingerprint_block", "changed_components",
    "stale_cache_fields", "INVALIDATION_RULES",
    "cache_row", "ensure_cache_indexes",
]
