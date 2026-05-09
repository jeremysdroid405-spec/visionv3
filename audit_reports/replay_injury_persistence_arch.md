# Replay Injury / Usage Layer — Persistence Architecture (Part 3 of Safe Haven Fix)

**Date:** 2026-05-09
**Author:** E1 (resume of part 3)
**Module:** `services/replay/injury_history.py` (new)

## Why
The replay engine had Matchup/Pace/DvP wired but the **injury / usage**
inputs `prop["usage_vacuum_factor"]` and `prop["usage_spike"]` were
unset → `vision_v2._context_component` was leaving 50% of its 4-input
context signal at neutral. Without it, historical replays could never
trigger the Safe Haven tier on roster-decimated games (the exact
scenarios where production fires Safe Haven hardest).

## What ships
1. **`services/replay/injury_history.py`** — pure-async pipeline that
   reconstructs historical injury / usage state strictly from
   `bdl_historical_game_logs` (no external API, no leakage):
   - `compute_team_injury_blob(db, team_id, snapshot_ts)`
   - `compute_player_usage_spike(db, bdl_player_id, snapshot_ts)`
   - `assemble_injury_blob(team_blob, spike_blob)` → returns the
     prop-ready blob with the two production fields at the top level
     (`usage_vacuum_factor`, `usage_spike`).

2. **`services/replay/engine.py`**
   - Imports the new module + adds two per-run process caches:
     - `injury_team_cache: (snapshot_date, team_id) → team_blob`
     - `injury_player_cache: (snapshot_date, bdl_player_id) → spike_blob`
   - After the matchup_blob block, builds the injury blob; passes it
     into `score_one_side` (new kwarg `injury_blob`); stamps
     `prop["usage_vacuum_factor"]` and `prop["usage_spike"]` exactly
     the way live production does.
   - Persists `injury_blob` (plus flat shortcut fields:
     `usage_vacuum_factor`, `usage_spike_flag`, `injury_completeness`)
     onto every Stage-B `replay_vk2_cache` row.
   - New eval-row fields: `usage_vacuum_factor`, `usage_spike`,
     `key_player_out_flag`, `rotation_compression`, `injury_out_count`,
     `injury_feature_completeness`.
   - Removed the `Injury usage_vacuum / usage_spike not yet wired`
     parity warning.

3. **`services/replay/scoring_only.py` (Stage C)** — reads
   `row["injury_blob"]`, stamps the same prop fields, writes them onto
   the eval row. Stage-C never re-aggregates BDL.

4. **`services/replay/cache.py`** — `injury_pipeline_hash()` now
   resolves to a real content hash (was placeholder
   `not_implemented:v1`), and `INVALIDATION_RULES["injury_pipeline_hash"]
   = ["injury_blob"]` — bumping the file invalidates cached injury
   blobs the way `matchup_pipeline_hash` invalidates `matchup_blob`.

5. **`backend/tests/test_replay_injury_history.py`** — 12 pytest tests:
   - parse_minutes / usage_proxy formula match production
   - team blob "missed last 3 → OUT" reconstruction
   - vacuum factor formula matches `feature_hydration` output
   - usage_spike threshold + flat-usage neutrality
   - leakage guard on rows after as-of
   - `assemble_injury_blob` completeness rollup
   - `vision_v2._context_component` contract check

## Reconstruction rules (no leakage, strictly as-of)
- **Rotation**: top-13 minutes leaders over the 20-day window strictly
  before snapshot.
- **OUT detection**: a rotation player is flagged OUT when they
  played 0 of the team's last 3 games (`min == 0` or no row).
  Three games is the standard threshold real-world injury reports
  converge on within 24-48 h.
- **Usage proxy**: production's `(fga + 0.44·fta + tov) / min · 36`,
  averaged over the player's last 10 games where `min > 0`
  (matches `feature_hydration._compute_team_injury_features`).
