/**
 * PIPE 2: useLiveOdds Hook
 * ========================
 * Live Wire - Queries Active Lines (Odds API destination) ONLY
 * 
 * Data Source: dg_cached_board (populated by Adaptive Sync Engine polling Odds API)
 * 
 * Contains:
 * - Live betting lines (Over 9.5, Under 22.5, etc.)
 * - Current odds (-110, +105, etc.)
 * - Game times, opponents
 * - NO historical stats (those come from PIPE 1)
 * 
 * Cache Strategy:
 * - staleTime: 15 seconds (odds change frequently)
 * - refetchInterval: 30 seconds (Open Door background polling)
 * - Fresh odds are critical for betting decisions
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const API = process.env.REACT_APP_BACKEND_URL;

// Polling intervals
const LIVE_ODDS_STALE_TIME = 15 * 1000;  // 15 seconds
const LIVE_ODDS_REFETCH_INTERVAL = 30 * 1000;  // 30 seconds

/**
 * Fetch all active lines (full board)
 */
const fetchLiveOdds = async () => {
  const response = await fetch(`${API}/api/v3/cached-props`);
  
  if (!response.ok) {
    throw new Error(`Live odds fetch failed: ${response.status}`);
  }
  
  const data = await response.json();
  return data;
};

/**
 * Fetch War Zone picks (top demon plays)
 */
