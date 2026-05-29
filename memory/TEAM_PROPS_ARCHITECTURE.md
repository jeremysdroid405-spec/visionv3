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

### 5.1 Direction (locked by user 2026-06-02)

> Keep the UI format consistent with the existing player prop
> experience. For Team Props, do NOT create a completely different
> layout or separate visual language. Use the same card structure,
> same tier styling, same badge system, same score display, same
> confidence/edge display, and same interaction pattern. The only
> difference is that the card entity is a TEAM, not a player.

This is the visual contract for Phase 1.E.

### 5.2 Card structure parity

Every field that exists on the Player Card has a 1:1 Team Card
analog:

| Player Card field | Team Card field | Behavior |
|---|---|---|
| Player headshot | Team logo | Same component, same dimensions, fallback initials disc when asset missing |
| Player name | Team full name | Same typography, same truncation rule |
| Team vs Opponent line | Team vs Opponent line | Identical — reused verbatim |
| Player stat market | Team prop market | Same metadata pill |
| Line + side | Line + side | Identical layout |
| Best-book chip (DK / FD / MGM …) | Best-book chip | Identical |
| Odds value | Odds value | Identical |
| Tier badge (SH / FL / WZ) | Tier badge | **Reused verbatim** — same colors, same border treatment, same animation |
| Score (0-100) | Score (0-100) | Same placement, same weight ramp |
| Edge % | Edge % | Same |
| TP probability | TP probability | Same |
| Confidence / vision_score | Confidence / vision_score | Same |
| Badge strip | Badge strip | Same component; badge VOCABULARY is sport+entity specific (see §5.5) |
| Expanded "Intel" drawer | Expanded "Intel" drawer | Same component; intel CONTENT changes |
| Multi-book chips row | Multi-book chips row | Same component, same `playable_on_*` flags |

### 5.3 Naming convention

In code, in logs, in admin UI, in API responses:

```
Team Prop Card  (canonical)
Team Card       (acceptable short form)
```

NEVER reuse the string `"player card"` for a team prop, even when
the underlying React component is shared.

### 5.4 Component architecture (no code yet — just the shape)

```
PropCard                       ← shared base component
├── PropCardHeader
│   ├── EntityAvatar           ← takes {kind: "player"|"team", asset_url, fallback}
│   ├── EntityName             ← takes {primary, secondary (team/opp)}
│   └── MarketChip             ← takes {market_label, line, side}
├── PropCardOdds
│   ├── OddsValue
│   ├── BestBookChip
│   └── PlayabilityRow         ← per-book check chips
├── PropCardScore
│   ├── TierBadge
│   ├── ScoreRing
│   └── EdgeMetric
├── PropCardBadgeStrip         ← takes {badges: BadgeDescriptor[]}
└── PropCardIntel (expanded)
    └── IntelSection           ← takes {sections: IntelSection[]} (entity-specific content)
```

Key abstractions:
- `EntityAvatar` is the only component that branches on
  `kind: "player" | "team"`. Every other component is shape-agnostic.
- `BadgeDescriptor` is a tagged-union of player + team badge
  vocabularies; the rendering is identical.
- `IntelSection[]` is supplied by the data layer (server-side),
  not branched in the renderer.

This is the "cleanly abstracted reuse" called out in Acceptance
Criterion #10.

### 5.5 Team Badge taxonomy (Phase 1)

These are the canonical Phase 1 badges. Each badge is rendered with
the same `BadgePill` component used today for player badges — same
icon position, same color treatment per polarity (positive / neutral
/ caution).

#### MLB Team Badges

| Badge | Polarity | Triggered by |
|---|---|---|
| Bullpen Edge | positive | `team_features.bullpen_innings_used_last_3_days` < threshold AND opp bullpen high-usage |
| Weak Starter Matchup | positive | opp `qb_id` equivalent = starting pitcher whose L5 ERA > league-95th-percentile |
| Wind Boost | positive | `team_context.weather.wind_in_out_score > +5 mph blowing out` |
| Park Boost | positive | `team_context.park_factor_runs > 1.10` |
| Hot Offense | positive | `team_features.last_7_team_for_market` > league-80th-percentile |
| Cold Opponent Pitching | positive | opp L7 starter+bullpen ERA in bottom quartile |
| Travel Disadvantage | caution | `team_context.travel_miles > 1500` AND `rest_days_team <= 1` |
| Rest Advantage | positive | `rest_days_team - rest_days_opp >= 2` |
| Line Steam | positive | `line_movement_open_to_close` favorable AND sharp signal |

