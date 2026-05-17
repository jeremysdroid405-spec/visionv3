"""NFL sport adapter — SKELETON ONLY.

Implementation deferred until MLB Phase 2c+ harness is stable.
Each method raises NotImplementedError with a TODO pointer.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from services.replay.providers.sport_adapter import (
    SportReplayAdapter, SportFixedConfig,
)

_NFL_PIPELINE_FILES = (
    "services/scoring/recompute.py",
    "services/scoring/scoring_stack.py",
    "services/scoring/tier_evaluator.py",
    "services/scoring/gates/engine.py",
    "services/scoring/gates/thresholds.py",
    # NFL-specific model file path TBD
)


class NFLReplayAdapter(SportReplayAdapter):
    SPORT = "nfl"

    @property
    def config(self) -> SportFixedConfig:
        return SportFixedConfig(
            sport="nfl",
            odds_collection="nfl_historical_alt_odds_raw",
            feature_cache_collection="nfl_replay_feature_cache",
            master_hub_collection="nfl_master_hub_2026",
            game_log_array_field="player_game_logs",
            default_pipeline_files=_NFL_PIPELINE_FILES,
            tier_short_codes={
                "safe_haven": "SH",
                "front_lines": "FL",
                "war_zone": "WZ",
            },
        )

    def normalize_stat_family(self, market: str,
                                replay_family: Optional[str] = None) -> str:
        raise NotImplementedError("NFL adapter — Phase 6+")

    def list_stat_families(self) -> List[str]:
        raise NotImplementedError("NFL adapter — Phase 6+")

    def load_model(self) -> Any:
        raise NotImplementedError("NFL adapter — Phase 6+")

    def predict(self, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("NFL adapter — Phase 6+")

    async def fetch_actuals(self, *, game_date: str) -> Dict[str, Dict[str, float]]:
        raise NotImplementedError("NFL adapter — Phase 6+")

    def grade_outcome(self, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("NFL adapter — Phase 6+")

    async def resolve_opp_pitcher(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None

    async def resolve_opposing_lineup(self, **kwargs: Any) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError("NFL adapter — Phase 6+")
