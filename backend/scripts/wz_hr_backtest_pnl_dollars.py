#!/usr/bin/env python3
"""
WZ HR-floor backtest — $10/pick P&L breakdown.

Reads `replay_outcomes` for the existing A/B run IDs and produces:
  * Per-tier (Safe Haven / Front Lines / War Zone) P&L
  * Per-stat-family P&L
  * Per-stat × per-tier matrix

De-duplicates so each unique (event_id, canonical_key) counts ONCE
per variant — `replay_outcomes` carries one row per book × snapshot,
which would otherwise inflate the pick count by ~3-5×.

Stake: $10 per pick. PnL units in `replay_outcomes.pnl_units` are
American-odds payout-per-1-unit-stake (built by
`services/replay/resolver.build_outcome_row`); we multiply by 10.

Usage:
    python wz_hr_backtest_pnl_dollars.py [--stake 10] [--prefix wz_hr_ab_1778457677]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / 'backend'))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / 'backend/.env'))

from pymongo import MongoClient  # noqa: E402


# Map raw stat_type strings to display family. Mirrors the engine's
# `stat_family.resolve_stat_family` minus the kwargs.
def _stat_family(raw):
    if not raw:
        return 'unknown'
    s = raw.strip().lower().replace(' ', '_')
    # NBA
    if 'points_rebounds_assists' in s: return 'PRA'
    if 'points_rebounds' in s:         return 'P+R'
    if 'points_assists' in s:          return 'P+A'
    if 'rebounds_assists' in s:        return 'R+A'
    if 'threes' in s or s in ('3pm',): return '3PM'
    if 'steals' in s or s == 'stl':    return 'STL'
    if 'blocks' in s or s == 'blk':    return 'BLK'
    if 'turnovers' in s or s == 'to':  return 'TO'
    if 'points' in s or s == 'pts':    return 'PTS'
    if 'rebounds' in s or s == 'reb':  return 'REB'
    if 'assists' in s or s == 'ast':   return 'AST'
    return raw.upper()[:8]


def _fmt_money(n):
    sign = '-' if n < 0 else '+'
    return f"{sign}${abs(n):>9,.2f}"


def _fmt_pct(n):
    if n is None:
        return '—'
    return f"{n:+5.2f}%"


def run(prefix: str, stake: float):
    cli = MongoClient(os.environ['MONGO_URL'])
    db = cli[os.environ['DB_NAME']]

    runs = [
        ('HR ≥ 50 (baseline)', f'{prefix}_hr50'),
        ('HR ≥ 35 (test)',     f'{prefix}_hr35'),
    ]

    summaries = {}
    for label, run_id in runs:
        print(f"\n{'=' * 86}")
        print(f"  {label}  run_id={run_id}  @ ${stake:.2f}/pick")
        print('=' * 86)

        # De-dup: one row per (event_id, canonical_key) by keeping
        # the first observed outcome. Aggregation pipeline is faster
        # than client-side dedup on 138k rows.
        pipeline = [
            {'$match': {
                'replay_run_id': run_id,
                'outcome': {'$in': ['hit', 'miss']},
                'tier_at_eval': {'$in': ['safe_haven', 'front_lines', 'war_zone']},
            }},
            {'$group': {
                '_id': {'event_id': '$event_id', 'canonical_key': '$canonical_key'},
                'tier':         {'$first': '$tier_at_eval'},
                'stat_family':  {'$first': '$stat_family'},
                'canonical_key':{'$first': '$canonical_key'},
                'outcome':      {'$first': '$outcome'},
                'pnl_unit':     {'$first': '$pnl_units'},
            }},
        ]

        # Aggregators
        per_tier      = defaultdict(lambda: {'n': 0, 'hits': 0, 'pnl': 0.0})
        per_stat      = defaultdict(lambda: {'n': 0, 'hits': 0, 'pnl': 0.0})
        per_combo     = defaultdict(lambda: {'n': 0, 'hits': 0, 'pnl': 0.0})
        grand         = {'n': 0, 'hits': 0, 'pnl': 0.0}

        for d in db.replay_outcomes.aggregate(pipeline, allowDiskUse=True):
            tier  = d['tier']
            # Prefer stamped `stat_family`; fall back to deriving from
            # canonical_key (`nba|<market>|<player>|<line>`) for legacy
            # rows that never carried the stamp.
            fam = d.get('stat_family')
            if not fam:
                ck = d.get('canonical_key') or ''
                market = ck.split('|', 2)[1] if '|' in ck else ''
                fam = _stat_family(market)
            stat  = fam.upper() if isinstance(fam, str) else 'UNKNOWN'
            outc  = d['outcome']
            unit  = d.get('pnl_unit') or 0.0
            dollars = float(unit) * stake
            hit = 1 if outc == 'hit' else 0
            for bucket in (per_tier[tier], per_stat[stat],
                           per_combo[(tier, stat)], grand):
                bucket['n'] += 1
                bucket['hits'] += hit
                bucket['pnl'] += dollars

        # ===== TIER TABLE =====
        print(f"\n  P&L BY TIER")
        print(f"  {'Tier':<14} {'Picks':>7} {'Hits':>6} {'Hit%':>7} {'Risked':>11} {'Profit':>13} {'ROI/u':>9}")
        print(f"  {'-' * 80}")
        tier_order = ['safe_haven', 'front_lines', 'war_zone']
        for tier in tier_order:
            b = per_tier.get(tier)
            if not b or b['n'] == 0:
                print(f"  {tier:<14} {'—':>7} {'—':>6} {'—':>7} {'—':>11} {'—':>13} {'—':>9}")
                continue
            risked = b['n'] * stake
            hr = 100.0 * b['hits'] / b['n']
            roi = 100.0 * b['pnl'] / risked
            print(f"  {tier:<14} {b['n']:>7,} {b['hits']:>6,} {hr:>6.2f}% "
                  f" ${risked:>9,.2f} {_fmt_money(b['pnl']):>13} {_fmt_pct(roi):>9}")
        # Grand total
        risked = grand['n'] * stake
        hr  = 100.0 * grand['hits'] / max(grand['n'], 1)
        roi = 100.0 * grand['pnl']  / max(risked, 1)
        print(f"  {'-' * 80}")
        print(f"  {'TOTAL':<14} {grand['n']:>7,} {grand['hits']:>6,} {hr:>6.2f}% "
              f" ${risked:>9,.2f} {_fmt_money(grand['pnl']):>13} {_fmt_pct(roi):>9}")

        # ===== STAT TABLE =====
        print(f"\n  P&L BY STAT FAMILY")
        print(f"  {'Stat':<10} {'Picks':>7} {'Hits':>6} {'Hit%':>7} {'Risked':>11} {'Profit':>13} {'ROI/u':>9}")
        print(f"  {'-' * 76}")
        # Sort by total profit descending
        for stat, b in sorted(per_stat.items(), key=lambda x: -x[1]['pnl']):
            risked = b['n'] * stake
            hr = 100.0 * b['hits'] / b['n']
            roi = 100.0 * b['pnl'] / risked
            print(f"  {stat:<10} {b['n']:>7,} {b['hits']:>6,} {hr:>6.2f}% "
                  f" ${risked:>9,.2f} {_fmt_money(b['pnl']):>13} {_fmt_pct(roi):>9}")

        # ===== STAT × TIER MATRIX =====
        print(f"\n  P&L MATRIX  (stat × tier — $ profit only, [picks])")
        stats_seen = sorted(per_stat.keys(), key=lambda s: -per_stat[s]['pnl'])
        header = f"  {'Stat':<8} | " + ' | '.join(f"{t:>20}" for t in tier_order) + " | " + f"{'TOTAL':>20}"
        print(header)
        print(f"  {'-' * (len(header) - 2)}")
        for stat in stats_seen:
            row_total = per_stat[stat]
            cells = []
            for tier in tier_order:
                b = per_combo.get((tier, stat))
                if not b or b['n'] == 0:
                    cells.append(f"{'—':>20}")
                else:
                    cells.append(f"{_fmt_money(b['pnl'])} [{b['n']:>3}]")
            cells.append(f"{_fmt_money(row_total['pnl'])} [{row_total['n']:>3}]")
            print(f"  {stat:<8} | " + ' | '.join(cells))

        summaries[run_id] = {
            'grand': grand,
            'per_tier': dict(per_tier),
            'per_stat': dict(per_stat),
        }

    # ===== A/B DELTA =====
    print(f"\n{'=' * 86}")
    print(f"  A/B DELTA — HR ≥ 35 minus HR ≥ 50  @ ${stake:.2f}/pick")
    print('=' * 86)
    a = summaries[f'{prefix}_hr50']
    b = summaries[f'{prefix}_hr35']
    print(f"\n  {'Bucket':<26} {'Δ Picks':>9} {'Δ Hits':>8} {'Δ Risked':>13} {'Δ Profit':>14}")
    print(f"  {'-' * 78}")
    # Grand
    delta_n  = b['grand']['n']   - a['grand']['n']
    delta_h  = b['grand']['hits'] - a['grand']['hits']
    delta_r  = (b['grand']['n']   - a['grand']['n']) * stake
    delta_p  = b['grand']['pnl']  - a['grand']['pnl']
    print(f"  {'TOTAL':<26} {delta_n:>+9,} {delta_h:>+8,} ${delta_r:>+11,.2f} {_fmt_money(delta_p):>14}")
    for tier in ['safe_haven', 'front_lines', 'war_zone']:
        ba = a['per_tier'].get(tier, {'n': 0, 'hits': 0, 'pnl': 0.0})
        bb = b['per_tier'].get(tier, {'n': 0, 'hits': 0, 'pnl': 0.0})
        print(f"  {tier:<26} "
              f"{bb['n'] - ba['n']:>+9,} {bb['hits'] - ba['hits']:>+8,} "
              f"${(bb['n'] - ba['n']) * stake:>+11,.2f} "
              f"{_fmt_money(bb['pnl'] - ba['pnl']):>14}")

    cli.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--prefix', default='wz_hr_ab_1778457677')
    p.add_argument('--stake', type=float, default=10.0)
    args = p.parse_args()
    run(args.prefix, args.stake)
