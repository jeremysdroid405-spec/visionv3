"""
D6 — Delta metrics + batch-cap regression tests.

Verifies:
  1. `record_tick` increments counters and appends to history.
  2. `history_snapshot` respects `n` and newest-last ordering.
  3. `prometheus_text` emits the expected metric names.
  4. Batch-cap logic in `RescoreDirtyPropsStep` caps the processed
     subset, prioritises updated over new, and reports counters.
  5. Ticks that bypass the full chain (prior-tick-running) still get
     recorded into metrics.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from services import delta_metrics
from services.pipeline.delta_steps import RescoreDirtyPropsStep
from services.delta.detector import DeltaDetectionResult


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Wipe metrics between tests to keep assertions deterministic."""
    delta_metrics._REGISTRY.clear()
    yield
    delta_metrics._REGISTRY.clear()


def _sample_tick(sport="nba", *, dirty=10, updated=4, new=6, retired=1,
                 rescored=10, duration=0.42, skipped=False,
                 skipped_reason=None, batch_capped=False,
                 keys_skipped_due_to_cap=0, upstream_lock_held=False):
    return {
        "sport": sport,
        "tick_id": "abc123",
        "success": True,
        "skipped": skipped,
        "skipped_reason": skipped_reason,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_duration_seconds": duration,
        "steps": {
            "1_detect": {
                "dirty_count": dirty, "updated_count": updated,
                "new_count": new, "retired_count": retired,
            },
            "2_lock_gate": {"upstream_lock_held": upstream_lock_held},
            "3_rescore_dirty": {
                "written": rescored, "batch_capped": batch_capped,
                "keys_skipped_due_to_cap": keys_skipped_due_to_cap,
                "batch_cap": 500,
            },
            "4_rebalance_tiers": {"retired_docs_modified": retired},
        },
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Metrics recording
# ---------------------------------------------------------------------------

def test_record_tick_increments_counters():
    delta_metrics.record_tick(_sample_tick(sport="nba", dirty=10, rescored=10))
    delta_metrics.record_tick(_sample_tick(sport="nba", dirty=5, rescored=5))
    c = delta_metrics.counters_snapshot("nba")
    assert c["ticks_total"] == 2
    assert c["ticks_success"] == 2
    assert c["dirty_props_sum"] == 15
    assert c["rescored_props_sum"] == 15
    assert c["duration_count"] == 2


def test_record_tick_captures_skipped_reasons():
    delta_metrics.record_tick(_sample_tick(
        sport="mlb", skipped=True, skipped_reason="upstream_lock_held"
    ))
    delta_metrics.record_tick(_sample_tick(
        sport="mlb", skipped=True, skipped_reason="prior_tick_in_progress"
    ))
    delta_metrics.record_tick(_sample_tick(
        sport="mlb", skipped=True, skipped_reason="upstream_lock_held"
    ))
    c = delta_metrics.counters_snapshot("mlb")
    assert c["ticks_total"] == 3
    assert c["ticks_success"] == 0
    assert c["ticks_skipped_total"] == 3
    assert c["ticks_skipped_by_reason"]["upstream_lock_held"] == 2
    assert c["ticks_skipped_by_reason"]["prior_tick_in_progress"] == 1


def test_history_snapshot_newest_last_and_limit():
    for i in range(5):
        delta_metrics.record_tick(_sample_tick(sport="nba", dirty=i))
    hist = delta_metrics.history_snapshot("nba", n=3)
    assert len(hist) == 3
    # Newest last → last entry should have dirty_count=4 (the final tick)
    assert hist[-1]["dirty_count"] == 4
    # Oldest of the 3 retained = i=2
    assert hist[0]["dirty_count"] == 2


def test_record_tick_captures_batch_cap_fields():
    delta_metrics.record_tick(_sample_tick(
        sport="nba", dirty=2000, rescored=500,
        batch_capped=True, keys_skipped_due_to_cap=1500,
    ))
    c = delta_metrics.counters_snapshot("nba")
    assert c["batch_cap_truncations_total"] == 1
    assert c["batch_cap_keys_skipped_sum"] == 1500
    hist = delta_metrics.history_snapshot("nba", n=1)
    assert hist[-1]["batch_capped"] is True
    assert hist[-1]["keys_skipped_due_to_cap"] == 1500


def test_prometheus_text_contains_all_series():
    delta_metrics.record_tick(_sample_tick(sport="nba", dirty=3))
    delta_metrics.record_tick(_sample_tick(
        sport="mlb", skipped=True, skipped_reason="upstream_lock_held"
    ))
    text = delta_metrics.prometheus_text()
    assert "propvision_delta_ticks_total" in text
    assert "propvision_delta_ticks_success_total" in text
    assert "propvision_delta_ticks_skipped_total" in text
    assert "propvision_delta_dirty_props_total" in text
    assert "propvision_delta_rescored_props_total" in text
    assert "propvision_delta_tick_duration_seconds_bucket" in text
    assert "propvision_delta_tick_duration_seconds_sum" in text
    assert "propvision_delta_tick_duration_seconds_count" in text
    assert 'sport="nba"' in text
    assert 'sport="mlb"' in text
    assert 'reason="upstream_lock_held"' in text


# ---------------------------------------------------------------------------
# Batch-cap logic in RescoreDirtyPropsStep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_cap_truncates_and_prioritises_updated(monkeypatch):
    updated = {f"nba|e1|p|st|{i}|OVER" for i in range(5)}
    new = {f"nba|e2|q|st|{i}|OVER" for i in range(15)}
    det = DeltaDetectionResult(
        sport="nba",
        watermark_utc=datetime.now(timezone.utc),
        dirty_keys=updated | new,
        updated_keys=updated,
        new_keys=new,
    )
    context = {
        "detection": det,
        "rescore_batch_cap": 10,  # updated(5) + 5 new = 10
    }

    captured = {}
    async def _fake_recompute(db, sport, version_tag, **kw):
        captured["keys"] = set(kw["only_canonical_keys"])
        captured["write_mode"] = kw.get("write_mode")
        return {
            "processed": len(captured["keys"]),
            "written": len(captured["keys"]),
            "skipped": 0, "replaced": 0,
            "collection": f"{sport}_prop_scores",
            "version_tag": version_tag,
            "only_canonical_keys_matched": len(captured["keys"]),
        }
    import services.pipeline.delta_steps as ds_mod
    monkeypatch.setattr(ds_mod, "recompute_sport", _fake_recompute)

    step = RescoreDirtyPropsStep()
    out = await step.run("nba", db=None, context=context)

    # Truncation reported correctly
    assert out["batch_capped"] is True
    assert out["keys_requested"] == 10
    assert out["keys_skipped_due_to_cap"] == 10   # 5+15−10
    assert out["total_dirty_requested"] == 20
    # ALL updated keys must be included (priority 1); remaining cap filled
    # deterministically from sorted new keys.
    assert updated.issubset(captured["keys"])
    assert len(captured["keys"]) == 10


@pytest.mark.asyncio
async def test_batch_cap_disabled_passes_everything(monkeypatch):
    keys = {f"nba|e|p|st|{i}|OVER" for i in range(2000)}
    det = DeltaDetectionResult(
        sport="nba",
        watermark_utc=datetime.now(timezone.utc),
        dirty_keys=keys, updated_keys=set(), new_keys=keys,
    )
    context = {"detection": det, "rescore_batch_cap": 0}  # disabled

    captured = {}
    async def _fake_recompute(db, sport, version_tag, **kw):
        captured["keys"] = set(kw["only_canonical_keys"])
        return {
            "processed": len(captured["keys"]), "written": len(captured["keys"]),
            "skipped": 0, "replaced": 0,
            "collection": f"{sport}_prop_scores",
            "version_tag": version_tag,
            "only_canonical_keys_matched": len(captured["keys"]),
        }
    import services.pipeline.delta_steps as ds_mod
    monkeypatch.setattr(ds_mod, "recompute_sport", _fake_recompute)

    step = RescoreDirtyPropsStep()
    out = await step.run("nba", db=None, context=context)
    assert out["batch_capped"] is False
    assert out["keys_requested"] == 2000
    assert out["keys_skipped_due_to_cap"] == 0
