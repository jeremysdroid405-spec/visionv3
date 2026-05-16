# User Preferences — Locked Defaults

**Set: 2026-05-16. Owner: user. Status: PERMANENT.**

## CRITICAL RULE — Be fast and cheap

When the user asks for a list, **just print the data**. No analysis. No breakdown. No "insights". No "tier" recommendations. No callouts. No "what this means". No follow-up questions. Print the data. Stop.

Target turnaround: under 30 seconds. One script run, one paste, done.

## List request defaults

- **Sort:** `hit_rate_l10` desc → `hit_rate_l20` desc → `hit_rate_l5` desc → `vision_score` desc
- **Scope:** MLB + NBA + NFL combined unless user specifies
- **Look-back:** last 6h of `computed_at`
- **Limit:** top 30 unless user specifies
- **Fields per row:** player, stat, line, side, sport, HR_L5/L10/L20, CV, VS, TP%, FairP, Edge, μ, σ, P̂, books_used, all per-book odds (DK/FD/EB/HRB/CSR/MGM/Caesars/PP), TP method+books_list, anchor, model+EB, distribution, shadow probs, margins, vision components, gates, intel, feature health, computed_at — **all of it, raw, no commentary**

## How to deliver

1. Run the existing `/app/backend/audits/list_top_warzone_30.py` (or equivalent).
2. Output the script's stdout. That's the entire response.
3. **Do not** add tables of conclusions, color emojis, "smoking guns", tier rankings, recommendations, or follow-up questions.
4. **Do not** ask "want me to dig deeper" or offer next steps.
5. If the user wants analysis, they will ask for it.
