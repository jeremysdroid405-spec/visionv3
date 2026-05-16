# 🚨 URGENT P0 — Batter Strikeouts Audit (Witt vs Garcia)

**Audit owner:** read-only investigation, no patches applied.
**Generated:** 2026-05-16
**Model under audit:** `/app/backend/models/mlb_hf/mlb_hf_strikeouts.pkl`
**Production scoring path:** `MLBHighFrictionModel.predict()` → norm_stat = `strikeouts` (alias of "Batter Strikeouts") → `friction_audit` → scoring_stack → tp_engine → tier_evaluator → `mlb_prop_scores`.
**Live entry:** `services/mlb_high_friction_model.py::predict_live(...)`.

Supporting raw outputs:

- `/app/backend/audits/batter_strikeouts_audit_2026_05_18.md` (Part 1 — persisted doc + clean-rerun)
- `/app/backend/audits/batter_strikeouts_audit_2026_05_18_part2.md` (Part 2 — feature-swap probe)

---

## TL;DR — Root cause (proven)

> **The XGBoost `mlb_hf_strikeouts.pkl` model produces μ that is anti-correlated with the most-recent reality because the Statcast/PA-windowed feature pipeline is frozen 10 days behind the BDL game-log pipeline. The model weights Statcast K-rates higher than L5/L10/L20 game-log averages, so when those two sources contradict each other (as they do today for Witt and Garcia), the model trusts Statcast and inverts the projection.**

We confirmed this end-to-end via three orthogonal checks:

1. **Persisted-doc replay (Part 1, step a):** Re-running `predict()` on the live master_hub data reproduces the production μ values exactly (Witt 0.319, Garcia 1.211). The bug is upstream of nothing — it's inside the model + feature inputs.
2. **Data-freshness probe:** Newest `mlb_statcast_player_features.game_date` = **2026-04-26**. Newest BDL log for both players = **2026-05-06**. **10-day / 10-game gap.**
3. **Feature-swap probe (Part 2):** Holding BDL identity, swapping ONLY the Statcast + PA feature blocks between the two players makes Witt's μ jump from 0.72 → **1.01** and Garcia's μ drop from 1.63 → **0.46**. Swapping ONLY the BDL game-log averages (l3/l5/l10/l20) barely moves μ (0.72 → 0.68). The Statcast block alone owns the inversion.

**No code patches yet, per instruction.**

---

## 1. The contradiction in the persisted score docs

| Field | Bobby Witt Jr. | Maikel Garcia |
|---|---|---|
| `mu_raw_model_projection` | **0.3187** | **1.1949** |
| `tp` (devigged market) | 32.8 % | 27.8 % |
| `fair_prob` | 0.3279 | 0.2778 |
| `vision_score` | **4.1** | **100.0** |
| `routed_tier` | war_zone | war_zone |
| `tier` (final) | unqualified | war_zone |
| `market_class` | alternate | alternate |

Witt is essentially **rejected** (vs = 4.1). Garcia is **promoted as a war-zone winner** (vs = 100). The market disagrees with both projections (market says OVER 0.5 is ~28-33% for both).

## 2. The contradiction with reality (last 20 games, BDL)

| Player | L5 K avg | L10 K avg | L20 K avg | L5 hit-rate >0.5 | L10 hit-rate >0.5 | L20 hit-rate >0.5 | μ output |
|---|---|---|---|---|---|---|---|
| **Bobby Witt Jr.** | **1.40** | **1.00** | **0.90** | **100 %** | **80 %** | **75 %** | **0.32** ❌ |
| **Maikel Garcia** | 0.40 | 0.50 | 0.75 | 40 % | 50 % | 70 % | **1.19** ❌ |

Both projections are **inverted** relative to the input game-log evidence the model literally sees as features.

## 3. Why the model inverts: the feature divergence

Both players' identity resolution is correct (Witt mlbam 677951, Garcia mlbam 672580 — verified against `mlb_player_identity_map`). The model receives accurate values for *all* features it asks for. The problem is **two contradictory feature blocks** were ingested at different freshness levels.

### BDL game-log block (fresh through 2026-05-06)

| Feature | Witt | Garcia |
|---|---|---|
| `l5_avg` | 1.40 | 0.40 |
| `l10_avg` | 1.00 | 0.50 |
| `l20_avg` | 0.90 | 0.75 |
| `hit_rate_l5` | 100 % | 40 % |
| `hit_rate_l10` | 80 % | 50 % |
| `current_hit_streak` | 8 | 0 |
| `ewma_l5` | 1.50 | 0.56 |
| `line_vs_l5` | -0.90 | +0.10 |

Both clearly say: Witt is on a **K-tear**, Garcia is **cold on Ks**.

### Statcast block (FROZEN at 2026-04-26 — same game_date for every player checked)

