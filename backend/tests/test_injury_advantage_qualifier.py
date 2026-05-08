"""Live Injury Advantage qualifier regression tests.

Locks down the 2026-05-08 freshness fix:
  - SECONDARY_ALPHA_THRESHOLD lowered from 22 → 18.
  - ROTATION_MINUTES_THRESHOLD added at 24.0; admit on usage OR minutes.

The qualifier must:
  - admit a player with usage 18 ≤ pct < 22 (e.g. OG Anunoby @ 19.4%).
  - admit a player with usage < 18 BUT minutes_per_game ≥ 24.
  - reject a player with usage < 18 AND minutes_per_game < 24.
  - keep admitting a primary alpha (usage ≥ 28) regardless of MPG.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _patched_service():
    from services.injury_vacuum_service import InjuryVacuumService
    svc = InjuryVacuumService.__new__(InjuryVacuumService)
    svc.star_profiles_cache = {}
    svc.active_vacuums = {}
    svc.db = MagicMock()
    return svc


def _mock_master_hub_doc(player_name, usage_pct, mpg, team='NYK'):
    return {
        "display_name": player_name,
        "team": team,
        "advanced_stats": {
            "usage_percentage": usage_pct,
            "minutes_per_game": mpg,
        },
    }


def test_thresholds_constants():
    from services.injury_vacuum_service import (
        SECONDARY_ALPHA_THRESHOLD,
        ROTATION_MINUTES_THRESHOLD,
        PRIMARY_ALPHA_THRESHOLD,
    )
    assert SECONDARY_ALPHA_THRESHOLD == 18.0
    assert ROTATION_MINUTES_THRESHOLD == 24.0
    assert PRIMARY_ALPHA_THRESHOLD == 28.0


def test_admits_og_anunoby_19_4_pct_via_master_hub():
    """OG Anunoby — usage 19.4% (below old 22% gate) — must surface."""
    svc = _patched_service()
    doc = _mock_master_hub_doc("OG Anunoby", 19.4, 33.0, "NYK")

    sync_client = MagicMock()
    sync_db = MagicMock()
    sync_client.__getitem__.return_value = sync_db
    # First lookup: nba_star_usage_cache returns empty
    # Second lookup: nba_master_hub_2026 returns OG's doc
    sync_db.__getitem__.side_effect = [
        # star_usage_cache
        MagicMock(find_one=MagicMock(return_value=None)),
        # master_hub
        MagicMock(find_one=MagicMock(return_value=doc)),
    ]
    sync_client.close = MagicMock()

    with patch("pymongo.MongoClient", return_value=sync_client):
        is_star, profile = svc._is_star_player("OG Anunoby")

    assert is_star is True
    assert profile["alpha_tier"] in ("secondary", "primary", "rotation")
    assert profile["usage_rate"] == 19.4


def test_admits_low_usage_high_minutes_player():
    """A 14% usage / 28 MPG player must qualify (rotation regular)."""
    svc = _patched_service()
    doc = _mock_master_hub_doc("Glue Guy", 14.0, 28.0, "BOS")

    sync_client = MagicMock()
    sync_db = MagicMock()
    sync_client.__getitem__.return_value = sync_db
    sync_db.__getitem__.side_effect = [
        MagicMock(find_one=MagicMock(return_value=None)),
        MagicMock(find_one=MagicMock(return_value=doc)),
    ]
    sync_client.close = MagicMock()

    with patch("pymongo.MongoClient", return_value=sync_client):
        is_star, profile = svc._is_star_player("Glue Guy")

    assert is_star is True, "rotation regular should pass on minutes-axis"
    assert profile["alpha_tier"] == "rotation"


def test_rejects_bench_scrub_low_usage_low_minutes():
    """A 14% usage / 12 MPG player must NOT qualify."""
    svc = _patched_service()
    doc = _mock_master_hub_doc("Bench Scrub", 14.0, 12.0, "BOS")

    sync_client = MagicMock()
    sync_db = MagicMock()
    sync_client.__getitem__.return_value = sync_db
    sync_db.__getitem__.side_effect = [
        MagicMock(find_one=MagicMock(return_value=None)),
        MagicMock(find_one=MagicMock(return_value=doc)),
    ]
    sync_client.close = MagicMock()

    with patch("pymongo.MongoClient", return_value=sync_client):
        is_star, _ = svc._is_star_player("Bench Scrub")

    assert is_star is False


def test_admits_primary_alpha_regardless_of_minutes():
    """Luka 36.8% usage must always pass."""
    svc = _patched_service()
    doc = _mock_master_hub_doc("Luka Doncic", 36.8, 35.0, "LAL")

    sync_client = MagicMock()
    sync_db = MagicMock()
    sync_client.__getitem__.return_value = sync_db
    sync_db.__getitem__.side_effect = [
        MagicMock(find_one=MagicMock(return_value=None)),
        MagicMock(find_one=MagicMock(return_value=doc)),
    ]
    sync_client.close = MagicMock()

    with patch("pymongo.MongoClient", return_value=sync_client):
        is_star, profile = svc._is_star_player("Luka Doncic")

    assert is_star is True
    assert profile["alpha_tier"] == "primary"
