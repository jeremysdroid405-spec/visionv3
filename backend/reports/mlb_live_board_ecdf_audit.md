# MLB Live-Board ECDF Audit
Generated: 2026-04-24 03:37:37 UTC  •  version_tag=`final-mlb-rt`  •  scanned docs: 2,165

Shadow simulation: for every active MLB scored doc, recomputes what `p_true_model` WOULD have been under the pre-ECDF Gaussian assumption (using persisted `model_projection` / `model_sigma`) and compares it to the ECDF output from the live artifact layer. Projections and gates are unchanged.

## 1. Current MLB tier counts

| tier | count |
|------|------:|
| safe_haven | 6 |
| front_lines | 1 |
| war_zone | 101 |
| unqualified | 2,198 |
| unassigned | 0 |

## 2. Top-20 MLB picks after ECDF (ranked by `ranking_score_v2`)

| # | player | stat | line | side | proj | sigma | gauss p_model | ecdf p_model | tier | edge_pct | rs2 |
|---|--------|------|-----:|------|-----:|------:|-------------:|-------------:|------|-------:|----:|
| 1 | Leody Taveras | rbis | 0.5 | OVER | 3.04 | 1.25 | 0.979 | 1.000 | war_zone | +0.0 | +2.540 |
| 2 | Nolan Schanuel | rbis | 0.5 | OVER | 2.58 | 1.41 | 0.929 | 0.997 | war_zone | +0.0 | +2.074 |
| 3 | Ozzie Albies | rbis | 0.5 | OVER | 2.51 | 1.23 | 0.949 | 0.996 | war_zone | +0.0 | +2.002 |
| 4 | Dansby Swanson | rbis | 0.5 | OVER | 1.59 | 1.15 | 0.827 | 0.932 | war_zone | +0.0 | +1.016 |
| 5 | Seiya Suzuki | rbis | 0.5 | OVER | 1.59 | 0.82 | 0.907 | 0.932 | war_zone | +0.0 | +1.016 |
| 6 | Yordan Alvarez | hits | 0.5 | OVER | 1.63 | 0.85 | 0.908 | 0.845 | safe_haven | +17.7 | +0.955 |
| 7 | Dominic Smith | hits | 0.5 | OVER | 1.54 | 0.97 | 0.859 | 0.772 | front_lines | +17.4 | +0.803 |
| 8 | Yusei Kikuchi | pitcher_strikeouts | 5.5 | OVER | 7.75 | 2.64 | 0.803 | 0.836 | war_zone | +0.0 | +0.802 |
| 9 | Adley Rutschman | rbis | 0.5 | OVER | 1.35 | 0.84 | 0.843 | 0.812 | war_zone | +0.0 | +0.690 |
| 10 | Mickey Moniak | rbis | 0.5 | OVER | 1.23 | 1.07 | 0.751 | 0.686 | war_zone | +0.0 | +0.501 |
| 11 | Ian Happ | singles | 0.5 | OVER | 1.24 | 0.63 | 0.879 | 0.574 | war_zone | +0.0 | +0.425 |
| 12 | Yusei Kikuchi | pitcher_strikeouts | 6.5 | OVER | 7.66 | 2.64 | 0.670 | 0.697 | war_zone | +0.0 | +0.317 |
| 13 | Zach Neto | hits | 0.5 | OVER | 0.91 | 0.63 | 0.742 | 0.596 | safe_haven | -8.3 | +0.244 |
| 14 | Ronald Acuna Jr. | hits | 0.5 | OVER | 0.87 | 0.63 | 0.721 | 0.596 | safe_haven | -7.5 | +0.221 |
| 15 | Freddie Freeman | hits | 0.5 | OVER | 0.86 | 0.82 | 0.669 | 0.596 | safe_haven | -9.2 | +0.215 |
| 16 | Jeremiah Jackson | rbis | 0.5 | OVER | 1.05 | 1.83 | 0.618 | 0.315 | war_zone | +0.0 | +0.173 |
| 17 | Carter Jensen | rbis | 0.5 | OVER | 1.05 | 0.82 | 0.748 | 0.315 | war_zone | +0.0 | +0.173 |
| 18 | Troy Johnston | rbis | 0.5 | OVER | 0.92 | 0.84 | 0.691 | 0.312 | war_zone | +0.0 | +0.131 |
| 19 | Kyle Manzardo | singles | 0.5 | OVER | 0.72 | 0.74 | 0.617 | 0.529 | war_zone | +0.0 | +0.116 |
| 20 | Max Muncy | singles | 0.5 | OVER | 0.68 | 1.06 | 0.568 | 0.529 | war_zone | +0.0 | +0.095 |

