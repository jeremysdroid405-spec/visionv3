"""Phase B runner unit tests (no DB).

Mocks the input provider + the `run_production_replay` delegate so
we can assert the orchestrator's behaviour:

  • Provider + writer resolution by (sport, mode, namespace)
  • Eligibility chain wired correctly (live vs historical)
  • Eligibility allow-set built from filtered props
  • Audit envelope composed with the right shape + versions
  • Multi-tier path runs provider+eligibility ONCE, then loops
  • Per-tier test_id suffix is SH / FL / WZ
  • NBA historical raises NotImplementedError (scaffold, fail-closed)
  • Single-tier return shape is preserved (back-compat)
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import pytest

from services.pipeline import runner as runner_module
from services.pipeline.providers.base import IInputProvider, IOutputWriter


# ── Helpers / fakes ───────────────────────────────────────────────
def _live_prop(**kw):
    base = dict(
        sport="mlb",
        player_name="Test", player_name_normalized="test",
        event_id="evt1",
        stat_type="Total Bases", stat_family="total_bases",
        recommendation="OVER", side="OVER", line=1.5,
        playable_on_pp=True,
        pp_layer={"book": "prizepicks", "line": 1.5, "odds": 100},
        draftkings_price=-180, fanduel_price=-175,
    )
    base.update(kw)
    return base


class FakeProvider:
    sport = "mlb"
    mode = "historical"
    name = "FakeProvider"

    def __init__(self, props):
        self._props = props

    async def load_props(self, db):
        return list(self._props)

    def describe_source(self):
        return {
            "provider": self.name, "mode": self.mode,
            "sport": self.sport,
            "source_collections": ["fake_coll"],
            "input_snapshot_hash": "fakehash",
            "extras": {"use_pp_registry_fallback": True},
        }


class FakeWriter:
    output_namespace = "test"
    name = "FakeWriter"

    def describe(self):
        return {"writer": self.name,
                "output_namespace": self.output_namespace,
                "writes_to": ["{sport}_test_runs"]}


class _RunRecorder:
    """Captures every call to run_production_replay so tests can
    assert on the dispatch arguments without needing a real DB."""
    def __init__(self):
        self.calls = []

    async def __call__(self, db, **kw):
        self.calls.append(kw)
        # Mimic the real return shape minimally.
        return {
            "serial": kw["serial_override"],
            "sport": kw["sport"], "game_date": kw["game_date"],
            "snapshot_iso": kw["snapshot_iso"],
            "tier": kw["tier"], "gate_path": "universal",
            "output_namespace": kw["output_namespace"],
            "rows_scanned": 100, "rows_qualified": 1,
            "eligibility_rejects": 5, "cards_displayed": 1,
            "wins": 0, "losses": 0, "pushes": 0, "ungraded": 1,
            "hit_rate_pct": 0.0, "roi_pct": 0.0,
            "profit_units": 0.0, "stake_units": 1.0,
            "elapsed_s": 0.5, "rss_mb_peak": 100.0,
            "canonical_path": True,
            "canonical_summary": {"canonical_props_built": 50},
        }


@pytest.fixture
def patch_run_production_replay(monkeypatch):
    rec = _RunRecorder()
    monkeypatch.setattr(runner_module,
                          "run_production_replay", rec)
    return rec


@pytest.fixture
def patch_providers(monkeypatch):
    """Patches both the provider resolver and the writer resolver
    so the runner uses our fakes."""
    props = [
        _live_prop(player_name="A", player_name_normalized="a"),
        _live_prop(player_name="B", player_name_normalized="b",
                   side="UNDER", recommendation="UNDER"),
        # PP-illegal: rbis UNDER (Phase A registry should drop)
        _live_prop(player_name="C", player_name_normalized="c",
                   stat_type="RBIs", stat_family="rbis",
                   line=0.5, side="UNDER", recommendation="UNDER",
                   playable_on_pp=None, pp_layer=None),
    ]
    fake_provider = FakeProvider(props)
    fake_writer = FakeWriter()
    monkeypatch.setattr(runner_module,
                          "_resolve_input_provider",
                          lambda sport, mode, **_: fake_provider)
    monkeypatch.setattr(runner_module,
                          "_resolve_output_writer",
                          lambda ns: fake_writer)
    return fake_provider, fake_writer


# ── Tests ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_single_tier_dispatches_one_run(
    patch_providers, patch_run_production_replay,
):
    summary = await runner_module.run_pipeline(
        db=object(), sport="mlb", mode="historical",
        snapshot_time="2026-05-05T11:00:00Z",
        output_namespace="test",
        test_id="MLB-HIST-20260505-1100UTC-TEST",
        tier="safe_haven",
    )
    rec = patch_run_production_replay
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["tier"] == "safe_haven"
    assert call["output_namespace"] == "test"
    assert call["serial_override"] == "MLB-HIST-20260505-1100UTC-TEST"
    assert call["canonical_path"] is True
    assert callable(call["eligibility_predicate"])
    # Single-tier return shape — back-compat with Phase B validation
    assert "multi_tier" not in summary
    assert summary["serial"] == "MLB-HIST-20260505-1100UTC-TEST"
    env = summary["audit_envelope"]
    assert env["pipeline_version"].startswith("universal_pipeline_v1")
    assert env["eligibility_version"].startswith(
        "apply_production_eligibility_v1"
    )


@pytest.mark.asyncio
async def test_multi_tier_runs_all_three_with_per_tier_serial(
    patch_providers, patch_run_production_replay,
):
    result = await runner_module.run_pipeline(
        db=object(), sport="mlb", mode="historical",
        snapshot_time="2026-05-05T11:00:00Z",
        output_namespace="test",
        test_id="MLB-HIST-20260505-1100UTC-MULTI",
        tier="all",
    )
    rec = patch_run_production_replay
    assert result["multi_tier"] is True
    assert result["tiers"] == ["safe_haven", "front_lines", "war_zone"]
    assert len(rec.calls) == 3
    # Per-tier test_id format: parent-{SH|FL|WZ}
    serial_by_tier = {c["tier"]: c["serial_override"]
                       for c in rec.calls}
    assert serial_by_tier == {
        "safe_haven":  "MLB-HIST-20260505-1100UTC-MULTI-SH",
        "front_lines": "MLB-HIST-20260505-1100UTC-MULTI-FL",
        "war_zone":    "MLB-HIST-20260505-1100UTC-MULTI-WZ",
    }


@pytest.mark.asyncio
async def test_multi_tier_loads_provider_once(
    patch_providers, patch_run_production_replay,
):
    """The provider's `load_props` is called ONCE for a 3-tier run.
    This is the whole point of multi-tier — don't hit the source 3×."""
    provider, _ = patch_providers
    counter = {"n": 0}
    real_load = provider.load_props

    async def counting_load(db):
        counter["n"] += 1
        return await real_load(db)
    provider.load_props = counting_load

    await runner_module.run_pipeline(
        db=object(), sport="mlb", mode="historical",
        snapshot_time="2026-05-05T11:00:00Z",
        output_namespace="test",
        test_id="MLB-HIST-20260505-1100UTC-LOADONCE",
        tier=["safe_haven", "front_lines", "war_zone"],
    )
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_list_tier_param_accepted(
    patch_providers, patch_run_production_replay,
):
    """`tier=["safe_haven", "front_lines"]` is a valid input."""
    result = await runner_module.run_pipeline(
        db=object(), sport="mlb", mode="historical",
        snapshot_time="2026-05-05T11:00:00Z",
        output_namespace="test",
        test_id="MLB-HIST-20260505-1100UTC-LIST",
        tier=["safe_haven", "front_lines"],
    )
    assert result["tiers"] == ["safe_haven", "front_lines"]
    assert len(patch_run_production_replay.calls) == 2


