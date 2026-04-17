"""Validation: Anomaly handling + score calibration."""
import asyncio, os, sys
sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')

import services.mlb_high_friction_model as hfm
hfm._mlb_hf_instance = None

async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
    db = client['pick_vision']
    
    from services.adapters.mlb_adapter import MLBAdapter
    adapter = MLBAdapter()
    props = await adapter.load_board(db)
    scored = await adapter.enrich_and_score(props, db)
    
    # 1. 20 props: raw score, normalized, anomaly flags, consensus source
    by_vs = sorted(scored, key=lambda x: x.get('vision_score_100', 0), reverse=True)
    
    print("=" * 150)
    print("20 PROPS: RAW SCORE, NORMALIZED, ANOMALY FLAGS, CONSENSUS SOURCE")
    print("=" * 150)
    hdr = f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'RAW':>10} {'VS100':>6} {'SRC':>12} {'FLAGS':>40}"
    print(hdr)
    print("-" * 150)
    for p in by_vs[:20]:
        flags = []
        if p.get('anomaly_line_mismatch_dk'): flags.append('DK_MISMATCH')
        if p.get('anomaly_line_mismatch_mgm'): flags.append('MGM_MISMATCH')
        if p.get('anomaly_true_disagreement'): flags.append('TRUE_DISAG')
        if p.get('anomaly_missing_ref'): flags.append('NO_REF')
        if p.get('consensus_strong'): flags.append('STRONG_CONS')
        flag_str = ','.join(flags) if flags else 'clean'
        
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {p.get('vision_score',0):>10.6f} {p.get('vision_score_100',0):>6.1f} {p.get('consensus_source','?'):>12} {flag_str:>40}")
    
    # 2a. Top 10 line mismatch
    mismatches = [p for p in scored if p.get('anomaly_line_mismatch_dk') or p.get('anomaly_line_mismatch_mgm')]
    mismatches.sort(key=lambda x: x.get('vision_score_100', 0), reverse=True)
    
    print()
    print("=" * 140)
    print(f"TOP 10 LINE MISMATCH CASES ({len(mismatches)} total)")
    print("=" * 140)
    hdr = f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK_MM':>6} {'MGM_MM':>7} {'DK_raw':>7} {'MGM_raw':>7} {'p_dk':>6} {'p_mgm':>6} {'SRC':>12}"
    print(hdr)
    print("-" * 140)
    for p in mismatches[:10]:
        dk_raw = p.get('dk_odds_raw')
        mgm_raw = p.get('mgm_odds_raw')
        dk_str = f"{dk_raw:+d}" if dk_raw else "N/A"
        mgm_str = f"{mgm_raw:+d}" if mgm_raw else "N/A"
        p_dk_str = f"{p.get('p_dk_raw',0):.3f}" if p.get('p_dk_raw') else "N/A"
        p_mgm_str = f"{p.get('p_mgm_raw',0):.3f}" if p.get('p_mgm_raw') else "N/A"
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {'Y' if p.get('anomaly_line_mismatch_dk') else 'N':>6} {'Y' if p.get('anomaly_line_mismatch_mgm') else 'N':>7} {dk_str:>7} {mgm_str:>7} {p_dk_str:>6} {p_mgm_str:>6} {p.get('consensus_source','?'):>12}")

    # 2b. Top 10 true market disagreement (NOT mismatch, just real disagreement)
    true_disag = [p for p in scored if p.get('anomaly_true_disagreement') and not p.get('anomaly_line_mismatch_dk') and not p.get('anomaly_line_mismatch_mgm')]
    true_disag.sort(key=lambda x: x.get('disagreement', 0), reverse=True)
    
    print()
    print("=" * 130)
    print(f"TOP 10 TRUE MARKET DISAGREEMENT (no mismatch, disagreement > 0.08) ({len(true_disag)} total)")
    print("=" * 130)
    hdr = f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK':>7} {'MGM':>7} {'p_dk':>6} {'p_mgm':>6} {'DISAG':>6} {'SRC':>12}"
    print(hdr)
    print("-" * 130)
    for p in true_disag[:10]:
        dk_str = f"{p['dk_odds']:+d}" if p.get('dk_odds') else "N/A"
        mgm_str = f"{p['mgm_odds']:+d}" if p.get('mgm_odds') else "N/A"
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {dk_str:>7} {mgm_str:>7} {p.get('p_dk',0):>6.3f} {p.get('p_mgm',0):>6.3f} {p.get('disagreement',0):>6.3f} {p.get('consensus_source','?'):>12}")

    # 2c. Top 10 strongest consensus
    strong = [p for p in scored if p.get('consensus_strong')]
    strong.sort(key=lambda x: x.get('vision_score_100', 0), reverse=True)
    
    print()
    print("=" * 130)
    print(f"TOP 10 STRONGEST CONSENSUS (disagreement < 0.03) ({len(strong)} total)")
    print("=" * 130)
    hdr = f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK':>7} {'MGM':>7} {'p_dk':>6} {'p_mgm':>6} {'DISAG':>6} {'VS100':>6} {'SRC':>12}"
    print(hdr)
    print("-" * 130)
    for p in strong[:10]:
        dk_str = f"{p['dk_odds']:+d}" if p.get('dk_odds') else "N/A"
        mgm_str = f"{p['mgm_odds']:+d}" if p.get('mgm_odds') else "N/A"
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {dk_str:>7} {mgm_str:>7} {p.get('p_dk',0):>6.3f} {p.get('p_mgm',0):>6.3f} {p.get('disagreement',0):>6.3f} {p.get('vision_score_100',0):>6.1f} {p.get('consensus_source','?'):>12}")

    # 3. Summary
    total = len(scored)
    both = sum(1 for p in scored if p.get('consensus_source') == 'dk+mgm')
    dk_only = sum(1 for p in scored if p.get('consensus_source') == 'dk_only')
    mgm_only = sum(1 for p in scored if p.get('consensus_source') == 'mgm_only')
    baseline = sum(1 for p in scored if p.get('consensus_source') == 'neutral_baseline')
    mm_dk = sum(1 for p in scored if p.get('anomaly_line_mismatch_dk'))
    mm_mgm = sum(1 for p in scored if p.get('anomaly_line_mismatch_mgm'))
    td = sum(1 for p in scored if p.get('anomaly_true_disagreement'))
    mr = sum(1 for p in scored if p.get('anomaly_missing_ref'))
    sc = sum(1 for p in scored if p.get('consensus_strong'))
    
    # Percentile distribution
    vs_values = sorted([p.get('vision_score_100', 0) for p in scored])
    p25 = vs_values[len(vs_values)//4] if vs_values else 0
    p50 = vs_values[len(vs_values)//2] if vs_values else 0
    p75 = vs_values[3*len(vs_values)//4] if vs_values else 0
    p95 = vs_values[int(len(vs_values)*0.95)] if vs_values else 0
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total props:                     {total}")
    print(f"Consensus dk+mgm:                {both} ({both*100//total}%)")
    print(f"DK-only fallback:                {dk_only} ({dk_only*100//total}%)")
    print(f"MGM-only fallback:               {mgm_only} ({mgm_only*100//total}%)")
    print(f"Neutral baseline (no ref):       {baseline} ({baseline*100//total}%)")
    print(f"")
    print(f"DK line mismatch (excluded):     {mm_dk}")
    print(f"MGM line mismatch (excluded):    {mm_mgm}")
    print(f"True market disagreement:        {td}")
    print(f"Missing all references:          {mr}")
    print(f"Strong consensus:                {sc}")
    print(f"")
    print(f"Vision Score 100 distribution:")
    print(f"  P25={p25}  P50={p50}  P75={p75}  P95={p95}")
    
    client.close()

asyncio.run(main())
