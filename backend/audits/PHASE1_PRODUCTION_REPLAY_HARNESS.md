# MLB Production Replay Harness — Phase 1 Discovery + Scaffolding

**Status:** Phase 1 complete. NO production code touched.

This document is the authoritative map of which production functions read which
data sources, and where dependency injection must occur in Phase 2 to enable a
historical-input replay harness without forking production logic.

---

## 1. Discovery Map — Production Input Touch Points

The production scoring pipeline consumes 6 logical inputs. Each row below
identifies the canonical entry function, its current input source (hardcoded
to live), and the injection seam that Phase 2 must add.

| # | Logical input | Production reader | Current source (hardcoded) | Phase-2 seam |
|---|---|---|---|---|
| 1 | Player props (odds + lines + books) | `services/scoring/recompute.py::recompute_sport` → reads `mlb_prop_scores` / equivalent collections | Pre-populated by `universal_odds_sync.py::sync_universal_odds` (live Odds API) | **Inject `IOddsProvider`** at the recompute boundary — replay path uses `mlb_historical_alt_odds_raw` instead of live API |
| 2 | Player rolling stats (L5/L10/L20, CV, etc.) | `services/mlb_high_friction_model.py::predict` → `self._build_friction_features(player=…, game_logs=…)` | Live: queries `mlb_master_hub_2026.bdl_game_logs[]` directly | **Inject `IFeatureProvider`** — replay path supplies pre-built rows from `mlb_replay_feature_cache` |
| 3 | Statcast features (batter rolling, pitcher rolling) | `services/mlb_high_friction_model.py::predict` → reads `statcast_features=` and `pitcher_statcast_features=` kwargs | Live: caller queries `mlb_statcast_player_features` / `mlb_statcast_pitcher_features` collections | **Inject `IStatcastProvider`** — replay supplies as-of-date snapshot |
| 4 | Opp pitcher + lineup metadata | `services/mlb_high_friction_model.py::predict` → `opponent_team`, `opp_pitcher_id`, `opposing_lineup` kwargs | Live: filled from `mlb_live_lineup_feed.py` or upstream | **Inject `ILineupProvider`** — replay returns None (historical lineup feed does not exist; documented gap) |
| 5 | Gate evaluation (thresholds + decisions) | `services/scoring/tier_evaluator.py` + `services/scoring/gates/engine.py` | **Pure functions** — no live coupling | ✅ Already injectable — no seam needed |
| 6 | Card selection / top-N-per-game / best-book | `services/picks_getter_service.py` (lines 2028+) | Reads scored docs from mongo and filters in-process | **Extract pure function** in Phase 3 — currently inlined in the getter; not refactor-safe in Phase 2 |

### 1.1 Map of model.predict() callers

| Caller | File:Line | Path |
|---|---|---|
| Live recompute | `recompute.py:1247` → adapters → `mlb_high_friction_model.predict()` | LIVE |
| Replay engine v1 | `replay/mlb_replay_engine.py:202` calls `model.predict()` via `replay_one()` | REPLAY (existing) |
| VK regression | `replay/vk2_historical.py` | REPLAY (separate model) |

`mlb_high_friction_model.predict()` is the **single shared inference function**.
It already accepts `statcast_features=`, `pitcher_statcast_features=`,
`pa_batter_features=`, `pa_pitcher_features=`, `batter_hand=`, and `opposing_lineup=`
as parameters — Phase 2 only needs to ensure callers pass historical values in
historical mode rather than letting the function fall back to live queries
inside the method body.

### 1.2 Model.predict()'s internal live-mongo lookups (lines 1480-1580 of `mlb_high_friction_model.py`)

These are the lookups that currently silently make `predict()` live-only:

| Internal lookup | What it queries | Replay risk |
|---|---|---|
| `self.db.mlb_master_hub_2026.find_one({"display_name": player_name})` | Full master_hub doc (current row, includes splits, handedness, history) | **HIGH** — leaks current data into past predictions |
| `self._fetch_recent_game_logs(player_id, …)` | `bdl_game_logs` array, no date filter | **HIGH** — leaks future games |
| Park factor lookup from `self.PARK_FACTORS_3YR` | Constant in-memory dict | Low |

Phase 2 must override these via injection or `predict()` must accept a
fully-formed `as_of_date` and route through `IFeatureProvider`.

---

## 2. Provider Interface Scaffolding