## 3. Downgraded / removed false-OVER candidates (144)

Props where the pre-ECDF Gaussian `p_over` ≥ 0.55 (would have triggered the OVER gate) but the ECDF `p_over` fell below 0.55. These are exactly the false-OVER calls the cutover is designed to eliminate.

| player | stat | line | proj | gauss p_over | ecdf p_over | Δ | tier |
|--------|------|-----:|-----:|------------:|-----------:|-----:|------|
| Lane Thomas | rbis | 0.5 | 0.86 | 0.873 | 0.310 | -0.562 | unqualified |
| Carson Benge | rbis | 0.5 | 0.85 | 0.866 | 0.310 | -0.556 | unqualified |
| Luis Robert Jr. | rbis | 0.5 | 0.85 | 0.866 | 0.310 | -0.556 | unqualified |
| Jazz Chisholm Jr. | rbis | 0.5 | 0.85 | 0.866 | 0.310 | -0.556 | unqualified |
| David Hamilton | rbis | 0.5 | 0.85 | 0.866 | 0.310 | -0.556 | unqualified |
| Marcelo Mayer | rbis | 0.5 | 0.83 | 0.852 | 0.309 | -0.542 | unqualified |
| Bo Bichette | total_bases | 2.5 | 3.89 | 0.871 | 0.392 | -0.478 | unqualified |
| Oneil Cruz | home_runs | 0.5 | 0.64 | 0.614 | 0.138 | -0.476 | unqualified |
| Dominic Smith | total_bases | 1.5 | 2.81 | 0.813 | 0.356 | -0.456 | unqualified |
| David Hamilton | total_bases | 1.5 | 2.59 | 0.796 | 0.345 | -0.451 | unqualified |
| Dylan Beavers | total_bases | 1.5 | 3.08 | 0.901 | 0.460 | -0.441 | unqualified |
| Bo Bichette | total_bases | 1.5 | 3.11 | 0.905 | 0.469 | -0.436 | unqualified |
| Carter Jensen | rbis | 0.5 | 1.05 | 0.748 | 0.315 | -0.433 | war_zone |
| Freddie Freeman | rbis | 0.5 | 0.69 | 0.726 | 0.304 | -0.422 | unqualified |
| Ian Happ | total_bases | 2.5 | 3.74 | 0.774 | 0.353 | -0.421 | unqualified |
| Brett Baty | rbis | 0.5 | 1.08 | 0.726 | 0.316 | -0.410 | unqualified |
| Carlos Narvaez | total_bases | 2.5 | 3.46 | 0.742 | 0.334 | -0.408 | unqualified |
| Jazz Chisholm Jr. | total_bases | 2.5 | 3.54 | 0.743 | 0.342 | -0.401 | unqualified |
| Nolan Schanuel | total_bases | 1.5 | 2.76 | 0.753 | 0.354 | -0.400 | unqualified |
| Adolis Garcia | total_bases | 1.5 | 3.05 | 0.850 | 0.454 | -0.396 | unqualified |
| J.C. Escarra | rbis | 0.5 | 1.09 | 0.728 | 0.336 | -0.392 | unqualified |
| Dustin Harris | rbis | 0.5 | 0.66 | 0.694 | 0.303 | -0.391 | unqualified |
| Bo Bichette | total_bases | 3.5 | 4.03 | 0.667 | 0.277 | -0.390 | unqualified |
| Kyle Tucker | runs | 0.5 | 0.91 | 0.786 | 0.397 | -0.390 | unqualified |
| Gary Sanchez | rbis | 0.5 | 1.05 | 0.703 | 0.315 | -0.388 | unqualified |
| Nolan Schanuel | total_bases | 2.5 | 3.66 | 0.736 | 0.349 | -0.387 | unqualified |
| Edouard Julien | total_bases | 1.5 | 2.82 | 0.744 | 0.357 | -0.387 | unqualified |
| Dylan Beavers | total_bases | 2.5 | 2.98 | 0.652 | 0.266 | -0.386 | unqualified |
| Coby Mayo | total_bases | 2.5 | 3.34 | 0.704 | 0.322 | -0.382 | unqualified |
| Troy Johnston | rbis | 0.5 | 0.92 | 0.691 | 0.312 | -0.379 | war_zone |
| Jazz Chisholm Jr. | total_bases | 1.5 | 2.32 | 0.696 | 0.320 | -0.376 | unqualified |
| Gary Sanchez | total_bases | 1.5 | 2.47 | 0.710 | 0.334 | -0.376 | unqualified |
| Brett Baty | total_bases | 1.5 | 3.13 | 0.851 | 0.475 | -0.376 | unqualified |
| Yordan Alvarez | rbis | 1.5 | 1.94 | 0.684 | 0.313 | -0.371 | unqualified |
| Brandon Marsh | rbis | 0.5 | 1.02 | 0.685 | 0.314 | -0.370 | unqualified |
| Brenton Doyle | rbis | 0.5 | 0.64 | 0.671 | 0.302 | -0.369 | unqualified |
| J.C. Escarra | total_bases | 2.5 | 3.34 | 0.689 | 0.322 | -0.367 | unqualified |
| Coby Mayo | total_bases | 1.5 | 3.18 | 0.858 | 0.491 | -0.367 | unqualified |
| Brett Baty | total_bases | 2.5 | 3.17 | 0.665 | 0.302 | -0.363 | unqualified |
| Carlos Narvaez | total_bases | 3.5 | 3.91 | 0.609 | 0.247 | -0.363 | unqualified |
| … | … | … | … | … | … | … | … |
| _(104 more rows truncated)_ |

