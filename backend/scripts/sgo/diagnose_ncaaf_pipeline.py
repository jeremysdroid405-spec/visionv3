"""
diagnose_ncaaf_pipeline.py — read-only diagnostic for the NCAAF historical pipeline.

PURPOSE
    NCAAF outcomes built but feature_ready=0. This script answers the
    questions in the patch-plan brief without writing a single byte to
    the database:

      1. Player continuity — distribution of prior-games per player
                              and per (player, stat_family).
      2. Outcome resolution — rate + reasons broken down by stat_family.
      3. Player-ID audit    — props vs stats overlap + DNP-vs-mismatch
                              classification.
      4. Feature-readiness experiment — what % of anchors hit min_prior
                              thresholds 1 / 2 / 3 / 5.
      5. Market-family filter — same readiness restricted to a clean
                              core-market allowlist.

CONSTRAINTS
    • Read-only. Never writes. Never drops. Never creates indexes.
    • Scoped to NCAAF — uses `league_id="NCAAF"` (or `league="NCAAF"`)
      on every query. MLB/NBA/NFL data is never read.
    • OOM-safe. Streams from Mongo with explicit batch sizes; in-process
      structures are bounded (~ thousands of players, not props).

USAGE
    # Default — full report
    python -m scripts.sgo.diagnose_ncaaf_pipeline

    # Limit the readiness scan to N anchors for a faster sample
    python -m scripts.sgo.diagnose_ncaaf_pipeline --sample 20000

    # Show top-N players_not_in_results player_ids
    python -m scripts.sgo.diagnose_ncaaf_pipeline --sample-dnp 25
"""
from __future__ import annotations
import argparse
import asyncio
import bisect
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


LEAGUE = "NCAAF"
PROPS_COLL    = "ncaaf_player_historical_props"
STATS_COLL    = "sgo_player_stats"
OUTCOMES_COLL = "sgo_ncaaf_research_outcomes"

# "Clean core markets" — the everyday box-score families that any decent
# play-by-play feed should cover. Used in §5.
CORE_MARKETS = {
    "passing_yards", "passing_attempts", "passing_completions",
    "passing_touchdowns",
    "rushing_yards", "rushing_attempts",
    "receiving_yards", "receptions", "receiving_receptions",
}


# ──────────────────────────────── helpers ──────────────────────────────
def _pct(a: int, b: int) -> float:
    return (100.0 * a / b) if b else 0.0


