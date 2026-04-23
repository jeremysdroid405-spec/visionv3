"""
Head-to-head evaluation — VK2 52-feat pruned baseline vs VK2 56-feat
+oppmodel (pruned52 + 4 opportunity-model features).

Goals (from the P0 integration spec):
  1. Confirm VK2 projections remain primary (opportunity model NEVER
     replaces / blends).
  2. Confirm opportunity features appear in top-N feature importance.
  3. Confirm low-line bias improves.
      - PTS < 10 line: over-prediction bias should shrink
      - PRA < 10 line: over-prediction bias should shrink
      - Bench players (min_played_L5_mean < 18) RMSE should drop
      - Starter stability: RMSE on starters (>= 28 min L5) must not
        regress by more than 1%.

Read-only — rebuilds the 2024 test matrix once for both models (same
row order via the same bdl_historical_game_logs sort), scores under
each pkl, and writes a markdown report to
`/app/backend/reports/vk2_oppmodel_eval.md`.
"""
from __future__ import annotations

import math
import os
import pickle
import sys
import time

import numpy as np
import pymongo

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from scripts.retrain_nba_vk2 import (  # noqa: E402
    NBAOpportunityAdapter,
    PRUNED_FEATURES,
    PRUNED_OPPMODEL_FEATURES,
    build_training_matrix,
    preload_advanced_stats,
)

MODEL_DIR = "/app/backend/models"
REPORT_PATH = "/app/backend/reports/vk2_oppmodel_eval.md"

STATS = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "3PM": "fg3m",
    "PRA": "pra",
}

# Low-line thresholds (from spec)
LOW_LINE = {"PTS": 10.0, "REB": 4.0, "AST": 3.0, "3PM": 1.5, "PRA": 20.0}
# Column name in feature matrix used for role bucketing
MIN_L5_COL = "min_played_L5_mean"


def _load(p):
    with open(p, "rb") as f:
        return pickle.load(f)


def _metrics(y_true, y_pred):
    err = y_pred - y_true
    return {
        "n": int(len(y_true)),
        "mae": float(np.abs(err).mean()) if len(y_true) else 0.0,
        "rmse": float(math.sqrt((err * err).mean())) if len(y_true) else 0.0,
        "bias_mean": float(err.mean()) if len(y_true) else 0.0,
    }


def _score(model_payload, X_full, feature_cols_full):
    """Slice X_full to payload's feature list, scale, predict."""
    want = model_payload["features"]
    idx = [feature_cols_full.index(f) for f in want]
    X = X_full[:, idx]
    X_s = model_payload["scaler"].transform(X)
    return model_payload["model"].predict(X_s)


