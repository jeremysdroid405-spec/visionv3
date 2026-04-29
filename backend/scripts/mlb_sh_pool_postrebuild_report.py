"""Post-rebuild MLB Safe Haven passing-pool + rejects report.
Read-only — no DB writes. Identifies ladder duplicates by
(player, stat_family, side) and surfaces the highest-edge entry as the
deduped representative.
"""
import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient


VERSION = "final-mlb"


async def fetch_pool(db, query, sort_key, limit):
    rows = await db.mlb_prop_scores.find(query, {"_id": 0}).to_list(length=10000)

    def num(v):
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    rows.sort(key=lambda r: (-num(r.get(sort_key)),
                              -num(r.get("vision_score")),
                              r.get("canonical_key") or ""))
    return rows[:limit]


def shape(r, ox):
    side = r.get("recommendation")
    proj = r.get("model_projection") or r.get("vk2_projection")
    p_true = r.get("p_true_active")
    return {
        "player": r.get("player_name"),
        "team": r.get("team") or ox.get("team"),
        "opp": r.get("opponent_team") or ox.get("opponent_team"),
        "stat": r.get("stat_type"),
        "stat_family": r.get("stat_family") or r.get("stat_type"),
        "line": r.get("line"),
        "side": side,
        "is_alt": bool(r.get("is_alternate_market") or ox.get("is_alternate_market")),
        "ref_odds": r.get("tier_reference_odds"),
        "ref_book": r.get("tier_reference_book"),
        "tp": r.get("tp"),                          # market TP
        "p_distribution": r.get("p_distribution"),
        "p_distribution_pct": (p_true * 100) if isinstance(p_true, (int, float)) else None,
        "edge_pct": r.get("edge_pct"),
        "tier": r.get("tier"),
        "tier_reason": r.get("tier_reason"),
        "hit_rate": r.get("hit_rate_over") if side == "OVER" else r.get("hit_rate_under"),
        "cv": r.get("cv"),
        "vision": r.get("vision_score"),
        "projection": round(float(proj), 3) if isinstance(proj, (int, float)) else None,
        "sigma": r.get("model_sigma"),
        "tp_source": r.get("tp_source"),
        "p_lom_shadow": r.get("p_lom_shadow"),
        "p_ecdf_shadow": r.get("p_ecdf_shadow"),
    }


