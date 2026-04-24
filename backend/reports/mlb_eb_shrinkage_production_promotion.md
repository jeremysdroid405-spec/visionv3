# MLB EB Shrinkage — Production Promotion Verification
Generated: 2026-04-24 03:59 UTC  •  flag: `MLB_HF_EB_SHRINKAGE_ENABLED=true`
Target: `mlb_prop_scores@final-mlb-rt`

## Summary: PROMOTED SUCCESSFULLY, NO REGRESSIONS

During the first rescore attempt a motor/async vs pymongo/sync mismatch
silently skipped every shrinkage call (the helper had been written for
a Motor handle but the hub collection is owned by the sync pymongo
client that already backs the HF model). Fix: helper now takes
`hf_model.master_hub` directly. Second rescore applied cleanly.

## Projection means: before vs after promotion

| stat | actual mean | pre-EB proj mean | post-EB proj mean | pre-EB max | post-EB max |
|------|------------:|-----------------:|------------------:|-----------:|------------:|
| home_runs | 0.118 | 0.233 | **0.180** | 1.49 | **0.52** |
| rbis | 0.448 | 0.665 | **0.543** | 3.04 | **1.59** |
| total_bases | 1.339 | 1.676 | **1.507** | 6.88 | **4.46** |
| hits+runs+rbis | 1.691 | 1.937 | **1.839** | 5.31 | **3.84** |

## Applied vs skipped

| field | count |
|-------|------:|
| active whitelisted docs with audit fields | 2,165 |
| `eb_shrinkage_applied=True` | **1,356** |
| `eb_shrinkage_applied=False` (whitelist but skipped) | 809 |
| non-whitelist docs with EB applied | **0** (invariant ✓) |
| projection == eb_shrunk_projection where applied | **1,356 / 1,356** (invariant ✓) |
| HR projections > 1.0 after promotion | **0** (was 1 pre-EB) |

The 809 whitelisted skips break down to
`stat_not_whitelisted` (for wrapped rows) plus the expected
`insufficient_games_*` and `player_not_found` fallbacks for players
with < 20 batter-AB games in the hub.

## Tier counts — stable

| tier | pre-EB | post-EB | Δ |
|------|-------:|--------:|--:|
| safe_haven | 6 | 6 | 0 |
| front_lines | 1 | 1 | 0 |
| war_zone | 101 | 101 | 0 |
| unqualified | 2,198 | 2,198 | 0 |
| **total** | **2,306** | **2,306** | 0 |

Top-tier picks re-ordered as expected (see Top-20 below). Pick COUNT
preserved — EB rewrote per-row projection but left the slate
membership unchanged.

## Top-20 tiered picks after promotion

| # | player | stat | line | side | proj | raw_hf | source | tier | rs2 |
|---|--------|------|-----:|------|-----:|------:|-------|------|----:|
| 1 | Seiya Suzuki | RBIs | 0.5 | OVER | 1.59 | 1.59 | raw | war_zone | +1.016 |
| 2 | Leody Taveras | RBIs | 0.5 | OVER | **1.58** | 3.04 | **EB** | war_zone | +1.004 |
| 3 | Yordan Alvarez | Hits | 0.5 | OVER | 1.63 | 1.63 | raw | safe_haven | +0.955 |
| 4 | Dominic Smith | Hits | 0.5 | OVER | 1.54 | 1.54 | raw | front_lines | +0.803 |
| 5 | Yusei Kikuchi | Pitcher Strikeouts | 5.5 | OVER | 7.75 | 7.75 | raw | war_zone | +0.802 |
| 6 | Ozzie Albies | RBIs | 0.5 | OVER | **1.35** | 2.51 | **EB** | war_zone | +0.693 |
| 7 | Nolan Schanuel | RBIs | 0.5 | OVER | **1.33** | 2.58 | **EB** | war_zone | +0.663 |
| 8 | Ian Happ | Singles | 0.5 | OVER | 1.24 | 1.24 | raw | war_zone | +0.425 |
| 9 | Yusei Kikuchi | Pitcher Strikeouts | 6.5 | OVER | 7.66 | 7.66 | raw | war_zone | +0.317 |
| 10 | Zach Neto | Hits | 0.5 | OVER | 0.91 | 0.91 | raw | safe_haven | +0.244 |
| 11 | Ronald Acuna Jr. | Hits | 0.5 | OVER | 0.87 | 0.87 | raw | safe_haven | +0.221 |
| 12 | Freddie Freeman | Hits | 0.5 | OVER | 0.86 | 0.86 | raw | safe_haven | +0.215 |
| 13 | Dansby Swanson | RBIs | 0.5 | OVER | **1.04** | 1.59 | **EB** | war_zone | +0.169 |
| 14 | Kyle Manzardo | Singles | 0.5 | OVER | 0.72 | 0.72 | raw | war_zone | +0.116 |
| 15 | Jeremiah Jackson | RBIs | 0.5 | OVER | **0.87** | 1.05 | **EB** | war_zone | +0.113 |
| 16 | Mickey Moniak | RBIs | 0.5 | OVER | **0.86** | 1.23 | **EB** | war_zone | +0.110 |
| 17 | Max Muncy | Singles | 0.5 | OVER | 0.68 | 0.68 | raw | war_zone | +0.095 |
| 18 | Carter Jensen | RBIs | 0.5 | OVER | **0.78** | 1.05 | **EB** | war_zone | +0.085 |
| 19 | Adley Rutschman | RBIs | 0.5 | OVER | **0.76** | 1.35 | **EB** | war_zone | +0.081 |
| 20 | Drake Baldwin | RBIs | 0.5 | OVER | 0.74 | 0.50 | EB | war_zone | +0.073 |

Every formerly-inflated RBI 0.5 OVER now sits in a physically-sensible
0.7–1.6 range. Brandon Marsh's 1.49-HR outlier is gone (no HR proj > 1.0).

## Invariants

- `MLB_HF_EB_SHRINKAGE_ENABLED=true` persisted in `/app/backend/.env`
- Non-whitelist docs with EB applied: **0**
- Projection mismatches between `model_projection` and `eb_shrunk_projection` when applied: **0**
- HR projections > 1.0: **0**
- ECDF layer untouched (same 10 MLB artifacts serving)
- Gate thresholds untouched (0.55 / 0.45)
- Tier counts preserved

## Pre-existing issues surfaced (NOT caused by EB)

**7 negative projections** remain (e.g. `Max Muncy Doubles = −0.06`,
`Max Muncy Total Bases = −0.06`). These originate from the RAW HF
model itself (`raw_hf_projection` = `−0.06` on these docs) — the EB
floor at 0 would catch them if EB ran, but these stats either fall
outside the whitelist (`doubles`) or the player's batter-AB log
count missed the 20-game minimum. Not in scope for this promotion;
logged for future HF-model review.

## Rollback (if ever needed)

```
# 1. Comment out or remove the line in /app/backend/.env:
#    MLB_HF_EB_SHRINKAGE_ENABLED=true
# 2. Restart backend:
#    sudo supervisorctl restart backend
# 3. Rescore MLB:
#    python -c "import asyncio; from services.scoring.recompute import recompute_sport; ..."
```

No other code changes required. Projections revert to raw HF output;
audit fields on prior docs remain for forensics.
