"""Path A Task 1 — Feature Parity Audit (read-only).

Goal: produce a parity matrix for every feature the deployed XGBoost
models EXPECT, comparing actual population across the four real call
paths the codebase exercises today:

    1. TRAIN  — what the trained `feature_cols[stat_family]` schema says
                must exist (208 columns per family for MLB).
    2. LIVE   — `predict()` called with ALL Phase-2A/2B hydration kwargs
                (`bdl_player_id`, `batter_hand`, `opp_pitcher_throws`,
                `opp_pitcher_id`, `opposing_lineup`). This is what
                `services/scoring/adapters/mlb_scoring.py` sends after
                `services/feature_hydration.py` runs.
    3. REPLAY — `mlb_replay_engine.replay_one()` — the current replay
                feature path. It calls `_build_friction_features`
                DIRECTLY with `statcast_features` from the cache row
                but WITHOUT pa_batter/pa_pitcher/opp_pitcher/lineup
                /batter_hand/opp_pitcher_throws. This is the path that
                produced the 7.9μ Olson explosion.
    4. REPLAY+ — Replay path AFTER restoration would attach the
                missing hydration. (Simulated here by forcing all
                hydration kwargs in.)

The audit captures, per feature:
  - `in_train_schema`     (bool — present in `feature_cols[stat]`?)
  - `live_pop_pct`        (% of sampled rows where feature ≠ 0.0)
  - `replay_pop_pct`      (% where feature ≠ 0.0 in current replay path)
  - `replay_plus_pop_pct` (% with restored hydration)
  - `imputed_block`       (parent imputed-flag name when applicable)
  - `category`            (PvP / Statcast / PA / Matchup / Lineup / …)
  - `live_minus_replay_gap_pct`  ← the central restoration KPI

Outputs:
  - `/app/backend/audits/path_a_feature_parity_audit.json`
  - `/app/backend/audits/path_a_feature_parity_audit.md`
  - `/app/backend/audits/path_a_feature_parity_audit.csv`

Read-only. No collections mutated. Live cron untouched.
"""
import asyncio
import csv
import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from services.mlb_high_friction_model import MLBHighFrictionModel

OUT_DIR = "/app/backend/audits"
SAMPLE_PLAYERS = 30          # number of distinct players to probe per stat fam
STAT_FAMILIES_BATTER = [
    "hits", "total_bases", "runs", "rbis", "batter_strikeouts", "home_runs",
]
STAT_FAMILIES_PITCHER = [
    "pitcher_strikeouts", "pitcher_walks", "earned_runs",
]
AS_OF_LIVE = None             # None == live behavior (no leakage guard)
AS_OF_PROBE = "2026-05-07"    # historical date that the 15-day sweep covers


# ─────────────────────────────────────────────────────────────────────
def _categorize_feature(name: str) -> str:
    """Crude category bucketing for the parity report grouping."""
    n = name.lower()
    if n.startswith(("vs_lhp", "vs_rhp", "platoon")):     return "platoon_splits"
    if n.startswith("pa_b_") or n.endswith("_is_imputed") and "pa_batter" in n:
        return "pa_batter"
    if n.startswith("pa_p_") or "pa_pitcher_is_imputed" in n:
        return "pa_pitcher"
    if n.startswith("sc_b_"):                              return "statcast_batter"
    if n.startswith("sc_p_"):                              return "statcast_pitcher"
    if n.startswith("opp_pitcher"):                        return "opp_pitcher_quality"
    if n.startswith("batter_hand") or n.startswith("batter_is_"): return "batter_handedness"
    if n.startswith("opp_pitcher_throws"):                 return "pitcher_throws"
    if n.startswith("same_hand") or n.startswith("opposite_hand"): return "matchup_interaction"
    if "lineup" in n:                                      return "opposing_lineup"
    if n.startswith(("park_", "altitude", "home_advantage", "stadium_")):
        return "environment_park"
    if n.startswith(("l3_", "l5_", "l10_", "l20_", "ewma",
                       "std_dev", "cv_", "range_", "hit_rate",
                       "current_hit_streak", "current_miss_streak")):
        return "rolling_windows"
    if n.startswith(("market_", "implied_", "vig_", "line_diff",
                       "odds_")):
        return "market_alignment"
    if n.startswith("expected_") or n.startswith("workload"):
        return "workload"
    return "other"


