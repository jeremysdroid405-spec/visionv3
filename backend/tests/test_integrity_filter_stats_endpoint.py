"""
Integrity-filter stats endpoint — seeded pytest (2026-05-17)
============================================================
Validates ``GET /api/admin/odds/integrity-filter-stats`` against
known-shape fixtures inserted directly into ``mlb_prop_scores`` and
``mlb_live_props`` so the rollup math, breakdown buckets, and
dropped-prop scan are all asserted deterministically.

The test is skipped when Mongo is unreachable (mirrors the pattern in
``test_score_document_parity.py``). It runs against the live local
Mongo defined by ``MONGO_URL`` / ``DB_NAME`` in ``backend/.env`` and
inserts/removes its own fixtures so it leaves the DB clean.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest


def _fixture_canon(label: str) -> str:
    return f"mlb|integrity-stats-fixture-{label}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def _mongo_config():
    from dotenv import load_dotenv
    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not set")
    return mongo_url, db_name


def _seed_docs() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    """Build (score_docs, live_docs, score_cks, live_cks) fixtures."""
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    ck_s1 = _fixture_canon("s1")
    ck_s2 = _fixture_canon("s2")
    ck_l1 = _fixture_canon("l1-dropped")
    ck_l2 = _fixture_canon("l2-survivor")
    score_docs = [
        # Affected prop #1: 2 ejected quotes (DK + FD), Total Bases.
        {
            "canonical_key": ck_s1,
            "version_tag": "test-integrity-stats",
            "sport": "mlb",
            "player_name": "Test Player A",
            "stat_type": "Total Bases",
            "line": 0.5,
            "market_class": "alternate",
            "integrity_filter_applied": True,
            "excluded_book_quotes": [
                {"book": "draftkings", "odds": 1500, "line": 0.5,
                 "market_class": "alternate", "reason": "mlb_alt_05line_long_odds"},
                {"book": "fanduel",    "odds": 900,  "line": 0.5,
                 "market_class": "alternate", "reason": "mlb_alt_05line_long_odds"},
            ],
            "computed_at": ts,
            "scored_at":   ts,
            "event_id":    "test-event-1",
        },
        # Affected prop #2: 1 ejected quote (DK), Stolen Bases.
        {
            "canonical_key": ck_s2,
            "version_tag":  "test-integrity-stats",
            "sport":        "mlb",
            "player_name":  "Test Player B",
            "stat_type":    "Stolen Bases",
            "line":         0.5,
            "market_class": "alternate",
            "integrity_filter_applied": True,
            "excluded_book_quotes": [
                {"book": "draftkings", "odds": 700, "line": 0.5,
                 "market_class": "alternate", "reason": "mlb_alt_05line_long_odds"},
            ],
            "computed_at": ts,
            "scored_at":   ts,
            "event_id":    "test-event-2",
        },
    ]
    live_docs = [
        # Dropped: every alt-bucket quote >= +500.
        {
            "canonical_key": ck_l1,
            "sport":         "mlb",
            "player_name":   "Test Player C",
            "stat_type":     "Hits",
            "line":          0.5,
            "market_class":  "alternate",
            "all_odds_alternate": {"draftkings": 1200, "fanduel": 900, "betmgm": 750},
            "fetched_at":    now,
            "event_id":      "test-event-3",
        },
        # Survivor: at least one alt quote below threshold — NOT dropped.
        {
            "canonical_key": ck_l2,
            "sport":         "mlb",
            "player_name":   "Test Player D",
            "stat_type":     "Hits",
            "line":          0.5,
            "market_class":  "alternate",
            "all_odds_alternate": {"draftkings": 1200, "fanduel": -120},
            "fetched_at":    now,
            "event_id":      "test-event-4",
        },
    ]
    return score_docs, live_docs, [ck_s1, ck_s2], [ck_l1, ck_l2]


@pytest.mark.asyncio
async def test_integrity_filter_stats_seeded_rollups(_mongo_config) -> None:
    """End-to-end: seed → call endpoint → assert rollups → cleanup."""
    mongo_url, db_name = _mongo_config
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except Exception as exc:
        pytest.skip(f"motor not importable: {exc}")
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
    db = client[db_name]
    try:
        await client.admin.command("ping")
    except Exception as exc:
        pytest.skip(f"Mongo unreachable: {exc}")

    score_docs, live_docs, score_cks, live_cks = _seed_docs()

    await db["mlb_prop_scores"].insert_many(score_docs)
    await db["mlb_live_props"].insert_many(live_docs)
    try:
        # Drive the endpoint via the real HTTP path so the wiring is
        # exercised exactly as production calls it. Use the local
        # supervisor-managed backend port + a sync httpx client run in
        # a worker thread (the async client flakes on the local loop).
        import asyncio as _asyncio
        import httpx
        def _call() -> Any:
            with httpx.Client(timeout=10.0) as c:
                resp = c.get(
                    "http://localhost:8001/api/admin/odds/integrity-filter-stats",
                    params={"sport": "mlb", "hours": 24, "top_n": 25},
                )
                return resp.status_code, resp.text, resp.json() if resp.status_code == 200 else None
        status, text, data = await _asyncio.to_thread(_call)
        assert status == 200, text

        # Affected props + excluded quotes.
        assert data["affected_props_count"] >= 2
        assert data["total_excluded_quotes"] >= 3
        # By sportsbook: DK contributed 2 ejections, FD 1.
        bbk = data["excluded_quotes_by_sportsbook"]
        assert bbk.get("draftkings", 0) >= 2
        assert bbk.get("fanduel", 0) >= 1
        # By stat family: at least Total Bases + Stolen Bases present.
        bsf = data["excluded_quotes_by_stat_family"]
        assert bsf.get("Total Bases", 0) >= 2
        assert bsf.get("Stolen Bases", 0) >= 1
        # By market class: every fixture row is "alternate".
        assert data["excluded_quotes_by_market_class"].get("alternate", 0) >= 3
        # Top examples carry full shape.
        ex = data["top_excluded_quote_examples"]
        assert len(ex) >= 3
        keys_required = {
            "canonical_key", "player_name", "stat_type", "line",
            "market_class", "book", "odds", "reason", "computed_at",
        }
        for entry in ex[:3]:
            assert keys_required.issubset(set(entry.keys()))

        # Dropped prop count: ck_l1 should count, ck_l2 should not.
        assert data["dropped_props_count"] >= 1
        dropped_cks = {d["canonical_key"] for d in data["dropped_prop_examples"]}
        assert any(ck in dropped_cks for ck in live_cks[:1]), \
            "Dropped-set must include the all-ejected fixture"
        assert live_cks[1] not in dropped_cks, \
            "Survivor fixture must NOT appear in dropped-set"

        # Constant metadata.
        assert data["rule"] == "mlb_alt_05line_long_odds"
        assert data["threshold_american_odds"] == 500
        assert data["window_hours"] == 24
    finally:
        await db["mlb_prop_scores"].delete_many({"canonical_key": {"$in": score_cks}})
        await db["mlb_live_props"].delete_many({"canonical_key": {"$in": live_cks}})
