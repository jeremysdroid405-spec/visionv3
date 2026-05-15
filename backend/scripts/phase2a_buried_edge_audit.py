"""Buried executable-edge audit (READ-ONLY, 2026-05-15).

Identifies MLB FL OVER rejects where:
  • consensus_edge (edge_vs_fair × 100) < 5.0 pp     ← gate rejection reason
  • executable_edge (total_edge × 100)  ≥ 8.0 pp     ← real shopping opportunity
  • tp ≥ 50
  • book_count ≥ 3
  • best_book is a major sportsbook (excludes PrizePicks/Underdog/Fliff/etc.)
  • best_book price has a non-null odds value

For each row:
  • dump every available book's odds + implied prob
  • compute the gap (best-book implied  −  consensus implied)
  • classify the source of the executable edge:
      a) one-rogue-book   (gap ≥ 4pp AND only 1 book off-market)
      b) stale-line        (only 1 book with the off-market price and book_count high)
      c) market-fragmentation (≥ 2 books cluster soft)
      d) legitimate-disagreement (broad bid-ask spread across major books)

Output: comprehensive per-prop table + summary.
"""
from __future__ import annotations
import asyncio, os, sys
from collections import Counter
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

BATTER_STATS = {"Hits","Total Bases","RBIs","Runs","Home Runs","Doubles",
                "Walks","Singles","Hits+Runs+RBIs","Stolen Bases",
                "Batter Strikeouts"}

# Books that count as "major sportsbook" for executable edge.
MAJOR_BOOKS = {
    "dk": "DraftKings",        "draftkings": "DraftKings",
    "fd": "FanDuel",           "fanduel": "FanDuel",
    "mgm": "BetMGM",           "betmgm": "BetMGM",
    "csr": "Caesars",          "caesars": "Caesars",
    "eb":  "ESPN BET",         "espnbet": "ESPN BET",
    "brv": "BetRivers",        "betrivers": "BetRivers",
    "hrb": "Hard Rock",        "hardrockbet": "Hard Rock", "hardrock": "Hard Rock",
    "fla": "Fanatics",         "fanatics": "Fanatics",
    # Smaller-but-tracked majors:
    "bol": "BetOnline",        "betonline": "BetOnline",
    "prx": "BetParx",          "betparx": "BetParx",
    "bly": "Bally Bet",        "ballybet": "Bally Bet",
    "flf": "Fliff",            "fliff": "Fliff",
    "pin": "Pinnacle",         "pinnacle": "Pinnacle",
}
# Books NEVER counted as executable (DFS / pick-em / PrizePicks family):
NON_EXECUTABLE = {"pp", "prizepicks", "underdog", "ud", "sleeper"}

# Field map: prop_scores book-suffix → (display name, executable yes/no)
BOOK_FIELDS = [
    # (suffix, display, executable)
    ("dk",  "DraftKings",  True),
    ("fd",  "FanDuel",     True),
    ("mgm", "BetMGM",      True),
    ("csr", "Caesars",     True),
    ("eb",  "ESPN BET",    True),
    ("brv", "BetRivers",   True),
    ("hrb", "Hard Rock",   True),
    ("bol", "BetOnline",   True),
    ("prx", "BetParx",     True),
    ("bly", "Bally Bet",   True),
    ("flf", "Fliff",       True),
    ("pin", "Pinnacle",    True),
    ("pp",  "PrizePicks",  False),
]


def amer_to_prob(o):
    if o is None:
        return None
    try:
        o = float(o)
    except Exception:
        return None
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def fmt_odds(o):
    if o is None:
        return "—"
    try:
        return f"{int(o):+d}"
    except Exception:
        return "—"


def fmt_p(p):
    return "—" if p is None else f"{p*100:.2f}"


