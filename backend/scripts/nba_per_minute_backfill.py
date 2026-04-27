"""
NBA Per-Minute Rate Backfill
=================================
Computes canonical per-minute rates for every player with bdl_game_logs
and writes them into `nba_master_hub_2026[<player>].season_per_minute`.

For each player:
  1. Season totals from latest-season logs
       total_minutes, total_pts, total_reb, total_ast, total_3pm, total_pra
  2. Per-minute season rates
       pts_per_min, reb_per_min, ast_per_min, threes_per_min, pra_per_min
  3. Recency rates (L3, L5, L10) computed as sum(stats) / sum(minutes)
       — NOT mean of per-game rates (sum-divided is the correct rate).
  4. Blended rate per stat:
       rate_blended = 0.50 × season + 0.30 × L10 + 0.20 × L3
     Falls back proportionally when one or more inputs is missing.

Writes to: nba_master_hub_2026[<player>].season_per_minute = {
    season_year, games_played, games_with_minutes,
    season: { totals: {...}, per_min: {...} },
    l10:    { totals: {...}, per_min: {...} },
    l5:     { totals: {...}, per_min: {...} },
    l3:     { totals: {...}, per_min: {...} },
    blended_per_min: {pts, reb, ast, threes, pra},
    blended_weights: {season: 0.50, l10: 0.30, l3: 0.20},
    backfilled_at: <ISO>,
}

Idempotent — overwrites the `season_per_minute` subdoc only.
Does NOT change projection / scoring logic.
"""
import os
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


BLEND_W = {"season": 0.50, "l10": 0.30, "l3": 0.20}


