"""
Team odds ingest worker — Phase 1.A.3.1.

Adds `run_pass(db, payload, *, snapshot_iso, mode)` — the single-pass
state machine from `SNAPSHOT_LOOP_DESIGN.md`. Real SGO HTTP fetch
is NOT wired here; the payload is injected (synthetic in tests,
TBD live in 1.A.3.2).

Pass states walked:
    idle → claim_window → fetch → normalize → write → settle → idle

`probe()` and `dry_run_promote()` remain available for orchestrator
wiring. All real writes are gated on:
    - mode="live"
    - dispatch_guard_ok()
    - dry_run_default() == False
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from services.team_master_hub.ingest_policy import (
    TeamIngestPolicy,
    dispatch_guard_ok,
    dry_run_default,
    is_book_blocked,
    is_book_reference_only,
    should_abort_on_market_explosion,
)

from ._normalize import normalize_sgo_payload
from ._sgo_provider import SGOFetchError, SGOPayloadProvider
from .base import TeamWorkerBase

logger = logging.getLogger("workers.team.team_odds_ingest")

# Planned SGO endpoints + markets per sport. NEVER hit at probe time.
# Used by the orchestrator UI (later) to show the operator what a
# real run would request.
_PLANNED_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "mlb": {
        "sgo_path": "/v2/events",
        "league_filter": "MLB",
        "markets": [
            "team_total_runs",
            "team_total_hits",
            "first_inning_runs",
            "first_five_innings_total",
        ],
    },
    "nba": {
        "sgo_path": "/v2/events",
        "league_filter": "NBA",
        "markets": [
            "team_total_points",
            "first_quarter_total",
            "first_half_total",
        ],
    },
    "nfl": {
        "sgo_path": "/v2/events",
        "league_filter": "NFL",
        "markets": [
            "team_total_points",
            "first_half_total",
            "team_total_passing_yards",
            "team_total_rushing_yards",
        ],
    },
}

LIVE_PROPS_COLL  = "team_live_props"
INGEST_RUNS_COLL = "team_odds_ingest_runs"
MASTER_HUB_COLL  = "team_master_hub"


def get_planned_markets(sport: str) -> List[str]:
    """Public read-only accessor for the planned-market list of a
    given sport. Used by `team_odds_dry_run_fetch --diff-planned`.
    """
    cfg = _PLANNED_ENDPOINTS.get((sport or "").lower())
    return list(cfg.get("markets", [])) if cfg else []


def _apply_book_policy(
    rows: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Mutates `rows` in place:
      - removes rows whose `book` is in BLOCKED_BOOKS (hard drop)
      - tags surviving rows with `reference_only: True/False`
    Returns counters.
    """
    blocked = 0
    refs = 0
    kept: List[Dict[str, Any]] = []
    for r in rows:
        book = r.get("book", "")
        if is_book_blocked(book):
            blocked += 1
            continue
        r["reference_only"] = is_book_reference_only(book)
        if r["reference_only"]:
            refs += 1
        kept.append(r)
    rows[:] = kept
    return {"n_blocked": blocked, "n_refs": refs}


async def _resolve_team_ids_in_rows(
    db,
    rows: List[Dict[str, Any]],
    *,
    sport: str,
) -> Dict[str, int]:
    """Mutates `rows` in place — attaches `team_id` via lookup against
    `team_master_hub.display_names`. Rows whose team can't be
    resolved are removed.
    """
    # Build distinct (team_name) set for one batched query
    names = sorted({r["_team_name"] for r in rows
                     if r.get("_team_name")})
    if not names:
        return {"n_unresolved": len(rows)}
    cursor = db[MASTER_HUB_COLL].find(
        {
            "sport": sport,
            "$or": [
                {"display_names.full":   {"$in": names}},
                {"display_names.short":  {"$in": names}},
                {"display_names.abbrev": {"$in": names}},
                {"display_names.market": {"$in": names}},
            ],
        },
        {"_id": 0, "team_id": 1, "display_names": 1},
    )
    lookup: Dict[str, str] = {}
    async for d in cursor:
        tid = d.get("team_id")
        if not tid:
            continue
        for variant in (d.get("display_names") or {}).values():
            if isinstance(variant, str) and variant:
                lookup[variant] = tid
    unresolved = 0
    kept: List[Dict[str, Any]] = []
    for r in rows:
        tid = lookup.get(r.get("_team_name", ""))
        if not tid:
            unresolved += 1
            continue
        r["team_id"] = tid
        r.pop("_team_name", None)
        kept.append(r)
    rows[:] = kept
    return {"n_unresolved": unresolved}


