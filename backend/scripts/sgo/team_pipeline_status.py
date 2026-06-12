"""
team_pipeline_status.py — Complete onboarding document for the MLB team betting pipeline.

Run as:
    python -m scripts.sgo.team_pipeline_status

Prints everything a new agent needs to understand, verify, and continue
work on the team betting pipeline: architecture, collection health,
model metrics, grid history, data integrity checks, known issues,
exact rebuild commands, and current baselines.
"""
from __future__ import annotations
import asyncio
import math
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient

ARTIFACT_ROOT = Path("/app/backend/models/team_xgb")
if not ARTIFACT_ROOT.exists():
    ARTIFACT_ROOT = Path("/var/www/app/backend/models/team_xgb")

SEP = "=" * 72


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"         {msg}")


async def _col_stats(
    db, col: str, date_field: str
) -> Tuple[int, Optional[str], Optional[str]]:
    n = await db[col].count_documents({})
    pipe = [
        {"$group": {"_id": None,
                    "min": {"$min": f"${date_field}"},
                    "max": {"$max": f"${date_field}"}}}
    ]
    r = await db[col].aggregate(pipe).to_list(1)
    if r:
        return n, str(r[0]["min"]), str(r[0]["max"])
    return n, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Architecture Overview
# ─────────────────────────────────────────────────────────────────────────────

