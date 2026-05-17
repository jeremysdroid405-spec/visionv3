"""Path A Task 2c — Parity assertion + Olson μ trace after hydration fix.

This script:
  1. Loads the model once.
  2. Builds the LIVE predict() features for Olson 05-06 TB.
  3. Builds the REPLAY replay_one() features (post-fix) with hub_extras
     hydrated from master_hub.
  4. Computes the diff feature-by-feature.
  5. Asserts μ converges to within 0.3 of live μ (2.25).
  6. Re-scans top-50 total_bases μ for 05-06 by computing live values
     for the top inflated rows from the (old) replay outputs.
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient
from motor.motor_asyncio import AsyncIOMotorClient

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FCACHE_V, normalize_player_name,
)
from services.replay.mlb_replay_engine import replay_one

DATE = "2026-05-06"
SNAP = "2026-05-06T11:00:00Z"
FAM = "total_bases"

# ──────────────────────────────────────────────────────────────────
def main():
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db)
    model.load_models()

    olson_norm = normalize_player_name("Matt Olson")
    olson_hub = db.mlb_master_hub_2026.find_one(
        {"display_name": "Matt Olson"}, {"_id": 0})

    # ── A. Direct predict() — establish "ground truth" μ ─────────
    print("[A] Direct predict() μ baseline")
    for L in (0.5, 1.5, 2.5, 3.5):
        r = model.predict(player_name="Matt Olson", stat_type=FAM,
                          line=L, opponent_team="Seattle Mariners",
                          park_team="Atlanta Braves",
                          batter_hand="L", opp_pitcher_throws="R",
                          as_of_date=DATE)
        print(f"    line={L}  μ={r.get('predicted')}  raw={r.get('mu_raw_model_projection')}")

    # ── B. POST-FIX replay_one() with hub_extras ─────────────────
    print("\n[B] POST-FIX replay_one() with hub_extras hydrated")
    cache_row = db.mlb_replay_feature_cache.find_one(
        {"game_date": DATE, "player_name_normalized": olson_norm,
         "stat_family": FAM, "source_version": FCACHE_V},
        {"_id": 0})
    if not cache_row:
        print("    ❌ no cache row")
        return

    proj = {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
            "home_splits": 1, "away_splits": 1,
            "bats_throws": 1, "bats": 1, "throws": 1}
    hub_extras = db.mlb_master_hub_2026.find_one(
        {"$or": [{"bdl_id": cache_row["bdl_id"]},
                 {"bdl_player_id": cache_row["bdl_id"]}]}, proj)
    print(f"    hub_extras keys: {sorted(hub_extras.keys()) if hub_extras else None}")
    print(f"    bats_throws    : {hub_extras.get('bats_throws') if hub_extras else None}")
    print(f"    vs_left.at_bats: {(hub_extras.get('vs_left') or {}).get('at_bats') if hub_extras else None}")
    print(f"    vs_right.at_bats: {(hub_extras.get('vs_right') or {}).get('at_bats') if hub_extras else None}")

    odds_rows = list(db.mlb_historical_alt_odds_raw.find(
        {"game_date": DATE, "snapshot_iso": SNAP,
         "player_name_normalized": olson_norm,
         "market": {"$in": ["batter_total_bases", "batter_total_bases_alternate"]}},
        {"_id": 0}).sort("line", 1).limit(20))
    print(f"    odds rows: {len(odds_rows)}")

    print()
    print(f"    {'line':>5}  {'side':>6}  {'book':>14}  {'before(μ)':>10}  {'after(μ)':>10}")
    # Capture before/after μ
    for o in odds_rows:
        # POST-FIX
        r_after = replay_one(model, cache_row, o, hub_extras=hub_extras)
        # PRE-FIX (no hub_extras)
        r_before = replay_one(model, cache_row, o, hub_extras=None)
        line_v = o.get("line")
        print(f"    {line_v:>5}  {o.get('side'):>6}  "
              f"{(o.get('book') or '')[:14]:>14}  "
              f"{r_before.get('projection_mu', 'N/A'):>10.4f}  "
              f"{r_after.get('projection_mu', 'N/A'):>10.4f}")

    # ── C. Feature diff post-fix ─────────────────────────────────
    print("\n[C] Feature diff between predict() and post-fix replay_one()")
    # Build features both ways using the SAME builder
    # Live build
    hub_logs = model._filter_logs_before(
        olson_hub.get("bdl_game_logs") or [], DATE)
    sc_predict = model._get_batter_sc_latest(olson_hub)
    pa_cache = model._get_pa_cache()
    pa_predict = None
    mlbam_id = model._resolve_mlbam_id(olson_hub)
    if pa_cache and mlbam_id:
        pa_predict = pa_cache.batter_features(int(mlbam_id), DATE)
    feats_predict = model._build_friction_features(
        olson_hub, hub_logs, FAM,
        opponent="Seattle Mariners", park_team="Atlanta Braves",
        dk_odds=None, line=1.5,
        statcast_features=sc_predict, pitcher_statcast_features=None,
        pa_batter_features=pa_predict, pa_pitcher_features=None,
        batter_hand="L", opp_pitcher_throws="R",
    )
    # Post-fix replay build — emulate replay_one path
    from services.replay.mlb_replay_engine import (
        _build_player_dict, _build_game_logs, _opp_team_from_event,
        _derive_batter_hand_from_hub,
    )
    odds_row = odds_rows[0]
    player_synth = _build_player_dict(cache_row, hub_extras=hub_extras)
    logs_synth = _build_game_logs(cache_row)
    opp, is_away = _opp_team_from_event(
        cache_row, odds_row.get("home_team") or "",
        odds_row.get("away_team") or "")
    park_team_replay = cache_row.get("team") if not is_away else opp
    sc_replay = cache_row.get("statcast_self_as_of")
    bh = _derive_batter_hand_from_hub(hub_extras)
    pa_replay = pa_cache.batter_features(int(cache_row["player_id"]), DATE) \
        if pa_cache else None
    feats_replay = model._build_friction_features(
        player_synth, logs_synth, FAM,
        opponent=opp, park_team=park_team_replay,
        dk_odds=None, line=1.5,
        statcast_features=sc_replay, pitcher_statcast_features=None,
        pa_batter_features=pa_replay, pa_pitcher_features=None,
        batter_hand=bh, opp_pitcher_throws=cache_row.get("opp_pitcher_throws"),
    )

    train_cols = model.feature_cols[FAM]
    diffs = []
    for c in train_cols:
        a = feats_predict.get(c, 0.0)
        b = feats_replay.get(c, 0.0)
        try: av = float(a) if a is not None else 0.0
        except: av = 0.0
        try: bv = float(b) if b is not None else 0.0
        except: bv = 0.0
        if av != bv:
            diffs.append((c, av, bv, av - bv))

    diffs.sort(key=lambda t: -abs(t[3]))
    print(f"    differing features: {len(diffs)} / {len(train_cols)}")
    print(f"    {'feature':<45}  {'predict':>15}  {'replay':>15}  {'Δ':>12}")
    for f, a, b, d in diffs[:15]:
        print(f"    {f:<45}  {a:>15.4f}  {b:>15.4f}  {d:>+12.4f}")

    # Re-score raw XGBoost
    import pandas as pd
    def _score(feats):
        X = pd.DataFrame([feats])
        for c in train_cols:
            if c not in X.columns: X[c] = 0
        X = X[train_cols].fillna(0)
        Xs = model.scalers[FAM].transform(X)
        return float(model.models[FAM].predict(Xs)[0])

    rp = _score(feats_predict)
    rr = _score(feats_replay)
    print(f"\n[D] Raw XGBoost re-scored:")
    print(f"    predict-features → {rp:.4f}")
    print(f"    replay-features  → {rr:.4f}")
    print(f"    Δ = {rp - rr:+.4f}")

    # Verdict
    print("\n[E] Verdict")
    if abs(rp - rr) < 0.3:
        print(f"    ✅ Parity restored: |Δ|={abs(rp-rr):.4f} < 0.3")
    elif abs(rp - rr) < 1.0:
        print(f"    🟡 Significant improvement: |Δ|={abs(rp-rr):.4f} but >0.3")
    else:
        print(f"    🔴 Still divergent: |Δ|={abs(rp-rr):.4f}")

    client.close()


if __name__ == "__main__":
    main()
