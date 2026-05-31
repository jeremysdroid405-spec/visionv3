"""
diagnose_ncaaf_master_coverage.py — post-ingest drill-down for NCAAF.

PURPOSE
    Run AFTER `ingest_player_master --league NCAAF` populates the
    `sgo_player_master` collection. Answers the five NCAAF-specific
    questions from the operator brief, without any DB writes.

QUESTIONS ANSWERED
    1. Are the mismatched prop player_ids present in /v2/players
       (i.e. in sgo_player_master)?
    2. What is their status distribution (active / inactive / retired /
       transferred / unknown)?
    3. Do any of them have aliases[] pointing to a stats-side player_id?
    4. Do they share (teamID, game_date) with a matched stats player?
    5. After all of the above, what residual is "prop-only player who
       never appeared in game results"?

DECISION GATE
    Final section prints a single-line recommendation:
      • RECOVERY-WORTH-PURSUING:   ≥20% of mismatch recoverable via
        master → next step is to extend the reconciliation writer.
      • PIVOT-TO-MARKET-FEATURES:  <5% recoverable → identity is a
        dead end for NCAAF; switch to market/team/opponent features.
      • MIXED:                     5-20% → partial unlock; pursue both.

CONSTRAINTS
    • Read-only. Zero writes. NCAAF-only. No SGO API calls.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


LEAGUE       = "NCAAF"
PROPS_COLL   = "ncaaf_player_historical_props"
STATS_COLL   = "sgo_player_stats"
MASTER_COLL  = "sgo_player_master"
OUTCOMES_COLL = "sgo_ncaaf_research_outcomes"


def _h(t: str) -> None:
    print()
    print("=" * 72)
    print(f"  {t}")
    print("=" * 72)


def _pct(a: int, b: int) -> float:
    return (100.0 * a / b) if b else 0.0


# ───────────────── universe loaders ─────────────────
async def load_mismatch_universe(
    db: AsyncIOMotorDatabase,
) -> Tuple[Set[str], Set[str], Set[str], Dict[str, Dict[str, Any]]]:
    """Returns (props_pids, stats_pids, mismatch_pids, per-pid metadata)."""
    stats_pids: Set[str] = set()
    async for d in db[STATS_COLL].find(
        {"league_id": LEAGUE}, {"_id": 0, "player_id": 1}
    ):
        if d.get("player_id"):
            stats_pids.add(d["player_id"])
    props_pids: Set[str] = set()
    async for r in db[PROPS_COLL].aggregate(
        [{"$match": {"league": LEAGUE}}, {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True,
    ):
        if r.get("_id"):
            props_pids.add(r["_id"])
    mismatch = props_pids - stats_pids
    # Per-pid metadata from outcomes (team_ids, game_dates, n_rows)
    info: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"team_ids": set(), "game_dates": set(),
                 "n_unresolved_rows": 0})
    async for d in db[OUTCOMES_COLL].find(
        {"unresolved_reason_detail": "player_not_in_results",
         "outcome_resolved": False},
        {"_id": 0, "player_id": 1, "team_id": 1, "game_date": 1}
    ):
        pid = d.get("player_id")
        if pid not in mismatch:
            continue
        info[pid]["n_unresolved_rows"] += 1
        if d.get("team_id"):
            info[pid]["team_ids"].add(d["team_id"])
        if d.get("game_date"):
            info[pid]["game_dates"].add(d["game_date"])
    return props_pids, stats_pids, mismatch, dict(info)


async def load_master_index(
    db: AsyncIOMotorDatabase, *, league: str,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Returns:
       master_by_pid: {player_id → master_doc (minus raw)}
       alias_to_pid:  {alias_value → player_id}  (multi-key)
    """
    by_pid: Dict[str, Dict[str, Any]] = {}
    alias_to_pid: Dict[str, str] = {}
    async for d in db[MASTER_COLL].find(
        {"league_id": league}, {"_id": 0, "raw": 0}
    ):
        pid = d.get("player_id")
        if not pid:
            continue
        by_pid[pid] = d
        for a in (d.get("aliases") or []):
            if a:
                alias_to_pid[str(a)] = pid
        # Names can also be aliases for matching purposes
        for n in (d.get("names") or []):
            if n:
                alias_to_pid.setdefault(str(n), pid)
    return by_pid, alias_to_pid


