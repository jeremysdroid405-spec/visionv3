"""
canonicalize_historical_outcomes.py — build canonical player/team outcome collections.

Creates/updates two collections as the single source of truth for all downstream pipelines:
  player_historical_outcomes  — one row per player prop bet decision (all sports)
  team_historical_outcomes    — one row per team prop bet decision (normalized in place)

Sources for player_historical_outcomes:
  1. sgo_pp_research_outcomes (2025-26 SGO player props, outcome_resolved=True)
  2. nba_player_historical_props (2024-25 historical, graded via BDL game logs)

Sources for team_historical_outcomes:
  Existing collection normalized in place to canonical schema (1.8M+ rows).

Usage:
    python -m scripts.sgo.canonicalize_historical_outcomes
    python -m scripts.sgo.canonicalize_historical_outcomes --dry-run
    python -m scripts.sgo.canonicalize_historical_outcomes --step 1
    python -m scripts.sgo.canonicalize_historical_outcomes --step 2
    python -m scripts.sgo.canonicalize_historical_outcomes --step 3
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient
import pymongo
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

# ── collection names ─────────────────────────────────────────────────────────
SGO_COLL          = "sgo_pp_research_outcomes"
HIST_PLAYER_COLL  = "nba_player_historical_props"
TEAM_COLL         = "team_historical_outcomes"    # source AND destination for step 2
HUB_COLL          = "nba_master_hub_2026"
BDL_COLL          = "bdl_historical_game_logs"
PLAYER_OUT_COLL   = "player_historical_outcomes"

BATCH_SIZE = 5_000
LOG_EVERY  = 10_000

# Extra fields to $unset from team_historical_outcomes during normalization
TEAM_EXTRA_FIELDS = {
    "book", "market_key", "away_score_used", "betTypeID", "commence_time",
    "home_away", "home_score_used", "market", "market_name", "opponent_team_id",
    "statEntityID", "statID", "periodID", "sideID", "market_category",
    "unresolved_reason", "odds", "league",
}

# Extra fields to $unset from player_historical_outcomes (left over from prior pipelines)
PLAYER_EXTRA_FIELDS = {
    "as_of_date", "avg_line", "cv", "edge", "fair_probability",
    "front_lines_failed_reasons", "front_lines_pass", "gate_reasons",
    "hit_rate_l10", "hit_rate_l20", "hit_rate_l5", "implied_probability",
    "market_category", "model_probability", "model_version", "odds_bucket",
    "pipeline_version", "player_name_normalized", "prop_type",
    "safe_haven_failed_reasons", "safe_haven_pass", "scored_at",
    "selected_tier", "ssot_source", "tier", "tp", "vision_score",
    "war_zone_failed_reasons", "war_zone_pass",
    # older aliases
    "grading_source", "hit_rate", "home_team", "away_team", "league",
    "market", "market_key", "book", "betTypeID", "odds", "sideID",
}

SPORT_TO_LEAGUE: Dict[str, str] = {
    "nba": "NBA", "mlb": "MLB", "nfl": "NFL", "nhl": "NHL",
    "ncaab": "NCAAB", "ncaaf": "NCAAF",
}

# ── stat resolvers: statID from nba_player_historical_props → BDL field ──────
def _safe(r: Dict, *keys: str) -> Optional[float]:
    for k in keys:
        v = r.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _sum(*vals: Optional[float]) -> Optional[float]:
    if any(v is None for v in vals):
        return None
    return float(sum(vals))  # type: ignore[arg-type]


STAT_RESOLVERS: Dict[str, Callable[[Dict], Optional[float]]] = {
    "points":                  lambda r: _safe(r, "pts"),
    "rebounds":                lambda r: _safe(r, "reb"),
    "assists":                 lambda r: _safe(r, "ast"),
    "threePointersMade":       lambda r: _safe(r, "fg3m"),
    "threes_made":             lambda r: _safe(r, "fg3m"),
    "threePointersAttempted":  lambda r: _safe(r, "fg3a"),
    "blocks":                  lambda r: _safe(r, "blk"),
    "steals":                  lambda r: _safe(r, "stl"),
    "turnovers":               lambda r: _safe(r, "turnover"),
    "fieldGoalsMade":          lambda r: _safe(r, "fgm"),
    "fieldGoalsAttempted":     lambda r: _safe(r, "fga"),
    "freeThrowsMade":          lambda r: _safe(r, "ftm"),
    "freeThrowsAttempted":     lambda r: _safe(r, "fta"),
    "twoPointersMade":         lambda r: (
        _safe(r, "fgm") - _safe(r, "fg3m")  # type: ignore[operator]
        if _safe(r, "fgm") is not None and _safe(r, "fg3m") is not None
        else None
    ),
    "points+rebounds+assists": lambda r: _sum(_safe(r, "pts"), _safe(r, "reb"), _safe(r, "ast")),
    "points+rebounds":         lambda r: _sum(_safe(r, "pts"), _safe(r, "reb")),
    "points+assists":          lambda r: _sum(_safe(r, "pts"), _safe(r, "ast")),
    "rebounds+assists":        lambda r: _sum(_safe(r, "reb"), _safe(r, "ast")),
    "blocks+steals":           lambda r: _sum(_safe(r, "blk"), _safe(r, "stl")),
    "blocks_steals":           lambda r: _sum(_safe(r, "blk"), _safe(r, "stl")),
    "pra":                     lambda r: _sum(_safe(r, "pts"), _safe(r, "reb"), _safe(r, "ast")),
    "pts_reb":                 lambda r: _sum(_safe(r, "pts"), _safe(r, "reb")),
    "pts_ast":                 lambda r: _sum(_safe(r, "pts"), _safe(r, "ast")),
    "reb_ast":                 lambda r: _sum(_safe(r, "reb"), _safe(r, "ast")),
}


# ── type coercion helpers ─────────────────────────────────────────────────────
def _format_anchor_odds(raw: Any) -> Optional[str]:
    """Convert any raw odds value to canonical '+X' / '-X' string."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "None", "null"):
        return None
    try:
        val = int(float(s))
    except (TypeError, ValueError):
        return s  # return as-is if unparseable
    return f"+{val}" if val > 0 else str(val)


