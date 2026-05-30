"""
Read-only database inventory audit.

Produces:
  /app/memory/DATABASE_INVENTORY_REPORT.md
  /app/memory/DATABASE_INVENTORY_SUMMARY.json

Usage:
  python -m scripts.audit_database_inventory

Rules:
  - Read-only. No writes, no SGO calls, no API calls.
  - Aggregations carry maxTimeMS=120000 to prevent runaway scans.
  - Missing collections are reported but never crash the audit.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


load_dotenv("/app/backend/.env")

REPORT_MD   = "/app/memory/DATABASE_INVENTORY_REPORT.md"
REPORT_JSON = "/app/memory/DATABASE_INVENTORY_SUMMARY.json"

# Aggregation guardrails
MAX_TIME_MS = 120_000

# Collections to audit (player + team buckets)
PLAYER_COLLECTIONS = [
    "sgo_pp_research_core",
    "sgo_pp_research_core_enriched",
    "sgo_pp_research_outcomes",
    "sgo_pp_research_model_features",
    "sgo_pp_research_model_predictions",
    "sgo_props_raw",
    "sgo_replay_alt_odds_raw",
    "sgo_book_consensus",
    "sgo_odds_outcomes",
    "sgo_player_stats",
    "sgo_team_stats",
    "mlb_prop_scores",
    "nba_prop_scores",
    "nfl_player_historical_props",
]

TEAM_COLLECTIONS = [
    "team_matchups",
    "team_historical_props",
    "team_live_props",
    "nfl_matchups",
    "nfl_historical_props",
    "team_prop_outcomes",
]

AUDIT_COLLECTIONS = [
    "historical_acquire_runs",
    "team_odds_ingest_runs",
]

ALL_COLLECTIONS = (PLAYER_COLLECTIONS
                    + TEAM_COLLECTIONS
                    + AUDIT_COLLECTIONS)


# ── Safe helpers ───────────────────────────────────────────────────
async def _safe_count(db, coll: str) -> int:
    try:
        return int(await db[coll].estimated_document_count())
    except Exception:
        return 0


async def _safe_stats(db, coll: str) -> Dict[str, Any]:
    """collStats: storageSize, totalIndexSize, n_indexes."""
    try:
        s = await db.command({"collStats": coll, "scale": 1})
        return {
            "storage_bytes":     int(s.get("storageSize", 0)),
            "index_total_bytes": int(s.get("totalIndexSize", 0)),
            "avg_obj_size":      int(s.get("avgObjSize", 0) or 0),
            "n_indexes":         int(s.get("nindexes", 0)),
        }
    except Exception:
        return {
            "storage_bytes": 0, "index_total_bytes": 0,
            "avg_obj_size": 0, "n_indexes": 0,
        }


async def _safe_distinct(db, coll: str, field: str,
                            limit: int | None = None) -> List[Any]:
    try:
        cursor = db[coll].find(
            {field: {"$exists": True, "$ne": None}},
            projection={"_id": 0, field: 1},
        ).limit(limit or 0)
        seen: set = set()
        async for d in cursor:
            v = d.get(field)
            if v is not None and not isinstance(v, (dict, list)):
                seen.add(v)
        return sorted(seen, key=lambda x: str(x))
    except Exception:
        return []


async def _safe_distinct_count(db, coll: str, field: str) -> int:
    try:
        vals = await db[coll].distinct(field)
        return len([v for v in vals if v is not None])
    except Exception:
        return 0


async def _safe_indexes(db, coll: str) -> List[str]:
    try:
        names: List[str] = []
        async for idx in db[coll].list_indexes():
            names.append(idx.get("name", "?"))
        return sorted(names)
    except Exception:
        return []


async def _safe_min_max(db, coll: str,
                          field: str) -> tuple[Any, Any]:
    """Returns (min, max) on `field` via two find_ones — cheap."""
    try:
        mn_doc = await db[coll].find_one(
            {field: {"$exists": True, "$ne": None}},
            sort=[(field, 1)], projection={"_id": 0, field: 1})
        mx_doc = await db[coll].find_one(
            {field: {"$exists": True, "$ne": None}},
            sort=[(field, -1)], projection={"_id": 0, field: 1})
        return (mn_doc.get(field) if mn_doc else None,
                mx_doc.get(field) if mx_doc else None)
    except Exception:
        return (None, None)


async def _agg_group(
    db, coll: str, group_key: Any,
    *, match: Optional[Dict[str, Any]] = None,
    sort_desc_n: bool = True, limit: int = 100,
) -> List[Dict[str, Any]]:
    pipe: List[Dict[str, Any]] = []
    if match:
        pipe.append({"$match": match})
    pipe.append({"$group": {"_id": group_key, "n": {"$sum": 1}}})
    if sort_desc_n:
        pipe.append({"$sort": {"n": -1}})
    pipe.append({"$limit": limit})
    out: List[Dict[str, Any]] = []
    try:
        async for d in db[coll].aggregate(
            pipe, maxTimeMS=MAX_TIME_MS):
            out.append(d)
    except Exception:
        pass
    return out


async def _sample_keys(db, coll: str) -> List[str]:
    try:
        d = await db[coll].find_one({}, projection={"_id": 0})
        if not d:
            return []
        return sorted(list(d.keys()))
    except Exception:
        return []


# ── Field discovery helper ─────────────────────────────────────────
async def _pick_date_field(db, coll: str) -> Optional[str]:
    candidates = ["game_date", "gameDate", "date",
                    "game_iso", "commence_time", "started_at",
                    "ingested_at"]
    for f in candidates:
        sample = await db[coll].find_one({f: {"$exists": True}})
        if sample is not None:
            return f
    return None


async def _pick_sport_field(db, coll: str) -> Optional[str]:
    for f in ("sport", "sportID", "sport_id", "league"):
        sample = await db[coll].find_one({f: {"$exists": True}})
        if sample is not None:
            return f
    return None


# ── Per-collection audit ───────────────────────────────────────────
async def audit_collection(db, coll: str) -> Dict[str, Any]:
    """Read-only audit of a single collection.

    Always returns a dict with `name` + `present` keys, even when the
    collection is missing/inaccessible.
    """
    try:
        names = await db.list_collection_names()
    except Exception:
        names = []
    if coll not in names:
        return {"name": coll, "present": False,
                 "n_docs": 0, "sample_keys": []}

    n_docs    = await _safe_count(db, coll)
    stats     = await _safe_stats(db, coll)
    sample    = await _sample_keys(db, coll)
    idx_names = await _safe_indexes(db, coll)

    date_field  = await _pick_date_field(db, coll)
    sport_field = await _pick_sport_field(db, coll)

    earliest = latest = None
    if date_field:
        earliest, latest = await _safe_min_max(db, coll, date_field)

    sports: List[str] = []
    if sport_field:
        try:
            vals = await db[coll].distinct(sport_field)
            sports = sorted([str(v) for v in vals if v is not None])
        except Exception:
            sports = []

    # Per-sport row counts
    rows_per_sport: Dict[str, int] = {}
    if sport_field:
        try:
            async for d in db[coll].aggregate([
                {"$group": {"_id": f"${sport_field}",
                              "n": {"$sum": 1}}},
            ], maxTimeMS=MAX_TIME_MS):
                key = str(d.get("_id") or "<null>")
                rows_per_sport[key] = int(d.get("n", 0))
        except Exception:
            pass

    return {
        "name":         coll,
        "present":      True,
        "n_docs":       n_docs,
        "storage_bytes": stats["storage_bytes"],
        "index_bytes":   stats["index_total_bytes"],
        "n_indexes":     stats["n_indexes"],
        "avg_obj_size":  stats["avg_obj_size"],
        "indexes":       idx_names,
        "sample_keys":   sample,
        "date_field":    date_field,
        "sport_field":   sport_field,
        "earliest":      earliest,
        "latest":        latest,
        "sports":        sports,
        "rows_per_sport": rows_per_sport,
    }


# ── Sport coverage block (cross-collection) ────────────────────────
async def sport_coverage_block(
    db, sport: str, audit: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "sport": sport,
        "event_count": {},
        "player_prop_count": {},
        "team_prop_count": {},
        "matchup_count": {},
        "graded_count": {},
        "ungraded_count": {},
        "seasons_present": [],
        "date_range": {},
    }

    # Event counts via distinct event_id per collection
    for coll in ["team_historical_props", "nfl_historical_props",
                  "nfl_player_historical_props", "team_live_props",
                  "team_matchups", "nfl_matchups"]:
        if not audit.get(coll, {}).get("present"):
            continue
        # filter by sport where possible
        sport_field = audit[coll].get("sport_field")
        if sport_field == "sport":
            try:
                vals = await db[coll].distinct(
                    "event_id", {"sport": sport})
                out["event_count"][coll] = len(vals)
            except Exception:
                out["event_count"][coll] = -1
        elif sport == "nfl" and coll.startswith("nfl_"):
            try:
                vals = await db[coll].distinct("event_id")
                out["event_count"][coll] = len(vals)
            except Exception:
                out["event_count"][coll] = -1

    # Team-prop counts
    for coll in ["team_historical_props", "team_live_props",
                  "nfl_historical_props"]:
        if not audit.get(coll, {}).get("present"):
            continue
        sport_field = audit[coll].get("sport_field")
        flt = {"sport": sport} if sport_field == "sport" else {}
        if coll.startswith("nfl_") and sport != "nfl":
            continue
        try:
            out["team_prop_count"][coll] = await db[
                coll].count_documents(flt)
        except Exception:
            out["team_prop_count"][coll] = -1

    # Player-prop counts
    for coll in ["nfl_player_historical_props",
                  "sgo_pp_research_core_enriched",
                  "sgo_pp_research_core",
                  "sgo_props_raw"]:
        if not audit.get(coll, {}).get("present"):
            continue
        sport_field = audit[coll].get("sport_field")
        flt: Dict[str, Any] = {}
        if sport_field == "sport":
            flt["sport"] = sport
        elif sport_field == "league":
            flt["league"] = sport.upper()
        if coll.startswith("nfl_") and sport != "nfl":
            continue
        try:
            out["player_prop_count"][coll] = await db[
                coll].count_documents(flt)
        except Exception:
            out["player_prop_count"][coll] = -1

    # Matchup counts
    for coll in ["team_matchups", "nfl_matchups"]:
        if not audit.get(coll, {}).get("present"):
            continue
        sport_field = audit[coll].get("sport_field")
        flt = {"sport": sport} if sport_field == "sport" else {}
        if coll.startswith("nfl_") and sport != "nfl":
            continue
        try:
            out["matchup_count"][coll] = await db[
                coll].count_documents(flt)
        except Exception:
            out["matchup_count"][coll] = -1

    # Graded / ungraded outcomes (best-effort across known shapes)
    for coll in ["team_prop_outcomes", "sgo_pp_research_outcomes",
                  "sgo_odds_outcomes"]:
        if not audit.get(coll, {}).get("present"):
            continue
        sport_field = audit[coll].get("sport_field")
        base_flt: Dict[str, Any] = {}
        if sport_field == "sport":
            base_flt["sport"] = sport
        try:
            graded = await db[coll].count_documents({
                **base_flt,
                "$or": [
                    {"outcome": {"$in": ["won", "lost", "push",
                                            "WIN", "LOSS", "PUSH"]}},
                    {"result":  {"$in": ["won", "lost", "push",
                                            "WIN", "LOSS", "PUSH"]}},
                    {"settled": True},
                ],
            })
            out["graded_count"][coll]   = graded
            total = await db[coll].count_documents(base_flt)
            out["ungraded_count"][coll] = max(total - graded, 0)
        except Exception:
            out["graded_count"][coll]   = -1
            out["ungraded_count"][coll] = -1

    # Seasons-present (rough): pick the largest team collection for
    # this sport and extract year-month from game_date
    main_coll = ("nfl_historical_props" if sport == "nfl"
                  else "team_historical_props")
    if audit.get(main_coll, {}).get("present"):
        try:
            flt = {} if main_coll.startswith("nfl_") else {"sport": sport}
            seasons = set()
            async for d in db[main_coll].aggregate([
                {"$match": flt},
                {"$group": {"_id": {"$substr":
                    ["$game_date", 0, 4]}}},
            ], maxTimeMS=MAX_TIME_MS):
                if d.get("_id"):
                    seasons.add(d["_id"])
            out["seasons_present"] = sorted(seasons)
            mn = await db[main_coll].find_one(
                flt, sort=[("game_date", 1)],
                projection={"_id": 0, "game_date": 1})
            mx = await db[main_coll].find_one(
                flt, sort=[("game_date", -1)],
                projection={"_id": 0, "game_date": 1})
            out["date_range"] = {
                "earliest": (mn or {}).get("game_date"),
                "latest":   (mx or {}).get("game_date"),
            }
        except Exception:
            pass

    return out


# ── Markets, books, players, teams per relevant collection ─────────
async def deep_inventory(db, coll: str) -> Dict[str, Any]:
    """Markets / books / players / teams summary for a single
    high-value historical collection. Tolerant of missing fields.
    """
    out: Dict[str, Any] = {"name": coll}
    try:
        names = await db.list_collection_names()
    except Exception:
        names = []
    if coll not in names:
        out["present"] = False
        return out
    out["present"] = True

    # markets/books/players/teams
    for fld in ("market", "book", "player_id", "team_id"):
        try:
            vals = await db[coll].distinct(fld)
            out[f"n_distinct_{fld}"] = len(
                [v for v in vals if v is not None])
        except Exception:
            out[f"n_distinct_{fld}"] = -1
    # top 10 markets
    top = await _agg_group(db, coll, "$market", limit=10)
    out["top_markets"] = [
        {"market": str(d["_id"]), "n": int(d["n"])} for d in top
    ]
    # top 10 books
    top_b = await _agg_group(db, coll, "$book", limit=10)
    out["top_books"] = [
        {"book": str(d["_id"]), "n": int(d["n"])} for d in top_b
    ]
    # books per sport per collection
    try:
        per_sport_books: Dict[str, int] = {}
        async for d in db[coll].aggregate([
            {"$group": {
                "_id": {"sport": "$sport", "book": "$book"},
            }},
        ], maxTimeMS=MAX_TIME_MS):
            sp = str((d["_id"] or {}).get("sport") or "<null>")
            per_sport_books[sp] = per_sport_books.get(sp, 0) + 1
        out["books_per_sport"] = per_sport_books
    except Exception:
        out["books_per_sport"] = {}

    # data-quality flags
    quality: Dict[str, int] = {}
    for fld in ("event_id", "game_date", "player_id",
                  "team_id", "book", "odds", "line", "market"):
        try:
            n_null = await db[coll].count_documents(
                {"$or": [{fld: None},
                          {fld: {"$exists": False}}]})
            if n_null > 0:
                quality[f"null_{fld}"] = n_null
        except Exception:
            pass
    out["null_field_counts"] = quality
    return out


# ── Acquisition runs summary ───────────────────────────────────────
async def runs_summary(db, coll: str) -> List[Dict[str, Any]]:
    try:
        names = await db.list_collection_names()
    except Exception:
        names = []
    if coll not in names:
        return []
    out: List[Dict[str, Any]] = []
    proj = {"_id": 0, "per_date_counts": 0,
            "market_keys_seen": 0, "sample_endpoints": 0,
            "stat_families": 0}
    try:
        cursor = db[coll].find({}, projection=proj
                                ).sort("started_at", -1).limit(50)
        async for d in cursor:
            out.append(d)
    except Exception:
        pass
    return out


# ── Markdown writer ────────────────────────────────────────────────
def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def render_markdown(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    g = summary["grand"]
    lines.append("# Database Inventory Report")
    lines.append(f"_Generated: {summary['generated_at']}_\n")

    # ── Executive Summary ──
    lines.append("## Executive Summary\n")
    lines.append(f"- **Collections audited:** {g['n_collections']}")
    lines.append(f"- **Collections present:** {g['n_present']}")
    lines.append(f"- **Collections missing:** {g['n_missing']}")
    lines.append(f"- **Total documents:** {g['total_docs']:,}")
    lines.append(f"- **Total storage:** {_fmt_bytes(g['total_storage_bytes'])}")
    lines.append(f"- **Total index storage:** {_fmt_bytes(g['total_index_bytes'])}")
    lines.append(f"- **Sports observed:** {', '.join(g['sports_observed']) or '<none>'}")
    seasons_observed = sorted(set(
        s for sport in summary["sport_coverage"].values()
        for s in (sport.get("seasons_present") or [])
    ))
    lines.append(f"- **Seasons observed:** {', '.join(seasons_observed) or '<none>'}\n")

    lines.append("### Dataset Status\n")
    lines.append("| sport | matchups | team props | player props | graded outcomes | model-ready? |")
    lines.append("|---|---|---|---|---|---|")
    for sport in ("mlb", "nba", "nfl"):
        s = summary["sport_coverage"].get(sport, {})
        mc = sum(s.get("matchup_count", {}).values())
        tc = sum(s.get("team_prop_count", {}).values())
        pc = sum(s.get("player_prop_count", {}).values())
        gc = sum(s.get("graded_count", {}).values())
        ready = "✅" if (mc > 0 and tc > 0 and gc > 0) else \
                  "⚠️" if (mc > 0 and tc > 0) else "❌"
        lines.append(f"| {sport.upper()} | {mc:,} | {tc:,} | {pc:,} | "
                       f"{gc:,} | {ready} |")
    lines.append("")
    lines.append("**Status legend:** ✅ matchups + props + outcomes  •  "
                  "⚠️ matchups + props, NO outcomes  •  ❌ missing core data\n")
    lines.append("**Per user instruction:** all data above is "
                  "acquisition-only — modeling, grading, and UI work "
                  "are frozen.\n")

    # ── 1. Collection Inventory ──
    lines.append("## 1. Collection Inventory\n")
    lines.append("| collection | docs | storage | indexes | sports | earliest | latest |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in summary["collections"]:
        if not c.get("present"):
            lines.append(f"| {c['name']} | _missing_ | — | — | — | — | — |")
            continue
        lines.append(
            f"| {c['name']} "
            f"| {c['n_docs']:,} "
            f"| {_fmt_bytes(c['storage_bytes'])} "
            f"| {c['n_indexes']} "
            f"| {', '.join(c.get('sports') or []) or '—'} "
            f"| {c.get('earliest') or '—'} "
            f"| {c.get('latest') or '—'} |"
        )
    lines.append("")
    lines.append("### Index details\n")
    for c in summary["collections"]:
        if not c.get("present"):
            continue
        if not c.get("indexes"):
            continue
        lines.append(f"- **{c['name']}** "
                       f"({c['n_indexes']} indexes, "
                       f"{_fmt_bytes(c['index_bytes'])} idx storage):  "
                       f"{', '.join(c['indexes'])}")
    lines.append("")

    # ── 2. Sport Coverage ──
    lines.append("## 2. Sport Coverage\n")
    for sport in ("mlb", "nba", "nfl"):
        s = summary["sport_coverage"].get(sport, {})
        lines.append(f"### {sport.upper()}\n")
        lines.append(f"- Seasons present: "
                       f"{', '.join(s.get('seasons_present') or []) or '<none>'}")
        dr = s.get("date_range") or {}
        lines.append(f"- Date range: {dr.get('earliest') or '—'} → "
                       f"{dr.get('latest') or '—'}")
        if s.get("event_count"):
            lines.append(f"- Event counts by collection:")
            for k, v in (s.get("event_count") or {}).items():
                lines.append(f"  - `{k}`: {v:,}")
        if s.get("matchup_count"):
            lines.append(f"- Matchup counts:")
            for k, v in (s.get("matchup_count") or {}).items():
                lines.append(f"  - `{k}`: {v:,}")
        if s.get("team_prop_count"):
            lines.append(f"- Team-prop counts:")
            for k, v in (s.get("team_prop_count") or {}).items():
                lines.append(f"  - `{k}`: {v:,}")
        if s.get("player_prop_count"):
            lines.append(f"- Player-prop counts:")
            for k, v in (s.get("player_prop_count") or {}).items():
                lines.append(f"  - `{k}`: {v:,}")
        if s.get("graded_count") or s.get("ungraded_count"):
            lines.append(f"- Outcome coverage:")
            for k, gv in (s.get("graded_count") or {}).items():
                uv = (s.get("ungraded_count") or {}).get(k, 0)
                lines.append(f"  - `{k}`: graded={gv:,} ungraded={uv:,}")
        lines.append("")

    # ── 3. Market/Prop Inventory ──
    lines.append("## 3. Market / Prop Inventory\n")
    for ent in summary.get("deep_inventory", []):
        if not ent.get("present"):
            lines.append(f"### {ent['name']}  _(missing)_\n")
            continue
        lines.append(f"### {ent['name']}\n")
        lines.append(f"- Distinct markets : {ent.get('n_distinct_market', 0):,}")
        lines.append(f"- Distinct books   : {ent.get('n_distinct_book', 0):,}")
        lines.append(f"- Distinct players : {ent.get('n_distinct_player_id', 0):,}")
        lines.append(f"- Distinct teams   : {ent.get('n_distinct_team_id', 0):,}")
        top = ent.get("top_markets") or []
        if top:
            lines.append("- Top markets:")
            for t in top[:10]:
                lines.append(f"  - `{t['market']}`: {t['n']:,}")
        topb = ent.get("top_books") or []
        if topb:
            lines.append("- Top books:")
            for t in topb[:10]:
                lines.append(f"  - `{t['book']}`: {t['n']:,}")
        lines.append("")

    # ── 4. Book Coverage ──
    lines.append("## 4. Book Coverage\n")
    for ent in summary.get("deep_inventory", []):
        if not ent.get("present"):
            continue
        bps = ent.get("books_per_sport") or {}
        if not bps:
            continue
        lines.append(f"- **{ent['name']}** — distinct books per sport: "
                       f"{', '.join(f'{k}={v}' for k,v in bps.items())}")
    lines.append("")

    # ── 5-7 — short single-block (cross-coll) ──
    lines.append("## 5-7. Players, Teams, Outcomes Summary\n")
    lines.append("Player/team distinct counts and outcome coverage are "
                  "summarised under §2 and §3. Outcome grading data was "
                  "specifically queried across `team_prop_outcomes`, "
                  "`sgo_pp_research_outcomes`, `sgo_odds_outcomes`.\n")

    # ── 8. Data Quality Warnings ──
    lines.append("## 8. Data Quality Warnings\n")
    any_dq = False
    for ent in summary.get("deep_inventory", []):
        if not ent.get("present"):
            continue
        flags = ent.get("null_field_counts") or {}
        if flags:
            any_dq = True
            lines.append(f"- **{ent['name']}**:")
            for k, v in sorted(flags.items(),
                                  key=lambda kv: -kv[1])[:10]:
                lines.append(f"  - {k}: {v:,}")
    if not any_dq:
        lines.append("_No null-field flags detected on the inventoried "
                       "high-value collections._\n")
    lines.append("")

    # ── 9. Acquisition Runs ──
    lines.append("## 9. Acquisition Runs\n")
    for run_coll in ("historical_acquire_runs", "team_odds_ingest_runs"):
        runs = summary.get("runs", {}).get(run_coll, [])
        lines.append(f"### {run_coll}  ({len(runs)} most recent)\n")
        if not runs:
            lines.append("_no runs_\n")
            continue
        lines.append("| run_id | sport | window | status | rows | "
                       "duration | started |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in runs[:20]:
            window = (
                f"{r.get('start_date') or r.get('snapshot_iso','')[:10]} → "
                f"{r.get('end_date') or r.get('snapshot_iso','')[:10]}"
            )
            rows = (r.get("n_props_written")
                      or r.get("n_writes")
                      or r.get("n_upserted") or 0)
            dur = r.get("duration_ms")
            dur_s = f"{int(dur)/1000:.1f}s" if dur else "—"
            started = str(r.get("started_at") or "")[:19]
            lines.append(
                f"| {str(r.get('run_id') or '')[:8]} "
                f"| {r.get('sport') or '—'} | {window} "
                f"| {r.get('status') or '—'} | {rows:,} "
                f"| {dur_s} | {started} |"
            )
        lines.append("")

    lines.append("---\n")
    lines.append("_Read-only audit; no Mongo writes performed._\n")
    return "\n".join(lines)


# ── Main ──
async def main() -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("ERROR: MONGO_URL or DB_NAME not set.", file=sys.stderr)
        return 2
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        summary: Dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mongo_db":     db_name,
            "collections":      [],
            "sport_coverage":   {},
            "deep_inventory":   [],
            "runs":             {},
            "grand":            {},
        }
        for coll in ALL_COLLECTIONS:
            audit = await audit_collection(db, coll)
            summary["collections"].append(audit)

        present_by_name: Dict[str, Dict[str, Any]] = {
            c["name"]: c for c in summary["collections"]
        }

        # sport coverage
        for sport in ("mlb", "nba", "nfl"):
            summary["sport_coverage"][sport] = (
                await sport_coverage_block(db, sport, present_by_name))

        # deep inventory only for the high-value historical sets
        for coll in [
            "team_historical_props",
            "nfl_historical_props",
            "nfl_player_historical_props",
            "team_live_props",
            "team_matchups",
            "nfl_matchups",
            "sgo_pp_research_core",
            "sgo_pp_research_core_enriched",
            "sgo_pp_research_outcomes",
            "mlb_prop_scores",
            "nba_prop_scores",
        ]:
            summary["deep_inventory"].append(
                await deep_inventory(db, coll))

        # runs summary
        for run_coll in ("historical_acquire_runs",
                          "team_odds_ingest_runs"):
            summary["runs"][run_coll] = await runs_summary(
                db, run_coll)

        # grand totals
        n_present = sum(1 for c in summary["collections"]
                         if c.get("present"))
        n_missing = sum(1 for c in summary["collections"]
                         if not c.get("present"))
        total_docs = sum(c.get("n_docs", 0)
                          for c in summary["collections"])
        total_storage = sum(c.get("storage_bytes", 0)
                              for c in summary["collections"])
        total_idx = sum(c.get("index_bytes", 0)
                          for c in summary["collections"])
        sports_observed = sorted({
            s for c in summary["collections"]
            for s in (c.get("sports") or [])
            if isinstance(s, str) and s.lower() in ("mlb", "nba", "nfl")
        })
        summary["grand"] = {
            "n_collections":        len(summary["collections"]),
            "n_present":            n_present,
            "n_missing":            n_missing,
            "total_docs":           int(total_docs),
            "total_storage_bytes":  int(total_storage),
            "total_index_bytes":    int(total_idx),
            "sports_observed":      sports_observed,
            "missing_collections":  [
                c["name"] for c in summary["collections"]
                if not c.get("present")
            ],
        }

        # write outputs
        with open(REPORT_JSON, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        with open(REPORT_MD, "w") as f:
            f.write(render_markdown(summary))

        # one-line operator summary
        print("─── DATABASE INVENTORY AUDIT (read-only) ───")
        print(f"  collections audited : {summary['grand']['n_collections']}")
        print(f"  collections present : {summary['grand']['n_present']}")
        print(f"  collections missing : {summary['grand']['n_missing']}")
        print(f"  total documents     : {summary['grand']['total_docs']:,}")
        print(f"  total storage       : "
                f"{_fmt_bytes(summary['grand']['total_storage_bytes'])}")
        print(f"  sports observed     : "
                f"{', '.join(summary['grand']['sports_observed']) or '<none>'}")
        seasons_observed = sorted(set(
            s for sport in summary["sport_coverage"].values()
            for s in (sport.get("seasons_present") or [])
        ))
        print(f"  seasons observed    : "
                f"{', '.join(seasons_observed) or '<none>'}")
        partials = []
        for sport, sc in summary["sport_coverage"].items():
            tc = sum(sc.get("team_prop_count", {}).values())
            pc = sum(sc.get("player_prop_count", {}).values())
            gc = sum(sc.get("graded_count", {}).values())
            if (tc > 0 or pc > 0) and gc == 0:
                partials.append(f"{sport}=ungraded")
            elif tc == 0 and pc == 0:
                partials.append(f"{sport}=no-data")
        print(f"  partial datasets    : "
                f"{', '.join(partials) or '<none>'}")
        print(f"  markdown report     : {REPORT_MD}")
        print(f"  json summary        : {REPORT_JSON}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