#### NBA Team Badges

| Badge | Polarity | Triggered by |
|---|---|---|
| Pace Boost | positive | `team_features.pace + opp_pace` in top decile |
| Defensive Mismatch | positive | opp DRtg in bottom quartile vs team's primary scoring axis |
| Rest Advantage | positive | `rest_days_team - rest_days_opp >= 2` |
| Back-to-Back Spot | caution | `back_to_back_flag` true on opponent only → positive; on team → negative |
| Injury Impact | caution | starter-level player out flag on either side |
| High Implied Total | positive | `vegas_team_total` in top quintile |
| Line Steam | positive | sharp signal |
| Blowout Risk | caution | spread > 12 — reduces 4th-quarter scoring (relevant for full game O/U) |

#### NFL Team Badges

| Badge | Polarity | Triggered by |
|---|---|---|
| Weather Boost | positive | dome OR `wind_mph < 8` AND `temp_f > 40` AND no precip |
| Weather Risk | caution | `wind_mph >= 15` OR `precip > 0.1` OR `temp_f < 25` |
| Run Funnel | positive | opp pass defense top-10 + run defense bottom-10 → drives rushing yards over |
| Pass Funnel | positive | opp run defense top-10 + pass defense bottom-10 → passing yards over |
| Defensive Mismatch | positive | opp DVOA bottom quartile on the relevant unit |
| Rest Advantage | positive | bye week OR > 7 days since last game while opp on short week |
| Travel Spot | caution | east coast team in west coast venue (1 PM ET kickoff scheduling penalty) |
| Line Steam | positive | sharp signal |
| High Implied Total | positive | `vegas_team_total` > 27 |

### 5.6 Filters added on Team Props tab

Same control library as Player Props tab (Shadcn `Select`, `Input`,
`Switch`). Filter set:

```
Sport         MLB / NFL / NBA
Market        Team Total / 1H / 1Q / Passing / Rushing / Hits / Runs / K / TB
Home / Away   Both / Home only / Away only
Side          Over / Under / Both
Tier          SH / FL / WZ / all
Min Edge      numeric
Min TP        numeric
Books         checkbox list (sticky)
Time window   next 1h / next 4h / tonight / tomorrow
Badge filter  multi-select of the badge vocabulary above
```

### 5.7 Visual rules NOT changing

- Tier colors — verbatim same hex values as player cards.
- Score ring animation — same component.
- Hover / focus / press states — same.
- Mobile responsive breakpoints — same.
- Dark / light theme handling — same.

### 5.8 Acceptance Criteria (locked)

1. Team Props have their own tab in the user-facing UI.
2. Team prop cards use the same card format as player props.
3. Team logo replaces player headshot.
4. Team name replaces player name.
5. Team market replaces player stat.
6. Team-specific badges are supported (see §5.5).
7. Expanded Intel section works the same way (same drawer, same
   open/close animation, same overflow behavior).
8. Player prop UI is not modified or broken.
9. No backend player-prop code is touched.
10. UI component reuse is allowed only if cleanly abstracted (see
    §5.4) and does not create player/team coupling.

### 5.9 Why NOT a unified board (re-affirmed)

A unified board would force every filter to be dual-shaped (player
attrs ∪ team attrs), confuse the operator, and bloat the prop card
with conditional rendering. The clean separation by tab is worth
the extra navigation click — and §5.2 parity makes the tab feel
familiar after the first second.

### 5.10 Cross-prop links (future, phase 2+)

Eventually we can add a "related player props" expand-row on a team
prop card and vice versa (both shapes carry `event_id`, so a join
is trivial). Do NOT build this in Phase 1.

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

Status as of 2026-06-02:

1. ✅ **APPROVED WITH CONDITION** — 11-collection inventory (§1).
   The three "extra" collections beyond the original brief
   (`team_historical_props`, `team_features`, `team_projections`)
   are LOCKED IN because Team Props will train a true predictive
   model, and clean separation of historical training data,
   engineered features, and model projections is required. Do NOT
   collapse them into one implicit collection (lesson from the
   player side).
2. ✅ **APPROVED** — Hybrid Scoring (Option C in §4.3). Shared
   CONTRACT, separate IMPLEMENTATION. No hard-wiring team props
   into the player gate engine.
