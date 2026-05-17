"""Dump every prop (standard + alternate, every book, every side) for every
game on 2026-05-06 into /app/backend/backtest5625, one CSV per game.

Each row contains:
  - Bet identity: event_id, home/away, commence_time, player, market,
    line, side, book, odds, is_alternate
  - Projection: projection_mu, sigma
  - Model: model_probability, fair_probability, implied_probability, edge
  - Features used by gates: hit_rate_l5/l10/l20, cv
  - Tier eligibility: passed_safe_haven, passed_front_lines, passed_war_zone,
    failed_gates_<tier>
  - Outcome: actual_value, game_log_game_id, game_log_timestamp,
    graded_as (win/loss/push/ungraded)
  - Profit per tier (1u flat stake)
"""
from __future__ import annotations
import asyncio
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.historical_alt_odds_ingest import normalize_player_name
from services.replay.mlb_replay_gate_eval import (
    _STAT_FIELD_MAP, grade_one,
)
from services.replay.mlb_replay_multi_tier_eval import (
    eval_safe_haven, eval_front_lines, eval_war_zone,
)

GAME_DATE = "2026-05-06"
SNAPSHOT  = f"{GAME_DATE}T11:00:00Z"
OUT_DIR   = Path("/app/backend/backtest5625")


def _team_short(name: str) -> str:
    return (name or "UNK").replace(" ", "").replace(".", "")[:6]


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "games").mkdir(exist_ok=True)

    # ── Step 1: collect every (player, game_log) for 2026-05-06 with full stats ──
    print(f"[1/5] Pulling all game logs for {GAME_DATE}...", flush=True)
    pipeline = [
        {"$project": {"logs": "$bdl_game_logs",
                      "display_name": 1, "player_name": 1, "mlb_full_name": 1}},
        {"$unwind": "$logs"},
        {"$project": {
            "d": {"$ifNull": [
                {"$substr": ["$logs.date", 0, 10]},
                {"$substr": ["$logs.game_date", 0, 10]}]},
            "ts": "$logs.date",
            "stats": "$logs",
            "name_canon": {"$ifNull": ["$display_name",
                            {"$ifNull": ["$player_name", "$mlb_full_name"]}]},
        }},
        {"$match": {"d": GAME_DATE}},
    ]
    # Per-player: list of (ts, game_id, stats_dict)
    logs_by_norm = defaultdict(list)
    async for r in db.mlb_master_hub_2026.aggregate(pipeline, allowDiskUse=True):
        nk = normalize_player_name(r.get("name_canon") or "")
        if not nk:
            continue
        logs_by_norm[nk].append({
            "ts": r["stats"].get("date") or r["stats"].get("game_date"),
            "game_id": r["stats"].get("game_id"),
            "stats": r["stats"],
            "team": r["stats"].get("team"),
            "opponent": r["stats"].get("opponent"),
        })
    print(f"      {sum(len(v) for v in logs_by_norm.values())} game logs across "
          f"{len(logs_by_norm)} unique players")

    # ── Step 2: pull every model output row for 2026-05-06 ──
    print(f"[2/5] Pulling all replay model outputs for {GAME_DATE} @ {SNAPSHOT}...",
          flush=True)
    model_rows = await db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT},
        {"_id": 0},
    ).to_list(None)
    print(f"      {len(model_rows)} model output rows")

    # Group by (event_id, home, away, commence_time)
    games = defaultdict(list)
    for r in model_rows:
        gk = (r["event_id"], r.get("home_team"), r.get("away_team"),
              r.get("commence_time"))
        games[gk].append(r)
    print(f"      {len(games)} unique games")

    # ── Step 3: helper to match a model row to its specific game log ──
    def match_log(player_norm: str, commence_time: str | None):
        """Pick the most likely game log for this player given the
        commence_time of the prop. If commence_time is set, prefer the log
        whose ts matches; fallback to any log."""
        logs = logs_by_norm.get(player_norm) or []
        if not logs:
            return None
        if commence_time and len(logs) > 1:
            # Try exact ts match first
            for L in logs:
                if L["ts"] and commence_time:
                    if str(L["ts"])[:16] == commence_time[:16]:
                        return L
            # Fallback: closest ts
            try:
                import datetime as dt
                target = dt.datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                def delta(L):
                    if not L["ts"]: return 10**12
                    try:
                        return abs((dt.datetime.fromisoformat(
                            str(L["ts"]).replace("Z", "+00:00")) - target).total_seconds())
                    except Exception:
                        return 10**12
                return min(logs, key=delta)
            except Exception:
                return logs[0]
        return logs[0]

    # ── Step 4: per-game CSV dump ──
    print(f"[3/5] Writing per-game CSVs to {OUT_DIR / 'games'}...", flush=True)
    columns = [
        # Identity
        "event_id", "commence_time_utc", "away_team", "home_team", "game_log_game_id",
        "player_name", "player_team", "player_opponent",
        # Market
        "market", "is_alternate", "stat_family", "line", "side", "book", "odds",
        # Model
        "projection_mu", "sigma",
        "model_probability_pct", "fair_probability_pct", "implied_probability_pct",
        "edge_pct",
        # Gate features
        "hit_rate_l5", "hit_rate_l10", "hit_rate_l20", "cv",
        # Tier eligibility
        "passed_safe_haven", "failed_gates_safe_haven",
        "passed_front_lines", "failed_gates_front_lines",
        "passed_war_zone", "failed_gates_war_zone",
        # Outcome
        "game_log_ts", "actual_value",
        "graded_safe_haven", "profit_safe_haven_u",
        "graded_front_lines", "profit_front_lines_u",
        "graded_war_zone", "profit_war_zone_u",
    ]

    game_index = []  # for the index manifest
    for (event_id, home, away, commence), rows in sorted(
            games.items(), key=lambda kv: (kv[0][3] or "", kv[0][1] or "")):
        # Determine the game log game_id by inspecting the player logs that
        # match this commence time.
        gl_gid = None
        for r in rows:
            L = match_log(r["player_name_normalized"], commence)
            if L and L.get("game_id"):
                gl_gid = L["game_id"]; break
        away_s = _team_short(away); home_s = _team_short(home)
        commence_tag = (commence or "TBD")[11:16].replace(":", "")
        fname = f"{commence_tag}UTC_{away_s}_at_{home_s}_event{event_id[:8]}_gid{gl_gid or 'NA'}.csv"
        fpath = OUT_DIR / "games" / _safe_filename(fname)
        with fpath.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            tier_summary = {"safe_haven":{"n":0,"w":0,"l":0,"p":0,"u":0,"profit":0.0,"stake":0.0},
                            "front_lines":{"n":0,"w":0,"l":0,"p":0,"u":0,"profit":0.0,"stake":0.0},
                            "war_zone":{"n":0,"w":0,"l":0,"p":0,"u":0,"profit":0.0,"stake":0.0}}
            for r in rows:
                L = match_log(r["player_name_normalized"], commence)
                actual = None; gl_ts = None
                if L:
                    field = _STAT_FIELD_MAP.get(r["stat_family"], r["stat_family"])
                    actual = L["stats"].get(field)
                    if actual is not None:
                        try: actual = float(actual)
                        except Exception: actual = None
                    gl_ts = L.get("ts")
                # Tier evaluations
                sh_pass, sh_fail = eval_safe_haven(r)
                fl_pass, fl_fail = eval_front_lines(r)
                wz_pass, wz_fail = eval_war_zone(r)
                # Grading per tier (only count for stake/profit if pass)
                def gr(passed):
                    if not passed:
                        return ("not_qualified", 0.0, 0.0)
                    g = grade_one(actual, float(r["line"]), r["side"], int(r["odds"]))
                    return (g["status"], g.get("profit_units") or 0.0,
                            g.get("stake") or 0.0)
                sh_status, sh_profit, sh_stake = gr(sh_pass)
                fl_status, fl_profit, fl_stake = gr(fl_pass)
                wz_status, wz_profit, wz_stake = gr(wz_pass)
                for tier, status, profit, stake in (
                    ("safe_haven", sh_status, sh_profit, sh_stake),
                    ("front_lines", fl_status, fl_profit, fl_stake),
                    ("war_zone", wz_status, wz_profit, wz_stake),
                ):
                    s = tier_summary[tier]
                    if status in ("win","loss","push","ungraded"):
                        s["n"] += 1
                    if status == "win": s["w"] += 1
                    elif status == "loss": s["l"] += 1
                    elif status == "push": s["p"] += 1
                    elif status == "ungraded": s["u"] += 1
                    s["profit"] += profit; s["stake"] += stake
                w.writerow({
                    "event_id": event_id,
                    "commence_time_utc": commence,
                    "away_team": away,
                    "home_team": home,
                    "game_log_game_id": gl_gid,
                    "player_name": r.get("player_name"),
                    "player_team": L.get("team") if L else None,
                    "player_opponent": L.get("opponent") if L else None,
                    "market": r["market"],
                    "is_alternate": r.get("is_alternate"),
                    "stat_family": r["stat_family"],
                    "line": r["line"], "side": r["side"], "book": r["book"],
                    "odds": r["odds"],
                    "projection_mu": r.get("projection_mu"),
                    "sigma": r.get("sigma"),
                    "model_probability_pct": (r.get("model_probability") or 0)*100,
                    "fair_probability_pct":  (r.get("fair_probability") or 0)*100,
                    "implied_probability_pct": (r.get("implied_probability") or 0)*100,
                    "edge_pct": (r.get("edge") or 0)*100,
                    "hit_rate_l5":  r.get("hit_rate_l5"),
                    "hit_rate_l10": r.get("hit_rate_l10"),
                    "hit_rate_l20": r.get("hit_rate_l20"),
                    "cv": r.get("cv"),
                    "passed_safe_haven":  sh_pass,
                    "failed_gates_safe_haven":  "|".join(sh_fail) if sh_fail else "",
                    "passed_front_lines": fl_pass,
                    "failed_gates_front_lines": "|".join(fl_fail) if fl_fail else "",
                    "passed_war_zone":    wz_pass,
                    "failed_gates_war_zone":    "|".join(wz_fail) if wz_fail else "",
                    "game_log_ts": gl_ts,
                    "actual_value": actual,
                    "graded_safe_haven":  sh_status,
                    "profit_safe_haven_u":  sh_profit,
                    "graded_front_lines": fl_status,
                    "profit_front_lines_u": fl_profit,
                    "graded_war_zone":    wz_status,
                    "profit_war_zone_u":    wz_profit,
                })
        game_index.append({
            "file": fpath.name,
            "event_id": event_id,
            "commence_time_utc": commence,
            "away_team": away, "home_team": home,
            "game_log_game_id": gl_gid,
            "total_props": len(rows),
            "alternates": sum(1 for r in rows if r.get("is_alternate")),
            "standards":  sum(1 for r in rows if not r.get("is_alternate")),
            "unique_players": len({r["player_name_normalized"] for r in rows}),
            "unique_books":   len({r["book"] for r in rows}),
            "qualified_safe_haven":  tier_summary["safe_haven"]["n"],
            "qualified_front_lines": tier_summary["front_lines"]["n"],
            "qualified_war_zone":    tier_summary["war_zone"]["n"],
            "safe_haven_W_L_P_U": (f"{tier_summary['safe_haven']['w']}/{tier_summary['safe_haven']['l']}/"
                                    f"{tier_summary['safe_haven']['p']}/{tier_summary['safe_haven']['u']}"),
            "front_lines_W_L_P_U": (f"{tier_summary['front_lines']['w']}/{tier_summary['front_lines']['l']}/"
                                     f"{tier_summary['front_lines']['p']}/{tier_summary['front_lines']['u']}"),
            "war_zone_W_L_P_U": (f"{tier_summary['war_zone']['w']}/{tier_summary['war_zone']['l']}/"
                                  f"{tier_summary['war_zone']['p']}/{tier_summary['war_zone']['u']}"),
            "safe_haven_profit_u":  round(tier_summary["safe_haven"]["profit"], 3),
            "front_lines_profit_u": round(tier_summary["front_lines"]["profit"], 3),
            "war_zone_profit_u":    round(tier_summary["war_zone"]["profit"], 3),
        })

    # ── Step 5: dump game logs CSV + manifest ──
    print(f"[4/5] Writing game_logs_{GAME_DATE}.csv...", flush=True)
    glog_cols = ["player_name_normalized","ts","game_id","team","opponent",
                  "at_bats","hits","total_bases","runs","rbis","home_runs",
                  "walks","strikeouts","plate_appearances",
                  "pitcher_strikeouts","pitcher_walks","earned_runs","pitcher_outs"]
    with (OUT_DIR / f"game_logs_{GAME_DATE}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=glog_cols, extrasaction="ignore")
        w.writeheader()
        for nk, logs in sorted(logs_by_norm.items()):
            for L in logs:
                w.writerow({
                    "player_name_normalized": nk,
                    "ts": L.get("ts"),
                    "game_id": L.get("game_id"),
                    "team": L["stats"].get("team"),
                    "opponent": L["stats"].get("opponent"),
                    **{k: L["stats"].get(k) for k in glog_cols if k not in
                       ("player_name_normalized","ts","game_id","team","opponent")},
                })

    print(f"[5/5] Writing manifest...", flush=True)
    manifest = {
        "game_date": GAME_DATE, "snapshot_iso": SNAPSHOT,
        "total_props": len(model_rows),
        "total_games": len(games),
        "unique_players_with_game_logs": len(logs_by_norm),
        "directory_layout": {
            "games/<commence>_<away>_at_<home>_event<id>_gid<id>.csv":
                "Every prop for that game, full feature snapshot, tier eligibility, outcome",
            f"game_logs_{GAME_DATE}.csv":
                "Every game-log entry on this date (one row per player×game)",
            "index.json": "This file",
        },
        "games": game_index,
    }
    with (OUT_DIR / "index.json").open("w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print()
    print("="*78)
    print("DONE")
    print("="*78)
    print(f"  Folder: {OUT_DIR}")
    print(f"  - {len(game_index)} per-game CSVs")
    print(f"  - 1 game_logs CSV ({sum(len(v) for v in logs_by_norm.values())} rows)")
    print(f"  - index.json manifest")
    print(f"  Total props dumped: {len(model_rows):,}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
