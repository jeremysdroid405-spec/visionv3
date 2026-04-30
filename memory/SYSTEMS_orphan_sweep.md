# Orphan Collection Sweep — 2026-04-30

## What this is
Documentation for the P0 #6 drop of 9 orphan/archive/backup collections
totaling 861,813 docs and 188.9 MB.

## Why these were dropped
None of the 9 collections were read from or written to by any runtime
code in `services/` or `routes/`. The only references were:

1. Docstring comments in `services/config/collection_names.py` (prose
   only, not code)
2. One dead `_SPORT_COLLECTIONS["prop_scores_archive"]` entry that was
   not imported or used anywhere

They were 8 months of rename-residue — old collections left behind
when their successors were created.

## Concrete harm being caused
- Confusion for new/forked agents reading the DB schema
- Database-wide queries (`list_collection_names`, admin tools) scan
  them needlessly
- Atlas tier storage cost (189 MB)
- Index memory footprint on the Mongo daemon

## The 9 dropped collections

| Collection | Docs | Size | Manifest kept? |
|---|---|---|---|
| `dg_cached_board_backup` | 114 | 15.2 MB | ✓ |
| `dg_events_cache_backup` | 1 | 0.0 MB | ✓ |
| `dg_live_props_backup` | 2,560 | 2.9 MB | ✓ |
| `dg_master_roster_backup` | 5,000 | 1.9 MB | ✓ |
| `dg_odds_cache_backup` | 182 | 5.3 MB | ✓ |
| `line_history_backup` | 801,955 | 93.1 MB | ✓ |
| `mlb_prop_scores_archive_stale_tags` | 10,090 | 13.1 MB | ✓ |
| `nba_prop_scores_archive_stale_tags` | 41,793 | 57.4 MB | ✓ |
| `referee_assignments_backup` | 118 | 0.0 MB | ✓ |
| **TOTAL** | **861,813** | **188.9 MB** | |

## Audit trail — manifest files
For each dropped collection, a JSON manifest was written to
`/app/backend/data/snapshots/archives/` BEFORE the drop. Each
manifest contains:
- Doc count, byte size, index count
- Complete field schema (union of fields seen in samples)
- Oldest + newest doc's ObjectId-derived timestamp
- 10 oldest + 10 newest full documents as forensic samples

Manifests are ~4–20 KB each. Preserved under git version control so
the audit trail cannot be lost to future sweeps.

## Code changes
- `services/config/collection_names.py` — removed dead `prop_scores_archive`
  entry from `_SPORT_COLLECTIONS`
- `scripts/sweep_orphan_collections.py` — new, idempotent dropper
- `tests/test_orphan_sweep_integrity.py` — regression guard

## Invariants (DO NOT BREAK)

**INV-1: None of the 9 dropped collection names may reappear in the
database.** Enforced by `test_orphan_collections_stay_dropped`. If the
name reappears the test tells you which and how many docs.

**INV-2: None of the 9 names may be reintroduced in
`_SPORT_COLLECTIONS`.** Enforced by
`test_orphan_names_not_reintroduced_in_collection_config`.

**INV-3: Manifest directory and all 9 manifest files must exist.**
Enforced by `test_manifest_directory_exists`. Losing the audit trail
defeats the purpose of the safe-sweep design.

## Recovery
If you need any of these collections back:
1. Short-term (< 2 days ago): Atlas daily snapshot restore — the
   free tier retains 2 snapshots automatically. Instructions:
   https://docs.atlas.mongodb.com/backup/cloud-backup/overview/
2. Long-term: The manifest files in
   `/app/backend/data/snapshots/archives/` preserve schema + representative
   samples for forensic reference, even if no backup exists.

## If a "legitimate" reason to reuse a name appears
**Don't.** Rename the new collection to something distinct. The old
name will be forever associated with the pre-sweep residue in commit
history; reusing it invites confusion.

## Re-running
The sweep script is IDEMPOTENT:
```
cd /app/backend && python scripts/sweep_orphan_collections.py
```
After the initial sweep, subsequent runs report `skipped (already absent)`
for all 9 entries — no action taken.
