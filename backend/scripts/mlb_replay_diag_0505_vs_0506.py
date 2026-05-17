"""Compare pick-quality distributions between 2026-05-05 and 2026-05-06
to detect whether the disaster day passed weaker picks (bug) or just lost
on similar-quality picks (variance)."""
from __future__ import annotations
import asyncio
import os
import statistics
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _stats(vals):
    if not vals: return None
    return {
        "n": len(vals),
        "min": min(vals), "max": max(vals),
        "mean": sum(vals)/len(vals),
        "median": statistics.median(vals),
        "p10": statistics.quantiles(vals, n=10)[0] if len(vals)>=10 else min(vals),
        "p90": statistics.quantiles(vals, n=10)[-1] if len(vals)>=10 else max(vals),
    }


def _fmt(s):
    if not s: return "no data"
    return (f"n={s['n']:>4}  min={s['min']:.3f}  p10={s['p10']:.3f}  "
            f"median={s['median']:.3f}  mean={s['mean']:.3f}  "
            f"p90={s['p90']:.3f}  max={s['max']:.3f}")


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    for tier in ("safe_haven", "front_lines", "war_zone"):
        ver = {"safe_haven":"mlb_sh_v1_2026_05_16",
               "front_lines":"mlb_fl_v1_2026_05_16",
               "war_zone":"mlb_war_zone_v1_2026_05_16"}[tier]
        print(f"\n{'='*90}\nTIER: {tier.upper()}  ({ver})\n{'='*90}")
        for d in ("2026-05-05", "2026-05-06"):
            cursor = db.mlb_replay_gate_results.find(
                {"game_date": d,
                 "snapshot_iso": f"{d}T11:00:00Z",
                 "gate_config_version": ver,
                 "gate_pass": True},
                {"_id":0,"hit_rate_l5":1,"hit_rate_l10":1,"hit_rate_l20":1,
                 "cv":1,"edge":1,"model_probability":1,"projection_mu":1,
                 "line":1,"side":1,"odds":1,"grade_status":1,"actual":1,
                 "stat_family":1,"production_family":1,"player_name":1,
                 "player_name_normalized":1},
            )
            picks = await cursor.to_list(None)
            n = len(picks)
            wins   = [p for p in picks if p["grade_status"]=="win"]
            losses = [p for p in picks if p["grade_status"]=="loss"]
            ungraded = [p for p in picks if p["grade_status"]=="ungraded"]
            gap = lambda p: (p["projection_mu"]-p["line"]) if p["side"]=="OVER" else (p["line"]-p["projection_mu"])

            print(f"\n  {d}  picks={n}  wins={len(wins)}  losses={len(losses)}  ungraded={len(ungraded)}")
            print(f"    HR_L20  : {_fmt(_stats([p['hit_rate_l20'] for p in picks if p.get('hit_rate_l20') is not None]))}")
            print(f"    HR_L10  : {_fmt(_stats([p['hit_rate_l10'] for p in picks if p.get('hit_rate_l10') is not None]))}")
            print(f"    HR_L5   : {_fmt(_stats([p['hit_rate_l5']  for p in picks if p.get('hit_rate_l5')  is not None]))}")
            print(f"    CV      : {_fmt(_stats([p['cv']           for p in picks if p.get('cv')           is not None]))}")
            print(f"    Edge(%) : {_fmt(_stats([p['edge']*100     for p in picks if p.get('edge')         is not None]))}")
            print(f"    Model_P : {_fmt(_stats([p['model_probability']*100 for p in picks if p.get('model_probability') is not None]))}")
            print(f"    μ-Line  : {_fmt(_stats([gap(p) for p in picks if p.get('projection_mu') is not None and p.get('line') is not None]))}")
            print(f"    Odds    : {_fmt(_stats([float(p['odds']) for p in picks]))}")

            # Stat-family breakdown
            from collections import Counter
            fam_counter = Counter(p.get("production_family") or p.get("stat_family") for p in picks)
            wins_fam = Counter(p.get("production_family") or p.get("stat_family") for p in wins)
            losses_fam = Counter(p.get("production_family") or p.get("stat_family") for p in losses)
            print(f"    By stat family (n / W / L):")
            for fam, count in fam_counter.most_common():
                print(f"        {fam:<22} {count:>4}  /  {wins_fam.get(fam,0):>3}  /  {losses_fam.get(fam,0):>3}")

    print(f"\n{'='*90}")
    print("FEATURE CACHE INTEGRITY CHECK")
    print(f"{'='*90}")
    # Do the L5/L10/L20 hit rates on 05-06 actually reflect rolling window through 05-05?
    # Pick a few losing players on 05-06 and verify their actual recent game log history.
    losers_5_06 = await db.mlb_replay_gate_results.find(
        {"game_date":"2026-05-06","gate_pass":True,"grade_status":"loss",
         "tier":"war_zone"},
        {"_id":0,"player_name":1,"player_name_normalized":1,"stat_family":1,
         "production_family":1,"line":1,"side":1,"hit_rate_l5":1,
         "hit_rate_l10":1,"hit_rate_l20":1,"projection_mu":1,"actual":1,"market":1},
    ).limit(8).to_list(8)

    for p in losers_5_06:
        pn = p["player_name_normalized"]
        # Find their last 20 game logs prior to 05-06 from BDL
        pipeline = [
            {"$project": {"logs": "$bdl_game_logs"}},
            {"$unwind": "$logs"},
            {"$project": {
                "d": {"$ifNull":[{"$substr":["$logs.date",0,10]},{"$substr":["$logs.game_date",0,10]}]},
                "name_norm": "$logs.player_name_normalized",
                "name_full": "$logs.player_name",
                "stats": "$logs",
            }},
            {"$match": {"d": {"$lt":"2026-05-06"}}},
        ]
        logs = []
        async for g in db.mlb_master_hub_2026.aggregate(pipeline, allowDiskUse=True):
            gname = (g.get("name_norm") or g.get("name_full") or "").lower()
            if pn.lower() in gname or gname in pn.lower():
                logs.append((g["d"], g["stats"]))
        logs.sort(reverse=True)
        recent = logs[:20]
        fam = p.get("production_family") or p.get("stat_family")
        # Pull the relevant stat for each game
        stat_key_map = {
            "hits":"hits","total_bases":"total_bases","runs":"runs","rbis":"rbis",
            "strikeouts":"strikeouts","pitcher_strikeouts":"pitcher_strikeouts",
            "batter_strikeouts":"strikeouts","walks_allowed":"pitcher_walks",
            "earned_runs":"earned_runs","pitcher_walks":"pitcher_walks",
            "hits_runs_rbis":"hits_runs_rbis"
        }
        sk = stat_key_map.get(fam, fam)
        recent_vals = [r[1].get(sk) for r in recent[:20]]
        # Independent recompute of "hit rate" against 05-06 pick's line
        line = p["line"]; side = p["side"]
        def covers(v):
            if v is None: return None
            return (v > line) if side == "OVER" else (v < line)
        hr_l5_independent  = [covers(v) for v in recent_vals[:5]  if v is not None]
        hr_l10_independent = [covers(v) for v in recent_vals[:10] if v is not None]
        hr_l20_independent = [covers(v) for v in recent_vals[:20] if v is not None]
        ihr5  = (100.0*sum(1 for x in hr_l5_independent  if x)/len(hr_l5_independent))  if hr_l5_independent  else None
        ihr10 = (100.0*sum(1 for x in hr_l10_independent if x)/len(hr_l10_independent)) if hr_l10_independent else None
        ihr20 = (100.0*sum(1 for x in hr_l20_independent if x)/len(hr_l20_independent)) if hr_l20_independent else None
        print(f"\n  {p['player_name']:<22}  {fam:<14}  line {p['line']} {p['side']}")
        print(f"    recorded   HR L5/L10/L20  =  "
              f"{int(p.get('hit_rate_l5') or 0)}/"
              f"{int(p.get('hit_rate_l10') or 0)}/"
              f"{int(p.get('hit_rate_l20') or 0)}")
        print(f"    independent HR L5/L10/L20 =  "
              f"{ihr5:.1f}/{ihr10:.1f}/{ihr20:.1f}" if ihr5 is not None else
              f"    independent HR — insufficient logs")
        print(f"    recorded μ projection     =  {p.get('projection_mu')}")
        print(f"    actual outcome on 05-06   =  {p.get('actual')}")
        print(f"    last 20 actual values     =  {recent_vals}")
        print(f"    dates of last 20 games    =  {[r[0] for r in recent[:20]]}")
    cli.close()

if __name__ == "__main__":
    asyncio.run(amain())
