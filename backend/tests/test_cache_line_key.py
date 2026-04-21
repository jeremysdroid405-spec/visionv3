"""
Tests for routes/ferrari_tiers.py::overlay_enrichment_cache

Locks the cache lookup key to (player|stat|line|recommendation) so the
Jarrett Allen PTS 9.5 vs 11.5 narrative cross-contamination bug
(2026-04-21) can't regress.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from routes.ferrari_tiers import overlay_enrichment_cache


def _write_cache(path: str, props: dict) -> None:
    with open(path, "w") as f:
        json.dump({"props": props}, f)


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch):
    """Point the enrichment cache at a temp file + reset in-memory dicts."""
    from routes import ferrari_tiers
    monkeypatch.setattr(ferrari_tiers, "_enrichment_cache", {})
    monkeypatch.setattr(ferrari_tiers, "_enrichment_cache_mtime", {})
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_line_disambiguates_cache_hits(tmp_cache_dir, monkeypatch):
    """Two cache entries for the same player+stat at different lines must
    not cross-contaminate. This is the exact Jarrett Allen bug."""
    cache_path = str(tmp_cache_dir / "nba_master_active_cache.json")
    # Point at the temp file
    import routes.ferrari_tiers as ft
    monkeypatch.setattr(ft, "__file__", ft.__file__)  # keep layout
    # Use the same path format overlay uses: /app/backend/data/<sport>_...
    real_path = f"/app/backend/data/nba_master_active_cache.json"
    # Back up real cache temporarily
    original_exists = os.path.exists(real_path)
    backup = None
    if original_exists:
        backup = real_path + ".test_backup"
        os.rename(real_path, backup)
    try:
        _write_cache(real_path, {
            "jarrett_allen|pts|9.5|pp": {
                "player_name": "Jarrett Allen",
                "stat_type": "PTS",
                "line": 9.5,
                "recommendation": "Over",
                "payload_hash": "hash-9-5",
                "vision_intel": "NARRATIVE-FOR-9.5: 90% L10",
            },
            "jarrett_allen|pts|11.5|pp": {
                "player_name": "Jarrett Allen",
                "stat_type": "PTS",
                "line": 11.5,
                "recommendation": "Over",
                "payload_hash": "hash-11-5",
                "vision_intel": "NARRATIVE-FOR-11.5: 70% L10",
            },
        })

        picks = [
            {"player_name": "Jarrett Allen", "stat_type": "PTS",
             "line": 9.5,  "recommendation": "Over", "vision_intel": ""},
            {"player_name": "Jarrett Allen", "stat_type": "PTS",
             "line": 11.5, "recommendation": "Over", "vision_intel": ""},
        ]
        overlay_enrichment_cache(picks, sport="nba")

        assert picks[0]["vision_intel"] == "NARRATIVE-FOR-9.5: 90% L10", (
            f"9.5 pick received wrong narrative: {picks[0]['vision_intel']!r}"
        )
        assert picks[1]["vision_intel"] == "NARRATIVE-FOR-11.5: 70% L10"
    finally:
        if os.path.exists(real_path):
            os.remove(real_path)
        if backup and os.path.exists(backup):
            os.rename(backup, real_path)


def test_recommendation_disambiguates_over_under(tmp_cache_dir, monkeypatch):
    """Over and Under narratives for the same line must not collide."""
    real_path = "/app/backend/data/nba_master_active_cache.json"
    original_exists = os.path.exists(real_path)
    backup = None
    if original_exists:
        backup = real_path + ".test_backup"
        os.rename(real_path, backup)
    import routes.ferrari_tiers as ft
    monkeypatch.setattr(ft, "_enrichment_cache", {})
    monkeypatch.setattr(ft, "_enrichment_cache_mtime", {})
    try:
        _write_cache(real_path, {
            "lebron|pts|24.5|over": {
                "player_name": "LeBron James", "stat_type": "PTS",
                "line": 24.5, "recommendation": "Over",
                "payload_hash": "h-over",
                "vision_intel": "OVER narrative",
            },
            "lebron|pts|24.5|under": {
                "player_name": "LeBron James", "stat_type": "PTS",
                "line": 24.5, "recommendation": "Under",
                "payload_hash": "h-under",
                "vision_intel": "UNDER narrative",
            },
        })
        picks = [
            {"player_name": "LeBron James", "stat_type": "PTS",
             "line": 24.5, "recommendation": "Under", "vision_intel": ""},
        ]
        overlay_enrichment_cache(picks, sport="nba")
        assert picks[0]["vision_intel"] == "UNDER narrative"
    finally:
        if os.path.exists(real_path):
            os.remove(real_path)
        if backup and os.path.exists(backup):
            os.rename(backup, real_path)


def test_legacy_line_none_entries_are_orphaned(tmp_cache_dir, monkeypatch):
    """Legacy entries with line=None can't match any real live pick
    (which always has a numeric line)."""
    real_path = "/app/backend/data/nba_master_active_cache.json"
    original_exists = os.path.exists(real_path)
    backup = None
    if original_exists:
        backup = real_path + ".test_backup"
        os.rename(real_path, backup)
    import routes.ferrari_tiers as ft
    monkeypatch.setattr(ft, "_enrichment_cache", {})
    monkeypatch.setattr(ft, "_enrichment_cache_mtime", {})
    try:
        _write_cache(real_path, {
            "Jarrett Allen_PTS_11.5": {
                "player_name": "Jarrett Allen", "stat_type": "PTS",
                "line": None, "recommendation": None,
                "payload_hash": "legacy-hash",
                "vision_intel": "LEGACY 70% narrative — must NOT overlay",
            },
        })
        picks = [
            {"player_name": "Jarrett Allen", "stat_type": "PTS",
             "line": 9.5, "recommendation": "Over", "vision_intel": ""},
        ]
        overlay_enrichment_cache(picks, sport="nba")
        assert picks[0]["vision_intel"] == "", (
            "Legacy line=None cache entries must be orphaned under the new key"
        )
    finally:
        if os.path.exists(real_path):
            os.remove(real_path)
        if backup and os.path.exists(backup):
            os.rename(backup, real_path)
