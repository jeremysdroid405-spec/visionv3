# NBA/MLB Betting Analytics — Product Requirements

## Original Problem Statement
Restructure React/FastAPI betting app into a 100% Local-First, ID-based multi-sport analytics engine with Universal Opportunity and Probability models. Every scoring/model change must be backed by regression + mutation tests. Integrate Google/Apple OAuth and Stripe.

## Architecture Overview
- **Frontend:** React + Shadcn UI; dashboards read from `/api/v3/ferrari/*` tier endpoints.
- **Backend:** FastAPI. Scoring in `services/scoring/` with per-sport adapters (`nba_scoring.py`, `mlb_scoring.py`) feeding a universal gate engine (`services/scoring/gates/`).
- **Board flow:** `nba_live_props` (ingested odds) → `nba_scoring.NBAScoringAdapter.score()` → `nba_prop_scores` (tiered & gated) → `services/board/reader.py` (read/dedupe/publish) → ferrari_tiers routes.
- **Testing:** `/app/backend/tests/` — mutation-tested for core scoring changes.

## Core Models
1. **Projection (μ)**: VK (legacy) + VK2 (5-year adv stats) trained per stat. Rate×minutes and recency blends layered on for PTS/PRA.
2. **Sigma (σ)**: Phase 2 heteroscedastic lookup (minutes_bucket × line_bucket) — rescales base σ per prop.
3. **Probability engine** (`services/probability/distribution.py`): Normal CDF / Poisson / NegBin selector driven by (sport, stat, line).
4. **Gates**: `services/scoring/gates/thresholds.py` + `engine.py`. Tiers = {safe_haven, front_lines, war_zone, unqualified}.
5. **Vision Score**: Percentile-ranked edge signal; used by SH vision_score_gate.

## Current Status (2026-05-02)
### Done this session
- **Safe Haven calibration**: `vision_score_gate.min` 85 → 80; `edge_gate.min` 0.01 → 0.0.
- **Reverted Phase 1 additive debias** — was overcorrecting volume stars (SGA PTS 32.7 → 28.4 proj).
- **Shipped Phase 2 Heteroscedastic Sigma**: MULTIPLIER_TABLES built from 272 settled outcomes; minutes_bucket × line_bucket multipliers clipped to [0.5, 2.0] per bucket, [0.4, 2.5] total; audit stamped on every score doc (`hetero_sigma_base/adjusted/multipliers`).
- **33 regression tests + 3 mutation tests** — all passing.
- **Outcome**: NBA SH board went from 1 → **10** picks; edges now reflect honest distribution variance per bucket.

### Open issues (priority)
- **P0** `scored_at` NULL on all `prop_scores` docs — blocks UI staleness badges.
- **P1** Pitcher Strikeouts L20 fallback missing.
- **P1** PP-Only alt-line TP calculation.
- **P1** Inactive-player UNDER inflation (`min projection > 0` filter).
- **P1** MLB Debias Audit (mirror NBA process — must avoid the additive overcorrection trap).
- **P1** Decompose Dashboard.jsx (2k LOC) and picks_getter_service.py (3.2k LOC).

### Upcoming
- P0 Google/Apple OAuth (Emergent-managed Google Auth).
- P0 Stripe payments.
- P1 NFL config scaffold.
- P1 STL/BLK/Double-Double NBA model training.
- P1 User-facing error/stale states in dashboard.

## Key files
- `/app/backend/config/nba_sigma_heteroscedastic.py` — Phase 2 bucket multiplier tables + classifiers.
- `/app/backend/config/nba_projection_calibration.py` — debias (now all zeros, kept for plumbing).
- `/app/backend/config/nba_sigma_buckets_provenance.yaml` — build-provenance for MULTIPLIER_TABLES.
- `/app/backend/services/scoring/adapters/nba_scoring.py` — `_engine_p_over` applies heteroscedastic σ.
- `/app/backend/services/scoring/gates/thresholds.py` — tier thresholds.
- `/app/backend/services/scoring/prop_scores_store.py` — `_SCORE_OUTPUT_FIELDS` allowlist controls persisted fields.
- `/app/backend/scripts/build_nba_sigma_buckets.py` — rebuilds MULTIPLIER_TABLES from settled outcomes.
- `/app/backend/tests/test_nba_heteroscedastic_sigma.py` — 33 regression tests.
- `/app/backend/scripts/mutation_test_heteroscedastic.sh` — 3 mutations, all caught.

## Data model highlights
- `nba_prop_scores` / `mlb_prop_scores` — versioned by `version_tag` (canonical live tag = `final-nba-rt` / `final-mlb-rt`).
- Every doc carries universal pool fields (`active`, `game_start_utc`, `inactive_reason`).
- New fields: `hetero_sigma_base`, `hetero_sigma_adjusted`, `hetero_sigma_multipliers`.

## Known fragilities
- Watcher ingestion pulls ~4 days ahead (May 2 → May 6 slate confirmed). Time horizon is NOT the bottleneck for SH scarcity.
- Vision score = 0.0 on direction-fail picks is expected behavior, not a bug.
- `_SCORE_OUTPUT_FIELDS` in `prop_scores_store.py` is a strict allowlist — new audit fields must be added there or they silently drop.
