# Rollback Procedure (Pre-Rename Phase)

## P1 — ✅ COMPLETED 2026-04-21 (this cycle)

- ✅ Gemini batching fix (routes/ferrari_tiers.py — `analyze_tier_batch` replaces per-prop fan-out)
- ✅ Switch Gemini model to `gemini-flash-lite-latest` (all callers + tests)
- ✅ Market Gap / "Book Spread" multi-sport feature
- ✅ Canonical `opponent_defensive_rank` multi-sport pipeline
- ✅ Stat-aware α in `ranking_score_v2`
- ✅ Stat-aware CV caps for Safe Haven eligibility
- ✅ Injury-Rank Phase 2 — usage-sorted beneficiaries via `usage_resolver`

## P1 — NEXT UP

- Emergent-managed Google OAuth integration (via `integration_playbook_expert_v2`)
- Stripe payments integration (pod test keys)
- Dashboard.jsx refactor (post-auth)

## P2 — Backlog

- Wave 3 Post-Migration Cleanup — Drop Step B (batch drop remaining backup
  legacy collections)
- Stale introspection artifacts under `/app/frontend/public/`
  (regenerate API specs to remove `demon-tracker` endpoints)
- Cross-sport logo collision — audit team logo lookup to require sport
  context (Cleveland MLB Guardians vs NBA Cavaliers)
- Legacy DB hardcodes in `scripts/ensure_indexes.py` and
  `scripts/init_database.py`
- Wind Tunnel weather API integration (MLB atmospheric friction)
- Abstract `nba_master_sync.py` into shared `PipelineStep` framework
- MLB opponent-defense provider (today `usage_resolver` / `defensive_rank_resolver`
  both return `unavailable` for MLB — if team-defense signal becomes useful,
  plug a provider without touching callers)



Applies to Wave 0 and Wave 1 of the NBA rebuild, BEFORE any live collection
rename has occurred. The indirection layer
(`services/config/collection_names.py`) is the single reversal surface.

## When to roll back
Any of the following, observed within 15 minutes of a Wave 0/1 step landing:

- Regression suite failure (any test in
  `tests/test_hit_rate_canonical.py`, `tests/test_tier_integrity.py`,
  `tests/test_decision_layer_sengun.py`, `tests/test_collection_names.py`).
- Any live collection document-count delta > 1% vs
  `/app/memory/migration_baseline.json` that isn't explained by an
  ingest cycle (odds_sync, injury refresh, game_start_scan).
- Any Ferrari board endpoint returning HTTP 5xx or `picks: []` where the
  baseline had picks.

## Procedure

1. **Revert `collection_names.py`** — restore the exact mapping from the last
   known-good git commit. Use a single diff; no partial reverts.
   ```bash
   git checkout HEAD~1 -- backend/services/config/collection_names.py
   ```
   (Substitute the offending commit if it is older.)

2. **Restart backend** via supervisor (not uvicorn):
   ```bash
   sudo supervisorctl restart backend
   ```

3. **Verify collection counts vs baseline**:
   ```bash
   python3 -c "
   import json, os
   from pymongo import MongoClient
   from dotenv import load_dotenv
   load_dotenv('/app/backend/.env')
   db = MongoClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
   base = json.load(open('/app/memory/migration_baseline.json'))
   for c, entry in base['databases'][os.environ['DB_NAME']].items():
       if not entry.get('exists'): continue
       live = db[c].estimated_document_count()
       drift = abs(live - entry['count']) / max(entry['count'], 1)
       flag = '!!' if drift > 0.01 else 'ok'
       print(f'{flag:2} {c:<40} baseline={entry[\"count\"]:>6}  live={live:>6}  drift={drift*100:.2f}%')
   "
   ```
   All rows must be `ok`.

4. **Confirm endpoints return identical results** (smoke test):
   ```bash
   API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2 | tr -d '"')
   for tier in safe-haven front-lines war-zone oracle-apex; do
     echo -n "  nba/$tier: "
     curl -sS "$API_URL/api/v3/ferrari/$tier?sport=nba" \
       | python3 -c "import json,sys; d=json.load(sys.stdin); print('picks='+str(len(d.get('picks') or d.get('apex_picks') or [])))"
   done
   ```
   Pick counts must match the pre-deploy counts.