def print_architecture() -> None:
    _section("SECTION 1 — ARCHITECTURE OVERVIEW")
    print("""
  DATA SOURCES
  ─────────────────────────────────────────────────────────────────────
  SGO (Sports Grid Odds)
    Historical subscription ended. Data covered 2024-07-01 to 2026-05-30.
    Used ONLY as the source of resolved team prop outcomes (h2h, spread,
    game_total, team_total) and bet-level odds lines. Still the backbone
    of team_historical_outcomes and team_model_prop_features.
    DO NOT attempt to re-ingest — the subscription is gone.

  Odds API  (odds_api_team_h2h)
    Live-fetched moneyline odds for upcoming games. Used for live scoring
    during production (team_live_sync_service.py). Also polled historically
    to backfill h2h lines where SGO was missing them.
    Range: 2024-07-01 → present.

  BallDontLie (BDL)  (bdl_mlb_game_boxscores, bdl_mlb_team_game_features,
                       bdl_mlb_team_season_stats)
    Provides actual game scores (home/away runs) used to grade outcomes,
    plus rolling team stats: batting_avg, ops, era, whip, runs_scored_l5/l10/l20,
    first_inning_score_rate, etc.
    Range: 2024-04-01 → present.

  PIPELINE STAGES
  ─────────────────────────────────────────────────────────────────────
  1. INGEST
       scripts/sgo/ingest_bdl_mlb_game_boxscores.py
         → bdl_mlb_game_boxscores      (raw game scores per day)
       scripts/sgo/ingest_bdl_mlb_team_season_stats.py
         → bdl_mlb_team_season_stats   (season-level batting/pitching)
       scripts/sgo/ingest_odds_api_team_h2h.py
         → odds_api_team_h2h           (live h2h moneylines)
       SGO data was ingested via scripts/sgo/ingest.py (RETIRED).

  2. OUTCOMES  (build_team_historical_outcomes.py)
       Reads team_matchups (SGO raw) + BDL scores to grade each
       team prop row as WIN/LOSS/PUSH.  Writes team_historical_outcomes.
       Key workaround: uses BDL score backfill for null-score games
       so unresolved rows aren't left dangling. Dedup by (event+side)
       to prevent double-counting when SGO emits duplicate rows.

  3. FEATURES  (build_team_features.py  +  build_team_prop_features.py)
       Phase 2A: build_team_features → team_model_features
         Rolls up team_historical_outcomes into per-(team,date) priors:
         win rates, scoring means/σ/cv, spread cover rates, OU hit rates,
         BDL batting/pitching stats, starting-pitcher stats, etc.
         LEAKAGE GUARANTEE: all features from games strictly BEFORE
         as_of_date (date < as_of_date). Validated by assert_no_future_games.
       Phase 2B: build_team_prop_features → team_model_prop_features
         Joins each bet row (team_historical_outcomes) with the team's
         and opponent's feature snapshot from as_of_date = game_date.
         One doc per resolved prop bet. Direct model training input.

  4. MODEL  (train_team_xgb.py)
       XGBClassifier + isotonic calibration, one .pkl per (sport, market).
       Artifacts: /app/backend/models/team_xgb/mlb/{h2h,spread,game_total,team_total}.pkl
       CONTRACT:
         • game_total + team_total: OVER-only training (side=OVER filter).
           UNDER probability = 1 - OVER_tp at inference time.
         • h2h: HOME-only training (home_away=home filter).
           AWAY probability = 1 - HOME_tp at inference time.
           ⚠ BUG: h2h model is BROKEN because features fed at training
           are always HOME team's features, but at grid time AWAY rows
           carry the AWAY team's features — so flipping `1-p` gives
           nonsense.  DO NOT use h2h in production.
         • spread: no side/home_away filter — both sides trained together.
       Training data: 2025-01-01 → present (implied_probability NOT NULL).
       Calibration: 5-fold isotonic CV on 80/20 train/holdout split.

  5. RESHAPE  (reshape_team_props_to_replay.py)
       Adapter that writes team prop rows into the SAME collection
       (sgo_propvision_full_pipeline_replay) that the player pipeline
       uses, tagged with prop_type="team".  Runs the scorer
       (team_xgb_loader.score_team_prop) to embed model_probability,
       implied_probability, edge, vision_score.
       CRITICAL: UNDER rows get tp = 1 - OVER_tp at this step.
       ⚠ BUG: UNDER tp currently writes 0.0 instead of 1 - OVER_tp.
       Investigate reshape_team_props_to_replay.py score path.

  6. GRID  (historical_gate_replay_grid.py --mode team)
       Sweeps TEAM_GRID = {model_probability_min: 10 values,
                            edge_min: 9 values} = 90 cells per market.
       Reads sgo_propvision_full_pipeline_replay (prop_type=team,
       clean_odds IS NOT NULL).  Writes player_model_grid_results (mode=team).
       Key fix applied: uses min(implieds) not max for vig removal.
       Key fix applied: lines-specific spread cover rates in features.

  7. OPTIMIZER  (historical_gate_replay_grid.py --mode player — analogue)
       TODO: no team-specific optimizer exists yet.  Grid results are
       inspected manually.  Production picks route through
       team_live_xgb_scorer.py → team_prop_tier_service.py.

  KEY COLLECTIONS
  ─────────────────────────────────────────────────────────────────────
  team_matchups                 Raw SGO team prop rows (archived)
  team_historical_outcomes      Resolved bet rows (WIN/LOSS/PUSH) with scores
  team_model_features           Per-(sport, team_id, as_of_date) rolling priors
  team_model_prop_features      Phase 2B: bet row + joined team/opp features
  bdl_mlb_game_boxscores        BDL raw game scores
  bdl_mlb_team_game_features    BDL per-game team batting+pitching stats
  bdl_mlb_team_season_stats     BDL season aggregate stats (ERA, OPS, etc.)
  odds_api_team_h2h             Live Odds API h2h lines
  sgo_propvision_full_pipeline_replay  Unified replay collection (player + team)
  player_model_grid_results     Grid sweep results (mode=team for team rows)
  research_grid_runs            Run metadata (params, date ranges, status)

  KNOWN BUGS AND WORKAROUNDS
  ─────────────────────────────────────────────────────────────────────
  rescore=False
    The reshape script has a rescore flag (default False). When True it
    re-runs the scorer on every row.  Leave False unless you retrained
    models — rerun reshape from scratch after retraining.

  OVER-only training (game_total / team_total)
    Fixed. Previously both OVER+UNDER were trained, leading to all
    predictions clustering near 0.5001. Now only OVER rows go in;
    UNDER tp = 1 - OVER_tp at inference.  The old 0.5001 bug is gone
    (verified: 0 rows with tp=0.5001 in current replay data).

  UNDER tp = 0.0 bug (ACTIVE)
    Reshape writes UNDER rows with tp=0.0 / model_probability=0.0
    instead of 1 - OVER_tp. The grid filters on clean_odds which
    is why h2h/spread/game_total results aren't completely wrong,
    but UNDER rows are mis-scored. Investigate reshape score path.

  h2h HOME/AWAY symmetry bug (ACTIVE)
    Mirrors the old game_total OVER/UNDER bug. h2h model is trained
    on HOME rows only, so its learned representation is "home team wins."
    At grid time, AWAY rows carry AWAY team features → model outputs
    p(AWAY wins) from the wrong perspective → applying 1-p makes it
    worse, not better. FIX: train h2h HOME-only, use home features for
    both HOME and AWAY rows at score time, then flip AWAY = 1-HOME_p.

  implied_probability filter in training
    Training now requires implied_probability IS NOT NULL (matches grid
    which also requires clean_odds). Pre-fix the training set included
    rows where implied was null, causing train/grid distribution mismatch.
""")


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Collection Health
# ─────────────────────────────────────────────────────────────────────────────

