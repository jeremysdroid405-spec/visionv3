"""
Delta Engine — Upstream Isolation Guard Test
============================================
HARD INVARIANT: the delta-engine code path MUST NOT import from any
upstream-fetch / network module. Per the architecture plan §2 (tenet 4)
and §7.2 (structural upstream isolation), the delta engine operates
purely on already-ingested state in MongoDB.

This test is a CI-style grep check over the delta-path modules. It fails
the moment any delta module pulls in an upstream client, even transitively
via a sibling service.

Scope under test:
  - services/delta/**
  - routes/delta_admin.py
  - (future) services/delta_engine.py, services/upstream_sync_lock.py

  Note (2026-05-07 P0-A): `services/delta_watermarks.py` was removed
  during the SSOT cleanup that elevated `delta_dirty_queue` to be the
  sole detection source. It is no longer in scope.
"""
import os
import re
import pathlib
import pytest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Delta-path files that must NOT import upstream fetchers.
DELTA_PATH_GLOBS = [
    "services/delta/**/*.py",
    "services/delta_engine.py",            # future D4
    "services/upstream_sync_lock.py",      # future D4
    "services/pipeline/delta_steps.py",    # future D3
    "routes/delta_admin.py",
]

# Modules whose import inside a delta-path file constitutes a violation.
FORBIDDEN_UPSTREAM_MODULES = [
    "services.universal_odds_sync",
    "services.odds_sync_service",  # deleted 2026-04-22
    "services.bdl_splits_cache",
    "services.bdl_universal_sync",
    "services.bdl_comprehensive_sync",
    "services.bdl_game_logs_sync",
    "services.bdl_game_logs_sync_batched",
    "services.bdl_advanced_stats_fetcher",
    "services.bdl_enhanced_data",
    "services.nba_official_sync",
    "services.action_network_scraper",
    "services.data_scraper",
    "services.historical_data_fetcher",
    "services.historical_odds_fetcher",
    "services.mlb_cached_board_builder",
    "services.mlb_sync_engine",  # deleted 2026-04-22
    "services.mlb_deep_ingestion",
    "services.mlb_advanced_stats_sync",
    "services.mlb_headshot_sync",
    "services.master_hub_sync",
    "services.insights_sync_service",  # deleted 2026-04-22
    "services.market_catalog",
    "services.odds_api_service",
    "services.master_sync",
]


def _expand_globs(globs):
    out = []
    for g in globs:
        for p in BACKEND_ROOT.glob(g):
            if p.is_file() and p.suffix == ".py":
                out.append(p)
    return out


def test_delta_path_has_no_upstream_imports():
    delta_files = _expand_globs(DELTA_PATH_GLOBS)
    # D1 scope — at least these must exist.
    required = {
        BACKEND_ROOT / "services" / "delta" / "detector.py",
        BACKEND_ROOT / "routes" / "delta_admin.py",
    }
    present = {p.resolve() for p in delta_files}
    missing = [p for p in required if p.resolve() not in present]
    assert not missing, f"D1 delta-path files missing: {missing}"

    violations = []
    for path in delta_files:
        text = path.read_text(encoding="utf-8")
        for mod in FORBIDDEN_UPSTREAM_MODULES:
            # Match `from services.X import ...` and `import services.X`
            pattern_from = rf"^\s*from\s+{re.escape(mod)}\s+import\b"
            pattern_import = rf"^\s*import\s+{re.escape(mod)}\b"
            if re.search(pattern_from, text, re.MULTILINE) or re.search(
                pattern_import, text, re.MULTILINE
            ):
                violations.append(f"{path.relative_to(BACKEND_ROOT)} imports {mod}")

    assert not violations, (
        "Delta engine upstream-isolation invariant violated:\n  "
        + "\n  ".join(violations)
    )


def test_delta_admin_router_is_registered():
    """Confirm the delta admin router is wired into the app."""
    init_py = (BACKEND_ROOT / "routes" / "__init__.py").read_text(encoding="utf-8")
    assert "delta_admin" in init_py, (
        "routes/__init__.py does not reference delta_admin router"
    )
    assert "set_delta_admin_db" in init_py, (
        "routes/__init__.py does not wire set_delta_admin_db"
    )
