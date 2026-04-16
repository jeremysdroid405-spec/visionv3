"""
Injury Source Adapters
======================
Each adapter fetches raw injury data from one external provider
and returns it in a common intermediate format.

Adapters do NOT normalize — they just fetch and label.
The sensor normalizes and diffs.

Trust hierarchy:
  BDL = STRUCTURAL AUTHORITY (player IDs, return dates, injury detail)
  ESPN / NBA Official = TIMING AUTHORITY (faster change detection, no IDs)
"""

from services.injury_sources.bdl_source import BDLInjurySource
from services.injury_sources.espn_source import ESPNInjurySource
from services.injury_sources.nba_official_source import NBAOfficialInjurySource

__all__ = ["BDLInjurySource", "ESPNInjurySource", "NBAOfficialInjurySource"]
