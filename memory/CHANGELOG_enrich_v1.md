# CHANGELOG

## 2026-05-20 — build_historical_consensus_probabilities v1

**Shipped:** `/app/backend/scripts/sgo/build_historical_consensus_probabilities.py`

- Reads `sgo_pp_research_core` (immutable), writes `sgo_pp_research_core_enriched`.
- Per-book pair-then-devig fair probabilities (only when same book quotes both
  sides on the same anchor line) → mean across books = `consensus_probability`.
- Sharp-only subset (`draftkings, fanduel, pinnacle, circa, bet365, betonline`).
- Derived fields: `consensus_probability`, `sharp_consensus_probability`,
  `pp_implied_probability`, `edge_vs_consensus`, `best_book_probability`,
  `best_book_id`, `best_book_edge`, `devig_book_count`, `sharp_book_count`,
  `book_count`, `market_width`, `consensus_disagreement`, `has_valid_devig`,
  `enrichment_version="v1"`, `enriched_at`.
- Preserves every source field via `{**source_doc, **enrichment_fields}` merge.
- OOM-safe: chunked by `game_date`, bulk_write batches of 1000.
- Resumable: `--resume` skips docs already at current version.
- Progress logs every 10k processed docs.
- Indexes: anchor pk + (league, game_date, player, stat, edge, consensus_p,
  has_valid_devig, enrichment_version).

**Synthetic verification** (preview pod, isolated DB, 9 anchors / 5 pairs):
- ✅ dry-run writes 0 docs
- ✅ manual pair-then-devig math matches script output to 1e-9
- ✅ siblingless prop → `has_valid_devig=False`, `consensus_probability=None`
- ✅ sibling-with-no-book-overlap → `devig_book_count=0`, `has_valid_devig=False`
- ✅ idempotent re-run (count stable)
- ✅ `--resume` skips already-enriched docs
- ✅ `--league MLB` filter (NBA anchors excluded)
- ✅ `--drop-existing` without `--yes` exits 2, preserves data
- ✅ date-window filter
- ✅ all required indexes created

**Deploy tarball:** `/tmp/sgo_deploy/build_historical_consensus_probabilities_v1.tar.gz` (7.7 K)
SHA256: `6c5886b0d67f1e1832436451b44e2c3a6bddb69f36942cddfe252ad2f152b7f5`