const fetchWarZone = async () => {
  const response = await fetch(`${API}/api/v3/war-zone`);
  if (!response.ok) throw new Error('War Zone fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch Safe Haven picks (goblin vault)
 */
const fetchSafeHaven = async () => {
  const response = await fetch(`${API}/api/v3/goblin-vault`);
  if (!response.ok) throw new Error('Safe Haven fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Preload images for faster rendering
 * Call this after fetching picks data
 */
const preloadImages = (picks) => {
  if (!picks || !Array.isArray(picks)) return;
  
  const backendUrl = process.env.REACT_APP_BACKEND_URL || '';
  
  picks.forEach(pick => {
    const photoUrl = pick.photo_url;
    if (photoUrl && photoUrl.startsWith('/api')) {
      const fullUrl = `${backendUrl}${photoUrl}`;
      const img = new Image();
      img.src = fullUrl;
    }
  });
};

/**
 * Fetch Front Lines picks (mixed)
 */
const fetchFrontLines = async () => {
  const response = await fetch(`${API}/api/v3/front-lines`);
  if (!response.ok) throw new Error('Front Lines fetch failed');
  const data = await response.json();
  // Preload images immediately after fetch
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch Most Popular Bets (by volume - all types)
 */
const fetchMostPopularBets = async () => {
  const response = await fetch(`${API}/api/v3/most-popular-bets`);
  if (!response.ok) throw new Error('Most Popular fetch failed');
  return response.json();
};

/**
 * Fetch live scores
 */
const fetchLiveScores = async () => {
  const response = await fetch(`${API}/api/live/scores`);
  if (!response.ok) return { games: [] };
  return response.json();
};

/**
 * Fetch breaking news
 */
const fetchBreakingNews = async () => {
  const response = await fetch(`${API}/api/live/news`);
  if (!response.ok) return { headlines: [] };
  return response.json();
};

/**
 * Search players
 */
const fetchPlayerSearch = async (query) => {
  if (!query || query.length < 2) return { players: [] };
  const response = await fetch(`${API}/api/command/search?query=${encodeURIComponent(query)}&limit=15`);
  if (!response.ok) throw new Error('Search failed');
  return response.json();
};

/**
 * Fetch player profile for Command Post
 */
const fetchPlayerProfile = async (playerName) => {
  if (!playerName) return null;
  // Use player-with-badges endpoint which returns data in UniversalPlayerCard format
  const response = await fetch(`${API}/api/v3/player-with-badges/${encodeURIComponent(playerName)}`);
  if (!response.ok) throw new Error('Profile fetch failed');
  const data = await response.json();
  // Transform to expected format
  if (data.success && data.player) {
    return {
      success: true,
      player_name: data.player.player_name,
      player_id: data.player.bdl_player_id,
      team: data.player.team,
      position: data.player.position,
      photo_url: data.player.photo_url,
      opponent: data.player.props?.[0]?.opponent || '',
      // Pass the full player object for UniversalPlayerCard
      playerData: data.player,
      // Lines/props for selection
      lines: data.player.props || [],
      demons: data.player.demons || [],
      goblins: data.player.goblins || []
    };
  }
  return data;
};

/**
 * Run parlay simulation
 */
const runSimulation = async (legs) => {
  const response = await fetch(`${API}/api/command/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ legs })
  });
  if (!response.ok) throw new Error('Simulation failed');
  return response.json();
};

// ==================== HOOKS ====================

/**
 * useLiveOdds - PIPE 2 Primary Hook
 * Returns full cached board with live lines
 */
export const useLiveOdds = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  
  return useQuery({
    queryKey: ['liveOdds'],
    queryFn: fetchLiveOdds,
    enabled,
    
    // LIGHT CACHE - Fresh odds are critical
    staleTime: LIVE_ODDS_STALE_TIME,
    
    // OPEN DOOR: Background polling every 30 seconds
    refetchInterval,
    refetchIntervalInBackground: false,  // Pause when tab not focused
    
    // Refetch on window focus for fresh odds
    refetchOnWindowFocus: true,
  });
};

/**
 * useWarZone - War Zone (Demon) picks
 */
export const useWarZone = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  
  return useQuery({
    queryKey: ['warZone'],
    queryFn: fetchWarZone,
    enabled,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useSafeHaven - Safe Haven (Goblin) picks
 */
export const useSafeHaven = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  
  return useQuery({
    queryKey: ['safeHaven'],
    queryFn: fetchSafeHaven,
    enabled,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useFrontLines - Front Lines (Mixed) picks
 */
export const useFrontLines = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  
  return useQuery({
    queryKey: ['frontLines'],
    queryFn: fetchFrontLines,
    enabled,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMostPopularBets - Most Popular bets by volume (all types)
 */
export const useMostPopularBets = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  
  return useQuery({
    queryKey: ['mostPopularBets'],
    queryFn: fetchMostPopularBets,
    enabled,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useLiveScores - Live game scores
 */
export const useLiveScores = () => {
  return useQuery({
    queryKey: ['liveScores'],
    queryFn: fetchLiveScores,
    staleTime: 10 * 1000,  // 10 seconds
    refetchInterval: 30 * 1000,  // 30 seconds
    refetchOnWindowFocus: true,
  });
};

/**
 * useBreakingNews - Breaking news ticker
 */
export const useBreakingNews = () => {
  return useQuery({
    queryKey: ['breakingNews'],
    queryFn: fetchBreakingNews,
    staleTime: 60 * 1000,  // 1 minute
    refetchInterval: 60 * 1000,  // 1 minute
    refetchOnWindowFocus: false,
  });
};

/**
 * usePlayerSearch - Player search with debouncing handled by caller
 */
export const usePlayerSearch = (query) => {
  return useQuery({
    queryKey: ['playerSearch', query],
    queryFn: () => fetchPlayerSearch(query),
    enabled: Boolean(query && query.length >= 2),
    staleTime: 30 * 1000,  // Cache search results for 30 seconds
    gcTime: 5 * 60 * 1000,  // Keep in cache for 5 minutes
  });
};

/**
 * usePlayerProfile - Command Post player profile
 */
export const usePlayerProfile = (playerName) => {
  return useQuery({
    queryKey: ['playerProfile', playerName],
    queryFn: () => fetchPlayerProfile(playerName),
    enabled: Boolean(playerName),
    staleTime: 60 * 1000,  // 1 minute
  });
};

/**
 * useSimulation - Parlay simulation using useMutation pattern
 * This is the proper pattern for POST operations
 */
export const useSimulation = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: runSimulation,
    onSuccess: (data) => {
      // Optionally invalidate related queries after successful simulation
      console.log('[SIMULATION] Success:', data);
    },
    onError: (error) => {
      console.error('[SIMULATION] Error:', error);
    }
  });
};

/**
 * useSimulationQuery - Parlay simulation (query pattern - deprecated)
 * Use useSimulation mutation instead for better UX.
 */
export const useSimulationQuery = (legs) => {
  return useQuery({
    queryKey: ['simulation', JSON.stringify(legs)],
    queryFn: () => runSimulation(legs),
    enabled: legs && legs.length > 0,
    staleTime: 0,  // Always fresh
    gcTime: 0,  // Don't cache
  });
};

export default useLiveOdds;
