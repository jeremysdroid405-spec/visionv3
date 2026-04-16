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
  └── Phase 7: INTEL       — Gemini enrichment (non-blocking)
```

**Sport Adapters:**
- `NBAAdapter` — BDL stats, MLR/VK model, blowout risk, vacuum/momentum
- `MLBAdapter` — BDL game logs, Lasso models, SP matchup, tempo

**Identical across both sports:**
- Phase structure, validation model, atomic writes, observability, serve-time behavior

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

### API Status Flags
Every tier endpoint returns:
```json
{
  "status": "full|partial|no_data",
  "pipeline": {
    "source": "elite_safe_haven",
    "fully_validated": 8,
    "with_mlr": 10,
    "with_gemini": 7
  }
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
| `services/unified_pipeline.py` | Shared pipeline framework (7 phases) |
| `services/adapters/nba_adapter.py` | NBA-specific scoring + MLR + tier selection |
| `services/adapters/mlb_adapter.py` | MLB-specific scoring + Lasso + tier selection |
| `services/nba_master_sync.py` | NBA orchestrator (thin wrapper) |
| `services/mlb_pipeline.py` | MLB orchestrator (thin wrapper) |

## What's Been Implemented

### Badge Deduplication Fix (4/17/2026)
- Removed `volatility_extreme` from MLB context badges — it was duplicated in both context and scout badges
- `volatility_extreme` now correctly lives only in scout badges (model-driven metric)

### Unified Pipeline Architecture (COMPLETE - 4/16/2026)
- Shared 7-phase pipeline framework
- NBA and MLB adapters with identical publish/validate/observe behavior
- Atomic writes (temp collection + rename, never leaves collections empty)
- Validation metadata on every prop
- Status flags on API responses (full/partial/no_data)
- Gemini decoupled from pipeline (failure marks has_gemini=false, doesn't crash)

### Previous Implementations
- MLB Intel Suite Full Enrichment (4/15/2026)
- Scout Badges UI + MLB/NBA Parity (4/15/2026)
- Gemini Scout Engine refactored to litellm (4/15/2026)
- Elite Tier Classification Fix (4/15/2026)
- MLB DK Tier Sorting Fix (4/15/2026)
- CV Scale Mismatch Fix (4/15/2026)
- Goblin Line Gates Calibration (4/15/2026)

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

---
*Last Updated: April 17, 2026*
