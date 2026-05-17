"""Sport-specific adapter abstract base.

The replay harness is sport-agnostic. Everything that varies between
MLB, NBA, NFL, etc. lives behind this single interface:

  - Odds collection name + match keys
  - Feature cache collection name + stat-family mapping
  - Model loader and inference call shape
  - Grading data source + per-stat-family field map
  - Lineup/context resolution

A `SportReplayAdapter` subclass IS the only place sport-specific code
lives. The universal harness (Phase 2c) injects whichever adapter the
caller chooses based on the `--sport` CLI arg.

Phase 1+2 design constraint:
  All adapters MUST be pure (no live API calls in historical mode).
  Adapters return data; the harness does scoring/gating/grading.
"""
from __future__ import annotations
import abc
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SportFixedConfig:
    """Static metadata about a sport that doesn't depend on date or run."""
    sport: str                          # "mlb" | "nba" | "nfl"
    odds_collection: str                # historical odds collection name
    feature_cache_collection: str       # feature cache collection name
    master_hub_collection: str          # master hub collection name
    game_log_array_field: str           # nested array path holding logs
    default_pipeline_files: Tuple[str, ...]   # sport's scoring source files
    tier_short_codes: Dict[str, str]    # {"safe_haven": "SH", ...}


class SportReplayAdapter(abc.ABC):
    """Abstract sport-specific adapter.

    Subclasses must be importable without performing any IO; constructor
    takes the live db handle and is expected to defer all reads to the
    actual method calls.
    """
    # Class-level metadata — subclasses override or use the property
    SPORT: str = ""

    def __init__(self, db: Any):
        self._db = db

    @property
    @abc.abstractmethod
    def config(self) -> SportFixedConfig: ...

    # ── Stat-family normalization ────────────────────────────────────
    @abc.abstractmethod
    def normalize_stat_family(self, market: str,
                                replay_family: Optional[str] = None) -> str:
        """Map (market, replay-family) to a model/grading family key."""

    @abc.abstractmethod
    def list_stat_families(self) -> List[str]:
        """Every family this sport's models can produce."""

    # ── Model loader / predict ───────────────────────────────────────
    @abc.abstractmethod
    def load_model(self) -> Any:
        """Load and cache the sport's prediction model instance."""

    @abc.abstractmethod
    def predict(self, *, model: Any,
                player_name: str, stat_family: str,
                line: float, opponent_team: Optional[str],
                home_team: Optional[str], away_team: Optional[str],
                is_away: bool,
                feature_cache_row: Optional[Dict[str, Any]],
                as_of_date: str) -> Dict[str, Any]:
        """Run inference. Must accept all-historical inputs. Must NOT
        emit live API calls or read live collections when historical
        mode is active."""

    # ── Grading ──────────────────────────────────────────────────────
    @abc.abstractmethod
    async def fetch_actuals(self, *, game_date: str) -> Dict[str, Dict[str, float]]:
        """Return `{player_name_normalized: {stat_family: actual_value}}`
        for the given game date. The actual_value semantics depend on
        sport; for MLB it is the per-game stat aggregate."""

    @abc.abstractmethod
    def grade_outcome(self, *, actual: Optional[float],
                       line: float, side: str, odds: int,
                       stake: float = 1.0) -> Dict[str, Any]:
        """Grade a single prop. Return dict with keys
        `status` (win/loss/push/ungraded), `profit_units`, `stake_units`."""

    # ── Event / context resolution ───────────────────────────────────
    @abc.abstractmethod
    async def resolve_opp_pitcher(self, *, event_id: str,
                                    game_date: str, home_team: str,
                                    away_team: str, is_away: bool,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        """Resolve opposing pitcher details for an event. MLB-only —
        other sports return None."""

    @abc.abstractmethod
    async def resolve_opposing_lineup(self, *, event_id: str,
                                        game_date: str, opp_team: str,
                                        as_of_date: str) -> Optional[List[Dict[str, Any]]]:
        """Resolve opposing lineup for an event. Sport-specific shape."""

    # ── Adapter identity / hash ──────────────────────────────────────
    def adapter_version(self) -> str:
        """Adapter version pin — concatenated module file SHA."""
        import hashlib, importlib
        mod = importlib.import_module(self.__class__.__module__)
        try:
            src = open(mod.__file__, "rb").read()
        except Exception:
            src = b""
        return hashlib.sha256(src).hexdigest()
