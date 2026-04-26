"""NBA Line-Outcome Model trainer — PTS / REB / AST / PRA.

For each player with >= 30 bdl_game_logs entries, walk chronologically.
At each game (starting at index 10) compute:
   proj  = mean of stat over the prior 10 games
   sigma = max(stdev of stat over prior 10, 0.5)
For each line in {floor(proj)-3 .. ceil(proj)+3} at every 0.5 boundary,
emit a training row {projection, line, line_distance, line_distance_ratio,
sigma, hr_at_line, hr_sample_size, cv} with label `actual > line`.

Trains CalibratedClassifierCV(LogisticRegression, isotonic) per stat,
saves to /app/backend/models/probability/lom/nba/{stat}.pkl with the
exact same artifact contract as the MLB LOM (so universal loader works).

Usage:
    python -m scripts.train_nba_lom --stat PTS
    python -m scripts.train_nba_lom --stat all
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

ARTIFACT_DIR = "/app/backend/models/probability/lom/nba"
SUPPORTED_STATS = ["PTS", "REB", "AST", "PRA"]
STAT_FIELD = {"PTS": "pts", "REB": "reb", "AST": "ast"}
FEATURE_COLS = [
    "projection",
    "line",
    "line_distance",
    "line_distance_ratio",
    "sigma",
    "hr_at_line",
    "hr_sample_size",
    "cv",
    # missing-flags so the model treats "no data" distinctly from 0
    "hr_missing",
]


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            mm, ss = str(v).split(":")
            return float(mm) + float(ss) / 60.0
        except Exception:
            return None


def _stat_value(g, st):
    if st == "PRA":
        p, r, a = g.get("pts"), g.get("reb"), g.get("ast")
        if p is None or r is None or a is None:
            return None
        try:
            return float(p) + float(r) + float(a)
        except Exception:
            return None
    return _num(g.get(STAT_FIELD.get(st)))


def _generate_line_grid(proj: float) -> List[float]:
    lo = math.floor(proj) - 3
    hi = math.ceil(proj) + 3
    out = []
    for ln_int in range(int(lo), int(hi) + 1):
        for off in (0.5, 1.5):
            out.append(float(ln_int) + off - 1.0)
    out = [round(x, 1) for x in out if x > 0]
    return sorted(set(out))


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
        avg_pred = float(y_pred[mask].mean())
        actual = float(y_true[mask].mean())
        out.append({"lo": float(lo), "hi": float(hi), "n": n,
                    "avg_pred": round(avg_pred, 4),
                    "actual_rate": round(actual, 4),
                    "error": round(abs(avg_pred - actual), 4)})
    return out


async def build_training_set(stat: str, max_players: Optional[int] = None) -> List[Dict[str, Any]]:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    rows: List[Dict[str, Any]] = []
    cur = db["nba_master_hub_2026"].find(
        {"bdl_game_logs": {"$exists": True, "$ne": []}},
        {"_id": 0, "bdl_game_logs": 1, "display_name": 1, "bdl_player_id": 1},
    )
    n_players = 0
    skipped = 0
    async for player_doc in cur:
        if max_players is not None and n_players >= max_players:
            break
        n_players += 1
        gl = sorted(
            player_doc.get("bdl_game_logs") or [],
            key=lambda x: x.get("date") or "1900-01-01",
        )
        if len(gl) < 30:
            skipped += 1
            continue
        # Walk chronologically (oldest first). At each game i >= 10 we
        # know the prior 10 games and use them as the projection sample.
        for i in range(10, len(gl)):
            window = gl[max(0, i - 10):i]
            stat_vals = [_stat_value(g, stat) for g in window]
            stat_vals = [v for v in stat_vals if v is not None]
            if len(stat_vals) < 8:
                continue
            proj = statistics.fmean(stat_vals)
            try:
                sigma = max(statistics.pstdev(stat_vals), 0.5)
            except statistics.StatisticsError:
                continue
            cv = sigma / proj if proj > 0 else 0.0
            actual = _stat_value(gl[i], stat)
            if actual is None:
                continue
            for line in _generate_line_grid(proj):
                # hr_at_line in 0..100: % of last-10 games that beat the line
                hits = sum(1 for v in stat_vals if v > line)
                hr_at_line = hits * 100.0 / len(stat_vals)
                rows.append({
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
                })
        if n_players % 50 == 0:
            log.info(f"  walked {n_players} players, rows={len(rows)} skipped={skipped}")
    log.info(f"  walked {n_players} players (final), rows={len(rows)} skipped={skipped}")
    return rows


def train_one(rows: List[Dict[str, Any]], stat: str) -> Dict[str, Any]:
    if not rows:
        raise RuntimeError(f"No training rows for {stat}")
    X = np.array([[r[c] for c in FEATURE_COLS] for r in rows], dtype=np.float64)
    y = np.array([r["_label"] for r in rows], dtype=np.int64)
    pos_rate = float(y.mean())
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.20, random_state=42, stratify=y)

    base = LogisticRegression(max_iter=1000, solver="lbfgs", C=1.0)
    model = CalibratedClassifierCV(base, method="isotonic", cv=5)
    model.fit(X_tr, y_tr)

    p_te = model.predict_proba(X_te)[:, 1]
    brier = float(brier_score_loss(y_te, p_te))
    ll = float(log_loss(y_te, np.clip(p_te, 1e-6, 1 - 1e-6)))
    rel = _reliability_curve(y_te, p_te)

    return {
        "model": model,
        "feature_cols": FEATURE_COLS,
        "stat_family": stat,
        "sport": "nba",
        "version": "v1-no-market",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": int(len(rows)),
        "n_train": int(len(y_tr)),
        "n_test": int(len(y_te)),
        "pos_rate": round(pos_rate, 4),
        "brier": round(brier, 4),
        "log_loss": round(ll, 4),
        "reliability": rel,
    }


async def run(stat: str, max_players: Optional[int] = None) -> None:
    log.info(f"=== Training NBA LOM stat={stat} ===")
    t0 = time.time()
    rows = await build_training_set(stat, max_players=max_players)
    if not rows:
        log.warning("  no rows produced — aborting")
        return
    art = train_one(rows, stat)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    out_path = os.path.join(ARTIFACT_DIR, f"{stat.lower()}.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(art, f)
    elapsed = time.time() - t0
    size = os.path.getsize(out_path)
    log.info(
        f"  Brier={art['brier']:.4f}  LogLoss={art['log_loss']:.4f}  "
        f"n={art['n_rows']:,} pos_rate={art['pos_rate']}"
    )
    log.info(f"  saved {out_path}  ({size:,} B)  elapsed={elapsed:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", required=True, help="PTS|REB|AST|PRA|all")
    ap.add_argument("--max-players", type=int, default=None)
    args = ap.parse_args()

    stats = SUPPORTED_STATS if args.stat == "all" else [args.stat.upper()]
    for st in stats:
        if st not in SUPPORTED_STATS:
            log.error(f"unsupported stat: {st}")
            sys.exit(1)
        asyncio.run(run(st, max_players=args.max_players))


if __name__ == "__main__":
    main()
