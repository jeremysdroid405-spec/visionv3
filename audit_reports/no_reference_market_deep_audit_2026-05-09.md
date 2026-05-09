# NBA `no_reference_market` Deep-Dive Audit
**Date:** 2026-05-09
**Mode:** READ-ONLY (no patches, no filter changes, no scoring touched)
**Universe:** All NBA `final-nba-rt` props with `tier_reason=no_reference_market` (n = 1,153 at audit time, slate is live so count drifts by ±50)

---

## TL;DR (1-line root cause)

> `_pick_reference_odds` in `services/scoring/scoring_stack.py:377-382` consults **only DK and MGM** for NBA. FanDuel and BetOnline odds — which exist in the data layer for **84.7%** of these rejects — are never read for NBA. **MLB has the fix; NBA does not.**

This is **adapter / matching logic**, not market structure.

---

## 1 · Stat-type breakdown (top 14 of 22 stat families)

| stat_type | n | avg_line | min | max | sample players |
|---|---:|---:|---:|---:|---|
| `PTS` | 232 | 15.81 | 0.50 | 49.50 | Maxey, Wembanyama, Castle |
| `player_rebounds_assists_alternate` | 167 | 10.03 | 0.50 | 21.50 | Wemby, Gobert |
| `PRA` | 139 | 27.01 | 7.50 | 59.50 | Hart, Gobert, Castle |
| `player_points_rebounds_alternate` | 83 | 17.10 | 3.50 | 36.50 | Naz Reid, Wemby, Castle |
| `3PM` | 82 | 2.73 | 0.50 | 7.50 | Dosunmu, McDaniels, Castle |
| `REB` | 78 | 6.86 | 1.50 | 15.50 | Gobert, KAT, Paul George |
| `AST` | 64 | 4.50 | 0.50 | 11.50 | Wemby, Vassell, KAT |
| `player_points_assists_alternate` | 50 | 19.76 | 7.50 | 35.50 | Wemby, Fox, Randle |
| `STL` | 49 | 1.38 | 0.50 | 3.50 | Conley, Vassell |
| `player_points_assists` (std) | 28 | 20.79 | 8.50 | 32.50 | Maxey, LeBron, Harper |
| `player_points_rebounds` (std) | 26 | 20.50 | 9.50 | 33.50 | Grimes, Maxey, Edgecombe |
| `player_rebounds_assists` (std) | 20 | 9.70 | 4.50 | 16.50 | Bridges, Embiid |
| `player_points_q1` | 18 | 4.72 | 2.50 | 8.50 | PG, Embiid |
| `BLK` | 14 | 1.86 | 0.50 | 4.50 | Holmgren, Allen, Gobert |
| `team_totals*` (5 markets) | 24 | — | — | — | NYK, PHI |

**Standard vs alt:** 776 standard / 302 alt → **NBA's biggest reject share is on STANDARD lines that PrizePicks anchors and that DK does NOT quote.**

**Side split:** 943 OVER / 135 UNDER (the OVER bias is structural — alt-line markets are quoted OVER-side by books).

**`book_count` distribution on the rejected docs themselves:**
- `pp_only` (book_count = 0) : 485 (45 %) — 100% truly unanchored at the score doc
- `single_book` (book_count = 1) : 438 (41 %) — one book quoted, just not DK/MGM
- `multi_book` (book_count ≥ 2) : 155 (14 %) — **two or more books quoted, still rejected**

The third row alone (155 props with multi-book coverage **AND** `no_reference_market`) is the canary: those props have ≥2 sportsbook anchors and STILL get tier-rejected. Pure routing-chain bug.

---

## 2 · Book-odds availability (where the markets actually are)

Per top stat family, fetched directly from `nba_live_props`:

| stat | n rejects | DK present | FD present | MGM present | BOL present |
|---|---:|---:|---:|---:|---:|
| PTS | 232 | **0** | **224** | 0 | 61 |
| `player_rebounds_assists_alternate` | 167 | 0 | 97 | 0 | 125 |
| PRA | 139 | 0 | 109 | 0 | 49 |
| `player_points_rebounds_alternate` | 83 | 0 | 83 | 0 | 0 |
| 3PM | 82 | 0 | 82 | 0 | 1 |
| REB | 78 | 0 | 78 | 0 | 2 |
| AST | 64 | 0 | 60 | 0 | 9 |
| `player_points_assists_alternate` | 50 | 0 | 50 | 0 | 0 |

**The pattern is identical across every family: zero DK / zero MGM, but FD and/or BOL DO have the line.** This is not a hydration gap (the data is in `nba_live_props`); it is a chain-resolution gap (`_pick_reference_odds` ignores FD/BOL for NBA).

