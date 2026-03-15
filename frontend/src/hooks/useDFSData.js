import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';

// API base URL from environment
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * useDFSData - Central data fetching hook for PickVision
 * 
 * HARD CUTOFF: Reads ONLY from MongoDB via cached endpoints.
 * NO direct Odds API calls from frontend.
 */
export const useDFSData = () => {
  // Core pick data
  const [players, setPlayers] = useState([]);
  const [radarPicks, setRadarPicks] = useState([]);     // War Zone
  const [vaultPicks, setVaultPicks] = useState([]);     // Safe Haven
  const [frontLinesPicks, setFrontLinesPicks] = useState([]); // Front Lines
  
  // Popular bets (live ticker)
  const [popularBets, setPopularBets] = useState([]);
  const [popularBetsLastUpdated, setPopularBetsLastUpdated] = useState(null);
  const [popularBetsStatus, setPopularBetsStatus] = useState('awaiting_action');
  const [popularBetsLoading, setPopularBetsLoading] = useState(false);
  
  // Live data
  const [liveScores, setLiveScores] = useState([]);
  const [breakingNews, setBreakingNews] = useState([]);
  const [tMinusGames, setTMinusGames] = useState([]);
  
  // Status tracking
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [boardIntelStatus, setBoardIntelStatus] = useState({
    time_since_sync_display: 'Loading...',
    last_sync_type: null,
    scheduler_running: false
  });
  
  // Helper to apply social signals to picks
  const applySignals = (picks, socialSignals) => {
    return picks.map(pick => {
      const signal = socialSignals[pick.player_name];
      if (signal) {
        return {
          ...pick,
          volatility_flag: signal.volatility_flag,
          volatility_reason: signal.volatility_reason,
          revenge_game: signal.revenge_game,
          revenge_opponent: signal.revenge_opponent
        };
      }
      return pick;
    });
  };
  
  // Filter picks with missing required data
  const filterValidPicks = (picks) => {
    return picks.filter(pick => {
      // Must have player_name, team, stat_type, and a line value
      if (!pick.player_name || !pick.team || !pick.stat_type) return false;
      const line = pick.demon_line || pick.goblin_line || pick.line;
      if (line === undefined || line === null) return false;
      return true;
    });
  };
  
  // Main data loader
  const loadCachedBoard = useCallback(async () => {
    try {
      console.log('[DFS DATA] Loading from MongoDB...');
      
      const [
        warZoneResponse,
        frontLinesResponse,
        vaultResponse,
        boardResponse,
        socialSignalsResponse,
        liveScoresResponse,
        newsResponse,
        tMinusResponse,
        boardIntelResponse,
        popularBetsResponse
      ] = await Promise.all([
        axios.get(`${API}/v3/war-zone`),
        axios.get(`${API}/v3/front-lines`),
        axios.get(`${API}/v3/goblin-vault`),
        axios.get(`${API}/v3/cached-props`),
        axios.get(`${API}/v3/social-signals`).catch(() => ({ data: { signals: {} }})),
        axios.get(`${API}/v3/live-scores`).catch(() => ({ data: { games: [] }})),
        axios.get(`${API}/v3/breaking-news`).catch(() => ({ data: { news: [] }})),
        axios.get(`${API}/v3/t-minus-games`).catch(() => ({ data: { games: [] }})),
        axios.get(`${API}/v3/board-intel/status`).catch(() => ({ data: {} })),
        axios.get(`${API}/v3/most-popular-bets`).catch(() => ({ data: { bets: [] }}))
      ]);
      
      const socialSignals = socialSignalsResponse.data?.signals || {};
      
      // Process and set War Zone picks
      if (warZoneResponse.data.success) {
        const picks = applySignals(warZoneResponse.data.picks || [], socialSignals);
        setRadarPicks(filterValidPicks(picks));
        console.log(`[WAR ZONE] ${picks.length} picks loaded`);
      }
      
      // Process and set Safe Haven picks
      if (vaultResponse.data.success) {
        const picks = applySignals(vaultResponse.data.picks || [], socialSignals);
        setVaultPicks(filterValidPicks(picks));
        console.log(`[SAFE HAVEN] ${picks.length} picks loaded`);
      }
      
      // Process and set Front Lines picks
      if (frontLinesResponse.data.success) {
        const picks = applySignals(frontLinesResponse.data.picks || [], socialSignals);
        setFrontLinesPicks(filterValidPicks(picks));
        console.log(`[FRONT LINES] ${picks.length} picks loaded`);
      }
      
      // Board data
      if (boardResponse.data.success) {
        setPlayers(boardResponse.data.players || []);
        setLinesLoaded(true);
      }
      
      // Live data
      setLiveScores(liveScoresResponse.data?.games || []);
      setBreakingNews(newsResponse.data?.news || []);
      setTMinusGames(tMinusResponse.data?.games || []);
      
      // Board intel status
      if (boardIntelResponse.data) {
        setBoardIntelStatus({
          time_since_sync_display: boardIntelResponse.data.time_since_sync_display || 'Not synced',
          last_sync_type: boardIntelResponse.data.last_sync_type,
          scheduler_running: boardIntelResponse.data.scheduler_running || false
        });
      }
      
      // Popular bets
      if (popularBetsResponse.data) {
        setPopularBets(popularBetsResponse.data.bets || []);
        setPopularBetsLastUpdated(popularBetsResponse.data.last_updated);
        setPopularBetsStatus(popularBetsResponse.data.status || 'awaiting_action');
      }
      
    } catch (error) {
      console.error('[DFS DATA] Load error:', error);
    }
  }, []);
  
  // Popular bets polling (45 second interval)
  const pollPopularBets = useCallback(async () => {
    try {
      setPopularBetsLoading(true);
      const response = await axios.get(`${API}/v3/popular-bets`);
      if (response.data.success) {
        setPopularBets(response.data.bets || []);
        setPopularBetsLastUpdated(response.data.last_updated);
        setPopularBetsStatus(response.data.status || 'awaiting_action');
      }
    } catch (error) {
      console.error('[POPULAR BETS] Poll error:', error);
    } finally {
      setPopularBetsLoading(false);
    }
  }, []);
  
  // Manual sync trigger
  const triggerSync = async () => {
    try {
      setSyncing(true);
      setLinesLoaded(false);
      toast.info('Syncing data...');
      
      const response = await axios.post(`${API}/v3/board-intel/primary-sync`, {}, { timeout: 600000 });
      
      if (response.data.success) {
        toast.success('Sync complete!');
        await loadCachedBoard();
      } else {
        toast.error('Sync failed');
      }
    } catch (error) {
      toast.error('Sync failed: ' + error.message);
    } finally {
      setSyncing(false);
    }
  };
  
  // Initial load
  useEffect(() => {
    loadCachedBoard();
  }, [loadCachedBoard]);
  
  // Popular bets polling
  useEffect(() => {
    const pollInterval = setInterval(pollPopularBets, 45000);
    return () => clearInterval(pollInterval);
  }, [pollPopularBets]);
  
  return {
    // Pick data
    players,
    radarPicks,
    vaultPicks,
    frontLinesPicks,
    
    // Popular bets
    popularBets,
    popularBetsLastUpdated,
    popularBetsStatus,
    popularBetsLoading,
    
    // Live data
    liveScores,
    breakingNews,
    tMinusGames,
    
    // Status
    linesLoaded,
    syncing,
    boardIntelStatus,
    
    // Actions
    triggerSync,
    loadCachedBoard
  };
};
