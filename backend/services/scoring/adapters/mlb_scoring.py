"""
MLB Scoring Adapter — reads mlb_live_props, produces ScoringContext.

Reuses MLBHighFrictionModel (XGBoost) for p_true predictions; all gate
evaluation routes through the Universal Gate Engine.
No odds-sync, no mutation of live props or cached_board.
"""
import os
import logging
from typing import Any, Dict, List, Optional

from services.scoring.adapters.base import ScoringAdapter, ScoringContext

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Phase 1 context field propagation (2026-05-15)
# ──────────────────────────────────────────────────────────────────────
# Three context fields that already exist somewhere in the system but
# were dropped before reaching the score doc. This helper is the ONE
# place that bridges the field-name mismatch between live_props /
# master_hub and the canonical score-doc schema.
#
#   batting_order  — present on live_props (~48.9% filled). Score doc
#                    schema previously aliased to `lineup_spot` causing
#                    the field to be dropped at allowlist time.
#   batter_hand    — present on master_hub as `bats` ('L'/'R'/'S').
#                    Normalised to canonical 'L' / 'R' / 'S'.
#   venue          — present on live_props as `venue` (stadium label).
#                    Just need to allowlist it on score doc.
# ──────────────────────────────────────────────────────────────────────
_BATTER_HAND_NORMALISE = {
    "L": "L", "LEFT": "L", "LH": "L", "LHB": "L",
    "R": "R", "RIGHT": "R", "RH": "R", "RHB": "R",
    # Switch-hitters. Master_hub format is `bats_throws="Both/<Throws>"`.
    "S": "S", "B": "S", "BOTH": "S", "SWITCH": "S", "SH": "S",
}


def _normalise_batter_hand(raw) -> Optional[str]:
    if raw is None:
        return None
    return _BATTER_HAND_NORMALISE.get(str(raw).strip().upper())


def _propagate_phase1_context(
    prop: Dict[str, Any], master_hub, bdl_player_id: Optional[int],
) -> None:
    """In-place stamping of canonical Phase-1 context fields on ``prop``.

    Reads from ``master_hub`` (in-memory by ``bdl_player_id``) and from
    the prop's own live-ingest fields, writes only canonical names so
    downstream (recompute / score doc / model input remap) doesn't
    need to know about the field-name aliases.

    Failure modes are intentional silent skips — Phase 1 is meant to
    LIFT projection coverage without ever breaking scoring on a row
    that simply doesn't have the data.
    """
    # ── batter_hand from master_hub.bats / bats_throws ────────────
    if not prop.get("batter_hand"):
        try:
            player_doc = None
            if master_hub is not None and bdl_player_id is not None:
                # `MLBHighFrictionModel.master_hub` is a sync pymongo
                # Collection — read by bdl_id. Tiny doc, projection
                # limits payload, no perf concern.
                try:
                    pid = int(bdl_player_id)
                except (TypeError, ValueError):
                    pid = None
                if pid is not None:
                    if hasattr(master_hub, "find_one"):
                        player_doc = master_hub.find_one(
                            {"$or": [
                                {"bdl_player_id": pid},
                                {"bdl_id": pid},
                            ]},
                            {"_id": 0, "bats": 1, "bats_throws": 1},
                        )
                    elif hasattr(master_hub, "get"):
                        # Dict-style master_hub (used in unit tests).
                        player_doc = master_hub.get(pid)
            if player_doc:
                # Primary: `bats` field. Production data has it `None`
                # for nearly all hitters as of 2026-05; the populated
                # form is `bats_throws` = "<Bats>/<Throws>" (e.g.
                # "Right/Right", "Both/Right"). Parse first half.
                raw_bats = player_doc.get("bats")
                if not raw_bats:
                    bt = player_doc.get("bats_throws")
                    if isinstance(bt, str) and "/" in bt:
                        raw_bats = bt.split("/", 1)[0]
                    elif isinstance(bt, dict):
                        raw_bats = bt.get("bats")
                normalised = _normalise_batter_hand(raw_bats)
                if normalised:
                    prop["batter_hand"] = normalised
        except Exception:  # pragma: no cover
            pass

    # ── batting_order from live_props ─────────────────────────────
    # Already present under canonical name on live_props for ~48.9%
    # of active props. Setdefault so we don't clobber if it was
    # already stamped upstream.
    if prop.get("batting_order") is None:
        # Fall back to common alias names emitted by older ingest
        # paths so propagation works on legacy rows too.
        for alias in ("lineup_spot", "lineup_position", "bo"):
            v = prop.get(alias)
            if v is not None:
                prop["batting_order"] = v
                break

    # ── venue from live_props (kept canonical, no remap needed) ───
    # The field already exists on live_props as `venue`; nothing to
    # transform — propagation only requires score-doc allowlist
    # (see `prop_scores_store._SCORE_OUTPUT_FIELDS`).

    # ── Phase 2A (2026-05-15) — Pitcher Matchup Flags ─────────────
    # Derive `same_hand_matchup` / `opposite_hand_matchup` from the
    # batter_hand × opp_pitcher_throws pair. Switch-hitters ('S')
    # always face the opposite hand (they swing from the side that
    # opposes the pitcher) so encode as opposite_hand=1.
    #
    # Flags are written ONLY when both inputs are known so the
    # downstream Step-5 missing-value audit can distinguish
    # 0=known-platoon-match from None=unknown.
    bh = prop.get("batter_hand")
    ph = prop.get("opp_pitcher_throws")
    if bh and ph:
        bh_u = str(bh).strip().upper()
        ph_u = str(ph).strip().upper()
        if bh_u == "S":
            prop["same_hand_matchup"] = 0
            prop["opposite_hand_matchup"] = 1
        elif bh_u in ("L", "R") and ph_u in ("L", "R"):
            prop["same_hand_matchup"] = 1 if bh_u == ph_u else 0
            prop["opposite_hand_matchup"] = 1 - prop["same_hand_matchup"]
        else:
            prop["same_hand_matchup"] = None
            prop["opposite_hand_matchup"] = None
    else:
        prop["same_hand_matchup"] = None
        prop["opposite_hand_matchup"] = None


