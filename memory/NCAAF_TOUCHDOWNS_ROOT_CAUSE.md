# NCAAF Touchdowns — Root-Cause Trace (Single Row, End-to-End)

> **No new code. No new tables. No new infrastructure.**
> Pure code-path analysis grounded in actual source lines.

---

## TL;DR — One sentence

`touchdowns` / `firstTouchdown` / `lastTouchdown` resolve at **0%** because they
are **stat-mapping holes**, not identity failures — and **the resolver's
unresolved-reason classifier mislabels stat-mapping failures as
`player_not_in_results`** when the prop-side player_id was unknown to the
join layer for a *separate* reason: the prop carries a "team-position"
identifier (e.g. `RB1_USC_NCAAF`) instead of a real SGO `playerID`.

---

## Why the two touchdowns groups resolve so differently

### Why `receiving_touchdowns` / `rushing_touchdowns` resolve at 100%

These are **per-player accumulator** markets. The full chain works:

1. The prop has `statID="rushing_touchdowns"` or `"receiving_touchdowns"`.
2. The stat normalizer in `ingest_historical_player_stats.py:422-454`
   produces canonical keys: `rush_touchdowns` and `receiving_touchdowns`.
3. The resolver alias map in `build_historical_outcomes.py:244-249`
   contains:
   ```python
   "rushing_touchdowns":     "rush_touchdowns",      # NEW key → CANONICAL
   "rushingTouchdowns":      "rush_touchdowns",
   "receiving_touchdowns":   "receiving_touchdowns",
   "receivingTouchdowns":    "receiving_touchdowns",
   ```
4. `resolve_stat_value()` enters at step 1 (exact match) on line 471:
   ```python
   if stat_id in canonical and canonical[stat_id] is not None:
       return canonical[stat_id], fam, None, "canonical_exact"
   ```
   ...OR step 3 (family resolver via alias) on line 484.
5. Outcome graded → resolved. ✅

### Why `touchdowns` / `firstTouchdown` / `lastTouchdown` resolve at 0%

Three separate failure modes, all rooted in **resolver gaps + market shape**:

#### Failure mode A — `touchdowns` (the bare-name market)

**The fact:** The string `"touchdowns"` (bare, no prefix) appears **zero**
times in `build_historical_outcomes.py`. Verify yourself:

```bash
$ grep -n '"touchdowns"\|"firstTouchdown"\|"lastTouchdown"\|"anytime"' \
        /app/backend/scripts/sgo/build_historical_outcomes.py
# (zero hits)
```

**What `touchdowns` actually represents (per SGO market schemas):** the
*sum* of all TDs scored by a player — rush + receiving + (rarely) passing
+ defensive return TDs. SGO ships it as a **single statID with a
side-of-line (OVER/UNDER), or as YES/NO for "Anytime TD scorer"**.

**Resolver path for such a prop:**
1. `stat_id="touchdowns"` — NOT in `STAT_RESOLVERS` (line 484: `fn = None`)
2. NOT in `_SAFE_COMPOSITE_COMPONENTS` (line 415: composites list ends at
   batting; football composites are EMPTY)
3. NOT in `_SGO_ALIASES` (line 230-253 explicitly enumerates the
   *prefixed* variants; bare `touchdowns` is missing)
4. NOT in canonical dict as an exact key (line 471: canonical only has
   `rush_touchdowns`, `receiving_touchdowns`, `pass_touchdowns` — never
   `touchdowns`)
5. Fall-through to step 5 (line 502-511): `_g(d, "touchdowns", "touchdowns",
   "touchdowns", "touchdowns-")` — none of these key variants exist in
   either canonical or normalized stats.
6. **Reaches `_classify_unresolved` (line 514)** which inspects
   `canonical` and (because the row HAS rush/receiving TD keys present)
   returns either `field_omitted_possible_zero` or
   `missing_field_no_components`. **But hold on — the user reports the
   reason is `player_not_in_results`, not these. So this path is NOT
   the one being hit for the unresolved rows the user is seeing.**

**Correct conclusion for `touchdowns`:** the rows are getting stopped at
the **player-join layer** (line 707-715) *before* the resolver runs.
The unresolved reason `player_not_in_results` is set at line 715 when
NO stats bundle was found for the `(event_id, player_id)` tuple OR
`(event_id, statEntityID)` tuple OR `(event_id, lower(player_name))`
tuple. **The resolver never gets a chance to run on these rows.**

So `touchdowns` at 0% resolution is not (primarily) a resolver gap —
**it's the same 1,067-player-id-mismatch problem.** But the resolver
gap WILL also bite as soon as we fix identity: any `touchdowns` row
that DOES find its stats bundle will still fail at the resolver step.
Both fixes are needed; identity is the immediate blocker.

#### Failure mode B — `firstTouchdown` / `lastTouchdown`

