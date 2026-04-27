# PRD — Living Document

## Problem Statement (verbatim)

Restructure React/FastAPI betting app to a 100% Local-First Database Model
with multi-sport support. Implement Google/Apple OAuth and Stripe for
payments. PRODUCT REQUIREMENTS: 100% ID-based joins. Universal Opportunity
Models and Probability modeling (ECDF / Line-Outcome Models) to fix
discrete zero-heavy props. FULL FEATURE ACTIVATION PROJECT for NBA and MLB.

## Architecture

- React frontend + FastAPI backend + MongoDB
- Local-First database: every external API call goes through a CRON-managed
  ingestion layer. Read paths are local-only.
- Universal Probability Engine (sport-agnostic, gates + thresholds in
  `services/scoring/gates/thresholds.py`)
- Three-tier output: Safe Haven / Front Lines / War Zone via odds-routed +
  gate-evaluated UniversalGateEngine.

## Active model configuration (2026-04-29)

- `NBA_RATE_BLEND_MODE = 100_0`  → PTS/PRA fully on rate × min layer
- `NBA_RFA_MINUTES_PENALTY = 0.85` → RFA picks get 0.85x expected minutes
- VK2 primary stats: AST, REB, 3PM (legacy VK still used for PTS, PRA)
- Tier routing universal across sports: ref_odds ≤ -240 → SH, -239..+149 →
  FL, ≥ +150 → WZ.

## Recent work (changelog)

### 2026-05 — Persistent forward-testing system (this session)
- New collection `nba_pick_history` with unique index on
  `(player, stat, line, game_date, side)`.
- Logger hook in `services/scoring/recompute.py` fires after
  `_reevaluate_tiers_post_vision` — read-only to model behavior, idempotent
  via `$setOnInsert` on outcome fields.
- Result updater script `scripts/update_nba_pick_results.py` joins
  ungraded rows against `nba_master_hub_2026.bdl_game_logs` +
  `bdl_historical_game_logs` + `nba_player_game_logs`. CLI flags:
  `--dry`, `--since`.
- Cron scheduled at 09:35 UTC nightly (5 min after master-hub sync).
- Analytics surface in `services/forward_test/pick_history.py`:
  `query_overall`, `query_by_stat`, `query_by_tier`,
  `query_by_edge_bucket`, `query_by_availability`, `query_by_side`.
- 8/8 unit tests passing. Smoke-tested end-to-end on live data:
  650 picks graded, edge-bucket sweet spot (10–15% bucket) confirmed at
  66% win rate / +25.9% ROI.

### 2026-04-29 — Production μ + σ historical backtest
- Built `/tmp/nba_propvision_backtest_full_prod.py` with full VK2 model
  driving μ for AST/REB/3PM (was using L10-mean as proxy, gave inflated
  numbers). Result: 53.3% / +1.7% ROI on 152 picks across 20 slates.
- Confirmed: gates alone are roughly break-even; the 77.2% live forward-test
  number comes from the post-gate stack (anchor + vision intel + market
  moves + operator selection).

### 2026-04-29 — RFA × 0.85 + 100/0 rate blend promotions
- Both flags promoted to production behind env vars.
- 272-pick curated forward-test: 77.2% hit rate (Δ +17.3 pts vs pre-cutover).
- Net +47 flips, 8.8:1 misses-avoided to hits-lost asymmetry.

## P0 / P1 / P2 backlog

### P0
- AST stat-level gate tightening (41.9% historical, lossy at population).
- Investigate 15%+ edge bucket inflation (54% win rate vs 66% in 10–15%).
- 0 Safe Haven historical picks — verify gate is reachable in practice.
- Decision: PRA / FULL_GO over-projection residual fix.

### P1
- 7-day shadow forward-test for War Zone recalibration.
- Recalibrate MLB Front Lines gates (blocked on user thresholds).
- PP-only stat-families TP calculation fix (PrizePicks hardcoded -137).
- Backfill historical game logs to close 2025-07 → 2026-02 coverage gap
  (would roughly double the historical replay sample).

### P2
- Emergent Google Auth + Stripe payments.
- `Dashboard.jsx` prop-drilling refactor.
- Cross-line synthetic UNDER pairing for alt-market devig.

### Future / Backlog
- NFL config scaffold for Universal Probability Engine.
- STL/BLK/Double-Double model training.
- Per-minute VK retrain (parked — user explicitly said not yet).

## Mocked / known limitations

- PrizePicks odds hardcoded at -137 placeholder (P1 to fix).
- `historical_odds` covers standard markets only — no alt lines, no PRA,
  no STL/BLK in the historical replay set.
- Live `team_total` and `sharp_implied` not stored historically; VK
  predictions silently default to baseline (115 / 50%) for those.
