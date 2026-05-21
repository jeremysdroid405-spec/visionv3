"""Sport-agnostic Pydantic schemas for the universal replay harness.

Collection names are derived from the SportReplayAdapter at runtime via
`audit.runs_collection_name(adapter)` etc. — the Schema constants below
are the per-sport defaults if needed standalone.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class InputCollectionPin(BaseModel):
    count: int
    scope: Optional[Dict[str, Any]] = None
    max_as_of_date_le_gamedate: Optional[str] = None


class ProductionReplayRun(BaseModel):
    """One doc per replay run. Sport recorded inside; collection is
    sport-prefixed externally via `audit.runs_collection_name(adapter)`.
    """
    serial: str
    sport: str
    game_date: str
    snapshot_iso: str
    tier: str

    production_pipeline_version: str
    scoring_config_version: str
    gate_config_version: str
    model_versions: Dict[str, str]
    feature_cache_version: str
    adapter_version: str   # SHA of sport adapter module — Phase-2 addition
    git_commit_sha: Optional[str] = None

    input_collection_versions: Dict[str, InputCollectionPin]

    replay_started_at: datetime
    replay_completed_at: Optional[datetime] = None
    elapsed_s: Optional[float] = None
    rss_mb_peak: Optional[float] = None

    rows_scanned: Optional[int] = None
    rows_qualified: Optional[int] = None
    cards_displayed: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    hit_rate_pct: Optional[float] = None
    roi_pct: Optional[float] = None
    profit_units: Optional[float] = None

    mode: str = "historical"
    dry_run: bool = False
    notes: Optional[str] = None


class ProductionReplayOutput(BaseModel):
    replay_serial: str
    sport: str
    game_date: str
    snapshot_iso: str
    event_id: str
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    commence_time: Optional[str] = None

    player_name: Optional[str] = None
    player_name_normalized: str
    stat_family: str
    market: str
    is_alternate: bool
    line: float
    side: str
    book: str
    odds: int

    projection_mu: float
    sigma: float
    model_probability: float
    fair_probability: float
    implied_probability: float
    edge: float

    hit_rate_l5: Optional[float] = None
    hit_rate_l10: Optional[float] = None
    hit_rate_l20: Optional[float] = None
    cv: Optional[float] = None

    tier: str
    gate_pass: bool
    failed_gates: List[str] = Field(default_factory=list)
    gate_config_version: str


class ProductionReplayCard(BaseModel):
    replay_serial: str
    sport: str
    tier: str
    game_id: str
    rank: int

    # 2026-05-21 — propagate event/game metadata onto the card so
    # downstream regraders / analytics never need to re-join to outputs.
    event_id: Optional[str] = None
    commence_time: Optional[str] = None
    game_date: Optional[str] = None

    player_name: str
    player_name_normalized: str
    stat_family: str
    market: str
    is_alternate: bool
    line: float
    side: str
    book: str
    odds: int
    odds_was_best_among_n_books: int

    projection_mu: float
    model_probability: float
    edge: float

    rank_key_hr_l10: float
    rank_key_hr_l20: float
    rank_key_hr_l5: float
    rank_key_edge: float

    actual_value: Optional[float] = None
    grade_status: str
    profit_units: float = 0.0
    stake_units: float = 1.0
