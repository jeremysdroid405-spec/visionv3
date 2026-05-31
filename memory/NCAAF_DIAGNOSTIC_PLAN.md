# NCAAF Diagnostic Report & Patch Plan

> Why this exists: NCAAF outcomes built (127,663) but only 13,482 resolved
> and `feature_ready = 0`. Goal: identify root cause and define a
> minimum-risk path to `feature_ready > 0` *without* touching MLB/NBA/NFL.

---

## How to run

```bash
# Full report (use this first)
python -m scripts.sgo.diagnose_ncaaf_pipeline

# Faster sample (200k anchors → ~5-10s)
python -m scripts.sgo.diagnose_ncaaf_pipeline --sample 200000
```

Script is **read-only** — never writes, never drops, never creates
indexes. Scoped to `league_id="NCAAF"` everywhere. Paste the output
back here when complete and we'll triage from data, not theory.

---

## What the report tells us (and what each section unlocks)

### §1 — Player Continuity

Distribution of `len(games_in_sgo_player_stats)` per player, plus a
per-stat_family table showing how many distinct players have ≥1 / ≥3
/ ≥5 historical games.

**Interpretation:**
- If **<1k players have ≥5 prior games** → college-football turnover
  + late-season-only stats are the bottleneck, not a code bug.
  Recommended response: lower `min_prior_games`, add team-context
  features (see §6).
- If **most players have ≥5 games** but feature_ready=0 → bug in
  the feature builder's lookback window or join key.

### §2 — Outcome Resolution Report

Per-`stat_family` resolution rate and a global breakdown of
`unresolved_reason_detail`. Plus a sample of `player_not_in_results`
rows.

**Interpretation:**
- High `player_not_in_results` → either DNP-style misses or, more
  likely, **player_id mismatch** between props and stats sources.
- High `stat_not_in_results` → resolver registry gap (e.g. NCAAF has
  passing markets that NFL doesn't, or vice versa).
- Resolution rate concentrated in core markets (passing/rushing
  /receiving yards) and bleeding on long-tail markets → expected, not
  a blocker.

### §3 — Player-ID Audit

Set intersection of `player_id` between `ncaaf_player_historical_props`
and `sgo_player_stats`. Classifies every `player_not_in_results`
unresolved row as **true DNP** (pid IS in stats, just not in that
game) vs **ID mismatch** (pid is NOT in stats at all).

**Interpretation — this is the diagnostic that decides everything:**

| Finding | Root cause | Fix difficulty |
|---|---|---|
| ID-mismatch >> DNP (e.g. 80% ID-miss) | SGO ships two different player_id namespaces for the same college player (one in props feed, one in stats feed). Common in NCAAF. | **Medium** — write a name-key reconciliation pass that maps props.player_id → stats.player_id via `(player_name, team_id)`. ~150 LOC. |
| DNP >> ID-mismatch | The props feed quoted markets for players who didn't actually play that week (injury, depth-chart change, transfer portal mid-season). This is data, not a bug. | **Low** — these rows just stay UNRESOLVED. Live with it. |
| Both balanced | Mixed problem — do ID reconciliation first to maximize signal, then accept the DNP tail. | **Medium**. |

### §4 — Feature-Readiness Experiment

For every outcome anchor, count games in `sgo_player_stats` for that
player STRICTLY BEFORE the anchor's `game_date`. Histogram (0 / 1 / 2
/ 3-4 / 5+) and threshold sweep at `min_prior_games ∈ {1, 2, 3, 5}`.