def _build_upsert_ops(
    rows: List[Dict[str, Any]],
) -> List[UpdateOne]:
    """Compound-unique-key upserts. Idempotent on snapshot_iso match.

    `ingested_at` lives under `$setOnInsert` so re-running an
    unchanged payload produces modified_count=0.
    """
    ops: List[UpdateOne] = []
    for r in rows:
        ingested_at = r.pop("ingested_at", None)
        filter_doc = {
            "event_id":     r["event_id"],
            "team_id":      r["team_id"],
            "market":       r["market"],
            "line":         r["line"],
            "side":         r["side"],
            "book":         r["book"],
            "snapshot_iso": r["snapshot_iso"],
        }
        ops.append(UpdateOne(
            filter_doc,
            {"$set": r,
             "$setOnInsert": {"ingested_at": ingested_at}},
            upsert=True,
        ))
    return ops


class TeamOddsIngestWorker(TeamWorkerBase):
    """Real-time `team_live_props` ingest worker.

    Phase 1.A.3.1: `run_pass(db, payload, ...)` walks the snapshot
    loop state machine on ONE injected payload. Real SGO HTTP fetch
    + cadence governor are deferred to 1.A.3.2.
    """

    WORKER_KEY = "team_odds_ingest"

    # ── existing probe + dry_run_promote (skeletons retained) ────────
    def probe(self) -> Dict[str, Any]:
        cfg = _PLANNED_ENDPOINTS[self.sport]
        ok, reasons = self.dispatch_guard_ok()
        return {
            "worker":           self.WORKER_KEY,
            "sport":            self.sport,
            "requires_sgo_key": self.requires_sgo_key(),
            "dispatch_allowed": ok,
            "dispatch_reasons": reasons,
            "planned":          cfg,
        }

    def dry_run_promote(
        self,
        candidate_event_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        events = list(candidate_event_ids or [])
        return {
            "worker":             self.WORKER_KEY,
            "sport":              self.sport,
            "mode":               "dry_run",
            "would_read":         "team_live_props",
            "would_write":        "team_historical_props",
            "n_candidate_events": len(events),
            "event_ids":          events[:50],
            "note": (
                "Phase 1.A.2 skeleton — no Mongo read or write "
                "performed. Real promotion lands in Phase 1.A.3."
            ),
        }

    # ── Phase 1.A.3.1 — run_pass single-pass injectable ──────────────
    async def run_pass(
        self,
        db,
        payload: Dict[str, Any],
        *,
        snapshot_iso: Optional[str] = None,
        mode: str = "dry_run",
        policy: Optional[TeamIngestPolicy] = None,
    ) -> Dict[str, Any]:
        """Walk one pass of the snapshot-loop state machine on `payload`.

        Args:
            db: Motor DB.
            payload: SGO-shape dict (see `_normalize.normalize_sgo_payload`).
            snapshot_iso: ISO UTC string; defaults to now.
            mode: 'dry_run' (default — NO writes) or 'live' (writes
                  to team_live_props, gated on dispatch_guard +
                  dry_run_default).
            policy: optional override; defaults to env-derived policy.

        Returns the audit summary dict that was also written to
        `team_odds_ingest_runs`.
        """
        if mode not in ("dry_run", "live"):
            raise ValueError(f"mode must be 'dry_run' or 'live', got {mode!r}")
        pol = policy or TeamIngestPolicy.from_env()
        run_id   = str(uuid.uuid4())
        started  = datetime.now(timezone.utc)
        snap_iso = snapshot_iso or started.isoformat()

        # State: idle → claim_window (gate check) ───────────────────
        guard_ok, guard_reasons = dispatch_guard_ok()
        dry_default = dry_run_default()
        live_allowed = guard_ok and not dry_default
        effective_mode = mode
        guard_blocked = False
        if mode == "live" and not live_allowed:
            # Hard-abort: do NOT write rows. Audit row still emitted.
            effective_mode = "dry_run"
            guard_blocked = True

        # State: fetch (payload injected) → normalize ───────────────
        rows, norm_counters = normalize_sgo_payload(
            payload,
            sport=self.sport,
            snapshot_iso=snap_iso,
            ingested_at=started,
        )

        # Book policy (drop blocked, tag refs)
        book_counters = _apply_book_policy(rows)

        # Resolve team_ids via team_master_hub (drop unresolved)
        resolve_counters = await _resolve_team_ids_in_rows(
            db, rows, sport=self.sport)

        # Market-explosion kill switch
        observed_markets = len({r["market"] for r in rows})
        expected_markets = len(
            _PLANNED_ENDPOINTS[self.sport]["markets"])
        explosion_abort, explosion_reason = (
            should_abort_on_market_explosion(
                observed_markets=observed_markets,
                expected_markets=expected_markets,
                policy=pol,
            )
        )

        # State: write ──────────────────────────────────────────────
        n_writes = 0
        n_upserted = 0
        n_modified = 0
        n_matched = 0
        if guard_blocked:
            status = "guard_closed"
            diagnosis = (
                f"live writes blocked: "
                f"{'; '.join(guard_reasons) or 'dry_run_default=True'}"
            )
        elif explosion_abort:
            status = "aborted_explosion"
            diagnosis = explosion_reason
        elif effective_mode == "dry_run":
            status = "dry_run"
            diagnosis = (
                "dry_run mode — no rows written to team_live_props"
            )
        elif not rows:
            status = "succeeded_empty"
            diagnosis = "no rows survived normalization"
        else:
            try:
                ops = _build_upsert_ops(rows)
                result = await db[LIVE_PROPS_COLL].bulk_write(
                    ops, ordered=False)
                n_writes   = len(ops)
                n_upserted = len(result.upserted_ids or {})
                n_modified = int(result.modified_count or 0)
                n_matched  = int(result.matched_count or 0)
                status = "succeeded"
                diagnosis = (
                    f"wrote {n_writes} rows "
                    f"(upserted={n_upserted}, modified={n_modified})"
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("[team_odds_ingest] write failed")
                status = "errored"
                diagnosis = f"bulk_write failed: {exc}"

        # State: settle — write the audit row ───────────────────────
        finished = datetime.now(timezone.utc)
        per_market: Dict[str, int] = dict(
            Counter(r["market"] for r in rows))
        audit_row = {
            "run_id":            run_id,
            "sport":             self.sport,
            "worker":            self.WORKER_KEY,
            "mode_requested":    mode,
            "mode_effective":    effective_mode,
            "dry_run":           effective_mode == "dry_run",
            "live_write_allowed": live_allowed,
            "guard_reasons":     guard_reasons,
            "started_at":        started,
            "finished_at":       finished,
            "duration_ms":       int((finished - started).total_seconds()
                                       * 1000),
            "snapshot_iso":      snap_iso,
            "status":            status,
            "diagnosis":         diagnosis,
            "n_sgo_events":      norm_counters["sgo_events"],
            "n_sgo_outcomes":    norm_counters["sgo_outcomes"],
            "n_rows_normalized": norm_counters["rows_emitted"],
            "n_blocked":         book_counters["n_blocked"],
            "n_refs":            book_counters["n_refs"],
            "n_unresolved":      resolve_counters["n_unresolved"],
            "n_writes":          n_writes,
            "n_upserted":        n_upserted,
            "n_modified":        n_modified,
            "n_matched":         n_matched,
            "observed_markets":  observed_markets,
            "expected_markets":  expected_markets,
            "explosion_abort":   explosion_abort,
            "per_market_counts": per_market,
        }
        await db[INGEST_RUNS_COLL].insert_one(dict(audit_row))
        # Strip Mongo's mutation of _id from the response (BSON safe)
        audit_row.pop("_id", None)
        return audit_row

    # ── Phase 1.A.3.3 — dry-run HTTP fetcher integration ─────────────
    async def fetch_and_run_pass(
        self,
        db,
        *,
        event_id: str,
        api_key: str,
        snapshot_iso: Optional[str] = None,
        mode: str = "dry_run",
        provider: Optional[SGOPayloadProvider] = None,
    ) -> Dict[str, Any]:
        """Tier 4 entrypoint: HTTP → sanitize → run_pass.

        Single fetch, no retries. The API key is passed explicitly
        (caller resolves from env) so the worker never reads env
        directly.

        Phase 1.A.3.3 scope: defaults to `mode="dry_run"`. Even when
        the caller passes `mode="live"`, `run_pass` will downgrade to
        dry-run unless BOTH the dispatch guard is open AND
        `dry_run_default()==False` (i.e. `TEAM_INGEST_LIVE=1`). This
        method does NOT relax that gate.

        On a transport / HTTP / parse failure, no audit row is
        written and `SGOFetchError` is re-raised for the caller.
        """
        prov = provider or SGOPayloadProvider(api_key)
        try:
            fetched = prov.fetch_event_odds(
                sport=self.sport, event_id=event_id)
        except SGOFetchError:
            raise
        return await self.run_pass(
            db, fetched["payload"],
            snapshot_iso=snapshot_iso, mode=mode,
        )

