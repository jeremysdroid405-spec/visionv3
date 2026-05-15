"""Phase 2A validation report — read-only analytics over mlb_prop_scores.

Sections:
  1. Audit 4 named players (Andy Pages, Kyle Tucker, Freddie Freeman,
     Ozzie Albies) — comparison of v3.0 vs v3.1 rows (when both exist).
  2. Top 30 MLB FL rejects by HR (model says low, market says low).
  3. Top 30 MLB FL rejects by edge (model says high edge but failed
     another gate).
  4. Binary prop audits — Hits 0.5 / Singles 0.5 / Walks 0.5
     direction distribution before/after.
  5. PRIMARY METRIC: count of "fake negative edge" rows — market
     favors OVER (book_count >= 3 AND consensus implied_prob > 0.55)
     AND HR favors OVER (hit_rate >= 60%) BUT model projection
     suppresses OVER (model_projection < line).
  6. Matchup feature importance summary (from training report).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _f(v, digits=3, default=""):
    if v is None:
        return default
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return str(v)


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ["DB_NAME"]
    ]
    print("=" * 100)
    print("PHASE 2A VALIDATION REPORT")
    print("=" * 100)

    # ── Section 0: counts ────────────────────────────────────────
    n_total = await db.mlb_prop_scores.count_documents({"active": True})
    n_v31 = await db.mlb_prop_scores.count_documents(
        {"active": True, "projection_model_version": "MLB_HF_v3.1_phase2a"}
    )
    n_v30 = await db.mlb_prop_scores.count_documents(
        {"active": True, "projection_model_version": "MLB_HF_v3.0_bayes"}
    )
    n_match = await db.mlb_prop_scores.count_documents(
        {"active": True, "same_hand_matchup": {"$ne": None}}
    )
    print(f"\nActive props: {n_total:,}")
    print(f"  v3.1_phase2a: {n_v31:,} ({100*n_v31/max(n_total,1):.1f}%)")
    print(f"  v3.0_bayes:   {n_v30:,} ({100*n_v30/max(n_total,1):.1f}%)")
    print(f"  Matchup ctx:  {n_match:,} ({100*n_match/max(n_total,1):.1f}%)")

    # ── Section 1: audit players ─────────────────────────────────
    print("\n" + "=" * 100)
    print("1. AUDIT PLAYERS — current slate")
    print("=" * 100)
    targets = [
        ("Andy Pages", "Hits", 0.5),
        ("Kyle Tucker", "Hits", 0.5),
        ("Freddie Freeman", "Hits", 0.5),
        ("Ozzie Albies", "Hits+Runs+RBIs", 1.5),
    ]
    for name, stat, line in targets:
        cursor = db.mlb_prop_scores.find(
            {
                "player_name": name, "stat_type": stat, "line": line,
                "active": True,
            },
            {
                "_id": 0, "recommendation": 1, "model_projection": 1,
                "tp": 1, "edge_vs_fair": 1, "total_edge": 1,
                "batter_hand": 1, "opp_pitcher_name": 1,
                "opp_pitcher_throws": 1, "same_hand_matchup": 1,
                "projection_model_version": 1, "raw_hf_projection": 1,
                "eb_shrunk_projection": 1, "tier": 1, "tier_reason": 1,
                "book_count": 1, "hit_rate_l20": 1, "commence_time": 1,
            },
        )
        rows = await cursor.to_list(length=10)
        if not rows:
            print(f"\n── {name} {stat} {line}: NO ACTIVE ROWS")
            continue
        rows.sort(key=lambda r: r.get("recommendation") or "")
        for r in rows[:2]:
            print(f"\n── {name} {stat} {line} {r.get('recommendation')} "
                   f"({r.get('projection_model_version')})")
            print(f"   commence: {r.get('commence_time')} | books: {r.get('book_count')}")
            print(f"   batter_hand={r.get('batter_hand')} | "
                   f"vs {r.get('opp_pitcher_name')} "
                   f"({r.get('opp_pitcher_throws')}) | "
                   f"same_hand={r.get('same_hand_matchup')}")
            print(f"   raw_hf={_f(r.get('raw_hf_projection'))} → "
                   f"eb={_f(r.get('eb_shrunk_projection'))} → "
                   f"projection={_f(r.get('model_projection'))}")
            print(f"   tp={_f(r.get('tp'),1)}% | "
                   f"edge_vs_fair={_f(r.get('edge_vs_fair'),4)} | "
                   f"total_edge={_f(r.get('total_edge'),4)} | "
                   f"hit_rate_l20={r.get('hit_rate_l20')}")
            print(f"   tier={r.get('tier')} ({r.get('tier_reason')})")

    # ── Section 5 (primary metric, computed first so user sees it
    #              prominently). "Fake negative edge" definition:
    #     market OVER-favored: consensus_implied_prob_over >= 0.55
    #                          (computed as 1 - p_under_market_avg)
    #     HR OVER-favored:    hit_rate_l20 >= 60
    #     model suppresses:   model_projection < line AND
    #                         recommendation == OVER AND edge < 0
    # We use BOTH v3.0 and v3.1 to compute the split.
    print("\n" + "=" * 100)
    print("5. PRIMARY METRIC — fake-negative-edge cluster")
    print("=" * 100)
    print("Definition: OVER row where HR ≥ 60%, book_count ≥ 3,")
    print("            model projection < line, edge_vs_fair < 0.")
    print()

    pipeline_base = [
        {"$match": {
            "active": True,
            "recommendation": "OVER",
            "book_count": {"$gte": 3},
            "hit_rate_l20": {"$gte": 60},
            "edge_vs_fair": {"$lt": 0},
            "$expr": {"$lt": ["$model_projection", "$line"]},
        }},
        {"$count": "n"},
    ]
    for ver in ("MLB_HF_v3.0_bayes", "MLB_HF_v3.1_phase2a"):
        pipe = [
            {"$match": {"projection_model_version": ver}},
        ] + pipeline_base
        r = await db.mlb_prop_scores.aggregate(pipe).to_list(length=1)
        n = r[0]["n"] if r else 0
        # Denominator: total OVER with HR≥60 in this version.
        total_pipe = [
            {"$match": {
                "projection_model_version": ver, "active": True,
                "recommendation": "OVER", "book_count": {"$gte": 3},
                "hit_rate_l20": {"$gte": 60},
            }},
            {"$count": "n"},
        ]
        rt = await db.mlb_prop_scores.aggregate(total_pipe).to_list(length=1)
        nt = rt[0]["n"] if rt else 0
        rate = (100 * n / nt) if nt else 0
        print(f"  {ver}: fake_neg_edge={n} / total_OVER_HR60={nt}  ({rate:.1f}%)")

    # ── Section 2: top FL rejects by edge (front_lines failed tier)
    print("\n" + "=" * 100)
    print("2. TOP 20 MLB FL REJECTS BY EDGE (positive edge, gate failed)")
    print("=" * 100)
    cursor = db.mlb_prop_scores.find(
        {
            "active": True,
            "recommendation": "OVER",
            "routed_tier": "front_lines",
            "tier": "unqualified",
            "edge_vs_fair": {"$gt": 0.08},
        },
        {
            "_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
            "model_projection": 1, "edge_vs_fair": 1, "total_edge": 1,
            "hit_rate_l20": 1, "tier_reason": 1, "tp": 1,
            "projection_model_version": 1, "opp_pitcher_name": 1,
            "opp_pitcher_throws": 1, "same_hand_matchup": 1,
        },
    ).sort("edge_vs_fair", -1).limit(20)
    rows = await cursor.to_list(length=20)
    print(f"  {'PLAYER':<22} {'STAT':<18} {'LN':>5} "
           f"{'PROJ':>6} {'EDGE':>7} {'HR':>5} {'TP':>6} "
           f"{'VS PITCHER':<25} {'TIER_REASON'}")
    for r in rows:
        print(f"  {r.get('player_name',''):<22.22} "
               f"{r.get('stat_type',''):<18.18} "
               f"{_f(r.get('line'),1):>5} "
               f"{_f(r.get('model_projection'),2):>6} "
               f"{_f(r.get('edge_vs_fair'),3):>7} "
               f"{_f(r.get('hit_rate_l20'),0):>5} "
               f"{_f(r.get('tp'),1):>6} "
               f"{(r.get('opp_pitcher_name') or '') + ' (' + (r.get('opp_pitcher_throws') or '?') + ')':<25.25} "
               f"{(r.get('tier_reason') or '')[:30]}")

    # ── Section 3: binary prop audit
    print("\n" + "=" * 100)
    print("3. BINARY PROP — Hits 0.5 / Singles 0.5 / Walks 0.5 OVER")
    print("=" * 100)
    for stat in ("Hits", "Singles", "Walks"):
        for ver in ("MLB_HF_v3.0_bayes", "MLB_HF_v3.1_phase2a"):
            pipe = [
                {"$match": {
                    "active": True, "recommendation": "OVER",
                    "stat_type": stat, "line": 0.5,
                    "projection_model_version": ver,
                }},
                {"$group": {
                    "_id": None, "n": {"$sum": 1},
                    "avg_proj": {"$avg": "$model_projection"},
                    "avg_edge": {"$avg": "$edge_vs_fair"},
                    "avg_tp": {"$avg": "$tp"},
                    "n_pos_edge": {
                        "$sum": {"$cond": [
                            {"$gt": ["$edge_vs_fair", 0]}, 1, 0,
                        ]}},
                    "n_neg_edge": {
                        "$sum": {"$cond": [
                            {"$lt": ["$edge_vs_fair", 0]}, 1, 0,
                        ]}},
                }},
            ]
            r = await db.mlb_prop_scores.aggregate(pipe).to_list(length=1)
            if r:
                d = r[0]
                pos_pct = 100 * d["n_pos_edge"] / max(d["n"], 1)
                print(f"  {stat:<10} {ver:<24} "
                       f"n={d['n']:<5} "
                       f"avg_proj={_f(d.get('avg_proj'),3):>5}  "
                       f"avg_edge={_f(d.get('avg_edge'),4):>7}  "
                       f"avg_tp={_f(d.get('avg_tp'),1):>5}  "
                       f"pos_edge={d['n_pos_edge']:>3} "
                       f"({pos_pct:.0f}%)")

    # ── Section 6: matchup feature importance (from training report)
    print("\n" + "=" * 100)
    print("6. MATCHUP FEATURE IMPORTANCE (from training report)")
    print("=" * 100)
    rpt_path = "/app/backend/models/mlb_hf/_phase2a_workdir/_train_report.json"
    if os.path.exists(rpt_path):
        with open(rpt_path) as f:
            rpt = json.load(f)
        print(f"  {'STAT':<18} {'R²_te':<8} {'MAE_te':<8} "
               f"{'samples':<8} {'sc_hit':<7} {'match_hit':<10}")
        for k, v in sorted(rpt["stats"].items()):
            print(f"  {k:<18} {_f(v['r2_test'],4):<8} "
                   f"{_f(v['mae_test'],4):<8} {v['samples']:<8,} "
                   f"{_f(v['sc_hit_rate'],3):<7} "
                   f"{_f(v['matchup_hit_rate'],3):<10}")
        print("\n  Top matchup-feature importance per stat:")
        for k, v in sorted(rpt["stats"].items()):
            mfi = v.get("matchup_feature_importance", [])
            nonzero = [(f, i) for f, i in mfi if i > 0.001][:4]
            if nonzero:
                print(f"    {k:<18}: " +
                       "  ".join(f"{f}={i:.3f}" for f, i in nonzero))


if __name__ == "__main__":
    asyncio.run(main())