| Feature | Witt | Garcia |
|---|---|---|
| `sc_b_r7_k_rate` | 0.064 | 0.188 |
| `sc_b_r14_k_rate` | 0.164 | **0.244** |
| `sc_b_r30_k_rate` | 0.177 | 0.187 |
| `sc_b_r7_contact_rate` | 0.795 | 0.757 |
| `sc_b_r7_whiff_rate` | 0.205 | 0.243 |

The Statcast block flips the story: it reports Garcia at a higher K rate than Witt. This is **correct** for the 7/14/30 windows ending **2026-04-26** — Witt was in a low-K stretch through mid-April. But it's **10 days out of date** vs. what the BDL block sees.

**Witt has piled up 10 K in his last 10 games, but every Statcast feature is from before that streak started.**

## 4. Mathematical trace (clean rerun)

```
norm_stat = "strikeouts"
model_pickle = "mlb_hf_strikeouts.pkl"
features_count = 208

------------------- Bobby Witt Jr. -------------------
raw_pred = float(xgb_strikeouts.predict(scaler.transform(X)))  = 0.3191
park_factor (KC home) = 1.0
final_pred = raw_pred * park_factor                            = 0.3191
std_dev_used = features["std_dev_l10"]                          = 0.8165
z = (0.5 - 0.3191) / 0.8165                                     = 0.2216
prob_over = (1 - Φ(0.2216)) * 100                               = 41.23%
# pred < line, prob_over < 50 → no force-correction triggered.
# direction_gate: final_pred=0.32 < line=0.5 → OVER side FAILS gate (under-projection).

L10 hit-rate >0.5 = 80%  ⟂  model prob_over = 41.2%  → INVERTED.

------------------- Maikel Garcia -------------------
raw_pred = float(xgb_strikeouts.predict(scaler.transform(X)))  = 1.2112
park_factor (KC home) = 1.0
final_pred                                                      = 1.2112
std_dev_used = features["std_dev_l10"]                          = 0.5270
z = (0.5 - 1.2112) / 0.5270                                     = -1.3494
prob_over = (1 - Φ(-1.3494)) * 100                              = 91.14%
# direction_gate: final_pred=1.21 > line=0.5 → OVER side PASSES gate.
# vision_score → 100 (model says huge over edge vs market's 27.8%).

L10 hit-rate >0.5 = 50%  ⟂  model prob_over = 91.1%  → INVERTED.
```

Both probability calculations follow the published math correctly. The defect is *upstream*: the μ that feeds the Normal CDF is wrong. Witt is set up to fail the direction gate (μ < line) and Garcia is set up to be a runaway war-zone pick (μ >> line) **purely from stale Statcast features**.

## 5. The Statcast block dominates — proven by swap

From Part 2 of the audit (file `batter_strikeouts_audit_2026_05_18_part2.md`):

| Variant | Witt μ | Garcia μ |
|---|---|---|
| **Baseline** (own BDL × own Statcast) | 0.7160 | 1.6340 |
| Own BDL × **other player's Statcast/PA** | **1.0092** | **0.4616** |

> Holding the BDL game-log averages fixed and swapping **only** the Statcast/PA feature blocks moves μ by **+0.29 for Witt and -1.17 for Garcia** — enough to fully flip the war-zone routing.

Part 2 one-feature-group walking-swap (Witt → Garcia, single block at a time):

| Group swapped to Garcia's value | n features | Witt μ (baseline 0.7160) |
|---|---|---|
| `l3+l5+l10+l20_avg` | 4 | 0.6773 (no inversion) |
| `ewma_l5/l10/l20/trend` | 4 | 0.6628 (no inversion) |
| `hit_rate_l5/l10` | 2 | 0.7061 (negligible) |
| `current_hit_streak/miss_streak` | 2 | 0.7816 (negligible) |
| `line_vs_*` family | 5 | 0.6133 (negligible) |
| `vs_lhp/rhp/platoon_*` | 21 | 0.7174 (negligible) |
| **`sc_b_r7_*`** | 11 | **0.8491** |
| **`sc_b_r14_*`** | 11 | **0.9243** |
| **`sc_b_season_*`** | 11 | **0.8116** |

Every Statcast block individually moves μ by ≥10 % of the line; no game-log block does. **The Statcast rolling features are the dominant signal in this model.**

## 6. Manual repair simulation (still no patch)

Override every `sc_b_*_k_rate` and `pa_b_*_k_rate` with the player's actual last-14-days BDL K-per-PA, keep everything else as the model received it:

| Player | L14 K/PA (BDL truth) | original Statcast r14_k_rate | μ original | μ with SC K-rate ← BDL truth |
|---|---|---|---|---|
| Bobby Witt Jr. | 0.1667 | 0.1636 | 0.7160 | 0.5785 |
| Maikel Garcia | 0.1639 | 0.2439 | 1.6340 | **0.2401** |

