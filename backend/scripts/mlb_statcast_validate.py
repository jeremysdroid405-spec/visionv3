"""
MLB Statcast Validator
======================
Sanity-check the ingested raw + feature collections.

Checks (per spec):
  * Row count > 0 in both raw + features
  * Required Statcast fields present on raw rows
  * xwOBA / hard_hit_rate / barrel_rate populated on rolling_30
  * Player coverage: how many batters in mlb_master_hub_2026 have
    at least one feature row joinable by normalized name
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mlb_statcast_validate")

RAW = "mlb_statcast_raw"
FEAT = "mlb_statcast_player_features"
HUB = "mlb_master_hub_2026"


def _norm(s):
    if not s: return None
    return s.strip().lower()


async def validate(db) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    # ---- Raw row count ----
    n_raw = await db[RAW].count_documents({})
    checks.append(dict(name="raw rows > 0", ok=n_raw > 0,
                        detail=f"n={n_raw:,}"))

    # ---- Required raw fields present ----
    required_fields = ["game_pk", "game_date", "batter", "pitcher",
                        "events", "description", "launch_speed",
                        "launch_angle", "estimated_woba_using_speedangle",
                        "woba_value", "bb_type", "pitch_type",
                        "release_speed", "stand", "p_throws",
                        "plate_x", "plate_z"]
    if n_raw:
        sample = await db[RAW].find_one({})
        for f in required_fields:
            checks.append(dict(name=f"raw.{f} present",
                                 ok=(f in sample),
                                 detail=f"value={sample.get(f)!r}"))

    # ---- Feature row count ----
    n_feat = await db[FEAT].count_documents({})
    checks.append(dict(name="features rows > 0", ok=n_feat > 0,
                        detail=f"n={n_feat:,}"))

    # ---- Population of key metrics on rolling_30 ----
    if n_feat:
        n_xwoba = await db[FEAT].count_documents(
            {"rolling_30.xwOBA": {"$ne": None}})
        n_hard  = await db[FEAT].count_documents(
            {"rolling_30.hard_hit_rate": {"$ne": None}})
        n_barrel = await db[FEAT].count_documents(
            {"rolling_30.barrel_rate": {"$ne": None}})
        n_woba = await db[FEAT].count_documents(
            {"rolling_30.wOBA": {"$ne": None}})
        n_k = await db[FEAT].count_documents(
            {"rolling_30.k_rate": {"$ne": None}})
        n_whiff = await db[FEAT].count_documents(
            {"rolling_30.whiff_rate": {"$ne": None}})
        for f, n in [("xwOBA", n_xwoba), ("wOBA", n_woba),
                      ("hard_hit_rate", n_hard),
                      ("barrel_rate", n_barrel),
                      ("k_rate", n_k), ("whiff_rate", n_whiff)]:
            checks.append(dict(
                name=f"rolling_30.{f} populated > 0",
                ok=n > 0,
                detail=f"n={n:,}/{n_feat:,} "
                        f"({n/max(n_feat,1)*100:.1f}%)"))

    # ---- Player mapping coverage ----
    feat_names = {x for x in await db[FEAT].distinct("player_name") if x}
    feat_names_norm = {_norm(x) for x in feat_names if x}
    hub_names = []
    async for d in db[HUB].find({"is_batter": True},
                                  {"_id": 0, "player_name": 1,
                                    "display_name": 1}):
        for cand in (d.get("display_name"), d.get("player_name")):
            if cand: hub_names.append(_norm(cand)); break
    n_hub = len(hub_names)
    n_join = sum(1 for n in hub_names if n in feat_names_norm)
    checks.append(dict(
        name="hub→features join coverage > 50%",
        ok=(n_join / max(n_hub, 1) >= 0.5) if n_hub else False,
        detail=f"{n_join:,}/{n_hub:,} batters joined "
                f"({n_join/max(n_hub,1)*100:.1f}%)"))

    return checks


async def _amain() -> None:
    p = argparse.ArgumentParser()
    p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    checks = await validate(db)
    print("=" * 78)
    print(" MLB STATCAST VALIDATOR")
    print("=" * 78)
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        print(f"  [{mark}]  {c['name']:46s} {c['detail']}")
    print()
    n_pass = sum(1 for c in checks if c["ok"])
    print(f"  RESULT: {n_pass}/{len(checks)} checks PASS")


if __name__ == "__main__":
    asyncio.run(_amain())