@pytest.mark.asyncio
async def test_eligibility_runs_for_historical(
    patch_providers, patch_run_production_replay,
):
    """Historical mode → SSOT eligibility runs, registry fallback
    stamps the rbis UNDER row, then `filter_pp_playable` drops it.
    The dispatched eligibility_predicate must reject rbis-UNDER."""
    summary = await runner_module.run_pipeline(
        db=object(), sport="mlb", mode="historical",
        snapshot_time="2026-05-05T11:00:00Z",
        output_namespace="test",
        test_id="MLB-HIST-20260505-1100UTC-ELIG",
        tier="safe_haven",
    )
    env = summary["audit_envelope"]
    extra = env["extra"]
    # PP-registry stamped at least the rbis UNDER row.
    assert extra["pp_registry_fallback_stamped"] >= 1
    # The dispatched predicate must reject rbis-UNDER row keys.
    pred = patch_run_production_replay.calls[0]["eligibility_predicate"]
    rbis_under = {
        "event_id": "evt1",
        "player_name_normalized": "c",
        "stat_family": "rbis",
        "line": 0.5, "side": "UNDER",
    }
    assert pred(rbis_under) is False
    # Hits OVER row keys must pass.
    hits_over = {
        "event_id": "evt1",
        "player_name_normalized": "a",
        "stat_family": "total_bases",
        "line": 1.5, "side": "OVER",
    }
    assert pred(hits_over) is True


