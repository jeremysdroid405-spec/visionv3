/**
 * PICKVISION AI DATA SERVICE
 * =======================
 * Clean, modular data-fetching service for unified backend endpoints.
 * 
 * All player data flows through:
 * - Master Hub (nba_master_hub_2026) for photos & stats
 * - Daily Slate Master for odds/lines
 * - Cached Board for enriched picks
 * 
 * NO direct calls to external APIs (Odds API, Stats API) from frontend.
 * Backend handles all external API integration via OddsApiMapper.
 */

import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL ? `${process.env.REACT_APP_BACKEND_URL}/api` : '/api';

// Request timeout in ms
const TIMEOUT = 30000;

// Create axios instance with defaults
const apiClient = axios.create({
  baseURL: API,
  timeout: TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ==================== CORE DATA ENDPOINTS ====================

/**
 * Fetch all dashboard data in a single batch call.
 * Returns: war zone, goblin vault, parlays, sync status, etc.
 */
export const fetchDashboardData = async () => {
  try {
    const [
      warZone,
      goblinVault,
      parlayBuilder,
      goblinRecon,
      syncStatus,
      liveScores,
      lockStatus,
    ] = await Promise.all([
      apiClient.get('/v3/ferrari/war-zone?sport=nba').catch(() => ({ data: { picks: [] } })),
      apiClient.get('/v3/ferrari/safe-haven?sport=nba').catch(() => ({ data: { picks: [] } })),
      apiClient.get('/v3/parlay-builder').catch(() => ({ data: { demon_parlays: {}, goblin_parlays: {} } })),
      apiClient.get('/v3/goblin-recon').catch(() => ({ data: { parlays: {} } })),
      apiClient.get('/v3/sync-status').catch(() => ({ data: { engine_status: 'offline' } })),
      apiClient.get('/v3/live-scores').catch(() => ({ data: { games: [] } })),
      apiClient.get('/v3/lock-status').catch(() => ({ data: { locked_games: 0 } })),
    ]);

    return {
      success: true,
      data: {
        warZone: warZone.data?.picks || warZone.data || [],
        goblinVault: goblinVault.data?.picks || goblinVault.data || [],
        parlayBuilder: parlayBuilder.data || { demon_parlays: {}, goblin_parlays: {} },
        goblinRecon: goblinRecon.data?.parlays || goblinRecon.data || {},
        syncStatus: syncStatus.data || {},
        boardIntelStatus: syncStatus.data || {},
        liveScores: liveScores.data?.games || [],
        lockStatus: lockStatus.data || {},
      },
    };
  } catch (error) {
    console.error('[DataService] fetchDashboardData error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Fetch war zone picks (top 10 high-probability demons)
 */
export const fetchWarZone = async () => {
  try {
    const response = await apiClient.get('/v3/ferrari/war-zone?sport=nba');
    return { success: true, picks: response.data?.picks || response.data || [] };
  } catch (error) {
    console.error('[DataService] fetchWarZone error:', error);
    return { success: false, picks: [], error: error.message };
  }
};

/**
 * Fetch goblin vault picks (top 10 safe plays)
 */
export const fetchGoblinVault = async () => {
  try {
    const response = await apiClient.get('/v3/ferrari/safe-haven?sport=nba');
    return { success: true, picks: response.data?.picks || response.data || [] };
  } catch (error) {
    console.error('[DataService] fetchGoblinVault error:', error);
    return { success: false, picks: [], error: error.message };
  }
};

/**
 * Fetch parlay builder data (demon & goblin parlays)
 */
export const fetchParlayBuilder = async () => {
  try {
    const response = await apiClient.get('/v3/parlay-builder');
    return {
      success: true,
      demonParlays: response.data?.demon_parlays || {},
      goblinParlays: response.data?.goblin_parlays || {},
    };
  } catch (error) {
    console.error('[DataService] fetchParlayBuilder error:', error);
    return { success: false, demonParlays: {}, goblinParlays: {}, error: error.message };
  }
};

/**
 * Fetch goblin recon parlays (pre-built safe parlays)
 */
export const fetchGoblinRecon = async () => {
  try {
    const response = await apiClient.get('/v3/goblin-recon');
    return { success: true, parlays: response.data?.parlays || response.data || {} };
  } catch (error) {
    console.error('[DataService] fetchGoblinRecon error:', error);
    return { success: false, parlays: {}, error: error.message };
  }
};

// ==================== SYNC & STATUS ENDPOINTS ====================

/**
 * Fetch sync status (last sync time, type, scheduler status)
 */
export const fetchSyncStatus = async () => {
  try {
    // Canonical universal sync-status is a single endpoint on the shared
    // Ferrari architecture. Fetch once and surface under both legacy keys
    // for backwards compatibility with existing UI consumers.
    const response = await apiClient.get('/v3/sync-status').catch(() => ({ data: {} }));
    return {
      success: true,
      ...(response.data || {}),
    };
  } catch (error) {
    console.error('[DataService] fetchSyncStatus error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Trigger a full sync (manual refresh) via the canonical universal
 * master-sync endpoint. Per-sport — NBA/MLB follow the same contract.
 */
export const triggerFullSync = async (sport = 'nba') => {
  try {
    const path = sport === 'mlb' ? '/mlb/sync/master' : '/nba/sync/master';
    const response = await apiClient.post(path, {}, { timeout: 300000 });
    return { success: true, ...response.data };
  } catch (error) {
    console.error('[DataService] triggerFullSync error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Trigger a delta refresh (odds only) via the canonical universal
 * priority-refresh endpoint.
 */
export const triggerDeltaRefresh = async () => {
  try {
    const response = await apiClient.post('/v3/priority-refresh', {}, { timeout: 120000 });
    return { success: true, ...response.data };
  } catch (error) {
    console.error('[DataService] triggerDeltaRefresh error:', error);
    return { success: false, error: error.message };
  }
};

// ==================== LIVE DATA ENDPOINTS ====================

/**
 * Fetch live scores
 */
export const fetchLiveScores = async () => {
  try {
    const response = await apiClient.get('/v3/live-scores');
    return { success: true, games: response.data?.games || [] };
  } catch (error) {
    console.error('[DataService] fetchLiveScores error:', error);
    return { success: false, games: [], error: error.message };
  }
};

/**
 * Fetch lock status (games in progress)
 */
export const fetchLockStatus = async () => {
  try {
    const response = await apiClient.get('/v3/lock-status');
    return { success: true, ...response.data };
  } catch (error) {
    console.error('[DataService] fetchLockStatus error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Fetch T-minus games (games starting within 15 minutes)
 */
export const fetchTMinusGames = async () => {
  try {
    const response = await apiClient.get('/v3/t-minus-games');
    return { success: true, games: response.data?.games || [], count: response.data?.count || 0 };
  } catch (error) {
    console.error('[DataService] fetchTMinusGames error:', error);
    return { success: false, games: [], count: 0, error: error.message };
  }
};

// ==================== PLAYER DATA ENDPOINTS ====================

/**
 * Fetch player by name from cached board
 * Photos & stats come from nba_master_hub_2026 via player_id
 */
export const fetchCachedPlayer = async (playerName) => {
  try {
    const response = await apiClient.get(`/v3/player-with-badges/${encodeURIComponent(playerName)}`);
    return { success: true, player: response.data };
  } catch (error) {
    console.error('[DataService] fetchCachedPlayer error:', error);
    return { success: false, player: null, error: error.message };
  }
};

/**
 * Fetch player directly from Master Hub by player_id
 */
export const fetchPlayerFromHub = async (playerId) => {
  try {
    const response = await apiClient.get(`/v3/master-hub/player/${encodeURIComponent(playerId)}`);
    return { success: true, player: response.data };
  } catch (error) {
    console.error('[DataService] fetchPlayerFromHub error:', error);
    return { success: false, player: null, error: error.message };
  }
};

/**
 * Search players in Master Hub
 */
export const searchPlayersInHub = async (query) => {
  try {
    const response = await apiClient.get(`/v3/master-hub/search?q=${encodeURIComponent(query)}`);
    return { success: true, players: response.data?.players || [] };
  } catch (error) {
    console.error('[DataService] searchPlayersInHub error:', error);
    return { success: false, players: [], error: error.message };
  }
};

// ==================== SUPPLEMENTARY DATA ====================

/**
 * Fetch injury alerts
 */
export const fetchInjuryAlerts = async () => {
  try {
    const response = await apiClient.get('/v3/vacuum/live-alerts?sport=nba');
    return { success: true, alerts: response.data?.alerts || {} };
  } catch (error) {
    console.error('[DataService] fetchInjuryAlerts error:', error);
    return { success: false, alerts: {}, error: error.message };
  }
};

/**
 * Fetch breaking news
 */
export const fetchBreakingNews = async () => {
  try {
    const response = await apiClient.get('/v3/breaking-news');
    return { success: true, news: response.data?.news || [] };
  } catch (error) {
    console.error('[DataService] fetchBreakingNews error:', error);
    return { success: false, news: [], error: error.message };
  }
};

/**
 * Fetch social signals
 */
export const fetchSocialSignals = async () => {
  try {
    const response = await apiClient.get('/v3/social-signals');
    return { success: true, signals: response.data?.signals || {} };
  } catch (error) {
    console.error('[DataService] fetchSocialSignals error:', error);
    return { success: false, signals: {}, error: error.message };
  }
};

/**
 * Fetch scouting projections (early bird data)
 */
export const fetchScoutingProjections = async () => {
  try {
    const response = await apiClient.get('/v3/scouting-projections');
    return { success: true, projections: response.data?.projections || [] };
  } catch (error) {
    console.error('[DataService] fetchScoutingProjections error:', error);
    return { success: false, projections: [], error: error.message };
  }
};

// ==================== HYDRATED BOARD (FULL DATA) ====================

/**
 * Fetch hydrated board — DEPRECATED.
 *
 * The legacy `/v3/hydrated-board` endpoint no longer exists on the
 * universal Ferrari architecture. Enriched player data is already
 * delivered on every tier response (`/v3/ferrari/safe-haven`,
 * `/v3/ferrari/front-lines`, `/v3/ferrari/war-zone`) — callers should
 * consume those directly.
 *
 * This shim remains to preserve the DataService surface; it returns an
 * empty success payload so existing consumers degrade gracefully.
 */
export const fetchHydratedBoard = async () => {
  return { success: true, players: [] };
};

// ==================== ROSTER ENDPOINTS (Semantic Separation) ====================

/**
 * Fetch full active NBA roster (~430-450 players)
 * Complete roster from Master Hub - all active NBA players
 * Use for: player search, complete league view, pre-game research
 */
export const fetchFullActiveRoster = async (options = {}) => {
  try {
    const { team, position, limit = 500, offset = 0 } = options;
    const params = new URLSearchParams();
    if (team) params.append('team', team);
    if (position) params.append('position', position);
    if (limit) params.append('limit', limit);
    if (offset) params.append('offset', offset);
    
    const response = await apiClient.get(`/roster/full-active?${params.toString()}`);
    return { 
      success: true, 
      players: response.data?.players || [],
      total: response.data?.total || 0,
      count: response.data?.count || 0
    };
  } catch (error) {
    console.error('[DataService] fetchFullActiveRoster error:', error);
    return { success: false, players: [], total: 0, count: 0, error: error.message };
  }
};

/**
 * Fetch mapped/supported roster
 * Players with full system support (BDL mapping + baseline stats)
 * Use for: analytics-ready players, coverage monitoring
 */
export const fetchMappedRoster = async (options = {}) => {
  try {
    const { team, limit = 500, offset = 0 } = options;
    const params = new URLSearchParams();
    if (team) params.append('team', team);
    if (limit) params.append('limit', limit);
    if (offset) params.append('offset', offset);
    
    const response = await apiClient.get(`/roster/mapped?${params.toString()}`);
    return { 
      success: true, 
      players: response.data?.players || [],
      total: response.data?.total || 0,
      count: response.data?.count || 0,
      coveragePercent: response.data?.coverage_percent || 0
    };
  } catch (error) {
    console.error('[DataService] fetchMappedRoster error:', error);
    return { success: false, players: [], total: 0, count: 0, error: error.message };
  }
};

/**
 * Fetch live/today roster (~100-200 players)
 * Players with active props available for betting today
 * Use for: live odds board, today's playable slate
 */
export const fetchLiveTodayRoster = async (options = {}) => {
  try {
    const { team, hasProps = true, limit = 200, offset = 0 } = options;
    const params = new URLSearchParams();
    if (team) params.append('team', team);
    params.append('has_props', hasProps);
    if (limit) params.append('limit', limit);
    if (offset) params.append('offset', offset);
    
    const response = await apiClient.get(`/roster/live-today?${params.toString()}`);
    return { 
      success: true, 
      players: response.data?.players || [],
      total: response.data?.total || 0,
      count: response.data?.count || 0,
      propsBreakdown: response.data?.props_breakdown || {},
      lastSync: response.data?.last_sync
    };
  } catch (error) {
    console.error('[DataService] fetchLiveTodayRoster error:', error);
    return { success: false, players: [], total: 0, count: 0, error: error.message };
  }
};

/**
 * Fetch roster status summary (counts for all roster types)
 * Use for: dashboard overview, health monitoring
 */
export const fetchRosterStatus = async () => {
  try {
    const response = await apiClient.get('/roster/status');
    return { success: true, ...response.data };
  } catch (error) {
    console.error('[DataService] fetchRosterStatus error:', error);
    return { success: false, error: error.message };
  }
};

// ==================== EXPORTS ====================

const DataService = {
  // Core data
  fetchDashboardData,
  fetchWarZone,
  fetchGoblinVault,
  fetchParlayBuilder,
  fetchGoblinRecon,
  
  // Sync & status
  fetchSyncStatus,
  triggerFullSync,
  triggerDeltaRefresh,
  
  // Live data
  fetchLiveScores,
  fetchLockStatus,
  fetchTMinusGames,
  
  // Player data
  fetchCachedPlayer,
  fetchPlayerFromHub,
  searchPlayersInHub,
  
  // Supplementary
  fetchInjuryAlerts,
  fetchBreakingNews,
  fetchSocialSignals,
  fetchScoutingProjections,
  
  // Full board
  fetchHydratedBoard,
  
  // Roster (Semantic Endpoints)
  fetchFullActiveRoster,
  fetchMappedRoster,
  fetchLiveTodayRoster,
  fetchRosterStatus,
};

export default DataService;
