# User Preferences — Locked Defaults

**Set: 2026-05-16. Owner: user. Status: PERMANENT.**

## List requests — default formatting

When user asks for a list of candidates, picks, props, war zone / safe haven / front lines, etc., **always**:

1. **Order by hit rate** — primary sort = `hit_rate_l10` desc. Tie-break: `hit_rate_l20`, then `hit_rate_l5`, then `vision_score`.
2. **Include the full detail block for every row** — not just the summary table. Each row must contain:
   - Player, stat, line, side, sport, tier, computed_at
   - Match + opposing pitcher (name, throws, K/9, ERA, WHIP)
   - Batter hand, same/opp matchup flags
   - **Odds per book**: DK, FanDuel, ESPNBet, HardRock, WilliamHill/CSR, PrizePicks, BetMGM, Caesars — line + odds + fetched_at for each
   - TP / devig: tp%, fair_prob, books_used, books_list, method
   - Anchor: best_book, best_book_odds, best_book_edge, total_edge, shopping_edge_source
   - Model: μ_raw, μ_final, σ, model version, EB shrinkage (if applied)
   - Distribution: kind, λ, threshold, p_over, selector_reason, mu/cv floor flags
   - Shadow probs: LoM, ECDF (bucket + n), raw Gaussian
   - **Hit-rate panel**: HR_L5, HR_L10, HR_L20, HR_over, HR_under, n_games
   - **Variance panel**: **CV**, stability, avg_hit_margin, avg_miss_margin, ceiling_rate, σ
   - Vision: total + every component (probability / projection / edge / consistency / context / market_confidence / direction_alignment / volatility_penalty)
   - Gates: every gate name, threshold vs value, pass/fail
   - Intel: tempo, injury context (out, dtd, players)
   - Feature health: imputed count + imputed feature list
3. **Lead with the ranked summary table** (HR_L5 / HR_L10 / HR_L20 / CV / VS / TP% / Edge / μ / σ / P̂ / books_used) THEN dump the per-row detail blocks below.
4. **Default sport scope:** MLB + NBA + NFL combined unless the user explicitly limits.
5. **Default look-back:** last 6 h of `computed_at` unless stated.
6. **Default limit:** top 30 unless stated.

## Other locked behavior

- Never collapse a list request into "I saved the full report to a file" — print the full content inline.
- Never re-ask "want me to dive deeper?" before dumping all available detail on the first response.
- When the user asks "ranked by X", change ONLY the rank order; the detail block stays the same (full).
