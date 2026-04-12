/**
 * NBADashboard - NBA-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with NBA sport context pre-set.
 * Used for /nba route to provide direct NBA access without manual sport switching.
 */
import React, { useEffect, useRef } from 'react';
import { useSport } from '../context/SportContext';
import Dashboard from './Dashboard';

const NBADashboard = ({ isDemoMode = false }) => {
  const { currentSport, switchSport } = useSport();
  const hasSetSport = useRef(false);
  
  // Set sport to NBA on mount only once
  useEffect(() => {
    if (!hasSetSport.current && currentSport !== 'nba') {
      hasSetSport.current = true;
      switchSport('nba');
    }
  }, []); // Empty deps - run once on mount
  
  return <Dashboard isDemoMode={isDemoMode} />;
};

export default NBADashboard;
