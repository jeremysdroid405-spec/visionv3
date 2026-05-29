# Vision Teams — Phase 1 Architecture (Team Props)

**Status:** Design only — no code, no DB schema migrations, no UI work
until this document is approved.

**Scope:** Add Team Props as a fully isolated modeling pipeline that
re-uses ONLY the universal scoring + tier infrastructure at the very
end. Player Props are NOT to be touched.

**Author / Date:** PropVision platform — drafted 2026-06-02.

---

## 0. Guiding Principles

1. **Hard isolation through Layer-3.** Team data, team features, team
   projections, and team TP estimates live in their own collection
   namespace (`team_*`) and their own service modules
   (`services/team_*/`). The first place team props re-enter the
   shared codebase is the Universal Gate Engine — and even that
   re-entry point should be re-architected as a shared CONTRACT, not
   a shared call site (see §4).

2. **Sport-agnostic core, sport-specific specializations.** The
   contract for "what a team-prop projection looks like" must be
   identical across MLB / NFL / NBA. Each sport plugs in its own
   feature extractor and its own distribution choice, but they all
   emit the same shape of output document.

3. **SSOT for team identity.** Just like Player Master Hub, Team
   Master Hub is the single source of truth for `team_id`. Every
   downstream collection joins on `team_id` (BSON-safe, never on
   team-name text).

4. **Multi-book / multi-snapshot from day 1.** The player-prop
   multi-book bug (one row per prop collapsing 21 books into 1)
   cannot be repeated here. Team-prop ingest emits one row per
   `(game, team, market, line, side, book, snapshot_iso)`.

5. **Research replay parity.** Every team-prop production code path
   must be replayable historically with the same gate decisions —
   same SSOT contract that drove the player-prop 33%→97% coverage
   restoration.

---

## 1. Team Data Architecture

### 1.1 Collection inventory

```
team_master_hub           ← canonical identity (sport, league, names, ext IDs)
team_live_props           ← real-time per-book odds snapshots
team_historical_props     ← immutable archive of team_live_props (per snapshot_iso)
team_prop_outcomes        ← graded historical truth (one row per game × team × market)
team_matchups             ← per-game schedule + game context
team_injuries             ← roster availability that affects team performance
team_context              ← weather, travel, rest, park factors, line movement
team_features             ← engineered features (one row per game × team × market)
team_projections          ← model outputs (mean, variance, distribution params)
team_prop_scores          ← final scored rows (TP, edge, tier, gate decisions)
team_replay_outputs       ← SSOT historical replay analog of team_prop_scores
```

Eleven collections — three more than the inventory in the original
brief. I added:

- **`team_historical_props`** — separation between live (high churn,
  ttl-indexed) and historical (immutable, queryable forever) odds is
  a lesson learned from the player-prop side, where `sgo_props_raw`
  conflated both.
- **`team_features`** — explicit feature collection so the projection
  engine reads from a SETTLED snapshot, not from a live-joined view.
- **`team_projections`** — explicit projection collection between
  features and TP. Player-side did this implicitly inside the
  scoring stack; doing it explicitly here makes backfill, replay,
  and model A/B testing trivial.

### 1.2 Per-collection design

#### `team_master_hub`

| Field | Type | Source | Notes |
|---|---|---|---|
| `_id` | `team_id` (deterministic — `mlb_nyy`, `nfl_kc`, `nba_lal`) | manual seed + SGO id mapping | Never rotate. |
| `sport` | enum `mlb|nfl|nba` | seed | |
| `league_id` | str | seed | `MLB`, `NFL`, `NBA` |
| `external_ids` | dict | seed + provider feeds | `{sgo: "...", oddsapi: "...", espn: "...", statsapi: "..."}` |
| `display_names` | dict | seed | `{full, short, abbrev, market}` |
| `colors` | dict | seed | UI tiering |
| `division` / `conference` | str | seed | playoff context |
| `created_at` / `updated_at` | dt | system | |

- **Ownership:** platform team / manual seed.
- **Update frequency:** quarterly (when SGO or providers change IDs).
- **Source of truth:** itself. Everything joins on `_id`.
- **Index:** `_id` (default), `(sport, league_id)`, `external_ids.sgo`.

