"""NBA LOM v1.1 trainer — alternate window experiments.

Variants:
  v11_L20    : sample window = prior 20 games (was 10)
  v11_blend  : window = 20, but adds proj_L10/sigma_L10 as extra features
               so the model can learn which window matters per stat.

Saves to /tmp/lom_v11/{variant}_{stat}.pkl so we don't disturb production
artifacts at /app/backend/models/probability/lom/nba/.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import pickle
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import train_test_split

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ARTIFACT_DIR = "/tmp/lom_v11"
SUPPORTED_STATS = ["PTS", "REB", "AST", "PRA"]
STAT_FIELD = {"PTS": "pts", "REB": "reb", "AST": "ast"}


def _num(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError):
        try:
            mm, ss = str(v).split(":")
            return float(mm) + float(ss) / 60.0
        except Exception: return None


def _stat_value(g, st):
    if st == "PRA":
        p, r, a = g.get("pts"), g.get("reb"), g.get("ast")
        if None in (p, r, a): return None
        try: return float(p) + float(r) + float(a)
        except Exception: return None
    return _num(g.get(STAT_FIELD.get(st)))


def _generate_line_grid(proj: float) -> List[float]:
    lo = math.floor(proj) - 3
    hi = math.ceil(proj) + 3
    out = [round(float(i) + off - 1.0, 1)
           for i in range(int(lo), int(hi) + 1)
           for off in (0.5, 1.5)]
    return sorted({x for x in out if x > 0})


def _reliability_curve(y_true: np.ndarray, y_pred: np.ndarray, n_buckets: int = 10):
    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    out = []
    for i in range(n_buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_pred >= lo) & (y_pred < hi if hi < 1.0 else y_pred <= hi)
        n = int(mask.sum())
        if n == 0:
            out.append({"lo": float(lo), "hi": float(hi), "n": 0,
                        "avg_pred": 0.0, "actual_rate": 0.0, "error": 0.0})
            continue
        out.append({"lo": float(lo), "hi": float(hi), "n": n,
                    "avg_pred": round(float(y_pred[mask].mean()), 4),
                    "actual_rate": round(float(y_true[mask].mean()), 4),
                    "error": round(abs(float(y_pred[mask].mean()) - float(y_true[mask].mean())), 4)})
    return out


async def build_training_set(
    stat: str, *, window: int = 10, blend: bool = False,
    max_players: Optional[int] = None,
):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    rows: List[Dict[str, Any]] = []
    cur = db["nba_master_hub_2026"].find(
        {"bdl_game_logs": {"$exists": True, "$ne": []}},
        {"_id": 0, "bdl_game_logs": 1},
    )
    n_players = 0
    skipped = 0
    min_history = max(window, 30)
    async for player_doc in cur:
        if max_players is not None and n_players >= max_players: break
        n_players += 1
        gl = sorted(player_doc.get("bdl_game_logs") or [],
                    key=lambda x: x.get("date") or "1900-01-01")
        if len(gl) < min_history: skipped += 1; continue

        for i in range(window, len(gl)):
            window_games = gl[max(0, i - window):i]
            stat_vals = [_stat_value(g, stat) for g in window_games]
            stat_vals = [v for v in stat_vals if v is not None]
            if len(stat_vals) < max(8, window // 2): continue

            proj = statistics.fmean(stat_vals)
            try: sigma = max(statistics.pstdev(stat_vals), 0.5)
            except statistics.StatisticsError: continue
            cv = sigma / proj if proj > 0 else 0.0

            # Optional L10 features (for blend variant)
            if blend:
                w10 = gl[max(0, i - 10):i]
                stat10 = [_stat_value(g, stat) for g in w10]
                stat10 = [v for v in stat10 if v is not None]
                if len(stat10) < 5: continue
                proj_l10 = statistics.fmean(stat10)
                try: sigma_l10 = max(statistics.pstdev(stat10), 0.5)
                except statistics.StatisticsError: continue
            else:
                proj_l10 = sigma_l10 = None

            actual = _stat_value(gl[i], stat)
            if actual is None: continue

            for line in _generate_line_grid(proj):
                hits = sum(1 for v in stat_vals if v > line)
                hr_at_line = hits * 100.0 / len(stat_vals)
                row = {
                    "projection": float(proj),
                    "line": float(line),
                    "line_distance": float(proj) - float(line),
                    "line_distance_ratio": (float(proj) - float(line)) / float(sigma),
                    "sigma": float(sigma),
                    "hr_at_line": float(hr_at_line),
                    "hr_sample_size": float(len(stat_vals)),
                    "cv": float(cv),
                    "hr_missing": 0.0,
                    "_label": 1 if actual > line else 0,
                }
                if blend:
                    row["projection_l10"] = float(proj_l10)
                    row["sigma_l10"] = float(sigma_l10)
                    row["proj_delta_l10_l20"] = float(proj_l10) - float(proj)
                rows.append(row)

        if n_players % 100 == 0:
            log.info(f"  walked {n_players} players, rows={len(rows)} skipped={skipped}")
    log.info(f"  walked {n_players} players (final), rows={len(rows)} skipped={skipped}")
    return rows


def train_one(rows, stat, *, blend: bool):
    feature_cols = [
        "projection", "line", "line_distance", "line_distance_ratio",
        "sigma", "hr_at_line", "hr_sample_size", "cv", "hr_missing",
    ]
    if blend:
        feature_cols += ["projection_l10", "sigma_l10", "proj_delta_l10_l20"]

    X = np.array([[r[c] for c in feature_cols] for r in rows], dtype=np.float64)
    y = np.array([r["_label"] for r in rows], dtype=np.int64)
    pos_rate = float(y.mean())
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y,
    )
    base = LogisticRegression(max_iter=1000, solver="lbfgs", C=1.0)
    model = CalibratedClassifierCV(base, method="isotonic", cv=5)
    model.fit(X_tr, y_tr)
    p_te = model.predict_proba(X_te)[:, 1]

    return {
        "model": model, "feature_cols": feature_cols, "stat_family": stat,
        "sport": "nba", "version": "v1.1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": int(len(rows)), "n_train": int(len(y_tr)), "n_test": int(len(y_te)),
        "pos_rate": round(pos_rate, 4),
        "brier": round(float(brier_score_loss(y_te, p_te)), 4),
        "log_loss": round(float(log_loss(y_te, np.clip(p_te, 1e-6, 1 - 1e-6))), 4),
        "reliability": _reliability_curve(y_te, p_te),
    }


async def run_variant(stat: str, *, window: int, blend: bool, label: str):
    log.info(f"=== Training NBA LOM {label} stat={stat} (window={window} blend={blend}) ===")
    t0 = time.time()
    rows = await build_training_set(stat, window=window, blend=blend)
    if not rows:
        log.warning("  no rows produced — abort"); return
    art = train_one(rows, stat, blend=blend)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    out_path = os.path.join(ARTIFACT_DIR, f"{label}_{stat.lower()}.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(art, f)
    log.info(
        f"  Brier={art['brier']:.4f}  LogLoss={art['log_loss']:.4f}  "
        f"n={art['n_rows']:,}  pos_rate={art['pos_rate']}  "
        f"saved {out_path}  elapsed={time.time()-t0:.1f}s"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", default="all")
    ap.add_argument("--variant", choices=["L20", "blend", "both"], default="both")
    args = ap.parse_args()

    stats = SUPPORTED_STATS if args.stat == "all" else [args.stat.upper()]
    variants = []
    if args.variant in ("L20", "both"):
        variants.append(("v11_L20", 20, False))
    if args.variant in ("blend", "both"):
        variants.append(("v11_blend", 20, True))

    for st in stats:
        for label, window, blend in variants:
            asyncio.run(run_variant(st, window=window, blend=blend, label=label))


if __name__ == "__main__":
    main()