def classify_executable_edge(book_prices: List[Dict[str, Any]],
                              consensus_p: float,
                              best_p: float) -> str:
    """Categorise WHY the best-book line is exploitable."""
    if not book_prices or consensus_p is None or best_p is None:
        return "indeterminate"
    # `book_prices` = executable books that have a price.
    soft_threshold = 0.04  # 4pp softer than consensus
    soft_books = [b for b in book_prices
                  if b["p"] is not None and consensus_p - b["p"] >= soft_threshold]
    n_total = len(book_prices)
    n_soft = len(soft_books)
    if n_soft <= 1 and n_total >= 4:
        # Single book deviating from a tight market → likely rogue/stale.
        return "a) one-rogue-book"
    if n_soft >= 2 and n_soft <= n_total // 2:
        return "c) market-fragmentation"
    # Broad spread — wide range across executable majors.
    ps = [b["p"] for b in book_prices if b["p"] is not None]
    if ps and (max(ps) - min(ps) >= 0.06):
        return "d) legitimate-disagreement"
    return "c) market-fragmentation"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    cur = db.mlb_prop_scores.find(
        {
            "active": True,
            "recommendation": "OVER",
            "routed_tier": "front_lines",
            "tier": "unqualified",
            "stat_type": {"$in": list(BATTER_STATS)},
            "projection_model_version": "MLB_HF_v3.1_phase2a",
            "edge_vs_fair": {"$lt": 0.05},
            "total_edge":   {"$gte": 0.08},
            "tp":           {"$gte": 50.0},
            "book_count":   {"$gte": 3},
        },
        {"_id": 0},
    )
    rows = await cur.to_list(length=None)

    # Filter to executable best_book (exclude PP/UD-only)
    actionable: List[Dict[str, Any]] = []
    for r in rows:
        bb = (r.get("best_book") or "").lower()
        if bb in NON_EXECUTABLE:
            continue
        if r.get("best_book_odds") is None:
            continue
        actionable.append(r)

    # Build per-row book table
    print("=" * 110)
    print("BURIED EXECUTABLE-EDGE AUDIT — MLB FL OVER rejects")
    print(f"  Filters: consensus_edge<5pp • total_edge≥8pp • tp≥50 • books≥3")
    print(f"          • best_book is executable major (no PP/UD)")
    print(f"  Universe (matching all filters): {len(actionable)}")
    print("=" * 110)

    book_outlier_tally = Counter()
    classification_tally = Counter()
    stat_family_tally = Counter()

    for i, r in enumerate(sorted(actionable,
                                   key=lambda x: -x.get("total_edge", 0)), 1):
        # Gather every book price on this prop
        book_prices = []
        for sfx, display, executable in BOOK_FIELDS:
            o = r.get(f"{sfx}_odds")
            if o is None:
                continue
            p = amer_to_prob(o)
            book_prices.append({
                "code": sfx, "display": display,
                "odds": o, "p": p,
                "executable": executable,
            })
        # Compute consensus implied (raw mean of executable books that have a price)
        execs = [b for b in book_prices if b["executable"]]
        exec_ps = [b["p"] for b in execs if b["p"] is not None]
        consensus_p = (sum(exec_ps) / len(exec_ps)) if exec_ps else None
        best_p = amer_to_prob(r.get("best_book_odds"))
        gap_pp = ((consensus_p or 0) - (best_p or 0)) * 100 if (consensus_p and best_p) else None
        category = classify_executable_edge(execs, consensus_p, best_p)

        bb = (r.get("best_book") or "").lower()
        book_outlier_tally[bb] += 1
        classification_tally[category] += 1
        stat_family_tally[r.get("stat_type") or ""] += 1

        print(f"\n── #{i}  {r['player_name']:<22}  "
               f"{r['stat_type']:<18}  {r.get('line')}  "
               f"OVER  ({r.get('team')} {'vs' if r.get('is_home_team')==1 else '@'} "
               f"{r.get('opponent')})")
        print(f"  Verdict:   {r.get('tier')} — {r.get('tier_reason')}")
        print(f"  HR L5/L10/L20:  {r.get('hit_rate_l5')}/"
               f"{r.get('hit_rate_l10')}/{r.get('hit_rate_l20')}")
        print(f"  Projection: {r.get('model_projection')}  "
               f"(p_model={(r.get('fair_prob') or 0) + (r.get('edge_vs_fair') or 0):.4f})")
        print(f"  fair_prob: {r.get('fair_prob'):.4f}   "
               f"tp: {r.get('tp')}%   "
               f"consensus_edge: {(r.get('edge_vs_fair') or 0)*100:+.2f}pp")
        print(f"  EXECUTABLE EDGE:  best_book={r.get('best_book')} @ "
               f"{fmt_odds(r.get('best_book_odds'))}  "
               f"total_edge={(r.get('total_edge') or 0)*100:+.2f}pp  "
               f"best_book_edge(devig)={(r.get('best_book_edge') or 0)*100:+.2f}pp")
        print(f"  best_book_devig_p={r.get('best_book_devig_probability')}  "
               f"shopping_edge_source={r.get('shopping_edge_source')}")
        # Show all books
        print(f"  Per-book board:")
        print(f"      {'BOOK':<14} {'ODDS':>6} {'IMPLIED %':>10} {'Δ vs consensus':>16}")
        for b in book_prices:
            tag_exec = "" if b["executable"] else "  (non-executable)"
            delta = ((b["p"] - consensus_p) * 100
                     if (b["p"] is not None and consensus_p is not None)
                     else None)
            star = "★ best" if b["code"] == ((r.get("best_book") or "")[:3].lower() or
                                              (r.get("best_book") or "").lower()) else ""
            print(f"      {b['display']:<14}{tag_exec:<18} "
                   f"{fmt_odds(b['odds']):>6} {fmt_p(b['p']):>10} "
                   f"{(f'{delta:+.2f}pp' if delta is not None else '—'):>16}  {star}")
        print(f"  Consensus implied (executable books only):  "
               f"{fmt_p(consensus_p)}%")
        print(f"  Gap (consensus − best raw):  "
               f"{(f'{gap_pp:+.2f}pp' if gap_pp is not None else '—')}")
        print(f"  Classification: {category}")

    # ── Summary section ─────────────────────────────────────────
    print("\n" + "=" * 110)
    print("SUMMARY")
    print("=" * 110)
    print(f"\n1. Actionable count under executable-edge architecture: "
           f"{len(actionable)}")
    print("\n2. Books producing exploitable best lines (count):")
    for bk, n in book_outlier_tally.most_common():
        nice = MAJOR_BOOKS.get(bk, bk)
        print(f"   {nice:<20} ({bk}): {n}")
    print("\n3. Stat-family breakdown:")
    for stat, n in stat_family_tally.most_common():
        print(f"   {stat:<22}: {n}")
    print("\n4. Source classification:")
    for cat, n in classification_tally.most_common():
        print(f"   {cat:<30}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
