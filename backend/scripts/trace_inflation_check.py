"""Tasks F + G — cross-check inflation across stat families on 2026-05-06."""
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def go():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    for family in ("total_bases", "hits"):
        print(f"\n{'='*100}")
        print(f"[F/G] Top 25 by μ DESC — stat_family={family}  date=2026-05-06")
        print("="*100)
        # Get unique (player, line, side) → highest μ row per player
        pipeline = [
            {"$match": {"game_date": "2026-05-06",
                        "snapshot_iso": "2026-05-06T11:00:00Z",
                        "stat_family": family,
                        "side": "OVER",
                        "line": 0.5}},
            {"$group": {
                "_id": "$player_name_normalized",
                "name": {"$first": "$player_name"},
                "mu": {"$first": "$projection_mu"},
                "hr10": {"$first": "$hit_rate_l10"},
                "hr20": {"$first": "$hit_rate_l20"},
                "edge": {"$first": "$edge"},
                "team": {"$first": "$team"},
            }},
            {"$sort": {"mu": -1}},
            {"$limit": 25},
        ]
        results = []
        async for r in db.mlb_replay_model_outputs.aggregate(pipeline):
            results.append(r)
        # Pull L10/L20 mean from feature cache + actual TB from BDL
        from collections import defaultdict
        # Actual lookup
        from services.replay.historical_alt_odds_ingest import normalize_player_name
        logs_pipe = [
            {"$project": {"logs": "$bdl_game_logs", "dn": "$display_name", "pn": "$player_name"}},
            {"$unwind": "$logs"},
            {"$project": {
                "d": {"$ifNull": [{"$substr": ["$logs.date", 0, 10]},
                                   {"$substr": ["$logs.game_date", 0, 10]}]},
                "nk": {"$ifNull": ["$dn", "$pn"]},
                "tb": "$logs.total_bases", "h": "$logs.hits",
                "ab": "$logs.at_bats",
            }},
            {"$match": {"d": "2026-05-06"}},
        ]
        actuals_max = {}  # if a player has multiple logs, take MAX (best case)
        actuals_all = {}
        async for r in db.mlb_master_hub_2026.aggregate(logs_pipe, allowDiskUse=True):
            nk = normalize_player_name(r.get("nk") or "")
            v = r.get("tb") if family == "total_bases" else r.get("h")
            ab = r.get("ab")
            cur = actuals_all.get(nk) or []
            cur.append((v, ab))
            actuals_all[nk] = cur
        for nk, lst in actuals_all.items():
            non_none = [x for x in lst if x[0] is not None]
            if non_none:
                actuals_max[nk] = max(non_none)

        print(f"  {'#':>2}  {'Player':<22}  {'μ':>6}  {'L10':>5}  {'L20':>5}  "
              f"{'inflate':>7}  {'actual':>6}(AB)   {'L10_HR%':>7}  {'edge%':>6}")
        for i, r in enumerate(results, 1):
            cache = await db.mlb_replay_feature_cache.find_one(
                {"game_date": "2026-05-06",
                 "player_name_normalized": r["_id"],
                 "stat_family": family})
            l10 = cache.get("l10_mean") if cache else None
            l20 = cache.get("l20_mean") if cache else None
            actual_pair = actuals_max.get(r["_id"])
            actual_val = actual_pair[0] if actual_pair else None
            actual_ab = actual_pair[1] if actual_pair else None
            mu = r["mu"]
            inflate = (mu / l10) if l10 and l10 > 0 else None
            inflate_s = f"{inflate:.2f}x" if inflate else "—"
            act_s = f"{actual_val:g}({actual_ab if actual_ab is not None else '?'})" if actual_val is not None else "—"
            l10s = f"{l10:.2f}" if l10 is not None else "—"
            l20s = f"{l20:.2f}" if l20 is not None else "—"
            print(f"  {i:>2}  {(r['name'] or r['_id'])[:22]:<22}  "
                  f"{mu:>6.2f}  {l10s:>5}  {l20s:>5}  "
                  f"{inflate_s:>7}  {act_s:>10}  "
                  f"{int(r['hr10'] or 0):>6}%   "
                  f"{(r['edge'] or 0)*100:>+5.1f}%")
    cli.close()


asyncio.run(go())