---

## 3 · Pair-recovery audit — 5-bucket same-line classification

For every reject, I queried `nba_live_props` for rows with the **same event + player + stat_type + exact line** (any side, any playability flag), then classified:

| Bucket | n | % | Recoverable? |
|---|---:|---:|---|
| **A1** — opposite side has book quotes (both-side reference for de-vig) | **654** | **56.7 %** | ✅ Yes |
| **A2** — same side has book quotes on a `playable_on_pp=False` row | **323** | **28.0 %** | ✅ Yes |
| **B** — exact-line live row exists but no book ever quoted | 104 | 9.0 % | ❌ No |
| **C** — no live row at all (likely stale score doc) | 72 | 6.2 % | ❌ No |

> **84.7 %** of NBA `no_reference_market` rejects (A1+A2 = 977 / 1,153) have a same-line book quote in `nba_live_props` already — they are just not seen by the NBA reference-odds chain.

---

## 4 · Root cause — exact file and function

`backend/services/scoring/scoring_stack.py`, function `_pick_reference_odds` (lines 317–382):

```python
sport_lc = (sport or "").lower()
if sport_lc == "mlb":
    # Full chain — DK + FD consensus, then DK → FD → MGM → BOL
    ...
    if fd_odds is not None:
        return fd_odds, "fd"
    if mgm_odds is not None:
        return mgm_odds, "mgm"
    if bol_odds is not None:
        return bol_odds, "bol"
    return None, "none"

# NBA / default — unchanged.       ← THE BUG
if dk_odds is not None:
    return dk_odds, "dk"
if mgm_odds is not None:
    return mgm_odds, "mgm"
return None, "none"
```

The NBA branch only checks DK and MGM. FanDuel and BetOnline odds — which `compute_tier` already receives via `fd_layer` and `bol_layer` parameters (line 886–894 of the same file) — are **discarded** at this step.

The MLB branch was extended on 2026-04-27 with the comment:

> _"DK is missing on FD-only families such as Runs / Stolen Bases / a long tail of pitcher props. Without this fallback, those props always resolve to routed_tier=None and never reach the gate stage."_

**The same condition exists for NBA**: DK is missing from FD-priced standard lines and from BOL-priced alts. The NBA branch was never updated to match.

---

## 5 · 25 example rejects (per spec)

### 5a · 10 likely-recoverable (book quote exists at same line)

| # | Reject | Companion in `nba_live_props` |
|---|---|---|
| 1 | Tyrese Maxey PTS 17.5 UNDER | OVER side `playable=False` `dk=−140 fd=+102` |
| 2 | Anthony Edwards PTS 22.5 OVER | UNDER side `playable=True` `bol=−118` |
| 3 | Anthony Edwards PTS 22.5 UNDER | OVER side `playable=True` `bol=−110` |
| 4 | Keldon Johnson PTS 8.5 OVER | UNDER side `playable=True` `fd=−106 bol=+105` |
| 5 | Keldon Johnson PTS 8.5 UNDER | OVER side `playable=True` `fd=−125 bol=−135` |
| 6 | Devin Vassell AST 1.5 OVER | same side `playable=False` `fd=−900 bol=−286` |
| 7 | Jaden McDaniels AST 1.5 OVER | same side `playable=False` `fd=−235 bol=−455` |
| 8 | Ayo Dosunmu AST 1.5 OVER | same side `playable=False` `fd=−320 bol=−278` |
| 9 | Dylan Harper AST 1.5 OVER | same side `playable=False` `fd=−490 bol=−769` |
| 10 | Victor Wembanyama AST 1.5 OVER | same side `playable=False` `fd=−350 bol=−714` |

### 5b · 5 ambiguous (book exists but only at distant line)

| # | Reject | Nearest book row |
|---|---|---|
| 1 | Julius Randle PRA 50.5 OVER | PRA 48.5 `bol=+1200` (Δ 2.0) |
| 2 | Julius Randle PRA 48.5 OVER | PRA 50.5 `bol=+1700` (Δ 2.0) |
| 3 | Julius Randle PRA 42.5 OVER | PRA 40.5 `bol=+370` (Δ 2.0) |
| 4 | Julius Randle PRA 46.5 OVER | PRA 48.5 `bol=+1200` (Δ 2.0) |
| 5 | Deandre Ayton PRA 22.5 OVER | PRA 24.5 `dk=+410 bol=+192` (Δ 2.0) |

