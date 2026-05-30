"""
SGO history floor discovery — find earliest available date per sport.

For each sport (mlb, nba, nfl) walks backwards in time probing the
SGO `/v2/events` endpoint until it finds the earliest UTC date that
returns ≥1 event. Then inspects the payload to count player-prop vs
team-prop markets.

NO writes. NO normalization. Pure probes.

Probe strategy:
  1. Year-anchor: probe months {01, 04, 07, 10} of year Y from current
     year backwards. First year with ANY events → "floor year".
  2. Within floor year, probe months 12 → 01 to find earliest month
     with events.
  3. Within earliest month, probe day 28 → 01 to find earliest day.

Output:
  /app/memory/SGO_HISTORY_FLOOR.json

Usage:
  SGO_API_KEY=… python -m scripts.sgo_history_probe
  SGO_API_KEY=… python -m scripts.sgo_history_probe --year-floor 2018
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from workers.team._sgo_provider import SGOFetchError, SGOPayloadProvider


SPORTS = ("nfl", "nba", "mlb")
TEAM_ENTITIES: set[str] = {"home", "away", "all", "game"}

REPORT_JSON = "/app/memory/SGO_HISTORY_FLOOR.json"


def _count_payload(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Walk one SGO payload of events; classify markets by entity."""
    n_player_markets = 0
    n_team_markets   = 0
    n_outcomes_player = 0
    n_outcomes_team   = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        odds = ev.get("odds") or {}
        if not isinstance(odds, dict):
            continue
        for mk_key, mkt in odds.items():
            if not isinstance(mkt, dict):
                continue
            entity = (mkt.get("statEntityID") or "").lower()
            by_bm = mkt.get("byBookmaker") or {}
            n_outcomes = len(by_bm) if isinstance(by_bm, dict) else 0
            if entity in TEAM_ENTITIES:
                n_team_markets   += 1
                n_outcomes_team  += n_outcomes
            elif entity:
                n_player_markets += 1
                n_outcomes_player += n_outcomes
    return {
        "n_events":           len(events),
        "n_player_markets":   n_player_markets,
        "n_team_markets":     n_team_markets,
        "n_player_outcomes":  n_outcomes_player,
        "n_team_outcomes":    n_outcomes_team,
    }


async def _probe_one(prov: SGOPayloadProvider,
                       sport: str, ymd: str) -> Dict[str, Any]:
    """Probe one (sport, date) tuple. Returns probe metadata."""
    t0 = time.time()
    try:
        fetched = prov.fetch_events_by_date(
            sport=sport, game_date=ymd, max_pages=5, page_size=50)
        events = fetched.get("events") or []
        counts = _count_payload(events)
        dt = time.time() - t0
        return {"sport": sport, "date": ymd, "ok": True,
                "elapsed_s": round(dt, 2), **counts,
                "n_pages": fetched.get("n_pages")}
    except SGOFetchError as exc:
        return {"sport": sport, "date": ymd, "ok": False,
                "elapsed_s": round(time.time() - t0, 2),
                "kind": exc.kind, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"sport": sport, "date": ymd, "ok": False,
                "elapsed_s": round(time.time() - t0, 2),
                "kind": "unexpected", "error": str(exc)[:200]}


