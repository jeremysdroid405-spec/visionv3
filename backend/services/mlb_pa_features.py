"""
MLB Per-Plate-Appearance (PA) Feature Builder
==============================================
Reads `mlb_statcast_raw` (per-pitch / per-PA Baseball Savant rows) and
emits player-level rolling-window aggregates over PA counts (NOT calendar
days). Provides two windows for batters and pitchers:

  Batter:  last 7 PA, last 14 PA, last 30 PA, season-to-date
  Pitcher: last 14 PA-faced, last 30 PA-faced, season-to-date

Distinct from `mlb_statcast_player_features` which buckets by calendar
day. PA-windowing reflects "form on the next pitch" rather than "form
over the last week".

Coverage: statcast_raw currently covers 2026 only (Mar 18 → present).
Pre-2026 lookups will return None → callers default to 0 with a
`*_is_imputed` flag.
"""
from __future__ import annotations
import bisect
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Field schema emitted per window. All values are floats.
BATTER_FIELDS = (
    "xwOBA",            # mean estimated_woba_using_speedangle (BIPs)
    "wOBA",             # mean woba_value (all PAs ending)
    "hard_hit_rate",    # fraction of BIPs with launch_speed >= 95
    "barrel_rate",      # fraction of BIPs meeting simplified barrel def
    "avg_exit_velocity",
    "avg_launch_angle",
    "sweet_spot_rate",  # fraction of BIPs with 8 ≤ LA ≤ 32
    "k_rate",           # PAs ending in K / total PAs
    "bb_rate",          # PAs ending in BB / total PAs
    "whiff_rate",       # swinging_strike / total swings
    "contact_rate",     # 1 - whiff_rate
    "plate_appearances", # PA count in the window
)
PITCHER_FIELDS = (
    "xwOBA_allowed",
    "wOBA_allowed",
    "hard_hit_allowed_rate",
    "barrel_allowed_rate",
    "k_rate",
    "bb_rate",
    "whiff_rate",       # induced
    "plate_appearances",
)

BATTER_WINDOWS = (("pa7", 7), ("pa14", 14), ("pa30", 30))
PITCHER_WINDOWS = (("pa14", 14), ("pa30", 30))


def _is_pa_terminal(row: Dict[str, Any]) -> bool:
    """True when this pitch row ends a plate appearance."""
    e = row.get("events")
    return bool(e) and e != ""


def _is_swing(row: Dict[str, Any]) -> bool:
    """True when the batter swung at the pitch."""
    desc = row.get("description") or ""
    return desc in {
        "hit_into_play", "swinging_strike",
        "swinging_strike_blocked", "foul",
        "foul_tip", "foul_bunt", "missed_bunt",
    }


def _is_whiff(row: Dict[str, Any]) -> bool:
    desc = row.get("description") or ""
    return desc in {"swinging_strike", "swinging_strike_blocked", "foul_tip", "missed_bunt"}


def _is_barrel(launch_speed: Optional[float], launch_angle: Optional[float]) -> bool:
    """Simplified barrel definition (Statcast-equivalent for ≥98 mph
    + 26°-30° core; broader for higher speeds). Captures ~85% of true
    Statcast barrels for the purposes of training-feature signal."""
    if launch_speed is None or launch_angle is None:
        return False
    if launch_speed < 98:
        return False
    # Allowed launch-angle band widens with exit velocity.
    extra = max(0.0, (launch_speed - 98) * 0.5)
    lo = 26 - extra
    hi = 30 + extra
    return lo <= launch_angle <= hi


