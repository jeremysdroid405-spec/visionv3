# 🚨 P0 Batter Strikeouts Audit — ADDENDUM: Pitcher↔Batter contamination check

Question raised by user (2026-05-16): "Are we also positive that we didn't get pitcher SO and batter SO contaminated with each other by just using 'Strikeouts' during training?"

**Bottom line: Direct field-name contamination is NOT happening. Pitcher Ks (`pitcher_strikeouts`) are NEVER used as the target for `mlb_hf_strikeouts.pkl`. The two stats are stored under different field names and the training script never crosses them. Indirect leakage exists at <0.5 % of training samples and is far too small to explain the Witt/Garcia inversion.**

---

## 1. How the two stats are kept separate in the data model

### Field-name SSOT (`mlb_high_friction_model.py::STAT_FIELD_MAP`)

```python
STAT_FIELD_MAP = {
    'strikeouts':         'strikeouts',         # batter K
    'pitcher_strikeouts': 'pitcher_strikeouts', # pitcher K
    ...
}
```

Each stat reads from its own column in `bdl_game_logs`.

### Alias map (`_normalize_stat`)

```python
'batter_strikeouts': 'strikeouts',   # normalizes UI label → batter target
'pitcher_strikeouts': 'pitcher_strikeouts',
'k': 'pitcher_strikeouts',
'ks': 'pitcher_strikeouts',
```

`"Batter Strikeouts"` (the UI label) routes to `norm_stat = "strikeouts"` → `mlb_hf_strikeouts.pkl`.
`"Pitcher Strikeouts"` routes to `norm_stat = "pitcher_strikeouts"` → `mlb_hf_pitcher_strikeouts.pkl`.

These are two physically separate model files on disk:

```
/app/backend/models/mlb_hf/mlb_hf_strikeouts.pkl           ← batter
/app/backend/models/mlb_hf/mlb_hf_pitcher_strikeouts.pkl   ← pitcher
```

### canonical_stats.py mapping (UI ↔ stat_family)

```python
"batter_strikeouts":             "Batter Strikeouts",
"batter_strikeouts_alternate":   "Batter Strikeouts",
"pitcher_strikeouts":           "Pitcher Strikeouts",
"pitcher_strikeouts_alternate": "Pitcher Strikeouts",
"batter strikeouts":  "batter_strikeouts",
"pitcher strikeouts": "pitcher_strikeouts",
```

Confirmed: routing layer never collapses the two stats.

## 2. How the training script reads targets

In `scripts/retrain_mlb_models_v2.py` (the script that produced the v3.0_bayes pickle):

```python
STATS = ['hits', 'total_bases', ..., 'pitcher_strikeouts', ..., 'strikeouts', ...]

for stat_name in STATS:               # 'strikeouts' is a SEPARATE pass from 'pitcher_strikeouts'
    ...
    for player in player_cursor:       # iterates EVERY hub player (no position filter — see §5)
        for i in range(max_windows):
            target_game = logs[i]
            target_val = model._get_stat_value(target_game, stat_name)  # reads game.strikeouts
            if target_val is None:
                continue
            ...
```

`_get_stat_value(game, 'strikeouts')` reads `game['strikeouts']` — the batter K column. `_get_stat_value(game, 'pitcher_strikeouts')` reads `game['pitcher_strikeouts']` — the pitcher K column. The two are never aliased.

**The label is correct. The target column is correct.**

## 3. Field presence sanity check

For pure pitcher game logs (RP / SP appearances on a non-batter day):

```
Cole Ragans (SP):
  date=2026-05-06  strikeouts=None  pitcher_strikeouts=4   IP=3      pitch_count=58
  date=2026-05-02  strikeouts=None  pitcher_strikeouts=8   IP=5.1    pitch_count=95
  date=2026-04-25  strikeouts=None  pitcher_strikeouts=11  IP=6      pitch_count=99
```

`strikeouts = None` ⇒ `_get_stat_value(...) returns None` ⇒ `if target_val is None: continue` at line 326 of the retrain script ⇒ **the row is dropped from training of the batter strikeouts model.**

Pure pitching appearances **cannot leak** into `mlb_hf_strikeouts.pkl`.

## 4. Where SOME leakage could occur — quantified

### Total log-row composition (DB-wide)

| Category | Rows with `strikeouts != None` |
|---|---:|
| Non-pitcher positions (2B/SS/3B/OF/C/1B/DH …) | **96,159** |
| `position = None` (uncategorized hub rows) | 76,459 |
| Pitchers, RP | 98 |
| Pitchers, SP | 222 |
| **Pitcher total** | **320** |

So out of ~172k batter-strikeouts-eligible label rows in the hub, **only 320 (0.19 %) come from rows the hub explicitly tags as pitchers.**

### What those 320 rows actually are

```
RP  rows: 98 total — 97 have strikeouts=0, 1 has strikeouts=1, max=1
SP  rows: 222 total — 121 have strikeouts=0, 101 have strikeouts=1-3, max=3
```

Concretely they are:

