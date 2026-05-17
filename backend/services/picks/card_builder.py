"""Production card builder — pure, sport-agnostic, deterministic.

Phase 3 of the universal replay harness. This module takes a list of
graded prop rows (the output of Layer-3 + Layer-4) and produces the
ranked card set that would be displayed to a user, applying:

  1. **Best-book selection**: for each (player, stat, line, side, event)
     key, keep the row with the highest edge (mirrors live "best book").
  2. **Per-player dedup** (configurable): keep only the single best row
     per unique key (default: `player_name_normalized` alone, matching
     `get_war_zone()`'s "one pick per player" rule).
  3. **Per-game top-N** (configurable, optional): cap the displayed
     picks per `event_id`.
  4. **Final card ordering**: stable, deterministic sort by the
     configured `order_by` field tuple.
  5. **Slate top-K**: hard cap on total displayed picks.

Pure: no DB I/O, no frontend coupling, no global state. Same inputs →
same outputs. Importable from both live and replay code paths.

Usage:
    from services.picks.card_builder import build_production_cards

    cards = build_production_cards(
        rows,                            # list of dicts (Layer-3+4 output)
        tier="war_zone",
        per_game_top_n=None,             # no per-game cap (matches live war_zone)
        slate_top_k=20,                  # live war_zone shows 20
        dedupe_keys=("player_name_normalized",),
        order_by=("edge", "projection_mu"),
        replay_serial="MLB-PRODREPLAY-...",  # carried through onto each card
        sport="mlb",
    )

The result is a list of dicts matching `ProductionReplayCard` (schemas.py).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ── Defaults for the live war_zone display contract ─────────────────
DEFAULT_DEDUPE_KEYS  = ("player_name_normalized",)
DEFAULT_ORDER_BY     = ("edge", "projection_mu")
DEFAULT_SLATE_TOP_K  = 20      # `get_war_zone()` hard-caps at 20
DEFAULT_PER_GAME_TOP_N: Optional[int] = None   # live applies no per-game cap


def _row_value(row: Dict[str, Any], field: str, default: float = 0.0) -> float:
    v = row.get(field)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _row_sort_key(row: Dict[str, Any],
                   order_by: Sequence[str]) -> Tuple[float, ...]:
    """All ordering fields are treated as numeric descending. Missing
    fields fall back to 0.0 so they sort last."""
    return tuple(-_row_value(row, f) for f in order_by)


def _book_key(row: Dict[str, Any]) -> Tuple:
    """Identity key for "the same pick across multiple books" — all the
    fields that describe the bet except `book` and `odds`."""
    return (
        row.get("player_name_normalized"),
        row.get("stat_family"),
        float(row.get("line") or 0.0),
        row.get("side"),
        row.get("event_id"),
    )


def select_best_book(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse rows that share `_book_key()` down to one row, keeping
    the row with the highest `edge`. Augments the kept row with
    `odds_was_best_among_n_books` (count of book offers collapsed).

    Pure function: returns NEW rows, does NOT mutate inputs.
    """
    grouped: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[_book_key(r)].append(r)
    out: List[Dict[str, Any]] = []
    for _, group in grouped.items():
        # Prefer max edge; tie-break on max model_probability for stability
        best = max(
            group,
            key=lambda r: (_row_value(r, "edge"),
                            _row_value(r, "model_probability")),
        )
        kept = dict(best)
        kept["odds_was_best_among_n_books"] = len(group)
        out.append(kept)
    return out


def dedupe_by_keys(rows: Iterable[Dict[str, Any]],
                    keys: Sequence[str],
                    order_by: Sequence[str]) -> List[Dict[str, Any]]:
    """Within rows sharing the dedupe key tuple, keep the row that
    sorts first by `order_by` (descending). Default keys collapse to
    one pick per player — matches `get_war_zone()`'s rule."""
    if not keys:
        return list(rows)
    by_key: Dict[Tuple, Dict[str, Any]] = {}
    for r in rows:
        k = tuple(r.get(field) for field in keys)
        cur = by_key.get(k)
        if cur is None or _row_sort_key(r, order_by) < _row_sort_key(cur, order_by):
            by_key[k] = r
    return list(by_key.values())


def per_game_top_n(rows: Iterable[Dict[str, Any]],
                    n: Optional[int],
                    order_by: Sequence[str]) -> List[Dict[str, Any]]:
    """If n is None, return all rows. Otherwise group by `event_id` and
    keep the top-n by `order_by` (descending) per game."""
    if n is None or n <= 0:
        return list(rows)
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_event[r.get("event_id") or "_no_event"].append(r)
    out: List[Dict[str, Any]] = []
    for _, group in by_event.items():
        group.sort(key=lambda r: _row_sort_key(r, order_by))
        out.extend(group[:n])
    return out