def _aggregate_window(rows: List[Dict[str, Any]], *, pitcher: bool) -> Dict[str, float]:
    """Aggregate a list of raw rows into a feature dict. The window
    includes ALL pitches whose PA terminates inside the window (so a
    multi-pitch PA contributes ONE PA but every pitch's swing/whiff
    counts toward whiff/contact rates)."""
    if not rows:
        return {}
    pa_count = sum(1 for r in rows if _is_pa_terminal(r))
    if pa_count == 0:
        return {}
    swings = sum(1 for r in rows if _is_swing(r))
    whiffs = sum(1 for r in rows if _is_whiff(r))
    # PA-end events
    ks = sum(1 for r in rows if r.get("events") == "strikeout")
    bbs = sum(1 for r in rows if r.get("events") == "walk")
    # Batted-ball aggregates: PA-terminal rows where the ball was hit
    bb = [r for r in rows if r.get("description") == "hit_into_play"]
    ev = [r.get("launch_speed") for r in bb if r.get("launch_speed") is not None]
    la = [r.get("launch_angle") for r in bb if r.get("launch_angle") is not None]
    xwoba = [r.get("estimated_woba_using_speedangle") for r in bb
              if r.get("estimated_woba_using_speedangle") is not None]
    woba = [r.get("woba_value") for r in rows if _is_pa_terminal(r)
              and r.get("woba_value") is not None]
    hard = sum(1 for v in ev if v is not None and v >= 95)
    sweet = sum(1 for r in bb
                  if r.get("launch_angle") is not None
                  and 8.0 <= r.get("launch_angle") <= 32.0)
    barrels = sum(1 for r in bb
                    if _is_barrel(r.get("launch_speed"), r.get("launch_angle")))

    def _safe(v, denom):
        return float(v) / float(denom) if denom else 0.0

    out: Dict[str, float] = {
        "plate_appearances": float(pa_count),
        "k_rate": _safe(ks, pa_count),
        "bb_rate": _safe(bbs, pa_count),
        "whiff_rate": _safe(whiffs, swings),
        "contact_rate": 1.0 - _safe(whiffs, swings) if swings else 0.0,
        "hard_hit_rate" if not pitcher else "hard_hit_allowed_rate":
            _safe(hard, len(bb)),
        "barrel_rate" if not pitcher else "barrel_allowed_rate":
            _safe(barrels, len(bb)),
        "avg_exit_velocity": float(sum(ev) / len(ev)) if ev else 0.0,
        "avg_launch_angle": float(sum(la) / len(la)) if la else 0.0,
        "sweet_spot_rate": _safe(sweet, len(bb)),
        "xwOBA" if not pitcher else "xwOBA_allowed":
            float(sum(xwoba) / len(xwoba)) if xwoba else 0.0,
        "wOBA" if not pitcher else "wOBA_allowed":
            float(sum(woba) / len(woba)) if woba else 0.0,
    }
    return out


