"""
Phase 1.A.3.1 — synthetic-SGO payload normalizer (pure function).

Transforms an SGO-shape event payload into the canonical
`team_live_props` row dicts. NO network, NO Mongo, NO env reads.
Used by `TeamOddsIngestWorker.run_pass()` and exercised directly
by Tier 1 unit tests.

Expected payload shape (matches SGO v4-ish event-odds response):
{
  "events": [
    {
      "event_id":      "evt_…",
      "commence_time": "2026-06-02T22:00:00Z",
      "home_team":     "New York Yankees",
      "away_team":     "Boston Red Sox",
      "bookmakers": [
        {
          "key": "draftkings",
          "markets": [
            {
              "key":   "team_total_runs",
              "team":  "New York Yankees",   # which team this market is on
              "outcomes": [
                {"name": "Over",  "point": 4.5, "price": -110},
                {"name": "Under", "point": 4.5, "price": -110}
              ]
            }
          ]
        }
      ]
    }
  ]
}

Output: list of dicts matching the `team_live_props` §1.2 schema.
Side is upper-cased; line is float; book is lower-cased.
`reference_only` and `team_id` are NOT set here — those come from
`_apply_book_policy` and `_resolve_team_ids_in_rows` respectively.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


def _norm_side(name: str) -> str | None:
    s = (name or "").strip().upper()
    if s in ("OVER", "O", "MORE"):
        return "OVER"
    if s in ("UNDER", "U", "LESS"):
        return "UNDER"
    return None


def _norm_book(key: str) -> str:
    return (key or "").strip().lower()


def _derive_game_date(commence_time_iso: str) -> str | None:
    if not commence_time_iso:
        return None
    try:
        # Tolerate trailing 'Z'
        s = commence_time_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(
            timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def normalize_sgo_payload(
    payload: Dict[str, Any],
    *,
    sport: str,
    snapshot_iso: str,
    ingested_at: datetime,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Pure transform. Returns (rows, counters).

    Counters:
        sgo_events:        events received
        sgo_outcomes:      raw outcomes scanned
        rows_emitted:      rows passing all in-payload guards
        dropped_bad_side:  outcomes with unparseable side
        dropped_no_team:   markets without `team` set
        dropped_bad_line:  outcomes whose `point` is not numeric
    """
    counters = {
        "sgo_events":       0,
        "sgo_outcomes":     0,
        "rows_emitted":     0,
        "dropped_bad_side": 0,
        "dropped_no_team":  0,
        "dropped_bad_line": 0,
    }
    rows: List[Dict[str, Any]] = []
    for ev in payload.get("events", []) or []:
        counters["sgo_events"] += 1
        event_id      = ev.get("event_id")
        commence_iso  = ev.get("commence_time", "")
        game_date     = _derive_game_date(commence_iso)
        if not event_id:
            continue
        for bm in ev.get("bookmakers", []) or []:
            book = _norm_book(bm.get("key", ""))
            for mkt in bm.get("markets", []) or []:
                market_name = mkt.get("key")
                team_name   = mkt.get("team")
                is_alternate = bool(market_name and "_alternate" in market_name)
                if not team_name:
                    counters["dropped_no_team"] += len(
                        mkt.get("outcomes", []) or [])
                    counters["sgo_outcomes"] += len(
                        mkt.get("outcomes", []) or [])
                    continue
                for oc in mkt.get("outcomes", []) or []:
                    counters["sgo_outcomes"] += 1
                    side = _norm_side(oc.get("name", ""))
                    if side is None:
                        counters["dropped_bad_side"] += 1
                        continue
                    point = oc.get("point")
                    try:
                        line = float(point) if point is not None else None
                    except (TypeError, ValueError):
                        line = None
                    if line is None:
                        counters["dropped_bad_line"] += 1
                        continue
                    odds = oc.get("price")
                    rows.append({
                        "event_id":      event_id,
                        "_team_name":    team_name,   # resolver input
                        "market":        market_name,
                        "line":          line,
                        "side":          side,
                        "book":          book,
                        "odds":          odds,
                        "is_alternate":  is_alternate,
                        "snapshot_iso":  snapshot_iso,
                        "commence_time": commence_iso,
                        "game_date":     game_date,
                        "home_away":     None,  # 1.A.4 backfill
                        "sport":         sport,
                        "ingested_at":   ingested_at,
                    })
                    counters["rows_emitted"] += 1
    return rows, counters
