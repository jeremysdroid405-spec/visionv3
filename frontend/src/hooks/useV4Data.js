/**
 * V4 SSOT Data Hooks
 * ==================
 * Uses the new Single Source of Truth architecture:
 * - BDL = SSOT for all NBA data (hit rates, game logs)
 * - Odds API = SSOT for all props (multi-book aggregation)
 * 
 * Key improvements:
 * - Fresh hit rates calculated from BDL game logs (never cached)
 * - Multi-book sharp edge data
 * - Variance and DNP detection
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const API = process.env.REACT_APP_BACKEND_URL;

// Polling intervals
const TIER_STALE_TIME = 60 * 1000;  // 1 minute
const TIER_REFETCH_INTERVAL = 2 * 60 * 1000;  // 2 minutes

/**
 * Fetch all tiers from V4 API
 */
const fetchAllTiers = async () => {
  const response = await fetch(`${API}/api/v4/tiers/all`);
  if (!response.ok) throw new Error('Tiers fetch failed');
  return response.json();
};

/**
 * Fetch fresh hit rates for a player/stat/line
 */
const fetchHitRates = async (playerName, statType, line) => {
  const response = await fetch(
    `${API}/api/v4/hit-rates/${encodeURIComponent(playerName)}/${statType}/${line}`
  );
  if (!response.ok) throw new Error('Hit rates fetch failed');
  return response.json();
};

/**
 * Fetch complete player data
 */
const fetchPlayerData = async (playerName) => {
  const response = await fetch(
    `${API}/api/v4/player/${encodeURIComponent(playerName)}`
  );
  if (!response.ok) throw new Error('Player data fetch failed');
  return response.json();
};

/**
 * Fetch sync status
 */
const fetchSyncStatus = async () => {
  const response = await fetch(`${API}/api/v4/sync/status`);
  if (!response.ok) throw new Error('Sync status fetch failed');
  return response.json();
};

/**
 * Trigger full sync
 */
const triggerFullSync = async () => {
  const response = await fetch(`${API}/api/v4/sync/full`, { method: 'POST' });
  if (!response.ok) throw new Error('Sync trigger failed');
  return response.json();
};

/**
 * Trigger tier rebuild
 */
const triggerTierRebuild = async () => {
  const response = await fetch(`${API}/api/v4/tiers/rebuild`, { method: 'POST' });
  if (!response.ok) throw new Error('Tier rebuild failed');
  return response.json();
};

// =============================================================================
// HOOKS
// =============================================================================

/**
 * useAllTiers - Get all tier picks with SSOT data
 * 
 * Returns: { safe_haven, front_lines, war_zone }
 * Each pick includes:
 * - Fresh BDL hit rates (l5_rate, l10_rate)
 * - Multi-book sharp data (sharp_line, consensus_line, line_spread)
 * - Edge details (sharp_edge, implied_prob, books_count)
 * - Variance/DNP flags
 */
export const useAllTiers = (options = {}) => {
  return useQuery({
    queryKey: ['v4-tiers-all'],
    queryFn: fetchAllTiers,
    staleTime: TIER_STALE_TIME,
    refetchInterval: options.refetchInterval || TIER_REFETCH_INTERVAL,
    select: (data) => ({
      safeHaven: transformPicks(data.safe_haven?.picks || []),
      frontLines: transformPicks(data.front_lines?.picks || []),
      warZone: transformPicks(data.war_zone?.picks || []),
      counts: {
        safeHaven: data.safe_haven?.count || 0,
        frontLines: data.front_lines?.count || 0,
        warZone: data.war_zone?.count || 0
      },
      fetchedAt: data.fetched_at
    })
  });
};

/**
 * useSafeHaven - V4 Safe Haven picks
 */
export const useSafeHavenV4 = (options = {}) => {
  const { data, ...rest } = useAllTiers(options);
  return {
    data: data ? { picks: data.safeHaven, count: data.counts.safeHaven } : null,
    ...rest
  };
};

/**
 * useFrontLines - V4 Front Lines picks
 */