3. ✅ **APPROVED FOR PHASE 1, MODEL-FIRST** — Distribution table
   (§2.3) is the baseline probability layer, NOT the entire model.
   The predictive model produces `μ + σ` (mean + uncertainty); the
   distribution layer converts that into `P(OVER) / P(UNDER)`. See
   §11.
4. ✅ **LOCKED 2026-06-02** — Team Card mirrors Player Card visual
   contract (§5.1–§5.8) with team-specific badge vocabulary.
5. ⛔ **NEW GATE — PENDING** — Predictive-model training plan (§11
   below). Phase 1 coding cannot begin until this is signed off.

---

## 11. Predictive-Model Training Plan (Phase 1.A deliverable)

Team Props use a real predictive model trained on historical
team-level data, with the same discipline as the player model. This
section is the training-plan blueprint required before any coding.

### 11.1 The full data flow (locked)

```
Historical Team Data        (truth + lines + features)
    ↓
Feature Engineering         (pre-game only, leak-proof)
    ↓
Team Prediction Model       (sport × market, gradient-boosted)
    ↓
Team Projection             (μ, σ or distribution params)
    ↓
Distribution Layer          (§2.3 — NegBinom/Poisson/Normal)
    ↓
Multi-Book De-vig           (anchor-weighted fair_probability)
    ↓
True Probability (TP)       (blend of model + market)
    ↓
Team GateAdapter            (universal contract, team thresholds)
    ↓
Tier + Score                (SH / FL / WZ + universal score formula)
```

This pipeline is reversible and replayable — every artifact has a
versioned write path to a collection in §1.1 so we can rebuild any
prior production decision from cold storage.

### 11.2 Historical data sources needed

| Source | What we need from it | Window | Owner |
|---|---|---|---|
| SGO historical odds API | Per-game team-prop opening + closing lines + per-book quotes | 3 prior seasons per sport | new `workers/team_odds_backfill.py` |
| MLB Stats API / Statcast | Game-level team box scores: R, H, K, TB; pitch-level for derived features | 2019 → present | reuse existing player-side ingest |
| nflfastR (or equivalent) | NFL team game logs + play-by-play (for situational features) | 2018 → present | new |
| NBA Stats API | NBA team box scores + advanced stats (pace, ORtg, DRtg) | 2018 → present | new |
| Weather provider | Historical weather for outdoor venues (MLB, NFL) at gametime | back to 2018 | reuse existing |
| Injury feed / news archive | Historical injury reports keyed to `as_of_snapshot_iso ≤ commence_time` | 2 prior seasons | new vendor TBD |
| Schedule archive | Rest days, travel, b2b, short-week, divisional flags | back to 2018 | derived from box scores |

**Data quality bar:** before any model trains, every source above
must pass a coverage audit (≥ 95% of expected game-team rows
present) with the same diagnostic shape used for the player-side
`/research/replay-outcome-coverage` endpoint.

### 11.3 Training labels per market

One label per `(game, team, market)`. Stored in `team_prop_outcomes`.

| Sport | Market | Label = `actual_value` |
|---|---|---|
| MLB | team_total_runs | runs scored by team |
| MLB | team_total_hits | hits by team |
| MLB | team_strikeouts | strikeouts by team's batters |
| MLB | team_total_bases | total bases by team |
| NFL | team_total_points | points scored by team |
| NFL | first_half_total | team points in H1 |
| NFL | team_passing_yards | net team passing yards |
| NFL | team_rushing_yards | team rushing yards |
| NBA | team_total_points | team points |
| NBA | first_half_total | team H1 points |
| NBA | first_quarter_total | team Q1 points |

Each row also gets `outcome_numeric ∈ {0, 0.5, 1}` relative to any
(line, side) pair the row is graded against — identical contract to
player-side `sgo_pp_research_outcomes`.

### 11.4 Feature sets by sport

Features come from `team_features` (engineered, versioned). The
universal features from §2.2 apply across all sports + markets;
sport/market-specific features layer on top.

Each sport-market combo gets its own feature manifest, e.g.:

```
team_features.feature_set_version = "mlb_runs_v1"
team_features.feature_vector = {
  # universal
  vegas_team_total, vegas_total, opponent_strength_rating,
  team_offensive_rating, team_defensive_rating,
  last_7_team_for_market, last_14_team_for_market,
  season_team_for_market, last_7_opp_allowed_for_market,
  line_movement_open_to_close, sharp_money_indicator,
  rest_days_team, rest_days_opp, travel_miles, home_away,
  # mlb-specific
  park_factor_runs, weather_wind_in_out_score, weather_temp_f,
  weather_humidity, dome_flag,
  opposing_starter_l5_era, opposing_starter_l5_whip,
  opposing_starter_l5_k_per_9, opposing_starter_l5_bb_per_9,
  opposing_starter_handedness, team_handedness_split,
  bullpen_innings_used_last_3_days,
  lineup_position_strength,
}
```