**Interpretation:**
- `MIN_GAMES_REQ=5` is the current cutoff in
  `build_historical_model_features.py`. If `≥5` is 0% but `≥3` is
  e.g. 40% → the cutoff is too aggressive for college (12-game
  regular season vs MLB's 162). Lower it.
- If `≥1` is also near 0% → the *date join* is wrong, not the
  threshold. Inspect `game_date` formats in both collections (one
  ISO timestamp vs one yyyy-mm-dd string is a classic NCAAF gotcha).

### §5 — Market-Filter Experiment

Repeats §4 restricted to the **core market allowlist**:

```
passing_yards, passing_attempts, passing_completions, passing_touchdowns,
rushing_yards, rushing_attempts,
receiving_yards, receptions, receiving_receptions
```

These nine markets cover ~90% of college football betting volume and
should be the cleanest signal. If readiness is dramatically better
here than in §4, **start training on this subset only**.

---

## §6 — Recommended NCAAF Feature Strategy

College football data is structurally noisier than the pros: shorter
season, higher roster churn, depth-chart volatility, transfer portal,
and SGO's player_id stability for CFB is historically worse than for
NFL. The feature strategy below explicitly accepts that and routes
around it rather than fighting it.

### Tier A — Player History (where available)

**Status:** Default path. Will work for QB1 / RB1 / WR1 starters with
≥3 games of prior data. Likely ≤30-40% of NCAAF anchors based on
typical CFB roster turnover.

**Concrete changes (in `build_historical_model_features.py`):**

1. **NCAAF-specific `MIN_GAMES_REQ`.** MLB/NBA/NFL stay at 5. Add:
   ```python
   MIN_GAMES_REQ_BY_LEAGUE = {
       "MLB": 5, "NBA": 5, "NFL": 5,
       "NCAAF": 3,   # 12-game regular season; 3 = ¼ season
   }
   ```
   Don't lower below 3 — `last_3` average is the minimum viable
   "form" signal. Anything less is noise.

2. **NCAAF-tuned lookback window.** Current default is 90 days. For
   college, that pulls in last season's data because of the long
   off-season. Either:
   - `--lookback-days 365` to capture both halves of the season +
     last bowl (full prior season), OR
   - Add `LOOKBACK_DAYS_BY_LEAGUE = {"NCAAF": 365, default 90}` so
     the user doesn't need to remember the flag.

3. **`last_3` is the primary feature, not `last_10`.** For NCAAF,
   `last_10` would span half a season + bowl + non-conference
   opener — that's a different player by then. Down-weight or skip.

### Tier B — Team Context

**Status:** Net-new for NCAAF. High-leverage. Should work even when
player history is zero.

**Concrete additions:**

1. **Team rolling-offense features** from `ncaaf_matchups`:
   - `team.points_per_game_last_3`
   - `team.yards_per_play_last_3`
   - `team.pass_attempts_per_game_last_3`
   - `team.rush_attempts_per_game_last_3`
   - `team.points_scored_var_last_3`  (volatility proxy)

2. **Team pace / play-count proxy** — `(home_score + away_score) /
   sum(game_clock)` rolling avg. Single biggest projection lever for
   over markets.

3. **Implementation hook:** new helper
   `_team_rolling_features(team_id, before_date, lookback_games=3)`
   inside `build_historical_model_features.py`, called once per
   anchor's home/away team and attached to the feature row.

### Tier C — Opponent Context

**Status:** Net-new for NCAAF. High-leverage for individual stats.

**Concrete additions:**

1. **Opponent-allowed rolling stats** from `ncaaf_matchups` +
   `sgo_player_stats` joined on opponent team_id:
   - `opp.pass_yards_allowed_last_3`
   - `opp.rush_yards_allowed_last_3`
   - `opp.receptions_allowed_last_3`
   - `opp.qb_attempts_faced_last_3`

2. **Why this matters more for college than NFL:** the talent gap
   between teams is much larger in CFB. "QB X vs an SEC defense" vs
   "QB X vs a MAC defense" is a 30+%-yards swing. Opponent-strength
   features are the single biggest signal source when player history
   is thin.

### Tier D — Pure Market Features (no history required)

**Status:** Already in the row payload (`book_count`, devig fields).
Make them feature_ready independently.

**Concrete additions:**

1. **Remove `feature_ready = (prior_games ≥ MIN_GAMES_REQ)` as the
   single gate.** Replace with two flags:
   - `history_features_ready` (current behaviour, threshold gated)
   - `market_features_ready = True` always (when devig data exists)

2. **Tier-1 market-only features** any model can use:
   - `consensus_devig_prob` (already computed)
   - `pp_implied_prob` (already computed)
   - `edge_vs_consensus` (already computed)
   - `book_count` (count of distinct books quoting the anchor)
   - `line_position_in_book_range` (normalized 0-1)
   - `pp_alt_line_count_for_this_player_market` (depth signal)

3. **Why this works:** with these features alone, the model is
   essentially learning "when does the consensus book disagree with
   PP enough to be exploitable". That's a low-ceiling but reliable
   ~52% strategy and gives you a baseline NCAAF model in days, not
   weeks. Stack player + team + opponent features on top once they
   land.

### Tier E — Book-Count and Line/Price Features (cheap and always-on)

Already implicitly available. Just need to be SURFACED in the
feature row:

```python
features = {
    # ...
    "book_count":                    len(set(quotes.book_id)),
    "book_count_majors":             count_majors_in(quotes),
    "line_dispersion":               max(lines) - min(lines),
    "price_dispersion":              max(prices) - min(prices),
    "is_outlier_pp_line":            abs(pp_line - median_line) > threshold,
    "pp_vs_consensus_line_delta":    pp_line - median_line,
}
```

Single point in `build_historical_model_features.py`; ~30 LOC.

---

## Proposed Patch Order (after diagnostic results land)

Triage step-by-step, smallest-blast-radius first:

1. **Run the diagnostic.** Paste output back. → Decide which Tier
   to start with based on §3 (player-id audit) and §5 (core market
   readiness).

2. **If §3 shows >50% ID-mismatch on unresolved:** write
   `reconcile_ncaaf_player_ids.py` (canonical name-team reconciliation
   pass). Idempotent, NCAAF-only, writes back to
   `sgo_ncaaf_research_outcomes` with `pid_resolved=true` flag and
   re-runnable as a separate pipeline stage. ETA: ~150 LOC.

3. **Lower `MIN_GAMES_REQ`** for NCAAF only (Tier A, change #1).
   Behind a per-league dict — no impact on MLB/NBA/NFL. Re-run
   `build_historical_model_features --league NCAAF`. Expect
   `feature_ready > 0` immediately.

4. **Add `market_features_ready` flag** (Tier D). One field added
   to the feature schema; no impact on other leagues' downstream code.
   Re-run features. Now you have ~100% of NCAAF rows
   `market_features_ready=true` — viable for an immediate baseline
   model trained on pure market signal.

5. **Add team + opponent rolling features** (Tier B + C). Single
   commit, ~250 LOC in `build_historical_model_features.py`. Per
   `--league` gated so MLB/NBA/NFL are untouched.

6. **Train baseline NCAAF model** on (3) + (4) + (5) outputs. The
   v1 baseline should easily clear 51.5% on the core-9-market subset.

## Hard Constraints Honoured

| Constraint | How |
|---|---|
| Do not touch MLB/NBA/NFL | All proposed code changes are behind league-keyed dicts or `if league == "NCAAF":` branches. |
| Do not change production live routing | Zero modifications to `team_master_hub`, `team_live_sync_service`, or any `ferrari_*` route. SGO research pipeline only. |
| Read-only diagnostic | `diagnose_ncaaf_pipeline.py` performs zero writes. |
| Don't train or score NCAAF yet | No model files, no `score_historical_model` invocation. |

## Anti-patterns to avoid (have already seen these elsewhere)

- ❌ Lowering `MIN_GAMES_REQ` globally — would silently degrade MLB/NBA/NFL.
- ❌ "Just use `last_season_avg` as a fallback" without checking that
   the player actually played last season (transfer portal makes this
   common). Always require ≥1 prior game IN the same season.
- ❌ Imputing missing player stats from team averages. Polite, but
   it teaches the model that the team carries the player. Wrong sign
   on the gradient. Better to drop the row.
- ❌ Renaming `feature_ready` semantics globally. Add a NEW field
   (`market_features_ready`) instead of overloading the old one.
