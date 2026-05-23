"""
probe_nfl_data.py — diagnostic-only NFL data discovery against SGO.

NEVER writes to Mongo. Hits SGO for the probe window and prints:

    1. raw event count + sample event keys
    2. distinct (statID, marketName) pairs observed across odds
    3. distinct playerStat keys seen in expandResults payloads
    4. mapping coverage report against
       services.replay.nfl_stat_family_map (mapped / unmapped / unused)
    5. a small sample raw player-stats dict so we can see field names

Use this BEFORE the real ingest to confirm:
    • SGO key has NFL access
    • our family map covers the live stat_id catalogue
    • playerStats payload shape matches what build_historical_outcomes
      expects

Usage:
    python -m scripts.sgo.probe_nfl_data \\
        --start=2025-09-04 --end=2025-09-09 \\
        [--max-events=200] [--save-samples=/tmp/nfl_probe.json]
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from scripts.sgo.client import SGOClient
from services.replay.nfl_stat_family_map import (
    NFL_FAMILIES, NFL_FAMILY_ALIASES, canonical_family,
)

LEAGUE = "NFL"


def _g(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """case-insensitive get."""
    if not isinstance(d, dict):
        return default
    lm = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = lm.get(k.lower())
        if v is not None:
            return v
    return default


async def _amain(args) -> int:
    print("=" * 72)
    print("  PROBE — NFL SGO data discovery (read-only)")
    print(f"  window: {args.start} → {args.end}")
    print("=" * 72)

    client = SGOClient()

    # ── 1. Pull a small batch of events with expandResults=true ────
    sample_event_keys: Set[str] = set()
    sample_player_keys: Counter = Counter()
    sample_stat_keys: Counter = Counter()       # keys inside playerStats[*].stats
    stat_id_market: Counter = Counter()         # (statID, marketName)
    odds_keys: Set[str] = set()
    n_events = 0
    sample_event: Dict[str, Any] | None = None
    sample_player_stats: List[Dict[str, Any]] = []

    try:
        async for ev in client.iter_finalized_events_with_results(
            league_id=LEAGUE,
            starts_after=f"{args.start}T00:00:00Z",
            starts_before=f"{args.end}T23:59:59Z",
            page_size=50,
            max_pages=20,
        ):
            n_events += 1
            sample_event_keys.update(ev.keys())
            if sample_event is None:
                sample_event = ev

            # 1a. Player-stats shape
            ps_block = (ev.get("playerStats") or ev.get("players") or
                          ev.get("playerResults") or [])
            for ps in ps_block:
                sample_player_keys.update(ps.keys())
                stats = (ps.get("stats") or ps.get("statistics") or {})
                if isinstance(stats, dict):
                    sample_stat_keys.update(stats.keys())
                if len(sample_player_stats) < 3 and isinstance(stats, dict) and stats:
                    sample_player_stats.append({
                        "player_id":   _g(ps, "playerID", "player_id", "id"),
                        "player_name": _g(ps, "playerName", "name"),
                        "team_id":     _g(ps, "teamID", "team_id"),
                        "stats":       stats,
                    })

            # 1b. Odds shape (statID / marketName pairs)
            odds_block = (ev.get("odds") or ev.get("oddss") or
                            ev.get("playerOdds") or [])
            for od in odds_block:
                odds_keys.update(od.keys())
                sid = _g(od, "statID", "stat_id")
                mkt = _g(od, "marketName", "market", "playerProp",
                              "propType")
                if sid or mkt:
                    stat_id_market[(str(sid or "?"), str(mkt or "?"))] += 1

            if args.max_events and n_events >= int(args.max_events):
                break
    finally:
        await client.close()

    # ── 2. Report ───────────────────────────────────────────────────
    print()
    print(f"  events fetched: {n_events}")
    if n_events == 0:
        print("  ❌ NO NFL EVENTS RETURNED. Either:")
        print("       • your SGO key lacks NFL coverage")
        print("       • the window contains no finalized games")
        print("       • leagueID is something other than 'NFL'")
        print()
        print("  Try: python -c \"from scripts.sgo.client import SGOClient; "
                "import asyncio; "
                "print(asyncio.run(SGOClient().get_leagues()))\"")
        print("=" * 72)
        return 2
    if sample_event:
        print(f"  sample event keys: {sorted(sample_event_keys)[:18]}…")
        print(f"  sample event id:   {_g(sample_event, 'eventID', 'event_id', 'id')}")
        print(f"  sample startTime:  {_g(sample_event, 'startTime', 'commenceTime')}")

    print()
    print("  PLAYER-STATS PAYLOAD shape  (keys observed on playerStats[*]):")
    for k, n in sample_player_keys.most_common(20):
        print(f"    {n:>6}  {k}")

    print()
    print("  PLAYER-STATS NUMERIC KEYS  (inside playerStats[*].stats):")
    for k, n in sample_stat_keys.most_common(60):
        print(f"    {n:>6}  {k}")

    print()
    print("  ODDS payload keys (sample):")
    print(f"    {sorted(odds_keys)[:25]}")

    print()
    print(f"  DISTINCT (statID, marketName) PAIRS — {len(stat_id_market)} unique:")
    for (sid, mkt), n in stat_id_market.most_common(80):
        canon = canonical_family(sid) or canonical_family(mkt) or "?"
        print(f"    n={n:<5} {sid:<32} {mkt:<32} → {canon}")

    # ── 3. Mapping coverage ─────────────────────────────────────────
    mapped: Set[str] = set()
    unmapped: List[str] = []
    for (sid, mkt) in stat_id_market.keys():
        c = canonical_family(sid) or canonical_family(mkt)
        if c:
            mapped.add(c)
        else:
            unmapped.append(f"{sid}|{mkt}")
    unused = [f for f in NFL_FAMILIES if f not in mapped]
    print()
    print("=" * 72)
    print("  MAPPING COVERAGE REPORT")
    print("=" * 72)
    print(f"  canonical families mapped to ≥1 SGO stat_id: "
            f"{len(mapped)} / {len(NFL_FAMILIES)}")
    print(f"    mapped:    {sorted(mapped)}")
    if unused:
        print(f"    NO DATA:   {unused}")
    if unmapped:
        print(f"  unmapped SGO stat_ids (need entry in nfl_stat_family_map.py):")
        for u in unmapped[:40]:
            print(f"    • {u}")
        if len(unmapped) > 40:
            print(f"    … and {len(unmapped) - 40} more")

    print()
    if args.save_samples and sample_player_stats:
        with open(args.save_samples, "w") as fh:
            json.dump({
                "event_keys": sorted(sample_event_keys),
                "player_stats_keys": dict(sample_player_keys),
                "stat_id_market_pairs": [
                    {"stat_id": s, "market": m, "n": n}
                    for (s, m), n in stat_id_market.most_common()
                ],
                "sample_player_stats": sample_player_stats,
            }, fh, indent=2, default=str)
        print(f"  samples written to {args.save_samples}")
    print("  PROBE COMPLETE.")
    print("=" * 72)
    return 0


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end",   required=True, help="YYYY-MM-DD")
    p.add_argument("--max-events", type=int, default=200,
                     help="Cap on events to inspect (cheap, default 200).")
    p.add_argument("--save-samples", default=None,
                     help="Path to dump sample JSON (event keys, "
                          "stat_id pairs, sample stats dicts).")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain(_parse())))
