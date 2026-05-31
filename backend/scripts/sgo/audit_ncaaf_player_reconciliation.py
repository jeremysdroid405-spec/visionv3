"""
audit_ncaaf_player_reconciliation.py — read-only audit for NCAAF player_id
reconciliation between `ncaaf_player_historical_props` and
`sgo_player_stats`.

PURPOSE
    The pipeline diagnostic showed 41.19% of NCAAF unresolved outcomes
    are `player_not_in_results` rows whose props.player_id is NOT in
    `sgo_player_stats` at all (vs DNP rows where the pid IS in stats
    but didn't play that game). This audit estimates how many of those
    ID-mismatched rows are recoverable via name reconciliation and
    breaks the recovery down by matching strategy.

FIVE STRATEGIES (each applied to the UNMATCHED remainder from the prior)
    1. Exact player_name match
    2. Normalized player_name match  (lowercase + alphanumerics only)
    3. Normalized name + same team_id
    4. Normalized name + ≥1 shared game_date  (same player playing
       in the exact game we have stats for)
    5. Fuzzy normalized player_name  (difflib SequenceMatcher ≥ 0.90)

NAME EXTRACTION (props side)
    SGO encodes player names INTO the player_id itself using the
    pattern `PLAYER_NAME_<NUM>_<LEAGUE>` (e.g. `CALEB_WILLIAMS_1_NCAAF`).
    The reshape script already relies on this when synthesizing names
    for sgo_players. We reuse the same extraction rule.

CONSTRAINTS
    • Read-only. No writes. No drops. No index changes.
    • Scoped to NCAAF — `league_id="NCAAF"` / `league="NCAAF"` everywhere.
      MLB / NBA / NFL data is never touched.
    • Recoverability estimate is a *cross-join* count, not a guarantee.
      Some matched ids will still be UNRESOLVED post-reconciliation
      (true DNP for that game). The audit reports both upper-bound
      (all unresolved rows for the pid) and tight-bound (only rows
      whose game_date appears in the matched stats player's game list).

USAGE
    python -m scripts.sgo.audit_ncaaf_player_reconciliation

    # Tighten fuzzy threshold:
    python -m scripts.sgo.audit_ncaaf_player_reconciliation \
        --fuzzy-threshold 0.93

    # Dump a CSV of all proposed mappings for review:
    python -m scripts.sgo.audit_ncaaf_player_reconciliation \
        --out-csv /tmp/ncaaf_reconciliation_proposals.csv

OUTPUT
    Prints a multi-section report to stdout. Writes nothing to Mongo.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

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
MATCHUPS_COLL = "ncaaf_matchups"


# ──────────────────────────── name helpers ────────────────────────────
# SGO encodes player names into the player_id: "JOHN_SMITH_1_NCAAF"
_SGO_PID_TAIL = re.compile(r"_\d+_[A-Z]+$")

def name_from_pid(pid: str) -> str:
    """Extract a readable player name from an SGO player_id.

    "CALEB_WILLIAMS_1_NCAAF"  →  "Caleb Williams"
    "BIJAN_ROBINSON_2_NCAAF"  →  "Bijan Robinson"
    "X_3_NCAAF"               →  "X"
    """
    if not pid:
        return ""
    base = _SGO_PID_TAIL.sub("", pid)
    return base.replace("_", " ").title()


_NORM = re.compile(r"[^a-z0-9]+")

def normalize(name: str) -> str:
    """Lowercase + strip every non-alphanumeric character. Stable key
    for matching across formatting drift ("J.D. Spielman" vs "JD
    Spielman" vs "J D Spielman")."""
    return _NORM.sub("", (name or "").lower())


def fuzzy_ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalized names. 1.0 = identical."""
    return SequenceMatcher(None, a, b).ratio()


# ──────────────────────────────── data ────────────────────────────────
class _Candidates:
    """Indexed view of sgo_player_stats for fast lookups."""
    def __init__(self) -> None:
        self.by_exact_name: Dict[str, List[str]] = defaultdict(list)
        self.by_norm_name:  Dict[str, List[str]] = defaultdict(list)
        # pid → metadata
        self.meta: Dict[str, Dict[str, Any]] = {}
        # team_id → set(pid)
        self.by_team: Dict[Any, Set[str]] = defaultdict(set)
        # (norm_name, team_id) → list of pid
        self.by_norm_team: Dict[Tuple[str, Any], List[str]] = defaultdict(list)
        # pid → set(game_date)  (strings)
        self.dates_by_pid: Dict[str, Set[str]] = defaultdict(set)


async def load_stats_candidates(
    db: AsyncIOMotorDatabase,
) -> _Candidates:
    """Build the indexed candidate pool from sgo_player_stats (NCAAF)."""
    cand = _Candidates()
    cursor = db[STATS_COLL].find(
        {"league_id": LEAGUE},
        {"_id": 0, "player_id": 1, "player_name": 1,
         "team_id": 1, "game_date": 1}
    ).batch_size(5000)
    async for d in cursor:
        pid = d.get("player_id")
        if not pid:
            continue
        nm = (d.get("player_name") or "").strip()
        team = d.get("team_id")
        gd = d.get("game_date")
        if pid not in cand.meta:
            cand.meta[pid] = {
                "player_name": nm,
                "team_id":     team,
                "norm":        normalize(nm),
            }
            if nm:
                cand.by_exact_name[nm].append(pid)
                cand.by_norm_name[normalize(nm)].append(pid)
            if team is not None:
                cand.by_team[team].add(pid)
                cand.by_norm_team[(normalize(nm), team)].append(pid)
        # Always track game_dates (multiple rows per player)
        if gd:
            cand.dates_by_pid[pid].add(gd)
        # Per-game team_id may differ (transfer mid-season) — index extra
        if team is not None:
            cand.by_team[team].add(pid)
    return cand


async def load_mismatch_universe(
    db: AsyncIOMotorDatabase,
    stats_pids: Set[str],
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """Walk sgo_ncaaf_research_outcomes and identify props.player_ids
    that are unresolved BECAUSE the pid is not in sgo_player_stats.

    Returns:
        {props_pid → {n_unresolved_rows, game_dates, sample_team_ids,
                      extracted_name}}
        plus the total unresolved-rows count over all those pids.
    """
    out: Dict[str, Dict[str, Any]] = {}
    total_rows = 0
    cursor = db[OUTCOMES_COLL].find(
        {"outcome_resolved": False,
         "unresolved_reason_detail": "player_not_in_results"},
        {"_id": 0, "player_id": 1, "player_name": 1,
         "game_date": 1, "team_id": 1, "stat_family": 1}
    ).batch_size(5000)
    async for d in cursor:
        pid = d.get("player_id")
        if not pid or pid in stats_pids:
            # pid is in stats → DNP-style, NOT an ID-mismatch row;
            # outside our recovery universe.
            continue
        total_rows += 1
        slot = out.get(pid)
        if slot is None:
            slot = {
                "n_rows":     0,
                "game_dates": set(),
                "team_ids":   set(),
                "name_from_pid":  name_from_pid(pid),
                "name_from_row":  d.get("player_name") or "",
                "fams":       set(),
            }
            out[pid] = slot
        slot["n_rows"] += 1
        gd = d.get("game_date")
        if gd:
            slot["game_dates"].add(gd)
        tid = d.get("team_id")
        if tid is not None:
            slot["team_ids"].add(tid)
        fam = d.get("stat_family")
        if fam:
            slot["fams"].add(fam)
    return out, total_rows


# ──────────────────────────── strategies ──────────────────────────────
def strategy_1_exact(unresolved: Dict[str, Dict[str, Any]],
                      cand: _Candidates,
                      already_matched: Dict[str, str]) -> int:
    """Strategy 1: exact player_name string (props extracted) == stats
    player_name string."""
    n = 0
    for pid, info in unresolved.items():
        if pid in already_matched:
            continue
        nm = info["name_from_pid"]
        if not nm:
            continue
        hits = cand.by_exact_name.get(nm)
        if hits and len(hits) == 1:
            already_matched[pid] = hits[0]
            n += 1
    return n


def strategy_2_normalized(unresolved: Dict[str, Dict[str, Any]],
                           cand: _Candidates,
                           already_matched: Dict[str, str]) -> int:
    """Strategy 2: normalized name match (lowercased alphanumerics)."""
    n = 0
    for pid, info in unresolved.items():
        if pid in already_matched:
            continue
        norm = normalize(info["name_from_pid"])
        if not norm:
            continue
        hits = cand.by_norm_name.get(norm)
        if hits and len(hits) == 1:
            already_matched[pid] = hits[0]
            n += 1
    return n


def strategy_3_name_team(unresolved: Dict[str, Dict[str, Any]],
                          cand: _Candidates,
                          already_matched: Dict[str, str]) -> int:
    """Strategy 3: normalized name + at least one shared team_id."""
    n = 0
    for pid, info in unresolved.items():
        if pid in already_matched:
            continue
        norm = normalize(info["name_from_pid"])
        if not norm or not info["team_ids"]:
            continue
        # Try (norm, team) for every team_id this player appeared with
        for tid in info["team_ids"]:
            hits = cand.by_norm_team.get((norm, tid))
            if hits and len(hits) == 1:
                already_matched[pid] = hits[0]
                n += 1
                break
        else:
            # Multi-hit on (norm, team) — try uniqueness inside the team
            for tid in info["team_ids"]:
                hits = cand.by_norm_team.get((norm, tid), [])
                if len(hits) == 1:
                    already_matched[pid] = hits[0]
                    n += 1
                    break
    return n


def strategy_4_name_date(unresolved: Dict[str, Dict[str, Any]],
                          cand: _Candidates,
                          already_matched: Dict[str, str]) -> int:
    """Strategy 4: normalized name + ≥1 shared game_date with the
    candidate (props had the same player playing the same game).
    Reuses by_norm_name to scope; then filters by date overlap."""
    n = 0
    for pid, info in unresolved.items():
        if pid in already_matched:
            continue
        norm = normalize(info["name_from_pid"])
        if not norm or not info["game_dates"]:
            continue
        candidates = cand.by_norm_name.get(norm) or []
        # Filter by date intersection
        date_matches = [
            c for c in candidates
            if cand.dates_by_pid.get(c, set()) & info["game_dates"]
        ]
        if len(date_matches) == 1:
            already_matched[pid] = date_matches[0]
            n += 1
    return n


def strategy_5_fuzzy(unresolved: Dict[str, Dict[str, Any]],
                      cand: _Candidates,
                      already_matched: Dict[str, str],
                      threshold: float) -> int:
    """Strategy 5: fuzzy match on normalized names. Scans all
    candidate normalized names → expensive; bucketed by first
    character for cheap pruning."""
    bucketed: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for nm, pids in cand.by_norm_name.items():
        if pids:
            bucketed[nm[:1]].append((nm, pids[0]))

    n = 0
    for pid, info in unresolved.items():
        if pid in already_matched:
            continue
        target = normalize(info["name_from_pid"])
        if len(target) < 4:    # too short → fuzzy is noise
            continue
        bucket = bucketed.get(target[:1], [])
        best: Optional[Tuple[float, str]] = None
        for cnorm, cpid in bucket:
            r = fuzzy_ratio(target, cnorm)
            if r >= threshold and (best is None or r > best[0]):
                best = (r, cpid)
        if best is not None:
            already_matched[pid] = best[1]
            n += 1
    return n


# ──────────────────────────── reporting ───────────────────────────────
def _h(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _pct(a: int, b: int) -> float:
    return (100.0 * a / b) if b else 0.0


def compute_recovery(
    mapping: Dict[str, str],
    unresolved: Dict[str, Dict[str, Any]],
    cand: _Candidates,
) -> Tuple[int, int]:
    """Returns (upper_bound, tight_bound) recoverable outcome rows.

    upper_bound = sum of all unresolved rows for the matched props_pids
                  (assumes EVERY unresolved row can be regraded once we
                   reroute the pid).
    tight_bound = sum of unresolved rows whose game_date appears in
                  the matched stats player's game list (the player
                  actually played that day according to stats).
    """
    upper = 0
    tight = 0
    for props_pid, stats_pid in mapping.items():
        info = unresolved[props_pid]
        n_rows = info["n_rows"]
        upper += n_rows
        if not info["game_dates"]:
            continue
        played = cand.dates_by_pid.get(stats_pid, set())
        # Tight bound: count fraction of rows whose game_date is in
        # the stats player's game list. We don't have per-row counts
        # by date here, but we have date-set overlap as a proxy.
        if played:
            overlap_dates = info["game_dates"] & played
            # Assume rows distributed uniformly across game_dates this
            # pid appeared on. (Cheap; for an audit, this is fine.)
            if info["game_dates"]:
                frac = len(overlap_dates) / len(info["game_dates"])
                tight += int(round(n_rows * frac))
    return upper, tight


# ──────────────────────────────── main ──────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] audit_ncaaf_player_reconciliation")
        print(f"  league={LEAGUE}  fuzzy_threshold={args.fuzzy_threshold}")
        print(f"  outcomes={OUTCOMES_COLL}  stats={STATS_COLL}  props={PROPS_COLL}")
        print("  READ-ONLY. No writes will be performed.")

        # ── Load candidate pool ─────────────────────────────────────
        print("\n  [load] sgo_player_stats candidates (NCAAF)…")
        cand = await load_stats_candidates(db)
        print(f"  [load] candidates: {len(cand.meta):,} players  "
              f"({sum(len(d) for d in cand.dates_by_pid.values()):,} player-game rows)")

        # ── Identify mismatched props pids and their outcome footprint
        print(f"  [load] mismatched-pid universe from {OUTCOMES_COLL}…")
        stats_pid_set = set(cand.meta.keys())
        unresolved, total_unresolved_rows = await load_mismatch_universe(
            db, stats_pid_set)
        print(f"  [load] mismatched props pids: {len(unresolved):,}")
        print(f"  [load] total unresolved outcome rows attached: "
              f"{total_unresolved_rows:,}")

        if not unresolved:
            print("\n  No ID-mismatched unresolved rows found. Nothing to "
                  "reconcile.")
            return 0

        # ── §A — Universe ──────────────────────────────────────────
        _h("§A  ID-MISMATCH UNIVERSE")
        print(f"  distinct mismatched props.player_ids:  "
              f"{len(unresolved):,}")
        print(f"  total unresolved outcome rows:          "
              f"{total_unresolved_rows:,}")
        fams = Counter()
        for info in unresolved.values():
            for f in info["fams"]:
                fams[f] += 1
        if fams:
            print("\n  Top stat_families in the mismatch universe:")
            for fam, n in fams.most_common(10):
                print(f"    {fam[:32]:<32s} {n:>6,} mismatched pids")

        # ── §B — Candidate pool ────────────────────────────────────
        _h("§B  CANDIDATE POOL (sgo_player_stats, NCAAF)")
        print(f"  distinct candidate pids:        {len(cand.meta):,}")
        print(f"  distinct normalized names:      "
              f"{len(cand.by_norm_name):,}")
        print(f"  distinct (norm_name, team_id):  "
              f"{len(cand.by_norm_team):,}")

        # ── §C — Strategy progression ──────────────────────────────
        _h("§C  STRATEGY PROGRESSION (later strategies skip already-matched)")
        mapping: Dict[str, str] = {}
        strategy_results: List[Tuple[str, int, int, int]] = []
        strategies = [
            ("1. exact player_name",
              lambda: strategy_1_exact(unresolved, cand, mapping)),
            ("2. normalized player_name",
              lambda: strategy_2_normalized(unresolved, cand, mapping)),
            ("3. normalized name + team",
              lambda: strategy_3_name_team(unresolved, cand, mapping)),
            ("4. normalized name + shared game_date",
              lambda: strategy_4_name_date(unresolved, cand, mapping)),
            (f"5. fuzzy normalized name (≥ {args.fuzzy_threshold:.2f})",
              lambda: strategy_5_fuzzy(unresolved, cand, mapping,
                                         args.fuzzy_threshold)),
        ]
        print(f"  {'strategy':<48s} {'new':>7s} {'cum':>7s} "
              f"{'+rows ub':>9s} {'+rows tb':>9s}")
        print(f"  {'-'*48} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")
        prev_upper = 0
        prev_tight = 0
        for label, fn in strategies:
            new = fn()
            upper, tight = compute_recovery(mapping, unresolved, cand)
            print(f"  {label:<48s} {new:>7,} {len(mapping):>7,} "
                  f"{upper - prev_upper:>9,} {tight - prev_tight:>9,}")
            strategy_results.append(
                (label, new, len(mapping), upper - prev_upper))
            prev_upper, prev_tight = upper, tight

        # ── §D — Unrecoverable ─────────────────────────────────────
        _h("§D  UNRECOVERABLE (no match under any strategy)")
        unmatched_pids = [p for p in unresolved if p not in mapping]
        unmatched_rows = sum(unresolved[p]["n_rows"] for p in unmatched_pids)
        print(f"  unmatched mismatched pids:    {len(unmatched_pids):,}  "
              f"({_pct(len(unmatched_pids), len(unresolved)):.2f}% of universe)")
        print(f"  attached outcome rows lost:    {unmatched_rows:,}  "
              f"({_pct(unmatched_rows, total_unresolved_rows):.2f}% of "
              f"mismatch volume)")
        sample = sorted(
            unmatched_pids,
            key=lambda p: -unresolved[p]["n_rows"])[:15]
        if sample:
            print("\n  Top 15 unmatched pids by outcome-row volume:")
            print(f"    {'props_pid':<48s} {'name(extracted)':<24s} "
                  f"{'rows':>6s}")
            print(f"    {'-'*48} {'-'*24} {'-'*6}")
            for p in sample:
                info = unresolved[p]
                print(f"    {p[:48]:<48s} "
                      f"{info['name_from_pid'][:24]:<24s} "
                      f"{info['n_rows']:>6,}")

        # ── §E — Recovery estimate ─────────────────────────────────
        _h("§E  TOTAL RECOVERY ESTIMATE")
        upper, tight = compute_recovery(mapping, unresolved, cand)
        print(f"  Currently unresolved (ID-mismatch only):  "
              f"{total_unresolved_rows:,}")
        print(f"  Mapped via reconciliation:                "
              f"{len(mapping):,}  pids")
        print(f"  Recoverable outcome rows  UPPER BOUND:    "
              f"{upper:,}  ({_pct(upper, total_unresolved_rows):.2f}%)")
        print(f"  Recoverable outcome rows  TIGHT BOUND:    "
              f"{tight:,}  ({_pct(tight, total_unresolved_rows):.2f}%)")
        print(f"  Remaining unresolved (no name match):     "
              f"{unmatched_rows:,}  "
              f"({_pct(unmatched_rows, total_unresolved_rows):.2f}%)")

        print("\n  Interpretation:")
        print("    UPPER BOUND assumes every unresolved row attached to a")
        print("    successfully-mapped pid will resolve once we reroute.")
        print("    TIGHT BOUND restricts to rows whose game_date appears")
        print("    in the matched stats player's game list (player actually")
        print("    played that day). True post-fix recovery is between the")
        print("    two bounds, closer to TIGHT for tier-1 strategies (1-3)")
        print("    and closer to UPPER for tier-2/3 strategies (4-5).")

        # ── Optional CSV export ─────────────────────────────────────
        if args.out_csv:
            with open(args.out_csv, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "props_player_id", "extracted_name",
                    "matched_stats_player_id", "matched_stats_name",
                    "matched_stats_team_id", "n_unresolved_rows",
                    "n_game_dates", "n_team_ids", "stat_families",
                ])
                for ppid, info in unresolved.items():
                    spid = mapping.get(ppid)
                    sm = cand.meta.get(spid) if spid else None
                    w.writerow([
                        ppid, info["name_from_pid"],
                        spid or "",
                        (sm or {}).get("player_name", ""),
                        (sm or {}).get("team_id", ""),
                        info["n_rows"],
                        len(info["game_dates"]),
                        len(info["team_ids"]),
                        ",".join(sorted(info["fams"])),
                    ])
            print(f"\n  CSV proposals written: {args.out_csv}  "
                  f"({len(unresolved):,} rows)")

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        _h(f"AUDIT COMPLETE — {elapsed:.1f}s")
        print("  READ-ONLY. Zero writes performed.")
        print("  Production data is untouched.")
    finally:
        client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fuzzy-threshold", type=float, default=0.90,
                    help="SequenceMatcher ratio threshold for strategy 5 "
                          "(default 0.90). Tighten to 0.93+ for high "
                          "precision; loosen to 0.85 for higher recall.")
    p.add_argument("--out-csv", default=None,
                    help="Optional path to dump every proposed mapping "
                          "as CSV for downstream human review.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
