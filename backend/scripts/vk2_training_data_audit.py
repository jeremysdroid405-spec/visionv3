"""
VK2 Training Data Coverage Audit (2026-04-23)

Read-only. Re-walks `bdl_historical_game_logs` with the **exact same
pipeline** the trainer uses (same sort, same per-player minimum, same
sweep-forward target selection) but skips feature-building — so we can
answer every coverage question in ~25s per stat instead of the ~100s
the full matrix build takes.

Per stat (PTS, REB, AST, 3PM, PRA):
  1. Row count per season (2020..2024).
  2. Weighted contribution per season.
  3. Weights are re-validated vs the SEASON_WEIGHTS table the trainer
     uses and vs the `season_weights` dict persisted in the pkl.
  4. Leakage check — every row is tagged by (player_id, game_id); we
     assert the intersection between train (weight < 0.99) and test
     (weight >= 0.99) is EMPTY and also that no game_id crosses
     seasons in the source data.
  5. Cross-check against the pkl's persisted `samples_train`,
     `samples_test`, `weighted_sum_train`.
  6. PASS / FAIL verdict per stat + a final global verdict.

Outputs:
  /app/backend/reports/vk2_training_data_audit.md  (markdown)
  /app/backend/reports/vk2_training_data_audit.json (raw)
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
from collections import defaultdict

import pymongo

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

# Mirror the trainer's config exactly so we don't drift.
from scripts.retrain_nba_vk2 import (  # noqa: E402
    SEASONS,
    SEASON_WEIGHTS,
    MIN_GAMES_PER_PLAYER,
    ROLLING_WINDOW,
    STATS,
)

MODEL_DIR = "/app/backend/models"
REPORT_MD = "/app/backend/reports/vk2_training_data_audit.md"
REPORT_JSON = "/app/backend/reports/vk2_training_data_audit.json"

EXPECTED_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}
TEST_WEIGHT_THRESHOLD = 0.99  # trainer: test_mask = sw >= 0.99


def _pkl_path(stat_label: str) -> str:
    return os.path.join(MODEL_DIR, f"vk2_{stat_label.lower()}.pkl")


def _target_value(stat_field: str, doc: dict):
    if stat_field == "pra":
        p, r, a = doc.get("pts"), doc.get("reb"), doc.get("ast")
        if p is None or r is None or a is None:
            return None
        try:
            return float(p) + float(r) + float(a)
        except (TypeError, ValueError):
            return None
    v = doc.get(stat_field)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def audit_stat(label: str, field: str, db) -> dict:
    t0 = time.monotonic()
    coll = db.bdl_historical_game_logs

    pkl_path = _pkl_path(label)
    pkl = None
    if os.path.exists(pkl_path):
        with open(pkl_path, "rb") as f:
            pkl = pickle.load(f)

    pipeline = [
        {"$match": {"season": {"$in": SEASONS}}},
        {"$sort": {"player_id": 1, "game_id": 1}},
    ]

    # Per-season accumulators
    per_season_rows = defaultdict(int)
    per_season_weighted = defaultdict(float)
    train_keys = set()   # (pid, gid)
    test_keys = set()    # (pid, gid)
    all_player_ids = set()
    all_game_ids = set()
    game_season_map = {}  # gid -> set(seasons) — flags cross-season games
    total_rows = 0
    total_weighted = 0.0
    samples_per_player = defaultdict(int)

    current_pid = None
    current_logs: list = []

    def flush(pid, logs_chrono):
        nonlocal total_rows, total_weighted
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        # Sweep forward exactly like the trainer: i in [5, N), require
        # target has value. We don't build features but we DO emulate
        # the `history_desc >= 5` guard (always true once len >= 6).
        for i in range(5, len(logs_chrono)):
            tgt = logs_chrono[i]
            season = tgt.get("season")
            if season not in SEASON_WEIGHTS:
                continue
            tval = _target_value(field, tgt)
            if tval is None:
                continue
            # History length guard mirrors build_features' `< 5` early
            # exit — with i >= 5 we always have >= 5 history rows.
            gid = tgt.get("game_id")
            weight = SEASON_WEIGHTS[season]
            per_season_rows[season] += 1
            per_season_weighted[season] += weight
            total_rows += 1
            total_weighted += weight
            samples_per_player[pid] += 1
            all_player_ids.add(pid)
            if gid is not None:
                all_game_ids.add(gid)
                game_season_map.setdefault(gid, set()).add(season)
            key = (pid, gid)
            if weight >= TEST_WEIGHT_THRESHOLD:
                test_keys.add(key)
            else:
                train_keys.add(key)

    cursor = coll.aggregate(pipeline, allowDiskUse=True, batchSize=5000)
    for doc in cursor:
        pid = doc.get("player_id")
        if pid != current_pid:
            if current_pid is not None and current_logs:
                flush(current_pid, current_logs)
            current_pid = pid
            current_logs = []
        current_logs.append(doc)
    if current_pid is not None and current_logs:
        flush(current_pid, current_logs)

    # --- Leakage checks ---
    overlap = train_keys & test_keys
    cross_season_games = {
        gid: sorted(seas) for gid, seas in game_season_map.items()
        if len(seas) > 1
    }

    # --- Pkl cross-check ---
    pkl_samples_train = pkl.get("samples_train") if pkl else None
    pkl_samples_test = pkl.get("samples_test") if pkl else None
    pkl_weighted_sum_train = pkl.get("weighted_sum_train") if pkl else None
    pkl_seasons_used = pkl.get("seasons_used") if pkl else None
    pkl_season_weights = pkl.get("season_weights") if pkl else None

    train_rows = sum(per_season_rows[s] for s in SEASONS if s < 2024)
    test_rows = per_season_rows[2024]
    train_weighted_sum = sum(per_season_weighted[s] for s in SEASONS if s < 2024)

    # --- Weight consistency ---
    weights_match_table = (SEASON_WEIGHTS == EXPECTED_WEIGHTS)
    weights_match_pkl = (
        pkl_season_weights is not None
        and {int(k): float(v) for k, v in pkl_season_weights.items()}
        == EXPECTED_WEIGHTS
    )
    seasons_match_pkl = (
        pkl_seasons_used is not None
        and sorted(int(x) for x in pkl_seasons_used) == sorted(SEASONS)
    )

    # --- Per-stat verdict ---
    missing_seasons = [s for s in SEASONS if per_season_rows[s] == 0]
    per_season_pct = {
        s: (100.0 * per_season_rows[s] / total_rows) if total_rows else 0.0
        for s in SEASONS
    }
    min_season_pct = min(per_season_pct.values()) if per_season_pct else 0.0
    underrep_seasons = [s for s in SEASONS if per_season_pct[s] < 5.0]

    samples_train_match = (pkl_samples_train == train_rows) if pkl else None
    samples_test_match = (pkl_samples_test == test_rows) if pkl else None
    weighted_sum_match = (
        pkl_weighted_sum_train is not None
        and abs(pkl_weighted_sum_train - train_weighted_sum) < 1.0
    )

    passes = all([
        weights_match_table,
        weights_match_pkl,
        seasons_match_pkl,
        not missing_seasons,
        not underrep_seasons,
        not overlap,
        not cross_season_games,
        samples_train_match is True,
        samples_test_match is True,
        weighted_sum_match is True,
    ])

    return {
        "stat": label,
        "field": field,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "total_rows": total_rows,
        "total_weighted": round(total_weighted, 2),
        "train_rows": train_rows,
        "test_rows": test_rows,
        "train_weighted_sum": round(train_weighted_sum, 2),
        "unique_players": len(all_player_ids),
        "unique_games": len(all_game_ids),
        "per_season_rows": {int(s): int(per_season_rows[s]) for s in SEASONS},
        "per_season_weighted": {
            int(s): round(per_season_weighted[s], 2) for s in SEASONS
        },
        "per_season_pct": {int(s): round(per_season_pct[s], 2) for s in SEASONS},
        "missing_seasons": [int(s) for s in missing_seasons],
        "underrep_seasons_lt_5pct": [int(s) for s in underrep_seasons],
        "train_test_overlap_count": len(overlap),
        "cross_season_game_count": len(cross_season_games),
        "pkl": {
            "present": pkl is not None,
            "seasons_used": pkl_seasons_used,
            "season_weights": pkl_season_weights,
            "samples_train": pkl_samples_train,
            "samples_test": pkl_samples_test,
            "weighted_sum_train": pkl_weighted_sum_train,
            "samples_train_match": samples_train_match,
            "samples_test_match": samples_test_match,
            "weighted_sum_match": weighted_sum_match,
            "season_weights_match_expected": weights_match_pkl,
            "seasons_match_expected": seasons_match_pkl,
        },
        "verdict": "PASS" if passes else "FAIL",
    }


def render_md(results):
    lines = [
        "# VK2 Training Data Coverage Audit",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Seasons: 2020–2024. Expected recency weights: "
        "2024=1.00, 2023=0.85, 2022=0.70, 2021=0.55, 2020=0.40.",
        "",
    ]

    # Headline table
    lines.append("## Headline — rows per season, per stat")
    lines.append("")
    lines.append(
        "| Stat | 2020 | 2021 | 2022 | 2023 | 2024 | total | "
        "train | test | weighted train |"
    )
    lines.append(
        "|------|------|------|------|------|------|-------|"
        "-------|------|----------------|"
    )
    for r in results:
        p = r["per_season_rows"]
        lines.append(
            f"| {r['stat']} | {p[2020]:,} | {p[2021]:,} | {p[2022]:,} | "
            f"{p[2023]:,} | {p[2024]:,} | {r['total_rows']:,} | "
            f"{r['train_rows']:,} | {r['test_rows']:,} | "
            f"{r['train_weighted_sum']:,.2f} |"
        )
    lines.append("")

    # % contribution after weighting
    lines.append("## Weighted contribution (share of total weighted_sum)")
    lines.append("")
    lines.append("| Stat | 2020 | 2021 | 2022 | 2023 | 2024 |")
    lines.append("|------|------|------|------|------|------|")
    for r in results:
        w = r["per_season_weighted"]
        total_w = r["total_weighted"] or 1.0
        pct = {s: 100.0 * w[s] / total_w for s in (2020, 2021, 2022, 2023, 2024)}
        lines.append(
            f"| {r['stat']} | {pct[2020]:.2f}% | {pct[2021]:.2f}% | "
            f"{pct[2022]:.2f}% | {pct[2023]:.2f}% | {pct[2024]:.2f}% |"
        )
    lines.append("")

    # Per-stat detail
    for r in results:
        lines.append(f"## {r['stat']} detail (target field: `{r['field']}`)")
        lines.append("")
        lines.append(
            f"- Unique players: **{r['unique_players']:,}**  \n"
            f"- Unique games:   **{r['unique_games']:,}**"
        )
        lines.append("")
        lines.append("### Weighting verification")
        pkl = r["pkl"]
        lines.append(
            f"- pkl present                       : "
            f"{'yes' if pkl['present'] else 'no'}"
        )
        lines.append(
            f"- pkl season_weights == expected    : "
            f"{pkl['season_weights_match_expected']}"
        )
        lines.append(
            f"- pkl seasons_used == expected      : "
            f"{pkl['seasons_match_expected']}"
        )
        lines.append(
            f"- pkl samples_train == audited      : "
            f"{pkl['samples_train_match']} "
            f"(pkl={pkl['samples_train']}, audit={r['train_rows']})"
        )
        lines.append(
            f"- pkl samples_test  == audited      : "
            f"{pkl['samples_test_match']} "
            f"(pkl={pkl['samples_test']}, audit={r['test_rows']})"
        )
        lines.append(
            f"- pkl weighted_sum_train ≈ audited  : "
            f"{pkl['weighted_sum_match']} "
            f"(pkl={pkl['weighted_sum_train']}, audit={r['train_weighted_sum']})"
        )
        lines.append("")
        lines.append("### Leakage checks")
        lines.append(
            f"- train∩test overlap (same pid+gid)  : "
            f"{r['train_test_overlap_count']} (must be 0)"
        )
        lines.append(
            f"- cross-season games (gid in ≥2 seasons) : "
            f"{r['cross_season_game_count']} (must be 0)"
        )
        lines.append(
            f"- missing seasons                   : "
            f"{r['missing_seasons'] or 'none'}"
        )
        lines.append(
            f"- under-represented seasons (<5%)   : "
            f"{r['underrep_seasons_lt_5pct'] or 'none'}"
        )
        lines.append("")
        lines.append(f"**Verdict:** **{r['verdict']}**")
        lines.append("")

    # Final summary
    all_pass = all(r["verdict"] == "PASS" for r in results)
    lines.append("## Global verdict")
    lines.append("")
    if all_pass:
        lines.append(
            "✅ **VK2 is correctly trained on full 5-year dataset** — "
            "every stat model passes weighting, leakage, and coverage "
            "checks against the persisted pkl metadata."
        )
    else:
        failed = [r["stat"] for r in results if r["verdict"] != "PASS"]
        lines.append(
            f"❌ **VK2 training data is incomplete or misweighted** — "
            f"failing stats: {', '.join(failed)}. See per-stat detail above."
        )
    lines.append("")
    return "\n".join(lines)


def main():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    results = []
    for label, field in STATS.items():
        print(f"[{label}] auditing...")
        r = audit_stat(label, field, db)
        results.append(r)
        print(f"  total={r['total_rows']:,} train={r['train_rows']:,} "
              f"test={r['test_rows']:,} overlap={r['train_test_overlap_count']} "
              f"verdict={r['verdict']} ({r['elapsed_s']}s)")
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write(render_md(results))
    with open(REPORT_JSON, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n→ {REPORT_MD}")
    print(f"→ {REPORT_JSON}")
    client.close()


if __name__ == "__main__":
    main()