# ---------------------------------------------------------------------------
class MLBPACache:
    """In-memory cache: per-player chronologically-sorted raw-row lists.
    Build once per training/scoring run; query with `(player_id,
    as_of_date)` to get rolling windows."""

    def __init__(self):
        # batter_id → list of rows sorted by (game_date, game_pk,
        # at_bat_number, pitch_number)
        self._batter: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self._pitcher: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        # per-list cumulative PA-terminal indices (sorted) for fast
        # "give me rows for the last N PAs ending strictly before date"
        self._batter_pa_idx: Dict[int, List[Tuple[str, int, int]]] = {}
        self._pitcher_pa_idx: Dict[int, List[Tuple[str, int, int]]] = {}

    def load_from_db(self, db, *, projection: Optional[Dict] = None) -> None:
        """Load all `mlb_statcast_raw` rows (or matching subset)."""
        proj = projection or {
            "_id": 0, "batter": 1, "pitcher": 1, "game_date": 1, "game_pk": 1,
            "at_bat_number": 1, "pitch_number": 1, "events": 1, "description": 1,
            "launch_speed": 1, "launch_angle": 1,
            "estimated_woba_using_speedangle": 1, "woba_value": 1,
        }
        n = 0
        for r in db.mlb_statcast_raw.find({}, proj):
            n += 1
            b = r.get("batter"); p = r.get("pitcher")
            if b is not None: self._batter[int(b)].append(r)
            if p is not None: self._pitcher[int(p)].append(r)
        # Sort + index
        for d, idx in ((self._batter, self._batter_pa_idx),
                        (self._pitcher, self._pitcher_pa_idx)):
            for pid, rows in d.items():
                rows.sort(key=lambda x: (
                    x.get("game_date") or "",
                    x.get("game_pk") or 0,
                    x.get("at_bat_number") or 0,
                    x.get("pitch_number") or 0,
                ))
                pa_terms: List[Tuple[str, int, int]] = []
                for i, x in enumerate(rows):
                    if _is_pa_terminal(x):
                        pa_terms.append((x.get("game_date") or "", i, i))
                idx[pid] = pa_terms
        return n

    def stats(self) -> Dict[str, int]:
        return {
            "rows_total": sum(len(v) for v in self._batter.values()),
            "batters": len(self._batter),
            "pitchers": len(self._pitcher),
        }

    # -----------------------------------------------------------------
    def _window_rows(self, store: Dict[int, List[Dict]],
                       pa_idx: Dict[int, List[Tuple[str, int, int]]],
                       pid: int, as_of_date: str, n_pa: Optional[int]
                       ) -> List[Dict]:
        rows = store.get(pid)
        if not rows:
            return []
        terms = pa_idx.get(pid) or []
        if not terms:
            return []
        # Find last PA-terminal row strictly BEFORE as_of_date.
        # bisect on date string (ISO format sortable).
        if as_of_date:
            cutoff = bisect.bisect_left(
                [t[0] for t in terms], as_of_date)  # first idx with date >= as_of
        else:
            cutoff = len(terms)
        if cutoff <= 0:
            return []
        if n_pa is None:
            start_term_i = 0
        else:
            start_term_i = max(0, cutoff - n_pa)
        last_idx = terms[cutoff - 1][2]    # row idx of last PA-terminal in scope
        start_idx = terms[start_term_i][1] if start_term_i < cutoff else last_idx
        # Walk backwards to include the FIRST pitch of the start PA so
        # whiff/contact rates count all pitches in the window.
        # rows are sorted in PA order; start_idx already points at the
        # PA-terminal row, so we walk back to its first pitch.
        gpk_target = rows[start_idx].get("game_pk")
        ab_target  = rows[start_idx].get("at_bat_number")
        i = start_idx
        while i > 0:
            prev = rows[i - 1]
            if prev.get("game_pk") == gpk_target and prev.get("at_bat_number") == ab_target:
                i -= 1
            else:
                break
        return rows[i: last_idx + 1]

    def batter_features(self, batter_id: int, as_of_date: str
                          ) -> Optional[Dict[str, Dict[str, float]]]:
        if batter_id not in self._batter: return None
        out: Dict[str, Dict[str, float]] = {}
        for tag, n in BATTER_WINDOWS:
            rows = self._window_rows(self._batter, self._batter_pa_idx,
                                       batter_id, as_of_date, n)
            agg = _aggregate_window(rows, pitcher=False)
            if agg: out[tag] = agg
        # season-to-date: all rows before as_of
        rows = self._window_rows(self._batter, self._batter_pa_idx,
                                   batter_id, as_of_date, None)
        agg = _aggregate_window(rows, pitcher=False)
        if agg: out["pa_season"] = agg
        return out or None

    def pitcher_features(self, pitcher_id: int, as_of_date: str
                            ) -> Optional[Dict[str, Dict[str, float]]]:
        if pitcher_id not in self._pitcher: return None
        out: Dict[str, Dict[str, float]] = {}
        for tag, n in PITCHER_WINDOWS:
            rows = self._window_rows(self._pitcher, self._pitcher_pa_idx,
                                       pitcher_id, as_of_date, n)
            agg = _aggregate_window(rows, pitcher=True)
            if agg: out[tag] = agg
        rows = self._window_rows(self._pitcher, self._pitcher_pa_idx,
                                   pitcher_id, as_of_date, None)
        agg = _aggregate_window(rows, pitcher=True)
        if agg: out["pa_season"] = agg
        return out or None
