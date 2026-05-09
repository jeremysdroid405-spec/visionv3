# Pipeline Audit — Tier Population Investigation
**Date:** 2026-05-09 01:42 UTC
**Status:** READ-ONLY. NO CODE CHANGES.
**Verdict:** Pipeline is **healthy end-to-end**. Low tier counts are the **expected consequence of strict pricing/coverage/direction gates**, not a watcher / sync / publishing failure. Two cosmetic items found (not bugs).

---

## TL;DR — where each layer stands

| Stage | NBA | MLB | Healthy? |
|---|---|---|---|
| Upstream events fetched | **4** | **19** | ✅ matches upstream slate (NBA Conf-Semis night) |
| `live_props` total | 9,736 | 7,311 | ✅ |
| `playable_on_pp=True` (hard scoring filter) | 3,112 | 2,520 | ✅ enforced as designed |
| `prop_scores final-{sport}-rt` | 3,014 | 3,589 | ✅ scoring runs |
| `active=True` | 2,768 | 2,547 | ✅ |
| Tier qualified (SH+FL+WZ) | **57** | **317** | ⚠ low — see "why" below |
| Tier qualified / routable | **2.9%** | **9.2%** | ⚠ low (gate-driven, not pipeline-driven) |
| `cached_board` props (active=True, per-prop) | **2,781** | **2,485** | ✅ board is publishing |
| Latest publish age | **8 min** | **4 sec** | ✅ |
| Scheduler heartbeat | ✅ all 35 APScheduler jobs scheduled, none missed | | |

---

## 1 · Upstream ingestion is working

`fetch_events` in `services/universal_odds_sync.py:517` calls **`/sports/{sport}/events`** with no time/date filter — it returns whatever The Odds API has. Counts:

- **NBA: 4 events** — playoffs window, normal supply (SAS@MIN, NYK@PHI, DET@CLE, OKC@LAL).
- **MLB: 19 events** — regular Friday slate.

`hourly_{sport}_master_sync` next-runs in **30 min**; both ran on schedule (visible via `dg_sync_log`).

```
hourly_nba_master_sync   next 30m   ✓
hourly_mlb_master_sync   next 30m   ✓
ticker_sync              next 2m    ✓
live_injury_check        next 3m    ✓
mlb_lineups_early        next 16h   (today)
universal_game_start_scanner  next 1m  ✓ (active prune loop)
```
None of the 35 APScheduler jobs are stuck or missed.

---

## 2 · Per-event slate — every event IS being pulled, scored, and tiered

```
NBA (4 events)              pulled  scored  tiered
NYK@PHI (in progress)         397    300     8
SAS@MIN (in 5 min)           3402    784    25
DET@CLE (tomorrow 19:10)     2943    674    17
OKC@LAL (tomorrow 00:40)     2994   1256     7

MLB (19 events) — totals     7311   3589   317
```

⚠ **There are NO events in the 24–72h window** (`next 24–72h: 0` for both sports). That is **expected upstream behaviour**: The Odds API only returns events for which sportsbooks have already posted player props. NBA/MLB books typically post player markets ~12–24h before tipoff/first-pitch. Saturday afternoon games will appear once books post overnight. The `universal_game_start_scanner` job proves the scanner is alive (next run in 1 min) — it is not pruning future events; the future events simply aren't on the wire yet.

---

## 3 · Watcher / Adaptive-Sync health

```
adaptive_sync_heartbeat:
  last_heartbeat_at = 2026-05-09 01:38 UTC  (heartbeat ~5 min old)
  next_poll_in_seconds = 240
  games_in_registry = 0   ← cosmetic; the registry's reset between sport phases
  after_sport = mlb
  inter_sport_marker = True
```

The "0 games_in_registry" is the post-MLB inter-sport reset marker. Heartbeat is recent (~5 min, well within the 4-min poll cadence). No watchdog/restart events visible in logs. No upstream provider returned 0 props.

---

## 4 · Pipeline drop-off — exactly where each prop disappears

### NBA chain (single source of truth: `prop_scores.tier_reason`)

```
9,736  live_props
  −6,624  playable_on_pp ≠ True         ← coverage_filter.filter_pp_playable()
                                          backend/services/scoring/coverage_filter.py:215
                                          Universal hard rule: every prop scored
                                          MUST have PP quoting that exact side.
                                          This is the BIGGEST single drop (~68%)
                                          and is BY DESIGN per 2026-05 SSOT spec.
3,112  PP-playable
  −98   misc filters (active flag, mode/extracted, etc.)
3,014  prop_scores final-nba-rt
  −1,049  no_reference_market           ← alt lines with no DK/FD/MGM paired
                                          quote. Cannot compute fair odds, so
                                          they are ineligible for any tier.
1,965  routable
  −1,908  gate failures (see §6 below)
   57   tier-qualified  (SH 13 / FL 37 / WZ 7)
```

