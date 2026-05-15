"""Universal Direction Gate validation audit (2026-05-15).

Re-evaluates the active MLB Front Lines OVER slate against the
refactored strict-inequality direction gate. Answers:

  1. Jorge Soler Hits 0.5 OVER — does it now pass direction?
  2. How many props would have failed direction under the OLD
     min-margin / min-projection-minus-line rule but now pass under
     the strict (proj > line) rule?
  3. What is the current failed-gate distribution across active MLB
     FL OVER score docs?

READ-ONLY. Touches no collection.
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient


def _diff(proj: Optional[float], line: Optional[float]) -> Optional[float]:
    if proj is None or line is None:
        return None
    try:
        return float(proj) - float(line)
    except (TypeError, ValueError):
        return None


def _direction_old(diff: Optional[float], min_margin: float) -> Optional[bool]:
    """OLD semantics: proj - line >= min_margin (positive cushion)."""
    if diff is None:
        return None
    return diff >= min_margin


def _direction_new(diff: Optional[float]) -> Optional[bool]:
    """NEW semantics (2026-05-15): strict proj > line."""
    if diff is None:
        return None
    return diff > 0.0


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Active MLB Front Lines OVER score docs.
    cur = db["mlb_prop_scores"].find({
        "active": True,
        "version_tag": "final-mlb-rt",
        "tier": "front_lines",
        "recommendation": "OVER",
    }, {"_id": 0})

    rows: List[Dict[str, Any]] = []
    async for d in cur:
        rows.append(d)

    print(f"Active MLB FL OVER docs: {len(rows)}")
    if not rows:
        print("No active rows — exiting.")
        return

    # ── Per-doc analysis ──────────────────────────────────────
    reason_counter: Counter = Counter()
    old_dir_pass = 0
    new_dir_pass = 0
    rescued_by_refactor: List[Dict[str, Any]] = []  # OLD direction fail → NEW pass
    direction_failures_new: List[Dict[str, Any]] = []

    soler_rows: List[Dict[str, Any]] = []

    # MIN-MARGIN floors per the previous (now-defunct) MLB FL config.
    OLD_MIN_MARGINS = {
        "hits": 0.50, "hits_runs_rbis": 1.00, "total_bases": 1.00,
        "rbis": 0.75, "runs": 0.75, "pitching_outs": 0.75,
        "pitcher_strikeouts": 0.75, "batter_strikeouts": 0.50,
        "earned_runs": 0.75, "_default": 0.75,
    }

    for d in rows:
        reason = d.get("tier_reason") or "unspecified"
        reason_counter[reason] += 1

        proj = d.get("model_projection")
        if proj is None:
            proj = (d.get("model") or {}).get("projection")
        line = d.get("line")
        diff = _diff(proj, line)
        family = (d.get("stat_family") or "_default").lower()
        old_floor = OLD_MIN_MARGINS.get(family, OLD_MIN_MARGINS["_default"])

        old_ok = _direction_old(diff, old_floor)
        new_ok = _direction_new(diff)
        if old_ok:
            old_dir_pass += 1
        if new_ok:
            new_dir_pass += 1
        if old_ok is False and new_ok is True:
            rescued_by_refactor.append({
                "player": d.get("player_name"),
                "stat_family": family,
                "line": line,
                "projection": proj,
                "diff": round(diff, 4) if diff is not None else None,
                "old_floor": old_floor,
                "tier_reason": reason,
            })
        if new_ok is False:
            direction_failures_new.append({
                "player": d.get("player_name"),
                "stat_family": family,
                "line": line,
                "projection": proj,
                "diff": round(diff, 4) if diff is not None else None,
                "tier_reason": reason,
            })

        # Jorge Soler hits 0.5
        if (str(d.get("player_name") or "").strip().lower().startswith("jorge soler")
            and family in ("hits", "hits_runs_rbis", "total_bases", "rbis", "runs")):
            soler_rows.append({
                "player": d.get("player_name"),
                "stat_family": family,
                "line": line,
                "projection": proj,
                "diff": round(diff, 4) if diff is not None else None,
                "tier": d.get("tier"),
                "tier_reason": reason,
                "edge_vs_fair": d.get("edge_vs_fair"),
                "total_edge": d.get("total_edge"),
                "hit_rate_over": d.get("hit_rate_over"),
                "cv": d.get("cv"),
                "tp": d.get("tp"),
                "fair_prob": d.get("fair_prob"),
            })

    # ── Output ────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("MLB FL OVER — Direction Gate Refactor Audit")
    print("=" * 78)
    print(f"\nDirection-gate verdict (OLD vs NEW)")
    print(f"  OLD (proj − line ≥ stat-family min_margin)   passing: {old_dir_pass} / {len(rows)}")
    print(f"  NEW (strict proj > line)                       passing: {new_dir_pass} / {len(rows)}")
    print(f"  Net rescued by refactor                              : {len(rescued_by_refactor)}")

    print(f"\nDirection-still-failing under NEW strict rule         : {len(direction_failures_new)}")

    print(f"\nCurrent reject reason distribution (top 12):")
    for reason, count in reason_counter.most_common(12):
        print(f"  {reason:50s} {count:5d}")

    if rescued_by_refactor:
        print(f"\nTop 20 props rescued by the refactor (OLD dir fail → NEW pass):")
        print(f"  {'player':30s} {'family':18s} {'line':>6s} {'proj':>7s} {'diff':>7s} {'old_floor':>10s} reason")
        for r in rescued_by_refactor[:20]:
            print(f"  {(r['player'] or '')[:30]:30s} "
                  f"{r['stat_family'][:18]:18s} "
                  f"{r['line']!s:>6s} "
                  f"{r['projection']!s:>7.7s} "
                  f"{r['diff']!s:>7.7s} "
                  f"{r['old_floor']:>10.3f} "
                  f"{r['tier_reason']}")

    print()
    print("=" * 78)
    print("Jorge Soler probe")
    print("=" * 78)
    if not soler_rows:
        print("  No active Jorge Soler row in the slate.")
    else:
        for r in soler_rows:
            print(f"  {r['player']} · {r['stat_family']} · line={r['line']}")
            print(f"    projection      : {r['projection']}")
            print(f"    diff (proj-line): {r['diff']}")
            print(f"    new direction   : {'PASS' if (r['diff'] is not None and r['diff'] > 0) else 'FAIL'}")
            print(f"    current tier    : {r['tier']}  ({r['tier_reason']})")
            print(f"    edge_vs_fair    : {r['edge_vs_fair']}")
            print(f"    total_edge      : {r['total_edge']}")
            print(f"    hit_rate_over   : {r['hit_rate_over']}")
            print(f"    cv              : {r['cv']}")
            print(f"    tp              : {r['tp']}")
            print(f"    fair_prob       : {r['fair_prob']}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
