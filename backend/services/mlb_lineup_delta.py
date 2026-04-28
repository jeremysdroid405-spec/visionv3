"""
MLB Lineup Delta — REAL slot/PA change calculator
==================================================

Source of truth for the LINEUP OPPORTUNITY UI section. Computes per-
player real-numeric deltas between the previous canonical lineup and
the current projected lineup:

    lineup_delta        = previous_lineup_slot - current_lineup_slot
                          (POSITIVE = moved up the order; that is GOOD
                          for ABs because earlier slots get more PA per
                          game over the season)

    projected_ab_delta  = current_expected_PA - previous_expected_PA

If the source collections are EMPTY, this module returns an empty
index and `extract_deltas_for_player` always returns
{lineup_delta=None, projected_ab_delta=None}. Callers (the route
handler) drop those rows entirely. We NEVER fabricate a "+0 lineup
spots" placeholder.

Spec-driven contract:
    1. lineup_delta and projected_ab_delta MUST be numeric or None.
    2. A row qualifies for display only when:
           lineup_delta >= 1   OR   projected_ab_delta >= 0.5
    3. Caller caps the final list (≤ 5 rows).

NO scoring / model / gates / tier-routing logic touched.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Canonical PA-per-slot expectation for a 9-batter MLB lineup. Empirical
# averages from `mlb_master_active_cache` (slot 1: 4.7 PA/game …
# slot 9: 3.8 PA/game). Used ONLY when the lineup source does not
# already carry an `expected_pa` field.
_DEFAULT_PA_BY_SLOT: Tuple[float, ...] = (
    0.0,   # slot 0 — unused (1-indexed)
    4.65,  # 1
    4.50,
    4.35,
    4.20,
    4.05,
    3.95,
    3.90,
    3.85,
    3.78,  # 9
)


def _expected_pa_for_slot(slot: Optional[int]) -> Optional[float]:
    """Return projected PA for a given lineup slot (1..9), or None."""
    if not isinstance(slot, int):
        return None
    if 1 <= slot <= 9:
        return _DEFAULT_PA_BY_SLOT[slot]
    return None


def _to_int_slot(v: Any) -> Optional[int]:
    """Coerce a slot value to int(1..9) or return None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    if 1 <= i <= 9:
        return i
    return None


