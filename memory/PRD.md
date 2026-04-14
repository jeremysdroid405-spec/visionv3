# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB/NBA evaluation system.

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: XGBoost regression (64 features)
- **Cache**: Rolling Cache Architecture (master_active_cache.json)

## What's Been Implemented

### Rolling Cache Architecture v2.0 (COMPLETE - 4/14/2026)

#### APEX-ONLY CACHING
- **NO "Empty Shell" Props** - Props are ONLY cached AFTER enrichment succeeds
- **Format Normalization Layer** - `normalize_to_intel_mapping.py`
- **Cache Integrity Check** - Missing intel treated as "new", re-processed
- **Synchronous Enrichment Stitching** - Validate before cache write

#### Architecture Files:
| File | Purpose |
|------|---------|
| `rolling_cache_manager.py` | Delta manager, JIT enrichment, cache I/O |
| `normalize_to_intel_mapping.py` | Format normalization, validation |
| `intel_cache.py` | API routes for instant frontend display |

#### Cache Files:
- `/app/backend/data/nba_master_active_cache.json`
- `/app/backend/data/mlb_master_active_cache.json`

#### API Endpoints:
- `GET /api/v3/intel-cache/nba` - Instant NBA props (from JSON file)
- `GET /api/v3/intel-cache/mlb` - Instant MLB props (from JSON file)
- `GET /api/v3/intel-cache/status` - Cache health status

### Enriched Prop Structure (Apex Version)
```json
{
  "player_name": "Miguel Rojas",
  "stat_type": "RBIs",
  "line": 0.5,
  "vk_data": {
    "predicted": 0.25,
    "prob_over": 33.1,
    "prob_under": 66.9,
    "edge": 2.4,
    "verdict": "STRONG_UNDER",
    "sigma_used": 0.56,
    "sigma_source": "TRUE_L20_STABILIZED_SHIELD"
  },
  "vision_summary": "Park: LAD (neutral, 1.00x) | vs RHP: .255 | L10: 0.1 | σ=0.56",
  "scout_badges": ["high_stability"],
  "matchup_analysis": {
    "splits": {"vs_lhp_avg": 0.23, "vs_rhp_avg": 0.255, ...},
    "park": {"venue": "LAD", "factor": 1.0, ...},
    "opponent": {"team": "LAD", "k_rate": 0.97},
    "trends": {"l10_avg": 0.1, "momentum": -0.05}
  },
  "l20_variance": {
    "cv_l20": 0.32,
    "std_l20": 0.56,
    "consistency": 0.68
  },
  "_enriched": true,
  "_enriched_at": "2026-04-14T06:20:00Z"
}
```

### Global Variance Synchronization (L20 Stabilized Shield)
| Layer | Metric | Window | Purpose |
|-------|--------|--------|---------|
| Performance | Hit Rate | L10 | Current "Heat" |
| Risk | CV/Sigma | L20 | "Stabilized Shield" |

### MLB Physical Engine v2.1
- 64-feature XGBoost model
- 5 trained models: hits, total_bases, rbis, runs, pitcher_strikeouts
- L20 variance calculations

## Pending Tasks

### P1 - High Priority
- Google OAuth integration
- Stripe payments integration

### P2 - Medium Priority
- Wind Tunnel weather API
- Refactor frontend Dashboard.jsx

## Test Results (4/14/2026)
```
MLB Cache Test:
- New props: 2158
- Enriched: 14  (players with BDL splits data)
- Failed: 16   (players missing from master hub)
- Total cached: 27 (ALL FULLY ENRICHED)
- Empty shells: 0
```

---
*Last Updated: April 14, 2026*
*Rolling Cache Architecture v2.0 - Apex-Only Caching Complete*
