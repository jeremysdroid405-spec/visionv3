"""
NBA Replay Engine — Layer 3.
============================

Thin wrapper around the PRODUCTION NBA scorer
(`services.scoring.recompute.recompute_sport` →
`NBAScoringAdapter.build_context` → `compute_scoring_stack` →
Universal Gate Engine).

Honours the cross-sport replay infrastructure contract — see
`services.replay.contract` for the SSOT policy. Specifically:
  - `bypass_eligibility=True` (no `filter_priceable` /
    `filter_pp_playable` row drops; tier as METADATA only)
  - `dry_run=True`             (production `nba_prop_scores` never
    mutated by a replay run)
  - `write_mode="upsert"`      (score docs returned directly)

Contract: same scoring logic, historical inputs, replay outputs.

  * Input  : `sgo_replay_alt_odds_raw` rows (per-book per-side flat
             rows) for one `(game_date, snapshot_iso)`.
  * Output : per-(book, side) rows in `nba_replay_model_outputs`,
             carrying the exact fields the MLB Layer-3 engine emits
             (`projection_mu`, `sigma`, `model_probability`, `edge`,
             `hit_rate_l5/10/20`, `cv`, …) PLUS the production-gate
             decisions (`tier`, `gate_pass`, `vision_score`,
             `vision_score_raw`, `tp`, `tp_source`, `edge_pct`,
             `model_projection`, `model_sigma`, …) computed by the
             SAME scoring pipeline live serving uses.

Leakage safety: each reshaped prop carries `commence_time = `
`{game_date}T{HH}:00:00Z`. `NBAScoringAdapter` extracts
`before_date = commence_time[:10]` and uses it as the "do-not-peek"
cutoff for all recency / availability / rate × minutes / shadow
recipes that read `bdl_game_logs`.
"""
from __future__ import annotations
import gc
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil
from pymongo import ASCENDING, UpdateOne

from services.replay.historical_alt_odds_ingest import normalize_player_name
from services.scoring.canonical_stats import canonical_stat_type, stat_family as canonical_stat_family

logger = logging.getLogger(__name__)

# Compliance flag read by `tests/test_replay_infrastructure_contract.py`
# to enforce the cross-sport replay contract via static audit.
REPLAY_CONTRACT_COMPLIANT = True

OUT_COLL = "nba_replay_model_outputs"
STATUS_COLL = "nba_replay_model_status"
# Version pin — bumped whenever this engine's behaviour materially
# changes. The underlying scoring pipeline carries its own per-run
# pins (recompute version_tag, gate config sha, model artefact SHAs)
# which are stamped on the production_replay_runs doc separately.
SCORING_CONFIG_VERSION = "nba_replay_v1_recompute_wrap_2026_06_02"
SOURCE_VERSION = "nba_replay_engine_v1_2026_06_02"

DEFAULT_MEM_LIMIT_MB = 3_500


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _american_to_implied(odds: int) -> float:
    odds = int(odds)
    if odds < 0:
        return (-odds) / ((-odds) + 100)
    return 100.0 / (odds + 100)


async def ensure_indexes(db) -> None:
    await db[OUT_COLL].create_index(
        [("game_date", ASCENDING), ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING), ("line", ASCENDING),
         ("side", ASCENDING), ("book", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING)],
        name="nba_replay_outputs_compound_unique", unique=True,
    )
    await db[OUT_COLL].create_index("game_date")
    await db[OUT_COLL].create_index("stat_family")
    await db[OUT_COLL].create_index([("game_date", ASCENDING),
                                       ("edge", ASCENDING)])
    await db[STATUS_COLL].create_index(
        [("game_date", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING)],
        name="nba_replay_status_unique", unique=True,
    )


# ── Player identity index ─────────────────────────────────────────────
async def _build_bdl_id_index(db) -> Dict[str, int]:
    """Build `{normalized_player_name: bdl_player_id}` lookup from the
    NBA master hub. `NBAScoringAdapter` requires `bdl_player_id` on
    every prop (Global Identity Rule); the historical reshape does not
    stamp it because the SGO source uses internal `player_id` keys.
    """
    idx: Dict[str, int] = {}
    cursor = db["nba_master_hub_2026"].find(
        {}, {"_id": 0, "display_name": 1, "player_name": 1,
             "bdl_id": 1, "bdl_player_id": 1},
    )
    async for r in cursor:
        pid = r.get("bdl_player_id") or r.get("bdl_id")
        if pid is None:
            continue
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            continue
        for raw_name in (r.get("display_name"), r.get("player_name")):
            if not raw_name:
                continue
            n = normalize_player_name(raw_name)
            if n and n not in idx:
                idx[n] = pid_i
    return idx


