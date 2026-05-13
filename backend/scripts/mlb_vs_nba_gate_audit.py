"""MLB vs NBA gates + coverage / vision_score parity audit.

OOM-safe: every Mongo iteration uses an aggregation pipeline so we
never load full collections into Python. No `to_list(length=None)`,
no per-doc Python aggregation loops.

Outputs a single JSON report to /app/audit_reports/mlb_vs_nba_gate_audit.json
and prints a Markdown summary.
"""
from __future__ import annotations
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient


def _md_section(title: str) -> str:
    return f"\n## {title}\n"


async def _bucket_stats(coll, version_tag: str):
    """Return supply / vision / coverage stats for one sport using
    aggregation only (no client-side iteration)."""
    base_match = {"version_tag": version_tag, "active": True}
    out = {}

    # Total active props
    out["total_active"] = await coll.count_documents(base_match)

    # Vision score presence
    out["vision_score_present"] = await coll.count_documents(
        {**base_match, "vision_score": {"$ne": None, "$gt": 0}}
    )
    out["vision_score_null"] = await coll.count_documents(
        {**base_match, "vision_score": None}
    )
    out["vision_score_zero"] = await coll.count_documents(
        {**base_match, "vision_score": 0}
    )

    # Book count distribution
    book_pipe = [
        {"$match": base_match},
        {"$group": {"_id": "$book_count", "n": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    out["book_count_dist"] = {
        (str(d["_id"]) if d["_id"] is not None else "null"): d["n"]
        async for d in coll.aggregate(book_pipe)
    }

    # >=2 books and DK-only counts
    out["multi_book_props"] = await coll.count_documents(
        {**base_match, "book_count": {"$gte": 2}}
    )
    out["single_book_props"] = await coll.count_documents(
        {**base_match, "book_count": {"$eq": 1}}
    )

    # Tier distribution
    tier_pipe = [
        {"$match": base_match},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]
    out["tier_dist"] = {
        (d["_id"] or "unknown"): d["n"]
        async for d in coll.aggregate(tier_pipe)
    }

    # Multi-book AND vision score present (the "production-quality" pool)
    out["mb_and_vision"] = await coll.count_documents(
        {**base_match, "book_count": {"$gte": 2}, "vision_score": {"$gt": 0}}
    )

    # Books that anchored (which sportsbook contributed)
    anchor_pipe = [
        {"$match": base_match},
        {"$unwind": {"path": "$books_anchored", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$books_anchored", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    out["books_anchored_dist"] = {
        (d["_id"] or "none"): d["n"]
        async for d in coll.aggregate(anchor_pipe)
    }

    # Reasons docs are unqualified
    reason_pipe = [
        {"$match": {**base_match, "tier": "unqualified"}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 15},
    ]
    out["top_unqualified_reasons"] = [
        {"reason": d["_id"], "n": d["n"]}
        async for d in coll.aggregate(reason_pipe)
    ]

    return out


async def main():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    if not mongo_url:
        print("ERROR: MONGO_URL not set", file=sys.stderr)
        sys.exit(1)

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    nba = await _bucket_stats(db["nba_prop_scores"], "final-nba-rt")
    mlb = await _bucket_stats(db["mlb_prop_scores"], "final-mlb-rt")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nba": nba,
        "mlb": mlb,
    }

    out_path = Path("/app/audit_reports/mlb_vs_nba_gate_audit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    # Print summary Markdown
    print("# MLB vs NBA Coverage / Vision Score Audit\n")
    print(f"Generated at: {payload['generated_at']}\n")
    print("| Metric | NBA (final-nba-rt) | MLB (final-mlb-rt) |")
    print("| --- | ---: | ---: |")
    print(f"| Total active props | {nba['total_active']} | {mlb['total_active']} |")
    print(f"| Vision score present (>0) | {nba['vision_score_present']} | {mlb['vision_score_present']} |")
    print(f"| Vision score null | {nba['vision_score_null']} | {mlb['vision_score_null']} |")
    print(f"| Vision score == 0 | {nba['vision_score_zero']} | {mlb['vision_score_zero']} |")
    print(f"| Single-book props | {nba['single_book_props']} | {mlb['single_book_props']} |")
    print(f"| Multi-book (>=2) props | {nba['multi_book_props']} | {mlb['multi_book_props']} |")
    print(f"| Multi-book AND vision>0 | {nba['mb_and_vision']} | {mlb['mb_and_vision']} |")

    def pct(num, den):
        return f"{(100.0 * num / den):.1f}%" if den else "n/a"

    print()
    print("**Multi-book share**: "
          f"NBA {pct(nba['multi_book_props'], nba['total_active'])}, "
          f"MLB {pct(mlb['multi_book_props'], mlb['total_active'])}")
    print("**Single-book share**: "
          f"NBA {pct(nba['single_book_props'], nba['total_active'])}, "
          f"MLB {pct(mlb['single_book_props'], mlb['total_active'])}")
    print("**Vision-score coverage (>0)**: "
          f"NBA {pct(nba['vision_score_present'], nba['total_active'])}, "
          f"MLB {pct(mlb['vision_score_present'], mlb['total_active'])}")
    print("**Multi-book + Vision**: "
          f"NBA {pct(nba['mb_and_vision'], nba['total_active'])}, "
          f"MLB {pct(mlb['mb_and_vision'], mlb['total_active'])}")

    print(_md_section("Book Count Distribution"))
    print("| Books | NBA | MLB |")
    print("| --- | ---: | ---: |")
    keys = sorted(set(nba["book_count_dist"].keys()) | set(mlb["book_count_dist"].keys()),
                  key=lambda x: (x == "null", x))
    for k in keys:
        print(f"| {k} | {nba['book_count_dist'].get(k, 0)} | {mlb['book_count_dist'].get(k, 0)} |")

    print(_md_section("Tier Distribution"))
    print("| Tier | NBA | MLB |")
    print("| --- | ---: | ---: |")
    tier_keys = sorted(set(nba["tier_dist"].keys()) | set(mlb["tier_dist"].keys()))
    for k in tier_keys:
        print(f"| {k} | {nba['tier_dist'].get(k, 0)} | {mlb['tier_dist'].get(k, 0)} |")

    print(_md_section("Books Anchored (which book contributed the anchor)"))
    print("| Book | NBA | MLB |")
    print("| --- | ---: | ---: |")
    book_keys = sorted(set(nba["books_anchored_dist"].keys()) | set(mlb["books_anchored_dist"].keys()))
    for k in book_keys:
        print(f"| {k} | {nba['books_anchored_dist'].get(k, 0)} | {mlb['books_anchored_dist'].get(k, 0)} |")

    print(_md_section("Top Unqualified Reasons (MLB)"))
    for r in mlb["top_unqualified_reasons"]:
        print(f"- `{r['reason']}` — {r['n']}")
    print(_md_section("Top Unqualified Reasons (NBA)"))
    for r in nba["top_unqualified_reasons"]:
        print(f"- `{r['reason']}` — {r['n']}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
