"""
LOCKED PHASE-1 VALIDATION SNAPSHOT — FL Core 4
Window: 2026-05-03 → 2026-05-15
Snapshot date: 2026-05-18  (this session)

DO NOT modify these configs during Phase-2 (April) durability testing.
All numbers below are reproducible from /app/backend/audits/portfolio_core4_analysis.py
which pulls from mlb_test_outputs (replay_serial pattern GSS-MLB-2026MMDD-FRON-POOL).

==============================================================================
LOCKED CORE-4 CONFIGS
==============================================================================

LEG 1 — pitcher_strikeouts OVER  [+101,+200]   EDGE >= 5  AND  CV <= 0.70
LEG 2 — earned_runs        UNDER [+101,+200]   baseline (no additional filter)
LEG 3 — runs               UNDER [-149,-110]   TP in [50, 55)
        Tracked micro-bands:  [-149,-140] / [-139,-130] / [-129,-120] / [-119,-110]
LEG 4 — batter_strikeouts  OVER  [-199,-150]   HR20 >= 65  AND  μ-line >= 0.5

==============================================================================
LOCKED PHASE-1 SNAPSHOT METRICS  (2026-05-03 → 2026-05-15)
==============================================================================

LEG-LEVEL
---------
LEG 1  PS_OVER_dog   n=53/48   HR=56.25%  ROI=+26.46%  P&L=+12.70u  consist=0.75
LEG 2  ER_UNDER_dog  n=87/81   HR=66.67%  ROI=+43.70%  P&L=+35.40u  consist=1.00
LEG 3  R_UNDER_mid   n=529/471 HR=64.54%  ROI=+14.72%  P&L=+69.35u  consist=0.846
LEG 4  BS_OVER_mid   n=121/103 HR=72.82%  ROI=+15.50%  P&L=+15.96u  consist=0.615

PORTFOLIO TOTAL
---------------
Total picks                : 790
Graded picks               : 703
W / L                      : 460 / 243
Overall HR                 : 65.43%
Overall ROI                : +18.98%
Total P&L                  : +133.41u
Avg picks/day              : 54.08 graded
Avg P&L/day                : +10.26u
Daily Sharpe ratio         : 1.16
Positive days              : 12 / 13
Negative days              : 1 / 13 (2026-05-13: -1.19u)
Max drawdown               : -1.19u
Longest winning streak     : 10 days
Longest losing streak      : 1 day

DRAWDOWN
--------
Max drawdown               : -1.19u (0.9% of total P&L)
Largest 3-day win stretch  : +48.55u (05-08 → 05-10)
Largest 5-day win stretch  : +70.11u (05-06 → 05-10)
Rolling 3-day ROI range    : +5.80% → +26.00%
Rolling 5-day ROI range    : +10.76% → +24.91%
Daily P&L std-dev          : 8.87u

FOLD BREAKDOWN
--------------
Fold A (05-03..06)  n=245/228  HR=69.30%  ROI=+25.96%  P&L=+59.20u  (4 days)
Fold B (05-07..10)  n=307/242  HR=66.53%  ROI=+20.30%  P&L=+49.14u  (4 days)
Fold C (05-11..15)  n=238/233  HR=60.52%  ROI=+10.76%  P&L=+25.08u  (5 days)
DECAY SIGNATURE: ROI drops 25.96% → 20.30% → 10.76% across folds
R_UNDER ROI by fold:  17.89% → 21.90% → 4.26%  (sharp Fold-C drop)

CORRELATION STRUCTURE
---------------------
Same-event multi-leg overlap          : 112 / 135 events (82.9%)
Same-pitcher overlap PS_OVER∩ER_UNDER : 19 pitchers
Same-game overlap R_UNDER∩BS_OVER     : 74 / 131 games (56.5%)
% picks in any overlap                : 76.5%
% P&L from overlapped picks           : 77.9%

SIDE/BAND CONCENTRATION
-----------------------
% P&L UNDER side             : 78.5%
% P&L OVER side              : 21.5%
% P&L from R_UNDER alone     : 52.0%
% P&L from band [-149,-110]  : 52.0%
% P&L from band [+101,+200]  : 36.1%
% P&L from band [-199,-150]  : 12.0%

R_UNDER MICRO-BAND BREAKDOWN
----------------------------
[-149,-140]  n=145/128  HR=67.19%  ROI=+14.39%  P&L=+18.41u  consist=0.692
[-139,-130]  n=149/134  HR=58.21%  ROI=+ 2.30%  P&L= +3.09u  consist=0.385  ← weak
[-129,-120]  n=147/136  HR=69.85%  ROI=+26.52%  P&L=+36.07u  consist=0.923  ← gold
[-119,-110]  n= 88/ 73  HR=61.64%  ROI=+16.13%  P&L=+11.78u  consist=0.75

INDIVIDUAL FOLD ROBUSTNESS (per leg)
------------------------------------
LEG 1 PS_OVER  : Fold A=+48.07%  B=+19.71%  C=+13.37%   (all positive)
LEG 2 ER_UNDER : Fold A=+48.92%  B=+26.44%  C=+55.50%   (all positive, all consist=1.0)
LEG 3 R_UNDER  : Fold A=+17.89%  B=+21.90%  C= +4.26%   (Fold C softens)
LEG 4 BS_OVER  : Fold A=+40.26%  B=+10.92%  C= +2.35%   (sharp time decay)

AUDIT VERDICTS (Phase-1)
------------------------
LEG 1  : Audit clean / 3-fold positive / robust
LEG 2  : Audit clean / 3-fold positive / 100% daily consistency
LEG 3  : Audit clean / 3-fold positive / Fold-C softens
LEG 4  : Audit clean / 3-fold positive / Fold-C dips to flat

PORTFOLIO CLASSIFICATION : SAFE WITH REDUCED STAKING
Bankroll recommendation  : Treat as ~38% of nominal due to overlap

==============================================================================
PHASE-2 DURABILITY TEST PLAN
==============================================================================
Window  : 2026-04-01 → 2026-04-14
Configs : LOCKED — identical to Phase-1, NO threshold changes
Goal    : Determine whether Core-4 niches are structurally durable across
          a completely different market environment, or whether May results
          were partially regime-dependent.
Required data ingestion:
  1. Historical odds snapshots from The Odds API
     - 14 days × props markets: pitcher_strikeouts, earned_runs, runs,
       batter_strikeouts
     - both OVER and UNDER lines (alt props too)
     - snapshot time 11:00:00Z (matches May methodology)
  2. Player actuals from BDL (Ball Don't Lie API)
     - daily batter & pitcher game-log ingestion 2026-04-01 → 04-14
     - canonical stat-family columns: pitcher_strikeouts, earned_runs,
       hits, total_bases, runs, rbis, batter_strikeouts, walks_allowed
  3. Replay pool serial generation (GSS-MLB-202604DD-FRON-POOL pattern)
"""
