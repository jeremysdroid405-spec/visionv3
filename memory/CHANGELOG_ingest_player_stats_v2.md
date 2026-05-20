# CHANGELOG

## 2026-05-20 — ingest_historical_player_stats v2 (SGO API as primary)

**Why:** Production showed `events_scanned=3565, events_with_zero_playerStats=3565, rows_emitted=0`. Root cause confirmed: SGO `/v2/events/` only includes player stats when `expandResults=true` is passed. Original ingest didn't pass it, so `sgo_events.raw` archives lack stats.

**SGO endpoint inspection (https://sportsgameodds.com/docs/reference):**
- `/v2/stats` → stat **taxonomy/metadata only** (statID, sportID, statLevel definitions). NOT per-player results.
- `/v2/events?expandResults=true&eventID=...` → event response includes `results` object with all stat values per player/team. **This is the canonical historical-stats source.**
- `/v2/events?leagueID=...&finalized=true&expandResults=true` → paginated stream of completed events with results inline.
- **No separate `/v2/players/{id}/stats` or `/v2/results/` endpoint exists.** Player stats live inside the event payload.

**Patched:**
- `scripts/sgo/client.py` — added `get_event_with_results()`, `iter_finalized_events_with_results()`, `get_stats_taxonomy()`.
- `scripts/sgo/ingest_historical_player_stats.py` — added `--source sgo_api` (PRIMARY in `auto`). Defensive extractor handles multiple `results` shapes (`byEventEntity`, `players[]`). Output now includes `stat_entity_id` and `stats_sgo_canonical` raw dict.
- `scripts/sgo/build_historical_outcomes.py` — multi-tier join (`player_id` → `stat_entity_id` → `player_name`). New resolvers for SGO canonical statIDs: `batting_basesOnBalls`, `batting_RBI`, `batting_hits+runs+rbi`, `pitching_strikeouts`, `pitching_hits`, `pitching_earnedRuns`, `pitching_pitchesThrown`, `points+rebounds+assists`, `points+rebounds`, `points+assists`, `rebounds+assists`, `blocks+steals`, `minutesPlayed`.
- MLB / NBA normalizers now accept SGO canonical keys (`batting_*`, `pitching_*`).

**New:**
- `scripts/sgo/verify_sgo_player_stats_coverage.py` — read-only coverage audit (event coverage %, player coverage %, alt-mapping recoverable %, outcome resolution %, stat_family breakdown, sample unresolved rows).

**Synthetic verification:**
- ✅ `_extract_results_to_player_rows` correctly handles `results.byEventEntity` shape (team aggregates skipped, players extracted with team/opp resolution)
- ✅ Same for `results.players[]` shape
- ✅ MLB normalizer recognizes SGO `batting_*` / `pitching_*` keys
- ✅ NBA composite SGO statIDs resolve correctly (`points+rebounds+assists` → 48 PRA, `blocks+steals` → 3, `minutesPlayed` "36:24" → 36.4)
- ✅ Pitcher SGO statIDs resolve to correct stat_family (`pitching_strikeouts` → `pitcher_strikeouts`, etc.)
- ✅ Outcomes pipeline test still passes (existing scenarios unaffected)
- ✅ ROI aggregation test still passes (8W/1L/1P on synthetic data)
- ✅ End-to-end ingest → outcomes test still passes (Judge hits OVER WIN, LeBron points OVER WIN)

**Deploy tarball:** `/tmp/sgo_deploy/ingest_historical_player_stats_v2.tar.gz` (26 K)
SHA256: `e60d985974f55c9badd932434c2eb36898167a92177658ad4860448bb69c5625`

**Contents:**
- `backend/scripts/sgo/client.py` (patched)
- `backend/scripts/sgo/ingest_historical_player_stats.py` (patched)
- `backend/scripts/sgo/build_historical_outcomes.py` (patched)
- `backend/scripts/sgo/verify_sgo_player_stats_coverage.py` (new)
- `README.txt` (with prod run plan, mongosh verify queries, defensive notes)