These are recoverable only via interpolation — explicitly forbidden by directive (no synthetic odds).

### 5c · 10 clearly unrecoverable

| # | Reject | Reason |
|---|---|---|
| 1 | Josh Hart PRA 29.5 OVER | no live_props row at all |
| 2 | Julius Randle `player_twos_alt` 4.5 OVER | live row exists, no book ever quoted |
| 3 | Terrence Shannon Jr `player_twos_alt` 1.5 OVER | live row exists, no book quoted |
| 4 | Jalen Brunson AST 8.5 UNDER | no live row (PP-only special) |
| 5 | Karl-Anthony Towns AST 6.5 OVER | no live row |
| 6 | Jaxson Hayes AST 0.5 OVER | no live row |
| 7 | Jaxson Hayes AST 0.5 UNDER | no live row |
| 8 | Isaiah Joe `player_rebounds_assists` 0.5 OVER | no live row |
| 9 | Marcus Smart `player_twos` 1.5 OVER | live row exists, no book quoted |
| 10 | Marcus Smart `player_twos` 1.5 UNDER | live row exists, no book quoted |

---

## 6 · Root-cause classification (per directive checklist)

| Candidate cause | Verdict | Evidence |
|---|---|---|
| Upstream books do not offer the market | ✅ **Confirmed for 9 % (B)** + 6 % (C) of rejects | 176 rejects have no book quote anywhere |
| PP alt line has no matching sportsbook line | ❌ **Rejected as primary cause** | 84.7 % have a same-line book quote |
| **Our matching logic is too strict** | ✅ **PRIMARY ROOT CAUSE — 84.7 %** | `_pick_reference_odds` NBA branch reads DK + MGM only |
| Stat normalization mismatch | ❌ Rejected | stat_type matches exactly; `is_alternate_market` flag mismatches but doesn't break joins |
| Side / opponent / event mismatch | ❌ Rejected | All companion rows share `event_id` + `player_name` |
| Book odds exist but missing from adapter | ✅ Partly — see above | The data is in `nba_live_props` and threaded into `compute_tier`; only the chain at line 377–382 ignores FD/BOL for NBA |

---

## 7 · Conclusions

1. **NBA volume is artificially limited by matching logic, not by market structure.** Of the 1,153 `no_reference_market` rejects, **977 (84.7 %) are recoverable using book data already in `nba_live_props`**.

2. The fix is **a single chain extension in one function** — porting the MLB branch to NBA — *not* a filter relaxation, *not* a synthetic-odds fallback, *not* a PP-as-truth shortcut. Every reference-odds value would still come from a real two-sided sportsbook quote.

3. Estimated supply impact post-fix (recovery × current routable→tier rate of 2.9 %, conservative): **+25 to +30 additional NBA tier qualifiers per slate**, lifting NBA WZ supply meaningfully above the 8–10 review threshold without touching any gate.

---

## 8 · Recommendation (per directive — no code changes)

A. **Verified evidence proves the market exists** for 84.7 % of these rejects.

B. The **proposed fix** (for your decision, not implemented):
> Extend `_pick_reference_odds` for NBA to consult FD and BOL after DK/MGM, mirroring the MLB chain (`dk → fd → mgm → bol`). The DK+FD consensus block can stay MLB-only, or be elevated to default if you want NBA to use a 2-book consensus as well — your call.

C. **What I will NOT do** without explicit approval:
   - touch any filter / threshold / gate / scoring formula
   - introduce synthetic odds, interpolation, or PP-as-truth fallback
   - implement the chain extension

D. **What's safe to consider next** if you approve a fix attempt:
   1. Port `_pick_reference_odds` NBA branch to `dk → fd → mgm → bol`.
   2. Add 4 regression tests:
      - NBA prop with DK only → uses DK (unchanged behaviour).
      - NBA prop with FD only → uses FD (was None, now FD).
      - NBA prop with FD + BOL only → uses FD (chain order respected).
      - NBA prop with neither DK/FD/MGM/BOL → still `no_reference_market` (true unanchored case preserved).
   3. Recompute `final-nba-rt` with `replace`, snapshot before/after, deliver:
      - Δ `no_reference_market` reject count
      - Δ tier_qualified count, breakdown by tier
      - Δ WZ qualified count
      - Top 25 newly tiered props with their reference book + odds
      - 5 randomly-picked previously-tiered props to confirm zero regressions
   4. Mutation guard test: emptying `_pick_reference_odds` NBA branch should put the system back into the current bug state (proves the test catches the regression).

This is a **mathematically clean recovery of pre-existing market data**, not a relaxation. Awaiting your decision.
