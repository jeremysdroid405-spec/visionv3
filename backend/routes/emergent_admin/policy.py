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
    # 2026-05-21 — universal model registry (research-side metadata only;
    # underlying pickles never written by this collection).
    "emergent_model_registry",
    # 2026-05-21 — auto-optimizer state + chosen testing defaults.
    "optimizer_runs",
    "admin_testing_defaults",
    # 2026-05-21 — SGO model predictions are research output, not the
    # raw SGO archive (which lives in `sgo_events`, `sgo_props_raw`,
    # `sgo_player_stats`). Allowing writes so historical scoring runs
    # invoked through the Admin API can populate this collection.
    "sgo_pp_research_model_features",
    "sgo_pp_research_model_predictions",
    # 2026-05-21 — full-pipeline replay output (exact production
    # pipeline replay over historical SGO outcomes).
    "sgo_propvision_full_pipeline_replay",
    "sgo_propvision_full_pipeline_replay_diff",
    "mlb_propvision_full_pipeline_runs",
    "mlb_propvision_full_pipeline_outputs",
    # 2026-05-21 — SGO reshape destination (mirrors mlb_historical_alt_odds_raw
    # schema) + the actual production-pipeline outputs.
    "sgo_replay_alt_odds_raw",
    "mlb_sgo_replay_runs", "mlb_sgo_replay_outputs", "mlb_sgo_replay_cards",
}

# Read-only allowed (in addition to protected ones, which are read-only too)
READ_ONLY_ALLOWED: Set[str] = (PROTECTED_COLLECTIONS | {
    # diagnostic reads
    "emergent_admin_jobs",
    "emergent_admin_audit_log",
    # 2026-05-21 — live MLB pipeline upstreams. READ-ONLY through the
    # Admin API; writes still happen only via the live ingest pipeline
    # or via the explicitly enabled backfill jobs. Researchers need
    # these to inspect coverage before triggering backfills.
    "mlb_master_hub_2026", "mlb_master_hub_data", "mlb_master_hub",
    "mlb_historical_logs", "mlb_player_game_logs",
    "mlb_statcast_player_features", "mlb_statcast_raw",
    "mlb_player_identity_map", "mlb_lineup_resolver",
    "mlb_live_lineup_feed",
    # 2026-05-24 — optimizer audit. Read-only access to the per-cell
    # result rows + run state so the operator can verify any combo
    # against the source data (rather than trust the headline number).
    "optimizer_runs",
    "optimizer_run_results",
    "research_grid_runs",
    "research_grid_results",
    "candidate_thresholds",
    "mlb_replay_model_status",
}) | WRITABLE_COLLECTIONS  # writable are also readable


