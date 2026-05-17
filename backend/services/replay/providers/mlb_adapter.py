"""MLB sport adapter — concrete `SportReplayAdapter` for baseball.

This adapter encapsulates ALL MLB-specific knowledge that the universal
replay harness will consume. It is the first concrete adapter; future
NBA / NFL adapters follow the same interface.

Existing MLB Layer 1-2-3 collections + code are reused verbatim — this
file imports and delegates rather than duplicating logic. Phase 1-2
existing services (Layer 1 alt-odds ingest, Layer 2 feature cache,
Layer 3 model replay engine) are NOT modified.

Phase 2 NOTE: `predict` and `grade_outcome` currently route through
the existing `services.replay.mlb_replay_engine` infrastructure. The
adapter is the seam where the universal harness calls into MLB-specific
code; do not duplicate scoring logic here.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from services.replay.providers.sport_adapter import (
    SportReplayAdapter, SportFixedConfig,
)


# Production MLB scoring pipeline files — used to compute the canonical
# `production_pipeline_version` for audit pinning.
_MLB_PIPELINE_FILES = (
    "services/scoring/recompute.py",
    "services/scoring/scoring_stack.py",
    "services/scoring/tier_evaluator.py",
    "services/scoring/gates/engine.py",
    "services/scoring/gates/thresholds.py",
    "services/scoring/gates/schema.py",
    "services/scoring/best_book.py",
    "services/scoring/universal_edge.py",
    "services/scoring/tp_engine.py",
    "services/mlb_high_friction_model.py",
)


# MLB market → production stat-family resolver.
def _resolve_mlb_family(market: Optional[str],
                          replay_family: Optional[str] = None) -> str:
    m = (market or "").lower()
    sf = (replay_family or "").lower()
    if sf == "strikeouts":
        return "pitcher_strikeouts" if "pitcher" in m else "batter_strikeouts"
    if sf == "pitcher_walks":
        return "walks_allowed"
    return sf or "unknown"


# MLB stat-family → game-log field name.
_MLB_STAT_FIELD_MAP = {
    "hits": "hits",
    "total_bases": "total_bases",
    "runs": "runs", "rbis": "rbis",
    "batter_strikeouts": "strikeouts",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_walks": "pitcher_walks",
    "walks_allowed": "pitcher_walks",
    "earned_runs": "earned_runs",
    "pitcher_outs": "pitcher_outs",
    "home_runs": "home_runs",
}


class MLBReplayAdapter(SportReplayAdapter):
    SPORT = "mlb"

    @property
    def config(self) -> SportFixedConfig:
        return SportFixedConfig(
            sport="mlb",
            odds_collection="mlb_historical_alt_odds_raw",
            feature_cache_collection="mlb_replay_feature_cache",
            master_hub_collection="mlb_master_hub_2026",
            game_log_array_field="bdl_game_logs",
            default_pipeline_files=_MLB_PIPELINE_FILES,
            tier_short_codes={
                "safe_haven": "SH",
                "front_lines": "FL",
                "war_zone": "WZ",
            },
        )

    # ── Stat-family ──────────────────────────────────────────────────
    def normalize_stat_family(self, market: str,
                                replay_family: Optional[str] = None) -> str:
        return _resolve_mlb_family(market, replay_family)

    def list_stat_families(self) -> List[str]:
        return list(_MLB_STAT_FIELD_MAP.keys())

    # ── Model loader ─────────────────────────────────────────────────
    def load_model(self) -> Any:
        from services.mlb_high_friction_model import MLBHighFrictionModel
        m = MLBHighFrictionModel(self._db)
        m.load_models()
        return m

    def predict(self, *, model: Any,
                player_name: str, stat_family: str,
                line: float, opponent_team: Optional[str],
                home_team: Optional[str], away_team: Optional[str],
                is_away: bool,
                feature_cache_row: Optional[Dict[str, Any]],
                as_of_date: str) -> Dict[str, Any]:
        # Production replay routes inference through `MLBHighFrictionModel.predict()`.
        # Phase 2b added `as_of_date` to that function. The harness passes it through.
        # Park team rule: hitting in opp's park when batter is the away team.
        park_team = (away_team or "") if is_away else (home_team or "")
        return model.predict(
            player_name=player_name,
            stat_type=stat_family,
            line=line,
            opponent_team=opponent_team,
            park_team=park_team,
            as_of_date=as_of_date,
        )

    # ── Actuals lookup ───────────────────────────────────────────────
    async def fetch_actuals(self, *, game_date: str) -> Dict[str, Dict[str, float]]:
        from services.replay.historical_alt_odds_ingest import normalize_player_name
        cfg = self.config
        pipeline = [
            {"$project": {"logs": "$" + cfg.game_log_array_field,
                          "display_name": 1, "player_name": 1, "mlb_full_name": 1}},
            {"$unwind": "$logs"},
            {"$project": {
                "d": {"$ifNull": [
                    {"$substr": ["$logs.date", 0, 10]},
                    {"$substr": ["$logs.game_date", 0, 10]}]},
                "stats": "$logs",
                "name_canon": {"$ifNull": [
                    "$display_name",
                    {"$ifNull": ["$player_name", "$mlb_full_name"]}]},
            }},
            {"$match": {"d": game_date}},
        ]
        out: Dict[str, Dict[str, float]] = {}
        async for r in self._db[cfg.master_hub_collection].aggregate(
            pipeline, allowDiskUse=True
        ):
            nk = normalize_player_name(r.get("name_canon") or "")
            if not nk:
                continue
            stats = r.get("stats") or {}
            existing = out.setdefault(nk, {})
            for fam, field in _MLB_STAT_FIELD_MAP.items():
                v = stats.get(field)
                if v is not None:
                    try:
                        existing[fam] = float(v)
                    except (TypeError, ValueError):
                        pass
            # Composite: hits_runs_rbis
            try:
                if all(k in existing for k in ("hits", "runs", "rbis")):
                    existing["hits_runs_rbis"] = (
                        existing["hits"] + existing["runs"] + existing["rbis"]
                    )
            except Exception:
                pass
        return out

    def grade_outcome(self, *, actual: Optional[float],
                       line: float, side: str, odds: int,
                       stake: float = 1.0) -> Dict[str, Any]:
        if actual is None:
            return {"status": "ungraded", "profit_units": 0.0,
                    "stake_units": 0.0, "actual": None}
        payout = (odds / 100.0) if odds > 0 else (100.0 / -odds)
        if side == "OVER":
            if actual > line:
                return {"status": "win", "profit_units": payout * stake,
                        "stake_units": stake, "actual": actual}
            if actual < line:
                return {"status": "loss", "profit_units": -stake,
                        "stake_units": stake, "actual": actual}
            return {"status": "push", "profit_units": 0.0,
                    "stake_units": stake, "actual": actual}
        # UNDER
        if actual < line:
            return {"status": "win", "profit_units": payout * stake,
                    "stake_units": stake, "actual": actual}
        if actual > line:
            return {"status": "loss", "profit_units": -stake,
                    "stake_units": stake, "actual": actual}
        return {"status": "push", "profit_units": 0.0,
                "stake_units": stake, "actual": actual}

    # ── Context resolution (Phase-2 gaps) ────────────────────────────
    async def resolve_opp_pitcher(self, *, event_id: str, game_date: str,
                                    home_team: str, away_team: str,
                                    is_away: bool, as_of_date: str
                                    ) -> Optional[Dict[str, Any]]:
        # Phase 3+ — historical opp pitcher resolution not yet wired
        return None

    async def resolve_opposing_lineup(self, *, event_id: str, game_date: str,
                                        opp_team: str, as_of_date: str
                                        ) -> Optional[List[Dict[str, Any]]]:
        # Phase 3+ — historical lineup snapshot collection not built yet
        return None