Versioning rule: any change to the manifest bumps
`feature_set_version` and **forces a full retrain**. No silent
feature drift.

### 11.5 Leakage rules (HARD)

These are non-negotiable. Every feature in `team_features` must
satisfy them or it is rejected at build time.

| Rule | Concrete enforcement |
|---|---|
| **No in-game data.** No feature may use any event recorded at or after `commence_time`. | Feature builder reads only data with `event_time < commence_time`. Audit field: `max_source_event_time < commence_time` must be True. |
| **No closing-line data in opening-line training.** If the model targets opener-to-game window, closing odds are off-limits. | Two separate feature manifests when needed: `*_open_v1` and `*_close_v1`. |
| **No box-score features.** Final box-score stats from the game being predicted are NEVER an input. | Explicit blocklist by SourceField. |
| **No future schedule data.** `rest_days_opp` for next game cannot leak into current game. | Computed as `commence_time - prior_game_end_time`. |
| **No leaked injury status.** Injury feature value is the LAST report whose timestamp is `< commence_time`. | `injury_as_of_snapshot_iso < commence_time` audit. |
| **No leaked weather.** Use the FORECAST captured before kickoff, not the actual weather observed during play. | Weather feature row carries `forecast_issued_at < commence_time`. |
| **No truth leakage via market.** Closing-line features OK only to anchor TP at score-time, not as training target. | Model never takes `actual_value` of any other game as a direct input. |

Every `team_features` row carries `leakage_audit_passed: bool`. The
feature builder REFUSES to write when this is False.

### 11.6 Backtest methodology

**Walk-forward, never random.** Time-series cross-validation only.

For each sport-market:

1. Sort all eligible rows by `commence_time`.
2. Fold pattern (default):
   - Train window: 18 months
   - Validation window: 1 month
   - Step: 1 month forward
3. Metrics computed PER FOLD:
   - **Regression**: MAE on `actual_value`, RMSE, Pearson r on
     model μ vs truth.
   - **Calibration**: KS statistic on residuals vs the chosen
     distribution. Reliability diagram (10 deciles).
   - **Probability**: log-loss + Brier score on OVER label using
     the model + distribution layer combined.
   - **Backtest ROI**: simulated wager-1-unit-on-positive-edge
     against historical closing line. Track cumulative PnL,
     drawdown, Sharpe.
4. **Acceptance gates per fold**:
   - MAE < pre-set sport-market threshold (e.g. MLB runs < 0.60).
   - Brier score better than the market-implied-probability baseline.
   - Backtest ROI > 0 net of −110 vig over the full validation set.
   - Calibration: 80% of decile bins within ±5 pp of diagonal.

A model that fails any acceptance gate on any fold does NOT go to
production, period.

### 11.7 Model family recommendations

| Sport-Market | Primary model | Backup / ensemble | Why |
|---|---|---|---|
| MLB team_runs | LightGBM regressor → μ; quantile heads for σ | Bayesian hierarchical (shrinkage to league mean) | Tabular, ~10K games/yr × 3 seasons, gradient boosting state-of-the-art for this shape |
| MLB team_hits | LightGBM | same | same |
| MLB team_strikeouts | LightGBM | Linear (interpretable baseline) | low-dispersion, simpler model competitive |
| MLB team_total_bases | LightGBM | NegBinom-GLM | heavy right tail; verify boost handles it |
| NFL team_total_points | LightGBM | linear + Bayesian shrinkage | ~270 games/season → small data; shrinkage crucial |
| NFL first_half_total | LightGBM (full-game features + H1 history) | derived: 0.5 × full + scaled noise | low data |
| NFL team_passing_yards | LightGBM | linear | tabular, multivariate |
| NFL team_rushing_yards | LightGBM w/ Gamma loss option | linear | right-tail risk |
| NBA team_total_points | LightGBM | linear | 1230 games/season → boost has enough data |
| NBA first_half_total | LightGBM | full × 0.5 + noise | |
| NBA first_quarter_total | LightGBM | full × 0.25 + amplified noise | smallest split, highest σ |

**Per-model artifacts (versioned):**

