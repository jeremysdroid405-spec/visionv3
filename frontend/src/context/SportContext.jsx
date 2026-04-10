/**
 * SportContext - Global Sport State Management
 * 
 * Manages the currently selected sport (NBA/MLB) across the entire app.
 * When sport changes, all components should clear data and refetch.
 */
import React, { createContext, useContext, useState, useCallback } from 'react';

const SportContext = createContext(null);

// Available sports configuration
export const SPORTS_CONFIG = {
  nba: {
    id: 'nba',
    name: 'NBA',
    fullName: 'Basketball',
    icon: '🏀',
    color: 'orange',
    bgGradient: 'from-orange-500/20 to-orange-600/10',
    borderColor: 'border-orange-500/30',
    textColor: 'text-orange-400',
    enabled: true
  },
  mlb: {
    id: 'mlb',
    name: 'MLB',
    fullName: 'Baseball',
    icon: '⚾',
    color: 'red',
    bgGradient: 'from-red-500/20 to-red-600/10',
    borderColor: 'border-red-500/30',
    textColor: 'text-red-400',
    enabled: false  // Coming soon
  }
};

export const SportProvider = ({ children }) => {
  const [currentSport, setCurrentSport] = useState('nba');
  const [isTransitioning, setIsTransitioning] = useState(false);

  // Switch sport with transition state for loading indicators
  const switchSport = useCallback((newSport) => {
    if (newSport === currentSport) return;
    if (!SPORTS_CONFIG[newSport]?.enabled) {
      console.warn(`Sport ${newSport} is not enabled yet`);
      return;
    }
    
    setIsTransitioning(true);
    setCurrentSport(newSport);
    
    // Clear transition state after a short delay to allow components to show loading
    setTimeout(() => {
      setIsTransitioning(false);
    }, 100);
  }, [currentSport]);

  const sportConfig = SPORTS_CONFIG[currentSport];

  const value = {
    currentSport,
    switchSport,
    isTransitioning,
    sportConfig,
    availableSports: Object.values(SPORTS_CONFIG).filter(s => s.enabled)
  };

  return (
    <SportContext.Provider value={value}>
      {children}
    </SportContext.Provider>
  );
};

export const useSport = () => {
  const context = useContext(SportContext);
  if (!context) {
    throw new Error('useSport must be used within a SportProvider');
  }
  return context;
};

export default SportContext;
