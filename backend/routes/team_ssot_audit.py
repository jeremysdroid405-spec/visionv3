"""
GET /api/v3/pipeline-audit/team-ssot — Team-prop pipeline SSOT health
snapshot for the Quant Terminal dashboard.

Returns per-sport status across all 8 pipeline stages, latest activity
dates, row counts, a red/yellow/green health glyph, and a list of
stale-data warnings. Mirrors the structure of
`scripts/team_pipeline_ssot_audit.py` so the CLI and the API agree on
gap definitions.

Response shape:
    {
      "generated_at": "<iso>",
      "now_iso":      "<iso>",
      "stale_thresholds": {
          "team_live_props_min":   60,
          "team_prop_scores_min":  60,
          "optimizer_run_hours":   168
      },
      "sports": [
        {
          "sport":         "nba",
          "league":        "NBA",
          "health":        "green" | "yellow" | "red",
          "stages": {
            "ingest":        {"count": int, "latest_iso": str|None,
                              "lag_minutes": float|None, "status": glyph},
            "features":      {... "latest_date": YYYY-MM-DD},
            "outcomes":      {...},
            "feature_cache": {...},
            "score":         {"adapters": {"h2h": bool, ...},
                              "all_present": bool, "status": glyph},
            "reshape":       {... "latest_date": YYYY-MM-DD},
            "replay":        {"reshape_count": int, "scored_count": int,
                              "scored_pct": float, "status": glyph},
            "grid":          {"latest_run_id": str|None,
                              "latest_started_at": iso|None,
                              "age_hours": float|None, "status": glyph}
          },
          "warnings": ["..."]
        },
        ...
      ],
      "scheduler": {
        "paused":          bool,
        "manual_override": bool,
        "reason":          str,
        "set_by":          str,
        "set_at":          iso|None,
        "warning":         str|None
      },
      "summary": {
        "overall_health":  "green" | "yellow" | "red",
        "gap_count":       int,
        "warning_count":   int
      }
    }

Health rules (per sport):
  - red    : any gap in score / reshape / replay / grid
  - yellow : all stages ✓ but ingest/scores stale (> threshold)
  - green  : everything green and fresh

Scheduler pause is surfaced as a global warning but does NOT downgrade
per-sport health on its own — the audit reflects DATA state, not
process state. The two are explicit and independent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter(tags=["pipeline-audit"])

_db = None

# ── Constants pulled from the CLI audit so the two never drift. ──
SPORTS = ("mlb", "nba", "nfl")
MARKET_CATEGORIES = ("h2h", "spread", "game_total", "team_total")
ARTIFACT_ROOT = Path("/var/www/app/backend/models/team_xgb")
REPLAY_COLL = "sgo_propvision_full_pipeline_replay"

# Stale thresholds (minutes / hours). Live odds + scores should be
# within ~1 hour during active sync; optimizer runs should be within
# the last week.
STALE_LIVE_PROPS_MIN = 60
STALE_SCORES_MIN     = 60
STALE_GRID_HOURS     = 24 * 7


def set_team_ssot_audit_db(db) -> None:
    global _db
    _db = db


# ── Time helpers (tolerant of mixed iso shapes in the wild). ─────
def _parse_iso(s: Optional[Any]) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    if not isinstance(s, str):
        return None
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _lag_minutes(iso: Optional[Any], *, now: datetime) -> Optional[float]:
    ts = _parse_iso(iso)
    if ts is None:
        return None
    return round((now - ts).total_seconds() / 60.0, 1)


def _glyph(ok: bool) -> str:
    return "ok" if ok else "fail"


async def _latest_field(coll: str, flt: Dict[str, Any],
                          field: str) -> Optional[Any]:
    doc = await _db[coll].find_one(
        {**flt, field: {"$ne": None}},
        sort=[(field, -1)],
        projection={field: 1, "_id": 0},
    )
    return (doc or {}).get(field)


# ── Per-stage probes. Each returns a stage dict ready for the API. ─
async def _stage_ingest(sport: str, now: datetime) -> Dict[str, Any]:
    count = await _db["team_live_props"].count_documents({"sport": sport})
    latest = await _latest_field("team_live_props", {"sport": sport},
                                   "snapshot_iso")
    if latest is None:
        latest = await _latest_field(
            "team_live_props", {"sport": sport}, "ingested_at")
    lag = _lag_minutes(latest, now=now)
    return {
        "count":       count,
        "latest_iso":  str(latest) if latest else None,
        "lag_minutes": lag,
        "status":      _glyph(count > 0),
    }


async def _stage_features(sport: str) -> Dict[str, Any]:
    count = await _db["team_model_features"].count_documents(
        {"sport": sport})
    latest = await _latest_field(
        "team_model_features", {"sport": sport}, "as_of_date")
    return {
        "count":        count,
        "latest_date":  latest,
        "status":       _glyph(count > 0),
    }


async def _stage_outcomes(sport: str) -> Dict[str, Any]:
    count = await _db["team_historical_outcomes"].count_documents(
        {"sport": sport})
    latest = await _latest_field(
        "team_historical_outcomes", {"sport": sport}, "game_date")
    return {
        "count":       count,
        "latest_date": latest,
        "status":      _glyph(count > 0),
    }


async def _stage_feature_cache(sport: str) -> Dict[str, Any]:
    count = await _db["team_model_prop_features"].count_documents(
        {"sport": sport})
    latest = await _latest_field(
        "team_model_prop_features", {"sport": sport}, "game_date")
    return {
        "count":       count,
        "latest_date": latest,
        "status":      _glyph(count > 0),
    }


def _stage_score(sport: str) -> Dict[str, Any]:
    """Per-market-category XGB artifact existence on disk."""
    adapters = {
        mc: (ARTIFACT_ROOT / sport / f"{mc}.pkl").exists()
        for mc in MARKET_CATEGORIES
    }
    all_present = all(adapters.values())
    return {
        "adapters":    adapters,
        "all_present": all_present,
        "status":      _glyph(all_present),
    }


async def _stage_reshape(sport: str) -> Dict[str, Any]:
    count = await _db[REPLAY_COLL].count_documents(
        {"prop_type": "team", "sport": sport})
    latest = await _latest_field(
        REPLAY_COLL,
        {"prop_type": "team", "sport": sport}, "game_date")
    return {
        "count":       count,
        "latest_date": latest,
        "status":      _glyph(count > 0),
    }


async def _stage_replay(sport: str, reshape_count: int) -> Dict[str, Any]:
    """Replay = reshape rows that carry model_probability +
    model_version='team_xgb_v1'. Anything less is a partial-replay
    coverage gap."""
    scored = await _db[REPLAY_COLL].count_documents({
        "prop_type":         "team",
        "sport":             sport,
        "model_probability": {"$ne": None},
        "model_version":     "team_xgb_v1",
    })
    pct = (round(scored / reshape_count * 100.0, 2)
            if reshape_count > 0 else 0.0)
    ok = (reshape_count > 0) and (scored == reshape_count)
    return {
        "reshape_count": reshape_count,
        "scored_count":  scored,
        "scored_pct":    pct,
        "status":        _glyph(ok),
    }


async def _stage_grid(sport: str, *, now: datetime) -> Dict[str, Any]:
    doc = await _db["optimizer_runs"].find_one(
        {"request.prop_type": "team",
         "request.sport":     sport.upper(),
         "status":            "succeeded"},
        sort=[("started_at", -1)],
        projection={"_id": 0, "run_id": 1, "started_at": 1,
                      "request.start": 1, "request.end": 1,
                      "combos_tested": 1, "cells_done": 1},
    ) or {}
    started = doc.get("started_at")
    age_hr = None
    ts = _parse_iso(started)
    if ts is not None:
        age_hr = round((now - ts).total_seconds() / 3600.0, 1)
    return {
        "latest_run_id":     doc.get("run_id"),
        "latest_started_at": str(started) if started else None,
        "age_hours":         age_hr,
        "window":            (doc.get("request") or {}),
        "combos_tested":     doc.get("combos_tested"),
        "cells_done":        doc.get("cells_done"),
        "status":            _glyph(bool(doc.get("run_id"))),
    }


# ── Per-sport assembly + health roll-up. ─────────────────────────
async def _audit_sport(sport: str, *, now: datetime) -> Dict[str, Any]:
    ingest        = await _stage_ingest(sport, now)
    features      = await _stage_features(sport)
    outcomes      = await _stage_outcomes(sport)
    feature_cache = await _stage_feature_cache(sport)
    score         = _stage_score(sport)
    reshape       = await _stage_reshape(sport)
    replay        = await _stage_replay(sport, reshape["count"])
    grid          = await _stage_grid(sport, now=now)

    warnings: List[str] = []

    # ── Adapter gaps drive red ───────────────────────────────────
    if feature_cache["count"] > 0:
        if not score["all_present"]:
            missing = [mc for mc, ok in score["adapters"].items() if not ok]
            warnings.append(
                f"score: missing XGB artifacts {missing}")
        if reshape["count"] == 0:
            warnings.append(
                "reshape: no rows in replay collection — run "
                "scripts.sgo.reshape_team_props_to_replay")
        elif replay["scored_count"] < reshape["count"]:
            warnings.append(
                f"replay: only {replay['scored_count']:,}/"
                f"{reshape['count']:,} rows carry "
                f"model_probability+model_version=team_xgb_v1 "
                f"({replay['scored_pct']}%)")
        if not grid["latest_run_id"]:
            warnings.append(
                f"grid: no succeeded optimizer run for "
                f"(prop_type=team, sport={sport.upper()})")

    # ── Freshness warnings (yellow, not red) ─────────────────────
    if ingest["lag_minutes"] is not None \
       and ingest["lag_minutes"] > STALE_LIVE_PROPS_MIN \
       and ingest["count"] > 0:
        warnings.append(
            f"ingest: latest team_live_props snapshot is "
            f"{ingest['lag_minutes']:.0f} min stale "
            f"(threshold {STALE_LIVE_PROPS_MIN} min)")

    if grid["age_hours"] is not None \
       and grid["age_hours"] > STALE_GRID_HOURS:
        warnings.append(
            f"grid: latest optimizer run is "
            f"{grid['age_hours']:.0f}h stale "
            f"(threshold {STALE_GRID_HOURS}h)")

    # ── Health roll-up ──────────────────────────────────────────
    has_red = (feature_cache["count"] > 0 and (
        not score["all_present"]
        or reshape["count"] == 0
        or replay["scored_count"] < reshape["count"]
        or not grid["latest_run_id"]
    ))
    has_yellow = any(
        w.startswith("ingest:") or w.startswith("grid:")
        for w in warnings
    )
    if has_red:
        health = "red"
    elif has_yellow:
        health = "yellow"
    else:
        health = "green"

    return {
        "sport":  sport,
        "league": sport.upper(),
        "health": health,
        "stages": {
            "ingest":        ingest,
            "features":      features,
            "outcomes":      outcomes,
            "feature_cache": feature_cache,
            "score":         score,
            "reshape":       reshape,
            "replay":        replay,
            "grid":          grid,
        },
        "warnings": warnings,
    }


async def _scheduler_state() -> Dict[str, Any]:
    """Surface system_state.live_sync (the APScheduler pause toggle).
    The audit reports it but never auto-mutates it — operator-only."""
    doc = await _db["system_state"].find_one(
        {"_id": "live_sync"}, projection={"_id": 0})
    if not doc:
        return {
            "paused":          False,
            "manual_override": False,
            "reason":          "",
            "set_by":          "",
            "set_at":          None,
            "warning":         None,
        }
    paused = bool(doc.get("paused"))
    manual = bool(doc.get("manual_override"))
    warning = None
    if paused:
        warning = (
            "APScheduler is paused — `team_live_sync_{mlb,nba,nfl}` "
            "jobs ARE registered but NOT running. team_live_props + "
            "team_prop_scores will go stale until resumed."
        )
        if manual:
            warning += (
                " Pause was set with manual_override=True; auto-resume "
                "is disabled.")
    return {
        "paused":          paused,
        "manual_override": manual,
        "reason":          doc.get("reason") or "",
        "set_by":          doc.get("set_by") or "",
        "set_at":          str(doc["set_at"]) if doc.get("set_at") else None,
        "warning":         warning,
    }


# ── Route. ───────────────────────────────────────────────────────
@router.get("/v3/pipeline-audit/team-ssot")
async def team_ssot_audit():
    """Single-pane health snapshot for the team-prop pipeline.

    Read-only. Cheap (8 small queries per sport). Suitable to poll
    from the Quant Terminal admin UI.
    """
    now = datetime.now(timezone.utc)
    sports = [await _audit_sport(sp, now=now) for sp in SPORTS]
    scheduler = await _scheduler_state()

    # ── Roll-up across sports ───────────────────────────────────
    healths = [s["health"] for s in sports]
    if any(h == "red" for h in healths):
        overall = "red"
    elif any(h == "yellow" for h in healths) \
         or scheduler.get("paused"):
        overall = "yellow"
    else:
        overall = "green"

    gap_count = sum(
        1 for s in sports
        for w in s["warnings"]
        if w.split(":", 1)[0] in
            ("score", "reshape", "replay", "grid")
    )
    warning_count = sum(len(s["warnings"]) for s in sports)
    if scheduler.get("paused"):
        warning_count += 1

    return {
        "generated_at": now.isoformat(),
        "now_iso":      now.isoformat(),
        "stale_thresholds": {
            "team_live_props_min":  STALE_LIVE_PROPS_MIN,
            "team_prop_scores_min": STALE_SCORES_MIN,
            "optimizer_run_hours":  STALE_GRID_HOURS,
        },
        "sports":    sports,
        "scheduler": scheduler,
        "summary": {
            "overall_health": overall,
            "gap_count":      gap_count,
            "warning_count":  warning_count,
        },
    }


__all__ = ["router", "set_team_ssot_audit_db"]
