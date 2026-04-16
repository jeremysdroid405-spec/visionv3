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

**Sport Adapters:**
- `NBAAdapter` — BDL stats, MLR/VK model, blowout risk, vacuum/momentum
- `MLBAdapter` — BDL game logs, Lasso models, SP matchup, tempo

**Identical across both sports:**
- Phase structure, validation model, atomic writes, observability, serve-time behavior, market moves

### Stack
- **Frontend**: React + Shadcn/UI
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML**: Lasso Regression (10 models), XGBoost (VegasKiller, 5 NBA models)
- **LLM**: Gemini 3 Flash Preview via litellm + google-genai SDK
- **Cache**: Rolling cache with background enrichment

### Validation Metadata (on every prop)
```json
{
  "validation": {
    "has_market_data": true,
    "has_hit_rates": true,
    "has_context": true,
    "has_mlr": false,
    "has_gemini": true,
    "is_fully_validated": false
  }
}
```

### Market Moves Event Shape
```json
{
  "sport": "nba",
  "pick_id": "nba|player_name|stat_type",
  "player_name": "Player Name",
  "stat_type": "PTS",
  "previous_tier": "Safe Haven",
  "old_line": 22.5,
  "new_line": 24.5,
  "status": "Line moved",
  "changed_at": "2026-04-17T05:35:00Z"
}
```

### Environment Variables
| Variable | Default (Preview) | Production |
|----------|-------------------|------------|
| MODEL_DIR | /app/backend/models | /var/www/app/backend/models |
| CACHE_DIR | /app/backend/data | /var/www/app/backend/data |
| GEMINI_MODEL | gemini-3-flash-preview | gemini-3-flash-preview |
| GOOGLE_API_KEY | (set in .env) | (set in .env) |

### Key Files
| File | Purpose |
|------|---------|
| `services/unified_pipeline.py` | Shared pipeline framework (7 phases + market moves) |
| `services/market_moves_engine.py` | Board-diff engine (shared NBA/MLB) |
| `services/adapters/nba_adapter.py` | NBA-specific scoring + MLR + tier selection |
| `services/adapters/mlb_adapter.py` | MLB-specific scoring + Lasso + tier selection |
| `services/gemini_scout_engine.py` | Batch AI insight generator |
| `routes/ferrari_tiers.py` | Core routing, serve-time overlay, market-moves API |

## What's Been Implemented

### Market Moves Feature (4/17/2026)
- Shared board-diff engine tracking picks that leave visible tiers
- In-memory previous-board snapshot per sport, diffs on each pipeline run
- MongoDB `market_moves` collection with 20-min TTL auto-prune
- API: `GET /api/v3/ferrari/market-moves?sport=nba|mlb`
- Frontend: `MarketMoves.jsx` renders below tiers, sport-filtered, visually secondary
- Statuses: Line moved, Moved off board, Locked, No longer qualified

### Badge Fixes (4/17/2026)
- Removed `volatility_extreme` from context badges (was duplicated in both)
- Added `volatility_extreme` to frontend SCOUT_KEYS so it renders in Performance Indicators grid
- Renamed "Context Badges" → "Environmental Factors", "Scout Badges" → "Performance Indicators"

### Auth Screen Updates (4/17/2026)
- Countdown changed from 3s to 5s
- "DEMO MODE ACTIVATED" text removed, replaced with "SYSTEM ACTIVATED"

### Unified Pipeline Architecture (COMPLETE - 4/16/2026)
- Shared 7-phase pipeline framework
- NBA and MLB adapters with identical publish/validate/observe behavior
- Atomic writes, validation metadata, status flags
- Gemini decoupled from pipeline (Phase 7, non-blocking)

## Tier Collections
| Sport | Safe Haven | Front Lines | War Zone |
|-------|-----------|-------------|----------|
| NBA | elite_safe_haven | elite_front_lines | elite_war_zone |
| MLB | mlb_safe_haven | mlb_front_lines | mlb_war_zone |

## Pending Tasks
- P1: Google OAuth integration
- P1: Stripe payments integration
- P2: Wind Tunnel weather API
- P2: Dashboard.jsx refactor
- P3: Remove dead 4AM NBA.com L5/L10 batch jobs (data comes from BDL game logs)

---
*Last Updated: April 17, 2026*
