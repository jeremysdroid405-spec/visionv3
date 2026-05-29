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
4. ✅ **LOCKED 2026-06-02** — Team Card mirrors Player Card visual
   contract (§5.1–§5.8) with team-specific badge vocabulary.
5. ✅ **APPROVED 2026-06-02** — Predictive-model training plan
   (§11). Phase 1.A foundation coding cleared.
6. ✅ **APPROVED 2026-06-02** — Historical Training Plan (§12).
   **Prod SGO_API_KEY release STAYS WITHHELD** until all items in
   §12.7 are True, §13 baseline benchmark and §14 forward-compat
   hooks are wired, and the pilot window is approved separately.
7. ✅ **APPROVED 2026-06-02** — §13 Historical Baseline Benchmark
   gate: every team model must beat sportsbook baseline on Brier,
   Log Loss, and ROI. Failures do not promote.
8. ✅ **APPROVED 2026-06-02** — §14 Team Optimizer Framework
   forward-compat hooks. Row-shape parity with player-prop replay
   rows. No optimizer code changes in Phase 1; design surface
   ready for Phase 2.

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

Status: ✅ **APPROVED by user on 2026-06-02.** Phase 1.A coding
cleared to start. Prod SGO key release remains gated on §12.8 plus
§12.7 checklist completion.

---

## 12. Phase 1.A — Historical Training Plan

Production SGO API access stays withheld until this section is signed
off. Historical ingest at scale commits us to storage growth,
collection schema, feature schema, and millions of immutable rows.
We design that surface area on paper FIRST, validate the math, then
turn on the firehose.

### 12.1 Data Inventory

For every (sport × market) we lock the following 5 attributes BEFORE
any ingest begins.

#### MLB

| Market | Label (target) | Historical source | Required features (manifest version) | Expected records / season (game-team rows × books × snapshots) | Earliest reliable date |
|---|---|---|---|---|---|
| `team_total_runs` | runs scored by team in 9 innings | MLB Stats API + SGO odds | `mlb_runs_v1` (28 fields, §11.4 plus park / weather / starter / bullpen) | 4,860 truth rows; ~2.3M odds rows | 2019-03-28 (Statcast era stable; SGO archive coverage robust from 2020) |
| `team_total_hits` | team hits | MLB Stats API + SGO | `mlb_hits_v1` (same skeleton + handedness split) | 4,860 truth; ~2.0M odds | 2019-03-28 |
| `team_strikeouts` | team batter strikeouts | MLB Stats API + SGO | `mlb_strikeouts_v1` (starter K/9, bullpen K/9) | 4,860 truth; ~1.5M odds | 2019-03-28 |
| `team_total_bases` | team total bases | MLB Stats API + SGO | `mlb_total_bases_v1` (slugging-tilted) | 4,860 truth; ~1.2M odds | 2020 (SGO market coverage spotty before) |

#### NBA

| Market | Label | Source | Features | Records / season | Earliest |
|---|---|---|---|---|---|
| `team_total_points` | team points (full game) | NBA Stats API + SGO | `nba_points_v1` (pace, ORtg, DRtg, rest, b2b, injury) | 2,460 truth; ~1.5M odds | 2018-10-16 |
| `first_half_total` | team H1 points | NBA Stats API quarter splits + SGO | `nba_h1_v1` (full features + 1H historical splits) | 2,460 truth; ~700K odds | 2019-10 (1H market liquidity threshold) |
| `first_quarter_total` | team Q1 points | NBA Stats API quarter splits + SGO | `nba_q1_v1` (starting-five minutes, opp Q1 D-rating) | 2,460 truth; ~400K odds | 2020-10 (Q1 market liquidity threshold) |

#### NFL

