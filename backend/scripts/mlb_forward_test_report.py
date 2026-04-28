"""
MLB Forward-Test Report — Locked Pipeline
==========================================
Read-only analytics over `mlb_pick_history`. Produces the slices
required by the lock-down spec; NO tuning, NO parameter changes —
measurement only.

Sections:
  1. Overall: picks / hit-rate / ROI
  2. By feature_source: statcast (id+name) vs bdl_proxy
  3. By tier
  4. By side
  5. By sample quality (BBE >= 25 vs BBE < 25)
  6. Picks per slate

Selection criteria:
  • model_version IN {"mlb_total_bases_v1_locked", "mlb_total_bases_v1"}
    (the lock bumped the tag — older locked rows are still in scope)
  • optional --since YYYY-MM-DD to bound the window

Run:
    python -m scripts.mlb_forward_test_report
    python -m scripts.mlb_forward_test_report --since 2026-04-26
"""
from __future__ import annotations

import argparse, asyncio, os, sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

COLL = "mlb_pick_history"
LOCKED_VERSIONS = ("mlb_total_bases_v1_locked", "mlb_total_bases_v1")


def _roi_minus110(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0: return 0.0
    return (wins * 100 - losses * 110) / (n * 110) * 100


def _bucket_edge(e):
    if e is None:    return "—"
    if e < 0:        return "<0%"
    if e < 5:        return "0–5%"
    if e < 10:       return "5–10%"
    if e < 15:       return "10–15%"
    return "15%+"


async def _all_picks(db, *, since: Optional[str]) -> List[Dict[str, Any]]:
    q: Dict[str, Any] = {"model_version": {"$in": list(LOCKED_VERSIONS)}}
    if since: q["game_date"] = {"$gte": since}
    return await db[COLL].find(q, {"_id": 0}).to_list(length=None)


def _row(label: str, picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_total   = len(picks)
    settled   = [p for p in picks if p.get("hit") in (True, False)]
    n_settled = len(settled)
    wins      = sum(1 for p in settled if p["hit"])
    losses    = n_settled - wins
    return {
        "label":    label,
        "n_total":  n_total,
        "settled":  n_settled,
        "wins":     wins,
        "losses":   losses,
        "hit_rate": (wins / n_settled * 100) if n_settled else 0.0,
        "roi_110":  _roi_minus110(wins, losses),
    }


def _print_table(title: str, rows: List[Dict[str, Any]],
                  label_pad: int = 22) -> None:
    print("=" * 80); print(f"  {title}"); print("=" * 80)
    print(f"  {'segment':{label_pad}s} {'picks':>6}  {'settled':>7}  "
          f"{'W':>4}  {'L':>4}  {'hit%':>6}  {'ROI':>7}")
    for r in rows:
        hr = f"{r['hit_rate']:.1f}%" if r["settled"] else "  —  "
        roi = f"{r['roi_110']:+.2f}%" if r["settled"] else "   —   "
        print(f"  {r['label']:{label_pad}s} {r['n_total']:>6,}  "
              f"{r['settled']:>7,}  {r['wins']:>4,}  {r['losses']:>4,}  "
              f"{hr:>6}  {roi:>7}")
    print()


# ---------------------------------------------------------------------------
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None,
                    help="ISO date — only count picks on/after this date.")
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    picks = await _all_picks(db, since=args.since)
    print()
    print("#" * 80)
    print("#  MLB FORWARD-TEST REPORT  ·  LOCKED PIPELINE")
    print(f"#  model_versions   : {LOCKED_VERSIONS}")
    print(f"#  since            : {args.since or '(all)'}")
    print(f"#  picks loaded     : {len(picks):,}")
    print("#" * 80)
    print()

    if not picks:
        print("  (no picks found)"); return

    # ---- 1. Overall -------------------------------------------------------
    _print_table("1. OVERALL", [_row("ALL", picks)])

    # ---- 2. By feature_source --------------------------------------------
    rows_fs = []
    for label in ("statcast_id", "statcast_name", "bdl_proxy", "—"):
        if label == "—":
            sub = [p for p in picks if not p.get("feature_source")]
        else:
            sub = [p for p in picks if p.get("feature_source") == label]
        if sub: rows_fs.append(_row(label, sub))
    # Combined statcast roll-up
    sc_all = [p for p in picks
               if p.get("feature_source") in ("statcast_id", "statcast_name")]
    if sc_all: rows_fs.insert(0, _row("statcast (all)", sc_all))
    _print_table("2. BY FEATURE_SOURCE", rows_fs)

    # ---- 3. By tier -------------------------------------------------------
    rows_tier = []
    for label in ("safe_haven", "front_lines", "war_zone"):
        sub = [p for p in picks if p.get("tier") == label]
        if sub: rows_tier.append(_row(label, sub))
    _print_table("3. BY TIER", rows_tier)

    # ---- 4. By side -------------------------------------------------------
    rows_side = []
    for label in ("OVER", "UNDER"):
        sub = [p for p in picks if p.get("side") == label]
        if sub: rows_side.append(_row(label, sub))
    _print_table("4. BY SIDE", rows_side)

    # ---- 5. By sample quality --------------------------------------------
    # BBE is the rolling_30 batted_ball_events the engine cached on the
    # pick record (`bbe_30` field). For locked-version rows it may be
    # absent (older logs predate the field) — those go to "no_bbe".
    rows_bbe = []
    sub_hi = [p for p in picks
              if (p.get("bbe_30") is not None and p["bbe_30"] >= 25)]
    sub_lo = [p for p in picks
              if (p.get("bbe_30") is not None and p["bbe_30"] < 25)]
    sub_no = [p for p in picks if p.get("bbe_30") is None]
    if sub_hi: rows_bbe.append(_row("BBE >= 25",  sub_hi))
    if sub_lo: rows_bbe.append(_row("BBE < 25",   sub_lo))
    if sub_no: rows_bbe.append(_row("no BBE field", sub_no))
    _print_table("5. BY SAMPLE QUALITY (rolling_30 batted_ball_events)", rows_bbe)

    # ---- 6. Picks per slate ----------------------------------------------
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in picks:
        if p.get("game_date"): by_date[p["game_date"]].append(p)
    print("=" * 80); print("  6. PICKS PER SLATE"); print("=" * 80)
    print(f"  {'date':12s} {'picks':>6} {'settled':>7} {'W':>4} {'L':>4} "
          f"{'hit%':>6} {'ROI':>7}")
    sums = []
    for d in sorted(by_date):
        r = _row(d, by_date[d]); sums.append(r["n_total"])
        hr = f"{r['hit_rate']:.1f}%" if r["settled"] else "  —  "
        roi = f"{r['roi_110']:+.2f}%" if r["settled"] else "   —   "
        print(f"  {r['label']:12s} {r['n_total']:>6,} {r['settled']:>7,} "
              f"{r['wins']:>4,} {r['losses']:>4,} {hr:>6} {roi:>7}")
    if sums:
        print()
        print(f"  slates: {len(sums)}   "
              f"avg picks/slate: {sum(sums)/len(sums):.2f}   "
              f"min: {min(sums)}   max: {max(sums)}")

    # ---- 7. Edge-bucket monotonicity sanity ------------------------------
    # Not asked as a section, but useful as a one-line sanity readout —
    # a healthy model has ROI rising with edge bucket.
    print()
    print("=" * 80); print("  EDGE-BUCKET MONOTONICITY (settled only)")
    print("=" * 80)
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"w": 0, "l": 0})
    for p in picks:
        if p.get("hit") not in (True, False): continue
        b = _bucket_edge(p.get("edge_pct"))
        counts[b]["w"] += 1 if p["hit"] else 0
        counts[b]["l"] += 0 if p["hit"] else 1
    print(f"  {'bucket':10s} {'n':>5} {'W':>4} {'L':>4} {'hit%':>6} {'ROI':>7}")
    for b in ("<0%", "0–5%", "5–10%", "10–15%", "15%+"):
        if b not in counts: continue
        w = counts[b]["w"]; l = counts[b]["l"]; n = w + l
        hr = (w / n * 100) if n else 0.0
        roi = _roi_minus110(w, l)
        print(f"  {b:10s} {n:>5,} {w:>4,} {l:>4,} {hr:>5.1f}% {roi:>+6.2f}%")
    print()
    print("  (no tuning — measurement only.)")


if __name__ == "__main__":
    asyncio.run(main())
