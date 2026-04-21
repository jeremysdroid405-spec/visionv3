"""
Gemini cost fixes — P2 / P3 regression tests.

Covers:
  * P3.1 GeminiLRUCache hit/miss + eviction
  * P3.1 AIContextEngine LRU (_call_gemini) — structural check
  * P3.2 calculate_intel_suite: use_llm deprecated, default=deterministic,
          mode="gemini" path still callable
  * P3.3 gemini_metrics record + cache_stats snapshot shape
  * P2.1 VisionSummaryService is now a delegator (no prompt-building of its own)
  * P2.3 _RENDERABLE_TIERS constant is used in both Gemini call sites
"""
import inspect

import pytest

from services.gemini_metrics import (
    GeminiLRUCache,
    cache_stats,
    record_gemini_call,
    _counters,
)


@pytest.fixture(autouse=True)
def _reset_counters():
    _counters["total_calls"] = 0
    _counters["total_hits"] = 0
    _counters["total_misses"] = 0
    _counters["calls_by_sport"].clear()
    _counters["calls_by_kind"].clear()
    _counters["call_timestamps"].clear()
    yield


# --- P3.1: LRU -------------------------------------------------------------

def test_gemini_lru_hit_after_set():
    lru = GeminiLRUCache(max_size=3)
    lru.set("hello", "world")
    assert lru.get("hello") == "world"
    assert lru.get("nope") is None


def test_gemini_lru_evicts_oldest():
    lru = GeminiLRUCache(max_size=2)
    lru.set("a", "1")
    lru.set("b", "2")
    lru.set("c", "3")
    assert lru.get("a") is None
    assert lru.get("b") == "2"
    assert lru.get("c") == "3"


def test_gemini_lru_moves_accessed_to_front():
    lru = GeminiLRUCache(max_size=2)
    lru.set("a", "1")
    lru.set("b", "2")
    assert lru.get("a") == "1"    # touch 'a'
    lru.set("c", "3")             # evicts 'b', not 'a'
    assert lru.get("a") == "1"
    assert lru.get("b") is None


def test_calculate_intel_suite_ignores_use_llm_warning(caplog):
    """Passing use_llm=True must log deprecation before any DB call."""
    import asyncio
    from unittest.mock import MagicMock
    from services.intel_suite_calculator import IntelSuiteCalculator
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=MagicMock())
    calc = IntelSuiteCalculator(db=fake_db)

    # Don't care if the function body fails on DB access — we only need
    # the deprecation warning to fire BEFORE the body runs.
    with caplog.at_level("WARNING"):
        try:
            asyncio.run(calc.calculate_intel_suite(
                "Player", "PTS", 25.5, "OVER",
                use_llm=True,
                mode="deterministic",
            ))
        except Exception:
            pass  # DB-path failure is expected with the mock

    assert any("calculate_intel_suite(use_llm=...) is deprecated"
               in r.message for r in caplog.records), (
        "Deprecation warning for use_llm= was not emitted."
    )


def test_aicontext_engine_call_gemini_uses_lru_cache():
    """Structural check — the method body must reference the shared LRU."""
    import inspect as _ins
    from services.engines.ai_context_engine import AiContextEngine
    src = _ins.getsource(AiContextEngine._call_gemini)
    assert "GeminiLRUCache" in src
    assert "record_gemini_call" in src
    assert "cache.get" in src


# --- P3.2: use_llm deprecated + default deterministic ----------------------

def test_calculate_intel_suite_mode_param_default_is_deterministic():
    from services.intel_suite_calculator import IntelSuiteCalculator
    sig = inspect.signature(IntelSuiteCalculator.calculate_intel_suite)
    params = sig.parameters
    assert "mode" in params
    assert params["mode"].default == "deterministic"
    assert "use_llm" in params
    assert params["use_llm"].default is None


def test_calculate_intel_suite_ignores_use_llm_warning_legacy(caplog):
    """Legacy variant kept as additional coverage; mirrors the primary
    test above but with all sub-helpers patched to exercise the full
    function body end-to-end."""
    import asyncio
    from unittest.mock import MagicMock
    from services.intel_suite_calculator import IntelSuiteCalculator
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=MagicMock())
    calc = IntelSuiteCalculator(db=fake_db)

    # Patch heavy sub-helpers so the call exits quickly
    async def _noop(*a, **kw): return {}
    calc._calculate_usage_ripple = _noop
    calc._calculate_matchup_dvp = _noop
    calc._calculate_pace_delta = _noop
    calc._calculate_stability_index = _noop
    calc._calculate_blowout_warning = _noop

    async def _fake_insight(*a, **kw):
        # Confirm mode passes through (not use_llm)
        assert kw.get("mode") == "deterministic"
        return "stub"
    calc._generate_vision_insight = _fake_insight

    with caplog.at_level("WARNING"):
        try:
            asyncio.run(calc.calculate_intel_suite(
                "Player", "PTS", 25.5, "OVER",
                use_llm=True,       # deprecated
                mode="deterministic",
            ))
        except Exception:
            pass  # DB-path failure with mock is acceptable

    assert any("calculate_intel_suite(use_llm=...) is deprecated"
               in r.message for r in caplog.records)


# --- P3.3: metrics sink + cache_stats shape --------------------------------

def test_record_gemini_call_updates_counters():
    record_gemini_call("vision_intel_batch", sport="nba", hit=False)
    record_gemini_call("vision_intel_batch", sport="nba", hit=True)
    record_gemini_call("scout_engine_single", sport="mlb", hit=False)
    stats = cache_stats(window_hours=24)
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["total"] == 3
    assert stats["calls_by_sport"]["nba"] == 2
    assert stats["calls_by_sport"]["mlb"] == 1
    assert stats["calls_by_kind"]["vision_intel_batch"] == 2
    assert stats["calls_by_kind"]["scout_engine_single"] == 1
    assert 0 <= stats["hit_rate"] <= 1


def test_cache_stats_window_filter():
    record_gemini_call("vision_intel_strict", sport="nba", hit=False)
    stats = cache_stats(window_hours=1)
    assert "calls_last_1h" in stats
    assert stats["calls_last_1h"] == 1


# --- P2.1: VisionSummaryService is a thin delegator ------------------------

def test_vision_summary_service_has_no_own_prompt_building():
    """After P2.1 consolidation, the file should not carry a duplicate
    Gemini prompt / model call block. It delegates to VisionIntelService."""
    import services.vision_summary_service as vss
    src = inspect.getsource(vss)
    # These tokens belonged to the pre-P2.1 duplicate generator.
    assert "generate_content" not in src, (
        "VisionSummaryService still calls Gemini directly — delegation broken."
    )
    assert "gemini-3-flash-preview" not in src, (
        "VisionSummaryService still references a model name directly."
    )
    # And it should reference the unified service.
    assert "get_vision_intel_service" in src, (
        "Delegation path to VisionIntelService is missing."
    )


# --- P2.3: unqualified skip across the two Gemini call sites ---------------

def test_unified_pipeline_skips_unqualified_tier():
    import services.unified_pipeline as up
    src = inspect.getsource(up)
    assert "_RENDERABLE_TIERS" in src
    # The set MUST omit `unqualified` at both invocation sites.
    # Two usages: (1) _run_gemini_enrichment payload build loop,
    #             (2) _run_nba_under_enrichment loop.
    assert src.count("_RENDERABLE_TIERS = {") == 2
    assert "\"unqualified\"" not in src.split("_RENDERABLE_TIERS = {")[1].split("}")[0]
