"""
Board Snapshot Publisher
========================
SINGLE WRITER for `{sport}_cached_board` as a materialized view over
`{sport}_prop_scores[version_tag=final-{sport}-rt]`.

Architecture contract (2026-05-08):
  * `final-{sport}-rt` is THE source of truth for tier / score / line /
    direction / recommendation / canonical_key / fair_prob / confidence /
    hit_rates / intel_suite / vision_intel / scored_at.
  * `{sport}_cached_board` is a materialized snapshot, REBUILT from the
    -rt source on every successful Delta Engine tick that wrote > 0
    score docs AND on every master_sync completion. It is NEVER the
    tier-membership authority.
  * Both call sites (`services/pipeline/delta_steps.py::PublishBoardSnapshotStep`
    and `services/master_sync.py` step 7) invoke `publish_board_snapshot`
    so there is exactly ONE board-build code path.

Write semantics (safety-first):
  * Upsert-only at the player-doc grain. Existing doc-level enrichment
    (photo_url, team_logo, bdl_id, injury_status, context_badges, etc.)
    is NEVER overwritten or cleared by the publisher.
  * For each player's props[]: every entry in the rebuilt array is
    MERGED from {existing cached_board entry with matching canonical_key}
    ← {authoritative fields from prop_scores[-rt]}. This preserves
    ingestion-layer fields (bookmaker prices, event_id, commence_time,
    home/away teams) while making tier/score/vision-intel authoritative
    from -rt.
  * Players no longer in the -rt source keep their doc but have `props`
    set to [] and `props_count` set to 0. They are NEVER deleted.
  * Zero-source guard: if `prop_scores[-rt]` is empty for the sport,
    the publisher is a no-op (returns `preserved=True`) and does not
    modify cached_board.

Freshness stamp (§3 SLO contract):
  * `updated_at`, `last_publish_ts`: publish wall-clock (UTC).
  * `source_score_max_scored_at`: max(scored_at) across ALL active -rt
    docs for the sport (not per-player — whole-board signal).
  * `version_tag`: `{sport}-cb-v1` (cached-board own tag, distinct from
    the score-doc `final-{sport}-rt`).

Non-goals:
  * No tier logic. `tier`, `routed_tier`, `tier_reason` are carried
    verbatim from -rt into the rebuilt props[] entries.
  * No scoring. The publisher is read-only against prop_scores.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pymongo import UpdateOne

# 2026-05-15 — Universal cached_board lifecycle stamping. Every doc
# this publisher writes (or marks stale) MUST go through the helper
# to guarantee identical `active / ttl_purge_at / stale_reason /
# stale_marked_at / updated_at` schema across all sports.
from services.boards.board_lifecycle import (
    lifecycle_set_for_upsert,
    lifecycle_set_inactive,
    DEFAULT_INACTIVE_REASON_EMPTY,
)

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


# Cached-board version tag — distinct from the score-doc `-rt` tag.
_CB_VERSION_TAG = {"nba": "nba-cb-v1", "mlb": "mlb-cb-v1"}


def _cb_version_tag(sport: str) -> str:
    return _CB_VERSION_TAG.get(sport, f"{sport}-cb-v1")


def _rt_source_tag(sport: str) -> str:
    return f"final-{sport}-rt"


def _cb_coll_name(sport: str) -> str:
    """Canonical cached_board collection for a sport. Uses COLL's
    `board_cache` concept: nba→nba_cached_board, mlb→mlb_cached_board.
    """
    try:
        return COLL("board_cache", sport)
    except Exception:
        return f"{sport}_cached_board"


def _ps_coll_name(sport: str) -> str:
    try:
        return COLL("prop_scores", sport)
    except Exception:
        return f"{sport}_prop_scores"


def _as_aware(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


# Fields taken authoritatively from prop_scores[-rt] on every rebuild.
# Everything else in a cached_board props[] entry is carried forward
# from the existing doc (ingestion layer: bookmaker odds, event info,
# team strings, PP layer fields, etc.).
_RT_AUTHORITATIVE_PROP_FIELDS = (
    "canonical_key",
    "player_name",
    "sport",
    "tier",
    "routed_tier",
    "tier_reason",
    "line",
    "stat_type",
    "stat_type_extracted",
    "recommendation",
    "direction",
    "fair_prob",
    "edge_vs_fair",
    "confidence",
    "p_true_active",
    "p_true_method",
    "p_true_model",
    "p_true_hit_rate",
    "ranking_score_v2",
    "vision_intel",
    "vision_intel_content_hash",
    "vision_intel_generated_at",
    "vision_score",
    "vision_score_v2",
    "hit_rate_l5",
    "hit_rate_l10",
    "hit_rate_l20",
    "hit_rate_over",
    "hit_rate_under",
    "hit_rate_sample_size",
    "hit_rate_status",
    "intel_suite",
    "injury_context",
    "market_probability",
    "pp_available",
    "pp_multiplier",
    "pp_multiplier_label",
    "pp_playable",
    "pp_playability_reason",
    "playable_on_pp",
    "momentum_data",
    "scored_at",
    "computed_at",
    "active",
    "active_changed_at",
    "event_id",
    "version_tag",
    "source_anchor",
    "availability_status",
    "identity_status",
    "gate_eval",
    "tier_gate_results",
)


def _merge_prop_entry(
    existing: Optional[Dict[str, Any]],
    rt_row: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a new props[] entry: existing ingestion fields ← -rt overrides.

    The existing entry (keyed by canonical_key in the current cached_board
    props[]) provides stable ingestion-layer fields (bookmaker prices,
    event_id, commence_time, home_team, away_team, PP layer fields,
    player_id, bookmaker, etc.). The -rt row provides authoritative
    scoring outputs (tier, score, recommendation, hit_rates, vision_intel,
    scored_at, etc.).
    """
    merged: Dict[str, Any] = dict(existing) if existing else {}
    for field in _RT_AUTHORITATIVE_PROP_FIELDS:
        if field in rt_row:
            merged[field] = rt_row[field]
    # Tag the merge timestamp for observability (not used by any SLO).
    merged["cb_snapshot_at"] = rt_row.get("scored_at")
    return merged