```
models/team/
├── mlb_runs_v1.joblib
├── mlb_hits_v1.joblib
├── mlb_strikeouts_v1.joblib
├── ...
└── manifest.json    # model_version → {features_used, training_window, fold_metrics, accepted}
```

### 11.8 Collection write flow

```
1. team_odds_backfill        → team_historical_props (immutable archive)
2. team_outcomes_backfill    → team_prop_outcomes (truth)
3. team_features_build       → team_features (engineered, leak-audited)
4. team_projection_build     → team_projections (model μ + σ + distribution_audit)
5. team_tp_compute           → team_prop_scores.{tp, fair_probability, model_probability, edge}
6. team_gates_evaluate       → team_prop_scores.{*_pass, selected_tier, score}
```

Every step is idempotent, replayable, and writes its own audit fields
(`built_at`, `feature_set_version`, `model_version`,
`gate_config_version`). No step ever reads from a later step.

### 11.9 How team projections become true probability

Given a `team_projections` row with `(distribution, μ, σ|None, k|None)`
and a `team_live_props` row with `(line, side, odds, book)`:

1. **Model probability**:
   - Normal: `model_p = 1 - Φ((line + 0.5 - μ) / σ)` for OVER.
   - Poisson: `model_p = 1 - F_pois(floor(line); μ)` for OVER.
   - NegBinom: `model_p = 1 - F_nb(floor(line); k, p)` where
     `p = k/(k+μ)`.
   - UNDER = `1 - OVER` (pushes computed when line is integer).
2. **Multi-book fair probability**: anchor-weighted multiplicative
   devig across all real-money books with a quote on the same
   `(event, team, market, line, side)`. Reference-only books
   excluded (same policy as player side).
3. **True probability (TP) blend**:
   ```
   α = f(model_confidence, n_books_for_devig, fold_calibration)
   tp = α · model_p + (1 - α) · fair_p
   ```
   α bounded `[0.2, 0.8]` so neither side dominates on thin data.
4. **Edge** = `tp - implied_probability`.
5. **TP source provenance** persisted in
   `team_prop_scores.tp_source ∈ {"model", "blend", "market"}` so
   we can always trace why a TP is what it is (lesson from player
   side).

### 11.10 How TP feeds the Team GateAdapter

The Team GateAdapter consumes a row whose shape is identical to the
player-side scoring row. The gate decisions read:

```
tp, edge, vision_score
hit_rate_l20  (team's own L20 hit rate for THIS market)
hit_rate_l10
cv            (coefficient of variation of recent perf)
book_count
sharp_anchor_count
line_movement_open_to_close
```

Threshold values live in
`services/team_scoring/gates/team_thresholds.py` and are **distinct
from player thresholds**. Each `(sport, market)` gets its own SH /
FL / WZ threshold table tuned by backtest in §11.6.

### 11.11 Production validation gates (pre-launch)

A team-prop model goes live ONLY after:

1. **Coverage gate**: ≥ 95% of historical games have all required
   features available without leakage.
2. **Backtest gate**: Every walk-forward fold passes §11.6 criteria.
3. **Calibration gate**: Reliability diagram on a holdout 2-month
   block matches the diagonal within ±5 pp in every decile.
4. **ROI gate**: Simulated −110 backtest on the holdout returns
   ROI > 2% net of vig over ≥ 200 graded bets per tier per
   sport-market.
5. **Drift gate**: Feature distribution on the holdout matches the
   training distribution (PSI < 0.2 for every feature).
6. **Shadow gate**: Model runs in shadow mode against live odds for
   ≥ 14 days, comparing model vs market without producing
   user-facing tier badges. Operator reviews shadow ROI vs
   prediction accuracy daily.
7. **Sign-off**: Platform owner signs the launch checklist with
   timestamp.

Shadow mode flag persists in
`team_prop_scores.production_visible: bool`. Until all 7 gates pass,
the UI hides team prop cards for that sport-market.

### 11.12 What §11 is NOT

- A schedule. Each phase ships when its gates pass, not on a date.
- A model code spec. Hyperparameters, exact feature transforms, and
  loss functions are decided in Phase 1.C during the model-fit
  loop.
- A research wish-list. Everything here is the minimum bar; we may
  iterate beyond it (e.g., neural ranking, market-impact features)
  after production stability.

### 11.13 Sign-off for §11

Status: **PENDING — awaiting operator approval before Phase 1
coding may begin.**

When approved, replace this line with:

```
Approved by <user> on <date>. Phase 1.A coding cleared to start.
```
