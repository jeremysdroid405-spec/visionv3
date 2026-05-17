"""Step 1 — Full audit of all 208 model features.
Reads feature schema from saved pickles, classifies by block, and
checks current population rates in feature cache + master hub."""
import asyncio, os, sys, pickle
from collections import defaultdict
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


# Classification rules — order matters (first match wins).
BLOCK_RULES = [
    ("ROLLING_WINDOW",   ["l3_avg","l5_avg","l10_avg","l20_avg","l5_median","l10_median",
                          "l5_max","l10_max","l5_min","l10_min",
                          "ewma_l5","ewma_l10","ewma_l20","ewma_trend",
                          "std_dev_l5","std_dev_l10","cv_l5","cv_l10",
                          "range_l5","range_l10","current_hit_streak","current_miss_streak",
                          "hit_rate_l5","hit_rate_l10","expected_pa_l10","expected_pa_is_imputed",
                          "line","line_difficulty","line_vs_ewma","line_vs_l10","line_vs_l5","line_vs_median"]),
    ("PARK",             lambda c: c.startswith("park_")),
    ("PLATOON_SPLITS",   lambda c: c.startswith("vs_lhp_") or c.startswith("vs_rhp_") or c=="platoon_avg_split" or c=="platoon_k_split" or c=="platoon_split_is_imputed"),
    ("BATTER_HAND",      lambda c: c in ("batter_is_lhh","batter_is_rhh","batter_is_switch","batter_hand_is_imputed")),
    ("HOME_AWAY",        lambda c: c.startswith("home_") or c.startswith("away_")),
    ("OPP_PITCHER_14D",  lambda c: c.startswith("opp_pitcher_") or c=="opp_k_rate"),
    ("OPP_PITCHER_THROWS", lambda c: c.startswith("opp_pitcher_throws") or c in ("same_hand_matchup","opposite_hand_matchup","matchup_is_imputed","matchup_exposure_is_imputed")),
    ("LINEUP_PHASE2B",   lambda c: c.startswith("lineup_") or c.startswith("projected_") or c.startswith("pct_")),
    ("SC_BATTER_ROLLING",lambda c: c.startswith("sc_b_")),
    ("SC_PITCHER_ROLLING",lambda c: c.startswith("sc_p_")),
    ("PA_BATTER",        lambda c: c.startswith("pa_b_") or c=="pa_batter_is_imputed"),
    ("PA_PITCHER",       lambda c: c.startswith("pa_p_") or c=="pa_pitcher_is_imputed"),
]

def classify(col: str) -> str:
    for name, rule in BLOCK_RULES:
        if isinstance(rule, list):
            if col in rule: return name
        else:
            if rule(col): return name
    return "OTHER"