async def publish_board_snapshot(
    db,
    sport: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Rebuild `{sport}_cached_board` as a materialized snapshot of
    `prop_scores[final-{sport}-rt]`.

    Returns a metrics dict suitable for nesting inside a delta-tick or
    master-sync step result. Never raises — on failure the caller
    continues.
    """
    sport_l = (sport or "").lower()
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    metrics: Dict[str, Any] = {
        "sport": sport_l,
        "source_tag": _rt_source_tag(sport_l),
        "target_collection": _cb_coll_name(sport_l),
        "updated_at": ts.isoformat(),
        "preserved": False,
        "upserted_players": 0,
        "emptied_stale_players": 0,
        "total_rt_active_props": 0,
        "source_score_max_scored_at": None,
        "duration_seconds": None,
        "error": None,
    }
    start = datetime.now(timezone.utc)

    try:
        ps_coll = db[_ps_coll_name(sport_l)]
        cb_coll = db[_cb_coll_name(sport_l)]

        # --- Read the entire active -rt surface for this sport. ---
        rt_cursor = ps_coll.find(
            {
                "version_tag": _rt_source_tag(sport_l),
                "active": {"$ne": False},
            },
            {"_id": 0},
        )
        rt_rows: List[Dict[str, Any]] = await rt_cursor.to_list(length=None)
        metrics["total_rt_active_props"] = len(rt_rows)

        # Zero-source guard: preserve existing cached_board unchanged.
        if not rt_rows:
            metrics["preserved"] = True
            metrics["reason"] = "empty_source"
            logger.warning(
                f"[BOARD_SNAPSHOT:{sport_l}] empty {_rt_source_tag(sport_l)} source — "
                f"preserving existing cached_board (no writes)."
            )
            metrics["duration_seconds"] = (
                datetime.now(timezone.utc) - start
            ).total_seconds()
            return metrics

        # Whole-board max(scored_at) for the freshness stamp.
        max_scored: Optional[datetime] = None
        for row in rt_rows:
            s = _as_aware(row.get("scored_at"))
            if s and (max_scored is None or s > max_scored):
                max_scored = s
        metrics["source_score_max_scored_at"] = (
            max_scored.isoformat() if max_scored else None
        )

        # Group -rt rows by player_name.
        by_player: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rt_rows:
            pn = row.get("player_name")
            if not pn:
                continue
            by_player[pn].append(row)

        if not by_player:
            # -rt rows exist but carry no player_name (shouldn't happen);
            # treat as preserved rather than wipe.
            metrics["preserved"] = True
            metrics["reason"] = "no_named_rt_rows"
            metrics["duration_seconds"] = (
                datetime.now(timezone.utc) - start
            ).total_seconds()
            return metrics

        # --- Read existing cached_board entries for the players we're
        # about to rebuild so we can preserve ingestion-layer prop
        # fields (bookmaker odds, event_id, commence_time, etc). ---
        live_players: List[str] = list(by_player.keys())
        existing_cursor = cb_coll.find(
            {"player_name": {"$in": live_players}},
            {"_id": 0, "player_name": 1, "props": 1},
        )
        existing_props_by_player: Dict[str, Dict[str, Dict[str, Any]]] = {}
        async for doc in existing_cursor:
            pn = doc.get("player_name")
            if not pn:
                continue
            by_ck: Dict[str, Dict[str, Any]] = {}
            for p in (doc.get("props") or []):
                ck = p.get("canonical_key")
                if ck:
                    by_ck[ck] = p
            existing_props_by_player[pn] = by_ck

        # --- Build upsert ops (one per player). ---
        bulk_ops: List[UpdateOne] = []
        version_tag = _cb_version_tag(sport_l)
        for player_name, rt_group in by_player.items():
            existing_map = existing_props_by_player.get(player_name, {})
            rebuilt_props: List[Dict[str, Any]] = []
            for rt_row in rt_group:
                ck = rt_row.get("canonical_key")
                existing_entry = existing_map.get(ck) if ck else None
                rebuilt_props.append(_merge_prop_entry(existing_entry, rt_row))

            bulk_ops.append(
                UpdateOne(
                    {"player_name": player_name},
                    {
                        "$set": {
                            "player_name": player_name,
                            "sport": sport_l,
                            "props": rebuilt_props,
                            "props_count": len(rebuilt_props),
                            "updated_at": ts,
                            "last_publish_ts": ts,
                            "source_score_max_scored_at": max_scored,
                            "version_tag": version_tag,
                            "source_version_tag": _rt_source_tag(sport_l),
                            # 2026-05-15 — Universal active lifecycle.
                            # Clears any stale TTL fields the doc may
                            # have picked up from a prior off-slate
                            # cycle so re-appearance auto-restores.
                            **lifecycle_set_for_upsert(now=ts),
                        },
                        # First-write defaults — never overwrite enrichment
                        # fields that other services maintain.
                        "$setOnInsert": {
                            "created_at": ts,
                        },
                    },
                    upsert=True,
                )
            )

        if bulk_ops:
            await cb_coll.bulk_write(bulk_ops, ordered=False)
            metrics["upserted_players"] = len(bulk_ops)

        # --- Stale players: empty their props[] AND mark inactive. ---
        # 2026-05-15 — Universal lifecycle. Players that fell off the
        # slate get their props emptied (existing behaviour, do NOT
        # delete) plus the inactive lifecycle stamp so the orphan
        # cleanup utility can TTL-purge them after the grace window.
        stale_set = {
            "props": [],
            "props_count": 0,
            "last_publish_ts": ts,
            "source_score_max_scored_at": max_scored,
            "version_tag": version_tag,
            "source_version_tag": _rt_source_tag(sport_l),
            **lifecycle_set_inactive(
                reason=DEFAULT_INACTIVE_REASON_EMPTY,
            ),
        }
        stale_result = await cb_coll.update_many(
            {
                "player_name": {"$nin": live_players},
                "$or": [
                    {"props_count": {"$gt": 0}},
                    {"props": {"$exists": True, "$ne": []}},
                    # 2026-05-15 — Also re-stamp any pre-existing
                    # empty doc that's missing the lifecycle fields
                    # so the migration is permanent (one-time backfill).
                    {"active": {"$exists": False}},
                ],
            },
            {"$set": stale_set},
        )
        metrics["emptied_stale_players"] = int(
            getattr(stale_result, "modified_count", 0) or 0
        )

        metrics["duration_seconds"] = (
            datetime.now(timezone.utc) - start
        ).total_seconds()
        logger.info(
            f"[BOARD_SNAPSHOT:{sport_l}] rebuilt {metrics['upserted_players']} "
            f"players (emptied {metrics['emptied_stale_players']} stale) from "
            f"{metrics['total_rt_active_props']} active -rt props in "
            f"{metrics['duration_seconds']:.2f}s "
            f"updated_at={ts.isoformat()} "
            f"source_score_max={metrics['source_score_max_scored_at']}"
        )
        return metrics

    except Exception as exc:  # noqa: BLE001
        metrics["error"] = str(exc)
        metrics["duration_seconds"] = (
            datetime.now(timezone.utc) - start
        ).total_seconds()
        logger.warning(
            f"[BOARD_SNAPSHOT:{sport_l}] publish failed: {exc} — "
            "cached_board left intact (no wipe)."
        )
        return metrics


__all__ = [
    "publish_board_snapshot",
    "_RT_AUTHORITATIVE_PROP_FIELDS",
    "_cb_version_tag",
    "_rt_source_tag",
]
