"""Diagnostic: locate duplicate canonical MLB prop rows.

Definition of duplicate per spec:
  sport + event_id + player + stat_family + line + side

For each duplicate cluster we capture:
  - canonical_key values
  - tier_reference_book values
  - is_alternate_market flags
  - market_key values
  - tp_source / book counts
  - whether they all have identical model_projection (proves same player×stat
    × line × side rescored twice vs split market_key partitioning)
"""
import os
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
from pymongo import MongoClient

mc = MongoClient(os.environ['MONGO_URL'])
m = mc[os.environ['DB_NAME']]
coll = m['mlb_prop_scores']

# Pull active rows only, project just identifying fields + ref-meta.
cur = coll.find(
    {'active': True},
    {
        '_id': 0,
        'canonical_key': 1,
        'event_id': 1,
        'player_name': 1,
        'stat_type': 1,
        'line': 1,
        'recommendation': 1,
        'tier': 1,
        'tier_reference_book': 1,
        'tier_reference_odds': 1,
        'is_alternate_market': 1,
        'market_key': 1,
        'tp': 1,
        'tp_source': 1,
        'tp_method': 1,
        'tp_books_used': 1,
        'tp_books_list': 1,
        'book_count': 1,
        'model_projection': 1,
        'p_true_active': 1,
        'edge_vs_fair': 1,
    },
)

groups = defaultdict(list)
total = 0
for d in cur:
    total += 1
    key = (
        d.get('event_id'),
        d.get('player_name'),
        d.get('stat_type'),
        d.get('line'),
        (d.get('recommendation') or '').upper(),
    )
    groups[key].append(d)

dups = {k: v for k, v in groups.items() if len(v) > 1}
print(f'Total active MLB scored rows: {total:,}')
print(f'Distinct (event,player,stat,line,side) keys: {len(groups):,}')
print(f'Duplicate clusters (>1 row): {len(dups):,}')
print(
    f'Total redundant rows (sum(cluster) - cluster_count): '
    f'{sum(len(v)-1 for v in dups.values()):,}'
)
print()

# Sort clusters by cluster size desc, then show top 12 verbose dumps.
top = sorted(dups.items(), key=lambda kv: -len(kv[1]))[:12]
print(f'=== TOP {len(top)} DUPLICATE CLUSTERS (verbose) ===\n')
for i, (k, rows) in enumerate(top, 1):
    eid, pl, st, ln, sd = k
    print(f'#{i}  [{len(rows)} rows]  {pl} | {st} {sd} {ln}  event={eid}')
    for r in rows:
        ck = r.get('canonical_key', '?')
        # canonical_key gets truncated for readability
        ck_short = ck if len(str(ck)) < 80 else f'{str(ck)[:77]}…'
        print(
            f'    ck={ck_short}'
        )
        print(
            f'      tier={r.get("tier")!r:<14} ref_book={r.get("tier_reference_book")!r:<13} '
            f'ref_odds={r.get("tier_reference_odds")!r:<6} alt={r.get("is_alternate_market")!r} '
            f'market_key={r.get("market_key")!r}'
        )
        print(
            f'      tp={r.get("tp")} tp_source={r.get("tp_source")} tp_method={r.get("tp_method")} '
            f'books_used={r.get("tp_books_used")}  book_count={r.get("book_count")}'
        )
        print(
            f'      proj={r.get("model_projection")}  p_model={r.get("p_true_active")}  '
            f'edge_vs_fair={r.get("edge_vs_fair")}'
        )
    print()

# Same-proj proof: are duplicates actually re-scoring the SAME underlying
# prop, or are they materially different (different model_projection /
# p_model values)? If they're identical underlying math → pure dedupe.
identical_proj = 0
divergent_proj = 0
for rows in dups.values():
    projs = {r.get('model_projection') for r in rows}
    if len(projs) == 1:
        identical_proj += 1
    else:
        divergent_proj += 1
print(
    f'Clusters with IDENTICAL projection across duplicates: {identical_proj:,}  '
    f'(safe to dedupe — same underlying prop)'
)
print(
    f'Clusters with DIVERGENT projection across duplicates: {divergent_proj:,}  '
    f'(different math — would need investigation before merging)'
)
print()

# What ref_book combinations appear in duplicate clusters?
ref_book_combos = defaultdict(int)
market_key_combos = defaultdict(int)
alt_flag_combos = defaultdict(int)
for rows in dups.values():
    books = tuple(sorted({r.get('tier_reference_book') for r in rows}))
    mkts = tuple(sorted({r.get('market_key') for r in rows}, key=lambda x: x or ''))
    alts = tuple(sorted({r.get('is_alternate_market') for r in rows}, key=lambda x: str(x)))
    ref_book_combos[books] += 1
    market_key_combos[mkts] += 1
    alt_flag_combos[alts] += 1

print('Top ref_book combos seen across duplicate clusters:')
for combo, n in sorted(ref_book_combos.items(), key=lambda kv: -kv[1])[:10]:
    print(f'  {n:>4}× {combo}')
print()
print('Top market_key combos:')
for combo, n in sorted(market_key_combos.items(), key=lambda kv: -kv[1])[:10]:
    print(f'  {n:>4}× {combo}')
print()
print('Top alternate-flag combos:')
for combo, n in sorted(alt_flag_combos.items(), key=lambda kv: -kv[1])[:10]:
    print(f'  {n:>4}× {combo}')
