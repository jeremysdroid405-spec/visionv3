"""
reshape_sgo_to_replay_odds.py — re-emit historical SGO props into the EXACT
schema the production replay pipeline already consumes.

Target collection: `sgo_replay_alt_odds_raw` — same shape as
`mlb_historical_alt_odds_raw` (the live odds-api backfill).  After this
step runs, the existing `run_production_replay()` can be invoked as-is by
pointing the adapter's `odds_collection` at this new collection.

Source:    sgo_pp_research_core_enriched  (one row per offer; books only)
Driver:    league=MLB, snapshot_iso = f"{game_date}T11:00:00Z"

Idempotent compound upsert key:
    (sport, game_date, event_id, player_name_normalized,
     market, line, side, book, snapshot_iso)

2026-05-21 — REWRITE. Previous version keyed on `stat_family`, but the
upstream producer (build_pp_research_core.py) writes `stat_id` only and
NEVER sets `stat_family`. That made the old script skip every row with
`n_no_market` and write zero docs. The fix:
  • Map directly from canonical SGO `stat_id` → market.
  • Pull real American odds from `anchor.price` (PrizePicks anchor) and
    optionally from `books[]` matching `best_book_id`. The old `-110`
    fallback masked which rows had no price data at all.
  • Telemetry: count distinct game_dates, league_ids, stat_ids in the
    matched window; report skip reasons with sample docs; verify post-run
    row count in the destination collection.
  • `--debug-source` prints the first 5 source doc field listings.

Usage (via Admin API job runner):
    --league=MLB --start=2025-05-01 --end=2025-05-02
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne, ASCENDING
from pymongo.errors import OperationFailure, BulkWriteError

SRC_ENRICHED = "sgo_pp_research_core_enriched"
SRC_CORE     = "sgo_pp_research_core"
SRC  = SRC_ENRICHED   # default; auto-falls-back to SRC_CORE if enriched empty
DEST = "sgo_replay_alt_odds_raw"
SNAPSHOT_HOUR_UTC = 11

ANCHOR_BOOK = "prizepicks"

# 2026-06-02 — Two-tier book policy.
#
# BLOCKED_BOOKS: hard-removed from ingestion entirely.
#   • fliff    : Fliff Coins is a free-play sweepstakes — not real money.
#   • mybookie : tiny offshore book with stale, unreliable lines.
#   • unknown  : missing/garbled book label — can't de-vig or grade.
#
# REFERENCE_ONLY_BOOKS: kept in the warehouse for playability tracking,
# but every mathematical aggregation (de-vig, fair-probability,
# ROI / HR in the optimizer, peer median, integrity filter) MUST
# exclude them. These books post fixed +100 / +PP-multiplier payouts
# that are NOT real sportsbook quotes — including them in math
# systematically pulls every estimate toward +100.
#   • prizepicks : DFS pick'em; fixed payout multipliers, always +100.
#   • underdog   : DFS pick'em; same fixed-payout model.
#
# To re-admit a BLOCKED book, remove it from BLOCKED_BOOKS AND re-run
# reshape for the affected window. To promote a book OUT of
# REFERENCE_ONLY (treat it as a real book), remove it from
# REFERENCE_ONLY_BOOKS. Keep these sets in sync with the canonical
# `routes/emergent_admin/policy.py::REFERENCE_ONLY_BOOKS` and the
# scoring sibling modules.
BLOCKED_BOOKS = {"fliff", "mybookie", "unknown"}
REFERENCE_ONLY_BOOKS = {"prizepicks", "underdog"}


# Canonical SGO stat_id → production replay `market` name. These keys are
# the values build_pp_research_core writes into doc["stat_id"]; the values
# match what the production replay adapter expects in `market`.
_STAT_ID_TO_MARKET: Dict[str, str] = {
    # batting
    "batting_hits":               "batter_hits",
    "batting_runs":               "batter_runs_scored",
    "batting_RBI":                "batter_rbis",
    "batting_rbi":                "batter_rbis",
    "batting_homeRuns":           "batter_home_runs",
    "batting_totalBases":         "batter_total_bases",
    "batting_strikeouts":         "batter_strikeouts",
    "batting_walks":              "batter_walks",
    "batting_basesOnBalls":       "batter_walks",
    "batting_stolenBases":        "batter_stolen_bases",
    "batting_singles":            "batter_singles",
    "batting_doubles":            "batter_doubles",
    "batting_triples":            "batter_triples",
    "batting_hits+runs+rbi":      "batter_hits_runs_rbis",
    # pitching
    "pitcher_strikeouts":         "pitcher_strikeouts",
    "pitching_strikeouts":        "pitcher_strikeouts",
    "pitcher_hits_allowed":       "pitcher_hits_allowed",
    "pitching_hits":              "pitcher_hits_allowed",
    "pitcher_earned_runs":        "pitcher_earned_runs",
    "pitching_earnedRuns":        "pitcher_earned_runs",
    "pitcher_walks":              "pitcher_walks",
    "pitching_basesOnBalls":      "pitcher_walks",
    "pitching_outs":              "pitcher_outs",
    "pitching_pitchesThrown":     "pitcher_pitches_thrown",
    # composite — kept as-is so optimizer can choose to exclude them
    "fantasyScore":               "fantasy_score",
}

# Best-effort fallback: also accept the bucket-name shorthand
# (build_historical_outcomes.STAT_FAMILY values) in case any upstream
# job writes them.
_STAT_FAMILY_FALLBACK: Dict[str, str] = {
    "hits":                "batter_hits",
    "total_bases":         "batter_total_bases",
    "hits_runs_rbis":      "batter_hits_runs_rbis",
    "rbi":                 "batter_rbis",
    "rbis":                "batter_rbis",
    "runs":                "batter_runs_scored",
    "home_runs":           "batter_home_runs",
    "singles":             "batter_singles",
    "doubles":             "batter_doubles",
    "triples":             "batter_triples",
    "batting_strikeouts":  "batter_strikeouts",
    "batting_walks":       "batter_walks",
    "stolen_bases":        "batter_stolen_bases",
    "pitcher_strikeouts":  "pitcher_strikeouts",
    "pitcher_hits_allowed": "pitcher_hits_allowed",
    "pitching_outs":       "pitcher_outs",
    "pitcher_earned_runs": "pitcher_earned_runs",
    "pitcher_walks":       "pitcher_walks",
    "pitches_thrown":      "pitcher_pitches_thrown",
    "fantasy_score":       "fantasy_score",
}


def _normalize_player_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    for ch in (",", ".", "'", "`", '"'):
        s = s.replace(ch, "")
    s = " ".join(s.split())
    return s


def _safe_int_odds(raw: Any) -> Optional[int]:
    """Coerce odds to int; return None if not coercible (NaN, strings, etc)."""
    if raw is None:
        return None
    try:
        f = float(raw)
        if f != f:    # NaN
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _safe_float_line(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        f = float(raw)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _resolve_market(d: Dict[str, Any]) -> Optional[str]:
    """Map a source doc to the production replay `market` string.

    Priority: direct stat_id → fallback bucket name → upstream-set `market`.
    """
    sid = d.get("stat_id")
    if sid and sid in _STAT_ID_TO_MARKET:
        return _STAT_ID_TO_MARKET[sid]
    sf = d.get("stat_family")
    if sf and sf in _STAT_FAMILY_FALLBACK:
        return _STAT_FAMILY_FALLBACK[sf]
    if d.get("market"):
        return d["market"]
    return None


def _resolve_odds(d: Dict[str, Any]) -> tuple[Optional[int], str]:
    """Return (american_odds_int, source_label). Tries in this order:

      1. Doc-level `best_book_odds` / `odds` if upstream set them
      2. Look up `best_book_id` inside `books[].book_id` and take `.price`
      3. Anchor price (`anchor.price` — PrizePicks anchor)
      4. None  →  caller treats as missing-odds skip
    """
    o = _safe_int_odds(d.get("best_book_odds"))
    if o is not None:
        return o, "best_book_odds"
    o = _safe_int_odds(d.get("odds"))
    if o is not None:
        return o, "odds"
    bb_id = d.get("best_book_id")
    books = d.get("books") or []
    if bb_id and isinstance(books, list):
        for b in books:
            if isinstance(b, dict) and b.get("book_id") == bb_id:
                o = _safe_int_odds(b.get("price"))
                if o is not None:
                    return o, f"books[{bb_id}].price"
    # Anchor (PP) price
    anchor = d.get("anchor")
    if isinstance(anchor, dict):
        o = _safe_int_odds(anchor.get("price"))
        if o is not None:
            return o, "anchor.price"
    return None, "missing"


def _resolve_book(d: Dict[str, Any]) -> str:
    bb_id = d.get("best_book_id")
    if bb_id:
        return str(bb_id)
    if d.get("book"):
        return str(d["book"])
    if isinstance(d.get("anchor"), dict) and d["anchor"].get("book_id"):
        return str(d["anchor"]["book_id"])
    return ANCHOR_BOOK


async def _ensure_indexes(db) -> None:
    """Idempotent index creation. Tolerates pre-existing indexes with
    conflicting names or options — we just need ONE compound index on the
    upsert key set; uniqueness is preferred but not required."""
    desired_keys = [
        ("sport", ASCENDING),
        ("game_date", ASCENDING),
        ("event_id", ASCENDING),
        ("player_name_normalized", ASCENDING),
        ("market", ASCENDING), ("line", ASCENDING),
        ("side", ASCENDING), ("book", ASCENDING),
        ("snapshot_iso", ASCENDING),
    ]
    try:
        await db[DEST].create_index(
            desired_keys, name="alt_odds_compound_unique_v2",
            unique=True, background=True,
        )
    except OperationFailure as e:
        print(f"  ! create_index failed (code={getattr(e, 'code', '?')}): "
                  f"{e!r}", flush=True)
        existing = await db[DEST].index_information()
        for name, info in existing.items():
            if list(info.get("key") or []) == desired_keys and info.get("unique"):
                print(f"  ✓ existing unique index '{name}' covers the same "
                          f"key set — continuing.", flush=True)
                break
        else:
            print("  ! falling back to non-unique compound index.", flush=True)
            try:
                await db[DEST].create_index(
                    desired_keys, name="alt_odds_compound_v2_nonunique",
                    background=True)
            except OperationFailure as e2:
                print(f"  ! fallback index also failed: {e2!r}", flush=True)
    for k in ("game_date", "event_id", "snapshot_iso"):
        try:
            await db[DEST].create_index(k, background=True)
        except OperationFailure as e:
            print(f"  ! secondary index '{k}' skipped: {e!r}", flush=True)


def reshape_row(d: Dict[str, Any], now: datetime) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Pure function — transform ONE enriched-core doc into ONE alt-odds
    row, or return (None, reason) explaining why it was skipped.

    Exported for the unit smoke test.
    """
    if not d.get("league_id"):
        return None, "no_league"
    if not d.get("game_date"):
        return None, "no_game_date"
    if not d.get("event_id"):
        return None, "no_event_id"

    market = _resolve_market(d)
    if not market:
        return None, "no_market"

    line = _safe_float_line(d.get("line"))
    if line is None:
        return None, "bad_line"

    side = (d.get("side") or "").upper()
    if side not in ("OVER", "UNDER"):
        return None, "bad_side"

    pname = d.get("player_name") or ""
    if not pname:
        return None, "no_player_name"

    odds, odds_src = _resolve_odds(d)
    if odds is None:
        return None, "no_odds"

    book = _resolve_book(d)
    if (book or "").lower() in BLOCKED_BOOKS:
        return None, f"blocked_book:{book}"
    snapshot_iso = f"{d['game_date']}T{SNAPSHOT_HOUR_UTC:02d}:00:00Z"
    commence_time = d.get("commence_time") or f"{d['game_date']}T22:00:00Z"

    row = {
        "sport": "mlb",
        "sport_key": "baseball_mlb",
        # `league` mirrors the upstream sgo_pp_research_core_enriched
        # `league_id` field. Production canonical schema doesn't use it
        # (that uses `sport: "mlb"`), but we carry it through anyway so the
        # collection is self-describing and joinable with grading data
        # (sgo_pp_research_outcomes uses `league_id: "MLB"`).
        "league": "MLB",
        "game_date": d["game_date"],
        "event_id": d["event_id"],
        "home_team": d.get("home_team"),
        "away_team": d.get("away_team"),
        "commence_time": commence_time,
        "market": market,
        "stat": market,
        "is_alternate": bool(d.get("is_alternate")),
        "player_name": pname,
        "player_name_normalized": _normalize_player_name(pname),
        "line": line,
        "side": side,
        "book": book,
        "odds": odds,
        "book_last_update": now.isoformat(),
        "snapshot_iso": snapshot_iso,
        "ingested_at": now,
        "_source": "sgo_pp_research_core_enriched",
        "_source_stat_id": d.get("stat_id"),
        "_odds_source": odds_src,
        "_sgo_consensus_probability": d.get("consensus_probability"),
        "_sgo_best_book_probability": d.get("best_book_probability"),
        # 2026-05-24 — Multi-book universe metadata. Propagated from
        # `sgo_pp_research_core` so the optimizer + UI can filter
        # by anchor book or by playability per book without re-joins.
        "anchor_book":      d.get("anchor_book") or book,
        "anchor_source":    d.get("anchor_source"),
        "available_books":  d.get("available_books") or [],
        "playable_on_pp":      bool(d.get("playable_on_pp")),
        "playable_on_dk":      bool(d.get("playable_on_dk")),
        "playable_on_fd":      bool(d.get("playable_on_fd")),
        "playable_on_mgm":     bool(d.get("playable_on_mgm")),
        "playable_on_caesars": bool(d.get("playable_on_caesars")),
        "playable_on_bol":     bool(d.get("playable_on_bol")),
    }
    return row, None