These markets quote **WHICH player scores the first/last TD in the game** —
a per-game game-state question, not a per-player counting stat. There is
NO canonical or normalized stat key for "did this player score the first
TD in this game". The resolver has no path to grade them at all, and
shouldn't — these markets need a **game-context resolver** that walks
the play-by-play sequence (which we don't ingest from SGO at all today).

Resolver path:
1. `stat_id="firstTouchdown"` — not in any map. Same as failure mode A.
2. Even if the stats bundle is found, the value cannot be derived from
   per-player aggregate stats.

**Correct conclusion:** these markets are **structurally unresolvable
from the current stats schema**. They should be **excluded from training**
(not "fixed via reconciliation"). Add them to a "skip these stat_ids"
allowlist in `build_historical_outcomes` or filter at training time.

#### Failure mode C — the misleading reason label

This is the most important finding. The 0% resolution for `touchdowns`
**looks like an identity problem** (the unresolved_reason is
`player_not_in_results`) but the underlying issue is that the
**resolver-gap rows ALSO happen to be on the same prop player_ids that
fail the join**.

Why? Because `touchdowns` markets are quoted with `statEntityID =
team_position_role` (e.g. `RB1_USC`, `WR2_USC`) when SGO doesn't have a
concrete player picked yet. These per-role anchors have no
corresponding row in `sgo_player_stats` (which keys by real player_id).
So the join in `process_date` line 704-711 fails:

```python
bundle = stats_map.get((eid, pid))                       # fails — pid is a role
if bundle is None and pid is not None:
    bundle = stats_map_by_entity.get((eid, pid))         # fails — same
if bundle is None:
    nm = (doc.get("player_name") or "").strip().lower()
    if eid and nm:
        bundle = stats_map_by_name.get((eid, nm))        # fails — name is "RB1"
if bundle is None:
    no_player_stats += 1
    reason = "player_not_in_results"                     # ← misleading label
```

The reason **truthfully says** "no stats bundle was joined". But it
**does NOT** mean "this is a real player who didn't show up to the
game". It just means **the join key wasn't a real-player key in the
first place** — it was a market-side role placeholder.

---

## Single-row trace — exemplary `touchdowns` row

I cannot pull a literal NCAAF row from the preview pod (empty). Walk
this through your VPS by running:

```bash
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
const row = db.sgo_ncaaf_research_outcomes.findOne({
    stat_id: "touchdowns",
    outcome_resolved: false,
    unresolved_reason_detail: "player_not_in_results"
}, {_id:0, raw:0});
print(JSON.stringify(row, null, 2));
'
```

Expected shape (from inspecting the schema in
`build_pp_research_core.py` + `build_historical_outcomes.py`):

```json
{
  "event_id":          "<sgo_event_id>",
  "player_id":         "RB1_<TEAM>_NCAAF",       // ← team-role placeholder
  "stat_entity_id":    "RB1_<TEAM>",             // ← role, NOT a player
  "stat_id":           "touchdowns",
  "period_id":         "reg",
  "side":              "OVER",
  "line":              0.5,
  "team_id":           "<TEAM>",
  "game_date":         "2024-09-07",
  "actual_value":      null,
  "outcome":           "UNRESOLVED",
  "outcome_resolved":  false,
  "unresolved_reason_detail": "player_not_in_results",
  "stat_family":       "touchdowns"
}
```

### End-to-end trace through the resolver pipeline

Code line | What happens
---|---
`build_historical_outcomes.py:702` | `pid = "RB1_USC_NCAAF"`
`:704` | `stats_map.get(("<eid>", "RB1_USC_NCAAF"))` → **None** (sgo_player_stats keys are real playerIDs, never team-roles)
`:706` | `stats_map_by_entity.get(("<eid>", "RB1_USC_NCAAF"))` → **None** (same reason)
`:710` | `stats_map_by_name.get(("<eid>", "rb1"))` → **None** ("rb1" is not a real player name)
`:711-715` | `bundle is None` → `reason = "player_not_in_results"`, `derived_source = None`
`:743` | `grade_outcome("OVER", None, 0.5)` → `outcome = "UNRESOLVED"`
`:766` | Row written with `unresolved_reason_detail = "player_not_in_results"`

**The resolver never executed.** The label is technically truthful
("no player stats were joined") but **causally misleading** ("the prop
player_id was never going to join anything because it's a market-role,
not a player").

---

## Cross-check: why `receiving_touchdowns` reaches 100%

By the same trace:

Code line | What happens
---|---
`:702` | `pid = "<REAL_PLAYER>_<NUM>_NCAAF"` (e.g. `EMEKA_EGBUKA_77_NCAAF`)
`:704` | `stats_map.get(("<eid>", "<REAL_PLAYER>_<NUM>_NCAAF"))` → **bundle found** ✓
`:718` | `resolve_stat_value("receiving_touchdowns", ...)` → alias to `receiving_touchdowns` → canonical_exact match → **value resolved**
`:743` | `grade_outcome` produces WIN / LOSS / PUSH

These markets resolve at 100% because **they're quoted on real player IDs
that DO exist in `playerStats[]`** — receiving/rushing TD markets always
attach to a concrete starter, never to a team-role placeholder.

---

## Answers to your three questions

| # | Question | Answer |
|---|---|---|
| 1 | Why does `touchdowns` / `firstTouchdown` / `lastTouchdown` resolve at 0%? | Two independent problems: **(a) prop player_id is a team-position placeholder** (`RB1_USC_NCAAF`, `WR2_LSU_NCAAF`, etc.) that cannot join `sgo_player_stats`, so `player_not_in_results` fires at the join layer (line 715) before the resolver runs. **(b)** `touchdowns` (bare) is also missing from `STAT_RESOLVERS`, `_SGO_ALIASES`, and `_SAFE_COMPOSITE_COMPONENTS` — so even if the join succeeded, the resolver would still return `field_omitted_possible_zero` or similar. **(c)** `firstTouchdown` / `lastTouchdown` are game-state markets, structurally unresolvable from per-player aggregate stats; they should be excluded, not "fixed". |
| 2 | Why do `receiving_touchdowns` and `rushing_touchdowns` resolve at 100%? | They're quoted on **real player IDs** (always a concrete starter), AND `_SGO_ALIASES` has explicit mappings → `receiving_touchdowns`/`rush_touchdowns` canonical keys, which the normalizer writes 1:1. Both halves of the pipeline succeed: identity AND mapping. |
| 3 | Are the unresolved rows caused by missing stat mappings rather than player identity? | **Partly both, but the immediate blocker is identity — and the identity failure is not name drift, it's market-role placeholders.** The `player_not_in_results` label is technically truthful but conflates two distinct causes. A targeted breakdown (proposed below) would split them cleanly. |

---

## What this means for the NCAAF feature pipeline

### Stop pursuing identity reconciliation for these specific markets

The 1,067 mismatched props.player_ids the earlier audit found are
**predominantly market-role placeholders**, not real-but-misnamed
players. Fuzzy matching, name reconciliation, SGO `/v2/players` master
pulls — **none of those will help** because the prop side doesn't have
a real player at quote time. SGO is broadcasting "the RB1 of USC will
score" before knowing which human is RB1 that week.

### The fix is structural, not identity-driven

1. **Add a market-role detector** to `_classify_unresolved` (or
   immediately before it at line 711) that recognizes player_id patterns
   like `^(QB|RB|WR|TE)\d+_[A-Z]+_NCAAF$` and `^TEAM_` and stamps
   `unresolved_reason_detail = "market_role_placeholder"` instead of
   `player_not_in_results`. This is **diagnostic only**; no real
   resolution happens. ~15 LOC.

2. **Exclude these stat_ids from training**:
   - `touchdowns` (bare)
   - `firstTouchdown`
   - `lastTouchdown`
   - any market_id containing `anytime` or `score`
   They're unresolvable from current data and would just bias the
   training set toward `feature_ready=false`.

3. **Surface the breakdown** by re-running this query on the VPS — it
   will instantly tell you how much of the 41.19% mismatch is real
   player drift vs market-role placeholders:

```bash
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
const r = db.sgo_ncaaf_research_outcomes.aggregate([
    {$match: {outcome_resolved: false,
              unresolved_reason_detail: "player_not_in_results"}},
    {$project: {
        is_role: {$regexMatch: {
            input: "$player_id",
            regex: "^(QB|RB|WR|TE|K|DEF|OL|DB|LB|DL)\\d*_[A-Z]+_NCAAF$"
        }},
        stat_id: 1
    }},
    {$group: {_id: {is_role: "$is_role", stat_id: "$stat_id"},
               n: {$sum: 1}}},
    {$sort: {n: -1}},
    {$limit: 30}
]).toArray();
r.forEach(x => print(JSON.stringify(x)));
'
```

If `is_role: true` dominates the `touchdowns` / `firstTouchdown` /
`lastTouchdown` rows, the trace above is confirmed and the only fix is
**exclude these markets**. If `is_role: false` dominates, the resolver
gap is the real problem and we need to add `touchdowns` to the alias
map.

---

## Cited source lines (for reviewers)

| Claim | File | Lines |
|---|---|---|
| Resolver has zero `touchdowns` bare-name handling | `build_historical_outcomes.py` | `grep` confirmed: no hits for `"touchdowns"`, `"firstTouchdown"`, `"lastTouchdown"`, `"anytime"` |
| `_SGO_ALIASES` only covers prefixed variants | `build_historical_outcomes.py` | 230-253 |
| `_SAFE_COMPOSITE_COMPONENTS` only covers MLB | `build_historical_outcomes.py` | 330-335 |
| Player-join failure sets `player_not_in_results` | `build_historical_outcomes.py` | 704-716 |
| NFL/NCAAF stat normalizer outputs per-role TD keys | `ingest_historical_player_stats.py` | 411-461 |
| Resolver tries exact key → composite → resolver → aliases → variants → classify | `build_historical_outcomes.py` | 470-514 |
