"""
Injury Source Adapters
======================
Each adapter fetches raw injury data from one external provider
and returns it in a common intermediate format.

Adapters do NOT normalize — they just fetch and label.
The sensor normalizes and diffs.
"""

from services.injury_sources.bdl_source import BDLInjurySource
from services.injury_sources.espn_source import ESPNInjurySource

__all__ = ["BDLInjurySource", "ESPNInjurySource"]