# ── Reshape: historical alt-odds rows → live-prop-shaped batch ────────
_BOOK_TO_LAYER = {
    "draftkings": "dk", "fanduel": "fd", "betmgm": "mgm",
    "betonline": "bol", "caesars": "csr", "espnbet": "eb",
    "hardrockbet": "hrb", "bovada": "brv", "pinnacle": "prx",
    "betrivers": "bly", "fliff": "flf", "prizepicks": "pp",
}


def _layer_prefix(book: str) -> Optional[str]:
    return _BOOK_TO_LAYER.get((book or "").strip().lower())


def _reshape_to_live_props(
    odds_rows: List[Dict[str, Any]],
    *,
    bdl_id_index: Dict[str, int],
    snapshot_iso: str,
) -> Tuple[List[Dict[str, Any]],
           Dict[Tuple[str, str, str, float, str], List[Dict[str, Any]]],
           Dict[str, int]]:
    """Group per-book odds rows into one canonical live-prop dict per
    `(event, player, market, line, side)`. Stamps:
        - `pp_layer` / `{book}_layer` nested book dicts
        - `pp_odds`, `dk_odds`, `fd_odds`, … flat fields
        - `bdl_player_id` from the master-hub index
        - `playable_on_pp` = True when any PP quote present
        - `book_count` = number of distinct books behind the canonical key

    Returns:
      (props, source_rows_by_key, skipped_counts)
      `source_rows_by_key` keeps the original odds rows for downstream
      per-(book, side) fan-out. `skipped_counts` is a `{reason: n}` map
      for observability.
    """
    by_key: Dict[Tuple[str, str, str, float, str], Dict[str, Any]] = {}
    src_by_key: Dict[Tuple[str, str, str, float, str], List[Dict[str, Any]]] = {}
    skipped: Dict[str, int] = {}

    for r in odds_rows:
        pname = r.get("player_name") or ""
        norm_p = r.get("player_name_normalized") or normalize_player_name(pname)
        market_raw = r.get("market") or r.get("stat") or ""
        stat_type = canonical_stat_type("nba", market_raw)
        if not stat_type or stat_type == market_raw and not market_raw:
            skipped["no_stat_type"] = skipped.get("no_stat_type", 0) + 1
            continue
        try:
            line = float(r.get("line"))
        except (TypeError, ValueError):
            skipped["bad_line"] = skipped.get("bad_line", 0) + 1
            continue
        side = (r.get("side") or "").upper()
        if side not in ("OVER", "UNDER"):
            skipped["bad_side"] = skipped.get("bad_side", 0) + 1
            continue
        event_id = r.get("event_id") or ""
        if not event_id or not norm_p:
            skipped["missing_id"] = skipped.get("missing_id", 0) + 1
            continue
        try:
            odds_i = int(r.get("odds"))
        except (TypeError, ValueError):
            skipped["bad_odds"] = skipped.get("bad_odds", 0) + 1
            continue
        bk = (r.get("book") or "").strip().lower()
        if not bk:
            skipped["no_book"] = skipped.get("no_book", 0) + 1
            continue

        k = (event_id, norm_p, stat_type, line, side)
        src_by_key.setdefault(k, []).append(r)

        if k not in by_key:
            bdl_pid = bdl_id_index.get(norm_p)
            by_key[k] = {
                # Identity
                "player_name": pname or norm_p,
                "player_name_normalized": norm_p,
                "bdl_player_id": bdl_pid,
                "identity_status": "resolved" if bdl_pid is not None else "missing_bdl_id",
                # Market
                "stat_type": stat_type,
                "market": market_raw,
                "market_key": market_raw,
                "line": float(line),
                "recommendation": side,
                "direction": side,
                "is_alternate_market": bool(r.get("is_alternate")),
                # Event context
                "event_id": event_id,
                "home_team": r.get("home_team"),
                "away_team": r.get("away_team"),
                "commence_time": r.get("commence_time") or f"{r.get('game_date')}T22:00:00Z",
                # Slate-level flags (eligibility chain)
                "playable_on_pp": False,
                "pp_available": False,
                "book_count": 0,
                "_books_seen": set(),
                # Used internally to fan out per-book rows on the output side.
                "_snapshot_iso": snapshot_iso,
                "_game_date": r.get("game_date"),
                "sport": "nba",
            }

        prop = by_key[k]
        prop["_books_seen"].add(bk)
        prefix = _layer_prefix(bk)
        layer_doc = {"book": bk, "line": float(line), "odds": odds_i}
        if prefix is not None:
            prop[f"{prefix}_layer"] = layer_doc
            prop[f"{prefix}_line"] = float(line)
            prop[f"{prefix}_odds"] = odds_i
            if prefix == "pp":
                prop["playable_on_pp"] = True
                prop["pp_available"] = True
                prop["source_anchor"] = "prizepicks"
                prop["anchor_book"] = "prizepicks"

    # Finalise: drop the internal set + compute book_count
    out: List[Dict[str, Any]] = []
    for k, prop in by_key.items():
        prop["book_count"] = len(prop["_books_seen"])
        prop["coverage_class"] = (
            "multi" if prop["book_count"] >= 2 else "single"
        )
        prop["books_anchored"] = sorted(prop["_books_seen"])
        del prop["_books_seen"]
        out.append(prop)
    return out, src_by_key, skipped


