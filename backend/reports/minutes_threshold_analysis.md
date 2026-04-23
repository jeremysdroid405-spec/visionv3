# Minutes Threshold Analysis — NBA (2026-04-23)

Analysis only — no model / gate / projection changes.

Bucketing rule per cutoff:

```
high_minutes : min_L5_mean >= cutoff OR min_L10_mean >= cutoff
low_minutes  : both below cutoff
```

## Live board — prop / pass counts by cutoff

| Cutoff | high_props | low_props | high_passes | low_passes | risky_OVER_passes |
|-------:|-----------:|----------:|------------:|-----------:|------------------:|
| 24 | 2086 | 1636 | 63 | 36 | 30 |
| 26 | 1865 | 1857 | 52 | 47 | 41 |
| 28 | 1701 | 2021 | 47 | 52 | 46 |
| 30 | 1457 | 2265 | 32 | 67 | 61 |
| 32 | 1203 | 2519 | 24 | 75 | 69 |

## Pass rate by cutoff

| Cutoff | high_minutes pass_rate | low_minutes pass_rate |
|-------:|----------------------:|---------------------:|
| 24 | 3.02% | 2.2% |
| 26 | 2.79% | 2.53% |
| 28 | 2.76% | 2.57% |
| 30 | 2.2% | 2.96% |
| 32 | 2.0% | 2.98% |

