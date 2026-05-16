"""
Alternate-ladder audit — directly probes The Odds API historical
endpoint for one MLB event and analyses raw payload depth per
(player, market, book, side).

Writes:
  - /app/backend/audits/alt_ladder_audit_<event_id>.json  (full raw)
  - prints depth breakdown to stdout
"""
import asyncio, json, os, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from scripts.odds_api_backfill.client import OddsAPIClient
from services.replay.odds_fetch import fetch_historical_event_odds_envelope

DATE = "2026-05-05"
SNAPSHOT = f"{DATE}T11:00:00Z"
REGIONS = ["us", "us2"]
MARKETS_ALT = [
    "batter_hits_alternate",
    "batter_total_bases_alternate",
    "batter_runs_scored_alternate",
    "batter_rbis_alternate",
    "batter_hits_runs_rbis_alternate",
    "batter_strikeouts_alternate",
    "pitcher_strikeouts_alternate",
]
OUT_DIR = "/app/backend/audits"


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    # Pick an event from the stored alt-odds raw (proves we're auditing
    # the same event our ingest saw).
    ev = await db.mlb_historical_alt_odds_raw.find_one(
        {"game_date": DATE},
        projection={"_id": 0, "event_id": 1, "home_team": 1,
                    "away_team": 1, "commence_time": 1},
    )
    event_id = ev["event_id"]
    home = ev["home_team"]; away = ev["away_team"]
    print(f"\n=== AUDIT EVENT ===\n  {away} @ {home}\n  event_id={event_id}\n"
          f"  commence={ev['commence_time']}\n  snapshot={SNAPSHOT}\n")

    raw_all = {"event_id": event_id, "snapshot": SNAPSHOT,
               "regions": REGIONS, "markets": {}}

    async with OddsAPIClient(min_remaining_credits=100) as client:
        for market in MARKETS_ALT:
            print(f"\n--- {market} ---")
            try:
                env = await fetch_historical_event_odds_envelope(
                    client, sport="baseball_mlb", event_id=event_id,
                    markets=[market], regions=REGIONS, snapshot_iso=SNAPSHOT,
                )
            except Exception as exc:
                print(f"  ERROR: {exc}")
                continue
            inner = env.get("data") if isinstance(env, dict) and "data" in env else env
            raw_all["markets"][market] = inner
            books = inner.get("bookmakers") or []
            print(f"  books returned: {len(books)}")
            for bm in books[:6]:
                bk = bm.get("key")
                outcomes = (bm.get("markets") or [{}])[0].get("outcomes") or []
                # Per-(player, side) → list of lines
                ladder = defaultdict(list)
                for o in outcomes:
                    ladder[(o.get("description"), (o.get("name") or "").lower())].append(o.get("point"))
                lines_total = sum(len(v) for v in ladder.values())
                players = len({p for (p, _) in ladder.keys()})
                print(f"    {bk:25s} outcomes={len(outcomes)}  players={players}  total_lines={lines_total}")

            # Per-player ladder analysis across ALL books
            per_pb = defaultdict(lambda: defaultdict(list))  # player -> book -> [lines per side]
            for bm in books:
                bk = bm.get("key")
                outs = (bm.get("markets") or [{}])[0].get("outcomes") or []
                for o in outs:
                    p = o.get("description")
                    side = (o.get("name") or "").lower()
                    line = o.get("point")
                    per_pb[p][bk].append((side, line))

            # depth stats: per-player aggregate (any book) line set
            depth_per_player_any = {}
            depth_per_player_per_book = []
            sides_seen_any = {}
            for p, books_d in per_pb.items():
                all_lines = set()
                sides = set()
                for bk, entries in books_d.items():
                    book_lines = {ln for (sd, ln) in entries if ln is not None}
                    depth_per_player_per_book.append(len(book_lines))
                    all_lines.update(book_lines)
                    sides.update(sd for (sd, _) in entries)
                depth_per_player_any[p] = len(all_lines)
                sides_seen_any[p] = sides

            if depth_per_player_any:
                vals = list(depth_per_player_any.values())
                vals_pb = depth_per_player_per_book
                both = sum(1 for s in sides_seen_any.values() if {"over","under"}.issubset(s))
                print(f"  ladder depth (distinct lines per player across all books): "
                      f"avg={sum(vals)/len(vals):.1f}  max={max(vals)}  min={min(vals)}")
                print(f"  ladder depth (per player per book): "
                      f"avg={sum(vals_pb)/len(vals_pb):.1f}  max={max(vals_pb)}  min={min(vals_pb)}")
                print(f"  players with BOTH over+under: {both}/{len(sides_seen_any)}")
                # Sample 3 players
                sample = sorted(depth_per_player_any.items(),
                                key=lambda kv: -kv[1])[:3]
                for p_name, depth in sample:
                    print(f"\n  Sample: {p_name}  total_lines_across_books={depth}")
                    for bk, entries in per_pb[p_name].items():
                        rows = sorted(entries, key=lambda x: (x[0], x[1] or 0))
                        rendered = ", ".join(f"{sd[:4]}{ln}" for sd, ln in rows)
                        print(f"    {bk:25s} {rendered}")

    # Persist raw payload
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"alt_ladder_audit_{event_id[:12]}.json")
    with open(out_path, "w") as f:
        json.dump(raw_all, f, indent=2, default=str)
    print(f"\nFull raw payload saved → {out_path}")

    # ── Expected vs actual: compare distinct lines in raw vs distinct
    # rows in our `mlb_historical_alt_odds_raw` collection.
    print("\n=== EXPECTED vs ACTUAL (raw API → DB stored) ===")
    for market in MARKETS_ALT:
        inner = raw_all["markets"].get(market) or {}
        books = inner.get("bookmakers") or []
        expected = 0
        per_pb_set = defaultdict(set)
        for bm in books:
            bk = bm.get("key")
            outs = (bm.get("markets") or [{}])[0].get("outcomes") or []
            for o in outs:
                k = (o.get("description"), (o.get("name") or "").lower(),
                     o.get("point"), bk)
                per_pb_set[bk].add(k)
                expected += 1  # raw outcomes count
        actual = await db.mlb_historical_alt_odds_raw.count_documents({
            "game_date": DATE, "event_id": event_id, "market": market,
        })
        print(f"  {market:40s}  raw_outcomes={expected:>5}   "
              f"db_rows={actual:>5}   delta={expected-actual:+d}")

    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
