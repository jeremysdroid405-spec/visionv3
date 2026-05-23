"""
historical_full_pipeline_replay.py — SSOT replay of historical SGO props.

REWRITTEN 2026-05-21 (P0 SSOT refactor).

This script NO LONGER contains any inlined gate / scoring logic. Every
historical prop is evaluated by the EXACT same code path as live props:

    services.replay.production_replay_runner.run_production_replay(...)

which in turn drives:

    Layer 3 — services.replay.mlb_replay_engine.replay_date
              (live MLB-HF model · live feature cache · live odds rebuild)
    Layer 4 — services.scoring.tier_evaluator.evaluate_tier_with_overrides
              (live SH / FL / WZ gate thresholds, NormalizedMetrics path)

Pipeline:

  ┌────────────────────────────────────────────────────────────────────┐
  │  Pre-flight                                                         │
  │   • require sgo_replay_alt_odds_raw rows in window                  │
  │   • require sgo_pp_research_outcomes rows in window                 │
  │   • optional sample-diff snapshot of existing replay rows           │
  └────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │  Per date × per tier  (default tiers: SH, FL, WZ — gate_path=universal)│
  │                                                                      │
  │    run_production_replay(db, sport="mlb",                            │
  │        game_date=gd, snapshot_iso=…,                                 │
  │        tier=t,                                                       │
  │        gate_path="universal",                                        │
  │        output_namespace="propvision_full_pipeline",                  │
  │        canonical_path=…,                                             │
  │    )  → writes mlb_propvision_full_pipeline_runs/outputs/cards       │
  │                                                                      │
  │  HARD FAIL if any run errors  (no silent fallback to inlined gates)  │
  └────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │  Post-run mirror to legacy sgo_propvision_full_pipeline_replay      │
  │   • collapse 3 tier-passes per (event,player,market,line,side)      │
  │   • attach outcome from sgo_pp_research_outcomes                    │
  │   • upsert in legacy schema  (UI Results panel + grid_sweep read it)│
  └────────────────────────────────────────────────────────────────────┘

Optional `--sample-diff N` mode emits a side-by-side comparison between
the legacy pre-existing replay rows and the new SSOT rows, written to
`sgo_propvision_full_pipeline_replay_diff` for the operator to audit.
The legacy gate code is NEVER re-executed — the diff compares against
whatever was previously written to the collection.

CLI:
  --league MLB
  --start  YYYY-MM-DD
  --end    YYYY-MM-DD
  --tiers  safe_haven,front_lines,war_zone     (default: all three)
  --gate-path universal | legacy_wz            (default: universal)
  --canonical-path                              (default off)
  --snapshot-hour 11                            (default 11 UTC)
  --no-mirror-to-legacy                         (skip post-run projection)
  --sample-diff N                               (emit diff for N random rows)
  --continue-on-error                           (do NOT hard-fail on a bad date)
  --dry-run                                     (no writes anywhere)
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import random
import sys
import traceback
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

# ── SSOT collection wiring ───────────────────────────────────────────────
SGO_ODDS_COLL       = "sgo_replay_alt_odds_raw"      # production-format reshape
SGO_OUTCOMES_COLL   = "sgo_pp_research_outcomes"     # grading source
LEGACY_OUT_COLL     = "sgo_propvision_full_pipeline_replay"   # UI reads
DIFF_COLL           = "sgo_propvision_full_pipeline_replay_diff"
OUTPUT_NAMESPACE    = "propvision_full_pipeline"
# Resolves at runtime to mlb_propvision_full_pipeline_{runs,outputs,cards}
RUNNER_OUTPUTS      = "mlb_propvision_full_pipeline_outputs"
RUNNER_RUNS         = "mlb_propvision_full_pipeline_runs"

PIPELINE_VERSION    = "ppv_ssot_2026_05_21"

VALID_TIERS = ("safe_haven", "front_lines", "war_zone")
VALID_GATE_PATHS = ("universal", "legacy_wz")


def _date_iter(start: str, end: str):
    s = date.fromisoformat(start); e = date.fromisoformat(end)
    cur = s
    while cur <= e:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _odds_bucket(odds: Optional[int]) -> str:
    if odds is None: return "odds_na"
    o = int(odds)
    if o < -200: return "odds_lt_-200"
    if o < -100: return "odds_-200_-100"
    if o <    0: return "odds_-100_-0"
    if o <  150: return "odds_+0_+150"
    if o <  300: return "odds_+150_+300"
    return "odds_+300p"


# ── Pre-flight ────────────────────────────────────────────────────────────
async def _preflight(db, *, league: str, start: str, end: str) -> None:
    """HARD-FAIL pre-flight. Raises RuntimeError if SSOT inputs missing.

    SCHEMA NOTE: `sgo_replay_alt_odds_raw` mirrors `mlb_historical_alt_odds_raw`
    which the production replay adapter consumes — that schema uses
    `sport: "mlb"` (lowercase, no `league`/`league_id` field). We query on
    `sport` here so this preflight stays in lockstep with what
    `services.pipeline.providers.historical_input.load_props()` actually
    reads downstream. Querying on `league: "MLB"` was a 2026-05-21 bug that
    falsely hard-failed even when reshape had written rows.
    """
    sport_canonical = "mlb" if league.upper() == "MLB" else league.lower()
    n_odds = await db[SGO_ODDS_COLL].count_documents({
        "sport": sport_canonical,
        "game_date": {"$gte": start, "$lte": end},
    })
    if n_odds == 0:
        # Try the legacy "league" field as a fallback hint so the error
        # message is maximally diagnostic for any older rows.
        n_legacy = await db[SGO_ODDS_COLL].count_documents({
            "league": league.upper(),
            "game_date": {"$gte": start, "$lte": end},
        })
        raise RuntimeError(
            f"[preflight] {SGO_ODDS_COLL} has 0 rows in "
            f"{league} {start}..{end} "
            f"(queried sport='{sport_canonical}'; legacy league='{league.upper()}' "
            f"would match {n_legacy} rows). Run "
            f"scripts.sgo.reshape_sgo_to_replay_odds first. "
            f"NO FALLBACK to inlined gates — SSOT-only.")
    n_outcomes = await db[SGO_OUTCOMES_COLL].count_documents({
        "league_id": league.upper(),
        "game_date": {"$gte": start, "$lte": end},
        "outcome_resolved": True,
    })
    if n_outcomes == 0:
        raise RuntimeError(
            f"[preflight] {SGO_OUTCOMES_COLL} has 0 resolved rows in "
            f"{league} {start}..{end}. Run "
            f"scripts.sgo.build_historical_outcomes first.")
    print(f"  ✓ preflight: {SGO_ODDS_COLL}={n_odds:,} rows (sport='{sport_canonical}') · "
          f"{SGO_OUTCOMES_COLL}={n_outcomes:,} resolved")


# ── Sample-diff snapshot (BEFORE the run) ─────────────────────────────────
async def _snapshot_sample_for_diff(db, *, league: str, start: str, end: str,
                                       n: int) -> List[Dict[str, Any]]:
    """Snapshot N random legacy-schema rows BEFORE the new run overwrites
    them. Compared against the post-run rows later. Returns the snapshots."""
    pipeline = [
        {"$match": {
            "league_id": league, "game_date": {"$gte": start, "$lte": end},
        }},
        {"$sample": {"size": int(n)}},
        {"$project": {"_id": 0,
                        "event_id": 1, "player_id": 1, "stat_id": 1,
                        "side": 1, "line": 1, "period_id": 1, "game_date": 1,
                        "stat_family": 1, "player_name": 1,
                        "selected_tier": 1,
                        "safe_haven_pass": 1, "front_lines_pass": 1,
                        "war_zone_pass": 1,
                        "safe_haven_failed_reasons": 1,
                        "front_lines_failed_reasons": 1,
                        "war_zone_failed_reasons": 1,
                        "model_probability": 1, "tp": 1, "cv": 1,
                        "edge": 1, "hit_rate_l20": 1,
                        "pipeline_version": 1, "scored_at": 1,
                        }},
    ]
    snaps: List[Dict[str, Any]] = []
    async for r in db[LEGACY_OUT_COLL].aggregate(pipeline):
        snaps.append(r)
    return snaps


# ── Mirror runner outputs → legacy collection ────────────────────────────
def _norm_line(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _norm_side(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip().upper()
    return s or None


def _norm_player(x) -> Optional[str]:
    if x is None:
        return None
    return str(x).strip().lower() or None


async def _build_outcome_index(db, *, event_ids: List[str]
                                  ) -> Dict[Tuple[str, str, float, str], List[Dict[str, Any]]]:
    """Per-event tolerant outcome index.

    Key = (event_id, stat_family, line_as_float, side_uppercase).
    Outcomes from `sgo_pp_research_outcomes` were historically written
    with several non-canonical key types (line as string, side
    lowercase, market/player_name_normalized missing), so we coerce on
    both the index side and the lookup side.

    Value is a *list* because the same (event, stat_family, line, side)
    can apply to multiple players on the same game (e.g. two batters
    with the hits 0.5 line). The mirror disambiguates by player using
    `player_name` substring match downstream.
    """
    index: Dict[Tuple[str, str, float, str], List[Dict[str, Any]]] = {}
    if not event_ids:
        return index
    async for o in db[SGO_OUTCOMES_COLL].find(
        {"event_id": {"$in": event_ids}, "outcome_resolved": True},
        projection={"_id": 0, "outcome_numeric": 1, "hit": 1,
                      "actual": 1, "player_id": 1, "stat_id": 1,
                      "period_id": 1, "league_id": 1,
                      "event_id": 1, "stat_family": 1, "line": 1,
                      "side": 1, "player_name": 1,
                      "player_name_normalized": 1, "market": 1},
    ):
        ln = _norm_line(o.get("line"))
        sd = _norm_side(o.get("side"))
        fam = o.get("stat_family") or ""
        eid = o.get("event_id") or ""
        if ln is None or sd is None or not eid:
            continue
        key = (eid, fam, ln, sd)
        index.setdefault(key, []).append(o)
    return index


def _pick_outcome(candidates: List[Dict[str, Any]], *,
                       wanted_player_norm: Optional[str],
                       wanted_player_raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pick the best outcome from a list of candidates that already
    share (event_id, stat_family, line, side). Disambiguates by
    player using normalized + raw name matches. Returns the first
    candidate as a last resort (1-prop-per-cell case)."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    np = _norm_player(wanted_player_norm) or _norm_player(wanted_player_raw)
    if np:
        # Exact match on normalized name
        for c in candidates:
            cn = _norm_player(c.get("player_name_normalized")) or _norm_player(c.get("player_name"))
            if cn and cn == np:
                return c
        # Substring fallback
        for c in candidates:
            cn = _norm_player(c.get("player_name_normalized")) or _norm_player(c.get("player_name"))
            if cn and (np in cn or cn in np):
                return c
    return candidates[0]


async def _mirror_to_legacy(db, *, replay_serials: List[str],
                                  league: str) -> Tuple[int, int]:
    """Mirror SSOT runner outputs into the legacy collection that the UI
    and grid_sweep read. Joins on (event_id, stat_family, line, side)
    via a *normalized* per-event lookup table so float-vs-string,
    upper-vs-lower, and missing-field drift between RUNNER_OUTPUTS and
    sgo_pp_research_outcomes no longer break the attach (single
    biggest source of HR=—/ROI=0 in the optimizer).

    Returns (rows_mirrored, rows_with_outcome).
    """
    if not replay_serials:
        return (0, 0)
    pipe = [
        {"$match": {"replay_serial": {"$in": replay_serials}}},
        {"$group": {
            "_id": {
                "event_id": "$event_id",
                "player_name_normalized": "$player_name_normalized",
                "market": "$market", "line": "$line", "side": "$side",
            },
            "game_date": {"$first": "$game_date"},
            "stat_family": {"$first": "$stat_family"},
            "player_name": {"$first": "$player_name"},
            "book": {"$first": "$book"}, "odds": {"$first": "$odds"},
            "projection_mu": {"$first": "$projection_mu"},
            "sigma": {"$first": "$sigma"},
            "model_probability": {"$first": "$model_probability"},
            "fair_probability": {"$first": "$fair_probability"},
            "implied_probability": {"$first": "$implied_probability"},
            "edge": {"$first": "$edge"}, "cv": {"$first": "$cv"},
            "hit_rate_l5": {"$first": "$hit_rate_l5"},
            "hit_rate_l10": {"$first": "$hit_rate_l10"},
            "hit_rate_l20": {"$first": "$hit_rate_l20"},
            "tier_evals": {"$push": {
                "tier": "$tier",
                "gate_pass": "$gate_pass",
                "failed_gates": "$failed_gates",
                "gate_config_version": "$gate_config_version",
            }},
        }},
    ]
    # Materialize the aggregation so we can do a two-pass:
    #   1) collect distinct event_ids → build the outcome index ONCE
    #   2) per group → lookup using the normalized key set
    groups: List[Dict[str, Any]] = []
    event_ids_seen: set = set()
    async for g in db[RUNNER_OUTPUTS].aggregate(pipe, allowDiskUse=True):
        groups.append(g)
        eid = (g.get("_id") or {}).get("event_id")
        if eid:
            event_ids_seen.add(eid)
    outcome_index = await _build_outcome_index(db, event_ids=list(event_ids_seen))

    rows_mirrored = 0
    rows_with_outcome = 0
    buf: List[UpdateOne] = []
    for g in groups:
        k = g["_id"]
        # Decompose tier evals
        evals = {e["tier"]: e for e in g.get("tier_evals", [])}
        sh = evals.get("safe_haven", {})
        fl = evals.get("front_lines", {})
        wz = evals.get("war_zone", {})
        sh_pass = bool(sh.get("gate_pass"))
        fl_pass = bool(fl.get("gate_pass"))
        wz_pass = bool(wz.get("gate_pass"))
        selected_tier = ("safe_haven" if sh_pass
                          else "front_lines" if fl_pass
                          else "war_zone" if wz_pass else None)

        # ── Tolerant outcome lookup ─────────────────────────────
        # Key normalization mirrors what `_build_outcome_index` does.
        key = (
            k.get("event_id") or "",
            g.get("stat_family") or "",
            _norm_line(k.get("line")),
            _norm_side(k.get("side")),
        )
        outcome = None
        if key[2] is not None and key[3]:
            candidates = outcome_index.get(key) or []
            outcome = _pick_outcome(
                candidates,
                wanted_player_norm=k.get("player_name_normalized"),
                wanted_player_raw=g.get("player_name"),
            )
        if outcome:
            rows_with_outcome += 1

        replay_row = {
            "event_id": k["event_id"],
            "player_id": (outcome or {}).get("player_id"),
            "stat_id":   (outcome or {}).get("stat_id"),
            "side":      k["side"],
            "line":      k["line"],
            "period_id": (outcome or {}).get("period_id"),
            "game_date": g.get("game_date"),
            "league_id": (outcome or {}).get("league_id") or league,
            "sport":     "mlb",
            "stat_family": g.get("stat_family"),
            "player_name": g.get("player_name"),
            "player_name_normalized": k["player_name_normalized"],
            "market":    k["market"],
            "book":      g.get("book"),
            "odds":      g.get("odds"),
            "odds_bucket": _odds_bucket(g.get("odds")),

            "projection_mu":       g.get("projection_mu"),
            "sigma":               g.get("sigma"),
            "cv":                  g.get("cv"),
            "model_probability":   g.get("model_probability"),
            "tp":                  g.get("model_probability"),
            "fair_probability":    g.get("fair_probability"),
            "implied_probability": g.get("implied_probability"),
            "edge":                g.get("edge"),

            "hit_rate_l5":  g.get("hit_rate_l5"),
            "hit_rate_l10": g.get("hit_rate_l10"),
            "hit_rate_l20": g.get("hit_rate_l20"),

            "safe_haven_pass":            sh_pass,
            "safe_haven_failed_reasons":  list(sh.get("failed_gates") or []),
            "safe_haven_gate_cfg_version": sh.get("gate_config_version"),
            "front_lines_pass":           fl_pass,
            "front_lines_failed_reasons": list(fl.get("failed_gates") or []),
            "front_lines_gate_cfg_version": fl.get("gate_config_version"),
            "war_zone_pass":              wz_pass,
            "war_zone_failed_reasons":    list(wz.get("failed_gates") or []),
            "war_zone_gate_cfg_version":  wz.get("gate_config_version"),
            "selected_tier":              selected_tier,

            "outcome_resolved": bool(outcome),
            "outcome_numeric":  (outcome or {}).get("outcome_numeric"),
            "hit":              (outcome or {}).get("hit"),
            "actual":           (outcome or {}).get("actual"),

            "pipeline_version": PIPELINE_VERSION,
            "ssot_source":      "production_replay_runner",
            "runner_serials":   sorted(replay_serials),
            "scored_at":        datetime.now(timezone.utc),
            "as_of_date":       g.get("game_date"),
        }
        flt = {
            "event_id": replay_row["event_id"],
            "player_name_normalized": replay_row["player_name_normalized"],
            "market": replay_row["market"],
            "line": replay_row["line"],
            "side": replay_row["side"],
            "pipeline_version": PIPELINE_VERSION,
        }
        buf.append(UpdateOne(flt, {"$set": replay_row}, upsert=True))
        rows_mirrored += 1
        if len(buf) >= 500:
            await db[LEGACY_OUT_COLL].bulk_write(buf, ordered=False)
            buf = []
    if buf:
        await db[LEGACY_OUT_COLL].bulk_write(buf, ordered=False)
    print(f"  [mirror] groups={len(groups)} events={len(event_ids_seen)} "
            f"outcome_index_keys={len(outcome_index)} "
            f"rows_mirrored={rows_mirrored} rows_with_outcome={rows_with_outcome}")
    return (rows_mirrored, rows_with_outcome)


# ── Sample-diff emit (AFTER the run) ─────────────────────────────────────
async def _emit_sample_diff(db, *, snapshots: List[Dict[str, Any]],
                                run_id: str) -> int:
    if not snapshots:
        return 0
    n_diffs = 0
    buf: List[UpdateOne] = []
    for snap in snapshots:
        # Locate the NEW SSOT-mirrored row for the same prop key
        new_row = await db[LEGACY_OUT_COLL].find_one(
            {
                "event_id": snap.get("event_id"),
                "side":     snap.get("side"),
                "line":     snap.get("line"),
                "game_date": snap.get("game_date"),
                "pipeline_version": PIPELINE_VERSION,
            },
            projection={"_id": 0,
                          "selected_tier": 1, "safe_haven_pass": 1,
                          "front_lines_pass": 1, "war_zone_pass": 1,
                          "safe_haven_failed_reasons": 1,
                          "front_lines_failed_reasons": 1,
                          "war_zone_failed_reasons": 1,
                          "model_probability": 1, "tp": 1, "cv": 1,
                          "edge": 1, "hit_rate_l20": 1, "ssot_source": 1},
        )
        diff_doc = {
            "diff_run_id": run_id,
            "diff_emitted_at": datetime.now(timezone.utc),
            "key": {k: snap.get(k) for k in
                       ("event_id", "side", "line", "game_date",
                        "player_name", "stat_family")},
            "legacy_inlined_gates": {
                "selected_tier": snap.get("selected_tier"),
                "safe_haven_pass": snap.get("safe_haven_pass"),
                "front_lines_pass": snap.get("front_lines_pass"),
                "war_zone_pass": snap.get("war_zone_pass"),
                "safe_haven_failed_reasons": snap.get("safe_haven_failed_reasons"),
                "front_lines_failed_reasons": snap.get("front_lines_failed_reasons"),
                "war_zone_failed_reasons": snap.get("war_zone_failed_reasons"),
                "model_probability": snap.get("model_probability"),
                "cv": snap.get("cv"), "edge": snap.get("edge"),
                "hit_rate_l20": snap.get("hit_rate_l20"),
                "pipeline_version": snap.get("pipeline_version"),
            },
            "ssot_production_runner": new_row or {"_missing": True},
            "tier_delta": (snap.get("selected_tier")
                            != (new_row or {}).get("selected_tier")),
        }
        if diff_doc["tier_delta"] or not new_row:
            n_diffs += 1
        buf.append(UpdateOne(
            {"diff_run_id": run_id, "key": diff_doc["key"]},
            {"$set": diff_doc}, upsert=True,
        ))
    if buf:
        await db[DIFF_COLL].bulk_write(buf, ordered=False)
    return n_diffs


# ── Main ─────────────────────────────────────────────────────────────────
async def _run(args: argparse.Namespace) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    league = args.league.upper()
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    for t in tiers:
        if t not in VALID_TIERS:
            raise SystemExit(f"--tiers element {t!r} not in {VALID_TIERS}")
    if args.gate_path not in VALID_GATE_PATHS:
        raise SystemExit(f"--gate-path must be one of {VALID_GATE_PATHS}")

    print("=" * 78)
    print("  HISTORICAL FULL-PIPELINE REPLAY — SSOT MODE (no inlined gates)")
    print(f"  window      : {args.start}..{args.end}")
    print(f"  league      : {league}")
    print(f"  tiers       : {tiers}")
    print(f"  gate_path   : {args.gate_path}")
    print(f"  canonical   : {args.canonical_path}")
    print(f"  runner ns   : mlb_{OUTPUT_NAMESPACE}_runs/outputs")
    print(f"  legacy mirror: {LEGACY_OUT_COLL}"
          + ("  [DISABLED]" if args.no_mirror_to_legacy else ""))
    print(f"  sample-diff : {args.sample_diff or '(off)'}")
    print(f"  on error    : "
          + ("CONTINUE" if args.continue_on_error else "HARD-FAIL"))
    print("=" * 78)

    # ── Pre-flight ────────────────────────────────────────────────────
    await _preflight(db, league=league, start=args.start, end=args.end)

    # ── Sample-diff snapshot ──────────────────────────────────────────
    diff_run_id = f"ppv_diff_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    sample_snaps: List[Dict[str, Any]] = []
    if args.sample_diff and args.sample_diff > 0:
        sample_snaps = await _snapshot_sample_for_diff(
            db, league=league, start=args.start, end=args.end,
            n=args.sample_diff)
        print(f"  ✓ sample-diff: snapshotted {len(sample_snaps)} pre-existing rows "
              f"(run id {diff_run_id})")

    # ── Lazy import the runner + adapter ──────────────────────────────
    from services.replay.production_replay_runner import run_production_replay

    # NOTE: previously this script monkey-patched
    # `MLBReplayAdapter.__init__` to set `self.config.odds_collection`,
    # but `config` is a @property that constructs a fresh
    # SportFixedConfig on every access — so the attribute assignment
    # was dropped. The dead patch ALSO broke `_resolve_adapter(cls(db))`
    # because the patched closure didn't accept `db` (2026-05-22
    # TypeError). The kwarg path (`odds_collection=SGO_ODDS_COLL` to
    # run_production_replay below) is what actually points Layer-3 and
    # the audit-pin code at the SGO collection — no patch required.

    dates = list(_date_iter(args.start, args.end))
    if args.limit_dates:
        dates = dates[: int(args.limit_dates)]
    print(f"  scheduling {len(dates)} dates × {len(tiers)} tiers = "
          f"{len(dates)*len(tiers)} runner calls")

    all_serials: List[str] = []
    grand = {"runs_ok": 0, "runs_failed": 0,
                "rows_scanned": 0, "rows_qualified": 0,
                "wins": 0, "losses": 0, "pushes": 0}
    failed: List[Tuple[str, str, str]] = []  # (date, tier, error)

    for gd in dates:
        snapshot_iso = f"{gd}T{args.snapshot_hour:02d}:00:00Z"
        for tier in tiers:
            try:
                summary = await run_production_replay(
                    db, sport="mlb",
                    game_date=gd, snapshot_iso=snapshot_iso, tier=tier,
                    gate_path=args.gate_path,
                    canonical_path=bool(args.canonical_path),
                    output_namespace=OUTPUT_NAMESPACE,
                    dry_run=bool(args.dry_run),
                    notes=f"historical_full_pipeline_replay SSOT "
                            f"{args.start}..{args.end}",
                    odds_collection=SGO_ODDS_COLL,
                    research_mode=bool(args.research_mode),
                )
            except Exception as e:
                grand["runs_failed"] += 1
                failed.append((gd, tier, repr(e)))
                msg = (f"[ssot] runner FAILED for {gd}/{tier}: {e!r}\n"
                          f"{traceback.format_exc(limit=4)}")
                print(msg)
                if not args.continue_on_error:
                    print()
                    print("⛔ HARD-FAIL: production runner could not process "
                            f"{gd}/{tier}. No silent fallback. "
                            "Re-run with --continue-on-error to skip bad cells.")
                    return 2
                continue
            grand["runs_ok"]        += 1
            grand["rows_scanned"]   += summary.get("rows_scanned", 0)
            grand["rows_qualified"] += summary.get("rows_qualified", 0)
            grand["wins"]           += summary.get("wins", 0)
            grand["losses"]         += summary.get("losses", 0)
            grand["pushes"]         += summary.get("pushes", 0)
            if summary.get("serial"):
                all_serials.append(summary["serial"])
            print(f"  [{gd}/{tier}]  serial={summary.get('serial')}  "
                    f"scanned={summary.get('rows_scanned')}  "
                    f"qual={summary.get('rows_qualified')}  "
                    f"W/L/P={summary.get('wins')}/{summary.get('losses')}/{summary.get('pushes')}")

    print()
    print("=" * 78)
    print(f"  runner-calls OK / FAILED : {grand['runs_ok']} / {grand['runs_failed']}")
    print(f"  rows_scanned    {grand['rows_scanned']:>10,}")
    print(f"  rows_qualified  {grand['rows_qualified']:>10,}")
    print(f"  wins/losses/p   {grand['wins']:>6,} / {grand['losses']:>6,} "
          f"/ {grand['pushes']:>6,}")
    if grand["runs_failed"]:
        print(f"  ⚠ {grand['runs_failed']} failed cells:")
        for gd, t, e in failed[:10]:
            print(f"    {gd}/{t}: {e[:120]}")

    # ── Mirror to legacy ──────────────────────────────────────────────
    if not args.no_mirror_to_legacy and not args.dry_run and all_serials:
        print()
        print(f"  mirroring {len(all_serials)} runner-serial outputs "
                f"→ {LEGACY_OUT_COLL} …")
        n_mirror, n_with_outcome = await _mirror_to_legacy(
            db, replay_serials=all_serials, league=league)
        print(f"  ✓ mirrored {n_mirror:,} rows ({n_with_outcome:,} "
                f"with resolved outcome)")
    elif args.no_mirror_to_legacy:
        print("  ⚠ legacy mirror DISABLED — UI Results panel will not "
                "see this run.")

    # ── Sample-diff emit ──────────────────────────────────────────────
    if sample_snaps and not args.dry_run:
        n_diffs = await _emit_sample_diff(db, snapshots=sample_snaps,
                                                run_id=diff_run_id)
        print(f"  ✓ sample-diff: emitted {len(sample_snaps)} compare rows "
                f"({n_diffs} tier-decision deltas) → {DIFF_COLL} "
                f"(diff_run_id={diff_run_id})")

    print("=" * 78)
    return 0 if grand["runs_failed"] == 0 else 1


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league",          default="MLB")
    p.add_argument("--start",           required=True)
    p.add_argument("--end",             required=True)
    p.add_argument("--tiers",           default=",".join(VALID_TIERS))
    p.add_argument("--gate-path",       default="universal",
                      choices=VALID_GATE_PATHS)
    p.add_argument("--canonical-path",  action="store_true")
    p.add_argument("--research-mode", action="store_true",
                      dest="research_mode",
                      help="Score every prop; do NOT short-circuit on "
                              "tier_odds_bucket_fail; grade every row that "
                              "has a known outcome (not only "
                              "production-gate-pass rows). For grid sweep / "
                              "candidate optimization research.")
    p.add_argument("--skip-production-gates", action="store_true",
                      dest="research_mode",
                      help="Alias for --research-mode.")
    p.add_argument("--snapshot-hour",   type=int, default=11)
    p.add_argument("--limit-dates",     type=int, default=None)
    p.add_argument("--no-mirror-to-legacy", action="store_true")
    p.add_argument("--sample-diff",     type=int, default=0,
                      help="Snapshot N existing legacy rows before run, "
                            "emit per-row diff after.")
    p.add_argument("--continue-on-error", action="store_true",
                      help="Default behavior is HARD-FAIL on any runner error. "
                            "Set to skip bad date/tier cells instead.")
    p.add_argument("--dry-run",         action="store_true")
    # Legacy CLI flags retained for backward-compat with existing
    # /admin/testing UI workflow + Admin API allowlist. These are
    # documented as no-ops in SSOT mode (the runner controls everything).
    p.add_argument("--exclude-stat-family", default=None,
                      help="[deprecated in SSOT mode — runner uses production "
                            "eligibility, set via runner policy not here]")
    p.add_argument("--limit", type=int, default=None,
                      help="[deprecated — use --limit-dates]")
    p.add_argument("--force", action="store_true",
                      help="[deprecated — runner handles idempotency]")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
