"""Replay A/B compare CLI — "Did this change help?"

Given two `replay_serial`s (from `mlb_production_replay_runs`),
diffs the resulting `mlb_production_replay_outputs` rows and produces
a unified report:

  1. HR delta
  2. ROI delta
  3. Profit / loss delta
  4. Qualified-pick count delta
  5. Overlap %  (Jaccard on the qualified pick set)
  6. Added picks   (in B but not A)
  7. Removed picks (in A but not B)
  8. Picks that switched gate-pass status / changed side / changed book
  9. Odds-bucket comparison
 10. Edge-bucket comparison
 11. Stat-family comparison
 12. Book comparison
 13. Top positive deltas  (qualified picks B-only that won)
 14. Top negative deltas  (qualified picks A-only that won, lost when B
                            dropped them)

Read-only. No schema changes. Sport-agnostic — `--sport` selects
`{sport}_production_replay_runs` and `{sport}_production_replay_outputs`.

Usage:
    python scripts/replay_compare.py --sport mlb \\
        --serial-a MLB-PRODREPLAY-20260505-WZ-1100UTC-00003 \\
        --serial-b MLB-PRODREPLAY-20260505-WZ-1100UTC-00005

Output:
    stdout — formatted comparison report
    audits/replay_compare_<serial_a>_vs_<serial_b>.json — machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient


OUT_DIR = "/app/backend/audits"


# ── Bucketing helpers ────────────────────────────────────────────────
ODDS_BUCKETS = [
    ("plus_high",   lambda o: o >= 200),
    ("plus_med",    lambda o: 100 <= o < 200),
    ("plus_low",    lambda o: 0 < o < 100),
    ("even",        lambda o: o == 100),
    ("minus_low",   lambda o: -110 < o < 0),
    ("minus_med",   lambda o: -150 < o <= -110),
    ("minus_heavy", lambda o: -250 < o <= -150),
    ("minus_xx",    lambda o: o <= -250),
]
EDGE_BUCKETS = [
    ("<5",     lambda e: e < 0.05),
    ("5-10",   lambda e: 0.05 <= e < 0.10),
    ("10-15",  lambda e: 0.10 <= e < 0.15),
    ("15-20",  lambda e: 0.15 <= e < 0.20),
    ("20-30",  lambda e: 0.20 <= e < 0.30),
    (">=30",   lambda e: e >= 0.30),
]


def _bucket(value: float, buckets: List[Tuple[str, Any]]) -> str:
    for label, pred in buckets:
        if pred(value):
            return label
    return "_unbucketed"


def _key(row: Dict[str, Any]) -> Tuple:
    """Canonical comparison key for a pick. Side intentionally
    included so OVER/UNDER on the same line are distinct."""
    return (
        row.get("player_name_normalized"),
        row.get("stat_family"),
        float(row.get("line") or 0.0),
        row.get("side"),
        row.get("event_id"),
    )


def _book_dim_key(row: Dict[str, Any]) -> Tuple:
    """Same as `_key()` but also distinguishes by book."""
    return _key(row) + (row.get("book"),)


# ── Aggregations ─────────────────────────────────────────────────────
def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One-pass aggregation over an output row set."""
    n = len(rows)
    by_grade = defaultdict(int)
    by_odds = defaultdict(lambda: {"n": 0, "stake": 0.0, "profit": 0.0, "wins": 0, "losses": 0})
    by_edge = defaultdict(lambda: {"n": 0, "stake": 0.0, "profit": 0.0, "wins": 0, "losses": 0})
    by_stat = defaultdict(lambda: {"n": 0, "stake": 0.0, "profit": 0.0, "wins": 0, "losses": 0})
    by_book = defaultdict(lambda: {"n": 0, "stake": 0.0, "profit": 0.0, "wins": 0, "losses": 0})
    qualified = 0
    stake_total = 0.0
    profit_total = 0.0
    wins = losses = pushes = ungraded = 0

    for r in rows:
        if not r.get("gate_pass"):
            continue
        qualified += 1
        st = (r.get("grade_status") or "ungraded").lower()
        by_grade[st] += 1
        stake = float(r.get("stake_units") or 0.0)
        profit = float(r.get("profit_units") or 0.0)
        stake_total += stake
        profit_total += profit
        if st == "win":   wins += 1
        elif st == "loss": losses += 1
        elif st == "push": pushes += 1
        else:              ungraded += 1

        ob = _bucket(float(r.get("odds") or 0), ODDS_BUCKETS)
        eb = _bucket(float(r.get("edge") or 0), EDGE_BUCKETS)
        sf = r.get("stat_family") or "_unknown"
        bk = r.get("book") or "_unknown"
        for d, key in ((by_odds, ob), (by_edge, eb),
                        (by_stat, sf), (by_book, bk)):
            d[key]["n"] += 1
            d[key]["stake"] += stake
            d[key]["profit"] += profit
            if st == "win":   d[key]["wins"] += 1
            elif st == "loss": d[key]["losses"] += 1

    decided = wins + losses
    hit_rate = (100.0 * wins / decided) if decided else 0.0
    roi = (100.0 * profit_total / stake_total) if stake_total else 0.0
    return {
        "n_rows": n,
        "qualified": qualified,
        "wins": wins, "losses": losses, "pushes": pushes, "ungraded": ungraded,
        "stake_units": round(stake_total, 4),
        "profit_units": round(profit_total, 4),
        "hit_rate_pct": round(hit_rate, 2),
        "roi_pct": round(roi, 2),
        "by_odds": {k: dict(v) for k, v in by_odds.items()},
        "by_edge": {k: dict(v) for k, v in by_edge.items()},
        "by_stat": {k: dict(v) for k, v in by_stat.items()},
        "by_book": {k: dict(v) for k, v in by_book.items()},
    }