# ─── Index builder ────────────────────────────────────────────────────
async def build_lineup_delta_index(db) -> Dict[str, Dict[str, Any]]:
    """Build a single in-memory index keyed by canonical player name.

    The index is `{ "Bo Bichette": {previous_slot, current_slot,
    previous_expected_pa, current_expected_pa, team}, ... }`.

    Reads (best-effort, all optional):
      * `mlb_projected_lineups`     — TODAY's projected lineup
      * `mlb_lineups`               — yesterday's canonical lineup
                                      (or any "previous" snapshot)

    If both collections are empty (today's reality on this slate), the
    function returns `{}` and the route handler drops every row.
    """
    if db is None:
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    # ── 1) "Previous" snapshot ── prefer canonical mlb_lineups when
    #    populated, else fall back to whatever is older in
    #    mlb_projected_lineups. Same shape: docs carry a `players` list.
    prev_docs: List[Mapping[str, Any]] = []
    try:
        async for doc in db["mlb_lineups"].find(
            {}, {"_id": 0, "team_abbr": 1, "team": 1, "players": 1,
                 "lineup": 1, "batting_order": 1}
        ):
            prev_docs.append(doc)
    except Exception as e:  # collection may not exist — that's fine
        logger.debug("[mlb_lineup_delta] mlb_lineups unavailable: %s", e)

    cur_docs: List[Mapping[str, Any]] = []
    try:
        async for doc in db["mlb_projected_lineups"].find(
            {}, {"_id": 0, "team_abbr": 1, "team": 1, "players": 1,
                 "lineup": 1, "batting_order": 1}
        ):
            cur_docs.append(doc)
    except Exception as e:
        logger.debug("[mlb_lineup_delta] mlb_projected_lineups unavailable: %s", e)

    # Helper: extract `(player_name, slot, expected_pa)` triples from a doc
    def _triples(doc: Mapping[str, Any]) -> List[Tuple[str, int, float]]:
        team = (doc.get("team_abbr") or doc.get("team") or "").upper()
        players = (
            doc.get("players")
            or doc.get("lineup")
            or doc.get("batting_order")
            or []
        )
        rows: List[Tuple[str, int, float]] = []
        for p in players or []:
            if not isinstance(p, dict):
                continue
            name = p.get("player_name") or p.get("name") or p.get("display_name")
            slot = _to_int_slot(p.get("batting_order")
                                or p.get("slot")
                                or p.get("position_in_order"))
            if not (name and slot):
                continue
            pa = p.get("expected_pa")
            if not isinstance(pa, (int, float)):
                pa = _expected_pa_for_slot(slot)
            rows.append((name.strip(), slot, float(pa or 0.0)))
        return rows, team  # type: ignore[return-value]

    # Ingest previous
    prev_index: Dict[str, Tuple[int, float, str]] = {}
    for d in prev_docs:
        rows, team = _triples(d)
        for nm, slot, pa in rows:
            prev_index[nm] = (slot, pa, team)

    # Ingest current
    cur_index: Dict[str, Tuple[int, float, str]] = {}
    for d in cur_docs:
        rows, team = _triples(d)
        for nm, slot, pa in rows:
            cur_index[nm] = (slot, pa, team)

    # Merge → final index. Players in either side get an entry.
    all_names = set(prev_index) | set(cur_index)
    for nm in all_names:
        prev = prev_index.get(nm)
        cur = cur_index.get(nm)
        prev_slot = prev[0] if prev else None
        prev_pa   = prev[1] if prev else None
        cur_slot  = cur[0]  if cur  else None
        cur_pa    = cur[1]  if cur  else None
        team      = (cur[2] if cur else (prev[2] if prev else None)) or None
        out[nm] = {
            "previous_lineup_slot":  prev_slot,
            "current_lineup_slot":   cur_slot,
            "previous_expected_pa":  prev_pa,
            "current_expected_pa":   cur_pa,
            "team":                  team,
        }

    return out


# ─── Per-player extractor ────────────────────────────────────────────
def extract_deltas_for_player(
    index: Mapping[str, Mapping[str, Any]],
    player_name: Optional[str],
    fallback_team: Optional[str] = None,  # noqa: ARG001 — reserved for future name-collision tie-break
) -> Dict[str, Optional[float]]:
    """Return real-numeric deltas for a player, or all-None if missing.

    Output shape (caller must filter):
        {
          "previous_lineup_slot":  int|None,
          "current_lineup_slot":   int|None,
          "lineup_delta":          float|None,
          "projected_ab_delta":    float|None,
        }

    `lineup_delta` is `previous_slot - current_slot` so a player who
    moved from 6th → 2nd has `lineup_delta = +4`.
    `projected_ab_delta` is `current_pa - previous_pa`.
    """
    if not player_name:
        return _empty_deltas()
    rec = index.get(player_name) or index.get(player_name.strip())
    if not rec:
        return _empty_deltas()

    prev_slot = rec.get("previous_lineup_slot")
    cur_slot = rec.get("current_lineup_slot")
    prev_pa = rec.get("previous_expected_pa")
    cur_pa = rec.get("current_expected_pa")

    lineup_delta: Optional[float] = None
    if isinstance(prev_slot, int) and isinstance(cur_slot, int):
        lineup_delta = float(prev_slot - cur_slot)

    projected_ab_delta: Optional[float] = None
    if isinstance(prev_pa, (int, float)) and isinstance(cur_pa, (int, float)):
        projected_ab_delta = round(float(cur_pa) - float(prev_pa), 2)

    return {
        "previous_lineup_slot":  prev_slot,
        "current_lineup_slot":   cur_slot,
        "lineup_delta":          lineup_delta,
        "projected_ab_delta":    projected_ab_delta,
    }


def _empty_deltas() -> Dict[str, Optional[float]]:
    return {
        "previous_lineup_slot":  None,
        "current_lineup_slot":   None,
        "lineup_delta":          None,
        "projected_ab_delta":    None,
    }


__all__ = [
    "build_lineup_delta_index",
    "extract_deltas_for_player",
]