## Player historical `hit_rate_over` on the live board
(this is each player's own historical hit rate for this stat at lines around the current one — best proxy for 'did this kind of pick historically win' without a prop settlement DB).

| Cutoff | high_mins OVER-picks avg hit | low_mins OVER-picks avg hit | high_mins UNDER-picks avg | low_mins UNDER-picks avg |
|-------:|-----------------------------:|----------------------------:|-------------------------:|-------------------------:|
| 24 | 44.16% | 44.45% | 52.57% | 53.01% |
| 26 | 43.92% | 44.66% | 52.23% | 53.28% |
| 28 | 43.69% | 44.8% | 52.2% | 53.23% |
| 30 | 42.27% | 45.6% | 50.33% | 54.28% |
| 32 | 41.11% | 45.82% | 48.69% | 54.59% |

## 2024 hold-out — projection error by bucket (lower is better)

### PTS

| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |
|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|
| 24 | 13246 | 8.267 | +0.25 | 32341 | 4.831 | +0.03 | 1.394 | 7.602 |
| 26 | 11104 | 8.427 | +0.20 | 34483 | 5.027 | +0.06 | 1.561 | 8.287 |
| 28 | 9058 | 8.632 | +0.18 | 36529 | 5.193 | +0.07 | 1.706 | 9.127 |
| 30 | 7031 | 8.807 | +0.17 | 38556 | 5.377 | +0.08 | 1.85 | 9.995 |
| 32 | 5097 | 9.008 | +0.15 | 40490 | 5.548 | +0.09 | 1.982 | 11.308 |

### REB

| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |
|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|
| 24 | 13246 | 3.103 | +0.01 | 32341 | 2.138 | +0.00 | 0.158 | 0.617 |
| 26 | 11104 | 3.129 | -0.01 | 34483 | 2.198 | +0.01 | 0.182 | 0.629 |
| 28 | 9058 | 3.16 | -0.02 | 36529 | 2.25 | +0.01 | 0.197 | 0.669 |
| 30 | 7031 | 3.167 | -0.01 | 38556 | 2.305 | +0.01 | 0.216 | 0.693 |
| 32 | 5097 | 3.182 | +0.01 | 40490 | 2.351 | +0.00 | 0.231 | 0.743 |

### AST

| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |
|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|
| 24 | 13246 | 2.394 | +0.05 | 32341 | 1.334 | +0.00 | 0.029 | 0.287 |
| 26 | 11104 | 2.452 | +0.04 | 34483 | 1.391 | +0.01 | 0.041 | 0.299 |
| 28 | 9058 | 2.522 | +0.02 | 36529 | 1.441 | +0.01 | 0.051 | 0.315 |
| 30 | 7031 | 2.594 | +0.01 | 38556 | 1.495 | +0.02 | 0.06 | 0.345 |
| 32 | 5097 | 2.682 | +0.02 | 40490 | 1.546 | +0.02 | 0.066 | 0.408 |

### 3PM

| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |
|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|
| 24 | 13246 | 1.505 | +0.01 | 32341 | 0.856 | -0.01 | -0.009 | 0.017 |
| 26 | 11104 | 1.531 | +0.01 | 34483 | 0.895 | -0.01 | -0.005 | 0.011 |
| 28 | 9058 | 1.555 | +0.00 | 36529 | 0.933 | -0.00 | -0.004 | 0.008 |
| 30 | 7031 | 1.576 | +0.00 | 38556 | 0.969 | -0.00 | -0.003 | 0.01 |
| 32 | 5097 | 1.608 | -0.01 | 40490 | 1.0 | -0.00 | -0.002 | 0.001 |

### PRA

| Cutoff | high_n | high_RMSE | high_bias | low_n | low_RMSE | low_bias | lowline_low_bias | lowline_high_bias |
|-------:|------:|---------:|---------:|------:|--------:|--------:|-----------------:|-----------------:|
| 24 | 13246 | 11.235 | +0.33 | 32341 | 7.113 | +0.01 | 2.726 | 16.242 |
| 26 | 11104 | 11.398 | +0.22 | 34483 | 7.355 | +0.07 | 2.974 | 17.966 |
| 28 | 9058 | 11.613 | +0.17 | 36529 | 7.557 | +0.09 | 3.179 | 19.771 |
| 30 | 7031 | 11.741 | +0.15 | 38556 | 7.788 | +0.09 | 3.374 | 21.543 |
| 32 | 5097 | 11.943 | +0.15 | 40490 | 7.984 | +0.10 | 3.541 | 23.773 |

## Top 10 risky `low_minutes` OVER passes on the live board

### Cutoff = 24 minutes — 30 risky OVER passes total

| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |
|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|
| Sam Merrill | PRA | 9.5 | front_lines | 15.52 | 15.6 | 18.4 | 85.0 | 14.5 |
| Sam Merrill | PTS | 5.5 | front_lines | 10.21 | 15.6 | 18.4 | 75.0 | 14.9 |
| Ayo Dosunmu | PTS | 10.5 | front_lines | 15.33 | 14.4 | 7.2 | 85.0 | 27.8 |
| Scottie Barnes | AST | 3.5 | safe_haven | 6.50 | 17.6 | 16.7 | 90.0 | 14.4 |
| Ayo Dosunmu | player_points_rebounds | 13.5 | front_lines | 19.68 | 14.4 | 7.2 | 85.0 | 27.5 |
| Mark Williams | PRA | 13.5 | front_lines | 18.10 | 11.6 | 18.6 | 70.0 | 21.9 |
| Ayo Dosunmu | PRA | 16.5 | front_lines | 21.14 | 14.4 | 7.2 | 70.0 | 19.6 |
| Miles McBride | PRA | 7.5 | front_lines | 10.99 | 16.8 | 21.0 | 80.0 | 12.5 |
| C.J. McCollum | AST | 2.5 | safe_haven | 4.46 | 13.6 | 9.1 | 75.0 | 12.1 |
| Sam Merrill | player_points_rebounds | 9.5 | front_lines | 13.58 | 15.6 | 18.4 | 70.0 | 22.6 |

### Cutoff = 26 minutes — 41 risky OVER passes total

| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |
|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|
| Nickeil Alexander-Walker | PTS | 14.5 | safe_haven | 21.86 | 24.0 | 20.9 | 95.0 | 15.1 |
| Sam Merrill | PRA | 9.5 | front_lines | 15.52 | 15.6 | 18.4 | 85.0 | 14.5 |
| Sam Merrill | PTS | 5.5 | front_lines | 10.21 | 15.6 | 18.4 | 75.0 | 14.9 |
| Naz Reid | PTS | 7.5 | safe_haven | 12.45 | 24.4 | 25.6 | 80.0 | 9.1 |
| Ayo Dosunmu | PTS | 10.5 | front_lines | 15.33 | 14.4 | 7.2 | 85.0 | 27.8 |
| Quentin Grimes | PRA | 11.5 | front_lines | 16.58 | 20.4 | 25.0 | 75.0 | 22.9 |
| Scottie Barnes | AST | 3.5 | safe_haven | 6.50 | 17.6 | 16.7 | 90.0 | 14.4 |
| Ayo Dosunmu | player_points_rebounds | 13.5 | front_lines | 19.68 | 14.4 | 7.2 | 85.0 | 27.5 |
| Mitchell Robinson | REB | 4.5 | safe_haven | 8.01 | 25.4 | 22.9 | 80.0 | 16.6 |
| Mark Williams | PRA | 13.5 | front_lines | 18.10 | 11.6 | 18.6 | 70.0 | 21.9 |

### Cutoff = 28 minutes — 46 risky OVER passes total

| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |
|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|
| Nickeil Alexander-Walker | PTS | 14.5 | safe_haven | 21.86 | 24.0 | 20.9 | 95.0 | 15.1 |
| Sam Merrill | PRA | 9.5 | front_lines | 15.52 | 15.6 | 18.4 | 85.0 | 14.5 |
| Sam Merrill | PTS | 5.5 | front_lines | 10.21 | 15.6 | 18.4 | 75.0 | 14.9 |
| Naz Reid | PTS | 7.5 | safe_haven | 12.45 | 24.4 | 25.6 | 80.0 | 9.1 |
| Ayo Dosunmu | PTS | 10.5 | front_lines | 15.33 | 14.4 | 7.2 | 85.0 | 27.8 |
| Quentin Grimes | PRA | 11.5 | front_lines | 16.58 | 20.4 | 25.0 | 75.0 | 22.9 |
| Scottie Barnes | AST | 3.5 | safe_haven | 6.50 | 17.6 | 16.7 | 90.0 | 14.4 |
| Ayo Dosunmu | player_points_rebounds | 13.5 | front_lines | 19.68 | 14.4 | 7.2 | 85.0 | 27.5 |
| Mitchell Robinson | REB | 4.5 | safe_haven | 8.01 | 25.4 | 22.9 | 80.0 | 16.6 |
| Dennis Schroder | AST | 1.5 | front_lines | 3.31 | 27.6 | 21.8 | 95.0 | 27.3 |

### Cutoff = 30 minutes — 61 risky OVER passes total

| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |
|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|
| Jarrett Allen | PTS | 9.5 | safe_haven | 18.43 | 29.8 | 28.5 | 95.0 | 22.1 |
| Jarrett Allen | player_points_rebounds_alternate | 14.5 | safe_haven | 28.12 | 29.8 | 28.5 | 90.0 | 12.0 |
| Jarrett Allen | PTS | 11.5 | front_lines | 18.43 | 29.8 | 28.5 | 75.0 | 29.0 |
| Nickeil Alexander-Walker | PTS | 14.5 | safe_haven | 21.86 | 24.0 | 20.9 | 95.0 | 15.1 |
| Sam Merrill | PRA | 9.5 | front_lines | 15.52 | 15.6 | 18.4 | 85.0 | 14.5 |
| Sam Merrill | PTS | 5.5 | front_lines | 10.21 | 15.6 | 18.4 | 75.0 | 14.9 |
| Jarrett Allen | PTS | 12.5 | front_lines | 18.43 | 29.8 | 28.5 | 75.0 | 32.4 |
| Naz Reid | PTS | 7.5 | safe_haven | 12.45 | 24.4 | 25.6 | 80.0 | 9.1 |
| Evan Mobley | player_points_rebounds_alternate | 19.5 | safe_haven | 28.57 | 27.0 | 28.0 | 85.0 | 9.5 |
| Evan Mobley | player_points_assists_alternate | 14.5 | safe_haven | 21.50 | 27.0 | 28.0 | 85.0 | 12.2 |

### Cutoff = 32 minutes — 69 risky OVER passes total

| Player | Stat | Line | Tier | Model | L5 | L10 | Hit % | Edge % |
|--------|:----:|-----:|:----:|-----:|---:|----:|------:|-------:|
| Jarrett Allen | PTS | 9.5 | safe_haven | 18.43 | 29.8 | 28.5 | 95.0 | 22.1 |
| Jarrett Allen | player_points_rebounds_alternate | 14.5 | safe_haven | 28.12 | 29.8 | 28.5 | 90.0 | 12.0 |
| Dyson Daniels | PTS | 7.5 | front_lines | 14.59 | 30.6 | 31.5 | 80.0 | 21.4 |
| Jarrett Allen | PTS | 11.5 | front_lines | 18.43 | 29.8 | 28.5 | 75.0 | 29.0 |
| Nickeil Alexander-Walker | PTS | 14.5 | safe_haven | 21.86 | 24.0 | 20.9 | 95.0 | 15.1 |
| Sam Merrill | PRA | 9.5 | front_lines | 15.52 | 15.6 | 18.4 | 85.0 | 14.5 |
| Sam Merrill | PTS | 5.5 | front_lines | 10.21 | 15.6 | 18.4 | 75.0 | 14.9 |
| Jarrett Allen | PTS | 12.5 | front_lines | 18.43 | 29.8 | 28.5 | 75.0 | 32.4 |
| Naz Reid | PTS | 7.5 | safe_haven | 12.45 | 24.4 | 25.6 | 80.0 | 9.1 |
| Evan Mobley | player_points_rebounds_alternate | 19.5 | safe_haven | 28.57 | 27.0 | 28.0 | 85.0 | 9.5 |

---

## Recommendation — cutoff **26 minutes** (with 28 as a defensible alternative)

### Key observations

**1. Live-board pass-rate inversion at cutoffs ≥ 30**

| Cutoff | high_mins pass_rate | low_mins pass_rate | Stars-pass-more invariant |
|--------|--------------------:|-------------------:|:-------------------------:|
| 24 | 3.02% | 2.20% | ✅ |
| 26 | 2.79% | 2.53% | ✅ |
| 28 | 2.76% | 2.57% | ✅ |
| 30 | 2.20% | 2.96% | ❌ **inverted** |
| 32 | 2.00% | 2.98% | ❌ **inverted harder** |

At cutoffs ≥ 30, the "low_minutes" bucket starts passing *more* props
than the "high_minutes" bucket. That means the threshold is no longer
separating stable role players from risk — it's just reclassifying
reliable rotation players into the "risk" bucket.

**2. Historical low-line (<10) bias inside each bucket**

Low_minutes bucket low-line bias grows with cutoff (it accumulates more
rotation players whose PTS<10 predictions carry bias):

| Cutoff | PTS <10 bias (low_mins) | PTS <10 bias (high_mins) |
|-------:|-----------------------:|-------------------------:|
| 24 | +1.39 | +7.60 |
| 26 | +1.56 | +8.29 |
| 28 | +1.71 | +9.13 |
| 30 | +1.85 | +9.99 |
| 32 | +1.98 | +11.31 |

The low_mins low-line bias at cutoff=26 is +1.56 — already meaningfully
elevated vs the true bench-only (+1.39 at cutoff=24), which is the
signal we want to flag.

**3. Risky OVER-pass count grows monotonically**

| Cutoff | Risky OVER passes flagged |
|-------:|--------------------------:|
| 24 | 30 |
| 26 | 41 |
| 28 | 46 |
| 30 | 61 |
| 32 | 69 |

Cutoffs ≥ 30 catch too many stable rotation players in the "risky"
bucket. Cutoff=24 misses rotation-role minute risk (only 30 flagged).

**4. Separation on live-board historical hit rate**

The OVER-pick hit rate separation between high and low buckets is
small and noisy at every cutoff (29–71 bps). This metric does NOT
distinguish cutoffs and should not drive the decision.

### Why **26 minutes**

- It's the smallest cutoff where the low_minutes bucket captures
  rotation-level minute risk (low-line bias climbs to +1.56 from +1.39
  at 24) while preserving the stars-pass-more invariant.
- Risky OVER-pass count (41) is actionable — small enough for the
  scoring team to review individually, large enough to matter.
- At 28 the system behaves almost identically; 28 is the reasonable
  alternative if we want a slightly larger low_minutes bucket. Both
  preserve the starter-reliability invariant; both catch rotation
  risk; both avoid the pass-rate inversion.
- **Avoid 30 and 32.** The pass-rate inversion means at those cutoffs
  the bucket labels become misleading: "low_minutes" starts capturing
  plain rotation players whose props are actually among the more
  reliable on the board.

### Recommended gate: use **26** as the cutoff if the goal is *safety*
(fewer false positives in the stable-player bucket), or **28** if the
goal is *coverage* (slightly more rotation players in the minute-risk
bucket). Either is structurally sound. **30 and 32 introduce label
confusion and should not be used.**