# ──────────────────────────────────────────────────────────────────────


class MLBScoringAdapter(ScoringAdapter):
    def __init__(self):
        self._stats_cache = None
        self._hf_model = None

    @property
    def sport(self) -> str:
        return "mlb"

    @property
    def live_props_collection(self) -> str:
        return "mlb_live_props"

    @property
    def scores_collection(self) -> str:
        return "mlb_prop_scores"

    @property
    def cached_board_collection(self) -> str:
        return "mlb_cached_board"

    async def load_live_props(self, db, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        # 2026-05-13 — OOM defence: pin server-side batch_size so motor
        # streams in 200-doc chunks instead of allocating the full
        # collection in a single wire buffer. With 16k+ MLB live props
        # and per-prop layer objects this measurably reduces peak
        # memory during the to_list materialization.
        cursor = db[self.live_props_collection].find(
            {}, {"_id": 0}
        ).batch_size(200)
        if limit:
            cursor = cursor.limit(int(limit))
        props = await cursor.to_list(length=None)
        logger.info(f"[MLB_SCORING] Loaded {len(props)} live props from {self.live_props_collection}")

        # 0-Book Exclusion Rule (2026-04-22): any prop with no exact-line
        # anchor from DraftKings / FanDuel / BetMGM / BetOnline is marked
        # pp_only and MUST NOT enter scoring, tiering, or the cached board.
        # This is the single MLB chokepoint — every scoring run funnels
        # through `load_live_props`, so filtering here covers delta,
        # master-sync, and recompute paths uniformly.
        from services.scoring.coverage_filter import (
            filter_priceable, filter_pp_playable,
        )
        priceable, coverage_stats = filter_priceable(props, sport="mlb")
        # Attach stats for the caller (pipeline logs / sync-result JSON).
        self.last_coverage_stats = coverage_stats

        # Multi-book de-vig TP engine (2026-04-22). Build the companion
        # (OVER ↔ UNDER) map over the FULL props list — not just the
        # priceable subset — so UNDER picks whose OVER companion was
        # pp_only-dropped still get their UNDER-side reference. The
        # companion map is attached to the instance so build_context
        # can reuse it without re-scanning live_props per prop.
        from services.scoring.tp_engine import build_companion_map
        self._companion_map = build_companion_map(props)

        # PP Side-Aware Playability Filter (2026-05). Universal across
        # NBA / MLB / NFL — a prop is eligible for scoring ONLY when
        # PrizePicks itself listed THAT EXACT player + stat + line + side.
        # Sportsbook-fallback rows (e.g., DK-only UNDER 0.5 stolen_bases
        # when PP listed only OVER) are dropped here so they never enter
        # tiering, rejects, or Safe Haven. Companion map above is built
        # over the full pre-filter pool so OVER-side de-vig pairing is
        # preserved when its UNDER twin gets dropped.
        pp_playable, pp_stats = filter_pp_playable(priceable, sport="mlb")
        self.last_pp_playable_stats = pp_stats
        return pp_playable

    def _get_stats_cache(self, db):
        """Return the MLB stat-utility cache (hit-rate / CV / ceiling rate
        lookups backed by BDL splits + historical logs). POST 2026-04-22
        Universal Gate Engine cleanup this helper no longer holds any
        gate logic; every threshold / reason code lives in
        `services.scoring.gates`. The class is kept as a stats cache
        only."""
        if self._stats_cache is None:
            from services.mlb_tier_sorter import MLBTierSorter
            self._stats_cache = MLBTierSorter(db)
        return self._stats_cache

    def _get_hf_model(self, db):
        if self._hf_model is None:
            import pymongo as _pymongo
            sync_client = _pymongo.MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client[os.environ.get('DB_NAME', 'pick_vision')]
            from services.mlb_high_friction_model import get_mlb_high_friction_model
            self._hf_model = get_mlb_high_friction_model(sync_db)
            if self._hf_model and not self._hf_model.models:
                self._hf_model.load_models()
        return self._hf_model

    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        """Normalize an mlb_live_props doc into a ScoringContext."""
        # Parity with NBA (2026-05): pull `p_true_method` override from
        # `config.override_config.vision_score.p_true_method`. MLB has
        # no vk2 model on disk today so the value is informational, but
        # the signature must mirror NBA so the shared
        # `resolve_p_true_ladder` receives `preferred_method` from
        # both sports.
        active_method_early = (
            ((config or {}).get("override_config") or {})
            .get("vision_score", {})
            .get("p_true_method")
        ) or "model"

        # Stats cache (hit-rate / CV / ceiling lookups). Name kept
        # intentionally generic post Universal Gate Engine cleanup —
        # no gate logic lives here.
        stats = self._get_stats_cache(db)
        if not getattr(stats, "_player_logs_cache", None):
            await stats._load_caches()

        player_name = prop.get("player_name")
        # Global Identity Rule (2026-04-23): `bdl_player_id` is the
        # canonical join key. Stamped at ingest by
        # `services/universal_odds_sync._stamp_identity_on_props`.
        # Absence is reported as `identity_status="missing_bdl_id"`
        # and downstream ID-based computations are skipped.
        bdl_player_id_raw = prop.get("bdl_player_id")
        bdl_player_id: Optional[int] = None
        if bdl_player_id_raw is not None:
            try:
                bdl_player_id = int(bdl_player_id_raw)
            except (TypeError, ValueError):
                bdl_player_id = None
        identity_status = "resolved" if bdl_player_id is not None else "missing_bdl_id"
        stat_type = prop.get("stat_type")
        line = prop.get("line")
        if line is None or player_name is None or stat_type is None:
            return None

        canon_key = prop.get("canonical_key")
        if not canon_key:
            canon_key = (
                f"mlb|{prop.get('event_id','?')}|{player_name}|{stat_type}|"
                f"{float(line)}|{prop.get('recommendation','OVER')}"
            )

        # Layers: prefer nested layers (new schema), else hydrate from flat fields.
        def _hydrate(book, ln_key, od_key, layer_key):
            nested = prop.get(layer_key)
            if nested:
                return nested
            ln = prop.get(ln_key)
            od = prop.get(od_key)
            if ln is None and od is None:
                return None
            return {"book": book, "line": ln, "odds": od}

        pp_layer = _hydrate("prizepicks", "pp_line", "pp_odds", "pp_layer") or {
            "book": "prizepicks", "line": line, "odds": prop.get("odds")
        }
        dk_layer = _hydrate("draftkings", "dk_line", "dk_odds", "dk_layer")
        # 2026-04-27 — FD is now a market source for MLB vision_score
        # (`scoring_stack._pick_fair_probability` MLB chain).
        fd_layer = _hydrate("fanduel", "fd_line", "fd_odds", "fd_layer")
        mgm_layer = _hydrate("betmgm", "mgm_line", "mgm_odds", "mgm_layer")
        # 2026-04-27 — BOL is the last fallback in the MLB tier-routing
        # chain (`scoring_stack._pick_reference_odds`). Not a vision
        # source — only used to bucket props when DK/FD/MGM are absent.
        bol_layer = _hydrate("betonlineag", "bol_line", "bol_odds", "bol_layer")
        sharp_layer = prop.get("sharp_layer") or (
            {"book": prop.get("sharp_book"), "line": prop.get("sharp_line"),
             "odds": prop.get("sharp_odds")}
            if prop.get("sharp_odds") is not None else None
        )

        # Stats from hub — ID-based joins only (Global Identity Rule).
        if bdl_player_id is None:
            cv = None
            cv_status = "missing_bdl_id"
            hit_rate = None
            hit_rate_over = None
            hit_rate_under = None
            hit_rate_status = "missing_bdl_id"
            hr_sample_size = None
            hit_rate_l5 = None
            hit_rate_l10 = None
            ceiling_rate = None
            avg_hit_margin = None
            avg_miss_margin = None
        else:
            cv = stats._calculate_cv(bdl_player_id, stat_type)
            cv_status = "computed" if cv is not None else "missing_source_distribution"
            # 2026-05 — 0.5-line margin metrics. Computed for every
            # MLB prop; the engine only consumes them when line=0.5.
            # See `gates/engine.py::evaluate` MLB+0.5 swap block.
            avg_hit_margin, avg_miss_margin = (
                stats._calculate_line_margins(bdl_player_id, stat_type, line)
            )
            # HR (2026-04-25, NBA-parity). Sibling method returns the
            # OVER/UNDER pair plus the sample size used. NBA-parity
            # `min_games=5` floor mirrors NBA `_compute_cv_and_hit_rate`
            # at `nba_scoring.py:1050`. No sample-size penalty applied
            # in the gate engine for either sport.
            hit_rate_over, hit_rate_under, _hr_avg, hr_sample_size = (
                stats._calculate_hit_rate_sides(
                    bdl_player_id, stat_type, line, num_games=20, min_games=5,
                )
            )
            side_for_hr = (prop.get("recommendation") or "OVER").upper()
            if "UNDER" in side_for_hr:
                hit_rate = hit_rate_under
            else:
                hit_rate = hit_rate_over
            hit_rate_status = (
                "computed" if hit_rate is not None
                else "missing_source_distribution"
            )
            # 2026-05-01 — sub-window hit rates for the universal L5
            # sub-gate (`gates/engine.py:_eval_hit_rate`) and for
            # recent-form display alongside the L20 gate input.
            hit_rate_l5, _l5_n = stats._calculate_subwindow_hit_rate(
                bdl_player_id, stat_type, line,
                side=side_for_hr, window=5, min_games=4,
            )
            hit_rate_l10, _l10_n = stats._calculate_subwindow_hit_rate(
                bdl_player_id, stat_type, line,
                side=side_for_hr, window=10, min_games=4,
            )
            ceiling_rate = stats._calculate_ceiling_hit_rate(
                bdl_player_id, stat_type, line,
            )

        # Model — gated on identity resolution (Global Identity Rule).
        hf_model = self._get_hf_model(db)
        p_true_model = None
        model_projection = None
        model_sigma = None
        # Fix C (2026-04-25): label the source of `model_projection`.
        # MLB has no combo-synth path today, so the only valid value
        # is "model" (or None when the HF model is unavailable / the
        # bdl_player_id is missing).
        projection_method: Optional[str] = None
        hf_feature_health = None
        if hf_model and bdl_player_id is not None:
            # 2026-05 feature-activation: prefer the hydrated 3-letter
            # abbr (`opponent_team`/`park_team`) written by
            # `feature_hydration.py`. Fall back to the legacy
            # `is_away_team`-based derivation when hydration didn't run.
            opponent = (
                prop.get("opponent_team")
                or (prop.get('away_team') if not prop.get('is_away_team')
                    else prop.get('home_team'))
            )
            park_team = (
                prop.get("park_team")
                or (prop.get('home_team') if prop.get('is_away_team')
                    else prop.get('team'))
            )
            dk_odds_int = None
            if dk_layer and dk_layer.get("odds") is not None:
                try:
                    dk_odds_int = int(dk_layer["odds"])
                except (TypeError, ValueError):
                    dk_odds_int = None
            result = hf_model.predict(
                player_name=player_name, stat_type=stat_type, line=line,
                opponent_team=opponent, park_team=park_team, dk_odds=dk_odds_int,
                bdl_player_id=bdl_player_id,
            )
            # 2026-05 missing-value policy — capture HF feature_health
            # so the score doc preserves which features were imputed.
            hf_feature_health = (result or {}).get("feature_health")
            if result and not result.get("error") and result.get("prob_over") is not None:
                prob_over_pct = result["prob_over"]
                # Side-aware flip: MLB stores over-prob; flip for UNDER picks
                # so p_true_model always reflects the side we're picking.
                side = (prop.get("recommendation") or "OVER").upper()
                # Preserve projection + sigma so recompute can populate
                # model_projection and ranking_score_v2 for MLB.
                model_projection = result.get("predicted")
                model_sigma = result.get("std_dev")
                # 2026-04-27 — μ-override audit fields (workload anchor /
                # active-lineup baseline / pitcher_outs analytical path).
                # Stamped on the raw prop so recompute's mirror block
                # propagates them onto the score doc.
                prop["mu_raw_model_projection"] = result.get("mu_raw_model_projection")
                prop["mu_pitcher_workload_anchored"] = result.get(
                    "mu_pitcher_workload_anchored", False,
                )
                prop["mu_active_baseline_applied"] = result.get(
                    "mu_active_baseline_applied", False,
                )
                prop["mu_active_baseline_value"] = result.get("mu_active_baseline_value")
                prop["expected_ip_used"] = result.get("expected_ip_used")
                prop["projection_model_version"] = result.get("model_version")

                # ============================================================
                # 2026-05-15 — PHASE 1 CONTEXT FIELD PROPAGATION
                # ------------------------------------------------------------
                # Audit identified three already-available context fields
                # that were silently dropped between live_props and the
                # score doc. Phase 1 propagation only — no new feeds yet.
                #   • batting_order  — already on live_props (~48.9% filled)
                #   • batter_hand    — derive from master_hub.bats
                #   • venue          — already on live_props
                # See `_propagate_phase1_context` for the lookup contract.
                # ============================================================
                _propagate_phase1_context(prop, hf_model.master_hub, bdl_player_id)

                # ============================================================
                # 2026-05-14 — EMPIRICAL-BAYES SHRINKAGE MOVED IN FRONT OF
                # THE PROBABILITY ENGINE (Order-of-Ops fix).
                # ------------------------------------------------------------
                # PREVIOUS BUG: EB ran AFTER `compute_probability`, so the
                # probability was computed off the RAW HF projection (e.g.
                # 0.61 for Andy Pages HRR) while `model_projection`
                # displayed to users was the EB-shrunk value (1.306).
                # Result: p_model collapsed toward 50% even when projection
                # said player would clear the 0.5 line by 2.6x.
                #
                # FIX: apply EB first → distribution engine sees the
                # canonical projection that the user sees. ECDF still gets
                # `raw_prediction` (training-distribution parity, computed
                # below using `result.get("raw_prediction")`).
                #
                # Whitelisted families: home_runs / rbis / total_bases /
                # hits+runs+rbis. Skipped families return None → projection
                # unchanged. Live behavior on non-whitelisted families is
                # identical to before this fix.
                # ============================================================
                try:
                    from services.scoring.mlb_eb_shrinkage import apply_eb_shrinkage
                    _shrunk, _eb_audit = apply_eb_shrinkage(
                        master_hub=hf_model.master_hub,
                        bdl_player_id=bdl_player_id,
                        stat_type=stat_type,
                        raw_projection=model_projection,
                    )
                    for _k, _v in _eb_audit.items():
                        prop[_k] = _v
                    if _shrunk is not None:
                        model_projection = _shrunk
                except Exception as _eb_exc:
                    logger.debug(f"[MLB_SCORING] EB shrinkage skipped: {_eb_exc}")

                # ============================================================
                # 2026-04-27 — DISTRIBUTION-BASED PROBABILITY LAYER (new base).
                # ------------------------------------------------------------
                # Replaces the HF model's internal heuristic Gaussian (which
                # applied a `prob_over = 50 - |z|*10` override when the bare
                # CDF disagreed with the projection vs line). This new layer
                # uses μ from the MLR projection engine (HF) and σ derived
                # from the player's CV (with per-family floors). The bare
                # normal-CDF result is used unchanged — no policy override.
                #
                # Inputs are NOT changed: HF projection is the same μ,
                # `cv` is the same per-player value already feeding gates.
                # Only the μ/σ → probability conversion changes.
                # ============================================================
                # 2026-04-27 — universal probability engine.
                # Routes (sport, stat_family, line) to the right
                # distribution (Normal / Bernoulli / Poisson / NB) via
                # `services.probability.distribution`. The engine
                # carries μ_floor / line cap / CV floor / over-dispersion
                # logic per family — see calibration/mlb.py.
                from services.probability.distribution import (
                    compute_probability,
                )
                dist_result = compute_probability(
                    sport="mlb",
                    stat_family=stat_type,
                    mu=model_projection,
                    line=line,
                    cv=cv,
                )
                if dist_result is not None:
                    if "UNDER" in side:
                        p_true_model = round(dist_result.p_under, 4)
                    else:
                        p_true_model = round(dist_result.p_over, 4)
                    # ---- universal audit fields -----------------------
                    prop["distribution_p_over"] = round(dist_result.p_over, 4)
                    prop["distribution_p_under"] = round(dist_result.p_under, 4)
                    prop["distribution_kind"] = dist_result.distribution
                    prop["distribution_selector_reason"] = dist_result.selector_reason
                    prop["distribution_clamped"] = dist_result.clamped
                    # Continuous-distribution fields (Normal / NB)
                    prop["distribution_sigma"] = dist_result.sigma
                    prop["distribution_sigma_source"] = dist_result.sigma_source
                    prop["distribution_effective_mu"] = dist_result.effective_mu
                    prop["distribution_mu_floor_applied"] = dist_result.mu_floor_applied
                    prop["distribution_mu_floor_capped"] = dist_result.mu_floor_capped
                    prop["distribution_cv_floor_applied"] = dist_result.cv_floor_applied
                    # Count-distribution fields (Poisson / NB)
                    prop["distribution_lambda"] = dist_result.lambda_
                    prop["distribution_threshold"] = dist_result.threshold
                    prop["distribution_dispersion_r"] = dist_result.dispersion_r
                    # Bernoulli field
                    prop["distribution_p_param"] = dist_result.p_param
                    # Legacy Gaussian retained for diff-vs-base observability.
                    prop["raw_gaussian_p_over"] = round(
                        float(prob_over_pct) / 100.0, 4,
                    )
                    # MLB Probability Rebuild (2026-04-29):
                    #   probability_method = "distribution_mlb_v1" once the
                    # distribution layer succeeds. The downstream ECDF /
                    # LOM blocks are now SHADOW-only (see flags below).
                    prop["probability_method"] = "distribution_mlb_v1"
                    prop["p_distribution"] = round(
                        dist_result.p_under if "UNDER" in side
                        else dist_result.p_over, 4)
                else:
                    # Fall back to HF's internal Gaussian when μ or line
                    # are missing (rare path).
                    if "UNDER" in side:
                        p_true_model = round((100.0 - prob_over_pct) / 100.0, 4)
                    else:
                        p_true_model = round(prob_over_pct / 100.0, 4)
                    prop["probability_method"] = "gaussian"
                    prop["p_distribution"] = p_true_model
                # 2026-05 P0 — `raw_prediction` is the un-modified
                # `model.predict()[0]` value the HF model produced
                # before the live-only park_factor / opp_k_rate
                # multipliers were applied. The ECDF artifacts on
                # disk were trained on this exact value (see
                # `scripts/train_mlb_ecdf_artifacts.regenerate_pairs`),
                # so `raw_prediction` is what we MUST send to the
                # ECDF lookup to keep training-distribution parity.
                # `model_projection` (= post-modifier `predicted`)
                # remains the displayed projection on the score doc.
                raw_prediction = result.get("raw_prediction")
                # Fix C (2026-04-25): stamp projection_method as soon
                # as a valid HF prediction is in hand. Falls back to
                # None when prediction is rejected upstream.
                if model_projection is not None:
                    projection_method = "model"

                # --- Empirical-Bayes post-shrinkage ---
                # 2026-05-14 MOVED to BEFORE compute_probability — see
                # comment block above the distribution engine call.
                # This stub stays so any reference to "EB applied after
                # probability" in older docs has a single grep hit.

                # --- Universal ECDF probability — SHADOW ONLY (2026-04-29) ---
                # MLB Probability Rebuild: ECDF no longer overrides
                # `p_true_model` for live MLB. The distribution layer
                # above is the canonical model probability (matching
                # the NBA pattern: projection μ + σ + line → P).
                # ECDF output is still computed and persisted in
                # shadow fields for audit / future re-enable.
                if model_projection is not None:
                    try:
                        from services.probability import get_universal_ecdf
                        canonical_stat = hf_model._normalize_stat(stat_type)
                        ecdf_projection = (
                            float(raw_prediction)
                            if raw_prediction is not None
                            else float(model_projection)
                        )
                        ecdf_pred = get_universal_ecdf().predict_over_probability(
                            sport="mlb",
                            stat_family=canonical_stat,
                            projection=ecdf_projection,
                            line=float(line),
                        )
                    except Exception as _exc:
                        ecdf_pred = None
                    if ecdf_pred is not None:
                        # SHADOW persistence only — do NOT touch
                        # `p_true_model` or `probability_method`.
                        prop["raw_gaussian_p_over"] = round(
                            float(prob_over_pct) / 100.0, 4,
                        )
                        prop["ecdf_p_over"] = round(ecdf_pred.p_over, 4)
                        prop["ecdf_bucket"] = int(ecdf_pred.bucket)
                        prop["ecdf_bucket_n"] = int(ecdf_pred.bucket_n)
                        prop["ecdf_version"] = ecdf_pred.version
                        prop["p_ecdf_shadow"] = round(
                            (1.0 - ecdf_pred.p_over) if "UNDER" in side
                            else ecdf_pred.p_over, 4)
                        prop["probability_method_shadow_ecdf"] = "ecdf_shadow"

                # --- Universal Line-Outcome Model (LOM) — SHADOW ONLY ---
                # MLB Probability Rebuild (2026-04-29): LOM is fully
                # disabled as a live MLB probability source. It is no
                # longer permitted to:
                #   • set p_true_model
                #   • set probability_method (live)
                #   • influence p_model / edge_pct / tier ranking
                # The model is still invoked so the calibrated output
                # is persisted for shadow comparison, but every live
                # consumer reads only the distribution layer above.
                if model_projection is not None:
                    try:
                        from services.probability.line_outcome import (
                            get_universal_lom,
                        )
                        canonical_stat = hf_model._normalize_stat(stat_type)
                        lom_projection = (
                            float(raw_prediction)
                            if raw_prediction is not None
                            else float(model_projection)
                        )
                        lom_p_over = get_universal_lom().predict_proba_over(
                            sport="mlb",
                            stat_family=canonical_stat,
                            projection=lom_projection,
                            line=float(line),
                            sigma=(
                                float(model_sigma)
                                if model_sigma is not None else None
                            ),
                            hit_rate_at_line=hit_rate_over,
                            hit_rate_sample_size=hr_sample_size,
                            cv=cv,
                            avg_hit_margin=avg_hit_margin,
                            avg_miss_margin=avg_miss_margin,
                        )
                    except Exception as _exc:
                        lom_p_over = None
                    if lom_p_over is not None:
                        # SHADOW persistence ONLY — do NOT touch
                        # `p_true_model` or `probability_method`.
                        prop["lom_p_over"] = round(lom_p_over, 4)
                        prop["lom_version"] = "v1-no-market"
                        prop["p_lom_shadow"] = round(
                            (1.0 - lom_p_over) if "UNDER" in side
                            else lom_p_over, 4)
                        prop["probability_method_shadow"] = "lom_shadow"
                # MLB-LOM live disablement marker (read by audit tools).
                prop["lom_disabled"] = True

        # Books available
        books = 0
        if pp_layer: books += 1
        if dk_layer: books += 1
        if mgm_layer: books += 1
        if sharp_layer: books += 1

        # ---- Multi-book de-vigged TP (2026-04-22) ----------------------
        # Single-prop path using `{book}_odds_opp` captured at extract
        # time — no companion-row lookup needed. Mathematically correct
        # per-book de-vig average across every book (DK/FD/MGM/BOL)
        # that quotes BOTH sides of this exact line. No 50% fallback.
        from services.scoring.tp_engine import compute_tp
        side_for_tp = (prop.get("recommendation") or "OVER").upper()
        tp_result = compute_tp(prop=prop, side=side_for_tp)
        tp = tp_result["tp"]
        tp_books_used = tp_result["tp_books_used"]
        tp_books_list = tp_result["tp_books_list"]
        tp_method = tp_result["tp_method"]
        tp_unavailable = tp is None
        prop["tp"] = tp
        prop["tp_books_used"] = tp_books_used
        prop["tp_books_list"] = tp_books_list
        prop["tp_method"] = tp_method
        prop["tp_unavailable"] = tp_unavailable
        # Fix B (2026-04-25): mirror the source label fields onto
        # `prop` so the recompute conditional mirror block
        # (recompute.py:582-587) propagates them onto the score doc.
        # `tp_source ∈ {"devig", "one_sided", None}`;
        # `tp_unavailable_reason` is the typed reason when tp is None.
        # `market_probability` is the 0..1 rescaled tp for UI use.
        prop["tp_source"] = tp_result.get("tp_source")
        prop["tp_unavailable_reason"] = tp_result.get("tp_unavailable_reason")
        if tp_result.get("market_probability") is not None:
            prop["market_probability"] = tp_result["market_probability"]

        # ---- Shared p_true ladder (carbon-copy with NBA) ---------------
        # Ladder order: model → hit_rate → vk2 → fair
        # MLB currently has no vk2 model on disk, so that rung stays None
        # until a 5-year adv-stat MLB VK2 model is trained. The structure
        # is identical to NBA — only the available rungs differ.
        p_true_hit_rate = (hit_rate / 100.0) if hit_rate is not None else None
        p_true_vk2 = None  # reserved: future MLB VK2 model
        from services.scoring.scoring_stack import resolve_p_true_ladder
        p_model, p_true_method_used = resolve_p_true_ladder(
            p_true_model=p_true_model,
            p_true_hit_rate=p_true_hit_rate,
            p_true_vk2=p_true_vk2,
            tp=tp,  # enables the "fair" rung when a reference market exists
            preferred_method=active_method_early,
        )

        # Multi-book de-vig TP contract (2026-04-22): no 50% fallback.
        # edge_pct is None when tp or p_model are unavailable so
        # downstream gates receive an explicit "unknown" signal rather
        # than a fabricated 0.0. Carbon-copy of NBA (2026-05 parity
        # cleanup): MLB previously had a `hit_rate - tp` fallback path
        # NBA never had — removed for strict parity.
        if tp is None or p_model is None:
            edge_pct = None
        else:
            edge_pct = round((p_model * 100.0) - tp, 1)

        # MLB has no verified PP multiplier data yet
        return ScoringContext(
            canonical_key=canon_key,
            sport="mlb",
            event_id=prop.get("event_id"),
            player_name=player_name,
            stat_type=stat_type,
            line=float(line),
            recommendation=prop.get("recommendation"),
            pp_layer=pp_layer,
            dk_layer=dk_layer,
            fd_layer=fd_layer,
            mgm_layer=mgm_layer,
            bol_layer=bol_layer,
            sharp_layer=sharp_layer,
            p_model=p_model,
            p_true_hit_rate=p_true_hit_rate,
            p_true_model=p_true_model,
            p_true_method=p_true_method_used,
            p_true_vk2=p_true_vk2,
            model_projection=model_projection,
            model_sigma=model_sigma,
            cv=cv,
            cv_status=cv_status,
            avg_hit_margin=avg_hit_margin,
            avg_miss_margin=avg_miss_margin,
            hit_rate=hit_rate,
            hit_rate_over=hit_rate_over,
            hit_rate_under=hit_rate_under,
            hit_rate_sample_size=hr_sample_size,
            hit_rate_l5=hit_rate_l5,
            hit_rate_l10=hit_rate_l10,
            hit_rate_status=hit_rate_status,
            projection_method=projection_method,
            edge_pct=edge_pct,
            tp=tp,
            ceiling_rate=ceiling_rate,
            books_available_count=books,
            raw_prop=prop,
            pp_combo_multiplier=None,  # not yet available for MLB
            pp_label=None,
            pp_multiplier_model=None,
            # Global Identity Rule (2026-04-23) — persist the identity
            # decision on every MLB score doc, same shape as NBA.
            bdl_player_id=bdl_player_id,
            identity_status=identity_status,
            feature_health=hf_feature_health,
            # 2026-04-29 — MLB injury_context plumbing parity with NBA.
            # OBSERVABILITY ONLY (display + Vision Intel input). NOT
            # fed into MLB scoring math, gates, or sigma; comment in
            # nba_scoring.py:3157 explains the same contract.
            # `feature_hydration._build_injury_summary` populates the
            # `team_injury_context` / `opp_injury_context` keys on the
            # raw prop dict; we forward them onto every score row so
            # downstream readers (Vision Intel, dashboard cards,
            # injury-advantage join) can see them.
            injury_context=({
                "team": prop.get("team_injury_context"),
                "opp": prop.get("opp_injury_context"),
                "team_injury_count": prop.get("team_injury_count"),
                "team_out_count": prop.get("team_out_count"),
                "missing_usage_estimate": prop.get("missing_usage_estimate"),
                "missing_minutes_estimate": prop.get("missing_minutes_estimate"),
                "usage_vacuum_factor": prop.get("usage_vacuum_factor"),
                "key_player_out_flag": prop.get("key_player_out_flag"),
                "injury_data_is_imputed": (
                    (prop.get("team_injury_context") or {})
                    .get("injury_data_is_imputed", 1)
                ),
            } if prop.get("team_injury_context") is not None else None),
        )

    # ---------------------------------------------------------
    # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): persist tempo +
    # intel_suite at scoring-write time. Eliminates the route-time
    # enrichers `enrich_mlb_prop_with_tempo` and `enrich_mlb_intel_suite`
    # from the live board path (D11).
    # ---------------------------------------------------------
    def enrich_score_doc(
        self, raw_prop: Dict[str, Any], ctx: ScoringContext
    ) -> Dict[str, Any]:
        # Lazy import to avoid circular deps (routes → services).
        try:
            from routes.ferrari_tiers import (
                enrich_mlb_prop_with_tempo,
                enrich_mlb_intel_suite,
            )
        except Exception as e:
            logger.debug(f"[MLB_SCORING] enrichers unavailable: {e}")
            return {}

        # Work on a shallow copy so we never mutate the live_props source.
        working = dict(raw_prop) if raw_prop else {}
        try:
            enrich_mlb_prop_with_tempo(working)
        except Exception as e:
            logger.debug(f"[MLB_SCORING] tempo enrich skipped: {e}")
        try:
            enrich_mlb_intel_suite(working)
        except Exception as e:
            logger.debug(f"[MLB_SCORING] intel_suite enrich skipped: {e}")

        return {
            "tempo_modifier": working.get("tempo_modifier"),
            "intel_suite": working.get("intel_suite"),
        }
