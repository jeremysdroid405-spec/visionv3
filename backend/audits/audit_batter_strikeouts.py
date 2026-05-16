"""
URGENT P0 AUDIT — MLB Batter Strikeouts model trace.

READ-ONLY. Compares the persisted score-doc μ values for
Bobby Witt Jr. and Maikel Garcia (Batter Strikeouts OVER 0.5)
against a clean re-run of the live predict() path.

No patches. No DB writes.
"""
from __future__ import annotations
import json
import os
import sys
import pickle
from pprint import pformat
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

ROOT = "/app/backend"
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from services.mlb_high_friction_model import (  # noqa: E402
    MLBHighFrictionModel,
    get_mlb_high_friction_model,
)

OUT = "/app/backend/audits/batter_strikeouts_audit_2026_05_18.md"

PLAYERS = [
    ("Bobby Witt Jr.", "KC", "OAK"),   # opp here is illustrative only
    ("Maikel Garcia", "KC", "OAK"),
]
STAT = "Batter Strikeouts"
LINE = 0.5

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def fmt(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{n}f}"
    return str(v)


def section(title: str) -> str:
    return f"\n\n## {title}\n"


def collect_persisted(name: str) -> dict:
    doc = db.mlb_prop_scores.find_one(
        {"player_name": name,
         "stat_type": {"$regex": "strikeouts", "$options": "i"},
         "line": 0.5, "side": "OVER",
         "is_alternate_market": True},
        sort=[("computed_at", -1)],
    )
    if not doc:
        return {}
    doc.pop("_id", None)
    keys = [
        "player_name", "stat_type", "line", "side", "computed_at",
        "routed_tier", "model_version", "model_name", "stat_family",
        "predicted", "mu", "sigma", "std_dev", "z_score", "p_hat",
        "prob_over", "tp", "fair_prob", "edge", "vision_score",
        "tier", "verdict", "direction_gate", "market_class",
        "is_alternate_market", "mu_raw_model_projection",
        "mu_active_baseline_applied", "mu_active_baseline_value",
        "raw_prediction", "feature_health",
    ]
    return {k: doc.get(k) for k in keys if k in doc}


def collect_game_logs(name: str) -> dict:
    p = db.mlb_master_hub_2026.find_one({"display_name": name}, {"_id": 0})
    if not p:
        return {}
    logs = p.get("bdl_game_logs", [])[:20]
    rows = []
    ks = []
    pas = []
    for g in logs:
        k = g.get("strikeouts")
        pa = g.get("plate_appearances")
        rows.append({"date": str(g.get("date"))[:10],
                     "K": k, "PA": pa,
                     "opp": g.get("opponent_abbr"),
                     "AB": g.get("at_bats")})
        if k is not None:
            ks.append(float(k))
        if pa is not None:
            pas.append(float(pa))

    def avg(arr): return sum(arr) / len(arr) if arr else None

    out = {
        "n_logs_total": len(p.get("bdl_game_logs", [])),
        "rows": rows,
        "L5_K_avg": avg(ks[:5]),
        "L10_K_avg": avg(ks[:10]),
        "L20_K_avg": avg(ks[:20]),
        "L5_PA_avg": avg(pas[:5]),
        "L10_PA_avg": avg(pas[:10]),
        "L5_HR_over_05_pct": (sum(1 for v in ks[:5] if v > 0.5) / max(len(ks[:5]), 1)) * 100,
        "L10_HR_over_05_pct": (sum(1 for v in ks[:10] if v > 0.5) / max(len(ks[:10]), 1)) * 100,
        "L20_HR_over_05_pct": (sum(1 for v in ks[:20] if v > 0.5) / max(len(ks[:20]), 1)) * 100,
        "is_in_lineup_today": p.get("is_in_lineup_today"),
        "bat_side": p.get("bat_side"),
        "team": p.get("team"),
        "position": p.get("position"),
    }
    return out


