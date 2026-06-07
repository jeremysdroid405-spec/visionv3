"""
Preflight diagnostics — read-only health check for the historical-replay
research stack. Surfaces everything the frontend Diagnostics panel needs
without exposing any write capability.

GET /api/emergent-admin/preflight/  →
{
  ok: bool,
  admin_api: { connected, agent_id, token_hash },
  policy:    { allowed_jobs: [...] },
  models:    { production_artifacts: {mlb:[…], nba:[…], nfl:[…]} },
  collections: { name → {exists, count}, … },
  recent_jobs: [{job_id, module, status, finished_at}, …],
  warnings:    [{code, severity, message, fix_job, fix_args?}, …],
}

This endpoint NEVER triggers a job. It only reads counts / lists / file
existence. Action items it surfaces include the job module the operator
should run to remediate (which they trigger themselves through /jobs/run).
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from .auth import audit_log, require_admin_token, _get_db
from .policy import ALLOWED_JOBS

router = APIRouter()

# Collections the research stack depends on, partitioned by role.
# "read"  = required upstream (live or historical input)
# "write" = research output (must be the only writable target)
RESEARCH_DEPS: Dict[str, Dict[str, str]] = {
    # Inputs
    "sgo_pp_research_core":          {"role": "read",  "league": "ALL"},
    "sgo_pp_research_core_enriched": {"role": "read",  "league": "ALL"},
    "sgo_pp_research_outcomes":      {"role": "read",  "league": "ALL"},
    "sgo_player_stats":              {"role": "read",  "league": "ALL"},
    "sgo_replay_alt_odds_raw":       {"role": "read",  "league": "MLB"},
    "mlb_master_hub_2026":           {"role": "read",  "league": "MLB"},
    "mlb_historical_logs":           {"role": "read",  "league": "MLB"},
    "mlb_statcast_raw":              {"role": "read",  "league": "MLB"},
    # Research outputs (writable by allowlisted research jobs only)
    "sgo_pp_research_model_features":      {"role": "write", "league": "ALL"},
    "sgo_pp_research_model_predictions":   {"role": "write", "league": "ALL"},
    "sgo_propvision_full_pipeline_replay": {"role": "write", "league": "ALL"},
    "research_grid_runs":                  {"role": "write", "league": "ALL"},
    "research_grid_results":               {"role": "write", "league": "ALL"},
    "candidate_gate_configs":              {"role": "write", "league": "ALL"},
    "candidate_thresholds":                {"role": "write", "league": "ALL"},
    "emergent_model_registry":             {"role": "write", "league": "ALL"},
}

# Production model artifact directories per league (read-only check)
MODEL_DIRS: Dict[str, List[str]] = {
    "MLB": [
        "/var/www/app/backend/models/mlb_hf",
        "/var/www/app/backend/models/mlb_hf",
    ],
    "NBA": [
        "/var/www/app/backend/models/nba",
        "/var/www/app/backend/models/nba",
    ],
    "NFL": [
        "/var/www/app/backend/models/nfl",
        "/var/www/app/backend/models/nfl",
    ],
}


def _scan_model_dir(paths: List[str]) -> Dict[str, Any]:
    for p in paths:
        if os.path.isdir(p):
            try:
                files = sorted(f for f in os.listdir(p) if f.endswith(".pkl"))
                return {
                    "path": p,
                    "exists": True,
                    "n_pickles": len(files),
                    "pickles": files[:40],   # cap for payload size
                }
            except OSError as e:
                return {"path": p, "exists": True, "error": str(e),
                          "n_pickles": 0, "pickles": []}
    return {"path": None, "exists": False, "n_pickles": 0, "pickles": []}


def _classify_warnings(collections: Dict[str, Dict[str, Any]],
                          models: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    # Empty critical inputs
    if collections.get("sgo_pp_research_outcomes", {}).get("count", 0) == 0:
        out.append({
            "code": "no_outcomes",
            "severity": "high",
            "message": "sgo_pp_research_outcomes is empty — no graded "
                          "outcomes available for replay/sweep.",
            "fix_job": "scripts.sgo.build_historical_outcomes",
            "fix_args": [],
        })
    if collections.get("sgo_replay_alt_odds_raw", {}).get("count", 0) == 0:
        out.append({
            "code": "no_reshape_odds",
            "severity": "high",
            "message": "sgo_replay_alt_odds_raw is empty — historical "
                          "full pipeline replay (SSOT mode) requires the "
                          "production odds-shape reshape to have run first. "
                          "Without it the replay script HARD-FAILS at "
                          "preflight (no silent fallback).",
            "fix_job": "scripts.sgo.reshape_sgo_to_replay_odds",
            "fix_args": [],
        })
    if collections.get("sgo_player_stats", {}).get("count", 0) == 0:
        out.append({
            "code": "no_player_stats",
            "severity": "high",
            "message": "sgo_player_stats is empty — feature hydration "
                          "will fail.",
            "fix_job": "scripts.sgo.ingest_historical_player_stats",
            "fix_args": [],
        })
    if collections.get("sgo_pp_research_model_features", {}).get("count", 0) == 0:
        out.append({
            "code": "no_features",
            "severity": "medium",
            "message": "No model features built yet.",
            "fix_job": "scripts.sgo.build_historical_model_features",
            "fix_args": [],
        })
    if collections.get("sgo_pp_research_model_predictions", {}).get("count", 0) == 0:
        out.append({
            "code": "no_predictions",
            "severity": "medium",
            "message": "No model predictions written yet.",
            "fix_job": "scripts.sgo.score_historical_with_live_mlb_hf",
            "fix_args": [],
        })
    if collections.get("sgo_propvision_full_pipeline_replay", {}).get("count", 0) == 0:
        out.append({
            "code": "no_replay_rows",
            "severity": "medium",
            "message": "No full-pipeline replay rows yet.",
            "fix_job": "scripts.sgo.historical_full_pipeline_replay",
            "fix_args": [],
        })
    if collections.get("research_grid_results", {}).get("count", 0) == 0:
        out.append({
            "code": "no_grid_results",
            "severity": "low",
            "message": "No grid-sweep results recorded yet.",
            "fix_job": "scripts.sgo.historical_gate_replay_grid",
            "fix_args": [],
        })
    # Missing model artifacts per sport
    for league, info in models.items():
        if not info.get("exists"):
            out.append({
                "code": f"no_model_dir_{league.lower()}",
                "severity": "low" if league != "MLB" else "high",
                "message": f"No {league} model artifact directory found.",
                "fix_job": None,
                "fix_args": [],
            })
        elif info.get("n_pickles", 0) == 0:
            out.append({
                "code": f"empty_model_dir_{league.lower()}",
                "severity": "medium",
                "message": f"{league} model directory empty (no .pkl files).",
                "fix_job": None,
                "fix_args": [],
            })
    return out


@router.get("")
@router.get("/")
async def preflight(request: Request, auth=Depends(require_admin_token)):
    db = _get_db()
    started = datetime.now(timezone.utc)

    # Collection existence + counts
    coll_state: Dict[str, Dict[str, Any]] = {}
    existing_set = set(await db.list_collection_names())
    for name, meta in RESEARCH_DEPS.items():
        info: Dict[str, Any] = {"role": meta["role"], "league": meta["league"],
                                   "exists": name in existing_set, "count": 0}
        if info["exists"]:
            try:
                info["count"] = await db[name].estimated_document_count()
            except Exception as e:  # pragma: no cover (mongo edge)
                info["count_error"] = repr(e)
        coll_state[name] = info

    # Model artifacts
    models = {league: _scan_model_dir(paths)
                for league, paths in MODEL_DIRS.items()}

    # Recent jobs (just headers, plus error/traceback/tail_preview fields
    # which the Diagnostics UI uses to inline-render failures without an
    # extra /jobs/{id} round-trip).
    recent_jobs: List[Dict[str, Any]] = []
    cur = db["emergent_admin_jobs"].find(
        {}, {"_id": 0, "log": 0}).sort([("queued_at", -1)]).limit(25)
    async for d in cur:
        recent_jobs.append(d)

    warnings = _classify_warnings(coll_state, models)

    payload = {
        "ok": True,
        "ts": started.isoformat(),
        "admin_api": {
            "connected": True,
            "agent_id":  auth["agent_id"],
            "token_hash": auth["token_hash"],
        },
        "policy": {
            "allowed_jobs": [
                {"module": m, **{k: v for k, v in info.items() if k != "args"}}
                for m, info in ALLOWED_JOBS.items()
            ],
            "research_writable_collections": [
                n for n, m in RESEARCH_DEPS.items() if m["role"] == "write"
            ],
            "research_readable_collections": [
                n for n, m in RESEARCH_DEPS.items() if m["role"] == "read"
            ],
            "isolation_promise": (
                "Research jobs write ONLY to research-namespaced collections. "
                "Live prop_scores / cached_board / mlb_test_outputs / production "
                "tier collections are READ-ONLY through this API."
            ),
        },
        "collections": coll_state,
        "models":      models,
        "recent_jobs": recent_jobs,
        "warnings":    warnings,
    }
    await audit_log(request, action="preflight",
                      params={},
                      response_summary={
                          "n_warnings": len(warnings),
                          "n_collections": len(coll_state),
                      },
                      **auth)
    return payload
