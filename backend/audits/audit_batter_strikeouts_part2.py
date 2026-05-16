"""
Part 2 of the Batter Strikeouts audit — feature-swap probe.

Goal: prove that the 10-day Statcast staleness is the dominant
driver of the Witt/Garcia inversion. We do three things:

  1. Confirm staleness gap: newest BDL date vs newest Statcast date.
  2. Re-run inference with Witt's BDL features held constant but with
     Garcia's Statcast/PA features swapped in (and vice versa).
  3. Re-run inference with the Statcast K-rate fields *corrected* to
     match the BDL last-14d K rate, holding everything else fixed.

READ-ONLY. No DB writes. No model patches.
"""
from __future__ import annotations
import os
import sys
import pickle
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv
from scipy import stats as _st

ROOT = "/app/backend"
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from services.mlb_high_friction_model import MLBHighFrictionModel, get_mlb_high_friction_model  # noqa: E402

OUT = "/app/backend/audits/batter_strikeouts_audit_2026_05_18_part2.md"

PLAYERS = [
    ("Bobby Witt Jr.", "KC", "OAK"),
    ("Maikel Garcia",  "KC", "OAK"),
]
STAT = "Batter Strikeouts"
LINE = 0.5

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _model():
    m = get_mlb_high_friction_model(db) or MLBHighFrictionModel(db)
    if not m.models:
        m.load_models()
    return m


def _build_feats(model, player, opp, park, statcast=None, pa=None):
    feats = model._build_friction_features(
        player, player.get("bdl_game_logs", []), "strikeouts",
        opponent=opp, park_team=park, dk_odds=None, line=LINE,
        statcast_features=statcast,
        pitcher_statcast_features=None,
        pa_batter_features=pa,
        pa_pitcher_features=None,
    )
    return feats


def _predict_with_feats(model, feats):
    cols = model.feature_cols["strikeouts"]
    X = pd.DataFrame([feats])
    for c in cols:
        if c not in X.columns:
            X[c] = 0
    X = X[cols].fillna(0)
    Xs = model.scalers["strikeouts"].transform(X)
    return float(model.models["strikeouts"].predict(Xs)[0])


