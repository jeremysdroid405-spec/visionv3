"""Phase 4b — Replay-side `tier_reference_odds` loader.

Reproduces the live `_pick_reference_odds(...)` chain (and MLB's
DK+FD consensus pre-step) for a historical snapshot, so the replay
path can call the same universal `resolve_target_tier` that live
serving calls — NO duplication of book priority or consensus math.

Returns: `{(event_id, player_norm, market, line, side) → (ref_odds, ref_book)}`

The function is read-only over `mlb_historical_alt_odds_raw` for the
snapshot. Mirrors what `_pick_reference_odds` would have seen in
production at `(game_date, snapshot_iso)`.
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple

from services.scoring.scoring_stack import (
    _pick_reference_odds, _american_to_prob, _prob_to_american,
)


# Per-sport reference book chains — derived from `_pick_reference_odds`
# docstring (NOT hard-coded — we call the live function and just need
# to know which books to fetch from the snapshot).
_REF_BOOKS = ("draftkings", "fanduel", "betmgm", "williamhill_us",
              "betonlineag")
# Map snapshot collection's `book` keys → the layer keys
# `_pick_reference_odds` expects.
_BOOK_TO_LAYER = {
    "draftkings":     "dk",
    "fanduel":        "fd",
    "betmgm":         "mgm",
    "williamhill_us": "csr",  # Caesars/WilliamHill (NJ branding)
    "betonlineag":    "bol",
}


async def load_reference_odds_for_snapshot(
    db, *, sport: str, game_date: str, snapshot_iso: str,
) -> Dict[Tuple[str, str, str, float, str], Tuple[Optional[int], str]]:
    """Build {(event_id, player_norm, market, line, side) → (ref_odds, ref_book)}.

    Calls the LIVE `_pick_reference_odds` for every (prop, side) key
    that exists in the snapshot — no duplicated routing logic.
    """
    coll = ("mlb_historical_alt_odds_raw" if sport == "mlb"
            else f"{sport}_historical_alt_odds_raw")
    cursor = db[coll].find(
        {"sport": sport, "game_date": game_date,
         "snapshot_iso": snapshot_iso,
         "book": {"$in": list(_REF_BOOKS)}},
        projection={"_id": 0, "event_id": 1,
                     "player_name_normalized": 1, "market": 1,
                     "line": 1, "side": 1, "book": 1, "odds": 1},
    )
    # Stage 1: gather per-(prop+side) the layers expected by
    # _pick_reference_odds: dk_layer, fd_layer, mgm_layer, csr_layer,
    # bol_layer.
    staged: Dict[Tuple[str, str, str, float, str], Dict[str, Dict]] = {}
    async for r in cursor:
        line = r.get("line")
        side = (r.get("side") or "").upper()
        if line is None or side not in ("OVER", "UNDER"):
            continue
        book_key = _BOOK_TO_LAYER.get(r.get("book"))
        if not book_key:
            continue
        k = (str(r["event_id"]),
             str(r["player_name_normalized"]),
             str(r["market"]),
             float(line),
             side)
        layers = staged.setdefault(k, {})
        # If multiple rows quote same (book, prop) at the same snapshot
        # take the latest seen (rare; should be the same value anyway).
        layers[f"{book_key}_layer"] = {"odds": r.get("odds")}

    # Stage 2: resolve each via the LIVE chain function.
    out: Dict[Tuple[str, str, str, float, str], Tuple[Optional[int], str]] = {}
    for k, layers in staged.items():
        ref, book = _pick_reference_odds(
            layers.get("dk_layer"),
            layers.get("mgm_layer"),
            fd_layer=layers.get("fd_layer"),
            bol_layer=layers.get("bol_layer"),
            csr_layer=layers.get("csr_layer"),
            sport=sport,
        )
        # `_pick_reference_odds` returns float (MLB consensus) or int
        # (single book). Normalise to int for the router.
        ref_int = int(round(float(ref))) if ref is not None else None
        out[k] = (ref_int, book)
    return out


__all__ = ["load_reference_odds_for_snapshot"]
