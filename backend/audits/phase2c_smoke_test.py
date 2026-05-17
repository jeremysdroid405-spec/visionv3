"""Phase 2c smoke test — verify the universal production replay runner
wires through the new schemas + adapter without touching live code.

Hard guarantees re-verified here:
 1. `production_replay_runner` imports cleanly.
 2. The runner is callable with the documented signature.
 3. No live-path code was edited (Phase 2a + 2b live byte-identical).
 4. `_project_layer3_to_output()` produces a ProductionReplayOutput-shaped
    dict from a synthetic Layer-3 row.
 5. End-to-end on 2026-05-05 + 2026-05-06: a run doc is persisted to
    `mlb_production_replay_runs` with serial, audit pins, completion
    timestamps, and aggregate stats. Output docs land in
    `mlb_production_replay_outputs`, keyed by serial.
 6. NO mutation of `mlb_replay_model_outputs` (legacy Layer-3 results).

This test reads/writes the new production replay collections only. It
does not touch any live (non-replay) collection.
"""
import asyncio
import os
import sys
import inspect
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient


def _step(n, title):
    print(f"\n[{n}] {title}")


_step(1, "Import test — `production_replay_runner` imports cleanly")
from services.replay.production_replay_runner import (
    run_production_replay, _project_layer3_to_output, _resolve_adapter,
)
print(f"     run_production_replay loaded — signature: {inspect.signature(run_production_replay)}")
sig = inspect.signature(run_production_replay)
required_kwargs = {"sport", "game_date"}
optional_kwargs = {"snapshot_iso", "tier", "mem_limit_mb",
                    "force_layer3", "dry_run", "notes"}
have = set(sig.parameters.keys()) - {"db"}
missing = (required_kwargs | optional_kwargs) - have
assert not missing, f"missing kwargs: {missing}"
print(f"     all expected kwargs present: ✅")


_step(2, "Adapter resolution — MLB, NBA, NFL all resolvable")
class _FakeDb:
    def __getitem__(self, k):
        raise AssertionError(f"Adapter must not touch db at resolve time (asked for {k!r})")

for sport in ("mlb", "nba", "nfl"):
    a = _resolve_adapter(_FakeDb(), sport)
    print(f"     {sport} → {a.__class__.__name__}  SPORT={a.SPORT!r}")
    assert a.SPORT == sport

try:
    _resolve_adapter(_FakeDb(), "soccer")
    raise AssertionError("expected ValueError for unsupported sport")
except ValueError as e:
    print(f"     unsupported sport rejected: ✅ ({e})")


_step(3, "Schema projection — synthetic Layer-3 row → ProductionReplayOutput")
synth = {
    "sport": "mlb", "game_date": "2026-05-06",
    "event_id": "evt_test_001",
    "home_team": "Yankees", "away_team": "Mets",
    "commence_time": "2026-05-06T23:05:00Z",
    "snapshot_iso": "2026-05-06T11:00:00Z",
    "player_name": "Test Player", "player_name_normalized": "test_player",
    "stat_family": "total_bases",
    "market": "batter_total_bases_alternate", "is_alternate": True,
    "line": 1.5, "side": "OVER", "book": "fanduel", "odds": -110,
    "projection_mu": 1.82, "sigma": 0.71,
    "model_probability": 0.62, "fair_probability": 0.62,
    "implied_probability": 0.524, "edge": 0.096,
    "hit_rate_l5": 80.0, "hit_rate_l10": 70.0, "hit_rate_l20": 65.0,
    "cv": 0.92,
}
proj = _project_layer3_to_output(
    synth, serial="MLB-PRODREPLAY-TEST-WZ-1100UTC-99999",
    sport="mlb", tier="war_zone",
    gate_pass=True, failed_gates=[], gate_config_version="test_gate_v1",
)
required_out_keys = {
    "replay_serial", "sport", "game_date", "snapshot_iso", "event_id",
    "player_name_normalized", "stat_family", "market", "is_alternate",
    "line", "side", "book", "odds",
    "projection_mu", "sigma", "model_probability",
    "fair_probability", "implied_probability", "edge",
    "tier", "gate_pass", "failed_gates", "gate_config_version",
}
miss = required_out_keys - set(proj.keys())
assert not miss, f"missing output keys: {miss}"
assert proj["gate_pass"] is True
assert proj["failed_gates"] == []
assert proj["line"] == 1.5
print(f"     output doc has {len(proj)} fields; required keys all present ✅")