#### `team_live_props`

| Field | Type | Notes |
|---|---|---|
| `event_id` | str | shared with player-prop pipeline |
| `team_id` | str | FK → `team_master_hub._id` |
| `market` | str | `team_total_runs`, `team_total_hits`, `team_total_points`, `first_half_total`, `first_quarter_total`, `team_total_passing_yards`, … |
| `line` | float | |
| `side` | `OVER` / `UNDER` | |
| `book` | str | `draftkings`, `fanduel`, … |
| `odds` | int (American) | |
| `is_alternate` | bool | |
| `snapshot_iso` | iso str | clock at ingest |
| `commence_time` | iso str | |
| `game_date` | str | `YYYY-MM-DD` |
| `home_away` | enum | derived from `team_matchups` |
| `ingested_at` | dt | |

- **Ownership:** odds-ingest worker (new module
  `workers/team_odds_ingest.py`).
- **Update frequency:** every snapshot (5–15 min depending on sport
  near-game-time policy already used by player props).
- **TTL:** rolling 48 h, then promoted to `team_historical_props`.
- **Source of truth:** SGO / OddsAPI feeds. The `book` field uses the
  same `BLOCKED_BOOKS` / `REFERENCE_ONLY_BOOKS` policy already
  established in
  `scripts/sgo/reshape_sgo_to_replay_odds.BLOCKED_BOOKS`.
- **Index:** compound unique on
  `(event_id, team_id, market, line, side, book, snapshot_iso)`,
  plus `(game_date, sport)`.

#### `team_historical_props`

Identical schema to `team_live_props` but written one snapshot at a
time by a promotion job. Equivalent of `sgo_replay_alt_odds_raw` on
the player side.

- **Source of truth:** itself, immutable.
- **Index:** same compound unique plus `(team_id, market, game_date)`
  for replay range scans.

#### `team_prop_outcomes`

The team-prop equivalent of `sgo_pp_research_outcomes`. ONE
authoritative row per `(event_id, team_id, market, line, side)`.

| Field | Type | Notes |
|---|---|---|
| `event_id` | str | |
| `team_id` | str | |
| `market` | str | |
| `line` | float | |
| `side` | `OVER`/`UNDER` | |
| `actual_value` | float | Real team performance |
| `outcome_numeric` | 0 / 0.5 / 1 | Loss / Push / Win |
| `outcome_resolved` | bool | |
| `game_date` | str | |
| `resolved_at` | dt | |

- **Source of truth:** itself. Grading worker writes once after
  game settles.
- **Index:** compound unique on
  `(event_id, team_id, market, line, side)`, plus
  `(game_date, sport)`.

#### `team_matchups`

| Field | Type | Notes |
|---|---|---|
| `event_id` | str | PK |
| `sport` | str | |
| `home_team_id` / `away_team_id` | str | |
| `game_date` / `commence_time` | str / dt | |
| `venue_id` | str | for park factors / weather |
| `season` / `week` / `playoffs` | mixed | |
| `created_at` / `updated_at` | dt | |

- **Source of truth:** SGO schedule feed + manual override
  (consistent with current MLB schedule ingest).
- **Update frequency:** daily + game-time recheck.

#### `team_injuries`

| Field | Type | Notes |
|---|---|---|
| `event_id` | str | |
| `team_id` | str | |
| `player_id` | str | FK to existing `player_master_hub` |
| `status` | str | `out`, `doubtful`, `questionable`, `probable` |
| `position_group` | str | for impact weighting |
| `impact_score` | float | engineered impact on team output |
| `reported_at` | dt | |

- **Source of truth:** Provider injury feed.
- **Update frequency:** every 30 min on game days.
- **Index:** `(event_id, team_id)`.

#### `team_context`

Per-game environmental + market-state snapshot.

| Field | Type | Notes |
|---|---|---|
| `event_id` | str | PK with `team_id` |
| `team_id` | str | |
| `weather` | dict | `{temp_f, wind_mph, wind_dir, precip, dome}` |
| `rest_days_team` / `rest_days_opp` | int | |
| `travel_miles` | int | |
| `vegas_team_total` | float | implied from spread + total |
| `vegas_spread` | float | |
| `vegas_total` | float | |
| `line_movement_open_to_close` | float | for sharp-money signal |
| `park_factor` / `pace_factor` | float | sport-specific |
| `opponent_strength` | float | DRtg / opp-allowed |
| `as_of_snapshot_iso` | iso str | match to `team_live_props` |

