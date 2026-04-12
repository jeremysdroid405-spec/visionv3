/**
 * NBADashboard - NBA-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with NBA sport context pre-set.
 * Used for /nba route to provide direct NBA access without manual sport switching.
 */
import React, { useEffect } from 'react';
import { useSport } from '../context/SportContext';
import Dashboard from './Dashboard';

const NBADashboard = ({ isDemoMode = false }) => {
  const { currentSport, setSport } = useSport();
  
  // Set sport to NBA on mount if not already
  useEffect(() => {
    if (currentSport !== 'nba') {
      setSport('nba');
    }
  }, [currentSport, setSport]);
  
  // Wait for sport to be set before rendering
  if (currentSport !== 'nba') {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="animate-pulse text-amber-400 text-lg">Loading NBA Board...</div>
      </div>
    );
  }
  
  return <Dashboard isDemoMode={isDemoMode} forceSport="nba" />;
};

export default NBADashboard;