## 4. All .5-line props that still pass (tiered) (82)

| player | stat | line | side | proj | ecdf p_model | tier | edge |
|--------|------|-----:|------|-----:|------------:|------|-----:|
| Leody Taveras | rbis | 0.5 | OVER | 3.04 | 1.000 | war_zone | +0.0 |
| Nolan Schanuel | rbis | 0.5 | OVER | 2.58 | 0.997 | war_zone | +0.0 |
| Ozzie Albies | rbis | 0.5 | OVER | 2.51 | 0.996 | war_zone | +0.0 |
| Dansby Swanson | rbis | 0.5 | OVER | 1.59 | 0.932 | war_zone | +0.0 |
| Seiya Suzuki | rbis | 0.5 | OVER | 1.59 | 0.932 | war_zone | +0.0 |
| Yordan Alvarez | hits | 0.5 | OVER | 1.63 | 0.845 | safe_haven | +17.7 |
| Yusei Kikuchi | pitcher_strikeouts | 5.5 | OVER | 7.75 | 0.836 | war_zone | +0.0 |
| Adley Rutschman | rbis | 0.5 | OVER | 1.35 | 0.812 | war_zone | +0.0 |
| Dominic Smith | hits | 0.5 | OVER | 1.54 | 0.772 | front_lines | +17.4 |
| Yusei Kikuchi | pitcher_strikeouts | 6.5 | OVER | 7.66 | 0.697 | war_zone | +0.0 |
| Mickey Moniak | rbis | 0.5 | OVER | 1.23 | 0.686 | war_zone | +0.0 |
| Ronald Acuna Jr. | hits | 0.5 | OVER | 0.87 | 0.596 | safe_haven | -7.5 |
| Zach Neto | hits | 0.5 | OVER | 0.91 | 0.596 | safe_haven | -8.3 |
| Freddie Freeman | hits | 0.5 | OVER | 0.86 | 0.596 | safe_haven | -9.2 |
| Ian Happ | singles | 0.5 | OVER | 1.24 | 0.574 | war_zone | +0.0 |
| Yusei Kikuchi | pitcher_strikeouts | 7.5 | OVER | 7.77 | 0.560 | war_zone | +0.0 |
| Bryce Harper | hits | 0.5 | OVER | 0.58 | 0.545 | safe_haven | -12.4 |
| Kyle Manzardo | singles | 0.5 | OVER | 0.72 | 0.529 | war_zone | +0.0 |
| Max Muncy | singles | 0.5 | OVER | 0.68 | 0.529 | war_zone | +0.0 |
| Daulton Varsho | singles | 0.5 | OVER | 0.59 | 0.514 | war_zone | +0.0 |
| Mike Yastrzemski | singles | 0.5 | OVER | 0.42 | 0.480 | war_zone | +0.0 |
| Alex Freeland | singles | 0.5 | OVER | 0.41 | 0.480 | war_zone | +0.0 |
| Jameson Taillon | pitcher_strikeouts | 5.5 | OVER | 5.06 | 0.464 | war_zone | +0.0 |
| Jameson Taillon | pitcher_strikeouts | 6.5 | OVER | 5.55 | 0.403 | war_zone | +0.0 |
| Jeremiah Jackson | rbis | 0.5 | OVER | 1.05 | 0.315 | war_zone | +0.0 |
| Carter Jensen | rbis | 0.5 | OVER | 1.05 | 0.315 | war_zone | +0.0 |
| Troy Johnston | rbis | 0.5 | OVER | 0.92 | 0.312 | war_zone | +0.0 |
| Vinnie Pasquantino | rbis | 0.5 | OVER | 0.72 | 0.305 | war_zone | +0.0 |
| Jo Adell | rbis | 0.5 | OVER | 0.69 | 0.304 | war_zone | +0.0 |
| TJ Rumfield | rbis | 0.5 | OVER | 0.63 | 0.301 | war_zone | +0.0 |
| Ian Happ | rbis | 0.5 | OVER | 0.61 | 0.300 | war_zone | +0.0 |
| Brayan Rocchio | rbis | 0.5 | OVER | 0.51 | 0.298 | war_zone | +0.0 |
| Hunter Goodman | rbis | 0.5 | OVER | 0.56 | 0.298 | war_zone | +0.0 |
| Trevor Story | rbis | 0.5 | OVER | 0.54 | 0.298 | war_zone | +0.0 |
| Drake Baldwin | rbis | 0.5 | OVER | 0.50 | 0.298 | war_zone | +0.0 |
| Bobby Witt Jr. | rbis | 0.5 | OVER | 0.51 | 0.298 | war_zone | +0.0 |
| Jorge Soler | rbis | 0.5 | OVER | 0.53 | 0.298 | war_zone | +0.0 |
| Carlos Correa | rbis | 0.5 | OVER | 0.33 | 0.277 | war_zone | +0.0 |
| Christian Walker | rbis | 0.5 | OVER | 0.33 | 0.277 | war_zone | +0.0 |
| Andy Pages | rbis | 0.5 | OVER | 0.33 | 0.277 | war_zone | +0.0 |
| Teoscar Hernandez | rbis | 0.5 | OVER | 0.33 | 0.277 | war_zone | +0.0 |
| Jarren Duran | rbis | 0.5 | OVER | 0.48 | 0.270 | war_zone | +0.0 |
| Vladimir Guerrero Jr. | rbis | 0.5 | OVER | 0.47 | 0.270 | war_zone | +0.0 |
| George Valera | rbis | 0.5 | OVER | 0.39 | 0.260 | war_zone | +0.0 |
| Austin Riley | rbis | 0.5 | OVER | 0.42 | 0.260 | war_zone | +0.0 |
| Dominic Smith | rbis | 0.5 | OVER | 0.38 | 0.260 | war_zone | +0.0 |
| Ben Rice | rbis | 0.5 | OVER | 0.37 | 0.260 | war_zone | +0.0 |
| Yainer Diaz | rbis | 0.5 | OVER | 0.41 | 0.260 | war_zone | +0.0 |
| Nico Hoerner | rbis | 0.5 | OVER | 0.39 | 0.260 | war_zone | +0.0 |
| Willson Contreras | rbis | 0.5 | OVER | 0.26 | 0.258 | war_zone | +0.0 |
| Mark Vientos | rbis | 0.5 | OVER | 0.28 | 0.258 | war_zone | +0.0 |
| Bryce Harper | rbis | 0.5 | OVER | 0.25 | 0.258 | war_zone | +0.0 |
| Zach Neto | rbis | 0.5 | OVER | 0.27 | 0.258 | war_zone | +0.0 |
| Cam Smith | rbis | 0.5 | OVER | 0.26 | 0.258 | war_zone | +0.0 |
| Kyle Tucker | rbis | 0.5 | OVER | 0.27 | 0.258 | war_zone | +0.0 |
| Logan O'Hoppe | rbis | 0.5 | OVER | 0.01 | 0.225 | war_zone | +0.0 |
| Bryce Harper | total_bases | 2.5 | OVER | 1.55 | 0.193 | war_zone | +0.0 |
| Yordan Alvarez | total_bases | 2.5 | OVER | 1.39 | 0.193 | war_zone | +0.0 |
| Ben Rice | total_bases | 2.5 | OVER | 0.90 | 0.156 | war_zone | +0.0 |
| Juan Soto | hits | 0.5 | OVER | 0.03 | 0.155 | safe_haven | -51.4 |
| Ben Rice | home_runs | 0.5 | OVER | 0.11 | 0.138 | war_zone | +0.0 |
| Yordan Alvarez | home_runs | 0.5 | OVER | 0.27 | 0.138 | war_zone | +0.0 |
| Matt Olson | home_runs | 0.5 | OVER | 0.07 | 0.122 | war_zone | +0.0 |
| Noah Cameron | pitcher_strikeouts | 6.5 | OVER | 2.77 | 0.091 | war_zone | +0.0 |
| Freddy Peralta | pitcher_strikeouts | 8.5 | OVER | 4.35 | 0.077 | war_zone | +0.0 |
| Gunnar Henderson | rbis | 0.5 | OVER | 0.19 | 0.075 | war_zone | +0.0 |
| Jesus Sanchez | rbis | 0.5 | OVER | 0.19 | 0.075 | war_zone | +0.0 |
| Emmet Sheehan | pitcher_strikeouts | 6.5 | OVER | 1.90 | 0.056 | war_zone | +0.0 |
| Noah Cameron | pitcher_strikeouts | 7.5 | OVER | 2.88 | 0.055 | war_zone | +0.0 |
| Matt Olson | total_bases | 2.5 | OVER | 0.59 | 0.050 | war_zone | +0.0 |
| Emmet Sheehan | pitcher_strikeouts | 7.5 | OVER | 2.29 | 0.041 | war_zone | +0.0 |
| Freddy Peralta | pitcher_strikeouts | 9.5 | OVER | 4.33 | 0.038 | war_zone | +0.0 |
| Max Scherzer | pitcher_strikeouts | 4.5 | OVER | 0.40 | 0.003 | war_zone | +0.0 |
| Max Scherzer | pitcher_strikeouts | 5.5 | OVER | 0.77 | 0.001 | war_zone | +0.0 |
| Dansby Swanson | hits+runs+rbis | 2.5 | OVER | 2.27 | -1.000 | war_zone | +0.0 |
| Adley Rutschman | doubles | 0.5 | OVER | 0.16 | -1.000 | war_zone | +0.0 |
| Ernie Clement | doubles | 0.5 | OVER | 0.27 | -1.000 | war_zone | +0.0 |
| Taylor Ward | doubles | 0.5 | OVER | 0.11 | -1.000 | war_zone | +0.0 |
| Nico Hoerner | hits+runs+rbis | 2.5 | OVER | 1.67 | -1.000 | war_zone | +0.0 |
| Matt Olson | doubles | 0.5 | OVER | 0.13 | -1.000 | war_zone | +0.0 |
| Austin Riley | hits+runs+rbis | 2.5 | OVER | 1.49 | -1.000 | war_zone | +0.0 |
| Jorge Soler | hits+runs+rbis | 2.5 | OVER | 1.46 | -1.000 | war_zone | +0.0 |