- **Source of truth:** itself, written by a context-build worker
  that joins weather + odds + schedule.
- **Index:** `(event_id, team_id)`, `(game_date, sport)`.

#### `team_features`

Engineered feature vector. Equivalent of
`sgo_pp_research_model_features` on the player side.

| Field | Type | Notes |
|---|---|---|
| `event_id` / `team_id` / `market` | str | compound key |
| `feature_vector` | dict | model-ready values |
| `feature_set_version` | str | semver — never re-use |
| `built_at` | dt | |

- **Source of truth:** feature-build worker
  (`workers/team_features.py`).
- **Update frequency:** once per (event × team × market) — backfillable.

#### `team_projections`

Model output BEFORE TP calculation. Lets us A/B different models
without touching the TP engine.

| Field | Type | Notes |
|---|---|---|
| `event_id` / `team_id` / `market` | str | compound key |
| `distribution` | str | `normal`, `poisson`, `nbinom`, `mixture` |
| `mu` | float | mean projection |
| `sigma` | float | std-dev (when applicable) |
| `dispersion_k` | float | for NegBinom |
| `model_version` | str | semver |
| `confidence_metric` | float | cross-val score on the day's slate |
| `built_at` | dt | |

- **Source of truth:** projection-build worker
  (`workers/team_projections.py`).

#### `team_prop_scores`

Final scored row — equivalent of
`sgo_propvision_full_pipeline_replay` for player side, but live.

Compound key includes `book` (multi-book lesson learned).

```
(event_id, team_id, market, line, side, book, snapshot_iso,
 model_version, gate_config_version)
```

Key fields:
```
team_id, market, line, side, book, odds,
implied_probability, fair_probability, edge,
tp, tp_source,                 # canonical TP for math
cv, vision_score,
gate decisions: safe_haven_pass, front_lines_pass, war_zone_pass,
selected_tier,
playable_on_*,                 # per-book playability flags
model_version, gate_config_version,
pipeline_version,
scored_at
```

- **Source of truth:** itself, written by the team scoring runner.
- **Index:** compound unique on the full key above.

#### `team_replay_outputs`

The SSOT replay mirror of `team_prop_scores`. Same shape, written by
the historical replay analog. Equivalent of
`sgo_propvision_full_pipeline_replay`.

### 1.3 Collection ownership matrix

| Collection | Owner | Update freq | Source of truth |
|---|---|---|---|
| `team_master_hub` | platform | quarterly | itself |
| `team_live_props` | odds ingest worker | per snapshot | SGO/OddsAPI |
| `team_historical_props` | promotion job | hourly | itself |
| `team_prop_outcomes` | grading worker | post-game | itself |
| `team_matchups` | schedule worker | daily | SGO schedule |
| `team_injuries` | injury worker | 30 min on game days | provider feed |
| `team_context` | context-build worker | per snapshot | itself |
| `team_features` | feature build | on-demand + backfill | itself |
| `team_projections` | projection build | on-demand + backfill | itself |
| `team_prop_scores` | scoring runner | per snapshot | itself |
| `team_replay_outputs` | replay runner | historical pipeline run | itself |

---

## 2. Team Projection Engine

### 2.1 Architectural separation

The Team Projection Engine is a **separate Python service module
namespace**:

```
services/team_projections/
├── __init__.py
├── base.py                  # TeamProjectionAdapter ABC
├── mlb/
│   ├── runs.py
│   ├── hits.py
│   ├── strikeouts.py
│   └── total_bases.py
├── nfl/
│   ├── points.py
│   ├── first_half_total.py
│   ├── passing_yards.py
│   └── rushing_yards.py
└── nba/
    ├── points.py
    ├── first_half_total.py
    └── first_quarter_total.py
```

The `TeamProjectionAdapter` ABC defines the contract:

