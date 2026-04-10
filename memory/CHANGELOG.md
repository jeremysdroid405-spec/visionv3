# PropVision AI - Changelog

## 2026-04-10 - MLB Scout Intel Badges in Vision Intel Suite

### Added
- **8 MLB Scout Intel Badges** integrated into BADGE_REGISTRY:
  - Pure Contact, High-Heat Trap, Workhorse, Barrel Master
  - Wind Blowing Out, Cold Zone, BvP Dominator, Split Advantage

- **Sport-aware Vision Intel Suite**:
  - NBA shows "CONTEXT BADGES" with 11 NBA-specific badges
  - MLB shows "SCOUT INTEL BADGES" with 8 MLB-specific badges
  - Badges filter automatically based on currentSport

- **MLB player API badge evaluation**:
  - `/api/v3/mlb/player/{name}` now returns `scout_badges` array
  - Each prop evaluated against badge criteria via `mlb_badge_system`

### Files Modified
- `/app/frontend/src/components/ui/BadgePill.jsx` - Added MLB badges to registry
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Sport-aware badge grid
- `/app/backend/routes/ferrari_tiers.py` - Badge evaluation in MLB player endpoint

---

## 2026-04-10 - MLB Stats Display Fix

### Fixed
- **MLB pick cards now display L5/L10/Avg stats** identical to NBA cards
  - Implemented `calculate_mlb_hit_rates()` in `mlb_sharp_sorting_service.py`
  - Calculates hit rates from `mlb_historical_logs` collection (2,270 players)
  - Maps MLB stat types (Hits, Total Bases, RBIs, Hits+Runs+RBIs, etc.) to game log fields
  
- **Removed NBA injury cards from MLB view**
  - Updated `Dashboard.jsx` to conditionally render `LiveInjuryAdvantageSection` only for NBA
  - MLB view now starts directly with Safe Haven section

### Technical Details
- Backend enriches MLB picks with `h5_rate`, `h10_rate`, `season_avg` during sharp sorting
- Added `normalize_mlb_pick_for_ui()` in `ferrari_tiers.py` as fallback for legacy data
- MLB historical logs contain game stats: hits, runs, rbis, total_bases, home_runs, stolen_bases, etc.

### Files Modified
- `/app/backend/services/mlb_sharp_sorting_service.py` - Added hit rate calculation from game logs
- `/app/backend/routes/ferrari_tiers.py` - Added MLB pick normalization
- `/app/frontend/src/pages/Dashboard.jsx` - Made injury section NBA-only

---

## Previous Sessions

### Session: MLB 4-Gate System & Oracle Integration
- Created `mlb_four_gate_system.py` for Gate 1-4 evaluation
- Created `propvision_oracle_service.py` for Gemini batch analysis
- Created `mlb_badge_system.py` for Scout Insight badges
- Fixed tier assignment to use PrizePicks odds (Pinnacle unavailable for MLB)
- Fixed duplicate tier sections on Dashboard

### Session: Universal Odds Sync
- Updated `universal_odds_sync.py` to pull ALL 27 available markets from PrizePicks
- Added DK/Pinnacle edge references (`dk_line`, `sharp_line`) for backend analysis
- Frontend strictly shows PrizePicks data only
