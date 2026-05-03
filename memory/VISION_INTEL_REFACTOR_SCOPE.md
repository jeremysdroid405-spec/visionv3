# Vision Intel — Universal Refactor Scope

## Mandate
Replace the sport-coupled Vision Intel layer with a universal engine + sport adapters + YAML config. NBA and MLB must produce identical output schema. Strictly enrichment — never gating, never ranking.

## Architecture target

```
backend/services/vision_intel/
  __init__.py
  schema.py               # NormalizedInput / BadgeOutput / SummaryOutput dataclasses
  engine.py               # Public: enrich(picks, sport) → picks (mutates `vision_intel`)
  badge_engine.py         # Pure: NormalizedInput + thresholds → list[BadgeOutput]
  summary_engine.py       # Pure: NormalizedInput + badges → SummaryOutput
  adapters/
    __init__.py
    base.py               # ABC: pick_to_normalized(pick) → NormalizedInput
    nba.py
    mlb.py
    nfl.py                # NotImplementedError stub
  config/
    nba.yaml
    mlb.yaml
    nfl.yaml              # Stub
backend/tests/
  test_vision_intel_universal.py
  test_vision_intel_output_contract.py
  test_vision_intel_mutation.sh
```

## Universal Schema

```python
@dataclass
class NormalizedInput:
    player: str
    sport: str                    # 'nba' | 'mlb' | 'nfl'
    stat_family: str              # 'volume_scoring' | 'rate_efficiency' | 'composite' | etc.
    stat_type: str                # 'PTS' | 'Hits+Runs+RBIs' | etc. (raw)
    line: float
    side: str                     # 'OVER' | 'UNDER'
    p_true: float                 # 0..1
    edge: float                   # signed; positive = +EV
    hit_rate: Optional[float]     # 0..100 (side-adjusted L20)
    cv: Optional[float]
    recent_form: dict             # {l5_rate, l10_rate, l20_rate, sample_size}
    context: dict                 # adapter-specific extras (opponent, dvp_rank, minutes, etc.)

@dataclass
class BadgeOutput:
    label: str                    # human display, e.g. "Elite Floor"
    sport: str
    type: str                     # 'edge' | 'consistency' | 'volatility' | 'matchup' | 'risk'
    severity: str                 # 'positive' | 'neutral' | 'caution' | 'critical'
    confidence: float             # 0..1
    supporting_metrics: dict      # {hit_rate: 92, edge: 0.13, ...}

@dataclass
class SummaryOutput:
    headline: str                 # "Elite floor on Tatum 3PM 1.5 OVER"
    body: str                     # 2-3 sentences
    confidence: float             # 0..1, derived from p_true + sample_size
    risk_flags: list[str]         # ['low_sample', 'high_volatility', 'direction_close']
    supporting_factors: list[str] # ['L10 hit rate 95%', '+14% edge', 'Vision 87']
```

## Final pick payload

```json
{
  ...existing pick fields...,
  "vision_intel": {
    "badges":  [BadgeOutput, ...],
    "summary": SummaryOutput
  }
}
```

## Engine constraints
1. `engine.py` MUST NOT branch on `sport`. It receives `NormalizedInput` and reads `config/{sport}.yaml`.
2. Adapters do data shape only — no logic. No threshold reads. No template strings.
3. Badge rules live in YAML:
   ```yaml
   thresholds:
     edge:
       elite: 0.10
       strong: 0.05
     hit_rate:
       elite: 90
       strong: 80
     cv:
       low: 0.30
       high: 0.60
     p_true:
       safe: 0.75
   stat_families:
     volume_scoring:    [PTS, Hits, Total Bases, RBIs]
     rate_efficiency:   [3PM, BB, BO]
     composite:         [PRA, H+R+RBI, PTS+REB]
   summary_tone:
     headline_max_chars: 60
     body_max_sentences: 3
   ```
4. Summary engine MUST be side-aware: an UNDER pick gets ceiling-language ("ride the under"), an OVER gets floor-language ("hammer the over").
5. Output identical across sports — verified by `test_vision_intel_output_contract.py` which round-trips one NBA pick + one MLB pick through the engine and asserts schema equality.

