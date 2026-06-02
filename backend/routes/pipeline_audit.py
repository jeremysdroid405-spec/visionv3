"""GET /api/v3/pipeline-audit — health snapshot of all four pipeline
quadrants (live × backtest × player × team).

Per /app/memory/ARCHITECTURE.md (locked 2026-06-02): PropVision has
EXACTLY two pipelines (player + team) × two modes (live + backtest).
This endpoint reads `PIPELINE_REGISTRY` from
`services/replay/contract.py` and surfaces live row counts +
freshness lag for each quadrant.

Response shape:
    {
      "generated_at": "...",
      "quadrants": {
        "live_player":     {"sport": {...}, "totals": {...}},
        "live_team":       {"sport": {...}, "totals": {...}},
        "backtest_player": {"sport": {...}, "totals": {...}},
        "backtest_team":   {"sport": {...}, "totals": {...}},
      },
      "pipeline_registry": {...},  # snapshot of the locked contract
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from services.replay.contract import PIPELINE_REGISTRY, PIPELINE_MODES

router = APIRouter(tags=["pipeline-audit"])

_db = None

_SUPPORTED_SPORTS = ("mlb", "nba", "nfl")


def set_pipeline_audit_db(db) -> None:
    global _db
    _db = db


async def _latest_iso(db, coll: str, flt: Dict[str, Any]) -> Optional[str]:
    """Return the most recent `snapshot_iso` (or `ingested_at`) in the
    collection matching `flt`, as an ISO string. None when empty."""
    for ts_field in ("snapshot_iso", "scored_at", "ingested_at",
                      "passthrough_at", "commence_time"):
        doc = await db[coll].find_one(
            {**flt, ts_field: {"$ne": None}},
            sort=[(ts_field, -1)],
            projection={ts_field: 1, "_id": 0},
        )
        if doc and doc.get(ts_field):
            return str(doc[ts_field])
    return None


def _lag_minutes(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        # Tolerate both "...+00:00" and "Z" suffixes.
        s = iso.replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return round(
        (datetime.now(timezone.utc) - ts).total_seconds() / 60.0, 1)


async def _quadrant_live_player() -> Dict[str, Any]:
    """LIVE Player: live row count + scored row count per sport +
    freshness lag (minutes since most recent snapshot_iso)."""
    out: Dict[str, Any] = {"by_sport": {}, "totals": {"live": 0, "scored": 0}}
    for sp in _SUPPORTED_SPORTS:
        live_coll = f"{sp}_live_props"
        score_coll = f"{sp}_prop_scores"
        try:
            n_live = await _db[live_coll].count_documents({})
        except Exception:
            n_live = 0
        try:
            n_scored = await _db[score_coll].count_documents({})
        except Exception:
            n_scored = 0
        latest_live = await _latest_iso(_db, live_coll, {}) if n_live else None
        latest_scored = await _latest_iso(_db, score_coll, {}) if n_scored else None
        out["by_sport"][sp] = {
            "live_props":         n_live,
            "scored_props":       n_scored,
            "latest_live_iso":    latest_live,
            "latest_scored_iso":  latest_scored,
            "lag_minutes_live":   _lag_minutes(latest_live),
            "lag_minutes_scored": _lag_minutes(latest_scored),
        }
        out["totals"]["live"] += n_live
        out["totals"]["scored"] += n_scored
    return out


async def _quadrant_live_team() -> Dict[str, Any]:
    """LIVE Team: same shape as live_player, but every sport reads
    from `team_live_props` / `team_prop_scores` filtered by `sport`."""
    out: Dict[str, Any] = {"by_sport": {}, "totals": {"live": 0, "scored": 0}}
    for sp in _SUPPORTED_SPORTS:
        n_live = await _db["team_live_props"].count_documents({"sport": sp})
        n_scored = await _db["team_prop_scores"].count_documents({"sport": sp})
        latest_live = await _latest_iso(
            _db, "team_live_props", {"sport": sp}) if n_live else None
        latest_scored = await _latest_iso(
            _db, "team_prop_scores", {"sport": sp}) if n_scored else None
        out["by_sport"][sp] = {
            "live_props":         n_live,
            "scored_props":       n_scored,
            "latest_live_iso":    latest_live,
            "latest_scored_iso":  latest_scored,
            "lag_minutes_live":   _lag_minutes(latest_live),
            "lag_minutes_scored": _lag_minutes(latest_scored),
        }
        out["totals"]["live"] += n_live
        out["totals"]["scored"] += n_scored
    return out


async def _quadrant_backtest_player() -> Dict[str, Any]:
    """BACKTEST Player: SGO historical row count per sport + replay
    output (NBA replay model outputs) + optimizer rows (if mirror
    has run)."""
    out: Dict[str, Any] = {
        "by_sport": {},
        "totals": {"historical": 0, "replay_scored": 0, "optimizer_rows": 0},
    }
    # Optimizer dataset is the same collection both pipelines write
    # to; for player rows we filter `prop_type=player`.
    try:
        opt_player = await _db["optimizer_input"].count_documents(
            {"prop_type": "player"})
    except Exception:
        opt_player = 0
    for sp in _SUPPORTED_SPORTS:
        hist_coll = f"{sp}_historical_props"
        try:
            n_hist = await _db[hist_coll].count_documents({})
        except Exception:
            n_hist = 0
        # NBA has a dedicated replay model output collection.
        n_replay = 0
        if sp == "nba":
            try:
                n_replay = await _db["nba_replay_model_outputs"].count_documents({})
            except Exception:
                n_replay = 0
        out["by_sport"][sp] = {
            "historical_props": n_hist,
            "replay_scored":    n_replay,
        }
        out["totals"]["historical"] += n_hist
        out["totals"]["replay_scored"] += n_replay
    out["totals"]["optimizer_rows"] = opt_player
    return out


async def _quadrant_backtest_team() -> Dict[str, Any]:
    """BACKTEST Team: historical team props per sport + scored team
    historical outcomes + optimizer rows (prop_type=team)."""
    out: Dict[str, Any] = {
        "by_sport": {},
        "totals": {"historical": 0, "scored_outcomes": 0, "optimizer_rows": 0},
    }
    try:
        opt_team = await _db["optimizer_input"].count_documents(
            {"prop_type": "team"})
    except Exception:
        opt_team = 0
    for sp in _SUPPORTED_SPORTS:
        try:
            n_hist = await _db["team_historical_props"].count_documents(
                {"sport": sp})
        except Exception:
            n_hist = 0
        try:
            n_outcomes = await _db["team_historical_outcomes"].count_documents(
                {"sport": sp})
        except Exception:
            n_outcomes = 0
        out["by_sport"][sp] = {
            "historical_props":  n_hist,
            "scored_outcomes":   n_outcomes,
        }
        out["totals"]["historical"] += n_hist
        out["totals"]["scored_outcomes"] += n_outcomes
    out["totals"]["optimizer_rows"] = opt_team
    return out


def _registry_snapshot() -> Dict[str, Any]:
    """Plain-dict snapshot of `PIPELINE_REGISTRY` for the response —
    `MappingProxyType` doesn't JSON-serialise."""
    def _to_dict(node):
        if hasattr(node, "items"):
            return {k: _to_dict(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [_to_dict(x) for x in node]
        return node
    return _to_dict(PIPELINE_REGISTRY)


@router.get("/v3/pipeline-audit")
async def get_pipeline_audit():
    """Single endpoint exposing health of all 4 pipeline quadrants.
    Reads-only. Suitable to ping from a monitoring dashboard."""
    quadrants = {
        "live_player":     await _quadrant_live_player(),
        "live_team":       await _quadrant_live_team(),
        "backtest_player": await _quadrant_backtest_player(),
        "backtest_team":   await _quadrant_backtest_team(),
    }

    # ── Health gradient — light heuristic so a dashboard can colour
    # each quadrant. "green" = nonzero rows + lag < 30 min for live,
    # nonzero rows for backtest. "amber" = some lag / partial data.
    # "red" = zero rows.
    health: Dict[str, str] = {}
    for q in ("live_player", "live_team"):
        totals = quadrants[q]["totals"]
        if totals["scored"] == 0:
            health[q] = "red"
        else:
            lags = [
                v["lag_minutes_scored"]
                for v in quadrants[q]["by_sport"].values()
                if v.get("lag_minutes_scored") is not None
            ]
            min_lag = min(lags) if lags else None
            if min_lag is None or min_lag > 60:
                health[q] = "amber"
            else:
                health[q] = "green"
    for q in ("backtest_player", "backtest_team"):
        totals = quadrants[q]["totals"]
        if totals["historical"] == 0:
            health[q] = "red"
        elif totals.get("optimizer_rows", 0) == 0:
            health[q] = "amber"
        else:
            health[q] = "green"

    return {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "quadrants":         quadrants,
        "health":            health,
        "pipeline_modes":    list(PIPELINE_MODES),
        "pipeline_registry": _registry_snapshot(),
    }


__all__ = ["router", "set_pipeline_audit_db"]