# ── Output projection (score doc → per-book Layer-3 row) ──────────────
_QUALIFIED_TIERS = frozenset({"safe_haven", "front_lines", "war_zone"})


def _project_score_doc_to_layer3_row(
    score_doc: Dict[str, Any],
    *,
    odds_row: Dict[str, Any],
    snapshot_iso: str,
) -> Dict[str, Any]:
    """Compose ONE Layer-3 row for a specific (book, side) tuple.

    Inherits the per-prop scoring outputs from `score_doc` (μ / σ /
    p_model / cv / hit_rate_l5/10/20 / vision_score / tier / gate
    decisions) and computes book-specific implied_probability + edge
    from the row's `odds`. This mirrors how `mlb_replay_engine`
    memoises μ per (player, family, line) and fans out per (book,
    side) at write time.
    """
    side = (odds_row.get("side") or "").upper()
    line_f = float(odds_row["line"])
    odds_i = int(odds_row["odds"])
    implied = _american_to_implied(odds_i)

    p_model = score_doc.get("p_true_active")
    p_over = p_model if p_model is not None else None
    # `ctx.p_model` is side-aware (already computed for the side this
    # score doc represents). For Layer-3 storage we standardise on
    # `model_probability = P(OVER)` so downstream consumers can
    # recompute fair-prob for any side. Convert UNDER score docs back
    # to OVER perspective.
    rec = (score_doc.get("recommendation") or "OVER").upper()
    if p_model is not None:
        if rec == "OVER":
            p_over = float(p_model)
        else:
            p_over = 1.0 - float(p_model)

    fair_prob = (
        p_over if side == "OVER"
        else (1.0 - p_over if p_over is not None else None)
    )
    edge = (fair_prob - implied) if fair_prob is not None else None

    fam = canonical_stat_family("nba", score_doc.get("stat_type"))

    row = {
        # Identity / keying
        "sport": "nba",
        "game_date": odds_row["game_date"],
        "event_id": odds_row["event_id"],
        "home_team": odds_row.get("home_team"),
        "away_team": odds_row.get("away_team"),
        "commence_time": odds_row.get("commence_time"),
        "snapshot_iso": snapshot_iso,
        "player_name_normalized": odds_row.get("player_name_normalized"),
        "player_name": score_doc.get("player_name") or odds_row.get("player_name"),
        "player_id": score_doc.get("bdl_player_id"),
        "team": score_doc.get("team"),
        "opponent": score_doc.get("opponent") or score_doc.get("opponent_team"),
        # Market
        "market": odds_row.get("market"),
        "is_alternate": bool(odds_row.get("is_alternate")),
        "stat_type": score_doc.get("stat_type"),
        "stat_family": fam,
        "line": line_f,
        "side": side,
        "book": odds_row.get("book"),
        "odds": odds_i,
        # Model outputs (from production scorer)
        "projection_mu": score_doc.get("model_projection"),
        "sigma": score_doc.get("model_sigma"),
        "model_probability": p_over,
        "fair_probability": fair_prob,
        "implied_probability": implied,
        "edge": edge,
        # SSOT scoring fields produced by `compute_scoring_stack`
        "tp": score_doc.get("tp"),
        "tp_source": score_doc.get("tp_source"),
        "edge_pct": score_doc.get("edge_pct") or score_doc.get("edge_vs_fair"),
        "cv": score_doc.get("cv"),
        "cv_status": score_doc.get("cv_status"),
        # Side-aware hit-rate panels
        "hit_rate_l5":  score_doc.get("hit_rate_l5"),
        "hit_rate_l10": score_doc.get("hit_rate_l10"),
        "hit_rate_l20": score_doc.get("hit_rate_l20"),
        "hit_rate_sample_size": score_doc.get("hit_rate_sample_size"),
        # Production gate decision (computed by Universal Gate Engine).
        # `gate_pass=True` ONLY when production landed the prop in one
        # of the three qualifying tiers (safe_haven / front_lines /
        # war_zone). `unqualified` and `rejected` both stamp False —
        # they carry distinct semantic signal through `tier_reason`.
        "vision_score":     score_doc.get("vision_score"),
        "vision_score_raw": score_doc.get("vision_score_raw"),
        "tier":             score_doc.get("tier"),
        "tier_reason":      score_doc.get("tier_reason"),
        "gate_pass":        bool(
            score_doc.get("tier") in _QUALIFIED_TIERS),
        "tier_gate_results": score_doc.get("tier_gate_results"),
        # Production p_true diagnostics
        "p_true_active":    score_doc.get("p_true_active"),
        "p_true_method":    score_doc.get("p_true_method"),
        "p_true_hit_rate":  score_doc.get("p_true_hit_rate"),
        "p_true_model":     score_doc.get("p_true_model"),
        "p_true_vk2":       score_doc.get("p_true_vk2"),
        "model_projection_direct": score_doc.get("model_projection_direct"),
        "model_sigma_direct":      score_doc.get("model_sigma_direct"),
        "model_projection_synth":  score_doc.get("model_projection_synth"),
        "model_sigma_synth":       score_doc.get("model_sigma_synth"),
        "projection_method":       score_doc.get("projection_method"),
        # Multi-book devig + universal alt-market field
        "devig_method":      score_doc.get("devig_method"),
        "book_count":        score_doc.get("book_count"),
        "coverage_class":    score_doc.get("coverage_class"),
        "books_anchored":    score_doc.get("books_anchored"),
        # Identity audit
        "bdl_player_id":   score_doc.get("bdl_player_id"),
        "identity_status": score_doc.get("identity_status"),
        # NBA-specific audit panels (passed through as-is)
        "mu_recency_blended":       score_doc.get("mu_recency_blended"),
        "availability_status":      score_doc.get("availability_status"),
        "availability_guard_applied": score_doc.get("availability_guard_applied"),
        "rate_model_applied":       score_doc.get("rate_model_applied"),
        "expected_minutes":         score_doc.get("expected_minutes"),
        # Provenance
        "scoring_config_version": SCORING_CONFIG_VERSION,
        "source_version": SOURCE_VERSION,
        "replayed_at": datetime.now(timezone.utc),
    }
    return row


