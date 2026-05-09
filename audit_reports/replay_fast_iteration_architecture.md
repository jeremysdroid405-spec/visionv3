# Fast Iteration Replay Architecture

_Implemented 2026-05-09._

> Goal: scoring/gate/threshold/TP-formula iteration must run in
> minutes, not hours. A full feature-rebuild replay of one 30-day
> NBA window takes ~30-40 min; an incremental scoring-only run on
> the same cache completes in **~3 min for 500k rows**.

## Three-stage architecture

| stage | what's in it | recompute cost | how often it changes |
|---|---|---|---|
| **A — Source data** | `replay_odds_snapshots`, `replay_props_normalized`, `replay_results`, `bdl_historical_game_logs`, `bdl_advanced_stats` | irrelevant (read-only) | Stage A regenerates only when ingestion job runs |
| **B — Cache** | `replay_vk2_cache` (this file's deliverable) | ~30 min for 500k | bumps when VK2 model files / feature pipeline change |
| **C — Scoring** | `replay_evaluations` | **~3 min for 500k** | every gate / threshold / TP tweak |

## Stage B cache schema (`replay_vk2_cache`)

Per `(event_id, snapshot_label, canonical_key, side)` (unique index):

```jsonc
{
  source_run_id, event_id, snapshot_label, canonical_key,
  market_key, stat_family, player, line, side,
  commence_time, snapshot_ts,

  // Everything expensive — never recomputed in Stage C:
  by_book_layers: { <book>: { line, over_odds, under_odds } },
  ref_book, ref_odds,
  tp_blob: { tp, books_used, tp_source, ... },
  edge_pct,
  vk2_blob: { projection, sigma, p_over, model_version,
              feature_count, feature_hash, adv_coverage_l10,
              feature_completeness, error, components, ... },
  feature_set: { sample_size, mu, sigma, cv, hit_rate_l5/l10/l20,
                 ceiling_rate, feature_completeness },

  // Lineage — invalidation is keyed off these:
  vk2_model_hash,         // per-stat content hash of the .pkl that
                          // produced the projection
  feature_pipeline_hash,  // hash of nba_vk2_features.py
  cached_at, _cached_first
}
```

Cache writes are **a free byproduct** of the normal full-engine run
(`run_replay_engine_vk2.py --cache-outputs true`, default).

## Stage C — incremental scorer

`services/replay/scoring_only.py::run_scoring_only` iterates the cache
and recomputes:

1. `prop` dict reconstruction from `by_book_layers` (no I/O)
2. `compute_tp` against cached layers (optional, skip with
   `--recompute-tp false` to reuse cached `tp_blob`)
3. `edge_pct` = `p_model * 100 − implied%`
4. `compute_scoring_stack` — the gate engine + vision_v2

Outputs to `replay_evaluations` under a fresh `replay_run_id`. Source
cache is never mutated.

## Fingerprint registry (`services/replay/cache.py`)

| component | data hashed | what bumps it |
|---|---|---|
| `vk2_model_hash` | content SHA-1 of each `.pkl` in `/app/backend/models/vk2_*.pkl` | retraining a VK2 model |
| `feature_pipeline_hash` | content SHA-1 of `nba_vk2_features.py` | feature engineering edits |
| `gate_config_hash` | SHA-1 of the `THRESHOLDS[sport]` *dict* (NOT the file) | gate threshold tweaks |
| `tp_engine_hash` | content SHA-1 of `tp_engine.py` | TP formula edits |

Each replay run summary stamps a `fingerprint_block`. Each cache row
stamps `vk2_model_hash` + `feature_pipeline_hash` (because those
determine whether the row is reusable).

`changed_components(before, after)` returns a flat list like
`["gate_config_hash"]` or `["vk2_model.AST", "feature_pipeline_hash"]`
so the diff runner can tell the user exactly what moved.

## Invalidation rules

| change | required action |
|---|---|
| gate thresholds only | **incremental scorer is enough.** Cache reuse 100%. |
| TP formula | incremental scorer with `--recompute-tp true` (default). |
| feature pipeline (`nba_vk2_features.py`) | **full rebuild.** The cache is stale. |
| One VK2 model retrained (e.g. AST) | full rebuild ONLY for that family. The incremental scorer is allowed to reuse cache rows whose `vk2_model_hash` for that stat matches the current process. (Implementation hook left in `cache.py::changed_components`; the incremental driver currently does NOT auto-skip invalidated rows — manual filter via `--source-run-id` is the recommended workflow.) |

## Sampling — sub-minute tuning loops

`run_replay_engine_vk2.py --sample-events 5` drops a deterministic
random sample of `event_ids` from the window into the engine. The
3-event smoke run that backed this implementation finished in 50 s
end-to-end (engine + cache + score). The matching incremental run
finished in **1.9 s** for 5,894 cache rows.

## CLI surface added in this turn

| script | purpose |
|---|---|
| `scripts/run_incremental_replay.py` | Stage-C only, < 5 min for 500k rows |
| `scripts/compare_replay_runs.py` | tier / ROI / gate-reason / promoted / demoted diff |
| `scripts/replay_safehaven_debug.py` | SH near-miss + counterfactual SH count |
| `scripts/run_replay_engine_vk2.py --sample-events N` | sub-minute tuning loops |
| `scripts/run_replay_engine_vk2.py --cache-outputs true/false` | toggle Stage-B cache writes |

## Validation results

End-to-end smoke against 3 sampled events:

```
cache rows written:    5,894
incremental scorer:    1.92 s (3,066 rows/s)
incremental output:    10,011 eval rows
tier distribution:     IDENTICAL to engine output:
                         FL=40   WZ=14   unqualified=9,957
```

Projected runtime at 500k cache rows: **~163 seconds (2 min 43 s)** —
well under the 5-minute target.

## What's deferred (call-outs for future work)

- **Auto-invalidating per-family cache reuse**: today the rules above
  are documented; the incremental driver does not yet auto-purge
  rows whose `vk2_model.{stat}` differs. Workaround: pass
  `--source-run-id` to manually scope.
- **TP-engine hash gating**: we always recompute TP unless the
  caller passes `--recompute-tp false`. A future enhancement could
  skip recompute when `tp_engine_hash` matches the cache row's
  stamp, gaining another ~30% perf.
- **Stage B persistence retention**: cache grows with each run.
  A weekly janitor that drops rows older than 14 days is the natural
  next addition.