def reproduce_inference(name: str, opp: str, park: str) -> dict:
    model = get_mlb_high_friction_model(db)
    if model is None:
        model = MLBHighFrictionModel(db)
    if not model.models:
        model.load_models()
    norm_stat = model._normalize_stat(STAT)

    # Load the player document directly the way predict() does.
    player = model.master_hub.find_one(
        {"$or": [
            {"display_name": name},
            {"player_name": name},
            {"mlb_full_name": name},
        ]},
        {"_id": 0},
    )
    if not player:
        return {"error": "player_not_found"}
    game_logs = player.get("bdl_game_logs", [])
    sc_batter = model._get_batter_sc_latest(player) if player else None
    pa_batter = None
    mid = model._resolve_mlbam_id(player)
    pa_cache = model._get_pa_cache()
    if pa_cache is not None and mid is not None:
        as_of = datetime.utcnow().strftime("%Y-%m-%d")
        pa_batter = pa_cache.batter_features(int(mid), as_of)

    feats = model._build_friction_features(
        player, game_logs, norm_stat,
        opponent=opp, park_team=park, dk_odds=None, line=LINE,
        statcast_features=sc_batter,
        pitcher_statcast_features=None,
        pa_batter_features=pa_batter,
        pa_pitcher_features=None,
    )
    if feats is None:
        return {"error": "feature_build_failed", "n_logs": len(game_logs)}

    feat_cols = model.feature_cols[norm_stat]
    X = pd.DataFrame([feats])
    for col in feat_cols:
        if col not in X.columns:
            X[col] = 0
    X = X[feat_cols].fillna(0)
    X_scaled = model.scalers[norm_stat].transform(X)
    raw_pred = float(model.models[norm_stat].predict(X_scaled)[0])
    park_factor = feats.get("park_factor", 1.0)

    # Reproduce the same μ-override path: batter strikeouts is NOT in
    # _ACTIVE_BASELINE and is NOT pitcher_strikeouts, so:
    final_pred = raw_pred * park_factor

    std_l10 = feats.get("std_dev_l10", 0.0)
    l10_avg = feats.get("l10_avg", final_pred)
    cv = std_l10 / l10_avg if l10_avg > 0 else 0.5
    std_used = std_l10
    # Floor only fires for ['hits','total_bases','rbis','runs','hits+runs+rbis','home_runs']
    # NOT for 'strikeouts' — so std_used remains raw σ.

    # Standard Normal CDF
    from scipy import stats as _st
    z = (LINE - final_pred) / std_used if std_used > 0 else None
    p_over = (1 - _st.norm.cdf(z)) * 100 if z is not None else None
    if p_over is not None and final_pred < LINE and p_over >= 50:
        p_over = max(5, 50 - abs(z) * 10)

    interesting = [
        "l3_avg", "l5_avg", "l10_avg", "l20_avg",
        "ewma_l5", "ewma_l10", "ewma_l20", "ewma_trend",
        "std_dev_l5", "std_dev_l10", "cv_l5", "cv_l10",
        "l5_max", "l10_max", "l5_min", "l10_min",
        "range_l5", "range_l10",
        "hit_rate_l5", "hit_rate_l10",
        "current_hit_streak", "current_miss_streak",
        "line", "line_vs_l5", "line_vs_l10", "line_vs_ewma",
        "line_vs_median", "line_difficulty",
        "park_factor", "park_k_factor", "opp_k_rate",
        "vs_lhp_avg", "vs_rhp_avg", "vs_lhp_k_rate", "vs_rhp_k_rate",
        "platoon_k_split", "platoon_split_is_imputed",
        "vs_lhp_is_imputed", "vs_rhp_is_imputed",
        "home_avg", "away_avg", "home_away_split",
        "home_away_split_is_imputed",
        "expected_pa_l10", "expected_pa_is_imputed",
        "sc_b_r7_k_rate", "sc_b_r14_k_rate", "sc_b_r30_k_rate",
        "sc_b_r7_whiff_rate", "sc_b_r7_contact_rate",
        "sc_batter_is_imputed",
        "pa_b_pa14_k_rate", "pa_b_pa30_k_rate", "pa_b_pa7_k_rate",
        "pa_b_pa30_whiff_rate", "pa_batter_is_imputed",
    ]
    feat_dump = {k: feats.get(k) for k in interesting if k in feats}
    imputed = sorted([k.replace("_is_imputed", "")
                      for k, v in feats.items()
                      if k.endswith("_is_imputed") and v == 1])

    return {
        "norm_stat": norm_stat,
        "model_pickle": "mlb_hf_strikeouts.pkl",
        "model_version": getattr(model, "_model_versions", {}).get(norm_stat),
        "n_feature_cols": len(feat_cols),
        "raw_pred_from_model": raw_pred,
        "park_factor": park_factor,
        "final_pred_mu": final_pred,
        "std_dev_used": std_used,
        "z_score": z,
        "prob_over_pct": p_over,
        "key_features": feat_dump,
        "imputed_features_count": len(imputed),
        "imputed_features": imputed,
    }


