"""Pydantic schemas for the 3 new production-replay collections.

Phase 1: schema definitions only. No collections are created or indexed
by this file. Phase 2 will use these schemas to validate documents
before insert and to drive index creation.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# Collection names (kept here as the canonical source of truth)
RUNS_COLL    = "mlb_production_replay_runs"
OUTPUTS_COLL = "mlb_production_replay_outputs"
CARDS_COLL   = "mlb_production_replay_cards"


class InputCollectionPin(BaseModel):
    count: int
    scope: Optional[Dict[str, Any]] = None
    max_as_of_date_le_gamedate: Optional[str] = None


class ProductionReplayRun(BaseModel):
    """One doc per replay invocation. Manifest + audit trail."""
    serial: str
    sport: str = "mlb"
    game_date: str
    snapshot_iso: str
    tier: str

    # Version pins
    production_pipeline_version: str
    scoring_config_version: str
    gate_config_version: str
    model_versions: Dict[str, str]   # stat_family → pickle file name
    feature_cache_version: str
    git_commit_sha: Optional[str] = None

    # Input fingerprints
    input_collection_versions: Dict[str, InputCollectionPin]

    # Runtime
    replay_started_at: datetime
    replay_completed_at: Optional[datetime] = None
    elapsed_s: Optional[float] = None
    rss_mb_peak: Optional[float] = None

    # Headline metrics (denormalized for quick listing)
    rows_scanned: Optional[int] = None
    rows_qualified: Optional[int] = None
    cards_displayed: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    hit_rate_pct: Optional[float] = None
    roi_pct: Optional[float] = None
    profit_units: Optional[float] = None

    # Run mode + flags
    mode: str = "historical"
    dry_run: bool = False
    notes: Optional[str] = None


class ProductionReplayOutput(BaseModel):
    """One doc per scored prop. EVERY prop the production pipeline saw
    on this date×snapshot, gated AND ungated. The full audit pool."""
    replay_serial: str
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

    # Model output
    projection_mu: float
    sigma: float
    model_probability: float
    fair_probability: float
    implied_probability: float
    edge: float

    # Rolling features used by gates
    hit_rate_l5: Optional[float] = None
    hit_rate_l10: Optional[float] = None
    hit_rate_l20: Optional[float] = None
    cv: Optional[float] = None

    # Gate outcome (one row per prop × tier we evaluated)
    tier: str
    gate_pass: bool
    failed_gates: List[str] = Field(default_factory=list)
    gate_config_version: str


class ProductionReplayCard(BaseModel):
    """One doc per FINAL DISPLAYED-CARD pick after dedupe-to-best-book
    + top-N-per-game. This is what would have been shown to the user."""
    replay_serial: str
    tier: str
    game_id: str
    rank: int   # within (tier × game), 1..N

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

    # Sort-key components, denormalized for reproducibility
    rank_key_hr_l10: float
    rank_key_hr_l20: float
    rank_key_hr_l5: float
    rank_key_edge: float

    # Outcome (grading)
    actual_value: Optional[float] = None
    grade_status: str    # win / loss / push / ungraded
    profit_units: float = 0.0
    stake_units: float = 1.0