def _h(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ─────────────────────── §1+§4: player history loader ─────────────────────
async def load_player_game_index(
    db: AsyncIOMotorDatabase,
) -> Dict[str, List[str]]:
    """{player_id → sorted list of game_dates from sgo_player_stats}.

    Source of truth for both player-continuity histograms and
    prior-games counting.
    """
    player_games: Dict[str, List[str]] = defaultdict(list)
    cursor = db[STATS_COLL].find(
        {"league_id": LEAGUE},
        {"_id": 0, "player_id": 1, "game_date": 1}
    ).batch_size(5000)
    async for d in cursor:
        pid = d.get("player_id")
        gd  = d.get("game_date")
        if pid and gd:
            player_games[pid].append(gd)
    for pid in player_games:
        player_games[pid].sort()
    return player_games


# ──────────────────────────── §1 ──────────────────────────────────────
def report_player_continuity(
    player_games: Dict[str, List[str]],
    outcomes_fam: Dict[Tuple[str, str], int],
) -> None:
    """§1 — Player continuity histogram + per-stat_family breakdown."""
    _h("§1  PLAYER CONTINUITY (sgo_player_stats, league=NCAAF)")
    print(f"  distinct players in sgo_player_stats: {len(player_games):,}")
    bucket = Counter()
    for pid, dates in player_games.items():
        n = len(dates)
        if n <= 4: bucket[n] += 1
        else:      bucket["5+"] += 1
    total = sum(bucket.values())
    print("\n  games_in_stats   players       pct")
    print("  ──────────────   ───────       ───")
    for k in (1, 2, 3, 4, "5+"):
        n = bucket.get(k, 0)
        print(f"  {str(k):>14s}   {n:>7,}    {_pct(n, total):5.1f}%")

    # Per-stat_family: distinct players who have AT LEAST one outcome row
    # in that family AND have at least 1, 2, 3, 5 prior games in stats.
    fam_to_players: Dict[str, set] = defaultdict(set)
    for (pid, fam), _ in outcomes_fam.items():
        fam_to_players[fam].add(pid)
    print("\n  Per-stat_family — distinct players who appear in outcomes:")
    print(f"  {'stat_family':<28s} {'players':>10s} {'≥1 game':>10s} "
          f"{'≥3 games':>10s} {'≥5 games':>10s}")
    print(f"  {'-'*28} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for fam in sorted(fam_to_players.keys(),
                        key=lambda f: -len(fam_to_players[f]))[:20]:
        pset = fam_to_players[fam]
        ge1 = sum(1 for p in pset if len(player_games.get(p, [])) >= 1)
        ge3 = sum(1 for p in pset if len(player_games.get(p, [])) >= 3)
        ge5 = sum(1 for p in pset if len(player_games.get(p, [])) >= 5)
        print(f"  {fam:<28s} {len(pset):>10,} {ge1:>10,} {ge3:>10,} "
              f"{ge5:>10,}")


# ──────────────────────────── §2 ──────────────────────────────────────
async def report_outcome_resolution(
    db: AsyncIOMotorDatabase, sample_dnp: int,
) -> None:
    """§2 — Resolution rate & unresolved-reason breakdown per stat_family."""
    _h("§2  OUTCOME RESOLUTION (sgo_ncaaf_research_outcomes)")
    # 2.A — overall + per-family resolution rate
    pipeline = [
        {"$group": {
            "_id": "$stat_family",
            "n_total":         {"$sum": 1},
            "n_resolved":      {"$sum": {"$cond": ["$outcome_resolved", 1, 0]}},
            "n_unresolved":    {"$sum": {"$cond": [
                {"$eq": ["$outcome_resolved", False]}, 1, 0]}},
        }},
        {"$sort": {"n_total": -1}},
        {"$limit": 30},
    ]
    rows = await db[OUTCOMES_COLL].aggregate(
        pipeline, allowDiskUse=True).to_list(length=None)
    grand_total = sum(r["n_total"] for r in rows)
    grand_resolved = sum(r["n_resolved"] for r in rows)
    print(f"  total outcomes      : {grand_total:,}")
    print(f"  resolved (top 30 fam): {grand_resolved:,}  "
          f"({_pct(grand_resolved, grand_total):.2f}%)")
    print("\n  Per-stat_family resolution (top 30 by volume):")
    print(f"  {'stat_family':<32s} {'total':>9s} {'resolved':>9s} "
          f"{'unres.':>9s} {'rate':>7s}")
    print(f"  {'-'*32} {'-'*9} {'-'*9} {'-'*9} {'-'*7}")
    for r in rows:
        fam = r["_id"] or "(null)"
        tot = r["n_total"]; res = r["n_resolved"]
        print(f"  {fam[:32]:<32s} {tot:>9,} {res:>9,} "
              f"{tot-res:>9,} {_pct(res, tot):>6.2f}%")

    # 2.B — unresolved reasons globally
    rows = await db[OUTCOMES_COLL].aggregate([
        {"$match": {"outcome_resolved": False}},
        {"$group": {"_id": "$unresolved_reason_detail",
                    "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ], allowDiskUse=True).to_list(length=None)
    total_u = sum(r["n"] for r in rows)
    print("\n  Unresolved-reason distribution (all stat_families):")
    print(f"  {'reason':<48s} {'n':>10s} {'pct':>7s}")
    print(f"  {'-'*48} {'-'*10} {'-'*7}")
    for r in rows:
        reason = r["_id"] or "(null)"
        print(f"  {reason[:48]:<48s} {r['n']:>10,} "
              f"{_pct(r['n'], total_u):>6.2f}%")

    # 2.C — sample of `player_not_in_results` rows
    print(f"\n  Sample `player_not_in_results` rows "
          f"(first {sample_dnp} distinct player_ids):")
    seen: set = set()
    cursor = db[OUTCOMES_COLL].find(
        {"unresolved_reason_detail": "player_not_in_results"},
        {"_id": 0, "player_id": 1, "player_name": 1,
         "event_id": 1, "game_date": 1, "stat_id": 1, "side": 1, "line": 1}
    ).limit(sample_dnp * 3)
    rows_out = []
    async for d in cursor:
        pid = d.get("player_id")
        if pid in seen: continue
        seen.add(pid)
        rows_out.append(d)
        if len(rows_out) >= sample_dnp: break
    print(f"  {'player_id':<40s} {'name':<25s} {'date':<11s} {'stat':<14s}")
    print(f"  {'-'*40} {'-'*25} {'-'*11} {'-'*14}")
    for d in rows_out:
        print(f"  {(d.get('player_id') or '')[:40]:<40s} "
              f"{(d.get('player_name') or '')[:25]:<25s} "
              f"{(d.get('game_date') or '')[:11]:<11s} "
              f"{(d.get('stat_id') or '')[:14]:<14s}")


# ──────────────────────────── §3 ──────────────────────────────────────
async def report_player_id_audit(
    db: AsyncIOMotorDatabase, player_games: Dict[str, List[str]],
) -> None:
    """§3 — Compare player_id universes; classify unresolved as DNP vs
    ID-mismatch."""
    _h("§3  PLAYER-ID AUDIT (props vs stats)")
    props_pids: set = set()
    async for r in db[PROPS_COLL].aggregate(
        [{"$match": {"league": LEAGUE}},
         {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True
    ):
        if r.get("_id"): props_pids.add(r["_id"])
    stats_pids = set(player_games.keys())
    print(f"  distinct player_ids in {PROPS_COLL}: {len(props_pids):,}")
    print(f"  distinct player_ids in {STATS_COLL} (NCAAF): "
          f"{len(stats_pids):,}")
    inter = props_pids & stats_pids
    only_props = props_pids - stats_pids
    only_stats = stats_pids - props_pids
    print(f"  intersection (overlap)             : {len(inter):,}  "
          f"({_pct(len(inter), len(props_pids)):.2f}% of props)")
    print(f"  in props but NOT in stats          : {len(only_props):,}  "
          f"({_pct(len(only_props), len(props_pids)):.2f}% of props)")
    print(f"  in stats but NOT in props          : {len(only_stats):,}")

    # Classify "player_not_in_results" unresolveds: DNP vs ID-mismatch.
    # If the pid exists in stats (just not for that event_id), the
    # player is in our universe but didn't play that game → DNP-style.
    # If the pid does NOT exist in stats at all, the ID is broken →
    # ID-mismatch (real fix is a name/id reconciliation).
    print("\n  Classify unresolved 'player_not_in_results' rows:")
    n_dnp = 0
    n_idmiss = 0
    cursor = db[OUTCOMES_COLL].find(
        {"unresolved_reason_detail": "player_not_in_results"},
        {"_id": 0, "player_id": 1}).batch_size(5000)
    async for d in cursor:
        pid = d.get("player_id")
        if not pid:
            continue
        if pid in stats_pids:
            n_dnp += 1
        else:
            n_idmiss += 1
    n_total = n_dnp + n_idmiss
    print(f"  true DNP / no-show (pid IS in stats, but not this game): "
          f"{n_dnp:,}  ({_pct(n_dnp, n_total):.2f}%)")
    print(f"  ID-mismatch (pid NOT in sgo_player_stats at all):        "
          f"{n_idmiss:,}  ({_pct(n_idmiss, n_total):.2f}%)")
    print("  → if ID-mismatch >> DNP: name-key reconciliation is the "
          "single biggest unlock.")


# ──────────────────────────── §4 + §5 ─────────────────────────────────
async def report_feature_readiness(
    db: AsyncIOMotorDatabase, player_games: Dict[str, List[str]],
    *, sample: Optional[int], market_filter: Optional[set],
    label: str,
) -> Dict[int, int]:
    """§4 / §5 — Feature-readiness experiment. For every anchor in
    sgo_ncaaf_research_outcomes, count games_played_prior (using
    bisect against the sorted per-player game list), and report the
    histogram at thresholds 1/2/3/5.

    Returns {threshold: ready_count} for caller composability.
    """
    title = f"§4  FEATURE READINESS EXPERIMENT — {label}"
    _h(title)
    if market_filter:
        print(f"  market filter: {sorted(market_filter)}")
    if sample:
        print(f"  sample cap: {sample:,} anchors")

    threshold_keys = [1, 2, 3, 5]
    ready: Counter = Counter()
    total = 0
    histo = Counter()
    # Per stat_family breakdown
    fam_total: Counter = Counter()
    fam_ready_ge5: Counter = Counter()
    fam_ready_ge3: Counter = Counter()
    fam_ready_ge1: Counter = Counter()

    match: Dict[str, Any] = {}
    if market_filter:
        match["stat_id"] = {"$in": list(market_filter)}

    cursor = db[OUTCOMES_COLL].find(
        match,
        {"_id": 0, "player_id": 1, "game_date": 1, "stat_family": 1,
         "stat_id": 1}
    ).batch_size(5000)
    if sample:
        cursor = cursor.limit(sample)
    async for d in cursor:
        pid = d.get("player_id")
        gd  = d.get("game_date")
        if not pid or not gd:
            continue
        total += 1
        games = player_games.get(pid) or []
        # games is sorted ascending — count strictly-before gd
        prior = bisect.bisect_left(games, gd)
        # Histogram bucket
        if   prior == 0:   histo["0"] += 1
        elif prior == 1:   histo["1"] += 1
        elif prior == 2:   histo["2"] += 1
        elif prior <= 4:   histo["3-4"] += 1
        else:              histo["5+"] += 1
        for t in threshold_keys:
            if prior >= t: ready[t] += 1
        fam = d.get("stat_family") or "(null)"
        fam_total[fam] += 1
        if prior >= 5: fam_ready_ge5[fam] += 1
        if prior >= 3: fam_ready_ge3[fam] += 1
        if prior >= 1: fam_ready_ge1[fam] += 1

    print(f"\n  Anchors scanned: {total:,}")
    print("\n  Prior-games histogram:")
    for k in ("0", "1", "2", "3-4", "5+"):
        n = histo.get(k, 0)
        print(f"    prior={k:>4s}    {n:>10,}  ({_pct(n, total):.2f}%)")
    print("\n  Threshold sweep (default current MIN_GAMES_REQ=5):")
    for t in threshold_keys:
        n = ready[t]
        print(f"    min_prior_games >= {t}    "
              f"{n:>10,} ready  ({_pct(n, total):.2f}%)")

    print("\n  Per-stat_family readiness (top 15 by volume):")
    print(f"  {'stat_family':<28s} {'anchors':>9s} "
          f"{'≥1':>9s} {'≥3':>9s} {'≥5':>9s}")
    print(f"  {'-'*28} {'-'*9} {'-'*9} {'-'*9} {'-'*9}")
    for fam, _ in fam_total.most_common(15):
        n = fam_total[fam]
        print(f"  {fam[:28]:<28s} {n:>9,} "
              f"{fam_ready_ge1[fam]:>9,} {fam_ready_ge3[fam]:>9,} "
              f"{fam_ready_ge5[fam]:>9,}")

    return dict(ready)


# ─────────────────────── outcomes (pid, fam) catalog ─────────────────────
async def load_outcomes_player_fam(
    db: AsyncIOMotorDatabase,
) -> Dict[Tuple[str, str], int]:
    """Lightweight catalog: {(player_id, stat_family) → n_outcomes}.
    Used by §1 to bucket players by stat_family without re-scanning."""
    out: Counter = Counter()
    cursor = db[OUTCOMES_COLL].find(
        {}, {"_id": 0, "player_id": 1, "stat_family": 1}
    ).batch_size(5000)
    async for d in cursor:
        pid = d.get("player_id")
        fam = d.get("stat_family")
        if pid and fam:
            out[(pid, fam)] += 1
    return out


# ──────────────────────────────── main ──────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] diagnose_ncaaf_pipeline")
        print(f"  league={LEAGUE}  props={PROPS_COLL}  "
              f"stats={STATS_COLL}  outcomes={OUTCOMES_COLL}")
        print(f"  sample={args.sample}  sample_dnp={args.sample_dnp}")

        # Quick row counts up front for context
        n_props = await db[PROPS_COLL].count_documents({"league": LEAGUE})
        n_stats = await db[STATS_COLL].count_documents({"league_id": LEAGUE})
        n_out   = await db[OUTCOMES_COLL].count_documents({})
        print(f"\n  {PROPS_COLL}      (NCAAF): {n_props:,}")
        print(f"  {STATS_COLL}      (NCAAF): {n_stats:,}")
        print(f"  {OUTCOMES_COLL}              : {n_out:,}")

        # Shared loads
        print("\n  Loading per-player game index from sgo_player_stats…")
        player_games = await load_player_game_index(db)
        print(f"  loaded: {len(player_games):,} distinct players")
        print("  Loading (player_id, stat_family) catalog from outcomes…")
        outcomes_fam = await load_outcomes_player_fam(db)
        print(f"  loaded: {len(outcomes_fam):,} (player, fam) pairs")

        # Reports
        report_player_continuity(player_games, outcomes_fam)
        await report_outcome_resolution(db, args.sample_dnp)
        await report_player_id_audit(db, player_games)
        await report_feature_readiness(
            db, player_games, sample=args.sample,
            market_filter=None, label="ALL stat_ids")
        await report_feature_readiness(
            db, player_games, sample=args.sample,
            market_filter=CORE_MARKETS,
            label=f"CORE markets only ({len(CORE_MARKETS)} families)")

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        _h(f"DIAGNOSTIC COMPLETE — {elapsed:.1f}s")
        print("  Read-only. Zero writes performed.")
        print("  See /app/memory/NCAAF_DIAGNOSTIC_PLAN.md for the patch plan.")
    finally:
        client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=int, default=None,
                    help="Cap anchors scanned in §4/§5 for faster sampling.")
    p.add_argument("--sample-dnp", type=int, default=20,
                    help="How many distinct DNP player_ids to display "
                          "in §2.C (default 20).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
