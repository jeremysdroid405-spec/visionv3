/**
 * PIPE 1: useMasterStats Hook
 * ===========================
 * SSOT Stats Vault - Queries NBA Master Hub database ONLY
 * 
 * Data Source: nba_master_hub_2026 (populated by 0400 EST Tank01 CRON)
 * 
 * Contains:
 * - baseline_stats: {PTS: {l5_avg, l10_avg, season_avg}, ...}
 * - game_logs: [{gameID, pts, reb, ast, ...}, ...]
 * - Player identity: player_id, display_name, photo_url, team, position
 * 
 * Cache Strategy:
 * - staleTime: 24 hours (data only changes at 0400 EST)
 * - Never refetch in same session unless manually invalidated
 * - Frontend should NEVER ask for this twice
 */

import { useQuery } from '@tanstack/react-query';

const API = process.env.REACT_APP_BACKEND_URL;

// 24 hours in milliseconds
const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

/**
 * Fetch player stats from NBA Master Hub
 * This is the ONLY authorized stats fetch function
 */
const fetchMasterStats = async (playerIdentifier) => {
  if (!playerIdentifier) return null;
  
  // Support both playerId (number) and playerName (string)
  const endpoint = typeof playerIdentifier === 'number' 
    ? `${API}/api/v3/master-hub/player/${playerIdentifier}`
    : `${API}/api/v3/cached-player/${encodeURIComponent(playerIdentifier)}`;
  
  const response = await fetch(endpoint);
  
  if (!response.ok) {
    throw new Error(`Master stats fetch failed: ${response.status}`);
  }
  
  const data = await response.json();
  
  if (!data.success) {
    throw new Error(data.message || 'Player not found in Master Hub');
  }
  
  return data.player || data;
};

/**
 * useMasterStats - PIPE 1 Hook
 * 
 * @param {string|number} playerIdentifier - Player name or ID
 * @returns {object} TanStack Query result with player stats
 * 
 * Usage:
 *   const { data: playerStats, isLoading, error } = useMasterStats('Kevin Durant');
 *   const l5Avg = playerStats?.baseline_stats?.PTS?.l5_avg;
 */
export const useMasterStats = (playerIdentifier) => {
  return useQuery({
    queryKey: ['masterStats', playerIdentifier],
    queryFn: () => fetchMasterStats(playerIdentifier),
    enabled: Boolean(playerIdentifier),
    
    // HEAVY CACHE - Data only changes at 0400 EST CRON
    staleTime: TWENTY_FOUR_HOURS,
    gcTime: TWENTY_FOUR_HOURS,
    
    // Never refetch automatically
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    
    // Keep previous data while fetching new player
    placeholderData: (previousData) => previousData,
  });
};

/**
 * Batch fetch multiple players' stats
 * Useful for pre-loading Command Post or War Zone players
 */
export const useMasterStatsBatch = (playerIdentifiers = []) => {
  return useQuery({
    queryKey: ['masterStatsBatch', playerIdentifiers.sort().join(',')],
    queryFn: async () => {
      const results = await Promise.all(
        playerIdentifiers.map(id => fetchMasterStats(id).catch(() => null))
      );
      
      // Return as a map for easy lookup
      const statsMap = {};
      playerIdentifiers.forEach((id, idx) => {
        if (results[idx]) {
          const key = typeof id === 'string' ? id.toLowerCase() : id;
          statsMap[key] = results[idx];
        }
      });
      
      return statsMap;
    },
    enabled: playerIdentifiers.length > 0,
    staleTime: TWENTY_FOUR_HOURS,
    gcTime: TWENTY_FOUR_HOURS,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
};

export default useMasterStats;
