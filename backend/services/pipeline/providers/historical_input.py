"""MLBHistoricalInputProvider.

Reads `mlb_historical_alt_odds_raw` for a `(game_date, snapshot_iso)`
window, normalizes the per-(book × side) rows into the LIVE prop
shape — one row per `(event, player, stat_family, line, side)` with
flat book-price fields — and returns the list.

The runner immediately hands this list to
`apply_production_eligibility(use_pp_registry_fallback=True)` (the
Phase A SSOT). The SSOT stamps `playable_on_pp` via the hardcoded
`SPORT_PP_SIDE_REGISTRY` (which fails closed for unknown
families/sides) so historical mode never invents playability.

What we do NOT do here:
  • PP filtering — that's the SSOT's job.
  • Coverage filtering — same, SSOT.
  • Canonical collapse — that happens later inside
    `run_production_replay(canonical_path=True)`.
  • Model inference (mu/sigma/edge/etc.) — Layer-3 (in
    `mlb_replay_model_outputs`) already computed those; the universal
    pipeline runner uses them when canonical evaluation hands rows
    to the gate engine. THIS provider is only the eligibility input.

Mapping contract (alt_odds row → live-prop shape field):

    alt_row.event_id              → prop.event_id
    alt_row.player_name           → prop.player_name
    alt_row.player_name_normalized→ prop.player_name_normalized
    alt_row.market                → prop.market
    market_to_stat_family(market) → prop.stat_family / prop.stat_type
    alt_row.line                  → prop.line
    alt_row.side ("OVER"|"UNDER") → prop.side + prop.recommendation
    alt_row.book + alt_row.odds   → flat book-price field per
                                     `coverage_filter._BOOK_FIELDS`
                                     (`draftkings_price`,
                                      `fanduel_price`, etc.)

The flat-field naming MUST match `_BOOK_FIELDS` exactly because
that's what `filter_priceable` inspects.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from services.pipeline.providers.base import IInputProvider

logger = logging.getLogger(__name__)


# Book name (Odds-API key) → flat-field legacy name used by
# `services.scoring.coverage_filter._BOOK_FIELDS`. Keep this table
# aligned with coverage_filter._BOOK_FIELDS — any new anchor book
# added there must be mirrored here.
_BOOK_TO_FLAT_FIELD: Dict[str, str] = {
    "draftkings":     "draftkings_price",
    "fanduel":        "fanduel_price",
    "betonlineag":    "betonline_price",
    "betmgm":         "betmgm_price",
    "williamhill_us": "caesars_price",
    "espnbet":        "espnbet_price",
    "hardrockbet":    "hardrockbet_price",
    "betrivers":      "betrivers_price",
    "betparx":        "betparx_price",
    "ballybet":       "ballybet_price",
    "fliff":          "fliff_price",
}


# Display labels for `stat_type` field. Used by `filter_pp_playable`
# only for audit-log strings; the actual eligibility decision keys
# off `stat_family` via the registry. Map keeps audit logs readable.
_FAMILY_TO_STAT_TYPE_DISPLAY: Dict[str, str] = {
    "hits":              "Hits",
    "total_bases":       "Total Bases",
    "hits_runs_rbis":    "Hits+Runs+RBIs",
    "runs":              "Runs",
    "rbis":              "RBIs",
    "home_runs":         "Home Runs",
    "doubles":           "Doubles",
    "singles":           "Singles",
    "batter_strikeouts": "Batter Strikeouts",
    "strikeouts":        "Batter Strikeouts",   # alias
    "batter_walks":      "Batter Walks",
    "pitcher_strikeouts": "Pitcher Strikeouts",
    "pitcher_walks":      "Pitcher Walks",
    "earned_runs":        "Earned Runs",
}


class MLBHistoricalInputProvider(IInputProvider):
    """Historical mode input source for MLB.

    Args:
        snapshot_iso: ISO instant matching the
            `mlb_historical_alt_odds_raw.snapshot_iso` partition,
            e.g. "2026-05-05T11:00:00Z".
        game_date: ISO YYYY-MM-DD matching `.game_date`.
    """
    sport = "mlb"
    mode = "historical"

    def __init__(self, *, game_date: str, snapshot_iso: str):
        self.game_date = game_date
        self.snapshot_iso = snapshot_iso
        self.name = (
            f"MLBHistoricalInputProvider(date={game_date},"
            f"snapshot={snapshot_iso})"
        )
        # Populated by load_props for the audit envelope.
        self._raw_row_count: int = 0
        self._normalized_prop_count: int = 0
        self._unknown_market_drops: int = 0
        self._under_alt_drops: int = 0
        self._input_snapshot_hash: Optional[str] = None

    async def load_props(self, db) -> List[Dict[str, Any]]:
        from services.replay.mlb_feature_cache import market_to_stat_family

        # ── Step 1: load raw per-(book × side) rows ─────────────────
        cursor = db["mlb_historical_alt_odds_raw"].find(
            {"sport": "mlb",
             "game_date": self.game_date,
             "snapshot_iso": self.snapshot_iso},
            projection={"_id": 0},
        )
        raw_rows: List[Dict[str, Any]] = []
        async for r in cursor:
            raw_rows.append(r)
        self._raw_row_count = len(raw_rows)

        # ── Step 2: respect the OVER-only alt rule (universal) ─────
        # `replay_market_coverage_rule_2026_05_16.md`: alternate
        # markets are OVER-only. UNDER on alt is silently skipped
        # in the live replay engine; we mirror that here so the
        # historical universe mirrors what live ingestion would
        # produce.
        filtered_rows: List[Dict[str, Any]] = []
        for r in raw_rows:
            if r.get("is_alternate") and r.get("side") != "OVER":
                self._under_alt_drops += 1
                continue
            filtered_rows.append(r)

        # ── Step 3: group by (event, player, stat_family, line, side) ─
        # Build the canonical live-prop key. Each group becomes ONE
        # live-shape prop with flat book-price fields populated from
        # every book in the group.
        groups: Dict[
            Tuple[str, str, str, float, str], Dict[str, Any]
        ] = {}
        for r in filtered_rows:
            fam = market_to_stat_family(r.get("market"))
            if fam is None:
                self._unknown_market_drops += 1
                continue
            try:
                line = round(float(r["line"]), 2)
            except (TypeError, ValueError):
                continue
            side = (r.get("side") or "").upper()
            if side not in ("OVER", "UNDER"):
                continue
            key = (
                str(r.get("event_id") or ""),
                str(r.get("player_name_normalized") or ""),
                fam, line, side,
            )
            if key not in groups:
                groups[key] = {
                    "sport": "mlb",
                    "game_date": self.game_date,
                    "snapshot_iso": self.snapshot_iso,
                    "event_id": r.get("event_id"),
                    "home_team": r.get("home_team"),
                    "away_team": r.get("away_team"),
                    "commence_time": str(r.get("commence_time") or ""),
                    "player_name": r.get("player_name"),
                    "player_name_normalized":
                        r.get("player_name_normalized"),
                    "stat_family": fam,
                    "stat_type":
                        _FAMILY_TO_STAT_TYPE_DISPLAY.get(fam, fam),
                    "market": r.get("market"),
                    "line": line,
                    "side": side,
                    "recommendation": side,
                    # PP fields — left UNSET so the SSOT's
                    # `use_pp_registry_fallback=True` stamps them.
                    "playable_on_pp": None,
                    "pp_layer": None,
                    "source_anchor": "sportsbook_fallback",
                    # Books seen for this prop (audit only)
                    "_historical_books_seen": [],
                }
            # Stamp the flat book-price field. Last-write-wins per
            # book is fine — each (book × side × line) is unique
            # in the upstream collection.
            book = (r.get("book") or "").lower()
            flat_field = _BOOK_TO_FLAT_FIELD.get(book)
            if flat_field is None:
                # Book not in the priceable anchor set
                # (e.g. fanatics, bovada, hardrockbet_oh). The
                # `filter_priceable` SSOT does not count these in
                # `book_count` — so we skip them here to match the
                # live contract exactly.
                continue
            try:
                groups[key][flat_field] = int(r["odds"])
                groups[key]["_historical_books_seen"].append(book)
            except (TypeError, ValueError):
                continue

        props = list(groups.values())
        self._normalized_prop_count = len(props)
        # Compute a deterministic hash of the (book, odds) layout for
        # the audit envelope. Lets us prove identical inputs across
        # repeat runs of the same snapshot.
        hash_payload = []
        for p in sorted(props, key=lambda x: (
            x.get("event_id") or "",
            x.get("player_name_normalized") or "",
            x.get("stat_family") or "",
            x.get("line") or 0.0,
            x.get("side") or "",
        )):
            for fl in sorted(_BOOK_TO_FLAT_FIELD.values()):
                hash_payload.append(f"{fl}={p.get(fl, '')}")
        self._input_snapshot_hash = hashlib.sha256(
            "|".join(hash_payload).encode()
        ).hexdigest()[:16]

        logger.info(
            "[MLBHistoricalInputProvider] date=%s snapshot=%s "
            "raw_rows=%d under_alt_drops=%d unknown_market_drops=%d "
            "normalized_props=%d input_hash=%s",
            self.game_date, self.snapshot_iso,
            self._raw_row_count, self._under_alt_drops,
            self._unknown_market_drops, self._normalized_prop_count,
            self._input_snapshot_hash,
        )
        return props

    def describe_source(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "sport": self.sport,
            "source_collections": [
                "mlb_historical_alt_odds_raw",
                "mlb_replay_model_outputs",  # consumed downstream by canonical+gate
            ],
            "input_snapshot_hash": self._input_snapshot_hash,
            "extras": {
                "raw_row_count": self._raw_row_count,
                "normalized_prop_count": self._normalized_prop_count,
                "unknown_market_drops": self._unknown_market_drops,
                "under_alt_drops": self._under_alt_drops,
                "eligibility_applied_by_runner": True,
                "ssot_function": "apply_production_eligibility",
                "use_pp_registry_fallback": True,
            },
        }


__all__ = ["MLBHistoricalInputProvider"]
