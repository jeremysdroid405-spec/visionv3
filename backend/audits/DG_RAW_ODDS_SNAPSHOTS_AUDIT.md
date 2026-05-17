# `dg_raw_odds_snapshots` — Read-Only Audit

**Generated:** 2026-05-17
**Mode:** READ-ONLY — no mutations executed

---

## 1. COLLECTION STATS

| Metric | Value |
|---|---:|
| Document count | **14,602,326** |
| Logical size (BSON) | **13,742.4 MB** |
| Storage size (WT compressed) | **1,564.0 MB** |
| Avg doc size | 986 bytes |
| Total index size | 715.9 MB |
| nIndexes | 6 |
| Oldest `fetched_at` | **2026-05-16T04:02:08Z** |
| Newest `fetched_at` | **2026-05-17T15:29:14Z** |
| Window | ~36 hours |
| Writes in last 1 h | **11,598** |
| Writes in last 24 h | **14,547,537** |
| Writes in last 7 d | **15,767,540** (collection only exists ~36 h, so this equals total) |

---

## 2. SAMPLE DOCUMENTS

### Sample A — most recent (NBA spread)
```json
{
  "_id": "6a09df1b3360f686ad6474dd",
  "scrape_id": "nba|465746f51ad349641512f36c52306968|2026-05-17T15:29:14...",
  "fetched_at": "2026-05-17T15:29:14.513557+00:00",
  "sport": "nba",
  "event_id": "465746f51ad349641512f36c52306968",
  "commence_time": "2026-05-18T00:10:00Z",
  "home_team": "Detroit Pistons",
  "away_team": "Cleveland Cavaliers",
  "bookmaker": "draftkings",
  "market_key": "h2h",
  "market_class": "standard",
  "raw_market_json": { "key": "h2h", "last_update": "...", "description": null },
  "raw_outcome_json": { "name": "Cleveland Cavaliers", "price": 154 },
  "outcome_name": "Cleveland Cavaliers",
  "outcome_description": null,
  "outcome_point": null,
  "outcome_price": 154,
  "canonical_candidate": null,
  "source_file": "universal_odds_sync._persist_raw_markets",
  "ingest_version": "v1.0_2026_05_17"
}
```

### Sample B — player prop (NBA 3-pointers under)
```json
{
  "scrape_id": "nba|...|2026-05-16T04:02:08...",
  "fetched_at": "2026-05-16T04:02:08+00:00",
  "sport": "nba",
  "bookmaker": "fliff",
  "market_key": "player_threes",
  "market_class": "standard",
  "raw_outcome_json": { "name": "Under", "description": "Ayo Dosunmu",
                         "price": -955, "point": 0.5 },
  "outcome_description": "Ayo Dosunmu",
  "outcome_point": 0.5,
  "outcome_price": -955,
  "canonical_candidate": "nba|...|Ayo Dosunmu|3PM|0.5|UNDER",
  "ingest_version": "v1.0_2026_05_17"
}
```

### Sample C — alternate-line spread (NBA)
```json
{
  "sport": "nba", "market_key": "spreads", "market_class": "standard",
  "raw_outcome_json": { "name": "Minnesota Timberwolves",
                         "price": -118, "point": 31.5 },
  "outcome_point": 31.5, "outcome_price": -118
}
```

### Detected key fields
- sport ✅ (`mlb`, `nba`)
- event_id ✅
- market_key ✅ (`spreads`, `h2h`, `player_threes`, `player_total_bases`, …)
- bookmaker ✅
- timestamps ✅ (`fetched_at` ISO + `scrape_id`)
- odds ✅ (`outcome_price`, `outcome_point`)
- **grading/result fields: NONE** — pure pre-game snapshot store

---

## 3. INDEXES (6)

| Name | Key | Unique |
|---|---|:---:|
| `_id_` | `{_id: 1}` | n/a |
| `canonical_fetched_at` | `{canonical_candidate: 1, fetched_at: -1}` | ❌ |
| `event_fetched_at` | `{event_id: 1, fetched_at: -1}` | ❌ |
| `player_fetched_at` | `{outcome_description: 1, fetched_at: -1}` | ❌ |
| `scrape_id` | `{scrape_id: 1}` | ❌ |
| `fetched_at_desc` | `{fetched_at: -1}` | ❌ |

All non-unique — confirms append-only contract.

---

## 4. CODEBASE REFERENCES

| File | Line | Function | Direction |
|---|---:|---|---|
| `services/universal_odds_sync.py` | 945 | `_persist_raw_markets` | **WRITE** (`insert_many`, append-only) |
| `routes/admin_odds_audit.py` | 94 | `GET /api/admin/odds/raw-snapshots` | **READ** (query by player/event/book/market/time) |
| `routes/admin_odds_audit.py` | 153 | `GET /api/admin/odds/canonical-trace` | **READ** (most recent 5 per canonical_key) |
| `tests/test_odds_pipeline_hardening.py` | 256, 294, 308 | unit tests | **READ + WRITE** in fixtures only |
| `audits/odds_pipeline_hardening_2026_05_17.md` | — | design doc | documentation only |

**Frontend references:** **NONE.** No React code touches this collection.

**Server mount confirmed:** `server.py:1680` includes the audit router.

---

## 5. ACTIVE RUNTIME USAGE