## 5. probability_method counts (shadow — reflects what a rescore would write)

| method | count | share |
|--------|------:|------:|
| ecdf | 1,411 | 65.2% |
| gaussian | 754 | 34.8% |

**stat_families falling back to Gaussian (no ECDF artifact):**

- `hits+runs+rbis` — 611
- `doubles` — 116
- `stolen_bases` — 27

## 6. Edge / probability distribution (before vs after ECDF)

| bucket | before (gauss) | after (ecdf) | Δ |
|--------|---------------:|-------------:|-----:|
| <10 | 443 | 217 | -226 |
| 10-30 | 604 | 675 | +71 |
| 30-45 | 389 | 188 | -201 |
| 45-55 | 192 | 152 | -40 |
| 55-70 | 267 | 115 | -152 |
| 70-90 | 232 | 27 | -205 |
| >=90 | 38 | 37 | -1 |
| none | 0 | 754 | +754 |

## 7. Zero-heavy props still showing inflated OVER probability

Zero-heavy stat families checked: doubles, home_runs, rbis, runs, singles, stolen_bases, total_bases, triples, walks.

**Count of OVER props with ECDF `p_over` ≥ 0.55: 64**

These are not necessarily bugs — a big slugger vs a weak pitcher CAN legitimately carry `p_over ≥ 0.55` on a 0.5 total_bases line. Listed for spot-check:

| player | stat | line | proj | ecdf p_over | tier | edge |
|--------|------|-----:|-----:|-----------:|------|-----:|
| Brandon Marsh | home_runs | 0.5 | 1.49 | 1.000 | unqualified | +0.0 |
| Ian Happ | runs | 0.5 | 1.89 | 1.000 | unqualified | +0.0 |
| Leody Taveras | rbis | 0.5 | 3.04 | 1.000 | war_zone | +0.0 |
| J.C. Escarra | runs | 0.5 | 1.77 | 1.000 | unqualified | +0.0 |
| Max Muncy | runs | 1.5 | 2.77 | 1.000 | unqualified | +0.0 |
| Michael Busch | rbis | 0.5 | 2.65 | 0.998 | unqualified | +0.0 |
| Nolan Schanuel | rbis | 0.5 | 2.58 | 0.997 | war_zone | +0.0 |
| Ozzie Albies | rbis | 0.5 | 2.51 | 0.996 | war_zone | +0.0 |
| Drake Baldwin | runs | 0.5 | 1.50 | 0.996 | unqualified | +0.0 |
| Bo Bichette | rbis | 0.5 | 2.44 | 0.995 | unqualified | +0.0 |
| Michael Harris II | rbis | 0.5 | 2.24 | 0.990 | unqualified | +0.0 |
| Coby Mayo | rbis | 0.5 | 2.22 | 0.989 | unqualified | +0.0 |
| Adley Rutschman | home_runs | 0.5 | 0.94 | 0.986 | unqualified | +0.0 |
| Coby Mayo | home_runs | 0.5 | 0.94 | 0.986 | unqualified | +0.0 |
| Michael Busch | home_runs | 0.5 | 0.94 | 0.986 | unqualified | +0.0 |
| Matt Olson | singles | 0.5 | 1.72 | 0.982 | unqualified | +0.0 |
| Adolis Garcia | home_runs | 0.5 | 0.91 | 0.975 | unqualified | +0.0 |
| Ozzie Albies | rbis | 1.5 | 2.81 | 0.966 | unqualified | +0.0 |
| Michael Busch | rbis | 1.5 | 2.79 | 0.964 | unqualified | +0.0 |
| Brandon Marsh | total_bases | 2.5 | 5.87 | 0.962 | unqualified | +0.0 |
| Carson Benge | total_bases | 1.5 | 4.76 | 0.950 | unqualified | +0.0 |
| Vinnie Pasquantino | total_bases | 1.5 | 4.75 | 0.949 | unqualified | +52.6 |
| Nathan Lukes | rbis | 0.5 | 1.61 | 0.938 | unqualified | +0.0 |
| Dansby Swanson | rbis | 0.5 | 1.59 | 0.932 | war_zone | +0.0 |
| Seiya Suzuki | rbis | 0.5 | 1.59 | 0.932 | war_zone | +0.0 |
| Nolan Schanuel | rbis | 1.5 | 2.55 | 0.919 | unqualified | +0.0 |
| Jazz Chisholm Jr. | runs | 0.5 | 1.15 | 0.909 | unqualified | +0.0 |
| Brandon Marsh | total_bases | 1.5 | 4.38 | 0.877 | unqualified | +0.0 |
| Bo Bichette | rbis | 1.5 | 2.45 | 0.876 | unqualified | +0.0 |
| Carson Benge | total_bases | 2.5 | 5.29 | 0.848 | unqualified | +0.0 |
| Adley Rutschman | rbis | 0.5 | 1.35 | 0.812 | war_zone | +0.0 |
| Brandon Marsh | runs | 0.5 | 1.09 | 0.768 | unqualified | +0.0 |
| Jose Caballero | runs | 0.5 | 1.09 | 0.768 | unqualified | +0.0 |
| Oneil Cruz | rbis | 0.5 | 1.29 | 0.758 | unqualified | +0.0 |
| Carlos Narvaez | home_runs | 0.5 | 0.79 | 0.745 | unqualified | +0.0 |
| Carson Benge | home_runs | 0.5 | 0.79 | 0.745 | unqualified | +0.0 |
| Brett Baty | home_runs | 0.5 | 0.79 | 0.745 | unqualified | +0.0 |
| Jazz Chisholm Jr. | home_runs | 0.5 | 0.79 | 0.745 | unqualified | +0.0 |
| Mickey Moniak | rbis | 0.5 | 1.23 | 0.686 | war_zone | +0.0 |
| Michael Harris II | rbis | 1.5 | 2.22 | 0.671 | unqualified | +0.0 |
| _(24 more rows truncated)_ |

### Zero-heavy OVER probability by stat family

| stat | total OVERs | ecdf ≥ 0.55 | share | max ecdf p_over |
|------|-------------:|-------------:|-------:|----------------:|
| home_runs | 76 | 9 | 11.8% | 1.000 |
| rbis | 249 | 20 | 8.0% | 1.000 |
| runs | 102 | 7 | 6.9% | 1.000 |
| singles | 152 | 17 | 11.2% | 0.982 |
| total_bases | 472 | 11 | 2.3% | 0.962 |

## Summary

- **Board playable: YES**. 82 tiered MLB picks on the slate.
- **ECDF coverage: 65.2%** of live scored props run through ECDF. Remaining 754 fall back to Gaussian (stat_family with no artifact).
- **False-OVER corrections live on the board: 144**. Each is a prop whose Gaussian p_over would have cleared the 0.55 OVER gate but ECDF pulled it back below. These would have been bad bets on the pre-ECDF board.
- **Zero-heavy OVER inflation: 64 candidates (see §7).** Listed rows are candidates for manual review, not automatic rejects.

Projections, sigmas, tier gates, and the 0-book exclusion are all unchanged. Only `p_true_model` changes under ECDF.