# NBA War Zone Alt-Line Projection Audit — Read-Only Findings

**Date:** 2026-05-09
**Scope:** Audit `model_projection` validity for `final-nba-rt` props that were routed to War Zone (ref_odds ≥ +150) but rejected by gates. Particular focus on inflated `projection / line` ratios on alt lines.
**Methodology:** Trace `model_projection` source → reproduce it offline via `nba_scoring.py` predictors against live game logs (Jarrett Allen, bdl_id=9 — user's reference example).
**Status:** ROOT CAUSE IDENTIFIED. NO CODE CHANGES MADE.

---

## 1. User-flagged example

> "Jarrett Allen PA alt 9.5 has projection / line ≈ 2.30 — looks inflated."

**Confirmed in DB:**

| canonical_key | stat_type | line | side | model_projection | proj / line | tier_reason |
|---|---|---|---|---|---|---|
| `nba\|…\|Jarrett Allen\|player_points_assists_alternate\|9.5\|OVER` | PA alt | 9.5 | OVER | **21.89** | **2.30** | safe_haven_failed: gate_cv_fail |

The 2.30 ratio is real. The reject reason is `safe_haven_failed: gate_cv_fail` — i.e. the prop **passed** direction / edge / hit-rate / vision-score, only failed Safe Haven's strict CV ≤ 0.40 ceiling.

---

## 2. The actual root cause — combo synth is using LEGACY VK1, not VK2

Allen's true rolling projections (verified against `bdl_game_logs`):

| stat | VK1 (legacy) | VK2 (current primary) | True L20 mean |
|---|---|---|---|
| PTS | **19.62** ❌ | 11.75 ✅ | 12.12 |
| REB | 9.28 | 8.67 ✅ | 8.67 |
| AST | 2.40 | 1.20 ✅ | 1.20 |
| PRA (direct model) | 25.42 ❌ | 20.35 ✅ | 21.99 |

**Allen averages ~12 PTS / 8.7 REB / 1.2 AST per game over L20.** The legacy VK1 PTS regressor is over-projecting him by **+7.5 points** (≈+62 %). That single dead model is pumping every PTS-bearing combo (PA, PR, PRA-synth) to a wildly inflated number.

Reproduced live in `nba_scoring._predict_combo_projection`:

| family | components | VK1 result (current LIVE) | VK2 result (correct) |
|---|---|---|---|
| `pts_ast` (PA) | PTS + AST | **22.02** ❌ | 12.95 ✅ |
| `pts_reb` (PR) | PTS + REB | **28.90** ❌ | 20.42 ✅ |
| `reb_ast` (RA) | REB + AST | 11.68 (+1.8) | 9.87 ✅ |

The DB `final-nba-rt` rows match the VK1 column exactly:
- PA (any line) → 21.89 → off by **+8.6**
- PR (any line) → 28.73 → off by **+8.0**
- RA (any line) → 11.67 → off by **+1.8**

---

## 3. Why combo families fall back to VK1 (the code path)

`backend/services/scoring/adapters/nba_scoring.py`:

```python
# line 80
_FAMILY_TO_MODEL_KEY = {
    "pts": "PTS", "reb": "REB", "ast": "AST",
    "pra": "PRA", "threes": "3PM",
    # 'pts_ast', 'pts_reb', 'reb_ast' INTENTIONALLY ABSENT — no direct model
}

# line 2672–2673 (inside _score)
resolved_family = self._resolve_family(stat_type) or ""
model_key = self._FAMILY_TO_MODEL_KEY.get(resolved_family)   # None for PA/PR/RA

# line 2739–2741
use_vk2_path = (active_method_early == "vk2") or (
    model_key in self._VK2_PRIMARY_STATS and not explicit_legacy
)
# model_key=None → None ∉ {"AST","REB","3PM"} → use_vk2_path = False
```

Then at line **3094** (the `elif resolved_family in self._COMBO_COMPONENTS:` branch):

```python
cres = self._predict_combo_projection(
    db=db, bdl_player_id=bdl_player_id, ...,
    use_vk2=use_vk2_path,                       # ← False for ALL combo families
    components=self._COMBO_COMPONENTS[resolved_family],
    ...
)
```

`_predict_combo_projection` then calls `_predict_model_prob_over` (legacy VK1) for each component. **The new VK2 PTS / REB / AST models — which the standalone PTS / REB / AST props ARE using — are bypassed entirely on combo families.**

This is a routing oversight from the 2026-04-28 VK2 primary-path promotion (only single-stat promotions were added; combo families were left on legacy VK1).

---

## 4. Downstream blast radius

Every NBA prop on a combo market (`player_points_assists*`, `player_points_rebounds*`, `player_rebounds_assists*`) is currently scored against an inflated `model_projection`. That inflation cascades into:

| field | impact |
|---|---|
| `model_projection` | +30 % to +60 % too high (PTS-bearing combos) |
| `distribution_p_over` | falsely elevated → high `p_true_model` |
| `vision_projection_component` | falsely elevated (`(proj − line) / sigma`) |
| `vision_v2_direction_margin` | falsely large → high vision_score_v2 |
| `vision_score` | falsely elevated → many phantom "high score" picks |
| `tier_routing` | irrelevant: gates run with wrong μ; CV / HR catch some but not all |

**1,082** WZ-routed final-nba-rt props are currently rejected; the combo families are over-represented in this set because they all consume Safe Haven's CV budget on the wrong side of the math.

It also affects **Safe Haven and Front Lines combo picks that ARE qualifying** — they're qualifying for the wrong reason. For example:

| Player | Stat | Line | Tier | Live μ | Correct μ (VK2) |
|---|---|---|---|---|---|
| Jarrett Allen | `player_points_rebounds` | 18.5 OVER | front_lines reject (HR fail) | 28.73 | 20.42 |
| Jarrett Allen | `player_points_assists_alternate` | 14.5 OVER | **war_zone PASS** | 22.13 | ~12.95 |
| Jarrett Allen | `player_points_rebounds_alternate` | 14.5 OVER | safe_haven reject (HR fail) | 28.79 | 20.42 |

The `war_zone PASS` row at line 14.5 is a fake winner — a real μ of 12.95 is **below** the 14.5 line. That's a directional flip on a published WZ pick.

---

## 5. NOT a bug

Items that look like bugs but check out:
- **`is_alternate_market` field is `None`** on score docs — irrelevant; the stat_type already encodes `_alternate` and the gates resolve it via `STAT_FAMILY_ALIASES`.
- **PRA direct-model μ = 20.35** (correct; rate × minutes blended, expected for Allen).
- **CV / hit-rate / sigma math** — correct.
- **War Zone routing band** (`ref_odds ≥ +150`) — correct per universal config.

---

## 6. Proposed fix (NOT applied — awaiting your approval)

**Surgical 4-line patch** in `backend/services/scoring/adapters/nba_scoring.py`:

In `_score`, just before line 2739 (`use_vk2_path = …`), add:

```python
# Combo families (pts_ast / pts_reb / reb_ast) have no direct model
# but their components DO have VK2 primary models. Route combo synth
# through VK2 so the components match what standalone PTS/REB/AST use.
if resolved_family in self._COMBO_COMPONENTS and (
    not explicit_legacy
):
    # at least one component is in VK2 primary set → prefer VK2
    components = self._COMBO_COMPONENTS[resolved_family]
    if any(c in self._VK2_PRIMARY_STATS for c in components):
        # forces use_vk2_path = True below
        active_method_early = "vk2"
```

Effect:
- PA / PR / RA / RA combo synth → uses VK2 components for both projection and sigma.
- Standalone PTS / REB / AST / 3PM unchanged.
- PRA direct model unchanged (synth-preferred fallback already on VK2 since `pra ∈ _SYNTH_FALLBACK_COMPONENTS` and use_vk2 there is keyed off `model_key="PRA" ∈ _VK2_PRIMARY_STATS`? — actually PRA isn't in _VK2_PRIMARY_STATS yet; verify before patch).

**Tests to add (as required by Master Architecture Directive):**
1. `tests/test_combo_synth_vk2_routing.py` — assert PA/PR/RA combo synth uses VK2 components for a fixture player.
2. Regression assertion: Allen PA alt 9.5 OVER produces μ ≈ 13 ± 1, not 21.89.
3. Mutation test: flip `_VK2_PRIMARY_STATS` to empty → combo synth falls back to VK1 (current behaviour) → projections inflate by >40 % for verified low-usage players.

**Expected re-score impact (rough estimate):**
- ~**400–600** combo-family final-nba-rt props will see μ drop 30 %+ → many will flip OVER → UNDER on direction gate, OR drop tier from war_zone/front_lines/safe_haven to unqualified.
- A handful of "previously qualifying" WZ combo picks (like Allen PA alt 14.5) will drop out — that is the **correct** behaviour.
- **No threshold changes required.** This is strictly a μ-correctness fix.

---

## 7. Sign-off requested

You asked me to NOT touch `vision_score_v2` thresholds, the WZ band, or any gate config until the projection root cause was confirmed.

**Confirmed:** the WZ-rejects table is NOT a gate-tuning problem. It is a μ source-of-truth problem, isolated to combo-family scoring. Specifically:

- **Affected:** `pts_ast`, `pts_reb`, `reb_ast` families (both standard + alternate markets).
- **Severity:** μ inflated by up to +62 % on PTS-bearing combos for low-usage players.
- **Cause:** `use_vk2_path = False` whenever `model_key=None`, which is precisely the combo-family case.

**Awaiting your decision on:**
- (a) Apply the surgical 4-line VK2-routing fix + add the 3 regression tests, run `recompute` on `final-nba-rt`, deliver a before/after diff of the WZ rejects table.
- (b) Hold for further investigation (e.g., quarantine combo families from WZ entirely until verified end-to-end).
- (c) Other directive.