export const useFrontLinesV4 = (options = {}) => {
  const { data, ...rest } = useAllTiers(options);
  return {
    data: data ? { picks: data.frontLines, count: data.counts.frontLines } : null,
    ...rest
  };
};

/**
 * useWarZone - V4 War Zone picks
 */
export const useWarZoneV4 = (options = {}) => {
  const { data, ...rest } = useAllTiers(options);
  return {
    data: data ? { picks: data.warZone, count: data.counts.warZone } : null,
    ...rest
  };
};

/**
 * useHitRates - Get fresh hit rates for a specific prop
 */
export const useHitRates = (playerName, statType, line, options = {}) => {
  return useQuery({
    queryKey: ['v4-hit-rates', playerName, statType, line],
    queryFn: () => fetchHitRates(playerName, statType, line),
    enabled: !!playerName && !!statType && !!line,
    staleTime: 5 * 60 * 1000, // 5 minutes
    ...options
  });
};

/**
 * usePlayerData - Get complete player data from BDL SSOT
 */
export const usePlayerData = (playerName, options = {}) => {
  return useQuery({
    queryKey: ['v4-player', playerName],
    queryFn: () => fetchPlayerData(playerName),
    enabled: !!playerName,
    staleTime: 5 * 60 * 1000, // 5 minutes
    ...options
  });
};

/**
 * useSyncStatus - Get current sync status
 */
export const useSyncStatus = (options = {}) => {
  return useQuery({
    queryKey: ['v4-sync-status'],
    queryFn: fetchSyncStatus,
    refetchInterval: 30 * 1000, // 30 seconds
    ...options
  });
};

/**
 * useTriggerSync - Mutation to trigger full sync
 */
export const useTriggerSync = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: triggerFullSync,
    onSuccess: () => {
      // Invalidate all V4 queries after sync
      queryClient.invalidateQueries({ queryKey: ['v4-tiers-all'] });
      queryClient.invalidateQueries({ queryKey: ['v4-sync-status'] });
    }
  });
};

/**
 * useTriggerTierRebuild - Mutation to rebuild tiers
 */
export const useTriggerTierRebuild = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: triggerTierRebuild,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['v4-tiers-all'] });
    }
  });
};

// =============================================================================
// HELPERS
// =============================================================================

/**
 * Transform picks to add computed fields and flags
 */
function transformPicks(picks) {
  return picks.map(pick => ({
    ...pick,
    // Computed flags
    isHighVariance: pick.variance_l10 > 25,
    hasDNP: pick.dnp_count_l10 > 0,
    isMultiBook: pick.books_count >= 3,
    hasSharpEdge: pick.sharp_edge > 5,
    
    // Display helpers
    hitRateDisplay: `${pick.l5_rate}% / ${pick.l10_rate}%`,
    varianceDisplay: pick.variance_l10 > 25 ? 'HIGH' : pick.variance_l10 > 15 ? 'MED' : 'LOW',
    edgeDisplay: pick.sharp_edge > 0 ? `+${pick.sharp_edge}%` : `${pick.sharp_edge}%`,
    booksDisplay: `${pick.books_count} book${pick.books_count !== 1 ? 's' : ''}`,
    
    // Legacy compatibility fields
    h5_rate: pick.l5_rate,
    h10_rate: pick.l10_rate,
    player_name: pick.player_name,
    stat_type: pick.stat_type
  }));
}

/**
 * Get variance badge color
 */
export function getVarianceBadgeColor(variance) {
  if (variance > 30) return 'red';
  if (variance > 20) return 'orange';
  if (variance > 10) return 'yellow';
  return 'green';
}

/**
 * Get edge badge color
 */
export function getEdgeBadgeColor(edge) {
  if (edge > 10) return 'green';
  if (edge > 5) return 'lime';
  if (edge > 0) return 'yellow';
  if (edge > -5) return 'orange';
  return 'red';
}

/**
 * Format line spread display
 */
export function formatLineSpread(spread) {
  if (spread === 0) return 'Locked';
  if (spread <= 1) return 'Tight';
  if (spread <= 2) return 'Normal';
  return 'Wide';
}