# ── 2. Job allowlist ──────────────────────────────────────────────────────
# Each entry: script module name (used as `python -m <module>`) →
# { "args": list of allowed args, "label": human label, "writes_to": str }
# Only listed args are accepted. Any other arg is rejected.
ALLOWED_JOBS: Dict[str, Dict] = {
    "scripts.sgo.build_pp_research_core": {
        "label": "Build sgo_pp_research_core (MLB) / sgo_nfl_research_core (NFL)",
        "writes_to": "sgo_pp_research_core OR sgo_nfl_research_core via "
                       "--league=NFL / --out-coll",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--dry-run",
                  "--drop-existing", "--yes", "--out-coll"],
    },
    # 2026-05-23 — NFL data discovery probe. Read-only against SGO.
    # Prints distinct (stat_id, market) pairs + sample playerStats keys,
    # validates services.replay.nfl_stat_family_map coverage, never
    # writes to Mongo.
    "scripts.sgo.probe_nfl_data": {
        "label": "Read-only NFL SGO data-discovery probe",
        "writes_to": "(read-only)",
        "enabled": True,
        "args": ["--start", "--end", "--max-events", "--save-samples"],
    },
    # 2026-05-24 — MLB market-availability probe. Read-only against SGO.
    # Dumps every market_id + statID returned for a MLB window so the
    # operator can confirm whether HR / SB / pitches_thrown / fantasy
    # are present in SGO's PrizePicks-anchored feed before chasing
    # ingest bugs.
    "scripts.sgo.probe_mlb_markets": {
        "label": "Read-only MLB SGO market probe (HR / SB / pitch count audit)",
        "writes_to": "(read-only)",
        "enabled": True,
        "args": ["--start", "--end", "--max-events", "--save"],
    },
    "scripts.sgo.build_historical_consensus_probabilities": {
        "label": "Enrich PP-anchored props with consensus probabilities",
        "writes_to": "sgo_pp_research_core_enriched (PROTECTED) — locked OFF",
        "enabled": False,
        "args": [],
    },
    "scripts.sgo.build_historical_outcomes": {
        "label": "Grade research-core anchors vs player stats "
                   "(MLB → sgo_pp_research_outcomes; "
                   "NFL → sgo_nfl_research_outcomes)",
        "writes_to": "sgo_pp_research_outcomes / sgo_nfl_research_outcomes",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--dry-run", "--resume",
                  "--debug-unresolved", "--limit",
                  "--out-coll", "--src-coll"],
    },
    "scripts.sgo.build_historical_model_features": {
        "label": "Build pre-game features for the model",
        "writes_to": "sgo_pp_research_model_features (research, writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--dry-run",
                  "--resume", "--lookback-days"],
    },
    "scripts.mlb_replay_build_feature_cache": {
        "label": "Build MLB Layer-2 feature cache for a date window",
        "writes_to": "mlb_replay_feature_cache (gated)",
        "enabled": True,
        "args": ["--date", "--start", "--end", "--mem-limit",
                  "--force", "--odds-collection", "--feature-source",
                  "--league", "--sgo-lookback-days", "--min-prior-games"],
    },
    "scripts.ingest_bdl_mlb_season": {
        "label": "Backfill MLB season game logs from BallDontLie",
        "writes_to": "bdl_mlb_historical_game_logs + "
                       "mlb_master_hub_2026.bdl_game_logs (per-season merge)",
        "enabled": True,
        "args": ["--season", "--use-existing-roster", "--skip-stats",
                  "--limit-players", "--force"],
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
        "label": "Cache-first ingest of historical player stats. "
                   "Skips events already in sgo_player_stats; uses SGO API "
                   "ONLY for gaps. --force bypasses cache.",
        "writes_to": "sgo_player_stats (research-controlled writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--source", "--dry-run",
                  "--resume", "--force", "--limit", "--debug-unresolved",
                  "--sgo-rpm", "--sleep-between-requests", "--rate-limit-ms",
                  "--abort-after-consecutive-429s", "--max-events",
                  "--retry-failed"],
    },
    "scripts.sgo.verify_sgo_player_stats_coverage": {
        "label": "Read-only coverage report (safe)",
        "writes_to": "(read-only)",
        "enabled": True,
        "args": ["--league", "--start", "--end"],
    },
    # 2026-05-21 — Live MLB-HF scorer driven over SGO historical features.
    # Writes the production gate-required row schema (μ, σ, TP, CV,
    # edge, projection_margin, hit_rates) into sgo_pp_research_model_predictions
    # so SH/FL/WZ gate replays evaluate the EXACT model output the live
    # pipeline emits. Has --probe mode for cheap dependency verification.
    "scripts.sgo.score_historical_with_live_mlb_hf": {
        "label": "Score SGO features with the live MLB-HF model",
        "writes_to": "sgo_pp_research_model_predictions (writable on prod)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--probe", "--limit",
                  "--force", "--dry-run",
                  "--strict-min-scored-ratio", "--dump-predictions"],
    },
    # 2026-05-21 — exact production-pipeline replay driver.
    # SGO outcome row → live MLBHighFrictionModel.predict(as_of_date) →
    # books-only edge → live SH/FL/WZ gate eval → tier selection →
    # write to sgo_propvision_full_pipeline_replay.
    "scripts.sgo.historical_full_pipeline_replay": {
        "label": "Replay historical SGO props through the live "
                   "PropVision scoring + gate pipeline (SSOT mode — "
                   "delegates to production_replay_runner)",
        "writes_to": "sgo_propvision_full_pipeline_replay, "
                       "sgo_propvision_full_pipeline_replay_diff, "
                       "mlb_propvision_full_pipeline_runs/outputs (writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--exclude-stat-family",
                  "--limit", "--force", "--dry-run",
                  # SSOT refactor flags (2026-05-21)
                  "--tiers", "--gate-path", "--canonical-path",
                  "--snapshot-hour", "--limit-dates",
                  "--no-mirror-to-legacy", "--sample-diff",
                  "--continue-on-error",
                  # Research mode (2026-05-22)
                  "--research-mode", "--skip-production-gates",
                  # Single-pass mode (2026-05-24) — tiers route by odds
                  "--multi-tier-gates"],
    },
    # 2026-05-21 — per-tier × per-stat_family threshold sweep over
    # the sgo_propvision_full_pipeline_replay collection.
    "scripts.sgo.historical_gate_replay_grid": {
        "label": "Per-tier × per-stat_family threshold sweep",
        "writes_to": "research_grid_runs, research_grid_results, "
                       "candidate_gate_configs (writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--min-bets", "--dry-run"],
    },
    # 2026-05-21 — reshape SGO enriched offers into the schema
    # expected by `mlb_historical_alt_odds_raw`, so the EXISTING
    # production replay pipeline can be driven over SGO data.
    "scripts.sgo.reshape_sgo_to_replay_odds": {
        "label": "Reshape SGO enriched → mlb_historical_alt_odds_raw shape",
        "writes_to": "sgo_replay_alt_odds_raw (writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--limit",
                  "--source", "--debug-source"],
    },
    # 2026-05-21 — invoke the existing production replay pipeline
    # against the SGO-derived odds, writing to the `sgo_replay` namespace.
    "scripts.sgo.run_sgo_production_replay": {
        "label": "Run the live production replay pipeline against SGO odds",
        "writes_to": "mlb_sgo_replay_runs/outputs/cards (writable)",
        "enabled": True,
        "args": ["--start", "--end", "--tier", "--gate-path",
                  "--canonical-path", "--limit-dates", "--dry-run"],
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
        "label": "Outcome-side grid sweep over sgo_pp_research_outcomes",
        "writes_to": "research_grid_runs, research_grid_results, "
                       "candidate_thresholds (all writable)",
        "enabled": True,
        "args": ["--league", "--start", "--end", "--dataset",
                  "--exclude-stat-family", "--min-bets", "--config",
                  "--dry-run"],
    },
    # 2026-05-21 — canonical-name backfill for legacy stat_family values.
    # Writes to mlb_replay_feature_cache / mlb_replay_model_outputs, which
    # are PROTECTED. Default --dry-run is safe; --commit is gated behind
    # the same admin token. Re-runs are idempotent (already-canonical rows
    # are skipped). Operators must whitelist this explicitly per-run.
    "scripts.research.backfill_stat_family_canonical": {
        "label": "Backfill canonical stat_family on legacy replay rows",
        "writes_to": "mlb_replay_feature_cache, mlb_replay_model_outputs "
                       "(idempotent; --dry-run is the default)",
        "enabled": True,
        "args": ["--collection", "--league", "--commit", "--dry-run",
                  "--chunk-size", "--sample-limit"],
    },
    # 2026-05-23 — out-of-process optimizer runner used by the
    # research_worker daemon. Never invoked directly by humans; the
    # /optimizer/run endpoint enqueues a job with --run-id pointing at a
    # persisted optimizer_runs doc.
    "scripts.research.run_optimizer_cli": {
        "label": "Out-of-process optimizer executor (worker-managed)",
        "writes_to": "optimizer_runs, candidate_thresholds (writable)",
        "enabled": True,
        "args": ["--run-id"],
    },
}


# ── 3. Service allowlist ──────────────────────────────────────────────────
# Only these supervisor process names may be restarted via this API.
ALLOWED_SERVICES: Set[str] = {"backend", "research_worker"}

# 2026-05-21 — git branches the /deploy endpoint may pull. Strict allowlist;
# typos and arbitrary refs are rejected at request time.
ALLOWED_GIT_BRANCHES: Set[str] = {"newestbuild"}
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