5. **Re-run regression suite**:
   ```bash
   cd /app/backend && REACT_APP_BACKEND_URL=http://localhost:8001 \
     python -m pytest tests/test_collection_names.py \
       tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py \
       tests/test_decision_layer_sengun.py -q
   ```
   All tests must pass.

6. **Log the rollback** in `/app/memory/CHANGELOG.md` with:
   - UTC timestamp
   - Trigger (test failure / count drift / endpoint 5xx / other)
   - Commit reverted
   - Counts-after-rollback (attach the diff from step 3)

## What this procedure does NOT cover
- Post-rename waves (Wave 2 onward) need a separate rollback that also
  handles dual-write / dual-read reversal. Document when that wave lands.
- Orphan-DB deletion (Wave 7) is one-way by design; requires a separate
  backup/restore plan.


# PropVision — Priority Roadmap

Source of truth: user directive 2026-04-18 — “make the next priority the universal
board migration and legacy writer retirement, not new features.”

## P0 — IN PROGRESS (Active)

### 1. Universal Board Migration + Legacy Writer Retirement (Step 6)
Goal: one authoritative writer (real-time event-driven engine) per sport.
Retire legacy full-rebuild writers (`mlb_master_sync.py`, `nba_master_sync.py`,
and any hourly coordinator still running `mode=replace` against
`{sport}_prop_scores`).

Acceptance gate (all must hold across the 48h observation window):
- `divergence_ratio` < 0.05 across 1h / 6h / 24h / 48h rolling windows.
- `missing` class = 0 across 48h (every real-time upsert still present in the
  current `{sport}_prop_scores` snapshot keyed on `(canonical_key, version_tag)`).
- `tier_changed` ≤ 1% across 48h.
- `vision_score_drift` entries are ≤ 1.0 absolute on a 0-100 scale.

### 1a. BLOCKER — A/B comparator race (DISCOVERED 2026-04-18)
Evidence (captured against live preview):
- Real-time path upserted 403 keys at 23:04:13 for 5 event_ids.
- Legacy rebuild ran at 23:11:48 with `mode=replace` on `version_tag=final-nba`
  and reduced it to 166 keys from a single event_id.
- 48h drift audit now reports 471 `missing` / 3 `converged` (99.4% divergence).

Structural cause: `write_versioned_scores(mode="replace")` in the legacy
rebuild wipes the fast-path upserts sharing the same `version_tag`. The clock
cannot produce a valid reading until one of these is done:

 (a) Legacy rebuild switches from `mode=replace` to `mode=upsert` and drops a
     stale-record sweeper (delete any doc with `computed_at < now - 2h AND
     active=true AND version_tag=<canonical>`).
 (b) Legacy and real-time write under distinct `version_tag`s during the
     observation window (e.g. `final-nba-legacy` vs `final-nba-rt`) and the
     drift auditor compares across tags.
 (c) Legacy writer retired *before* the 48h clock and the real-time path
     becomes the sole writer (skipping the A/B).

Recommended: path (b) for the observation window, then path (c) to retire.

### 1b. Prop-scores hygiene (prerequisite)
`nba_prop_scores` currently holds 41,959 docs across 16 stale experimental
`version_tag`s (`nba-cv-v1`, `nba-wz-varA/B/baseline`, `prod-20260417T222314`,
`nba-hitrate-20260417T230218`, `nba-model-20260417T230218`, `vk2_5yr_v1`,
`hit_rate_baseline_v1`, `live-test-v1`, `vk2_pp_playable_v1/v1b`,
`vk2_gate_fix_v2`, …). Same shape in MLB (`mlb_prop_scores` = 15,033 docs).

Action: one-shot sweeper that retains only `version_tag == <canonical>`
(`final-nba` / `final-mlb`) with a configurable grace window, then recreates
the `(version_tag, tier, active, vision_score desc)` index used by the reader.

## P1 — NEXT (After Step 6 lands)

- Emergent-managed Google Auth integration
- Stripe payments integration (pod test keys already available)
- Dashboard.jsx refactor (decompose into smaller components, already hit 1962 lines)

## P2 — BACKLOG

- Cross-sport logo collision (CLE → sport-aware logo lookup)
- Wind Tunnel weather API integration (MLB friction)
- BDL modifier precision audit (PRD Section 3 requirement)

## Constraints — User Directive

- No new features until P0 is closed.
- No UI work until P0 is closed.
- No agent-led resume of the 48h clock without an explicit decision on 1a.