def _coerce(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _aggregate(logs: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sum stats + minutes across a slice of game logs."""
    out = {"games": 0, "games_with_minutes": 0,
           "minutes": 0.0,
           "pts": 0.0, "reb": 0.0, "ast": 0.0, "threes": 0.0,
           "stl": 0.0, "blk": 0.0}
    for log in logs:
        out["games"] += 1
        m = _coerce(log.get("min"))
        if m is not None and m > 0:
            out["games_with_minutes"] += 1
            out["minutes"] += m
        for src, dst in [("pts", "pts"), ("reb", "reb"), ("ast", "ast"),
                          ("fg3m", "threes"), ("stl", "stl"), ("blk", "blk")]:
            v = _coerce(log.get(src))
            if v is not None:
                out[dst] += v
    out["pra"] = out["pts"] + out["reb"] + out["ast"]
    return out


def _per_min(totals: Dict[str, float]) -> Dict[str, Optional[float]]:
    """Convert summed totals into per-minute rates (sum(stat)/sum(min))."""
    m = totals.get("minutes", 0.0)
    if not m or m <= 0:
        return {k: None for k in ("pts", "reb", "ast", "threes", "pra")}
    return {
        "pts":    totals["pts"]    / m,
        "reb":    totals["reb"]    / m,
        "ast":    totals["ast"]    / m,
        "threes": totals["threes"] / m,
        "pra":    totals["pra"]    / m,
    }


def _blend(season_r, l10_r, l3_r) -> Dict[str, Optional[float]]:
    """Per-stat weighted blend of three rate dicts."""
    out: Dict[str, Optional[float]] = {}
    for stat in ("pts", "reb", "ast", "threes", "pra"):
        parts = []
        if season_r and season_r.get(stat) is not None:
            parts.append((season_r[stat], BLEND_W["season"]))
        if l10_r and l10_r.get(stat) is not None:
            parts.append((l10_r[stat], BLEND_W["l10"]))
        if l3_r and l3_r.get(stat) is not None:
            parts.append((l3_r[stat], BLEND_W["l3"]))
        if not parts:
            out[stat] = None
            continue
        wsum = sum(w for _, w in parts)
        out[stat] = sum(v * (w / wsum) for v, w in parts) if wsum > 0 else None
    return out


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cur = db.nba_master_hub_2026.find(
        {"bdl_game_logs_count": {"$gt": 0}},
        {"_id": 1, "display_name": 1, "player_name": 1,
         "bdl_id": 1, "bdl_player_id": 1, "bdl_game_logs": 1})

    n_processed = n_updated = n_skipped = 0
    coverage = {"season_per_min": 0, "l10": 0, "l5": 0, "l3": 0,
                "blend_pts": 0, "blend_reb": 0, "blend_ast": 0,
                "blend_threes": 0, "blend_pra": 0}
    samples: List[Dict[str, Any]] = []
    SAMPLE_TARGETS = {"Nikola Jokic", "Joel Embiid", "Giannis Antetokounmpo",
                      "Victor Wembanyama", "Anthony Edwards",
                      "Stephen Curry", "Luka Doncic", "Devin Booker",
                      "Bam Adebayo", "Domantas Sabonis"}

    async for d in cur:
        n_processed += 1
        oid = d["_id"]
        nm = (d.get("display_name") or "").strip() \
             or (d.get("player_name") or "").strip()
        if not nm:
            n_skipped += 1; continue
        logs_all: List[Dict[str, Any]] = d.get("bdl_game_logs") or []
        if not logs_all:
            n_skipped += 1; continue

        # Latest season slice
        seasons = {l.get("season") for l in logs_all if l.get("season") is not None}
        cur_season = max(seasons) if seasons else None
        season_logs = [l for l in logs_all if l.get("season") == cur_season] \
                       if cur_season is not None else logs_all

        # Sort by date desc — recency slices come from this
        season_logs.sort(key=lambda log: log.get("date") or "", reverse=True)

        season_t = _aggregate(season_logs)
        l3_t  = _aggregate(season_logs[:3])
        l5_t  = _aggregate(season_logs[:5])
        l10_t = _aggregate(season_logs[:10])

        season_pm = _per_min(season_t)
        l3_pm     = _per_min(l3_t)
        l5_pm     = _per_min(l5_t)
        l10_pm    = _per_min(l10_t)

        blended = _blend(season_pm, l10_pm, l3_pm)

        # Build the subdocument
        subdoc = {
            "season_year": cur_season,
            "games_played": season_t["games"],
            "games_with_minutes": season_t["games_with_minutes"],
            "season": {
                "totals": {
                    "minutes": round(season_t["minutes"], 2),
                    "pts": round(season_t["pts"], 1),
                    "reb": round(season_t["reb"], 1),
                    "ast": round(season_t["ast"], 1),
                    "threes": round(season_t["threes"], 1),
                    "pra": round(season_t["pra"], 1),
                },
                "per_min": {k: (round(v, 6) if v is not None else None)
                             for k, v in season_pm.items()},
                "per_game": {
                    "minutes": round(season_t["minutes"] / season_t["games_with_minutes"], 3)
                                if season_t["games_with_minutes"] else None,
                    "pts": round(season_t["pts"] / season_t["games"], 3) if season_t["games"] else None,
                    "reb": round(season_t["reb"] / season_t["games"], 3) if season_t["games"] else None,
                    "ast": round(season_t["ast"] / season_t["games"], 3) if season_t["games"] else None,
                    "threes": round(season_t["threes"] / season_t["games"], 3) if season_t["games"] else None,
                    "pra": round(season_t["pra"] / season_t["games"], 3) if season_t["games"] else None,
                },
            },
            "l10": {
                "totals": {k: round(season_l["minutes" if k == "minutes" else k], 2)
                            if (season_l := l10_t).get(k) is not None else None
                            for k in ("minutes", "pts", "reb", "ast", "threes", "pra")},
                "per_min": {k: (round(v, 6) if v is not None else None)
                             for k, v in l10_pm.items()},
            },
            "l5": {
                "totals": {k: round(l5_t[k], 2) for k in
                            ("minutes", "pts", "reb", "ast", "threes", "pra")},
                "per_min": {k: (round(v, 6) if v is not None else None)
                             for k, v in l5_pm.items()},
            },
            "l3": {
                "totals": {k: round(l3_t[k], 2) for k in
                            ("minutes", "pts", "reb", "ast", "threes", "pra")},
                "per_min": {k: (round(v, 6) if v is not None else None)
                             for k, v in l3_pm.items()},
            },
            "blended_per_min": {k: (round(v, 6) if v is not None else None)
                                 for k, v in blended.items()},
            "blended_weights": BLEND_W,
            "backfilled_at": datetime.now(timezone.utc).isoformat(),
        }

        await db.nba_master_hub_2026.update_one(
            {"_id": oid},
            {"$set": {"season_per_minute": subdoc}},
        )
        n_updated += 1

        # Coverage tally
        if season_pm["pts"] is not None: coverage["season_per_min"] += 1
        if l10_pm["pts"]    is not None: coverage["l10"] += 1
        if l5_pm["pts"]     is not None: coverage["l5"] += 1
        if l3_pm["pts"]     is not None: coverage["l3"] += 1
        for stat in ("pts", "reb", "ast", "threes", "pra"):
            if blended.get(stat) is not None:
                coverage[f"blend_{stat}"] += 1

        if nm in SAMPLE_TARGETS:
            samples.append({
                "name": nm,
                "season": cur_season,
                "GP": season_t["games"],
                "MIN": season_t["minutes"],
                "MPG": season_t["minutes"] / season_t["games_with_minutes"]
                       if season_t["games_with_minutes"] else None,
                "PTS_total": season_t["pts"],
                "REB_total": season_t["reb"],
                "AST_total": season_t["ast"],
                "season_pts_pm": season_pm["pts"],
                "season_reb_pm": season_pm["reb"],
                "season_ast_pm": season_pm["ast"],
                "l10_pts_pm": l10_pm["pts"],
                "l10_reb_pm": l10_pm["reb"],
                "l3_pts_pm": l3_pm["pts"],
                "l3_reb_pm": l3_pm["reb"],
                "blend_pts_pm": blended["pts"],
                "blend_reb_pm": blended["reb"],
                "blend_ast_pm": blended["ast"],
            })

    print(f"[BACKFILL] Processed {n_processed}, updated {n_updated}, "
          f"skipped {n_skipped}")
    print("\n=== COVERAGE %% =================================================")
    base = max(n_updated, 1)
    for k, v in coverage.items():
        print(f"  {k:25s} {v:>4d} / {n_updated}  ({v/base*100:.1f}%)")

    # Players missing season per-min entirely
    missing = await db.nba_master_hub_2026.count_documents({
        "bdl_game_logs_count": {"$gt": 0},
        "$or": [
            {"season_per_minute": {"$exists": False}},
            {"season_per_minute.season.per_min.pts": None},
        ]})
    print(f"\n  Players missing season per-min: {missing}")

    # Sanity check on 10 stars
    print("\n=== SAMPLE VALIDATION (10 stars) ============================")
    print(f"  {'player':25s} {'GP':>3s} {'MPG':>5s}  "
          f"{'season_pts/min':>14s} {'l10_pts/min':>11s} {'l3_pts/min':>10s} "
          f"{'BLEND_pts/min':>13s}")
    for s in sorted(samples, key=lambda x: x["name"]):
        mpg = f"{s['MPG']:.1f}" if s.get('MPG') is not None else "—"
        sp  = f"{s['season_pts_pm']:.4f}" if s.get('season_pts_pm') is not None else "—"
        lp  = f"{s['l10_pts_pm']:.4f}"    if s.get('l10_pts_pm')    is not None else "—"
        l3p = f"{s['l3_pts_pm']:.4f}"     if s.get('l3_pts_pm')     is not None else "—"
        bp  = f"{s['blend_pts_pm']:.4f}"  if s.get('blend_pts_pm')  is not None else "—"
        print(f"  {s['name'][:25]:25s} {s['GP']:>3d} {mpg:>5s}  "
              f"{sp:>14s} {lp:>11s} {l3p:>10s} {bp:>13s}")

    print(f"\n  {'player':25s}  {'season_reb/min':>14s} {'BLEND_reb/min':>13s}  "
          f"{'season_ast/min':>14s} {'BLEND_ast/min':>13s}")
    for s in sorted(samples, key=lambda x: x["name"]):
        sr = f"{s['season_reb_pm']:.4f}" if s.get('season_reb_pm') is not None else "—"
        br = f"{s['blend_reb_pm']:.4f}"  if s.get('blend_reb_pm')  is not None else "—"
        sa = f"{s['season_ast_pm']:.4f}" if s.get('season_ast_pm') is not None else "—"
        ba = f"{s['blend_ast_pm']:.4f}"  if s.get('blend_ast_pm')  is not None else "—"
        print(f"  {s['name'][:25]:25s}  {sr:>14s} {br:>13s}  "
              f"{sa:>14s} {ba:>13s}")

    # Verify writes by reading one back
    print("\n=== ROUND-TRIP VERIFICATION =================================")
    chk = await db.nba_master_hub_2026.find_one(
        {"display_name": "Nikola Jokic"},
        {"_id": 0, "season_per_minute": 1})
    if chk and chk.get("season_per_minute"):
        sp = chk["season_per_minute"]
        print(f"  Jokic.season_per_minute.season_year      = {sp.get('season_year')}")
        print(f"  Jokic.season_per_minute.games_played     = {sp.get('games_played')}")
        print(f"  Jokic.season_per_minute.season.per_min.pts = {sp['season']['per_min']['pts']}")
        print(f"  Jokic.season_per_minute.l10.per_min.pts    = {sp['l10']['per_min']['pts']}")
        print(f"  Jokic.season_per_minute.l3.per_min.pts     = {sp['l3']['per_min']['pts']}")
        print(f"  Jokic.season_per_minute.blended_per_min.pts = "
              f"{sp['blended_per_min']['pts']}")
        print(f"  Jokic.season_per_minute.blended_weights    = "
              f"{sp.get('blended_weights')}")
        print(f"  Jokic.season_per_minute.backfilled_at      = "
              f"{sp.get('backfilled_at')}")
    else:
        print("  ERROR: Round-trip read returned no data for Jokic")

    print("\n[BACKFILL] DONE")


if __name__ == "__main__":
    asyncio.run(main())
