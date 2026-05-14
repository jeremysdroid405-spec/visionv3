"""Ad-hoc report: top 20 MLB Front Lines REJECTS (active, routed to FL,
sorted by total_edge desc). Diagnostic only."""
import os
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
from pymongo import MongoClient

mc = MongoClient(os.environ['MONGO_URL'])
m = mc[os.environ['DB_NAME']]
coll = m['mlb_prop_scores']

query = {
    'active': True,
    'tier': 'unqualified',
    'tier_reference_odds': {'$gte': -299, '$lte': 149, '$ne': None},
}
n_active_fl_rejects = coll.count_documents(query)
print(f'MLB FL rejects (active, ref_odds in [-299, +149]): {n_active_fl_rejects:,}')
print(f'MLB FL passing (active, tier=front_lines): '
      f'{coll.count_documents({"active": True, "tier": "front_lines"}):,}')

import sys
sort_mode = sys.argv[1] if len(sys.argv) > 1 else 'total_edge'
sort_key = {
    'total_edge': [('total_edge', -1)],
    'hr':         [('hit_rate_l20', -1), ('hit_rate_l10', -1), ('hit_rate_l5', -1)],
    'model':      [('edge_vs_fair', -1)],
}[sort_mode]
rows = list(coll.find(query, {'_id': 0}).sort(sort_key).limit(20))

print()
print(f'=== TOP 20 MLB FRONT-LINES REJECTS (sorted by {sort_mode} desc) ===\n')
hdr = (
    f"{'#':>2}  {'PLAYER':<22} {'STAT':<16} {'SIDE':<5} {'LINE':>5}  "
    f"{'REF_BOOK':<11} {'REF_ODDS':>9}  {'TP':>5}  {'CV':>5}  "
    f"{'HR20':>5}  {'HR10':>5}  {'HR5':>4}  {'MODEL':>7}  {'TOTAL':>7}  "
    f"{'REASON':<32}  GATES_FAILED"
)
print(hdr)
print('-' * len(hdr))

def fmt(v, spec='.2f'):
    if v is None:
        return '—'
    try:
        return f'{v:{spec}}'
    except Exception:
        return str(v)

for i, p in enumerate(rows, 1):
    name = (p.get('player_name') or '')[:22]
    stat = (p.get('stat_type') or '')[:16]
    side = (p.get('recommendation') or '')[:5]
    line = p.get('line')
    ref_b = (p.get('tier_reference_book') or '')[:11]
    ref_o = p.get('tier_reference_odds')
    tp = p.get('tp')
    cv = p.get('cv')
    hr20 = p.get('hit_rate_l20')
    hr10 = p.get('hit_rate_l10')
    hr5 = p.get('hit_rate_l5')
    mdl = p.get('edge_vs_fair')
    tot = p.get('total_edge')
    reason = (p.get('tier_reason') or '')[:32]

    gates = p.get('tier_gate_results') or {}
    failed = []
    if isinstance(gates, dict):
        for gname, gval in gates.items():
            if isinstance(gval, dict):
                if gval.get('passed') is False:
                    failed.append(gname)
            elif gval in (False, 'fail', 'failed'):
                failed.append(gname)
    gates_short = ','.join(failed[:5]) or '—'

    print(
        f"{i:>2}  {name:<22} {stat:<16} {side:<5} {fmt(line, '.1f'):>5}  "
        f"{ref_b:<11} {fmt(ref_o, '.0f'):>9}  "
        f"{fmt(tp, '.1f'):>5}  {fmt(cv, '.2f'):>5}  "
        f"{fmt(hr20, '.0f'):>5}  {fmt(hr10, '.0f'):>5}  "
        f"{fmt(hr5, '.0f'):>4}  "
        f"{fmt(mdl * 100 if mdl is not None else None, '+.1f'):>7}  "
        f"{fmt(tot * 100 if tot is not None else None, '+.1f'):>7}  "
        f"{reason:<32}  {gates_short}"
    )
