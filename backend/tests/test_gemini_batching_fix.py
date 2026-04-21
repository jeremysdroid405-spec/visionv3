"""
Gemini UNDER-enrichment batching audit — regression tests.

Verifies:
  1. `routes/ferrari_tiers._enrich_under_picks_with_gemini` now calls
     `analyze_tier_batch(..., strict=True)` — ONE Gemini call per tier,
     not N gathered calls per prop.
  2. `VisionIntelService.analyze_tier_batch(strict=True)` returns
     `List[Optional[Dict]]` with None for Gemini-missed props (preserves
     the cache-Gemini-authored-only invariant).
  3. `VisionIntelService.analyze_tier_batch()` (default strict=False)
     is unchanged — other callers still get fallback text.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Structural: the non-batched bulk path is gone
# ---------------------------------------------------------------------------

def test_under_enrichment_no_longer_fans_out():
    """After the batching fix, the UNDER enricher must not loop
    analyze_prop_strict over the list."""
    from routes import ferrari_tiers
    src = inspect.getsource(ferrari_tiers._enrich_under_picks_with_gemini)
    # The fan-out comprehension signature must not appear anywhere.
    assert "analyze_prop_strict(p, tier_name) for p in to_call" not in src
    # And the batched path MUST be in use.
    assert "analyze_tier_batch" in src
    assert "strict=True" in src


def test_analyze_tier_batch_strict_signature():
    """VisionIntelService.analyze_tier_batch must accept strict= kwarg."""
    from services.vision_intel_service import VisionIntelService
    sig = inspect.signature(VisionIntelService.analyze_tier_batch)
    assert "strict" in sig.parameters
    assert sig.parameters["strict"].default is False


# ---------------------------------------------------------------------------
# Behavioural: strict=True returns [None, dict, None, ...] in input order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strict_batch_returns_none_for_unechoed_props():
    from services.vision_intel_service import VisionIntelService
    svc = VisionIntelService.__new__(VisionIntelService)
    svc.enabled = True
    svc.client = MagicMock()
    svc.model_name = "gemini-3-flash-preview"

    # Fake batch response stub — we patch _parse_batch_response so the
    # test doesn't care about the real prompt/response shape.
    props = [
        {"canonical_key": "p1", "player_name": "A", "stat_type": "PTS",
         "line": 10, "direction": "UNDER"},
        {"canonical_key": "p2", "player_name": "B", "stat_type": "AST",
         "line": 5, "direction": "UNDER"},
        {"canonical_key": "p3", "player_name": "C", "stat_type": "REB",
         "line": 7, "direction": "UNDER"},
    ]

    # Fake Gemini response: only p1 and p3 echoed back with narrative.
    # p2 returns empty intel → should map to None in strict mode.
    intel_map = {
        "p1": {"vision_intel": "A narrative"},
        "p2": {},  # empty = no Gemini text
        "p3": {"vision_intel": "C narrative"},
    }

    # Stub prompt-builder + parse + API call
    svc._build_batch_prompt = MagicMock(return_value="fake prompt")
    svc._parse_batch_response = MagicMock(return_value=intel_map)

    class _FakeResp:
        text = "stub"
    svc.client.models = MagicMock()
    svc.client.models.generate_content = MagicMock(return_value=_FakeResp())

    result = await svc.analyze_tier_batch(props, "safe_haven", strict=True)

    assert isinstance(result, list)
    assert len(result) == len(props)
    assert result[0] == {"vision_intel": "A narrative"}
    assert result[1] is None                  # ← fallback NOT cached
    assert result[2] == {"vision_intel": "C narrative"}


@pytest.mark.asyncio
async def test_strict_batch_when_service_disabled_returns_all_none():
    from services.vision_intel_service import VisionIntelService
    svc = VisionIntelService.__new__(VisionIntelService)
    svc.enabled = False
    svc.client = None
    svc.model_name = "gemini-3-flash-preview"

    props = [
        {"canonical_key": "p1", "player_name": "A", "stat_type": "PTS",
         "line": 10, "direction": "UNDER"},
        {"canonical_key": "p2", "player_name": "B", "stat_type": "AST",
         "line": 5, "direction": "UNDER"},
    ]
    out = await svc.analyze_tier_batch(props, "safe_haven", strict=True)
    assert out == [None, None]


@pytest.mark.asyncio
async def test_non_strict_batch_legacy_callers_unchanged():
    """Non-strict mode must still merge fallback text into every prop
    (MLB / other callers rely on this)."""
    from services.vision_intel_service import VisionIntelService
    svc = VisionIntelService.__new__(VisionIntelService)
    svc.enabled = False
    svc.client = None
    svc.model_name = "gemini-3-flash-preview"

    props = [{"canonical_key": "p1", "player_name": "A", "stat_type": "PTS",
              "line": 10, "direction": "UNDER"}]
    out = await svc.analyze_tier_batch(props, "safe_haven")
    # Default strict=False → fallback enrichment returned
    assert out[0] is not None
    assert isinstance(out[0], dict)


# ---------------------------------------------------------------------------
# Call-count proof: exactly 1 Gemini API call per tier in batched path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_makes_exactly_one_api_call_per_tier():
    from services.vision_intel_service import VisionIntelService
    svc = VisionIntelService.__new__(VisionIntelService)
    svc.enabled = True
    svc.model_name = "gemini-3-flash-preview"
    svc._build_batch_prompt = MagicMock(return_value="fake prompt")
    svc._parse_batch_response = MagicMock(return_value={})

    call_count = {"n": 0}

    class _FakeResp:
        text = "stub"

    def _gen_content(model, contents):
        call_count["n"] += 1
        return _FakeResp()

    svc.client = MagicMock()
    svc.client.models = MagicMock()
    svc.client.models.generate_content = _gen_content

    # 10-prop tier should still result in exactly 1 API call.
    props = [
        {"canonical_key": f"p{i}", "player_name": f"P{i}",
         "stat_type": "PTS", "line": 10 + i, "direction": "UNDER"}
        for i in range(10)
    ]
    await svc.analyze_tier_batch(props, "safe_haven", strict=True)
    assert call_count["n"] == 1, (
        f"Expected exactly 1 Gemini API call for a 10-prop tier, got {call_count['n']}"
    )
