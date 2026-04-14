# Changelog

## 2026-04-14 - MLB Deep Ingestion Complete
- Built async 15x parallel ingestion engine (`scripts/async_ingestion.py`)
  - `asyncio.Semaphore(15)` + `aiohttp.ClientSession` for max throughput
  - Active-only filter (777 players)
  - `bulk_write` every 100 players
  - Status counter per batch
- Ingested 777 active MLB players with 149,989 game logs (3 seasons)
- Post-migration: added `bdl_game_logs` (mapped fields), `is_pitcher`/`is_batter`
- Fixed `dk_odds` parameter mismatch in `rolling_cache_manager.py` (`_calculate_nba_intel`)
- Fixed BDL_API_KEY in backend .env (was set to wrong value)
- Purged empty shell documents from previous failed ingestion

## 2026-04-14 - NBA Deep Ingestion + Advanced Overlay Complete
- NBA async ingestion: 559 players, 112,778 game logs, 181.4s, 0 errors
- No-Loss Merge: preserved headshots, badges, advanced_stats via `$set` only
- Advanced Overlay: 15 metrics (usage_pct, true_shooting_pct, off_rating, def_rating, pace, etc.) merged into every NBA game log (100% coverage)

## 2026-04-14 - Automated Feature Discovery + Lasso Prediction Engine
- AutoFE pipeline: Raw Scan → 366 engineered features (interactions, time-derivatives, rolling volatility) → Lasso L1 selection
- 3 models trained: MLB Hits (R²=0.030), MLB Total Bases (R²=0.030), NBA Points (R²=0.486)
- Lasso Predictor engine (`/app/backend/models/predictor.py`) with StandardScaler normalization
- API endpoints: `GET /api/v3/lasso/predict/{sport}/{player}/{stat}` and `GET /api/v3/lasso/models`
- Validation: SGA → 23.4 pts (HIGH_FIDELITY), Ohtani → 0.57 hits / 1.02 TB (HIGH_VARIANCE)
