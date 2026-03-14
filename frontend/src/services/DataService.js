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
      boardIntelStatus,
      liveScores,
      lockStatus,
    ] = await Promise.all([
      apiClient.get('/v3/war-zone').catch(() => ({ data: { picks: [] } })),
      apiClient.get('/v3/goblin-vault').catch(() => ({ data: { picks: [] } })),
      apiClient.get('/v3/parlay-builder').catch(() => ({ data: { demon_parlays: {}, goblin_parlays: {} } })),
      apiClient.get('/v3/goblin-recon').catch(() => ({ data: { parlays: {} } })),
      apiClient.get('/v3/sync-status').catch(() => ({ data: { engine_status: 'offline' } })),
      apiClient.get('/v3/board-intel/status').catch(() => ({ data: { time_since_sync_display: 'N/A' } })),
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
        boardIntelStatus: boardIntelStatus.data || {},
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
    const response = await apiClient.get('/v3/war-zone');
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
    const response = await apiClient.get('/v3/goblin-vault');
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
    const [syncStatus, boardIntelStatus] = await Promise.all([
      apiClient.get('/v3/sync-status').catch(() => ({ data: {} })),
      apiClient.get('/v3/board-intel/status').catch(() => ({ data: {} })),
    ]);

    return {
      success: true,
      ...syncStatus.data,
      ...boardIntelStatus.data,
    };
  } catch (error) {
    console.error('[DataService] fetchSyncStatus error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Trigger a full sync (manual refresh)
 */
export const triggerFullSync = async () => {
  try {
    const response = await apiClient.post('/v3/sync', {}, { timeout: 300000 });
    return { success: true, ...response.data };
  } catch (error) {
    console.error('[DataService] triggerFullSync error:', error);
    return { success: false, error: error.message };
  }
};

/**
 * Trigger a delta refresh (odds only)
 */
export const triggerDeltaRefresh = async () => {
  try {
    const response = await apiClient.post('/v3/board-intel/delta-refresh', {}, { timeout: 120000 });
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
    const response = await apiClient.get(`/v3/cached-player/${encodeURIComponent(playerName)}`);
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
    const response = await apiClient.get('/v3/injuries/alerts');
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
 * Fetch hydrated board with all player data enriched
 */
export const fetchHydratedBoard = async () => {
  try {
    const response = await apiClient.get('/v3/hydrated-board');
    return { success: true, players: response.data?.players || response.data || [] };
  } catch (error) {
    console.error('[DataService] fetchHydratedBoard error:', error);
    return { success: false, players: [], error: error.message };
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
};

export default DataService;
