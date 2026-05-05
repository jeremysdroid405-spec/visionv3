# Universal SSOT Overwrite Firewall — Audit & Enforcement

**Status:** Active
**Created:** 2026-05-05
**Owner module:** `services/field_ownership/firewall.py`
**Test suite:** `backend/tests/test_field_ownership_firewall.py` (7 contracts, all green)

---

## What this is

A runtime + test-time enforcement layer that prevents non-owner code paths from overwriting any field declared in `services/field_ownership/registry.py`. Plugs the gap between the declarative registry (which described ownership) and the production code paths (which silently violated it via overlay, merge, fallback, alias).

Trigger: the Daniss-Jenkins cross-line cached_board leak — a `hit_rates` bag computed at line=9.5 was overwriting the canonical `hit_rate_l10` on a line=14.5 score doc.

---

## Public surface (`services.field_ownership.firewall`)

| Symbol | Purpose |
|---|---|
| `safe_overlay(target, source, *, owner_layer=False, exclude=())` | Overlay primitive that blocks owned-field writes from non-owner sources AND honours sticky-write (never replaces existing non-empty values). Returns `{applied, blocked, skipped_present}` for observability. |
| `assert_no_owned_overwrite(before, after, *, allowed=(), context="")` | Test/audit guard that raises `OwnedFieldOverwriteError` if any protected key changed value between two snapshots. |
| `protected_field_names() -> frozenset[str]` | Set of every storage-key currently protected. Derived from `FIELD_REGISTRY` at call time — adding a new registry entry auto-expands the firewall. |
| `OwnedFieldOverwriteError` | Class raised by `assert_no_owned_overwrite`. **Do not catch.** |

---

## 8-rule contract (matches user spec)

| # | Rule | Enforcement |
|---|---|---|
| 1 | Every owned field has exactly one writer | `FieldSpec.writers` allowlist in `registry.py` |
| 2 | Non-owner layers may read only | `safe_overlay()` default mode; `accessor.get_owned_field()` for canonical reads |
| 3 | Overlay fields cannot overwrite owned fields | `safe_overlay()` blocks owned keys when `owner_layer=False` |
| 4 | Fallbacks cannot source owned fields from aliases | `accessor` + frontend canonical-first preference (Contract 3 test) |
| 5 | Frontend reads canonical first | Static test parses `UniversalPlayerCard.jsx` and asserts `hit_rate_l10 ?? legacy` ordering |
| 6 | Line-specific fields cannot come from stat-level data | Sticky-write rule + per-site overlay restriction (e.g. `hit_rates` removed from `STAT_LEVEL_FIELDS`) |
| 7 | Player-level fields cannot overwrite prop-level data | `safe_overlay()` blocks by storage-key set; player-level `team`/`opponent` overlays still go through but never clobber existing prop values (sticky-write) |
| 8 | Enrichment-only fields preserve-on-replace | `_PRESERVE_ON_REPLACE` allowlist in `prop_scores_store.py`; Contract 4 pins required members |

---

## Owned-field audit (per-field)

For every entry in `FIELD_REGISTRY` (alphabetical):

