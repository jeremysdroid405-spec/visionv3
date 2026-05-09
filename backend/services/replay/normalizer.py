"""
Normalize a raw historical-event-odds envelope into flat
`replay_props_normalized` rows.

Side-effect-free: pure function from envelope + metadata → list of dicts.

We filter to the Phase-1 book whitelist DURING normalization so the
`replay_props_normalized` collection stays focused on the books we score
on. Raw envelopes still keep all books in `replay_odds_snapshots`, so no
data is lost — we can re-flatten with a different filter later without
re-querying the API.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from scripts.odds_api_backfill.sport_markets import (
    is_alternate, is_combo, market_to_family,
)

from .markets import REPLAY_BOOK_WHITELIST_PHASE1

logger = logging.getLogger(__name__)


# Bumped whenever the normalization rules change. Stamps every row.
NORMALIZER_VERSION = "replay-norm-v1"


def stable_payload_hash(payload: Dict[str, Any]) -> str:
    """Deterministic SHA-256 of a payload for change detection."""
    blob = json.dumps(payload, sort_keys=True, default=str,
                       separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _parse_iso_z(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


def _canonical_key(*, sport_key: str, market_key: str,
                   player: str, line: float) -> str:
    """Mirror the production canonical_key contract for replay rows."""
    sport_short = "nba" if sport_key == "basketball_nba" else (
        "mlb" if sport_key == "baseball_mlb" else sport_key.split("_")[-1]
    )
    return f"{sport_short}|{market_key}|{player}|{line}"


def _implied_prob_from_american(odds: int) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return round(100.0 / (odds + 100.0), 6)
    if odds < 0:
        return round((-odds) / ((-odds) + 100.0), 6)
    return None


def normalize_envelope(
    envelope: Dict[str, Any],
    *,
    sport_key: str,
    event_id: str,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    snapshot_label: str,
    requested_ts: datetime,
    region: str,
    book_whitelist: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return BOTH artefacts in one call:
      - `raw_doc`: ready for `replay_odds_snapshots` (one row per
        (event, market, snapshot_label) — caller will iterate markets)
      - `flat_rows`: list of dicts ready for `replay_props_normalized`
                    bulk-upsert
    """
    if commence_time.tzinfo is None or requested_ts.tzinfo is None:
        raise ValueError("commence_time and requested_ts must be tz-aware UTC")

    if book_whitelist is None:
        book_whitelist_set = set(REPLAY_BOOK_WHITELIST_PHASE1)
    else:
        book_whitelist_set = set(book_whitelist)

    returned_ts = _parse_iso_z(envelope.get("timestamp"))
    previous_ts = _parse_iso_z(envelope.get("previous_timestamp"))
    next_ts = _parse_iso_z(envelope.get("next_timestamp"))

    data = envelope.get("data") or {}
    bookmakers = data.get("bookmakers") or []

    flat_rows: List[Dict[str, Any]] = []
    market_counts: Dict[str, int] = {}
    book_counts: Dict[str, int] = {}

    for bm in bookmakers:
        bm_key = (bm.get("key") or "").strip().lower()
        if not bm_key:
            continue
        if bm_key not in book_whitelist_set:
            continue  # filter to Phase-1 books
        for market in (bm.get("markets") or []):
            mkey = market.get("key")
            if not mkey:
                continue
            last_update = _parse_iso_z(market.get("last_update"))
            for outcome in (market.get("outcomes") or []):
                player = (outcome.get("description")
                           or outcome.get("participant")
                           or "").strip().lower()
                side = (outcome.get("name") or "").strip().upper()
                line = outcome.get("point")
                price = outcome.get("price")
                if not player or side not in ("OVER", "UNDER", "YES", "NO"):
                    continue
                if line is None or price is None:
                    continue
                line_f = float(line)
                price_i = int(price)
                row = {
                    "sport_key":     sport_key,
                    "event_id":      event_id,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "commence_time": commence_time,
                    "snapshot_label": snapshot_label,
                    "snapshot_ts":   returned_ts or requested_ts,
                    "requested_ts":  requested_ts,
                    "minutes_before_start": int(round(
                        (commence_time - (returned_ts or requested_ts))
                        .total_seconds() / 60.0
                    )),
                    "bookmaker":     bm_key,
                    "region":        region,
                    "market_key":    mkey,
                    "is_alternate":  is_alternate(mkey),
                    "is_combo":      is_combo(sport_key, mkey),
                    "stat_family":   market_to_family(sport_key, mkey),
                    "player":        player,
                    "line":          line_f,
                    "side":          side,
                    "odds_american": price_i,
                    "implied_probability":
                        _implied_prob_from_american(price_i),
                    "last_update":   last_update,
                    "canonical_key": _canonical_key(
                        sport_key=sport_key, market_key=mkey,
                        player=player, line=line_f),
                    "normalizer_version": NORMALIZER_VERSION,
                    "normalized_at": datetime.now(timezone.utc),
                    "source": "odds_api_v4_historical",
                }
                flat_rows.append(row)
                market_counts[mkey] = market_counts.get(mkey, 0) + 1
                book_counts[bm_key] = book_counts.get(bm_key, 0) + 1

    return {
        "envelope_meta": {
            "sport_key":      sport_key,
            "event_id":       event_id,
            "snapshot_label": snapshot_label,
            "requested_ts":   requested_ts,
            "returned_ts":    returned_ts,
            "previous_ts":    previous_ts,
            "next_ts":        next_ts,
            "region":         region,
            "payload_hash":   stable_payload_hash(envelope),
            "bookmakers_in_payload": [
                (b.get("key") or "").strip().lower() for b in bookmakers
            ],
            "ingested_at":    datetime.now(timezone.utc),
        },
        "flat_rows":      flat_rows,
        "market_counts":  market_counts,
        "book_counts":    book_counts,
    }


__all__ = [
    "NORMALIZER_VERSION",
    "stable_payload_hash",
    "normalize_envelope",
]