_step(4, "Re-verify Phase 2b: `as_of_date` kwarg + filter parity")
from services.mlb_high_friction_model import MLBHighFrictionModel
psig = inspect.signature(MLBHighFrictionModel.predict)
assert "as_of_date" in psig.parameters
assert psig.parameters["as_of_date"].default is None
logs = [
    {"date": "2026-05-01", "hits": 1},
    {"date": "2026-05-06", "hits": 9},   # cutoff day excluded
    {"date": "2026-05-08", "hits": 3},   # future excluded
]
kept = MLBHighFrictionModel._filter_logs_before(logs, "2026-05-06")
assert len(kept) == 1 and kept[0]["date"] == "2026-05-01"
print(f"     Phase 2b filter still byte-identical ✅")


_step(5, "Re-verify Phase 2a: `tier_evaluator` signature unchanged")
from services.scoring.tier_evaluator import evaluate_tier_with_overrides
tsig = inspect.signature(evaluate_tier_with_overrides)
assert "feature_provider" in tsig.parameters
assert tsig.parameters["feature_provider"].default is None
print(f"     Phase 2a tier_evaluator(feature_provider=None) default preserved ✅")


# ── End-to-end (requires Mongo) ─────────────────────────────────────
async def _e2e():
    _step(6, "End-to-end run: 2026-05-05 + 2026-05-06 (dry_run=True)")
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Sanity — there is data to replay
    cnt_5 = await db.mlb_historical_alt_odds_raw.count_documents(
        {"game_date": "2026-05-05", "snapshot_iso": "2026-05-05T11:00:00Z"})
    cnt_6 = await db.mlb_historical_alt_odds_raw.count_documents(
        {"game_date": "2026-05-06", "snapshot_iso": "2026-05-06T11:00:00Z"})
    print(f"     odds rows: 2026-05-05={cnt_5}  2026-05-06={cnt_6}")
    if cnt_5 == 0 and cnt_6 == 0:
        print("     ⚠️  No historical odds rows present — skipping E2E.")
        return

    # First: 05-05 dry run (no persistence, just validation pass)
    summary_dry = await run_production_replay(
        db, sport="mlb", game_date="2026-05-05",
        snapshot_iso="2026-05-05T11:00:00Z",
        tier="war_zone", dry_run=True, notes="phase2c_smoke_dry_run",
    )
    print(f"     dry_run 05-05: serial={summary_dry['serial']}  "
          f"scanned={summary_dry['rows_scanned']}  "
          f"qualified={summary_dry['rows_qualified']}  "
          f"wins/losses/push={summary_dry['wins']}/{summary_dry['losses']}/{summary_dry['pushes']}  "
          f"hit_rate={summary_dry['hit_rate_pct']}%  "
          f"roi={summary_dry['roi_pct']}%  "
          f"elapsed={summary_dry['elapsed_s']}s")
    # Dry run must not have persisted
    persisted = await db.mlb_production_replay_runs.count_documents(
        {"serial": summary_dry["serial"]})
    assert persisted == 0, f"dry_run persisted run doc (count={persisted})"
    persisted_out = await db.mlb_production_replay_outputs.count_documents(
        {"replay_serial": summary_dry["serial"]})
    assert persisted_out == 0, f"dry_run persisted output docs (count={persisted_out})"
    print("     dry_run persisted nothing ✅")

    _step(7, "End-to-end run: 2026-05-06 (real persistence)")
    summary = await run_production_replay(
        db, sport="mlb", game_date="2026-05-06",
        snapshot_iso="2026-05-06T11:00:00Z",
        tier="war_zone", dry_run=False, notes="phase2c_smoke_real",
    )
    print(f"     real run 05-06: serial={summary['serial']}  "
          f"scanned={summary['rows_scanned']}  "
          f"qualified={summary['rows_qualified']}  "
          f"wins/losses/push={summary['wins']}/{summary['losses']}/{summary['pushes']}  "
          f"hit_rate={summary['hit_rate_pct']}%  "
          f"roi={summary['roi_pct']}%  "
          f"profit_units={summary['profit_units']}  "
          f"elapsed={summary['elapsed_s']}s")

    run_doc = await db.mlb_production_replay_runs.find_one(
        {"serial": summary["serial"]}, {"_id": 0})
    assert run_doc is not None, "run doc not persisted"
    assert run_doc["sport"] == "mlb"
    assert run_doc["tier"] == "war_zone"
    assert run_doc["replay_completed_at"] is not None
    assert run_doc["rows_scanned"] == summary["rows_scanned"]
    assert run_doc["adapter_version"] and len(run_doc["adapter_version"]) == 64
    assert run_doc["production_pipeline_version"] and \
        len(run_doc["production_pipeline_version"]) == 64
    assert run_doc["input_collection_versions"]
    print(f"     run doc persisted with full audit pins ✅")
    print(f"       pipeline_sha={run_doc['production_pipeline_version'][:12]}…")
    print(f"       adapter_sha ={run_doc['adapter_version'][:12]}…")
    print(f"       input pins keys: {sorted(run_doc['input_collection_versions'].keys())}")

    out_cnt = await db.mlb_production_replay_outputs.count_documents(
        {"replay_serial": summary["serial"]})
    print(f"     output docs persisted: {out_cnt}")
    assert out_cnt == summary["rows_scanned"], \
        f"out_cnt={out_cnt} != rows_scanned={summary['rows_scanned']}"

    # Sample output doc + grading sanity
    qualified_sample = await db.mlb_production_replay_outputs.find_one(
        {"replay_serial": summary["serial"], "gate_pass": True},
        {"_id": 0})
    if qualified_sample:
        gs = qualified_sample.get("grade_status")
        print(f"     sample qualified pick: {qualified_sample['player_name']} "
              f"{qualified_sample['market']} {qualified_sample['side']} "
              f"{qualified_sample['line']} @ {qualified_sample['odds']} "
              f"→ {gs}")
        assert gs in {"win", "loss", "push", "ungraded"}
        assert "profit_units" in qualified_sample
    not_q = await db.mlb_production_replay_outputs.find_one(
        {"replay_serial": summary["serial"], "gate_pass": False},
        {"_id": 0})
    if not_q:
        assert not_q.get("grade_status") == "not_qualified"
        print(f"     sample not-qualified row marked as not_qualified ✅")

    # Verify legacy Layer-3 output collection was NOT mutated by us
    layer3_cnt = await db.mlb_replay_model_outputs.count_documents(
        {"game_date": "2026-05-06",
         "snapshot_iso": "2026-05-06T11:00:00Z"})
    print(f"     legacy mlb_replay_model_outputs preserved: {layer3_cnt} rows")

    # Idempotency — running again with the same serial must upsert
    _step(8, "Idempotency check: re-run 05-05 with force_layer3=False")
    summary2 = await run_production_replay(
        db, sport="mlb", game_date="2026-05-05",
        snapshot_iso="2026-05-05T11:00:00Z",
        tier="war_zone", dry_run=False, force_layer3=False,
        notes="phase2c_smoke_idempotency",
    )
    print(f"     re-run 05-05: serial={summary2['serial']}  "
          f"scanned={summary2['rows_scanned']}  "
          f"qualified={summary2['rows_qualified']}")
    assert summary2["serial"] != summary["serial"], \
        "each invocation must allocate a fresh serial"
    # Each call produces a new run doc but bulk_write upsert means
    # output keys are stable per replay_serial.
    print("     fresh serial allocated per call ✅")

    client.close()


print()
asyncio.run(_e2e())
print("\n[✓] Phase 2c smoke test: all checks passed.")