async def _discover_floor(prov: SGOPayloadProvider, sport: str,
                              *, year_floor: int = 2018) -> Dict[str, Any]:
    """Return earliest UTC date for `sport`. Walks year-quarter,
    then month-down, then day-down."""
    out: Dict[str, Any] = {"sport": sport,
                             "probes": [],
                             "year_floor_input": year_floor}
    today = date.today()
    cur_year = today.year
    sport_seasons = {
        "mlb": [4, 5, 6, 7, 8, 9, 10],   # MLB active months
        "nba": [11, 12, 1, 2, 3, 4, 5], # NBA active months (Oct-Jun)
        "nfl": [9, 10, 11, 12, 1, 2],    # NFL active months (Sep-Feb)
    }
    active_months = sport_seasons.get(sport, list(range(1, 13)))

    # ── Phase 1: walk backwards year by year (use mid-season month) ──
    mid_month = active_months[len(active_months) // 2]
    earliest_year_hit: Optional[int] = None
    for yr in range(cur_year, year_floor - 1, -1):
        ymd = f"{yr:04d}-{mid_month:02d}-15"
        r = await _probe_one(prov, sport, ymd)
        out["probes"].append(r)
        if r.get("ok") and r.get("n_events", 0) > 0:
            earliest_year_hit = yr
        elif earliest_year_hit is not None:
            # we already had a hit and now we're empty → previous year
            # was the floor year
            break
    if earliest_year_hit is None:
        out["floor_year"] = None
        out["earliest_date"] = None
        out["status"] = "no_data_found"
        return out

    out["floor_year"] = earliest_year_hit

    # ── Phase 2: within floor year, find earliest month with events ──
    earliest_month: Optional[int] = None
    for m in range(1, 13):
        ymd = f"{earliest_year_hit:04d}-{m:02d}-15"
        r = await _probe_one(prov, sport, ymd)
        out["probes"].append(r)
        if r.get("ok") and r.get("n_events", 0) > 0:
            earliest_month = m
            break
    if earliest_month is None:
        # Floor year had hits in mid_month only — fall through.
        earliest_month = mid_month

    # If we landed at January → also probe December of prior year for
    # completeness (so NFL/NBA seasons that straddle Dec/Jan aren't
    # truncated). We've already confirmed the prior year is empty for
    # the mid-month probe, but Dec might still have data.
    if earliest_month <= 2 and earliest_year_hit > year_floor:
        for back_m in (12, 11, 10):
            ymd = f"{earliest_year_hit - 1:04d}-{back_m:02d}-15"
            r = await _probe_one(prov, sport, ymd)
            out["probes"].append(r)
            if r.get("ok") and r.get("n_events", 0) > 0:
                earliest_year_hit = earliest_year_hit - 1
                earliest_month   = back_m
                break

    # ── Phase 3: within earliest month, find earliest day ──
    earliest_day: Optional[int] = None
    # walk forward 1..28 — first hit is earliest day
    for d in range(1, 29):
        ymd = f"{earliest_year_hit:04d}-{earliest_month:02d}-{d:02d}"
        r = await _probe_one(prov, sport, ymd)
        out["probes"].append(r)
        if r.get("ok") and r.get("n_events", 0) > 0:
            earliest_day = d
            break
    if earliest_day is None:
        earliest_day = 1

    out["earliest_date"] = (
        f"{earliest_year_hit:04d}-{earliest_month:02d}-{earliest_day:02d}")
    out["status"] = "ok"
    # latest date — probe today and walk backwards if empty
    import datetime as _dt
    latest: Optional[str] = None
    for back in range(0, 60):
        candidate = (_dt.datetime.now(_dt.timezone.utc).date()
                     - _dt.timedelta(days=back))
        ymd = candidate.isoformat()
        r = await _probe_one(prov, sport, ymd)
        out["probes"].append(r)
        if r.get("ok") and r.get("n_events", 0) > 0:
            latest = ymd
            break
    out["latest_date"] = latest
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(prog="sgo_history_probe")
    ap.add_argument("--year-floor", type=int, default=2018)
    ap.add_argument("--sports", default="mlb,nba,nfl")
    args = ap.parse_args()

    api_key = os.environ.get("SGO_API_KEY", "")
    if not api_key:
        print("ERROR: SGO_API_KEY not set", file=sys.stderr)
        return 2
    prov = SGOPayloadProvider(api_key)

    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    results: List[Dict[str, Any]] = []
    for sport in sports:
        print(f"\n─── probing {sport} ───")
        r = await _discover_floor(prov, sport,
                                       year_floor=args.year_floor)
        results.append(r)
        print(f"  earliest: {r.get('earliest_date')}  "
              f"latest: {r.get('latest_date')}  "
              f"n_probes: {len(r.get('probes', []))}")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "year_floor":   args.year_floor,
        "sports":       results,
    }
    with open(REPORT_JSON, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nReport → {REPORT_JSON}")
    print("\n─── EARLIEST DATA AVAILABLE ───")
    print(f"  {'sport':<6} {'earliest':<12} {'latest':<12} "
          f"{'sample @ earliest (events / player_mk / team_mk)':<60}")
    for r in results:
        ed = r.get("earliest_date") or "—"
        lt = r.get("latest_date") or "—"
        sample = None
        for p in r.get("probes", []):
            if p.get("date") == ed and p.get("ok"):
                sample = p
                break
        ev   = sample.get("n_events", 0) if sample else 0
        pmk  = sample.get("n_player_markets", 0) if sample else 0
        tmk  = sample.get("n_team_markets", 0) if sample else 0
        print(f"  {r['sport']:<6} {ed:<12} {lt:<12} "
              f"events={ev} player_markets={pmk} team_markets={tmk}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
