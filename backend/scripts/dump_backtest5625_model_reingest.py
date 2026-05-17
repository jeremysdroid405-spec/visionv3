"""Full model re-ingest for 2026-05-06 props.

1. Snapshots existing mlb_replay_model_outputs to BEFORE.csv
2. Force-runs the full model from scratch (replay_date with force=True)
3. Snapshots fresh outputs to AFTER.csv
4. Produces a row-by-row diff into model_diff.csv + summary
"""
from __future__ import annotations
import asyncio
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

GAME_DATE = "2026-05-06"
SNAPSHOT  = f"{GAME_DATE}T11:00:00Z"
OUT_DIR   = Path("/app/backend/backtest5625")

KEY_FIELDS = ("event_id","player_name_normalized","market","line","side","book")
SCORE_FIELDS = ("projection_mu","sigma","model_probability","fair_probability",
                 "implied_probability","edge","hit_rate_l5","hit_rate_l10",
                 "hit_rate_l20","cv")


async def _dump(db, fname: str) -> int:
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT},
        {"_id":0},
    )
    rows = []
    async for r in cursor: rows.append(r)
    if not rows:
        (OUT_DIR / fname).write_text("(no rows)\n")
        return 0
    cols = sorted({k for r in rows for k in r.keys()})
    with (OUT_DIR / fname).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow(r)
    return len(rows)


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"[1/4] Snapshotting current model_outputs for {GAME_DATE}...", flush=True)
    n_before = await _dump(db, "model_outputs_BEFORE.csv")
    print(f"      {n_before:,} rows → {OUT_DIR}/model_outputs_BEFORE.csv")

    # Index the BEFORE rows for diffing
    before_idx = {}
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT}, {"_id":0})
    async for r in cursor:
        key = tuple(r.get(k) for k in KEY_FIELDS)
        before_idx[key] = {f: r.get(f) for f in SCORE_FIELDS}

    print(f"\n[2/4] Running full model re-ingest (force=True)...", flush=True)
    from services.replay.mlb_replay_engine import replay_date, DEFAULT_MEM_LIMIT_MB
    s = await replay_date(db, GAME_DATE, snapshot_iso=SNAPSHOT,
                           mem_limit_mb=DEFAULT_MEM_LIMIT_MB, force=True)
    print(f"      summary:")
    for k in ("alt_odds_rows_seen","model_outputs_written",
              "unique_mu_predictions","candidates_skipped_no_cache",
              "candidates_skipped_inference_failed",
              "candidates_skipped_under_alt","elapsed_s",
              "rss_mb_peak","scoring_config_version","source_version"):
        print(f"        {k:40s} {s.get(k)}")

    print(f"\n[3/4] Snapshotting fresh model_outputs to AFTER.csv...", flush=True)
    n_after = await _dump(db, "model_outputs_AFTER.csv")
    print(f"      {n_after:,} rows → {OUT_DIR}/model_outputs_AFTER.csv")

    # Index AFTER + diff
    print(f"\n[4/4] Computing row-by-row diff...", flush=True)
    after_idx = {}
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT}, {"_id":0})
    async for r in cursor:
        key = tuple(r.get(k) for k in KEY_FIELDS)
        after_idx[key] = {f: r.get(f) for f in SCORE_FIELDS}

    only_before = set(before_idx) - set(after_idx)
    only_after  = set(after_idx) - set(before_idx)
    common = set(before_idx) & set(after_idx)
    changed = []
    EPS = 1e-9
    for k in common:
        b, a = before_idx[k], after_idx[k]
        diffs = {}
        for f in SCORE_FIELDS:
            bv, av = b.get(f), a.get(f)
            if bv is None and av is None: continue
            if bv is None or av is None:
                diffs[f] = (bv, av); continue
            try:
                if abs(float(bv) - float(av)) > EPS:
                    diffs[f] = (bv, av)
            except (TypeError, ValueError):
                if bv != av: diffs[f] = (bv, av)
        if diffs: changed.append((k, diffs))

    with (OUT_DIR / "model_diff.csv").open("w", newline="") as f:
        cols = list(KEY_FIELDS) + ["status"]
        for sf in SCORE_FIELDS:
            cols += [f"{sf}_before", f"{sf}_after", f"{sf}_delta"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(only_before):
            row = dict(zip(KEY_FIELDS, k)); row["status"] = "REMOVED"
            for sf in SCORE_FIELDS:
                row[f"{sf}_before"] = before_idx[k].get(sf)
                row[f"{sf}_after"] = None; row[f"{sf}_delta"] = None
            w.writerow(row)
        for k in sorted(only_after):
            row = dict(zip(KEY_FIELDS, k)); row["status"] = "ADDED"
            for sf in SCORE_FIELDS:
                row[f"{sf}_before"] = None
                row[f"{sf}_after"] = after_idx[k].get(sf)
                row[f"{sf}_delta"] = None
            w.writerow(row)
        for k, diffs in sorted(changed):
            row = dict(zip(KEY_FIELDS, k)); row["status"] = "CHANGED"
            for sf in SCORE_FIELDS:
                bv = before_idx[k].get(sf); av = after_idx[k].get(sf)
                row[f"{sf}_before"] = bv; row[f"{sf}_after"] = av
                try: row[f"{sf}_delta"] = float(av) - float(bv) if bv is not None and av is not None else None
                except (TypeError, ValueError): row[f"{sf}_delta"] = None
            w.writerow(row)

    print()
    print("="*78)
    print("MODEL RE-INGEST RESULT")
    print("="*78)
    print(f"  BEFORE rows : {n_before:,}")
    print(f"  AFTER rows  : {n_after:,}")
    print(f"  Common keys : {len(common):,}")
    print(f"  Removed (in BEFORE only) : {len(only_before):,}")
    print(f"  Added   (in AFTER only)  : {len(only_after):,}")
    print(f"  Score deltas detected    : {len(changed):,}")
    if not changed and not only_before and not only_after:
        print()
        print("  *** PERFECT MATCH — model is deterministic and reproducible ***")
    else:
        print()
        print(f"  → see model_diff.csv for details")

    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