| Field | Owner | Writer (single) | Reader paths | Overlay sites (firewall-protected) | Fallback paths | Frontend readers | Overwrite risk |
|---|---|---|---|---|---|---|---|
| `active` | `prop_scores.active` | `services/board/set_active.py:set_active` | reader, delta detector, ferrari | none (filter field) | none | n/a (filter) | LOW (single writer) |
| `computed_at` | `prop_scores.computed_at` | `services/scoring/prop_scores_store.py:write_versioned_scores` | drift_audit, shadow_capture, health probe | none | none | `—` if missing | LOW (single writer) |
| `cv` | `prop_scores.cv` | `services/scoring/adapters/{nba,mlb}_scoring.py:score` | gates, intel_suite, frontend | NBA stat-level overlay (now firewall-blocked) | local game-log std_dev (last-resort) | UniversalPlayerCard, PlayerDetailPage | MEDIUM → LOW (firewall) |
| `edge` (`edge_vs_fair`) | `prop_scores.edge_vs_fair` | `services/scoring/scoring_stack.py:compute_vision_score` | gates, ferrari | aliases stripped via `.pop()` in merge | none | reads canonical only | LOW |
| `hit_rate_l5` | `prop_scores.hit_rate_l5` | `services/scoring/adapters/{nba,mlb}_scoring.py:score` | gates, ferrari | NBA stat-level (firewall) | none | UniversalPlayerCard trio | LOW (firewall) |
| `hit_rate_l10` | `prop_scores.hit_rate_l10` | `services/scoring/adapters/{nba,mlb}_scoring.py:score` | gates, ferrari | NBA stat-level (firewall) | none | UniversalPlayerCard trio + chip | **WAS HIGH → LOW (firewall + chip rewrite)** |
| `hit_rate_l20` | `prop_scores.hit_rate_l20` | `services/scoring/recompute.py:recompute_sport` | gates | NBA stat-level (firewall) | none | UniversalPlayerCard trio | LOW (firewall) |
| `event_id` | `live_props.event_id` | `services/universal_odds_sync.py:_persist_prop` | scoring, routes | none | fail_loud | n/a | LOW |
| `game_start_utc` | `prop_scores.game_start_utc` | `services/scoring/recompute.py:recompute_sport` | scanner, ferrari merge | merge re-pins `commence_time` (same-owner alias) | none | hidden if missing | LOW |
| `line` | `live_props.line` | `services/universal_odds_sync.py:_persist_prop` | universal | none | fail_loud | required | LOW |
| `momentum_data` | `prop_scores.momentum_data` | `services/master_sync.py:_enrich_nba_momentum` | ferrari, player, picks_getter | NBA stat-level (firewall blocks; same writer also writes via mirror) | none | momentum chip | MEDIUM (mirror coverage) |
| `odds_type` | `pp_multiplier_lab.selected_projections.odds_type` | `services/pp_multiplier_lab.py:build_projection` | universal | none | normaliser maps unknown → "standard" | implicit | LOW |
| `opponent` | `live_props.opponent_team` | `services/universal_odds_sync.py:_build_prop_record` | ferrari, player, dvp, vegas, sim, mlb_vision | cached_board overlay (firewall) | live_props override re-pins post-merge | `—` if missing | MEDIUM → LOW (firewall + override) |
| `p_true` | `prop_scores.p_true_active` | `services/scoring/recompute.py:recompute_sport` | universal | none | fail_loud | required | LOW |
| `photo_url` | `master_hub.photo_url` | `services/bdl_universal_sync.py:sync_players` | picks_getter, card contract, player route | cached_board (firewall) | initials placeholder | initials when null | LOW |
| `player_name` | `master_hub.display_name` | `services/bdl_universal_sync.py:sync_players` | universal | merge re-pins | fail_loud | required | LOW |
| `pp_projection_id` | `pp_projection_id_cache.projection_ids[]` | `services/pp_multiplier_lab.py:seed_projection_ids_from_scraper` | pp_multiplier_lab, health probe | none | none (never synthesised) | implicit | LOW |
| `ranking_score_v2` | `prop_scores.ranking_score_v2` | `services/scoring/recompute.py:recompute_sport` | player, ferrari, vacuum, market_moves, board publisher | none | vision_score secondary-sort with one-time SSOT warn | implicit | LOW |
| `scored_at` | `prop_scores.scored_at` | `services/scoring/prop_scores_store.py:write_versioned_scores` | health probe | none | none | `—` if missing | LOW |
| `side` (`recommendation`) | `live_props.recommendation` | `services/universal_odds_sync.py:_build_prop_record` | universal | merge strips `direction` alias; firewall blocks | `direction` tolerance fallback (planned removal) | required | MEDIUM → LOW (firewall + alias strip) |
| `stat_type` | `live_props.stat_type` | `services/universal_odds_sync.py:_build_prop_record` | universal | none | none | required | LOW |
| `team` | `live_props.team` | `services/universal_odds_sync.py:_build_prop_record` | universal | cached_board (firewall) | none (route fallback chains removed) | `—` if missing | LOW (firewall) |
| `tier` | `prop_scores.tier` | `services/scoring/tiering.py:* + recompute_sport` | universal | none | fail_loud | required | LOW |
| `vision_intel` | `prop_scores.vision_intel` | `services/vision_intel/engine.py:enrich` (planned single writer) | ferrari, frontend | merge layer + master_sync writers | template fallback NULLIFIED 2026-05-04 | "Vision unavailable" when null | MEDIUM (multiple legacy writers being consolidated) |

