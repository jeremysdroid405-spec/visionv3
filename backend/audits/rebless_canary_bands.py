"""One-shot re-bless of the μ canary bands using actual predict() values.

For each canary, calls predict() once, captures μ, then writes a new
JSON with bands = [μ - 0.30, μ + 0.30] (a wider 0.6-wide window around
the observed μ). Run after intentional model retraining or band
calibration. Read-only against DB except for the canary JSON write.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import json
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient
from services.mlb_high_friction_model import MLBHighFrictionModel

CANARY_FILE = "/app/backend/audits/replay_mu_canaries.json"
BAND_HALF_WIDTH = 0.30


def main():
    with open(CANARY_FILE) as fh:
        cfg = json.load(fh)
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db)
    model.load_models()

    print(f"{'#':>3}  {'player':>18}  {'stat':>14}  {'line':>5}  "
          f"{'μ':>8}  {'new_band':>14}  source")
    for i, c in enumerate(cfg["canaries"], 1):
        r = model.predict(
            player_name=c["name"], stat_type=c["stat"], line=c["line"],
            opponent_team=c.get("opponent_team", "Atlanta Braves"),
            park_team=c.get("park_team", "Atlanta Braves"),
            batter_hand=c.get("batter_hand"),
            opp_pitcher_throws=c.get("opp_pitcher_throws"),
            as_of_date=c["date"],
        )
        mu = r.get("predicted")
        if mu is None:
            print(f"{i:>3}  {c['name']:>18}  {c['stat']:>14}  "
                  f"{c['line']:>5}  SKIP {r.get('error','')}")
            continue
        new_low = max(0.0, round(mu - BAND_HALF_WIDTH, 2))
        new_high = round(mu + BAND_HALF_WIDTH, 2)
        old_band = f"[{c['mu_low']},{c['mu_high']}]"
        new_band = f"[{new_low},{new_high}]"
        c["mu_low"] = new_low
        c["mu_high"] = new_high
        c["_reblessed_at"] = "2026-05-17"
        c["_reblessed_mu"] = round(mu, 4)
        print(f"{i:>3}  {c['name']:>18}  {c['stat']:>14}  "
              f"{c['line']:>5}  {mu:>8.4f}  {new_band:>14}  "
              f"was {old_band}")

    cfg["_last_reblessed"] = "2026-05-17"
    cfg["_band_half_width"] = BAND_HALF_WIDTH
    with open(CANARY_FILE, "w") as fh:
        json.dump(cfg, fh, indent=2)
    print(f"\nrewrote {CANARY_FILE} with re-blessed bands")
    client.close()


if __name__ == "__main__":
    main()
