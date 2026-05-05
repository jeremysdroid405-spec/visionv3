"""
Regression test for the Vision Intel canonical_key pairing fix
(CHANGELOG 2026-05-05 prop_id mis-mapping fix).

`MLBVisionIntel.analyze_tier_batch` and `VisionIntelService.analyze_tier_batch`
both internally `enriched_props.sort(key=composite_score)` before
returning. `master_sync._enrich_{nba,mlb}_board_vision_intel` previously
zipped the SOURCE list with the SORTED result list positionally — when
the two orderings drifted, the wrong narrative landed on the wrong
canonical_key.

Fix locked in by these tests: pairing is now an EXACT canonical_key dict
lookup, so reordering `analyze_tier_batch` output cannot misroute a
narrative. Unmatched results are silently discarded — no fallback, no
fuzzy match, no order-based assignment.
"""

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    base = os.environ["DB_NAME"]
    test_db = client[f"{base}_vi_pair_test"]
    yield test_db
    for coll in await test_db.list_collection_names():
        await test_db.drop_collection(coll)


def _make_pick(player_name: str, vision_score: float, tier: str = "safe_haven"):
    """Distinct canonical_keys per player so the dict lookup is unambiguous."""
    return {
        "canonical_key":  f"mlb|evt1|{player_name}|Hits+Runs+RBIs|0.5|OVER",
        "version_tag":    "final-mlb-rt",
        "active":         True,
        "tier":           tier,
        "sport":          "mlb",
        "player_name":    player_name,
        "stat_type":      "Hits+Runs+RBIs",
        "line":           0.5,
        "side":           "OVER",
        "direction":      "Over",
        "recommendation": "OVER",
        "vision_score":   vision_score,
    }


async def test_reversed_results_do_not_misroute_narratives(db):
    """Worst-case: `analyze_tier_batch` returns results in REVERSED
    order (mimics composite_score sorting that flips the input order).
    Each persisted narrative must match its own canonical_key, never
    a neighbor's."""
    players = ["Josh Jung", "Aaron Judge", "Ozzie Albies", "Yandy Diaz", "Bobby Witt Jr."]
    picks = [_make_pick(p, vision_score=80.0 + i) for i, p in enumerate(players)]
    await db["mlb_prop_scores"].insert_many([dict(p) for p in picks])

    async def fake_analyze_tier_batch(tier_picks, tier_name, *, strict=False):
        # Simulate analyze_tier_batch's internal sort by returning in
        # REVERSED order. Each result carries its own canonical_key
        # (preserved by `_merge_intel_to_prop({**prop}, …)`).
        out = []
        for p in tier_picks:
            out.append({
                **p,
                "vision_intel": f"Narrative for {p['player_name']}",
                "composite_score": 1.0,
            })
        out.reverse()
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

    assert metrics["score_docs_written"] == 5

    # Each player's row must carry that player's narrative — no swaps.
    for p in players:
        doc = await db["mlb_prop_scores"].find_one({"player_name": p})
        assert doc is not None
        vi = doc.get("vision_intel") or ""
        assert vi == f"Narrative for {p}", (
            f"narrative for {p!r} got misrouted: stored={vi!r}"
        )


async def test_unmatched_canonical_key_is_discarded(db):
    """Defensive: if Gemini hallucinates a canonical_key not in the
    source batch, the result is dropped. No fuzzy fallback, no
    misrouting, no exception."""
    pick = _make_pick("Josh Jung", vision_score=85.0)
    await db["mlb_prop_scores"].insert_one(dict(pick))

    async def fake(tier_picks, tier_name, *, strict=False):
        return [{
            "canonical_key": "mlb|evt1|GHOST_PLAYER|Hits+Runs+RBIs|0.5|OVER",
            "vision_intel":  "Hallucinated narrative for nobody",
        }]

    fake_service = type("S", (), {
        "enabled": True,
        "analyze_tier_batch": staticmethod(fake),
    })()

    with patch(
        "services.master_sync.MLB_LIVE", "final-mlb-rt"
    ), patch(
        "services.mlb_vision_intel.get_mlb_vision_intel", return_value=fake_service
    ):
        from services.master_sync import _enrich_mlb_board_vision_intel
        metrics = await _enrich_mlb_board_vision_intel(db)

    assert metrics["score_docs_written"] == 0
    doc = await db["mlb_prop_scores"].find_one({"player_name": "Josh Jung"})
    # Source pick stays untouched — no narrative leaked onto it.
    assert not (doc.get("vision_intel") or "").strip()
