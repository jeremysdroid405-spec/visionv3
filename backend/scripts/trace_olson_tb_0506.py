"""End-to-end trace of Matt Olson total_bases on 2026-05-06.

No patches — root-cause investigation only.
"""
from __future__ import annotations
import asyncio
import os
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
import numpy as np
import pandas as pd

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_replay_engine import (
    _build_player_dict, _build_game_logs, _opp_team_from_event, replay_one,
)
from services.replay.mlb_feature_cache import _STAT_FIELD_MAP, _PITCHER_FAMILIES


PLAYER = "Matt Olson"
GAME_DATE = "2026-05-06"
SNAPSHOT = f"{GAME_DATE}T11:00:00Z"
STAT_FAMILY = "total_bases"


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("="*100)
    print(f"END-TO-END TRACE  —  {PLAYER}  {STAT_FAMILY}  {GAME_DATE}")
    print("="*100)

    # ── A. Historical odds rows ────────────────────────────────────────
    print("\n[A] HISTORICAL ODDS ROWS (Olson TB, 05-06 @ 11:00Z)")
    print("-"*100)
    odds_rows = await db.mlb_historical_alt_odds_raw.find(
        {"game_date": GAME_DATE,
         "snapshot_iso": SNAPSHOT,
         "player_name_normalized": "matt olson",
         "$or": [{"market": "batter_total_bases"},
                  {"market": "batter_total_bases_alternate"}]},
        {"_id":0},
    ).sort([("market",1),("line",1),("book",1)]).to_list(None)
    print(f"  rows: {len(odds_rows)}")
    if odds_rows:
        sample = odds_rows[0]
        print(f"  sample:  {sample.get('market')}  line={sample.get('line')}  side={sample.get('side')}  book={sample.get('book')}  odds={sample.get('odds')}  event={sample.get('event_id')[:16]}")
        print(f"  home={sample.get('home_team')}  away={sample.get('away_team')}  commence={sample.get('commence_time')}")

    # ── B. Feature cache row ───────────────────────────────────────────
    print("\n[B] FEATURE CACHE ROW")
    print("-"*100)
    cache_row = await db.mlb_replay_feature_cache.find_one(
        {"game_date": GAME_DATE,
         "player_name_normalized": "matt olson",
         "stat_family": STAT_FAMILY},
        {"_id":0},
    )
    if not cache_row:
        print("  *** NO FEATURE CACHE ROW FOUND ***"); return
    print(f"  source_version          {cache_row.get('source_version')}")
    print(f"  team                    {cache_row.get('team')}")
    print(f"  bat_side                {cache_row.get('bat_side')}")
    print(f"  stat_family             {cache_row.get('stat_family')}")
    print(f"  cv_l10                  {cache_row.get('cv_l10')}")
    stat_vals = cache_row.get("stat_values") or []
    dates = cache_row.get("dates") or []
    pa_vals = cache_row.get("pa_values") or []
    print(f"  stat_values  (n={len(stat_vals)})")
    print(f"    full list: {stat_vals}")
    print(f"  dates        (n={len(dates)})")
    print(f"    full list: {dates}")
    print(f"  pa_values    (n={len(pa_vals)})")
    print(f"    full list: {pa_vals}")
    print(f"  newest date  {dates[0] if dates else None}")
    print(f"  oldest date  {dates[-1] if dates else None}")
    # Verify no future leakage
    future = [d for d in dates if d and d[:10] >= GAME_DATE]
    print(f"  ★ future-leakage check: {len(future)} dates ≥ {GAME_DATE}  →  {future[:5]}")

    if stat_vals:
        for k, n in (("L5",5),("L10",10),("L20",20)):
            sub = stat_vals[:n]
            if sub:
                print(f"  {k}  mean={np.mean(sub):.3f}  max={max(sub)}  min={min(sub)}  "
                      f"sd={np.std(sub, ddof=1) if len(sub)>1 else 0:.3f}  cv={(np.std(sub,ddof=1)/np.mean(sub) if np.mean(sub)>0 else 0):.3f}")

    # Statcast features included on the row?
    sc = cache_row.get("statcast_self_as_of") or {}
    print(f"\n  statcast_self_as_of: {len(sc)} keys")
    for k in sorted(sc.keys())[:10]:
        print(f"    {k:<32} {sc[k]}")
    if len(sc) > 10: print(f"    ... ({len(sc)-10} more)")

    # ── C. Build model input vector ────────────────────────────────────
    print("\n[C] MODEL FEATURE VECTOR (full)")
    print("-"*100)
    model = MLBHighFrictionModel(db.delegate)
    try:
        model.load_models()
    except Exception as e:
        print(f"  could not load models: {e}")
    if STAT_FAMILY not in model.feature_cols:
        print(f"  *** stat_family '{STAT_FAMILY}' not in model.feature_cols ***")
        print(f"  available: {list(model.feature_cols.keys())}")
        return
    cols = model.feature_cols[STAT_FAMILY]
    print(f"  model expects {len(cols)} feature columns")

    player = _build_player_dict(cache_row)
    game_logs = _build_game_logs(cache_row)
    is_pitcher_fam = STAT_FAMILY in _PITCHER_FAMILIES
    sc_self = cache_row.get("statcast_self_as_of")

    # Use sample odds row to get event context
    sample = odds_rows[0] if odds_rows else {"home_team":"Seattle Mariners","away_team":"Atlanta Braves","line":0.5}
    line_f = float(sample.get("line") or 0.5)
    opp, is_away = _opp_team_from_event(cache_row, sample.get("home_team") or "", sample.get("away_team") or "")
    park_team = cache_row.get("team") if not is_away else opp
    print(f"  opp resolved             {opp}  (is_away={is_away})")
    print(f"  park_team                {park_team}")
    print(f"  line used                {line_f}")

    feats = model._build_friction_features(
        player, game_logs, STAT_FAMILY,
        opponent=opp, park_team=park_team, dk_odds=None, line=line_f,
        statcast_features=(sc_self if not is_pitcher_fam else None),
        pitcher_statcast_features=(sc_self if is_pitcher_fam else None),
        pa_batter_features=None, pa_pitcher_features=None,
    )
    if feats is None:
        print("  *** _build_friction_features returned None ***"); return

    print(f"\n  feature vector ({len(feats)} keys; showing those that affect μ first):")
    print(f"  {'feature':<40} {'value':>14}")
    # Show the trend/scale features first
    priority = ["l3_avg","l5_avg","l10_avg","l20_avg","l5_median","l10_median",
                "l5_max","l10_max","l5_min","l10_min",
                "ewma_l5","ewma_l10","ewma_l20","ewma_trend",
                "std_dev_l5","std_dev_l10","cv_l5","cv_l10",
                "range_l5","range_l10",
                "expected_pa","park_factor","line"]
    seen = set()
    for k in priority:
        if k in feats:
            print(f"  {k:<40} {feats[k]!r:>14}"); seen.add(k)
    print("  ---")
    for k in sorted(feats.keys()):
        if k in seen: continue
        v = feats[k]
        try: vs = f"{v:.4f}" if isinstance(v,(int,float)) else str(v)
        except: vs = str(v)
        print(f"  {k:<40} {vs:>14}")

    # ── D. Raw model prediction ────────────────────────────────────────
    print("\n[D] RAW MODEL PREDICTION")
    print("-"*100)
    X = pd.DataFrame([feats])
    for c in cols:
        if c not in X.columns: X[c] = 0
    X = X[cols].fillna(0)
    # Save for inspection: which columns required imputation to 0?
    raw_keys = set(feats.keys())
    missing = [c for c in cols if c not in raw_keys]
    print(f"  model.feature_cols[{STAT_FAMILY!r}] length: {len(cols)}")
    print(f"  features built by _build_friction_features that match model cols: {len(set(cols) & raw_keys)}")
    print(f"  features MISSING (auto-imputed to 0): {len(missing)}")
    if missing[:10]:
        print(f"    first 10 missing: {missing[:10]}")
    extra = [k for k in raw_keys if k not in set(cols)]
    print(f"  features built but NOT used by model (extra): {len(extra)}")

    Xs = model.scalers[STAT_FAMILY].transform(X)
    raw_pred = float(model.models[STAT_FAMILY].predict(Xs)[0])
    print(f"\n  RAW model.predict           {raw_pred:.4f}")
    pf = float(feats.get("park_factor", 1.0))
    print(f"  park_factor                  {pf:.4f}")
    mu = raw_pred * pf
    print(f"  μ = raw × park_factor        {mu:.4f}")
    print(f"  feature 'l10_avg'            {feats.get('l10_avg'):.4f}")
    print(f"  feature 'l20_avg'            {feats.get('l20_avg'):.4f}")
    print(f"  feature 'l5_max'             {feats.get('l5_max')}")
    print(f"  feature 'std_dev_l10'        {feats.get('std_dev_l10'):.4f}")

    # ── E. Sanity bounds ───────────────────────────────────────────────
    print("\n[E] SANITY BOUNDS")
    print("-"*100)
    print(f"  Recent L5 TB mean            {np.mean(stat_vals[:5]):.3f}")
    print(f"  Recent L10 TB mean           {np.mean(stat_vals[:10]):.3f}")
    print(f"  Recent L20 TB mean           {np.mean(stat_vals[:min(20,len(stat_vals))]):.3f}")
    print(f"  Max single-game TB recent    {max(stat_vals[:20]) if stat_vals else None}")
    print(f"  Min single-game TB recent    {min(stat_vals[:20]) if stat_vals else None}")
    print()
    print(f"  ⇒ Olson's reasonable single-game μ ≈ {np.mean(stat_vals[:20]):.2f} - {np.mean(stat_vals[:10]):.2f}")
    print(f"  ⇒ Replay μ output: {mu:.2f}")
    print(f"  ⇒ Inflation factor vs. L10 avg: {mu / max(np.mean(stat_vals[:10]), 1e-9):.2f}×")

    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
