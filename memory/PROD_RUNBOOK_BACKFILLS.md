# Production Runbook — Backfills via Emergent Admin API

> All commands assume two env vars set in your shell:
>
> ```bash
> export PV_HOST="https://propvision.bet"                       # production base
> export PV_ADMIN_TOKEN="<the EMERGENT_ADMIN_TOKEN from prod>"  # NOT the preview pod token
> ```
>
> Reference for endpoint semantics: `/app/memory/EMERGENT_ADMIN_API.md`.

---

## 0. Sanity checks (always run first)

```bash
# 0a. Token works?
curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/auth/whoami"
# → {"ok":true,"agent_id":"anonymous","token_hash":"…"}

# 0b. Policy lists the job we're about to run?
curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/policy/" \
 | jq '.allowed_jobs["scripts.research.backfill_stat_family_canonical"]'
```

If `whoami` returns 401/403/503 — STOP. Either the token is wrong or the
prod backend hasn't picked up `EMERGENT_ADMIN_TOKEN` yet.

---

## 1. Canonical-name `stat_family` backfill

Target collections (one at a time):

| Collection | Why |
|---|---|
| `mlb_replay_feature_cache` | Cached pre-game feature rows from before the 2026-05-18 SSOT cutover. |
| `mlb_replay_model_outputs` | Layer-3/4 outputs from before the cutover. |

### 1a. Dry-run (always do this first)

```bash
COLL=mlb_replay_feature_cache         # or mlb_replay_model_outputs

JOB=$(curl -s -X POST \
  -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "X-Agent-Id: ops-runbook" \
  -H "Content-Type: application/json" \
  -d "{\"module\":\"scripts.research.backfill_stat_family_canonical\",
        \"args\":[\"--collection=$COLL\",\"--dry-run\"]}" \
  "$PV_HOST/api/emergent-admin/jobs/run" | jq -r .job_id)

echo "JOB=$JOB"
```

### 1b. Watch the job until it finishes

```bash
# Poll status every 5s (cheap; the script is fast)
while :; do
  S=$(curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
       "$PV_HOST/api/emergent-admin/jobs/$JOB" | jq -r .job.status)
  echo "[$(date +%T)] status=$S"
  case "$S" in
    succeeded|failed|errored|cancelled) break ;;
  esac
  sleep 5
done

# Full output
curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/jobs/$JOB/log?tail=2000" \
 | jq -r '.lines[]'
```

### 1c. Read the mapping breakdown

The dry-run prints lines like:

```
  ── mapping breakdown ──
    'strikeouts'           → 'batter_strikeouts'  (12,431)
    'pitcher_walks'        → 'walks_allowed'      (   923)
    'hits+runs+rbis'       → 'hits_runs_rbis'     (   504)
    'Hits'                 → 'hits'               (    18)
```

**Stop and review.** If you see any mapping you don't expect (e.g. an
unfamiliar legacy token mapping to an unrelated canonical family), do
NOT commit — open `services/scoring/canonical_stats.py::_FAMILY_ALIAS`
first and fix the alias table.

### 1d. Commit

```bash
JOB=$(curl -s -X POST \
  -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "X-Agent-Id: ops-runbook" \
  -H "Content-Type: application/json" \
  -d "{\"module\":\"scripts.research.backfill_stat_family_canonical\",
        \"args\":[\"--collection=$COLL\",\"--commit\",\"--chunk-size=1000\"]}" \
  "$PV_HOST/api/emergent-admin/jobs/run" | jq -r .job_id)
echo "JOB=$JOB"
# repeat the polling loop from 1b
```

### 1e. Verify (idempotent re-run = 0 updates)

```bash
JOB=$(curl -s -X POST \
  -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"module\":\"scripts.research.backfill_stat_family_canonical\",
        \"args\":[\"--collection=$COLL\",\"--dry-run\"]}" \
  "$PV_HOST/api/emergent-admin/jobs/run" | jq -r .job_id)
# poll, then:
curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/jobs/$JOB/log?tail=200" \
 | jq -r '.lines[]' \
 | grep -E "rows scanned|already canonical|needs update|unrecognised"
# expect: needs update = 0
```