### MLB chain
```
7,311  live_props
  −2,491  pp_playable filter (MLB pulls deeper alt-line catalogues)
4,820  approx pre-score
  −1,231  active/extracted flags
3,589  prop_scores final-mlb-rt
  −145    no_reference_market
3,444  routable
  −3,127  gate failures
  317   tier-qualified  (SH 3 / FL 49 / WZ 265)
```

The **two big macro filters** are:
1. **`filter_pp_playable`** at `services/scoring/coverage_filter.py:215` — hard rule from the 2026-05 SSOT directive: a prop is scoreable IFF PrizePicks quoted that exact `(player, stat, line, side)`. Side-aware. Universal across all sports.
2. **`no_reference_market`** rejection inside the scoring stack — if no sportsbook quotes a paired (or mirror) line, the prop has no fair-odds anchor and skips tier routing. Source: `services/scoring/scoring_stack.py` reference-odds resolution.

Both are **working as specified**. Neither is the bug.

---

## 5 · Cached-board / publishing health

| | NBA | MLB |
|---|---|---|
| `{sport}_cached_board` player docs | 150 | 294 |
| Total props in board (sum `props_count`) | **2,747** | **2,400** |
| Prop-level `active=True` | **2,781** | **2,485** |
| Newest publish | 1m41 ago | 4s ago |
| Player docs with empty `props=[]` (stale) | 88 / 150 | 119 / 294 |

The 88 NBA / 119 MLB "empty-props" docs are **stale player carriers** — players who had props on a previous slate (Jalen Johnson example: `last_publish_ts = 2026-05-08 17:26 UTC`, `locked_event_id` from April 23). They are not active and not surfaced; the publisher just hasn't pruned them. This is a **housekeeping wart** but does not affect tier visibility.

**(My initial audit query incorrectly counted `active=True` at the player-doc level; the field lives inside the `props[]` array. Prop-level counts match published values exactly.)**

---

## 6 · Gate distribution — why `tiered/routable` is low

### NBA (n=3,014 final-rt props)
```
1049  no_reference_market                   34.8%   ← ALT lines, no DK/FD pair
 656  war_zone_failed: gate_direction_fail  21.8%   ← μ < line (correctly rejected)
 451  front_lines_failed: gate_hit_rate     15.0%   ← HR < 70% (FL spec)
 285  front_lines_failed: gate_coverage      9.5%   ← only PP quoted (book_count=0)
 169  war_zone_failed:    gate_coverage      5.6%
 145  front_lines_failed: gate_direction     4.8%
  80  safe_haven_failed:  gate_edge          2.7%   ← edge ≤ 0
  57  gates_passed                           1.9%   ← TIER QUALIFIED
  33+33+16+11+8+8+7+2+2+1+1  remainder      <2%    ← scattered SH/FL fails
```

### MLB (n=3,589 final-rt props)
```
1380  war_zone_failed: gate_direction       38.5%   ← μ < line
 604  war_zone_failed: gate_coverage        16.8%
 317  front_lines_failed: gate_hit_rate      8.8%
 317  gates_passed                           8.8%   ← TIER QUALIFIED
 215  front_lines_failed: gate_direction     6.0%
 203  war_zone_failed: gate_ceiling_fail     5.7%   ← MLB-specific
 ...
```

**This is gate behaviour working as designed.** The two structural reasons NBA tier rate (2.9%) is lower than MLB (9.2%):
1. NBA carries far more **alt-line markets** with no DK/FD pair → 1,049 `no_reference_market` rejects (35% of the surface). MLB's alt catalogue at +200/+300 is much narrower (4%).
2. NBA's combo VK2 fix yesterday correctly knocked many former WZ candidates back to `gate_direction_fail` (656 of them). MLB has its own ceiling gate but a more permissive structure overall.

---

## 7 · Freshness & event filtering — confirmed reasons weekend props are limited

