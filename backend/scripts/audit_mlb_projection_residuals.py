"""MLB HF projection residual audit for rare-event stats.

Diagnoses WHY the MLB projection model appears to over-project rare
events (home_runs, RBIs, total_bases, hits+runs+rbis). Combines:

  (a) Distribution of projections persisted in `mlb_prop_scores`
      (i.e. what the HF model is emitting right now on the live board).
  (b) Historical actual distribution from `mlb_master_hub_2026.bdl_game_logs`
      (ground truth).
  (c) Structural probe:
       - Continuous vs discrete output? (histogram of predictions by
         integer-bucket residuals)
       - Regression-to-mean? (projection-to-global-mean pull vs
         player-career-mean pull)
       - Over-weighting recent spikes? (correlation between L5 hot-
         streak and projection deviation from career mean)
       - Ignoring zero-rate frequency? (projection vs player's historical
         `zero_rate` for that stat)

Read-only. Writes `reports/mlb_projection_residual_audit.md`.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

VERSION_TAG = "final-mlb-rt"
REPORT_PATH = "/app/backend/reports/mlb_projection_residual_audit.md"

FOCUS_STATS = ["home_runs", "rbis", "total_bases", "hits+runs+rbis"]

# Stat-field map for computing from bdl_game_logs.
def _actual_from_log(log: Dict[str, Any], stat: str) -> Optional[float]:
    if stat == "hits+runs+rbis":
        vals = [log.get("hits"), log.get("runs"), log.get("rbis")]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))
    v = log.get(stat)
    return float(v) if v is not None else None


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # ------------------------------------------------------------------
    # (a) Live projection distribution for each focus stat.
    # ------------------------------------------------------------------
    proj_by_stat: Dict[str, List[float]] = {s: [] for s in FOCUS_STATS}
    proj_with_player: Dict[str, List[Dict[str, Any]]] = {s: [] for s in FOCUS_STATS}

    # Also capture the L10 / L5 signals the model uses (if persisted).
    # Not strictly required — we'll derive hot-streak from game_logs instead.

    cur = db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "model_projection": {"$ne": None}},
        {"_id": 0, "stat_type": 1, "player_name": 1, "bdl_player_id": 1,
         "model_projection": 1, "model_sigma": 1, "line": 1,
         "recommendation": 1, "tier": 1},
    )
    async for d in cur:
        stat = (d.get("stat_type") or "").lower().replace(" ", "_")
        # Map simple aliases used by scoring adapter
        if stat in ("tb",): stat = "total_bases"
        if stat in ("rbi",): stat = "rbis"
        if stat in ("hr",): stat = "home_runs"
        if stat in ("h",): stat = "hits"
        if stat in ("r",): stat = "runs"
        if stat in ("hrr", "hits+runs+rbi"): stat = "hits+runs+rbis"
        if stat not in FOCUS_STATS:
            continue
        proj = float(d["model_projection"])
        proj_by_stat[stat].append(proj)
        proj_with_player[stat].append({
            "player": d.get("player_name"),
            "bdl_id": d.get("bdl_player_id"),
            "proj": proj,
            "sigma": d.get("model_sigma"),
            "line": d.get("line"),
            "side": d.get("recommendation"),
            "tier": d.get("tier"),
        })

    # ------------------------------------------------------------------
    # (b) Historical actual distribution from bdl_game_logs.
    #     Pull a representative sample: hub rows that currently have a
    #     projection on the live board (so we're comparing apples-to-
    #     apples player pool).
    # ------------------------------------------------------------------
    live_bdl_ids = set()
    for stat in FOCUS_STATS:
        for r in proj_with_player[stat]:
            if r.get("bdl_id") is not None:
                live_bdl_ids.add(int(r["bdl_id"]))

    actual_by_stat: Dict[str, List[float]] = {s: [] for s in FOCUS_STATS}
    player_logs: Dict[int, List[Dict[str, Any]]] = {}

    # Limit to the 2025 season for a tight actual-rate comparison (the
    # props on the board are for 2025-season players).
    cur = db.mlb_master_hub_2026.find(
        {"$or": [
            {"bdl_player_id": {"$in": list(live_bdl_ids)}},
            {"bdl_id": {"$in": list(live_bdl_ids)}},
        ]},
        {"_id": 0, "bdl_player_id": 1, "bdl_id": 1,
         "display_name": 1, "player_name": 1,
         "bdl_game_logs": 1},
    )
    async for d in cur:
        pid = d.get("bdl_player_id") or d.get("bdl_id")
        if pid is None:
            continue
        logs = d.get("bdl_game_logs") or []
        if not logs:
            continue
        player_logs[int(pid)] = logs
        for log in logs:
            # Filter to batters for home_runs / rbis / total_bases /
            # hits+runs+rbis — pitchers appearing in the log (no ABs)
            # would bias the "actual" distribution toward zero for
            # batter stats.
            abs_ = log.get("at_bats")
            pa_ = log.get("plate_appearances")
            is_batter_log = (abs_ is not None and abs_ > 0) or (
                pa_ is not None and pa_ > 0
            )
            if not is_batter_log:
                continue
            for stat in FOCUS_STATS:
                v = _actual_from_log(log, stat)
                if v is not None:
                    actual_by_stat[stat].append(v)

    # ------------------------------------------------------------------
    # (c) Structural probes.
    # ------------------------------------------------------------------
    structural: Dict[str, Dict[str, Any]] = {}
    for stat in FOCUS_STATS:
        player_means = []
        zero_rates = []
        recent_hot_vs_proj = []  # correlation signal
        mean_diff_from_career_to_proj = []
        for row in proj_with_player[stat]:
            pid = row.get("bdl_id")
            if pid is None:
                continue
            logs = player_logs.get(int(pid)) or []
            # Filter to batter games only (same gate as above).
            batter_logs = [
                l for l in logs if (l.get("at_bats") and l["at_bats"] > 0)
                or (l.get("plate_appearances") and l["plate_appearances"] > 0)
            ]
            actuals = [
                _actual_from_log(l, stat) for l in batter_logs
                if _actual_from_log(l, stat) is not None
            ]
            if len(actuals) < 10:
                continue
            career_mean = float(np.mean(actuals))
            zero_rate = float(np.mean([a == 0 for a in actuals]))
            l5 = actuals[-5:]
            l5_mean = float(np.mean(l5)) if l5 else career_mean
            proj = row["proj"]
            player_means.append(career_mean)
            zero_rates.append(zero_rate)
            recent_hot_vs_proj.append((l5_mean - career_mean, proj - career_mean))
            mean_diff_from_career_to_proj.append(proj - career_mean)

        # Summary stats.
        summary: Dict[str, Any] = {
            "n_players_with_logs": len(player_means),
            "player_career_means_mean": (
                float(np.mean(player_means)) if player_means else None),
            "player_career_means_median": (
                float(np.median(player_means)) if player_means else None),
            "player_zero_rate_mean": (
                float(np.mean(zero_rates)) if zero_rates else None),
            "mean_proj_above_career": (
                float(np.mean(mean_diff_from_career_to_proj))
                if mean_diff_from_career_to_proj else None),
            "median_proj_above_career": (
                float(np.median(mean_diff_from_career_to_proj))
                if mean_diff_from_career_to_proj else None),
        }
        # Correlation between recent spike and projection deviation.
        if recent_hot_vs_proj:
            x = np.array([t[0] for t in recent_hot_vs_proj])
            y = np.array([t[1] for t in recent_hot_vs_proj])
            if x.std() > 0 and y.std() > 0:
                summary["corr_l5_hot_vs_proj_deviation"] = float(
                    np.corrcoef(x, y)[0, 1])
            else:
                summary["corr_l5_hot_vs_proj_deviation"] = None
        structural[stat] = summary

    # ------------------------------------------------------------------
    # Render report.
    # ------------------------------------------------------------------
    def _fmt(x, nd=3):
        if x is None: return "-"
        return f"{x:.{nd}f}"

    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    md = [
        "# MLB Projection Residual Audit — Rare-Event Stats",
        f"Generated: {now}  •  source: `mlb_prop_scores@{VERSION_TAG}` (live projections) "
        "× `mlb_master_hub_2026.bdl_game_logs` (actuals, batter-ABs only)",
        "",
        "Read-only diagnosis. No model changes, no caps, no ECDF tweaks.",
        "Compares the HF model's live-board projections to the actual "
        "historical per-game distribution for the same player pool.",
        "",
        "## 1. Live projection vs historical actual distribution",
        "",
    ]
    for stat in FOCUS_STATS:
        projs = np.array(proj_by_stat[stat]) if proj_by_stat[stat] else np.array([])
        acts = np.array(actual_by_stat[stat]) if actual_by_stat[stat] else np.array([])
        md.append(f"### `{stat}`")
        md.append("")
        md.append(
            f"- **Live projections**: n = **{len(projs):,}**  "
            f"mean = **{_fmt(projs.mean() if len(projs) else None)}**  "
            f"median = {_fmt(np.median(projs) if len(projs) else None)}  "
            f"p10/p50/p90 = {_fmt(np.percentile(projs,10) if len(projs) else None)} / "
            f"{_fmt(np.percentile(projs,50) if len(projs) else None)} / "
            f"{_fmt(np.percentile(projs,90) if len(projs) else None)}  "
            f"max = {_fmt(projs.max() if len(projs) else None)}")
        md.append(
            f"- **Historical actuals** (batter-AB games only): "
            f"n = {len(acts):,}  "
            f"mean = **{_fmt(acts.mean() if len(acts) else None)}**  "
            f"median = {_fmt(np.median(acts) if len(acts) else None)}  "
            f"zero-rate = **{_fmt(float(np.mean(acts == 0)) if len(acts) else None)}**  "
            f"over-1 rate = {_fmt(float(np.mean(acts > 1)) if len(acts) else None)}  "
            f"over-2 rate = {_fmt(float(np.mean(acts > 2)) if len(acts) else None)}  "
            f"max = {_fmt(acts.max() if len(acts) else None)}")
        # Key bias metric.
        if len(projs) and len(acts):
            mean_bias = projs.mean() - acts.mean()
            md.append(
                f"- **Mean projection bias**: proj − actual = "
                f"**{mean_bias:+.3f}**  "
                f"({100.0 * mean_bias / max(acts.mean(), 1e-9):+.1f}% vs "
                f"actual mean)")
        # Distribution of projections >1 / >1.5 / >2 (the "rare event"
        # inflation).
        if len(projs):
            over1 = float(np.mean(projs > 1.0))
            over15 = float(np.mean(projs > 1.5))
            over2 = float(np.mean(projs > 2.0))
            md.append(
                f"- **Projection tail**: P(proj>1) = {over1:.3f}  "
                f"P(proj>1.5) = {over15:.3f}  P(proj>2) = {over2:.3f}")
        if len(acts):
            aover1 = float(np.mean(acts > 1))
            aover15 = float(np.mean(acts > 1.5))
            aover2 = float(np.mean(acts > 2))
            md.append(
                f"- **Actual tail**:     P(actual>1) = {aover1:.3f}  "
                f"P(actual>1.5) = {aover15:.3f}  P(actual>2) = {aover2:.3f}")
        # Histogram of projections vs integer outcomes.
        if len(projs):
            bins = [0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 100.0]
            labels = ["0-0.25", "0.25-0.5", "0.5-0.75", "0.75-1",
                      "1-1.5", "1.5-2", "2-3", "3+"]
            hist, _ = np.histogram(projs, bins=bins)
            md.append("")
            md.append("Projection histogram (all live projections):")
            md.append("")
            md.append("| proj bucket | count | share |")
            md.append("|---|---:|---:|")
            tot = int(hist.sum()) or 1
            for lab, c in zip(labels, hist):
                md.append(f"| {lab} | {int(c):,} | {100.0 * c / tot:.1f}% |")
        if len(acts):
            bins = [-0.01, 0.5, 1.5, 2.5, 3.5, 100.0]
            labels = ["0", "1", "2", "3", "4+"]
            hist, _ = np.histogram(acts, bins=bins)
            md.append("")
            md.append("Actual outcome histogram (batter-AB games only):")
            md.append("")
            md.append("| actual | count | share |")
            md.append("|---|---:|---:|")
            tot = int(hist.sum()) or 1
            for lab, c in zip(labels, hist):
                md.append(f"| {lab} | {int(c):,} | {100.0 * c / tot:.1f}% |")
        md.append("")

    # (2) + (4) Structural failure-mode section.
    md.extend([
        "## 2. Structural probe per stat",
        "",
        "Each row compares the **live projection** of each player to "
        "that player's own **career mean** (from their historical game "
        "log) and historical **zero-rate** (fraction of batter games "
        "where the stat was 0). Corr_L5 measures whether projections "
        "track a player's last-5-games deviation from career mean — a "
        "proxy for recency-bias.",
        "",
        "| stat | players | career mean (player-avg) | player zero-rate (avg) | "
        "mean(proj − career) | median(proj − career) | corr(L5-deviation, proj-deviation) |",
        "|------|--------:|-------------------------:|-----------------------:|"
        "--------------------:|----------------------:|-----------------------------------:|",
    ])
    for stat in FOCUS_STATS:
        s = structural.get(stat, {})
        md.append(
            f"| `{stat}` | {s.get('n_players_with_logs', 0)} | "
            f"{_fmt(s.get('player_career_means_mean'))} | "
            f"{_fmt(s.get('player_zero_rate_mean'))} | "
            f"**{_fmt(s.get('mean_proj_above_career'))}** | "
            f"{_fmt(s.get('median_proj_above_career'))} | "
            f"{_fmt(s.get('corr_l5_hot_vs_proj_deviation'))} |"
        )
    md.append("")

    md.extend([
        "## 3. Failure-mode checklist (per stat)",
        "",
        "Symptoms read from the numbers above:",
        "",
    ])
    for stat in FOCUS_STATS:
        projs = np.array(proj_by_stat[stat]) if proj_by_stat[stat] else np.array([])
        acts = np.array(actual_by_stat[stat]) if actual_by_stat[stat] else np.array([])
        s = structural.get(stat, {})
        md.append(f"### `{stat}`")
        md.append("")
        if not len(projs) or not len(acts):
            md.append("- (insufficient data)")
            md.append("")
            continue
        flags = []
        mean_bias = projs.mean() - acts.mean()
        # Over-projection relative to actual mean.
        if mean_bias > 0.2 * max(acts.mean(), 1e-9):
            flags.append(
                f"❌ **Over-projects vs population mean**: "
                f"proj_mean = {projs.mean():.3f} vs "
                f"actual_mean = {acts.mean():.3f}  "
                f"(bias = {mean_bias:+.3f}, "
                f"{100.0 * mean_bias / max(acts.mean(), 1e-9):+.1f}%)")
        elif mean_bias < -0.2 * max(acts.mean(), 1e-9):
            flags.append(
                f"⚠️ Under-projects vs population mean: "
                f"bias = {mean_bias:+.3f}")
        else:
            flags.append(
                f"✅ Population mean aligns with actuals "
                f"(bias = {mean_bias:+.3f}).")

        # Tail overshoot — the "Brandon Marsh HR = 1.49" failure mode.
        tail_frac = float(np.mean(projs > 1.0))
        actual_tail = float(np.mean(acts > 1))
        if stat in ("home_runs",):
            # HRs > 1 per game are extremely rare historically.
            if tail_frac > 2 * actual_tail and tail_frac > 0.05:
                flags.append(
                    f"❌ **Extreme tail overshoot**: "
                    f"P(proj > 1) = {tail_frac:.3f} vs "
                    f"P(actual > 1) = {actual_tail:.3f}  "
                    f"→ model emits > 1 HR/game ~"
                    f"{tail_frac/max(actual_tail,1e-6):.1f}× more "
                    f"often than it happens")

        # Regression-to-mean vs player-career-mean
        m_diff = s.get("mean_proj_above_career")
        if m_diff is not None and m_diff > 0.15:
            flags.append(
                f"❌ **Per-player inflation**: on average each player's "
                f"projection sits +{m_diff:.3f} above their own career "
                f"mean — model regresses UP toward league rate rather "
                f"than DOWN toward personal rate")
        elif m_diff is not None and abs(m_diff) <= 0.05:
            flags.append(
                f"✅ Per-player projection sits near that player's "
                f"career mean (delta = {m_diff:+.3f}).")

        # Zero-rate concern: for HR and rare events the zero-rate is
        # the dominant mass. If projection median > 0 but actual median = 0,
        # the model is emitting a continuous signal through a discrete
        # event.
        proj_median = float(np.median(projs))
        actual_median = float(np.median(acts))
        if actual_median == 0 and proj_median > 0.3:
            flags.append(
                f"❌ **Discrete-event blindness**: median actual = 0 "
                f"(majority of batter games produce 0 for this stat), "
                f"yet median projection = {proj_median:.2f}. Model "
                f"treats the count as continuous and smears probability "
                f"mass across a space that's ~"
                f"{100.0*float(np.mean(acts==0)):.0f}% zeros")

        # Recency-bias: high positive correlation between L5 hot-streak and
        # projection deviation indicates over-weighting recent games.
        corr = s.get("corr_l5_hot_vs_proj_deviation")
        if corr is not None:
            if corr > 0.4:
                flags.append(
                    f"❌ **Recency overweighting**: corr(L5 hot-streak, "
                    f"projection deviation) = {corr:.3f} — model echoes "
                    f"short-run spikes into projections rather than "
                    f"shrinking toward career rate")
            elif corr > 0.2:
                flags.append(
                    f"⚠️ Mild recency tilt: corr = {corr:.3f}")
            else:
                flags.append(
                    f"✅ Low recency bias: corr = {corr:.3f}")
        for f in flags:
            md.append(f"- {f}")
        md.append("")

    md.extend([
        "## 4. Observations + hypothesis (read-only)",
        "",
        "Combining the bias, tail overshoot, per-player inflation, "
        "discrete-event blindness, and recency-bias signals above gives "
        "the following hypothesis for WHY the projections are wrong — "
        "none of which require touching the ECDF layer:",
        "",
        "1. **Base rate is not being respected on zero-heavy stats.** "
        "If actual `P(actual = 0) > 70%` for a stat like `home_runs` or "
        "`rbis` but the model's median projection is well above zero, "
        "the XGBoost regression head is treating the count as a "
        "continuous quantity and smearing probability mass across a "
        "range that is *dominated* by zeros in reality. A regression "
        "loss (MSE) trained on a distribution with mode=0 and a long "
        "right tail will systematically overshoot the mode.",
        "",
        "2. **Park factor + opponent-K-rate multipliers compound the "
        "signal multiplicatively.** Every projection is `raw_pred × "
        "park_factor × opp_k_rate`. For a hitter like Brandon Marsh in "
        "a hitter-friendly park (`COL` → HR×1.32) with a K-prone "
        "opponent (`ARI` → K×1.14), the multipliers stack. The raw "
        "predict may be reasonable, but the post-multiplier pushes "
        "rare-event tails past physical limits.",
        "",
        "3. **Volatility floor inflates sigma but not projection.** The "
        "model applies `std_dev = l10_avg * 0.35` when CV < 0.35 on "
        "rare events. This widens the Gaussian probability curve (and "
        "is what caused the Gaussian OVER-gate false triggers the "
        "ECDF cutover fixed). Not a projection bug per se, but worth "
        "flagging since the floor interacts with the projection.",
        "",
        "4. **No shrinkage toward player career rate.** If `mean(proj − "
        "career_rate)` above is meaningfully positive, the model is "
        "pulling projections UPWARD away from each player's personal "
        "baseline — the opposite of what Bayesian shrinkage would do. "
        "Combined with the multiplicative park/opp factors this creates "
        "the projection outliers seen on the live board.",
        "",
        "5. **Recent-hot-streak echo.** A high `corr(L5-hot, proj-"
        "deviation)` indicates the model is reading a few recent good "
        "games as signal rather than regression-to-mean noise.",
        "",
        "No projection model change applied. This report is diagnostic "
        "only, per user instruction.",
    ])

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(md))

    # Console TL;DR
    print(f"=== MLB PROJECTION AUDIT ({now}) ===")
    for stat in FOCUS_STATS:
        projs = np.array(proj_by_stat[stat]) if proj_by_stat[stat] else np.array([])
        acts = np.array(actual_by_stat[stat]) if actual_by_stat[stat] else np.array([])
        if len(projs) and len(acts):
            print(f"{stat}: proj_mean={projs.mean():.3f} "
                  f"actual_mean={acts.mean():.3f} "
                  f"bias={projs.mean() - acts.mean():+.3f} "
                  f"proj_max={projs.max():.2f} "
                  f"actual_max={acts.max():.0f} "
                  f"n_proj={len(projs)} n_act={len(acts)}")
    print(f"report → {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