Audit row spot-check (post-commit, find a few rewritten rows by their
preserved `stat_family_legacy`):

```bash
curl -s -X POST -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"filter\":{\"stat_family_legacy\":{\"\$exists\":true}},
        \"projection\":{\"_id\":0,\"stat_family\":1,
                          \"stat_family_legacy\":1,
                          \"player_name\":1,\"market\":1},
        \"limit\":10}" \
  "$PV_HOST/api/emergent-admin/collections/$COLL/find" | jq
```

---

## 2. SGO multi-day historical validations

Used as a smoke test before larger backfills (e.g. `build_historical_outcomes`).

```bash
curl -s -X POST -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"module":"scripts.sgo.verify_sgo_player_stats_coverage",
        "args":["--league=MLB","--start=2025-06-01","--end=2025-06-07"]}' \
  "$PV_HOST/api/emergent-admin/jobs/run"
# → job_id; poll with the same loop from 1b.
```

Key numbers to read off the log:

- `event coverage` ≥ 98 % (≥99 % preferred)
- `player coverage` ≥ 95 %
- `outcome resolution rate` ≥ 90 % — if lower, do NOT proceed to commit.

---

## 3. Replay-card metadata propagation (verify, no backfill needed)

New runs already stamp `event_id`, `commence_time`, `game_date` onto
`mlb_production_replay_cards`. Confirm on a recent run:

```bash
curl -s -X POST -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filter":{},"projection":{"_id":0,"replay_serial":1,
        "event_id":1,"commence_time":1,"game_date":1},
        "sort":[["replay_serial",-1]],"limit":5}' \
  "$PV_HOST/api/emergent-admin/collections/mlb_production_replay_cards/find" \
 | jq
```

Historical (pre-2026-05-21) cards will show `event_id=null`. A separate
backfill script for those is a future task — not built yet.

---

## 4. Audit-log discipline

After every commit-step above, capture the audit slice for change-control:

```bash
curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/audit/?action=job_run&limit=20" \
 | jq '.entries[] | {ts, agent_id, status_code, params: .params_redacted}'

curl -s -H "X-Admin-Token: $PV_ADMIN_TOKEN" \
     "$PV_HOST/api/emergent-admin/audit/summary?hours=24" \
 | jq .rows
```

Paste both into the change ticket.

---

## 5. Rollback

There is **no rollback** for protected-collection writes via the Admin API
(deletes/updates on `mlb_replay_feature_cache` etc. are blocked by policy).
Rollback options, in order of cheapness:

1. **Re-run the script** — it's idempotent and resilient to interruption.
2. **`stat_family_legacy` field is preserved on every rewritten row**.
   To revert a single mistaken mapping you can hand-roll a Mongo update
   in production (operator-only, NOT via this API):
   ```js
   db.mlb_replay_feature_cache.updateMany(
     {stat_family_legacy: "Hits"},
     [{$set: {stat_family: "$stat_family_legacy"},
        $unset: ["stat_family_legacy"]}]
   )
   ```
3. **Re-run the replay** end-to-end. Cards/outputs are re-derivable from
   raw SGO. Use only if 1 and 2 are not viable.

---

## 6. Cheat sheet

| Need | One-liner |
|---|---|
| Token check | `curl -sH "X-Admin-Token: $PV_ADMIN_TOKEN" $PV_HOST/api/emergent-admin/auth/whoami` |
| List active jobs | `curl -sH "X-Admin-Token: $PV_ADMIN_TOKEN" "$PV_HOST/api/emergent-admin/jobs?status=running"` |
| Last-50 audit log | `curl -sH "X-Admin-Token: $PV_ADMIN_TOKEN" "$PV_HOST/api/emergent-admin/audit/?limit=50"` |
| Cancel a runaway job | `curl -X POST -H "X-Admin-Token: $PV_ADMIN_TOKEN" -d '{"confirm":true}' "$PV_HOST/api/emergent-admin/jobs/$JOB/cancel"` |
| Restart backend | `curl -X POST -H "X-Admin-Token: $PV_ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"service":"backend","confirm":true}' "$PV_HOST/api/emergent-admin/services/restart"` |
