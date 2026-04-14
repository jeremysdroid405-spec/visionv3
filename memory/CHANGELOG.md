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
