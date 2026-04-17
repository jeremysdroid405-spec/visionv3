"""Validation: BetMGM consensus scoring integration."""
import asyncio, os, sys, json
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
    
    # --- Section 10: VALIDATION OUTPUT ---
    
    # 1. Sample of 25 props
    both_books = [p for p in scored if p.get('p_dk') is not None and p.get('p_mgm') is not None]
    both_books.sort(key=lambda x: x.get('vision_score_100', 0), reverse=True)
    
    print("=" * 130)
    print("SAMPLE: 25 PROPS WITH BOTH DK AND MGM")
    print("=" * 130)
    hdr = f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK':>7} {'MGM':>7} {'p_dk':>6} {'p_mgm':>6} {'p_cons':>6} {'disag':>6} {'agree':>6} {'edge':>6} {'VS100':>6}"
    print(hdr)
    print("-" * 130)
    for p in both_books[:25]:
        dk_str = f"{p['dk_odds']:+d}" if p.get('dk_odds') else "N/A"
        mgm_str = f"{p['mgm_odds']:+d}" if p.get('mgm_odds') else "N/A"
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {dk_str:>7} {mgm_str:>7} {p.get('p_dk',0):>6.3f} {p.get('p_mgm',0):>6.3f} {p.get('p_market_consensus',0):>6.3f} {p.get('disagreement',0):>6.3f} {p.get('agreement_factor',0):>6.3f} {p.get('edge_pct',0):>6.1f} {p.get('vision_score_100',0):>6.1f}")
    
    # 2a. Top 10 highest disagreement
    disagreements = [p for p in scored if p.get('p_dk') is not None and p.get('p_mgm') is not None]
    disagreements.sort(key=lambda x: x.get('disagreement', 0), reverse=True)
    
    print()
    print("=" * 110)
    print("TOP 10 HIGHEST DISAGREEMENT (DK vs MGM)")
    print("=" * 110)
    print(f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK':>7} {'MGM':>7} {'p_dk':>6} {'p_mgm':>6} {'DISAG':>6} {'FLAGS':>20}")
    print("-" * 110)
    for p in disagreements[:10]:
        dk_str = f"{p['dk_odds']:+d}" if p.get('dk_odds') else "N/A"
        mgm_str = f"{p['mgm_odds']:+d}" if p.get('mgm_odds') else "N/A"
        flags = []
        if p.get('dk_outlier'): flags.append('DK_OUTLIER')
        if p.get('mgm_outlier'): flags.append('MGM_OUTLIER')
        if p.get('market_disagreement_high'): flags.append('HIGH_DISAG')
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {dk_str:>7} {mgm_str:>7} {p.get('p_dk',0):>6.3f} {p.get('p_mgm',0):>6.3f} {p.get('disagreement',0):>6.3f} {','.join(flags):>20}")
    
    # 2b. Top 10 strongest consensus
    consensus = [p for p in scored if p.get('consensus_strong')]
    consensus.sort(key=lambda x: x.get('vision_score_100', 0), reverse=True)
    
    print()
    print("=" * 110)
    print("TOP 10 STRONGEST CONSENSUS (disagreement < 0.03)")
    print("=" * 110)
    print(f"{'PLAYER':22s} {'STAT':18s} {'LN':>4} {'DK':>7} {'MGM':>7} {'p_dk':>6} {'p_mgm':>6} {'DISAG':>6} {'VS100':>6}")
    print("-" * 110)
    for p in consensus[:10]:
        dk_str = f"{p['dk_odds']:+d}" if p.get('dk_odds') else "N/A"
        mgm_str = f"{p['mgm_odds']:+d}" if p.get('mgm_odds') else "N/A"
        print(f"{p['player_name'][:22]:22s} {p['stat_type'][:18]:18s} {p['line']:>4} {dk_str:>7} {mgm_str:>7} {p.get('p_dk',0):>6.3f} {p.get('p_mgm',0):>6.3f} {p.get('disagreement',0):>6.3f} {p.get('vision_score_100',0):>6.1f}")
    
    # 3. Summary stats
    total = len(scored)
    both = sum(1 for p in scored if p.get('p_dk') is not None and p.get('p_mgm') is not None)
    dk_only = sum(1 for p in scored if p.get('p_dk') is not None and p.get('p_mgm') is None)
    mgm_only = sum(1 for p in scored if p.get('p_dk') is None and p.get('p_mgm') is not None)
    neither = sum(1 for p in scored if p.get('p_dk') is None and p.get('p_mgm') is None)
    high_disag = sum(1 for p in scored if p.get('market_disagreement_high'))
    strong_cons = sum(1 for p in scored if p.get('consensus_strong'))
    dk_out = sum(1 for p in scored if p.get('dk_outlier'))
    mgm_out = sum(1 for p in scored if p.get('mgm_outlier'))
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total props:                {total}")
    print(f"Both DK + MGM:              {both} ({both*100//total}%)")
    print(f"DK only (fallback):         {dk_only} ({dk_only*100//total}%)")
    print(f"MGM only (fallback):        {mgm_only} ({mgm_only*100//total}%)")
    print(f"Neither (neutral baseline):  {neither} ({neither*100//total}%)")
    print(f"High disagreement (>0.08):  {high_disag}")
    print(f"Strong consensus (<0.03):   {strong_cons}")
    print(f"DK outlier flags:           {dk_out}")
    print(f"MGM outlier flags:          {mgm_out}")
    
    client.close()

asyncio.run(main())
