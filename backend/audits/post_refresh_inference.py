"""
Post-refresh model inference rerun for Witt & Garcia.
Lean — no PA cache load (avoids OOM).
"""
import os, sys, pickle
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv
from scipy import stats as _st

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
from services.mlb_high_friction_model import MLBHighFrictionModel

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
model = MLBHighFrictionModel(db)
model.load_models()

PLAYERS = [("Bobby Witt Jr.", "KC", "OAK"), ("Maikel Garcia", "KC", "OAK")]
LINE = 0.5

print(f"\n=== POST-REFRESH inference (Statcast now fresh through 2026-05-15) ===\n")
print(f"{'Player':<22} {'raw_pred':>9} {'σ':>6} {'z':>7} {'P(over)%':>9} {'L5_K':>5} {'L10_K':>6}")
print("-" * 75)

for name, team, opp in PLAYERS:
    p = model.master_hub.find_one({"display_name": name}, {"_id": 0})
    logs = p.get("bdl_game_logs", [])
    sc = model._get_batter_sc_latest(p)
    # PA cache deliberately skipped — pa_b_* features will be zero/imputed
    feats = model._build_friction_features(
        p, logs, "strikeouts",
        opponent=opp, park_team=team, dk_odds=None, line=LINE,
        statcast_features=sc, pa_batter_features=None,
        pitcher_statcast_features=None, pa_pitcher_features=None,
    )
    cols = model.feature_cols["strikeouts"]
    X = pd.DataFrame([feats])
    for c in cols:
        if c not in X.columns:
            X[c] = 0
    X = X[cols].fillna(0)
    Xs = model.scalers["strikeouts"].transform(X)
    raw = float(model.models["strikeouts"].predict(Xs)[0])
    std = feats.get("std_dev_l10", 0.0)
    z = (LINE - raw) / std if std > 0 else None
    p_over = (1 - _st.norm.cdf(z)) * 100 if z is not None else None
    if p_over is not None and raw < LINE and p_over >= 50:
        p_over = max(5, 50 - abs(z) * 10)

    l5 = feats.get("l5_avg")
    l10 = feats.get("l10_avg")
    sc_r14 = sc.get("rolling_14", {}).get("k_rate") if isinstance(sc, dict) else None
    print(f"{name:<22} {raw:>9.4f} {std:>6.3f} {z:>7.3f} {p_over:>9.2f} {l5:>5.2f} {l10:>6.2f}")
    print(f"  Statcast r14 k_rate now: {sc_r14}")
