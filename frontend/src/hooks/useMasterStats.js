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
  
  // For numeric IDs, use master hub player endpoint
  if (typeof playerIdentifier === 'number') {
    const response = await fetch(`${API}/api/v3/master-hub/player/${playerIdentifier}`);
    if (!response.ok) {
      throw new Error(`Master stats fetch failed: ${response.status}`);
    }
    return response.json();
  }
  
  // For string names, try cached player first (has betting lines)
  const cachedResponse = await fetch(`${API}/api/v3/cached-player/${encodeURIComponent(playerIdentifier)}`);
  
  if (cachedResponse.ok) {
    const data = await cachedResponse.json();
    if (data.success && data.player) {
      return data.player;
    }
  }
  
  // Fallback: Search master hub by name
  const searchResponse = await fetch(`${API}/api/v3/master-hub/search?q=${encodeURIComponent(playerIdentifier)}`);
  
  if (!searchResponse.ok) {
    throw new Error('Player not found in Master Hub');
  }
  
  const searchData = await searchResponse.json();
  
  if (searchData.players && searchData.players.length > 0) {
    return searchData.players[0];
  }
  
  throw new Error('Player not found in Master Hub');
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
