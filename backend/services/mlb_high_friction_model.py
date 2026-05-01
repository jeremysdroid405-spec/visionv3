"""
MLB High-Friction Ensemble Model v1.0
=====================================
GOAT-Tier XGBoost ensemble trained on 3+ years of BDL data with
full friction features.

CATEGORY 1: PITCHER-BATTER COLLISION (PvP)
- Batter vs LHP/RHP splits (AVG, OBP, K%)
- Handedness matchup advantage
- Contact rates and chase tendencies

CATEGORY 2: ENVIRONMENTAL FRICTION
- Park factors (3-year historical)
- Home/Away performance splits
- Venue-specific adjustments

CATEGORY 3: MARKET ALIGNMENT
- DraftKings implied probability
- Vig-removed baseline
- Market efficiency features

CATEGORY 4: PLATE DISCIPLINE & TRENDS
- L5/L10/L20 EWMA with trend detection
- Volatility (CV, std_dev)
- Streak analysis

STRICT OUTPUT REQUIREMENTS:
- NO FALLBACKS to season_avg
- High-precision decimals (5.42 K)
- True L10 σ for probability
- Standard Normal CDF for vk_prob_over

Author: PropVision AI
Version: 1.0.0 (High-Friction Ensemble)
"""
import logging
import numpy as np
import pandas as pd
import pickle
import os
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from scipy import stats

from services.config.collection_names import COLL
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)


