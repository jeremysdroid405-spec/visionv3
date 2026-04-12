/**
 * MLBDashboard - MLB-specific dashboard wrapper
 * 
 * This component wraps the main Dashboard with MLB sport context pre-set.
 * Used for /mlb route to provide direct MLB access without manual sport switching.
 */
import React, { useEffect } from 'react';
import { useSport } from '../context/SportContext';
import Dashboard from './Dashboard';

const MLBDashboard = ({ isDemoMode = false }) => {
  const { currentSport, setSport } = useSport();
  
  // Set sport to MLB on mount if not already
  useEffect(() => {
    if (currentSport !== 'mlb') {
      setSport('mlb');
    }
  }, [currentSport, setSport]);
  
  // Wait for sport to be set before rendering
  if (currentSport !== 'mlb') {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="animate-pulse text-green-400 text-lg">Loading MLB Board...</div>
      </div>
    );
  }
  
  return <Dashboard isDemoMode={isDemoMode} forceSport="mlb" />;
};

export default MLBDashboard;
