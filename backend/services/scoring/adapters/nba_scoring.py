"""
NBA Scoring Adapter — reads dg_live_props (NBA's canonical layered-equivalent
collection) and produces a ScoringContext.

NOTE: NBA has not yet migrated to the exact-match canonical layered schema
used by MLB. Instead, dg_live_props stores PP as the primary row with a
`sharp_market` sub-document carrying dk/fd/betonline prices. This adapter
normalizes that shape into the standard ScoringContext.

NBA has a real `multiplier` field + `is_demon`/`is_goblin` flags sourced
from PP, so pp_utility gets actual multiplier-source data.
"""
import logging
from typing import Any, Dict, List, Optional

from services.scoring.adapters.base import ScoringAdapter, ScoringContext

logger = logging.getLogger(__name__)


class _NBAGateSorter:
    """
    Minimal NBA gate sorter satisfying the MLBTierSorter-compatible contract.
    Uses hit-rate + edge + tp + CV thresholds scaled for NBA's stat types.

    These thresholds are intentionally conservative placeholders — tuned
    by NBA analytics can be passed in via config.override_config.tier.
    """
    SAFE_HAVEN = {"max_cv": 0.50, "min_hit_rate": 75, "min_edge": 8, "min_tp": 70}
    FRONT_LINES = {"max_cv": 0.75, "min_hit_rate": 60, "min_edge": 5, "min_tp": 55}
    # NBA war_zone defaults (varA, locked 2026-04-17 after tuning validation).
    # MLB-scale thresholds (cv=0.80, ceil=30, edge=15) were unreachable on NBA.
    # varA produces ~1.5% slate-share legitimate moonshots with zero cannibalization
    # of safe_haven/front_lines; all entrants migrate from unqualified only.
    WAR_ZONE = {"min_cv": 0.45, "min_ceiling_rate": 20, "min_edge": 10}

    def __init__(self, overrides: Optional[Dict[str, Any]] = None):
        o = overrides or {}
        self.SAFE_HAVEN = {**self.SAFE_HAVEN, **(o.get("safe_haven") or {})}
        self.FRONT_LINES = {**self.FRONT_LINES, **(o.get("front_lines") or {})}
        self.WAR_ZONE = {**self.WAR_ZONE, **(o.get("war_zone") or {})}

    def _check(self, gates_def, *, cv=None, hit_rate=None, edge_pct=None,
               tp=None, ceiling_rate=None):
        results = {}
        if "max_cv" in gates_def:
            results["gate_cv"] = {
                "threshold": gates_def["max_cv"], "value": cv,
                "passed": cv is not None and cv <= gates_def["max_cv"],
            }
        if "min_cv" in gates_def:
            results["gate_cv"] = {
                "threshold": gates_def["min_cv"], "value": cv,
                "passed": cv is not None and cv >= gates_def["min_cv"],
            }
        if "min_hit_rate" in gates_def:
            results["gate_hit_rate"] = {
                "threshold": gates_def["min_hit_rate"], "value": hit_rate,
                "passed": hit_rate is not None and hit_rate >= gates_def["min_hit_rate"],
            }
        if "min_ceiling_rate" in gates_def:
            results["gate_ceiling"] = {
                "threshold": gates_def["min_ceiling_rate"], "value": ceiling_rate,
                "passed": ceiling_rate is not None and ceiling_rate >= gates_def["min_ceiling_rate"],
            }
        if "min_edge" in gates_def:
            results["gate_edge"] = {
                "threshold": gates_def["min_edge"], "value": edge_pct,
                "passed": edge_pct is not None and edge_pct >= gates_def["min_edge"],
            }
        if "min_tp" in gates_def:
            results["gate_tp"] = {
                "threshold": gates_def["min_tp"], "value": tp,
                "passed": tp is not None and tp >= gates_def["min_tp"],
            }
        failed = [k for k, v in results.items() if not v["passed"]]
        return (len(failed) == 0), (",".join(failed) or "ok"), results

    def check_safe_haven_gates(self, prop, cv, hit_rate, edge_pct, tp):
        return self._check(self.SAFE_HAVEN, cv=cv, hit_rate=hit_rate,
                           edge_pct=edge_pct, tp=tp)

    def check_front_lines_gates(self, prop, cv, hit_rate, edge_pct, tp):
        return self._check(self.FRONT_LINES, cv=cv, hit_rate=hit_rate,
                           edge_pct=edge_pct, tp=tp)

    def check_war_zone_gates(self, prop, cv, ceiling_rate, edge_pct):
        return self._check(self.WAR_ZONE, cv=cv, ceiling_rate=ceiling_rate,
                           edge_pct=edge_pct)


