"""Central registry of ephemeral realtime collections per sport.

The cleanup utility (``services/cleanup/ephemeral_cleanup.py``) reads
this config and uses it to:

1. Decide which collections to scan for orphan documents.
2. Decide which field to use for the canonical_key match against the
   live prop collection.
3. Decide the grace period before TTL purges a marked-inactive doc.

Adding a new sport is a one-line change in the dict below — keep the
utility code sport-agnostic.

ABSOLUTE GUARANTEES — DO NOT BREAK:
- Only ephemeral / realtime / scoring output collections appear here.
- Historical, resolved-outcome, backtest, replay, multiplier-lab, model
  performance, and any other "permanent" collections must NEVER be
  added to a ``collections`` list below. The cleanup utility refuses
  to touch any collection not explicitly listed here.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ──────────────────────────────────────────────────────────────────────
# Config schema
# ──────────────────────────────────────────────────────────────────────
# ``live_collection`` (str) — source of truth for "what's on the current
#     slate". Cleanup builds a set of `canonical_key_field` values from
#     this collection and uses it as the keep-set.
#
# ``canonical_key_field`` (str) — field on both live + ephemeral docs
#     used for the orphan match. Default ``"canonical_key"``.
#
# ``grace_hours`` (int) — once a doc is marked inactive, it lives this
#     many hours longer before Mongo's TTL index purges it (so admins
#     can debug the slate handover after an issue).
#
# ``collections`` (List[CollectionEntry]) — each entry is either:
#     • a bare string (collection name; uses ``canonical_key_field``)
#     • a dict ``{"name": "...", "key_field": "...", "skip_orphan_scan":
#       bool}`` for collections that key on a different field (e.g.
#       per-player cached boards) or want index-only management.
#
# ``enabled`` (bool) — gate the entire sport. False = no-op.
# ──────────────────────────────────────────────────────────────────────

CollectionEntry = Dict[str, Any]


EPHEMERAL_CLEANUP_CONFIG: Dict[str, Dict[str, Any]] = {
    "mlb": {
        "enabled": True,
        "live_collection": "mlb_live_props",
        "canonical_key_field": "canonical_key",
        "grace_hours": 24,
        "collections": [
            # Per-prop scoring output. Hosts the orphan bloat that
            # motivated this cleanup utility (5,581/12,619 unqualified
            # MLB FL OVER docs were stale at 2026-05-15 audit).
            "mlb_prop_scores",
            # Per-player cached board. Doc-level canonical_key is
            # absent (one doc per player; props are nested). We
            # match on the prop list — see _match_doc_to_live() in
            # ephemeral_cleanup.py.
            {
                "name": "mlb_cached_board",
                "key_field": "canonical_key",  # nested in `props[].canonical_key`
                "nested_key_path": "props",
            },
        ],
    },
    "nba": {
        "enabled": True,
        "live_collection": "nba_live_props",
        "canonical_key_field": "canonical_key",
        "grace_hours": 24,
        "collections": [
            "nba_prop_scores",
            {
                "name": "nba_cached_board",
                "key_field": "canonical_key",
                "nested_key_path": "props",
            },
        ],
    },
    # Future sports: copy/paste a block below and set enabled=True.
    "nfl": {
        "enabled": False,
        "live_collection": "nfl_live_props",
        "canonical_key_field": "canonical_key",
        "grace_hours": 24,
        "collections": [
            "nfl_prop_scores",
            {"name": "nfl_cached_board", "key_field": "canonical_key",
             "nested_key_path": "props"},
        ],
    },
}


# ──────────────────────────────────────────────────────────────────────
# Permanent / historical collections that must NEVER be cleaned up.
# Defensive blocklist — used by ephemeral_cleanup.py to refuse any
# accidental misconfiguration that adds one of these to a sport block.
# ──────────────────────────────────────────────────────────────────────
PROTECTED_COLLECTIONS: set = {
    # Resolved outcomes / settled bets (historical truth).
    "mlb_outcomes", "nba_outcomes", "nfl_outcomes",
    "mlb_resolved_props", "nba_resolved_props",
    "settled_bets", "bet_history",
    # Backtests / replay datasets.
    "backtest_results", "backtest_runs", "backtest_eval",
    "replay_evaluations", "replay_outcomes", "replay_runs",
    # PrizePicks multiplier lab.
    "pp_multiplier_lab_runs", "pp_multiplier_lab_combos",
    "pp_multiplier_lab_quotes",
    # Model performance history.
    "model_performance", "model_calibration_runs",
    "mlb_model_performance", "nba_model_performance",
    # Betting log / audit trail.
    "bet_logs", "ai_intel_log", "scoring_lineage",
    # Master hub / player metadata (semi-permanent reference).
    "mlb_master_hub_2026", "nba_master_hub_2026",
    # Game logs, training datasets, model artifacts.
    "mlb_game_logs", "nba_game_logs",
    "training_datasets", "feature_store",
    # Drift / contract enforcement audit (managed by their own TTLs).
    "contract_violations", "board_drift_audit", "sync_locks",
}


def list_configured_sports() -> List[str]:
    """All sports declared in the config (regardless of `enabled`)."""
    return list(EPHEMERAL_CLEANUP_CONFIG.keys())


def get_sport_config(sport: str) -> Dict[str, Any]:
    """Return the config block for ``sport`` (KeyError if missing)."""
    return EPHEMERAL_CLEANUP_CONFIG[sport]


def normalize_collection_entry(entry) -> Dict[str, Any]:
    """Coerce a bare-string entry into the dict form."""
    if isinstance(entry, str):
        return {"name": entry, "key_field": None, "nested_key_path": None}
    out = dict(entry)
    out.setdefault("key_field", None)
    out.setdefault("nested_key_path", None)
    return out


def iter_collections(sport: str):
    """Yield normalized collection dicts for ``sport``."""
    block = EPHEMERAL_CLEANUP_CONFIG.get(sport)
    if not block:
        return
    default_key_field = block.get("canonical_key_field") or "canonical_key"
    for entry in block.get("collections", []):
        norm = normalize_collection_entry(entry)
        if not norm["key_field"]:
            norm["key_field"] = default_key_field
        # Refuse protected collections (defensive — config-time bug).
        if norm["name"] in PROTECTED_COLLECTIONS:
            raise RuntimeError(
                f"ephemeral_collections config error: "
                f"protected collection {norm['name']!r} listed under "
                f"sport={sport!r}. Refusing to expose it to cleanup."
            )
        yield norm
