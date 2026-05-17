"""Test: how much does each feature group contribute to Olson's 7.9 μ?
Run controlled ablations on the feature vector."""
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
import numpy as np, pandas as pd
from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_replay_engine import _build_player_dict, _build_game_logs


async def go():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db.delegate)
    model.load_models()

    cache_row = await db.mlb_replay_feature_cache.find_one(
        {"game_date": "2026-05-06", "player_name_normalized": "matt olson",
         "stat_family": "total_bases"}, {"_id":0})
    odds = await db.mlb_historical_alt_odds_raw.find_one(
        {"game_date": "2026-05-06", "snapshot_iso": "2026-05-06T11:00:00Z",
         "player_name_normalized": "matt olson", "market": "batter_total_bases"})

    player = _build_player_dict(cache_row)
    game_logs = _build_game_logs(cache_row)
    sc_self = cache_row.get("statcast_self_as_of")
    feats = model._build_friction_features(
        player, game_logs, "total_bases",
        opponent="Seattle Mariners", park_team="Seattle Mariners",
        dk_odds=None, line=0.5,
        statcast_features=sc_self, pitcher_statcast_features=None,
        pa_batter_features=None, pa_pitcher_features=None,
    )
    cols = model.feature_cols["total_bases"]
    scaler = model.scalers["total_bases"]
    mdl = model.models["total_bases"]

    def predict(d):
        X = pd.DataFrame([d])
        for c in cols:
            if c not in X.columns: X[c] = 0
        return float(mdl.predict(scaler.transform(X[cols].fillna(0)))[0])

    base = predict(feats)
    print(f"BASELINE prediction (full feature vector):     {base:.4f}")

    # Zero EVERYTHING
    zero = {c: 0.0 for c in cols}
    print(f"\n[A] All-zero feature vector:                   {predict(zero):.4f}")

    # Only rolling stats (the 22 non-zero rolling-window features)
    rolling = {c: 0.0 for c in cols}
    rolling_keys = ["l3_avg","l5_avg","l10_avg","l20_avg","l5_median","l10_median",
                    "l5_max","l10_max","l5_min","l10_min",
                    "ewma_l5","ewma_l10","ewma_l20","ewma_trend",
                    "std_dev_l5","std_dev_l10","cv_l5","cv_l10",
                    "range_l5","range_l10",
                    "current_hit_streak","current_miss_streak",
                    "hit_rate_l5","hit_rate_l10",
                    "line_difficulty","line_vs_ewma","line_vs_l10","line_vs_l5","line_vs_median",
                    "expected_pa_l10","line"]
    for k in rolling_keys:
        if k in feats and k in cols: rolling[k] = feats[k]
    print(f"\n[B] ONLY rolling-stats + expected_pa + line:    {predict(rolling):.4f}")

    # Add park factors
    pf = dict(rolling)
    for k in ("park_factor","park_hits_factor","park_hr_factor","park_k_factor",
              "park_runs_factor","park_tb_factor","park_factor_is_imputed",
              "opp_k_rate"):
        if k in feats and k in cols: pf[k] = feats[k]
    print(f"[C] B + park factors + opp_k_rate:              {predict(pf):.4f}")

    # Add Statcast batter
    sc_test = dict(pf)
    for k in cols:
        if k.startswith("sc_b_") and k in feats: sc_test[k] = feats[k]
    print(f"[D] C + Statcast batter rolling (sc_b_*):       {predict(sc_test):.4f}")

    # Add all the imputed=1 flags
    imp = dict(sc_test)
    for k in cols:
        if k.endswith("_is_imputed") and k in feats:
            imp[k] = feats[k]
    print(f"[E] D + all *_is_imputed flags (set to 1):      {predict(imp):.4f}")

    # Same as the actual replay output:
    full = predict(feats)
    print(f"[F] Full feature vector (replay-equivalent):    {full:.4f}")

    # Diagnose: what's the model's intercept-ish behavior?
    # Use a NEUTRAL player profile (L5/10/20 mean = 1.0, etc.)
    neutral = {c: 0.0 for c in cols}
    for k in ("l3_avg","l5_avg","l10_avg","l20_avg","l5_median","l10_median",
             "ewma_l5","ewma_l10","ewma_l20"):
        if k in cols: neutral[k] = 1.0
    for k in ("l5_max","l10_max"): 
        if k in cols: neutral[k] = 2.0
    for k in ("std_dev_l5","std_dev_l10"): 
        if k in cols: neutral[k] = 1.0
    for k in ("park_factor","park_hits_factor","park_hr_factor","park_k_factor",
              "park_runs_factor","park_tb_factor","opp_k_rate"):
        if k in cols: neutral[k] = 1.0
    if "line" in cols: neutral["line"] = 0.5
    if "expected_pa_l10" in cols: neutral["expected_pa_l10"] = 4.0
    print(f"\n[Z] Neutral player (L_avg=1.0, PA=4, park=1.0): {predict(neutral):.4f}")

    # And a 'normal' player (L10=1.0 — league-avg TB/game)
    print()
    print(f"  → Olson's L10 TB mean is 3.2")
    print(f"  → Neutral test with L_avg=1.0 should yield μ near 1.0")
    print(f"  → If [Z] >> 1.0 (e.g. 4-6), the model bias is the problem")
    print(f"  → If [Z] ≈ 1.0, the model is using rolling stats to over-extrapolate Olson")

    cli.close()

asyncio.run(go())