class NBAScoringAdapter(ScoringAdapter):
    # Map our stat_type to the bdl_game_logs field
    _STAT_FIELD_MAP = {
        "PTS": "pts",
        "REB": "reb",
        "AST": "ast",
        "PRA": "pra",  # synthesized
        "3PM": "fg3m",
        "STL": "stl",
        "BLK": "blk",
        "TO": "turnover",
    }

    def __init__(self):
        self._sorter = None
        self._cv_cache: dict = {}
        self._logs_cache: dict = {}
        self._logs_loaded = False

    @property
    def sport(self) -> str:
        return "nba"

    @property
    def live_props_collection(self) -> str:
        return "dg_live_props"

    @property
    def scores_collection(self) -> str:
        return "nba_prop_scores"

    @property
    def cached_board_collection(self) -> str:
        return "dg_cached_board"

    async def load_live_props(self, db, limit: Optional[int] = None):
        cursor = db[self.live_props_collection].find({}, {"_id": 0})
        if limit:
            cursor = cursor.limit(int(limit))
        props = await cursor.to_list(length=None)
        logger.info(f"[NBA_SCORING] Loaded {len(props)} props from {self.live_props_collection}")
        return props

    def get_sorter(self, db):
        return self._sorter  # populated per-recompute with config

    def _build_sorter(self, config):
        overrides = ((config or {}).get("override_config") or {}).get("tier")
        self._sorter = _NBAGateSorter(overrides)
        return self._sorter

    async def _preload_game_logs(self, db) -> None:
        """Pull NBA game logs from master hub once per recompute."""
        if self._logs_loaded:
            return
        hub = db["nba_master_hub_2026"]
        cursor = hub.find(
            {"bdl_game_logs_count": {"$gt": 0}},
            {"display_name": 1, "bdl_game_logs": 1, "_id": 0},
        )
        count = 0
        async for doc in cursor:
            name = (doc.get("display_name") or "").strip()
            if not name:
                continue
            self._logs_cache[name.lower()] = doc.get("bdl_game_logs") or []
            count += 1
        self._logs_loaded = True
        logger.info(f"[NBA_SCORING] Cached game logs for {count} players")

    def _compute_cv_and_hit_rate(
        self, player_name: str, stat_type: str, line: float, window: int = 20
    ):
        """
        Compute (cv, hit_rate, ceiling_rate) from the player's last-N game logs.
        Returns (None, None, None) if unavailable.
        """
        field = self._STAT_FIELD_MAP.get(stat_type)
        if field is None:
            return None, None, None
        logs = self._logs_cache.get((player_name or "").lower()) or []
        if not logs:
            return None, None, None

        # Newest-first order is NOT guaranteed; sort by date desc for the window.
        try:
            logs_sorted = sorted(
                logs,
                key=lambda g: str(g.get("date") or ""),
                reverse=True,
            )
        except Exception:
            logs_sorted = logs
        window_logs = logs_sorted[:window]

        import numpy as np
        # PRA synthesized
        if stat_type == "PRA":
            vals = [
                (g.get("pts") or 0) + (g.get("reb") or 0) + (g.get("ast") or 0)
                for g in window_logs
                if g.get("pts") is not None
            ]
        else:
            vals = [g.get(field) for g in window_logs if g.get(field) is not None]
        vals = [float(v) for v in vals if v is not None]
        if len(vals) < 5:
            return None, None, None

        arr = np.array(vals)
        mean = float(arr.mean())
        if mean <= 0:
            cv = None
        else:
            cv = round(float(arr.std(ddof=1) / mean), 4)
        hits = int(sum(1 for v in vals if v > line))
        hit_rate = round((hits / len(vals)) * 100.0, 1)
        # Ceiling: hit rate vs 2x line (or line + 50% mean) — use 1.5x for NBA
        ceiling_thresh = max(line * 1.5, line + 0.5)
        ceiling_hits = int(sum(1 for v in vals if v >= ceiling_thresh))
        ceiling_rate = round((ceiling_hits / len(vals)) * 100.0, 1)
        return cv, hit_rate, ceiling_rate

    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        if self._sorter is None:
            self._build_sorter(config)
        # Ensure game logs loaded once
        await self._preload_game_logs(db)

        player_name = prop.get("player_name")
        # NBA market/prop_type → stat_type
        market = prop.get("market", "")
        stat_type = {
            "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
            "player_points_rebounds_assists": "PRA",
            "player_points_alternate": "PTS", "player_rebounds_alternate": "REB",
            "player_assists_alternate": "AST",
            "player_points_rebounds_assists_alternate": "PRA",
        }.get(market, prop.get("stat_type_extracted") or market)

        line = prop.get("line")
        if player_name is None or line is None or stat_type is None:
            return None

        direction = (prop.get("direction") or "OVER").upper()
        side = "OVER" if "OVER" in direction else "UNDER"
        event_id = prop.get("event_id", "?")

        canon_key = (
            f"nba|{event_id}|{player_name}|{stat_type}|{float(line)}|{side}"
        )

        # PP layer (primary)
        pp_layer = {
            "book": "prizepicks",
            "line": float(line),
            "odds": prop.get("price"),
        }

        # Sharp market → dk/sharp layers (line is assumed to match PP since dg_live_props
        # stores one row per PP-anchored prop)
        sm = prop.get("sharp_market") or {}
        dk_price = sm.get("draftkings_price")
        dk_layer = (
            {"book": "draftkings", "line": float(line), "odds": dk_price}
            if dk_price is not None else None
        )
        fd_price = sm.get("fanduel_price")
        # NBA has no MGM in dg_live_props — use FanDuel as the second reference book
        # but label it in the layer as 'fanduel'. The scoring_stack only uses
        # dk/mgm/sharp; to keep compatibility, treat FanDuel as mgm_layer for NBA.
        mgm_layer = (
            {"book": "fanduel", "line": float(line), "odds": fd_price}
            if fd_price is not None else None
        )
        bo_price = sm.get("betonline_price")
        sharp_layer = (
            {"book": "betonline", "line": float(line), "odds": bo_price}
            if bo_price is not None else None
        )

        # Hit rates (embedded in prop as fallback)
        hr = (prop.get("hit_rates") or {})
        season = hr.get("season") or {}
        l10 = hr.get("l10") or {}
        season_rate = season.get("hit_rate")
        l10_rate = l10.get("hit_rate")
        embedded_hit_rate = round(l10_rate * 100.0, 1) if l10_rate is not None else (
            round(season_rate * 100.0, 1) if season_rate is not None else None
        )

        # CV + recomputed hit_rate + ceiling_rate from master-hub game logs.
        # Prefer computed over embedded; fall back to embedded if logs missing.
        cv, computed_hit_rate, ceiling_rate = self._compute_cv_and_hit_rate(
            player_name, stat_type, float(line), window=20
        )
        hit_rate = computed_hit_rate if computed_hit_rate is not None else embedded_hit_rate

        # Model probability: NBA lacks a dedicated XGBoost model in this adapter;
        # use hit_rate as a pragmatic proxy (config can override).
        p_model = (hit_rate / 100.0) if hit_rate is not None else None

        # tp from reference-market implied prob (dk preferred, else fanduel)
        def _amer(o):
            if o is None: return None
            try: o = float(o)
            except (TypeError, ValueError): return None
            return abs(o)/(abs(o)+100.0) if o < 0 else 100.0/(o+100.0)
        dk_p = _amer(dk_price)
        fd_p = _amer(fd_price)
        if dk_p is not None and fd_p is not None:
            tp = round(((dk_p + fd_p) / 2.0) * 100.0, 1)
        elif dk_p is not None:
            tp = round(dk_p * 100.0, 1)
        elif fd_p is not None:
            tp = round(fd_p * 100.0, 1)
        else:
            tp = 50.0

        if p_model is not None:
            edge_pct = round(p_model * 100.0 - tp, 1)
        else:
            edge_pct = 0.0

        books = 1 + int(dk_layer is not None) + int(mgm_layer is not None) + int(sharp_layer is not None)

        # PP multiplier (REAL source for NBA)
        pp_multiplier = prop.get("multiplier")
        is_demon = bool(prop.get("is_demon"))
        is_goblin = bool(prop.get("is_goblin"))
        pp_label = (
            "demon" if is_demon else "goblin" if is_goblin
            else ("standard" if prop.get("prop_type") == "standard" else None)
        )

        return ScoringContext(
            canonical_key=canon_key, sport="nba", event_id=event_id,
            player_name=player_name, stat_type=stat_type, line=float(line),
            recommendation=side,
            pp_layer=pp_layer, dk_layer=dk_layer, mgm_layer=mgm_layer,
            sharp_layer=sharp_layer,
            p_model=p_model, cv=cv, hit_rate=hit_rate, edge_pct=edge_pct,
            tp=tp, ceiling_rate=ceiling_rate,
            books_available_count=books,
            raw_prop=prop,
            pp_combo_multiplier=pp_multiplier,
            pp_label=pp_label, pp_multiplier_model=None,
        )
