"""
Rolling Cache — File Readers (post-D7 retirement, 2026-04-21)
==============================================================
This module previously housed the legacy "Rolling Cache" system:
  * `RollingCacheManager` class        → DELETED in D7 (zero callers)
  * `DeltaManager` class               → DELETED in D7 (replaced by
                                          `services/delta_engine.py`)
  * `run_cache_refresh_loop` coroutine → DELETED in D7 (90s overlay
                                          loop superseded by D5's
                                          continuous `DeltaEngine.run_forever`)

What remains is a narrow, read-only file-cache surface consumed by two
active routes that still merge the on-disk `{sport}_master_active_cache.json`
payload into their responses:

  1. `routes/intel_cache.py`          (/api/v3/intel-cache/*)
  2. `routes/ferrari_tiers.py`        (MLB player-detail vision-intel merge)

The cache files are NO LONGER refreshed by a background loop. They are
whatever the last full sync left on disk. This is intentional for D7 —
if either downstream reader becomes functionally broken we will delete
the reader too, not resurrect a background loop.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Cache file paths (unchanged from the pre-D7 module).
CACHE_DIR = "/app/backend/data"
NBA_CACHE_FILE = os.path.join(CACHE_DIR, "nba_master_active_cache.json")
MLB_CACHE_FILE = os.path.join(CACHE_DIR, "mlb_master_active_cache.json")


def get_cached_props(sport: str = "NBA") -> Dict[str, Any]:
    """Return the on-disk cache payload for `sport` (NO DB hit)."""
    cache_file = NBA_CACHE_FILE if sport.upper() == "NBA" else MLB_CACHE_FILE

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
            return {
                "success": True,
                "sport": sport,
                "prop_count": data.get("prop_count", 0),
                "last_updated": data.get("last_updated"),
                "props": data.get("props", {}),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[CACHE_API] Error reading cache for {sport}: {exc}")

    return {
        "success": False,
        "sport": sport,
        "prop_count": 0,
        "props": {},
        "error": "Cache not available",
    }


def get_cached_prop_by_id(prop_id: str, sport: str = "NBA") -> Optional[Dict]:
    """Get a single prop from the on-disk cache by ID."""
    cache_data = get_cached_props(sport)
    if cache_data.get("success"):
        return cache_data.get("props", {}).get(prop_id)
    return None


__all__ = ["get_cached_props", "get_cached_prop_by_id"]
