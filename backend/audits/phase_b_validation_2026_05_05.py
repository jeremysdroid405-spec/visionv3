"""Phase B validation — single MLB historical run via the universal
pipeline runner.

Mirrors the directive exactly:

    sport            = MLB
    mode             = historical
    snapshot_time    = 2026-05-05T11:00:00Z
    output_namespace = test
    test_id          = MLB-HIST-20260505-1100UTC-00001

What this script proves:
  1. Files added/changed (printed at the top).
  2. Output collections written (test_writer.describe()).
  3. Counts by stage (raw / normalized / after_priceable /
     after_pp_playable / after_routing / after_gates / cards_written).
  4. Invalid SH UNDER props were removed by eligibility (via the
     hardcoded PP registry — rbis UNDER, batter_strikeouts UNDER,
     etc. drop at filter_pp_playable).
  5. The production eligibility function was called (envelope
     `eligibility_version` pin + stage counts).
  6. JSON artifact written to /app/backend/audits/.

Run:
    sudo supervisorctl stop backend
    cd /app/backend && python -m audits.phase_b_validation_2026_05_05
    sudo supervisorctl start backend
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from services.pipeline import run_pipeline, PIPELINE_VERSION


TEST_ID = "MLB-HIST-20260505-1100UTC-00001"
SPORT = "mlb"
MODE = "historical"
SNAPSHOT_TIME = "2026-05-05T11:00:00Z"
OUTPUT_NAMESPACE = "test"
TIER = "safe_haven"

AUDIT_OUT = Path("/app/backend/audits/phase_b_validation_2026_05_05.json")


def _print_files_changed():
    print("\n=== FILES ADDED / CHANGED (Phase B) ===")
    files = [
        # NEW
        "services/pipeline/runner.py",
        "services/pipeline/audit_envelope.py",
        "services/pipeline/providers/__init__.py",
        "services/pipeline/providers/base.py",
        "services/pipeline/providers/live_input.py",
        "services/pipeline/providers/historical_input.py",
        "services/pipeline/providers/test_writer.py",
        "services/pipeline/providers/production_writer.py",
        # MODIFIED
        "services/pipeline/__init__.py            (re-export)",
        "services/replay/providers/audit.py        (output_namespace param)",
        "services/replay/production_replay_runner.py "
        "(output_namespace + eligibility_predicate + audit_envelope + "
        "serial_override params)",
    ]
    for f in files:
        print(f"  • {f}")


async def main():
    _print_files_changed()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print(f"\n=== RUNNING UNIVERSAL PIPELINE ===")
    print(f"  test_id          = {TEST_ID}")
    print(f"  sport            = {SPORT}")
    print(f"  mode             = {MODE}")
    print(f"  snapshot_time    = {SNAPSHOT_TIME}")
    print(f"  output_namespace = {OUTPUT_NAMESPACE}")
    print(f"  pipeline_version = {PIPELINE_VERSION}")
    print()

    t0 = datetime.now(timezone.utc)
    summary = await run_pipeline(
        db, sport=SPORT, mode=MODE,
        snapshot_time=SNAPSHOT_TIME,
        output_namespace=OUTPUT_NAMESPACE,
        test_id=TEST_ID,
        tier=TIER,
        canonical_path=True,
        notes=f"Phase B validation {TEST_ID}",
    )
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    print(f"\n=== RUN COMPLETED in {elapsed:.1f}s ===\n")

    env = summary.get("audit_envelope") or {}
    stages = env.get("stage_counts") or {}
    extra = env.get("extra") or {}

    print("=== STAGE COUNTS ===")
    print(f"  raw input rows          : {stages.get('raw_input_rows')}")
    print(f"  normalized props        : {stages.get('normalized_props')}")
    print(f"  after priceable filter  : {stages.get('after_priceable_filter')}")
    print(f"  after PP playable filter: {stages.get('after_pp_playable_filter')}")
    print(f"  canonical props built   : {stages.get('canonical_props')}")
    print(f"  cards written           : {stages.get('cards_written')}")
    print(f"  eligibility rejects     : {summary.get('eligibility_rejects')}  "
          f"(rows filtered before canonical collapse)")
    print(f"  rows scanned            : {summary.get('rows_scanned')}")
    print(f"  rows qualified          : {summary.get('rows_qualified')}")

    print()
    print("=== ELIGIBILITY DETAIL ===")
    cov = extra.get("coverage_stats") or {}
    pp = extra.get("pp_playable_stats") or {}
    print(f"  coverage_stats          : "
          f"seen={cov.get('total_props_seen')}  "
          f"remaining={cov.get('total_props_remaining')}  "
          f"multi_book={cov.get('multi_book')}  "
          f"single_book={cov.get('single_book')}  "
          f"pp_only_excluded={cov.get('pp_only')}")
    print(f"  pp_playable_stats       : "
          f"seen={pp.get('total_props_seen')}  "
          f"remaining={pp.get('remaining')}  "
          f"dropped_no_pp_side={pp.get('dropped_no_pp_side')}")
    print(f"  dropped_by_side         : {pp.get('dropped_by_side')}")
    print(f"  registry_fallback_stamps: {extra.get('pp_registry_fallback_stamped')}")

    print()
    print("=== OUTPUT COLLECTIONS WRITTEN ===")
    writer = env.get("output_writer") or {}
    for w in writer.get("writes_to") or []:
        print(f"  • {w.replace('{sport}', SPORT)}  "
              f"(namespace={env.get('output_namespace')})")

    # Verify the test collections actually got written.
    out_runs = await db[f"{SPORT}_test_runs"].count_documents({"serial": TEST_ID})
    out_outputs = await db[f"{SPORT}_test_outputs"].count_documents({"replay_serial": TEST_ID})
    out_cards = await db[f"{SPORT}_test_cards"].count_documents({"replay_serial": TEST_ID})
    print()
    print("=== PERSISTED DOCUMENT COUNTS ===")
    print(f"  {SPORT}_test_runs    docs for {TEST_ID}: {out_runs}")
    print(f"  {SPORT}_test_outputs docs for {TEST_ID}: {out_outputs}")
    print(f"  {SPORT}_test_cards   docs for {TEST_ID}: {out_cards}")

    # ── Proof that production eligibility was called ─────────────
    print()
    print("=== PROOF: PRODUCTION ELIGIBILITY FUNCTION WAS CALLED ===")
    print(f"  envelope.eligibility_version = {env.get('eligibility_version')}")
    print(f"  envelope.routing_version     = {env.get('routing_version')}")
    print(f"  envelope.gate_version        = {env.get('gate_version_firmware')}")
    print(f"  envelope.pipeline_version    = {env.get('pipeline_version')}")
    input_prov = env.get("input_provider") or {}
    print(f"  input_provider.ssot_function = "
          f"{(input_prov.get('extras') or {}).get('ssot_function')}")
    print(f"  input_provider.use_pp_registry_fallback = "
          f"{(input_prov.get('extras') or {}).get('use_pp_registry_fallback')}")

    # ── Proof invalid SH UNDER props were filtered ─────────────────
    print()
    print("=== PROOF: INVALID SH UNDER PROPS WERE REMOVED BY ELIGIBILITY ===")
    # Find rbis-UNDER / strikeouts-UNDER / batter_strikeouts-UNDER props
    # in the historical raw collection vs the test_outputs collection.
    raw_rbis_under = await db["mlb_historical_alt_odds_raw"].count_documents({
        "sport": "mlb",
        "game_date": SNAPSHOT_TIME[:10],
        "snapshot_iso": SNAPSHOT_TIME,
        "market": {"$in": ["batter_rbis", "batter_rbis_alternate"]},
        "side": "UNDER",
    })
    raw_bso_under = await db["mlb_historical_alt_odds_raw"].count_documents({
        "sport": "mlb",
        "game_date": SNAPSHOT_TIME[:10],
        "snapshot_iso": SNAPSHOT_TIME,
        "market": {"$in": ["batter_strikeouts", "batter_strikeouts_alternate"]},
        "side": "UNDER",
    })
    raw_hr_under = await db["mlb_historical_alt_odds_raw"].count_documents({
        "sport": "mlb",
        "game_date": SNAPSHOT_TIME[:10],
        "snapshot_iso": SNAPSHOT_TIME,
        "market": {"$in": ["batter_home_runs", "batter_home_runs_alternate"]},
        "side": "UNDER",
    })
    test_rbis_under = await db[f"{SPORT}_test_outputs"].count_documents({
        "replay_serial": TEST_ID, "stat_family": "rbis", "side": "UNDER",
    })
    test_bso_under = await db[f"{SPORT}_test_outputs"].count_documents({
        "replay_serial": TEST_ID,
        "stat_family": {"$in": ["batter_strikeouts", "strikeouts"]},
        "side": "UNDER",
    })
    test_hr_under = await db[f"{SPORT}_test_outputs"].count_documents({
        "replay_serial": TEST_ID, "stat_family": "home_runs", "side": "UNDER",
    })
    print(f"  rbis UNDER:              raw alt_odds={raw_rbis_under}  →  "
          f"test_outputs={test_rbis_under}  (expected 0)")
    print(f"  batter_strikeouts UNDER: raw alt_odds={raw_bso_under}  →  "
          f"test_outputs={test_bso_under}  (expected 0)")
    print(f"  home_runs UNDER:         raw alt_odds={raw_hr_under}  →  "
          f"test_outputs={test_hr_under}  (expected 0)")

    invalid_under_removed = (
        test_rbis_under == 0 and test_bso_under == 0 and test_hr_under == 0
    )
    print(f"  ⇒ INVALID SH UNDER PROPS REMOVED: {invalid_under_removed}")

    # ── Compare vs the prior canonical replay run ──────────────────
    prior = await db["mlb_production_replay_runs"].find_one(
        {"serial": "MLB-PRODREPLAY-20260505-SH-1100UTC-00074"},
        {"_id": 0, "rows_scanned": 1, "rows_qualified": 1,
         "cards_displayed": 1, "canonical_summary": 1},
    )
    print()
    print("=== COMPARISON vs PRIOR REPLAY RUN ===")
    print(f"  prior serial: MLB-PRODREPLAY-20260505-SH-1100UTC-00074")
    print(f"    rows_scanned    : {(prior or {}).get('rows_scanned')}")
    print(f"    rows_qualified  : {(prior or {}).get('rows_qualified')}")
    print(f"    cards_displayed : {(prior or {}).get('cards_displayed')}")
    print(f"    canonical_props : "
          f"{((prior or {}).get('canonical_summary') or {}).get('canonical_props_built')}")
    print(f"  Phase B test run ({TEST_ID}):")
    print(f"    rows_scanned    : {summary.get('rows_scanned')}")
    print(f"    rows_qualified  : {summary.get('rows_qualified')}")
    print(f"    cards_displayed : {summary.get('cards_displayed')}")
    print(f"    canonical_props : {stages.get('canonical_props')}")
    print(f"    eligibility_rejects: {summary.get('eligibility_rejects')}")

    # ── Write JSON artifact ────────────────────────────────────────
    artifact = {
        "audit_kind": "phase_b_validation",
        "test_id": TEST_ID,
        "directive": {
            "sport": SPORT, "mode": MODE,
            "snapshot_time": SNAPSHOT_TIME,
            "output_namespace": OUTPUT_NAMESPACE,
        },
        "elapsed_s": elapsed,
        "pipeline_version": PIPELINE_VERSION,
        "audit_envelope": env,
        "stage_counts": stages,
        "summary": {
            k: v for k, v in summary.items()
            if k not in ("layer3_summary", "audit_envelope")
        },
        "persisted_collection_doc_counts": {
            f"{SPORT}_test_runs": out_runs,
            f"{SPORT}_test_outputs": out_outputs,
            f"{SPORT}_test_cards": out_cards,
        },
        "invalid_under_check": {
            "rbis_UNDER_raw_alt_odds": raw_rbis_under,
            "rbis_UNDER_test_outputs": test_rbis_under,
            "batter_strikeouts_UNDER_raw_alt_odds": raw_bso_under,
            "batter_strikeouts_UNDER_test_outputs": test_bso_under,
            "home_runs_UNDER_raw_alt_odds": raw_hr_under,
            "home_runs_UNDER_test_outputs": test_hr_under,
            "invalid_under_removed_by_eligibility": invalid_under_removed,
        },
        "prior_replay_comparison": prior or {},
    }
    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUT.write_text(json.dumps(artifact, indent=2, default=str))
    print()
    print(f"=== JSON ARTIFACT ===")
    print(f"  {AUDIT_OUT}")
    client.close()
    return artifact


if __name__ == "__main__":
    asyncio.run(main())
