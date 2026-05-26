"""
build_historical_model_features.py — derive sgo_pp_research_model_features.

Reads:   sgo_pp_research_core_enriched   (immutable; SSOT for anchor + edges)
         sgo_player_stats                (immutable reference; player game logs)
Writes:  sgo_pp_research_model_features  (idempotent upserts)

For every PP-anchored enriched prop, builds a model-ready feature row using
ONLY data available BEFORE the prop's game_date (no future-data leakage).

Default feature set (per stat-family, computed from prior player game logs):
    last_3_avg, last_5_avg, last_10_avg, last_20_avg
    season_to_date_avg
    games_played_prior
    days_since_last_game
    recent_volatility       (population stdev of last 10 prior values)
    line_hit_rate_last_5    (% of last 5 games where the prop *would have hit*
    line_hit_rate_last_10    given the *current* anchor side & line)
    line_hit_rate_last_20
    line_margin_avg_last_10 (avg margin_vs_line across last 10 prior games)

Plus passthrough from the enriched source (edges + market signal):
    consensus_probability, sharp_consensus_probability, pp_implied_probability,
    edge_vs_consensus, best_book_probability, best_book_edge, devig_book_count,
    sharp_book_count, book_count, market_width, consensus_disagreement,
    has_valid_devig

feature_ready = (games_played_prior >= MIN_GAMES_REQ) and stat resolver knew
how to extract the family value.

OOM-safe:
    Chunked by game_date. For each date, builds a per-player prior-history
    cache keyed by (player_id, stat_family). Cached histories are scoped to
    a per-month sliding window to avoid loading all-time history at once.

Resumable:
    --resume skips docs already at feature_version=v1 with feature_ready=True.

Usage:
    python -m scripts.sgo.build_historical_model_features \\
        --league MLB --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.build_historical_model_features --dry-run
    python -m scripts.sgo.build_historical_model_features --drop-existing --yes
    python -m scripts.sgo.build_historical_model_features --resume

EXTENSION POINTS (for swapping in live PropVision feature logic):
    - Replace STAT_RESOLVERS with your live feature builder's stat extractors.
    - Add new features to compute_features(); they will be saved under
      `model_input_features` automatically.
    - Override FEATURE_VERSION when changing the schema.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

SRC_COLL    = "sgo_pp_research_core_enriched"
STATS_COLL  = "sgo_player_stats"
OUT_COLL    = "sgo_pp_research_model_features"
FEATURE_VERSION = "v1"
MIN_GAMES_REQ = 5  # games_played_prior threshold to mark feature_ready


# ─── reuse stat resolver registry from build_historical_outcomes ──────────
try:
    from scripts.sgo.build_historical_outcomes import (
        STAT_RESOLVERS, STAT_FAMILY, resolve_stat_value, _num)
except ImportError:
    # Fallback minimal copy if the outcomes script isn't on the path. Keep
    # this script self-sufficient when deployed alone.
    def _num(v):
        if v is None or v == "": return None
        try: return float(v)
        except (TypeError, ValueError): return None

    def _g(s, *ks):
        if not s: return None
        for k in ks:
            if k in s and s[k] is not None: return s[k]
        lm = {k.lower(): v for k, v in s.items() if isinstance(k, str)}
        for k in ks:
            v = lm.get(k.lower())
            if v is not None: return v
        return None

    def _sum_or_none(*vs):
        if any(v is None for v in vs): return None
        return float(sum(vs))

    STAT_RESOLVERS = {
        "batting_hits":   lambda s: _num(_g(s, "hits", "H")),
        "batting_runs":   lambda s: _num(_g(s, "runs", "R")),
        "batting_rbi":    lambda s: _num(_g(s, "rbi", "RBI")),
        "batting_homeRuns": lambda s: _num(_g(s, "homeRuns", "HR")),
        "batting_totalBases": lambda s: _num(_g(s, "totalBases", "TB")),
        "pitcher_strikeouts": lambda s: _num(_g(s, "pitcher_strikeouts",
                                                  "strikeouts", "SO")),
        "hits_runs_rbis": lambda s: _sum_or_none(_num(_g(s, "hits")),
                                                   _num(_g(s, "runs")),
                                                   _num(_g(s, "rbi", "RBI"))),
        "points":  lambda s: _num(_g(s, "points", "PTS")),
        "rebounds":lambda s: _num(_g(s, "rebounds", "REB")),
        "assists": lambda s: _num(_g(s, "assists", "AST")),
        "pts_reb_ast": lambda s: _sum_or_none(_num(_g(s, "points")),
                                                _num(_g(s, "rebounds")),
                                                _num(_g(s, "assists"))),
    }
    STAT_FAMILY = {k: k for k in STAT_RESOLVERS}

    def resolve_stat_value(stat_id, raw_stats):
        if raw_stats is None: return None, STAT_FAMILY.get(stat_id, stat_id)
        fn = STAT_RESOLVERS.get(stat_id)
        if fn: return fn(raw_stats), STAT_FAMILY.get(stat_id, stat_id)
        return _num(_g(raw_stats, stat_id)), STAT_FAMILY.get(stat_id, stat_id)


# ─── side/line hit logic (matches outcomes grader) ─────────────────────────
def _is_hit(side: str, value: Optional[float], line: float) -> Optional[bool]:
    if value is None: return None
    # 2026-05-21 defensive coerce — `value` and `line` are sometimes
    # written as strings by upstream ingest. Coerce here so the
    # comparison cannot raise TypeError mid-batch (which previously
    # killed entire dates of feature builds).
    try:
        v = float(value); ln = float(line)
    except (TypeError, ValueError):
        return None
    if v == ln: return None  # push doesn't count as hit/miss
    s = (side or "").upper()
    if s in ("OVER", "YES"):  return v > ln
    if s in ("UNDER", "NO"):  return v < ln
    return None


# ─── feature computation ──────────────────────────────────────────────────
def compute_features(
    *, stat_id: str, side: str, line: Optional[float],
    prior_values: List[Tuple[str, float]],  # [(game_date, value), ...] ASC
    prop_game_date: str,
) -> Tuple[Dict[str, Any], str, List[str]]:
    """Return (features_dict, stat_family_name, missing_reasons[]).

    prior_values MUST already be filtered to game_date < prop_game_date and
    sorted ascending. Values must be the family-resolved numeric (not raw stats).
    """
    fam = STAT_FAMILY.get(stat_id, stat_id or "unknown")
    missing: List[str] = []
    feats: Dict[str, Any] = {
        "stat_family":      fam,
        "games_played_prior": len(prior_values),
    }

    if not prior_values:
        missing.append("no_prior_games")
        return feats, fam, missing

    # 2026-05-21 defensive coerce — upstream `sgo_player_stats` rows
    # occasionally carry string-typed numeric stats. Anything that
    # can't be coerced is dropped silently so a single bad row doesn't
    # take down the whole date's feature build.
    _vals: List[float] = []
    for _, _v in prior_values:
        try:
            _vals.append(float(_v))
        except (TypeError, ValueError):
            continue
    vals = _vals
    if not vals:
        missing.append("no_prior_games")
        feats["games_played_prior"] = 0
        return feats, fam, missing

    def avg(window):
        sub = vals[-window:]
        return (sum(sub) / len(sub)) if sub else None

    feats["last_3_avg"]   = avg(3)
    feats["last_5_avg"]   = avg(5)
    feats["last_10_avg"]  = avg(10)
    feats["last_20_avg"]  = avg(20)
    feats["season_to_date_avg"] = sum(vals) / len(vals)

    # Volatility
    if len(vals) >= 2:
        feats["recent_volatility"] = statistics.pstdev(vals[-10:])
    else:
        feats["recent_volatility"] = None

    # Days since last game
    try:
        last_dt = datetime.strptime(prior_values[-1][0][:10], "%Y-%m-%d")
        cur_dt = datetime.strptime(prop_game_date[:10], "%Y-%m-%d")
        feats["days_since_last_game"] = (cur_dt - last_dt).days
    except Exception:
        feats["days_since_last_game"] = None

    # Line-relative hit rates (skip if line is None)
    try:
        line_f = float(line) if line is not None else None
    except (TypeError, ValueError):
        line_f = None
    if line_f is None:
        feats["line_hit_rate_last_5"]  = None
        feats["line_hit_rate_last_10"] = None
        feats["line_hit_rate_last_20"] = None
        feats["line_margin_avg_last_10"] = None
        missing.append("no_line_for_hit_rate")
    else:
        def hit_rate(window):
            sub = vals[-window:]
            if not sub: return None
            hits = [_is_hit(side, v, line_f) for v in sub]
            decided = [h for h in hits if h is not None]
            return (sum(1 for h in decided if h) / len(decided)) if decided else None

        feats["line_hit_rate_last_5"]  = hit_rate(5)
        feats["line_hit_rate_last_10"] = hit_rate(10)
        feats["line_hit_rate_last_20"] = hit_rate(20)

        sub10 = vals[-10:]
        # margin >0 = won for the prop's side
        if sub10:
            margins = []
            for v in sub10:
                if (side or "").upper() in ("OVER", "YES"):
                    margins.append(v - line_f)
                elif (side or "").upper() in ("UNDER", "NO"):
                    margins.append(line_f - v)
            feats["line_margin_avg_last_10"] = (
                sum(margins) / len(margins) if margins else None)
        else:
            feats["line_margin_avg_last_10"] = None

    if len(prior_values) < MIN_GAMES_REQ:
        missing.append(f"insufficient_history_({len(prior_values)}<{MIN_GAMES_REQ})")
    return feats, fam, missing


# ─── prior history loader (date-windowed) ─────────────────────────────────
async def load_prior_history(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    end_exclusive: str,  # exclusive upper bound = prop's game_date
    window_start: str,   # inclusive lower bound (e.g. season start)
    player_ids: List[str],
    stat_resolver: Callable[[str, Dict[str, Any]],
                              Tuple[Optional[float], str]] = resolve_stat_value,
    stat_ids: Optional[List[str]] = None,
) -> Dict[Tuple[str, str], List[Tuple[str, float]]]:
    """Returns {(player_id, stat_id): [(game_date, value), ...]} ASC.

    Only loads stats for the given player_ids and date window, computing
    family value for the requested stat_ids on the fly.
    """
    match: Dict[str, Any] = {
        "player_id": {"$in": player_ids},
        "game_date": {"$gte": window_start, "$lt": end_exclusive},
    }
    if league:
        match["league_id"] = league
    cache: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
    # 2026-05-26 — Chunk player_ids and pull each chunk with an
    # explicit large batch_size. WHY:
    #   1. Motor's default batch_size=101 turns a 2-3 M row history
    #      pull (typical for an MLB date with 180-day lookback) into
    #      ~25 000 network round-trips. At ~5 ms RTT that's >2 min of
    #      pure latency PER DATE — for a 180-date window it blows the
    #      7200 s worker timeout and looks like "the worker died."
    #   2. A single `$in` over 300+ player_ids forces Mongo to
    #      OR-merge that many index scans into one result stream;
    #      chunking lets each scan return faster and lets us page
    #      memory pressure (`stats` blobs are large).
    CHUNK = int(os.environ.get("BHMF_PID_CHUNK", "150"))
    BATCH = int(os.environ.get("BHMF_BATCH_SIZE", "5000"))
    pid_chunks = [player_ids[i:i + CHUNK] for i in range(0, len(player_ids), CHUNK)]
    for chunk in pid_chunks:
        m = dict(match, **{"player_id": {"$in": chunk}})
        cursor = db[STATS_COLL].find(
            m, {"_id": 0, "player_id": 1, "game_date": 1, "stats": 1}
        ).sort([("game_date", ASCENDING)]).batch_size(BATCH)
        async for row in cursor:
            pid = row.get("player_id")
            gd  = row.get("game_date")
            stats = row.get("stats") or {}
            if not pid or not gd:
                continue
            for sid in (stat_ids or []):
                res = stat_resolver(sid, stats)
                # Backward-compatible unpacking: resolver may return (val, fam)
                # or (val, fam, reason) depending on version.
                if isinstance(res, tuple) and len(res) >= 1:
                    val = res[0]
                else:
                    val = None
                if val is None:
                    continue
                cache.setdefault((pid, sid), []).append((gd, val))
    return cache


# ─── indexes ───────────────────────────────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db[OUT_COLL]
    await c.create_index(
        [("event_id", ASCENDING), ("player_id", ASCENDING),
         ("stat_id", ASCENDING), ("side", ASCENDING),
         ("line", ASCENDING), ("period_id", ASCENDING)],
        unique=True, name="feature_anchor_pk")
    await c.create_index("league_id")
    await c.create_index("game_date")
    await c.create_index("player_id")
    await c.create_index("stat_id")
    await c.create_index("stat_family")
    await c.create_index("feature_ready")
    await c.create_index("feature_version")
    await c.create_index("edge_vs_consensus")
    await c.create_index("has_valid_devig")


# ─── per-date processing ──────────────────────────────────────────────────
async def _distinct_game_dates(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    start: Optional[str], end: Optional[str],
) -> List[str]:
    match: Dict[str, Any] = {}
    if league: match["league_id"] = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    pipeline: List[Dict[str, Any]] = []
    if match: pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": "$game_date"}})
    pipeline.append({"$sort": {"_id": 1}})
    dates: List[str] = []
    async for r in db[SRC_COLL].aggregate(pipeline, allowDiskUse=True):
        if r.get("_id"): dates.append(r["_id"])
    return dates


def _history_window_start(prop_date: str, lookback_days: int = 90) -> str:
    """Conservative lookback window (default 180 days) for player history."""
    try:
        d = datetime.strptime(prop_date[:10], "%Y-%m-%d")
    except Exception:
        return "1970-01-01"
    from datetime import timedelta
    return (d - timedelta(days=lookback_days)).strftime("%Y-%m-%d")


async def process_date(
    db: AsyncIOMotorDatabase, *, league: Optional[str], game_date: str,
    dry_run: bool, resume: bool, lookback_days: int,
) -> Dict[str, Any]:
    src_match: Dict[str, Any] = {"game_date": game_date}
    if league: src_match["league_id"] = league

    # Collect all anchors for date + the set of (player, stat) pairs we need
    anchors: List[Dict[str, Any]] = []
    needed_players: set = set()
    needed_stats: set = set()
    # 2026-05-26 — explicit batch_size on the anchors find. The
    # default 101 docs/batch turns a date-with-5k-props pull into ~50
    # network round-trips. With batch_size 5 000 it's one. Same
    # rationale as the history loader (see load_prior_history).
    async for d in db[SRC_COLL].find(src_match, {"_id": 0}).batch_size(5000):
        anchors.append(d)
        if d.get("player_id"): needed_players.add(d["player_id"])
        if d.get("stat_id"):   needed_stats.add(d["stat_id"])

    if not anchors:
        return {"processed": 0, "ready": 0, "not_ready": 0,
                 "skipped_resume": 0, "missing_reasons": {},
                 "sample_docs": []}

    # Resume set
    already_done: set = set()
    if resume and not dry_run:
        async for r in db[OUT_COLL].find(
            {"game_date": game_date, "feature_version": FEATURE_VERSION,
              "feature_ready": True},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                         "stat_id": 1, "side": 1, "line": 1, "period_id": 1}
        ).batch_size(5000):
            already_done.add((r.get("event_id"), r.get("player_id"),
                                r.get("stat_id"),
                                (r.get("side") or "").upper(),
                                r.get("line"), r.get("period_id")))

    # Load prior history scoped to needed players × needed stats, ending before this date
    window_start = _history_window_start(game_date, lookback_days)
    hist = await load_prior_history(
        db, league=league,
        end_exclusive=game_date,
        window_start=window_start,
        player_ids=list(needed_players),
        stat_ids=list(needed_stats),
    )

    upserts: List[UpdateOne] = []
    processed = 0
    ready = 0
    not_ready = 0
    skipped = 0
    missing_reasons: Dict[str, int] = {}
    sample_docs: List[Dict[str, Any]] = []

    for doc in anchors:
        processed += 1
        uid = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               (doc.get("side") or "").upper(), doc.get("line"),
               doc.get("period_id"))
        if uid in already_done:
            skipped += 1
            continue
        prior = hist.get((doc.get("player_id"), doc.get("stat_id")), [])
        feats, fam, miss = compute_features(
            stat_id=doc.get("stat_id"), side=doc.get("side") or "",
            line=doc.get("line"),
            prior_values=prior, prop_game_date=game_date)
        for r in miss:
            missing_reasons[r] = missing_reasons.get(r, 0) + 1

        # Feature-ready means: enough history AND resolver knew this stat
        is_ready = (feats.get("games_played_prior") or 0) >= MIN_GAMES_REQ
        if not is_ready: not_ready += 1
        else:            ready += 1

        # Passthrough fields from enriched source
        passthrough = {
            k: doc.get(k) for k in (
                "league_id", "sport_id", "player_name",
                "consensus_probability", "sharp_consensus_probability",
                "pp_implied_probability", "edge_vs_consensus",
                "best_book_probability", "best_book_id", "best_book_edge",
                "devig_book_count", "sharp_book_count", "book_count",
                "market_width", "consensus_disagreement",
                "has_valid_devig", "enrichment_version",
            ) if k in doc
        }

        merged = {
            "event_id":      doc.get("event_id"),
            "league_id":     doc.get("league_id"),
            "game_date":     doc.get("game_date"),
            "player_id":     doc.get("player_id"),
            "stat_id":       doc.get("stat_id"),
            "stat_family":   fam,
            "side":          doc.get("side"),
            "line":          doc.get("line"),
            "period_id":     doc.get("period_id"),
            **passthrough,
            "model_input_features": feats,
            "feature_ready":        is_ready,
            "missing_reasons":      miss,
            "feature_version":      FEATURE_VERSION,
            "features_built_at":    datetime.now(timezone.utc),
        }
        if is_ready and len(sample_docs) < 2:
            sample_docs.append(merged)

        filt = {
            "event_id":  merged["event_id"],
            "player_id": merged["player_id"],
            "stat_id":   merged["stat_id"],
            "side":      merged["side"],
            "line":      merged["line"],
            "period_id": merged["period_id"],
        }
        upserts.append(UpdateOne(filt, {"$set": merged}, upsert=True))
        if len(upserts) >= 1000 and not dry_run:
            await db[OUT_COLL].bulk_write(upserts, ordered=False)
            upserts = []

    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "processed":       processed,
        "ready":           ready,
        "not_ready":       not_ready,
        "skipped_resume":  skipped,
        "missing_reasons": missing_reasons,
        "sample_docs":     sample_docs,
    }


# ─── main ─────────────────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    # 2026-05-26 — Wrap entire body in try/finally so the client
    # ALWAYS closes — including on unhandled exceptions. Previously
    # the script had 3 explicit `client.close()` calls in separate
    # branches, but any exception outside those branches would leak.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        return await _amain_body(args, client)
    finally:
        client.close()


async def _amain_body(args: argparse.Namespace, client) -> int:
    db = client[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_historical_model_features (version={FEATURE_VERSION})")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  drop={args.drop_existing}  resume={args.resume}  "
          f"lookback_days={args.lookback_days}")

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes (or --dry-run).")
            return 2
        if not args.dry_run:
            existing = await db[OUT_COLL].count_documents({})
            print(f"  [drop] {OUT_COLL} has {existing} docs — dropping")
            await db[OUT_COLL].drop()
        else:
            print(f"  [drop] dry-run: would have dropped {OUT_COLL}")

    await ensure_out_indexes(db)

    dates = await _distinct_game_dates(
        db, league=args.league, start=args.start, end=args.end)
    if not dates:
        print(f"  [err] no anchor docs in {SRC_COLL} for window")
        return 1
    print(f"  [plan] {len(dates)} game_dates to process  "
          f"(from {dates[0]} to {dates[-1]})")

    tot = {"dates": 0, "processed": 0, "ready": 0, "not_ready": 0,
            "skipped_resume": 0, "missing_reasons": {}, "sample_docs": []}
    log_every = 10_000
    next_log = log_every

    for gd in dates:
        try:
            r = await process_date(
                db, league=args.league, game_date=gd,
                dry_run=args.dry_run, resume=args.resume,
                lookback_days=args.lookback_days)
        except Exception as e:
            print(f"    [{gd}] FAILED: {e!r}")
            continue
        tot["dates"] += 1
        for k in ("processed", "ready", "not_ready", "skipped_resume"):
            tot[k] += r[k]
        for rkey, n in r["missing_reasons"].items():
            tot["missing_reasons"][rkey] = (
                tot["missing_reasons"].get(rkey, 0) + n)
        if r.get("sample_docs") and len(tot["sample_docs"]) < 2:
            tot["sample_docs"].extend(
                r["sample_docs"][:2 - len(tot["sample_docs"])])
        if tot["processed"] >= next_log:
            el = time.time() - t0
            rate = tot["processed"]/el if el > 0 else 0
            print(f"  [{gd}] cumulative processed={tot['processed']:,}  "
                  f"ready={tot['ready']:,}  not_ready={tot['not_ready']:,}  "
                  f"rate={rate:,.0f}/s  elapsed={el:,.0f}s")
            next_log += log_every

    runtime = time.time() - t0
    pct = lambda a, b: (100.0*a/b) if b else 0.0
    print()
    print("=" * 72)
    print(f"  build_historical_model_features SUMMARY  ({FEATURE_VERSION})")
    print("=" * 72)
    print(f"  game_dates processed:   {tot['dates']:,}")
    print(f"  docs processed:         {tot['processed']:,}")
    print(f"  feature_ready:          {tot['ready']:,}  "
          f"({pct(tot['ready'], tot['processed']):.2f}%)")
    print(f"  not_ready:              {tot['not_ready']:,}  "
          f"({pct(tot['not_ready'], tot['processed']):.2f}%)")
    print(f"  skipped (resume):       {tot['skipped_resume']:,}")
    print(f"  runtime:                {runtime:,.1f}s")

    if tot["missing_reasons"]:
        print(f"\n  Missing feature reasons (top causes):")
        for reason, n in sorted(tot["missing_reasons"].items(),
                                  key=lambda kv: -kv[1])[:15]:
            print(f"    {reason:<40s}  {n:,}")

    if tot["sample_docs"]:
        import json
        print(f"\n  Sample feature_ready docs (first {len(tot['sample_docs'])}):")
        for d in tot["sample_docs"]:
            print("    " + "─" * 60)
            print("    " + json.dumps(d, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--drop-existing", action="store_true")
    p.add_argument("--yes",    action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument(
        "--lookback-days", type=int, default=90,
        help=("Days of prior history to load per date. "
                "Default 90 — large enough to fully populate "
                "last_3 / last_5 / last_10 / last_20 features and "
                "the line-relative hit-rate windows for any everyday "
                "player, while keeping `season_to_date_avg` a "
                "meaningful ~3-month baseline. Lowered from 180 on "
                "2026-05-26 after the 180-day setting was found to "
                "balloon peak memory for `process_date` past the "
                "4 GiB worker rlimit on multi-month MLB windows. "
                "Push higher (120-180) only if you specifically need "
                "`season_to_date_avg` to span the full season."))
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