See: `services/replay/providers/base.py`, `live.py`, `historical.py`.

All interfaces are defined as `abc.ABC` Protocols. Phase 2 will pass concrete
instances into refactored production functions via keyword argument
`input_provider: IInputProvider` (default = `LiveInputProvider()` to preserve
backwards-compatible behavior).

---

## 3. Replay Serial / Audit Schema

Continues the existing `mlb_replay_audit` collection scheme used by the
multi-tier sweep, with three additional fields for production-pipeline replay:

```
{
  serial: "MLB-PRODREPLAY-{YYYYMMDD}-{TIER}-{HHMMUTC}-{NNNNN}",
  production_pipeline_version: <hash of recompute.py + scoring_stack.py + gates>,
  input_collection_versions: {
    "mlb_historical_alt_odds_raw":     {count, max_snapshot_iso},
    "mlb_replay_feature_cache":        {count, max_built_at},
    "mlb_statcast_player_features":    {count, max_built_at},
  },
  git_commit_sha: <if available>,
  ...rest of existing audit fields...
}
```

---

## 4. Proposed New Collections

| Collection | Purpose | Compound unique key |
|---|---|---|
| `mlb_production_replay_runs` | One doc per replay run with manifest, serial, version pins, input fingerprints, runtime stats | `(game_date, snapshot_iso, gate_config_version, pipeline_version, replay_started_at)` |
| `mlb_production_replay_outputs` | One doc per scored prop (every prop in the universe, gated AND ungated) | `(replay_serial, event_id, player_norm, market, line, side, book)` |
| `mlb_production_replay_cards` | One doc per displayed-card pick after dedupe-to-best-book + top-N-per-game | `(replay_serial, tier, game_id, rank)` |

All three are write-only by replay. No live-pipeline code reads them.

---

## 5. Risk Report for Phase 2

| Phase 2 work item | Files touched | Live regression risk | Mitigation |
|---|---|---|---|
| **2a — Gate engine provider plumb-through** | `services/scoring/gates/engine.py`, `services/scoring/tier_evaluator.py` | **LOW** — gates already pure-functional; adding a no-op provider argument is non-breaking | Default arg keeps live behavior identical |
| **2b — `MLBHighFrictionModel.predict()` `as_of_date` parameter** | `services/mlb_high_friction_model.py` | **MEDIUM** — function is called from ~5 places live; need to ensure all callers still work when `as_of_date=None` | Add as optional kwarg; route to live path when None; unit-test live path unchanged |
| **2c — `_fetch_recent_game_logs` accepts `as_of_date`** | `services/mlb_high_friction_model.py` | **MEDIUM** — historical leak risk | Add date filter to query; live behavior preserved when `None` |
| **2d — Universal odds sync historical adapter** | NEW FILE only; no edits to `universal_odds_sync.py` | **NONE** — additive | Replay path reads `mlb_historical_alt_odds_raw` directly without touching the live sync |

**Recommended smallest safe first refactor for Phase 2 (do in this order):**

1. **2a (gate engine)** — lowest risk, biggest payoff. Confirms the architectural approach works.
2. **2c (game-log as-of filter)** — closes the future-data-leakage hole in production code as a side benefit.
3. **2b (predict as_of_date)** — only after 2c is green.
4. Stop. Do not proceed to card-extraction (Phase 3) until 2a-2c are signed off with live regression smoke tests.

---

## 6. Live Pipeline Confirmation

✅ No production files modified in Phase 1.
✅ No live cron schedules modified.
✅ No mongo indexes added/dropped on live collections.
✅ All new code lives under `services/replay/providers/`.
✅ All new collections are not yet created (defined in code only).

---

## Files Created in Phase 1

| File | Purpose |
|---|---|
| `services/replay/providers/__init__.py` | Package marker |
| `services/replay/providers/base.py` | Abstract interfaces |
| `services/replay/providers/live.py` | Live provider skeletons (no-op pass-throughs) |
| `services/replay/providers/historical.py` | Historical provider skeletons + concrete `HistoricalOddsProvider`, `HistoricalFeatureProvider` |
| `services/replay/providers/audit.py` | Serial + checksum helpers extending `mlb_replay_multi_tier_eval` patterns |
| `services/replay/providers/schemas.py` | Pydantic models for the 3 new collection doc shapes |
| `backend/audits/PHASE1_PRODUCTION_REPLAY_HARNESS.md` | This document |