# ───────────────── §1 ─────────────────
def q1_master_presence(
    mismatch: Set[str],
    master_by_pid: Dict[str, Dict[str, Any]],
) -> Tuple[Set[str], Set[str]]:
    """Which mismatched pids are in sgo_player_master?"""
    _h("§Q1  Are the mismatched prop player_ids present in /v2/players?")
    in_master = {p for p in mismatch if p in master_by_pid}
    not_in_master = mismatch - in_master
    print(f"  mismatch universe:      {len(mismatch):,}")
    print(f"  master collection size: {len(master_by_pid):,}")
    print(f"  ── in /v2/players:      {len(in_master):,}  "
          f"({_pct(len(in_master), len(mismatch)):.2f}%)")
    print(f"  ── NOT in /v2/players:  {len(not_in_master):,}  "
          f"({_pct(len(not_in_master), len(mismatch)):.2f}%)")
    print("\n  VERDICT: " + (
        "Master CAN see most mismatched props pids — identity layer is "
        "real coverage."
        if _pct(len(in_master), len(mismatch)) >= 50 else
        "Master sees <50% of mismatches — props is quoting markets for "
        "players SGO's master itself doesn't track. Identity layer is "
        "NOT the fix."))
    return in_master, not_in_master


# ───────────────── §2 ─────────────────
def q2_status_distribution(
    in_master: Set[str],
    master_by_pid: Dict[str, Dict[str, Any]],
) -> Counter:
    """Status / position / team breakdown of the master-present mismatch."""
    _h("§Q2  Status distribution (active / inactive / retired / etc.)")
    if not in_master:
        print("  No master rows for the mismatch universe — nothing to "
              "breakdown.")
        return Counter()
    status_c: Counter = Counter()
    pos_c: Counter = Counter()
    team_present: Counter = Counter()
    for pid in in_master:
        d = master_by_pid[pid]
        status_c[(d.get("status") or "(none)").lower()] += 1
        pos_c[(d.get("position") or "(none)")] += 1
        team_present[
            "with_team" if d.get("team_id") else "no_team"] += 1
    print(f"  Status (n={len(in_master)}):")
    for k, v in status_c.most_common():
        print(f"    {k:<24s} {v:>6,}  ({_pct(v, len(in_master)):.2f}%)")
    print("\n  Position top 10:")
    for k, v in pos_c.most_common(10):
        print(f"    {k:<10s} {v:>6,}")
    print("\n  Has team_id?:")
    for k, v in team_present.most_common():
        print(f"    {k:<14s} {v:>6,}")
    return status_c


# ───────────────── §3 ─────────────────
def q3_aliases(
    in_master: Set[str],
    master_by_pid: Dict[str, Dict[str, Any]],
    stats_pids: Set[str],
) -> Tuple[Set[str], Dict[str, str]]:
    """Do any mismatched pids have aliases[] pointing to a stats-side pid?
    This is the deterministic master-FK resolution path."""
    _h("§Q3  Aliases pointing to stats player_ids (deterministic FK)")
    if not in_master:
        print("  No master rows — nothing to check.")
        return set(), {}
    mapping: Dict[str, str] = {}
    n_with_aliases = 0
    for pid in in_master:
        d = master_by_pid[pid]
        aliases = [str(a) for a in (d.get("aliases") or []) if a]
        if not aliases:
            continue
        n_with_aliases += 1
        for a in aliases:
            if a in stats_pids:
                mapping[pid] = a
                break
    resolved = set(mapping.keys())
    print(f"  master rows with non-empty aliases:    {n_with_aliases:,}  "
          f"({_pct(n_with_aliases, len(in_master)):.2f}% of "
          f"in-master mismatches)")
    print(f"  rows whose alias → known stats pid:    {len(resolved):,}  "
          f"({_pct(len(resolved), len(in_master)):.2f}% of "
          f"in-master mismatches)")
    print("\n  Sample alias-resolutions (up to 10):")
    for k, v in list(mapping.items())[:10]:
        print(f"    props.pid = {k}")
        print(f"      → alias of master row → stats.pid = {v}")
    return resolved, mapping


