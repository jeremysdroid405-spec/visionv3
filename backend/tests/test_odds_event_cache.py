"""Unit tests for the per-event odds cache helpers.

Covers hash stability, TTL freshness, the SyncModeTag contextvar and
how `current_sync_mode()` interleaves with CallerTag.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from services import odds_event_props_cache as evc
from services import odds_api_budget as ob


class TestHashStability:
    def test_same_props_same_hash(self):
        a = [{"player_name": "Aaron Judge", "line": 1.5, "odds": -110}]
        b = [{"player_name": "Aaron Judge", "line": 1.5, "odds": -110}]
        assert evc.hash_props(a) == evc.hash_props(b)

    def test_key_order_doesnt_matter(self):
        a = [{"player_name": "X", "line": 2.5, "odds": -120}]
        b = [{"odds": -120, "player_name": "X", "line": 2.5}]
        assert evc.hash_props(a) == evc.hash_props(b)

    def test_value_change_changes_hash(self):
        a = [{"player_name": "X", "line": 2.5, "odds": -120}]
        b = [{"player_name": "X", "line": 2.5, "odds": -119}]
        assert evc.hash_props(a) != evc.hash_props(b)

    def test_extra_field_changes_hash(self):
        a = [{"player_name": "X"}]
        b = [{"player_name": "X", "extra": 1}]
        assert evc.hash_props(a) != evc.hash_props(b)

    def test_datetime_serializes(self):
        ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        a = [{"player_name": "X", "updated_at": ts}]
        # Should not raise.
        h = evc.hash_props(a)
        assert isinstance(h, str) and len(h) == 64


class TestFreshness:
    def test_none_record_not_fresh(self):
        assert evc.is_fresh(None) is False

    def test_no_timestamp_not_fresh(self):
        assert evc.is_fresh({"props": []}) is False

    def test_recent_is_fresh(self):
        now = datetime.now(timezone.utc)
        rec = {"last_synced_at": now - timedelta(seconds=60)}
        assert evc.is_fresh(rec, ttl_seconds=600) is True

    def test_stale_not_fresh(self):
        now = datetime.now(timezone.utc)
        rec = {"last_synced_at": now - timedelta(seconds=601)}
        assert evc.is_fresh(rec, ttl_seconds=600) is False

    def test_boundary_inclusive(self):
        now = datetime.now(timezone.utc)
        # exactly at TTL is NOT fresh (strict <)
        rec = {"last_synced_at": now - timedelta(seconds=600)}
        assert evc.is_fresh(rec, ttl_seconds=600, now=now) is False
        # one second under is fresh
        rec2 = {"last_synced_at": now - timedelta(seconds=599)}
        assert evc.is_fresh(rec2, ttl_seconds=600, now=now) is True


class TestSyncModeTag:
    def test_default_is_delta(self):
        # the contextvar default — no scope active
        assert ob.current_sync_mode() == "delta"

    def test_full_scope(self):
        with ob.SyncModeTag("full"):
            assert ob.current_sync_mode() == "full"
        assert ob.current_sync_mode() == "delta"

    def test_delta_scope(self):
        with ob.SyncModeTag("delta"):
            assert ob.current_sync_mode() == "delta"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            ob.SyncModeTag("partial")

    def test_nested_scope(self):
        with ob.SyncModeTag("full"):
            with ob.SyncModeTag("delta"):
                assert ob.current_sync_mode() == "delta"
            assert ob.current_sync_mode() == "full"

    def test_independent_of_caller_tag(self):
        # CallerTag and SyncModeTag are separate axes.
        with ob.CallerTag("scheduled_cron"), ob.SyncModeTag("delta"):
            assert ob.current_caller() == "scheduled_cron"
            assert ob.current_sync_mode() == "delta"