```
project(event_id, team_id, market, features, context) →
    TeamProjection(distribution, mu, sigma|None, dispersion_k|None,
                   model_version, confidence_metric)
```

The runner that orchestrates daily projections doesn't import any
sport-specific module directly — it dispatches by `(sport, market)`
through a registry, exactly like `production_replay_runner.py`
already does for player props via `SportReplayAdapter`.

### 2.2 Feature inventory

#### Universal features (all sports, all markets)

```
team_id_home, team_id_away         (matchup symmetry)
home_away                          (binary)
rest_days_team, rest_days_opp      (int)
travel_miles                       (int, sport-specific weights)
vegas_team_total                   (float)  ← strongest single signal
vegas_spread                       (float)
vegas_total                        (float)
opponent_strength_rating           (float, sport-normalized)
team_offensive_rating              (float, sport-normalized)
team_defensive_rating              (float, sport-normalized)
last_7_team_for_market             (rolling 7-game avg)
last_14_team_for_market            (rolling 14-game avg)
season_team_for_market             (season-to-date)
last_7_opp_allowed_for_market      (rolling 7)
season_opp_allowed_for_market      (season-to-date)
line_movement_open_to_close        (float)
sharp_money_indicator              (proxy from line move + book signals)
```

#### MLB-specific

```
park_factor_runs / hits / hr / so   (per ballpark, per year)
weather: temp_f, wind_mph, wind_in_out_score, humidity, dome
opposing_starter_id + their L5 ERA + L5 WHIP + L5 K/9 + L5 BB/9
opposing_starter_handedness
team_handedness_split (R/L matchup-adjusted offense)
bullpen_innings_used_last_3_days
team_lineup_position_strength      (lineup card has been published)
```

#### NFL-specific

```
qb_id + qb_passing_l4
qb_status (injury)
opp_pass_rush_grade
opp_run_defense_grade
team_oline_pff_grade
neutral_pace_seconds_per_play
pass_run_split_situational
weather_wind_mph                    (passing-yards killer)
weather_precip_inches
roof_type                           (dome / open / retractable)
turf_or_grass
divisional_game_flag
short_week_flag                     (Thursday game)
travel_timezones                    (int)
```

#### NBA-specific

```
team_pace                           (possessions/48)
opp_pace
team_ortg / drtg
opp_ortg / drtg
team_3pt_rate / 3pt_pct_l10
opp_def_3pt_pct_allowed
back_to_back_flag
b2b_road_flag                       (penalty)
key_player_out_flags (multi-player: top-3 minutes-leader)
projected_minutes_top5
```

### 2.3 Model selection

| Sport | Market | Primary distribution | Why |
|---|---|---|---|
| MLB | Team Total Runs | **Negative Binomial** | Runs are clustered (big innings). Variance > mean by design. NegBinom matches empirical data far better than Poisson. |
| MLB | Team Total Hits | **Negative Binomial** | Same clustering argument (rallies). |
| MLB | Team Strikeouts | **Poisson** | Each PA is roughly independent for K outcome; underdispersed vs hits. Validate empirically. |
| MLB | Team Total Bases | **Negative Binomial** | Tail driven by extra-base hits. |
| NFL | Team Total Points | **Normal** (μ, σ) with **Skellam** for spreads | Football scoring is multi-modal (FG vs TD) but team-total is roughly normal at NFL scale. |
| NFL | First Half Total | **Normal**, σ ≈ σ_full × 0.7 | Halftime variance roughly 70% of full game by empirical regression. |
| NFL | Team Passing Yards | **Normal** | High volume, CLT applies cleanly. |
| NFL | Team Rushing Yards | **Normal with floor / spike** | Most teams cluster; occasional 200-yd outlier game. Consider Gamma if right-tail handling matters. Start Normal, escalate. |
| NBA | Team Total Points | **Normal** | High volume (~100 pts/game), tight gaussian by CLT. σ ≈ 12. |
| NBA | First Half Total | **Normal**, σ ≈ σ_full × 0.71 | Same halftime scaling as NFL. |
| NBA | First Quarter Total | **Normal**, σ ≈ σ_full × 0.50 | Higher relative variance — single quarter, lineup variability. |

