/**
 * NBADashboard - NBA-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with NBA sport context pre-set.
 * Used for /nba route to provide direct NBA access without manual sport switching.
 * Includes live injury polling for real-time injury updates.
 */
import React, { useEffect, useRef } from 'react';
import { useSport } from '../context/SportContext';
import { useLiveInjuries } from '../hooks/useLiveInjuries';
import Dashboard from './Dashboard';

const NBADashboard = ({ isDemoMode = false }) => {
  const { currentSport, switchSport } = useSport();
  const hasSetSport = useRef(false);
  
  // Live injury polling - runs every 30 seconds
  const { data: liveInjuries } = useLiveInjuries({ 
    sport: 'nba',
    enabled: true 
  });
  
  // Set sport to NBA on mount only once
  useEffect(() => {
    if (!hasSetSport.current && currentSport !== 'nba') {
      hasSetSport.current = true;
      switchSport('nba');
    }
  }, []); // Empty deps - run once on mount
  
  // Log injury updates for debugging
  useEffect(() => {
    if (liveInjuries?.total > 0) {
      console.log(`[NBA] Live injuries: ${liveInjuries.total} (${liveInjuries.high_risk?.length || 0} OUT)`);
    }
  }, [liveInjuries]);
  
  return <Dashboard isDemoMode={isDemoMode} liveInjuries={liveInjuries} />;
};

export default NBADashboard;
