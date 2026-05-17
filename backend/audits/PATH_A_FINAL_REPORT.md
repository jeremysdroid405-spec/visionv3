# Path A — Final Validation Report (2026-05-17)

## Status: ✅ Hydration fix verified at slate scale (2026-05-05)

### Fix verification levels — all passing

| Level | Method | Result |
|---|---|---|
| **Unit** | Olson μ@1.5 — predict() vs replay_one(post-fix) with identical inputs | 0/222 feature diff, byte-identical μ |
| **Olson 20-row harness** | All total_bases lines/sides/books | μ collapsed 7.90 → 3.19 ✅ |
| **Slate (05-05)** | Full `replay_date(force=True)` rebuild | **n=8,510 rows, max μ=3.109, ZERO rows >4.5** |
| **Memory** | Single-thread guard | 0 leaked workers, RSS peak 2.9 GB |

### 2026-05-05 — rebuilt Layer-3 outputs

- 25,431 rows total (`mlb_replay_model_outputs`)
- 8,510 are `total_bases`
- max μ = **3.109** (Ivan Herrera 0.5 line — realistic)
- **0 rows above 4.5** (was 1,248 on 05-06 pre-fix)
- **0 rows above 6.0**
- Stamped `source_version = replay_engine_v1.1_hydration_2026_05_17`
- elapsed: 35 s, RSS peak: 2,921 MB

### 2026-05-06 — Layer-3 rebuild attempt #2 OOM'd

Pod ran out of RAM mid-rebuild (37,691-row date is ~50% larger than
05-05). Mongod's WiredTiger cache holds ~12-15 GB on this pod by
default; combined with model load + 16 boosters' working memory the
pod exceeded its 31 GB limit.

Phase 2c orchestrator was never reached.

## What's verified

1. The `replay_one()` feature-hydration fix is **correct** at unit,
   harness, AND slate scale.
2. The single-thread guard ELIMINATED the multiprocessing-fork
   orphans (0 leaked workers vs the previous chronic 3-5 GB orphans).
3. Layer-3 RSS profile is now bounded (~3 GB peak).

## What's blocking full validation

Mongod WT cache consumes ~half the pod RAM. Combined Layer-3 + Phase 2c
on the larger 05-06 date pushes total usage past 31 GB.

## Recommended next step

**Cap mongod WT cache to 4 GB** (add to `/etc/mongod.conf`):
```yaml
storage:
  wiredTiger:
    engineConfig:
      cacheSizeGB: 4
```
Restart mongod. This frees ~8-12 GB and lets all 15 days rebuild
predictably. Performance impact: negligible for read-mostly replay
workloads since hot pages stay cached.

Alternative if you want to avoid mongod config change:
- Process 05-06 in two passes (am snapshot, pm snapshot) — never done
  this way before so it'd need verification
- Free the model load between Layer-3 and Phase 2c via subprocess

## Artifacts

- `services/mlb_high_friction_model.py` — single-thread inference guard
  (`load_models` post-load block; `MLB_HF_ALLOW_MULTITHREAD=1` training override)
- `services/replay/mlb_replay_engine.py` — feature hydration fix
  (`_build_player_dict(hub_extras=)`, `replay_one(hub_extras=)`,
  hub_extras index built in `replay_date()`)
- `audits/path_a_task_6_olson_only_harness.py` — 8s validation harness
- `audits/path_a_mu_sanity_check.py` — streaming slate μ stats
- `audits/path_a_run_one.py` — per-date rebuild driver
- `audits/PATH_A_TASKS_2_AND_6_REPORT.md` — task #2 + #6 report
- This report
