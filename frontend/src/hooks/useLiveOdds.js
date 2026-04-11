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
 * 
 * Sport-Aware:
 * - All fetches include ?sport= parameter
 * - Query keys include sport for proper cache invalidation
 * 
 * Mock Data Mode:
 * - Set USE_MOCK_DATA = true in config/mockData.js
 * - Returns mock NBA/MLB data based on currentSport
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSport } from '../context/SportContext';
import { USE_MOCK_DATA, getMockTierData, getMockAllProps } from '../config/mockData';

const API = process.env.REACT_APP_BACKEND_URL;

// Polling intervals
const LIVE_ODDS_STALE_TIME = 15 * 1000;  // 15 seconds
const LIVE_ODDS_REFETCH_INTERVAL = 30 * 1000;  // 30 seconds

/**
 * Build URL with sport parameter
 */
const buildUrl = (endpoint, sport = 'nba') => {
  const separator = endpoint.includes('?') ? '&' : '?';
  return `${API}${endpoint}${separator}sport=${sport}`;
};

/**
 * Fetch all active lines (full board)
 */
const fetchLiveOdds = async (sport = 'nba') => {
  // MOCK DATA MODE - MLB ONLY
  if (USE_MOCK_DATA && sport === 'mlb') {
    console.log(`[MOCK] Returning mock props for MLB`);
    return getMockAllProps(sport);
  }
  
  const response = await fetch(buildUrl('/api/v3/cached-props', sport));
  
  if (!response.ok) {
    throw new Error(`Live odds fetch failed: ${response.status}`);
  }
  
  const data = await response.json();
  return data;
};

/**
 * Fetch War Zone picks (Elite Demons - Ferrari filtered)
 * Sharp price >= +500, Bovada 200+ pts separation
 */
