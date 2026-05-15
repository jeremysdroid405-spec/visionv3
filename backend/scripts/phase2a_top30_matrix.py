"""Compact one-row-per-prop matrix for the top-30 rejects.
Companion to the deep-dive file at top30_comprehensive_2026_05_15.txt.
"""
from __future__ import annotations
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

BATTER_STATS = {"Hits","Total Bases","RBIs","Runs","Home Runs","Doubles",
                "Walks","Singles","Hits+Runs+RBIs","Stolen Bases",
                "Batter Strikeouts"}


def f(v, d=2, default="—", sign=False):
    if v is None: return default
    if isinstance(v, (int,float)):
        try:
            if sign:
                return f"{v:+.{d}f}"
            return f"{v:.{d}f}"
        except: return str(v)
    return str(v)


def odds(v):
    if v is None: return "—"
    try: return f"{int(v):+d}"
    except: return "—"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    cur = db.mlb_prop_scores.find(
        {"active": True, "recommendation": "OVER",
         "routed_tier":"front_lines", "tier":"unqualified",
         "stat_type":{"$in":list(BATTER_STATS)},
         "projection_model_version":"MLB_HF_v3.1_phase2a",
         "hit_rate_l20":{"$ne": None}},
        {"_id":0},
    ).sort([("hit_rate_l20",-1),("edge_vs_fair",-1)])
    rows = await cur.to_list(length=200)
    seen=set(); uniq=[]
    for r in rows:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); uniq.append(r)
        if len(uniq) >= 30: break

    print(f"\n{'#':>2} {'PLAYER':<19} {'STAT':<17} {'LN':>3}  "
          f"{'L5':>3} {'L10':>3} {'L20':>3}  "
          f"{'PROJ':>5} {'µ':>5} {'σ':>5} {'CV':>5}  "
          f"{'p_M':>5} {'fair':>5} {'TP':>5}  "
          f"{'EDGE':>6} {'TE':>6} {'BBE':>6}  "
          f"{'DK':>5} {'FD':>5} {'MGM':>5} {'BK':>2}  "
          f"{'BH':>2} {'VS':<18} {'GATE FAIL'}")
    print("─"*200)
    for i,r in enumerate(uniq,1):
        evf = r.get("edge_vs_fair")
        proj = r.get("model_projection")
        line = r.get("line")
        margin = (proj - line) if (proj is not None and line is not None) else None
        opp_p = r.get("opp_pitcher_name") or "—"
        opp_t = r.get("opp_pitcher_throws") or "?"
        gate = (r.get("tier_reason") or "").replace("front_lines_failed:","").strip()
        # Identify all failing gates
        gr = r.get("tier_gate_results") or {}
        all_fails = [g.replace("_gate","") for g,v in gr.items()
                     if isinstance(v,dict) and v.get("passed") is False]
        gate_disp = ",".join(all_fails) if all_fails else gate

        print(f"{i:>2} {r['player_name'][:19]:<19} "
              f"{r['stat_type'][:17]:<17} "
              f"{f(line,1):>3}  "
              f"{f(r.get('hit_rate_l5'),0):>3} "
              f"{f(r.get('hit_rate_l10'),0):>3} "
              f"{f(r.get('hit_rate_l20'),0):>3}  "
              f"{f(proj,2):>5} "
              f"{f(r.get('distribution_effective_mu'),2):>5} "
              f"{f(r.get('distribution_sigma'),2):>5} "
              f"{f(r.get('cv'),2):>5}  "
              f"{f((r.get('fair_prob') or 0) + (evf or 0),3):>5} "
              f"{f(r.get('fair_prob'),3):>5} "
              f"{f(r.get('tp'),1):>5}  "
              f"{f((evf*100) if evf is not None else None,2,sign=True):>6} "
              f"{f((r.get('total_edge') or 0)*100,2,sign=True):>6} "
              f"{f((r.get('best_book_edge') or 0)*100,2,sign=True):>6}  "
              f"{odds(r.get('dk_odds')):>5} "
              f"{odds(r.get('fd_odds')):>5} "
              f"{odds(r.get('mgm_odds')):>5} "
              f"{r.get('book_count') or 0:>2}  "
              f"{r.get('batter_hand') or '?':>2} "
              f"{(opp_p[:16]+' '+opp_t)[:18]:<18} "
              f"{gate_disp}")


if __name__ == "__main__":
    asyncio.run(main())