| Market | Label | Source | Features | Records / season | Earliest |
|---|---|---|---|---|---|
| `team_total_points` | team points (full game) | nflfastR + SGO | `nfl_points_v1` (weather, rest, divisional, QB-tier) | 544 truth; ~280K odds | 2018-09 (nflfastR + SGO joint coverage) |
| `first_half_total` | team H1 points | nflfastR + SGO | `nfl_h1_v1` | 544 truth; ~140K odds | 2019-09 |
| `team_passing_yards` | net passing yards | nflfastR + SGO | `nfl_pass_v1` (opp pass D-grade, QB L4, weather) | 544 truth; ~250K odds | 2018-09 |
| `team_rushing_yards` | rushing yards | nflfastR + SGO | `nfl_rush_v1` (opp run D-grade, OL grade, weather) | 544 truth; ~220K odds | 2018-09 |

**Total dataset estimate (all sports, 1 season):**

```
Truth rows (team_prop_outcomes):       ~25,400 / season
Engineered features (team_features):   ~25,400 / season (same cardinality)
Projections (team_projections):        ~25,400 / season
Live + historical odds (multi-book):   ~10.5M  / season
```

Odds dominates by 400×. Storage discipline is critical (§12.3).

### 12.2 Training Architecture per market

Same shape for every (sport × market). All collections are versioned
and write-once for that version.

```
SGO historical odds API
    ↓ workers/team_odds_backfill
team_historical_props          ── immutable archive (compound-keyed, multi-book)
    │
    │ joined on (event_id, team_id, market, line, side, book)
    ↓
MLB Stats / NBA Stats / nflfastR
    ↓ workers/team_outcomes_backfill
team_prop_outcomes             ── ONE row per (event, team, market, line, side); actual_value + outcome_numeric
    │
    │ joined on (event_id, team_id) with…
    ↓
team_matchups + team_injuries + team_context
    ↓ workers/team_features_build  (manifest = mlb_runs_v1, nfl_pass_v1, …)
team_features                  ── leak-audited, versioned, one row per (event, team, market)
    │
    │ feeds model training (offline, walk-forward, §11.6)
    ↓ models/team/<sport>_<market>_<version>.joblib
team_projections               ── μ, σ|None, distribution, model_version
    │
    │ joined with live `team_live_props` row
    ↓ services/team_tp/
team_prop_scores               ── model_p, fair_p, tp, edge, tp_source provenance
    ↓ services/team_scoring/gates/
team_prop_scores               ── *_pass, selected_tier, score (same row, updated in-place)
    ↓ research-mode mirror (analog of player-side replay)
team_replay_outputs            ── SSOT replay artifact
```

Critical invariants from this diagram:

1. **No step reads from a later step.** Same SSOT discipline that
   carried the player-side replay coverage from 33% to 97%.
2. **`team_historical_props` is immutable.** Once written for a
   game-snapshot, never updated. New snapshots are new rows.
3. **`team_features` is reproducible.** Given a `feature_set_version`
   the builder must produce byte-identical output (modulo float
   precision) on a rebuild. Locks out "the data drifted under me"
   bugs.
4. **`team_projections` is bound to `model_version` + `feature_set_version`.**
   You cannot retrofit features into an old projection. New features
   = new projection rows + new model version.

### 12.3 Storage Estimate

Per-document size estimate (conservative, with indexes):

| Collection | Doc size (bytes) | Index size factor |
|---|---|---|
| `team_historical_props` | 600 (small doc, many odds quotes) | 1.4× |
| `team_prop_outcomes` | 400 | 1.3× |
| `team_features` | 2,500 (rich feature vector) | 1.2× |
| `team_projections` | 600 | 1.2× |
| `team_prop_scores` | 900 | 1.4× |
| `team_replay_outputs` | 900 | 1.4× |

#### 1-year horizon (all sports, all markets)

