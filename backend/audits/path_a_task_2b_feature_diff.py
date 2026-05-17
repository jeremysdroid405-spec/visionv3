"""Path A Task 2b — Feature vector diff: predict() vs replay_one()
for Matt Olson total_bases 2026-05-06.

Identifies the exact features that differ between the two code paths
that produce μ=2.25 (predict) vs μ=7.8 (replay_one). Read-only.
"""
import os
import sys
import json
import hashlib

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
)

DATE = "2026-05-06"
SNAP = "2026-05-06T11:00:00Z"
FAM = "total_bases"
LINE = 1.5

client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

model = MLBHighFrictionModel(db)
model.load_models()
norm = normalize_player_name("Matt Olson")

# ── A. predict()'s features dict ────────────────────────────────────
olson_hub = db.mlb_master_hub_2026.find_one(
    {"display_name": "Matt Olson"}, {"_id": 0})
hub_logs = model._filter_logs_before(olson_hub.get("bdl_game_logs") or [], DATE)

sc_predict = model._get_batter_sc_latest(olson_hub)
pa_cache = model._get_pa_cache()
pa_predict = None
mlbam_id = model._resolve_mlbam_id(olson_hub)
if pa_cache and mlbam_id:
    pa_predict = pa_cache.batter_features(int(mlbam_id), DATE)

feats_predict = model._build_friction_features(
    olson_hub, hub_logs, FAM,
    opponent="Seattle Mariners", park_team="Atlanta Braves",
    dk_odds=None, line=LINE,
    statcast_features=sc_predict, pitcher_statcast_features=None,
    pa_batter_features=pa_predict, pa_pitcher_features=None,
    batter_hand="L", opp_pitcher_throws="R",
    opp_pitcher_features=None, opposing_lineup=None,
)

# ── B. replay_one()'s features dict ─────────────────────────────────
cache_row = db.mlb_replay_feature_cache.find_one(
    {"game_date": DATE, "player_name_normalized": norm,
     "stat_family": FAM, "source_version": FCACHE_V},
    {"_id": 0})
odds_row = db.mlb_historical_alt_odds_raw.find_one(
    {"game_date": DATE, "snapshot_iso": SNAP,
     "player_name_normalized": norm,
     "market": {"$in": ["batter_total_bases", "batter_total_bases_alternate"]},
     "line": LINE},
    {"_id": 0})

print(f"cache.source_version = {cache_row.get('source_version')}")
print(f"odds.event_id        = {odds_row['event_id']}")
print(f"odds.home/away       = {odds_row.get('home_team')} / {odds_row.get('away_team')}")

# Build the EXACT inputs replay_one builds
player_synth = _build_player_dict(cache_row)
logs_synth = _build_game_logs(cache_row)
opp, is_away = _opp_team_from_event(
    cache_row, odds_row.get("home_team") or "",
    odds_row.get("away_team") or "")
park_team_replay = cache_row.get("team") if not is_away else opp
sc_replay = cache_row.get("statcast_self_as_of")

print(f"\nreplay synth player keys: {sorted(player_synth.keys())}")
print(f"replay synth logs first3: {logs_synth[:3]}")
print(f"replay synth logs count : {len(logs_synth)}")
print(f"hub logs count (filtered): {len(hub_logs)}")
print(f"sc_replay (cache.statcast_self_as_of) keys: "
      f"{list((sc_replay or {}).keys())[:8] if sc_replay else None}")
print(f"sc_predict (live latest) keys             : "
      f"{list((sc_predict or {}).keys())[:8] if sc_predict else None}")
print(f"park: predict='Atlanta Braves'  replay={park_team_replay}")
print(f"opp:  predict='Seattle Mariners' replay={opp}")

feats_replay = model._build_friction_features(
    player_synth, logs_synth, FAM,
    opponent=opp, park_team=park_team_replay,
    dk_odds=None, line=LINE,
    statcast_features=sc_replay, pitcher_statcast_features=None,
    pa_batter_features=None, pa_pitcher_features=None,
    batter_hand=None, opp_pitcher_throws=None,
    opp_pitcher_features=None, opposing_lineup=None,
)

