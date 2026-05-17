"""Phase 4 — Field coverage scan for replay-row → NormalizedMetrics.

Read-only. Runs against the existing `MLB-PRODREPLAY-20260505-WZ-1100UTC-00008`
qualified pool (361 rows) AND the full gate_pass=False pool to assess
coverage on both the qualified subset and the broader candidate set.

For each NormalizedMetrics field, reports % populated. For the three
fields that are required-but-conditional (book_count, tp_source,
avg_hit_margin), also reports the per-tier "would-fail-closed" count
on the population the gate engine would actually try to evaluate.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, json
from collections import Counter
from dataclasses import asdict, fields

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.replay_field_hydrators import (
    load_book_inventory, load_player_game_logs_as_of,
    resolve_canonical_stat_family,
)
from services.replay.replay_metrics_builder import build_metrics_from_replay_row


SERIAL = "MLB-PRODREPLAY-20260505-WZ-1100UTC-00008"
GAME_DATE = "2026-05-05"
SNAPSHOT = "2026-05-05T11:00:00Z"
SPORT = "mlb"


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"\n=== Phase 4 field coverage — serial={SERIAL} ===\n")

    # Hydrators
    print("[1/3] loading book inventory ...", flush=True)
    inv = await load_book_inventory(
        db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAPSHOT)
    print(f"      → {len(inv):,} unique (event,player,market,line) keys "
          f"across the snapshot")

    print("[2/3] loading player game logs (as-of < game_date) ...", flush=True)
    plogs = await load_player_game_logs_as_of(db, game_date=GAME_DATE)
    print(f"      → {len(plogs):,} players with non-empty pre-{GAME_DATE} logs")

    # Cohorts: qualified (gate_pass) + full pool (scanned)
    print("[3/3] scanning replay outputs ...", flush=True)
    for cohort_name, cohort_filter in [
        ("QUALIFIED (gate_pass=True)",
            {"replay_serial": SERIAL, "gate_pass": True}),
        ("FULL_POOL (gate_pass=any)",
            {"replay_serial": SERIAL}),
    ]:
        await _scan_cohort(db, cohort_name, cohort_filter, inv, plogs)
    cli.close()


async def _scan_cohort(db, cohort_name, cohort_filter, inv, plogs):
    print(f"\n──── COHORT: {cohort_name}")
    total = await db.mlb_production_replay_outputs.count_documents(cohort_filter)
    print(f"     rows: {total}")
    if total == 0:
        return

    # Track for each NormalizedMetrics field:
    #   (populated_count, total_count, sample_value)
    populated: Counter = Counter()
    families: Counter = Counter()
    line_eq_half = 0
    line_eq_half_with_margin = 0
    line_eq_half_without_margin = 0
    bc_zero_or_missing = 0
    tp_source_missing = 0
    tp_source_devig = 0
    tp_source_one_sided = 0
    per_family_line_05: Counter = Counter()
    per_family_line_05_missing_margin: Counter = Counter()

    nm_field_names = [f.name for f in fields(__import__(
        "services.scoring.gates.schema", fromlist=["NormalizedMetrics"]
    ).NormalizedMetrics)]

    cursor = db.mlb_production_replay_outputs.find(
        cohort_filter, projection={"_id": 0})
    n_seen = 0
    async for r in cursor:
        n_seen += 1
        # Build metrics for each tier — coverage is tier-independent
        # for most fields but we'll show one (war_zone) since the
        # builder body is tier-agnostic.
        m = build_metrics_from_replay_row(
            r, tier="war_zone", sport=SPORT,
            book_inventory=inv, player_game_logs=plogs,
        )
        families[m.stat_family] += 1
        d = asdict(m)
        for k in nm_field_names:
            v = d.get(k)
            if k == "extras":
                # Count the two extras keys we explicitly populate
                if v and v.get("projection") is not None:
                    populated["extras.projection"] += 1
                continue
            if k == "context_vetoes":
                continue  # always [] by construction
            if v is not None and v != [] and v != {}:
                populated[k] += 1
        # Margin gate triggers
        if m.line is not None and float(m.line) == 0.5:
            line_eq_half += 1
            per_family_line_05[m.stat_family] += 1
            if m.avg_hit_margin is not None:
                line_eq_half_with_margin += 1
            else:
                line_eq_half_without_margin += 1
                per_family_line_05_missing_margin[m.stat_family] += 1
        if m.book_count in (None, 0):
            bc_zero_or_missing += 1
        if m.tp_source is None:
            tp_source_missing += 1
        elif m.tp_source == "devig":
            tp_source_devig += 1
        elif m.tp_source == "one_sided":
            tp_source_one_sided += 1
        if n_seen % 5000 == 0:
            print(f"     ... scanned {n_seen}")

    print(f"\n     ── stat_family distribution (top 12)")
    for fam, n in families.most_common(12):
        print(f"       {fam:25s}  {n:>6}  ({100*n/total:5.2f}%)")

    print(f"\n     ── NormalizedMetrics field coverage (% populated)")
    order = [
        "sport", "tier", "stat_family", "side",
        "reference_book", "reference_odds", "book_count",
        "tp", "tp_source", "is_alt", "vision_score",
        "p_model_pct", "edge_pct",
        "hit_rate", "hit_rate_l20", "hit_rate_l10", "hit_rate_l5",
        "hit_rate_sample_size", "ceiling_rate",
        "cv", "line", "avg_hit_margin", "avg_miss_margin",
        "extras.projection",
        "blowout_risk", "lineup_confirmed", "injury_flag",
    ]
    for f in order:
        # sport/tier/side/stat_family always set by builder
        if f in ("sport", "tier", "stat_family", "side"):
            pct = 100.0
            n = total
        else:
            n = populated.get(f, 0)
            pct = 100.0 * n / total
        print(f"       {f:25s}  {n:>6}/{total:<6}  {pct:6.2f}%")

    print(f"\n     ── tp_source distribution")
    print(f"       devig       : {tp_source_devig:>6}  ({100*tp_source_devig/total:5.2f}%)")
    print(f"       one_sided   : {tp_source_one_sided:>6}  ({100*tp_source_one_sided/total:5.2f}%)")
    print(f"       MISSING     : {tp_source_missing:>6}  ({100*tp_source_missing/total:5.2f}%)")
    print(f"     ── book_count==0/missing: {bc_zero_or_missing} "
          f"({100*bc_zero_or_missing/total:5.2f}%)")

    print(f"\n     ── 0.5-line cohort (engine cv→margin swap fires here)")
    print(f"       rows at line==0.5            : {line_eq_half} "
          f"({100*line_eq_half/total:5.2f}%)")
    print(f"       0.5-line WITH avg_hit_margin : {line_eq_half_with_margin}")
    print(f"       0.5-line MISSING margin      : {line_eq_half_without_margin}")
    if line_eq_half_without_margin:
        print(f"     ── 0.5-line MISSING margin by stat_family")
        for fam, n in per_family_line_05_missing_margin.most_common():
            tot_fam = per_family_line_05[fam]
            print(f"       {fam:25s}  {n:>4}/{tot_fam:<4}  "
                  f"({100*n/tot_fam:5.1f}%)")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
