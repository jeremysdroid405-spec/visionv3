/**
 * MLBDashboard - MLB-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with MLB sport context pre-set.
 * Used for /mlb route to provide direct MLB access without manual sport switching.
 * Includes live injury polling for real-time injury updates.
 */
import React, { useEffect, useRef } from 'react';
import { useSport } from '../context/SportContext';
import { useLiveInjuries } from '../hooks/useLiveInjuries';
import Dashboard from './Dashboard';

const MLBDashboard = ({ isDemoMode = false }) => {
  const { currentSport, switchSport } = useSport();
  const hasSetSport = useRef(false);
  
  // Live injury polling - runs every 30 seconds
  const { data: liveInjuries } = useLiveInjuries({ 
    sport: 'mlb',
    enabled: true 
  });
  
  // Set sport to MLB on mount only once
  useEffect(() => {
    if (!hasSetSport.current && currentSport !== 'mlb') {
      hasSetSport.current = true;
      switchSport('mlb');
    }
  }, []); // Empty deps - run once on mount
  
  // Log injury updates for debugging
  useEffect(() => {
    if (liveInjuries?.total > 0) {
      console.log(`[MLB] Live injuries: ${liveInjuries.total} (${liveInjuries.high_risk?.length || 0} OUT/IL)`);
    }
  }, [liveInjuries]);
  
  return <Dashboard isDemoMode={isDemoMode} liveInjuries={liveInjuries} />;
};

export default MLBDashboard;