def _implied_prob(raw: Any) -> Optional[float]:
    """American odds (any form) → implied probability [0, 1]."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "None", "null"):
        return None
    try:
        o = float(s)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return round(100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0), 6)


def _clean_odds(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "None", "null"):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        low = v.lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
    return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "None", "null"):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "None", "null"):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _sport_from_league(league_id: str) -> str:
    return league_id.lower() if league_id else "unknown"


# ── index builders ────────────────────────────────────────────────────────────
async def build_hub_indexes(
    db,
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """
    Returns:
        hub_id_idx:   sgo_player_id → int(bdl_id)
        hub_name_idx: sgo_player_id → display_name
    """
    hub_id_idx: Dict[str, int] = {}
    hub_name_idx: Dict[str, str] = {}

    async for doc in db[HUB_COLL].find(
        {"sgo_player_id": {"$exists": True, "$ne": None}},
        {"sgo_player_id": 1, "bdl_id": 1, "display_name": 1, "_id": 0},
    ):
        sgo_id = str(doc.get("sgo_player_id") or "").strip()
        if not sgo_id:
            continue
        bdl_id = doc.get("bdl_id")
        if bdl_id is not None:
            try:
                hub_id_idx[sgo_id] = int(bdl_id)
            except (TypeError, ValueError):
                pass
        name = doc.get("display_name")
        if name:
            hub_name_idx[sgo_id] = str(name)

    return hub_id_idx, hub_name_idx


async def build_bdl_index(db) -> Dict[Tuple[int, str], Dict]:
    """
    (bdl_player_id_int, date_str) → stats doc.

    Sources (applied in order; later sources overwrite earlier ones):
    1. bdl_historical_game_logs — old-API small integer player IDs
    2. hub history (2023/2024/2025 seasons) — new-API large integer bdl_id per player
    3. hub bdl_game_logs (2025-26 live season) — new-API large integer bdl_id
    """
    idx: Dict[Tuple[int, str], Dict] = {}

    # 1. standalone historical collection (old-style small player IDs)
    async for doc in db[BDL_COLL].find({}, {"_id": 0}):
        pid = doc.get("player_id")
        dt  = str(doc.get("date") or "")
        if pid is not None and dt:
            try:
                idx[(int(pid), dt)] = doc
            except (TypeError, ValueError):
                pass

    # 2. hub history (all stored seasons: typically 2023, 2024, 2025)
    async for hub in db[HUB_COLL].find(
        {"bdl_id": {"$exists": True}, "history": {"$exists": True}},
        {"bdl_id": 1, "history": 1, "_id": 0},
    ):
        bdl_id = hub.get("bdl_id")
        if bdl_id is None:
            continue
        try:
            pid_int = int(bdl_id)
        except (TypeError, ValueError):
            continue
        history = hub.get("history") or {}
        if not isinstance(history, dict):
            continue
        for season_key, entries in history.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                dt = str(entry.get("date") or entry.get("game_date") or "")
                if dt:
                    idx[(pid_int, dt)] = entry

    # 3. hub embedded bdl_game_logs (live 2025-26 season; overwrites history entries)
    async for hub in db[HUB_COLL].find(
        {"bdl_id": {"$exists": True},
         "bdl_game_logs": {"$exists": True, "$not": {"$size": 0}}},
        {"bdl_id": 1, "bdl_game_logs": 1, "_id": 0},
    ):
        bdl_id = hub.get("bdl_id")
        if bdl_id is None:
            continue
        try:
            pid_int = int(bdl_id)
        except (TypeError, ValueError):
            continue
        for entry in hub.get("bdl_game_logs") or []:
            dt = str(entry.get("date") or entry.get("game_date") or "")
            if dt:
                idx[(pid_int, dt)] = entry

    return idx


# ── bulk write helper ─────────────────────────────────────────────────────────
async def _flush(coll, ops: List[UpdateOne]) -> Tuple[int, int]:
    """Returns (upserted + modified, error_count)."""
    if not ops:
        return 0, 0
    try:
        result = await coll.bulk_write(ops, ordered=False)
        return result.upserted_count + result.modified_count, 0
    except BulkWriteError as bwe:
        errors = bwe.details.get("writeErrors", [])
        dup = sum(1 for e in errors if e.get("code") == 11000)
        non_dup = len(errors) - dup
        if non_dup:
            print(f"  [warn] {non_dup} non-duplicate bulk write errors", flush=True)
        written = len(ops) - len(errors)
        return written, len(errors)


# ── Step 1a: ingest resolved SGO player rows ──────────────────────────────────
async def step1a_sgo(db, dry_run: bool) -> None:
    t0 = time.time()
    scanned = written = errors = 0

    query = {
        "player_id":       {"$exists": True},
        "team_id":         {"$exists": False},
        "home_away":       {"$exists": False},
        "outcome_resolved": True,
    }
    total = await db[SGO_COLL].count_documents(query)
    print(f"  [1a] SGO resolved player rows: {total:,}", flush=True)

    ops: List[UpdateOne] = []

    async for doc in db[SGO_COLL].find(query):
        scanned += 1

        event_id    = doc.get("event_id")
        player_id   = str(doc.get("player_id") or "").strip()
        player_name = doc.get("player_name")
        game_date   = doc.get("game_date")
        league_id   = str(doc.get("league_id") or "").upper()
        sport       = _sport_from_league(league_id)

        # stat_family is already canonical in SGO; stat_id is the raw source field
        stat_family = doc.get("stat_family") or doc.get("stat_id")
        period_id   = doc.get("period_id") or "game"
        side        = str(doc.get("side") or "").lower()
        line        = doc.get("line")

        is_alternate = _to_bool(doc.get("is_alternate")) or False

        # odds — book_odds is the cleanest source; anchor dict is fallback
        raw_odds = doc.get("book_odds")
        if raw_odds is None:
            anchor = doc.get("anchor")
            if isinstance(anchor, dict):
                raw_odds = anchor.get("price")

        anchor_odds    = _format_anchor_odds(raw_odds)
        pp_implied_prob = _to_float(doc.get("pp_implied_probability"))
        clean_odds_val  = _clean_odds(raw_odds)

        hit     = _to_bool(doc.get("hit"))
        push    = _to_bool(doc.get("push"))
        outcome = doc.get("outcome") or "UNRESOLVED"
        outcome_numeric_val = _to_int(doc.get("outcome_numeric"))
        actual_value    = _to_float(doc.get("actual_value"))
        margin_vs_line  = _to_float(doc.get("margin_vs_line"))
        graded_at       = doc.get("resolved_at") or doc.get("graded_at")
        grading_version = doc.get("grading_version")

        if not player_id or not stat_family or not game_date:
            continue

        filt = {
            "player_id":   player_id,
            "game_date":   game_date,
            "stat_family": stat_family,
            "side":        side,
            "line":        line,
            "period_id":   period_id,
        }

        payload = {
            "event_id":               event_id,
            "game_date":              game_date,
            "sport":                  sport,
            "league_id":              league_id,
            "player_id":              player_id,
            "player_name":            player_name,
            "stat_family":            stat_family,
            "period_id":              period_id,
            "side":                   side,
            "line":                   line,
            "is_alternate":           is_alternate,
            "anchor_odds":            anchor_odds,
            "pp_implied_probability": pp_implied_prob,
            "clean_odds":             clean_odds_val,
            "hit":                    hit,
            "push":                   push,
            "outcome":                outcome,
            "outcome_numeric":        outcome_numeric_val,
            "outcome_resolved":       True,
            "actual_value":           actual_value,
            "margin_vs_line":         margin_vs_line,
            "graded_at":              graded_at,
            "grading_version":        grading_version,
        }

        ops.append(UpdateOne(filt, {"$set": payload}, upsert=True))

        if len(ops) >= BATCH_SIZE:
            if not dry_run:
                w, e = await _flush(db[PLAYER_OUT_COLL], ops)
                written += w
                errors  += e
            else:
                written += len(ops)
            ops = []

        if scanned % LOG_EVERY == 0:
            print(
                f"  [1a] … {scanned:,} scanned  written={written:,}  "
                f"errors={errors}  elapsed={time.time()-t0:.1f}s",
                flush=True,
            )

    if ops:
        if not dry_run:
            w, e = await _flush(db[PLAYER_OUT_COLL], ops)
            written += w
            errors  += e
        else:
            written += len(ops)
        ops = []

    print(
        f"  [1a] done  scanned={scanned:,}  written={written:,}  "
        f"errors={errors}  elapsed={time.time()-t0:.1f}s",
        flush=True,
    )


# ── Step 1b: grade nba_player_historical_props via BDL ───────────────────────
async def step1b_historical(
    db,
    hub_id_idx: Dict[str, int],
    hub_name_idx: Dict[str, str],
    bdl_idx: Dict[Tuple[int, str], Dict],
    dry_run: bool,
) -> None:
    t0 = time.time()
    scanned = written = no_hub = no_bdl = no_stat = n_push = errors = 0
    now_utc = datetime.now(timezone.utc)

    total = await db[HIST_PLAYER_COLL].count_documents({})
    print(f"  [1b] historical player props to grade: {total:,}", flush=True)

    ops: List[UpdateOne] = []

    async for doc in db[HIST_PLAYER_COLL].find({}):
        scanned += 1

        player_id = str(doc.get("player_id") or "").strip()
        if not player_id:
            no_hub += 1
            continue

        bdl_pid = hub_id_idx.get(player_id)
        if bdl_pid is None:
            no_hub += 1
            continue

        game_date = str(doc.get("game_date") or "")
        if not game_date:
            no_bdl += 1
            continue

        stats_doc = bdl_idx.get((bdl_pid, game_date))
        if stats_doc is None:
            no_bdl += 1
            continue

        stat_id = str(doc.get("statID") or "")
        resolver = STAT_RESOLVERS.get(stat_id)
        if resolver is None:
            no_stat += 1
            continue

        try:
            actual_value = resolver(stats_doc)
        except Exception:
            no_stat += 1
            continue
        if actual_value is None:
            no_stat += 1
            continue

        raw_line = doc.get("line")
        try:
            line = float(str(raw_line)) if raw_line is not None else None
        except (TypeError, ValueError):
            no_stat += 1
            continue
        if line is None:
            no_stat += 1
            continue

        # sideID is lowercase in historical props ("under", "over"), side is uppercase
        side_raw = str(doc.get("sideID") or doc.get("side") or "").upper()
        if side_raw not in ("OVER", "UNDER", "YES", "NO"):
            no_stat += 1
            continue

        side_lower = side_raw.lower()
        margin     = actual_value - line

        if actual_value == line:
            # push
            n_push += 1
            hit_val = None
            push_val = True
            outcome_str = "PUSH"
            outcome_numeric_val = None
        else:
            won = (actual_value > line) if side_raw in ("OVER", "YES") else (actual_value < line)
            hit_val = won
            push_val = False
            outcome_str = "WIN" if won else "LOSS"
            outcome_numeric_val = 1 if won else 0

        odds_raw    = doc.get("odds")
        anchor_odds = _format_anchor_odds(odds_raw) if odds_raw is not None else None
        pp_prob     = _implied_prob(odds_raw)
        clean_o     = _clean_odds(odds_raw)

        period_id   = str(doc.get("periodID") or doc.get("period_id") or "game")
        league_id   = str(doc.get("league") or doc.get("league_id") or "NBA").upper()
        sport       = str(doc.get("sport") or league_id.lower())
        player_name = hub_name_idx.get(player_id)

        is_alternate = _to_bool(doc.get("is_alternate")) or False

        filt = {
            "player_id":   player_id,
            "game_date":   game_date,
            "stat_family": stat_id,
            "side":        side_lower,
            "line":        line,
            "period_id":   period_id,
        }

        payload = {
            "event_id":               doc.get("event_id"),
            "game_date":              game_date,
            "sport":                  sport,
            "league_id":              league_id,
            "player_id":              player_id,
            "player_name":            player_name,
            "stat_family":            stat_id,
            "period_id":              period_id,
            "side":                   side_lower,
            "line":                   line,
            "is_alternate":           is_alternate,
            "anchor_odds":            anchor_odds,
            "pp_implied_probability": pp_prob,
            "clean_odds":             clean_o,
            "hit":                    hit_val,
            "push":                   push_val,
            "outcome":                outcome_str,
            "outcome_numeric":        outcome_numeric_val,
            "outcome_resolved":       True,
            "actual_value":           actual_value,
            "margin_vs_line":         margin,
            "graded_at":              now_utc,
            "grading_version":        "canonical_v1",
        }

        ops.append(UpdateOne(filt, {"$set": payload}, upsert=True))
        written += 1

        if len(ops) >= BATCH_SIZE:
            if not dry_run:
                w, e = await _flush(db[PLAYER_OUT_COLL], ops)
                errors += e
            ops = []

        if scanned % LOG_EVERY == 0:
            elapsed = time.time() - t0
            rate = scanned / elapsed if elapsed else 0
            print(
                f"  [1b] … {scanned:,} scanned  no_hub={no_hub:,}  no_bdl={no_bdl:,}  "
                f"no_stat={no_stat:,}  push={n_push:,}  written={written:,}  "
                f"rate={rate:,.0f}/s",
                flush=True,
            )

    if ops and not dry_run:
        w, e = await _flush(db[PLAYER_OUT_COLL], ops)
        errors += e

    print(
        f"  [1b] done  scanned={scanned:,}  no_hub={no_hub:,}  no_bdl={no_bdl:,}  "
        f"no_stat={no_stat:,}  push={n_push:,}  written={written:,}  "
        f"errors={errors}  elapsed={time.time()-t0:.1f}s",
        flush=True,
    )


# ── Step 1c: sweep player_historical_outcomes to remove non-canonical fields ──
async def step1c_cleanup_player(db, dry_run: bool) -> None:
    """$unset all non-canonical extra fields from player_historical_outcomes."""
    t0 = time.time()
    unset_doc = {f: "" for f in PLAYER_EXTRA_FIELDS}
    count = await db[PLAYER_OUT_COLL].count_documents({})
    print(f"  [1c] sweeping {count:,} player docs for extra field cleanup …", flush=True)
    if not dry_run:
        result = await db[PLAYER_OUT_COLL].update_many({}, {"$unset": unset_doc})
        print(
            f"  [1c] done  modified={result.modified_count:,}  "
            f"elapsed={time.time()-t0:.1f}s",
            flush=True,
        )
    else:
        print(f"  [1c] dry_run — would $unset {len(unset_doc)} fields on {count:,} docs", flush=True)


# ── Step 2: normalize team_historical_outcomes in place ───────────────────────
async def _prepare_team_indexes(db, dry_run: bool) -> None:
    """Drop schema-conflicting old indexes; create canonical compound indexes."""
    coll = db[TEAM_COLL]
    # Indexes that include fields being renamed/removed (side casing, market_key, book)
    to_drop = [
        "team_outcome_pk",           # unique on (event_id, team_id, market_key, line, side, book)
        "ix_team_hist_hr_lookup",    # includes market_category and commence_time
        "ix_team_hist_game_history", # includes commence_time
        "market_category_1",         # field being removed
        "betTypeID_1",               # field being removed
    ]
    for idx_name in to_drop:
        try:
            if not dry_run:
                await coll.drop_index(idx_name)
            print(f"  [2-idx] {'would drop' if dry_run else 'dropped'} {idx_name}", flush=True)
        except Exception:
            pass

    if not dry_run:
        # Canonical lookup index for downstream pipelines
        await coll.create_index(
            [("team_id", pymongo.ASCENDING),
             ("sport",   pymongo.ASCENDING),
             ("stat_family", pymongo.ASCENDING),
             ("side",    pymongo.ASCENDING),
             ("outcome_resolved", pymongo.ASCENDING),
             ("game_date", pymongo.DESCENDING)],
            name="ix_team_canonical_lookup",
            background=True,
        )
        print("  [2-idx] created ix_team_canonical_lookup", flush=True)


async def step2_normalize_teams(db, dry_run: bool) -> None:
    t0 = time.time()
    scanned = written = errors = 0

    await _prepare_team_indexes(db, dry_run)

    total = await db[TEAM_COLL].count_documents({})
    print(f"  [2] team rows to normalize: {total:,}", flush=True)

    ops: List[UpdateOne] = []

    async for doc in db[TEAM_COLL].find({}):
        scanned += 1
        oid = doc["_id"]

        sport    = str(doc.get("sport") or "").lower()
        league_id = SPORT_TO_LEAGUE.get(sport, sport.upper() if sport else None)

        # stat_family: prefer market_category (h2h, spread, game_total) over statID (points)
        stat_family = (
            doc.get("stat_family")        # already canonical if previously normalized
            or doc.get("market_category")  # h2h, spread, game_total, team_total, etc.
            or doc.get("statID")           # fallback: "points"
        )

        period_id = (
            doc.get("period_id")
            or doc.get("periodID")
            or "game"
        )

        # side: prefer sideID (lowercase canonical) over side (may be uppercase)
        side_raw = doc.get("sideID") or doc.get("side") or ""
        side     = str(side_raw).lower()

        line = _to_float(doc.get("line"))

        is_alternate = _to_bool(doc.get("is_alternate")) or False

        # anchor_odds: may already exist from a prior normalization run; otherwise compute from odds
        existing_anchor = doc.get("anchor_odds")
        if existing_anchor:
            anchor_odds = existing_anchor
        else:
            raw_odds = doc.get("odds")
            anchor_odds = _format_anchor_odds(raw_odds) if raw_odds is not None else None

        pp_prob = _to_float(doc.get("pp_implied_probability"))
        if pp_prob is None and anchor_odds:
            pp_prob = _implied_prob(anchor_odds)

        existing_clean = doc.get("clean_odds")
        clean_o = _to_int(existing_clean) if existing_clean is not None else _clean_odds(anchor_odds)

        hit            = _to_bool(doc.get("hit"))
        push           = _to_bool(doc.get("push"))
        outcome        = doc.get("outcome") or "UNRESOLVED"
        outcome_numeric_val = _to_int(doc.get("outcome_numeric"))
        outcome_resolved    = _to_bool(doc.get("outcome_resolved")) or False
        actual_value   = _to_float(doc.get("actual_value"))
        margin         = _to_float(doc.get("margin_vs_line"))

        set_fields: Dict[str, Any] = {
            "sport":                  sport,
            "league_id":              league_id,
            "stat_family":            stat_family,
            "period_id":              period_id,
            "side":                   side,
            "line":                   line,
            "is_alternate":           is_alternate,
            "anchor_odds":            anchor_odds,
            "pp_implied_probability": pp_prob,
            "clean_odds":             clean_o,
            "hit":                    hit,
            "push":                   push,
            "outcome":                outcome,
            "outcome_numeric":        outcome_numeric_val,
            "outcome_resolved":       outcome_resolved,
            "actual_value":           actual_value,
            "margin_vs_line":         margin,
        }

        unset_fields = {f: "" for f in TEAM_EXTRA_FIELDS if f in doc}

        update: Dict[str, Any] = {"$set": set_fields}
        if unset_fields:
            update["$unset"] = unset_fields

        ops.append(UpdateOne({"_id": oid}, update))
        written += 1

        if len(ops) >= BATCH_SIZE:
            if not dry_run:
                _, e = await _flush(db[TEAM_COLL], ops)
                errors += e
            ops = []

        if scanned % LOG_EVERY == 0:
            print(
                f"  [2] … {scanned:,} scanned  written={written:,}  "
                f"errors={errors}  elapsed={time.time()-t0:.1f}s",
                flush=True,
            )

    if ops and not dry_run:
        _, e = await _flush(db[TEAM_COLL], ops)
        errors += e

    print(
        f"  [2] done  scanned={scanned:,}  written={written:,}  "
        f"errors={errors}  elapsed={time.time()-t0:.1f}s",
        flush=True,
    )


# ── Step 3: schema compliance report ─────────────────────────────────────────
async def step3_verify(db) -> None:
    print("\n" + "=" * 72)
    print("  SCHEMA COMPLIANCE REPORT")
    print("=" * 72)

    for coll_name, label, id_field in [
        (PLAYER_OUT_COLL, "player_historical_outcomes", "player_id"),
        (TEAM_COLL,       "team_historical_outcomes",   "team_id"),
    ]:
        coll = db[coll_name]
        total = await coll.count_documents({})
        print(f"\n── {label} ──")
        print(f"  total docs: {total:,}")

        if total == 0:
            print("  (empty collection)")
            continue

        # Per-sport breakdown
        pipeline_sport = [
            {"$group": {
                "_id": "$sport",
                "count": {"$sum": 1},
                "resolved": {"$sum": {"$cond": ["$outcome_resolved", 1, 0]}},
                "unresolved": {"$sum": {"$cond": ["$outcome_resolved", 0, 1]}},
                "null_anchor_odds": {"$sum": {"$cond": [{"$eq": ["$anchor_odds", None]}, 1, 0]}},
                "null_pp_prob": {"$sum": {"$cond": [{"$eq": ["$pp_implied_probability", None]}, 1, 0]}},
                "min_date": {"$min": "$game_date"},
                "max_date": {"$max": "$game_date"},
            }},
            {"$sort": {"_id": 1}},
        ]
        async for row in coll.aggregate(pipeline_sport):
            sport = row["_id"] or "(null)"
            print(f"\n  sport={sport}")
            print(f"    docs:              {row['count']:,}")
            print(f"    resolved:          {row['resolved']:,}")
            print(f"    unresolved:        {row['unresolved']:,}")
            print(f"    null anchor_odds:  {row['null_anchor_odds']:,}")
            print(f"    null pp_prob:      {row['null_pp_prob']:,}")
            print(f"    date range:        {row['min_date']} — {row['max_date']}")

        # Sample doc per sport
        print(f"\n  sample docs per sport:")
        async for sport_doc in coll.aggregate([
            {"$group": {"_id": "$sport", "doc_id": {"$first": "$_id"}}},
        ]):
            sample = await coll.find_one({"_id": sport_doc["doc_id"]})
            if sample:
                sample.pop("_id", None)
                sport_val = sample.get("sport", "?")
                print(f"\n    [{sport_val}] {sample.get(id_field)}")
                canonical_keys = [
                    "event_id", "game_date", "sport", "league_id", id_field,
                    "stat_family", "period_id", "side", "line", "is_alternate",
                    "anchor_odds", "pp_implied_probability", "clean_odds",
                    "hit", "push", "outcome", "outcome_numeric", "outcome_resolved",
                    "actual_value", "margin_vs_line", "graded_at", "grading_version",
                ]
                for k in canonical_keys:
                    v = sample.get(k)
                    print(f"      {k}: {v!r}")

                # Check for unexpected extra fields
                expected = set(canonical_keys) | {"player_name"} | {id_field}
                extra = set(sample.keys()) - expected
                if extra:
                    print(f"    [warn] unexpected fields: {sorted(extra)}")

    print("\n" + "=" * 72)


# ── index management ──────────────────────────────────────────────────────────
async def _deduplicate_player_coll(db) -> int:
    """Remove duplicate docs, keeping the first _id per canonical key group."""
    coll = db[PLAYER_OUT_COLL]
    removed = 0
    pipeline = [
        {"$group": {
            "_id": {
                "player_id":   "$player_id",
                "game_date":   "$game_date",
                "stat_family": "$stat_family",
                "side":        "$side",
                "line":        "$line",
                "period_id":   "$period_id",
            },
            "ids":   {"$push": "$_id"},
            "count": {"$sum": 1},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]
    async for group in coll.aggregate(pipeline, allowDiskUse=True):
        ids_to_delete = group["ids"][1:]   # keep the first, delete the rest
        if ids_to_delete:
            result = await coll.delete_many({"_id": {"$in": ids_to_delete}})
            removed += result.deleted_count
    return removed


async def ensure_indexes(db) -> None:
    """Deduplicate and create unique index on player_historical_outcomes."""
    coll = db[PLAYER_OUT_COLL]

    # Drop old/conflicting indexes by name
    for old_name in (
        "uniq_player_outcome",
        "uniq_player_outcome_v1",
        "uniq_player_historical_outcome",
    ):
        try:
            await coll.drop_index(old_name)
            print(f"  [idx] dropped {old_name}", flush=True)
        except Exception:
            pass

    # Deduplicate existing docs before creating the unique index
    existing = await coll.count_documents({})
    if existing > 0:
        print(f"  [idx] deduplicating {existing:,} existing docs …", flush=True)
        removed = await _deduplicate_player_coll(db)
        if removed:
            print(f"  [idx] removed {removed:,} duplicate docs", flush=True)

    # Single unique index covering both SGO and historical rows
    await coll.create_index(
        [
            ("player_id",   pymongo.ASCENDING),
            ("game_date",   pymongo.ASCENDING),
            ("stat_family", pymongo.ASCENDING),
            ("side",        pymongo.ASCENDING),
            ("line",        pymongo.ASCENDING),
            ("period_id",   pymongo.ASCENDING),
        ],
        unique=True,
        name="uniq_player_historical_outcome",
        sparse=True,
    )
    print("  [idx] unique index created on player_historical_outcomes", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name   = os.environ.get("DB_NAME", "pick_vision")
    client = AsyncIOMotorClient(mongo_url)
    db     = client[db_name]

    run_steps = set(args.step) if args.step else {1, 2, 3}
    t_global  = time.time()

    print(f"[{datetime.now(timezone.utc).isoformat()}] canonicalize_historical_outcomes")
    print(f"  db={db_name}  dry_run={args.dry_run}  steps={sorted(run_steps)}")

    if 1 in run_steps:
        print("\n── Step 1: player_historical_outcomes ──────────────────────────────")

        if not args.dry_run:
            print("  creating indexes …", flush=True)
            await ensure_indexes(db)

        print("  building hub indexes …", flush=True)
        hub_id_idx, hub_name_idx = await build_hub_indexes(db)
        print(f"  hub: {len(hub_id_idx):,} sgo_player_id → bdl_id  "
              f"/ {len(hub_name_idx):,} → display_name", flush=True)

        print("  building BDL index (bdl_historical_game_logs + hub history + hub bdl_game_logs) …",
              flush=True)
        bdl_idx = await build_bdl_index(db)
        print(f"  bdl_idx: {len(bdl_idx):,} (player_id, date) entries", flush=True)

        print("\n  ── 1a: SGO resolved player rows → player_historical_outcomes ──")
        await step1a_sgo(db, args.dry_run)

        print("\n  ── 1b: nba_player_historical_props (graded via BDL) ──")
        await step1b_historical(db, hub_id_idx, hub_name_idx, bdl_idx, args.dry_run)

        print("\n  ── 1c: sweep player_historical_outcomes — remove non-canonical fields ──")
        await step1c_cleanup_player(db, args.dry_run)

    if 2 in run_steps:
        print("\n── Step 2: normalize team_historical_outcomes in place ─────────────")
        await step2_normalize_teams(db, args.dry_run)

    if 3 in run_steps:
        print("\n── Step 3: verify schema compliance ────────────────────────────────")
        await step3_verify(db)

    print(f"\n  total elapsed: {time.time() - t_global:.1f}s")
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Compute and log but do not write to MongoDB",
    )
    p.add_argument(
        "--step", type=int, nargs="+", choices=[1, 2, 3],
        help="Run only specific step(s) (default: all three)",
    )
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