**Validation protocol:** for each (sport × market) pair, before
production:
1. Fit chosen distribution on prior-season truth data.
2. Run a Kolmogorov-Smirnov goodness-of-fit test.
3. If KS p-value < 0.05, escalate to mixture model.
4. Document chosen distribution + KS result in
   `team_projections.distribution_audit` field.

---

## 3. Team True Probability Engine

### 3.1 Module layout

```
services/team_tp/
├── __init__.py
├── base.py                          # TeamTPAdapter ABC
├── pricing.py                       # American-odds → implied_probability
├── devig.py                         # multi-book de-vig (shared logic w/ player side)
├── distributions/
│   ├── normal.py
│   ├── poisson.py
│   ├── nbinom.py
│   └── mixture.py
└── anchors.py                       # market-aware anchoring
```

### 3.2 Computation flow

For each `(event_id, team_id, market, line, side, book)` row:

1. **Read projection** → `(distribution, mu, sigma|None, k|None)`
2. **Compute model probability**
   - Normal: `P(X > line) = 1 - Φ((line + 0.5 - μ) / σ)` for OVER
     (continuity correction)
   - Poisson: `P(X > line) = 1 - F_pois(floor(line); μ)` for OVER
   - NegBinom: `P(X > line) = 1 - F_nb(floor(line); k, p)` where
     `p = k / (k + μ)`
3. **Compute book-implied probability** from `odds`
4. **Compute fair probability via multi-book de-vig**
   (same algorithm as player-prop side, see
   `services/scoring/devig.py`).
5. **Compute `tp` (true probability)** = blend of
   `model_probability` and `fair_probability` weighted by
   `confidence_metric` and `book_count`.
   - Cold start (no model history): `tp = fair_probability`.
   - Warm (validated model): `tp = α·model + (1-α)·fair` with
     α tuned per sport-market via backtest.
6. **Compute edge** = `tp - implied_probability`.
7. **Persist** to `team_prop_scores.tp`, `.fair_probability`,
   `.model_probability`, `.edge`, etc.

### 3.3 Anchoring

Market-aware anchoring is critical because team props have FEWER
books than player props for non-NFL sports.

- **NFL team totals:** typically 15-20 books — robust multi-book
  de-vig works fine.
- **NBA team totals:** 10-15 books — robust de-vig works.
- **MLB team runs:** 8-15 books — usable.
- **MLB team strikeouts / total bases:** 4-8 books — **anchor
  matters**. Anchor to sharp books (Pinnacle, Circa) with higher
  weight.

The anchoring config lives in `team_tp/anchors.py`:

```
{
  "mlb": {
    "default": {"pinnacle": 3.0, "circa": 2.5, "bookmakereu": 2.0, "default": 1.0},
    "team_strikeouts": {"pinnacle": 4.0, "circa": 3.0, "default": 1.0},
  },
  "nfl": { "default": {"pinnacle": 2.0, "circa": 2.0, "default": 1.0} },
  "nba": { "default": {"pinnacle": 2.0, "circa": 2.0, "default": 1.0} },
}
```

### 3.4 De-vig recommendation per market

| Market | De-vig method | Reason |
|---|---|---|
| All team totals (single line) | **Multiplicative** | Standard two-way devig |
| Alternate team totals (multi-line) | **Power devig** | Better tail calibration |
| Team strikeouts / bases (rare books) | **Power devig + sharp anchor** | Compensate for thin book population |

---

## 4. Team Scoring Layer — Reuse vs Dedicated

### 4.1 The two options

**Option A — Reuse Universal Gate Engine**
Team prop rows enter the same
`services/scoring/gates/{safe_haven,front_lines,war_zone}.py`
pipeline that player props use today.

**Option B — Dedicated Team Gate Engine**
A parallel `services/team_scoring/gates/...` namespace with team-
specific thresholds.

### 4.2 Trade-off analysis