| Collection | Rows | Raw | With indexes |
|---|---|---|---|
| `team_historical_props` | 10.5M | 6.3 GB | 8.8 GB |
| `team_prop_outcomes` | 25.4K | 10 MB | 13 MB |
| `team_features` | 25.4K | 64 MB | 77 MB |
| `team_projections` | 25.4K | 15 MB | 18 MB |
| `team_prop_scores` | ~3M (multi-book live) | 2.7 GB | 3.8 GB |
| `team_replay_outputs` | ~10M (multi-book replay) | 9.0 GB | 12.6 GB |
| **Subtotal** | | **18.1 GB** | **25.3 GB** |

#### 3-year horizon

```
team_historical_props:  ~31M    rows   ≈  26 GB on disk
team_prop_outcomes:     ~76K           ≈  39 MB
team_features:          ~76K           ≈ 231 MB
team_projections:       ~76K           ≈  54 MB
team_prop_scores:       ~9M            ≈ 11 GB
team_replay_outputs:    ~30M           ≈ 38 GB
Subtotal:                              ≈ 75 GB on disk
```

#### 5-year horizon

```
team_historical_props:  ~52M    rows   ≈  44 GB
team_prop_outcomes:     ~127K          ≈  66 MB
team_features:          ~127K          ≈ 380 MB
team_projections:       ~127K          ≈  91 MB
team_prop_scores:       ~15M           ≈  19 GB
team_replay_outputs:    ~52M           ≈  65 GB
Subtotal:                              ≈ 130 GB on disk
```

**Storage discipline rules (lessons from player-side replay
explosion):**

1. **TTL on live, no TTL on historical.** `team_live_props` purges
   to `team_historical_props` after 48 h.
2. **Compound unique index on every multi-book collection** so
   double-ingest can't double the row count.
3. **`bulk_write` chunk size = 1,000** for every backfill writer,
   identical to the player-side fix that resolved Grid Sweep OOMs.
4. **`gc.collect()` after every flush** in long-running backfills
   (we already learned this on the replay engine).
5. **No `find().to_list(None)`** anywhere — only cursors with
   `batch_size=500`. This is the rule we wrote after the 3.6 GB
   FastAPI leak.
6. **`maxTimeMS` on every aggregation** so a runaway pipeline can't
   wedge the server.
7. **Per-month coverage panel** (analog of the warehouse coverage
   page) so missing rows are visible BEFORE training, not after.

### 12.4 Ingestion Plan

Three independent backfill workers. Each writes to ONE collection
and can be re-run idempotently.

```
─────────────────────────────────────────────────────────────
Worker 1: team_odds_backfill (uses SGO_API_KEY)
─────────────────────────────────────────────────────────────
  for sport in (MLB, NBA, NFL):
    for game_date in window:
      pull /v2/events?sportID={sport}&date={game_date}
      for each event:
        for each team-prop market in registry[sport]:
          for each book quote in books[]:
            UPSERT into team_historical_props
              keyed on (event_id, team_id, market, line, side, book, snapshot_iso)
  emit: skip_reasons{blocked_book, no_market, no_line, etc.}

─────────────────────────────────────────────────────────────
Worker 2: team_outcomes_backfill (no SGO needed; uses sport APIs)
─────────────────────────────────────────────────────────────
  for sport in (MLB, NBA, NFL):
    for game_date in window:
      pull box scores from MLB Stats / NBA Stats / nflfastR
      for each (event_id, team_id, market in registry[sport]):
        UPSERT into team_prop_outcomes
          keyed on (event_id, team_id, market, line, side)
        with actual_value + outcome_numeric

─────────────────────────────────────────────────────────────
Worker 3: team_stats_backfill (running team performance history)
─────────────────────────────────────────────────────────────
  for sport in (MLB, NBA, NFL):
    for game_date in window:
      pull team game logs (offense + defense + pace + injuries)
      UPSERT into team_game_stats (NEW collection — feeds features)
        keyed on (event_id, team_id)
```

**Join contract:**

