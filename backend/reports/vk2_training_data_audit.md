# VK2 Training Data Coverage Audit
Generated: 2026-04-24 00:20:05 UTC

Seasons: 2020–2024. Expected recency weights: 2024=1.00, 2023=0.85, 2022=0.70, 2021=0.55, 2020=0.40.

## Headline — rows per season, per stat

| Stat | 2020 | 2021 | 2022 | 2023 | 2024 | total | train | test | weighted train |
|------|------|------|------|------|------|-------|-------|------|----------------|
| PTS | 28,930 | 33,279 | 43,197 | 45,545 | 45,587 | 196,538 | 150,951 | 45,587 | 98,826.60 |
| REB | 28,930 | 33,279 | 43,197 | 45,545 | 45,587 | 196,538 | 150,951 | 45,587 | 98,826.60 |
| AST | 28,930 | 33,279 | 43,197 | 45,545 | 45,587 | 196,538 | 150,951 | 45,587 | 98,826.60 |
| 3PM | 28,930 | 33,279 | 43,197 | 45,545 | 45,587 | 196,538 | 150,951 | 45,587 | 98,826.60 |
| PRA | 28,930 | 33,279 | 43,197 | 45,545 | 45,587 | 196,538 | 150,951 | 45,587 | 98,826.60 |

## Weighted contribution (share of total weighted_sum)

| Stat | 2020 | 2021 | 2022 | 2023 | 2024 |
|------|------|------|------|------|------|
| PTS | 8.01% | 12.67% | 20.94% | 26.81% | 31.57% |
| REB | 8.01% | 12.67% | 20.94% | 26.81% | 31.57% |
| AST | 8.01% | 12.67% | 20.94% | 26.81% | 31.57% |
| 3PM | 8.01% | 12.67% | 20.94% | 26.81% | 31.57% |
| PRA | 8.01% | 12.67% | 20.94% | 26.81% | 31.57% |

## PTS detail (target field: `pts`)

- Unique players: **927**  
- Unique games:   **6,384**

### Weighting verification
- pkl present                       : yes
- pkl season_weights == expected    : True
- pkl seasons_used == expected      : True
- pkl samples_train == audited      : True (pkl=150951, audit=150951)
- pkl samples_test  == audited      : True (pkl=45587, audit=45587)
- pkl weighted_sum_train ≈ audited  : True (pkl=98826.59375, audit=98826.6)

### Leakage checks
- train∩test overlap (same pid+gid)  : 0 (must be 0)
- cross-season games (gid in ≥2 seasons) : 0 (must be 0)
- missing seasons                   : none
- under-represented seasons (<5%)   : none

**Verdict:** **PASS**

## REB detail (target field: `reb`)

- Unique players: **927**  
- Unique games:   **6,384**

### Weighting verification
- pkl present                       : yes
- pkl season_weights == expected    : True
- pkl seasons_used == expected      : True
- pkl samples_train == audited      : True (pkl=150951, audit=150951)
- pkl samples_test  == audited      : True (pkl=45587, audit=45587)
- pkl weighted_sum_train ≈ audited  : True (pkl=98826.59375, audit=98826.6)

### Leakage checks
- train∩test overlap (same pid+gid)  : 0 (must be 0)
- cross-season games (gid in ≥2 seasons) : 0 (must be 0)
- missing seasons                   : none
- under-represented seasons (<5%)   : none

**Verdict:** **PASS**

## AST detail (target field: `ast`)

- Unique players: **927**  
- Unique games:   **6,384**

### Weighting verification
- pkl present                       : yes
- pkl season_weights == expected    : True
- pkl seasons_used == expected      : True
- pkl samples_train == audited      : True (pkl=150951, audit=150951)
- pkl samples_test  == audited      : True (pkl=45587, audit=45587)
- pkl weighted_sum_train ≈ audited  : True (pkl=98826.59375, audit=98826.6)

### Leakage checks
- train∩test overlap (same pid+gid)  : 0 (must be 0)
- cross-season games (gid in ≥2 seasons) : 0 (must be 0)
- missing seasons                   : none
- under-represented seasons (<5%)   : none

**Verdict:** **PASS**

## 3PM detail (target field: `fg3m`)

- Unique players: **927**  
- Unique games:   **6,384**

### Weighting verification
- pkl present                       : yes
- pkl season_weights == expected    : True
- pkl seasons_used == expected      : True
- pkl samples_train == audited      : True (pkl=150951, audit=150951)
- pkl samples_test  == audited      : True (pkl=45587, audit=45587)
- pkl weighted_sum_train ≈ audited  : True (pkl=98826.59375, audit=98826.6)

### Leakage checks
- train∩test overlap (same pid+gid)  : 0 (must be 0)
- cross-season games (gid in ≥2 seasons) : 0 (must be 0)
- missing seasons                   : none
- under-represented seasons (<5%)   : none

**Verdict:** **PASS**

## PRA detail (target field: `pra`)

- Unique players: **927**  
- Unique games:   **6,384**

### Weighting verification
- pkl present                       : yes
- pkl season_weights == expected    : True
- pkl seasons_used == expected      : True
- pkl samples_train == audited      : True (pkl=150951, audit=150951)
- pkl samples_test  == audited      : True (pkl=45587, audit=45587)
- pkl weighted_sum_train ≈ audited  : True (pkl=98826.59375, audit=98826.6)

### Leakage checks
- train∩test overlap (same pid+gid)  : 0 (must be 0)
- cross-season games (gid in ≥2 seasons) : 0 (must be 0)
- missing seasons                   : none
- under-represented seasons (<5%)   : none

**Verdict:** **PASS**

## Global verdict

✅ **VK2 is correctly trained on full 5-year dataset** — every stat model passes weighting, leakage, and coverage checks against the persisted pkl metadata.