const fetchWarZone = async (sport = 'nba') => {
  // MOCK DATA MODE - MLB ONLY
  if (USE_MOCK_DATA && sport === 'mlb') {
    console.log(`[MOCK] Returning War Zone mock data for MLB`);
    return getMockTierData(sport, 'war_zone');
  }
  
  const response = await fetch(buildUrl('/api/v3/ferrari/war-zone', sport));
  if (!response.ok) throw new Error('War Zone fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch Safe Haven picks (Elite Goblins - Ferrari filtered)
 * Sharp price <= -250, L10 >= 70%
 */
const fetchSafeHaven = async (sport = 'nba') => {
  // MOCK DATA MODE - MLB ONLY
  if (USE_MOCK_DATA && sport === 'mlb') {
    console.log(`[MOCK] Returning Safe Haven mock data for MLB`);
    return getMockTierData(sport, 'safe_haven');
  }
  
  const response = await fetch(buildUrl('/api/v3/ferrari/safe-haven', sport));
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
 * Fetch Front Lines picks (Battleground - Ferrari filtered)
 * Sharp price -245 to -149, L10 >= 70%, sorted by hit rate
 */
const fetchFrontLines = async (sport = 'nba') => {
  // MOCK DATA MODE - MLB ONLY
  if (USE_MOCK_DATA && sport === 'mlb') {
    console.log(`[MOCK] Returning Front Lines mock data for MLB`);
    return getMockTierData(sport, 'front_lines');
  }
  
  const response = await fetch(buildUrl('/api/v3/ferrari/front-lines', sport));
  if (!response.ok) throw new Error('Front Lines fetch failed');
  const data = await response.json();
  // Preload images immediately after fetch
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch MLB Sharp Goblins (Sharp money confirmed)
 * MLB-only tier: Pinnacle odds ≤ -150 AND VK confirms
 */
const fetchMLBGoblins = async () => {
  const response = await fetch(`${API}/api/v3/mlb/sharp/goblins`);
  if (!response.ok) throw new Error('MLB Goblins fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch MLB Demons (DK/PP line discrepancy)
 * MLB-only tier: Line discrepancy + high edge
 */
const fetchMLBDemons = async () => {
  const response = await fetch(`${API}/api/v3/mlb/sharp/demons`);
  if (!response.ok) throw new Error('MLB Demons fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch MLB HRR (Hits+Runs+RBIs) combo picks
 * MLB-only tier: High edge + high hit rate on combo stats
 */
const fetchMLBHRRPicks = async () => {
  const response = await fetch(`${API}/api/v3/mlb/ferrari/hrr-picks`);
  if (!response.ok) throw new Error('MLB HRR picks fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch MLB Safe Haven picks (3-Gate qualified Goblins)
 * MLB-only: DK Odds ≤ -240 + passes HR/CV/Edge gates
 */
const fetchMLBSafeHaven = async () => {
  const response = await fetch(`${API}/api/v3/ferrari/safe-haven?sport=mlb`);
  if (!response.ok) throw new Error('MLB Safe Haven fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch MLB Front Lines picks (Mid-juice 3-Gate qualified)
 * MLB-only: -240 < DK Odds ≤ -145 + passes HR/CV/Edge gates with Pivot Rule
 */
const fetchMLBFrontLines = async () => {
  const response = await fetch(`${API}/api/v3/ferrari/front-lines?sport=mlb`);
  if (!response.ok) throw new Error('MLB Front Lines fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * Fetch Most Popular Bets (by volume - all types)
 */
const fetchMostPopularBets = async (sport = 'nba') => {
  const response = await fetch(buildUrl('/api/v3/most-popular-bets', sport));
  if (!response.ok) throw new Error('Most Popular fetch failed');
  return response.json();
};

/**
 * Fetch live scores
 */
const fetchLiveScores = async (sport = 'nba') => {
  const response = await fetch(buildUrl('/api/live/scores', sport));
  if (!response.ok) return { games: [] };
  return response.json();
};

/**
 * Fetch breaking news
 */
const fetchBreakingNews = async (sport = 'nba') => {
  const response = await fetch(buildUrl('/api/live/news', sport));
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
  try {
    // Use player-with-badges endpoint which returns data in UniversalPlayerCard format
    const response = await fetch(`${API}/api/v3/player-with-badges/${encodeURIComponent(playerName)}`);
    if (!response.ok) {
      // Return structured error instead of throwing
      return { success: false, message: `Player not found (${response.status})`, player: null };
    }
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
    // Return the data with success: false
    return { success: false, message: data.message || 'Player not in cache', player: null };
  } catch (error) {
    console.error('[fetchPlayerProfile] Error:', error);
    return { success: false, message: error.message || 'Failed to fetch player', player: null };
  }
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
 * Sport-aware: includes currentSport in query key and fetch
 */
export const useLiveOdds = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['liveOdds', currentSport],
    queryFn: () => fetchLiveOdds(currentSport),
    enabled: enabled && !isTransitioning,
    
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
 * Sport-aware
 */
export const useWarZone = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['warZone', currentSport],
    queryFn: () => fetchWarZone(currentSport),
    enabled: enabled && !isTransitioning,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useSafeHaven - Safe Haven (Goblin) picks
 * Sport-aware
 */
export const useSafeHaven = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['safeHaven', currentSport],
    queryFn: () => fetchSafeHaven(currentSport),
    enabled: enabled && !isTransitioning,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useFrontLines - Front Lines (Mixed) picks
 * Sport-aware
 */
export const useFrontLines = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['frontLines', currentSport],
    queryFn: () => fetchFrontLines(currentSport),
    enabled: enabled && !isTransitioning,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMostPopularBets - Most Popular bets by volume (all types)
 * Sport-aware
 */
export const useMostPopularBets = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mostPopularBets', currentSport],
    queryFn: () => fetchMostPopularBets(currentSport),
    enabled: enabled && !isTransitioning,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * Fetch Trap Graveyard picks (flagged hook/bait picks)
 */
const fetchTrapGraveyard = async (sport = 'nba') => {
  const response = await fetch(buildUrl('/api/v3/trap-graveyard', sport));
  if (!response.ok) throw new Error('Trap Graveyard fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * useTrapGraveyard - Trap Graveyard (Hook Risk / Vegas Bait) picks
 * Sport-aware
 */
export const useTrapGraveyard = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['trapGraveyard', currentSport],
    queryFn: () => fetchTrapGraveyard(currentSport),
    enabled: enabled && !isTransitioning,
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMLBGoblins - MLB Sharp Goblins (Pinnacle confirmed)
 * MLB-only
 */
export const useMLBGoblins = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbGoblins'],
    queryFn: fetchMLBGoblins,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMLBDemons - MLB Demons (DK/PP mispricing)
 * MLB-only
 */
export const useMLBDemons = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbDemons'],
    queryFn: fetchMLBDemons,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMLBHRRPicks - MLB Hits+Runs+RBIs combo picks
 * MLB-only
 */
export const useMLBHRRPicks = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbHRRPicks'],
    queryFn: fetchMLBHRRPicks,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMLBSafeHaven - MLB Safe Haven picks (3-Gate qualified Goblins)
 * MLB-only: DK ≤ -240 + HR/CV/Edge gates
 */
export const useMLBSafeHaven = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbSafeHaven'],
    queryFn: fetchMLBSafeHaven,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useMLBFrontLines - MLB Front Lines picks (Mid-juice 3-Gate qualified)
 * MLB-only: -240 < DK ≤ -145 + HR/CV/Edge gates with Pivot Rule
 */
export const useMLBFrontLines = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbFrontLines'],
    queryFn: fetchMLBFrontLines,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * Fetch MLB War Zone picks (Moonshot Demons with Ceiling Protocol)
 * MLB-only: High CV + Boom Rule + Moonshot Edge
 */
const fetchMLBWarZone = async () => {
  const response = await fetch(`${API}/api/v3/ferrari/war-zone?sport=mlb`);
  if (!response.ok) throw new Error('MLB War Zone fetch failed');
  const data = await response.json();
  preloadImages(data.picks);
  return data;
};

/**
 * useMLBWarZone - MLB War Zone picks (Moonshot Demons)
 * MLB-only: High volatility Lottery Tickets
 */
export const useMLBWarZone = (options = {}) => {
  const { enabled = true, refetchInterval = LIVE_ODDS_REFETCH_INTERVAL } = options;
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['mlbWarZone'],
    queryFn: fetchMLBWarZone,
    enabled: enabled && !isTransitioning && currentSport === 'mlb',
    staleTime: LIVE_ODDS_STALE_TIME,
    refetchInterval,
    refetchOnWindowFocus: true,
  });
};

/**
 * useLiveScores - Live game scores
 * Sport-aware
 */
export const useLiveScores = () => {
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['liveScores', currentSport],
    queryFn: () => fetchLiveScores(currentSport),
    enabled: !isTransitioning,
    staleTime: 10 * 1000,  // 10 seconds
    refetchInterval: 30 * 1000,  // 30 seconds
    refetchOnWindowFocus: true,
  });
};

/**
 * useBreakingNews - Breaking news ticker
 * Sport-aware
 */
export const useBreakingNews = () => {
  const { currentSport, isTransitioning } = useSport();
  
  return useQuery({
    queryKey: ['breakingNews', currentSport],
    queryFn: () => fetchBreakingNews(currentSport),
    enabled: !isTransitioning,
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

/**
 * Fetch Live Vacuum Alerts (Usage Vacuum)
 * Returns players benefiting from late-breaking injury news
 */
const fetchLiveVacuumAlerts = async () => {
  const response = await fetch(`${API}/api/v3/vacuum/live-alerts`);
  if (!response.ok) throw new Error('Vacuum alerts fetch failed');
  return response.json();
};

/**
 * useLiveVacuumAlerts - Live Injury Advantage alerts
 * Polls every 60 seconds for late-breaking injury news
 */
export const useLiveVacuumAlerts = () => {
  return useQuery({
    queryKey: ['live-vacuum-alerts'],
    queryFn: fetchLiveVacuumAlerts,
    staleTime: 30 * 1000,  // 30 seconds
    refetchInterval: 60 * 1000,  // Poll every 60 seconds
    refetchOnWindowFocus: true,
    retry: 2
  });
};

/**
 * Fetch MLB Live Vacuum Alerts (Usage Vacuum)
 * Returns players benefiting from late-breaking MLB injury news
 */
const fetchMLBLiveVacuumAlerts = async () => {
  const response = await fetch(`${API}/api/v3/mlb/vacuum/live-alerts`);
  if (!response.ok) throw new Error('MLB Vacuum alerts fetch failed');
  return response.json();
};

/**
 * useMLBLiveVacuumAlerts - MLB Live Injury Advantage alerts
 * Polls every 60 seconds for late-breaking MLB injury news
 */
export const useMLBLiveVacuumAlerts = () => {
  return useQuery({
    queryKey: ['mlb-live-vacuum-alerts'],
    queryFn: fetchMLBLiveVacuumAlerts,
    staleTime: 30 * 1000,  // 30 seconds
    refetchInterval: 60 * 1000,  // Poll every 60 seconds
    refetchOnWindowFocus: true,
    retry: 2
  });
};

export default useLiveOdds;