class MLBHighFrictionModel:
    """
    MLB High-Friction Ensemble - GOAT-Tier XGBoost.
    
    Features 70+ inputs across 4 friction categories:
    1. Pitcher-Batter Collision (PvP)
    2. Environmental Friction (Park/Weather)
    3. Market Alignment (Odds API)
    4. Plate Discipline & Trends
    """
    
    MLB_STAT_TYPES = [
        'hits',
        'total_bases', 
        'rbis',
        'runs',
        'pitcher_strikeouts',
        'hits+runs+rbis',
        'home_runs',
        'stolen_bases',
        'strikeouts',
        'doubles',
        'walks',
        'singles',
        'earned_runs',
        'hits_allowed',
        'pitcher_walks',
        # Analytical-only — no trained XGBoost model.
        # μ derived from expected_IP × 3 (workload anchor).
        'pitcher_outs',
    ]
    
    STAT_FIELD_MAP = {
        'hits': 'hits',
        'total_bases': 'total_bases',
        'rbis': 'rbis',
        'runs': 'runs',
        'stolen_bases': 'stolen_bases',
        'home_runs': 'home_runs',
        'walks': 'walks',
        'doubles': 'doubles',
        'strikeouts': 'strikeouts',
        'pitcher_strikeouts': 'pitcher_strikeouts',
        'earned_runs': 'earned_runs',
        'hits_allowed': 'hits_allowed',
        'pitcher_walks': 'pitcher_walks',
        'hits+runs+rbis': ['hits', 'runs', 'rbis'],
        'singles': '_calc_singles',
    }
    
    # =========================================================================
    # PARK FACTORS (3-Year Historical)
    # =========================================================================
    # Format: {team: {hits: factor, runs: factor, hr: factor, k: factor}}
    PARK_FACTORS_3YR = {
        # HITTER PARADISE
        'COL': {'hits': 1.18, 'runs': 1.25, 'hr': 1.32, 'k': 0.88, 'tb': 1.22},  # Coors Field
        'CIN': {'hits': 1.10, 'runs': 1.15, 'hr': 1.18, 'k': 0.94, 'tb': 1.12},  # Great American
        'TEX': {'hits': 1.08, 'runs': 1.12, 'hr': 1.15, 'k': 0.95, 'tb': 1.10},  # Globe Life
        'BOS': {'hits': 1.06, 'runs': 1.08, 'hr': 0.95, 'k': 0.97, 'tb': 1.04},  # Fenway
        'PHI': {'hits': 1.05, 'runs': 1.08, 'hr': 1.10, 'k': 0.96, 'tb': 1.06},  # Citizens Bank
        'CHC': {'hits': 1.04, 'runs': 1.06, 'hr': 1.08, 'k': 0.97, 'tb': 1.05},  # Wrigley
        'MIL': {'hits': 1.03, 'runs': 1.05, 'hr': 1.06, 'k': 0.98, 'tb': 1.04},  # American Family
        
        # NEUTRAL
        'NYY': {'hits': 1.02, 'runs': 1.04, 'hr': 1.12, 'k': 0.98, 'tb': 1.04},  # Yankee Stadium
        'LAD': {'hits': 1.00, 'runs': 1.00, 'hr': 1.02, 'k': 1.00, 'tb': 1.01},  # Dodger Stadium
        'ATL': {'hits': 1.00, 'runs': 1.02, 'hr': 1.06, 'k': 0.99, 'tb': 1.02},  # Truist Park
        'HOU': {'hits': 0.98, 'runs': 0.98, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},  # Minute Maid
        'MIN': {'hits': 1.00, 'runs': 1.02, 'hr': 1.10, 'k': 0.98, 'tb': 1.03},  # Target Field
        'STL': {'hits': 0.99, 'runs': 1.00, 'hr': 1.02, 'k': 0.99, 'tb': 1.00},  # Busch Stadium
        'DET': {'hits': 1.00, 'runs': 1.00, 'hr': 0.98, 'k': 1.00, 'tb': 0.99},  # Comerica
        'BAL': {'hits': 1.01, 'runs': 1.02, 'hr': 1.05, 'k': 0.99, 'tb': 1.02},  # Camden Yards
        'TOR': {'hits': 1.00, 'runs': 1.01, 'hr': 1.04, 'k': 0.99, 'tb': 1.01},  # Rogers Centre
        'CLE': {'hits': 0.99, 'runs': 0.99, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},  # Progressive
        'KC': {'hits': 1.00, 'runs': 1.01, 'hr': 0.96, 'k': 1.00, 'tb': 0.99},   # Kauffman
        'ARI': {'hits': 1.01, 'runs': 1.02, 'hr': 1.04, 'k': 0.99, 'tb': 1.02},  # Chase Field
        'PIT': {'hits': 0.99, 'runs': 0.98, 'hr': 0.95, 'k': 1.01, 'tb': 0.98},  # PNC Park
        'CHW': {'hits': 1.00, 'runs': 1.02, 'hr': 1.08, 'k': 0.99, 'tb': 1.02},  # Guaranteed Rate
        'LAA': {'hits': 0.98, 'runs': 0.97, 'hr': 0.96, 'k': 1.01, 'tb': 0.97},  # Angel Stadium
        'WSH': {'hits': 0.99, 'runs': 0.98, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},  # Nationals Park
        
        # PITCHER FRIENDLY
        'SF': {'hits': 0.92, 'runs': 0.88, 'hr': 0.80, 'k': 1.06, 'tb': 0.88},   # Oracle Park
        'OAK': {'hits': 0.94, 'runs': 0.90, 'hr': 0.86, 'k': 1.05, 'tb': 0.90},  # Oakland Coliseum
        'SD': {'hits': 0.95, 'runs': 0.92, 'hr': 0.88, 'k': 1.04, 'tb': 0.92},   # Petco Park
        'MIA': {'hits': 0.96, 'runs': 0.94, 'hr': 0.86, 'k': 1.03, 'tb': 0.93},  # LoanDepot Park
        'TB': {'hits': 0.96, 'runs': 0.94, 'hr': 0.90, 'k': 1.03, 'tb': 0.94},   # Tropicana
        'SEA': {'hits': 0.94, 'runs': 0.90, 'hr': 0.84, 'k': 1.06, 'tb': 0.90},  # T-Mobile Park
        'NYM': {'hits': 0.97, 'runs': 0.95, 'hr': 0.92, 'k': 1.02, 'tb': 0.95},  # Citi Field
    }
    DEFAULT_PARK = {'hits': 1.00, 'runs': 1.00, 'hr': 1.00, 'k': 1.00, 'tb': 1.00}
    
    # =========================================================================
    # TEAM STRIKEOUT TENDENCIES (2024-2026 avg)
    # =========================================================================
    TEAM_K_RATES = {
        # HIGH K TEAMS (strikes out a lot - good for pitcher K props)
        'ARI': 1.14, 'DET': 1.12, 'OAK': 1.10, 'CHC': 1.08, 'MIA': 1.07,
        'COL': 1.06, 'PIT': 1.05, 'CIN': 1.04, 'SEA': 1.03, 'TEX': 1.02,
        # NEUTRAL
        'ATL': 1.00, 'NYM': 0.99, 'PHI': 0.98, 'LAD': 0.97, 'SD': 0.97,
        'SF': 0.96, 'STL': 0.98, 'MIL': 0.99, 'CHW': 1.01, 'BAL': 1.00,
        'TOR': 1.01, 'BOS': 0.98, 'TB': 0.99, 'WSH': 1.02, 'LAA': 1.00,
        # LOW K TEAMS (makes contact - bad for pitcher K props)
        'HOU': 0.92, 'NYY': 0.94, 'CLE': 0.93, 'KC': 0.91, 'MIN': 0.93,
    }
    
    MODEL_DIR = '/app/backend/models/mlb_hf'
    
    def __init__(self, db):
        """
        Initialize MLB High-Friction Model.
        
        Args:
            db: PyMongo database (SYNC)
        """
        self.db = db
        self.master_hub = db[COLL("master_hub", "mlb")]
        self.historical_logs = db.mlb_historical_logs
        self.live_props = db[COLL("live_props", "mlb")]
        
        self.models = {}
        self.scalers = {}
        self.feature_cols = {}
        
        os.makedirs(self.MODEL_DIR, exist_ok=True)
        logger.info("[MLB_HF_MODEL] Initialized MLB High-Friction Ensemble v3.0_bayes")
    
    # =========================================================================
    # 2026-04-29 — Statcast LIVE lookups (v2.0 retrain wiring)
    # -------------------------------------------------------------------------
    # The retrain script pre-builds (mlbam_id, game_date) → SC dict
    # lookup maps in memory. Live predict() doesn't have those handy,
    # so it uses these per-call helpers to fetch the most-recent SC doc
    # for a player. mlbam_id is resolved via the identity map (bdl_id)
    # with a normalized-name fallback.
    # =========================================================================
    # 2026-04-29 v2.1 — lazy-loaded PA cache (mlb_statcast_raw)
    def _get_pa_cache(self):
        """Lazy-build & cache the PA-windowed Statcast index. Returns
        None if the collection is empty."""
        cache = getattr(self, "_pa_cache", None)
        if cache is not None:
            return cache
        try:
            from services.mlb_pa_features import MLBPACache
            c = MLBPACache()
            n = c.load_from_db(self.db)
            if n == 0:
                self._pa_cache = None
                return None
            self._pa_cache = c
            return c
        except Exception:
            self._pa_cache = None
            return None

    def _resolve_mlbam_id(self, player: Dict[str, Any]) -> Optional[int]:
        """Return the player's MLBAM (statcast) numeric ID or None."""
        # Direct fields on the hub doc, if any.
        for key in ("mlbam_id", "mlb_id", "statcast_id"):
            v = player.get(key)
            try:
                if v is not None: return int(v)
            except (TypeError, ValueError):
                continue
        # Identity-map lookup keyed on bdl_id.
        bdl_id = player.get("bdl_id") or player.get("bdl_player_id") or player.get("player_id")
        try: bdl_id = int(bdl_id) if bdl_id is not None else None
        except (TypeError, ValueError): bdl_id = None
        if bdl_id is not None:
            try:
                m = self.db.mlb_player_identity_map.find_one(
                    {"bdl_id": bdl_id, "mlb_id": {"$ne": None}},
                    {"_id": 0, "mlb_id": 1, "statcast_id": 1},
                )
                if m:
                    v = m.get("statcast_id") or m.get("mlb_id")
                    if v is not None: return int(v)
            except Exception as _swept_exc:
                log_silent_failure("services.mlb_high_friction_model._resolve_mlbam_id", _swept_exc)  # sweep-auto-converted
        # Last resort: normalized-name match on identity map.
        name = (player.get("display_name") or player.get("player_name") or "").lower().strip()
        if name:
            try:
                m = self.db.mlb_player_identity_map.find_one(
                    {"$or": [{"normalized_name": name}, {"statcast_name": name}],
                     "mlb_id": {"$ne": None}},
                    {"_id": 0, "mlb_id": 1, "statcast_id": 1},
                )
                if m:
                    v = m.get("statcast_id") or m.get("mlb_id")
                    if v is not None: return int(v)
            except Exception as _swept_exc:
                log_silent_failure("services.mlb_high_friction_model._resolve_mlbam_id", _swept_exc)  # sweep-auto-converted
        return None

    def _get_batter_sc_latest(self, player: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch the most recent statcast batter feature doc for player."""
        mid = self._resolve_mlbam_id(player)
        if mid is None: return None
        try:
            import pymongo as _pm
            doc = self.db.mlb_statcast_player_features.find_one(
                {"player_id": mid},
                {"_id": 0, "rolling_7": 1, "rolling_14": 1, "rolling_30": 1, "season_window": 1},
                sort=[("game_date", _pm.DESCENDING)],
            )
            return doc or None
        except Exception:
            return None

    def _get_pitcher_sc_latest(self, player: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch the most recent statcast pitcher feature doc."""
        mid = self._resolve_mlbam_id(player)
        if mid is None: return None
        try:
            import pymongo as _pm
            doc = self.db.mlb_statcast_pitcher_features.find_one(
                {"pitcher_id": mid},
                {"_id": 0, "rolling_14": 1, "rolling_30": 1, "season_window": 1},
                sort=[("game_date", _pm.DESCENDING)],
            )
            return doc or None
        except Exception:
            return None

    def _normalize_stat(self, stat_type: str) -> str:
        """Normalize stat type."""
        stat_lower = stat_type.lower().replace(' ', '_').replace('+', '+')
        aliases = {
            'k': 'pitcher_strikeouts', 'ks': 'pitcher_strikeouts',
            'pitcher_k': 'pitcher_strikeouts', 'pitcher_strikeouts': 'pitcher_strikeouts',
            'tb': 'total_bases', 'rbi': 'rbis', 'sb': 'stolen_bases',
            'hr': 'home_runs', 'h': 'hits', 'r': 'runs',
            'hrr': 'hits+runs+rbis', 'hits+runs+rbi': 'hits+runs+rbis',
            'batter_strikeouts': 'strikeouts',
            'batter_walks': 'walks',
            'walks_allowed': 'pitcher_walks',
            # 2026-04-27 fix: pitcher_outs is its OWN stat. The previous
            # alias to pitcher_strikeouts caused outs μ to come back as
            # the K projection (5-8 instead of 12-18). Outs μ is now
            # derived analytically from expected_IP × 3.
            'pitcher_outs':  'pitcher_outs',
            'pitching_outs': 'pitcher_outs',
            'outs':          'pitcher_outs',
            'outs_recorded': 'pitcher_outs',
        }
        return aliases.get(stat_lower, stat_lower)
    
    def _get_stat_value(self, game: Dict, stat: str) -> Optional[float]:
        """Extract stat from game log."""
        field = self.STAT_FIELD_MAP.get(stat, stat)
        if field == '_calc_singles':
            h = game.get('hits')
            if h is None:
                return None
            return max(0, float(h) - float(game.get('doubles', 0) or 0) - float(game.get('triples', 0) or 0) - float(game.get('home_runs', 0) or 0))
        if isinstance(field, list):
            return sum(float(game.get(f, 0) or 0) for f in field)
        val = game.get(field)
        return float(val) if val is not None else None
    
    def _ewma(self, values: List[float], alpha: float) -> float:
        """Exponentially Weighted Moving Average."""
        if not values:
            return 0.0
        result = values[0]
        for v in values[1:]:
            result = alpha * v + (1 - alpha) * result
        return result
    
    def _get_park_factor(self, team: str, stat: str) -> float:
        """Get 3-year park factor."""
        park = self.PARK_FACTORS_3YR.get(team, self.DEFAULT_PARK)
        
        if stat in ['hits', 'total_bases']:
            return park.get('hits', 1.0)
        elif stat in ['runs', 'rbis', 'hits+runs+rbis']:
            return park.get('runs', 1.0)
        elif stat == 'home_runs':
            return park.get('hr', 1.0)
        elif stat == 'pitcher_strikeouts':
            return park.get('k', 1.0)
        elif stat == 'total_bases':
            return park.get('tb', 1.0)
        return 1.0
    
    def _get_team_k_rate(self, team: str) -> float:
        """Get team's K tendency."""
        return self.TEAM_K_RATES.get(team, 1.0)

    # =========================================================================
    # 2026-04-27 — Workload anchors for pitcher props
    # =========================================================================
    # Pitcher Outs / Strikeouts μ is anchored on recent STARTS only. A
    # "start" is identified by pitch_count ≥ 60 OR innings_pitched ≥ 4.0
    # (suppresses the reliever cameo IP=0-1 logs that were collapsing
    # the projection).
    _START_MIN_PITCH_COUNT = 60
    _START_MIN_INNINGS = 4.0
    # Decaying weights over the last 4 starts (most recent first).
    _START_DECAY_WEIGHTS = [0.40, 0.30, 0.20, 0.10]

    @staticmethod
    def _decode_innings(ip: Any) -> Optional[float]:
        """
        Convert MLB-style innings notation to fractional innings.
        '5.0' → 5.000   (15 outs)
        '5.1' → 5.333   (16 outs)
        '5.2' → 5.667   (17 outs)
        Floats like 5.667 are passed through unchanged.
        """
        if ip is None:
            return None
        try:
            v = float(ip)
        except (TypeError, ValueError):
            return None
        whole = int(v)
        frac = round(v - whole, 2)
        # MLB-baseball notation only allows .0, .1, .2 in stat lines;
        # anything else is treated as already a true fraction.
        if frac in (0.0, 0.1, 0.2):
            return whole + (frac * 10.0 / 3.0)
        return v

    @classmethod
    def _is_start(cls, log: Dict[str, Any]) -> bool:
        pc = log.get('pitch_count')
        ip = cls._decode_innings(log.get('innings_pitched'))
        try:
            pc_f = float(pc) if pc is not None else 0.0
        except (TypeError, ValueError):
            pc_f = 0.0
        ip_f = ip if ip is not None else 0.0
        return pc_f >= cls._START_MIN_PITCH_COUNT or ip_f >= cls._START_MIN_INNINGS

    @classmethod
    def _expected_ip_from_starts(cls, game_logs: List[Dict[str, Any]]) -> Optional[Tuple[float, int, List[float]]]:
        """
        Returns (expected_IP_decoded, n_starts_used, raw_start_innings_list)
        from the most recent STARTS only. Returns None when fewer than
        2 starts are available (insufficient signal).
        """
        starts = [cls._decode_innings(g.get('innings_pitched'))
                  for g in game_logs if cls._is_start(g)]
        starts = [s for s in starts if s is not None and s > 0]
        if len(starts) < 2:
            return None
        # Most recent first, take up to 4
        recent = starts[:len(cls._START_DECAY_WEIGHTS)]
        weights = cls._START_DECAY_WEIGHTS[:len(recent)]
        wsum = sum(weights)
        weighted = sum(ip * w for ip, w in zip(recent, weights)) / wsum
        return weighted, len(starts), starts

    @classmethod
    def _recent_k_per_inning(cls, game_logs: List[Dict[str, Any]]) -> Optional[float]:
        """K rate per inning over recent STARTS (last 4 starts blend)."""
        starts = [g for g in game_logs if cls._is_start(g)][:4]
        total_ip = 0.0
        total_k = 0
        for g in starts:
            ip = cls._decode_innings(g.get('innings_pitched')) or 0.0
            try:
                k = float(g.get('pitcher_strikeouts') or 0)
            except (TypeError, ValueError):
                k = 0
            total_ip += ip
            total_k += k
        if total_ip <= 0:
            return None
        return total_k / total_ip

    # =========================================================================
    # 2026-04-27 — Active-lineup baseline floor for batter 0.5-line props
    # =========================================================================
    # Per-stat-family baseline μ for an active hitter (in today's
    # starting lineup with ~4 PAs). Applied as a FLOOR — if the model
    # μ is already above the baseline, the baseline does nothing.
    _ACTIVE_BASELINE = {
        'hits':           0.45,
        'singles':        0.45,
        'runs':           0.35,
        'rbis':           0.40,
        'hits+runs+rbis': 0.75,
        # Rare events keep the model's projection — baselines don't apply.
    }
    # Lineup detection heuristic: ≥2 games in the last 5 days OR a
    # confirmed lineup flag (`is_in_lineup_today`) on the master_hub
    # row (set by an upstream lineup feed once available).
    _ACTIVE_RECENT_DAYS = 5
    _ACTIVE_MIN_GAMES = 2

    @classmethod
    def _is_active_today(cls, player: Dict[str, Any], game_logs: List[Dict[str, Any]]) -> bool:
        # Confirmed lineup wins outright.
        if player.get('is_in_lineup_today') is True:
            return True
        # Count games in the recent window. Logs are stored most
        # recent first so we can scan the head.
        try:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(days=cls._ACTIVE_RECENT_DAYS)
        except Exception:
            return False
        n = 0
        for g in game_logs[:10]:
            d = g.get('date')
            if not d:
                continue
            try:
                d_parsed = datetime.fromisoformat(str(d).replace('Z', '+00:00'))
            except (TypeError, ValueError):
                continue
            if d_parsed >= cutoff:
                n += 1
                if n >= cls._ACTIVE_MIN_GAMES:
                    return True
        return False
    
    # =========================================================================
    # 2026-04-29 — Statcast feature defaults (used when SC lookup misses)
    # Each rolling/season window emits the same shape so the downstream
    # XGBoost matrix is rectangular. Sentinels are 0.0 with imputed=1.
    # =========================================================================
    _SC_BATTER_FIELDS = (
        "xwOBA", "wOBA",
        "hard_hit_rate", "barrel_rate",
        "avg_exit_velocity", "avg_launch_angle",
        "sweet_spot_rate",
        "k_rate", "whiff_rate", "contact_rate",
        "plate_appearances",
    )
    _SC_PITCHER_FIELDS = (
        "xwOBA_allowed", "wOBA_allowed",
        "hard_hit_allowed_rate", "barrel_allowed_rate",
        "k_rate", "bb_rate",
        "plate_appearances",
    )

    def _build_friction_features(
        self,
        player: Dict,
        game_logs: List[Dict],
        stat: str,
        opponent: str = None,
        park_team: str = None,
        dk_odds: int = None,
        line: float = None,
        statcast_features: Optional[Dict[str, Any]] = None,
        pitcher_statcast_features: Optional[Dict[str, Any]] = None,
        pa_batter_features: Optional[Dict[str, Any]] = None,
        pa_pitcher_features: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, float]]:
        """
        Build the FULL High-Friction feature vector.

        Categories:
        1. PvP (Pitcher-Batter Collision)
        2. Environmental Friction
        3. Market Alignment
        4. Plate Discipline & Trends
        5. Statcast Quality-of-Contact (2026-04-29 v2.0):
           - Batter rolling 7/14/30 + season xwOBA/wOBA/HardHit/Barrel/EV/LA
           - Pitcher rolling 14/30 xwOBA-allowed/HardHit-allowed/Barrel-allowed/K%/BB%
        6. Workload (expected PA derived from recent L10).
        7. PA-windowed Statcast (2026-04-29 v2.1):
           - Batter last-7/14/30 PA + season xwOBA/wOBA/HardHit/Barrel/EV/LA/SS%/K%/BB%/Whiff%/Contact%
           - Pitcher last-14/30 PA + season xwOBA-allowed/HardHit-allowed/Barrel-allowed/K%/BB%/Whiff%
        """
        features = {}
        
        # Extract stat values
        stat_values = []
        for g in game_logs[:30]:
            val = self._get_stat_value(g, stat)
            if val is not None:
                stat_values.append(val)
        
        if len(stat_values) < 5:
            return None
        
        # Slices
        l3 = stat_values[:3]
        l5 = stat_values[:5]
        l10 = stat_values[:10]
        l20 = stat_values[:20]
        
        # =====================================================================
        # CATEGORY 4: PLATE DISCIPLINE & TRENDS
        # =====================================================================
        features['l3_avg'] = np.mean(l3)
        features['l5_avg'] = np.mean(l5)
        features['l10_avg'] = np.mean(l10)
        features['l20_avg'] = np.mean(l20) if len(l20) >= 10 else np.mean(l10)
        
        features['l5_median'] = np.median(l5)
        features['l10_median'] = np.median(l10)
        
        features['l5_max'] = max(l5)
        features['l10_max'] = max(l10)
        features['l5_min'] = min(l5)
        features['l10_min'] = min(l10)
        
        # EWMA
        features['ewma_l5'] = self._ewma(l5, 0.5)
        features['ewma_l10'] = self._ewma(l10, 0.3)
        features['ewma_l20'] = self._ewma(l20, 0.2) if len(l20) >= 10 else features['ewma_l10']
        
        # Trend
        if features['ewma_l10'] > 0:
            features['ewma_trend'] = (features['ewma_l5'] - features['ewma_l10']) / features['ewma_l10']
        else:
            features['ewma_trend'] = 0
        
        # Volatility
        features['std_dev_l5'] = np.std(l5, ddof=1) if len(l5) > 1 else 0
        features['std_dev_l10'] = np.std(l10, ddof=1) if len(l10) > 1 else 0
        
        features['cv_l5'] = features['std_dev_l5'] / features['l5_avg'] if features['l5_avg'] > 0 else 0
        features['cv_l10'] = features['std_dev_l10'] / features['l10_avg'] if features['l10_avg'] > 0 else 0
        
        features['range_l5'] = features['l5_max'] - features['l5_min']
        features['range_l10'] = features['l10_max'] - features['l10_min']
        
        # Streak analysis
        if line is not None:
            hit_streak = 0
            miss_streak = 0
            for val in stat_values[:10]:
                if val > line:
                    hit_streak += 1
                else:
                    break
            for val in stat_values[:10]:
                if val <= line:
                    miss_streak += 1
                else:
                    break
            
            features['current_hit_streak'] = hit_streak
            features['current_miss_streak'] = miss_streak
            
            l5_hits = sum(1 for v in l5 if v > line)
            l10_hits = sum(1 for v in l10 if v > line)
            features['hit_rate_l5'] = l5_hits / len(l5) * 100
            features['hit_rate_l10'] = l10_hits / len(l10) * 100
        
        # =====================================================================
        # CATEGORY 1: PITCHER-BATTER COLLISION (PvP)
        # =====================================================================
        # Get handedness splits from player data
        vs_left = player.get('vs_left', {})
        vs_right = player.get('vs_right', {})
        # 2026-05 missing-value policy: flag platoon features as imputed
        # when the source split blocks aren't on the master_hub doc.
        # (Currently always missing — no external splits feed; will be
        # toggled to 0 once a splits sync lands.)
        _platoon_imputed = 0 if (vs_left or vs_right) else 1
        features['vs_lhp_is_imputed'] = _platoon_imputed
        features['vs_rhp_is_imputed'] = _platoon_imputed
        features['platoon_split_is_imputed'] = _platoon_imputed
        
        # vs LHP stats
        features['vs_lhp_ab'] = vs_left.get('at_bats', 0) or 0
        features['vs_lhp_hits'] = vs_left.get('hits', 0) or 0
        features['vs_lhp_avg'] = features['vs_lhp_hits'] / features['vs_lhp_ab'] if features['vs_lhp_ab'] > 0 else 0
        features['vs_lhp_hr'] = vs_left.get('home_runs', 0) or 0
        features['vs_lhp_k'] = vs_left.get('strikeouts', 0) or 0
        features['vs_lhp_k_rate'] = features['vs_lhp_k'] / features['vs_lhp_ab'] if features['vs_lhp_ab'] > 0 else 0
        features['vs_lhp_bb'] = vs_left.get('walks', 0) or 0
        features['vs_lhp_obp'] = (features['vs_lhp_hits'] + features['vs_lhp_bb']) / (features['vs_lhp_ab'] + features['vs_lhp_bb']) if (features['vs_lhp_ab'] + features['vs_lhp_bb']) > 0 else 0
        
        # vs RHP stats  
        features['vs_rhp_ab'] = vs_right.get('at_bats', 0) or 0
        features['vs_rhp_hits'] = vs_right.get('hits', 0) or 0
        features['vs_rhp_avg'] = features['vs_rhp_hits'] / features['vs_rhp_ab'] if features['vs_rhp_ab'] > 0 else 0
        features['vs_rhp_hr'] = vs_right.get('home_runs', 0) or 0
        features['vs_rhp_k'] = vs_right.get('strikeouts', 0) or 0
        features['vs_rhp_k_rate'] = features['vs_rhp_k'] / features['vs_rhp_ab'] if features['vs_rhp_ab'] > 0 else 0
        features['vs_rhp_bb'] = vs_right.get('walks', 0) or 0
        features['vs_rhp_obp'] = (features['vs_rhp_hits'] + features['vs_rhp_bb']) / (features['vs_rhp_ab'] + features['vs_rhp_bb']) if (features['vs_rhp_ab'] + features['vs_rhp_bb']) > 0 else 0
        
        # Platoon advantage (positive = better vs RHP)
        features['platoon_avg_split'] = features['vs_rhp_avg'] - features['vs_lhp_avg']
        features['platoon_k_split'] = features['vs_lhp_k_rate'] - features['vs_rhp_k_rate']  # Positive = strikes out more vs LHP
        
        # =====================================================================
        # CATEGORY 2: ENVIRONMENTAL FRICTION
        # =====================================================================
        # Park factors
        if park_team:
            pf = self.PARK_FACTORS_3YR.get(park_team, self.DEFAULT_PARK)
            features['park_hits_factor'] = pf.get('hits', 1.0)
            features['park_runs_factor'] = pf.get('runs', 1.0)
            features['park_hr_factor'] = pf.get('hr', 1.0)
            features['park_k_factor'] = pf.get('k', 1.0)
            features['park_tb_factor'] = pf.get('tb', 1.0)
            features['park_factor'] = self._get_park_factor(park_team, stat)
            features['park_factor_is_imputed'] = 0
        else:
            features['park_hits_factor'] = 1.0
            features['park_runs_factor'] = 1.0
            features['park_hr_factor'] = 1.0
            features['park_k_factor'] = 1.0
            features['park_tb_factor'] = 1.0
            features['park_factor'] = 1.0
            features['park_factor_is_imputed'] = 1
        
        # Home/Away splits
        home_splits = player.get('home_splits', {})
        away_splits = player.get('away_splits', {})
        # Missing-value flag: home/away splits aren't on the master_hub
        # doc; will be toggled to 0 once a splits sync lands.
        _ha_imputed = 0 if (home_splits or away_splits) else 1
        features['home_away_split_is_imputed'] = _ha_imputed
        
        home_ab = home_splits.get('at_bats', 0) or 0
        home_hits = home_splits.get('hits', 0) or 0
        away_ab = away_splits.get('at_bats', 0) or 0
        away_hits = away_splits.get('hits', 0) or 0
        
        features['home_avg'] = home_hits / home_ab if home_ab > 0 else 0
        features['away_avg'] = away_hits / away_ab if away_ab > 0 else 0
        features['home_away_split'] = features['home_avg'] - features['away_avg']
        
        features['home_runs_split'] = (home_splits.get('runs', 0) or 0) - (away_splits.get('runs', 0) or 0)
        features['home_hr_split'] = (home_splits.get('home_runs', 0) or 0) - (away_splits.get('home_runs', 0) or 0)
        
        # Opponent K rate (for pitcher strikeouts)
        if opponent:
            features['opp_k_rate'] = self._get_team_k_rate(opponent)
        else:
            features['opp_k_rate'] = 1.0
        
        # =====================================================================
        # CATEGORY 3: MARKET ALIGNMENT
        # =====================================================================
        # 2026-04-30: dk_odds-derived features removed from training inputs.
        # Investigation showed these features had 0.000 importance in v2.0
        # because the training corpus had stale/missing dk_odds in most
        # rows. More importantly, including market signal in projection
        # creates a circular reference when we then compute
        # `edge = model_p - market_p` — the model partially tracks market,
        # dampening the edge signal we're trying to extract. Market data
        # IS used downstream (TP, devig) where it belongs — for SCORING,
        # not projection. v3.0_bayes drops them entirely.
        # If a future re-train wants to bring them back, restore this
        # block and add the field names back into `_get_feature_columns`.
        if dk_odds is not None:
            # Keep the parameter signature for callers; it's just unused
            # in the feature dict now.
            pass
        # Line features
        if line is not None:
            features['line'] = line
            features['line_vs_l5'] = line - features['l5_avg']
            features['line_vs_l10'] = line - features['l10_avg']
            features['line_vs_ewma'] = line - features['ewma_l10']
            features['line_vs_median'] = line - features['l10_median']
            
            # Line difficulty (how far above average)
            features['line_difficulty'] = (line - features['l10_avg']) / features['std_dev_l10'] if features['std_dev_l10'] > 0 else 0
        
        # =====================================================================
        # CATEGORY 5: STATCAST QUALITY-OF-CONTACT (v2.0)
        # =====================================================================
        # Emit a fixed feature shape regardless of whether SC data was
        # available. Imputed flag = 1 when the lookup missed.
        #
        # 2026-04-30 (v3.0_bayes): Apply Bayesian shrinkage to every
        # rate BEFORE writing it into the feature dict. Tiny-sample
        # rolling windows (e.g. Bleday's 1 BBE → barrel_rate=1.0)
        # were producing values far outside the model's training
        # distribution, causing wild extrapolation (μ=6.74 for a
        # 2.0-mean player). The shrinkage formula
        #     shrunk = (X*N + league_avg*prior_n) / (N + prior_n)
        # smoothly pulls small-sample rates toward league average
        # while leaving large-sample rates essentially unchanged.
        # Applied at BOTH training and inference time so features
        # are always on the same scale.
        # See `services.scoring.mlb_statcast_bayes` for the math
        # and `tests/test_mlb_statcast_bayes.py` for the locked-in
        # invariants.
        from services.scoring.mlb_statcast_bayes import (
            bayes_shrink_rolling_window,
        )
        for window in ("rolling_7", "rolling_14", "rolling_30", "season_window"):
            wkey = window.replace("rolling_", "r").replace("season_window", "season")
            block = (statcast_features or {}).get(window) or {}
            block_shrunk = bayes_shrink_rolling_window(block) if block else {}
            for fld in self._SC_BATTER_FIELDS:
                v = block_shrunk.get(fld)
                features[f"sc_b_{wkey}_{fld}"] = float(v) if v is not None else 0.0
        features["sc_batter_is_imputed"] = 0 if statcast_features else 1

        # Pitcher SC features (used for pitcher-prop targets and as
        # opposing-pitcher quality if caller passes them in).
        for window in ("rolling_14", "rolling_30", "season_window"):
            wkey = window.replace("rolling_", "r").replace("season_window", "season")
            block = (pitcher_statcast_features or {}).get(window) or {}
            block_shrunk = bayes_shrink_rolling_window(block) if block else {}
            for fld in self._SC_PITCHER_FIELDS:
                v = block_shrunk.get(fld)
                features[f"sc_p_{wkey}_{fld}"] = float(v) if v is not None else 0.0
        features["sc_pitcher_is_imputed"] = 0 if pitcher_statcast_features else 1

        # =====================================================================
        # CATEGORY 6: WORKLOAD ANCHORS (expected PA / batting workload)
        # =====================================================================
        pa_vals = []
        ab_vals = []
        for g in game_logs[:10]:
            pa = g.get("plate_appearances")
            ab = g.get("at_bats")
            if pa is not None:
                try: pa_vals.append(float(pa))
                except (TypeError, ValueError): pass
            if ab is not None:
                try: ab_vals.append(float(ab))
                except (TypeError, ValueError): pass
        if pa_vals:
            features["expected_pa_l10"] = float(np.mean(pa_vals))
            features["expected_pa_is_imputed"] = 0
        elif ab_vals:
            # fallback: AB+0.4 walks ≈ PA
            features["expected_pa_l10"] = float(np.mean(ab_vals)) + 0.4
            features["expected_pa_is_imputed"] = 0
        else:
            features["expected_pa_l10"] = 4.0
            features["expected_pa_is_imputed"] = 1

        # =====================================================================
        # CATEGORY 7: PA-WINDOWED STATCAST (v2.1, 2026-04-29)
        # =====================================================================
        # Built from `mlb_statcast_raw` per-pitch rows; windows are
        # plate-appearance-counted (last 7/14/30 PA, plus season-to-date).
        # Distinct from Category 5 which is calendar-day windowed.
        from services.mlb_pa_features import (
            BATTER_FIELDS as _PA_B_FIELDS, PITCHER_FIELDS as _PA_P_FIELDS,
            BATTER_WINDOWS as _PA_B_WINDOWS, PITCHER_WINDOWS as _PA_P_WINDOWS,
        )
        for tag, _ in _PA_B_WINDOWS + (("pa_season", None),):
            block = (pa_batter_features or {}).get(tag) or {}
            for fld in _PA_B_FIELDS:
                v = block.get(fld)
                features[f"pa_b_{tag}_{fld}"] = float(v) if v is not None else 0.0
        features["pa_batter_is_imputed"] = 0 if pa_batter_features else 1

        for tag, _ in _PA_P_WINDOWS + (("pa_season", None),):
            block = (pa_pitcher_features or {}).get(tag) or {}
            for fld in _PA_P_FIELDS:
                v = block.get(fld)
                features[f"pa_p_{tag}_{fld}"] = float(v) if v is not None else 0.0
        features["pa_pitcher_is_imputed"] = 0 if pa_pitcher_features else 1

        return features
    
    def build_training_dataset(self, stat_type: str) -> pd.DataFrame:
        """
        Build training dataset with FULL High-Friction features.
        """
        logger.info(f"[MLB_HF_TRAIN] Building High-Friction dataset for {stat_type}")
        
        norm_stat = self._normalize_stat(stat_type)
        training_data = []
        
        # Get all players with historical data
        cursor = self.historical_logs.find({}, {'_id': 0})
        
        for player_doc in cursor:
            player_name = player_doc.get('player_name')
            player_id = player_doc.get('player_id')
            game_logs = player_doc.get('game_logs', [])
            
            if len(game_logs) < 20:
                continue
            
            # Sort by date
            game_logs = sorted(game_logs, key=lambda x: x.get('date') or '1900-01-01', reverse=True)
            
            # Get player master data for splits
            player_master = self.master_hub.find_one(
                {"$or": [{"display_name": player_name}, {"player_name": player_name}]},
                {"_id": 0}
            )
            
            if not player_master:
                player_master = {}
            
            # Create training samples
            for i in range(len(game_logs) - 20):
                target_game = game_logs[i]
                history = game_logs[i+1:i+31]
                
                target_value = self._get_stat_value(target_game, norm_stat)
                if target_value is None:
                    continue
                
                opponent = target_game.get('opponent_abbr')
                
                # Build features
                features = self._build_friction_features(
                    player_master,
                    history,
                    norm_stat,
                    opponent_team=opponent,
                    park_team=None,
                    dk_odds=None,
                    line=None
                )
                
                if features is None:
                    continue
                
                features['target'] = target_value
                features['player_name'] = player_name
                features['game_date'] = target_game.get('date')
                features['opponent'] = opponent
                
                training_data.append(features)
        
        df = pd.DataFrame(training_data)
        logger.info(f"[MLB_HF_TRAIN] Built {len(df)} samples with {len(df.columns) - 4} features")
        
        return df
    
    def train(self, stat_type: str, test_size: float = 0.2) -> Dict[str, Any]:
        """
        Train XGBoost High-Friction model.
        """
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import mean_absolute_error, r2_score
        
        try:
            import xgboost as xgb
        except ImportError:
            return {'error': 'XGBoost not installed'}
        
        norm_stat = self._normalize_stat(stat_type)
        logger.info(f"[MLB_HF_TRAIN] Training High-Friction model for {norm_stat}")
        
        df = self.build_training_dataset(stat_type)
        
        if len(df) < 100:
            return {'error': f'Insufficient data: {len(df)}'}
        
        exclude = ['target', 'player_name', 'game_date', 'opponent']
        feature_cols = [c for c in df.columns if c not in exclude]
        
        X = df[feature_cols].fillna(0)
        y = df['target']
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42)
        
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        model = xgb.XGBRegressor(
            n_estimators=250,
            max_depth=7,
            learning_rate=0.08,
            subsample=0.85,
            colsample_bytree=0.85,
            min_child_weight=3,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_train_scaled, y_train)
        
        train_pred = model.predict(X_train_scaled)
        test_pred = model.predict(X_test_scaled)
        
        train_mae = mean_absolute_error(y_train, train_pred)
        test_mae = mean_absolute_error(y_test, test_pred)
        train_r2 = r2_score(y_train, train_pred)
        test_r2 = r2_score(y_test, test_pred)
        
        # Feature importance
        importance = dict(zip(feature_cols, model.feature_importances_))
        importance = dict(sorted(importance.items(), key=lambda x: -x[1])[:25])
        
        # Store
        self.models[norm_stat] = model
        self.scalers[norm_stat] = scaler
        self.feature_cols[norm_stat] = feature_cols
        
        metrics = {
            'stat_type': norm_stat,
            'n_samples': len(df),
            'n_features': len(feature_cols),
            'train': {'mae': round(train_mae, 4), 'r2': round(train_r2, 4)},
            'test': {'mae': round(test_mae, 4), 'r2': round(test_r2, 4)},
            'top_features': importance
        }
        
        logger.info(f"[MLB_HF_TRAIN] {norm_stat}: MAE={test_mae:.4f}, R²={test_r2:.4f}, Features={len(feature_cols)}")
        
        return metrics
    
    def save_models(self):
        """Save trained models."""
        for stat in self.models:
            data = {
                'model': self.models[stat],
                'scaler': self.scalers[stat],
                'features': self.feature_cols[stat],
                'version': 'MLB_HF_v3.0_bayes',
                'trained_at': datetime.now(timezone.utc).isoformat()
            }
            path = os.path.join(self.MODEL_DIR, f'mlb_hf_{stat}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"[MLB_HF_MODEL] Saved {stat} to {path}")
    
    def load_models(self) -> int:
        """Load trained models."""
        # 2026-04-29 — verify on-disk SHA256 matches `.LOCKED` manifest
        # (warns only; load proceeds either way).
        try:
            from services.mlb_model_lock import assert_load_ok
            assert_load_ok()
        except Exception as e:
            logger.warning(f"[MLB_HF_MODEL] lock-integrity check failed: {e!r}")
        loaded = 0
        for stat in self.MLB_STAT_TYPES:
            path = os.path.join(self.MODEL_DIR, f'mlb_hf_{stat}.pkl')
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        data = pickle.load(f)
                    self.models[stat] = data['model']
                    self.scalers[stat] = data['scaler']
                    self.feature_cols[stat] = data['features']
                    loaded += 1
                    logger.info(f"[MLB_HF_MODEL] Loaded {stat}")
                except Exception as e:
                    logger.error(f"[MLB_HF_MODEL] Failed to load {stat}: {e}")
        
        logger.info(f"[MLB_HF_MODEL] Loaded {loaded}/{len(self.MLB_STAT_TYPES)} models")
        return loaded

    # -------------------------------------------------------------------
    # 2026-04-27 — Pitcher Outs analytical projection.
    # No XGBoost model on disk for pitcher_outs (the previous alias to
    # pitcher_strikeouts was the bug). μ is derived directly from
    # workload:  μ_outs = expected_IP × 3, with σ from std(starts) × 3.
    # -------------------------------------------------------------------
    def _predict_pitcher_outs(
        self,
        player_name: Optional[str],
        stat_type: str,
        line: Optional[float],
        opponent_team: Optional[str],
        park_team: Optional[str],
        dk_odds: Optional[int],
        bdl_player_id: Optional[int],
    ) -> Dict[str, Any]:
        # Resolve the player by ID (preferred) or name fallback.
        player = None
        if bdl_player_id is not None:
            try:
                pid_int = int(bdl_player_id)
                player = self.master_hub.find_one(
                    {"$or": [{"bdl_player_id": pid_int}, {"bdl_id": pid_int}]},
                    {"_id": 0},
                )
            except (TypeError, ValueError):
                player = None
        if player is None and player_name:
            player = self.master_hub.find_one(
                {"$or": [
                    {"display_name": player_name},
                    {"player_name": player_name},
                    {"mlb_full_name": player_name},
                ]}, {"_id": 0},
            )
        if not player:
            return {"error": f"Player not found: {player_name}"}

        game_logs = player.get("bdl_game_logs", [])
        if not game_logs:
            return {"error": "No game logs"}

        ip_block = self._expected_ip_from_starts(game_logs)
        if ip_block is None:
            return {"error": "Insufficient starts (need ≥2)"}
        expected_ip, n_starts, start_innings = ip_block

        mu_outs = expected_ip * 3.0
        # σ from variation across starts in OUTS units.
        starts_outs = [ip * 3.0 for ip in start_innings]
        sigma_outs = float(np.std(starts_outs, ddof=1)) if len(starts_outs) > 1 else mu_outs * 0.18

        # Probability for the gates' legacy `prob_over` consumer
        # (the universal probability engine downstream re-derives this
        # from μ/σ/CV anyway, so this is purely informational).
        prob_over = None
        z_score = None
        if line is not None and sigma_outs > 0:
            z_score = (float(line) - mu_outs) / sigma_outs
            prob_over = (1.0 - stats.norm.cdf(z_score)) * 100.0

        logger.info(
            f"[MLB_HF_PRED] {player_name} Pitcher Outs: "
            f"expected_IP={expected_ip:.2f} starts={n_starts} "
            f"μ={mu_outs:.2f} σ={sigma_outs:.2f}"
        )
        return {
            "player_name": player_name,
            "stat_type": stat_type,
            "predicted": round(mu_outs, 2),
            "raw_prediction": round(mu_outs, 4),
            "std_dev": round(sigma_outs, 4),
            "line": line,
            "prob_over": round(prob_over, 1) if prob_over is not None else None,
            "z_score": round(z_score, 4) if z_score is not None else None,
            "friction_audit": {
                "trends": {
                    "expected_ip": round(expected_ip, 2),
                    "starts_used": n_starts,
                },
            },
            "full_features": {},
            "mlr_features_used": False,
            "model_version": "MLB_HF_v1.0_pitcher_outs_analytical",
            "feature_health": {"imputed_count": 0, "imputed_features": []},
            # μ-override audit fields (parity with model path)
            "mu_raw_model_projection": round(mu_outs, 4),
            "mu_pitcher_workload_anchored": True,
            "mu_active_baseline_applied": False,
            "mu_active_baseline_value": None,
            "expected_ip_used": round(expected_ip, 2),
        }


    
    def predict(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None,
        park_team: str = None,
        dk_odds: int = None,
        bdl_player_id: int = None,
    ) -> Dict[str, Any]:
        """
        Generate High-Friction prediction.
        
        NO FALLBACKS - returns error if features can't be built.

        Global Identity Rule (2026-04-23): when `bdl_player_id` is
        provided, the hub row is resolved by ID (canonical identity).
        `player_name` is retained only as a fallback for legacy callers
        and is never used when an ID is supplied.
        """
        norm_stat = self._normalize_stat(stat_type)

        # 2026-04-27 — Pitcher Outs analytical projection.
        # No XGBoost model; μ derives from expected_IP × 3 (workload
        # anchor). σ is std(starts) × 3 so the universal probability
        # engine receives a coherent CV signal. Bypasses model.predict().
        if norm_stat == 'pitcher_outs':
            return self._predict_pitcher_outs(
                player_name=player_name, stat_type=stat_type,
                line=line, opponent_team=opponent_team, park_team=park_team,
                dk_odds=dk_odds, bdl_player_id=bdl_player_id,
            )

        if norm_stat not in self.models:
            return {"error": f"No model for {stat_type}"}
        
        try:
            # Find player — prefer ID-based identity (Global Identity Rule).
            player = None
            if bdl_player_id is not None:
                try:
                    pid_int = int(bdl_player_id)
                    player = self.master_hub.find_one(
                        {"$or": [
                            {"bdl_player_id": pid_int},
                            {"bdl_id": pid_int},
                        ]},
                        {"_id": 0},
                    )
                except (TypeError, ValueError):
                    player = None
            if player is None and player_name:
                # Legacy name lookup retained only when ID is absent.
                player = self.master_hub.find_one(
                    {"$or": [
                        {"display_name": player_name},
                        {"player_name": player_name},
                        {"mlb_full_name": player_name}
                    ]},
                    {"_id": 0}
                )
            
            if not player:
                return {"error": f"Player not found: {player_name}"}
            
            game_logs = player.get('bdl_game_logs', [])
            if len(game_logs) < 5:
                return {"error": f"Insufficient games: {len(game_logs)}"}
            
            # Build HIGH-FRICTION features
            # 2026-04-29 v2.0: also fetch latest Statcast features.
            # Pitcher props use pitcher SC; batter props use batter SC.
            sc_batter = None
            sc_pitcher = None
            pitcher_stat_set = {"pitcher_strikeouts", "pitcher_walks",
                                "hits_allowed", "earned_runs", "pitcher_outs"}
            if norm_stat in pitcher_stat_set:
                sc_pitcher = self._get_pitcher_sc_latest(player)
            else:
                sc_batter = self._get_batter_sc_latest(player)

            # 2026-04-29 v2.1: PA-windowed Statcast lookup (live).
            pa_batter = None
            pa_pitcher = None
            mlbam_id = self._resolve_mlbam_id(player)
            if mlbam_id is not None:
                # Use today's date as the as_of cutoff (everything strictly
                # before "today" counts).
                from datetime import datetime as _dt
                as_of = _dt.utcnow().strftime("%Y-%m-%d")
                pa_cache = self._get_pa_cache()
                if pa_cache is not None:
                    if norm_stat in pitcher_stat_set:
                        pa_pitcher = pa_cache.pitcher_features(int(mlbam_id), as_of)
                    else:
                        pa_batter = pa_cache.batter_features(int(mlbam_id), as_of)

            features = self._build_friction_features(
                player,
                game_logs,
                norm_stat,
                opponent_team,
                park_team,
                dk_odds,
                line,
                statcast_features=sc_batter,
                pitcher_statcast_features=sc_pitcher,
                pa_batter_features=pa_batter,
                pa_pitcher_features=pa_pitcher,
            )
            
            if features is None:
                return {"error": "Could not build friction features"}
            
            # Get model
            model = self.models[norm_stat]
            scaler = self.scalers[norm_stat]
            feature_cols = self.feature_cols[norm_stat]
            
            # Prepare features
            X = pd.DataFrame([features])
            for col in feature_cols:
                if col not in X.columns:
                    X[col] = 0
            
            X = X[feature_cols].fillna(0)
            X_scaled = scaler.transform(X)
            
            # HIGH-PRECISION PREDICTION
            raw_pred = float(model.predict(X_scaled)[0])
            
            # Apply park factor
            park_factor = features.get('park_factor', 1.0)
            opp_k_rate = features.get('opp_k_rate', 1.0)
            
            # Final prediction with friction modifiers
            if norm_stat == 'pitcher_strikeouts':
                final_pred = raw_pred * park_factor * opp_k_rate
            else:
                final_pred = raw_pred * park_factor

            # =================================================================
            # 2026-04-27 — μ overrides (workload anchor + active baseline)
            # -----------------------------------------------------------------
            # Probability engine, distribution selection, and CV/σ logic are
            # NOT modified. Only the μ feeding them is corrected:
            #   • Pitcher K: blend the model μ with a workload-anchored μ
            #     (expected_IP × K_per_inning) at 40% model / 60% workload.
            #   • Batter 0.5-line stats (Hits / Singles / Runs / RBIs / HRR):
            #     enforce an active-lineup baseline FLOOR so cold L5
            #     stretches don't collapse μ to 0.02 for confirmed starters.
            # =================================================================
            mu_raw_model_projection = final_pred
            mu_pitcher_workload_anchored = False
            mu_active_baseline_applied = False
            mu_active_baseline_value = None
            expected_ip_used = None

            if norm_stat == 'pitcher_strikeouts':
                ip_block = self._expected_ip_from_starts(game_logs)
                kpi = self._recent_k_per_inning(game_logs)
                if ip_block is not None and kpi is not None:
                    expected_ip, _n_starts, _raw_starts = ip_block
                    workload_mu = expected_ip * kpi
                    # 60/40 workload-vs-model blend (per 2026-04-27 plan).
                    final_pred = 0.6 * workload_mu + 0.4 * final_pred
                    expected_ip_used = round(expected_ip, 2)
                    mu_pitcher_workload_anchored = True

            # Batter active-lineup baseline floor (0.5-line families only).
            if norm_stat in self._ACTIVE_BASELINE:
                if self._is_active_today(player, game_logs):
                    baseline = self._ACTIVE_BASELINE[norm_stat]
                    if final_pred < baseline:
                        final_pred = baseline
                        mu_active_baseline_applied = True
                        mu_active_baseline_value = baseline
            
            # TRUE L10 SIGMA
            std_dev = features.get('std_dev_l10', 0)
            l10_avg = features.get('l10_avg', final_pred)
            cv = std_dev / l10_avg if l10_avg > 0 else 0.5
            
            # MLB VOLATILITY FLOOR
            if norm_stat in ['hits', 'total_bases', 'rbis', 'runs', 'hits+runs+rbis', 'home_runs']:
                if cv < 0.35:
                    std_dev = l10_avg * 0.35
            
            # STANDARD NORMAL CDF PROBABILITY
            prob_over = None
            z_score = None
            
            if line is not None and std_dev > 0:
                z_score = (line - final_pred) / std_dev
                prob_over = (1 - stats.norm.cdf(z_score)) * 100
                
                # STRICT: If Prediction < Line, Probability MUST be < 50%
                if final_pred < line and prob_over >= 50:
                    prob_over = 50 - abs(z_score) * 10  # Force below 50
                    prob_over = max(5, prob_over)
            
            # Build friction audit
            friction_audit = {
                'pvp': {
                    'vs_lhp_avg': features.get('vs_lhp_avg'),
                    'vs_rhp_avg': features.get('vs_rhp_avg'),
                    'vs_lhp_k_rate': features.get('vs_lhp_k_rate'),
                    'vs_rhp_k_rate': features.get('vs_rhp_k_rate'),
                    'platoon_split': features.get('platoon_avg_split'),
                },
                'environment': {
                    'park_team': park_team,
                    'park_factor': park_factor,
                    'park_hits': features.get('park_hits_factor'),
                    'park_runs': features.get('park_runs_factor'),
                    'park_k': features.get('park_k_factor'),
                    'opp_k_rate': opp_k_rate,
                    'home_away_split': features.get('home_away_split'),
                },
                'market': {
                    'dk_odds': dk_odds,
                    'dk_implied_prob': features.get('dk_implied_prob'),
                    'dk_vig_removed': features.get('dk_vig_removed_prob'),
                },
                'trends': {
                    'l5_avg': features.get('l5_avg'),
                    'l10_avg': features.get('l10_avg'),
                    'ewma_l10': features.get('ewma_l10'),
                    'ewma_trend': features.get('ewma_trend'),
                    'cv_l10': features.get('cv_l10'),
                    'hit_rate_l5': features.get('hit_rate_l5'),
                    'hit_rate_l10': features.get('hit_rate_l10'),
                }
            }
            
            # 2026-05 missing-value policy — emit a feature_health
            # block summarising which features were silent defaults vs
            # real values. Surface up via the predict() return so the
            # scoring adapter can stamp it on the score doc.
            imputed_features = sorted(
                k.replace("_is_imputed", "")
                for k, v in features.items()
                if k.endswith("_is_imputed") and v == 1
            )
            feature_health = {
                "imputed_count": len(imputed_features),
                "imputed_features": imputed_features,
            }

            result = {
                'player_name': player_name,
                'stat_type': stat_type,
                'predicted': round(final_pred, 2),
                'raw_prediction': round(raw_pred, 4),
                'std_dev': round(std_dev, 4),
                'line': line,
                'prob_over': round(prob_over, 1) if prob_over is not None else None,
                'z_score': round(z_score, 4) if z_score is not None else None,
                'friction_audit': friction_audit,
                'full_features': friction_audit,
                'mlr_features_used': True,
                'model_version': 'MLB_HF_v3.0_bayes',
                'feature_health': feature_health,
                # 2026-04-27 — μ-override audit fields
                'mu_raw_model_projection': round(mu_raw_model_projection, 4),
                'mu_pitcher_workload_anchored': mu_pitcher_workload_anchored,
                'mu_active_baseline_applied': mu_active_baseline_applied,
                'mu_active_baseline_value': mu_active_baseline_value,
                'expected_ip_used': expected_ip_used,
            }
            
            logger.info(
                f"[MLB_HF_PRED] {player_name} {stat_type}: pred={final_pred:.2f}, "
                f"park={park_factor:.2f}, opp_k={opp_k_rate:.2f}, σ={std_dev:.3f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[MLB_HF_MODEL] Predict failed: {e}")
            return {"error": str(e)}


# Global instance
_mlb_hf_instance = None

def get_mlb_high_friction_model(db=None):
    """Get global MLB High-Friction model."""
    global _mlb_hf_instance
    if _mlb_hf_instance is None and db is not None:
        _mlb_hf_instance = MLBHighFrictionModel(db)
    return _mlb_hf_instance


# -----------------------------------------------------------------------------
# Stage 3 (2026-04-20, MLB↔NBA carbon-copy): canonical MLB live-model shim.
#
# Every live call site (MLBAdapter, oracle_apex_service, rolling_cache_manager,
# mlb_scoring adapter) routes through `predict_live()` so MLB has EXACTLY ONE
# live model path — `MLBHighFrictionModel`. The returned object exposes the
# attribute shape legacy callers used with MLBPhysicalEngine (`is_valid`,
# `mlr_predicted`, `sigma_used`, `mlr_matchup`, `vk_prob_over`, `vk_prob_under`,
# `vk_edge`, `vk_verdict`, `sigma_source`, `z_score`, `error`). This eliminates
# the live-path model cascade (Physical → VegasKiller → HighFriction) without
# churning every caller's attribute accesses. Eliminates deviation D12.
# -----------------------------------------------------------------------------

class _LiveMLBPrediction:
    """Attribute-access wrapper around MLBHighFrictionModel.predict() output."""
    __slots__ = (
        "is_valid", "error",
        "mlr_predicted", "sigma_used", "sigma_source",
        "vk_prob_over", "vk_prob_under", "vk_edge", "vk_verdict",
        "z_score", "mlr_matchup",
    )

    def __init__(
        self, *, is_valid: bool, error: Optional[str] = None,
        mlr_predicted: Optional[float] = None,
        sigma_used: Optional[float] = None,
        sigma_source: Optional[str] = None,
        vk_prob_over: Optional[float] = None,
        vk_prob_under: Optional[float] = None,
        vk_edge: Optional[float] = None,
        vk_verdict: Optional[str] = None,
        z_score: Optional[float] = None,
        mlr_matchup: Optional[Dict[str, Any]] = None,
    ):
        self.is_valid = is_valid
        self.error = error
        self.mlr_predicted = mlr_predicted
        self.sigma_used = sigma_used
        self.sigma_source = sigma_source
        self.vk_prob_over = vk_prob_over
        self.vk_prob_under = vk_prob_under
        self.vk_edge = vk_edge
        self.vk_verdict = vk_verdict
        self.z_score = z_score
        self.mlr_matchup = mlr_matchup or {}


def _build_mlr_matchup_from_friction(audit: Dict[str, Any]) -> Dict[str, Any]:
    """Translate HF friction_audit into the legacy `mlr_matchup` shape so
    downstream display/scoring code continues to work unchanged."""
    pvp = (audit or {}).get("pvp") or {}
    env = (audit or {}).get("environment") or {}
    trends = (audit or {}).get("trends") or {}
    # Pick the side-specific matchup_avg; callers only need a single value.
    matchup_avg = pvp.get("vs_rhp_avg")
    if matchup_avg is None:
        matchup_avg = pvp.get("vs_lhp_avg") or 0.0
    return {
        "park":    {"venue": env.get("park_team"), "factor": env.get("park_factor", 1.0),
                    "park_hits": env.get("park_hits"), "park_runs": env.get("park_runs"),
                    "park_k": env.get("park_k")},
        "splits":  {"matchup_avg": matchup_avg or 0.0,
                    "platoon_split": pvp.get("platoon_split") or pvp.get("platoon_avg_split") or 0.0,
                    "vs_lhp_avg": pvp.get("vs_lhp_avg"),
                    "vs_rhp_avg": pvp.get("vs_rhp_avg")},
        "opponent": {"k_rate": env.get("opp_k_rate")},
        "trends":  {"l10_avg": trends.get("l10_avg") or 0.0,
                    "l5_avg": trends.get("l5_avg"),
                    "cv_l10": trends.get("cv_l10"),
                    "hit_rate_l10": trends.get("hit_rate_l10")},
        "discipline": {},
        "variance": {"cv_l10": trends.get("cv_l10")},
    }


def predict_live(
    db,
    *,
    player_name: str,
    stat_type: str,
    line: Optional[float],
    opponent_team: Optional[str] = None,
    park_team: Optional[str] = None,
    pitcher_hand: Optional[str] = None,
    dk_odds: Optional[int] = None,
) -> _LiveMLBPrediction:
    """Single canonical MLB live-model entry point. Returns a
    `_LiveMLBPrediction` wrapping the MLBHighFrictionModel output."""
    model = get_mlb_high_friction_model(db)
    if model is None:
        return _LiveMLBPrediction(is_valid=False, error="hf_model_unavailable")
    if not model.models:
        try:
            model.load_models()
        except Exception as e:
            return _LiveMLBPrediction(is_valid=False, error=f"hf_load_failed:{e}")
        if not model.models:
            return _LiveMLBPrediction(is_valid=False, error="hf_models_empty")
    try:
        raw = model.predict(
            player_name=player_name, stat_type=stat_type, line=line,
            opponent_team=opponent_team, park_team=park_team,
            dk_odds=int(dk_odds) if dk_odds else None,
        )
    except Exception as e:
        return _LiveMLBPrediction(is_valid=False, error=f"hf_predict_failed:{e}")
    if not raw or raw.get("error"):
        return _LiveMLBPrediction(is_valid=False, error=(raw or {}).get("error") or "hf_no_result")

    predicted = raw.get("predicted")
    std_dev = raw.get("std_dev")
    prob_over = raw.get("prob_over")  # percentage 0-100
    z_score = raw.get("z_score")
    friction = raw.get("friction_audit") or raw.get("full_features") or {}

    if predicted is None or std_dev is None or prob_over is None:
        return _LiveMLBPrediction(
            is_valid=False, error="hf_missing_fields",
            mlr_predicted=predicted, sigma_used=std_dev, z_score=z_score,
        )

    # Market-implied vs model-implied edge.
    vk_edge = None
    if dk_odds is not None:
        try:
            o = float(dk_odds)
            market_prob_pct = (abs(o) / (abs(o) + 100.0) * 100.0) if o < 0 else (100.0 / (o + 100.0) * 100.0)
            vk_edge = round(prob_over - market_prob_pct, 1)
        except (TypeError, ValueError):
            vk_edge = None
    verdict = "OVER" if prob_over >= 50.0 else "UNDER"

    return _LiveMLBPrediction(
        is_valid=True,
        error=None,
        mlr_predicted=round(float(predicted), 3),
        sigma_used=round(float(std_dev), 4),
        sigma_source="hf_empirical",
        vk_prob_over=round(float(prob_over), 1),
        vk_prob_under=round(100.0 - float(prob_over), 1),
        vk_edge=vk_edge,
        vk_verdict=verdict,
        z_score=round(float(z_score), 4) if z_score is not None else None,
        mlr_matchup=_build_mlr_matchup_from_friction(friction),
    )