| Usage path | Active? | Details |
|---|:---:|---|
| **Cron writes** | ✅ **YES, currently writing** | Every `universal_odds_sync.sync_event()` call's `_persist_raw_markets` step inserts. ~11.5K docs/hour. |
| Live API reads | ❌ Admin-only | Only `/api/admin/odds/raw-snapshots` + `/canonical-trace` query it. Both are debug/forensic endpoints, not user-facing. |
| Replay reads | ❌ **NO** | `mlb_replay_engine.py`, `production_replay_runner.py`, `mlb_feature_cache.py`, `tier_evaluator.py` — **zero references**. |
| Forward testing reads | ❌ NO | Not consumed by `picks_getter_service.py`, `scoring/`, or `mlb_high_friction_model.py`. |
| Phase 2c production replay | ❌ NO | The Phase 2c orchestrator reads `mlb_replay_model_outputs`, never this collection. |
| Legacy migration target | ✅ N/A — born new | Collection is **newly introduced 2026-05-17** as part of the odds-pipeline hardening (see `audits/odds_pipeline_hardening_2026_05_17.md`). It is NOT a legacy collection. |

**Status:** Write-heavy forensic store. Reads happen ONLY through 2 admin endpoints. Not on any user-facing or model-scoring hot path.

---

## 6. TIMELINE / STALENESS

- **Born:** 2026-05-16 (~36 hours ago) — see `audits/odds_pipeline_hardening_2026_05_17.md` § "NEW collection".
- **Last write:** 2026-05-17T15:29:14Z (continuously written).
- **Growth rate:** 11.6 k inserts/hour ⇒ ~280 k/day ⇒ at current growth, ~100 M/year.
- **No retirement plan** in the codebase. No TTL index. Will grow unbounded unless capped.

NOT replaced by any newer collection. It IS the newer collection — the legacy companion is `dg_raw_odds_markets` (latest-state cache, overwrite-on-scrape, much smaller).

---

## 7. SAFETY ASSESSMENT

**Classification: B — active but replaceable (with conditions).**

| Pro / Con | Detail |
|---|---|
| ✅ Pro — currently written | Active append every minute |
| ✅ Pro — has 2 admin read endpoints | `/api/admin/odds/raw-snapshots` + `/canonical-trace` |
| ✅ Pro — full audit metadata captured | scrape_id, ingest_version, source_file |
| 🔸 Mid — purely forensic | Not on any prediction/scoring/grading hot path |
| 🔸 Mid — small storage footprint | 1.56 GB on disk (WiredTiger compresses 13.7 GB BSON to 1.56 GB) |
| ❌ Con — unbounded growth | No TTL, no retention, ~100 M/year projected |
| ❌ Con — only 36 hours of history exists today | Recent value is small; long-term value depends on whether you'll actually query historic snapshots |

**Disk recoverable if dropped (current state):**
- BSON logical: **13.7 GB**
- WT storage: **1.56 GB**
- Indexes: **716 MB**
- **Total physical reclaim: ~2.28 GB**

The `/app` partition is currently 9.8 GB / 97% used and `/data/db` sits on the same volume. Dropping this single collection frees **~2.3 GB** instantly — the largest single recoverable chunk that isn't on the production hot path.

---

## 8. SAFE NEXT STEPS — Recommendation

**PARTIAL PRUNE (NOT immediate drop).**

Rationale:
- The collection is genuinely useful for forensic/audit. Don't lose the capability.
- The last 24 h is the most likely interrogation window for any production issue.
- Anything older than 7 days has no realistic forensic value today.

Recommended retention strategy (to discuss before executing):

1. **Add TTL index on `fetched_at`** — `expireAfterSeconds=604800` (7 days). MongoDB will reap older docs automatically. Reclaims most of the disk immediately and prevents future unbounded growth.
2. **Cap the in-memory verbatim JSON payload size**. The `raw_market_json` block is already header-only — but `raw_outcome_json` carries the full outcome dict. We could trim unused keys (e.g. `multiplier` is consistently null in the 14M docs).
3. **(Optional) Sample-write instead of full-write**. Today every outcome of every market of every book of every event for every scrape inserts. We could write every Nth scrape verbatim and only write deltas/anomalies in between. Big design change — defer.

### What I am NOT recommending
- ❌ Immediate drop — the collection has live readers (admin audit endpoints) and active writers. Dropping it would silently break audit functionality and the next write would auto-recreate without indexes.
- ❌ Archive+rename — adds complexity without freeing space.
- ❌ Export — no downstream system consumes it; export would just be a tombstone.

### Decision required from you
- Apply TTL=7d? TTL=24h? TTL=14d?
- Or keep as-is and free space elsewhere (the ad-hoc `backtest5625/cache/model_outputs.pkl` 39 MB, `.git` GC, etc.)?

The current situation is sustainable for ~24 more hours at write rate, after which `/data/db` will start pressuring the shared `/app` partition again.

---

## Quick numerical snapshot for your decision

```
collection size on disk : 1,564 MB  (WT compressed)
expected growth/day     : ~280 MB   (~280 k inserts × 986 bytes avg, WT compressed ~10%)
shared partition free   : 345 MB    (right now)
TTL=7d reclaim          : ~5,000 MB (estimated — current 36h scaled to 7d retention from open growth)
TTL=24h reclaim         : ~1,250 MB
```

Audit complete. No mutations executed.
