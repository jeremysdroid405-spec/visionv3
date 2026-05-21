"""
Policy — single source of truth for allowlists.

PROTECTED collections are READ-ONLY no matter what.
WRITABLE collections accept inserts/updates/deletes.
EVERYTHING ELSE is denied.

ALLOWED_JOBS is the explicit allowlist of script modules the API can spawn.
ALLOWED_SERVICES is the supervisor process names allowed via /services/restart.
"""
from __future__ import annotations
import os
from typing import Dict, List, Set, Tuple

# ── 1. Mongo collection policy ────────────────────────────────────────────
# READ-ONLY collections — never accept writes through this API, EVER.
PROTECTED_COLLECTIONS: Set[str] = {
    # raw SGO archive (immutable historical record)
    "sgo_events", "sgo_props_raw", "sgo_players", "sgo_book_consensus",
    "sgo_odds_outcomes", "sgo_player_stats",
    # production prop scoring SSOT
    "live_props", "live_prop_scores", "production_prop_scores",
    "mlb_production_replay_cards", "mlb_production_lines",
    "prop_scores_live", "prop_scores",
    # auth / user / payment domain
    "users", "user_sessions", "auth_tokens", "user_accounts",
    "payments", "stripe_events", "subscriptions", "billing_history",
    # core historical SSOT
    "sgo_pp_research_core", "sgo_pp_research_core_enriched",
    "sgo_pp_research_outcomes",
    "mlb_replay_feature_cache", "mlb_replay_model_outputs",
}

# READ + WRITE collections — research, candidate configs, derived caches.
WRITABLE_COLLECTIONS: Set[str] = {
    # Emergent's research scratch space
    "emergent_research_runs",
    "emergent_research_results",
    "emergent_candidate_configs",
    "emergent_admin_audit_log",
    "emergent_admin_jobs",
    # NFL master hub data (user explicitly allowed)
    "nfl_master_hub_data",
    "nfl_master_hub_cache",
    "nfl_replay_feature_cache",
    "nfl_replay_model_outputs",
    # General research/feature-cache extensions
    "research_grid_runs",
    "research_grid_results",
    "candidate_gate_configs",
    "candidate_thresholds",
    "candidate_model_configs",
    "feature_pipeline_runs",
}

# Read-only allowed (in addition to protected ones, which are read-only too)
READ_ONLY_ALLOWED: Set[str] = (PROTECTED_COLLECTIONS | {
    # diagnostic reads
    "emergent_admin_jobs",
    "emergent_admin_audit_log",
}) | WRITABLE_COLLECTIONS  # writable are also readable


# ── 2. Job allowlist ──────────────────────────────────────────────────────
# Each entry: script module name (used as `python -m <module>`) →
# { "args": list of allowed args, "label": human label, "writes_to": str }
# Only listed args are accepted. Any other arg is rejected.
ALLOWED_JOBS: Dict[str, Dict] = {
    "scripts.sgo.build_pp_research_core": {
        "label": "Build sgo_pp_research_core from raw props",
        "writes_to": "sgo_pp_research_core (PROTECTED on this server — "
                       "operator only) — locked OFF via API",
        "enabled": False,   # protected destination
        "args": [],
    },
    "scripts.sgo.build_historical_consensus_probabilities": {
        "label": "Enrich PP-anchored props with consensus probabilities",
        "writes_to": "sgo_pp_research_core_enriched (PROTECTED) — locked OFF",
        "enabled": False,
        "args": [],
    },
    "scripts.sgo.build_historical_outcomes": {
        "label": "Grade enriched anchors vs player stats",
        "writes_to": "sgo_pp_research_outcomes (PROTECTED) — locked OFF",
        "enabled": False,
        "args": [],
    },
    "scripts.sgo.build_historical_model_features": {
        "label": "Build pre-game features for the model",
        "writes_to": "sgo_pp_research_model_features (research, writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--dry-run",
                  "--resume", "--lookback-days"],
    },
    "scripts.sgo.score_historical_model": {
        "label": "Score historical features with a registered model",
        "writes_to": "sgo_pp_research_model_predictions (research, writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--model-path",
                  "--model-entrypoint", "--model-version",
                  "--feature-keys", "--dry-run", "--resume"],
    },
    "scripts.sgo.ingest_historical_player_stats": {
        "label": "Re-ingest player stats from SGO API",
        "writes_to": "sgo_player_stats (PROTECTED on this server) — locked OFF",
        "enabled": False,
        "args": [],
    },
    "scripts.sgo.verify_sgo_player_stats_coverage": {
        "label": "Read-only coverage report (safe)",
        "writes_to": "(read-only)",
        "enabled": True,
        "args": ["--league", "--start", "--end"],
    },
    # NFL master-hub maintenance hooks (user-allowed)
    "scripts.nfl.refresh_master_hub": {
        "label": "Refresh nfl_master_hub_data + cache",
        "writes_to": "nfl_master_hub_data, nfl_master_hub_cache (writable)",
        "enabled": True,
        "args": ["--week", "--season", "--dry-run", "--force"],
    },
    # Generic grid-search runner
    "scripts.research.grid_sweep": {
        "label": "Grid sweep for thresholds / gates",
        "writes_to": "research_grid_runs, research_grid_results (writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--config", "--dry-run"],
    },
}


# ── 3. Service allowlist ──────────────────────────────────────────────────
# Only these supervisor process names may be restarted via this API.
ALLOWED_SERVICES: Set[str] = {"backend"}
# Explicitly NOT allowed: mongodb, frontend, supervisord, code-server, nginx*


# ── 4. Helpers ────────────────────────────────────────────────────────────
def collection_writable(name: str) -> bool:
    return name in WRITABLE_COLLECTIONS and name not in PROTECTED_COLLECTIONS


def collection_readable(name: str) -> bool:
    return name in READ_ONLY_ALLOWED


def job_allowed(module: str) -> bool:
    entry = ALLOWED_JOBS.get(module)
    return bool(entry and entry.get("enabled"))


def job_args_allowed(module: str, args: List[str]) -> Tuple[bool, List[str]]:
    """Return (ok, rejected_args)."""
    entry = ALLOWED_JOBS.get(module) or {}
    allowed = set(entry.get("args") or [])
    rejected: List[str] = []
    for a in args:
        if a.startswith("--"):
            key = a.split("=", 1)[0]
            if key not in allowed:
                rejected.append(key)
    return len(rejected) == 0, rejected


# ── 5. Public router (introspection of the policy itself) ────────────────
from fastapi import APIRouter, Depends
from .auth import require_admin_token

router = APIRouter()


@router.get("")
@router.get("/")
async def get_policy(_auth=Depends(require_admin_token)):
    return {
        "protected_collections":      sorted(PROTECTED_COLLECTIONS),
        "writable_collections":       sorted(WRITABLE_COLLECTIONS),
        "allowed_services_restart":   sorted(ALLOWED_SERVICES),
        "allowed_jobs":               {
            m: {**v, "args": v.get("args", [])}
            for m, v in ALLOWED_JOBS.items()
        },
        "deny_list_examples": {
            "any_shell_exec":         False,
            "fs_outside_app_dir":     False,
            "mongo_admin":            False,
            "ssh":                    False,
            "destructive_protected":  False,
        },
    }
