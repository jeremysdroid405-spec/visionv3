# CHANGELOG

## 2026-05-20 — build_pp_research_core v1 (P0 last working item)

**Shipped:** `/app/backend/scripts/sgo/build_pp_research_core.py` (production-safe).

- Derives `sgo_pp_research_core` from `sgo_props_raw`, anchored on
  `book_id="prizepicks"`.
- Attaches all matching other-book quotes for the same
  `(event_id, player_id, stat_id, side, line, period_id)` group.
- Joins consensus (`fair_odds` / `book_odds` / `consensus_probability`)
  from `sgo_book_consensus`, preferring non-PP `odd_id`s.
- OOM-safe: month-chunked aggregation w/ `allowDiskUse=True`, bulk-write
  upserts in batches of 500.
- Idempotent: unique key `(event_id, player_id, stat_id, side, line, period_id)`.
- CLI flags: `--league`, `--start`, `--end`, `--dry-run`, `--drop-existing`, `--yes`.

**Bug found & fixed during synthetic testing:**
- Consensus lookup was using `async for ... .to_list(None)` — that returns
  a Future, not a cursor. Rewritten to `await cursor.to_list(length=1)`.

**Synthetic verification (preview pod, isolated DB):**
- ✅ dry-run writes 0 docs
- ✅ full run produces expected 5 anchors (orphan props w/o PP are dropped)
- ✅ idempotent re-run (count stable)
- ✅ `--league MLB` filter (NBA anchor excluded)
- ✅ `--drop-existing` without `--yes` exits 2 and preserves data
- ✅ date window excludes out-of-range months
- ✅ latest PP snapshot wins over earlier duplicates
- ✅ consensus attached when present, `null` otherwise

**Deploy tarball:** `/tmp/sgo_deploy/build_pp_research_core_v1.tar.gz` (6.4 K)
SHA256: `49887abe70ebe812e95f1d52d83593063fdb234b83df000bdfc4f3cc6cb1e20f`
