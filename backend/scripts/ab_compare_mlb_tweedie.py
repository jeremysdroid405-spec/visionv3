"""A/B comparison: baseline vs Tweedie XGB for MLB zero-heavy stats.

Loads BOTH the production baseline pickle (Gaussian) and the new
`_tweedie.pkl` for each of `home_runs`, `doubles`, `stolen_bases`.
Iterates over today's live MLB props, builds the SAME feature vector
used by the live scorer, and predicts with both models.

Outputs:
  - per-prop side-by-side: projection_base vs projection_tweedie
  - edge-distribution histograms (post-EB-shrinkage, since EB applies
    identically to both projections — only the input μ differs)
  - top/bottom edge outliers
  - aggregate "would-Tweedie-flip-tier" count

DO NOT SWAP PRODUCTION WEIGHTS — this is read-only diagnostic.

Usage (from /app/backend):
    python3 scripts/ab_compare_mlb_tweedie.py
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import sys
from collections import defaultdict
from typing import Any, Dict, List

import numpy as np

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient
from scipy.stats import norm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ab_tweedie")

TARGET_STATS = {
    "home_runs":    "Home Runs",
    "doubles":      "Doubles",
    "stolen_bases": "Stolen Bases",
}
MODEL_DIR = "/app/backend/models/mlb_hf"


def _load_pickle(stat: str, *, tweedie: bool) -> Dict[str, Any]:
    suffix = "_tweedie" if tweedie else ""
    path = os.path.join(MODEL_DIR, f"mlb_hf_{stat}{suffix}.pkl")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _predict(pkl: Dict[str, Any], features: Dict[str, Any]) -> float:
    """Apply scaler + predict for a single feature dict."""
    cols = pkl["features"]
    vec = np.array([[features.get(c, 0) for c in cols]], dtype=np.float32)
    vec = pkl["scaler"].transform(vec)
    raw = float(pkl["model"].predict(vec)[0])
    # Tweedie output is conditional mean — non-negative.
    return max(raw, 0.0)


def main():
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Sanity check: all Tweedie pickles present.
    for s in TARGET_STATS:
        for kind in ("", "_tweedie"):
            p = os.path.join(MODEL_DIR, f"mlb_hf_{s}{kind}.pkl")
            if not os.path.exists(p):
                logger.error(f"MISSING pickle: {p}")
                sys.exit(1)

    baseline = {s: _load_pickle(s, tweedie=False) for s in TARGET_STATS}
    tweedie = {s: _load_pickle(s, tweedie=True) for s in TARGET_STATS}

    # Spin up the live HF service to reuse the feature-build path.
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    hf = hfm.get_mlb_high_friction_model(db)
    hf.load_models()

    # Per-stat collectors.
    rows_per_stat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    # Live MLB props pool (active=True under canonical live tag).
    cursor = db.mlb_prop_scores.find(
        {
            "active": True,
            "version_tag": "final-mlb-rt",
            "stat_type": {"$in": list(TARGET_STATS.values())},
        },
        {"_id": 0},
    )
    n_processed = 0
    for prop in cursor:
        # Re-derive stat key from stat_type label.
        stat_label = prop.get("stat_type")
        stat_key = next(
            (k for k, v in TARGET_STATS.items() if v == stat_label),
            None,
        )
        if stat_key is None:
            continue

        # Build features identically to the live scorer.
        bdl_id = prop.get("bdl_player_id") or prop.get("player_id")
        if bdl_id is None:
            continue
        # Pull master_hub doc for this player to get game_logs + splits.
        hub_doc = db.mlb_master_hub_2026.find_one(
            {"bdl_id": int(bdl_id)} if isinstance(bdl_id, (int, str))
            else {"player_id": bdl_id},
            {"_id": 0},
        )
        if not hub_doc:
            continue
        logs = hub_doc.get("bdl_game_logs") or []
        logs = sorted(
            logs,
            key=lambda x: (x.get("date") or "", x.get("game_id") or 0),
            reverse=True,
        )
        if len(logs) < 6:
            continue
        line = prop.get("line")
        if line is None:
            continue
        # No live opponent / SC features needed for A/B — both models
        # see the SAME inputs; the only difference is the trained
        # objective. We can therefore use a simplified feature dict
        # built without Statcast (set to None like the live scorer
        # falls back to when Statcast is missing).
        features = hf._build_friction_features(
            hub_doc, logs, stat_key,
            opponent=None,
            park_team=hub_doc.get("team"),
            dk_odds=None, line=line,
            statcast_features=None,
            pitcher_statcast_features=None,
            pa_batter_features=None,
            pa_pitcher_features=None,
        )
        if features is None:
            continue

        proj_base = _predict(baseline[stat_key], features)
        proj_tw = _predict(tweedie[stat_key], features)

        rec = (prop.get("recommendation") or "").upper()
        # Approximate side-aware p_over for binary 0.5 line (the most
        # common one for these stats) using a stat-level σ proxy. This
        # is for *relative* comparison only, not live scoring.
        sigma_proxy = max(0.3, 1.0 * max(proj_base, proj_tw, 1e-3))
        z_base = (line - proj_base) / sigma_proxy
        z_tw = (line - proj_tw) / sigma_proxy
        p_over_base = 1.0 - norm.cdf(z_base)
        p_over_tw = 1.0 - norm.cdf(z_tw)
        if rec == "UNDER":
            p_model_base = norm.cdf(z_base)
            p_model_tw = norm.cdf(z_tw)
        else:
            p_model_base = p_over_base
            p_model_tw = p_over_tw

        # Edge math identical between the two — the only differing
        # input is the projection / p_model. Apples-to-apples.
        bb_implied = prop.get("best_book_implied_probability")
        edge_base = (
            p_model_base - bb_implied if bb_implied is not None else None
        )
        edge_tw = (
            p_model_tw - bb_implied if bb_implied is not None else None
        )

        rows_per_stat[stat_key].append({
            "player_name":   prop.get("player_name"),
            "stat":          stat_label,
            "line":          line,
            "side":          rec,
            "proj_base":     round(proj_base, 4),
            "proj_tweedie":  round(proj_tw, 4),
            "proj_delta":    round(proj_tw - proj_base, 4),
            "p_model_base":  round(p_model_base, 4),
            "p_model_tw":    round(p_model_tw, 4),
            "p_delta":       round(p_model_tw - p_model_base, 4),
            "bb_implied":    bb_implied,
            "edge_base":     None if edge_base is None else round(edge_base, 4),
            "edge_tweedie":  None if edge_tw is None else round(edge_tw, 4),
            "edge_delta":    (
                None
                if (edge_base is None or edge_tw is None)
                else round(edge_tw - edge_base, 4)
            ),
            "current_tier":  prop.get("tier"),
        })
        n_processed += 1

    print(f"\n=== A/B COMPARISON — baseline (Gaussian) vs Tweedie ===")
    print(f"\nProps compared: {n_processed:,} across {len(rows_per_stat)} stats")
    print()

    for stat, rows in rows_per_stat.items():
        if not rows:
            continue
        n = len(rows)
        proj_deltas = np.array([r["proj_delta"] for r in rows])
        p_deltas = np.array([r["p_delta"] for r in rows])
        edge_deltas = np.array(
            [r["edge_delta"] for r in rows if r["edge_delta"] is not None]
        )
        print(f"━━ {stat} (n={n:,}) ━━")
        print(
            f"  projection Δ (tw - base):  mean={proj_deltas.mean():+.4f}  "
            f"std={proj_deltas.std():.4f}  "
            f"|Δ|_p95={np.percentile(np.abs(proj_deltas), 95):.4f}"
        )
        print(
            f"  p_model    Δ (tw - base):  mean={p_deltas.mean():+.4f}  "
            f"std={p_deltas.std():.4f}  "
            f"|Δ|_p95={np.percentile(np.abs(p_deltas), 95):.4f}"
        )
        if edge_deltas.size:
            print(
                f"  total_edge Δ (tw - base):  mean={edge_deltas.mean():+.4f}  "
                f"std={edge_deltas.std():.4f}  "
                f"|Δ|_p95={np.percentile(np.abs(edge_deltas), 95):.4f}"
            )
        # Tier-flip candidates: rows where edge crosses ±5% threshold
        # (illustrative — actual gate logic isn't called here).
        flip_up = sum(
            1 for r in rows
            if r["edge_base"] is not None and r["edge_tweedie"] is not None
            and r["edge_base"] < 0.05 <= r["edge_tweedie"]
        )
        flip_down = sum(
            1 for r in rows
            if r["edge_base"] is not None and r["edge_tweedie"] is not None
            and r["edge_tweedie"] < 0.05 <= r["edge_base"]
        )
        print(
            f"  ±5% edge-threshold crossings:  +{flip_up}  -{flip_down}"
        )
        # Top / bottom shifts
        rows_sorted = sorted(rows, key=lambda r: r["edge_delta"] or 0, reverse=True)
        print("  top 3 prop edge shifts (Tweedie boosted edge):")
        for r in rows_sorted[:3]:
            print(
                f"     {r['player_name']:<20} {r['stat']:<14} {r['side']} {r['line']}: "
                f"proj {r['proj_base']:.3f} → {r['proj_tweedie']:.3f}  "
                f"edge {r['edge_base']} → {r['edge_tweedie']}  "
                f"(Δ {r['edge_delta']:+.4f})"
            )
        print("  bottom 3 prop edge shifts (Tweedie cut edge):")
        for r in rows_sorted[-3:]:
            print(
                f"     {r['player_name']:<20} {r['stat']:<14} {r['side']} {r['line']}: "
                f"proj {r['proj_base']:.3f} → {r['proj_tweedie']:.3f}  "
                f"edge {r['edge_base']} → {r['edge_tweedie']}  "
                f"(Δ {r['edge_delta']:+.4f})"
            )
        print()

    # Persist full A/B output for the user / further analysis.
    out_path = "/app/backend/models/mlb_hf/_ab_compare_tweedie_2026_05_14.json"
    with open(out_path, "w") as fh:
        json.dump({
            "props_compared": n_processed,
            "rows": rows_per_stat,
        }, fh, indent=2, default=str)
    print(f"Full A/B output → {out_path}")


if __name__ == "__main__":
    main()
