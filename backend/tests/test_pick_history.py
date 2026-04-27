"""Unit tests for `services.forward_test.pick_history`.

Covers:
  * the schema projection (`_build_pick_doc`) — required fields, side
    normalization, edge fallback, RFA fields, board fingerprint.
  * the unique-index contract (idempotent re-insert).
  * the analytics aggregations.

These are pure-python (mongomock-free) where possible. Mongo-touching
tests use motor against the live test database via `MONGO_URL` so they
mirror production behavior.
"""
import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, "/app/backend")

from services.forward_test.pick_history import (
    COLLECTION_NAME,
    _bucket_edge,
    _build_pick_doc,
    _model_version,
    _roi_minus110,
    ensure_indexes,
    log_selected_picks,
    query_by_edge_bucket,
    query_by_stat,
    query_overall,
)


# ---------- pure-python helpers ----------
def test_model_version_format(monkeypatch):
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "100_0")
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.85")
    assert _model_version() == "nba_v3_100_0_rfa_0.85"


def test_bucket_edge():
    assert _bucket_edge(None) == "—"
    assert _bucket_edge(-1) == "<0%"
    assert _bucket_edge(0) == "0–5%"
    assert _bucket_edge(4.99) == "0–5%"
    assert _bucket_edge(5) == "5–10%"
    assert _bucket_edge(9.9) == "5–10%"
    assert _bucket_edge(10) == "10–15%"
    assert _bucket_edge(14.99) == "10–15%"
    assert _bucket_edge(15) == "15%+"


def test_roi_minus110():
    assert _roi_minus110(0, 0) == 0.0
    # Break-even at -110: w/n = 110/210 ≈ 0.5238
    assert abs(_roi_minus110(110, 100) - 0.0) < 1e-9
    # 1-1: lose money
    assert _roi_minus110(1, 1) < 0


def test_build_pick_doc_drops_unqualified():
    doc = {"tier": "unqualified", "stat_type": "PTS", "line": 22.5,
           "player_name": "X", "recommendation": "OVER"}
    assert _build_pick_doc(doc, fingerprint="x", model_version="v") is None


def test_build_pick_doc_required_fields():
    src = {
        "tier": "front_lines",
        "stat_type": "PTS", "line": 22.5,
        "player_name": "Stephen Curry",
        "recommendation": "Over",  # mixed case → should normalize
        "model_projection": 24.1, "model_sigma": 5.8,
        "p_true_active": 0.62, "tp": 55.0, "edge_pct": 7.0,
        "vision_score": 88.4, "expected_minutes": 33.5,
        "availability_status": "FULL_GO",
        "rfa_minutes_penalty_applied": False,
        "rfa_minutes_penalty_factor": 1.0,
        "tier_reference_odds": -130, "tp_source": "devig",
        "canonical_key": "nba|abc|Stephen Curry|PTS|22.5|OVER",
        "event_id": "abc",
    }
    out = _build_pick_doc(src, fingerprint="fp123", model_version="v3")
    assert out is not None
    assert out["side"] == "OVER"  # normalized
    assert out["stat"] == "PTS"
    assert out["mu"] == 24.1
    assert out["sigma"] == 5.8
    assert out["p_model"] == 0.62
    assert out["edge"] == 7.0
    assert out["board_fingerprint"] == "fp123"
    assert out["model_version"] == "v3"
    # Outcome fields must be None on initial insert.
    assert out["result"] is None
    assert out["actual"] is None
    assert out["hit"] is None


def test_build_pick_doc_edge_fallback_when_field_absent():
    src = {
        "tier": "war_zone", "stat_type": "AST", "line": 4.5,
        "player_name": "Test", "recommendation": "UNDER",
        "p_true_active": 0.65, "tp": 55.0,  # → derived edge ≈ 10.0
    }
    out = _build_pick_doc(src, fingerprint="x", model_version="v")
    assert out["edge"] is not None
    assert abs(out["edge"] - 10.0) < 1e-9


# ---------- mongo-touching ----------
@pytest.mark.asyncio
async def test_log_and_query_roundtrip():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    # Use a unique collection name for test isolation
    test_db_name = os.environ.get("DB_NAME") + "_pick_history_test_" + uuid.uuid4().hex[:6]
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[test_db_name]

    try:
        # Patch the COLLECTION_NAME so we use a temp coll.
        # (The module reads it at call time; we just hand the test db.)
        await ensure_indexes(db)

        score_docs = [
            {"tier": "front_lines", "stat_type": "PTS", "line": 22.5,
             "player_name": "A", "recommendation": "OVER",
             "model_projection": 24.0, "model_sigma": 5.8,
             "p_true_active": 0.62, "tp": 55.0, "edge_pct": 7.0,
             "vision_score": 80, "tier_reference_odds": -130,
             "tp_source": "devig", "canonical_key": "k1"},
            {"tier": "safe_haven", "stat_type": "REB", "line": 7.5,
             "player_name": "B", "recommendation": "OVER",
             "model_projection": 9.5, "model_sigma": 2.4,
             "p_true_active": 0.78, "tp": 62.0, "edge_pct": 16.0,
             "vision_score": 92, "tier_reference_odds": -260,
             "tp_source": "devig", "canonical_key": "k2"},
            # Should NOT be logged
            {"tier": "unqualified", "stat_type": "AST", "line": 4.5,
             "player_name": "C", "recommendation": "OVER"},
        ]
        r = await log_selected_picks(db, score_docs, sport="nba")
        assert r["inserted"] == 2
        assert r["skipped"] == 1

        # Re-run identical input → no new inserts (idempotent).
        r2 = await log_selected_picks(db, score_docs, sport="nba")
        assert r2["inserted"] == 0

        # Manually grade one
        await db[COLLECTION_NAME].update_one(
            {"player": "A", "stat": "PTS"},
            {"$set": {"hit": True, "actual": 25.0, "result": "OVER"}},
        )
        await db[COLLECTION_NAME].update_one(
            {"player": "B", "stat": "REB"},
            {"$set": {"hit": False, "actual": 6.0, "result": "UNDER"}},
        )

        overall = await query_overall(db)
        assert overall["n"] == 2
        assert overall["wins"] == 1

        by_stat = await query_by_stat(db)
        keys = {row["key"] for row in by_stat}
        assert keys == {"PTS", "REB"}

        by_edge = await query_by_edge_bucket(db)
        # PTS edge=7 → 5–10%, REB edge=16 → 15%+
        cells = {row["key"]: row for row in by_edge}
        assert cells["5–10%"]["n"] == 1
        assert cells["15%+"]["n"] == 1
    finally:
        # Clean up test database
        await cli.drop_database(test_db_name)


@pytest.mark.asyncio
async def test_log_skips_non_nba():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    r = await log_selected_picks(db, [{"tier": "safe_haven"}], sport="mlb")
    assert r == {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
