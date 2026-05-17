"""NBA sport adapter — SKELETON ONLY.

Implementation deferred until MLB Phase 2c+ harness is stable.
Each method raises NotImplementedError with a TODO pointer.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from services.replay.providers.sport_adapter import (
    SportReplayAdapter, SportFixedConfig,
)

_NBA_PIPELINE_FILES = (
    # TODO Phase 5+: enumerate NBA production scoring files
    "services/scoring/recompute.py",
    "services/scoring/scoring_stack.py",
    "services/scoring/tier_evaluator.py",
    "services/scoring/gates/engine.py",
    "services/scoring/gates/thresholds.py",
    # NBA-specific model file path TBD
)


class NBAReplayAdapter(SportReplayAdapter):
    SPORT = "nba"

    @property
    def config(self) -> SportFixedConfig:
        return SportFixedConfig(
            sport="nba",
            odds_collection="nba_historical_alt_odds_raw",          # NOT YET BUILT
            feature_cache_collection="nba_replay_feature_cache",    # NOT YET BUILT
            master_hub_collection="nba_master_hub_2026",
            game_log_array_field="bdl_game_logs",
            default_pipeline_files=_NBA_PIPELINE_FILES,
            tier_short_codes={
                "safe_haven": "SH",
                "front_lines": "FL",
                "war_zone": "WZ",
            },
        )

    def normalize_stat_family(self, market: str,
                                replay_family: Optional[str] = None) -> str:
        raise NotImplementedError("NBA adapter — Phase 5+")

    def list_stat_families(self) -> List[str]:
        raise NotImplementedError("NBA adapter — Phase 5+")

    def load_model(self) -> Any:
        raise NotImplementedError("NBA adapter — Phase 5+")

    def predict(self, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("NBA adapter — Phase 5+")

    async def fetch_actuals(self, *, game_date: str) -> Dict[str, Dict[str, float]]:
        raise NotImplementedError("NBA adapter — Phase 5+")

    def grade_outcome(self, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("NBA adapter — Phase 5+")

    async def resolve_opp_pitcher(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None   # N/A for NBA

    async def resolve_opposing_lineup(self, **kwargs: Any) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError("NBA adapter — Phase 5+")
