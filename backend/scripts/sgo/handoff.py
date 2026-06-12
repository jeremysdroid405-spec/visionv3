"""
handoff.py — Rewrites /var/www/app/backend/HANDOFF.md with live data.

Call update_handoff() after any fix script completes.  Editable section:
KNOWN_ISSUES — add/remove entries manually.  Everything else is pulled
from MongoDB (grid results, collection health) and pkl files (model AUC,
tier hit-rates).

Usage:
    from scripts.sgo.handoff import update_handoff
    update_handoff(fixed="game_total UNDER tp bug", next_task="retrain h2h")

    # or standalone
    python -m scripts.sgo.handoff
"""
from __future__ import annotations
import asyncio
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient

# ─────────────────────────────────────────────────────────────────────────────
# MANUALLY EDITED: add/remove issues here only
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_ISSUES = [
    {
        "priority": 1,
        "status": "BROKEN",
        "title": "game_total ROI inflation",
        "detail": "min(implied) picks outlier low-implied alternate lines. "
                  "54% hit with +107% ROI impossible.",
        "fix": "Add implied bounds (0.35, 0.65) filter in "
               "historical_gate_replay_grid.py _load_team()",
    },
    {
        "priority": 2,
        "status": "BROKEN",
        "title": "NBA h2h",
        "detail": "Grid shows -12% to -27% ROI. May need more training data "
                  "(64K vs 172K rows).",
        "fix": "Investigate after game_total fix",
    },
    {
        "priority": 3,
        "status": "TODO",
        "title": "Run optimizer",
        "detail": "No team optimizer exists. Grid results inspected manually.",
        "fix": "Adapt historical_gate_replay_grid.py player_model_gate_configs "
               "into live router",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
ARTIFACT_ROOT = Path("/app/backend/models/team_xgb")
if not ARTIFACT_ROOT.exists():
    ARTIFACT_ROOT = Path("/var/www/app/backend/models/team_xgb")

HANDOFF_PATH = Path("/var/www/app/backend/HANDOFF.md")

GRID_WINDOWS = [
    ("2024 season",      "2024-07-01", "2024-11-01"),
    ("2025 season",      "2025-04-01", "2025-10-01"),
    ("2026 season YTD",  "2026-04-01", "2026-06-08"),
]

MARKETS = ["game_total", "spread", "h2h", "team_total"]

KEY_COLLECTIONS = [
    ("team_matchups",                "game_date"),
    ("team_historical_outcomes",     "game_date"),
    ("team_model_features",          "as_of_date"),
    ("team_model_prop_features",     "game_date"),
    ("sgo_propvision_full_pipeline_replay", "game_date"),
    ("player_model_grid_results",     "created_at"),
    ("bdl_mlb_game_boxscores",       "game_date"),
    ("odds_api_team_h2h",            "commence_time"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _col_stats(db, col: str, date_field: str) -> Tuple[int, Optional[str], Optional[str]]:
    n = await db[col].count_documents({})
    pipe = [{"$group": {"_id": None,
                        "mn": {"$min": f"${date_field}"},
                        "mx": {"$max": f"${date_field}"}}}]
    r = await db[col].aggregate(pipe).to_list(1)
    if r:
        return n, str(r[0]["mn"])[:10], str(r[0]["mx"])[:10]
    return n, None, None


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _roi_str(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB pulls
# ─────────────────────────────────────────────────────────────────────────────

async def _pull_grid_results(db) -> Dict[str, Any]:
    """Best ROI row per (window, market) from player_model_grid_results mode=team."""
    results: Dict[str, Any] = {}
    for label, start, end in GRID_WINDOWS:
        run_docs = await db["research_grid_runs"].find(
            {"mode": "team", "params.start": start, "params.end": end,
             "status": "succeeded"}
        ).to_list(100)
        run_ids = [r["run_id"] for r in run_docs]
        window_results: Dict[str, Any] = {}
        for mc in MARKETS:
            if not run_ids:
                window_results[mc] = None
                continue
            top = await db["player_model_grid_results"].find(
                {"mode": "team", "market_category": mc,
                 "run_id": {"$in": run_ids}}
            ).sort("roi", -1).limit(1).to_list(1)
            if top:
                r = top[0]
                suspect = (
                    (r.get("hit_rate") or 1.0) < 0.58
                    and (r.get("roi") or 0.0) > 0.80
                )
                window_results[mc] = {
                    "n": r.get("n_bets"),
                    "hit": r.get("hit_rate"),
                    "roi": r.get("roi"),
                    "prob_min": r.get("prob_min"),
                    "edge_min": r.get("edge_min"),
                    "suspect": suspect,
                }
            else:
                window_results[mc] = None
        results[(label, start, end)] = window_results
    return results


async def _pull_collection_health(db) -> List[Dict[str, Any]]:
    rows = []
    for col, df in KEY_COLLECTIONS:
        n, mn, mx = await _col_stats(db, col, df)
        rows.append({"collection": col, "count": n,
                     "date_field": df, "min": mn, "max": mx})
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# PKL pulls
# ─────────────────────────────────────────────────────────────────────────────

def _pull_model_stats() -> List[Dict[str, Any]]:
    rows = []
    if not ARTIFACT_ROOT.exists():
        return rows
    for sport_dir in sorted(ARTIFACT_ROOT.iterdir()):
        if not sport_dir.is_dir():
            continue
        sport = sport_dir.name
        for pkl_path in sorted(sport_dir.glob("*.pkl")):
            mc = pkl_path.stem
            try:
                with open(pkl_path, "rb") as f:
                    d = pickle.load(f)
            except Exception as e:
                rows.append({"sport": sport, "market": mc, "error": str(e)})
                continue
            m = d.get("metrics", {})
            auc = m.get("auc_test")
            sh = (m.get("roi", {}).get("by_tier") or {}).get("safe_haven", {})
            rows.append({
                "sport": sport,
                "market": mc,
                "auc": auc,
                "samples": d.get("samples"),
                "trained_at": (d.get("trained_at") or "")[:10],
                "sh_n": sh.get("n"),
                "sh_hit": sh.get("hit_rate"),
                "sh_roi": sh.get("roi"),
                "error": None,
            })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Session log preservation
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_LOG_MARKER = "## SESSION LOG"

def _read_existing_log() -> List[str]:
    """Return existing session log lines (after the marker) if HANDOFF.md exists."""
    if not HANDOFF_PATH.exists():
        return []
    text = HANDOFF_PATH.read_text(encoding="utf-8")
    idx = text.find(_SESSION_LOG_MARKER)
    if idx == -1:
        return []
    after = text[idx + len(_SESSION_LOG_MARKER):]
    return [ln for ln in after.splitlines() if ln.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Document builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_doc(
    *,
    generated_at: str,
    grid: Dict,
    collections: List[Dict],
    models: List[Dict],
    session_lines: List[str],
    new_entry: Optional[str],
) -> str:
    lines: List[str] = []
    a = lines.append

    a(f"# HANDOFF")
    a(f"Generated: {generated_at}")
    a("")

    # ── KEY RULES ──────────────────────────────────────────────────────────
    a("## KEY RULES")
    a("- `rescore=False` always")
    a("- `game_total` / `team_total`: OVER-only training; UNDER = `1 - tp` at inference")
    a("- `h2h`: HOME-only training; AWAY gets feature swap + `1 - HOME_tp` at inference")
    a("- Training filter: `game_date >= 2025-01-01` AND `implied_probability != None`")
    a("- Grid dedup key: `(event_id, side, market_category, line)`")
    a("- Replay source: `sgo_propvision_full_pipeline_replay` where `clean_odds IS NOT NULL`")
    a("")

    # ── KNOWN ISSUES ───────────────────────────────────────────────────────
    a("## KNOWN ISSUES")
    for issue in sorted(KNOWN_ISSUES, key=lambda x: x["priority"]):
        p = issue["priority"]
        status = issue["status"]
        title = issue["title"]
        detail = issue["detail"]
        fix = issue["fix"]
        a(f"### P{p} [{status}] {title}")
        a(f"**Detail:** {detail}")
        a(f"**Fix:** {fix}")
        a("")

    # ── MODEL STATUS ───────────────────────────────────────────────────────
    a("## MODEL STATUS")
    a("")
    a("| Sport | Market | AUC | Samples | Trained | SH n | SH hit% | SH ROI |")
    a("|-------|--------|-----|---------|---------|------|---------|--------|")
    for r in models:
        if r.get("error"):
            a(f"| {r['sport']} | {r['market']} | ERROR: {r['error']} | | | | | |")
            continue
        auc_s = f"{r['auc']:.4f}" if r.get("auc") is not None else "—"
        samples_s = f"{r['samples']:,}" if r.get("samples") else "—"
        a(f"| {r['sport']} | {r['market']} | {auc_s} | {samples_s} | "
          f"{r['trained_at'] or '—'} | {r['sh_n'] or '—'} | "
          f"{_pct(r['sh_hit'])} | {_roi_str(r['sh_roi'])} |")
    a("")

    # ── GRID RESULTS ───────────────────────────────────────────────────────
    a("## GRID RESULTS")
    a("")
    for (label, start, end), markets in grid.items():
        a(f"### {label} ({start} → {end})")
        a("")
        a("| Market | n | hit% | ROI | prob_min | edge_min | Flag |")
        a("|--------|---|------|-----|----------|----------|------|")
        for mc in MARKETS:
            r = markets.get(mc)
            if r is None:
                a(f"| {mc} | — | — | — | — | — | NO RUNS |")
                continue
            flag = "SUSPECT" if r["suspect"] else ""
            a(f"| {mc} | {r['n'] or '—'} | {_pct(r['hit'])} | "
              f"{_roi_str(r['roi'])} | {r['prob_min'] or '—'} | "
              f"{r['edge_min'] or '—'} | {flag} |")
        a("")

    # ── COLLECTION HEALTH ──────────────────────────────────────────────────
    a("## COLLECTION HEALTH")
    a("")
    a("| Collection | Count | Min Date | Max Date |")
    a("|------------|-------|----------|----------|")
    for r in collections:
        count_s = f"{r['count']:,}"
        a(f"| {r['collection']} | {count_s} | {r['min'] or '—'} | {r['max'] or '—'} |")
    a("")

    # ── SESSION LOG ────────────────────────────────────────────────────────
    a(_SESSION_LOG_MARKER)
    for ln in session_lines:
        a(ln)
    if new_entry:
        a(new_entry)

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Async core
# ─────────────────────────────────────────────────────────────────────────────

async def _async_update(fixed: Optional[str], next_task: Optional[str]) -> None:
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        grid, collections = await asyncio.gather(
            _pull_grid_results(db),
            _pull_collection_health(db),
        )
    finally:
        client.close()

    models = _pull_model_stats()
    session_lines = _read_existing_log()

    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()

    new_entry: Optional[str] = None
    if fixed is not None or next_task is not None:
        parts = [f"- {now.strftime('%Y-%m-%d %H:%M UTC')}"]
        if fixed:
            parts.append(f"fixed={fixed!r}")
        if next_task:
            parts.append(f"next={next_task!r}")
        new_entry = "  ".join(parts)

    doc = _build_doc(
        generated_at=generated_at,
        grid=grid,
        collections=collections,
        models=models,
        session_lines=session_lines,
        new_entry=new_entry,
    )

    HANDOFF_PATH.parent.mkdir(parents=True, exist_ok=True)
    HANDOFF_PATH.write_text(doc, encoding="utf-8")
    print(f"[handoff] wrote {HANDOFF_PATH}  ({len(doc):,} bytes)")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def update_handoff(
    fixed: Optional[str] = None,
    next_task: Optional[str] = None,
) -> None:
    """Rewrite HANDOFF.md with live MongoDB + pkl data.

    Args:
        fixed:     Short description of what was just fixed (goes into session log).
        next_task: What the next agent should tackle (goes into session log).
    """
    asyncio.run(_async_update(fixed=fixed, next_task=next_task))


if __name__ == "__main__":
    update_handoff()
