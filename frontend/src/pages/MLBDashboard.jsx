/**
 * MLBDashboard - MLB-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with MLB sport context pre-set.
 * Used for /mlb route to provide direct MLB access without manual sport switching.
 */
import React, { useEffect, useRef } from 'react';
import { useSport } from '../context/SportContext';
import Dashboard from './Dashboard';

const MLBDashboard = ({ isDemoMode = false }) => {
  const { currentSport, switchSport } = useSport();
  const hasSetSport = useRef(false);
  
  // Set sport to MLB on mount only once
  useEffect(() => {
    if (!hasSetSport.current && currentSport !== 'mlb') {
      hasSetSport.current = true;
      switchSport('mlb');
    }
  }, []); // Empty deps - run once on mount
  
  return <Dashboard isDemoMode={isDemoMode} />;
};

export default MLBDashboard;
