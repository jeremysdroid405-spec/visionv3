/**
 * useLiveInjuries Hook
 * 
 * High-frequency polling hook for live injury updates.
 * Polls the /api/v3/injuries/live endpoint every 30-60 seconds.
 * 
 * This is decoupled from main data fetching to ensure injury updates
 * are reflected immediately in the UI without waiting for full board refresh.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSport } from '../context/SportContext';

const API_URL = process.env.REACT_APP_BACKEND_URL || '';

// Polling interval in milliseconds (30 seconds)
const INJURY_POLL_INTERVAL = 30 * 1000;

/**
 * Fetch live injuries from the micro-sync cache
 */
const fetchLiveInjuries = async (sport) => {
  const url = sport 
    ? `${API_URL}/api/v3/injuries/live?sport=${sport}`
    : `${API_URL}/api/v3/injuries/live`;
  
  const response = await fetch(url);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch live injuries: ${response.status}`);
  }
  
  return response.json();
};

/**
 * Hook to get live injury data with automatic polling
 * 
 * @param {Object} options
 * @param {boolean} options.enabled - Whether polling is enabled (default: true)
 * @param {number} options.refetchInterval - Override polling interval in ms
 * @param {string} options.sport - Filter by sport (null for all)
 * @returns {Object} Query result with injury data
 */
export const useLiveInjuries = (options = {}) => {
  const { currentSport } = useSport();
  const sport = options.sport ?? currentSport;
  
  const {
    enabled = true,
    refetchInterval = INJURY_POLL_INTERVAL,
  } = options;
  
  return useQuery({
    queryKey: ['live-injuries', sport],
    queryFn: () => fetchLiveInjuries(sport),
    enabled,
    refetchInterval: enabled ? refetchInterval : false,
    refetchIntervalInBackground: true, // Keep polling even when tab is not focused
    staleTime: 15 * 1000, // Consider data stale after 15 seconds
    gcTime: 60 * 1000, // Keep in cache for 60 seconds
    retry: 2,
    retryDelay: 5000,
  });
};

/**
 * Hook to check if a specific player is injured
 * 
 * @param {string} playerName - Player name to check
 * @returns {Object|null} Injury info if injured, null if healthy
 */
export const usePlayerInjuryStatus = (playerName) => {
  const { data: injuries } = useLiveInjuries();
  
  if (!injuries || !playerName) return null;
  
  const allInjuries = [
    ...(injuries.high_risk || []),
    ...(injuries.medium_risk || [])
  ];
  
  return allInjuries.find(
    inj => inj.player_name?.toLowerCase() === playerName.toLowerCase()
  ) || null;
};

/**
 * Hook to get injury counts for display badges
 * 
 * @returns {Object} Counts by risk level
 */
export const useInjuryCounts = () => {
  const { data: injuries, isLoading } = useLiveInjuries();
  
  if (isLoading || !injuries) {
    return { highRisk: 0, mediumRisk: 0, total: 0, loading: true };
  }
  
  return {
    highRisk: injuries.high_risk?.length || 0,
    mediumRisk: injuries.medium_risk?.length || 0,
    total: injuries.total || 0,
    lastSync: injuries.last_sync,
    loading: false
  };
};

/**
 * Hook to manually trigger an injury sync
 */
export const useTriggerInjurySync = () => {
  const queryClient = useQueryClient();
  
  const triggerSync = async () => {
    try {
      const response = await fetch(`${API_URL}/api/v3/injuries/live/sync`, {
        method: 'POST'
      });
      
      if (response.ok) {
        // Invalidate queries to refresh data
        queryClient.invalidateQueries({ queryKey: ['live-injuries'] });
        return await response.json();
      }
      throw new Error('Sync failed');
    } catch (error) {
      console.error('[INJURY] Manual sync failed:', error);
      throw error;
    }
  };
  
  return { triggerSync };
};

export default useLiveInjuries;
