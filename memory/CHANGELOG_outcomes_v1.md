# CHANGELOG

## 2026-05-20 — build_historical_outcomes v1

**Shipped:** `/app/backend/scripts/sgo/build_historical_outcomes.py`

- Reads `sgo_pp_research_core_enriched` + `sgo_player_stats` (both immutable);
  writes `sgo_pp_research_outcomes`.
- Pluggable stat-resolver registry covering MLB batting/pitching + composites
  (hits_runs_rbis, fantasyScore), NBA atomic + composites (PRA, pts_reb,
  pts_ast, reb_ast). Unknown stat_ids fall back to direct key lookup in the
  player stats dict.
- Per-anchor grading: hit / push / WIN / LOSS / UNRESOLVED + outcome_numeric
  (1 / 0 / 0.5 / null) + margin_vs_line + stat_family canonical name.
- OOM-safe: game-date chunked; loads sgo_player_stats for that date into a
  `(event_id, player_id) → stats` map once per date.
- Resumable: `--resume` skips docs already at `grading_version=v1` with
  `outcome_resolved=True`.
- Bulk upserts (1000-doc batches); progress logged every 10k docs.
- Indexes: anchor pk + stat_family, outcome, outcome_resolved, hit,
  edge_vs_consensus, has_valid_devig, grading_version, etc.
- Telemetry: stat_family coverage breakdown printed in summary; sample
  graded docs in output.

**Synthetic verification** (12 scenarios, all pass):
- ✅ OVER win, LOSS, UNDER win, PUSH
- ✅ UNRESOLVED when player_stats row missing
- ✅ UNRESOLVED when stat field absent in stats dict
- ✅ Composite `hits_runs_rbis` (h+r+rbi) resolves correctly
- ✅ `fantasyScore` resolves via `fantasy_score` snake_case alias
- ✅ NBA `pts_reb_ast` (PRA) composite
- ✅ NBA atomic `points` UNDER
- ✅ Pitcher stats `pitcher_strikeouts`
- ✅ Unknown stat_id fallback via direct key lookup
- ✅ Idempotency, `--resume`, `--league`, date window, `--drop-existing` safety
- ✅ All required indexes
- ✅ **End-goal ROI aggregation works** (10 resolved props → 8W/1L/1P,
  roi_avg_numeric=0.85)

**Deploy tarball:** `/tmp/sgo_deploy/build_historical_outcomes_v1.tar.gz` (7.9 K)
SHA256: `5f5870b766c73ac5a70bbb37bddc0f867592895cd244e61fbb622abd2f60af83`