def final_card_order(rows: Iterable[Dict[str, Any]],
                      order_by: Sequence[str]) -> List[Dict[str, Any]]:
    """Stable slate-wide sort using the configured field tuple."""
    return sorted(rows, key=lambda r: _row_sort_key(r, order_by))


def _build_card_record(row: Dict[str, Any], *, rank: int,
                        replay_serial: str, sport: str, tier: str
                        ) -> Dict[str, Any]:
    """Project a Layer-3+4 output row into a ProductionReplayCard dict."""
    return {
        "replay_serial": replay_serial,
        "sport": sport,
        "tier": tier,
        "game_id": row.get("event_id") or "",
        "rank": rank,
        "player_name": row.get("player_name") or row.get("player_name_normalized"),
        "player_name_normalized": row.get("player_name_normalized") or "",
        "stat_family": row.get("stat_family") or "",
        "market": row.get("market") or "",
        "is_alternate": bool(row.get("is_alternate")),
        "line": float(row.get("line") or 0.0),
        "side": row.get("side") or "",
        "book": row.get("book") or "",
        "odds": int(row.get("odds") or 0),
        "odds_was_best_among_n_books": int(
            row.get("odds_was_best_among_n_books") or 1),
        "projection_mu": float(row.get("projection_mu") or 0.0),
        "model_probability": float(row.get("model_probability") or 0.0),
        "edge": float(row.get("edge") or 0.0),
        "rank_key_hr_l5":  float(row.get("hit_rate_l5") or 0.0),
        "rank_key_hr_l10": float(row.get("hit_rate_l10") or 0.0),
        "rank_key_hr_l20": float(row.get("hit_rate_l20") or 0.0),
        "rank_key_edge":   float(row.get("edge") or 0.0),
        "actual_value": row.get("actual_value"),
        "grade_status": row.get("grade_status") or "ungraded",
        "profit_units": float(row.get("profit_units") or 0.0),
        "stake_units":  float(row.get("stake_units") or 1.0),
    }


def build_production_cards(
    rows: Iterable[Dict[str, Any]],
    *,
    tier: str,
    replay_serial: str,
    sport: str,
    per_game_top_n_value: Optional[int] = DEFAULT_PER_GAME_TOP_N,
    slate_top_k: int = DEFAULT_SLATE_TOP_K,
    dedupe_keys: Sequence[str] = DEFAULT_DEDUPE_KEYS,
    order_by: Sequence[str] = DEFAULT_ORDER_BY,
    require_gate_pass: bool = True,
) -> List[Dict[str, Any]]:
    """Top-level orchestrator. Pure. Deterministic.

    Pipeline:
        rows (Layer-3+4 outputs)
          → filter gate_pass=True
          → select_best_book   (collapse books)
          → dedupe_by_keys     (one pick per player by default)
          → per_game_top_n     (optional per-game cap)
          → final_card_order   (slate-wide sort)
          → slate_top_k        (hard cap)
          → _build_card_record (per-row projection)

    Returns the displayable card list, each item conforming to the
    `ProductionReplayCard` Pydantic schema fields.
    """
    pool = list(rows)
    if require_gate_pass:
        pool = [r for r in pool if r.get("gate_pass")]

    # 1. Best book
    pool = select_best_book(pool)

    # 2. Per-player (or other configurable) dedup
    pool = dedupe_by_keys(pool, dedupe_keys, order_by)

    # 3. Optional per-game cap
    pool = per_game_top_n(pool, per_game_top_n_value, order_by)

    # 4. Final slate sort
    pool = final_card_order(pool, order_by)

    # 5. Slate-wide top-K
    pool = pool[: max(0, int(slate_top_k))]

    # 6. Project to schema-conformant dicts
    return [
        _build_card_record(r, rank=i + 1, replay_serial=replay_serial,
                            sport=sport, tier=tier)
        for i, r in enumerate(pool)
    ]


__all__ = [
    "build_production_cards",
    "select_best_book",
    "dedupe_by_keys",
    "per_game_top_n",
    "final_card_order",
    "DEFAULT_DEDUPE_KEYS",
    "DEFAULT_ORDER_BY",
    "DEFAULT_SLATE_TOP_K",
    "DEFAULT_PER_GAME_TOP_N",
]
