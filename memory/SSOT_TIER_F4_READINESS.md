# SSOT Tier F #4 — `ScoreDocument` `extra="forbid"` Readiness Report

**Date**: 2026-05-04
**Mode**: read-only readiness check (no flip executed)
**Source files**:
- `backend/services/scoring/score_document_schema.py`
- `backend/services/scoring/prop_scores_store.py`
- live DB: `nba_prop_scores`, `mlb_prop_scores`

---

## Current state

| Setting | Value |
|---|---|
| `model_config.extra` (in code) | **`"allow"`** |
| `SSOT_PYDANTIC_STRICT` env | **`true`** (active) |
| Validation mode today | log-and-count via `validate_score_document` (line 586 of `prop_scores_store.py`) |
| Recent Pydantic failures in log | **0** |

The `SSOT_PYDANTIC_STRICT` env flag only governs whether `validate_score_document` **re-raises** on a `ValidationError`. Because the schema's `model_config` hard-codes `extra="allow"`, no `ValidationError` is ever produced today — the flag is effectively a no-op for the extras-rejection problem.

Flipping `extra="forbid"` is the missing piece.

---

## Field-level inventory

| Counter | Count |
|---|---|
| Fields declared in `ScoreDocument` | **127** |
| Fields projected by `_project_score_doc` (= `_IDENTITY_FIELDS ∪ _SCORE_OUTPUT_FIELDS ∪ _UNIVERSAL_POOL_FIELDS ∪ versioning`) | **226** |
| Fields actually present on live `*_prop_scores` docs (sample size: 1,000 docs) | **subset of 226** |
| Live-DB fields NOT declared in Pydantic | **0** |
| Projected fields NOT declared in Pydantic | **108** |
| Pydantic-declared fields NOT projected | **9** (harmless — declared optional, never written) |

### Why the live-DB count is 0

`_project_score_doc` (lines 467–470 of `prop_scores_store.py`) filters every adapter output through `_IDENTITY_FIELDS ∪ _SCORE_OUTPUT_FIELDS` BEFORE the Pydantic validator runs. Anything outside the allowlist is silently dropped at the projection step. Therefore the docs that hit Pydantic are guaranteed to be a subset of the projection allowlist — and the projection allowlist already prevents non-canonical pollution. **The risk surface for the flip is the 108 projected-but-undeclared fields, not arbitrary adapter junk.**

---

## The 108 fields that would fail `extra="forbid"` today

Grouped by feature domain (most are recent additions that updated `_SCORE_OUTPUT_FIELDS` but skipped the Pydantic schema):

| Domain | Fields | Count |
|---|---|---|
| Distribution probability layer (2026-04-27) | `distribution_p_over`, `distribution_p_under`, `distribution_kind`, `distribution_selector_reason`, `distribution_sigma`, `distribution_sigma_source`, `distribution_clamped`, `distribution_effective_mu`, `distribution_mu_floor_applied`, `distribution_mu_floor_capped`, `distribution_cv_floor_applied`, `distribution_lambda`, `distribution_threshold`, `distribution_dispersion_r`, `distribution_p_param` | 15 |
| ECDF / calibration audit | `ecdf_p_over`, `ecdf_bucket`, `ecdf_bucket_n`, `ecdf_version`, `raw_gaussian_p_over`, `isotonic_p_over`, `probability_method`, `probability_calibration_applied`, `raw_p_over`, `projection_intercept_applied`, `projection_intercept_delta`, `pre_intercept_projection` | 12 |
| NBA availability guard (2026-04-27) | `availability_guard_applied`, `availability_status`, `availability_sub_status`, `availability_guard_reason`, `dnp_risk_flag`, `injury_return_flag`, `minutes_restriction_flag`, `minutes_restriction_factor`, `minutes_recovery_ratio`, `games_missed_recently`, `return_game_number`, `normal_minutes`, `expected_minutes`, `expected_minutes_raw`, `mu_before_availability_guard`, `mu_after_availability_guard` | 16 |
| NBA rate × minutes layer (2026-04-28) | `rate_model_applied`, `rate_pts_per_min`, `rate_reb_per_min`, `rate_ast_per_min`, `mu_rate_projection`, `mu_model_projection`, `mu_final_projection`, `rate_model_blend_weights`, `rate_model_blend_mode`, `rate_model_trigger` | 10 |
| NBA recency μ blend | `mu_recency_blended`, `mu_recency_blend_l3`, `mu_recency_blend_l5`, `mu_recency_blend_l10_median`, `mu_recency_blend_l20`, `mu_recency_blend_weights`, `mu_minutes_regression_applied`, `mu_minutes_regression_factor`, `mu_minutes_l3`, `mu_minutes_l10`, `mu_raw_model_projection` | 11 |
| NBA shadow projections (E + VK2 + REB/AST) | `mu_recency_E`, `mu_recency_E_applied`, `delta_mu_E_vs_A`, `mu_recency_E_l3`, `mu_recency_E_l10`, `mu_recency_E_l10med`, `mu_pts_vk2`, `mu_pts_vk2_applied`, `delta_mu_pts_vk2_vs_vk1`, `mu_rate_reb_shadow`, `mu_rate_reb_shadow_applied`, `delta_mu_rate_reb_shadow_vs_current`, `rate_reb_per_min_shadow`, `mu_rate_ast_shadow`, `mu_rate_ast_shadow_applied`, `delta_mu_rate_ast_shadow_vs_current`, `rate_ast_per_min_shadow`, `expected_minutes_shadow` | 18 |
| NBA Phase 2 heteroscedastic σ | `hetero_sigma_adjusted`, `hetero_sigma_multipliers` (declared `hetero_sigma_base` is fine; `hetero_sigma_multiplier` declared but unused) | 2 |
| NBA per-stat debias (2026-05-02) | `projection_raw_pre_debias`, `projection_debias_amount`, `projection_debias_source` | 3 |
| NBA RFA minutes penalty | `rfa_minutes_penalty_applied`, `rfa_minutes_penalty_factor`, `expected_minutes_before_rfa_penalty`, `expected_minutes_after_rfa_penalty` | 4 |
| MLB Empirical-Bayes shrinkage | `eb_shrunk_projection`, `eb_player_career_mean`, `eb_weight_model`, `eb_weight_player`, `eb_shrinkage_applied`, `eb_skip_reason`, `eb_career_sample_n`, `raw_hf_projection` | 8 |
| MLB pitcher / batter μ overrides | `mu_pitcher_workload_anchored`, `mu_active_baseline_applied`, `mu_active_baseline_value`, `expected_ip_used`, `projection_model_version` | 5 |
| LOM audit | `lom_p_over`, `lom_version` | 2 |
| War Zone | `war_zone_cv_modifier` | 1 |
| Ceiling rate | `ceiling_rate` | 1 |

