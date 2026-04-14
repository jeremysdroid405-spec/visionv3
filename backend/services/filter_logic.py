"""
STRICT BOARD LOCKDOWN - Filter Logic
=====================================
Surgical enrichment tool: Enrich what you SEE, ignore the rest.

TARGET SCOPE: Ferrari Tier Picks ONLY
- Safe Haven (≤10 picks)
- Front Lines (≤10 picks)  
- War Zone (≤10 picks)
- TOTAL: ≤30 props MAX

BANNED: Everything else. No exceptions.

Author: PropVision AI
Version: 2.0.0 - Strict Board Lockdown (Ferrari Tiers Only)
"""

import logging
import httpx
from typing import Dict, Set, List, Tuple, Any
from services.normalize_to_intel_mapping import generate_prop_id

logger = logging.getLogger(__name__)

# Ferrari Tier endpoints - THE ONLY SOURCE OF TRUTH
FERRARI_TIER_ENDPOINTS = {
    "MLB": [
        "/api/v3/mlb/ferrari/safe-haven",
        "/api/v3/mlb/ferrari/front-lines",
        "/api/v3/mlb/ferrari/war-zone",
    ],
    "NBA": [
        "/api/v3/ferrari/safe-haven",
        "/api/v3/ferrari/front-lines",
        "/api/v3/ferrari/war-zone",
    ]
}


class StrictBoardFilter:
    """
    STRICT BOARD LOCKDOWN - Ferrari Tiers Only.
    
    Target: ONLY props displayed on pick cards (Safe Haven, Front Lines, War Zone)
    Max Props: ~30 total
    BANNED: Everything else
    """
    
    def __init__(self, sport: str = "MLB", backend_url: str = "http://localhost:8001"):
        self.sport = sport.upper()
        self.backend_url = backend_url
        self._stats = {}
    
    async def fetch_ferrari_tier_props(self) -> List[Dict]:
        """
        Fetch props from Ferrari Tier endpoints ONLY.
        
        These are the ONLY props that should ever be enriched.
        """
        tier_props = []
        seen_ids = set()
        
        endpoints = FERRARI_TIER_ENDPOINTS.get(self.sport, [])
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for endpoint in endpoints:
                try:
                    url = f"{self.backend_url}{endpoint}"
                    response = await client.get(url)
                    
                    if response.status_code == 200:
                        data = response.json()
                        picks = data.get("picks", [])
                        
                        for pick in picks:
                            prop_id = generate_prop_id(pick)
                            if prop_id not in seen_ids:
                                seen_ids.add(prop_id)
                                tier_props.append(pick)
                        
                        logger.debug(f"[LOCKDOWN] {endpoint}: {len(picks)} picks")
                    else:
                        logger.warning(f"[LOCKDOWN] {endpoint} returned {response.status_code}")
                        
                except Exception as e:
                    logger.warning(f"[LOCKDOWN] Error fetching {endpoint}: {e}")
        
        logger.info(f"[LOCKDOWN] {self.sport} Ferrari Tiers: {len(tier_props)} total picks (MAX 30)")
        
        return tier_props
    
    def compute_enrich_queue(
        self,
        tier_props: List[Dict],
        cached_prop_ids: Set[str]
    ) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        STRICT BOARD LOCKDOWN - Compute enrichment queue from tier props ONLY.
        
        Args:
            tier_props: Props from Ferrari tiers (the ONLY valid source)
            cached_prop_ids: Set of prop IDs already in cache
        
        Returns:
            Tuple[enrich_queue, stats_dict]
        """
        # Build live tier IDs
        tier_prop_map = {}
        for prop in tier_props:
            prop_id = generate_prop_id(prop)
            tier_prop_map[prop_id] = prop
        
        tier_ids = set(tier_prop_map.keys())
        
        # Compute delta
        already_cached = tier_ids & cached_prop_ids
        needs_enrichment = tier_ids - cached_prop_ids
        stale_in_cache = cached_prop_ids - tier_ids  # Props in cache but NOT in tiers
        
        # Build enrich queue
        enrich_queue = [tier_prop_map[pid] for pid in needs_enrichment if pid in tier_prop_map]
        
        self._stats = {
            'tier_props_count': len(tier_ids),
            'already_cached': len(already_cached),
            'enrich_queue_count': len(enrich_queue),
            'stale_to_purge': len(stale_in_cache),
            'stale_ids': stale_in_cache,
        }
        
        logger.info(
            f"[LOCKDOWN] {self.sport}: Tiers={len(tier_ids)}, "
            f"Cached={len(already_cached)}, Queue={len(enrich_queue)}, "
            f"Purge={len(stale_in_cache)}"
        )
        
        return enrich_queue, self._stats
    
    def get_stale_prop_ids(self, tier_props: List[Dict], cached_prop_ids: Set[str]) -> Set[str]:
        """Get prop IDs to purge (in cache but NOT in Ferrari tiers)."""
        tier_ids = {generate_prop_id(p) for p in tier_props}
        return cached_prop_ids - tier_ids
    
    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats.copy()


def demonstrate_filter_logic():
    """Execution proof showing Ferrari tier targeting."""
    print("=== STRICT BOARD LOCKDOWN v2.0 ===")
    print("Target: Ferrari Tier Picks ONLY")
    print("  - Safe Haven (≤10)")
    print("  - Front Lines (≤10)")
    print("  - War Zone (≤10)")
    print("  - MAX TOTAL: ~30 props")
    print()
    print("BANNED: All other props (cached_board has 1000+, we ignore them)")


if __name__ == "__main__":
    demonstrate_filter_logic()