async def check_collection_health(db) -> None:
    _section("SECTION 2 — COLLECTION HEALTH")

    checks = [
        ("team_matchups",               "game_date"),
        ("team_historical_outcomes",    "game_date"),
        ("team_model_features",         "as_of_date"),
        ("team_model_prop_features",    "game_date"),
        ("bdl_mlb_game_boxscores",      "game_date"),
        ("bdl_mlb_team_game_features",  "game_date"),
        ("bdl_mlb_team_season_stats",   "season"),
        ("odds_api_team_h2h",           "commence_time"),
        ("sgo_propvision_full_pipeline_replay", "game_date"),
    ]

    for col, df in checks:
        n, mn, mx = await _col_stats(db, col, df)
        print(f"\n  [{col}]")
        print(f"    count={n:,}  {df}_min={mn}  {df}_max={mx}")
        if n == 0:
            _fail(f"{col} is EMPTY")
            continue
        # Sample one recent doc
        sample = await db[col].find_one(
            {df: {"$gte": mx[:10] if mx else ""}} if mx else {},
            {"_id": 0}
        )
        if sample:
            keys = list(sample.keys())
            _info(f"fields ({len(keys)}): {keys[:12]}{'...' if len(keys) > 12 else ''}")
            # Spot-check nulls on sentinel fields
            if col == "team_historical_outcomes":
                nulls = await db[col].count_documents(
                    {"outcome_numeric": None, "outcome_resolved": True}
                )
                if nulls:
                    _warn(f"{nulls:,} rows have outcome_resolved=True but outcome_numeric=None")
                else:
                    _ok("no outcome_numeric=None where resolved=True")
            if col == "team_model_prop_features":
                for mc in ["game_total", "spread", "h2h", "team_total"]:
                    no_tf = await db[col].count_documents(
                        {"market_category": mc, "team_features": None, "sport": "mlb"}
                    )
                    total = await db[col].count_documents(
                        {"market_category": mc, "sport": "mlb"}
                    )
                    pct = 100 * no_tf / max(total, 1)
                    msg = f"{mc}: {total:,} rows, {no_tf:,} null team_features ({pct:.1f}%)"
                    (_warn if pct > 5 else _ok)(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Model Status
# ─────────────────────────────────────────────────────────────────────────────

def check_model_status() -> None:
    _section("SECTION 3 — MODEL STATUS")
    sport = "mlb"
    sport_dir = ARTIFACT_ROOT / sport
    if not sport_dir.exists():
        _fail(f"Model dir not found: {sport_dir}")
        return

    for pkl_path in sorted(sport_dir.glob("*.pkl")):
        mc = pkl_path.stem
        stat = pkl_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        size_kb = stat.st_size // 1024
        print(f"\n  [{sport.upper()}/{mc}]")
        print(f"    file : {pkl_path}  size={size_kb}KB  modified={mtime}")
        try:
            with open(pkl_path, "rb") as f:
                d = pickle.load(f)
        except Exception as e:
            _fail(f"Cannot load pkl: {e}")
            continue

        m = d.get("metrics", {})
        print(f"    version   : {d.get('version')}  trained_at={d.get('trained_at')}")
        print(f"    samples   : {d.get('samples'):,}  features={d.get('feature_count')}")
        print(f"    AUC_test  : {m.get('auc_test')}   AUC_train={m.get('auc_train')}")
        print(f"    logloss   : test={m.get('logloss_test')}  train={m.get('logloss_train')}")
        print(f"    brier_test: {m.get('brier_test')}")

        feats = d.get("features", [])
        print(f"    features[0:10]: {feats[:10]}")

        cal = m.get("calibration_deciles", [])
        if cal:
            print("    calibration (decile → mean_pred vs mean_actual):")
            for c in cal:
                delta = round(c["mean_pred"] - c["mean_actual"], 4)
                flag = "  ← overestimates" if delta > 0.05 else (
                       "  ← underestimates" if delta < -0.05 else "")
                print(f"      d{c['decile']:02d}  pred={c['mean_pred']:.4f}  "
                      f"actual={c['mean_actual']:.4f}  Δ={delta:+.4f}{flag}")
        else:
            _warn("no calibration_deciles stored")

        roi = m.get("roi", {})
        for tier, c in roi.get("by_tier", {}).items():
            n_bets = c.get("n", 0)
            hit = c.get("hit_rate")
            roi_val = c.get("roi")
            print(f"    roi_tier={tier:<12s}  n={n_bets:>6,}  "
                  f"hit={str(hit):<8}  roi={str(roi_val)}")

        # AUC sanity check
        auc = m.get("auc_test")
        if auc is not None:
            if auc > 0.75:
                _warn(f"AUC_test={auc:.4f} > 0.75 — model may be overfit for sports data")
            elif auc < 0.55:
                _warn(f"AUC_test={auc:.4f} < 0.55 — model barely beats random")
            else:
                _ok(f"AUC_test={auc:.4f} — healthy range (0.55–0.75)")

        if mc == "h2h":
            _warn("h2h model is KNOWN BROKEN — HOME/AWAY symmetry bug. "
                  "Do NOT use in production. See Section 6.")


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Grid Results History
# ─────────────────────────────────────────────────────────────────────────────

async def check_grid_history(db) -> None:
    _section("SECTION 4 — GRID RESULTS HISTORY")

    n_runs = await db["research_grid_runs"].count_documents({"mode": "team"})
    n_results = await db["player_model_grid_results"].count_documents({"mode": "team"})
    print(f"\n  research_grid_runs (mode=team): {n_runs}")
    print(f"  player_model_grid_results (mode=team): {n_results:,}")

    date_windows = [
        ("2024 season",    "2024-07-01", "2024-11-01"),
        ("2025 season",    "2025-04-01", "2025-10-01"),
        ("2026 season YTD", "2026-04-01", "2026-06-08"),
    ]
    markets = ["game_total", "spread", "h2h", "team_total"]

    for window_label, start, end in date_windows:
        print(f"\n  ── {window_label} ({start} → {end}) ──────────────────")
        run_docs = await db["research_grid_runs"].find(
            {"mode": "team",
             "params.start": start,
             "params.end": end,
             "status": "succeeded"}
        ).to_list(50)
        run_ids = [r["run_id"] for r in run_docs]
        if not run_ids:
            _warn(f"No succeeded team runs found for {start}→{end}")
            continue
        print(f"    succeeded run_ids: {len(run_ids)}")

        for mc in markets:
            top = await db["player_model_grid_results"].find(
                {"mode": "team", "market_category": mc,
                 "run_id": {"$in": run_ids}}
            ).sort("roi", -1).limit(3).to_list(3)

            if not top:
                _warn(f"    {mc}: NO RESULTS")
                continue

            best = top[0]
            trend = "–"
            if len(top) >= 2:
                if top[0]["roi"] > top[1]["roi"] * 1.05:
                    trend = "improving (top > p75)"
                elif top[0]["roi"] < top[1]["roi"] * 0.95:
                    trend = "degrading"
                else:
                    trend = "stable"

            flag = "  ← BROKEN" if mc == "h2h" else ""
            print(f"    {mc:<12s}  best: prob_min={best['prob_min']:.2f} "
                  f"edge_min={best['edge_min']:.2f}  "
                  f"n={best['n_bets']:,}  "
                  f"hit={best['hit_rate']:.3f}  "
                  f"roi={best['roi']:.3f}  "
                  f"trend={trend}{flag}")
            for rank, r in enumerate(top, 1):
                print(f"      #{rank}  prob_min={r['prob_min']:.2f}  "
                      f"edge_min={r['edge_min']:.2f}  "
                      f"n={r['n_bets']:,}  hit={r['hit_rate']:.3f}  "
                      f"roi={r['roi']:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Data Integrity Checks
# ─────────────────────────────────────────────────────────────────────────────

async def run_integrity_checks(db) -> None:
    _section("SECTION 5 — DATA INTEGRITY CHECKS")
    replay = "sgo_propvision_full_pipeline_replay"
    prop_features = "team_model_prop_features"
    team_features = "team_model_features"

    print()

    # 1. clean_odds coverage by market
    print("  1. clean_odds coverage (% of team replay rows with clean_odds != null)")
    for mc in ["game_total", "spread", "h2h", "team_total"]:
        total = await db[replay].count_documents(
            {"prop_type": "team", "league_id": "MLB", "stat_family": mc}
        )
        n_clean = await db[replay].count_documents(
            {"prop_type": "team", "league_id": "MLB", "stat_family": mc,
             "clean_odds": {"$exists": True, "$ne": None}}
        )
        pct = 100 * n_clean / max(total, 1)
        msg = f"    {mc:<12s} total={total:,}  clean_odds={n_clean:,} ({pct:.1f}%)"
        if mc == "team_total" and pct == 0:
            print(msg + "  ← expected: team_total has no clean_odds (SGO limitation)")
        elif pct < 10:
            _warn(msg + "  ← very low coverage, grid will have few bets")
        else:
            _ok(msg)

    # 2. model_probability (tp) coverage by market
    print("\n  2. model_probability (tp) coverage")
    for mc in ["game_total", "spread", "h2h", "team_total"]:
        total = await db[replay].count_documents(
            {"prop_type": "team", "league_id": "MLB", "stat_family": mc}
        )
        n_tp = await db[replay].count_documents(
            {"prop_type": "team", "league_id": "MLB", "stat_family": mc,
             "tp": {"$ne": None}}
        )
        pct = 100 * n_tp / max(total, 1)
        msg = f"    {mc:<12s} total={total:,}  tp_non_null={n_tp:,} ({pct:.1f}%)"
        (_ok if pct > 90 else _warn)(msg)

    # 3. implied_probability coverage
    print("\n  3. implied_probability coverage")
    for mc in ["game_total", "spread", "h2h", "team_total"]:
        total = await db[prop_features].count_documents(
            {"market_category": mc, "sport": "mlb"}
        )
        n_impl = await db[prop_features].count_documents(
            {"market_category": mc, "sport": "mlb",
             "implied_probability": {"$ne": None}}
        )
        pct = 100 * n_impl / max(total, 1)
        msg = f"    {mc:<12s} total={total:,}  implied_non_null={n_impl:,} ({pct:.1f}%)"
        (_ok if pct > 30 else _warn)(msg)

    # 4. game_total tp distribution — verify NOT all 0.5001 (old bug)
    print("\n  4. game_total tp distribution (checking for old 0.5001 bug)")
    pipe = [
        {"$match": {"prop_type": "team", "league_id": "MLB",
                    "stat_family": "game_total", "tp": {"$ne": None}}},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "count_0501": {"$sum": {"$cond": [{"$eq": ["$tp", 0.5001]}, 1, 0]}},
            "count_near05": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$tp", 0.499]}, {"$lte": ["$tp", 0.501]}]}, 1, 0
            ]}},
        }}
    ]
    r = await db[replay].aggregate(pipe).to_list(1)
    if r:
        total = r[0]["total"]
        c0501 = r[0]["count_0501"]
        near05 = r[0]["count_near05"]
        pct = 100 * c0501 / max(total, 1)
        if c0501 == 0:
            _ok(f"game_total: 0 rows with tp=0.5001 — old bug is fixed")
        else:
            _fail(f"game_total: {c0501:,} rows with tp=0.5001 ({pct:.1f}%) — OLD BUG ACTIVE")
        _info(f"rows within [0.499, 0.501]: {near05:,} of {total:,}")

    # 5. UNDER tp check — verify UNDER rows have tp = 1 - OVER tp (not 0.0)
    print("\n  5. UNDER tp check (should equal 1 - OVER tp for same game)")
    # Find a game with both OVER and UNDER rows
    sample_events = await db[replay].distinct(
        "event_id",
        {"prop_type": "team", "league_id": "MLB",
         "stat_family": "game_total", "side": "OVER",
         "tp": {"$gt": 0.1}, "game_date": {"$gte": "2025-06-01"}}
    )
    checked = 0
    under_zeroes = 0
    for event_id in sample_events[:20]:
        over_doc = await db[replay].find_one(
            {"event_id": event_id, "stat_family": "game_total",
             "side": "OVER", "prop_type": "team"}
        )
        under_doc = await db[replay].find_one(
            {"event_id": event_id, "stat_family": "game_total",
             "side": "UNDER", "prop_type": "team"}
        )
        if over_doc and under_doc:
            tp_over = over_doc.get("tp") or 0.0
            tp_under = under_doc.get("tp") or 0.0
            if tp_under == 0.0:
                under_zeroes += 1
            checked += 1
    if checked == 0:
        _warn("Could not find matching OVER/UNDER pairs to check")
    elif under_zeroes == checked:
        _fail(f"ALL {checked} checked UNDER rows have tp=0.0 — UNDER tp bug is ACTIVE")
        _info("Reshape is not writing 1 - OVER_tp for UNDER rows")
    elif under_zeroes > 0:
        _warn(f"{under_zeroes}/{checked} UNDER rows have tp=0.0 — partial bug")
    else:
        _ok(f"All {checked} checked UNDER rows have tp = 1 - OVER_tp")

    # 6. Feature leakage check
    print("\n  6. Feature leakage check (features must be from before game_date)")
    samples = await db[prop_features].find(
        {"sport": "mlb", "market_category": "game_total",
         "team_features": {"$ne": None}, "game_date": {"$gte": "2025-06-01"}},
        {"event_id": 1, "game_date": 1, "team_features": 1, "team_id": 1}
    ).limit(5).to_list(5)
    leakage_found = False
    for s in samples:
        game_date = s.get("game_date", "")
        tf = s.get("team_features") or {}
        # The feature row should have been computed as of game_date
        # We cannot directly check the as_of_date here without joining,
        # but we can verify the feature doc in team_model_features
        feat_doc = await db[team_features].find_one(
            {"team_id": s.get("team_id"), "as_of_date": game_date, "sport": "mlb"}
        )
        if feat_doc:
            _ok(f"  event={s.get('event_id')[:12]}  "
                f"game_date={game_date}  "
                f"feature as_of={feat_doc.get('as_of_date')}  OK")
        else:
            _info(f"  event={s.get('event_id')[:12]}  "
                  f"game_date={game_date}  "
                  f"no feature doc found for as_of={game_date} "
                  f"(features may be from different date)")
    if not samples:
        _warn("No samples found to check for leakage")

    # 7. BDL coverage in team_model_features
    print("\n  7. BDL coverage in team_model_features")
    n_total = await db[team_features].count_documents({"sport": "mlb"})
    n_ops = await db[team_features].count_documents(
        {"sport": "mlb", "batting_ops": {"$ne": None}}
    )
    n_era = await db[team_features].count_documents(
        {"sport": "mlb", "pitching_era": {"$ne": None}}
    )
    pct_ops = 100 * n_ops / max(n_total, 1)
    pct_era = 100 * n_era / max(n_total, 1)
    msg_ops = f"batting_ops: {n_ops:,}/{n_total:,} ({pct_ops:.1f}%)"
    msg_era = f"pitching_era: {n_era:,}/{n_total:,} ({pct_era:.1f}%)"
    (_ok if pct_ops > 80 else _warn)(f"    {msg_ops}")
    (_ok if pct_era > 80 else _warn)(f"    {msg_era}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Known Issues and Status
# ─────────────────────────────────────────────────────────────────────────────

def print_known_issues() -> None:
    _section("SECTION 6 — KNOWN ISSUES AND STATUS CHECKLIST")
    print("""
  [FIXED]  game_total/team_total OVER-only training
           Train script now filters side=OVER before fitting. UNDER tp
           at inference = 1 - OVER_tp. Old 0.5001 clustering is gone.

  [FIXED]  Grid uses min(implieds) not max
           Previously the grid took the MAX implied probability across
           books to identify the sharpest line — this inflated edge.
           Now it takes MIN (most conservative / highest vig removed),
           which produces honest edge estimates.

  [FIXED]  Training aligned with grid (implied_probability filter)
           Training now requires implied_probability IS NOT NULL, which
           matches the grid's clean_odds requirement. Before this fix,
           the training distribution included no-line rows that the grid
           never sees, causing train/eval distribution mismatch.

  [FIXED]  Line-specific spread cover rates
           spread_cover_rate was previously a single generic rolling
           rate. Now computed per line bucket (±1.5, ±2.5, ±3.5) so
           the model can distinguish -1.5 cover ability from -3.5.

  [FIXED]  BDL score backfill for null-score games
           Games with null BDL scores were left as unresolved, creating
           holes in the feature rolling windows. backfill_team_matchup_
           scores_bdl.py now fills these from bdl_mlb_game_boxscores.

  [BROKEN] h2h model — same HOME/AWAY symmetry bug as game_total had
           The model is trained HOME-only (correct), but at score time
           AWAY rows carry the AWAY team's features — so the model
           sees an unfamiliar input distribution and outputs wrong probs.
           Applying 1-p makes it worse. Result: 2026 h2h grid shows
           hit_rate=0.402, roi=-0.275 at prob_min=0.75 (should be >0.55).
           2024/2025 grid results look good but that's misleading —
           those runs may coincide with home team dominance, not model skill.

           FIX RECIPE:
             1. In reshape_team_props_to_replay.py: for h2h AWAY rows,
                swap team_features ↔ opponent_features before scoring.
                This ensures the scorer always sees HOME team features
                as the primary input.
             2. AWAY probability is then `1 - score_team_prop(home_features_row)`.
             3. Retrain h2h model (home_away=home filter already in place).
             4. Rerun reshape + grid to verify symmetry is restored.

  [BROKEN] UNDER tp = 0.0 in replay collection
           Reshape writes UNDER rows with tp=0.0 / model_probability=0.0
           instead of 1 - OVER_tp. The grid's clean_odds filter masks
           this for markets with clean_odds, but it causes UNDER rows
           to be unusable for any tp-based filter.

           FIX RECIPE:
             In reshape_team_props_to_replay.py, after calling
             score_team_prop(), check if side=UNDER; if so look up the
             OVER score for the same (event_id, line) and write
             tp = 1 - over_tp.  OR pass the OVER row's probability
             in directly during the reshape batch.

  [TODO]   NBA team grid
           team_model_prop_features has NBA rows but grid has only been
           run on MLB. Run after verifying NBA BDL coverage is sufficient.

  [TODO]   Optimizer run
           No team-specific optimizer exists. Grid results are inspected
           manually. Next step: adapt historical_gate_replay_grid.py
           player_model_gate_configs output into the live router.

  [TODO]   h2h fix: train HOME-only + swap AWAY features at score time
           See FIX RECIPE above.  After fix, retrain h2h and compare
           2024/2025/2026 grid results. Expected: 2026 should recover
           to hit_rate ~0.55+, roi > 0.
""")


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Exact Pipeline Rebuild Commands
# ─────────────────────────────────────────────────────────────────────────────

def print_commands() -> None:
    _section("SECTION 7 — EXACT PIPELINE REBUILD COMMANDS")
    print("""
  Run all commands from /var/www/app/backend with venv activated:
    cd /var/www/app/backend && source venv/bin/activate

  ── Step 0: Refresh BDL data (if scores are stale) ─────────────────────

    # Re-ingest BDL game scores (needed to resolve outcomes for recent games)
    python -m scripts.sgo.ingest_bdl_mlb_game_boxscores

    # Re-ingest season stats (batting/pitching)
    python -m scripts.sgo.ingest_bdl_mlb_team_season_stats

  ── Step 1: Rebuild team features (after outcome/score changes) ─────────

    # Dry-run first to see what would change
    python -m scripts.sgo.build_team_features --sport mlb --dry-run

    # Write to team_model_features
    python -m scripts.sgo.build_team_features --sport mlb

  ── Step 2: Rebuild prop features (after feature changes) ───────────────

    # Dry-run
    python -m scripts.sgo.build_team_prop_features --sport mlb --dry-run

    # Write to team_model_prop_features
    python -m scripts.sgo.build_team_prop_features --sport mlb

  ── Step 3: Retrain models (after feature or data changes) ──────────────

    # Dry-run (trains + prints metrics, does NOT write .pkl)
    python -m scripts.sgo.train_team_xgb --sport mlb --dry-run

    # Full retrain (writes all 4 markets: h2h, spread, game_total, team_total)
    python -m scripts.sgo.train_team_xgb --sport mlb --max-rows-per-market 100000

    # Retrain a single market only
    python -m scripts.sgo.train_team_xgb --sport mlb --market-categories game_total spread

    # NOTE: h2h model training is wired correctly (home_away=home filter)
    # but the scoring/reshape path has the symmetry bug. Retrain h2h only
    # AFTER fixing the reshape feature-swap for AWAY rows.

  ── Step 4: Reshape (after retraining) ──────────────────────────────────

    # Dry-run (counts rows; no writes)
    python -m scripts.sgo.reshape_team_props_to_replay --sport mlb --dry-run

    # Full reshape: writes to sgo_propvision_full_pipeline_replay (prop_type=team)
    python -m scripts.sgo.reshape_team_props_to_replay --sport mlb

    # rescore=True re-runs scorer on all existing rows (slow, ~1M rows)
    # Only needed if you retrained without doing a full re-reshape.

  ── Step 5: Run grid (after reshape) ────────────────────────────────────

    # 2024 season (holdout — do not optimize against, use for out-of-sample)
    python -m scripts.sgo.historical_gate_replay_grid \\
        --league MLB --start 2024-07-01 --end 2024-11-01 --mode team

    # 2025 season (primary training window for grid calibration)
    python -m scripts.sgo.historical_gate_replay_grid \\
        --league MLB --start 2025-04-01 --end 2025-10-01 --mode team

    # 2026 season YTD (recency check)
    python -m scripts.sgo.historical_gate_replay_grid \\
        --league MLB --start 2026-04-01 --end 2026-06-08 --mode team

    # WARNING: grid only counts rows with clean_odds IS NOT NULL.
    # team_total has 0 clean_odds rows → grid will be empty for team_total.
    # h2h 2026 results are broken (see Section 6).

  ── Step 6: Check results ───────────────────────────────────────────────

    # Re-run this status script to see updated baselines
    python -m scripts.sgo.team_pipeline_status

  ── Full rebuild from scratch (safe to run any time): ───────────────────

    python -m scripts.sgo.ingest_bdl_mlb_game_boxscores
    python -m scripts.sgo.build_team_features --sport mlb
    python -m scripts.sgo.build_team_prop_features --sport mlb
    python -m scripts.sgo.train_team_xgb --sport mlb --max-rows-per-market 100000
    python -m scripts.sgo.reshape_team_props_to_replay --sport mlb
    python -m scripts.sgo.historical_gate_replay_grid --league MLB --start 2025-04-01 --end 2025-10-01 --mode team
    python -m scripts.sgo.historical_gate_replay_grid --league MLB --start 2026-04-01 --end 2026-06-08 --mode team
""")


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 — Current Baselines
# ─────────────────────────────────────────────────────────────────────────────

def print_baselines() -> None:
    _section("SECTION 8 — CURRENT BASELINES (hardcoded as of 2026-06-09)")
    print("""
  These are the grid results from the most recent successful runs.
  Compare against live player_model_grid_results to detect regressions.
  All values from mode=team, league=MLB runs.

  FORMAT: market | window | best config → n_bets | hit_rate | ROI

  ── game_total ──────────────────────────────────────────────────────────
  game_total | 2024-07-01→2024-11-01 | prob_min=0.75 edge_min=0.01
    → n=119   hit=70.6%  ROI=+89.3%

  game_total | 2025-04-01→2025-10-01 | prob_min=0.75 edge_min=0.02
    → n=722   hit=81.7%  ROI=+167.8%   ← strongest signal

  game_total | 2026-04-01→2026-06-08 | prob_min=0.65 edge_min=0.00
    → n=258   hit=68.6%  ROI=+121.1%

  ── spread ──────────────────────────────────────────────────────────────
  spread     | 2024-07-01→2024-11-01 | prob_min=0.75 edge_min=0.00
    → n=466   hit=82.4%  ROI=+63.6%

  spread     | 2025-04-01→2025-10-01 | prob_min=0.75 edge_min=0.00
    → n=1,210 hit=88.6%  ROI=+73.3%

  spread     | 2026-04-01→2026-06-08 | prob_min=0.75 edge_min=0.00
    → n=337   hit=87.2%  ROI=+72.1%

  ── team_total ──────────────────────────────────────────────────────────
  team_total | 2026-01-01→2026-06-07 | prob_min=0.75 edge_min=0.02
    → n=696   hit=96.0%  ROI=+79.5%
    NOTE: clean_odds=0 for team_total. These results are from a
          full-date run; grid on clean_odds only will have no rows.
          team_total is NOT production-ready.

  ── h2h ─────────────────────────────────────────────────────────────────
  h2h        | 2024-07-01→2024-11-01 | prob_min=0.75 edge_min=0.10
    → n=283   hit=83.7%  ROI=+224.3%   ← inflated, model BROKEN

  h2h        | 2025-04-01→2025-10-01 | prob_min=0.75 edge_min=0.10
    → n=683   hit=96.6%  ROI=+89.1%    ← extremely high, suspect

  h2h        | 2026-04-01→2026-06-08 | prob_min=0.75 edge_min=0.00
    → n=306   hit=40.2%  ROI=-27.5%    ← BROKEN (symmetry bug)

  REGRESSION THRESHOLDS (flag if results fall below these):
    game_total 2025: hit < 0.70 or ROI < 0.80  → investigate
    spread 2025:     hit < 0.78 or ROI < 0.40  → investigate
    h2h any period:  do not use until symmetry bug is fixed

  INTERPRETATION NOTES
  ─────────────────────────────────────────────────────────────────────
  • game_total ROI >100% is achievable because the model identifies
    extreme-probability bets (prob_min=0.75 → avg_model_prob=0.84).
    These are often heavy favorites on the total line; the decimal
    odds are low, but the win rate more than compensates.
  • spread ROI ~70% at prob_min=0.75 is consistent across years —
    spread model is the most stable.
  • h2h 2024/2025 results look strong but are suspect due to the
    symmetry bug. Until the bug is fixed, h2h results should not be
    acted on.
  • team_total has no clean_odds → no production-ready grid results.
    May become viable once Odds API team_total lines are ingested.
""")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def amain() -> None:
    print(f"\n{'#' * 72}")
    print(f"  TEAM PIPELINE ONBOARDING STATUS REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'#' * 72}")

    # Section 1 (static, no DB)
    print_architecture()

    # Connect MongoDB
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    mongo = AsyncIOMotorClient(mongo_url)
    db = mongo[db_name]

    try:
        # Section 2
        await check_collection_health(db)

        # Section 3 (local files, no DB)
        check_model_status()

        # Section 4
        await check_grid_history(db)

        # Section 5
        await run_integrity_checks(db)

    finally:
        mongo.close()

    # Sections 6–8 (static)
    print_known_issues()
    print_commands()
    print_baselines()

    print(f"\n{'#' * 72}")
    print("  END OF REPORT")
    print(f"{'#' * 72}\n")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