# ───────────────── §4 ─────────────────
async def q4_team_date_match(
    db: AsyncIOMotorDatabase,
    in_master_remaining: Set[str],
    master_by_pid: Dict[str, Dict[str, Any]],
    mismatch_info: Dict[str, Dict[str, Any]],
) -> Set[str]:
    """For mismatched pids still unresolved after §Q3: do they share a
    (teamID, game_date) tuple with any stats player? If yes, that
    stats player is the deterministic correspondent."""
    _h("§Q4  team_id + game_date overlap with stats")
    if not in_master_remaining:
        print("  No remaining unresolved master-present pids.")
        return set()
    # Build {team_id → {game_date → set(stats_pid)}}
    print("  building stats index by (team_id, game_date)…")
    stats_by_td: Dict[Tuple[Any, str], Set[str]] = defaultdict(set)
    async for d in db[STATS_COLL].find(
        {"league_id": LEAGUE},
        {"_id": 0, "player_id": 1, "team_id": 1, "game_date": 1}
    ):
        tid = d.get("team_id"); gd = d.get("game_date")
        pid = d.get("player_id")
        if tid is not None and gd and pid:
            stats_by_td[(tid, gd)].add(pid)

    resolved: Set[str] = set()
    for pid in in_master_remaining:
        info = mismatch_info.get(pid) or {}
        # Prefer the master's current team_id; fall back to outcome
        # team_ids if master shipped no team.
        master_team = master_by_pid[pid].get("team_id")
        teams = (set([master_team])
                  if master_team is not None
                  else info.get("team_ids", set()))
        dates = info.get("game_dates") or set()
        if not teams or not dates:
            continue
        # Any (team, date) tuple that has exactly ONE stats pid → match
        for t in teams:
            for d in dates:
                pool = stats_by_td.get((t, d))
                if pool and len(pool) == 1:
                    resolved.add(pid)
                    break
            if pid in resolved:
                break
    print(f"  master-present pids remaining after §Q3:  "
          f"{len(in_master_remaining):,}")
    print(f"  resolvable via (team, date) overlap:      "
          f"{len(resolved):,}  "
          f"({_pct(len(resolved), len(in_master_remaining)):.2f}%)")
    return resolved


