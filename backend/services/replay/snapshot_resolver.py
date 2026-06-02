"""Snapshot-resolution fallback policy for replay engines.

Replay invariant: never silently score zero rows.

The orchestrator passes a CANDIDATE `snapshot_iso` (typically
`{game_date}T11:00:00Z` for the 11:00 UTC ladder), but historical
data ingest may have used a different snapshot label for the same
slate — different hour, different format, or only one snapshot per
date with no scheduled hour. An over-constrained match silently
collapses to zero rows.

Resolution order (per user contract 2026-06-02):

  1. Exact `snapshot_iso` match.
  2. LATEST `snapshot_iso` for the `(sport, game_date)` slate.
  3. ANY rows for `(sport, game_date)` (no snapshot constraint).
  4. None resolved — telemetry stamp + zero return (legitimate
     "no data ever ingested for this date" case).

Used by all replay engines that read `sgo_replay_alt_odds_raw` or
any other date-keyed historical odds source. Same contract MLB /
NBA / NFL / NCAAF / future sports.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The resolution-tier label is stamped on the engine summary AND
# the `*_replay_model_status` doc so audits can see which fallback
# tier serviced each run.
SNAPSHOT_TIER_EXACT       = "exact"
SNAPSHOT_TIER_LATEST_DATE = "latest_for_date"
SNAPSHOT_TIER_ANY_DATE    = "any_for_date"
SNAPSHOT_TIER_NONE        = "none"


async def resolve_snapshot_iso(
    db, *,
    sport: str,
    game_date: str,
    requested_snapshot_iso: str,
    odds_collection: str = "sgo_replay_alt_odds_raw",
) -> Tuple[Optional[str], str, Dict[str, int]]:
    """Pick the snapshot_iso the engine will read from.

    Returns:
      `(snapshot_iso, tier_label, telemetry)` where:
        * `snapshot_iso` is the resolved value (or None if no rows
          exist for `game_date` at all);
        * `tier_label` is one of the `SNAPSHOT_TIER_*` constants;
        * `telemetry` is `{rows_for_date: int, rows_for_exact_snapshot: int,
          distinct_snapshots_for_date: int}` for the engine summary.

    Telemetry is computed unconditionally so the engine summary
    ALWAYS surfaces the per-stage counts — no silent zero returns.
    """
    base_filter = {"sport": sport, "game_date": game_date}
    exact_filter = {**base_filter, "snapshot_iso": requested_snapshot_iso}

    # ── Tier 1 — exact match ────────────────────────────────────────
    n_exact = await db[odds_collection].count_documents(exact_filter)
    n_date = await db[odds_collection].count_documents(base_filter)
    # Cap the distinct-snapshots audit at 50 so an unbounded slate
    # never balloons the response.
    distinct_snapshots: List[str] = await db[odds_collection].distinct(
        "snapshot_iso", base_filter)
    n_distinct = len(distinct_snapshots)
    telemetry = {
        "rows_for_date": n_date,
        "rows_for_exact_snapshot": n_exact,
        "distinct_snapshots_for_date": n_distinct,
    }

    if n_exact > 0:
        logger.info(
            "[snapshot_resolver][%s] %s exact %s matched %d rows",
            sport, game_date, requested_snapshot_iso, n_exact,
        )
        return requested_snapshot_iso, SNAPSHOT_TIER_EXACT, telemetry

    if n_date == 0:
        logger.warning(
            "[snapshot_resolver][%s] %s NO rows in %s for this date — "
            "nothing to replay. Verify ingest ran for this slate.",
            sport, game_date, odds_collection,
        )
        return None, SNAPSHOT_TIER_NONE, telemetry

    # ── Tier 2 — latest snapshot for the date ───────────────────────
    # ISO 8601 strings sort lexicographically by time. Take the
    # alphabetically largest as "latest".
    if distinct_snapshots:
        # MongoDB `distinct` does not guarantee order. Sort in Python.
        sorted_snaps = sorted(
            [s for s in distinct_snapshots if isinstance(s, str)],
            reverse=True,
        )
        if sorted_snaps:
            latest = sorted_snaps[0]
            n_latest = await db[odds_collection].count_documents(
                {**base_filter, "snapshot_iso": latest})
            logger.warning(
                "[snapshot_resolver][%s] %s requested snapshot %s had "
                "0 rows; falling back to LATEST snapshot %s (%d rows). "
                "Total date has %d rows across %d distinct snapshots: "
                "%s",
                sport, game_date, requested_snapshot_iso, latest,
                n_latest, n_date, n_distinct,
                sorted_snaps[:10],
            )
            telemetry["resolved_snapshot_iso"] = latest
            telemetry["rows_for_resolved_snapshot"] = n_latest
            return latest, SNAPSHOT_TIER_LATEST_DATE, telemetry

    # ── Tier 3 — any rows for the date (no snapshot constraint) ─────
    # Reached only when `distinct_snapshots` is all non-strings/None
    # (legacy ingest that didn't stamp snapshot_iso). Engine should
    # read the whole date without filtering by snapshot.
    logger.warning(
        "[snapshot_resolver][%s] %s date has %d rows but no usable "
        "`snapshot_iso` values; falling back to ANY-FOR-DATE scan.",
        sport, game_date, n_date,
    )
    telemetry["resolved_snapshot_iso"] = None
    telemetry["rows_for_resolved_snapshot"] = n_date
    return None, SNAPSHOT_TIER_ANY_DATE, telemetry


__all__ = [
    "SNAPSHOT_TIER_EXACT",
    "SNAPSHOT_TIER_LATEST_DATE",
    "SNAPSHOT_TIER_ANY_DATE",
    "SNAPSHOT_TIER_NONE",
    "resolve_snapshot_iso",
]