def _bucket_table(label: str, a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    keys = sorted(set(a.keys()) | set(b.keys()))
    lines = [f"\n[{label}]   keys={len(keys)}",
              f"  {'bucket':>14}  {'A_n':>6}  {'B_n':>6}  {'Δn':>6}  "
              f"{'A_HR':>7}  {'B_HR':>7}  {'A_ROI':>8}  {'B_ROI':>8}  "
              f"{'Δ_profit_u':>10}"]
    for k in keys:
        av = a.get(k, {})
        bv = b.get(k, {})
        an = av.get("n", 0); bn = bv.get("n", 0)
        a_dec = av.get("wins", 0) + av.get("losses", 0)
        b_dec = bv.get("wins", 0) + bv.get("losses", 0)
        a_hr = (100.0 * av.get("wins", 0) / a_dec) if a_dec else 0.0
        b_hr = (100.0 * bv.get("wins", 0) / b_dec) if b_dec else 0.0
        a_stake = av.get("stake", 0.0); b_stake = bv.get("stake", 0.0)
        a_roi = (100.0 * av.get("profit", 0) / a_stake) if a_stake else 0.0
        b_roi = (100.0 * bv.get("profit", 0) / b_stake) if b_stake else 0.0
        delta_profit = bv.get("profit", 0) - av.get("profit", 0)
        delta_n = bn - an
        sign_n = "+" if delta_n > 0 else ""
        lines.append(
            f"  {k:>14}  {an:>6}  {bn:>6}  {sign_n}{delta_n:>5}  "
            f"{a_hr:>6.1f}%  {b_hr:>6.1f}%  {a_roi:>+7.1f}%  {b_roi:>+7.1f}%  "
            f"{delta_profit:>+10.2f}"
        )
    return lines


# ── Pick-set diff ────────────────────────────────────────────────────
def _build_pick_index(rows: List[Dict[str, Any]]) -> Dict[Tuple, Dict[str, Any]]:
    """Index qualified picks by (player, stat, line, side, event_id).
    When multiple books offer the same pick, keep the row with the
    best edge (matches the live best-book selection convention)."""
    idx: Dict[Tuple, Dict[str, Any]] = {}
    for r in rows:
        if not r.get("gate_pass"):
            continue
        k = _key(r)
        cur = idx.get(k)
        if cur is None or float(r.get("edge") or 0) > float(cur.get("edge") or 0):
            idx[k] = r
    return idx


def _pretty_pick(r: Dict[str, Any]) -> str:
    return (f"{r.get('player_name') or r.get('player_name_normalized')} | "
            f"{r.get('stat_family')} {r.get('line')}/{r.get('side')} | "
            f"{r.get('book')}@{r.get('odds')} | "
            f"μ={float(r.get('projection_mu') or 0):.2f} "
            f"edge={float(r.get('edge') or 0):.3f}")


# ── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Replay A/B compare CLI")
    p.add_argument("--sport", required=True, choices=["mlb", "nba", "nfl"])
    p.add_argument("--serial-a", required=True, dest="serial_a")
    p.add_argument("--serial-b", required=True, dest="serial_b")
    p.add_argument("--top-n", type=int, default=8,
                    help="number of added/removed/top-delta picks to print")
    args = p.parse_args()

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    runs_coll = f"{args.sport}_production_replay_runs"
    outs_coll = f"{args.sport}_production_replay_outputs"

    run_a = db[runs_coll].find_one({"serial": args.serial_a}, {"_id": 0})
    run_b = db[runs_coll].find_one({"serial": args.serial_b}, {"_id": 0})
    if not run_a:
        print(f"❌ serial-A not found: {args.serial_a} (coll: {runs_coll})")
        return 2
    if not run_b:
        print(f"❌ serial-B not found: {args.serial_b}")
        return 2

    rows_a = list(db[outs_coll].find({"replay_serial": args.serial_a}, {"_id": 0}))
    rows_b = list(db[outs_coll].find({"replay_serial": args.serial_b}, {"_id": 0}))

    # Aggregates
    agg_a = _aggregate(rows_a)
    agg_b = _aggregate(rows_b)

    # Pick indexes (qualified, best-book-per-key)
    idx_a = _build_pick_index(rows_a)
    idx_b = _build_pick_index(rows_b)
    keys_a = set(idx_a.keys())
    keys_b = set(idx_b.keys())
    intersect = keys_a & keys_b
    added   = keys_b - keys_a
    removed = keys_a - keys_b
    union = keys_a | keys_b
    jaccard = (100.0 * len(intersect) / len(union)) if union else 0.0

    # Among the intersection, find rows where status / side / book changed
    changed: List[Dict[str, Any]] = []
    for k in sorted(intersect):
        a = idx_a[k]; b = idx_b[k]
        diffs = {}
        for field in ("grade_status", "book", "odds",
                       "gate_pass", "projection_mu", "edge"):
            av = a.get(field); bv = b.get(field)
            try:
                if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                    if abs(av - bv) > 1e-6:
                        diffs[field] = {"a": av, "b": bv}
                elif av != bv:
                    diffs[field] = {"a": av, "b": bv}
            except Exception:
                if av != bv: diffs[field] = {"a": av, "b": bv}
        if diffs:
            changed.append({"key": list(k), "diffs": diffs,
                             "a": _pretty_pick(a), "b": _pretty_pick(b)})

    # Top positive / negative deltas — picks in B-only or A-only and graded
    added_rows  = [idx_b[k] for k in added]
    removed_rows = [idx_a[k] for k in removed]
    added_won  = [r for r in added_rows
                   if (r.get("grade_status") or "").lower() == "win"]
    added_lost = [r for r in added_rows
                   if (r.get("grade_status") or "").lower() == "loss"]
    removed_won = [r for r in removed_rows
                    if (r.get("grade_status") or "").lower() == "win"]
    removed_lost = [r for r in removed_rows
                     if (r.get("grade_status") or "").lower() == "loss"]

    def _by_profit(rows): return sorted(rows, key=lambda r: -float(r.get("profit_units") or 0))

    # ── Report ───────────────────────────────────────────────────────
    print("=" * 100)
    print(f" REPLAY A/B COMPARE   sport={args.sport}")
    print("=" * 100)
    print(f"\nA: {args.serial_a}")
    print(f"   date={run_a.get('game_date')}  snap={run_a.get('snapshot_iso')}  "
          f"tier={run_a.get('tier')}")
    print(f"   pipeline_v={(run_a.get('production_pipeline_version') or '')[:16]}…  "
          f"adapter_v={(run_a.get('adapter_version') or '')[:16]}…")
    print(f"   notes: {run_a.get('notes') or '-'}")
    print(f"\nB: {args.serial_b}")
    print(f"   date={run_b.get('game_date')}  snap={run_b.get('snapshot_iso')}  "
          f"tier={run_b.get('tier')}")
    print(f"   pipeline_v={(run_b.get('production_pipeline_version') or '')[:16]}…  "
          f"adapter_v={(run_b.get('adapter_version') or '')[:16]}…")
    print(f"   notes: {run_b.get('notes') or '-'}")

    print(f"\n──── HEADLINE METRICS  ({'='*70}")
    fmt = "{:<22} {:>14} {:>14} {:>14}"
    print(fmt.format("", "A", "B", "Δ (B−A)"))
    rows_disp = [
        ("rows_scanned",   agg_a["n_rows"], agg_b["n_rows"]),
        ("qualified picks", agg_a["qualified"], agg_b["qualified"]),
        ("wins",           agg_a["wins"], agg_b["wins"]),
        ("losses",         agg_a["losses"], agg_b["losses"]),
        ("pushes",         agg_a["pushes"], agg_b["pushes"]),
        ("hit_rate_pct",   agg_a["hit_rate_pct"], agg_b["hit_rate_pct"]),
        ("roi_pct",        agg_a["roi_pct"], agg_b["roi_pct"]),
        ("stake_units",    agg_a["stake_units"], agg_b["stake_units"]),
        ("profit_units",   agg_a["profit_units"], agg_b["profit_units"]),
    ]
    for name, a, b in rows_disp:
        delta = round(b - a, 4) if isinstance(a, (int, float)) else "—"
        sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
        print(fmt.format(name, a, b, f"{sign}{delta}"))

    print(f"\n──── PICK SET OVERLAP  (qualified picks; key = player+stat+line+side+event)")
    print(f"  |A|             = {len(keys_a)}")
    print(f"  |B|             = {len(keys_b)}")
    print(f"  |A ∩ B|         = {len(intersect)}")
    print(f"  added (in B only)   = {len(added)}")
    print(f"  removed (in A only) = {len(removed)}")
    print(f"  Jaccard overlap = {jaccard:.2f}%")
    print(f"  picks changed in intersection (book/odds/grade/μ/edge) = {len(changed)}")

    print(f"\n──── ODDS BUCKET COMPARISON")
    for line in _bucket_table("by_odds", agg_a["by_odds"], agg_b["by_odds"]):
        print(line)
    print(f"\n──── EDGE BUCKET COMPARISON")
    for line in _bucket_table("by_edge", agg_a["by_edge"], agg_b["by_edge"]):
        print(line)
    print(f"\n──── STAT-FAMILY COMPARISON")
    for line in _bucket_table("by_stat", agg_a["by_stat"], agg_b["by_stat"]):
        print(line)
    print(f"\n──── BOOK COMPARISON  (top 12 by combined n)")
    combined = {k: agg_a["by_book"].get(k, {}).get("n", 0) +
                     agg_b["by_book"].get(k, {}).get("n", 0)
                 for k in set(agg_a["by_book"]) | set(agg_b["by_book"])}
    top_books = {k: True for k, _ in sorted(combined.items(),
                                              key=lambda kv: -kv[1])[:12]}
    a_subset = {k: v for k, v in agg_a["by_book"].items() if k in top_books}
    b_subset = {k: v for k, v in agg_b["by_book"].items() if k in top_books}
    for line in _bucket_table("by_book", a_subset, b_subset):
        print(line)

    print(f"\n──── TOP {args.top_n} ADDED PICKS THAT WON (B helped)")
    for r in _by_profit(added_won)[:args.top_n]:
        print(f"  +{float(r.get('profit_units') or 0):>+6.2f}u   {_pretty_pick(r)}")
    print(f"\n──── TOP {args.top_n} ADDED PICKS THAT LOST (B hurt)")
    for r in _by_profit(added_lost)[-args.top_n:]:
        print(f"  {float(r.get('profit_units') or 0):>+7.2f}u   {_pretty_pick(r)}")
    print(f"\n──── TOP {args.top_n} REMOVED PICKS THAT WON IN A (B missed)")
    for r in _by_profit(removed_won)[:args.top_n]:
        print(f"  +{float(r.get('profit_units') or 0):>+6.2f}u   {_pretty_pick(r)}")
    print(f"\n──── TOP {args.top_n} REMOVED PICKS THAT LOST IN A (B dodged)")
    for r in _by_profit(removed_lost)[-args.top_n:]:
        print(f"  {float(r.get('profit_units') or 0):>+7.2f}u   {_pretty_pick(r)}")

    print(f"\n──── VERDICT")
    profit_delta = agg_b["profit_units"] - agg_a["profit_units"]
    hr_delta = agg_b["hit_rate_pct"] - agg_a["hit_rate_pct"]
    roi_delta = agg_b["roi_pct"] - agg_a["roi_pct"]
    qual_delta = agg_b["qualified"] - agg_a["qualified"]
    direction = "🟢 B helps" if profit_delta > 0 else (
        "🔴 B hurts" if profit_delta < 0 else "⚪ tie")
    print(f"  {direction}: ΔHR={hr_delta:+.2f}pp  ΔROI={roi_delta:+.2f}pp  "
          f"Δprofit={profit_delta:+.2f}u  Δqualified={qual_delta:+d}  "
          f"overlap={jaccard:.1f}%")

    # ── JSON artifact ────────────────────────────────────────────────
    report = {
        "sport": args.sport,
        "serial_a": args.serial_a,
        "serial_b": args.serial_b,
        "run_a": {k: run_a.get(k) for k in
                   ("game_date", "snapshot_iso", "tier",
                    "production_pipeline_version", "adapter_version",
                    "scoring_config_version", "gate_config_version",
                    "feature_cache_version", "git_commit_sha", "notes")},
        "run_b": {k: run_b.get(k) for k in
                   ("game_date", "snapshot_iso", "tier",
                    "production_pipeline_version", "adapter_version",
                    "scoring_config_version", "gate_config_version",
                    "feature_cache_version", "git_commit_sha", "notes")},
        "headline": {
            "hr_delta_pp": round(hr_delta, 4),
            "roi_delta_pp": round(roi_delta, 4),
            "profit_delta_u": round(profit_delta, 4),
            "qualified_delta": qual_delta,
            "stake_a": agg_a["stake_units"], "stake_b": agg_b["stake_units"],
            "wins_a": agg_a["wins"], "wins_b": agg_b["wins"],
            "losses_a": agg_a["losses"], "losses_b": agg_b["losses"],
            "rows_scanned_a": agg_a["n_rows"], "rows_scanned_b": agg_b["n_rows"],
            "verdict": direction,
        },
        "overlap": {
            "size_a": len(keys_a), "size_b": len(keys_b),
            "intersect": len(intersect),
            "added": len(added), "removed": len(removed),
            "jaccard_pct": round(jaccard, 4),
            "changed_in_intersection": len(changed),
        },
        "buckets": {
            "by_odds":  {"a": agg_a["by_odds"],  "b": agg_b["by_odds"]},
            "by_edge":  {"a": agg_a["by_edge"],  "b": agg_b["by_edge"]},
            "by_stat":  {"a": agg_a["by_stat"],  "b": agg_b["by_stat"]},
            "by_book":  {"a": agg_a["by_book"],  "b": agg_b["by_book"]},
        },
        "added_won_top":  [_pretty_pick(r) for r in _by_profit(added_won)[:args.top_n]],
        "added_lost_top": [_pretty_pick(r) for r in _by_profit(added_lost)[-args.top_n:]],
        "removed_won_top": [_pretty_pick(r) for r in _by_profit(removed_won)[:args.top_n]],
        "removed_lost_top": [_pretty_pick(r) for r in _by_profit(removed_lost)[-args.top_n:]],
        "changed_picks_sample": changed[:50],
    }
    out_path = f"{OUT_DIR}/replay_compare_{args.serial_a}_vs_{args.serial_b}.json"
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\n[json] wrote {out_path}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