async def _preflight_diagnostics(db, *, league: str, start: str, end: str) -> Dict[str, Any]:
    """Run BEFORE the main loop. Counts + distinct-value sampling that
    pinpoints the failure category if reshape ends up writing 0 rows.

    Inspects BOTH source collections (enriched + core) so the operator can
    see whether enrichment has been built for the window or not. The main
    `_run()` then picks whichever non-empty collection has data."""

    async def _inspect(coll_name: str) -> Dict[str, Any]:
        match = {"league_id": league,
                 "game_date": {"$gte": start, "$lte": end}}
        n = await db[coll_name].count_documents(match)
        info: Dict[str, Any] = {
            "match_count": n,
            "total_count": await db[coll_name].estimated_document_count(),
        }
        if n == 0 and info["total_count"] > 0:
            info["distinct_league_ids"] = await db[coll_name].distinct("league_id")
            sample_dates = await db[coll_name].aggregate([
                {"$group": {"_id": "$game_date"}},
                {"$sort":  {"_id": -1}}, {"$limit": 30},
            ]).to_list(length=30)
            info["recent_game_dates"] = [r["_id"] for r in sample_dates]
            sample = []
            async for d in db[coll_name].find({}, {"_id": 0}).limit(2):
                sample.append({k: (f"<{type(v).__name__} len={len(v)}>"
                                       if isinstance(v, (list, dict)) else v)
                                  for k, v in d.items()})
            info["sample_docs"] = sample
        if n > 0:
            info["distinct_game_dates"] = await db[coll_name].distinct(
                "game_date", match)
            info["distinct_stat_ids"]   = await db[coll_name].distinct(
                "stat_id", match)
            info["distinct_sides"]      = await db[coll_name].distinct(
                "side", match)
        return info

    return {
        SRC_ENRICHED: await _inspect(SRC_ENRICHED),
        SRC_CORE:     await _inspect(SRC_CORE),
    }


