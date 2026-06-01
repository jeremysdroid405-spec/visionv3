"""Unit tests for services.odds_api_budget (Odds API call guard)."""
from __future__ import annotations
import importlib
import os
import sys

import pytest

sys.path.insert(0, "/app/backend")
import services.odds_api_budget as ob  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """Ensure each test starts from a clean budget state."""
    ob._hour_calls.clear()
    ob._by_caller.clear()
    ob._by_sport.clear()
    ob._by_endpoint.clear()
    ob._total_today = 0
    ob._today_ymd = ""
    ob._blocked_total = 0
    yield


class TestFullSyncAllowList:
    def test_startup_allowed(self):
        ob.assert_full_sync_allowed("startup")

    def test_manual_admin_allowed(self):
        ob.assert_full_sync_allowed("manual_admin")

    def test_scheduled_cron_allowed(self):
        ob.assert_full_sync_allowed("scheduled_cron")

    def test_bootstrap_allowed(self):
        ob.assert_full_sync_allowed("bootstrap_script")

    def test_watcher_blocked(self):
        with pytest.raises(ob.FullSyncNotAllowed):
            ob.assert_full_sync_allowed("adaptive_sync_watcher")

    def test_unknown_blocked(self):
        with pytest.raises(ob.FullSyncNotAllowed):
            ob.assert_full_sync_allowed("unknown")

    def test_empty_blocked(self):
        with pytest.raises(ob.FullSyncNotAllowed):
            ob.assert_full_sync_allowed("")


class TestBudgetCounter:
    def test_increment_increases_hour_count(self):
        ob.check_and_increment(caller="x", sport="nba", endpoint="events")
        assert ob.hourly_count() == 1

    def test_per_caller_split(self):
        for _ in range(3):
            ob.check_and_increment(caller="a", sport="nba", endpoint="events")
        ob.check_and_increment(caller="b", sport="nba", endpoint="events")
        snap = ob.snapshot()
        assert snap["by_caller"]["a"] == 3
        assert snap["by_caller"]["b"] == 1

    def test_per_endpoint_split(self):
        ob.check_and_increment(caller="x", sport="nba", endpoint="events")
        ob.check_and_increment(caller="x", sport="nba", endpoint="event_odds")
        ob.check_and_increment(caller="x", sport="nba", endpoint="event_odds")
        snap = ob.snapshot()
        assert snap["by_endpoint"]["events"] == 1
        assert snap["by_endpoint"]["event_odds"] == 2

    def test_hard_cap_blocks_over_limit(self, monkeypatch):
        # Temporarily clamp the limit to a tiny number.
        monkeypatch.setattr(ob, "MAX_CALLS_PER_HOUR", 3, raising=True)
        for _ in range(3):
            ob.check_and_increment(caller="x", sport="nba", endpoint="events")
        with pytest.raises(ob.OddsApiBudgetExceeded):
            ob.check_and_increment(caller="x", sport="nba", endpoint="events")
        assert ob._blocked_total == 1


class TestKillSwitch:
    def test_kill_switch_blocks_all_callers(self, monkeypatch):
        monkeypatch.setattr(ob, "KILL_SWITCH", True, raising=True)
        with pytest.raises(ob.OddsApiBudgetExceeded):
            ob.check_and_increment(
                caller="manual_admin", sport="nba", endpoint="events")
        assert ob._blocked_total == 1


class TestCallerTag:
    def test_default_unknown(self):
        assert ob.current_caller() == "unknown"

    def test_context_scope(self):
        with ob.CallerTag("scheduled_cron"):
            assert ob.current_caller() == "scheduled_cron"
        assert ob.current_caller() == "unknown"

    def test_nested_scope(self):
        with ob.CallerTag("outer"):
            with ob.CallerTag("inner"):
                assert ob.current_caller() == "inner"
            assert ob.current_caller() == "outer"