# ───────────────── §5 ─────────────────
def q5_residual(
    mismatch: Set[str],
    in_master: Set[str],
    not_in_master: Set[str],
    resolved_alias: Set[str],
    resolved_td: Set[str],
    info: Dict[str, Dict[str, Any]],
) -> None:
    """Quantify the residual prop-only-no-game-results bucket."""
    _h("§Q5  Residual: prop-listed players who never appeared in results")
    fully_resolved = resolved_alias | resolved_td
    residual_in_master = in_master - fully_resolved
    print(f"  mismatch universe:                  {len(mismatch):,}")
    print(f"  ── in /v2/players  (§Q1):           {len(in_master):,}")
    print(f"        ├── alias-resolved (§Q3):     {len(resolved_alias):,}")
    print(f"        ├── team+date resolved (§Q4): {len(resolved_td):,}")
    print("        └── residual (in master,      ")
    print(f"             no stats correspondent): {len(residual_in_master):,}")
    print(f"  ── NOT in /v2/players (§Q1):        {len(not_in_master):,}")
    print("        these are players that even SGO's master doesn't ")
    print("        list for NCAAF — props feed is over-broadcasting.")
    # Outcome-row impact (using mismatch_info collected from outcomes)
    rows_recovered_alias = sum(
        (info.get(p) or {}).get("n_unresolved_rows", 0)
        for p in resolved_alias)
    rows_recovered_td = sum(
        (info.get(p) or {}).get("n_unresolved_rows", 0)
        for p in resolved_td)
    rows_residual = sum(
        (info.get(p) or {}).get("n_unresolved_rows", 0)
        for p in residual_in_master)
    rows_no_master = sum(
        (info.get(p) or {}).get("n_unresolved_rows", 0)
        for p in not_in_master)
    rows_total = (rows_recovered_alias + rows_recovered_td
                    + rows_residual + rows_no_master)
    print("\n  Outcome-row recovery (UPPER BOUND on grading impact):")
    print(f"    via alias FK:           {rows_recovered_alias:,}  rows")
    print(f"    via team+date overlap:  {rows_recovered_td:,}  rows")
    print(f"    residual in master:     {rows_residual:,}  rows  "
          f"(true DNP-like at master level)")
    print(f"    rows for non-master pids:{rows_no_master:,}  rows  "
          f"(prop-only ghosts)")
    print("    ──────")
    print(f"    total mismatch rows:    {rows_total:,}")

    # Decision gate
    _h("DECISION GATE")
    recovered_rows = rows_recovered_alias + rows_recovered_td
    coverage_pct = _pct(recovered_rows, rows_total) if rows_total else 0.0
    print(f"  Total recovery via deterministic identity paths: "
          f"{recovered_rows:,} rows  ({coverage_pct:.2f}%)")
    if coverage_pct >= 20:
        print("  ──>  RECOVERY-WORTH-PURSUING")
        print("        Identity layer recovers ≥20% of mismatch rows. ")
        print("        Next step: build an idempotent writer that re-")
        print("        routes props.player_id via these mappings and re-")
        print("        runs `build_historical_outcomes --league NCAAF`.")
    elif coverage_pct >= 5:
        print("  ──>  MIXED")
        print(f"        Identity unlocks {coverage_pct:.1f}% of mismatch. ")
        print("        Worthwhile, but ALSO pivot to market/team/opponent ")
        print("        features (the bulk is still residual).")
    else:
        print("  ──>  PIVOT-TO-MARKET-FEATURES")
        print("        Identity recovers <5% of mismatch rows. Stop ")
        print("        pursuing identity reconciliation. Switch to:")
        print("          • market-only features (book count, devig, ")
        print("             edge_vs_consensus, line dispersion)")
        print("          • team and opponent rolling features")
        print("          • lower history dependence (min_prior_games=3)")
        print("          • exclude DNP-heavy markets from training")


# ───────────────── main ─────────────────
async def amain(_args: argparse.Namespace) -> int:
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] diagnose_ncaaf_master_coverage")
        print("  READ-ONLY. No DB writes. No SGO API calls.")
        # Pre-flight
        n_master = await db[MASTER_COLL].count_documents({"league_id": LEAGUE})
        print(f"  {MASTER_COLL}  (NCAAF rows): {n_master:,}")
        if n_master == 0:
            print("\n  WARNING: sgo_player_master is empty for NCAAF. ")
            print("  Run first:  python -m scripts.sgo.ingest_player_master "
                  "--league NCAAF")
            return 1

        props_pids, stats_pids, mismatch, info = await load_mismatch_universe(db)
        print(f"  distinct props pids:                {len(props_pids):,}")
        print(f"  distinct stats pids (NCAAF):        {len(stats_pids):,}")
        print(f"  mismatch (props ∖ stats):           {len(mismatch):,}")
        if not mismatch:
            print("\n  No mismatches — nothing to drill into.")
            return 0

        master_by_pid, _alias_to_pid = await load_master_index(
            db, league=LEAGUE)

        in_master, not_in_master = q1_master_presence(mismatch, master_by_pid)
        q2_status_distribution(in_master, master_by_pid)
        resolved_alias, _alias_map = q3_aliases(
            in_master, master_by_pid, stats_pids)
        remaining = in_master - resolved_alias
        resolved_td = await q4_team_date_match(
            db, remaining, master_by_pid, info)
        q5_residual(mismatch, in_master, not_in_master,
                     resolved_alias, resolved_td, info)

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"\n  Runtime: {elapsed:.1f}s")
        print("  Read-only. Zero writes performed.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