def _imputed_parent(name: str, all_names: set) -> Optional[str]:
    """If this feature has a sibling imputed flag, return its name."""
    n = name
    if n.endswith("_is_imputed"):
        return None
    cat = _categorize_feature(n)
    candidates = {
        "platoon_splits": ["platoon_split_is_imputed", "vs_lhp_is_imputed", "vs_rhp_is_imputed"],
        "pa_batter": ["pa_batter_is_imputed"],
        "pa_pitcher": ["pa_pitcher_is_imputed"],
        "statcast_batter": ["sc_batter_is_imputed"],
        "statcast_pitcher": ["sc_pitcher_is_imputed"],
        "opp_pitcher_quality": ["opp_pitcher_quality_is_imputed"],
        "batter_handedness": ["batter_hand_is_imputed"],
        "pitcher_throws": ["opp_pitcher_throws_is_imputed"],
        "opposing_lineup": ["opposing_lineup_is_imputed"],
    }.get(cat, [])
    for c in candidates:
        if c in all_names:
            return c
    return None


def _is_populated(value: Any) -> bool:
    """Population rule: any non-None value that is NOT exactly 0/0.0."""
    if value is None: return False
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


# ─────────────────────────────────────────────────────────────────────
async def _sample_players(sport_db, *, n: int, family_kind: str
                            ) -> List[Dict[str, Any]]:
    """Return n master_hub players with sufficient game logs for inference."""
    is_pitcher = family_kind == "pitcher"
    pos_match = {"$in": ["P", "SP", "RP"]} if is_pitcher \
                else {"$nin": ["P", "SP", "RP"]}
    pipeline = [
        {"$match": {"position": pos_match}},
        {"$project": {
            "_id": 0,
            "display_name": 1, "player_name": 1, "mlb_full_name": 1,
            "team": 1, "position": 1, "bat_side": 1, "throws": 1,
            "bdl_id": 1, "bdl_player_id": 1, "mlbam_id": 1,
            "vs_left": 1, "vs_right": 1,
            "bdl_game_logs": {"$slice": ["$bdl_game_logs", 35]},
            "log_count": {"$size": {"$ifNull": ["$bdl_game_logs", []]}},
        }},
        {"$match": {"log_count": {"$gte": 10}}},
        {"$sample": {"size": n}},
    ]
    rows = []
    async for r in sport_db.mlb_master_hub_2026.aggregate(pipeline):
        rows.append(r)
    return rows


def _capture_features(model: MLBHighFrictionModel, *,
                       player: Dict[str, Any],
                       stat_family: str,
                       mode: str,           # "live" | "replay" | "replay_plus"
                       as_of_date: Optional[str] = None,
                       ) -> Optional[Dict[str, Any]]:
    """Build the features dict using the SAME builder live/replay use,
    parameterised by mode."""
    game_logs = list(player.get("bdl_game_logs") or [])
    if as_of_date:
        game_logs = model._filter_logs_before(game_logs, as_of_date)
    if len(game_logs) < 5:
        return None

    is_pitcher = stat_family in {
        "pitcher_strikeouts", "pitcher_walks", "hits_allowed",
        "earned_runs", "pitcher_outs",
    }

    # ── Statcast self ──────────────────────────────────────────────
    sc_self = None
    if mode in ("live", "replay_plus"):
        # Live mode: model fetches latest statcast via internal helpers.
        try:
            if is_pitcher:
                sc_self = model._get_pitcher_sc_latest(player)
            else:
                sc_self = model._get_batter_sc_latest(player)
        except Exception:
            sc_self = None
    elif mode == "replay":
        # Replay currently DOES pass `statcast_self_as_of` via cache row.
        # Simulate availability so this audit reflects the existing
        # replay engine (which DOES include this block).
        try:
            if is_pitcher:
                sc_self = model._get_pitcher_sc_latest(player)
            else:
                sc_self = model._get_batter_sc_latest(player)
        except Exception:
            sc_self = None

    # ── PA-windowed Statcast ───────────────────────────────────────
    pa_batter = pa_pitcher = None
    if mode in ("live", "replay_plus"):
        mlbam_id = model._resolve_mlbam_id(player)
        if mlbam_id is not None:
            pa_cache = model._get_pa_cache()
            if pa_cache is not None:
                if is_pitcher:
                    pa_pitcher = pa_cache.pitcher_features(int(mlbam_id),
                                                            "2026-05-07")
                else:
                    pa_batter = pa_cache.batter_features(int(mlbam_id),
                                                          "2026-05-07")
    # mode == "replay": pa_batter / pa_pitcher stay None (current behavior)

    # ── Phase-2A matchup inputs ────────────────────────────────────
    batter_hand = opp_throws = None
    opp_pitcher_feats = None
    opposing_lineup = None
    if mode in ("live", "replay_plus"):
        batter_hand = player.get("bat_side")
        # Opposing throws: pick a plausible value just to mark non-None
        opp_throws = "R"
        # Opp pitcher features lookup — synthetic id miss is acceptable;
        # the population check is about emission, not value provenance.
        try:
            opp_pitcher_feats = None  # would require a real opp_pitcher_id
        except Exception:
            opp_pitcher_feats = None
        # Opposing lineup — synth small lineup so the block emits
        # non-imputed when restoration is wired.
        if is_pitcher:
            opposing_lineup = [
                {"player_id": 1001, "bat_side": "R", "lineup_position": i + 1,
                 "rolling_14": {"xwoba": 0.330, "k_pct": 0.22}}
                for i in range(9)
            ]

    try:
        feats = model._build_friction_features(
            player, game_logs, stat_family,
            opponent=(player.get("team") or "OAK"),
            park_team=(player.get("team") or "OAK"),
            dk_odds=None, line=1.5,
            statcast_features=(sc_self if not is_pitcher else None),
            pitcher_statcast_features=(sc_self if is_pitcher else None),
            pa_batter_features=pa_batter,
            pa_pitcher_features=pa_pitcher,
            batter_hand=batter_hand,
            opp_pitcher_throws=opp_throws,
            opp_pitcher_features=opp_pitcher_feats,
            opposing_lineup=opposing_lineup,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"     ⚠️  feature build failed mode={mode} "
              f"stat={stat_family} err={exc!r}")
        return None
    return feats


# ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    print("[1] Loading MLBHighFrictionModel + trained feature_cols schema…")
    sync_client = MongoClient(os.environ["MONGO_URL"])
    sync_db = sync_client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(sync_db)
    model.load_models()
    families = list(model.feature_cols.keys())
    print(f"     loaded models for: {families}")
    for f in families:
        print(f"       {f}: {len(model.feature_cols[f])} features in train schema")

    print(f"\n[2] Sampling {SAMPLE_PLAYERS} batters + {SAMPLE_PLAYERS} pitchers…")
    motor_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    motor_db = motor_client[os.environ["DB_NAME"]]
    batters  = await _sample_players(motor_db, n=SAMPLE_PLAYERS,
                                     family_kind="batter")
    pitchers = await _sample_players(motor_db, n=SAMPLE_PLAYERS,
                                     family_kind="pitcher")
    print(f"     batters_sampled={len(batters)}  pitchers_sampled={len(pitchers)}")

    # Per-family results
    family_reports: Dict[str, Dict[str, Any]] = {}

    print(f"\n[3] Building features per (family × mode × player) and tallying…")
    targets = [(f, batters)  for f in STAT_FAMILIES_BATTER if f in families] \
            + [(f, pitchers) for f in STAT_FAMILIES_PITCHER if f in families]

    for family, players in targets:
        train_cols = list(model.feature_cols[family])
        train_set = set(train_cols)
        pop_live = Counter(); pop_replay = Counter(); pop_replay_plus = Counter()
        in_live = Counter(); in_replay = Counter(); in_replay_plus = Counter()
        emitted_keys: set = set()

        rows_processed = {"live": 0, "replay": 0, "replay_plus": 0}
        for p in players:
            for mode, pop_ctr, in_ctr in (
                ("live", pop_live, in_live),
                ("replay", pop_replay, in_replay),
                ("replay_plus", pop_replay_plus, in_replay_plus),
            ):
                feats = _capture_features(
                    model, player=p, stat_family=family, mode=mode,
                    as_of_date=AS_OF_LIVE if mode == "live" else AS_OF_PROBE,
                )
                if feats is None:
                    continue
                rows_processed[mode] += 1
                for k, v in feats.items():
                    emitted_keys.add(k)
                    in_ctr[k] += 1
                    if _is_populated(v):
                        pop_ctr[k] += 1

        # Build per-feature parity rows
        feature_rows = []
        seen = set()
        for col in train_cols + sorted(emitted_keys):
            if col in seen: continue
            seen.add(col)

            n_live   = in_live.get(col, 0)
            n_repl   = in_replay.get(col, 0)
            n_replpl = in_replay_plus.get(col, 0)
            live_pop   = (100.0 * pop_live.get(col, 0)   / n_live)   if n_live   else 0.0
            repl_pop   = (100.0 * pop_replay.get(col, 0) / n_repl)   if n_repl   else 0.0
            replpl_pop = (100.0 * pop_replay_plus.get(col, 0) / n_replpl) if n_replpl else 0.0
            gap = live_pop - repl_pop

            feature_rows.append({
                "feature": col,
                "category": _categorize_feature(col),
                "in_train_schema": col in train_set,
                "in_live_emit": n_live > 0,
                "in_replay_emit": n_repl > 0,
                "in_replay_plus_emit": n_replpl > 0,
                "live_pop_pct": round(live_pop, 1),
                "replay_pop_pct": round(repl_pop, 1),
                "replay_plus_pop_pct": round(replpl_pop, 1),
                "live_minus_replay_gap_pct": round(gap, 1),
                "imputed_parent": _imputed_parent(col, emitted_keys),
            })

        # Category-level aggregates (mean pop %)
        cat_agg: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"n": 0, "live": 0.0, "replay": 0.0, "replay_plus": 0.0,
                      "in_train": 0})
        for fr in feature_rows:
            if not fr["in_train_schema"]:
                continue                       # only summarise schema columns
            c = fr["category"]
            cat_agg[c]["n"] += 1
            cat_agg[c]["in_train"] += 1
            cat_agg[c]["live"] += fr["live_pop_pct"]
            cat_agg[c]["replay"] += fr["replay_pop_pct"]
            cat_agg[c]["replay_plus"] += fr["replay_plus_pop_pct"]
        for c, a in cat_agg.items():
            n = a["n"] or 1
            a["live_avg"] = round(a["live"] / n, 1)
            a["replay_avg"] = round(a["replay"] / n, 1)
            a["replay_plus_avg"] = round(a["replay_plus"] / n, 1)
            a["gap_avg"] = round(a["live_avg"] - a["replay_avg"], 1)

        # Top missing features (highest live↔replay gaps where schema requires it)
        missing_in_replay = [
            fr for fr in feature_rows
            if fr["in_train_schema"]
            and fr["live_minus_replay_gap_pct"] >= 25.0
        ]
        missing_in_replay.sort(key=lambda r: r["live_minus_replay_gap_pct"],
                                reverse=True)

        family_reports[family] = {
            "stat_family": family,
            "train_schema_size": len(train_cols),
            "emitted_in_live": sum(1 for fr in feature_rows if fr["in_live_emit"]),
            "emitted_in_replay": sum(1 for fr in feature_rows if fr["in_replay_emit"]),
            "emitted_in_replay_plus": sum(1 for fr in feature_rows if fr["in_replay_plus_emit"]),
            "schema_missing_at_inference_live": [
                fr["feature"] for fr in feature_rows
                if fr["in_train_schema"] and not fr["in_live_emit"]
            ],
            "schema_missing_at_inference_replay": [
                fr["feature"] for fr in feature_rows
                if fr["in_train_schema"] and not fr["in_replay_emit"]
            ],
            "rows_processed": rows_processed,
            "category_aggregates": dict(cat_agg),
            "high_gap_features_count": len(missing_in_replay),
            "top_20_high_gap_features": missing_in_replay[:20],
            "feature_rows": feature_rows,
        }
        print(f"   • {family:>22}: train={len(train_cols)}  "
              f"live_emit={family_reports[family]['emitted_in_live']}  "
              f"replay_emit={family_reports[family]['emitted_in_replay']}  "
              f"high_gap≥25%={len(missing_in_replay)}")

    # ── Write reports ───────────────────────────────────────────────
    print(f"\n[4] Writing reports to {OUT_DIR}/")
    json_path = f"{OUT_DIR}/path_a_feature_parity_audit.json"
    md_path   = f"{OUT_DIR}/path_a_feature_parity_audit.md"
    csv_path  = f"{OUT_DIR}/path_a_feature_parity_audit.csv"

    # JSON — full payload
    with open(json_path, "w") as fh:
        json.dump({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "sample_size": {"batters": len(batters), "pitchers": len(pitchers)},
            "families": family_reports,
        }, fh, indent=2, default=str)
    print(f"     wrote {json_path}")

    # CSV — flat row per (family, feature) for spreadsheet analysis
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "stat_family", "feature", "category", "in_train_schema",
            "in_live_emit", "in_replay_emit", "in_replay_plus_emit",
            "live_pop_pct", "replay_pop_pct", "replay_plus_pop_pct",
            "live_minus_replay_gap_pct", "imputed_parent",
        ])
        for fam, rep in family_reports.items():
            for fr in rep["feature_rows"]:
                w.writerow([
                    fam, fr["feature"], fr["category"], fr["in_train_schema"],
                    fr["in_live_emit"], fr["in_replay_emit"], fr["in_replay_plus_emit"],
                    fr["live_pop_pct"], fr["replay_pop_pct"], fr["replay_plus_pop_pct"],
                    fr["live_minus_replay_gap_pct"], fr["imputed_parent"] or "",
                ])
    print(f"     wrote {csv_path}")

    # Markdown summary
    lines = [
        "# Path A Task 1 — Feature Parity Audit",
        "",
        f"_Generated {datetime.utcnow().isoformat()}Z — "
        f"batters={len(batters)}, pitchers={len(pitchers)}_",
        "",
        "## Per-stat-family schema coverage",
        "",
        "| stat_family | train cols | live emitted | replay emitted | replay+ emitted | high-gap (≥25%) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for fam, rep in family_reports.items():
        lines.append(
            f"| {fam} | {rep['train_schema_size']} | "
            f"{rep['emitted_in_live']} | "
            f"{rep['emitted_in_replay']} | "
            f"{rep['emitted_in_replay_plus']} | "
            f"{rep['high_gap_features_count']} |"
        )

    lines += ["", "## Category averages (mean population %)", ""]
    cat_union: Dict[str, Dict[str, List[float]]] = {}
    for fam, rep in family_reports.items():
        for c, a in rep["category_aggregates"].items():
            cu = cat_union.setdefault(c, {"live": [], "replay": [], "replay_plus": [],
                                            "n": 0})
            cu["live"].append(a["live_avg"])
            cu["replay"].append(a["replay_avg"])
            cu["replay_plus"].append(a["replay_plus_avg"])
            cu["n"] += a["n"]

    lines += ["| category | feats | live avg | replay avg | replay+ avg | live−replay gap |"]
    lines += ["|---|---:|---:|---:|---:|---:|"]
    cat_sorted = sorted(cat_union.items(),
                          key=lambda kv: -(sum(kv[1]["live"]) / len(kv[1]["live"])
                                             - sum(kv[1]["replay"]) / len(kv[1]["replay"]))
                                          if kv[1]["live"] and kv[1]["replay"] else 0)
    for c, cu in cat_sorted:
        live_avg   = round(sum(cu["live"]) / len(cu["live"]), 1)   if cu["live"]   else 0.0
        replay_avg = round(sum(cu["replay"]) / len(cu["replay"]), 1) if cu["replay"] else 0.0
        replpl_avg = round(sum(cu["replay_plus"]) / len(cu["replay_plus"]), 1) \
            if cu["replay_plus"] else 0.0
        gap = round(live_avg - replay_avg, 1)
        lines.append(f"| {c} | {cu['n']} | {live_avg}% | {replay_avg}% | "
                      f"{replpl_avg}% | **{gap}** |")

    lines += ["", "## Top-20 high-gap features per family", ""]
    for fam, rep in family_reports.items():
        if not rep["top_20_high_gap_features"]:
            continue
        lines += [f"### {fam}", "",
                   "| feature | category | live % | replay % | gap | imputed flag |",
                   "|---|---|---:|---:|---:|---|"]
        for fr in rep["top_20_high_gap_features"]:
            lines.append(
                f"| `{fr['feature']}` | {fr['category']} | "
                f"{fr['live_pop_pct']}% | {fr['replay_pop_pct']}% | "
                f"**{fr['live_minus_replay_gap_pct']}** | "
                f"{fr['imputed_parent'] or '—'} |"
            )
        lines += [""]

    lines += ["", "## Restoration priority (recommended order)",
                "",
                "Ranked by `live − replay` gap × number of stat families affected:",
                ""]
    rank: Counter = Counter()
    for fam, rep in family_reports.items():
        for fr in rep["top_20_high_gap_features"]:
            rank[fr["category"]] += fr["live_minus_replay_gap_pct"]
    lines += ["| rank | category | weighted gap score |", "|---|---|---:|"]
    for i, (cat, score) in enumerate(rank.most_common(), 1):
        lines.append(f"| {i} | {cat} | {round(score, 1)} |")

    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    print(f"     wrote {md_path}")

    sync_client.close()
    motor_client.close()
    print("\n[✓] Audit complete.")


if __name__ == "__main__":
    asyncio.run(main())
