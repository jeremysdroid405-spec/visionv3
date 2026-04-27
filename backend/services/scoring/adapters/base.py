"""
ScoringAdapter base class — sport-agnostic contract.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ScoringContext:
    """Standardized scoring context produced by a sport adapter."""
    canonical_key: str
    sport: str
    event_id: Optional[str]
    player_name: Optional[str]
    stat_type: Optional[str]
    line: Optional[float]
    recommendation: Optional[str]  # "OVER" / "UNDER"
    # Layer objects (all optional)
    pp_layer: Optional[Dict[str, Any]] = None
    dk_layer: Optional[Dict[str, Any]] = None
    fd_layer: Optional[Dict[str, Any]] = None  # 2026-04-27 — MLB market source
    mgm_layer: Optional[Dict[str, Any]] = None
    bol_layer: Optional[Dict[str, Any]] = None  # 2026-04-27 — MLB tier fallback only
    sharp_layer: Optional[Dict[str, Any]] = None
    # Model + stats inputs
    p_model: Optional[float] = None          # final model probability (0-1)
    cv: Optional[float] = None
    cv_status: Optional[str] = None  # computed | unavailable_stat_family | missing_source_distribution | not_supported_yet
    # 0.5-line margin metrics (2026-05). Replace cv_gate for binary
    # MLB props in the gate engine. Computed for every MLB prop;
    # left None on NBA. The engine consults them only when
    # `metrics.line == 0.5` AND `metrics.sport == "mlb"`.
    avg_hit_margin: Optional[float] = None
    avg_miss_margin: Optional[float] = None
    # Universal HR status (2026-04-23). Mirror of cv_status for the
    # hit-rate pipeline so a null hit_rate is distinguishable from a
    # legitimate 0% and callers can act on the distinction.
    hit_rate_status: Optional[str] = None  # computed | unavailable_stat_family | missing_source_distribution
    # Source of `model_projection` / `model_sigma` on this prop:
    #   "model"       — direct VK / VK2 model prediction
    #   "combo_synth" — synthesized from two component model projections
    #                   (e.g. pts_reb = pts_model + reb_model, sigma from
    #                    empirical covariance of the two stats).
    # None when no projection is available (family has no model).
    projection_method: Optional[str] = None
    # PRA dual-projection audit fields (2026-04-23). When both the
    # direct model and the 3-way component synth produce a valid
    # projection for a PRA prop, both are stamped side-by-side so we
    # can evaluate them against actuals later. Live production
    # behaviour is unchanged — `model_projection` / `model_sigma`
    # still carry whatever the current live pipeline chose.
    model_projection_direct: Optional[float] = None
    model_sigma_direct: Optional[float] = None
    model_projection_synth: Optional[float] = None
    model_sigma_synth: Optional[float] = None
    projection_delta_abs: Optional[float] = None
    projection_delta_pct: Optional[float] = None
    projection_compare_status: Optional[str] = None  # both_available | direct_only | synth_only | neither
    projection_primary_method: Optional[str] = None   # mirrors live projection_method, preserved for audit
    hit_rate: Optional[float] = None
    edge_pct: Optional[float] = None
    tp: Optional[float] = None
    ceiling_rate: Optional[float] = None
    books_available_count: int = 0
    # Passthrough full prop (used by Universal Gate Engine input
    # normalization + diagnostic enrichers)
    raw_prop: Dict[str, Any] = field(default_factory=dict)
    # Multiplier hints (for pp_utility when real data exists)
    pp_combo_multiplier: Optional[float] = None
    pp_label: Optional[str] = None           # goblin | standard | demon
    pp_multiplier_model: Optional[float] = None
    # Diagnostic-only: alternative p_true candidates (not used by scoring_stack)
    p_true_hit_rate: Optional[float] = None
    p_true_model: Optional[float] = None
    p_true_method: Optional[str] = None       # "hit_rate" | "model" | "vk2"
    model_projection: Optional[float] = None  # raw regressor output (stat units)
    model_sigma: Optional[float] = None       # residual SD used for CDF conversion
    # VK2 (5-year adv-stat) diagnostics
    p_true_vk2: Optional[float] = None
    vk2_projection: Optional[float] = None
    vk2_sigma: Optional[float] = None
    vk2_error: Optional[str] = None
    # Side-aware hit-rate diagnostics (the one passed to gates is stored in hit_rate)
    hit_rate_over: Optional[float] = None
    hit_rate_under: Optional[float] = None
    # Sample size behind hit_rate{,_over,_under} (2026-04-25, HR v3).
    # Number of valid game logs used to compute HR. `None` when HR
    # itself is None or when the source path doesn't surface it (NBA
    # is currently always 20 by construction → leaves it None).
    # Gate engine reads this to apply small-sample penalty when
    # 10 ≤ n < 20 and to fail INSUFFICIENT_SAMPLE when n < 10.
    hit_rate_sample_size: Optional[int] = None
    # Global Identity Rule (2026-04-23): canonical player identity
    # stamped at ingest. `bdl_player_id` is the join key for every
    # downstream stat / projection computation; `player_name` is
    # display-only. `identity_status` is "resolved" when a
    # `bdl_player_id` is present on the raw prop, otherwise
    # "missing_bdl_id" — in which case HR / CV / model projections
    # must be skipped.
    bdl_player_id: Optional[int] = None
    identity_status: Optional[str] = None
    # Expected-minutes composition (2026-04-23). Narrow rollout:
    # NBA PTS / PRA only, only when min_played_L10_mean <
    # _MIN_BENCH_THRESHOLD (bench regime). When applied,
    # `vk2_projection` / `model_projection` already reflect the
    # composed value; these fields capture the audit trail so we
    # can replay baseline vs composed downstream.
    minutes_composition_applied: Optional[bool] = None
    minutes_composition_baseline_projection: Optional[float] = None
    minutes_composition_predicted_minutes: Optional[float] = None
    minutes_composition_per_min_rate: Optional[float] = None
    # 2026-05 missing-value policy — feature-health summary surfaced
    # by the underlying ML model (`vk.predict()` / `hf.predict()`).
    # `imputed_features` lists feature names that the model received
    # as silent defaults rather than real values. Persisted on every
    # score doc so observability / gates can see the data-deficit
    # surface. Live behaviour is unchanged — model still receives its
    # training default; this just makes the deficit explicit.
    feature_health: Optional[Dict[str, Any]] = None
    # 2026-05 injury context — NBA-only team-level aggregates. NOT a
    # model input (VK is not trained on injuries). Carried for
    # observability and downstream consumption.
    injury_context: Optional[Dict[str, Any]] = None


class ScoringAdapter(ABC):
    """Sport-specific scoring adapter contract."""

    # ---------------------------------------------------------
    # Metadata
    # ---------------------------------------------------------
    @property
    @abstractmethod
    def sport(self) -> str:
        """Lowercase sport key, e.g. 'mlb', 'nba'."""

    @property
    @abstractmethod
    def live_props_collection(self) -> str:
        """Mongo collection with the canonical (or canonicalizable) live props."""

    @property
    @abstractmethod
    def scores_collection(self) -> str:
        """Output collection name, e.g. 'mlb_prop_scores'."""

    @property
    @abstractmethod
    def cached_board_collection(self) -> str:
        """Collection that must NOT be mutated by recompute (used for leak checks)."""

    # ---------------------------------------------------------
    # Behaviour
    # ---------------------------------------------------------
    @abstractmethod
    async def load_live_props(
        self, db, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Read-only load from the sport's live props collection."""

    @abstractmethod
    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        """Normalize a raw prop into a ScoringContext. Return None to skip."""

    # ---------------------------------------------------------
    # Optional: Enrich a score doc at WRITE time.
    # ---------------------------------------------------------
    # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): sport-specific enrichers
    # that previously ran at route time (e.g. MLB tempo + intel_suite) now
    # run once inside `recompute_sport` and get persisted into
    # `{sport}_prop_scores`. Default implementation is a no-op so NBA is
    # unaffected. Returns a dict of additional fields to merge into the
    # score doc (must be in `_SCORE_OUTPUT_FIELDS` to survive projection).
    def enrich_score_doc(
        self, raw_prop: Dict[str, Any], ctx: "ScoringContext"
    ) -> Dict[str, Any]:
        return {}

    # ---------------------------------------------------------
    # Optional: derive the canonical_key from a raw prop WITHOUT scoring.
    # ---------------------------------------------------------
    # Phase D1 (2026-04-21, Delta Engine): the detector compares
    # live_props canonical_keys against scored RT canonical_keys to surface
    # NEW / RETIRED props. Some sport ingests already persist
    # `canonical_key` on the raw prop doc (MLB via universal_odds_sync);
    # others (NBA's legacy per-book odds path) do not. Default behaviour:
    # return whatever's already on the raw prop. Sport adapters may
    # override this to compute the same key their `build_context` would
    # produce — allowing the detector to do set-diff without mutating
    # ingest. MUST be consistent with `build_context().canonical_key`.
    def canonical_key_from_raw(self, raw_prop: Dict[str, Any]) -> Optional[str]:
        ck = raw_prop.get("canonical_key")
        return ck if isinstance(ck, str) and ck else None
