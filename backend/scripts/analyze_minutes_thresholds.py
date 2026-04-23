"""Minutes-Threshold Analysis for NBA Role Stability (2026-04-23).

Pure analysis — no projection / gate / model changes.

Tests five candidate cutoffs (24, 26, 28, 30, 32 minutes) to find the
best separator between "stable role player" and "minutes-risk" for
live NBA props.

Bucketing rule (per cutoff):
  high_minutes : min_L5_mean >= cutoff OR min_L10_mean >= cutoff
  low_minutes  : both are below cutoff

Sources:
  * Live board (`nba_prop_scores @ final-nba-rt`) + player rolling
    minutes computed from `bdl_historical_game_logs`: for the "how
    many live props / passes / risky OVERs" slice.
  * 2024 training hold-out (45k+ samples per stat, from the VK2
    build_training_matrix): for projection error + low-line bias
    stratified by bucket. Uses the rolling features already in the
    matrix so no re-sweep needed.

Output:
  /app/backend/reports/minutes_threshold_analysis.json
  /app/backend/reports/minutes_threshold_analysis.md
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from collections import OrderedDict, defaultdict
from typing import Dict, List, Tuple

import numpy as np
import pymongo

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from scripts.retrain_nba_vk2 import (  # noqa: E402
    build_training_matrix, preload_advanced_stats,
)
from scripts.train_expected_minutes import _parse_minutes  # noqa: E402

REPORT_JSON = "/app/backend/reports/minutes_threshold_analysis.json"
REPORT_MD = "/app/backend/reports/minutes_threshold_analysis.md"
os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)

CUTOFFS = [24, 26, 28, 30, 32]
VK2_MODEL_FMT = "/app/backend/models/vk2_{stat}.pkl"
STATS = [("PTS", "pts"), ("REB", "reb"), ("AST", "ast"),
         ("3PM", "fg3m"), ("PRA", "pra")]


def _predict_vk2(stat, X, feature_cols):
    with open(VK2_MODEL_FMT.format(stat=stat.lower()), "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]
    idx = [feature_cols.index(f) for f in schema]
    X_s = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(X_s)


def _rmse(y, yp):
    return float(math.sqrt(np.mean((y - yp) ** 2)))


def _segment_metrics(y, yp, label):
    if len(y) == 0:
        return {"label": label, "n": 0}
    mae = float(np.mean(np.abs(y - yp)))
    rmse = _rmse(y, yp)
    bias = float(np.mean(yp - y))
    return {
        "label": label,
        "n": int(len(y)),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "actual_mean": round(float(y.mean()), 3),
        "pred_mean": round(float(yp.mean()), 3),
    }


def analyze_historical_buckets(cutoffs):
    """For each stat and cutoff, compute bucketed error metrics on 2024."""
    print("[mta] preloading adv map...", flush=True)
    adv = preload_advanced_stats()

    by_stat: Dict[str, Dict] = OrderedDict()
    for stat_label, stat_field in STATS:
        t0 = time.time()
        print(f"[mta] {stat_label} building matrix...", flush=True)
        X, y, sw, feature_cols = build_training_matrix(
            stat_label, stat_field, adv_map=adv, target_schema=None,
        )
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        pred = _predict_vk2(stat_label, X_te, feature_cols)
        print(f"[mta] {stat_label} n_test={len(y_te):,} "
              f"build={time.time() - t0:.1f}s", flush=True)

        l5 = X_te[:, feature_cols.index("min_played_L5_mean")]
        l10 = X_te[:, feature_cols.index("min_played_L10_mean")]

        rows_for_stat: Dict[int, Dict] = {}
        for cutoff in cutoffs:
            high_mask = (l5 >= cutoff) | (l10 >= cutoff)
            low_mask = ~high_mask
            low_line_mask = y_te < 10
            high_line_mask = y_te >= 20

            rows_for_stat[cutoff] = {
                "high_minutes": {
                    "n_total": int(high_mask.sum()),
                    "overall": _segment_metrics(y_te[high_mask], pred[high_mask], "high_all"),
                    "low_line": _segment_metrics(
                        y_te[high_mask & low_line_mask],
                        pred[high_mask & low_line_mask], "high_<10",
                    ),
                    "high_line": _segment_metrics(
                        y_te[high_mask & high_line_mask],
                        pred[high_mask & high_line_mask], "high_>=20",
                    ),
                },
                "low_minutes": {
                    "n_total": int(low_mask.sum()),
                    "overall": _segment_metrics(y_te[low_mask], pred[low_mask], "low_all"),
                    "low_line": _segment_metrics(
                        y_te[low_mask & low_line_mask],
                        pred[low_mask & low_line_mask], "low_<10",
                    ),
                    "high_line": _segment_metrics(
                        y_te[low_mask & high_line_mask],
                        pred[low_mask & high_line_mask], "low_>=20",
                    ),
                },
            }
        by_stat[stat_label] = rows_for_stat
    return by_stat


def compute_player_minutes_l5_l10(db, player_ids):
    """Pull each player's most recent 10 game logs (any season) and
    compute L5_mean / L10_mean. Returns {player_id: (L5, L10)}."""
    coll = db.bdl_historical_game_logs
    out: Dict[int, Tuple[float, float]] = {}
    for pid in player_ids:
        docs = list(coll.find(
            {"player_id": int(pid)},
            {"_id": 0, "min": 1, "game_id": 1},
        ).sort("game_id", -1).limit(10))
        if not docs:
            continue
        mins = []
        for d in docs:
            m = _parse_minutes(d.get("min"))
            mins.append(m if m is not None else 0.0)
        L5  = float(np.mean(mins[:5]))  if len(mins) >= 1 else 0.0
        L10 = float(np.mean(mins[:10])) if len(mins) >= 1 else 0.0
        out[int(pid)] = (L5, L10)
    return out


def analyze_live_board(cutoffs):
    """Pull live NBA scored docs, bucket by cutoff, count passes and
    risky OVER picks."""
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    scores = db["nba_prop_scores"]
    base_match = {
        "version_tag": "final-nba-rt",
        "sport": "nba",
        "model_projection": {"$ne": None},
        "bdl_player_id": {"$ne": None},
    }
    docs = list(scores.find(base_match, {
        "_id": 0, "player_name": 1, "bdl_player_id": 1,
        "stat_type": 1, "line": 1, "recommendation": 1,
        "tier": 1, "tier_reason": 1,
        "model_projection": 1, "hit_rate_over": 1, "hit_rate_under": 1,
        "pp_playable": 1, "pp_multiplier_label": 1,
        "tp": 1, "edge_pct": 1, "ranking_score_v2": 1,
    }))
    print(f"[mta] live scored NBA docs = {len(docs):,}", flush=True)
    pids = sorted({int(d["bdl_player_id"]) for d in docs})
    pm = compute_player_minutes_l5_l10(db, pids)
    print(f"[mta] resolved minutes for {len(pm):,} unique players", flush=True)

    # Attach L5/L10 to each doc; drop any doc with no minute history.
    for d in docs:
        L5, L10 = pm.get(int(d["bdl_player_id"]), (0.0, 0.0))
        d["_L5"] = L5
        d["_L10"] = L10

    def bucket(d, cutoff):
        return "high_minutes" if (d["_L5"] >= cutoff or d["_L10"] >= cutoff) else "low_minutes"

    results: Dict[int, Dict] = {}
    for cutoff in cutoffs:
        counts = defaultdict(lambda: defaultdict(int))
        passes = defaultdict(lambda: defaultdict(int))
        over_passes_risky: List[Dict] = []
        hit_rates = defaultdict(list)
        for d in docs:
            b = bucket(d, cutoff)
            rec = d.get("recommendation") or "?"
            tier = d.get("tier") or "unknown"
            counts[b][rec] += 1
            counts[b]["total"] += 1
            if tier in ("front_lines", "safe_haven"):
                passes[b][tier] += 1
                passes[b]["passes_total"] += 1
                if b == "low_minutes" and rec == "OVER":
                    over_passes_risky.append({
                        "player": d.get("player_name"),
                        "stat": d.get("stat_type"),
                        "line": d.get("line"),
                        "tier": tier,
                        "model_projection": d.get("model_projection"),
                        "hit_rate_over": d.get("hit_rate_over"),
                        "edge_pct": d.get("edge_pct"),
                        "ranking_score": d.get("ranking_score_v2"),
                        "L5": round(d["_L5"], 1),
                        "L10": round(d["_L10"], 1),
                        "tp": d.get("tp"),
                    })
            if d.get("hit_rate_over") is not None:
                hit_rates[b].append({
                    "over": float(d["hit_rate_over"] or 0),
                    "rec": rec,
                })
        # Summarise hit rates by bucket (OVER and UNDER separately,
        # using the player's historical hit-rate for THIS stat at THIS
        # line — best settled proxy we have without a prop settlement DB).
        summaries = {}
        for b in ("high_minutes", "low_minutes"):
            over_rows = [r["over"] for r in hit_rates[b] if r["rec"] == "OVER"]
            under_rows = [r["over"] for r in hit_rates[b] if r["rec"] == "UNDER"]
            summaries[b] = {
                "total_props": counts[b]["total"],
                "over_count":  counts[b]["OVER"],
                "under_count": counts[b]["UNDER"],
                "front_lines_passes": passes[b]["front_lines"],
                "safe_haven_passes": passes[b]["safe_haven"],
                "passes_total": passes[b]["passes_total"],
                "pass_rate_pct": (
                    round(100 * passes[b]["passes_total"] / max(counts[b]["total"], 1), 2)
                ),
                "avg_player_hit_rate_over_for_OVER_picks": (
                    round(float(np.mean(over_rows)), 2) if over_rows else None
                ),
                "avg_player_hit_rate_over_for_UNDER_picks": (
                    round(float(np.mean(under_rows)), 2) if under_rows else None
                ),
            }
        # Top 10 risky OVER picks — sort by ranking_score then edge_pct
        over_passes_risky.sort(
            key=lambda r: (-(r.get("ranking_score") or 0),
                           -(r.get("edge_pct") or 0)),
        )
        results[cutoff] = {
            "buckets": summaries,
            "risky_over_passes_count": len(over_passes_risky),
            "top_10_risky_over_passes": over_passes_risky[:10],
        }
    client.close()
    return results


def write_markdown(hist, live):
    lines = []
    lines.append("# Minutes Threshold Analysis — NBA (2026-04-23)\n")
    lines.append("Analysis only — no model / gate / projection changes.\n")
    lines.append(
        "Bucketing rule per cutoff:\n\n"
        "```\n"
        "high_minutes : min_L5_mean >= cutoff OR min_L10_mean >= cutoff\n"
        "low_minutes  : both below cutoff\n"
        "```\n"
    )

    # Live board snapshot
    lines.append("## Live board — prop / pass counts by cutoff\n")
    lines.append("| Cutoff | high_props | low_props | high_passes | low_passes | risky_OVER_passes |")
    lines.append("|-------:|-----------:|----------:|------------:|-----------:|------------------:|")
    for c in CUTOFFS:
        L = live[c]
        h = L["buckets"]["high_minutes"]
        lo = L["buckets"]["low_minutes"]
        lines.append(
            f"| {c} | {h['total_props']} | {lo['total_props']} | "
            f"{h['passes_total']} | {lo['passes_total']} | "
            f"{L['risky_over_passes_count']} |"
        )
    lines.append("")

    # Pass rates by cutoff
    lines.append("## Pass rate by cutoff\n")
    lines.append("| Cutoff | high_minutes pass_rate | low_minutes pass_rate |")
    lines.append("|-------:|----------------------:|---------------------:|")
    for c in CUTOFFS:
        L = live[c]
        lines.append(
            f"| {c} | {L['buckets']['high_minutes']['pass_rate_pct']}% | "
            f"{L['buckets']['low_minutes']['pass_rate_pct']}% |"
        )
    lines.append("")

    # Historical hit-rate proxy by bucket (player-level hit_rate_over on
    # the live docs; shown per-pick type)
    lines.append(
        "## Player historical `hit_rate_over` on the live board\n"
        "(this is each player's own historical hit rate for this stat "
        "at lines around the current one — best proxy for 'did this "
        "kind of pick historically win' without a prop settlement DB).\n"
    )
    lines.append("| Cutoff | high_mins OVER-picks avg hit | low_mins OVER-picks avg hit | high_mins UNDER-picks avg | low_mins UNDER-picks avg |")
    lines.append("|-------:|-----------------------------:|----------------------------:|-------------------------:|-------------------------:|")
    for c in CUTOFFS:
        h = live[c]["buckets"]["high_minutes"]
        lo = live[c]["buckets"]["low_minutes"]
        lines.append(
            f"| {c} | "
            f"{h['avg_player_hit_rate_over_for_OVER_picks']}% | "
            f"{lo['avg_player_hit_rate_over_for_OVER_picks']}% | "
            f"{h['avg_player_hit_rate_over_for_UNDER_picks']}% | "
            f"{lo['avg_player_hit_rate_over_for_UNDER_picks']}% |"
        )
    lines.append("")

    # Historical 2024 hold-out projection error
    lines.append("## 2024 hold-out — projection error by bucket (lower is better)\n")
    for stat_label in ("PTS", "REB", "AST", "3PM", "PRA"):
        lines.append(f"### {stat_label}\n")
        lines.append("| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |")
        lines.append("|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|")
        for c in CUTOFFS:
            r = hist[stat_label][c]
            h_all = r["high_minutes"]["overall"]
            l_all = r["low_minutes"]["overall"]
            h_low = r["high_minutes"]["low_line"]
            l_low = r["low_minutes"]["low_line"]
            lines.append(
                f"| {c} | {h_all['n']} | {h_all['rmse']} | "
                f"{h_all['bias']:+.2f} | "
                f"{l_all['n']} | {l_all['rmse']} | {l_all['bias']:+.2f} | "
                f"{l_low.get('bias','-')} | {h_low.get('bias','-')} |"
            )
        lines.append("")

    # Top risky OVER passes at each cutoff
    lines.append("## Top 10 risky `low_minutes` OVER passes on the live board\n")
    for c in CUTOFFS:
        rows = live[c]["top_10_risky_over_passes"]
        if not rows:
            continue
        lines.append(f"### Cutoff = {c} minutes — {live[c]['risky_over_passes_count']} risky OVER passes total\n")
        lines.append("| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |")
        lines.append("|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|")
        for r in rows:
            lines.append(
                f"| {r['player']} | {r['stat']} | {r['line']} | "
                f"{r['tier']} | {r['model_projection']:.2f} | "
                f"{r['L5']} | {r['L10']} | {r['hit_rate_over']} | "
                f"{r['edge_pct']} |"
            )
        lines.append("")

    with open(REPORT_MD, "w") as f:
        f.write("\n".join(lines))
    print(f"[mta] wrote {REPORT_MD}", flush=True)


def main():
    print(f"[mta] cutoffs = {CUTOFFS}", flush=True)
    print("[mta] --- live board analysis ---", flush=True)
    live = analyze_live_board(CUTOFFS)
    print("[mta] --- 2024 hold-out analysis ---", flush=True)
    hist = analyze_historical_buckets(CUTOFFS)

    # Persist JSON
    out = {"cutoffs": CUTOFFS, "historical_2024": hist, "live_board": live}
    with open(REPORT_JSON, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[mta] wrote {REPORT_JSON}", flush=True)

    write_markdown(hist, live)
    print("[mta] DONE.", flush=True)


if __name__ == "__main__":
    main()
