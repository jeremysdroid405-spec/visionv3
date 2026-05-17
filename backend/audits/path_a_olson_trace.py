"""Olson μ comparison trace — verify the actual μ produced by predict()
across live and replay code paths, with identical inputs.

If μ values match within tolerance, the handoff's "150 missing features
causing μ inflation" hypothesis is REFUTED and the real root cause is
elsewhere (training-data sparsity, model overfit on sc_b_r7_wOBA, etc.).
"""
import os
import sys
import json
import asyncio
from datetime import datetime

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient
from services.mlb_high_friction_model import MLBHighFrictionModel

PLAYER_QUERIES = [
    {"display_name": "Matt Olson"},
    {"player_name": "Matt Olson"},
    {"mlb_full_name": "Matt Olson"},
]
STAT = "total_bases"
AS_OF = "2026-05-06"
LINE = 1.5

client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

print("[1] Loading model + feature schema")
model = MLBHighFrictionModel(db)
model.load_models()
print(f"     train cols [{STAT}] = {len(model.feature_cols[STAT])}")

print("\n[2] Resolving Olson in master_hub")
olson = None
for q in PLAYER_QUERIES:
    olson = db.mlb_master_hub_2026.find_one(q, {"_id": 0})
    if olson:
        break
if not olson:
    print("     ❌ Olson not in master_hub — aborting")
    sys.exit(1)
print(f"     team={olson.get('team')} bat={olson.get('bat_side')} "
      f"throws={olson.get('throws')}  "
      f"logs={len(olson.get('bdl_game_logs') or [])}")

# ── Direct predict() calls ───────────────────────────────────────────
print("\n[3] LIVE predict() — full hydration kwargs")
r_live = model.predict(
    player_name="Matt Olson",
    stat_type=STAT,
    line=LINE,
    opponent_team="Seattle Mariners",
    park_team="Atlanta Braves",
    batter_hand="L",                       # Olson bats left
    opp_pitcher_throws="R",
    opp_pitcher_id=None,
    opposing_lineup=None,
    as_of_date=AS_OF,                      # leak guard ON
)
print(f"     μ={r_live.get('predicted')}  σ={r_live.get('std_dev')}  "
      f"p_over={r_live.get('prob_over')}  "
      f"feature_health.imputed_pct={r_live.get('feature_health', {}).get('imputed_pct')}")

print("\n[4] REPLAY-style predict() — no hydration kwargs (mimics current replay path)")
r_replay = model.predict(
    player_name="Matt Olson",
    stat_type=STAT,
    line=LINE,
    opponent_team="Seattle Mariners",
    park_team="Atlanta Braves",
    batter_hand=None,
    opp_pitcher_throws=None,
    opp_pitcher_id=None,
    opposing_lineup=None,
    as_of_date=AS_OF,
)
print(f"     μ={r_replay.get('predicted')}  σ={r_replay.get('std_dev')}  "
      f"p_over={r_replay.get('prob_over')}  "
      f"feature_health.imputed_pct={r_replay.get('feature_health', {}).get('imputed_pct')}")

# ── Diff ────────────────────────────────────────────────────────────
mu_live = r_live.get("predicted")
mu_replay = r_replay.get("predicted")
print(f"\n[5] Δμ (live − replay) = "
      f"{(mu_live or 0) - (mu_replay or 0):.4f}  "
      f"(live={mu_live}, replay={mu_replay})")

# ── Capture the actual features dict each path built ────────────────
print("\n[6] Capturing raw features dicts for column-by-column diff")
def _build(model, batter_hand, opp_throws):
    game_logs = list(olson.get("bdl_game_logs") or [])
    game_logs = model._filter_logs_before(game_logs, AS_OF)
    sc = model._get_batter_sc_latest(olson)
    pa_cache = model._get_pa_cache()
    pa = None
    mlbam_id = model._resolve_mlbam_id(olson)
    if pa_cache is not None and mlbam_id is not None:
        pa = pa_cache.batter_features(int(mlbam_id), AS_OF)
    return model._build_friction_features(
        olson, game_logs, STAT,
        opponent="Seattle Mariners", park_team="Atlanta Braves",
        dk_odds=None, line=LINE,
        statcast_features=sc, pitcher_statcast_features=None,
        pa_batter_features=pa, pa_pitcher_features=None,
        batter_hand=batter_hand, opp_pitcher_throws=opp_throws,
        opp_pitcher_features=None, opposing_lineup=None,
    )

feats_live = _build(model, "L", "R")
feats_replay = _build(model, None, None)

train_cols = model.feature_cols[STAT]
diffs = []
for c in train_cols:
    lv = feats_live.get(c, 0.0)
    rv = feats_replay.get(c, 0.0)
    if lv != rv:
        diffs.append((c, lv, rv))

print(f"     features differing between live & replay builds: {len(diffs)} / {len(train_cols)}")
for c, lv, rv in diffs[:15]:
    print(f"       {c:>40}  live={lv}  replay={rv}")

print("\n[7] Dumping last 5 trained-schema feature names + their LIVE values:")
for c in train_cols[-5:]:
    print(f"     {c:>40} = {feats_live.get(c, '<missing>')}")

# Final verdict
print("\n[8] VERDICT")
if mu_live is not None and mu_replay is not None:
    delta = abs(mu_live - mu_replay)
    if delta < 0.01:
        print(f"     ✅ μ identical (Δ={delta:.4f}). The Phase-2A/2B "
              f"hydration kwargs do NOT change Olson's μ on this date.")
        print(f"     → 'missing features cause μ inflation' hypothesis NOT "
              f"supported by direct trace. Inflation must come from elsewhere.")
    else:
        print(f"     ⚠️ μ diverges by {delta:.4f}. Restoration would change μ.")

client.close()
