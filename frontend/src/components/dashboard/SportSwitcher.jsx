/**
 * SportSwitcher - Global Sport Toggle Component
 * 
 * Displays in the header, allows switching between NBA and MLB.
 * Shows loading state during transition.
 * Navigates to sport-specific routes (/nba, /mlb) for clean URL state.
 */
import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useSport, SPORTS_CONFIG } from '../../context/SportContext';
import { ChevronDown, Check, Lock } from 'lucide-react';

const SportSwitcher = () => {
  const { currentSport, switchSport, isTransitioning, sportConfig, availableSports } = useSport();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);
  const navigate = useNavigate();
  const location = useLocation();

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSportSelect = (sportId) => {
    const sport = SPORTS_CONFIG[sportId];
    if (!sport.enabled) return;
    
    // Switch the sport context
    switchSport(sportId);
    setIsOpen(false);
    
    // Navigate to sport-specific route if on a dashboard page
    const isDashboardPage = location.pathname.includes('/dashboard') || 
                           location.pathname === '/nba' || 
                           location.pathname === '/mlb' ||
                           location.pathname.includes('/demo');
    
    if (isDashboardPage) {
      // Check if we're in demo mode
      const isDemo = location.pathname.includes('/demo');
      
      if (isDemo) {
        navigate(`/demo/${sportId}`);
      } else {
        navigate(`/${sportId}`);
      }
    }
  };

  return (
    <div className="relative" ref={dropdownRef} data-testid="sport-switcher">
      {/* Current Sport Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={isTransitioning}
        className={`
          flex items-center gap-2 px-3 py-1.5 rounded-lg
          bg-gradient-to-r ${sportConfig.bgGradient}
          border ${sportConfig.borderColor}
          hover:border-opacity-60 transition-all duration-200
          ${isTransitioning ? 'opacity-50 cursor-wait' : 'cursor-pointer'}
        `}
        data-testid="sport-switcher-button"
      >
        <span className="text-lg">{sportConfig.icon}</span>
        <span className={`font-bold text-sm ${sportConfig.textColor}`}>
          {sportConfig.name}
        </span>
        <ChevronDown 
          className={`w-4 h-4 ${sportConfig.textColor} transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} 
        />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div 
          className="absolute top-full left-0 mt-2 w-48 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 overflow-hidden"
          data-testid="sport-switcher-dropdown"
        >
          {Object.values(SPORTS_CONFIG).map((sport) => (
            <button
              key={sport.id}
              onClick={() => handleSportSelect(sport.id)}
              disabled={!sport.enabled}
              className={`
                w-full flex items-center gap-3 px-4 py-3
                ${sport.enabled 
                  ? 'hover:bg-zinc-800 cursor-pointer' 
                  : 'opacity-50 cursor-not-allowed'
                }
                ${currentSport === sport.id ? 'bg-zinc-800' : ''}
                transition-colors duration-150
              `}
              data-testid={`sport-option-${sport.id}`}
            >
              <span className="text-xl">{sport.icon}</span>
              <div className="flex-1 text-left">
                <div className={`font-semibold text-sm ${sport.enabled ? 'text-white' : 'text-zinc-500'}`}>
                  {sport.name}
                </div>
                <div className="text-[10px] text-zinc-500">
                  {sport.fullName}
                </div>
              </div>
              {currentSport === sport.id && sport.enabled && (
                <Check className="w-4 h-4 text-green-400" />
              )}
              {!sport.enabled && (
                <div className="flex items-center gap-1 text-[10px] text-zinc-500">
                  <Lock className="w-3 h-3" />
                  <span>Soon</span>
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default SportSwitcher;