---

## Wired overlay sites (firewall active)

| File | Site | Source | Notes |
|---|---|---|---|
| `routes/ferrari_tiers.py:1364-1390` | NBA stat-level overlay | `nba_cached_board.props[*]` | `safe_overlay()` blocks owned + sticky-write |
| `routes/ferrari_tiers.py:1370-1385` (player_doc) | NBA player-level overlay | `nba_cached_board.player` | `safe_overlay()` |
| `routes/player.py:399-426` | Per-prop + stat-level + player-level overlays | `nba_cached_board` | `safe_overlay()` × 3 (line-level, stat-level, player-level) |

`_merge_score_with_board` initial 5/4-tuple merge intentionally NOT wired through `safe_overlay()` — it's the line-EXACT path where cached_board values are the right grain. The sticky-write semantics still apply via per-key checks, and downstream score-doc field writes are explicit-owner unconditional writes.

MLB tier helper (`_get_mlb_tier_picks_from_scores`) does not perform a stat-level cached_board overlay (per design comment: MLB enrichment is pure-function downstream). No firewall wiring required.

---

## Test contracts (`tests/test_field_ownership_firewall.py`)

```
test_cached_board_cannot_overwrite_owned_prop_fields                    PASS
test_route_merge_cannot_overwrite_owned_fields_assertion                PASS
test_frontend_does_not_prefer_legacy_aliases_over_canonical             PASS
test_preserve_on_replace_fields_survive_full_recompute                  PASS
test_unknown_aliases_are_rejected                                       PASS
test_protected_field_names_includes_every_registered_owned_field        PASS
test_safe_overlay_owner_layer_escape_hatch_writes_unconditionally       PASS
```

**Live API verification (Daniss Jenkins WZ P+A 14.5 OVER):** `hit_rate_l5/l10/l20 = 20/20/60`, `h10_rate = 20`, `hit_rates = None`, `tier = war_zone` — canonical SSOT preserved.

---

## What this does NOT do

- **No feature changes.** Cap, gate thresholds, scoring math, badge logic, prompts — all untouched.
- **No new fields invented.** The firewall only protects what's already in `FIELD_REGISTRY`.
- **No retroactive backfill.** Already-stored cross-line `hit_rates` in `nba_cached_board` documents remain — the firewall stops them from leaking into API responses, not from existing on disk.
- **No frontend behaviour change.** The two `UniversalPlayerCard.jsx` chip patches were SSOT preference order edits (canonical first); the rendered value matches the canonical.

---

## Adding a new owned field

1. Add a `FieldSpec(...)` entry to `FIELD_REGISTRY` in `registry.py`.
2. If the storage key differs from the public name, add it to both `_STORAGE_FIELD_MAP` (in `accessors.py`) and `_PUBLIC_TO_STORAGE` (in `firewall.py`).
3. Run `pytest tests/test_field_ownership_firewall.py::test_protected_field_names_includes_every_registered_owned_field` — must pass.
4. Wire `safe_overlay()` at any new overlay site that touches the field.

The firewall scales with the registry — no manual sync.
