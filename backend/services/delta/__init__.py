"""PropVision Delta Engine — package root.

See /tmp/delta_engine_architecture_plan.md for the full contract.

HARD INVARIANT (enforced by tests/test_delta_upstream_isolation.py):
  Modules under `services.delta.*` MUST NOT import from any upstream-fetch
  module (universal_odds_sync, bdl_*, nba_com_*, etc). The delta engine
  operates purely on already-ingested state in `{sport}_live_props` and
  writes only to `{sport}_prop_scores`.
"""
