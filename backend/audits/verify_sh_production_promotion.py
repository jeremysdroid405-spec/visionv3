"""Lightweight verification of the 2026-05-18 SH Volume-First promotion.

Re-evaluates the NEW gate stack against the existing
`GSS-MLB-{date}-SAFE-POOL` rows (the disable-all-gates audit pool of
1,883 SH-routed props) and confirms the production-gated subset
matches the audit target (43 grd / 40W / 3L / 93.0% HR / +18.05% ROI /
+7.76u).

Memory-light: zero pipeline runs, zero motor inserts. Pulls the layer-3
audit pool and replays the gate engine in pure Python.
"""
from __future__ import annotations
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.scoring.gates.engine import UniversalGateEngine
from services.scoring.gates.schema import NormalizedMetrics


DATES = [(datetime(2026, 5, 3) + timedelta(days=i)).strftime("%Y%m%d")
         for i in range(13)]
SERIALS = [f"GSS-MLB-{d}-SAFE-POOL" for d in DATES]
TARGET_FAMILIES = {"hits", "pitcher_strikeouts"}
BLOCKED_FAMILIES = {"earned_runs", "batter_strikeouts", "total_bases",
                    "hits_runs_rbis", "runs", "rbis", "pitching_outs"}


def _safe(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _agg(rows):
    w = l = p = u = 0
    stake = profit = 0.0
    for r in rows:
        st = r.get("grade_status")
        if st == "win":
            w += 1
            stake += 1.0
            profit += float(r.get("profit_units") or 0)
        elif st == "loss":
            l += 1
            stake += 1.0
            profit -= 1.0
        elif st == "push":
            p += 1
            stake += 1.0
        else:
            u += 1
    dec = w + l
    return dict(
        n=len(rows), graded=dec + p, W=w, L=l, P=p, U=u,
        hr=(100.0 * w / dec) if dec else None,
        roi=(100.0 * profit / stake) if stake else None,
        profit=round(profit, 4),
    )


def _row_to_metrics(r):
    """Build a NormalizedMetrics from a layer-3 audit row."""
    ref_odds = _safe(r.get("tier_reference_odds"))
    return NormalizedMetrics(
        sport="mlb",
        tier="safe_haven",
        stat_family=r.get("stat_family"),
        side=(r.get("side") or "OVER").upper(),
        reference_book=r.get("tier_reference_book") or r.get("book"),
        reference_odds=int(ref_odds) if ref_odds is not None else None,
        book_count=int(r.get("canonical_book_count_either_side") or 0),
        tp=_safe(r.get("tp")),
        hit_rate=_safe(r.get("hit_rate")),
        hit_rate_l20=_safe(r.get("hit_rate_l20")),
        hit_rate_l10=_safe(r.get("hit_rate_l10")),
        hit_rate_l5=_safe(r.get("hit_rate_l5")),
        hit_rate_sample_size=r.get("hit_rate_sample_size"),
        cv=_safe(r.get("cv")),
        edge_pct=_safe(r.get("edge_pct")),
        line=_safe(r.get("line")),
        avg_hit_margin=_safe(r.get("avg_hit_margin")),
        avg_miss_margin=_safe(r.get("avg_miss_margin")),
        vision_score=_safe(r.get("vision_score")),
        tp_source=r.get("tp_source"),
        is_alt=bool(r.get("is_alternate") or r.get("is_alternate_market")),
        p_model_pct=_safe(r.get("p_model_pct")),
    )


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    engine = UniversalGateEngine()

    pool = await db["mlb_test_outputs"].find(
        {"replay_serial": {"$in": SERIALS},
         "routed_tier": "safe_haven"},
        projection={"_id": 0},
    ).to_list(100000)

    print("=" * 92)
    print(f"  PRODUCTION-PROMOTION VERIFICATION — replay new gate stack on existing pool")
    print(f"  pool size: {len(pool)} SH-routed props · 13 days")
    print("=" * 92)

    new_pass = []
    blocked_pass_count = {fam: 0 for fam in BLOCKED_FAMILIES}
    odds_floor_violations = 0

    for r in pool:
        m = _row_to_metrics(r)
        result = engine.evaluate(m)
        if not result.passed:
            continue
        # this row PASSED the new gate stack
        new_pass.append(r)
        fam = r.get("stat_family")
        if fam in BLOCKED_FAMILIES:
            blocked_pass_count[fam] += 1
        o = _safe(r.get("tier_reference_odds"))
        if o is not None and o <= -500:
            odds_floor_violations += 1

    a = _agg(new_pass)
    print(f"\nNew gate stack gate_pass=True (production-as-written):")
    print(f"  n={a['n']}  graded={a['graded']}  W={a['W']}/L={a['L']} P={a['P']} U={a['U']}")
    print(f"  HR={a['hr']:.2f}%   ROI={a['roi']:+.2f}%   P&L={a['profit']:+.3f}u")

    print(f"\nTARGET (audit Volume-First):  43 grd  40W/3L  93.00% HR  +18.05% ROI  +7.76u")

    # Per-family
    by_fam = defaultdict(list)
    for r in new_pass:
        by_fam[r.get("stat_family")].append(r)
    print(f"\nBy stat_family:")
    for fam in sorted(by_fam):
        x = _agg(by_fam[fam])
        hr = f"{x['hr']:.2f}%" if x['hr'] is not None else "—"
        roi = f"{x['roi']:+.2f}%" if x['roi'] is not None else "—"
        print(f"  {fam:<24s} n={x['n']:>3d} grd={x['graded']:>3d} "
              f"W={x['W']:>2d} L={x['L']:>2d}  HR={hr:>8s}  ROI={roi:>9s}  P&L={x['profit']:+.3f}u")

    print(f"\nBlocked-family verification (each must = 0):")
    for fam in sorted(BLOCKED_FAMILIES):
        ok = "✓" if blocked_pass_count[fam] == 0 else "✗"
        print(f"  {ok}  {fam:<24s}: gate_pass=True count = {blocked_pass_count[fam]}")

    print(f"\nOdds-floor verification:")
    ok = "✓" if odds_floor_violations == 0 else "✗"
    print(f"  {ok}  gate_pass=True with odds <= -500: {odds_floor_violations}  (must be 0)")


if __name__ == "__main__":
    asyncio.run(main())