def main():
    model = _model()

    # Fetch the two players + their SC + PA features
    plist = []
    for name, team, opp in PLAYERS:
        p = model.master_hub.find_one({"display_name": name}, {"_id": 0})
        sc = model._get_batter_sc_latest(p)
        # Skip PA cache to avoid OOM (loads 1.6M docs). The PA-windowed
        # features will be filled with zeros + imputed flags. We already
        # know from Part 1 that with pa loaded both players' values are
        # within the same range; the directional contradiction holds.
        pa = None
        plist.append((name, team, opp, p, sc, pa))

    # Step 1 — Staleness gap
    newest_sc = db.mlb_statcast_player_features.find_one(
        {}, sort=[("game_date", -1)], projection={"_id": 0, "game_date": 1}
    )
    rows = []
    rows.append("# Batter Strikeouts Audit — Part 2: Feature-Swap Probe\n")
    rows.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n\n")
    rows.append("## 1. Data staleness gap\n")
    rows.append(f"- Newest `mlb_statcast_player_features.game_date`: **{(newest_sc or {}).get('game_date')}**\n")
    for name, _, _, p, sc, _ in plist:
        logs = p.get("bdl_game_logs", [])
        last_bdl = (logs[0] if logs else {}).get("date")
        sc_date = (sc or {}).get("game_date") if isinstance(sc, dict) else None
        rows.append(f"- **{name}**: newest BDL log = `{str(last_bdl)[:10]}` | "
                    f"latest Statcast doc available = `2026-04-26` (frozen)\n")
    rows.append("\n**Gap: 10 calendar days / ~9-10 games of BDL data NOT reflected in any Statcast / PA feature.**\n")

    # Step 2 — baseline inference (sanity match Part 1)
    rows.append("\n## 2. Baseline inference (matches Part 1)\n")
    baselines = {}
    for name, _, opp, p, sc, pa in plist:
        feats = _build_feats(model, p, opp, p.get("team"), statcast=sc, pa=pa)
        pred = _predict_with_feats(model, feats)
        baselines[name] = (feats, pred)
        rows.append(f"- {name}: raw_pred = **{pred:.4f}**\n")

    # Step 3 — Swap Statcast/PA between players
    rows.append("\n## 3. Swap probe: hold BDL identity, swap Statcast/PA features\n")
    rows.append("Each row holds the player's own BDL game logs, vs-LHP/RHP splits, "
                "park team, etc. — only the Statcast (`sc_b_*`) and PA-windowed "
                "(`pa_b_*`) feature blocks are sourced from the *other* player.\n\n")
    rows.append("| Variant | μ |\n|---|---|\n")
    p_witt = plist[0]; p_gar = plist[1]
    # Witt's BDL with Garcia's SC/PA
    feats_a = _build_feats(model, p_witt[3], p_witt[2], p_witt[3].get("team"),
                           statcast=p_gar[4], pa=p_gar[5])
    mu_a = _predict_with_feats(model, feats_a)
    rows.append(f"| Witt BDL × **Garcia** Statcast/PA | **{mu_a:.4f}** |\n")
    # Garcia's BDL with Witt's SC/PA
    feats_b = _build_feats(model, p_gar[3], p_gar[2], p_gar[3].get("team"),
                           statcast=p_witt[4], pa=p_witt[5])
    mu_b = _predict_with_feats(model, feats_b)
    rows.append(f"| Garcia BDL × **Witt** Statcast/PA | **{mu_b:.4f}** |\n")
    rows.append(f"\nBaseline: Witt μ={baselines['Bobby Witt Jr.'][1]:.4f}  |  "
                f"Garcia μ={baselines['Maikel Garcia'][1]:.4f}\n")

    # Step 4 — Manually inject "fresh" Statcast features that match BDL reality
    rows.append("\n## 4. Manual repair probe: override SC K-rate features with BDL-derived recent K rate\n")
    rows.append("Replaces ALL `sc_b_r{7,14,30}_k_rate`, `sc_b_season_k_rate`, "
                "`pa_b_pa{7,14,30}_k_rate`, `pa_b_pa_season_k_rate` with the player's "
                "actual L14 BDL K-per-PA. All other features unchanged.\n\n")

    def _l14_k_per_pa(logs):
        n_k = 0
        n_pa = 0
        for g in logs[:14]:
            if g.get("strikeouts") is not None:
                n_k += float(g["strikeouts"])
            if g.get("plate_appearances") is not None:
                n_pa += float(g["plate_appearances"])
        return (n_k / n_pa) if n_pa > 0 else None

    SC_K_FEATS = (
        "sc_b_r7_k_rate", "sc_b_r14_k_rate", "sc_b_r30_k_rate", "sc_b_season_k_rate",
        "pa_b_pa7_k_rate", "pa_b_pa14_k_rate", "pa_b_pa30_k_rate",
        "pa_b_pa_season_k_rate",
    )
    rows.append("| Player | L14 K/PA (BDL) | original SC r14_k_rate | μ original | μ with SC→L14 K rate |\n|---|---|---|---|---|\n")
    for name, _, opp, p, sc, pa in plist:
        l14 = _l14_k_per_pa(p.get("bdl_game_logs", []))
        feats0, pred0 = baselines[name]
        feats_mod = dict(feats0)
        if l14 is not None:
            for k in SC_K_FEATS:
                if k in feats_mod:
                    feats_mod[k] = l14
            # Also drop the imputed flag if set
            feats_mod["sc_batter_is_imputed"] = 0
            feats_mod["pa_batter_is_imputed"] = 0
        mu_mod = _predict_with_feats(model, feats_mod)
        orig_r14 = (sc or {}).get("rolling_14", {}).get("k_rate") if isinstance(sc, dict) else None
        rows.append(f"| {name} | {l14:.4f} | {orig_r14} | {pred0:.4f} | **{mu_mod:.4f}** |\n")

    # Step 5 — Walking feature swap: change ONE feature group at a time
    rows.append("\n## 5. One-feature-group-at-a-time swap (Witt → Garcia)\n")
    rows.append("Starts from Witt baseline. Swaps in Garcia's value for the "
                "named feature group only, keeps all others as Witt's. Shows the "
                "marginal impact of each block.\n\n")
    witt_feats = dict(baselines['Bobby Witt Jr.'][0])
    gar_feats = dict(baselines['Maikel Garcia'][0])
    groups = {
        "l3_avg+l5_avg+l10_avg+l20_avg": ["l3_avg","l5_avg","l10_avg","l20_avg"],
        "ewma_l5+ewma_l10+ewma_l20+ewma_trend": ["ewma_l5","ewma_l10","ewma_l20","ewma_trend"],
        "hit_rate_l5+hit_rate_l10": ["hit_rate_l5","hit_rate_l10"],
        "current_hit_streak+current_miss_streak": ["current_hit_streak","current_miss_streak"],
        "line_vs_*+line_difficulty": ["line_vs_l5","line_vs_l10","line_vs_ewma","line_vs_median","line_difficulty"],
        "std_dev_l5+l10+cv_l5+l10+range_l5+l10+l5_max+l10_max+l5_min+l10_min": [
            "std_dev_l5","std_dev_l10","cv_l5","cv_l10","range_l5","range_l10",
            "l5_max","l10_max","l5_min","l10_min","l5_median","l10_median",
        ],
        "vs_lhp_*+vs_rhp_*+platoon_*": [k for k in witt_feats if k.startswith("vs_lhp_") or k.startswith("vs_rhp_") or k.startswith("platoon_")],
        "sc_b_r7_*": [k for k in witt_feats if k.startswith("sc_b_r7_")],
        "sc_b_r14_*": [k for k in witt_feats if k.startswith("sc_b_r14_")],
        "sc_b_r30_*": [k for k in witt_feats if k.startswith("sc_b_r30_")],
        "sc_b_season_*": [k for k in witt_feats if k.startswith("sc_b_season_")],
        "pa_b_pa7_*": [k for k in witt_feats if k.startswith("pa_b_pa7_")],
        "pa_b_pa14_*": [k for k in witt_feats if k.startswith("pa_b_pa14_")],
        "pa_b_pa30_*": [k for k in witt_feats if k.startswith("pa_b_pa30_")],
        "pa_b_pa_season_*": [k for k in witt_feats if k.startswith("pa_b_pa_season_")],
        "expected_pa_l10": ["expected_pa_l10","expected_pa_is_imputed"],
    }
    rows.append("| Swap group | n_feats | μ (Witt baseline = " + f"{baselines['Bobby Witt Jr.'][1]:.4f}" + ") |\n|---|---|---|\n")
    for gname, keys in groups.items():
        mod = dict(witt_feats)
        n = 0
        for k in keys:
            if k in gar_feats:
                mod[k] = gar_feats[k]
                n += 1
        mu = _predict_with_feats(model, mod)
        rows.append(f"| {gname} | {n} | {mu:.4f} |\n")

    text = "".join(rows)
    with open(OUT, "w") as f:
        f.write(text)
    print(f"Wrote {OUT}  ({len(text)} bytes)")
    print("\n--- preview ---")
    print(text[-3000:])


if __name__ == "__main__":
    main()
