"""Path A Task 2d — Verify post-fix parity is correct.

When both paths use:
  - the SAME as-of-date Statcast cache row (no leakage)
  - the SAME opp_pitcher info (None for this date — cache has no probable pitcher)
  - the SAME platoon / home-away splits

…they should produce identical μ. Show that.
"""
import os
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from pymongo import MongoClient

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FCACHE_V, normalize_player_name,
)
from services.replay.mlb_replay_engine import (
    _build_player_dict, _build_game_logs, _opp_team_from_event,
    _derive_batter_hand_from_hub, replay_one,
)
import pandas as pd

DATE = "2026-05-06"
SNAP = "2026-05-06T11:00:00Z"
FAM = "total_bases"

client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
model = MLBHighFrictionModel(db)
model.load_models()

olson_norm = normalize_player_name("Matt Olson")
olson_hub = db.mlb_master_hub_2026.find_one({"display_name": "Matt Olson"}, {"_id": 0})
cache_row = db.mlb_replay_feature_cache.find_one(
    {"game_date": DATE, "player_name_normalized": olson_norm,
     "stat_family": FAM, "source_version": FCACHE_V}, {"_id": 0})
odds_row = db.mlb_historical_alt_odds_raw.find_one(
    {"game_date": DATE, "snapshot_iso": SNAP,
     "player_name_normalized": olson_norm,
     "market": {"$in": ["batter_total_bases", "batter_total_bases_alternate"]},
     "line": 1.5}, {"_id": 0})

hub_extras = db.mlb_master_hub_2026.find_one(
    {"bdl_id": cache_row["bdl_id"]},
    {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
     "home_splits": 1, "away_splits": 1, "bats_throws": 1, "bats": 1})

# Build POST-FIX replay features
opp, is_away = _opp_team_from_event(cache_row,
    odds_row.get("home_team") or "", odds_row.get("away_team") or "")
player_synth = _build_player_dict(cache_row, hub_extras=hub_extras)
logs_synth = _build_game_logs(cache_row)
park_replay = cache_row.get("team") if not is_away else opp
sc_replay = cache_row.get("statcast_self_as_of")
pa_cache = model._get_pa_cache()
pa_replay = pa_cache.batter_features(int(cache_row["player_id"]), DATE)
bh = _derive_batter_hand_from_hub(hub_extras)

feats_replay = model._build_friction_features(
    player_synth, logs_synth, FAM,
    opponent=opp, park_team=park_replay,
    dk_odds=None, line=1.5,
    statcast_features=sc_replay, pitcher_statcast_features=None,
    pa_batter_features=pa_replay, pa_pitcher_features=None,
    batter_hand=bh, opp_pitcher_throws=cache_row.get("opp_pitcher_throws"),
)

# Build "predict-EQUIVALENT" — feed predict-style with the SAME sc + no opp_pitcher
print("[A] Test: predict-equivalent with replay's as-of sc + NO opp_pitcher")
hub_logs = model._filter_logs_before(olson_hub.get("bdl_game_logs") or [], DATE)
feats_predict_eq = model._build_friction_features(
    olson_hub, hub_logs, FAM,
    opponent=opp, park_team=park_replay,
    dk_odds=None, line=1.5,
    statcast_features=sc_replay,        # ← use as-of cache, not latest
    pitcher_statcast_features=None,
    pa_batter_features=pa_replay,
    pa_pitcher_features=None,
    batter_hand=bh,                      # ← derived from hub_extras
    opp_pitcher_throws=None,             # ← match cache (no opp_pitcher data)
)

train_cols = model.feature_cols[FAM]
diffs = []
for c in train_cols:
    a = feats_predict_eq.get(c, 0.0)
    b = feats_replay.get(c, 0.0)
    try: av = float(a) if a is not None else 0.0
    except: av = 0.0
    try: bv = float(b) if b is not None else 0.0
    except: bv = 0.0
    if av != bv:
        diffs.append((c, av, bv, av - bv))

diffs.sort(key=lambda t: -abs(t[3]))
print(f"    diffs (post-equivalent): {len(diffs)} / {len(train_cols)}")
for f, a, b, d in diffs[:8]:
    print(f"      {f}: predict_eq={a:.4f}  replay={b:.4f}  Δ={d:+.4f}")

def _score(feats):
    X = pd.DataFrame([feats])
    for c in train_cols:
        if c not in X.columns: X[c] = 0
    X = X[train_cols].fillna(0)
    Xs = model.scalers[FAM].transform(X)
    return float(model.models[FAM].predict(Xs)[0])

print(f"\n    predict-equivalent → {_score(feats_predict_eq):.4f}")
print(f"    post-fix replay    → {_score(feats_replay):.4f}")

print("\n[B] Top-50 μ scan: which (player, line) values used to produce μ > 4.5?")
# Pull from existing legacy outputs the top inflated rows
top_old = list(db.mlb_replay_model_outputs.find(
    {"game_date": DATE, "stat_family": FAM},
    {"_id": 0, "player_name": 1, "player_name_normalized": 1,
     "line": 1, "side": 1, "book": 1, "projection_mu": 1,
     "raw_prediction": 1, "park_factor": 1}
).sort("projection_mu", -1).limit(15))
print(f"    top 15 from legacy outputs:")
for r in top_old:
    print(f"      {r['player_name']:>22}  L={r['line']:>5} {r['side']:>5}  "
          f"book={(r.get('book') or '')[:14]:>14}  "
          f"old_μ={r['projection_mu']:.3f}")

# Re-run replay_one (POST-FIX) for these and report the new μ
print("\n[C] POST-FIX μ for the same top-15 (using new replay_one):")
top_re = []
for r in top_old[:15]:
    pname_norm = r["player_name_normalized"]
    crow = db.mlb_replay_feature_cache.find_one(
        {"game_date": DATE, "player_name_normalized": pname_norm,
         "stat_family": FAM, "source_version": FCACHE_V}, {"_id": 0})
    if not crow:
        print(f"      {r['player_name']}: no cache row"); continue
    he = db.mlb_master_hub_2026.find_one(
        {"bdl_id": crow["bdl_id"]},
        {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
         "home_splits": 1, "away_splits": 1, "bats_throws": 1, "bats": 1})
    orow = db.mlb_historical_alt_odds_raw.find_one(
        {"game_date": DATE, "snapshot_iso": SNAP,
         "player_name_normalized": pname_norm,
         "line": r["line"], "side": r["side"], "book": r["book"],
         "market": {"$in": ["batter_total_bases", "batter_total_bases_alternate"]}},
        {"_id": 0})
    if not orow:
        print(f"      {r['player_name']}: no odds row matching"); continue
    new_r = replay_one(model, crow, orow, hub_extras=he)
    new_mu = (new_r or {}).get("projection_mu")
    print(f"      {r['player_name']:>22}  L={r['line']:>5}  "
          f"old_μ={r['projection_mu']:.3f}  →  new_μ={new_mu:.3f}  "
          f"Δ={(new_mu - r['projection_mu']):+.3f}")
    top_re.append((r["player_name"], r["line"], r["projection_mu"], new_mu))

print("\n[D] Verdict")
post_max = max((nm for _, _, _, nm in top_re), default=0.0)
print(f"    post-fix max μ in top-15 contaminated rows: {post_max:.3f}")
if post_max < 4.5:
    print("    ✅ All sampled inflated rows have collapsed below 4.5")
else:
    print("    🟡 Some rows still inflated — investigate further")

client.close()