async def _run(args: argparse.Namespace) -> int:
    # 2026-05-26 — close the motor client on exit so the subprocess
    # actually terminates (see historical_full_pipeline_replay.py).
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        return await _run_body(args, client[os.environ["DB_NAME"]])
    finally:
        client.close()


async def _run_body(args: argparse.Namespace, db) -> int:
    await _ensure_indexes(db)

    print("=" * 72)
    print(f"  RESHAPE SGO → REPLAY ODDS  (→ {DEST})")
    print(f"  window: {args.start}..{args.end}   league: {args.league}")
    print(f"  snapshot_hour_utc = {SNAPSHOT_HOUR_UTC:02d}")
    print("=" * 72)

    # ── PREFLIGHT — inspect BOTH possible source collections ─────────────
    pf = await _preflight_diagnostics(db, league=args.league,
                                            start=args.start, end=args.end)
    n_enr  = pf[SRC_ENRICHED]["match_count"]
    n_core = pf[SRC_CORE]["match_count"]
    print()
    print(f"[preflight] source-collection counts for "
              f"league_id='{args.league}', game_date in [{args.start}..{args.end}]:")
    print(f"             {SRC_ENRICHED}: {n_enr} matched  "
              f"(total docs ~{pf[SRC_ENRICHED]['total_count']})")
    print(f"             {SRC_CORE}: {n_core} matched  "
              f"(total docs ~{pf[SRC_CORE]['total_count']})")

    # Source selection: explicit override → enriched if has data →
    # core as fallback → fail loudly
    chosen_src: Optional[str] = None
    if args.source:
        if args.source not in (SRC_ENRICHED, SRC_CORE):
            print(f"ERROR: --source must be one of {SRC_ENRICHED} or "
                      f"{SRC_CORE}")
            return 0
        chosen_src = args.source
        print(f"[preflight] using explicit --source={chosen_src}")
    elif n_enr > 0:
        chosen_src = SRC_ENRICHED
        print(f"[preflight] choosing {SRC_ENRICHED} (has {n_enr} matching rows)")
    elif n_core > 0:
        chosen_src = SRC_CORE
        print(f"[preflight] {SRC_ENRICHED} is empty for this window; "
                  f"falling back to {SRC_CORE} ({n_core} matching rows)")
        print("[preflight]   (NOTE: core rows lack `best_book_id` / "
                  "enrichment fields; odds will come from anchor.price "
                  "or books[].price directly.)")
    else:
        # Both empty — print everything we know about both collections
        print()
        print("[preflight] BOTH source collections have ZERO rows for the "
                  "requested filter. Diagnostic dump:")
        for cn, info in pf.items():
            print(f"\n  ── {cn} ──")
            print(f"  total docs ~{info['total_count']}")
            if info["total_count"] > 0:
                print(f"  distinct league_id values: "
                          f"{info.get('distinct_league_ids')}")
                print("  most recent 30 game_date values:")
                for gd in (info.get("recent_game_dates") or []):
                    print(f"     {gd}")
                if info.get("sample_docs"):
                    print("  sample doc shape:")
                    for i, sd in enumerate(info["sample_docs"]):
                        print(f"     [{i}] {json.dumps(sd, default=str)[:400]}")
        print()
        print("Diagnosis: nothing to reshape. Either neither source collection "
                  "has been ingested for this window, OR the storage format "
                  "for league_id / game_date differs from the filter "
                  "(see distinct values above).")
        return 0

    # Show what's in the chosen source's window
    chosen = pf[chosen_src]
    print(f"[preflight]   distinct game_dates in window: "
              f"{chosen.get('distinct_game_dates')}")
    print(f"[preflight]   distinct stat_ids in window: "
              f"{chosen.get('distinct_stat_ids')}")
    print(f"[preflight]   distinct sides in window: "
              f"{chosen.get('distinct_sides')}")
    print()

    # ── MAIN LOOP ────────────────────────────────────────────────────────
    n_seen = n_written = n_bulk_err = 0
    skip_reasons: Counter = Counter()
    skip_samples: Dict[str, List[Dict[str, Any]]] = {}
    odds_source_counts: Counter = Counter()
    unmapped_stat_ids: Counter = Counter()
    sample_outputs: List[Dict[str, Any]] = []

    buf: List[UpdateOne] = []
    now = datetime.now(timezone.utc)

    async def _flush_buf():
        nonlocal n_written, n_bulk_err
        if not buf:
            return
        try:
            r = await db[DEST].bulk_write(buf, ordered=False)
            n_written += (r.upserted_count + r.modified_count)
        except BulkWriteError as bwe:
            wr = bwe.details or {}
            n_written += int(wr.get("nUpserted", 0)) + int(wr.get("nModified", 0))
            errs = wr.get("writeErrors") or []
            n_bulk_err += len(errs)
            if errs:
                first = errs[0]
                print(f"  ! bulk_write partial failure: code={first.get('code')} "
                          f"msg={first.get('errmsg','')[:200]}", flush=True)

    match = {"league_id": args.league,
              "game_date": {"$gte": args.start, "$lte": args.end}}
    cur = db[chosen_src].find(match, projection={"_id": 0})
    if args.limit:
        cur = cur.limit(int(args.limit))

    async for d in cur:
        n_seen += 1

        if args.debug_source and n_seen <= 5:
            print(f"[debug-source #{n_seen}] keys={sorted(d.keys())}")
            for k in ("league_id", "game_date", "event_id", "player_id",
                          "player_name", "stat_id", "stat_family", "side",
                          "line", "best_book_id", "best_book_odds",
                          "consensus_probability"):
                if k in d:
                    print(f"                  {k} = {d[k]!r}")
            if isinstance(d.get("anchor"), dict):
                print(f"                  anchor = {d['anchor']!r}")
            if isinstance(d.get("books"), list):
                print(f"                  n_books = {len(d['books'])}, "
                          f"first = {d['books'][0] if d['books'] else None!r}")

        try:
            row, reason = reshape_row(d, now)
        except Exception as row_exc:
            reason = f"row_exception:{row_exc!r}"
            row = None

        if row is None:
            skip_reasons[reason] += 1
            if reason == "no_market":
                unmapped_stat_ids[d.get("stat_id") or "<none>"] += 1
            if len(skip_samples.get(reason, [])) < 2:
                skip_samples.setdefault(reason, []).append({
                    "stat_id": d.get("stat_id"),
                    "stat_family": d.get("stat_family"),
                    "side": d.get("side"),
                    "line": d.get("line"),
                    "player_name": d.get("player_name"),
                    "best_book_id": d.get("best_book_id"),
                    "best_book_odds": d.get("best_book_odds"),
                    "anchor_price": (d.get("anchor") or {}).get("price"),
                    "league_id": d.get("league_id"),
                    "game_date": d.get("game_date"),
                    "event_id": d.get("event_id"),
                })
            continue

        odds_source_counts[row["_odds_source"]] += 1
        if len(sample_outputs) < 3:
            sample_outputs.append(row)

        flt = {k: row[k] for k in
                ("sport", "game_date", "event_id", "player_name_normalized",
                  "market", "line", "side", "book", "snapshot_iso")}
        buf.append(UpdateOne(flt, {"$set": row}, upsert=True))
        if len(buf) >= 1000:
            await _flush_buf()
            buf = []

    if buf:
        await _flush_buf()

    # ── POST-RUN VERIFICATION ───────────────────────────────────────────
    n_dest = await db[DEST].count_documents({
        "sport": "mlb",
        "game_date": {"$gte": args.start, "$lte": args.end},
    })

    print()
    print("─" * 72)
    print(f"  source scanned       {n_seen}")
    print(f"  rows written         {n_written}")
    print(f"  bulk write errors    {n_bulk_err}")
    print(f"  destination rows now {n_dest}  "
              f"(sport='mlb', game_date in window)")
    print()
    if skip_reasons:
        print("  SKIPPED rows by reason:")
        for reason, cnt in skip_reasons.most_common():
            print(f"    {reason:.<28}  {cnt}")
        for reason, samples in skip_samples.items():
            print(f"    sample [{reason}]:")
            for s in samples:
                print(f"      {json.dumps(s, default=str)[:300]}")
    if unmapped_stat_ids:
        print()
        print("  UNMAPPED stat_id counts (top 20):")
        for sid, cnt in unmapped_stat_ids.most_common(20):
            print(f"    {sid!r:.<40}  {cnt}")
        print("  → Add these to _STAT_ID_TO_MARKET in reshape_sgo_to_replay_odds.py")
    if odds_source_counts:
        print()
        print("  ODDS source distribution:")
        for src, cnt in odds_source_counts.most_common():
            print(f"    {src:.<28}  {cnt}")
    if sample_outputs:
        print()
        print("  SAMPLE output rows:")
        for i, r in enumerate(sample_outputs):
            r2 = {k: v for k, v in r.items() if not isinstance(v, datetime)}
            print(f"    [{i}] {json.dumps(r2, default=str)[:500]}")
    print("─" * 72)
    print(f"  source = {chosen_src}")
    print(f"  → {DEST}")
    return 0


def _parse():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="MLB")
    p.add_argument("--start", required=True)
    p.add_argument("--end",   required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--source", default=None,
                      choices=[SRC_ENRICHED, SRC_CORE],
                      help="Force a specific source collection. Default is "
                              "enriched with automatic fallback to core.")
    p.add_argument("--debug-source", action="store_true",
                      help="Print the first 5 source docs' field listings.")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
