# CHANGELOG

## 2026-05-20 — ingest_historical_player_stats v1

**Shipped:** `/app/backend/scripts/sgo/ingest_historical_player_stats.py`

**Source inspection findings:**
- `scripts/sgo/normalize.py:extract_player_stats()` already exists → SGO event
  payloads are preserved under `sgo_events.raw`, so we can **re-extract for
  free** without external API calls.
- `services/historical_data_fetcher.py` already integrates BallDontLie (BDL)
  for NBA — has `BDL_API_KEY` env var, paginated stats endpoint.
- No existing MLB Stats API adapter. Built one from scratch (free public,
  no key required).

**Architecture:**
- Single script, three pluggable sources + auto orchestrator:
  - `--source sgo` — re-extract from `sgo_events.raw`. Works for ALL leagues.
    Zero API calls.
  - `--source mlbstatsapi` — `https://statsapi.mlb.com/api/v1/schedule|boxscore`.
    MLB only. Free, no key.
  - `--source bdl` — `https://api.balldontlie.io/v1/stats`. NBA only.
    Requires `BDL_API_KEY` env var.
  - `--source auto` (default) — SGO first, then league-appropriate fallback
    for any date with zero rows.
- League-aware normalizer (`_normalize_mlb_stats` + `_normalize_nba_stats`)
  auto-dispatches by `league_id`. NBA composites (PRA, pts_reb, pts_ast,
  reb_ast, blocks_steals) computed in-flight; MLB `total_bases` derived from
  components when missing in source. `minutes` accepts "MM:SS" or float.
- Player_name → SGO player_id mapping via `sgo_players` cache (lowercased).
  Unmapped names fall back to `mlbam:<id>` / `bdl:<id>` synthetic IDs so
  data isn't lost.
- Preserves full raw source payload under `raw_source`.

**Synthetic verification (10 scenarios, all pass):**
- ✅ MLB normalizer (hits, doubles, HR, total_bases — including derived TB
  from 1B + 2*2B + 3*3B + 4*HR)
- ✅ NBA normalizer (PRA = pts + reb + ast, pts_reb, pts_ast, reb_ast,
  blocks_steals, minutes parsed from "MM:SS")
- ✅ Auto-detect normalize correctly picks NBA shape over MLB
- ✅ Dry-run writes 0
- ✅ End-to-end: ingest → outcomes pipeline grades both leagues
  (Judge MLB hits OVER 1.5 = WIN, LeBron NBA points OVER 27.5 = WIN)
- ✅ team/opponent correctly resolved from team_id matching
- ✅ Pitcher stats (pitcher_strikeouts, pitching_outs, ER, walks, pitches)
- ✅ Idempotency (count stable)
- ✅ `--league` filter (MLB-only, NBA-only)
- ✅ `--drop-existing` requires `--yes`
- ✅ All 6 required indexes created

**Deploy tarball:** `/tmp/sgo_deploy/ingest_historical_player_stats_v1.tar.gz`
(13 K)
SHA256: `70e4d894e8b80f14d87aeaf73ea858c854bcf206c5a9d5ceaa8c264afdddb9ed`
