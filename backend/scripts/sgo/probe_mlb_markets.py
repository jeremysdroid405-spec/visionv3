"""
Probe SGO for MLB market availability, especially home_runs / stolen_bases.

WHY: Our prod replay cache for May 2025 has NO `batter_home_runs` rows
even though the live MLB-HF model has a trained pkl for HR. Both
`sgo_replay_alt_odds_raw` and `sgo_pp_research_outcomes` are also
HR-free for the same window. That means HR is being dropped UPSTREAM
of our writes — either:
  (a) SGO's PrizePicks-anchored feed doesn't carry HR for MLB
  (b) SGO does but our reshape/ingest filters HR
This probe hits SGO directly and dumps every market_id + market_name
returned for a recent MLB window so we can confirm which.

RUN ON PROD ONLY — preview pod has no SGO_API_KEY.

CLI:
    python -m scripts.sgo.probe_mlb_markets \
        --start=2025-05-01 --end=2025-05-03 \
        --max-events=50 --save=/tmp/mlb_market_probe.json
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
for env_path in ("/app/backend/.env", "/var/www/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

import httpx  # noqa: E402


SGO_BASE = os.environ.get("SGO_BASE_URL", "https://api.sportsgameodds.com/v2")
SGO_KEY  = os.environ.get("SGO_API_KEY") or os.environ.get("SPORTS_GAME_ODDS_API_KEY")
# Markets we specifically want to confirm/deny.
LOOK_FOR_KEYWORDS = (
    "home_run", "homerun", "homer", "hr",
    "stolen_base", "stolen", "sb",
    "single", "double", "triple",
    "walk", "bb",
    "fantasy", "pitch", "out",
    "hits", "rbi",
)


async def _probe(start: str, end: str, max_events: int = 50) -> Dict[str, Any]:
    if not SGO_KEY:
        raise SystemExit("SGO_API_KEY is not set — must run on prod.")
    market_counter: Counter = Counter()
    statid_counter: Counter = Counter()
    market_to_statids: Dict[str, set] = {}
    sample_props: List[Dict[str, Any]] = []
    samples_per_market: Dict[str, int] = {}
    n_events_probed = 0
    headers = {"x-api-key": SGO_KEY}
    async with httpx.AsyncClient(timeout=60.0) as c:
        # Fetch event list for window.
        evt_resp = await c.get(
            f"{SGO_BASE}/events",
            params={"sportID": "BASEBALL", "leagueID": "MLB",
                     "startsAfter": start, "startsBefore": end,
                     "limit": max_events, "expandResults": "true"},
            headers=headers)
        evt_resp.raise_for_status()
        events = evt_resp.json().get("data") or []
        print(f"[probe] fetched {len(events)} MLB events "
                f"{start}..{end}")
        for ev in events:
            n_events_probed += 1
            props = ev.get("props") or []
            if not props:
                # Some payloads return props under `markets[].outcomes`.
                # Walk the entire payload just in case.
                pass
            for p in props:
                # Probable shape: {marketName, statID, statName, ...}
                m = p.get("marketName") or p.get("market") or p.get("marketID")
                sid = p.get("statID") or p.get("stat_id")
                if not m:
                    continue
                market_counter[m] += 1
                if sid:
                    statid_counter[sid] += 1
                    market_to_statids.setdefault(m, set()).add(sid)
                # Keep up to 2 sample rows per unique market so the
                # operator can eyeball the schema.
                if samples_per_market.get(m, 0) < 2:
                    samples_per_market[m] = samples_per_market.get(m, 0) + 1
                    sample_props.append({
                        "event_id":   ev.get("eventID") or ev.get("id"),
                        "game_date":  ev.get("startsAt"),
                        "market":     m,
                        "stat_id":    sid,
                        "line":       p.get("line") or p.get("point"),
                        "player_id":  (p.get("playerID")
                                              or p.get("player_id")),
                        "raw_keys":   list(p.keys()),
                    })
    return {
        "ts":                    datetime.now(timezone.utc).isoformat(),
        "window":                {"start": start, "end": end},
        "n_events_probed":       n_events_probed,
        "n_distinct_markets":    len(market_counter),
        "n_distinct_stat_ids":   len(statid_counter),
        "markets_by_count": [
            {"market": m, "n": n,
              "stat_ids": sorted(list(market_to_statids.get(m, set())))}
            for m, n in market_counter.most_common()
        ],
        "stat_ids_by_count": [
            {"stat_id": s, "n": n} for s, n in statid_counter.most_common()
        ],
        "matches_for_keywords": {
            kw: [
                {"market": m, "n": n}
                for m, n in market_counter.most_common()
                if kw in (m or "").lower()
            ]
            for kw in LOOK_FOR_KEYWORDS
        },
        "sample_props":           sample_props,
    }


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SGO MLB market probe")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         required=True)
    p.add_argument("--max-events", type=int, default=50)
    p.add_argument("--save",        default=None,
                       help="optional path to dump full JSON")
    return p.parse_args()


def main() -> int:
    args = _parse()
    out = asyncio.run(_probe(args.start, args.end, args.max_events))
    print(f"[probe] probed {out['n_events_probed']} events / "
            f"{out['n_distinct_markets']} markets / "
            f"{out['n_distinct_stat_ids']} stat_ids")
    print("\n=== TOP 30 markets ===")
    for row in out["markets_by_count"][:30]:
        sids = ",".join(row["stat_ids"][:5])
        print(f"  {row['market']:40s} n={row['n']:5d}   stat_ids=[{sids}]")
    print("\n=== KEYWORD MATCHES ===")
    for kw, hits in out["matches_for_keywords"].items():
        if hits:
            print(f"  [{kw}]")
            for h in hits[:5]:
                print(f"    {h['market']:40s} n={h['n']}")
    if args.save:
        with open(args.save, "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n[probe] wrote {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