# ── Public entrypoint ────────────────────────────────────────────────
async def replay_date(
    db, replay_date_str: str, *,
    snapshot_iso: Optional[str] = None,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    force: bool = False,
    odds_collection: str = "sgo_replay_alt_odds_raw",
) -> Dict[str, Any]:
    """Warm-replay a single NBA date through the production scorer.

    Reads odds from `odds_collection` filtered by
    `{sport:"nba", game_date, snapshot_iso}`, reshapes to NBA
    live-prop shape, calls `recompute_sport(db, "nba", ..., dry_run=True,
    write_mode="upsert")`, fans the resulting score docs out per
    (book, side) into `nba_replay_model_outputs`. `nba_prop_scores`
    is never mutated (dry_run=True).
    """
    await ensure_indexes(db)
    if snapshot_iso is None:
        snapshot_iso = f"{replay_date_str}T11:00:00Z"

    s_filter = {"game_date": replay_date_str, "snapshot_iso": snapshot_iso,
                "scoring_config_version": SCORING_CONFIG_VERSION}
    if not force:
        s = await db[STATUS_COLL].find_one(s_filter, {"_id": 0, "status": 1})
        if s and s.get("status") == "completed":
            return {"date": replay_date_str, "snapshot_iso": snapshot_iso,
                    "skipped": True}

    started_at = datetime.now(timezone.utc)
    rss0 = _rss_mb()

    await db[STATUS_COLL].update_one(
        s_filter,
        {"$set": {"status": "in_progress", "started_at": started_at,
                  "rss_mb_start": round(rss0, 1),
                  "scoring_config_version": SCORING_CONFIG_VERSION,
                  "source_version": SOURCE_VERSION}},
        upsert=True,
    )

    # 1. Resolve the snapshot_iso through the cross-sport fallback
    #    policy so we NEVER silently score zero rows when the
    #    orchestrator-supplied snapshot tag doesn't match the
    #    ingest-time label. Three tiers (exact → latest-for-date →
    #    any-for-date) — see services/replay/snapshot_resolver.py.
    from services.replay.snapshot_resolver import (
        resolve_snapshot_iso,
        SNAPSHOT_TIER_EXACT, SNAPSHOT_TIER_ANY_DATE, SNAPSHOT_TIER_NONE,
    )
    resolved_snap, snap_tier, snap_telemetry = await resolve_snapshot_iso(
        db, sport="nba", game_date=replay_date_str,
        requested_snapshot_iso=snapshot_iso,
        odds_collection=odds_collection,
    )
    logger.info(
        "[nba_replay] %s/%s snapshot resolution: tier=%s telemetry=%s",
        replay_date_str, snapshot_iso, snap_tier, snap_telemetry,
    )

    # Build the read filter based on the resolved tier.
    if snap_tier == SNAPSHOT_TIER_NONE:
        # No rows in the odds collection for this date at all.
        read_filter = None
    elif snap_tier == SNAPSHOT_TIER_ANY_DATE:
        # Legacy ingest without snapshot_iso labels — read the whole
        # date without constraining.
        read_filter = {"sport": "nba", "game_date": replay_date_str}
    else:
        read_filter = {"sport": "nba",
                       "game_date": replay_date_str,
                       "snapshot_iso": resolved_snap}

    odds_rows: List[Dict[str, Any]] = []
    if read_filter is not None:
        cursor = db[odds_collection].find(
            read_filter, projection={"_id": 0},
        )
        async for r in cursor:
            odds_rows.append(r)
    n_odds = len(odds_rows)

    # If we fell back to a different snapshot, use that as the
    # canonical snapshot_iso for downstream Layer-3 row keying so
    # the unique index stamps the actual snapshot the data lives
    # under (not the orchestrator's wishful tag).
    effective_snapshot_iso = (
        resolved_snap if resolved_snap is not None else snapshot_iso
    )

    if n_odds == 0:
        finished_at = datetime.now(timezone.utc)
        summary = {
            "date": replay_date_str,
            "snapshot_iso": snapshot_iso,
            "snapshot_iso_resolved": resolved_snap,
            "snapshot_resolution_tier": snap_tier,
            "snapshot_resolution_telemetry": snap_telemetry,
            "alt_odds_rows_seen": 0, "model_outputs_written": 0,
            "props_built": 0, "score_docs_returned": 0,
            "elapsed_s": (finished_at - started_at).total_seconds(),
            "scoring_config_version": SCORING_CONFIG_VERSION,
            "source_version": SOURCE_VERSION,
        }
        await db[STATUS_COLL].update_one(
            s_filter, {"$set": {"status": "completed",
                                "completed_at": finished_at, **summary}},
        )
        # Replay invariant: NEVER silently score zero without
        # surfacing WHY. The telemetry above is the answer.
        if snap_tier == SNAPSHOT_TIER_NONE:
            logger.warning(
                "[nba_replay] %s/%s zero rows: no historical odds ever "
                "ingested for this date. Verify "
                "`sgo_replay_alt_odds_raw` was populated for the "
                "window before scheduling the replay.",
                replay_date_str, snapshot_iso,
            )
        else:
            # Defensive — we reached n_odds==0 even though the
            # resolver picked a non-NONE tier. Indicates an exotic
            # race or filter mismatch; loud telemetry over silent
            # zero.
            logger.error(
                "[nba_replay] %s/%s zero rows after fallback "
                "resolution (tier=%s, resolved=%s, telemetry=%s). "
                "This should NEVER happen — investigate.",
                replay_date_str, snapshot_iso, snap_tier,
                resolved_snap, snap_telemetry,
            )
        return summary

    # 2. Build the bdl_player_id name → id index once per replay.
    bdl_id_idx = await _build_bdl_id_index(db)
    rss_after_idx = _rss_mb()

    # 3. Reshape per-book odds rows into one live-prop dict per canonical key.
    props, src_by_key, reshape_skipped = _reshape_to_live_props(
        odds_rows, bdl_id_index=bdl_id_idx,
        snapshot_iso=effective_snapshot_iso,
    )
    n_props = len(props)
    logger.info(
        "[nba_replay] %s/%s reshape: %d odds rows → %d live-prop dicts "
        "(skipped=%s)",
        replay_date_str, snapshot_iso, n_odds, n_props, reshape_skipped,
    )

    if n_props == 0:
        finished_at = datetime.now(timezone.utc)
        summary = {
            "date": replay_date_str,
            "snapshot_iso": snapshot_iso,
            "snapshot_iso_resolved": resolved_snap,
            "snapshot_resolution_tier": snap_tier,
            "snapshot_resolution_telemetry": snap_telemetry,
            "alt_odds_rows_seen": n_odds, "model_outputs_written": 0,
            "props_built": 0, "score_docs_returned": 0,
            "reshape_skipped": reshape_skipped,
            "elapsed_s": (finished_at - started_at).total_seconds(),
            "scoring_config_version": SCORING_CONFIG_VERSION,
            "source_version": SOURCE_VERSION,
        }
        await db[STATUS_COLL].update_one(
            s_filter, {"$set": {"status": "completed",
                                "completed_at": finished_at, **summary}},
        )
        return summary

    # 4. Run the production NBA scorer through the REPLAY CONTRACT
    #    (services.replay.contract.REPLAY_RECOMPUTE_KWARGS). This is
    #    cross-sport SSOT — `dry_run=True`, `write_mode="upsert"`,
    #    `bypass_eligibility=True` — so the optimizer pool receives
    #    every scored prop with gate state as METADATA.
    from services.scoring.recompute import recompute_sport
    from services.replay.contract import REPLAY_RECOMPUTE_KWARGS
    version_tag = (
        f"nba_replay_{replay_date_str}_"
        f"{snapshot_iso.replace(':', '').replace('-', '')}"
    )
    recompute_result = await recompute_sport(
        db, sport="nba", version_tag=version_tag,
        props=props,
        **REPLAY_RECOMPUTE_KWARGS,
    )
    score_docs = recompute_result.get("score_docs") or []
    rss_after_score = _rss_mb()
    logger.info(
        "[nba_replay] %s/%s recompute_sport(nba, dry_run=True) → "
        "%d score docs (processed=%d, skipped=%d)",
        replay_date_str, snapshot_iso, len(score_docs),
        recompute_result.get("processed", 0),
        recompute_result.get("skipped", 0),
    )

    # 5. Fan score docs out per (book, side) into Layer-3 row buffer.
    score_doc_by_key: Dict[Tuple[str, str, str, float, str], Dict[str, Any]] = {}
    for d in score_docs:
        sd_side = (d.get("recommendation") or "OVER").upper()
        try:
            sd_line = float(d.get("line"))
        except (TypeError, ValueError):
            continue
        k = (
            d.get("event_id") or "",
            normalize_player_name(d.get("player_name") or ""),
            d.get("stat_type") or "",
            sd_line,
            sd_side,
        )
        score_doc_by_key[k] = d

    buffer: List[Dict[str, Any]] = []
    written = 0
    no_score_doc = 0

    async def _flush():
        nonlocal written, buffer
        if not buffer:
            return
        ops = []
        key_fields = ("game_date", "event_id", "player_name_normalized",
                       "market", "line", "side", "book",
                       "snapshot_iso", "scoring_config_version")
        for r in buffer:
            f = {k: r[k] for k in key_fields}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        try:
            res = await db[OUT_COLL].bulk_write(ops, ordered=False)
            written += int(res.upserted_count or 0) + int(res.modified_count or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("[nba_replay_engine] bulk_write failed: %s", exc)
        buffer.clear()

    for k, src_rows in src_by_key.items():
        sd = score_doc_by_key.get(k)
        if sd is None:
            no_score_doc += len(src_rows)
            continue
        for src in src_rows:
            row = _project_score_doc_to_layer3_row(
                sd, odds_row=src, snapshot_iso=effective_snapshot_iso,
            )
            buffer.append(row)
            if len(buffer) >= 500:
                await _flush()
                gc.collect()
                rss = _rss_mb()
                if rss > mem_limit_mb:
                    await db[STATUS_COLL].update_one(
                        s_filter, {"$set": {"status": "memory_halt",
                                            "rss_mb_at_halt": round(rss, 1),
                                            "rows_written_so_far": written}},
                    )
                    raise MemoryError(
                        f"nba_replay_engine RSS {rss:.1f} > {mem_limit_mb} "
                        f"({replay_date_str}/{snapshot_iso})"
                    )
    await _flush()

    finished_at = datetime.now(timezone.utc)
    elapsed = (finished_at - started_at).total_seconds()
    summary = {
        "date": replay_date_str,
        "snapshot_iso": snapshot_iso,
        # ── Snapshot resolution telemetry ──────────────────────────
        # The orchestrator passes a CANDIDATE snapshot_iso; the
        # resolver picks the actual one to read. Tier explains
        # which fallback served (exact / latest_for_date /
        # any_for_date / none). See snapshot_resolver.py.
        "snapshot_iso_resolved": resolved_snap,
        "snapshot_resolution_tier": snap_tier,
        "snapshot_resolution_telemetry": snap_telemetry,
        # ── Volume telemetry (stage by stage) ──────────────────────
        # 1) Raw per-book per-side historical odds rows in scope.
        "alt_odds_rows_seen": n_odds,
        # 2) Reshape collapses per-book rows by (event, player,
        #    stat, line, side) into canonical "live-prop dicts" the
        #    NBA scorer consumes. Each canonical key carries every
        #    book quote in its layer slots.
        "props_built": n_props,
        # 3) Reasons reshape dropped any rows.
        "reshape_skipped": reshape_skipped,
        # 4) Production `recompute_sport` returns one score doc per
        #    canonical (player, stat, line, side) it could score.
        #    `bypass_eligibility=True` so PP-only and non-PP props
        #    are NOT dropped — they score with tier=rejected /
        #    coverage_gate=fail as METADATA, never row-dropped.
        "score_docs_returned": len(score_docs),
        "recompute_processed": recompute_result.get("processed", 0),
        "recompute_skipped":   recompute_result.get("skipped", 0),
        # 5) Layer-3 rows written: one per (book, side) tuple in
        #    `nba_replay_model_outputs`. Each book inherits the
        #    score doc's μ/σ/p_model/cv/HR/tier and computes
        #    book-specific implied_probability + edge.
        "model_outputs_written": written,
        # 6) Audit: source rows whose canonical key did not produce
        #    a score doc (should be 0 with bypass_eligibility=True;
        #    if non-zero, indicates `build_context` returned None
        #    for that prop — typically missing player_name / line /
        #    stat_type, which is a legitimate drop).
        "candidates_skipped_no_score_doc": no_score_doc,
        # ── Memory + timing ─────────────────────────────────────────
        "rss_mb_start": round(rss0, 1),
        "rss_mb_after_index": round(rss_after_idx, 1),
        "rss_mb_after_score": round(rss_after_score, 1),
        "rss_mb_end": round(_rss_mb(), 1),
        "elapsed_s": elapsed,
        # ── Versioning ──────────────────────────────────────────────
        "scoring_config_version": SCORING_CONFIG_VERSION,
        "source_version": SOURCE_VERSION,
        "production_recompute_version_tag": version_tag,
        "bypass_eligibility": True,   # replay contract
    }
    await db[STATUS_COLL].update_one(
        s_filter, {"$set": {"status": "completed",
                            "completed_at": finished_at, **summary}},
    )
    return summary


__all__ = [
    "OUT_COLL", "STATUS_COLL", "SCORING_CONFIG_VERSION", "SOURCE_VERSION",
    "DEFAULT_MEM_LIMIT_MB",
    "ensure_indexes", "replay_date",
]
