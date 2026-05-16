"""MLB War Zone rewrite — validation report (2026-05-16)."""
import os
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
db = MongoClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]

since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

# Latest version_tag
recent_tag = db.mlb_prop_scores.find_one(
    {"computed_at": {"$gte": since}}, sort=[("computed_at",-1)]
)
print(f"Newest version_tag in window: {recent_tag.get('version_tag')}  computed_at={recent_tag['computed_at']}")
print()

# Tier distribution (latest per canonical_key)
pipeline = [
    {"$match": {"computed_at": {"$gte": since}}},
    {"$sort": {"computed_at": -1}},
    {"$group": {"_id": "$canonical_key_v2", "doc": {"$first": "$$ROOT"}}},
    {"$replaceRoot": {"newRoot": "$doc"}},
    {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    {"$sort": {"n": -1}},
]
print("=== Tier distribution (latest doc per prop) ===")
for r in db.mlb_prop_scores.aggregate(pipeline, allowDiskUse=True):
    print(f"  {r['_id']:20s} {r['n']}")
print()

# Gate rejection counts for routed_tier=war_zone
pipeline = [
    {"$match": {"computed_at": {"$gte": since}, "routed_tier": "war_zone"}},
    {"$sort": {"computed_at": -1}},
    {"$group": {"_id": "$canonical_key_v2", "doc": {"$first": "$$ROOT"}}},
    {"$replaceRoot": {"newRoot": "$doc"}},
]
routed_wz = list(db.mlb_prop_scores.aggregate(pipeline, allowDiskUse=True))
print(f"=== Routed-to-WZ pool (latest) = {len(routed_wz)} props ===")

passed = [d for d in routed_wz if d.get("tier") == "war_zone"]
print(f"  PASSED all 5 gates → tier=war_zone:   {len(passed)}")
print(f"  FAILED gates → fell back:             {len(routed_wz) - len(passed)}")
print()

# Tally failed_gates names
from collections import Counter
fail_counter = Counter()
gate_seen_counter = Counter()
for d in routed_wz:
    gates = d.get("tier_gate_results") or d.get("gate_results") or {}
    if not isinstance(gates, dict):
        continue
    for gname, gv in gates.items():
        if not isinstance(gv, dict): continue
        gate_seen_counter[gname] += 1
        if gv.get("passed") is False:
            fail_counter[gname] += 1

print("=== Gate fail counts (routed_tier=war_zone, latest pool) ===")
print(f"  {'gate':25s} {'evaluated':>10s} {'failed':>8s} {'fail %':>7s}")
for gname, n_seen in gate_seen_counter.most_common():
    n_fail = fail_counter.get(gname, 0)
    pct = (100*n_fail/n_seen) if n_seen else 0
    print(f"  {gname:25s} {n_seen:>10d} {n_fail:>8d} {pct:>6.1f}%")
print()

# Verify no legacy gates present
print("=== Forbidden-gate detection (should be 0) ===")
FORBIDDEN = ["tp_gate","ceiling_gate","tp_source_gate","margin_gate",
             "vision_score_gate","market_structure_gate"]
for fg in FORBIDDEN:
    n = sum(1 for d in routed_wz
            if isinstance(d.get("tier_gate_results") or d.get("gate_results"), dict)
            and fg in (d.get("tier_gate_results") or d.get("gate_results") or {}))
    flag = "OK" if n == 0 else "❌"
    print(f"  [{flag}] {fg:25s} present in {n} WZ docs")

# Sample one passing WZ doc and dump gate trace
print("\n=== Sample passing WZ doc — gate trace (proof of active gates) ===")
if passed:
    s = passed[0]
    print(f"  {s.get('player_name')} {s.get('stat_type')} {s.get('side')} {s.get('line')}")
    for g, v in (s.get("tier_gate_results") or s.get("gate_results") or {}).items():
        print(f"    {g:25s} passed={v.get('passed')} threshold={v.get('threshold')} actual={v.get('actual')}")