- **Vacuum formula**: `usage_vacuum_factor = 1 + Σ(out_usage_l10) / Σ(top13_usage_l10)`
  — same expression as `feature_hydration` line 514.
- **Usage spike**: `magnitude = (usage_l3 − usage_l10) / max(usage_l10, 1)`,
  flag when `magnitude ≥ 0.15`.
- **Key player out flag**: 1 if any of the top-2 minutes leaders is
  in the OUT list.

## End-to-end verification (small sample)
Single-event smoke (Kings vs Timberwolves, 2024-03-01, 200 props):
- 2 team_blob builds, 11 player_blob builds → ~196 cache hits
  → confirms per-team / per-player amortization is working.
- 304 / 308 cache rows: `injury_full`. 4 missing (player team
  resolver returned None — pre-existing edge case in `player_team_map`).
- avg `usage_vacuum_factor` = 1.065, max = 1.123, 21 of 308 props
  flagged `usage_spike` (≈7%) — within plausible historical ranges.
- Stage-C re-scoring picked up the cached blobs without re-aggregation
  (0.3s for 500 rows in the smoke earlier).

## Performance budget
- Stage-B work added per run: **2 builds per (snapshot_date, team)**
  + **N builds per (snapshot_date, player)**. For a 30-team night
  with 12 games and ~200 unique rotation players that's ~232 BDL
  aggregations / day, each scanning ~30 days × 1 team or ~10 games
  × 1 player — well under the 5-minute Stage-C budget.
- Stage-C unchanged: dict reads only.

## Cache schema additions (Stage-B `replay_vk2_cache`)
```jsonc
{
  "injury_blob": {
    "usage_vacuum_factor":  1.123,
    "usage_spike":          true,
    "key_player_out_flag":  1,
    "rotation_compression": 0.077,
    "out_count":            1,
    "out_player_names":     ["Jabari Smith Jr."],
    "missing_minutes":      27.6,
    "missing_usage_pct":    21.4,
    "team_total_usage":     181.2,
    "team_blob":            { /* full team rollup */ },
    "spike_blob":           { /* full player spike rollup */ },
    "feature_completeness": "injury_full",
    "error":                null
  },
  "usage_vacuum_factor":   1.123,   // flat shortcut
  "usage_spike_flag":      true,    // flat shortcut
  "injury_completeness":   "injury_full"
}
```

## Stage-C eval-row schema additions
- `usage_vacuum_factor`, `usage_spike`, `key_player_out_flag`,
  `rotation_compression`, `injury_out_count`,
  `injury_feature_completeness`.

## Fingerprint / invalidation
`fingerprint_block("nba")` now includes a real
`injury_pipeline_hash`. The diff runner
(`scripts/compare_replay_runs.py`) automatically picks this up and
will report `injury_pipeline_hash` as a changed component when
`injury_history.py` is edited; `INVALIDATION_RULES` then marks
`injury_blob` as the stale field.

## Backwards compatibility
Existing cache rows (24,670 from `parity_eng_1778340541`) lack
`injury_blob`. Stage-C reads `row.get("injury_blob") or {}` → defaults
to `usage_vacuum_factor=None`, `usage_spike=False` (vision_v2 treats
both as zero context contribution). No regression. To benefit from
the new layer those rows need a fresh full-engine run.

## Tests
```
$ pytest backend/tests/test_replay_*.py -q
................................................................   [100%]
64 passed in 1.84s
```
12 new tests added; 0 regressions.

## Next steps (downstream)
1. Run a fresh full replay over the Feb 2024+ window so the cache is
   re-populated with `injury_blob`.
2. Re-run the Safe Haven debug script
   (`scripts/replay_safehaven_debug.py`) to confirm Safe Haven now
   triggers on roster-decimated nights.
3. Extend `compare_replay_runs.py` to bucket by `usage_vacuum_factor`
   to surface "boost was the difference" cases.