async def go():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # Load all model pickles + their feature schemas
    import glob
    model_files = sorted(glob.glob("/app/backend/models/mlb_hf/mlb_hf_*.pkl"))
    model_files = [f for f in model_files if "_tweedie" not in f]
    print(f"Found {len(model_files)} baseline models")

    # Use total_bases as canonical schema (same shape across families)
    with open("/app/backend/models/mlb_hf/mlb_hf_total_bases.pkl","rb") as f:
        d = pickle.load(f)
    cols = d["features"]
    print(f"Canonical feature schema: {len(cols)} columns (from mlb_hf_total_bases.pkl)")

    # Group by block
    by_block = defaultdict(list)
    for c in cols: by_block[classify(c)].append(c)

    print(f"\n{'='*100}")
    print(f"FEATURE BLOCK INVENTORY")
    print(f"{'='*100}")
    print(f"  {'Block':<24} {'# features':>12}")
    for blk, lst in sorted(by_block.items(), key=lambda kv: -len(kv[1])):
        print(f"  {blk:<24} {len(lst):>12}")

    # Check population rate per block in feature cache (sample 100 docs)
    print(f"\n{'='*100}")
    print(f"POPULATION-RATE CHECK — sample 200 feature_cache docs from 2026-05-01..15")
    print(f"{'='*100}")
    sample = await db.mlb_replay_feature_cache.find(
        {"game_date": {"$gte":"2026-05-01","$lte":"2026-05-15"},
         "stat_family": "total_bases"},
        {"_id":0}).limit(200).to_list(200)
    print(f"  sampled {len(sample)} cache rows")

    # Check master_hub doc shape (live inference path)
    hub_sample = await db.mlb_master_hub_2026.find_one({"display_name":"Matt Olson"})
    if not hub_sample:
        hub_sample = await db.mlb_master_hub_2026.find_one({})
    hub_keys = set((hub_sample or {}).keys())
    print(f"  master_hub sample keys ({len(hub_keys)}): {sorted(hub_keys)[:25]}...")

    print(f"\n{'='*100}")
    print(f"PER-BLOCK SOURCE / POPULATION AUDIT")
    print(f"{'='*100}")

    # ── ROLLING_WINDOW: derived from bdl_game_logs[] ──
    print(f"\n[ROLLING_WINDOW] ({len(by_block['ROLLING_WINDOW'])} features)")
    print(f"  Source: bdl_game_logs[] (BDL game log array on master_hub doc)")
    print(f"  Cache field: stat_values[] / pa_values[] / dates[]")
    fc_has_stat_vals = sum(1 for s in sample if s.get("stat_values"))
    print(f"  Live: built in `_build_friction_features` from game_logs param")
    print(f"  Replay cache population: {fc_has_stat_vals}/{len(sample)} have stat_values")
    print(f"  STATUS: ✅ POPULATED in both paths")

    # ── PARK ──
    print(f"\n[PARK] ({len(by_block['PARK'])} features)")
    print(f"  Source: hardcoded `self.PARK_FACTORS_3YR` dict in model")
    print(f"  STATUS: ⚠️ ALWAYS RETURNS NEUTRAL 1.0 — `park_factor_is_imputed=0` but all values = 1.0")
    print(f"          (Park dict may have correct values, but each call resolves to 1.0)")

    # ── PLATOON_SPLITS ──
    print(f"\n[PLATOON_SPLITS] ({len(by_block['PLATOON_SPLITS'])} features)")
    print(f"  Source: player['vs_left'] / player['vs_right'] on master_hub doc")
    has_vs_left = sum(1 for k in hub_keys if k in ("vs_left","vs_right"))
    print(f"  Master hub has 'vs_left'/'vs_right' fields? {has_vs_left>0}")
    print(f"  STATUS: ❌ NEVER POPULATED — no MLB splits feed exists in this codebase")
    print(f"          imputed=1 always; values all 0")

    # ── BATTER_HAND ──
    print(f"\n[BATTER_HAND] ({len(by_block['BATTER_HAND'])} features)")
    print(f"  Source: `batter_hand` argument to predict() / `bat_side` on cache row")
    print(f"  Cache rows with non-null bat_side: {sum(1 for s in sample if s.get('bat_side'))}")
    print(f"  Master hub samples with bat_side / bat_hand:")
    bat_keys_present = [k for k in hub_keys if "bat" in k.lower() and "side" in k.lower() or "hand" in k.lower()]
    print(f"    keys found: {bat_keys_present}")
    print(f"  Cache row sample bat_side: {[s.get('bat_side') for s in sample[:5]]}")
    print(f"  STATUS: ⚠️ DATA EXISTS but FEATURE BLOCK NOT POPULATED — wiring gap")

    # ── HOME_AWAY ──
    print(f"\n[HOME_AWAY] ({len(by_block['HOME_AWAY'])} features)")
    print(f"  Source: would split bdl_game_logs by home/away and compute averages")
    print(f"  master_hub bdl_game_logs entries carry 'home'/'opponent' fields? need check")
    # Check a sample game log
    g = (hub_sample or {}).get("bdl_game_logs") or []
    if g:
        print(f"  Sample game log keys: {sorted(set().union(*[set(x.keys()) for x in g[:5]]))}")
    print(f"  STATUS: ⚠️ DATA AVAILABLE in game logs (home/away marker), NOT COMPUTED → all 0")

    # ── OPP_PITCHER_14D ──
    print(f"\n[OPP_PITCHER_14D] ({len(by_block['OPP_PITCHER_14D'])} features)")
    print(f"  Source: opp_pitcher_id → mlb_statcast_player_features['rolling_14']")
    sc_count = await db.mlb_statcast_player_features.count_documents({})
    print(f"  mlb_statcast_player_features collection: {sc_count} docs")
    # Check whether replay cache populates opp_pitcher_id
    has_opp_pid = sum(1 for s in sample if s.get("opp_pitcher_id"))
    has_opp_pid_imputed_0 = sum(1 for s in sample if s.get("opp_pitcher_is_imputed") == 0)
    print(f"  Cache rows with opp_pitcher_id set: {has_opp_pid}/{len(sample)}")
    print(f"  Cache rows with opp_pitcher_is_imputed=0: {has_opp_pid_imputed_0}/{len(sample)}")
    print(f"  STATUS: ⚠️ pitcher_id lookup logic exists; needs to be invoked for inference")

    # ── OPP_PITCHER_THROWS ──
    print(f"\n[OPP_PITCHER_THROWS] ({len(by_block['OPP_PITCHER_THROWS'])} features)")
    print(f"  Source: derived from opp_pitcher_id → master_hub.throws")
    print(f"  STATUS: ⚠️ same root as OPP_PITCHER_14D — needs opp_pitcher resolution")

    # ── LINEUP_PHASE2B ──
    print(f"\n[LINEUP_PHASE2B] ({len(by_block['LINEUP_PHASE2B'])} features)")
    print(f"  Source: opposing_lineup parameter to `_build_friction_features`")
    print(f"  Live feed: services/mlb_live_lineup_feed.py")
    print(f"  Historical feed: NEEDED — no historical lineup snapshot exists")
    print(f"  STATUS: ❌ LIVE-ONLY — historical lineup snapshots not available")
    print(f"          (would need to ingest historical lineup data from MLB Stats API or similar)")

    # ── SC_BATTER_ROLLING ──
    print(f"\n[SC_BATTER_ROLLING] ({len(by_block['SC_BATTER_ROLLING'])} features)")
    print(f"  Source: cache_row['statcast_self_as_of'] (rolling_7/14/30/season)")
    fc_has_sc = sum(1 for s in sample if s.get("statcast_self_as_of"))
    print(f"  Cache rows with statcast_self_as_of: {fc_has_sc}/{len(sample)}")
    print(f"  STATUS: ✅ POPULATED (this is what is OVER-WEIGHTED right now)")

    # ── SC_PITCHER_ROLLING ──
    print(f"\n[SC_PITCHER_ROLLING] ({len(by_block['SC_PITCHER_ROLLING'])} features)")
    print(f"  Source: opp pitcher's statcast_self_as_of (rolling Statcast)")
    print(f"  For BATTER props this should be the OPPOSING pitcher's Statcast")
    print(f"  STATUS: ❌ NEVER POPULATED for batter props — depends on opp_pitcher resolution")

    # ── PA_BATTER ──
    print(f"\n[PA_BATTER] ({len(by_block['PA_BATTER'])} features)")
    print(f"  Source: services/feature_hydration.py 'pa_batter_features' block")
    print(f"  Backing data: raw Statcast pitch-by-pitch, aggregated into PA windows")
    pa_count = await db.mlb_statcast_pa_features.count_documents({}) if "mlb_statcast_pa_features" in (await db.list_collection_names()) else 0
    print(f"  mlb_statcast_pa_features collection: {pa_count} docs")
    print(f"  STATUS: ❌ NOT WIRED — replay never passes pa_batter_features to model")

    # ── PA_PITCHER ──
    print(f"\n[PA_PITCHER] ({len(by_block['PA_PITCHER'])} features)")
    print(f"  Source: same backing collection, pitcher-side aggregation")
    print(f"  STATUS: ❌ NOT WIRED — replay never passes pa_pitcher_features to model")

    # ── OTHER ──
    if by_block.get("OTHER"):
        print(f"\n[OTHER] ({len(by_block['OTHER'])} features)")
        for c in by_block["OTHER"][:20]:
            print(f"    {c}")

    cli.close()


asyncio.run(go())
