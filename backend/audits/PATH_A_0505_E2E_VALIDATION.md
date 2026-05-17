# 2026-05-05 End-to-End Validation — Hydration Fix Confirmed at Slate Scale

## Phase 2c rebuild against the hydrated Layer-3 outputs

| Metric | Value |
|---|---:|
| Serial | `MLB-PRODREPLAY-20260505-WZ-1100UTC-00004` |
| Rows scanned | 25,431 |
| Rows qualified (gate_pass) | **361** |
| W / L / Push / Ungraded | 226 / 43 / 0 / 92 |
| Hit rate | **84.01 %** |
| ROI | **+31.09 %** |
| Profit (units) | **+83.62 u** on 269 u stake |
| Elapsed | 4.5 s |
| RSS peak | 293 MB |

## μ distribution — `total_bases` rows (n = 8,510)

| Metric | Before fix | After fix |
|---|---:|---:|
| Max μ | 7.902 (Olson) | **3.109** (Herrera) |
| Avg μ | ~1.97 | 1.241 |
| Rows μ > 4.5 | 1,248 | **0** |
| Rows μ > 6.0 | many | **0** |

## Top-5 qualified picks (by edge)

| Player | Line | Side | Book | Odds | μ | Edge | Outcome |
|---|---:|---|---|---:|---:|---:|---|
| Spencer Steer | 0.5 | OVER | betmgm | -160 | 2.89 | 0.355 | **WIN** (+0.62 u) |
| Spencer Steer | 0.5 | OVER | williamhill_us | -162 | 2.89 | 0.352 | **WIN** (+0.62 u) |
| Spencer Steer | 0.5 | OVER | fanatics | -165 | 2.89 | 0.347 | **WIN** (+0.61 u) |
| Spencer Steer | 0.5 | OVER | hardrockbet_oh | -175 | 2.89 | 0.334 | **WIN** (+0.57 u) |
| Ivan Herrera | 0.5 | OVER | betmgm | -190 | 3.11 | 0.324 | ungraded |

These look like legitimate signals — modest μ, realistic edge, books all aligned, actual outcome present.

## Audit pins

- production_pipeline_version: `d09dae0e91ff8ee6…` (64-char SHA over the live pipeline files)
- adapter_version: `26854957195577c9…` (MLB adapter module SHA)
- feature_cache_version: `feature_cache_v1.0_2026_05_16`
- gate_config_version: `mlb_war_zone_v1_2026_05_16`
- scoring_config_version: `scoring_v3.1_phase2a__wz_rewrite_2026_05_16`

## What this proves

1. The `replay_one()` hydration fix produces correct μ at **slate scale** (8,510 rows, zero outliers).
2. The Phase 2c orchestrator correctly forwards the corrected μ into gate eval and grading.
3. End-to-end pipeline (Layer-3 hydrated → Phase 2c → ProductionReplayOutput) is healthy.
4. With Layer-3 already cached, Phase 2c on a full slate runs in 4.5 s with ~300 MB RAM. The OOM issues we saw earlier were specifically Layer-3 model-load + 37K-row processing — Phase 2c standalone is lightweight.

## What's still pending

- 05-06 Layer-3 rebuild (data still contaminated)
- 05-07 → 05-15 Layer-3 rebuild (12 more dates)
- Phase 3 (production card extraction)
- Phase 4 (gate engine swap)

The 05-06 → 05-15 rebuilds are now safe to run individually given the single-thread guard. They just need to NOT run inside the same process as Phase 2c. The driver script `audits/path_a_layer3_only.py` already does this — one date per process.
