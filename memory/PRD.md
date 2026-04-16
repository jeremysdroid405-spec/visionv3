# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB). Implement automated feature engineering, generative AI scout intelligence, and a unified pipeline architecture for production reliability.

## Core Architecture

### Unified Pipeline Framework (April 16, 2026)
**One architecture, two sports.**

```
UnifiedPipeline (shared framework)
  ├── Phase 1: LOAD        — Read board, flatten, deduplicate
  ├── Phase 2-3: ENRICH    — Stats + scoring (adapter-specific)
  ├── Phase 4: VALIDATE    — Attach validation metadata
  ├── Phase 5: SELECT      — Tier classification + gate checks
  ├── Phase 6: PUBLISH     — Atomic writes (temp + rename)
  ├── Phase 6b: MARKET MOVES — Board-diff tracking (shared engine)
  └── Phase 7: INTEL       — Gemini enrichment (non-blocking)
```

### Market Moves Architecture (April 17, 2026)
**Board-diff, not market-diff. Triggered on every publish, not just hourly.**

```
ANY board publish event
  ├── UnifiedPipeline (Phase 6b)     → diff_and_update_from_tiers(db, sport, tiers)
  ├── mlb_tier_service (atomic_upsert) → diff_and_update_from_db(db, "mlb")
  └── mlb_master_sync (_store_tier)    → diff_and_update_from_db(db, "mlb")

Two entry points, same diff logic:
  - diff_and_update_from_tiers: uses in-memory tier dicts (fast, used by UnifiedPipeline)
  - diff_and_update_from_db: reads live tier collections from MongoDB (works for any write path)

Persistence:
  - Snapshots: market_moves_snapshots collection (survives restarts)
  - Events: market_moves collection (20-min TTL, upserted by pick_id)
```

### Stack
- **Frontend**: React + Shadcn/UI
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML**: Lasso Regression (10 models), XGBoost (VegasKiller, 5 NBA models)
- **LLM**: Gemini 3 Flash Preview via litellm + google-genai SDK
- **Cache**: Rolling cache with background enrichment

### Key Files
| File | Purpose |
|------|---------|
| `services/unified_pipeline.py` | Shared pipeline framework (7 phases + market moves) |
| `services/market_moves_engine.py` | Board-diff engine (shared NBA/MLB, MongoDB-persisted) |
| `services/adapters/nba_adapter.py` | NBA-specific scoring + MLR + tier selection |
| `services/adapters/mlb_adapter.py` | MLB-specific scoring + Lasso + tier selection |
| `services/gemini_scout_engine.py` | Batch AI insight generator |
| `routes/ferrari_tiers.py` | Core routing, serve-time overlay, market-moves API |

## What's Been Implemented

### Market Moves — Trigger Model Fix (4/17/2026)
- Decoupled from hourly sync: now fires on ANY board publish
- Two entry points: `diff_and_update_from_tiers` (UnifiedPipeline) + `diff_and_update_from_db` (legacy writers)
- Hooked into all 3 write paths: unified pipeline, mlb_tier_service, mlb_master_sync
- MongoDB-persisted snapshots survive restarts

### Market Moves Feature (4/17/2026)
- Shared board-diff engine tracking picks that leave visible tiers
- API: `GET /api/v3/ferrari/market-moves?sport=nba|mlb`
- Frontend: `MarketMoves.jsx` renders below tiers, sport-filtered

### Badge Fixes (4/17/2026)
- Removed `volatility_extreme` from context badges (deduplicated)
- Added `volatility_extreme` to frontend SCOUT_KEYS
- Renamed sections: "Environmental Factors" / "Performance Indicators"

### Auth Screen (4/17/2026)
- Countdown 3s → 5s, removed "DEMO MODE ACTIVATED" text

### Unified Pipeline Architecture (COMPLETE - 4/16/2026)
- Shared 7-phase pipeline, atomic writes, validation metadata

## Tier Collections
| Sport | Safe Haven | Front Lines | War Zone |
|-------|-----------|-------------|----------|
| NBA | elite_safe_haven | elite_front_lines | elite_war_zone |
| MLB | mlb_safe_haven | mlb_front_lines | mlb_war_zone |

## Pending Tasks
- P1: Google OAuth integration
- P1: Stripe payments integration
- P2: Live injury source investigation (NBA API vs ESPN staleness)
- P2: Wind Tunnel weather API
- P2: Dashboard.jsx refactor
- P3: Remove dead 4AM NBA.com L5/L10 batch jobs

---
*Last Updated: April 17, 2026*