```
team_historical_props          ── (event_id, team_id, market, line, side, book, snapshot_iso)
                                        ▲
                                        │ joined on (event_id, team_id)
team_prop_outcomes             ── (event_id, team_id, market, line, side)
                                        ▲
                                        │ joined on (event_id, team_id)
team_game_stats                ── (event_id, team_id)
team_matchups                  ── (event_id)
team_context                   ── (event_id, team_id)
team_injuries                  ── (event_id, team_id)
                                        ↓
                            team_features (built from the above)
```

The join key for label vs feature is `(event_id, team_id, market)`.
Cardinality must match — any feature row without a matching outcome
row gets `label_present=False` and is dropped from training (but
kept for inference / future grading).

**Backfill order (mandatory):**

1. team_master_hub seed (one-time)
2. team_matchups for the entire window (schedule must be complete)
3. team_outcomes_backfill (truth labels must exist before features)
4. team_stats_backfill (need historical performance for rolling features)
5. team_odds_backfill (can run in parallel with #4 — they don't overlap)
6. team_features_build (after #3, #4, #5 are complete)
7. Model training (offline, see §12.5)

### 12.5 Validation Plan

NO production ingest is allowed until the validation methodology
is signed off here. This section is the contract.

#### 12.5.1 Train / Validation / Test split

Time-series only — never random. For a model targeting 2025
production:

```
Train:           2020-01-01 → 2024-04-30   (~4 seasons of MLB, 5 of NFL/NBA)
Validation:      2024-05-01 → 2024-12-31   (in-sample tuning, walk-forward)
Out-of-sample:   2025-01-01 → 2025-04-30   (HOLDOUT — never touched until final acceptance)
Backtest:        2025-05-01 → 2025-09-30   (paper-trade simulation, ROI computation)
```

Walk-forward fold pattern within the train+validation window:
18-month train → 1-month validation → step 1 month (§11.6).

#### 12.5.2 Success criteria (must hit ALL before launch)

| Gate | Metric | Threshold |
|---|---|---|
| **G1 — Coverage** | `% game-team rows with leak-audited feature row` | ≥ 95% |
| **G2 — Regression** | MAE on `actual_value` (validation) | sport-specific table below |
| **G3 — Calibration** | KS p-value on `(actual − μ) / σ` vs chosen distribution | ≥ 0.05 |
| **G4 — Decile calibration** | % of 10 deciles where reliability matches diagonal ±5 pp | ≥ 80% |
| **G5 — Probability skill** | Brier score vs market-baseline | strictly lower |
| **G6 — Out-of-sample** | Brier + MAE on the untouched 4-month holdout | within 10% of validation |
| **G7 — Backtest ROI** | Simulated −110 wager-on-positive-edge ROI on the 5-month backtest window | > +2% net of vig over ≥ 200 graded bets per tier |
| **G8 — Drift** | PSI on each feature comparing train vs OOS distribution | < 0.2 per feature |
| **G9 — Shadow mode** | 14-day live shadow comparing model to market without user-facing tiers | operator review approves |

MAE acceptance thresholds (G2):

| Sport / Market | MAE upper bound (validation) |
|---|---|
| MLB team_total_runs | 0.60 runs |
| MLB team_total_hits | 0.75 hits |
| MLB team_strikeouts | 1.40 K |
| MLB team_total_bases | 1.20 TB |
| NBA team_total_points | 5.5 pts |
| NBA first_half_total | 3.0 pts |
| NBA first_quarter_total | 2.0 pts |
| NFL team_total_points | 4.5 pts |
| NFL first_half_total | 2.6 pts |
| NFL team_passing_yards | 38 yds |
| NFL team_rushing_yards | 26 yds |

(Thresholds set from public published win-rates of comparable
prop-model papers; sharpen during fitting.)

#### 12.5.3 Failure handling

If ANY gate fails for a (sport × market):

- Model is NOT promoted.
- `team_prop_scores.production_visible = False` for that market.
- UI hides that market under that sport.
- Diagnostic report auto-generated (which gate failed, by how much,
  with the calibration plot + ROI curve + feature drift table).
- Re-train cycle begins; production ingest continues but downstream
  scoring + UI surface remain dark for that market.

### 12.6 Phased rollout of production ingest

Once §12 is approved AND the SGO key is set in prod:

| Phase | Window | Purpose | Stop condition |
|---|---|---|---|
| **12.A-pilot** | 2025-04-01 → 2025-04-30 (single month) | Validate schema, joins, blocked-book filter, coverage audit, multi-book invariant | Coverage < 95% on any one collection → STOP and fix |
| **12.A-2024** | 2024-01-01 → 2024-12-31 | First full season per sport for training | All 9 success gates pass on 2024 walk-forward folds → GO |
| **12.A-3yr** | 2022-01-01 → 2024-12-31 | Three seasons of training data | 75 GB storage budget honored (§12.3) |
| **12.A-5yr** | 2020-01-01 → 2024-12-31 (sport-dependent) | Five-season corpus for the markets that need it (NFL, NBA points) | 130 GB storage budget honored |
| **12.B-shadow** | continuous from launch + 14 days | Shadow mode validation | G9 passes → user-facing toggle ON |
| **12.B-go-live** | continuous | Production scoring | All 9 gates maintained on rolling 30-day window |

Each phase has a stop condition that an automated audit can detect.
The audit job runs at the end of each ingest window and writes a
`team_phase_audit` document with PASS/FAIL per gate.

### 12.7 Pre-prod-ingest checklist (mandatory)

The following ALL must be True before the first prod ingest call:

```
[ ] §10 gate 5 (predictive-model training plan, §11) signed
[ ] §12.5 validation methodology signed (this section)
[ ] team_master_hub seeded for the 3 sports (~100 teams)
[ ] storage budget reserved (≥ 30 GB Mongo headroom)
[ ] backfill workers shipped with `bulk_write` chunked at 1000
[ ] backfill workers carry `gc.collect()` after each flush
[ ] coverage audit endpoint live: /api/emergent-admin/team/coverage
[ ] blocked-book + reference-only policies wired into team_odds_backfill
[ ] regression tests pin multi-book invariant for team odds (analog of test_reshape_multi_book.py)
[ ] team_outcomes_backfill verified on a 1-week pilot — manually spot-check 20 rows
[ ] SGO_API_KEY set in PROD env (this is the last switch to flip)
```

### 12.8 Sign-off for §12

Status: ✅ **APPROVED by user on 2026-06-02.** Phase 1.A coding
cleared. **Prod SGO_API_KEY release remains WITHHELD** until all
items in §12.7 are True and §13 baseline-benchmark + §14 optimizer
forward-compat requirements are wired into the implementation.

The key-release contract (still binding):

```
Prod SGO_API_KEY release requires ALL of:
  ✓ §11.13 signed (done 2026-06-02)
  ✓ §12.8 signed (done 2026-06-02)
  ☐ §13 Historical Baseline Benchmark wired (per-market vs sportsbook)
  ☐ §14 Team Optimizer Framework forward-compat wired
  ☐ All 11 items in §12.7 checklist = True
  ☐ Pilot ingest target window approved separately by operator
```

Until that line is fully checked, no team-prop ingest dispatch
runs against prod.

---

## 13. Historical Baseline Benchmark (Pre-Production Gate)

**Locked by operator 2026-06-02.** Every team model must beat a
simple sportsbook baseline on every market before it can be promoted
to production. A model that loses to the market on its own
hand-picked validation window is, by definition, adding noise — and
shipping it would actively degrade trust in the platform.

### 13.1 The baseline

For every `(sport, market)`:

```
Baseline_p(OVER | line)  =  multi_book_devigged_fair_probability
                            (anchor-weighted, identical to player-side devig)
Baseline_p(UNDER | line) =  1 - Baseline_p(OVER | line)
```

The baseline reads from `team_historical_props` at `commence_time -
30 min` snapshot (consistent "30-minutes-out" benchmark across all
sports) and produces a probability for every (event, team, market,
line, side) tuple that has a graded outcome.

### 13.2 Metrics tracked (per market, per validation fold)

For BOTH the model and the baseline, on the SAME row set:

| Metric | What it measures | Direction |
|---|---|---|
| **MAE** on `actual_value` vs μ | regression error of the projection | lower better |
| **Brier score** on `outcome_numeric` vs `tp` (or baseline prob) | overall probabilistic accuracy | lower better |
| **Log loss** on `outcome_numeric` vs probability | calibration-sensitive accuracy | lower better |
| **Reliability diagram + ECE** (expected calibration error) | how well probabilities reflect frequencies | lower better |
| **Backtest ROI** at −110 flat-staking, positive-edge filter | real-money simulation | higher better |
| **Backtest ROI — sharp-only** at −110, positive-edge, sharp-book-only | how the model performs when forced to bet sharp prices | higher better |

### 13.3 The hard rules

1. **A model that loses to baseline on ANY of `Brier`, `Log loss`,
   or `Backtest ROI` does NOT promote to production.** Period.
2. **MAE alone is insufficient.** A model can have lower MAE than
   baseline and still lose on Brier (e.g., overconfident on
   mid-line props). All three of (Brier, Log Loss, ROI) must beat
   baseline.
3. **The comparison must be on the SAME row set** — every row that
   has a baseline probability available is included; every row that
   doesn't is excluded from BOTH sides. No cherry-picking the
   model's good days.
4. **The baseline is computed ONCE per row from a frozen snapshot
   (`commence_time - 30 min`).** The model does not get to peek at
   later odds movement. Identical evaluation footing.

### 13.4 Persistence

Each model's benchmark result lives in a new collection
`team_model_benchmark_runs` with one row per `(model_version,
sport, market, fold_id)`:

```
{
  model_version, feature_set_version, sport, market, fold_id,
  fold_window: { train_start, train_end, val_start, val_end },
  n_rows: int,
  model: { mae, brier, log_loss, ece, roi, roi_sharp_only },
  baseline: { mae, brier, log_loss, ece, roi, roi_sharp_only },
  delta:    { brier, log_loss, roi, roi_sharp_only },  # model - baseline (negative = model wins)
  promoted: bool,
  promoted_reason_if_not: str | null,
  built_at,
}
```

The diagnostic page reads from this collection. Before a model is
promoted, the operator can inspect this table and see every fold's
delta vs baseline.

### 13.5 Acceptance integration with §11.6 / §12.5

The §11.6 / §12.5 acceptance gates already include "Brier better
than market-baseline" and "ROI > +2% net of vig over the holdout."
§13 makes this explicit, per-market, recorded, and visible. It
strengthens §11.6 — does not replace it.

### 13.6 What §13 prevents

- Promoting a model whose calibration is wrong but whose mean is
  close to market (silently bleeds ROI in production).
- Promoting a model that fits the average but mis-prices the tails
  (where the +150+ longshot value lives — same lesson from the
  player-side Fliff incident).
- Hiding behind cherry-picked metrics. The benchmark table is the
  single canonical artifact.

---

## 14. Team Optimizer Framework — Forward-Compat (Phase 1 design only)

**Locked by operator 2026-06-02.** Team Props must be designed from
day one to plug into the existing optimizer research workflow used
for player props. The optimizer is one of the most valuable research
tools on the platform; retrofitting it for team props later would
cost months and re-introduce coupling we just spent weeks removing.

### 14.1 The forward-compat contract

The optimizer's `_evaluate_combo` function reads rows with a known
shape. Team prop rows in `team_prop_scores` and
`team_replay_outputs` MUST produce rows with that same shape so the
existing optimizer can consume them with zero code change beyond a
collection-name parameter.

The shared row contract (subset of player-prop replay row):

```
event_id, game_date, league_id, sport,
entity_id     (player_id for player rows, team_id for team rows),
entity_name   (player_name / team_name),
entity_kind   ("player" | "team")               ← NEW shared field
market, stat_family, line, side,
book, odds, implied_probability, fair_probability,
tp, tp_source, edge, cv, vision_score,
hit_rate_l20, hit_rate_l10, hit_rate_l5,
selected_tier, safe_haven_pass, front_lines_pass, war_zone_pass,
outcome_resolved, outcome_numeric, hit, actual,
pipeline_version, ssot_source, scored_at,
model_version, feature_set_version, gate_config_version,
n_reference_only_skipped                        ← reused audit field
```

Every team-prop row carries `entity_kind: "team"`. Every player-prop
row gets `entity_kind: "player"` (backfilled on next mirror pass).
The optimizer reads `entity_kind` for diagnostics + filtering but
its math does not branch on it.

### 14.2 Optimizer run-id namespacing

Future optimizer runs targeting team data get a distinct run-id
prefix and a distinct results collection:

```
opt_team_runs          ← MLB team runs
opt_team_hits          ← MLB team hits
opt_team_strikeouts    ← MLB team K's
opt_team_total_bases   ← MLB team TB
opt_team_points        ← NBA / NFL team total points (sport disambiguated via filter)
opt_team_first_half    ← NBA / NFL 1H totals
opt_team_first_quarter ← NBA 1Q totals
opt_team_pass_yards    ← NFL passing yards
opt_team_rush_yards    ← NFL rushing yards
```

This isolation lets us:

- Tune optimizer thresholds per team market without polluting the
  player-prop tuning.
- Run team and player optimizers in parallel on the same window
  without locking each other out.
- Compare team-vs-player edge distributions side-by-side as a
  cross-product audit (does the team model agree with the player
  models that drive that team's score?).

### 14.3 Required hooks in Phase 1 code

To honor §14 without doing any optimizer code now:

| Hook | Where | What |
|---|---|---|
| `entity_kind` field on row writes | `team_prop_scores`, `team_replay_outputs` | Always set to `"team"` |
| `entity_kind` backfill on player rows | one-time mirror script | Adds `entity_kind: "player"` to every existing player replay row (idempotent, indexed) |
| Optimizer `--collection` flag | placeholder added to existing `/optimizer/launch` endpoint, default = player replay collection | Operator can later target team replay collection without code change |
| Reference-only / blocked-book policies | shared module | Already implemented for player side; team workers must `import` from the SAME module, not duplicate |
| `n_reference_only_skipped` audit | same name on team-row writes | Optimizer's existing audit panel works unchanged |

### 14.4 What §14 explicitly does NOT do in Phase 1

- Does NOT modify the optimizer code (no scoring tweaks, no new
  metrics).
- Does NOT add a "Team Optimizer" tab to the admin UI.
- Does NOT run the optimizer on team data.
- Does NOT define team-specific score-formula weights.

All of the above happens in Phase 2 (post-launch). Phase 1's
obligation is solely: write rows in a shape the optimizer can later
read.

### 14.5 Regression test contract

Phase 1 ships with a contract test that pins:

1. Every `team_prop_scores` write carries `entity_kind: "team"`.
2. Every `team_replay_outputs` write carries `entity_kind: "team"`.
3. The player-side mirror pass produces `entity_kind: "player"` on
   every row.
4. The shared `BLOCKED_BOOKS` and `REFERENCE_ONLY_BOOKS` sets are
   the SAME object (`from policy import …`) on both team and
   player code paths — verified by `id()` identity check in tests.

Without these pins the forward-compat surface drifts silently and
Phase 2 optimizer work becomes a refactor instead of a config
change.

### 14.6 Sign-off for §14

Status: ✅ **APPROVED by operator 2026-06-02.** The hooks in §14.3
MUST land in Phase 1.A. The actual optimizer integration ships in
Phase 2.