| Dimension | A (reuse) | B (dedicated) |
|---|---|---|
| **Speed of delivery** | Faster — gates already exist | Slower — duplicate work upfront |
| **Risk of regression to player props** | **Medium** — shared code means a team-prop bug can leak | None — physical isolation |
| **Calibration accuracy per market** | **Worse** — gates tuned to player props | Best — gates tuned per team market |
| **Operational complexity** | Lower | Higher — two threshold tables, two replay paths |
| **Auditability** | Harder — gate decisions span data shapes | Easier — clear lineage per data shape |
| **Cost of future "add a market"** | Low if it fits existing gate structure | Low — pattern is well-defined |
| **Mathematical correctness** | gates use HR-L20, edge, CV, vision_score — same concepts apply to team props, **but the empirical thresholds will differ.** | Each set tuned independently. |

### 4.3 Recommendation: **Hybrid — Shared CONTRACT, Separate IMPLEMENTATION**

This matches the user's stated preference (`separate modeling,
shared scoring architecture`) and avoids both extremes:

1. Define a `GateAdapter` ABC that takes a normalized "prop row"
   (whose schema is identical for player and team) and returns
   `(safe_haven_pass, front_lines_pass, war_zone_pass,
   failed_gates, selected_tier)`.
2. **Player props**: existing
   `services/scoring/gates/universal_gates.py` implements
   `GateAdapter` for player-shape thresholds.
3. **Team props**: new
   `services/team_scoring/gates/team_universal_gates.py` implements
   the SAME `GateAdapter` interface with team-specific thresholds,
   tuned per `(sport, market)`.
4. The Universal Tier Routing layer (the odds-bucket router
   `safe_haven ≤ -300 | front_lines -299..+149 | war_zone ≥ +150`)
   is **shared verbatim**. Tier routing is purely a function of
   `odds`, which has the same meaning for both shapes.
5. The Universal Score Formula (HR, ROI, edge, vision_score weights)
   is **shared verbatim**. These are universal metrics.

This way:
- A team-prop bug cannot regress a player-prop gate.
- A change to the score formula automatically applies to both.
- The optimizer treats team props as just another stream of rows
  with the same shape.

---

## 5. TeamVision Product Design

### 5.1 Recommended UX

**Tab navigation: dedicated "Team Props" tab.**

```
[ Player Props ]   [ Team Props ]   [ Parlays (future) ]
```

But internally the data shape is the **same prop card** with a badge:

```
PLAYER • Aaron Judge • HR  O 0.5  +280   SAFE HAVEN
TEAM   • Yankees     • Total Runs  O 4.5  -140   FRONT LINES
```

So the UI uses the **same card component** with different metadata
fields. This keeps the rendering engine simple while letting the
operator filter cleanly by tab.

### 5.2 Filters added on Team Props tab

```
[ Sport ]            MLB / NFL / NBA
[ Market ]           Team Total / 1H / 1Q / Passing / Rushing / Hits / Runs / K / TB
[ Home / Away ]      Both / Home only / Away only
[ Side ]             Over / Under / Both
[ Tier ]             SH / FL / WZ / all
[ Min Edge ]         numeric
[ Min TP ]           numeric
[ Books ]            checkbox list (sticky)
[ Time window ]      next 1h / next 4h / tonight / tomorrow
```

### 5.3 Why NOT a unified board

A unified board with badges would:

- Force every filter to be dual-shaped (player attrs ∪ team attrs).
- Confuse the operator who wants to focus on one shape at a time.
- Bloat the prop card with conditional rendering.
- Make the API contract messier on the WebSocket / SSE channel.

The clean separation by tab is worth the extra navigation click.

### 5.4 Cross-prop links (future)

Eventually we can add a "related player props" expand-row on a team
prop card (and vice versa). This is a phase-2 enhancement — design
the data layer to support it (both player and team rows carry
`event_id`, so a join is trivial) but do NOT build the UI in phase 1.

---

## 6. Implementation Roadmap

### Phase 1.A — Foundation (no UI, no scoring)
1. Create the 11 collections with empty indexes.
2. Seed `team_master_hub` for MLB / NFL / NBA (~100 teams total).
3. Wire SGO team-prop ingest into `team_live_props` + promotion job
   to `team_historical_props`.
4. Backfill `team_matchups` for 2024 + 2025 from SGO schedule feed.
5. Verify multi-book invariant with the same regression-test
   contract used for player-prop reshape
   (`test_reshape_multi_book.py`).

### Phase 1.B — Outcomes
1. Build grading worker → `team_prop_outcomes`.
2. Backfill 2024 + 2025 season for MLB.
3. Audit grading coverage with the same diagnostic shape used by
   `/research/replay-outcome-coverage`.

### Phase 1.C — Projection
1. Build `team_features` engineered output for MLB only first.
2. Implement MLB team_runs projection with NegBinom.
3. Backtest on 2024 MLB season. Acceptance: KS p > 0.05, MAE on
   `mu` < 0.6 runs.
4. Roll out remaining MLB markets, then NBA (lowest-variance
   market), then NFL.

### Phase 1.D — TP & Scoring
1. Implement `team_tp` engine with all 4 distributions.
2. Stand up scoring runner.
3. Run historical replay pipeline for MLB 2025.
4. Audit `tp_source` integrity (same pattern that caught the
   player-prop TP-scale percent / probability bug).

### Phase 1.E — UI
1. Add Team Props tab.
2. Reuse prop card.
3. Wire optimizer to team_replay_outputs.
4. Ship to admin-testing first; promote to user UI after one full
   week of stable scoring.

### Phase 1.F — Multi-sport rollout
1. NBA after MLB is stable for 4 weeks.
2. NFL last (highest stakes, smallest sample size per team per
   week → most prior-driven).

---

## 7. Risks & Mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sample size per (sport × market) too small for NegBinom fit | Medium | Pool across teams within a season; Bayesian shrinkage to league mean. |
| Team-name drift between SGO / OddsAPI / ESPN | High | Hard-mapped `external_ids` table in `team_master_hub`; nightly drift audit. |
| Re-using player-prop optimizer code creates cross-contamination | High | Hybrid contract layer (§4.3); no shared mutable state. |
| Weather data feed outage degrades MLB / NFL projections silently | Medium | Mark `team_context.weather = None` and propagate `feature_completeness < 1.0` to scoring; gate WZ rows on full feature completeness. |
| Sharp anchor books drop a market mid-season | Medium | Anchor weights are config not code; ops can hot-edit. |
| Team injury feed lag corrupts feature snapshot | Medium | Capture `as_of_snapshot_iso` on every feature; refuse to score a row whose feature snapshot is > X minutes stale. |
| Multi-book mirror collapse (lesson from player-prop side) | Already mitigated | Mandatory regression test pins `book` in $group._id + upsert filter (analog of `test_mirror_multi_book.py`). |

---

## 8. Open Questions for the User

These need answers before Phase 1.A starts:

1. **NFL kick-off scope:** Just regular season, or include preseason?
   Preseason is wildly noisy and hurts model calibration.
2. **NBA scope:** Include in-season tournament games as a separate
   modeling regime? Reduced rotation/intensity vs regular season.
3. **MLB roster handling at trade deadline:** Should
   `team_master_hub.colors` carry "since-date" so historical replays
   render with the right era?
4. **Live scoring cadence:** Same 5-15 min snapshots as player props,
   or a separate cadence (especially NFL — once-a-week games shift
   the freshness/cost tradeoff)?
5. **Optimizer access:** Should team props get their own optimizer
   run id namespace (`opt_team_xxx`) or share the same id space
   with player runs?
6. **Threshold authorship:** Who tunes the team-side gate
   thresholds for SH/FL/WZ — same person who owns the player-side
   thresholds, or a different SME?

---

## 9. What this document explicitly is NOT

- A code spec. No function signatures, no SQL/Mongo migration
  scripts, no UI components.
- A model spec. The chosen distributions are starting points,
  validated against truth data in Phase 1.C.
- A schedule commitment. Phase ordering is a recommendation, not
  a Gantt chart.
- An infrastructure plan. Compute and storage sizing happen after
  the projection engine sees one month of real data.

---

## 10. Approval Gates

Before any code is written:

1. Sign-off on the 11-collection inventory in §1.
2. Sign-off on Hybrid Scoring (Option C in §4.3).
3. Sign-off on the distribution table in §2.3.
4. Sign-off on the dedicated-tab UI in §5.

Each gate is a one-line "approved by `<user>` on `<date>`" entry in
this document. Do not start coding until all four gates are signed.
