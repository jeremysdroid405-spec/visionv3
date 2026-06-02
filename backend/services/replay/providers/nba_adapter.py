"""NBA sport adapter — thin wrapper around the production NBA scorer.

The adapter does NOT score props itself. It declares the static
metadata the universal replay harness needs and delegates the actual
inference to the production code path
(`services.scoring.recompute.recompute_sport` invoked by
`services.replay.nba_replay_engine.replay_date`).

Identity: see `services.scoring.adapters.nba_scoring.NBAScoringAdapter`
for the full per-prop scoring logic (recency blend, availability guard,
rate × minutes, universal probability engine, gate engine, vision score,
tier classification). Historical replay reuses that pipeline verbatim
by feeding `recompute_sport` a list of historical props with their
`commence_time` set so the in-built `before_date` cutoffs prevent
leakage.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from services.replay.providers.sport_adapter import (
    SportReplayAdapter, SportFixedConfig,
)


# Production NBA scoring pipeline files — used to compute the canonical
# `production_pipeline_version` for audit pinning.
_NBA_PIPELINE_FILES = (
    "services/scoring/recompute.py",
    "services/scoring/scoring_stack.py",
    "services/scoring/tier_evaluator.py",
    "services/scoring/gates/engine.py",
    "services/scoring/gates/thresholds.py",
    "services/scoring/gates/schema.py",
    "services/scoring/best_book.py",
    "services/scoring/universal_edge.py",
    "services/scoring/tp_engine.py",
    "services/scoring/canonical_stats.py",
    "services/scoring/adapters/nba_scoring.py",
    "services/vegas_killer_model.py",
    "services/replay/nba_replay_engine.py",
)


# NBA canonical stat-family → game-log field map. Combo families
# (`pra`, `pts_reb`, `pts_ast`, `reb_ast`) are derived from component
# fields when grading actuals.
_NBA_STAT_FIELD_MAP = {
    "pts":       ["pts"],
    "reb":       ["reb"],
    "ast":       ["ast"],
    "pra":       ["pts", "reb", "ast"],
    "threes":    ["fg3m"],
    "stl":       ["stl"],
    "blk":       ["blk"],
    "pts_reb":   ["pts", "reb"],
    "pts_ast":   ["pts", "ast"],
    "reb_ast":   ["reb", "ast"],
    "turnovers": ["turnover"],
}


class NBAReplayAdapter(SportReplayAdapter):
    SPORT = "nba"

    @property
    def config(self) -> SportFixedConfig:
        return SportFixedConfig(
            sport="nba",
            # Default historical odds source. The SSOT historical replay
            # script (`scripts.sgo.historical_full_pipeline_replay`)
            # overrides this via the `odds_collection` kwarg on
            # `run_production_replay` so SGO-namespace data drives
            # Layer-3 without polluting the production odds archive.
            odds_collection="sgo_replay_alt_odds_raw",
            # NBA has no per-date feature cache — `NBAScoringAdapter`
            # reads game logs directly from `nba_master_hub_2026` and
            # uses `commence_time` as the leakage cutoff. The field is
            # retained for adapter parity but no collection is built.
            feature_cache_collection="nba_replay_feature_cache",
            master_hub_collection="nba_master_hub_2026",
            game_log_array_field="bdl_game_logs",
            default_pipeline_files=_NBA_PIPELINE_FILES,
            tier_short_codes={
                "safe_haven": "SH",
                "front_lines": "FL",
                "war_zone": "WZ",
            },
        )

    # ── Stat-family ──────────────────────────────────────────────────
    def normalize_stat_family(self, market: str,
                                replay_family: Optional[str] = None) -> str:
        """Resolve any market key (or already-canonical stat token) to
        the NBA canonical stat family used by gate thresholds and the
        actuals lookup. Falls through to the lowercased input if the
        registry has no mapping (matches the production behaviour)."""
        from services.scoring.canonical_stats import (
            canonical_stat_type, stat_family,
        )
        if replay_family:
            fam = stat_family("nba", replay_family)
            if fam and fam != "_default":
                return fam
        if not market:
            return "unknown"
        stat = canonical_stat_type("nba", market)
        fam = stat_family("nba", stat)
        if fam == "_default":
            return (stat or market).lower()
        return fam

    def list_stat_families(self) -> List[str]:
        return list(_NBA_STAT_FIELD_MAP.keys())

    # ── Model loader / predict ───────────────────────────────────────
    def load_model(self) -> Any:
        """The NBA replay path does NOT use this entrypoint.

        Production NBA scoring is invoked through
        `services.scoring.recompute.recompute_sport(db, "nba", ...)`,
        which lazily loads every model artefact (legacy VK, VK2, expected-
        minutes) the first time `NBAScoringAdapter.build_context` needs
        them. Returning a singleton `NBAScoringAdapter` here keeps the
        abstract-method contract satisfied without duplicating that
        loader path.
        """
        from services.scoring.adapters.nba_scoring import NBAScoringAdapter
        return NBAScoringAdapter()

    def predict(self, **kwargs: Any) -> Dict[str, Any]:
        """Not used. NBA replay scoring goes through
        `nba_replay_engine.replay_date` → `recompute_sport(db, "nba", …)`.
        """
        raise NotImplementedError(
            "NBA scoring is invoked through "
            "`services.replay.nba_replay_engine.replay_date` (which "
            "calls `recompute_sport(db, 'nba', dry_run=True)`), NOT "
            "through `NBAReplayAdapter.predict()`."
        )

    # ── Actuals lookup ───────────────────────────────────────────────
    async def fetch_actuals(self, *, game_date: str) -> Dict[str, Dict[str, float]]:
        """Return `{player_name_normalized: {stat_family: actual_value}}`
        for the given NBA game date, sourced from
        `nba_master_hub_2026.bdl_game_logs`.
        """
        from services.replay.historical_alt_odds_ingest import normalize_player_name
        cfg = self.config
        pipeline = [
            {"$project": {"logs": "$" + cfg.game_log_array_field,
                          "display_name": 1, "player_name": 1}},
            {"$unwind": "$logs"},
            {"$project": {
                "d": {"$ifNull": [
                    {"$substr": ["$logs.date", 0, 10]},
                    {"$substr": ["$logs.game_date", 0, 10]}]},
                "stats": "$logs",
                "name_canon": {"$ifNull": [
                    "$display_name", "$player_name"]},
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
            for fam, fields in _NBA_STAT_FIELD_MAP.items():
                vals: List[float] = []
                ok = True
                for fld in fields:
                    v = stats.get(fld)
                    if v is None:
                        ok = False
                        break
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        ok = False
                        break
                if ok and vals:
                    existing[fam] = sum(vals)
        return out

    # ── Grading ──────────────────────────────────────────────────────
    def grade_outcome(self, *, actual: Optional[float],
                       line: float, side: str, odds: int,
                       stake: float = 1.0) -> Dict[str, Any]:
        """Grade a single NBA prop. Math is sport-agnostic — same payout
        formula MLB uses (American odds → payout multiplier; OVER wins
        when `actual > line`, UNDER wins when `actual < line`, exact
        line is a push)."""
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

    # ── Context resolution ───────────────────────────────────────────
    async def resolve_opp_pitcher(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None   # N/A for NBA

    async def resolve_opposing_lineup(self, *, event_id: str, game_date: str,
                                        opp_team: str, as_of_date: str
                                        ) -> Optional[List[Dict[str, Any]]]:
        # Optional — NBA opposing lineup snapshot collection is not yet
        # wired into replay. NBA scoring does not currently consume an
        # opposing-lineup snapshot (unlike MLB pitcher matchups), so a
        # None return is functionally complete for now.
        return None