**Total**: 108 fields, all `Optional[float | bool | str | Dict | List[str]] = None`.

### 9 declared-but-not-projected (no action needed)

`consistency_band`, `half_line_variance`, `hetero_sigma_multiplier`, `hit_distance_from_line`, `l10_rate`, `l20_rate`, `l5_rate`, `miss_distance_from_line`, `stability_half_line` — declared as Optional, never reach the projector. Harmless.

---

## Risk assessment for the flip

| Aspect | Risk | Notes |
|---|---|---|
| Live DB pollution | **None** | 0 undeclared fields on real docs |
| Adapter output drift after flip | Medium | If a future adapter adds a field but skips the Pydantic schema AND skips `_SCORE_OUTPUT_FIELDS`, the new field is silently dropped (existing behaviour) AND not flagged at write-time. Adding it only to `_SCORE_OUTPUT_FIELDS` (skipping Pydantic) is what makes the flip valuable — that's exactly what gets caught. |
| Recompute pipeline stall | **High if flipped without declaration backfill** | All 108 projected-undeclared fields become `ValidationError`s. Every NBA + MLB write batch fails until declarations are added. |
| Test surface | Low | The 6 contract suites (115 + new TestDgCachedBoardRetired = 119 tests) do not exercise the strict-flip path. A new test would assert that for a representative score doc, `ScoreDocument.model_validate` succeeds with `extra="forbid"`. |

---

## Recommended go/no-go path

**Not safe to flip today.** Required preparation:

1. **One-shot batch**: add the 108 field declarations to `ScoreDocument` (all `Optional[…] = None`, types derived from the comments in `_SCORE_OUTPUT_FIELDS`). One PR, one file — but it's a 108-line addition that must mirror the existing 127-field structure.
2. **Add a declaration-parity test** that compares `ScoreDocument.model_fields.keys()` against `set(_IDENTITY_FIELDS) | set(_SCORE_OUTPUT_FIELDS) | set(_UNIVERSAL_POOL_FIELDS) | {"version_tag","computed_at","scored_at"}` — fail loudly if either side adds a field without the other side getting it. **This is the structural fix that prevents this gap from ever reopening.**
3. Flip `model_config.extra` from `"allow"` → `"forbid"`.
4. Run `recompute_sport(NBA)` and `recompute_sport(MLB)` end-to-end in `dry_run=True` mode and verify `pydantic_failures == 0`.
5. Remove `SSOT_PYDANTIC_STRICT` env wrapper; strict is the only mode going forward.

**Estimated work**: ~30 min code (Step 1) + ~10 min test (Step 2) + ~5 min flip (Step 3) + ~10 min smoke (Step 4) = **~1 hour**, low risk if done in this order.

**DO NOT** flip `extra="forbid"` before completing Steps 1–2 in the same change — the recompute pipeline will hard-fail on every write.

---

## Action items for the next session (post-user-approval)

- [ ] Add 108 `Optional[…] = None` declarations to `ScoreDocument` (grouped by domain, mirroring `_SCORE_OUTPUT_FIELDS` order).
- [ ] Add `tests/test_score_document_parity.py` enforcing key-set equality.
- [ ] Flip `extra="allow"` → `"forbid"` in `score_document_schema.py`.
- [ ] Smoke recompute (dry_run) on both NBA and MLB; confirm `pydantic_failures=0` in the writer log.
- [ ] Optional: collapse `SSOT_PYDANTIC_STRICT` env flag once strict-by-default lands.