def dedup_by_player_stat_side(rows):
    """Returns (deduped_rows, ladder_groups).

    A "ladder group" = multiple line entries for the same
    (player, stat_family/stat_type, side). Within each group the
    highest-edge entry wins.
    """
    groups = defaultdict(list)
    for r in rows:
        key = (
            (r["player"] or "").strip().lower(),
            (r["stat"] or "").strip().lower(),
            (r["side"] or "").strip().upper(),
        )
        groups[key].append(r)

    dedup = []
    ladder_groups = []
    for key, members in groups.items():
        if len(members) > 1:
            # Sort by edge_pct desc; mark all members.
            members_sorted = sorted(
                members,
                key=lambda x: (-(x["edge_pct"] or float("-inf")),
                                -(x.get("p_distribution_pct") or 0)),
            )
            ladder_groups.append({
                "key": {"player": members[0]["player"],
                          "stat": members[0]["stat"],
                          "side": members[0]["side"]},
                "members": [{"line": m["line"],
                              "ref_odds": m["ref_odds"],
                              "tp": m["tp"],
                              "p_dist_pct": m["p_distribution_pct"],
                              "edge_pct": m["edge_pct"],
                              "tier": m["tier"]} for m in members_sorted],
            })
            dedup.append(members_sorted[0])
        else:
            dedup.append(members[0])

    # Stable sort the deduped output by edge desc, vision desc.
    dedup.sort(key=lambda r: (-(r["edge_pct"] or float("-inf")),
                                -(r["vision"] or 0)))
    return dedup, ladder_groups


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # 1. SH passing-pool (all 14)
    sh_passes = await fetch_pool(
        db, {"version_tag": VERSION, "tier": "safe_haven"},
        sort_key="edge_pct", limit=200,
    )
    # 2. SH-routed rejects (top 20)
    sh_rejects = await fetch_pool(
        db,
        {"version_tag": VERSION,
         "tier_reference_odds": {"$lte": -300},
         "tier": {"$ne": "safe_haven"}},
        sort_key="edge_pct", limit=20,
    )

    keys = {r["canonical_key"] for r in sh_passes + sh_rejects if r.get("canonical_key")}
    odds_by_key = {}
    cursor = db.mlb_live_props.find(
        {"canonical_key": {"$in": list(keys)}},
        {"_id": 0, "canonical_key": 1, "team": 1, "opponent_team": 1,
         "is_alternate_market": 1},
    )
    async for d in cursor:
        odds_by_key[d["canonical_key"]] = d

    sh_rows = [shape(r, odds_by_key.get(r.get("canonical_key"), {})) for r in sh_passes]
    rj_rows = [shape(r, odds_by_key.get(r.get("canonical_key"), {})) for r in sh_rejects]

    sh_dedup, sh_ladders = dedup_by_player_stat_side(sh_rows)
    rj_dedup, rj_ladders = dedup_by_player_stat_side(rj_rows)

    # ── Print SH passing pool (raw, then deduped) ───────────────
    print()
    print("=" * 165)
    print(f"SAFE HAVEN PASSING POOL — full ({len(sh_rows)} picks)")
    print(f"version_tag={VERSION}  generated_at={datetime.now(timezone.utc).isoformat()}")
    print("=" * 165)
    print(f"{'#':>2} {'Player':<22} {'TM/OPP':<10} {'Stat':<22} {'L':>5} {'Side':<5} "
          f"{'RefOdds':>8} {'TP%':>5} {'Proj':>5} {'σ':>5} {'P_dist%':>7} "
          f"{'Edge':>6} {'HR':>4} {'CV':>5} {'TierReason':<20}")
    print("-" * 165)
    for i, r in enumerate(sh_rows, 1):
        team_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
        proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
        sig = f"{r['sigma']:.2f}" if isinstance(r["sigma"], (int, float)) else "—"
        pd = f"{r['p_distribution_pct']:.2f}" if isinstance(r["p_distribution_pct"], (int, float)) else "—"
        cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
        reason = (r["tier_reason"] or "")[:20]
        print(f"{i:>2} {r['player'][:22]:<22} {team_opp:<10} {r['stat'][:22]:<22} "
              f"{str(r['line']):>5} {r['side']:<5} "
              f"{str(r['ref_odds']):>8} {r['tp']:>5} {proj:>5} {sig:>5} {pd:>7} "
              f"{r['edge_pct']:>6} {r['hit_rate']:>4} {cv:>5} {reason:<20}")

    # ── Ladder duplicates ────────────────────────────────────────
    print()
    print("=" * 165)
    print(f"LADDER DUPLICATES IN SH POOL — {len(sh_ladders)} group(s)")
    print("=" * 165)
    if not sh_ladders:
        print("  (none — every SH pick is unique by (player, stat, side))")
    for g in sh_ladders:
        k = g["key"]
        print(f"  {k['player']} / {k['stat']} / {k['side']}")
        for m in g["members"]:
            print(f"    L={m['line']:>5}  ref={m['ref_odds']:>+6}  tp={m['tp']:>5}  "
                  f"p_dist={m['p_dist_pct']:.2f}%  edge={m['edge_pct']:>+6}  tier={m['tier']}")

    # ── Deduped SH passing pool ──────────────────────────────────
    print()
    print("=" * 165)
    print(f"SAFE HAVEN PASSING POOL — DEDUPED ({len(sh_dedup)} unique picks)")
    print("=" * 165)
    print(f"{'#':>2} {'Player':<22} {'TM/OPP':<10} {'Stat':<22} {'L':>5} {'Side':<5} "
          f"{'RefOdds':>8} {'TP%':>5} {'Proj':>5} {'σ':>5} {'P_dist%':>7} "
          f"{'Edge':>6} {'HR':>4} {'CV':>5} {'TierReason':<20}")
    print("-" * 165)
    for i, r in enumerate(sh_dedup, 1):
        team_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
        proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
        sig = f"{r['sigma']:.2f}" if isinstance(r["sigma"], (int, float)) else "—"
        pd = f"{r['p_distribution_pct']:.2f}" if isinstance(r["p_distribution_pct"], (int, float)) else "—"
        cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
        reason = (r["tier_reason"] or "")[:20]
        print(f"{i:>2} {r['player'][:22]:<22} {team_opp:<10} {r['stat'][:22]:<22} "
              f"{str(r['line']):>5} {r['side']:<5} "
              f"{str(r['ref_odds']):>8} {r['tp']:>5} {proj:>5} {sig:>5} {pd:>7} "
              f"{r['edge_pct']:>6} {r['hit_rate']:>4} {cv:>5} {reason:<20}")

    # ── SH-routed rejects (top 20) ───────────────────────────────
    print()
    print("=" * 165)
    print(f"TOP 20 SAFE-HAVEN-ROUTED REJECTS  (ref_odds ≤ -300, tier ≠ safe_haven)")
    print("=" * 165)
    print(f"{'#':>2} {'Player':<22} {'TM/OPP':<10} {'Stat':<22} {'L':>5} {'Side':<5} "
          f"{'RefOdds':>8} {'TP%':>5} {'Proj':>5} {'σ':>5} {'P_dist%':>7} "
          f"{'Edge':>6} {'HR':>4} {'CV':>5} {'TierReason':<35}")
    print("-" * 175)
    for i, r in enumerate(rj_rows, 1):
        team_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
        proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
        sig = f"{r['sigma']:.2f}" if isinstance(r["sigma"], (int, float)) else "—"
        pd = f"{r['p_distribution_pct']:.2f}" if isinstance(r["p_distribution_pct"], (int, float)) else "—"
        cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
        reason = (r["tier_reason"] or "")[:35]
        edge_s = f"{r['edge_pct']:>+6}" if isinstance(r["edge_pct"], (int, float)) else f"{'—':>6}"
        hr_s = f"{r['hit_rate']:>4}" if r["hit_rate"] is not None else f"{'—':>4}"
        tp_s = f"{r['tp']:>5}" if isinstance(r["tp"], (int, float)) else f"{'—':>5}"
        print(f"{i:>2} {(r['player'] or '?')[:22]:<22} {team_opp:<10} {(r['stat'] or '?')[:22]:<22} "
              f"{str(r['line']):>5} {(r['side'] or ''):<5} "
              f"{str(r['ref_odds']):>8} {tp_s} {proj:>5} {sig:>5} {pd:>7} "
              f"{edge_s} {hr_s} {cv:>5} {reason:<35}")

    print()
    print("=" * 165)
    print(f"REJECTS LADDER DUPLICATES — {len(rj_ladders)} group(s)")
    print("=" * 165)
    for g in rj_ladders:
        k = g["key"]
        print(f"  {k['player']} / {k['stat']} / {k['side']}")
        for m in g["members"]:
            print(f"    L={m['line']:>5}  ref={m['ref_odds']:>+6}  tp={m['tp']:>5}  "
                  f"p_dist={m['p_dist_pct']:.2f}%  edge={m['edge_pct']:>+6}  tier={m['tier']}")

    # Save artifact
    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/mlb_sh_pool_postrebuild_{stamp}.json"
    with open(path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version_tag": VERSION,
            "sh_pool_count": len(sh_rows),
            "sh_pool_deduped_count": len(sh_dedup),
            "sh_pool_full": sh_rows,
            "sh_pool_deduped": sh_dedup,
            "sh_ladder_groups": sh_ladders,
            "sh_rejects_top20": rj_rows,
            "sh_rejects_ladder_groups": rj_ladders,
        }, f, default=str, indent=2)
    print()
    print(f"ARTIFACT: {path}")


if __name__ == "__main__":
    asyncio.run(main())
