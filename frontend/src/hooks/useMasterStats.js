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
import { normalizeFerrariPicks } from '../utils/normalizeFerrariPick';

const API = process.env.REACT_APP_BACKEND_URL;

// 24 hours in milliseconds
const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

/**
 * Fetch player stats from NBA Master Hub
 * This is the ONLY authorized stats fetch function
 */
const fetchMasterStats = async (playerIdentifier) => {
  console.log('[fetchMasterStats] Starting fetch for:', playerIdentifier);
  
  if (!playerIdentifier) {
    console.log('[fetchMasterStats] No identifier, returning null');
    return null;
  }
  
  // For numeric IDs, use master hub player endpoint
  if (typeof playerIdentifier === 'number') {
    console.log('[fetchMasterStats] Fetching by ID:', playerIdentifier);
    const response = await fetch(`${API}/api/v3/master-hub/player/${playerIdentifier}`);
    if (!response.ok) {
      throw new Error(`Master stats fetch failed: ${response.status}`);
    }
    return response.json();
  }
  
  // For string names, try cached player first (has betting lines)
  console.log('[fetchMasterStats] Fetching player-with-badges for:', playerIdentifier);
  const cachedResponse = await fetch(`${API}/api/v3/player-with-badges/${encodeURIComponent(playerIdentifier)}`);
  
  if (cachedResponse.ok) {
    const data = await cachedResponse.json();
    console.log('[fetchMasterStats] Got response:', { success: data.success, hasPlayer: !!data.player, propsCount: data.player?.props?.length });
    if (data.success && data.player) {
      // Normalize every prop so the detail page's grouping (via
      // stat_type_extracted + getCategoryKey(market)) and charts
      // (chart_data) receive the shape they read.
      const player = data.player;
      if (Array.isArray(player.props)) {
        player.props = normalizeFerrariPicks(player.props);
      }
      return player;
    }
  } else {
    console.log('[fetchMasterStats] Response not OK:', cachedResponse.status);
  }
  
  // Fallback: Use command profile endpoint which has full lines data
  const profileResponse = await fetch(`${API}/api/command/profile/${encodeURIComponent(playerIdentifier)}`);
  
  if (profileResponse.ok) {
    const profileData = await profileResponse.json();
    if (profileData.success) {
      // Transform command profile format to match expected player format
      return {
        player_name: profileData.player_name,
        player_id: profileData.player_id,
        team: profileData.team,
        team_name: profileData.team_name,
        position: profileData.position,
        photo_url: profileData.photo_url,
        headshot_url: profileData.headshot_url,
        opponent: profileData.opponent,
        // Transform lines to props format expected by PlayerDetailPage
        props: (profileData.lines || []).map(line => ({
          market: `player_${line.stat_type?.toLowerCase()}`,
          stat_type: line.stat_type,
          stat_type_extracted: line.stat_type,
          line: line.line,
          direction: line.direction || 'over',
          odds: line.odds,
          // 2026-05-07 P0 Phase 4B: canonical reads only. Backend
          // no longer ships the nested `hit_rates` bag on tier picks.
          l5_avg: line.l5_avg,
          l10_avg: line.l10_avg,
          season_avg: line.season_avg,
          // 2026-05-07 P0 Phase 4B: canonical-only hit-rate windows.
          // Legacy `h5_rate`/`h10_rate` and the nested `hit_rates`
          // bag have been retired backend-side and are no longer
          // emitted on tier picks. Frontend consumes
          // `hit_rate_l5/l10/l20` exclusively.
          hit_rate_l5: line.hit_rate_l5,
          hit_rate_l10: line.hit_rate_l10,
          hit_rate_l20: line.hit_rate_l20,
          is_demon: line.is_demon,
          is_goblin: line.is_goblin,
          tier_style: line.tier_style,
          tier_label: line.tier_label,
          demon_line: line.demon_line,
          goblin_line: line.goblin_line,
          dvp_rank: line.dvp_rank,
          dvp_rank_color: line.dvp_rank_color
        })),
        baseline_stats: profileData.baseline_stats,
        demons: profileData.demons || [],
        goblins: profileData.goblins || [],
        // Add context badges and vision insight
        badges: profileData.badges || [],
        vision_insight: profileData.vision_insight
      };
    }
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
    
    // Cache for 5 minutes - allow refetch on player change
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    
    // Refetch when mounting with a different player
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    
    // Don't use placeholder data - let the loading state show
    // placeholderData can cause stale data issues when switching players
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