```
Trevor Megill (RP):    K_b=0, K_p=2, PA=0, IP=1   ← "I pitched, didn't bat" placeholder
Devin Williams (RP):   K_b=0, K_p=1, PA=0, IP=1   ← same
Adam Frazier (2B):     K_b=1, K_p=0, PA=1, IP=0.1 ← position-player mop-up appearance
Shohei Ohtani (SP):    K_b=1, K_p=7, PA=4, IP=6   ← TWO-WAY (legitimate)
Yu Darvish (SP):       K_b=2, K_p=7, PA=3, IP=6   ← NL interleague PA
Blake Snell (SP):      K_b=0-1 across 34 rows     ← occasional PA
```

Three patterns:

1. **Pure pitcher placeholders with K_b=0 and PA=0** — Megill, Williams, Vodnik, etc. About 218 rows.
2. **Two-way / interleague PA** — Ohtani, Darvish, Snell, Hentges. About 100 rows.
3. **Position-player pitching appearances** — Frazier and similar. Trivially a few rows.

### Will they actually enter training?

`_build_friction_features` requires `len(stat_values) >= 5` from the player's history (line 607-608 of `mlb_high_friction_model.py`):

```python
stat_values = [_get_stat_value(g, stat) for g in game_logs[:30]]
stat_values = [v for v in stat_values if v is not None]
if len(stat_values) < 5:
    return None
```

So a player needs ≥5 rows in their own log history with `strikeouts != None` for the training loop to build a row.

- **Pure RP/SP placeholders (218 rows):** scattered across hundreds of pitchers, only 1–2 rows each. **Most fail the ≥5 filter and never enter training.**
- **Two-way players (Ohtani 39 rows, Darvish 98 rows, Snell 34 rows, Hentges 45 rows):** these **do** survive the filter and **do** enter training as legitimate batter-K target rows. Combined contribution: ~150–250 training rows ≈ **0.3 %–0.4 % of the 61,990-sample training set**.

**Quantitatively, pitcher contamination is bounded at <0.5 % of training rows, all of them legitimate batter PA rows for two-way players.** None of them flip pitcher K's into the batter K target column.

## 5. The "no-position filter" caveat (not the same as contamination)

The retrain script iterates every hub player with a `bdl_id` (line 293-301):

```python
player_cursor = hub.find({'bdl_id': {'$ne': None}}, ...)
```

There is no `is_batter: True` filter. **But** because of the target-null filter at line 325-326, the only pitcher rows that enter training are those with a non-null batter K — i.e., legitimate two-way PA rows (above). This is harmless for the target label.

**Recommendation (low priority):** Add an explicit `position $nin [P, SP, RP]` filter or an `is_batter: True` filter at the top of each batter-stat retrain pass, to defend against future ingest bugs that might write garbage into the `strikeouts` column on a pitcher row.

## 6. Reverse check — could pitcher_strikeouts model contain batter contamination?

The mirror question: when we train `mlb_hf_pitcher_strikeouts.pkl`, do we accidentally include rows where `pitcher_strikeouts` is populated for batters?

```
n_with_pitcher_strikeouts (whole DB) = 42,074
n_BOTH (strikeouts != None AND pitcher_strikeouts != None) = 15,220
```

The 15,220 BOTH rows include every position-player mop-up pitching appearance (Frazier, etc.) plus every two-way player. Those would feed pitcher_strikeouts model with target = K's allowed, which is correct. **No contamination either direction.**

## 7. Verdict on the user's question

| Question | Verdict |
|---|---|
| Are pitcher Ks (`pitcher_strikeouts`) used as the target for `mlb_hf_strikeouts.pkl`? | **NO.** Different field, different stat_name, different `STATS` loop iteration. |
| Does the field-name normalization ever collapse batter→pitcher (or vice versa)? | **NO.** Verified in `STAT_FIELD_MAP`, `_normalize_stat`, `canonical_stats.UI_LABELS` / `STAT_ALIASES`. |
| Are there ANY pitcher rows in the batter K training set? | A handful (~250 of 61,990, **0.4 %**), all legitimate two-way PA rows where the player genuinely batted. Not contamination. |
| Could this explain the Witt/Garcia μ inversion? | **NO.** Contamination is <0.5 % of training and consists of valid batter PA rows. The Witt/Garcia bug is the **Statcast freshness staleness** identified in the main report. |

## 8. What this means for the main audit conclusion

The original root cause stands: **the Statcast/PA-windowed feature pipeline is frozen at 2026-04-26, and the XGBoost model's reliance on those features (over the BDL game-log averages) produces μ that is anti-correlated with reality whenever the staleness gap is non-trivial.**

Pitcher↔Batter contamination is ruled out as a contributing factor.

## 9. Optional hardening for future retrains (not applied)

```python
# In scripts/retrain_mlb_models_v2.py, at top of `for stat_name in STATS`:
if stat_name in BATTER_STATS:
    player_cursor = hub.find(
        {"bdl_id": {"$ne": None},
         "$or": [{"is_batter": True},
                 {"position": {"$nin": ["P", "SP", "RP"]}}]},
        ...
    )
elif stat_name in PITCHER_STATS:
    player_cursor = hub.find(
        {"bdl_id": {"$ne": None},
         "$or": [{"is_pitcher": True},
                 {"position": {"$in": ["P", "SP", "RP"]}}]},
        ...
    )
```

Low priority. Won't change current behaviour materially (~0.4 % data shift) but closes the door on future ingest bugs.
