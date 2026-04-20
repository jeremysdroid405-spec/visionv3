# PRD — NBA Ferrari / PropVision AI

## Product Goal
Restructure React/FastAPI betting app into a 100% local-first MongoDB
architecture with multi-sport support, automated feature engineering, and
a unified pipeline anchored on canonical odds data (PrizePicks anchor,
DraftKings/BetMGM exact-match reference layers). Surface pricing anomalies
through market-consensus probabilities.

## Current Focus
Restore the historically profitable VK1 signal (done), optimize the
projection-gap ranking formula (done, α=0.40), and balance small-line
dominance without hard-rejecting profitable plays (done — audits prove
α=0.40 + per-tier cap 10 + player dedupe is accretive to ROI).

---

## Completed Highlights (chronological)
- **2026-02-20** VK1 signal restored as `p_true_method="model"` with `p_model ≥ 0.55` gate in `scoring_stack.py:315`.
- **2026-02-20** `ranking_score_v2 = (raw_gap / max(line,1)^0.40) × p_model` shipped behind `?sort=gap` API param.
- **2026-02-20** Dashboard.jsx UI toggle (Default vs Projection Gap) shipped with localStorage persistence.
- **2026-04-20** NBA tiers rebuilt to apply new ranking across all tiers.
- **2026-04-20** Board-truth audit completed (`/tmp/board_truth_report.md`): UI endpoint output is byte-identical to reader output; no hidden filter.
- **2026-04-20** Board-faithful historical replay completed (`/tmp/board_faithful_replay_report.md`): board output delivers **+14.1 pp real-odds ROI over candidate-universe Top-25** at 22% smaller sample size. Per-tier cap (54) + dedupe (6) account for the 60 demoted picks; both are accretive.
- **2026-04-20** Sort head-to-head completed (`/tmp/sort_head_to_head.log`): `vision_score` and `ranking_score_v2` share 200/207 (96.6%) of picks; the 14 divergent picks are within noise. Toggle removed.
- **2026-04-20** **Projection-gap made the default sort in the UI.** Removed the `default vs gap` toggle from `Dashboard.jsx`; hardcoded `nbaSortParam = 'gap'`. Backend retains support for `?sort=default` for internal testing. (Frontend-only diff, no backend changes.)

---

## Architecture
- **Frontend:** React + Shadcn UI — `/app/frontend/src/pages/Dashboard.jsx`, hooks in `/app/frontend/src/hooks/useLiveOdds.js`.
- **Backend:** FastAPI — `/app/backend/server.py`, `/app/backend/routes/ferrari_tiers.py`.
- **Services:** `services/scoring/recompute.py` (ranking), `services/scoring/scoring_stack.py` (tier gates), `services/scoring/adapters/nba_scoring.py`, `services/board/reader.py` (universal reader), `services/board/adapters/nba.py` (NBA tier sort keys).
- **DB:** MongoDB — `nba_prop_scores`, `nba_live_props`, `historical_odds`, `bdl_historical_game_logs`, `nba_master_hub_2026`.

## Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=nba[&sort=gap]`
- `GET /api/v3/ferrari/front-lines?sport=nba[&sort=gap]`
- `GET /api/v3/ferrari/war-zone?sport=nba[&sort=gap]`
- UI hardcodes `&sort=gap`. Backend still accepts `?sort=default` for internal debugging.

---

## Roadmap

### P1 — Next
- **Injury-Rank Phase 2 (usage-sorted teammate semantics).** Replace `my_index` loop-order in `services/injury_advantage.py` with a descending sort against `nba_master_hub_2026.advanced_stats.usage_percentage` / `star_usage_cache`.
- **Emergent Google OAuth** (via `integration_playbook_expert_v2`). Replace Demo Mode as primary entry.
- **Stripe payments** (pod test keys; via `integration_playbook_expert_v2`).
- **Dashboard.jsx refactor** — break 2000-line file into focused sections after auth lands.

### P2 — Backlog
- Wave 3 post-migration cleanup Drop Step B (after 24h observation window).
- "Batch 15" script plumb-through: 14 maintenance scripts in `scripts/*` still reference legacy DB names.
- Wind Tunnel weather API integration (Atmospheric Data for MLB).
- Retire Legacy Writers (Universal Multi-Sport Board Engine Step 6).
- Regenerate stale introspection artifacts under `/app/frontend/public/` (remove `demon-tracker` endpoints).
- Cross-sport logo collision audit (MLB Cleveland vs NBA Cavaliers).
- Audit `scripts/ensure_indexes.py` and `scripts/init_database.py` for legacy DB hardcodes.
- **(New)** NBA-native tier admission table — today NBA stats fall through to MLB `"hits"` gate defaults (not a bug, but worth formalizing).

---

## Integrations
- BallDontLie API (user key)
- The Odds API (user key)
- Google Gemini (user key)
- Emergent LLM key available as fallback for Claude/OpenAI/Gemini text+image.

## Test Notes
- Last board-truth + board-faithful audits 2026-04-20 against live NBA slate of 2,746 props, tier-qualified 155, board surfaced 29 across 3 tiers.
- Historical replay universe: 7,403 VK1-graded props across 28 slates (2025-02 → 2026-03). Board-faithful ROI +83.8% real-odds / 61.4% WR on 207 bets.

## Health
- Broken: None
- Mocked: None