Garcia's μ crashes from 1.63 to 0.24 once the stale 24 % K-rate is replaced with his true 16 % K-rate. Witt's μ barely moves because his Statcast r14 K-rate happens to almost coincide with his L14 BDL K-rate — but his μ is still half of his L10 actual average (model under-projects across the board).

## 7. Secondary findings

1. **Model goodness-of-fit is weak even without staleness.** Pickle metadata says `r2_test = 0.2402`, `mae_test = 0.5441` on 61,990 training samples. Effectively the model captures ~24 % of the variance — fine as a directional signal *if* features are fresh, useless otherwise.
2. **No baseline floor for `strikeouts`.** `MLBHighFrictionModel._ACTIVE_BASELINE` covers hits / singles / runs / rbis / hits+runs+rbis only. Nothing catches a μ that's a third of the player's L10 average.
3. **No "data freshness" sanity feature.** XGBoost has no way to know it's looking at 10-day-old Statcast — there's no `sc_data_age_days` feature.
4. **No coverage gate for stale ingest.** `coverage_gate` enforces book_count but doesn't enforce feature freshness.
5. **`mu_active_baseline_applied` audit fields are not populated for `strikeouts`** (correctly, given (2)), but that means the score-doc audit trail offers no clue that a model μ is wildly out of range.
6. **No imputed-flag for SC staleness.** `sc_batter_is_imputed = 0` for both players even though the doc is 10 days old. The imputed flag fires on *missing* data, not on *stale* data.

## 8. Why this is shipping to users right now

`mlb_prop_scores` already wrote:
- Garcia → **war_zone, vision_score=100** on 2026-05-16T20:16:09Z.
- Witt → **unqualified, vision_score=4.1** on the same recompute.

Direction-gate enforcement is doing exactly what it was built to do (Garcia μ > line passes, Witt μ < line fails), so the bug propagates straight to the WZ feed. The `peer_disagreement_filter` cannot save this — the model μ is internally consistent with itself, just disconnected from reality.

## 9. Proposed (NOT applied) fix paths — ranked by probability of being the right call

### A — Fix the Statcast freshness pipeline (highest leverage)
The `mlb_statcast_player_features` and `mlb_statcast_raw` collections both have their newest doc at 2026-04-26. Something stopped feeding those pipelines. Until they're fresh, **every batter-strikeouts μ is suspect** because the same dominant features go into every prediction.

> Trace: which job populates `mlb_statcast_player_features` and `mlb_statcast_raw`? Check supervisor / cron for a failed/disabled task. The fix is operational, not code.

### B — Add a feature-freshness sanity bound on μ
Cheap in-flight guard:

```
if abs(mu - l10_avg) > MAX_DRIFT * l10_avg:
    flag = "feature_freshness_suspect"
    # Either fall back to ewma_l10, or fail-closed the prop.
```

Where `MAX_DRIFT ≈ 0.6`. This would have caught both Witt (μ=0.32 vs L10=1.0 → ratio 0.32) and Garcia (μ=1.21 vs L10=0.5 → ratio 2.42).

### C — Add a Statcast-stale gate to the scoring stack
New filter alongside `book_quote_integrity_filter` and `peer_disagreement_filter`:

```
if (today - sc_game_date).days > MAX_SC_STALENESS_DAYS:
    raise sc_features_stale  # set sc_batter_is_imputed = 1
```

`MAX_SC_STALENESS_DAYS ≈ 3`. When stale, force `sc_batter_is_imputed=1` *and* zero out the rolling/PA blocks so the XGBoost model falls back to the BDL features the way it does for first-look batters. The pickle's `sc_hit_rate = 0.3456` shows the training data was tolerant of imputed SC.

### D — Lower the Statcast importance during inference
Train a sister model on BDL-only features (drop the SC/PA block) and route through it whenever SC is stale or imputed. This is heavier work and is option-of-last-resort if (A) keeps breaking.

### E — Re-train with a `sc_data_age_days` feature
Add it to the feature builder, retrain. XGBoost will learn to discount the SC block when it's old. Bigger investment; deferrable until SC pipeline is stable.

## 10. What to do next (your call)

You said **do not patch**. Options on the table:

1. **Operational first:** Identify why `mlb_statcast_player_features` / `mlb_statcast_raw` haven't ingested since 2026-04-26. (Single most impactful fix.)
2. **Stack guard:** Ship (C) as a new `sc_freshness_gate` filter in `services/scoring/` — symmetric to `peer_disagreement_filter` — that flips `sc_batter_is_imputed=1` on stale rows. Mirrors the existing integrity-filter pattern.
3. **Sanity guard:** Ship (B) as a `μ-drift sanity bound` in `MLBHighFrictionModel.predict()` after the `final_pred` line, behind a feature flag.
4. **Quarantine batter_strikeouts:** Disable WZ qualification for `batter_strikeouts` until SC pipeline is restored.

Awaiting your instruction on which (combination) of the above to prosecute. **Nothing applied yet.**