@pytest.mark.asyncio
async def test_live_mode_skips_runner_side_eligibility(
    monkeypatch, patch_run_production_replay,
):
    """When mode='live', the LiveInputProvider's adapter already
    invoked the SSOT inside load_live_props. The runner MUST NOT
    re-run eligibility (double-filter / double-log)."""
    # Build a fake live provider that records whether the runner
    # called apply_production_eligibility after its load.
    seen = {"elig_called": False}
    from services.pipeline import runner as rm

    def fake_elig(*a, **kw):
        seen["elig_called"] = True
        raise RuntimeError("should not be called in live mode")
    monkeypatch.setattr(rm, "apply_production_eligibility", fake_elig)

    fp = FakeProvider([_live_prop()])
    fp.mode = "live"
    monkeypatch.setattr(rm, "_resolve_input_provider",
                          lambda *a, **kw: fp)
    monkeypatch.setattr(rm, "_resolve_output_writer",
                          lambda ns: FakeWriter())

    summary = await rm.run_pipeline(
        db=object(), sport="mlb", mode="live",
        output_namespace="test",
        test_id="MLB-LIVE-20260517-1200UTC-CHECK",
        tier="safe_haven",
    )
    assert seen["elig_called"] is False
    env = summary["audit_envelope"]
    # Coverage_stats has the skip-note.
    assert "skipped" in env["extra"]["coverage_stats"]["note"]


@pytest.mark.asyncio
async def test_nba_historical_fails_closed(monkeypatch):
    """Phase D scope — NBA historical provider must raise
    NotImplementedError, NOT silently fall back to MLB or live."""
    from services.pipeline import runner as rm
    with pytest.raises(NotImplementedError, match="NBAHistorical"):
        await rm.run_pipeline(
            db=object(), sport="nba", mode="historical",
            snapshot_time="2026-05-05T11:00:00Z",
            output_namespace="test",
            test_id="NBA-HIST-20260505-1100UTC-FAIL",
            tier="safe_haven",
        )


@pytest.mark.asyncio
async def test_historical_requires_snapshot_time():
    from services.pipeline import runner as rm
    with pytest.raises(ValueError, match="snapshot_time required"):
        await rm.run_pipeline(
            db=object(), sport="mlb", mode="historical",
            snapshot_time=None,
            output_namespace="test",
            test_id="MLB-HIST-MISSING",
            tier="safe_haven",
        )


def test_resolve_output_writer_namespace():
    from services.pipeline.runner import _resolve_output_writer
    assert _resolve_output_writer("test").output_namespace == "test"
    assert _resolve_output_writer("production_replay").output_namespace == "production_replay"
    with pytest.raises(ValueError):
        _resolve_output_writer("not_a_real_namespace")


def test_audit_envelope_versions_are_pinned():
    """Pin the version strings so accidental rename surfaces."""
    from services.pipeline.audit_envelope import (
        PIPELINE_VERSION, ELIGIBILITY_VERSION,
        ROUTING_VERSION, GATE_VERSION_FIRMWARE,
    )
    assert PIPELINE_VERSION == "universal_pipeline_v1_phase_b_2026_05_17"
    assert ELIGIBILITY_VERSION == (
        "apply_production_eligibility_v1_phase_a"
    )
    assert "sh_le_neg300_wz_ge_pos150" in ROUTING_VERSION
    assert GATE_VERSION_FIRMWARE == "tier_evaluator_universal_v1"


# ── Pytest async config ──────────────────────────────────────────
@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


# Mark all async tests for pytest-asyncio mode strict.
pytestmark = pytest.mark.asyncio
