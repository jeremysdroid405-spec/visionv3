"""
Regression tests for `_enrich_mlb_board_vision_intel` (master_sync step 6
MLB wire-up, 2026-05-05).

Locks the contract that:
  - The function persists Gemini-authored narratives to
    `mlb_prop_scores.vision_intel` (and the `_content_hash` /
    `_generated_at` fields).
  - Strict mode is honored: empty / None Gemini output is NOT replaced
    with the deterministic fallback template — we write nothing for
    that slot.
  - Mirror to `mlb_cached_board.props[].vision_intel` happens for
    successful slots only.
  - The cap envelope (per-tier + global) matches NBA's.

Mocks `MLBVisionIntel.analyze_tier_batch` so tests never call Gemini.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    base = os.environ["DB_NAME"]
    test_db = client[f"{base}_mlb_vi_test"]
    yield test_db
    for coll in await test_db.list_collection_names():
        await test_db.drop_collection(coll)


def _make_pick(canonical_key: str, tier: str = "safe_haven", vision_score: float = 80.0):
    return {
        "canonical_key":   canonical_key,
        "version_tag":     "final-mlb-rt",
        "active":          True,
        "tier":            tier,
        "sport":           "mlb",
        "player_name":     f"Player_{canonical_key}",
        "stat_type":       "Hits",
        "line":            1.5,
        "side":            "OVER",
        "direction":       "Over",
        "recommendation":  "OVER",
        "vision_score":    vision_score,
    }


async def test_enrich_mlb_writes_narratives_and_skips_empty_slots(db):
    """Gemini returns 2 narratives + 1 empty for 3 picks. Only the 2
    non-empty narratives reach `mlb_prop_scores`. Empty slot is NOT
    backfilled with the fallback template."""
    picks = [_make_pick(f"ck_{i}") for i in range(3)]
    await db["mlb_prop_scores"].insert_many([dict(p) for p in picks])

    captured = {}

    async def fake_analyze_tier_batch(tier_picks, tier_name, *, strict=False):
        captured["strict"] = strict
        out = []
        for i, p in enumerate(tier_picks):
            out.append({
                **p,
                "vision_intel": "" if i == 1 else f"Mock narrative {p['canonical_key']}",
            })
        return out

    fake_service = type("S", (), {
        "enabled": True,
        "analyze_tier_batch": staticmethod(fake_analyze_tier_batch),
    })()

    with patch(
        "services.master_sync.MLB_LIVE", "final-mlb-rt"
    ), patch(
        "services.mlb_vision_intel.get_mlb_vision_intel", return_value=fake_service
    ):
        from services.master_sync import _enrich_mlb_board_vision_intel
        metrics = await _enrich_mlb_board_vision_intel(db)

    assert captured["strict"] is True
    assert metrics["board_picks_total"] == 3
    assert metrics["gemini_returned"] == 2
    assert metrics["gemini_empty_or_failed"] == 1
    assert metrics["score_docs_written"] == 2

    docs = {d["canonical_key"]: d async for d in db["mlb_prop_scores"].find()}
    assert docs["ck_0"]["vision_intel"] == "Mock narrative ck_0"
    assert "vision_intel" not in docs["ck_1"] or not docs["ck_1"].get("vision_intel")
    assert docs["ck_2"]["vision_intel"] == "Mock narrative ck_2"
    # Hash + timestamp present on the populated docs
    assert docs["ck_0"].get("vision_intel_content_hash")
    assert docs["ck_0"].get("vision_intel_generated_at")


async def test_enrich_mlb_disabled_service_returns_skip_marker(db):
    """When `MLBVisionIntel.enabled=False`, function exits early without
    DB reads / writes."""
    fake_service = type("S", (), {"enabled": False, "analyze_tier_batch": AsyncMock()})()
    with patch(
        "services.mlb_vision_intel.get_mlb_vision_intel", return_value=fake_service
    ):
        from services.master_sync import _enrich_mlb_board_vision_intel
        metrics = await _enrich_mlb_board_vision_intel(db)
    assert metrics["board_picks_total"] == 0
    assert metrics["score_docs_written"] == 0
    assert "service_disabled" in metrics["skip_reasons"]


async def test_enrich_mlb_mirrors_to_cached_board(db):
    """Successful narrative writes also mirror onto
    `mlb_cached_board.props[].vision_intel` via the array_filter."""
    pick = _make_pick("ck_mirror_1", tier="front_lines")
    await db["mlb_prop_scores"].insert_one(dict(pick))
    await db["mlb_cached_board"].insert_one({
        "player_name": pick["player_name"],
        "props": [{
            "stat_type": pick["stat_type"],
            "line":      pick["line"],
            "direction": pick["direction"],
            "vision_intel": "",
        }],
    })

    async def fake(picks, tier, *, strict=False):
        return [{**p, "vision_intel": "Mirror narrative"} for p in picks]

    fake_service = type("S", (), {"enabled": True, "analyze_tier_batch": staticmethod(fake)})()
    with patch(
        "services.mlb_vision_intel.get_mlb_vision_intel", return_value=fake_service
    ):
        from services.master_sync import _enrich_mlb_board_vision_intel
        metrics = await _enrich_mlb_board_vision_intel(db)

    assert metrics["cached_board_writes"] == 1
    cb = await db["mlb_cached_board"].find_one({"player_name": pick["player_name"]})
    assert cb["props"][0]["vision_intel"] == "Mirror narrative"