## Performance
- Pure-Python rule engine. NO Gemini in the rule path.
- Content-hash cache key: `sha256(player|stat|line|side|round(p_true,3)|round(edge,3)|round(cv,3)|hit_rate)`.
- Cache stored in `vision_intel_cache` collection with TTL 1h.
- Target: <500ms for 100 picks.

## Pre-existing fields available on every pick (use these in adapters)

**Universal:**
- `cv`, `volatility_score`, `volatility_label`
- `edge_vs_fair`, `vk_edge`, `true_edge`
- `p_true_active`, `vk_prob_over`, `propvision_true_prob`, `distribution_p_over/under`
- `hit_rate_over`, `hit_rate_under`, `h10_rate`, `l10_rate`, `l5_rate`, `sample_size`
- `model_projection`, `vk2_projection`
- `vision_score`
- `recommendation`, `direction`, `line`, `stat_type`, `player_name`, `team`, `opponent`
- `game_start_utc`, `event_id`

**NBA-only:**
- `mu_minutes_l10`, `usage_vacuum_factor`, `hetero_sigma_*`

**MLB-only:**
- `opp_pitcher_name`, `dvp_rank`, `park_factor`

## Pipeline integration (DO LAST)
1. `services/master_sync.py:272` — replace `if sport == "nba"` Gemini block with:
   ```python
   from services.vision_intel.engine import enrich
   await enrich(db, sport)   # writes vision_intel field on prop_scores docs
   ```
2. `routes/ferrari_tiers.py` — read `pick.vision_intel` straight from the score doc; no merging logic needed.
3. Frontend follow-up commit:
   - `UniversalPlayerCard.jsx` — read `pick.vision_intel.summary.headline` instead of `pick.vision_intel` string.
   - `PlayerDetailPage.jsx` — render `pick.vision_intel.badges` array.

## Existing code to deprecate (DO NOT delete in same PR)
- `services/vision_intel_service.py` — Gemini-based NBA path. Keep alive; mark legacy. The universal engine can OPTIONALLY call it as an enrichment step that fills `summary.body_long` (Gemini-authored long-form), but the rule-based `headline`/`body`/`confidence` always win.
- `services/mlb_vision_intel.py` — same pattern.
- `routes/ferrari_tiers.py:_generate_vision_fallback` — keep for one release; remove once frontend reads structured payload.
- `data/{nba,mlb}_master_active_cache.json` — Apr-23 stale snapshots. Drop the `overlay_enrichment_cache` call after the universal engine ships.

## Acceptance criteria
- [ ] Sample output: 3 NBA picks + 3 MLB picks printed by `python -m services.vision_intel.engine sample` showing identical schema.
- [ ] All existing scoring/adapter tests pass (no regression).
- [ ] One mutation test: empty `thresholds.edge.elite` in `nba.yaml` → at least one universal test fails. Caught.
- [ ] Engine runs <500ms for 100 picks (no Gemini calls).
- [ ] No engine code references `sport` for branching (grep + manual review).
- [ ] Cache hit rate >70% on second run with identical picks.

## Hard-learned constraints (read first!)
- `_SCORE_OUTPUT_FIELDS` allowlist in `services/scoring/prop_scores_store.py` — add `vision_intel` to it or the field silently drops on every recompute.
- Watcher `new_keys` set-diff was JUST fixed (2026-05-02) to subtract `active=True` rt keys only. Don't regress it.
- The user is highly critical of fluffy/templated language. Keep Vision Intel summaries grounded in numbers.

## Suggested order of operations
1. `schema.py` + tests for the dataclasses (15 min).
2. `config/nba.yaml` + `config/mlb.yaml` populated with current thresholds (15 min).
3. `adapters/base.py` + `adapters/nba.py` + `adapters/mlb.py` (30 min).
4. `badge_engine.py` + `summary_engine.py` (40 min).
5. `engine.py` orchestrator + content-hash cache (20 min).
6. `test_vision_intel_universal.py` + mutation test (20 min).
7. Pipeline wiring in `master_sync.py` + verify on live data (15 min).
8. Frontend payload swap (separate commit, ~20 min).

Total: ~3h with buffer. Plan for one 3h focused session OR split across two sessions: backend (1-7) + frontend (8) the next day.
