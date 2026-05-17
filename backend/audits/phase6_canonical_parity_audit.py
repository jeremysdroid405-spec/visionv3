"""Phase 6 — Canonical-prop parity audit (read-only).

Builds canonical props from the raw replay snapshot for 2026-05-05
and compares against the existing raw-row replay universe. Reports
every metric in the directive's REQUIRED AUDITS list.

NO writes. NO modifications to live/replay code paths. NO gate or
threshold changes.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
import asyncio, json
from collections import Counter
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.canonical.canonical_prop import build_canonical_props
from services.canonical.market_normalizer import normalize_market
from services.scoring.odds_bucket_router import get_odds_bucket


GAME_DATE = "2026-05-05"
SNAP = "2026-05-05T11:00:00Z"


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    print(f"\n=== Phase 6 — Canonical Prop Engine PARITY AUDIT "
          f"({GAME_DATE} {SNAP}) ===\n")

    # 1. Load raw rows
    print("[1/4] loading raw replay rows ...")
    raw = await db.mlb_historical_alt_odds_raw.find(
        {"sport": "mlb", "game_date": GAME_DATE, "snapshot_iso": SNAP},
        projection={"_id": 0},
    ).to_list(length=None)
    n_raw = len(raw)
    print(f"   raw rows: {n_raw:,}")

    # 2. Canonical-prop unknown-market discovery
    n_unknown_market = 0
    unknown_markets: Counter = Counter()
    for r in raw:
        m = r.get("market")
        fam, _, _ = normalize_market("mlb", m)
        if fam is None:
            n_unknown_market += 1
            unknown_markets[m] += 1
    print(f"   raw rows with UNKNOWN markets: {n_unknown_market} "
          f"({100*n_unknown_market/n_raw:.2f}%)")
    if unknown_markets:
        print(f"   unknown markets sample: {unknown_markets.most_common(8)}")

    # 3. Build canonical props
    print("\n[2/4] building canonical props from raw rows ...")
    props = build_canonical_props(raw, sport="mlb")
    n_canon = len(props)
    print(f"   canonical props: {n_canon:,}")
    print(f"   COLLAPSE RATIO : {n_raw/n_canon:.2f}x  "
          f"(every canonical prop absorbs ~{n_raw/n_canon:.1f} raw rows)")

    # 4. Aggregate counters
    print("\n[3/4] aggregate metrics ...")
    sum_src = sum(p.source_rows_count for p in props)
    n_one_sided = sum(1 for p in props
                       if not p.has_cross_book_devig and not p.has_same_book_devig)
    n_cross = sum(1 for p in props
                   if p.has_cross_book_devig and not p.has_same_book_devig)
    n_same  = sum(1 for p in props if p.has_same_book_devig)
    n_no_books = sum(1 for p in props
                      if p.book_count_either_side_any_book == 0)
    print(f"   source rows captured     : {sum_src:,} of {n_raw:,} "
          f"({100*sum_src/n_raw:.2f}%)")
    print(f"   canonical props with:")
    print(f"     same-book devig        : {n_same:>6} ({100*n_same/n_canon:.2f}%)")
    print(f"     cross-book devig only  : {n_cross:>6} ({100*n_cross/n_canon:.2f}%)")
    print(f"     ANY devig (same+cross) : {n_same+n_cross:>6} "
          f"({100*(n_same+n_cross)/n_canon:.2f}%)")
    print(f"     true one-sided         : {n_one_sided:>6} ({100*n_one_sided/n_canon:.2f}%)")

    # 5. SH-cohort comparison (canonical vs raw-row)
    print(f"\n[4/4] SH-cohort comparison")
    # Raw side: use the legacy book_inventory approach used by Phase 4b.
    # Mirror the existing `load_book_inventory` keyed on (event, player,
    # market, line) — but here directly on raw rows.
    # Routing: a raw ROW routes to SH iff its row.odds <= -300.
    # Canonical: a canonical PROP routes to SH iff its consensus_over_price
    # <= -300 (the canonical reference odds is the consensus implied).
    n_raw_sh = sum(1 for r in raw
                    if r.get("odds") is not None
                    and int(r.get("odds")) <= -300)
    n_canon_sh_over = sum(1 for p in props
                           if p.consensus_over_price is not None
                           and get_odds_bucket(int(p.consensus_over_price)) == "safe_haven")
    n_canon_sh_under = sum(1 for p in props
                            if p.consensus_under_price is not None
                            and get_odds_bucket(int(p.consensus_under_price)) == "safe_haven")
    print(f"   raw rows routing to SH (row-level)    : {n_raw_sh:,}")
    print(f"   canonical props with OVER → SH        : {n_canon_sh_over:,}")
    print(f"   canonical props with UNDER → SH       : {n_canon_sh_under:,}")
    print(f"   ── canonical SH = unique playable SH props, not row count")

    # 6. Devig opportunity comparison
    print("\n──── REQUIRED AUDIT TABLE ────────────────────────────────")
    print("\n   1. RAW SPORTSBOOK ROWS                    : "
          f"{n_raw:,}")
    print(f"   2. CANONICAL PROP COUNT                   : {n_canon:,}")
    print(f"   3. DUPLICATE COLLAPSE RATIO               : {n_raw/n_canon:.2f}x")

    # Same-book-only legacy view: how many props would have devig
    # under the OLD same-book-only rule?
    n_legacy_devig = n_same
    n_new_devig = n_same + n_cross
    print(f"   4. ONE-SIDED REDUCTION                    : ")
    print(f"      same-book-only (legacy)  one_sided    : {n_canon - n_legacy_devig:,}")
    print(f"      cross-book-allowed       one_sided    : {n_one_sided:,}")
    print(f"      → REDUCTION                            : "
          f"{(n_canon - n_legacy_devig) - n_one_sided:,} "
          f"({100*((n_canon-n_legacy_devig)-n_one_sided)/(n_canon-n_legacy_devig):.2f}%)")
    print(f"   5. DEVIG INCREASE                         : ")
    print(f"      same-book-only           devig        : {n_legacy_devig:,}")
    print(f"      cross-book-allowed       devig        : {n_new_devig:,}")
    print(f"      → INCREASE                             : "
          f"+{n_new_devig - n_legacy_devig:,} "
          f"({100*(n_new_devig-n_legacy_devig)/max(n_legacy_devig,1):.2f}%)")
    print(f"   6. TP_SOURCE DISTRIBUTION                 :")
    print(f"      devig (any)                            : {n_new_devig:>6}  "
          f"({100*n_new_devig/n_canon:5.2f}%)")
    print(f"      one_sided                              : {n_one_sided:>6}  "
          f"({100*n_one_sided/n_canon:5.2f}%)")
    print(f"      no_books (degenerate)                  : {n_no_books:>6}  "
          f"({100*n_no_books/n_canon:5.2f}%)")

    # SH/FL/WZ canonical volume (using consensus over price as reference)
    print(f"   7. CANONICAL → TIER ROUTING (consensus_over_price as ref)")
    tier_canon = Counter()
    for p in props:
        if p.consensus_over_price is None: continue
        t = get_odds_bucket(int(p.consensus_over_price))
        tier_canon[t] += 1
    print(f"      safe_haven  : {tier_canon['safe_haven']:>6}")
    print(f"      front_lines : {tier_canon['front_lines']:>6}")
    print(f"      war_zone    : {tier_canon['war_zone']:>6}")
    print(f"      None        : {tier_canon[None]:>6}")
    # Compare against the 6-day Phase 4b totals where SH=8 unique cards
    # over 6 days, FL=118.

    # By stat family
    print(f"\n   8. CANONICAL PROPS BY STAT FAMILY")
    fam_counts = Counter(p.stat_family for p in props)
    for f, n in fam_counts.most_common():
        n_devig = sum(1 for p in props if p.stat_family == f
                       and (p.has_cross_book_devig or p.has_same_book_devig))
        print(f"      {f:<22}  n={n:>5}  devig={n_devig:>4} ({100*n_devig/n:5.2f}%)")

    # By odds bucket
    print(f"\n   9. CANONICAL PROPS BY ODDS BUCKET (OVER side, consensus)")
    buckets = Counter()
    for p in props:
        if p.consensus_over_price is None: continue
        o = int(p.consensus_over_price)
        if o >= 200: b = "plus_high"
        elif o >= 100: b = "plus_med"
        elif o >= -110: b = "minus_low_to_med"
        elif o >= -150: b = "minus_med"
        elif o >= -250: b = "minus_heavy"
        elif o >= -300: b = "minus_xx_(SH boundary)"
        else: b = "deep_SH"
        buckets[b] += 1
    for b in ("plus_high","plus_med","minus_low_to_med","minus_med",
               "minus_heavy","minus_xx_(SH boundary)","deep_SH"):
        if b in buckets:
            print(f"      {b:<26}  {buckets[b]:>5}")

    # Persist
    art = "/app/backend/audits/phase6_canonical_parity_2026_05_05.json"
    out = {
        "game_date": GAME_DATE, "snapshot_iso": SNAP,
        "raw_rows": n_raw,
        "unknown_market_rows": n_unknown_market,
        "canonical_props": n_canon,
        "collapse_ratio": round(n_raw/n_canon, 4),
        "n_same_book_devig": n_same,
        "n_cross_book_devig_only": n_cross,
        "n_one_sided_after_cross_book": n_one_sided,
        "n_no_books": n_no_books,
        "tier_routing_canonical": dict(tier_canon),
        "by_stat_family": dict(fam_counts),
        "odds_bucket_distribution": dict(buckets),
        "sh_routed_rows_legacy": n_raw_sh,
        "sh_routed_canonical_over": n_canon_sh_over,
        "sh_routed_canonical_under": n_canon_sh_under,
        "unknown_markets_sample": dict(unknown_markets.most_common(10)),
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")
    cli.close()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