# ── Diff ────────────────────────────────────────────────────────────
train_cols = model.feature_cols[FAM]
diffs = []
zeros_in_replay_only = []
zeros_in_predict_only = []
for c in train_cols:
    a = feats_predict.get(c, None) if feats_predict else None
    b = feats_replay.get(c, None) if feats_replay else None
    av = 0.0 if a is None else float(a) if isinstance(a, (int, float, bool)) else a
    bv = 0.0 if b is None else float(b) if isinstance(b, (int, float, bool)) else b
    if av != bv:
        diffs.append((c, av, bv, av - bv if isinstance(av, (int, float)) and isinstance(bv, (int, float)) else None))
    if av != 0.0 and bv == 0.0:
        zeros_in_replay_only.append((c, av, bv))
    if av == 0.0 and bv != 0.0:
        zeros_in_predict_only.append((c, av, bv))

print(f"\n[DIFF] features with different values: {len(diffs)} / {len(train_cols)}")
print(f"   predict-populated, replay-zero: {len(zeros_in_replay_only)}")
print(f"   predict-zero, replay-populated: {len(zeros_in_predict_only)}")

# Sort diffs by absolute change
diffs.sort(key=lambda t: -abs(t[3]) if t[3] is not None else 0)
print("\n[TOP 30 DIFFS by |Δ|]")
print(f"{'feature':<45}  {'predict':>15}  {'replay':>15}  {'Δ':>12}")
for f, a, b, d in diffs[:30]:
    dstr = f"{d:>+12.4f}" if d is not None else "          —"
    print(f"{f:<45}  {a:>15}  {b:>15}  {dstr}")

# Identify features that the model heavily weights — list those that differ
print("\n[TOP DIFF FEATURES by category]")
from collections import defaultdict
by_cat: dict = defaultdict(list)
for f, a, b, d in diffs:
    cat = "other"
    if f.startswith("sc_b_") or f.startswith("statcast_") or "sc_batter" in f: cat = "sc_batter"
    elif f.startswith("sc_p_"): cat = "sc_pitcher"
    elif f.startswith("pa_b_") or "pa_batter" in f: cat = "pa_batter"
    elif f.startswith("pa_p_") or "pa_pitcher" in f: cat = "pa_pitcher"
    elif f.startswith("vs_lhp") or f.startswith("vs_rhp") or f.startswith("platoon"): cat = "platoon"
    elif f.startswith(("l3_", "l5_", "l10_", "l20_", "ewma", "std_dev", "cv_", "range_", "hit_rate", "current_")): cat = "rolling"
    elif "park" in f.lower() or "altitude" in f.lower(): cat = "park"
    elif "batter_" in f or "opp_pitcher_throws" in f or "matchup" in f: cat = "matchup"
    elif "lineup" in f: cat = "lineup"
    elif "expected" in f or "workload" in f: cat = "workload"
    by_cat[cat].append((f, a, b, d))
for cat, lst in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
    print(f"  {cat}: {len(lst)} feats")
    for f, a, b, d in lst[:4]:
        print(f"     {f}  predict={a}  replay={b}")

# Park factor specifically
print(f"\n[park_factor] predict={feats_predict.get('park_factor')}  replay={feats_replay.get('park_factor')}")
print(f"[park lookup] PARK_FACTORS_3YR.get('Atlanta Braves') = {model.PARK_FACTORS_3YR.get('Atlanta Braves')}")
print(f"[park lookup] PARK_FACTORS_3YR.get('ATL')           = {model.PARK_FACTORS_3YR.get('ATL')}")
print(f"[park lookup] DEFAULT_PARK = {model.DEFAULT_PARK}")
print(f"[park keys sample] {list(model.PARK_FACTORS_3YR.keys())[:8]}")

# Raw XGBoost re-prediction to confirm
import pandas as pd
def _score(feats):
    X = pd.DataFrame([feats])
    for c in train_cols:
        if c not in X.columns:
            X[c] = 0
    X = X[train_cols].fillna(0)
    Xs = model.scalers[FAM].transform(X)
    return float(model.models[FAM].predict(Xs)[0])

raw_predict = _score(feats_predict)
raw_replay = _score(feats_replay)
print(f"\n[RAW XGBoost re-scored]  predict-features→{raw_predict:.4f}   "
      f"replay-features→{raw_replay:.4f}")

client.close()