def main():
    lines = []
    lines.append(f"# Batter Strikeouts Audit — Witt vs Garcia\n")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"\nLine: {LINE}  Side: OVER  Stat: {STAT}\n")

    persisted_rows = {}
    log_rows = {}
    inf_rows = {}

    for name, team, opp in PLAYERS:
        persisted_rows[name] = collect_persisted(name)
        log_rows[name] = collect_game_logs(name)
        inf_rows[name] = reproduce_inference(name, opp, team)

    # 1. Persisted score doc snapshot
    lines.append(section("1. Persisted score-doc snapshot (latest, alternate, OVER 0.5)"))
    fields = ["computed_at", "routed_tier", "tier", "vision_score",
              "tp", "fair_prob", "edge", "predicted",
              "mu_raw_model_projection", "std_dev", "z_score",
              "prob_over", "market_class", "is_alternate_market"]
    lines.append("| Field | " + " | ".join(p[0] for p in PLAYERS) + " |")
    lines.append("|" + "|".join(["---"] * (len(PLAYERS) + 1)) + "|")
    for f in fields:
        row = [f]
        for name, _, _ in PLAYERS:
            row.append(fmt(persisted_rows[name].get(f)))
        lines.append("| " + " | ".join(row) + " |")

    # 2. Raw game logs
    lines.append(section("2. Raw L20 game logs (strikeouts per game)"))
    for name, _, _ in PLAYERS:
        gl = log_rows[name]
        lines.append(f"\n### {name}\n")
        lines.append(f"team={gl.get('team')}, bat_side={gl.get('bat_side')}, "
                     f"in_lineup={gl.get('is_in_lineup_today')}, "
                     f"n_logs_total={gl.get('n_logs_total')}\n")
        lines.append("| # | Date | K | PA | AB | Opp |")
        lines.append("|---|---|---|---|---|---|")
        for i, r in enumerate(gl.get("rows", []), 1):
            lines.append(f"| {i} | {r['date']} | {r['K']} | {r['PA']} | {r['AB']} | {r['opp']} |")
        lines.append(f"\n**Averages:** L5={fmt(gl.get('L5_K_avg'))}  "
                     f"L10={fmt(gl.get('L10_K_avg'))}  L20={fmt(gl.get('L20_K_avg'))}\n")
        lines.append(f"**Hit-rate >0.5:** L5={fmt(gl.get('L5_HR_over_05_pct'), 1)}%  "
                     f"L10={fmt(gl.get('L10_HR_over_05_pct'), 1)}%  "
                     f"L20={fmt(gl.get('L20_HR_over_05_pct'), 1)}%\n")
        lines.append(f"**PA avg:** L5={fmt(gl.get('L5_PA_avg'), 2)}  "
                     f"L10={fmt(gl.get('L10_PA_avg'), 2)}\n")

    # 3. Reproduced inference
    lines.append(section("3. Live predict() reproduction (clean re-run)"))
    lines.append(f"Model pickle: `mlb_hf_strikeouts.pkl`  norm_stat: `strikeouts`  "
                 f"(no μ-override fires for this stat — not in `_ACTIVE_BASELINE`, not pitcher).\n")
    summary_rows = ["raw_pred_from_model", "park_factor", "final_pred_mu",
                    "std_dev_used", "z_score", "prob_over_pct",
                    "imputed_features_count"]
    lines.append("| Field | " + " | ".join(p[0] for p in PLAYERS) + " |")
    lines.append("|" + "|".join(["---"] * (len(PLAYERS) + 1)) + "|")
    for f in summary_rows:
        row = [f]
        for name, _, _ in PLAYERS:
            v = inf_rows[name].get(f)
            row.append(fmt(v))
        lines.append("| " + " | ".join(row) + " |")

    # 4. Key feature side-by-side
    lines.append(section("4. Key feature values side-by-side"))
    feat_names = list((inf_rows[PLAYERS[0][0]].get("key_features") or {}).keys())
    lines.append("| Feature | " + " | ".join(p[0] for p in PLAYERS) + " |")
    lines.append("|" + "|".join(["---"] * (len(PLAYERS) + 1)) + "|")
    for fn in feat_names:
        row = [fn]
        for name, _, _ in PLAYERS:
            v = inf_rows[name].get("key_features", {}).get(fn)
            row.append(fmt(v))
        lines.append("| " + " | ".join(row) + " |")

    # 5. Imputed-feature lists
    lines.append(section("5. Imputed-feature counts"))
    for name, _, _ in PLAYERS:
        info = inf_rows[name]
        lines.append(f"\n### {name}\n")
        lines.append(f"`imputed_count = {info.get('imputed_features_count')}`")
        lines.append("\n<details><summary>Imputed list</summary>\n\n```\n")
        lines.append("\n".join(info.get("imputed_features", [])))
        lines.append("\n```\n\n</details>\n")

    md = "\n".join(lines)
    with open(OUT, "w") as f:
        f.write(md)
    print(f"Audit report written: {OUT}")
    print(f"({len(md)} bytes)")

    # Console summary
    print("\n=== CONSOLE SUMMARY ===")
    for name, _, _ in PLAYERS:
        info = inf_rows[name]
        gl = log_rows[name]
        ps = persisted_rows[name]
        print(f"\n{name}:")
        print(f"  L5 K avg: {fmt(gl.get('L5_K_avg'))}  "
              f"L10 K avg: {fmt(gl.get('L10_K_avg'))}  "
              f"L20 K avg: {fmt(gl.get('L20_K_avg'))}")
        print(f"  L5 hit-rate >0.5: {fmt(gl.get('L5_HR_over_05_pct'), 1)}%")
        print(f"  Model raw_pred: {fmt(info.get('raw_pred_from_model'))}  "
              f"park_factor: {fmt(info.get('park_factor'))}  "
              f"final_mu: {fmt(info.get('final_pred_mu'))}")
        print(f"  Persisted score-doc mu_raw_model_projection: "
              f"{fmt(ps.get('mu_raw_model_projection'))}")
        print(f"  Persisted tp={fmt(ps.get('tp'))}%  "
              f"fair_prob={fmt(ps.get('fair_prob'))}  "
              f"vision_score={fmt(ps.get('vision_score'))}")


if __name__ == "__main__":
    main()