def main():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    adv_map = preload_advanced_stats()
    adapter = NBAOpportunityAdapter()
    adapter._load()

    # Build ONCE using the superset (56-feat) schema — pruned52 is a
    # subset, so we can evaluate both against the same rows.
    schema = set(PRUNED_OPPMODEL_FEATURES)

    lines_out = [
        "# VK2 +oppmodel (56-feat) vs pruned52 — head-to-head\n",
        "Generated: " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "",
        "Opportunity model = strictly feature generator (expected_minutes, risk, bucket one-hot). "
        "VK2 remains the ONLY projection model. Comparing 2024 test-set (held-out) "
        "performance, with focus on low-line bias and bench-vs-starter stability.",
        "",
    ]

    for label, field in STATS.items():
        t0 = time.monotonic()
        lines_out.append(f"## {label}\n")
        base_pkl = os.path.join(MODEL_DIR, f"vk2_{label.lower()}.pkl")  # production = pruned52
        opp_pkl  = os.path.join(MODEL_DIR, f"vk2_{label.lower()}_oppmodel.pkl")
        if not (os.path.exists(base_pkl) and os.path.exists(opp_pkl)):
            lines_out.append(f"- SKIP: missing pkl (base={os.path.exists(base_pkl)}, opp={os.path.exists(opp_pkl)})\n")
            continue
        base = _load(base_pkl)
        opp  = _load(opp_pkl)

        X, y, sw, feature_cols = build_training_matrix(
            label, field, adv_map=adv_map,
            target_schema=schema,
            opportunity_adapter=adapter,
        )
        if X is None:
            lines_out.append("- SKIP: empty matrix\n")
            continue

        # Test mask = 2024 rows (weight 1.0) — same split as training.
        test_mask = sw >= 0.99
        X_te = X[test_mask]; y_te = y[test_mask]

        # Score both models
        yp_base = _score(base, X_te, feature_cols)
        yp_opp  = _score(opp,  X_te, feature_cols)

        m_base = _metrics(y_te, yp_base)
        m_opp  = _metrics(y_te, yp_opp)
        lines_out.append(f"### Global (n={m_base['n']})")
        lines_out.append(
            f"- **base52 **: MAE={m_base['mae']:.4f}  RMSE={m_base['rmse']:.4f}  bias={m_base['bias_mean']:+.4f}"
        )
        lines_out.append(
            f"- **oppmodel56**: MAE={m_opp['mae']:.4f}  RMSE={m_opp['rmse']:.4f}  bias={m_opp['bias_mean']:+.4f}"
        )
        mae_d = m_opp['mae'] - m_base['mae']
        rmse_d = m_opp['rmse'] - m_base['rmse']
        bias_d = abs(m_opp['bias_mean']) - abs(m_base['bias_mean'])
        lines_out.append(f"- Δ (opp-base): MAE {mae_d:+.4f}  RMSE {rmse_d:+.4f}  |bias| {bias_d:+.4f}")
        lines_out.append("")

        # Low-line segment (props with implied line < threshold).
        # We don't have actual lines; proxy: "low-line candidates" =
        # samples whose opportunity bucket is 'low' (bench) OR whose
        # predicted value (from oppmodel) is under the low-line cut.
        ll = LOW_LINE[label]
        low_mask = yp_opp < ll
        if low_mask.sum() >= 30:
            m_base_low = _metrics(y_te[low_mask], yp_base[low_mask])
            m_opp_low  = _metrics(y_te[low_mask], yp_opp[low_mask])
            lines_out.append(f"### Low-line (predicted < {ll}, n={m_base_low['n']})")
            lines_out.append(
                f"- **base52 **: MAE={m_base_low['mae']:.4f}  RMSE={m_base_low['rmse']:.4f}  bias={m_base_low['bias_mean']:+.4f}"
            )
            lines_out.append(
                f"- **oppmodel56**: MAE={m_opp_low['mae']:.4f}  RMSE={m_opp_low['rmse']:.4f}  bias={m_opp_low['bias_mean']:+.4f}"
            )
            lines_out.append(
                f"- Δ (opp-base): MAE {m_opp_low['mae']-m_base_low['mae']:+.4f}  "
                f"RMSE {m_opp_low['rmse']-m_base_low['rmse']:+.4f}  "
                f"|bias| {abs(m_opp_low['bias_mean'])-abs(m_base_low['bias_mean']):+.4f}"
            )
            lines_out.append("")

        # Bench / starter split via min_played_L5_mean column
        try:
            min_idx = feature_cols.index(MIN_L5_COL)
            min_l5 = X_te[:, min_idx]
            bench_mask = min_l5 < 18.0
            starter_mask = min_l5 >= 28.0
            for name, mask in [("bench (L5<18min)", bench_mask),
                               ("starters (L5>=28min)", starter_mask)]:
                if mask.sum() < 30:
                    continue
                m_b = _metrics(y_te[mask], yp_base[mask])
                m_o = _metrics(y_te[mask], yp_opp[mask])
                lines_out.append(f"### {name} (n={m_b['n']})")
                lines_out.append(
                    f"- base52 : MAE={m_b['mae']:.4f}  RMSE={m_b['rmse']:.4f}  bias={m_b['bias_mean']:+.4f}"
                )
                lines_out.append(
                    f"- opp56  : MAE={m_o['mae']:.4f}  RMSE={m_o['rmse']:.4f}  bias={m_o['bias_mean']:+.4f}"
                )
                lines_out.append(
                    f"- Δ: MAE {m_o['mae']-m_b['mae']:+.4f}  "
                    f"RMSE {m_o['rmse']-m_b['rmse']:+.4f}  "
                    f"|bias| {abs(m_o['bias_mean'])-abs(m_b['bias_mean']):+.4f}"
                )
                lines_out.append("")
        except ValueError:
            lines_out.append("- (could not locate min_played_L5_mean for bench/starter split)")

        # Feature importance of opp_* features in the new model
        feats = opp["features"]
        imps = opp["model"].feature_importances_
        fi = sorted(zip(feats, imps), key=lambda t: -t[1])
        top20_names = [n for n, _ in fi[:20]]
        opp_in_top = [f for f in ("opp_expected_minutes", "opp_risk_score",
                                  "opp_bucket_high", "opp_bucket_low")
                      if f in top20_names]
        lines_out.append(f"### Feature importance (opportunity features)")
        rank_map = {n: i + 1 for i, (n, _) in enumerate(fi)}
        for f in ("opp_expected_minutes", "opp_risk_score",
                  "opp_bucket_high", "opp_bucket_low"):
            imp = dict(fi).get(f, 0.0)
            rank = rank_map.get(f, "?")
            lines_out.append(f"- `{f}` rank #{rank}/{len(feats)} importance={imp:.4f}")
        lines_out.append(f"- opportunity features in top-20: {opp_in_top}")
        lines_out.append("")
        lines_out.append(f"_eval {label} took {time.monotonic()-t0:.1f}s_")
        lines_out.append("")

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines_out))
    print(f"\nReport written to {REPORT_PATH}")
    client.close()


if __name__ == "__main__":
    main()