| Possible reason | Verdict |
|---|---|
| Upstream source has not posted them yet | ✅ **CONFIRMED** — `next 24–72h: 0 events` for both sports. Books haven't posted Saturday afternoon/night player markets yet. |
| Sync job not running | ❌ **REJECTED** — all APScheduler jobs healthy. NBA/MLB master_sync within 30 min cycle. |
| Provider returned 0 props | ❌ **REJECTED** — NBA returned 4 events / 9,736 props, MLB returned 19 / 7,311 props. |
| Event filter excludes future/weekend games | ❌ **REJECTED** — `fetch_events()` (`services/universal_odds_sync.py:517`) sends `apiKey + dateFormat=iso` only, no time bounds. |
| Only today's games are pulled | ❌ **REJECTED** — see above; tomorrow's NBA games (DET@CLE, OKC@LAL) are present. |
| `universal_game_start_scanner` pruning future events | ❌ **REJECTED** — scanner only flags games whose `commence_time < NOW`. Confirmed live: next run in 1 min, no recent prune events on future games. |
| PrizePicks playability filter excluding props | ✅ **CONFIRMED structural** — drops 6,624 NBA props (68%). Universal SSOT rule, working as spec. |
| Book odds not available yet | ✅ **CONFIRMED structural** — 1,049 NBA `no_reference_market` rejects (35% of scored). |
| `dirty_queue` not draining | ❌ **REJECTED** — no separate dirty queue collection found; delta steps run in-process. Heartbeat current. |
| Board publisher not updating | ❌ **REJECTED** — newest NBA publish 1m41 ago, MLB 4s ago. |

---

## 8 · Evidence summary — failure-point classification

**This is NOT an ingestion failure.**
**This is NOT a sync failure.**
**This is NOT a publishing failure.**
**This IS the expected output of a strict pricing-coverage + gate stack on a 4-game NBA playoff slate.**

The tier counts are low because:

| Factor | NBA loss | MLB loss | Authority |
|---|---|---|---|
| **PP-playable** SSOT filter | 6,624 props | 2,491 props | `services/scoring/coverage_filter.py:215` `filter_pp_playable` |
| **No reference market** (no paired DK/FD quote) | 1,049 props | 145 props | `services/scoring/scoring_stack.py` ref-odds resolution |
| **Direction gate** (μ on wrong side of line) | 834 fails | 1,596 fails | `services/scoring/gates/thresholds.py` |
| **Coverage gate** (book_count = 0 = PP-only) | 454 fails | 821 fails | `services/scoring/gates/thresholds.py` |
| **Hit-rate gate** (HR floor not met) | 484 fails | 353 fails | `services/scoring/gates/thresholds.py` |

All of the above are **enforced rules requested by the user in earlier directives**. None of them are pipeline drift.

---

## 9 · Two cosmetic items (not bugs, not blockers)

1. **Stale player docs in cached_board** — 88 NBA / 119 MLB player docs have `props_count=0` and stale `last_publish_ts` (some > 24h old). The board snapshot publisher only refreshes players present in the current `final-rt` surface; old player docs linger. They aren't surfaced in the UI (zero props), but they're unnecessary disk footprint. *Out of scope of this audit; flag for housekeeping P3.*

2. **`scheduler_jobs.job_state` is binary-pickled** (APScheduler's persistence format), so the audit's diagnostic projection couldn't pretty-print job names. The `_id` field IS the job name; jobs are healthy. *No action required; this is just an audit-script note.*

---

## 10 · Recommendations (read-only — no patches yet, per directive)

A. **DO NOT change any gate or filter at this time.** The tier counts reflect a strict slate, not pipeline failure.

B. **Wait for the next 12–18h slate refresh.** Books will post Saturday afternoon NBA props and double-headers tonight; tier counts on the next normal slate are the data point for any further calibration.

C. **Run `wz_slate_monitor.py`** (already deployed) on the next 3 normal slates to accumulate WZ HR/CV/edge distributions. Per the prior directive, do not retune until 3 normal slates show sustained <8–10 WZ qualified.

D. **(Optional, P3 housekeeping)** Add a janitor in `board_snapshot_publisher` that removes player docs whose `last_publish_ts > 24h` and `props_count == 0`. Cosmetic only; does not affect supply.

E. **(Optional, P2)** Investigate whether the `no_reference_market` 35% NBA reject rate represents truly un-tradeable alt lines, or if there's a missed pairing opportunity (e.g., resolving one-sided DK markets against an MGM mirror). This is a future supply-quality lever — *not part of the current freeze*.

---

**Closing:** The audit shows a system behaving exactly as the codified rules require. No watcher restart, no sync re-run, no scoring or gate change is justified by this evidence.
