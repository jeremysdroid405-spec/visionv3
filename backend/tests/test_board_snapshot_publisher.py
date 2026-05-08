"""
Tests — board_snapshot_publisher (2026-05-08 architecture fix).

Covers the seven behavioral contracts required by the cached_board
materialization design:

  1. final-{sport}-rt IS the source used for snapshot rebuild.
  2. A delta tick with written > 0 rebuilds/publishes cached_board.
  3. A delta tick with written = 0 does NOT rebuild.
  4. Empty source does NOT wipe cached_board.
  5. master_sync uses the same publisher path as Delta Engine.
  6. cached_board freshness fields match final-{sport}-rt source
     timestamps (updated_at, source_score_max_scored_at).
  7. No independent tier assignment happens inside the publisher —
     tier values are carried verbatim from -rt rows.

All tests use an in-process mongomock DB (no real MongoDB) so the
module is safe to run in CI without network.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# Backend path set before importing services.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# --------------------------------------------------------------------------
# In-process async mongo shim (sufficient for the publisher's query
# surface: find().to_list, bulk_write(UpdateOne), update_many, count_documents).
# --------------------------------------------------------------------------
try:
    import mongomock  # type: ignore
except Exception:  # pragma: no cover
    pytest.skip("mongomock not installed", allow_module_level=True)


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length=None):
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


class _AsyncColl:
    def __init__(self, sync_coll):
        self._c = sync_coll

    def find(self, query=None, projection=None):
        docs = list(self._c.find(query or {}, projection or None))
        # strip _id if projection says so (mongomock ignores projection='_id':0 inconsistently)
        if projection and projection.get("_id") == 0:
            for d in docs:
                d.pop("_id", None)
        return _AsyncCursor(docs)

    async def find_one(self, query=None, projection=None):
        d = self._c.find_one(query or {}, projection or None)
        if d and projection and projection.get("_id") == 0:
            d.pop("_id", None)
        return d

    async def count_documents(self, query=None):
        return self._c.count_documents(query or {})

    async def insert_many(self, docs, ordered=True):
        return self._c.insert_many(docs, ordered=ordered)

    async def update_many(self, query, update):
        return self._c.update_many(query, update)

    async def bulk_write(self, ops, ordered=False):
        return self._c.bulk_write(ops, ordered=ordered)

    async def delete_many(self, query):
        return self._c.delete_many(query)


class _AsyncDB:
    def __init__(self, sync_db):
        self._d = sync_db

    def __getitem__(self, name):
        return _AsyncColl(self._d[name])

    def __getattr__(self, name):
        return _AsyncColl(self._d[name])


@pytest.fixture
def db():
    client = mongomock.MongoClient()
    return _AsyncDB(client["test_pickvision"])


# --------------------------------------------------------------------------
# Helpers for seeding test data.
# --------------------------------------------------------------------------
def _rt_row(sport, player, ck, stat, line, tier, scored_at, *, active=True):
    return {
        "sport": sport,
        "player_name": player,
        "canonical_key": ck,
        "stat_type": stat,
        "line": line,
        "tier": tier,
        "routed_tier": tier,
        "tier_reason": f"{tier}_test",
        "direction": "OVER",
        "recommendation": "OVER",
        "fair_prob": 0.62,
        "confidence": 0.7,
        "scored_at": scored_at,
        "computed_at": scored_at,
        "active": active,
        "version_tag": f"final-{sport}-rt",
        "vision_intel": f"vi-{ck}",
        "hit_rate_l5": 80.0,
        "hit_rate_l10": 75.0,
        "event_id": f"event-{player}",
    }


async def _seed_rt(db, sport, rows):
    from pymongo import InsertOne
    coll = db[f"{sport}_prop_scores"]
    await coll.bulk_write([InsertOne(dict(r)) for r in rows])


async def _seed_cb(db, sport, player_docs):
    from pymongo import InsertOne
    coll = db[f"{sport}_cached_board"]
    await coll.bulk_write([InsertOne(dict(d)) for d in player_docs])


# --------------------------------------------------------------------------
# (1) final-{sport}-rt IS the source used for snapshot rebuild.
# (7) No independent tier assignment — tier carried verbatim from -rt.
# --------------------------------------------------------------------------
def test_publisher_rebuilds_from_rt_and_carries_tier_verbatim(db):
    from services.board_snapshot_publisher import publish_board_snapshot

    scored_at = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)

    async def run():
        # Seed three -rt props for two players and one decoy prop on a
        # DIFFERENT version_tag to prove the publisher ignores it.
        await _seed_rt(db, "nba", [
            _rt_row("nba", "LeBron James", "nba|e1|LeBron James|PTS|25.5|OVER", "PTS", 25.5, "safe_haven", scored_at),
            _rt_row("nba", "LeBron James", "nba|e1|LeBron James|AST|7.5|OVER",  "AST", 7.5, "front_lines", scored_at - timedelta(seconds=30)),
            _rt_row("nba", "Luka Doncic", "nba|e2|Luka Doncic|PTS|30.5|OVER",   "PTS", 30.5, "war_zone",   scored_at - timedelta(seconds=60)),
        ])
        # Decoy: BASELINE tag should be ignored.
        await _seed_rt(db, "nba", [
            {**_rt_row("nba", "Ignored Player", "nba|decoy|Ignored|PTS|1|OVER", "PTS", 1, "oracle_apex",
                       scored_at + timedelta(hours=1)),
             "version_tag": "final-nba"},
        ])

        res = await publish_board_snapshot(db, "nba")
        assert res["preserved"] is False
        assert res["upserted_players"] == 2
        assert res["total_rt_active_props"] == 3

        docs = await db["nba_cached_board"].find({}, {"_id": 0}).to_list(None)
        by_player = {d["player_name"]: d for d in docs}

        # Tier carried verbatim per-prop, no mutation.
        lebron_tiers = {p["stat_type"]: p["tier"] for p in by_player["LeBron James"]["props"]}
        assert lebron_tiers == {"PTS": "safe_haven", "AST": "front_lines"}
        luka = by_player["Luka Doncic"]["props"]
        assert [p["tier"] for p in luka] == ["war_zone"]

        # Decoy baseline-tag row is NOT present.
        assert "Ignored Player" not in by_player

        # version_tag at doc level is the cb-own tag, not the rt tag.
        assert by_player["LeBron James"]["version_tag"] == "nba-cb-v1"
        assert by_player["LeBron James"]["source_version_tag"] == "final-nba-rt"

    asyncio.run(run())


# --------------------------------------------------------------------------
# (2) Delta tick with written > 0 rebuilds/publishes cached_board.
# (3) Delta tick with written = 0 does NOT rebuild.
# --------------------------------------------------------------------------
def test_delta_step_rebuilds_only_when_written_gt_zero(db):
    from services.pipeline.delta_steps import PublishBoardSnapshotStep

    step = PublishBoardSnapshotStep()

    async def run():
        # Seed -rt so the publisher, if called, would succeed.
        scored_at = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
        await _seed_rt(db, "nba", [
            _rt_row("nba", "Jalen Brunson", "nba|e3|Jalen Brunson|AST|7.5|OVER", "AST", 7.5, "safe_haven", scored_at),
        ])

        # Case A: written = 0, retired_modified = 0 → SKIP publisher.
        ctx = {"rescore_result": {"written": 0}, "rebalance_result": {"retired": {"modified": 0}}}
        result_skip = await step.run("nba", db, ctx)
        assert result_skip["skipped"] is True
        assert result_skip["reason"] == "no_rt_writes_this_tick"
        cb_after_skip = await db["nba_cached_board"].count_documents({})
        assert cb_after_skip == 0  # publisher never ran

        # Case B: written = 2 → publisher runs.
        ctx2 = {"rescore_result": {"written": 2}, "rebalance_result": {"retired": {"modified": 0}}}
        result_run = await step.run("nba", db, ctx2)
        assert result_run.get("skipped") is not True
        assert result_run["publisher"]["upserted_players"] == 1

        # Case C: upstream lock held → SKIP regardless of written count.
        ctx3 = {"abort_remaining_steps": True, "rescore_result": {"written": 99}}
        result_lock = await step.run("nba", db, ctx3)
        assert result_lock["skipped"] is True
        assert result_lock["reason"] == "upstream_lock_held"

    asyncio.run(run())


# --------------------------------------------------------------------------
# (4) Empty source does NOT wipe cached_board.
# --------------------------------------------------------------------------
def test_empty_rt_source_preserves_cached_board(db):
    from services.board_snapshot_publisher import publish_board_snapshot

    async def run():
        # Pre-existing cached_board doc with enrichment and props.
        prior_ts = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        await _seed_cb(db, "nba", [{
            "player_name": "Preserved Player",
            "sport": "nba",
            "photo_url": "https://ex.com/p.png",
            "team": "NYK",
            "injury_status": "Healthy",
            "props": [{"canonical_key": "x", "line": 1.0, "tier": "front_lines"}],
            "props_count": 1,
            "updated_at": prior_ts,
            "last_publish_ts": prior_ts,
            "version_tag": "nba-cb-v1",
        }])

        # No -rt rows seeded → zero-source guard must trigger.
        res = await publish_board_snapshot(db, "nba")
        assert res["preserved"] is True
        assert res["reason"] == "empty_source"
        assert res["upserted_players"] == 0
        assert res["emptied_stale_players"] == 0

        # Confirm doc unchanged.
        doc = await db["nba_cached_board"].find_one({"player_name": "Preserved Player"}, {"_id": 0})
        assert doc["photo_url"] == "https://ex.com/p.png"
        assert doc["injury_status"] == "Healthy"
        assert doc["props_count"] == 1
        # timestamp not advanced (mongomock strips tzinfo on roundtrip;
        # compare naive for equality).
        assert doc["updated_at"].replace(tzinfo=None) == prior_ts.replace(tzinfo=None)

    asyncio.run(run())


# --------------------------------------------------------------------------
# (5) master_sync uses the same publisher path — verified via import
# contract + step key name. (Integration-free: we prove master_sync
# references exactly `publish_board_snapshot` and no longer references
# `stamp_cached_board_freshness`.)
# --------------------------------------------------------------------------
def test_master_sync_uses_publish_board_snapshot():
    import re
    path = os.path.join(_BACKEND, "services", "master_sync.py")
    src = open(path, "r").read()
    # Step 7 identifier + publisher import + step-metrics key are all present.
    assert "from services.board_snapshot_publisher import publish_board_snapshot" in src
    assert "7_cached_board_snapshot_publish" in src
    # The old freshness-stamp call has been removed from master_sync.
    assert "from services.board_freshness import stamp_cached_board_freshness" not in src
    assert "7_cached_board_freshness_stamp" not in src


# --------------------------------------------------------------------------
# (6) cached_board freshness fields match -rt source timestamps.
# --------------------------------------------------------------------------
def test_freshness_fields_match_rt_source(db):
    from services.board_snapshot_publisher import publish_board_snapshot

    async def run():
        ts_old = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)
        ts_new = datetime(2026, 5, 8, 17, 30, tzinfo=timezone.utc)
        # Two props with different scored_at; max should win.
        await _seed_rt(db, "mlb", [
            _rt_row("mlb", "Mookie Betts", "mlb|e1|Mookie Betts|Hits|1.5|OVER", "Hits", 1.5, "safe_haven", ts_old),
            _rt_row("mlb", "Aaron Judge",   "mlb|e2|Aaron Judge|HR|0.5|OVER",    "HR",   0.5, "front_lines", ts_new),
        ])

        publish_ts = datetime(2026, 5, 8, 17, 31, tzinfo=timezone.utc)
        res = await publish_board_snapshot(db, "mlb", now=publish_ts)
        assert res["preserved"] is False
        assert res["source_score_max_scored_at"] == ts_new.isoformat()

        # Every upserted doc carries the matching stamp.
        docs = await db["mlb_cached_board"].find({}, {"_id": 0}).to_list(None)
        assert len(docs) == 2
        for d in docs:
            # mongomock strips tzinfo on roundtrip — compare naive.
            assert d["updated_at"].replace(tzinfo=None) == publish_ts.replace(tzinfo=None)
            assert d["last_publish_ts"].replace(tzinfo=None) == publish_ts.replace(tzinfo=None)
            assert d["source_score_max_scored_at"].replace(tzinfo=None) == ts_new.replace(tzinfo=None)
            assert d["source_version_tag"] == "final-mlb-rt"

    asyncio.run(run())


# --------------------------------------------------------------------------
# Bonus: stale-player emptying preserves enrichment (never delete).
# --------------------------------------------------------------------------
def test_stale_player_is_emptied_not_deleted(db):
    from services.board_snapshot_publisher import publish_board_snapshot

    async def run():
        prior_ts = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        # Pre-existing doc with enrichment + a prop.
        await _seed_cb(db, "nba", [{
            "player_name": "Stale Player",
            "sport": "nba",
            "photo_url": "https://ex.com/stale.png",
            "team": "BOS",
            "props": [{"canonical_key": "old", "line": 9.5, "tier": "safe_haven"}],
            "props_count": 1,
            "updated_at": prior_ts,
        }])

        scored_at = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
        await _seed_rt(db, "nba", [
            _rt_row("nba", "Fresh Player", "nba|eX|Fresh Player|PTS|10.5|OVER", "PTS", 10.5, "front_lines", scored_at),
        ])

        res = await publish_board_snapshot(db, "nba")
        assert res["upserted_players"] == 1
        assert res["emptied_stale_players"] == 1

        stale = await db["nba_cached_board"].find_one({"player_name": "Stale Player"}, {"_id": 0})
        assert stale is not None  # NOT deleted
        assert stale["photo_url"] == "https://ex.com/stale.png"
        assert stale["team"] == "BOS"
        assert stale["props"] == []
        assert stale["props_count"] == 0

    asyncio.run(run())


# --------------------------------------------------------------------------
# Bonus: ingestion-layer fields are preserved across a rebuild via
# canonical_key merge (the publisher must NOT drop bookmaker prices,
# event_id, commence_time, etc. that the existing cached_board carries).
# --------------------------------------------------------------------------
def test_ingestion_fields_preserved_via_canonical_key_merge(db):
    from services.board_snapshot_publisher import publish_board_snapshot

    async def run():
        prior_ts = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        ck = "nba|e7|Kyrie Irving|PTS|22.5|OVER"
        # Pre-existing prop with ingestion fields.
        await _seed_cb(db, "nba", [{
            "player_name": "Kyrie Irving",
            "sport": "nba",
            "photo_url": "https://ex.com/k.png",
            "props": [{
                "canonical_key": ck,
                "line": 22.5,
                "dk_odds": -115,
                "fd_odds": -110,
                "event_id": "e7",
                "commence_time": "2026-05-08T23:00:00Z",
                "home_team": "DAL",
                "away_team": "OKC",
                # Scoring fields that should be overwritten by -rt:
                "tier": "war_zone",
                "recommendation": "UNDER",
            }],
            "props_count": 1,
            "updated_at": prior_ts,
        }])

        scored_at = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
        rt = _rt_row("nba", "Kyrie Irving", ck, "PTS", 22.5, "safe_haven", scored_at)
        rt["event_id"] = "e7"  # match existing cached_board's event_id
        await _seed_rt(db, "nba", [rt])

        res = await publish_board_snapshot(db, "nba")
        assert res["upserted_players"] == 1

        doc = await db["nba_cached_board"].find_one({"player_name": "Kyrie Irving"}, {"_id": 0})
        assert doc["photo_url"] == "https://ex.com/k.png"  # preserved
        prop = doc["props"][0]
        # Ingestion-layer fields NOT on prop_scores are preserved.
        assert prop["dk_odds"] == -115
        assert prop["fd_odds"] == -110
        assert prop["home_team"] == "DAL"
        assert prop["away_team"] == "OKC"
        assert prop["commence_time"] == "2026-05-08T23:00:00Z"
        # event_id is on -rt docs → -rt authoritative (matches here).
        assert prop["event_id"] == "e7"
        # -rt overrides scoring fields.
        assert prop["tier"] == "safe_haven"
        assert prop["recommendation"] == "OVER"

    asyncio.run(run())
