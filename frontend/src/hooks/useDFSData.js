import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';

// API base URL from environment
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * useDFSData - CENTRAL DATA HUB for PickVision
 * ============================================
 * 
 * SSOT ARCHITECTURE NOTE:
 * This hook is the APPROVED central data fetching layer.
 * It reads ONLY from MongoDB via cached backend endpoints.
 * NO direct external API calls (Tank01, BallDontLie, Odds API).
 * 
 * All data flows:
 *   Backend (MongoDB) → useDFSData → Components
 * 
 * Components are FORBIDDEN from making their own fetch() calls.
 * They must consume data from this hook or subscribe to Global Store.
 * 
 * TODO: Migrate to Global Store (Zustand/Redux) for reactive state management
 */
export const useDFSData = () => {
  // Core pick data
  const [players, setPlayers] = useState([]);
  const [trending, setTrending] = useState([]);
  const [radarPicks, setRadarPicks] = useState([]);     // War Zone (Demons)
  const [vaultPicks, setVaultPicks] = useState([]);     // Safe Haven (Goblins)
  const [frontLinesPicks, setFrontLinesPicks] = useState([]); // Front Lines (Mixed)
  
  // Popular bets (live ticker)
  const [popularBets, setPopularBets] = useState([]);
  const [popularBetsLastUpdated, setPopularBetsLastUpdated] = useState(null);
  const [popularBetsStatus, setPopularBetsStatus] = useState('awaiting_action');
  const [popularBetsLoading, setPopularBetsLoading] = useState(false);
  
  // Live data
  const [liveScores, setLiveScores] = useState([]);
  const [breakingNews, setBreakingNews] = useState([]);
  const [tMinusGames, setTMinusGames] = useState([]);
  const [injuryAlerts, setInjuryAlerts] = useState({});
  
  // Scouting projections (early bird)
  const [scoutingProjections, setScoutingProjections] = useState([]);
  const [isEarlyBirdActive, setIsEarlyBirdActive] = useState(false);
  
  // Status tracking
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [boardIntelStatus, setBoardIntelStatus] = useState({
    time_since_sync_display: 'Loading...',
    last_sync_type: null,
    scheduler_running: false,
    next_scheduled_sync: null
  });
  const [syncStatus, setSyncStatus] = useState({
    engine_status: 'loading',
    sync_age_display: '...',
    seconds_since_sync: 0,
    mission_critical_games: 0,
    has_stale_intel: false
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
  
  /**
   * CRITICAL: Filter picks with missing required data
   * Specifically hunts for Demon attributes that cause blackout
   * 
   * @param {Array} picks - Raw picks array
   * @param {string} pickType - 'demon' | 'goblin' | 'mixed' for logging
   * @returns {Array} - Validated picks only
   */
  const filterValidPicks = (picks, pickType = 'unknown') => {
    const validPicks = [];
    const droppedPicks = [];
    
    for (const pick of picks) {
      const issues = [];
      
      // Required fields check
      if (!pick.player_name) issues.push('missing player_name');
      if (!pick.team) issues.push('missing team');
      if (!pick.stat_type) issues.push('missing stat_type');
      
      // LINE VALUE CHECK - Critical for Demons
      const lineValue = pick.demon_line || pick.goblin_line || pick.line;
      if (lineValue === undefined || lineValue === null) {
        issues.push('missing line value (demon_line/goblin_line/line)');
      }
      
      // DEMON-SPECIFIC VALIDATION
      if (pick.is_demon || pickType === 'demon') {
        // Demon must have demon_line
        if (!pick.demon_line && pick.demon_line !== 0) {
          issues.push('DEMON DATA MISMATCH: is_demon=true but no demon_line');
        }
        // Demon should have scoring data
        if (!pick.radar_score && pick.radar_score !== 0) {
          issues.push('DEMON DATA MISMATCH: missing radar_score');
        }
      }
      
      // GOBLIN-SPECIFIC VALIDATION
      if (pick.is_goblin || pickType === 'goblin') {
        if (!pick.goblin_line && pick.goblin_line !== 0) {
          issues.push('GOBLIN DATA MISMATCH: is_goblin=true but no goblin_line');
        }
      }
      
      // Win probability check (important for EV calculation)
      if (pick.win_probability === undefined && pick.h10_rate === undefined) {
        issues.push('missing win_probability and h10_rate');
      }
      
      if (issues.length > 0) {
        droppedPicks.push({ player: pick.player_name, stat: pick.stat_type, issues });
      } else {
        validPicks.push(pick);
      }
    }
    
    // Log data mismatches for debugging
    if (droppedPicks.length > 0) {
      console.warn(`[DATA MISMATCH] ${pickType.toUpperCase()} - Dropped ${droppedPicks.length} picks BEFORE matrix:`);
      droppedPicks.forEach(d => {
        console.warn(`  • ${d.player} (${d.stat}): ${d.issues.join(', ')}`);
      });
    }
    
    console.log(`[${pickType.toUpperCase()}] Valid: ${validPicks.length}/${picks.length} picks passed validation`);
    return validPicks;
  };
  
  // Main data loader
  const loadCachedBoard = useCallback(async () => {
    try {
      console.log('[DFS DATA] Loading from MongoDB...');
      
      const [
        boardResponse,
        warZoneResponse,
        frontLinesResponse,
        vaultResponse,
        socialSignalsResponse,
        liveScoresResponse,
        newsResponse,
        tMinusResponse,
        boardIntelResponse,
        popularBetsResponse,
        injuryResponse,
        syncStatusResponse,
        scoutingResponse
      ] = await Promise.all([
        axios.get(`${API}/v3/cached-props`),
        axios.get(`${API}/v3/war-zone`),
        axios.get(`${API}/v3/front-lines`),
        axios.get(`${API}/v3/goblin-vault`),
        axios.get(`${API}/v3/social-signals`).catch(() => ({ data: { signals: {} }})),
        axios.get(`${API}/v3/live-scores`).catch(() => ({ data: { games: [] }})),
        axios.get(`${API}/v3/breaking-news`).catch(() => ({ data: { news: [] }})),
        axios.get(`${API}/v3/t-minus-games`).catch(() => ({ data: { games: [] }})),
        axios.get(`${API}/v3/board-intel/status`).catch(() => ({ data: {} })),
        axios.get(`${API}/v3/most-popular-bets`).catch(() => ({ data: { bets: [] }})),
        axios.get(`${API}/v3/injuries/alerts`).catch(() => ({ data: { alerts: {} }})),
        axios.get(`${API}/v3/sync-status`).catch(() => ({ data: {} })),
        axios.get(`${API}/v3/scouting-projections`).catch(() => ({ data: { projections: [] }}))
      ]);
      
      const socialSignals = socialSignalsResponse.data?.signals || {};
      
      // Process and set board data
      if (boardResponse.data.success && boardResponse.data.players_count > 0) {
        setPlayers(boardResponse.data.players || []);
        setTrending(boardResponse.data.trending || []);
        setStaticLoaded(true);
        setLinesLoaded(true);
        console.log(`[BOARD] Loaded ${boardResponse.data.players_count} players`);
      } else {
        setStaticLoaded(true);
        setLinesLoaded(false);
      }
      
      // Process War Zone (Demons) with strict validation
      if (warZoneResponse.data.success) {
        const rawPicks = warZoneResponse.data.picks || [];
        const picksWithSignals = applySignals(rawPicks, socialSignals);
        const validPicks = filterValidPicks(picksWithSignals, 'demon');
        setRadarPicks(validPicks);
      }
      
      // Process Safe Haven (Goblins) with validation
      if (vaultResponse.data.success) {
        const rawPicks = vaultResponse.data.picks || [];
        const picksWithSignals = applySignals(rawPicks, socialSignals);
        const validPicks = filterValidPicks(picksWithSignals, 'goblin');
        setVaultPicks(validPicks);
      }
      
      // Process Front Lines (Mixed) with validation
      if (frontLinesResponse.data.success) {
        const rawPicks = frontLinesResponse.data.picks || [];
        const picksWithSignals = applySignals(rawPicks, socialSignals);
        const validPicks = filterValidPicks(picksWithSignals, 'mixed');
        setFrontLinesPicks(validPicks);
      }
      
      // Live data
      setLiveScores(liveScoresResponse.data?.games || []);
      setBreakingNews(newsResponse.data?.news || []);
      setTMinusGames(tMinusResponse.data?.games || []);
      setInjuryAlerts(injuryResponse.data?.alerts || {});
      
      // Board intel status
      if (boardIntelResponse.data) {
        setBoardIntelStatus({
          time_since_sync_display: boardIntelResponse.data.time_since_sync_display || 'Not synced',
          last_sync_type: boardIntelResponse.data.last_sync_type,
          scheduler_running: boardIntelResponse.data.scheduler_running || false,
          next_scheduled_sync: boardIntelResponse.data.next_scheduled_sync
        });
      }
      
      // Sync status
      if (syncStatusResponse.data) {
        setSyncStatus({
          engine_status: syncStatusResponse.data.engine_status || 'offline',
          sync_age_display: syncStatusResponse.data.sync_age_display || 'N/A',
          seconds_since_sync: syncStatusResponse.data.seconds_since_sync || 0,
          mission_critical_games: syncStatusResponse.data.mission_critical_games || 0,
          has_stale_intel: (syncStatusResponse.data.seconds_since_sync || 0) > 300
        });
      }
      
      // Popular bets
      if (popularBetsResponse.data) {
        setPopularBets(popularBetsResponse.data.bets || []);
        setPopularBetsLastUpdated(popularBetsResponse.data.last_updated);
        setPopularBetsStatus(popularBetsResponse.data.status || 'awaiting_action');
      }
      
      // Scouting projections
      if (scoutingResponse.data?.projections) {
        setScoutingProjections(scoutingResponse.data.projections || []);
        setIsEarlyBirdActive(scoutingResponse.data.status === 'early_bird_active');
      }
      
    } catch (error) {
      console.error('[DFS DATA] Load error:', error);
      setStaticLoaded(true);
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
  
  // Live scores refresh when games are in progress
  useEffect(() => {
    const hasLiveGames = liveScores.some(g => g.status === 'in_play');
    if (!hasLiveGames) return;
    
    const refreshInterval = setInterval(async () => {
      try {
        const response = await axios.get(`${API}/v3/live-scores`);
        if (response.data.success) {
          setLiveScores(response.data.games || []);
        }
      } catch (e) {
        console.error('[LIVE SCORES] Refresh error:', e);
      }
    }, 60000);
    
    return () => clearInterval(refreshInterval);
  }, [liveScores]);
  
  return {
    // Pick data
    players,
    trending,
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
    injuryAlerts,
    
    // Scouting
    scoutingProjections,
    isEarlyBirdActive,
    
    // Status
    linesLoaded,
    staticLoaded,
    syncing,
    boardIntelStatus,
    syncStatus,
    
    // Actions
    triggerSync,
    loadCachedBoard
  };
};
