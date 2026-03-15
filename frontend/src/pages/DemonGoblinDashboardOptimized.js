import React, { useState, useEffect, useCallback, memo, useMemo, useRef } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { 
  Activity, RefreshCw, Search, Database, 
  ChevronDown, ChevronRight, ChevronLeft, AlertTriangle,
  User, Flame, Star, Clock, Zap, HardDrive, ArrowLeft, X,
  DollarSign, TrendingUp, Target, Layers, CheckCircle, XCircle,
  LogOut, Crown, Eye, Radio, Brain, Shield
} from 'lucide-react';
import { toast } from 'sonner';

// Import from refactored components
import { DemonIcon, GoblinIcon, VisionBadge } from '../components/dashboard/Icons';
import { 
  API, 
  NBA_HEADSHOT_URL, 
  TEAM_LOGOS
} from '../components/dashboard/constants';

// ==================== BEACON GLOW CSS ====================
// Injected CSS for the infinite pulse animation
const BeaconGlowStyles = () => (
  <style>{`
    /* ==================== WAR ZONE - Gold/Orange Beacon ==================== */
    @keyframes beacon-glow-pulse {
      0% { 
        box-shadow: 0 0 5px #FFD700, 0 0 10px rgba(255, 215, 0, 0.3); 
        border-color: #FFD700; 
      }
      50% { 
        box-shadow: 0 0 20px #FF4500, 0 0 40px rgba(255, 69, 0, 0.4); 
        border-color: #FF4500; 
      }
      100% { 
        box-shadow: 0 0 5px #FFD700, 0 0 10px rgba(255, 215, 0, 0.3); 
        border-color: #FFD700; 
      }
    }
    
    .beacon-glow {
      animation: beacon-glow-pulse 2s ease-in-out infinite;
      border-width: 2px;
      border-style: solid;
    }
    
    .beacon-glow-subtle {
      animation: beacon-glow-pulse 3s ease-in-out infinite;
      opacity: 0.9;
    }
    
    /* ==================== GOBLIN RECON - Emerald Green Beacon ==================== */
    @keyframes emerald-glow-pulse {
      0% { 
        box-shadow: 0 0 5px #90EE90, 0 0 10px rgba(144, 238, 144, 0.3); 
        border-color: #90EE90; 
      }
      50% { 
        box-shadow: 0 0 20px #228B22, 0 0 40px rgba(34, 139, 34, 0.5); 
        border-color: #228B22; 
      }
      100% { 
        box-shadow: 0 0 5px #90EE90, 0 0 10px rgba(144, 238, 144, 0.3); 
        border-color: #90EE90; 
      }
    }
    
    .emerald-glow {
      animation: emerald-glow-pulse 2s ease-in-out infinite;
      border-width: 2px;
      border-style: solid;
    }
    
    .emerald-glow-subtle {
      animation: emerald-glow-pulse 3s ease-in-out infinite;
      opacity: 0.9;
    }
    
    /* ==================== SECTION BACKGROUND GLOWS ==================== */
    .war-zone-section {
      position: relative;
      background: linear-gradient(135deg, rgba(220, 38, 38, 0.15) 0%, rgba(15, 15, 15, 0.95) 50%, rgba(220, 38, 38, 0.1) 100%);
      border-radius: 16px;
      padding: 16px;
      border: 1px solid rgba(239, 68, 68, 0.4);
      box-shadow: 0 0 30px rgba(239, 68, 68, 0.2), inset 0 0 60px rgba(239, 68, 68, 0.05);
    }
    
    .war-zone-section::before {
      content: '';
      position: absolute;
      inset: -4px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(239, 68, 68, 0.4), transparent 40%, transparent 60%, rgba(239, 68, 68, 0.3));
      filter: blur(12px);
      z-index: -1;
      animation: radar-pulse 2.5s ease-in-out infinite;
    }
    
    @keyframes radar-pulse {
      0%, 100% { opacity: 0.6; transform: scale(1); }
      50% { opacity: 1; transform: scale(1.01); }
    }
    
    .goblin-recon-section {
      position: relative;
      background: linear-gradient(135deg, rgba(34, 197, 94, 0.15) 0%, rgba(15, 15, 15, 0.95) 50%, rgba(34, 197, 94, 0.1) 100%);
      border-radius: 16px;
      padding: 16px;
      border: 1px solid rgba(34, 197, 94, 0.4);
      box-shadow: 0 0 30px rgba(34, 197, 94, 0.2), inset 0 0 60px rgba(34, 197, 94, 0.05);
    }
    
    .goblin-recon-section::before {
      content: '';
      position: absolute;
      inset: -4px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(34, 197, 94, 0.4), transparent 40%, transparent 60%, rgba(34, 197, 94, 0.3));
      filter: blur(12px);
      z-index: -1;
      animation: recon-pulse 2.5s ease-in-out infinite;
    }
    
    @keyframes recon-pulse {
      0%, 100% { opacity: 0.6; transform: scale(1); }
      50% { opacity: 1; transform: scale(1.01); }
    }
    
    /* ==================== FRONT LINES - Tactical Amber/Yellow ==================== */
    @keyframes amber-glow-pulse {
      0% { 
        box-shadow: 0 0 5px #FCD34D, 0 0 10px rgba(252, 211, 77, 0.3); 
        border-color: #FCD34D; 
      }
      50% { 
        box-shadow: 0 0 20px #F59E0B, 0 0 40px rgba(245, 158, 11, 0.5); 
        border-color: #F59E0B; 
      }
      100% { 
        box-shadow: 0 0 5px #FCD34D, 0 0 10px rgba(252, 211, 77, 0.3); 
        border-color: #FCD34D; 
      }
    }
    
    .front-lines-section {
      position: relative;
      background: linear-gradient(135deg, rgba(245, 158, 11, 0.15) 0%, rgba(15, 15, 15, 0.95) 50%, rgba(245, 158, 11, 0.1) 100%);
      border-radius: 16px;
      padding: 16px;
      border: 1px solid rgba(245, 158, 11, 0.4);
      box-shadow: 0 0 30px rgba(245, 158, 11, 0.2), inset 0 0 60px rgba(245, 158, 11, 0.05);
    }
    
    .front-lines-section::before {
      content: '';
      position: absolute;
      inset: -4px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(245, 158, 11, 0.4), transparent 40%, transparent 60%, rgba(245, 158, 11, 0.3));
      filter: blur(12px);
      z-index: -1;
      animation: frontlines-pulse 2.5s ease-in-out infinite;
    }
    
    @keyframes frontlines-pulse {
      0%, 100% { opacity: 0.6; transform: scale(1); }
      50% { opacity: 1; transform: scale(1.01); }
    }
    
    .amber-glow {
      animation: amber-glow-pulse 2s ease-in-out infinite;
      border-width: 2px;
      border-style: solid;
    }
    
    .front-lines-card-glow {
      background: linear-gradient(135deg, rgba(120, 80, 10, 0.6) 0%, rgba(24, 24, 27, 0.95) 100%) !important;
      border: 1px solid rgba(245, 158, 11, 0.5) !important;
      box-shadow: 0 0 20px rgba(245, 158, 11, 0.3), 0 0 40px rgba(245, 158, 11, 0.1) !important;
    }
    
    /* ==================== CARD GLOW EFFECTS ==================== */
    .demon-card-glow {
      background: linear-gradient(135deg, rgba(127, 29, 29, 0.6) 0%, rgba(24, 24, 27, 0.95) 100%) !important;
      border: 1px solid rgba(239, 68, 68, 0.5) !important;
      box-shadow: 0 0 20px rgba(239, 68, 68, 0.3), 0 0 40px rgba(239, 68, 68, 0.1) !important;
    }
    
    .goblin-card-glow {
      background: linear-gradient(135deg, rgba(20, 83, 45, 0.6) 0%, rgba(24, 24, 27, 0.95) 100%) !important;
      border: 1px solid rgba(34, 197, 94, 0.5) !important;
      box-shadow: 0 0 20px rgba(34, 197, 94, 0.3), 0 0 40px rgba(34, 197, 94, 0.1) !important;
    }
    
    /* ==================== MOBILE SWIPE CARDS ==================== */
    .swipe-container {
      display: flex !important;
      flex-direction: row !important;
      overflow-x: auto !important;
      scroll-snap-type: x mandatory;
      scroll-behavior: smooth;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
      -ms-overflow-style: none;
      gap: 12px;
      padding: 4px 16px;
    }
    
    .swipe-container::-webkit-scrollbar {
      display: none;
    }
    
    .swipe-card {
      scroll-snap-align: center;
      flex-shrink: 0 !important;
      width: calc(100vw - 48px) !important;
      max-width: 340px !important;
      min-width: calc(100vw - 48px) !important;
    }
    
    @media (min-width: 640px) {
      .swipe-container {
        display: grid !important;
        grid-template-columns: repeat(2, 1fr) !important;
        overflow-x: visible !important;
        scroll-snap-type: none;
        padding: 0;
        gap: 8px;
      }
      
      .swipe-card {
        width: auto !important;
        max-width: none !important;
        min-width: 0 !important;
      }
    }
    
    @media (min-width: 1024px) {
      .swipe-container {
        grid-template-columns: repeat(3, 1fr) !important;
      }
    }
    
    @media (min-width: 1280px) {
      .swipe-container {
        grid-template-columns: repeat(4, 1fr) !important;
      }
    }
    
    @media (min-width: 1536px) {
      .swipe-container {
        grid-template-columns: repeat(5, 1fr) !important;
      }
    }
  `}</style>
);

// ==================== SWIPE INDICATOR COMPONENT ====================
const SwipeIndicator = memo(({ current, total, accentColor = 'orange' }) => {
  const colorClasses = {
    orange: 'bg-orange-500',
    green: 'bg-emerald-500',
    blue: 'bg-blue-500',
    purple: 'bg-purple-500'
  };
  
  return (
    <div className="flex items-center justify-center gap-2 mt-3 sm:hidden">
      <span className="text-xs text-zinc-500">{current} / {total}</span>
      <div className="flex gap-1">
        {Array.from({ length: Math.min(total, 5) }).map((_, i) => (
          <div
            key={i}
            className={`w-1.5 h-1.5 rounded-full transition-all ${
              i === Math.min(current - 1, 4)
                ? `${colorClasses[accentColor]} scale-125`
                : 'bg-zinc-600'
            }`}
          />
        ))}
        {total > 5 && <span className="text-zinc-600 text-xs">...</span>}
      </div>
    </div>
  );
});

// ==================== SWIPE HINT COMPONENT ====================
const SwipeHint = memo(({ show, accentColor = 'orange' }) => {
  const [visible, setVisible] = useState(true);
  
  useEffect(() => {
    if (!show) {
      // Fade out after user has swiped
      const timer = setTimeout(() => setVisible(false), 300);
      return () => clearTimeout(timer);
    }
  }, [show]);
  
  if (!visible) return null;
  
  const colorClasses = {
    orange: 'text-orange-400 border-orange-500/30',
    green: 'text-emerald-400 border-emerald-500/30',
    blue: 'text-blue-400 border-blue-500/30',
    purple: 'text-purple-400 border-purple-500/30',
    amber: 'text-amber-400 border-amber-500/30'
  };
  
  return (
    <div 
      className={`absolute inset-0 pointer-events-none sm:hidden transition-opacity duration-500 ${show ? 'opacity-100' : 'opacity-0'}`}
    >
      {/* Left arrow hint */}
      <div className="absolute left-2 top-1/2 -translate-y-1/2 z-10">
        <div className={`w-8 h-8 rounded-full bg-black/60 backdrop-blur-sm flex items-center justify-center border ${colorClasses[accentColor]} animate-pulse`}>
          <ChevronLeft className={`w-5 h-5 ${colorClasses[accentColor].split(' ')[0]}`} />
        </div>
      </div>
      
      {/* Right arrow hint */}
      <div className="absolute right-2 top-1/2 -translate-y-1/2 z-10">
        <div className={`w-8 h-8 rounded-full bg-black/60 backdrop-blur-sm flex items-center justify-center border ${colorClasses[accentColor]} animate-pulse`}>
          <ChevronRight className={`w-5 h-5 ${colorClasses[accentColor].split(' ')[0]}`} />
        </div>
      </div>
      
      {/* Swipe text hint */}
      <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10">
        <div className={`px-3 py-1 rounded-full bg-black/70 backdrop-blur-sm border ${colorClasses[accentColor]} text-[10px] ${colorClasses[accentColor].split(' ')[0]} animate-pulse`}>
          ← Swipe to explore →
        </div>
      </div>
    </div>
  );
});

SwipeHint.displayName = 'SwipeHint';

// ==================== SWIPEABLE SECTION HOOK ====================
const SWIPE_HINT_KEY = 'pickvision_swipe_discovered';

const useSwipeTracker = (itemCount) => {
  const [currentIndex, setCurrentIndex] = useState(1);
  const [hasUserSwiped, setHasUserSwiped] = useState(() => {
    // Check localStorage to see if user has already discovered swipe
    if (typeof window !== 'undefined') {
      return localStorage.getItem(SWIPE_HINT_KEY) === 'true';
    }
    return false;
  });
  const containerRef = useRef(null);
  
  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const cardWidth = container.querySelector('.snap-center')?.offsetWidth || 300;
    const scrollLeft = container.scrollLeft;
    const newIndex = Math.round(scrollLeft / (cardWidth + 12)) + 1;
    setCurrentIndex(Math.max(1, Math.min(newIndex, itemCount)));
    
    // Mark as swiped if user has scrolled past first card
    if (scrollLeft > 50 && !hasUserSwiped) {
      setHasUserSwiped(true);
      localStorage.setItem(SWIPE_HINT_KEY, 'true');
    }
  }, [itemCount, hasUserSwiped]);
  
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    
    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);
  
  return { containerRef, currentIndex, showHint: !hasUserSwiped };
};


// ==================== PLAYER HEADSHOT COMPONENT ====================

const PlayerHeadshot = memo(({ nbaId, playerName, team, photoUrl, size = 'md', className = '' }) => {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  
  // Size classes
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-12 h-12',
    lg: 'w-16 h-16',
    xl: 'w-24 h-24',
  };
  
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  
  // Get team logo URL for fallback
  const teamLogoUrl = team ? TEAM_LOGOS[team] : null;
  
  // Determine the headshot URL to use (priority: photoUrl > NBA CDN > fallback)
  // photoUrl comes from ESPN via sync_player_photos
  // Check if photoUrl is a valid headshot (not a "nophoto" placeholder)
  const isValidPhotoUrl = photoUrl && 
    !photoUrl.includes('nophoto') && 
    !photoUrl.includes('placeholder');
  
  const headshotUrl = isValidPhotoUrl 
    ? photoUrl 
    : (nbaId ? NBA_HEADSHOT_URL(nbaId) : null);
  
  // If no valid headshot URL or image failed, show team logo or user icon
  if (!headshotUrl || error) {
    // GLOBAL FALLBACK: Show team logo instead of gray user icon
    if (teamLogoUrl) {
      return (
        <div 
          className={`
            ${sizeClass} rounded-full overflow-hidden flex-shrink-0
            bg-gradient-to-br from-zinc-800 to-zinc-900 flex items-center justify-center p-1.5
            ${className}
          `}
          title={playerName}
        >
          <img
            src={teamLogoUrl}
            alt={`${team} logo`}
            className="w-full h-full object-contain"
            onError={(e) => e.target.style.display = 'none'}
          />
        </div>
      );
    }
    
    // Final fallback: User icon
    return (
      <div 
        className={`
          ${sizeClass} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 
          flex items-center justify-center flex-shrink-0 ${className}
        `}
        title={playerName}
      >
        <User className={`${size === 'sm' ? 'w-4 h-4' : size === 'lg' ? 'w-8 h-8' : 'w-6 h-6'} text-zinc-500`} />
      </div>
    );
  }
  
  return (
    <div 
      className={`
        ${sizeClass} rounded-full overflow-hidden flex-shrink-0 
        bg-gradient-to-br from-zinc-700 to-zinc-800 ${className}
      `}
      title={playerName}
    >
      {!loaded && (
        <div className={`${sizeClass} animate-pulse bg-zinc-700 rounded-full`} />
      )}
      <img
        src={headshotUrl}
        alt={playerName}
        loading="lazy"
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
        className={`
          w-full h-full object-cover object-top
          ${loaded ? 'opacity-100' : 'opacity-0'}
          transition-opacity duration-300
        `}
        style={{ 
          objectPosition: 'center 20%',
          transform: 'scale(1.3)'  // Zoom in for face focus
        }}
      />
    </div>
  );
});

PlayerHeadshot.displayName = 'PlayerHeadshot';

// ==================== INJURY BADGE COMPONENT ====================

const InjuryBadge = memo(({ playerName, injuryAlerts, size = 'sm' }) => {
  const injury = injuryAlerts?.[playerName];
  
  if (!injury) return null;
  
  const isHighRisk = injury.severity === 'HIGH';
  const isMediumRisk = injury.severity === 'MEDIUM';
  
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-5 h-5',
    lg: 'w-6 h-6'
  };
  
  return (
    <div 
      className={`
        ${sizeClasses[size]} rounded-full flex items-center justify-center
        ${isHighRisk ? 'bg-red-500 animate-pulse' : isMediumRisk ? 'bg-yellow-500' : 'bg-green-500'}
        cursor-pointer relative group
      `}
      title={`${injury.status}: ${injury.description}`}
      data-testid={`injury-badge-${playerName.replace(/\s/g, '-')}`}
    >
      <AlertTriangle className={`${size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3'} text-white`} />
      
      {/* Tooltip on hover */}
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-48 p-2 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl opacity-0 group-hover:opacity-100 transition-opacity z-50 pointer-events-none">
        <div className="flex items-center gap-1 mb-1">
          <span className={`text-xs font-bold ${isHighRisk ? 'text-red-400' : isMediumRisk ? 'text-yellow-400' : 'text-green-400'}`}>
            {injury.status}
          </span>
        </div>
        <p className="text-[10px] text-zinc-400 line-clamp-3">{injury.description}</p>
      </div>
    </div>
  );
});

InjuryBadge.displayName = 'InjuryBadge';

// ==================== T-MINUS COUNTDOWN BADGE ====================

const TMinusBadge = memo(({ commenceTime, tMinusGames }) => {
  const [countdown, setCountdown] = useState(null);
  
  useEffect(() => {
    if (!commenceTime) return;
    
    // Find this game in tMinusGames to get the countdown
    const matchingGame = tMinusGames?.find(g => g.commence_time === commenceTime);
    if (matchingGame && matchingGame.t_minus_seconds > 0 && matchingGame.t_minus_seconds <= 900) {
      setCountdown(matchingGame.t_minus_seconds);
    } else {
      // Calculate manually if not in tMinusGames
      try {
        const gameTime = new Date(commenceTime);
        const now = new Date();
        const diffSeconds = Math.floor((gameTime - now) / 1000);
        
        if (diffSeconds > 0 && diffSeconds <= 900) {
          setCountdown(diffSeconds);
        } else {
          setCountdown(null);
        }
      } catch {
        setCountdown(null);
      }
    }
  }, [commenceTime, tMinusGames]);
  
  // Update countdown every second when active
  useEffect(() => {
    if (!countdown || countdown <= 0) return;
    
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    
    return () => clearInterval(timer);
  }, [countdown > 0]);
  
  if (!countdown || countdown <= 0) return null;
  
  const minutes = Math.floor(countdown / 60);
  const seconds = countdown % 60;
  const isUrgent = countdown <= 300; // Under 5 minutes
  const isCritical = countdown <= 60; // Under 1 minute
  
  return (
    <div 
      className={`
        absolute -top-2 -right-2 z-20 px-2 py-1 rounded-md text-[10px] font-mono font-bold
        ${isCritical 
          ? 'bg-red-500 text-white animate-pulse shadow-[0_0_10px_rgba(239,68,68,0.7)]' 
          : isUrgent 
            ? 'bg-orange-500 text-black shadow-[0_0_8px_rgba(249,115,22,0.5)]'
            : 'bg-yellow-500 text-black shadow-[0_0_5px_rgba(234,179,8,0.4)]'
        }
      `}
      title={`Game tips off in ${minutes}:${seconds.toString().padStart(2, '0')}`}
      data-testid="t-minus-badge"
    >
      T-{minutes}:{seconds.toString().padStart(2, '0')}
    </div>
  );
});

TMinusBadge.displayName = 'TMinusBadge';

// ==================== LOCKED BADGE - Game Started ====================

const LockedBadge = memo(({ isLocked, commenceTime }) => {
  const [gameStarted, setGameStarted] = useState(false);
  
  useEffect(() => {
    if (isLocked) {
      setGameStarted(true);
      return;
    }
    
    // Also check if game has started based on commence_time
    if (commenceTime) {
      try {
        const gameTime = new Date(commenceTime);
        const now = new Date();
        if (now >= gameTime) {
          setGameStarted(true);
        }
      } catch {
        // Ignore parsing errors
      }
    }
  }, [isLocked, commenceTime]);
  
  if (!gameStarted) return null;
  
  return (
    <div 
      className="absolute inset-0 z-30 bg-black/70 backdrop-blur-[2px] rounded-lg flex flex-col items-center justify-center"
      data-testid="locked-badge"
    >
      <div className="bg-red-600 text-white px-4 py-2 rounded-lg font-mono font-bold text-sm tracking-wider shadow-[0_0_20px_rgba(220,38,38,0.5)] flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
        LOCKED
      </div>
      <p className="text-zinc-400 text-xs mt-2 font-medium">Game In Progress</p>
    </div>
  );
});

LockedBadge.displayName = 'LockedBadge';

// ==================== SCOUTING BADGE - Projection Cards ====================
// Orange themed badge for players awaiting official lines
const ScoutingBadge = memo(({ isProjection, status }) => {
  if (!isProjection) return null;
  
  return (
    <div className="absolute top-2 right-2 z-10">
      <div 
        className="badge-scouting px-2 py-1 rounded-md flex items-center gap-1.5 animate-pulse"
        style={{
          background: 'rgba(255, 165, 0, 0.2)',
          border: '1px solid #FFA500',
          color: '#FFA500'
        }}
      >
        <Eye className="w-3 h-3" />
        <span className="text-[10px] font-bold uppercase tracking-wider">SCOUTING</span>
      </div>
      {status && (
        <div className="mt-1 text-[9px] text-orange-400/80 text-center font-mono">
          {status}
        </div>
      )}
    </div>
  );
});

ScoutingBadge.displayName = 'ScoutingBadge';

// ==================== SCOUTING MISSION BRIEFING CARD ====================
// Placeholder card for games without live lines yet
const ScoutingMissionCard = memo(({ projection, onClick }) => {
  if (!projection) return null;
  
  const {
    player_name,
    team,
    opponent,
    projections,
    season_avg,
    last_3_avg,
    smart_anchor_vision,
    status,
    commence_time
  } = projection;
  
  return (
    <div 
      className="relative bg-gradient-to-br from-zinc-900 via-orange-950/20 to-zinc-900 border border-orange-500/30 rounded-lg p-4 cursor-pointer hover:border-orange-400/50 transition-all"
      onClick={() => onClick?.(projection)}
      data-testid={`scouting-card-${player_name?.replace(/\s/g, '-')}`}
    >
      {/* Scouting Badge */}
      <div 
        className="absolute top-2 right-2 px-2 py-1 rounded-md flex items-center gap-1.5"
        style={{
          background: 'rgba(255, 165, 0, 0.2)',
          border: '1px solid #FFA500',
          color: '#FFA500'
        }}
      >
        <Eye className="w-3 h-3" />
        <span className="text-[10px] font-bold uppercase tracking-wider">SCOUTING</span>
      </div>
      
      {/* Player Header */}
      <div className="flex items-center gap-3 mb-3">
        <div className="w-12 h-12 bg-orange-500/20 rounded-full flex items-center justify-center border border-orange-500/30">
          <Target className="w-6 h-6 text-orange-400" />
        </div>
        <div>
          <h3 className="text-white font-bold text-sm">{player_name}</h3>
          <p className="text-orange-400/80 text-xs">{team} vs {opponent}</p>
        </div>
      </div>
      
      {/* Mission Status */}
      <div className="bg-zinc-950/50 rounded p-2 mb-3 border border-orange-500/20">
        <p className="text-[10px] text-orange-300 font-mono text-center uppercase tracking-wider">
          {status || "Awaiting Official Mission Parameters"}
        </p>
      </div>
      
      {/* Projection Stats */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="bg-zinc-950/30 rounded p-2">
          <p className="text-[10px] text-zinc-500 uppercase mb-1">Season Avg</p>
          <div className="flex gap-2 text-xs">
            <span className="text-orange-300">{season_avg?.pts || '--'} PTS</span>
            <span className="text-orange-300">{season_avg?.reb || '--'} REB</span>
            <span className="text-orange-300">{season_avg?.ast || '--'} AST</span>
          </div>
        </div>
        <div className="bg-zinc-950/30 rounded p-2">
          <p className="text-[10px] text-zinc-500 uppercase mb-1">Last 3 Games</p>
          <div className="flex gap-2 text-xs">
            <span className="text-orange-200">{last_3_avg?.pts || '--'} PTS</span>
            <span className="text-orange-200">{last_3_avg?.reb || '--'} REB</span>
            <span className="text-orange-200">{last_3_avg?.ast || '--'} AST</span>
          </div>
        </div>
      </div>
      
      {/* Smart Anchor Vision */}
      {smart_anchor_vision && (
        <div className="bg-orange-500/10 rounded p-2 border border-orange-500/20">
          <div className="flex items-center gap-1 mb-1">
            <Brain className="w-3 h-3 text-orange-400" />
            <span className="text-[10px] text-orange-400 font-bold uppercase">Smart Anchor Intel</span>
          </div>
          <p className="text-xs text-orange-200/80 leading-relaxed">
            {smart_anchor_vision}
          </p>
        </div>
      )}
      
      {/* Expected Projections */}
      <div className="mt-3 flex items-center justify-between">
        <div className="flex gap-2">
          <div className="bg-orange-500/20 px-2 py-1 rounded">
            <span className="text-[10px] text-orange-300">PTS: ~{projections?.points || '--'}</span>
          </div>
          <div className="bg-orange-500/20 px-2 py-1 rounded">
            <span className="text-[10px] text-orange-300">PRA: ~{projections?.pra || '--'}</span>
          </div>
        </div>
        <span className="text-[9px] text-zinc-500 font-mono">
          Projection • Not Live
        </span>
      </div>
    </div>
  );
});

ScoutingMissionCard.displayName = 'ScoutingMissionCard';

// ==================== BREAKING NEWS TICKER ====================

// ==================== LIVE SCORE TICKER - Command Center ====================

const LiveScoreTicker = memo(({ games }) => {
  const [isPaused, setIsPaused] = useState(false);
  
  if (!games || games.length === 0) return null;
  
  const liveGames = games.filter(g => g.status === 'in_play');
  const upcomingGames = games.filter(g => g.status === 'upcoming');
  
  return (
    <div 
      className="bg-gradient-to-r from-zinc-950 via-emerald-950/30 to-zinc-950 border-b border-emerald-500/30 py-1.5 overflow-hidden"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className={`flex items-center gap-4 ${isPaused ? '' : 'animate-score-scroll'}`}>
        {/* Live Badge */}
        <div className="flex items-center gap-2 px-3 flex-shrink-0">
          <div className="flex items-center gap-1.5 bg-emerald-600 px-2 py-0.5 rounded animate-pulse">
            <Radio className="w-3 h-3 text-white" />
            <span className="text-[10px] font-bold text-white uppercase tracking-wider">LIVE</span>
          </div>
          {liveGames.length > 0 && (
            <span className="text-[10px] text-emerald-400 font-mono">{liveGames.length} IN PLAY</span>
          )}
        </div>
        
        <div className="flex items-center gap-6 whitespace-nowrap">
          {/* Live Games - Neon Green */}
          {liveGames.map((game, idx) => (
            <div key={game.id} className="flex items-center gap-2 bg-emerald-950/50 px-3 py-1 rounded border border-emerald-500/30">
              <span className="text-xs font-bold text-emerald-300">{game.away_team}</span>
              <span className="text-sm font-bold text-white">{game.away_score}</span>
              <span className="text-[10px] text-zinc-500">@</span>
              <span className="text-sm font-bold text-white">{game.home_score}</span>
              <span className="text-xs font-bold text-emerald-300">{game.home_team}</span>
              <span className="text-[9px] text-emerald-400 bg-emerald-900/50 px-1.5 py-0.5 rounded font-mono">
                {game.status_display}
              </span>
            </div>
          ))}
          
          {/* Upcoming Games */}
          {upcomingGames.slice(0, 5).map((game, idx) => (
            <div key={game.id} className="flex items-center gap-2 bg-zinc-900/50 px-3 py-1 rounded border border-zinc-700/30">
              <span className="text-xs text-zinc-400">{game.away_team}</span>
              <span className="text-[10px] text-zinc-500">@</span>
              <span className="text-xs text-zinc-400">{game.home_team}</span>
              <span className="text-[9px] text-amber-400 font-mono">{game.status_display}</span>
            </div>
          ))}
          
          {/* Duplicate for seamless loop */}
          {liveGames.map((game, idx) => (
            <div key={`dup-${game.id}`} className="flex items-center gap-2 bg-emerald-950/50 px-3 py-1 rounded border border-emerald-500/30">
              <span className="text-xs font-bold text-emerald-300">{game.away_team}</span>
              <span className="text-sm font-bold text-white">{game.away_score}</span>
              <span className="text-[10px] text-zinc-500">@</span>
              <span className="text-sm font-bold text-white">{game.home_score}</span>
              <span className="text-xs font-bold text-emerald-300">{game.home_team}</span>
              <span className="text-[9px] text-emerald-400 bg-emerald-900/50 px-1.5 py-0.5 rounded font-mono">
                {game.status_display}
              </span>
            </div>
          ))}
          {upcomingGames.slice(0, 5).map((game, idx) => (
            <div key={`dup-up-${game.id}`} className="flex items-center gap-2 bg-zinc-900/50 px-3 py-1 rounded border border-zinc-700/30">
              <span className="text-xs text-zinc-400">{game.away_team}</span>
              <span className="text-[10px] text-zinc-500">@</span>
              <span className="text-xs text-zinc-400">{game.home_team}</span>
              <span className="text-[9px] text-amber-400 font-mono">{game.status_display}</span>
            </div>
          ))}
        </div>
      </div>
      
      <style jsx>{`
        @keyframes scoreScroll {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .animate-score-scroll {
          animation: scoreScroll 40s linear infinite;
        }
      `}</style>
    </div>
  );
});

LiveScoreTicker.displayName = 'LiveScoreTicker';

// ==================== BREAKING NEWS TICKER - Command Center ====================

const BreakingNewsTicker = memo(({ news }) => {
  const [isPaused, setIsPaused] = useState(false);
  
  if (!news || news.length === 0) return null;
  
  return (
    <div 
      className="bg-gradient-to-r from-red-950/50 via-zinc-900 to-red-950/50 border-b border-red-800/30 py-1.5 overflow-hidden"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className={`flex items-center gap-3 ${isPaused ? '' : 'animate-news-scroll'}`}>
        <div className="flex items-center gap-2 px-3 flex-shrink-0">
          <div className="flex items-center gap-1 bg-red-600 px-2 py-0.5 rounded animate-pulse">
            <Zap className="w-3 h-3 text-white" />
            <span className="text-[10px] font-bold text-white uppercase tracking-wider">Breaking</span>
          </div>
        </div>
        
        <div className="flex items-center gap-6 whitespace-nowrap">
          {news.map((item, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <AlertTriangle className="w-3 h-3 text-red-400 flex-shrink-0 animate-pulse" />
              <span className="text-xs text-red-200">{item.headline || item.title}</span>
              {item.source && (
                <span className="text-[9px] text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">{item.source}</span>
              )}
              {idx < news.length - 1 && <span className="text-zinc-700 mx-2">|</span>}
            </div>
          ))}
          {/* Duplicate for seamless loop */}
          {news.map((item, idx) => (
            <div key={`dup-${idx}`} className="flex items-center gap-2">
              <AlertTriangle className="w-3 h-3 text-red-400 flex-shrink-0 animate-pulse" />
              <span className="text-xs text-red-200">{item.headline || item.title}</span>
              {item.source && (
                <span className="text-[9px] text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">{item.source}</span>
              )}
              {idx < news.length - 1 && <span className="text-zinc-700 mx-2">|</span>}
            </div>
          ))}
        </div>
      </div>
      
      <style jsx>{`
        @keyframes newsScroll {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .animate-news-scroll {
          animation: newsScroll 45s linear infinite;
        }
      `}</style>
    </div>
  );
});

BreakingNewsTicker.displayName = 'BreakingNewsTicker';

// ==================== V3.1 TRUTH ENGINE - DATA VALIDATION STATUS LIGHT ====================

const DataValidationLight = memo(({ dataStatus }) => {
  if (!dataStatus) return null;
  
  const { status, verified_count, failed_count, verification_rate, total_props } = dataStatus;
  
  // Determine light color and status text
  let lightColor, statusText, statusBg, pulseColor;
  
  switch (status) {
    case 'verified':
      lightColor = 'bg-green-500';
      statusBg = 'bg-green-500/10 border-green-500/30';
      pulseColor = 'animate-pulse-green';
      statusText = 'Data Verified';
      break;
    case 'discrepancy_found':
      lightColor = 'bg-red-500';
      statusBg = 'bg-red-500/10 border-red-500/30';
      pulseColor = 'animate-pulse-red';
      statusText = 'Discrepancy Found';
      break;
    case 'no_data':
      lightColor = 'bg-zinc-500';
      statusBg = 'bg-zinc-500/10 border-zinc-500/30';
      pulseColor = '';
      statusText = 'No Data';
      break;
    case 'pending_verification':
      lightColor = 'bg-amber-500';
      statusBg = 'bg-amber-500/10 border-amber-500/30';
      pulseColor = 'animate-pulse-amber';
      statusText = 'Verifying...';
      break;
    default:
      lightColor = 'bg-zinc-600';
      statusBg = 'bg-zinc-600/10 border-zinc-600/30';
      pulseColor = '';
      statusText = 'Loading...';
  }
  
  return (
    <div 
      className={`flex items-center gap-2 px-2.5 py-1 rounded-lg border ${statusBg}`}
      data-testid="data-validation-light"
      title={`${verified_count || 0} verified, ${failed_count || 0} failed, ${verification_rate || 0}% verification rate`}
    >
      {/* Status Light */}
      <div className="relative flex items-center justify-center">
        <div className={`w-2 h-2 rounded-full ${lightColor} ${pulseColor}`} />
        {status === 'verified' && (
          <div className="absolute w-4 h-4 rounded-full bg-green-500/20 animate-ping" style={{ animationDuration: '2s' }} />
        )}
        {status === 'discrepancy_found' && (
          <div className="absolute w-4 h-4 rounded-full bg-red-500/20 animate-ping" style={{ animationDuration: '1.5s' }} />
        )}
      </div>
      
      {/* Status Text */}
      <span className={`text-[10px] font-medium ${
        status === 'verified' ? 'text-green-400' : 
        status === 'discrepancy_found' ? 'text-red-400' : 
        status === 'pending_verification' ? 'text-amber-400' :
        'text-zinc-400'
      }`}>
        {statusText}
      </span>
      
      {/* Verification Rate Badge */}
      {total_props > 0 && (
        <Badge className={`text-[8px] px-1.5 py-0 h-4 ${
          status === 'verified' ? 'bg-green-500/20 text-green-300 border-green-500/30' :
          status === 'discrepancy_found' ? 'bg-red-500/20 text-red-300 border-red-500/30' :
          'bg-zinc-500/20 text-zinc-300 border-zinc-500/30'
        }`}>
          {verification_rate}%
        </Badge>
      )}
      
      <style jsx>{`
        @keyframes pulse-green {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }
          50% { opacity: 0.8; box-shadow: 0 0 8px 2px rgba(34, 197, 94, 0.4); }
        }
        @keyframes pulse-red {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
          50% { opacity: 0.8; box-shadow: 0 0 8px 2px rgba(239, 68, 68, 0.4); }
        }
        @keyframes pulse-amber {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        .animate-pulse-green { animation: pulse-green 2s ease-in-out infinite; }
        .animate-pulse-red { animation: pulse-red 1.5s ease-in-out infinite; }
        .animate-pulse-amber { animation: pulse-amber 1s ease-in-out infinite; }
      `}</style>
    </div>
  );
});

DataValidationLight.displayName = 'DataValidationLight';

// ==================== RAW STAT VALIDATION TABLE ====================
// DATA INTEGRITY CHECK - Shows RAW API values for manual ESPN verification

const RawValidationTable = memo(({ isVisible, onClose }) => {
  const [validationData, setValidationData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [customPlayer, setCustomPlayer] = useState('');
  
  // Kill List players for verification
  const KILL_LIST = ['Luka Doncic', 'Anthony Edwards', 'Naji Marshall'];
  
  const fetchValidationData = async (playerNames) => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.post(`${API}/v3/raw-validation/batch`, playerNames);
      if (response.data.success) {
        setValidationData(response.data.validation_entries);
      } else {
        setError('Failed to fetch validation data');
      }
    } catch (err) {
      setError(err.message || 'Error fetching data');
    } finally {
      setLoading(false);
    }
  };
  
  const addPlayer = async () => {
    if (!customPlayer.trim()) return;
    setLoading(true);
    try {
      const response = await axios.get(`${API}/v3/raw-validation/${encodeURIComponent(customPlayer)}`);
      if (response.data.success) {
        setValidationData(prev => {
          // Remove existing entry for this player
          const filtered = prev.filter(p => p.player_name?.toLowerCase() !== customPlayer.toLowerCase());
          return [...filtered, response.data.validation_entry];
        });
        setCustomPlayer('');
      }
    } catch (err) {
      setError(`Player not found: ${customPlayer}`);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    if (isVisible && validationData.length === 0) {
      fetchValidationData(KILL_LIST);
    }
  }, [isVisible]);
  
  if (!isVisible) return null;
  
  return (
    <div className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4" data-testid="raw-validation-modal">
      <div className="bg-zinc-900 border border-red-500/50 rounded-lg max-w-5xl w-full max-h-[90vh] overflow-auto">
        {/* Header */}
        <div className="sticky top-0 bg-zinc-900 border-b border-red-500/30 p-4 flex justify-between items-center">
          <div>
            <h2 className="text-xl font-bold text-red-500 font-mono">
              ⚠️ DATA INTEGRITY CHECK
            </h2>
            <p className="text-xs text-zinc-400 mt-1">
              RAW API VALUES - Compare against ESPN box scores
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-white p-2"
            data-testid="close-validation-modal"
          >
            ✕
          </button>
        </div>
        
        {/* Add Player Input */}
        <div className="p-4 border-b border-zinc-800">
          <div className="flex gap-2">
            <input
              type="text"
              value={customPlayer}
              onChange={(e) => setCustomPlayer(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addPlayer()}
              placeholder="Add player to verify (e.g., LeBron James)"
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500"
              data-testid="validation-player-input"
            />
            <button
              onClick={addPlayer}
              disabled={loading}
              className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-sm font-medium rounded"
              data-testid="add-validation-player"
            >
              {loading ? '...' : 'ADD'}
            </button>
            <button
              onClick={() => fetchValidationData(KILL_LIST)}
              disabled={loading}
              className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white text-sm rounded"
            >
              RESET
            </button>
          </div>
          {error && (
            <p className="text-red-400 text-xs mt-2">{error}</p>
          )}
        </div>
        
        {/* Validation Table */}
        <div className="p-4">
          {loading && validationData.length === 0 ? (
            <div className="text-center text-zinc-400 py-8">Loading raw data...</div>
          ) : (
            <div className="space-y-6">
              {validationData.map((player, idx) => (
                <div key={idx} className="bg-zinc-800/50 rounded-lg p-4 border border-zinc-700">
                  {player.error ? (
                    <div className="text-red-400">
                      <span className="font-bold">{player.player_name}</span>: {player.error}
                    </div>
                  ) : (
                    <>
                      <div className="flex justify-between items-center mb-3">
                        <h3 className="text-lg font-bold text-white">{player.player_name}</h3>
                        <span className="text-xs text-zinc-500 font-mono">
                          BDL ID: {player.bdl_player_id}
                        </span>
                      </div>
                      
                      {/* Stats Table */}
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-zinc-700">
                              <th className="text-left py-2 px-3 text-zinc-400 font-mono text-xs">DATE</th>
                              <th className="text-left py-2 px-3 text-zinc-400 font-mono text-xs">TEAM</th>
                              <th className="text-left py-2 px-3 text-zinc-400 font-mono text-xs">SCORE</th>
                              <th className="text-center py-2 px-3 text-yellow-500 font-mono text-xs font-bold">PTS (RAW)</th>
                              <th className="text-center py-2 px-3 text-blue-500 font-mono text-xs font-bold">REB (RAW)</th>
                              <th className="text-center py-2 px-3 text-green-500 font-mono text-xs font-bold">AST (RAW)</th>
                            </tr>
                          </thead>
                          <tbody>
                            {player.last_5_games?.map((game, gIdx) => (
                              <tr key={gIdx} className="border-b border-zinc-800 hover:bg-zinc-700/30">
                                <td className="py-2 px-3 text-zinc-300 font-mono text-xs">
                                  {game.date ? new Date(game.date).toLocaleDateString() : '-'}
                                </td>
                                <td className="py-2 px-3 text-zinc-300 font-mono text-xs">{game.team || '???'}</td>
                                <td className="py-2 px-3 text-zinc-300 font-mono text-xs">{game.score || '-'}</td>
                                <td className="py-2 px-3 text-center">
                                  <span className="text-yellow-400 font-bold text-lg">{game.pts ?? 'NULL'}</span>
                                </td>
                                <td className="py-2 px-3 text-center">
                                  <span className="text-blue-400 font-bold text-lg">{game.reb ?? 'NULL'}</span>
                                </td>
                                <td className="py-2 px-3 text-center">
                                  <span className="text-green-400 font-bold text-lg">{game.ast ?? 'NULL'}</span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        
        {/* Footer */}
        <div className="sticky bottom-0 bg-zinc-900 border-t border-zinc-800 p-4">
          <p className="text-xs text-zinc-500 text-center font-mono">
            SOURCE: balldontlie_raw_unprocessed | ZERO PROCESSING APPLIED | Verify against ESPN.com
          </p>
        </div>
      </div>
    </div>
  );
});

RawValidationTable.displayName = 'RawValidationTable';

// Cache keys
const CACHE_KEYS = {
  STATIC_SHELL: 'dg_static_shell',
  CACHE_TIMESTAMP: 'dg_cache_timestamp'
};

// Condensed market names (RPA format)
const MARKET_SHORT = {
  'player_points': 'PTS',
  'player_rebounds': 'REB',
  'player_assists': 'AST',
  'player_threes': '3PM',
  'player_blocks': 'BLK',
  'player_steals': 'STL',
  'player_turnovers': 'TO',
  'player_points_alternate': 'PTS',
  'player_rebounds_alternate': 'REB',
  'player_assists_alternate': 'AST',
  'player_threes_alternate': '3PM',
  'player_blocks_alternate': 'BLK',
  'player_steals_alternate': 'STL',
  'player_turnovers_alternate': 'TO',
  'player_points_rebounds': 'P+R',
  'player_points_assists': 'P+A',
  'player_rebounds_assists': 'R+A',
  'player_points_rebounds_assists': 'PRA',
  'player_points_rebounds_alternate': 'P+R',
  'player_points_assists_alternate': 'P+A',
  'player_rebounds_assists_alternate': 'R+A',
  'player_points_rebounds_assists_alternate': 'PRA'
};

// Get condensed market name
const getMarketName = (market) => {
  if (!market) return '---';
  const clean = market.replace('_alternate', '');
  return MARKET_SHORT[market] || MARKET_SHORT[clean] || market.split('_').pop()?.toUpperCase() || '---';
};

// ==================== CACHE SERVICE ====================

const CacheService = {
  getStaticShell: () => {
    try {
      const cached = localStorage.getItem(CACHE_KEYS.STATIC_SHELL);
      const timestamp = localStorage.getItem(CACHE_KEYS.CACHE_TIMESTAMP);
      if (cached && timestamp) {
        const age = Date.now() - parseInt(timestamp);
        if (age < 24 * 60 * 60 * 1000) {
          return { hit: true, age: age / 1000, data: JSON.parse(cached) };
        }
      }
    } catch (e) { console.error('Cache read error:', e); }
    return { hit: false, data: null };
  },
  setStaticShell: (data) => {
    try {
      localStorage.setItem(CACHE_KEYS.STATIC_SHELL, JSON.stringify(data));
      localStorage.setItem(CACHE_KEYS.CACHE_TIMESTAMP, Date.now().toString());
    } catch (e) { console.error('Cache write error:', e); }
  },
  clear: () => {
    localStorage.removeItem(CACHE_KEYS.STATIC_SHELL);
    localStorage.removeItem(CACHE_KEYS.CACHE_TIMESTAMP);
  }
};

// ==================== SKELETON LOADERS ====================

const SkeletonProp = () => (
  <div className="flex items-center justify-between p-2 bg-zinc-900/50 rounded animate-pulse">
    <div className="flex items-center gap-2">
      <div className="w-10 h-5 bg-zinc-800 rounded" />
      <div className="w-8 h-5 bg-zinc-800 rounded" />
    </div>
    <div className="w-12 h-5 bg-zinc-800 rounded" />
  </div>
);

const SkeletonPlayerDetail = () => (
  <div className="space-y-2 p-3">
    {[1, 2, 3, 4, 5, 6].map(i => <SkeletonProp key={i} />)}
  </div>
);

// ==================== PROP ROW (Condensed RPA Format) ====================

const PropRow = memo(({ prop, compact = false }) => {
  const market = getMarketName(prop.market);
  const line = prop.line;
  const direction = prop.direction;
  const price = prop.price;
  const hitRate = prop.hit_rates?.l10?.hit_rate || prop.hit_rate || 0;
  const hitPct = Math.round((hitRate || 0) * 100);
  
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  
  // Compact single-line format for mobile
  return (
    <div 
      className={`
        flex items-center justify-between px-2 py-1.5 rounded text-sm
        ${isDemon ? 'bg-red-950/40 border-l-2 border-red-500' : ''}
        ${isGoblin ? 'bg-green-950/40 border-l-2 border-green-500' : ''}
        ${!isDemon && !isGoblin ? 'bg-zinc-900/30' : ''}
      `}
    >
      {/* Left: Type Icon + Market + Line */}
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        {isDemon && <DemonIcon size={14} className="flex-shrink-0" />}
        {isGoblin && <GoblinIcon size={14} className="flex-shrink-0" />}
        {!isDemon && !isGoblin && <div className="w-3.5" />}
        
        <span className="text-zinc-400 font-mono text-xs">{market}</span>
        
        <span className={`font-bold text-white ${compact ? 'text-sm' : 'text-base'}`}>
          {line}
        </span>
        
        <span className={`text-[10px] ${direction === 'Over' ? 'text-green-400' : 'text-red-400'}`}>
          {direction === 'Over' ? 'O' : 'U'}
        </span>
      </div>
      
      {/* Right: Hit Rate (high contrast) */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {price !== undefined && (
          <span className={`text-xs font-mono ${
            price === 100 ? 'text-red-400' : price < 0 ? 'text-green-400' : 'text-zinc-500'
          }`}>
            {price > 0 ? `+${price}` : price}
          </span>
        )}
        
        <span className={`font-bold text-sm min-w-[36px] text-right ${
          hitPct >= 70 ? 'text-green-400' :
          hitPct >= 50 ? 'text-yellow-400' :
          hitPct > 0 ? 'text-zinc-400' : 'text-zinc-600'
        }`}>
          {hitPct > 0 ? `${hitPct}%` : '---'}
        </span>
      </div>
    </div>
  );
});

PropRow.displayName = 'PropRow';

// ==================== TRENDING CARD (Clickable with Headshot) ====================

const TrendingCard = memo(({ player, rank, onClick, linesLoaded, injuryAlerts }) => {
  const injury = injuryAlerts?.[player.player_name];
  const hasInjury = !!injury;
  const isHighRisk = injury?.severity === 'HIGH';
  
  return (
    <Card 
      className={`
        bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 
        hover:border-purple-500/50 hover:scale-[1.02] transition-all duration-200
        cursor-pointer active:scale-[0.98]
        ${hasInjury ? (isHighRisk ? 'ring-1 ring-red-500/50' : 'ring-1 ring-yellow-500/30') : ''}
      `}
      onClick={onClick}
      data-testid={`trending-card-${rank}`}
    >
      <div className="p-3">
        {/* Header: Headshot + Rank Badge + Name + Injury Badge */}
        <div className="flex items-center gap-2 mb-2">
          {/* Headshot with Rank Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={player.nba_id} 
              playerName={player.player_name}
              team={player.team}
              photoUrl={player.photo_url}
              size="md"
              className="ring-2 ring-zinc-700"
            />
            {/* Rank Badge - Positioned at bottom right of headshot */}
            <div className={`
              absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center 
              font-bold text-[10px] border-2 border-zinc-900
              ${rank === 1 ? 'bg-yellow-500 text-black' : 
                rank === 2 ? 'bg-zinc-400 text-black' :
                rank === 3 ? 'bg-amber-700 text-white' :
                'bg-zinc-700 text-zinc-300'}
            `}>
              {rank}
            </div>
            
            {/* Injury Badge - Positioned at top right */}
            {hasInjury && (
              <div className="absolute -top-1 -right-1">
                <InjuryBadge playerName={player.player_name} injuryAlerts={injuryAlerts} size="sm" />
              </div>
            )}
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{player.player_name}</span>
              {rank <= 3 && <Flame className="w-3 h-3 text-orange-500 flex-shrink-0" />}
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{player.team || '---'}</span>
              {player.position && <span>· {player.position}</span>}
              {hasInjury && (
                <span className={`ml-1 px-1 rounded text-[8px] font-bold ${isHighRisk ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                  {injury.status}
                </span>
              )}
            </div>
          </div>
        </div>
        
        {/* Demon/Goblin Counts */}
        {linesLoaded ? (
          <div className="flex items-center gap-3">
            {(player.demons_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <DemonIcon size={14} />
                <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
              </div>
            )}
            {(player.goblins_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <GoblinIcon size={14} />
                <span className="text-green-400 font-bold text-sm">{player.goblins_count}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <div className="w-10 h-4 bg-zinc-800 animate-pulse rounded" />
            <div className="w-10 h-4 bg-zinc-800 animate-pulse rounded" />
          </div>
        )}
        
        {/* Injury Warning */}
        {hasInjury && (
          <div className="mt-2 text-[10px] text-yellow-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            {player.injury_info?.injury_status || 'CHECK STATUS'}
          </div>
        )}
      </div>
    </Card>
  );
});

TrendingCard.displayName = 'TrendingCard';

// ==================== DEMON RADAR CARD ====================

// ==================== UNIVERSAL PICK CARD ====================
// Single unified card template for all 3 tiers (War Zone, Front Lines, Safe Haven)
// Only visual differences: colorTheme (red/amber/green) and emblem (fire/bullet/gem)

// Bullet Emblem SVG Component (rifle ammunition graphic)
const BulletEmblem = memo(({ size = 20 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="bulletGradient" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#D4AF37" />
        <stop offset="50%" stopColor="#FFD700" />
        <stop offset="100%" stopColor="#B8860B" />
      </linearGradient>
      <linearGradient id="casingGradient" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#CD7F32" />
        <stop offset="50%" stopColor="#DAA520" />
        <stop offset="100%" stopColor="#8B4513" />
      </linearGradient>
    </defs>
    {/* Bullet tip (projectile) */}
    <path d="M12 2 L15 8 L15 10 L9 10 L9 8 Z" fill="url(#bulletGradient)" />
    {/* Brass casing */}
    <rect x="9" y="10" width="6" height="10" rx="0.5" fill="url(#casingGradient)" />
    {/* Casing rim */}
    <rect x="8" y="20" width="8" height="2" rx="0.5" fill="#8B4513" />
    {/* Primer circle */}
    <circle cx="12" cy="21" r="1.5" fill="#444" />
    {/* Highlight on bullet */}
    <path d="M10.5 3 L11.5 7 L10 7 Z" fill="rgba(255,255,255,0.3)" />
  </svg>
));
BulletEmblem.displayName = 'BulletEmblem';

// Fire Emblem Component
const FireEmblem = memo(({ size = 20 }) => (
  <span 
    className="text-[20px]" 
    style={{ 
      fontSize: size,
      filter: 'drop-shadow(0 0 4px #ff6b35) drop-shadow(0 0 8px #ff4500)',
      animation: 'pulse 1.5s ease-in-out infinite'
    }}
  >
    🔥
  </span>
));
FireEmblem.displayName = 'FireEmblem';

// Gem Emblem Component  
const GemEmblem = memo(({ size = 20 }) => (
  <span 
    className="text-[20px]"
    style={{ 
      fontSize: size,
      color: '#00BFFF',
      filter: 'drop-shadow(0 0 4px #00BFFF) drop-shadow(0 0 8px #00CED1)',
      animation: 'pulse 2s ease-in-out infinite'
    }}
  >
    💎
  </span>
));
GemEmblem.displayName = 'GemEmblem';

const UniversalPickCard = memo(({ 
  pick, 
  rank, 
  onClick, 
  tMinusGames = [], 
  colorTheme = 'red',  // 'red' | 'amber' | 'green'
  emblem = 'fire'       // 'fire' | 'bullet' | 'gem'
}) => {
  // Check if this has a special Vision insight (Master Tier)
  const hasVisionGlow = pick.has_high_conflict || 
    ((pick.intel_briefing || pick.insight_summary) && !(pick.intel_briefing || pick.insight_summary).toLowerCase().includes('standard'));
  
  // Color theme variants - RED (War Zone), AMBER (Front Lines), GREEN (Safe Haven)
  const themeColors = {
    red: { 
      border: 'border-red-500/40', 
      glow: 'rgba(239, 68, 68, 0.3)', 
      text: 'text-red-400', 
      bg: 'from-red-950/50',
      ring: 'ring-red-800/50',
      rankBg: 'bg-red-600',
      priceColor: 'text-red-400',
      borderLine: 'border-red-900/30',
      scoreBarHigh: 'from-red-500 to-red-400',
      scoreBarMid: 'from-orange-500 to-orange-400'
    },
    amber: { 
      border: 'border-amber-500/40', 
      glow: 'rgba(245, 158, 11, 0.3)', 
      text: 'text-amber-400', 
      bg: 'from-amber-950/50',
      ring: 'ring-amber-800/50',
      rankBg: 'bg-amber-600',
      priceColor: 'text-amber-400',
      borderLine: 'border-amber-900/30',
      scoreBarHigh: 'from-amber-500 to-amber-400',
      scoreBarMid: 'from-yellow-500 to-yellow-400'
    },
    green: { 
      border: 'border-green-500/40', 
      glow: 'rgba(34, 197, 94, 0.3)', 
      text: 'text-green-400', 
      bg: 'from-green-950/50',
      ring: 'ring-green-800/50',
      rankBg: 'bg-green-600',
      priceColor: 'text-green-400',
      borderLine: 'border-green-900/30',
      scoreBarHigh: 'from-green-500 to-green-400',
      scoreBarMid: 'from-emerald-500 to-emerald-400'
    }
  };
  const theme = themeColors[colorTheme] || themeColors.red;
  
  // Render the emblem based on tier
  const renderEmblem = () => {
    switch(emblem) {
      case 'fire': return <FireEmblem size={20} />;
      case 'bullet': return <BulletEmblem size={22} />;
      case 'gem': return <GemEmblem size={20} />;
      default: return <FireEmblem size={20} />;
    }
  };
  
  // Heat Level indicator rendering (works for all tiers)
  const heatLevel = pick.heat_level || 0;
  const h10Rate = pick.h10_rate || 0;
  
  // Calculate display level based on hit rate for non-war-zone
  const getDisplayLevel = () => {
    if (emblem === 'fire') return heatLevel;
    // For amber/green, calculate based on hit rate
    if (h10Rate >= 100) return 5;
    if (h10Rate >= 90) return 4;
    if (h10Rate >= 80) return 3;
    if (h10Rate >= 70) return 2;
    if (h10Rate >= 60) return 1;
    return 0;
  };
  
  const displayLevel = getDisplayLevel();
  
  const renderIndicators = () => {
    if (displayLevel === 0) return null;
    const indicatorEmoji = emblem === 'fire' ? '🔥' : emblem === 'gem' ? '💎' : null;
    
    if (emblem === 'bullet') {
      // Render mini bullets for Front Lines
      return (
        <div className="flex items-center gap-0.5" title={`${displayLevel} bullets - ${h10Rate}% hit rate`}>
          {[...Array(Math.min(5, displayLevel))].map((_, i) => (
            <BulletEmblem key={i} size={12} />
          ))}
        </div>
      );
    }
    
    return (
      <div className="flex items-center gap-0.5" title={getHeatDescription(displayLevel)}>
        {[...Array(displayLevel)].map((_, i) => (
          <span key={i} className="text-[12px]" style={{ 
            filter: emblem === 'fire' 
              ? 'drop-shadow(0 0 2px #ff6b35)' 
              : 'drop-shadow(0 0 2px #00BFFF)'
          }}>
            {indicatorEmoji}
          </span>
        ))}
      </div>
    );
  };
  
  const getHeatDescription = (level) => {
    if (emblem === 'gem') {
      switch(level) {
        case 5: return 'FORTRESS! 100% L10 hit rate';
        case 4: return 'DIAMOND! 90%+ L10 hit rate';
        case 3: return 'VAULT! 80%+ L10 hit rate';
        case 2: return 'SAFE! 70%+ L10 hit rate';
        case 1: return 'BASE! 60%+ L10 hit rate';
        default: return 'Below 60% hit rate';
      }
    }
    switch(level) {
      case 5: return 'ON FIRE! 9-10/10 games hit';
      case 4: return 'HOT! 80%+ L10 or 5-game streak';
      case 3: return 'WARM! 70%+ L10 or 3-game streak';
      case 2: return 'Mild - 60%+ L10';
      case 1: return 'Cool - 50%+ L10';
      default: return 'Cold';
    }
  };
  
  const getLevelLabel = (level) => {
    if (emblem === 'gem') {
      switch(level) {
        case 5: return 'FORTRESS';
        case 4: return 'DIAMOND';
        case 3: return 'VAULT';
        case 2: return 'SAFE';
        case 1: return 'BASE';
        default: return '';
      }
    }
    if (emblem === 'bullet') {
      switch(level) {
        case 5: return 'ELITE';
        case 4: return 'STRONG';
        case 3: return 'SOLID';
        case 2: return 'FAIR';
        case 1: return 'BASE';
        default: return '';
      }
    }
    switch(level) {
      case 5: return 'ON FIRE';
      case 4: return 'HOT';
      case 3: return 'WARM';
      case 2: return 'MILD';
      case 1: return 'COOL';
      default: return '';
    }
  };
  
  // Get line value based on tier
  const lineValue = pick.demon_line || pick.goblin_line || pick.line || 0;
  const priceValue = pick.price || 100;
  
  // Get score value based on tier
  const scoreValue = pick.radar_score || pick.vault_score || (pick.vault_score_100 ? pick.vault_score_100 / 100 : 0) || (h10Rate / 100);
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${theme.bg} to-zinc-900 border ${theme.border}
        hover:scale-[1.02] transition-all duration-300
        cursor-pointer active:scale-[0.98] relative overflow-visible
        min-h-[280px]
        ${pick.locked ? 'pointer-events-none' : ''}
      `}
      style={{ boxShadow: `0 0 20px ${theme.glow}` }}
      onClick={pick.locked ? undefined : onClick}
      data-testid={`pick-card-${colorTheme}-${rank}`}
    >
      {/* LOCKED Badge - Game In Progress */}
      <LockedBadge isLocked={pick.locked} commenceTime={pick.commence_time} />
      
      {/* T-Minus Countdown Badge */}
      <TMinusBadge commenceTime={pick.commence_time} tMinusGames={tMinusGames} />
      
      {/* Vision Synergy Badge */}
      {hasVisionGlow && <VisionBadge type={colorTheme === 'green' ? 'goblin' : 'demon'} hasVision={true} />}
      
      <div className="p-3">
        {/* Header: Demon/Goblin Icon + Headshot + Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Demon/Goblin Icon based on pick type or tier default */}
          {/* War Zone (red) = demon by default, Safe Haven (green) = goblin by default */}
          {/* Front Lines (amber) = show based on is_demon/is_goblin flag */}
          <div className="flex-shrink-0">
            {pick.is_demon ? (
              <DemonIcon size={20} hasVision={hasVisionGlow} />
            ) : pick.is_goblin ? (
              <GoblinIcon size={20} hasVision={hasVisionGlow} />
            ) : colorTheme === 'red' ? (
              <DemonIcon size={20} hasVision={hasVisionGlow} />
            ) : colorTheme === 'green' ? (
              <GoblinIcon size={20} hasVision={hasVisionGlow} />
            ) : null}
          </div>
          
          {/* Headshot with Rank Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name}
              team={pick.team}
              photoUrl={pick.photo_url}
              size="md"
              className={`ring-2 ${theme.ring}`}
            />
            {/* Rank Badge */}
            <div className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center 
                          font-bold text-[10px] border-2 border-zinc-900 ${theme.rankBg} text-white`}>
              {rank}
            </div>
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{pick.player_name}</span>
              {/* Social Signal Icons */}
              {pick.volatility_flag && (
                <span 
                  className="text-[14px] cursor-help" 
                  title={`Intel: ${pick.volatility_reason || 'Volatility detected'}`}
                  style={{ filter: 'drop-shadow(0 0 2px #f97316)' }}
                >
                  🗞️
                </span>
              )}
              {pick.revenge_game && (
                <span 
                  className="text-[14px] cursor-help" 
                  title={`Revenge Game vs ${pick.revenge_opponent || 'former team'}`}
                  style={{ filter: 'drop-shadow(0 0 2px #22c55e)' }}
                >
                  🗡️
                </span>
              )}
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{pick.team || pick.team_abbr || '---'}</span>
              <span>· {pick.stat_type}</span>
            </div>
          </div>
        </div>
        
        {/* Tier Emblem Indicator Row (Fire/Bullet/Gem) - UNDER the header */}
        {displayLevel > 0 && (
          <div className="flex items-center justify-between mb-2 px-1">
            {renderIndicators()}
            <span className={`text-[10px] font-medium ${theme.text}`}>
              {getLevelLabel(displayLevel)}
            </span>
          </div>
        )}
        
        {/* Stats Section */}
        <div className="space-y-1.5">
          {/* Line Info */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Line:</span>
            <div className="flex items-center gap-1">
              <span className="text-white font-bold">{lineValue}</span>
              <span className={`${theme.priceColor} font-mono`}>
                {priceValue > 0 ? `+${priceValue}` : priceValue}
              </span>
            </div>
          </div>
          
          {/* Gap Ratio */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Gap:</span>
            <span className="text-yellow-400 font-medium">
              {pick.gap_pct > 0 ? '+' : ''}{pick.gap_pct || 0}% above std
            </span>
          </div>
          
          {/* Value Score Bar */}
          <div className="mt-2">
            <div className="flex items-center justify-between text-[10px] mb-1">
              <span className="text-zinc-500">Value Score</span>
              <span className={`font-bold ${theme.text}`}>
                {(scoreValue * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all bg-gradient-to-r ${theme.scoreBarHigh}`}
                style={{ width: `${Math.min(100, scoreValue * 100)}%` }}
              />
            </div>
          </div>
          
          {/* Hit Rate Info */}
          <div className="flex items-center justify-between text-[10px] text-zinc-500 mt-1">
            <span>L10: <span className="text-white">{pick.h10_rate || 0}%</span></span>
            <span>L5: <span className="text-white">{pick.h5_rate || 0}%</span></span>
            {pick.is_hot_streak && (
              <span className={`${theme.text} font-medium`}>
                {emblem === 'fire' ? '🔥' : emblem === 'gem' ? '💎' : '🎯'} STREAK
              </span>
            )}
          </div>
          
          {/* AI Explainer - The Vision */}
          {(pick.intel_briefing || pick.insight_summary) && (
            <div className={`mt-2 pt-2 border-t ${theme.borderLine}`}>
              <div className="flex items-center gap-1 mb-1">
                <Zap className="w-2.5 h-2.5 text-purple-400" />
                <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
              </div>
              <p className="text-[10px] text-purple-300/80 leading-relaxed italic">
                "{pick.intel_briefing || pick.insight_summary}"
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
});

UniversalPickCard.displayName = 'UniversalPickCard';

// Legacy alias for backward compatibility
const RadarCard = UniversalPickCard;


// ==================== UNIVERSAL PARLAY TICKET ====================
// Single unified ticket template for all 3 tiers (War Zone, Front Lines, Safe Haven)
// Only visual differences: colorTheme (red/amber/green) and emblem (fire/bullet/gem)

const UniversalParlayTicket = memo(({ 
  parlay, 
  pickCount, 
  onClick,
  colorTheme = 'red',  // 'red' | 'amber' | 'green'
  emblem = 'fire'       // 'fire' | 'bullet' | 'gem'
}) => {
  const picks = parlay?.picks || [];
  const payoutMultiplier = parlay?.estimated_payout || 0;
  const combinedProb = parlay?.combined_probability || parlay?.reliability || 0;
  const payoutRange = parlay?.payout_range || '';
  const lineupValid = parlay?.lineup_valid ?? true;
  const lineupStatus = parlay?.lineup_status || 'Valid (Multi-Team)';
  const teamCount = parlay?.team_count || 0;
  const hasOpponentPair = parlay?.has_opponent_pair || false;
  const badge = parlay?.badge || '';
  
  // Theme colors - RED (War Zone), AMBER (Front Lines), GREEN (Safe Haven)
  const themeColors = {
    red: { 
      bg: 'from-red-950/50', 
      border: 'border-red-500/40', 
      text: 'text-red-400', 
      badge: 'bg-red-500/20',
      glow: 'rgba(239, 68, 68, 0.3)',
      statusValid: 'bg-red-500/20 text-red-300',
      meterHigh: 'from-red-500 to-red-400',
      meterMid: 'from-orange-500 to-orange-400'
    },
    amber: { 
      bg: 'from-amber-950/50', 
      border: 'border-amber-500/40', 
      text: 'text-amber-400', 
      badge: 'bg-amber-500/20',
      glow: 'rgba(245, 158, 11, 0.3)',
      statusValid: 'bg-amber-500/20 text-amber-300',
      meterHigh: 'from-amber-500 to-amber-400',
      meterMid: 'from-yellow-500 to-yellow-400'
    },
    green: { 
      bg: 'from-green-950/50', 
      border: 'border-green-500/40', 
      text: 'text-green-400', 
      badge: 'bg-green-500/20',
      glow: 'rgba(34, 197, 94, 0.3)',
      statusValid: 'bg-green-500/20 text-green-300',
      meterHigh: 'from-green-500 to-green-400',
      meterMid: 'from-emerald-500 to-emerald-400'
    }
  };
  const theme = themeColors[colorTheme] || themeColors.red;
  
  // Render emblem icon
  const renderEmblem = (size = 16) => {
    switch(emblem) {
      case 'fire': return <FireEmblem size={size} />;
      case 'bullet': return <BulletEmblem size={size + 2} />;
      case 'gem': return <GemEmblem size={size} />;
      default: return <FireEmblem size={size} />;
    }
  };
  
  // Render pick emblem (smaller, for list items)
  const renderPickEmblem = (pick) => {
    // For picks, show fire/bullet/gem based on tier emblem
    switch(emblem) {
      case 'fire':
        return pick.has_heat_boost ? (
          <Flame className="w-3 h-3 text-orange-400 flex-shrink-0" fill="currentColor" />
        ) : (
          <span className="text-[10px]" style={{ filter: 'drop-shadow(0 0 2px #ff6b35)' }}>🔥</span>
        );
      case 'bullet':
        return <BulletEmblem size={14} />;
      case 'gem':
        return <span className="text-[10px]" style={{ color: '#00BFFF', textShadow: '0 0 4px #00BFFF' }}>💎</span>;
      default:
        return <span className="text-[10px]">🔥</span>;
    }
  };
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${theme.bg} to-zinc-950 ${theme.border}
        hover:scale-[1.02] transition-all duration-200 cursor-pointer
        overflow-hidden min-h-[280px] ${!lineupValid ? 'opacity-60' : ''}
      `}
      style={{ boxShadow: `0 0 20px ${theme.glow}` }}
      onClick={onClick}
      data-testid={`parlay-ticket-${colorTheme}-${pickCount}`}
    >
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div>
              <div className={`text-sm font-bold ${theme.text}`}>
                {parlay?.name || `${pickCount}-PICK`}
              </div>
              <div className="text-[10px] text-zinc-500">{parlay?.description || ''}</div>
            </div>
          </div>
          
          {/* Payout Badge */}
          <Badge className={`${theme.badge} ${theme.text} border-none text-xs font-bold px-2 py-1`}>
            {payoutMultiplier}x
          </Badge>
        </div>
        
        {/* Lineup Status Indicator */}
        <div className={`flex items-center gap-1 mb-2 text-[9px] px-2 py-0.5 rounded ${
          lineupValid 
            ? hasOpponentPair ? 'bg-blue-500/20 text-blue-300' : theme.statusValid
            : 'bg-red-500/20 text-red-300'
        }`}>
          {lineupValid ? (
            <>
              <CheckCircle className="w-3 h-3" />
              <span>{lineupStatus}</span>
              {teamCount > 0 && <span className="text-zinc-400">({teamCount} teams)</span>}
            </>
          ) : (
            <>
              <XCircle className="w-3 h-3" />
              <span>INVALID - Single Team</span>
            </>
          )}
        </div>
        
        {/* Reliability/Combined Prob Meter */}
        <div className="mb-2">
          <div className="flex items-center justify-between text-[10px] mb-1">
            <span className="text-zinc-400">{emblem === 'gem' ? 'Reliability' : 'Combined Prob'}</span>
            <span className={`font-bold ${theme.text}`}>
              {combinedProb}%
            </span>
          </div>
          <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div 
              className={`h-full transition-all duration-500 bg-gradient-to-r ${theme.meterHigh}`}
              style={{ width: `${Math.min(combinedProb, 100)}%` }}
            />
          </div>
        </div>
        
        {/* Picks List */}
        <div className="space-y-1.5 mb-3">
          {picks.slice(0, 4).map((pick, idx) => (
            <div 
              key={`${pick.player_name}-${pick.stat_type}-${idx}`}
              className="bg-zinc-900/50 rounded px-2 py-1"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-white truncate">{pick.player_name}</span>
                  <span className="text-[10px] text-zinc-500">{pick.team}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-zinc-400">{pick.stat_type}</span>
                  <span className="text-xs font-bold text-white">{pick.line}</span>
                  {(pick.intel_briefing || pick.insight_summary) && (
                    <Zap className="w-3 h-3 text-purple-400" title="Has AI Vision" />
                  )}
                </div>
              </div>
            </div>
          ))}
          {picks.length > 4 && (
            <div className="text-[10px] text-zinc-500 text-center">
              +{picks.length - 4} more picks
            </div>
          )}
        </div>
        
        {/* Stats Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-zinc-800/50">
          <div className="flex items-center gap-1 text-[10px]">
            <Target className="w-3 h-3 text-zinc-500" />
            <span className="text-zinc-500">Picks:</span>
            <span className={`font-bold ${theme.text}`}>{pickCount}</span>
          </div>
          {payoutRange && (
            <div className="text-[10px] text-zinc-500">
              Range: <span className="text-white">{payoutRange}</span>
            </div>
          )}
          {badge && (
            <Badge className={`${theme.badge} ${theme.text} border-none text-[9px] px-1.5 py-0.5`}>
              {badge}
            </Badge>
          )}
        </div>
      </div>
    </Card>
  );
});

UniversalParlayTicket.displayName = 'UniversalParlayTicket';

// ParlayCard - DELETED (now uses UniversalParlayTicket)
// ReconCard - DELETED (now uses UniversalParlayTicket)


// ==================== SWIPEABLE SECTION COMPONENTS ====================
// Mobile-first swipeable card sections with Tinder-style navigation

// War Zone Swipeable Section - RED theme + FIRE emblem
const WarZoneSwipeSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(picks.length);
  
  return (
    <div data-testid="war-zone-section" className="war-zone-section">
      <div className="flex items-center justify-between mb-2 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-red-400">WAR ZONE</span>
          <Badge className="bg-red-950/50 text-red-400 border-red-800/50 text-[10px] hidden sm:inline-flex">
            TOP 10 DEMON PLAYS
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          King of Longshots | Dangerous but Profitable
        </div>
      </div>
      
      {/* Mobile: Horizontal scroll / Desktop: Grid */}
      <div className="relative">
        <SwipeHint show={showHint} accentColor="orange" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-2"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {picks.map((pick, idx) => (
            <div 
              key={`${pick.player_name}-${pick.stat_type}-${pick.demon_line}-${idx}`} 
              className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
            >
              <UniversalPickCard 
                pick={pick} 
                rank={idx + 1}
                onClick={() => onPickClick(pick)}
                tMinusGames={tMinusGames}
                colorTheme="red"
                emblem="fire"
              />
            </div>
          ))}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={picks.length} accentColor="orange" />
    </div>
  );
});

WarZoneSwipeSection.displayName = 'WarZoneSwipeSection';

// Goblin Recon Swipeable Section
const GoblinReconSwipeSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(picks.length);
  
  return (
    <div data-testid="recon-section" className="goblin-recon-section">
      <div className="flex items-center justify-between mb-2 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-green-400">SAFE HAVEN</span>
          <Badge className="bg-green-950/50 text-green-400 border-green-800/50 text-[10px] hidden sm:inline-flex">
            TOP 10 GOBLIN PLAYS
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          Consistent Vault-Hunters | Stack Green
        </div>
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="green" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-2"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
        {picks.map((pick, idx) => (
          <div 
            key={`${pick.player_name}-${pick.stat_type}-${pick.goblin_line}-${idx}`} 
            className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
          >
            <UniversalPickCard 
              pick={pick} 
              rank={idx + 1}
              onClick={() => onPickClick(pick)}
              tMinusGames={tMinusGames}
              colorTheme="green"
              emblem="gem"
            />
          </div>
        ))}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={picks.length} accentColor="green" />
    </div>
  );
});

GoblinReconSwipeSection.displayName = 'GoblinReconSwipeSection';

// Gauntlet (Demon Parlay) Swipeable Section
const GauntletSwipeSection = memo(({ parlayData, onParlayClick }) => {
  const parlays = [2, 3, 4, 5, 6].map(n => parlayData[`${n}_pick`]).filter(Boolean);
  const { containerRef, currentIndex, showHint } = useSwipeTracker(parlays.length);
  
  return (
    <div data-testid="gauntlet-section" className="mt-6">
      <div className="flex items-center justify-between mb-3 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-amber-400">THE GAUNTLET</span>
          <Badge className="bg-amber-950/50 text-amber-400 border-amber-800/50 text-[10px] hidden sm:inline-flex">
            PARLAY GENERATOR
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          Whale Scoring + Correlation Filter
        </div>
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="orange" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-3"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {[2, 3, 4, 5, 6].map(pickCount => {
            const parlay = parlayData[`${pickCount}_pick`];
            if (!parlay) return null;
            // Force lineup_valid to true and has_opponent_pair to false for consistent red styling
            const fixedParlay = { ...parlay, lineup_valid: true, lineup_status: 'Valid (Multi-Team)', has_opponent_pair: false };
            return (
              <div 
                key={`parlay-${pickCount}`} 
                className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
              >
                <UniversalParlayTicket
                  parlay={fixedParlay}
                  pickCount={pickCount}
                  onClick={() => onParlayClick(parlay)}
                  colorTheme="red"
                  emblem="fire"
                />
              </div>
            );
          })}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={parlays.length} accentColor="orange" />
      
      {/* Parlay Legend - Desktop only */}
      <div className="mt-2 hidden sm:flex items-center justify-center gap-4 text-[10px] text-zinc-500">
        <span><Flame className="w-3 h-3 inline text-orange-400" /> = Heat Boost (20%)</span>
        <span><Layers className="w-3 h-3 inline text-blue-400" /> = Same-Game Correlation</span>
        <span><TrendingUp className="w-3 h-3 inline text-green-400" /> = 30%+ Ceiling Frequency</span>
      </div>
    </div>
  );
});

GauntletSwipeSection.displayName = 'GauntletSwipeSection';

// The Shield Swipeable Section - High Reliability Parlays (under Safe Haven)
const TheShieldSwipeSection = memo(({ reconData, onParlayClick }) => {
  const tiers = ['daily_double', 'green_ladder_3', 'green_ladder_4', 'green_stack_5', 'fortress_flex'];
  const parlays = tiers.map(t => reconData[t]).filter(Boolean);
  const { containerRef, currentIndex, showHint } = useSwipeTracker(parlays.length);
  
  return (
    <div data-testid="the-shield-section" className="mt-6">
      <div className="flex items-center justify-between mb-3 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-emerald-400">THE SHIELD</span>
          <Badge className="bg-emerald-950/50 text-emerald-400 border-emerald-800/50 text-[10px] hidden sm:inline-flex">
            HIGH RELIABILITY PARLAYS
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          Floor Scoring + 88%+ Hit Rate
        </div>
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="green" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-3"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {tiers.map(tier => {
            const parlay = reconData[tier];
            if (!parlay) return null;
            const pickCount = tier === 'daily_double' ? 2 : 
                              tier === 'green_ladder_3' ? 3 : 
                              tier === 'green_ladder_4' ? 4 : 
                              tier === 'green_stack_5' ? 5 : 6;
            return (
              <div 
                key={`recon-${tier}`} 
                className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
              >
                <UniversalParlayTicket
                  parlay={parlay}
                  pickCount={pickCount}
                  onClick={() => onParlayClick(parlay)}
                  colorTheme="green"
                  emblem="gem"
                />
              </div>
            );
          })}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={parlays.length} accentColor="green" />
      
      {/* Sapphire Gem Legend - Desktop only */}
      <div className="mt-2 hidden sm:flex items-center justify-center gap-4 text-[10px] text-zinc-500">
        <span style={{ color: '#00BFFF' }}>💎</span><span>= 70%+</span>
        <span style={{ color: '#00BFFF' }}>💎💎</span><span>= 80%+</span>
        <span style={{ color: '#00BFFF' }}>💎💎💎</span><span>= 90%+</span>
        <span style={{ color: '#00BFFF' }}>💎💎💎💎</span><span>= 100% FORTRESS</span>
      </div>
    </div>
  );
});

TheShieldSwipeSection.displayName = 'TheShieldSwipeSection';

// Front Lines Swipeable Section - AMBER theme + BULLET emblem (5-18% gap)
const FrontLinesSwipeSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(picks.length);
  
  return (
    <div data-testid="front-lines-section" className="front-lines-section">
      <div className="flex items-center justify-between mb-2 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-amber-400">FRONT LINES</span>
          <Badge className="bg-amber-950/50 text-amber-400 border-amber-800/50 text-[10px] hidden sm:inline-flex">
            TOP 10 MID-TIER PLAYS
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          Tactical Alternates | 5-18% from Standard
        </div>
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="amber" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-2"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {picks.slice(0, 10).map((pick, idx) => (
            <div 
              key={`${pick.player_name}-${pick.stat_type}-${pick.line}-${idx}`} 
              className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
            >
              <UniversalPickCard 
                pick={pick} 
                rank={idx + 1}
                onClick={() => onPickClick(pick)}
                tMinusGames={tMinusGames}
                colorTheme="amber"
                emblem="bullet"
              />
            </div>
          ))}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={Math.min(picks.length, 10)} accentColor="amber" />
    </div>
  );
});

FrontLinesSwipeSection.displayName = 'FrontLinesSwipeSection';

// Front Lines Parlay Section - AMBER theme + BULLET emblem
// Generates 2-leg through 6-leg parlay tickets from Front Lines picks
const FrontLinesParlaySection = memo(({ picks, onParlayClick }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(5); // 5 parlay tiers
  
  // Parlay tier names for The Strike
  const parlayNames = {
    2: { name: 'Quick Strike', description: '2 tactical picks - fast execution' },
    3: { name: 'Triple Tap', description: '3 picks diversified across games' },
    4: { name: 'Fire Squad', description: '4 picks for balanced firepower' },
    5: { name: 'Full Clip', description: '5 picks stacked for premium payout' },
    6: { name: 'Armory', description: 'PrizePicks Flex Play - Win on 5 OR 6 hits!' }
  };
  
  // Generate parlay data from picks (2-leg through 6-leg)
  const generateParlays = () => {
    if (!picks || picks.length < 2) return [];
    
    const parlayTiers = [2, 3, 4, 5, 6];
    return parlayTiers.map(count => {
      const parlayPicks = picks.slice(0, count);
      if (parlayPicks.length < count) return null;
      
      // Calculate combined probability
      const combinedProb = parlayPicks.reduce((acc, pick) => {
        const rate = (pick.h10_rate || 50) / 100;
        return acc * rate;
      }, 1) * 100;
      
      // Estimate payout multiplier
      const payoutMultiplier = Math.round(Math.pow(1.8, count) * 10) / 10;
      
      const tierInfo = parlayNames[count];
      
      return {
        name: tierInfo.name,
        description: tierInfo.description,
        picks: parlayPicks,
        estimated_payout: payoutMultiplier,
        combined_probability: Math.round(combinedProb * 10) / 10,
        reliability: Math.round(combinedProb * 10) / 10,
        payout_range: `${payoutMultiplier - 1}x - ${payoutMultiplier + 2}x`,
        lineup_valid: true,
        lineup_status: 'Valid (Multi-Team)',
        team_count: new Set(parlayPicks.map(p => p.team)).size,
        badge: count === 2 ? 'QUICK' : count === 6 ? 'FLEX' : ''
      };
    }).filter(Boolean);
  };
  
  const parlays = generateParlays();
  
  if (parlays.length === 0) return null;
  
  return (
    <div data-testid="the-strike-section" className="mt-6">
      <div className="flex items-center justify-between mb-3 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-amber-400">THE STRIKE</span>
          <Badge className="bg-amber-950/50 text-amber-400 border-amber-800/50 text-[10px] hidden sm:inline-flex">
            TACTICAL PARLAYS
          </Badge>
        </div>
        <div className="text-[10px] text-zinc-500 hidden sm:block">
          Front Lines Parlay Combinations
        </div>
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="amber" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-3"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {parlays.map((parlay, idx) => (
            <div 
              key={`strike-parlay-${idx + 2}`} 
              className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
            >
              <UniversalParlayTicket
                parlay={parlay}
                pickCount={idx + 2}
                onClick={() => onParlayClick(parlay)}
                colorTheme="amber"
                emblem="bullet"
              />
            </div>
          ))}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={parlays.length} accentColor="amber" />
    </div>
  );
});

FrontLinesParlaySection.displayName = 'FrontLinesParlaySection';

// FrontLinesCard - DELETED (now uses UniversalPickCard with amber theme + bullet emblem)

// Trending Players Swipeable Section
const TrendingSwipeSection = memo(({ players, linesLoaded, onPlayerClick, injuryAlerts }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(players.length);
  
  return (
    <div data-testid="trending-section">
      <div className="flex items-center gap-2 mb-2 px-4 sm:px-0">
        <Flame className="w-4 h-4 text-orange-500" />
        <span className="text-sm font-bold text-white">Most Popular Today</span>
        <Star className="w-4 h-4 text-yellow-500" />
      </div>
      
      <div className="relative">
        <SwipeHint show={showHint} accentColor="orange" />
        <div 
          ref={containerRef} 
          className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-2"
          style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
        >
          {players.map((player, idx) => (
            <div 
              key={player.player_name} 
              className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[340px] sm:w-auto sm:max-w-none"
            >
              <TrendingCard 
                player={player} 
                rank={idx + 1}
                linesLoaded={linesLoaded}
                onClick={() => onPlayerClick(player.player_name)}
                injuryAlerts={injuryAlerts}
              />
            </div>
          ))}
        </div>
      </div>
      
      <SwipeIndicator current={currentIndex} total={players.length} accentColor="orange" />
    </div>
  );
});

TrendingSwipeSection.displayName = 'TrendingSwipeSection';

// ==================== MOST POPULAR BETS (LIVE TICKER) ====================
// Shows Top 20 most heavily bet props with smart polling

// Individual bet card for Most Popular section
const PopularBetCard = memo(({ bet, rank, onClick }) => {
  const lineType = bet.line_type || 'standard';
  
  // Theme based on line type - symbols only, no text
  const themes = {
    demon: { 
      border: 'border-red-500/40', 
      bg: 'from-red-950/30', 
      badge: 'bg-red-500/20 text-red-400 border-red-500/30',
      symbol: '🔥',
      showBadge: true,
      glow: 'rgba(239, 68, 68, 0.2)'
    },
    goblin: { 
      border: 'border-green-500/40', 
      bg: 'from-green-950/30', 
      badge: 'bg-green-500/20 text-green-400 border-green-500/30',
      symbol: '💎',
      showBadge: true,
      glow: 'rgba(34, 197, 94, 0.2)'
    },
    standard: { 
      border: 'border-zinc-700', 
      bg: 'from-zinc-900/50', 
      badge: '',
      symbol: '',
      showBadge: false,
      glow: 'rgba(100, 100, 100, 0.1)'
    }
  };
  const theme = themes[lineType];
  
  return (
    <Card 
      className={`bg-gradient-to-br ${theme.bg} to-zinc-950 ${theme.border} hover:scale-[1.02] transition-all cursor-pointer overflow-hidden`}
      style={{ boxShadow: `0 0 15px ${theme.glow}` }}
      onClick={onClick}
      data-testid={`popular-bet-${rank}`}
    >
      <div className="p-3">
        {/* Header with rank and line type symbol */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-full bg-orange-500/20 flex items-center justify-center">
              <span className="text-xs font-bold text-orange-400">#{rank}</span>
            </div>
            {theme.showBadge && (
              <span className="text-sm">{theme.symbol}</span>
            )}
          </div>
          <div className="text-[10px] text-zinc-500">
            {bet.h10_rate}% L10
          </div>
        </div>
        
        {/* Player info */}
        <div className="flex items-center gap-2 mb-2">
          {bet.photo_url ? (
            <img src={bet.photo_url} alt="" className="w-10 h-10 rounded-full object-cover bg-zinc-800" />
          ) : (
            <div className="w-10 h-10 rounded-full bg-zinc-800 flex items-center justify-center">
              <User className="w-5 h-5 text-zinc-500" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-white truncate">{bet.player_name}</div>
            <div className="text-[10px] text-zinc-500">{bet.team}</div>
          </div>
        </div>
        
        {/* Bet details */}
        <div className="bg-zinc-900/50 rounded p-2 mb-2">
          <div className="flex items-center justify-between">
            <div className="text-xs text-zinc-400">{bet.stat_type}</div>
            <div className="flex items-center gap-1">
              <span className="text-sm font-bold text-white">{bet.line}</span>
              <TrendingUp className="w-3 h-3 text-green-400" />
              <span className="text-[10px] text-green-400 uppercase">{bet.direction}</span>
            </div>
          </div>
        </div>
        
        {/* Popularity indicator */}
        <div className="flex items-center justify-between text-[10px]">
          <div className="flex items-center gap-1 text-orange-400">
            <Flame className="w-3 h-3" />
            <span>Hot Bet</span>
          </div>
          {bet.gap_pct && bet.gap_pct !== 0 && (
            <span className="text-yellow-400">
              {bet.gap_pct > 0 ? '+' : ''}{bet.gap_pct}% value
            </span>
          )}
        </div>
      </div>
    </Card>
  );
});

PopularBetCard.displayName = 'PopularBetCard';

// Awaiting Action Empty State for Most Popular section
const AwaitingActionState = memo(() => (
  <div 
    data-testid="awaiting-action-state"
    className="bg-gradient-to-br from-zinc-900/50 to-zinc-950 border border-zinc-800/50 rounded-lg p-8 text-center"
  >
    <div className="flex flex-col items-center gap-4">
      {/* Pulsing radar animation */}
      <div className="relative">
        <div className="w-16 h-16 rounded-full bg-orange-500/10 flex items-center justify-center">
          <Radio className="w-8 h-8 text-orange-400" />
        </div>
        <div className="absolute inset-0 rounded-full border-2 border-orange-400/30 animate-ping" />
        <div className="absolute inset-[-8px] rounded-full border border-orange-400/20 animate-pulse" />
      </div>
      
      {/* Status text */}
      <div>
        <h3 className="text-lg font-semibold text-white mb-1">Awaiting Public Action...</h3>
        <p className="text-sm text-zinc-500">Compiling live bets from today's slate</p>
      </div>
      
      {/* Scanning indicator */}
      <div className="flex items-center gap-2 text-[11px] text-orange-400/80">
        <div className="w-2 h-2 rounded-full bg-orange-400 animate-pulse" />
        <span>Scanning PrizePicks for live lines</span>
      </div>
      
      {/* Info text */}
      <p className="text-[10px] text-zinc-600 max-w-xs">
        Popular bets will appear here once today's games are posted and public action begins.
        Check back closer to tip-off times.
      </p>
    </div>
  </div>
));

AwaitingActionState.displayName = 'AwaitingActionState';

// Most Popular Bets Live Ticker Section
const MostPopularBetsSection = memo(({ bets, lastUpdated, onBetClick, isLoading, status }) => {
  const { containerRef, currentIndex, showHint } = useSwipeTracker(Math.min(bets?.length || 0, 20));
  
  // Format last updated time
  const formatLastUpdated = (isoString) => {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffSeconds = Math.floor((now - date) / 1000);
      if (diffSeconds < 60) return `${diffSeconds}s ago`;
      if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
      return date.toLocaleTimeString();
    } catch {
      return '';
    }
  };
  
  // Always render the section - show empty state if no bets
  const hasBets = bets && bets.length > 0;
  
  return (
    <div data-testid="most-popular-bets-section" className="mt-6">
      <div className="flex items-center justify-between mb-2 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <Flame className="w-4 h-4 text-orange-500" />
          <span className="text-sm font-bold text-white">MOST POPULAR BETS</span>
          <Badge className={`${hasBets ? 'bg-orange-500/20 text-orange-400 border-orange-500/30' : 'bg-zinc-700/50 text-zinc-400 border-zinc-600/30'} text-[10px] hidden sm:inline-flex`}>
            {hasBets ? 'LIVE TICKER' : 'SCANNING'}
          </Badge>
          {isLoading && (
            <div className="w-3 h-3 border border-orange-400 border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        <div className="text-[10px] text-zinc-500 flex items-center gap-1">
          <Radio className={`w-3 h-3 ${hasBets ? 'text-green-400' : 'text-yellow-400'} animate-pulse`} />
          <span>{hasBets ? `Updated ${formatLastUpdated(lastUpdated)}` : 'Searching for live lines...'}</span>
        </div>
      </div>
      
      {/* Show empty state or bets */}
      {!hasBets ? (
        <AwaitingActionState />
      ) : (
        <>
          <div className="relative">
            <SwipeHint show={showHint} accentColor="orange" />
            <div 
              ref={containerRef} 
              className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide gap-3 px-4 pb-2 sm:grid sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5 sm:overflow-visible sm:px-0 sm:gap-2"
              style={{ WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none', msOverflowStyle: 'none' }}
            >
              {bets.slice(0, 20).map((bet, idx) => (
                <div 
                  key={`${bet.player_name}-${bet.stat_type}-${bet.line}-${idx}`} 
                  className="snap-center flex-shrink-0 w-[calc(100vw-48px)] max-w-[280px] sm:w-auto sm:max-w-none"
                >
              <PopularBetCard 
                bet={bet} 
                rank={idx + 1}
                onClick={() => onBetClick(bet)}
              />
            </div>
          ))}
            </div>
          </div>
          
          <SwipeIndicator current={currentIndex} total={Math.min(bets.length, 20)} accentColor="orange" />
          
          {/* Legend */}
          <div className="mt-2 hidden sm:flex items-center justify-center gap-4 text-[10px] text-zinc-500">
            <span><span className="text-red-400">DEMON</span> = High Payout Lines</span>
            <span><span className="text-green-400">GOBLIN</span> = High Probability Lines</span>
            <span><span className="text-zinc-400">STANDARD</span> = Base Lines</span>
          </div>
        </>
      )}
    </div>
  );
});

MostPopularBetsSection.displayName = 'MostPopularBetsSection';

// ==================== EXPANDED PARLAY VIEW ====================
// Shows all picks in a parlay with player cards and bet details

// Individual pick card with expandable hit rate dropdown
const ParlayPickCard = memo(({ pick, idx, isRecon, colors, playerData, onPickClick }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Get hit rate data from pick
  const h5Over = pick.h5_over || 0;
  const h5Games = pick.h5_games || 0;
  const h10Over = pick.h10_over || 0;
  const h10Games = pick.h10_games || 0;
  const seasonAvg = pick.season_avg || 0;
  
  // Calculate percentages
  const h5Pct = h5Games > 0 ? Math.round((h5Over / h5Games) * 100) : 0;
  const h10Pct = h10Games > 0 ? Math.round((h10Over / h10Games) * 100) : (pick.h10_rate || 0);
  const weightedPct = pick.weighted_hit_rate || h10Pct;
  
  // Color helper for percentages
  const getPctColor = (pct) => {
    if (pct >= 70) return 'text-green-400';
    if (pct >= 50) return 'text-yellow-400';
    if (pct >= 30) return 'text-orange-400';
    return 'text-red-400';
  };
  
  return (
    <div 
      className={`
        bg-zinc-900/70 rounded-xl border border-zinc-800 overflow-hidden
        hover:border-${colors.accent}-500/50 transition-all
      `}
      data-testid={`parlay-pick-${idx}`}
    >
      {/* Main Pick Info - Clickable to expand */}
      <div 
        className="p-3 cursor-pointer"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        {/* Player Info Row */}
        <div className="flex items-center gap-3 mb-3">
          {/* Player Headshot */}
          <PlayerHeadshot 
            nbaId={pick.nba_id || playerData?.nba_id}
            playerName={pick.player_name}
            team={pick.team}
            photoUrl={pick.photo_url || playerData?.photo_url}
            size="lg"
            className="ring-2 ring-zinc-700"
          />
          
          {/* Player Details */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-white font-bold text-lg truncate">{pick.player_name}</span>
              <span className="text-xs text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">{pick.team}</span>
            </div>
            {pick.opponent_team && (
              <div className="text-xs text-zinc-500 mt-0.5">
                vs {pick.opponent_team}
              </div>
            )}
          </div>
          
          {/* Pick Number Badge + Expand Icon */}
          <div className="flex items-center gap-2">
            <ChevronDown className={`w-5 h-5 text-zinc-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
            <div className={`w-8 h-8 rounded-full bg-${colors.accent}-500/30 flex items-center justify-center`}>
              <span className={`text-sm font-bold ${colors.text}`}>#{idx + 1}</span>
            </div>
          </div>
        </div>
        
        {/* Bet Details */}
        <div className={`
          flex items-center justify-between
          bg-zinc-800/50 rounded-lg px-3 py-2
          border-l-4 ${isRecon ? 'border-green-500' : 'border-red-500'}
        `}>
          <div className="flex items-center gap-3">
            {isRecon ? (
              <GoblinIcon size={20} />
            ) : (
              <DemonIcon size={20} />
            )}
            <div>
              <div className="text-white font-bold text-xl">
                {pick.line} <span className="text-green-400 text-sm">{pick.direction || 'Over'}</span>
              </div>
              <div className="text-xs text-zinc-400">{pick.stat_type}</div>
            </div>
          </div>
          
          {/* Quick Hit Rate */}
          <div className="text-right flex items-center gap-2">
            <div className="flex items-center gap-1">
              <span className="text-xs text-zinc-500">L10:</span>
              <span className={`text-lg font-bold ${getPctColor(h10Pct)}`}>
                {h10Pct}%
              </span>
            </div>
            {pick.is_recon_lock && (
              <Badge className="bg-emerald-500/30 text-emerald-300 border-none text-[10px]">
                LOCK
              </Badge>
            )}
            {pick.has_heat_boost && (
              <Flame className="w-4 h-4 text-orange-400" />
            )}
          </div>
        </div>
        
        {/* THE VISION - AI Insight Preview (Always visible) */}
        {(pick.intel_briefing || pick.insight_summary) && (
          <div className="mt-2 bg-gradient-to-r from-purple-950/40 via-zinc-900/50 to-purple-950/40 rounded-lg px-3 py-2 border border-purple-800/30">
            <div className="flex items-center gap-1.5 mb-1">
              <Zap className="w-3 h-3 text-purple-400" />
              <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
            </div>
            <p className="text-[11px] text-purple-200/80 leading-relaxed italic">
              "{pick.intel_briefing || pick.insight_summary}"
            </p>
          </div>
        )}
      </div>
      
      {/* Expanded Hit Rate Dropdown */}
      {isExpanded && (
        <div className="px-3 pb-3 border-t border-zinc-700/50">
          <div className="bg-zinc-950/70 rounded-lg p-3 mt-2 space-y-2">
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider font-semibold mb-2">
              Stat Insight for {pick.line}+ {pick.direction || 'Over'}
            </div>
            
            {/* L5 Hit Rate */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400 w-24">Last 5 Games:</span>
                <span className={`text-sm font-bold ${getPctColor(h5Pct)}`}>
                  {h5Games > 0 ? `${h5Over}/${h5Games}` : '---'}
                </span>
              </div>
              <div className={`text-lg font-bold ${getPctColor(h5Pct)}`}>
                {h5Games > 0 ? `${h5Pct}%` : '---'}
              </div>
            </div>
            
            {/* L10 Hit Rate */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400 w-24">Last 10 Games:</span>
                <span className={`text-sm font-bold ${getPctColor(h10Pct)}`}>
                  {h10Games > 0 ? `${h10Over}/${h10Games}` : '---'}
                </span>
              </div>
              <div className={`text-lg font-bold ${getPctColor(h10Pct)}`}>
                {h10Games > 0 ? `${h10Pct}%` : '---'}
              </div>
            </div>
            
            {/* Weighted Hit Rate (for Recon) */}
            {isRecon && weightedPct > 0 && (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400 w-24">Weighted Rate:</span>
                  <span className={`text-sm font-bold ${getPctColor(weightedPct)}`}>
                    L5×2 + L10
                  </span>
                </div>
                <div className={`text-lg font-bold ${getPctColor(weightedPct)}`}>
                  {weightedPct}%
                </div>
              </div>
            )}
            
            {/* Season Average */}
            <div className="flex items-center justify-between bg-zinc-800/50 rounded px-2 py-1.5 mt-2">
              <span className="text-xs text-zinc-400">Season Average</span>
              <div className="flex items-center gap-2">
                <span className="text-white font-bold">{seasonAvg > 0 ? seasonAvg.toFixed(1) : '---'}</span>
                {seasonAvg > 0 && (
                  <span className={`text-xs px-1.5 py-0.5 rounded ${seasonAvg > pick.line ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                    {seasonAvg > pick.line ? `+${(seasonAvg - pick.line).toFixed(1)} above` : `${(seasonAvg - pick.line).toFixed(1)} below`}
                  </span>
                )}
              </div>
            </div>
            
            {/* Floor Score (for Recon) */}
            {isRecon && pick.floor_score !== undefined && (
              <div className="flex items-center justify-between bg-emerald-950/30 rounded px-2 py-1.5 border border-emerald-800/30">
                <span className="text-xs text-emerald-400">Floor Score</span>
                <div className="flex items-center gap-2">
                  <span className="text-emerald-300 font-bold">{pick.floor_score?.toFixed(1) || '---'}</span>
                  {pick.is_recon_lock && (
                    <Badge className="bg-emerald-500/30 text-emerald-300 border-none text-[9px]">
                      FLOOR ≥ LINE
                    </Badge>
                  )}
                </div>
              </div>
            )}
            
            {/* THE VISION - Full AI Analysis Section */}
            {(pick.intel_briefing || pick.insight_summary) && (
              <div className="mt-3 pt-3 border-t border-purple-800/30">
                <div className="flex items-center gap-1.5 mb-2">
                  <Zap className="w-3.5 h-3.5 text-purple-400" />
                  <span className="text-[10px] text-purple-400 uppercase tracking-wider font-semibold">THE VISION</span>
                  <span className="text-[9px] text-zinc-600">AI Analysis</span>
                </div>
                
                {/* AI Insight Box */}
                <div className="bg-gradient-to-r from-purple-950/50 via-zinc-900 to-purple-950/50 rounded-lg px-3 py-2.5 border border-purple-700/40 relative overflow-hidden">
                  <div className="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-purple-500 to-purple-700" />
                  <p className="text-sm text-purple-200 leading-relaxed pl-2 italic">
                    "{pick.intel_briefing || pick.insight_summary}"
                  </p>
                </div>
                
                {/* AI Confidence Meter */}
                {pick.ai_confidence_rating !== undefined && (
                  <div className="mt-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[9px] text-zinc-500 uppercase tracking-wider">AI Confidence</span>
                      <span className={`text-xs font-bold ${
                        pick.ai_confidence_rating >= 80 ? 'text-green-400' :
                        pick.ai_confidence_rating >= 60 ? 'text-yellow-400' :
                        pick.ai_confidence_rating >= 40 ? 'text-orange-400' :
                        'text-red-400'
                      }`}>
                        {pick.ai_confidence_rating}%
                      </span>
                    </div>
                    <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                      <div 
                        className={`h-full rounded-full transition-all ${
                          pick.ai_confidence_rating >= 80 ? 'bg-gradient-to-r from-green-600 to-green-400' :
                          pick.ai_confidence_rating >= 60 ? 'bg-gradient-to-r from-yellow-600 to-yellow-400' :
                          pick.ai_confidence_rating >= 40 ? 'bg-gradient-to-r from-orange-600 to-orange-400' :
                          'bg-gradient-to-r from-red-600 to-red-400'
                        }`}
                        style={{ width: `${pick.ai_confidence_rating}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          
          {/* Click to view full ladder */}
          <button 
            onClick={(e) => {
              e.stopPropagation();
              onPickClick(pick);
            }}
            className="w-full mt-2 py-2 text-center text-xs text-zinc-400 hover:text-white bg-zinc-800/30 hover:bg-zinc-800/50 rounded transition-colors"
          >
            View {pick.player_name}'s full prop ladder →
          </button>
        </div>
      )}
    </div>
  );
});

ParlayPickCard.displayName = 'ParlayPickCard';

const ExpandedParlayView = memo(({ parlay, type, onClose, onPickClick, players }) => {
  if (!parlay) return null;
  
  const picks = parlay.picks || [];
  const isRecon = type === 'recon';
  
  // Get color scheme based on type
  const colors = isRecon
    ? { bg: 'from-emerald-950/90', border: 'border-emerald-500/50', text: 'text-emerald-400', accent: 'emerald' }
    : { bg: 'from-amber-950/90', border: 'border-amber-500/50', text: 'text-amber-400', accent: 'amber' };
  
  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      onClick={onClose}
      data-testid="expanded-parlay-overlay"
    >
      <div 
        className={`
          relative w-full max-w-2xl max-h-[85vh] overflow-auto
          bg-gradient-to-br ${colors.bg} to-zinc-950 ${colors.border} border-2
          rounded-xl shadow-2xl
        `}
        onClick={(e) => e.stopPropagation()}
        data-testid="expanded-parlay-modal"
      >
        {/* Header */}
        <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800 px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isRecon ? (
                <GoblinIcon size={24} />
              ) : (
                <DollarSign className={`w-6 h-6 ${colors.text}`} />
              )}
              <div>
                <h3 className={`text-lg font-bold ${colors.text}`}>{parlay.name}</h3>
                <p className="text-xs text-zinc-400">{parlay.description || `${picks.length} picks`}</p>
              </div>
            </div>
            
            <div className="flex items-center gap-3">
              {/* Stats badges */}
              <Badge className={`bg-${colors.accent}-500/20 ${colors.text} border-none`}>
                {parlay.estimated_payout}x Payout
              </Badge>
              {isRecon && parlay.reliability && (
                <Badge className="bg-green-500/20 text-green-400 border-none">
                  {parlay.reliability}% Reliable
                </Badge>
              )}
              
              {/* Close button */}
              <button 
                onClick={onClose}
                className="p-1.5 rounded-full bg-zinc-800 hover:bg-zinc-700 transition-colors"
                data-testid="close-parlay-modal"
              >
                <X className="w-5 h-5 text-zinc-400" />
              </button>
            </div>
          </div>
          
          {/* Lineup Status */}
          <div className={`mt-2 flex items-center gap-2 text-xs px-2 py-1 rounded-full w-fit ${
            parlay.lineup_valid 
              ? 'bg-green-500/20 text-green-300'
              : 'bg-red-500/20 text-red-300'
          }`}>
            {parlay.lineup_valid ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
            <span>{parlay.lineup_status || 'Valid Lineup'}</span>
            {parlay.team_count > 0 && <span className="text-zinc-400">• {parlay.team_count} teams</span>}
          </div>
        </div>
        
        {/* Picks List */}
        <div className="p-4 space-y-3">
          <div className="text-xs text-zinc-500 uppercase tracking-wider mb-2 flex items-center gap-2">
            <span>Parlay Picks ({picks.length})</span>
            <span className="text-zinc-600">• Click to expand stats</span>
          </div>
          
          {picks.map((pick, idx) => {
            const playerData = players?.find(p => p.player_name === pick.player_name);
            
            return (
              <ParlayPickCard
                key={`${pick.player_name}-${pick.stat_type}-${idx}`}
                pick={pick}
                idx={idx}
                isRecon={isRecon}
                colors={colors}
                playerData={playerData}
                onPickClick={onPickClick}
              />
            );
          })}
        </div>
        
        {/* Footer */}
        <div className="sticky bottom-0 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 px-4 py-3">
          <div className="flex items-center justify-between text-xs text-zinc-500">
            <span>Combined Probability: <span className="text-white font-bold">{parlay.combined_probability || parlay.flex_probability || '---'}%</span></span>
            <span>Est. Payout: <span className={`font-bold ${colors.text}`}>{parlay.payout_range || `~${parlay.estimated_payout}x`}</span></span>
          </div>
        </div>
      </div>
    </div>
  );
});

ExpandedParlayView.displayName = 'ExpandedParlayView';

// ==================== STAT CATEGORIES ====================

const STAT_CATEGORIES = {
  'PTS': { name: 'Points', markets: ['player_points', 'player_points_alternate'], icon: 'target', color: 'purple' },
  'REB': { name: 'Rebounds', markets: ['player_rebounds', 'player_rebounds_alternate'], icon: 'circle', color: 'blue' },
  'AST': { name: 'Assists', markets: ['player_assists', 'player_assists_alternate'], icon: 'zap', color: 'yellow' },
  'PRA': { name: 'Pts+Reb+Ast', markets: ['player_points_rebounds_assists', 'player_points_rebounds_assists_alternate'], icon: 'star', color: 'orange' },
  'P+R': { name: 'Pts+Reb', markets: ['player_points_rebounds', 'player_points_rebounds_alternate'], icon: 'layers', color: 'cyan' },
  'P+A': { name: 'Pts+Ast', markets: ['player_points_assists', 'player_points_assists_alternate'], icon: 'layers', color: 'pink' },
  'R+A': { name: 'Reb+Ast', markets: ['player_rebounds_assists', 'player_rebounds_assists_alternate'], icon: 'layers', color: 'emerald' },
  '3PM': { name: '3-PT Made', markets: ['player_threes', 'player_threes_alternate'], icon: 'crosshair', color: 'red' },
  'BLK': { name: 'Blocks', markets: ['player_blocks', 'player_blocks_alternate'], icon: 'shield', color: 'slate' },
  'STL': { name: 'Steals', markets: ['player_steals', 'player_steals_alternate'], icon: 'eye', color: 'amber' },
  'TO': { name: 'Turnovers', markets: ['player_turnovers', 'player_turnovers_alternate'], icon: 'alert', color: 'gray' },
};

// Get category key from market
const getCategoryKey = (market) => {
  for (const [key, config] of Object.entries(STAT_CATEGORIES)) {
    if (config.markets.includes(market)) {
      return key;
    }
  }
  return 'OTHER';
};

// Category color classes
const getCategoryColor = (key) => {
  const colors = {
    'PTS': 'from-purple-600/20 to-transparent border-purple-500/50 text-purple-400',
    'REB': 'from-blue-600/20 to-transparent border-blue-500/50 text-blue-400',
    'AST': 'from-yellow-600/20 to-transparent border-yellow-500/50 text-yellow-400',
    'PRA': 'from-orange-600/20 to-transparent border-orange-500/50 text-orange-400',
    'P+R': 'from-cyan-600/20 to-transparent border-cyan-500/50 text-cyan-400',
    'P+A': 'from-pink-600/20 to-transparent border-pink-500/50 text-pink-400',
    'R+A': 'from-emerald-600/20 to-transparent border-emerald-500/50 text-emerald-400',
    '3PM': 'from-red-600/20 to-transparent border-red-500/50 text-red-400',
    'BLK': 'from-slate-600/20 to-transparent border-slate-500/50 text-slate-400',
    'STL': 'from-amber-600/20 to-transparent border-amber-500/50 text-amber-400',
    'TO': 'from-gray-600/20 to-transparent border-gray-500/50 text-gray-400',
  };
  return colors[key] || 'from-zinc-600/20 to-transparent border-zinc-500/50 text-zinc-400';
};

// ==================== CATEGORY ACCORDION ====================

const CategoryAccordion = memo(({ categoryKey, categoryName, props, isExpanded, onToggle, stats, isHighlightedProp, highlightRef, glowClass = 'beacon-glow', glowSubtleClass = 'beacon-glow-subtle', highlightType = 'demon', playerInsights }) => {
  // Count demons, goblins, standard
  const demons = props.filter(p => p.is_demon);
  const goblins = props.filter(p => p.is_goblin);
  const standard = props.filter(p => !p.is_demon && !p.is_goblin);
  
  // Check if this category has a highlighted prop
  const hasHighlightedProp = props.some(p => isHighlightedProp?.(p));
  
  // Determine border color for highlighted category based on type
  const highlightBorderClass = highlightType === 'goblin' 
    ? 'border-green-500/50' 
    : 'border-yellow-500/50';
  
  // Sort by line value (ladder sorting - lowest to highest)
  const sortedProps = [...props].sort((a, b) => {
    // First sort by line value
    if (a.line !== b.line) return a.line - b.line;
    // Then by type (standard, goblin, demon)
    if (a.is_demon && !b.is_demon) return 1;
    if (!a.is_demon && b.is_demon) return -1;
    if (a.is_goblin && !b.is_goblin) return -1;
    if (!a.is_goblin && b.is_goblin) return 1;
    return 0;
  });
  
  // Get stats for this category
  const categoryStats = stats || {};
  const l10Stats = categoryStats?.l10 || {};
  const seasonStats = categoryStats?.season || {};
  
  const colorClasses = getCategoryColor(categoryKey);
  
  return (
    <div className={`rounded-lg overflow-hidden border ${hasHighlightedProp ? `${highlightBorderClass} ${glowSubtleClass}` : 'border-zinc-800/50'}`}>
      {/* Category Header - Clickable */}
      <div
        onClick={onToggle}
        className={`
          flex items-center justify-between px-3 py-2.5 cursor-pointer
          bg-gradient-to-r ${colorClasses.split(' ').slice(0, 2).join(' ')}
          hover:bg-zinc-800/50 transition-all
        `}
        data-testid={`category-${categoryKey}`}
      >
        <div className="flex items-center gap-2">
          <div className={`text-lg font-bold ${colorClasses.split(' ').slice(-1)[0]}`}>
            {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </div>
          
          <div>
            <div className="flex items-center gap-2">
              <span className={`font-bold text-sm ${colorClasses.split(' ').slice(-1)[0]}`}>
                {categoryName}
              </span>
              <span className="text-zinc-500 text-xs">({props.length})</span>
              {hasHighlightedProp && (
                <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/50 shadow-[0_0_10px_rgba(234,179,8,0.5)] text-[10px]">
                  VISION TARGET
                </Badge>
              )}
            </div>
            
            {/* Quick Stats - L10 Hit Rate */}
            {l10Stats.hit_rate !== undefined && (
              <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                <span>L10: <span className={`font-bold ${l10Stats.hit_rate >= 0.6 ? 'text-green-400' : l10Stats.hit_rate >= 0.4 ? 'text-yellow-400' : 'text-zinc-400'}`}>
                  {Math.round(l10Stats.hit_rate * 100)}%
                </span></span>
                <span className="text-zinc-600">|</span>
                <span>Avg: <span className="text-white font-mono">{l10Stats.avg?.toFixed(1) || '---'}</span></span>
              </div>
            )}
          </div>
        </div>
        
        {/* Type Badges */}
        <div className="flex items-center gap-2">
          {demons.length > 0 && (
            <div className="flex items-center gap-1 bg-red-950/50 px-1.5 py-0.5 rounded">
              <DemonIcon size={12} />
              <span className="text-red-400 text-xs font-bold">{demons.length}</span>
            </div>
          )}
          {goblins.length > 0 && (
            <div className="flex items-center gap-1 bg-green-950/50 px-1.5 py-0.5 rounded">
              <GoblinIcon size={12} />
              <span className="text-green-400 text-xs font-bold">{goblins.length}</span>
            </div>
          )}
          {standard.length > 0 && (
            <div className="flex items-center gap-1 bg-zinc-800/50 px-1.5 py-0.5 rounded">
              <span className="text-zinc-400 text-xs font-bold">{standard.length}</span>
            </div>
          )}
        </div>
      </div>
      
      {/* Expanded Content - Ladder View */}
      {isExpanded && (
        <div className="bg-zinc-900/30 p-2 space-y-1">
          {/* Stats Summary Bar */}
          {(l10Stats.hit_rate !== undefined || seasonStats.hit_rate !== undefined) && (
            <div className="flex items-center justify-between px-2 py-1.5 bg-zinc-800/50 rounded mb-2 text-xs">
              <div className="flex items-center gap-4">
                {l10Stats.hit_rate !== undefined && (
                  <div className="flex items-center gap-1">
                    <span className="text-zinc-500">L10:</span>
                    <span className={`font-bold ${l10Stats.hit_rate >= 0.6 ? 'text-green-400' : l10Stats.hit_rate >= 0.4 ? 'text-yellow-400' : 'text-zinc-400'}`}>
                      {l10Stats.games_over || 0}/{l10Stats.total_games || 0}
                    </span>
                    <span className="text-zinc-600">({Math.round(l10Stats.hit_rate * 100)}%)</span>
                  </div>
                )}
                {seasonStats.hit_rate !== undefined && (
                  <div className="flex items-center gap-1">
                    <span className="text-zinc-500">Season:</span>
                    <span className={`font-bold ${seasonStats.hit_rate >= 0.6 ? 'text-green-400' : seasonStats.hit_rate >= 0.4 ? 'text-yellow-400' : 'text-zinc-400'}`}>
                      {seasonStats.games_over || 0}/{seasonStats.total_games || 0}
                    </span>
                    <span className="text-zinc-600">({Math.round(seasonStats.hit_rate * 100)}%)</span>
                  </div>
                )}
              </div>
              <div className="text-zinc-500">
                Avg: <span className="text-white font-mono">{l10Stats.avg?.toFixed(1) || seasonStats.avg?.toFixed(1) || '---'}</span>
              </div>
            </div>
          )}
          
          {/* Ladder Lines */}
          {sortedProps.map((prop, idx) => (
            <LadderPropRow 
              key={`${categoryKey}-${idx}`} 
              prop={prop} 
              categoryStats={categoryStats}
              isFirst={idx === 0}
              isLast={idx === sortedProps.length - 1}
              isHighlighted={isHighlightedProp?.(prop)}
              highlightRef={highlightRef}
              glowClass={glowClass}
              highlightType={highlightType}
              playerInsights={playerInsights}
            />
          ))}
        </div>
      )}
    </div>
  );
});

CategoryAccordion.displayName = 'CategoryAccordion';

// ==================== LADDER PROP ROW ====================

const LadderPropRow = memo(({ prop, categoryStats, isFirst, isLast, isHighlighted, highlightRef, glowClass = 'beacon-glow', highlightType = 'demon', playerInsights }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  
  const line = prop.line;
  const direction = prop.direction;
  const price = prop.price;
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  
  // Get detailed hit rate data
  const l5Data = prop.hit_rates?.l5 || {};
  const l10Data = prop.hit_rates?.l10 || {};
  const seasonData = prop.hit_rates?.season || {};
  
  // Calculate percentages
  const l5HitRate = l5Data.hit_rate || 0;
  const l10HitRate = l10Data.hit_rate || 0;
  const seasonHitRate = seasonData.hit_rate || 0;
  const l5Pct = Math.round(l5HitRate * 100);
  const l10Pct = Math.round(l10HitRate * 100);
  const seasonPct = Math.round(seasonHitRate * 100);
  
  // Get games over/total
  const l5Over = l5Data.games_over || 0;
  const l5Games = l5Data.total_games || 0;
  const l10Over = l10Data.games_over || 0;
  const l10Games = l10Data.total_games || 0;
  const seasonOver = seasonData.games_over || 0;
  const seasonGames = seasonData.total_games || 0;
  const seasonAvg = seasonData.avg || 0;
  
  // Advanced Analytics from playerInsights
  const insights = playerInsights || {};
  const volatilityScore = insights.volatility_score || 'Low';
  const paceFactor = insights.pace_adjustment_factor || 1.0;
  const usageBump = insights.usage_bump_percent || 0;
  // Prefer intel_briefing (Gemini) over insight_summary (Claude)
  const insightSummary = prop.intel_briefing || insights.intel_briefing || insights.insight_summary || '';
  
  // Calculate per-prop AI confidence based on actual hit rates
  // Formula: (L5 weight * 0.4) + (L10 weight * 0.35) + (Season weight * 0.25) + bonuses
  const calculatePropConfidence = () => {
    if (seasonGames === 0) return 50; // No data = neutral
    
    let baseScore = 0;
    
    // L5 contribution (40% weight) - most recent form matters most
    if (l5Games > 0) {
      baseScore += (l5Pct / 100) * 40;
    } else {
      baseScore += 20; // Neutral if no L5 data
    }
    
    // L10 contribution (35% weight)
    if (l10Games > 0) {
      baseScore += (l10Pct / 100) * 35;
    } else {
      baseScore += 17.5;
    }
    
    // Season contribution (25% weight)
    if (seasonGames > 0) {
      baseScore += (seasonPct / 100) * 25;
    } else {
      baseScore += 12.5;
    }
    
    // Bonus: Season average above line (+5 to +15)
    if (seasonAvg > 0 && seasonAvg > line) {
      const buffer = ((seasonAvg - line) / line) * 100;
      baseScore += Math.min(buffer * 0.5, 15);
    }
    
    // Penalty: Season average below line (-5 to -15)
    if (seasonAvg > 0 && seasonAvg < line) {
      const deficit = ((line - seasonAvg) / line) * 100;
      baseScore -= Math.min(deficit * 0.5, 15);
    }
    
    // Bonus for Goblin status (high consistency)
    if (isGoblin) baseScore += 5;
    
    // Penalty for Demon status (high variance)
    if (isDemon) baseScore -= 3;
    
    // Clamp between 10-98
    return Math.round(Math.max(10, Math.min(98, baseScore)));
  };
  
  const confidenceRating = calculatePropConfidence();
  
  // Determine play type label
  let playTypeLabel = '';
  let playTypeColor = 'text-zinc-500';
  if (isGoblin) {
    playTypeLabel = 'Safety Play';
    playTypeColor = 'text-green-400';
  } else if (isDemon) {
    playTypeLabel = 'Payout Play';
    playTypeColor = 'text-red-400';
  } else {
    playTypeLabel = 'Main Line';
    playTypeColor = 'text-zinc-400';
  }
  
  // Color helper for percentages
  const getPctColor = (pct) => {
    if (pct >= 70) return 'text-green-400';
    if (pct >= 50) return 'text-yellow-400';
    if (pct >= 30) return 'text-orange-400';
    return 'text-red-400';
  };
  
  // Volatility color
  const getVolatilityColor = (vol) => {
    if (vol === 'High') return 'text-red-400 bg-red-500/20';
    if (vol === 'Med') return 'text-yellow-400 bg-yellow-500/20';
    return 'text-green-400 bg-green-500/20';
  };
  
  return (
    <div 
      ref={isHighlighted ? highlightRef : null}
      className={`
        rounded-lg transition-all overflow-hidden
        ${isDemon ? 'bg-red-950/30 border-l-3 border-red-500' : ''}
        ${isGoblin ? 'bg-green-950/30 border-l-3 border-green-500' : ''}
        ${!isDemon && !isGoblin ? 'bg-zinc-800/30 border-l-3 border-zinc-600' : ''}
        ${isHighlighted ? glowClass : ''}
      `}
      data-testid={`ladder-prop-${line}`}
      data-highlighted={isHighlighted ? 'true' : 'false'}
    >
      {/* Main Row - Clickable */}
      <div 
        onClick={() => setIsExpanded(!isExpanded)}
        className={`
          flex items-center justify-between px-3 py-2 cursor-pointer
          ${isDemon ? 'hover:bg-red-950/50' : ''}
          ${isGoblin ? 'hover:bg-green-950/50' : ''}
          ${!isDemon && !isGoblin ? 'hover:bg-zinc-800/50' : ''}
        `}
      >
        {/* Left: Line Value + Direction */}
        <div className="flex items-center gap-3">
          {/* Expand Icon */}
          <ChevronDown className={`w-4 h-4 text-zinc-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
          
          {/* Type Icon */}
          <div className="w-5 flex justify-center">
            {isDemon && <DemonIcon size={16} />}
            {isGoblin && <GoblinIcon size={16} />}
            {!isDemon && !isGoblin && <div className="w-2 h-2 bg-zinc-500 rounded-full" />}
          </div>
          
          {/* Line Value */}
          <div className="flex items-baseline gap-1.5">
            <span className="text-xl font-bold text-white">{line}</span>
            <span className={`text-xs font-medium ${direction === 'Over' ? 'text-green-400' : 'text-red-400'}`}>
              {direction}
            </span>
          </div>
          
          {/* Play Type Label */}
          <span className={`text-[10px] font-medium ${playTypeColor} bg-zinc-800/50 px-1.5 py-0.5 rounded`}>
            {playTypeLabel}
          </span>
          
          {/* Vision Pick Badge */}
          {isHighlighted && (
            <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/50 shadow-[0_0_10px_rgba(234,179,8,0.5)] text-[10px] animate-pulse">
              VISION PICK
            </Badge>
          )}
        </div>
        
        {/* Right: Odds + Quick Hit Rate */}
        <div className="flex items-center gap-4">
          {/* Quick L10 indicator */}
          {l10Games > 0 && (
            <div className="flex items-center gap-1 text-xs">
              <span className="text-zinc-500">L10:</span>
              <span className={`font-bold ${getPctColor(l10Pct)}`}>
                {l10Pct}%
              </span>
            </div>
          )}
          
          {/* Odds */}
          <div className={`
            text-sm font-mono font-bold min-w-[50px] text-right px-2 py-1 rounded
            ${price === 100 ? 'bg-red-500/20 text-red-400' : ''}
            ${price < 0 ? 'bg-green-500/20 text-green-400' : ''}
            ${price > 0 && price !== 100 ? 'text-zinc-400' : ''}
          `}>
            {price > 0 ? `+${price}` : price}
          </div>
        </div>
      </div>
      
      {/* Expanded Stat Insight Panel */}
      {isExpanded && (
        <div className="px-3 pb-3 pt-1 border-t border-zinc-700/50">
          <div className="bg-zinc-900/50 rounded-lg p-3 space-y-2">
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider font-semibold mb-2">
              Stat Insight for {line}+ {direction}
            </div>
            
            {/* Show "No Data" message if no stats available */}
            {seasonGames === 0 ? (
              <div className="flex flex-col items-center justify-center py-4 text-center">
                <div className="text-zinc-500 text-sm mb-1">📊 Stats Unavailable</div>
                <div className="text-zinc-600 text-xs">
                  Game log data not found for this player.
                  <br />
                  This may be a rookie or recently traded player.
                </div>
              </div>
            ) : (
              <>
                {/* L5 Hit Rate */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400 w-20">Last 5 Games:</span>
                    <span className={`text-sm font-bold ${getPctColor(l5Pct)}`}>
                      {l5Games > 0 ? `${l5Over}/${l5Games}` : '---'}
                    </span>
                  </div>
                  <div className={`text-lg font-bold ${getPctColor(l5Pct)}`}>
                    {l5Games > 0 ? `${l5Pct}%` : '---'}
                  </div>
                </div>
                
                {/* L10 Hit Rate */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400 w-20">Last 10 Games:</span>
                    <span className={`text-sm font-bold ${getPctColor(l10Pct)}`}>
                      {l10Games > 0 ? `${l10Over}/${l10Games}` : '---'}
                    </span>
                  </div>
                  <div className={`text-lg font-bold ${getPctColor(l10Pct)}`}>
                    {l10Games > 0 ? `${l10Pct}%` : '---'}
                  </div>
                </div>
                
                {/* Season Hit Rate */}
                <div className="flex items-center justify-between border-t border-zinc-700/50 pt-2 mt-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400 w-20">Season:</span>
                    <span className={`text-sm font-bold ${getPctColor(seasonPct)}`}>
                      {seasonGames > 0 ? `${seasonOver}/${seasonGames}` : '---'}
                    </span>
                  </div>
                  <div className={`text-lg font-bold ${getPctColor(seasonPct)}`}>
                    {seasonGames > 0 ? `${seasonPct}%` : '---'}
                  </div>
                </div>
                
                {/* Season Average Value */}
                <div className="flex items-center justify-between bg-zinc-800/50 rounded px-2 py-1.5 mt-2">
                  <span className="text-xs text-zinc-400">Season Average</span>
                  <div className="flex items-center gap-2">
                    <span className="text-white font-bold">{seasonAvg > 0 ? seasonAvg.toFixed(1) : '---'}</span>
                    {seasonAvg > 0 && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${seasonAvg > line ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                        {seasonAvg > line ? `+${(seasonAvg - line).toFixed(1)} above line` : `${(seasonAvg - line).toFixed(1)} below line`}
                      </span>
                    )}
                  </div>
                </div>
                
                {/* THE VISION - AI Insight Section (Always Show) */}
                <div className="mt-3 pt-3 border-t border-zinc-700/50">
                  <div className="text-[10px] uppercase tracking-wider font-semibold mb-2 flex items-center gap-1 text-purple-400">
                    <Zap className="w-3 h-3" />
                    THE VISION
                    <span className="text-zinc-600 font-normal ml-1">AI Analysis</span>
                  </div>
                  
                  {/* AI Insight Summary - Featured Box */}
                  {insightSummary ? (
                    <div className="bg-gradient-to-r from-purple-950/50 via-zinc-900 to-purple-950/50 rounded-lg px-3 py-2.5 mb-3 border border-purple-700/40 relative overflow-hidden">
                      <div className="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-purple-500 to-purple-700" />
                      <p className="text-sm text-purple-200 leading-relaxed pl-2 italic">
                        "{insightSummary}"
                      </p>
                    </div>
                  ) : (
                    <div className="bg-zinc-900/50 rounded-lg px-3 py-2.5 mb-3 border border-zinc-800 text-center">
                      <p className="text-xs text-zinc-500">Analyzing Sector Data...</p>
                    </div>
                  )}
                  
                  {/* Confidence Score Meter */}
                  <div className="mb-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">AI Confidence</span>
                      <span className={`text-sm font-bold ${
                        confidenceRating >= 80 ? 'text-green-400' :
                        confidenceRating >= 60 ? 'text-yellow-400' :
                        confidenceRating >= 40 ? 'text-orange-400' :
                        'text-red-400'
                      }`}>
                        {confidenceRating}%
                      </span>
                    </div>
                    <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                      <div 
                        className={`h-full rounded-full transition-all duration-500 ${
                          confidenceRating >= 80 ? 'bg-gradient-to-r from-green-600 to-green-400' :
                          confidenceRating >= 60 ? 'bg-gradient-to-r from-yellow-600 to-yellow-400' :
                          confidenceRating >= 40 ? 'bg-gradient-to-r from-orange-600 to-orange-400' :
                          'bg-gradient-to-r from-red-600 to-red-400'
                        }`}
                        style={{ width: `${confidenceRating}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-[8px] text-zinc-600 mt-0.5">
                      <span>Low</span>
                      <span>Med</span>
                      <span>High</span>
                    </div>
                  </div>
                  
                  {/* Analytics Grid */}
                  <div className="grid grid-cols-2 gap-2">
                    {/* Volatility Score */}
                    <div className="flex items-center justify-between bg-zinc-800/40 rounded px-2 py-1.5">
                      <span className="text-[10px] text-zinc-500">Volatility</span>
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${getVolatilityColor(volatilityScore)}`}>
                        {volatilityScore}
                      </span>
                    </div>
                    
                    {/* Pace Factor */}
                    <div className="flex items-center justify-between bg-zinc-800/40 rounded px-2 py-1.5">
                      <span className="text-[10px] text-zinc-500">Pace</span>
                      <span className={`text-xs font-bold ${
                        paceFactor > 1.02 ? 'text-green-400' : 
                        paceFactor < 0.98 ? 'text-red-400' : 
                        'text-zinc-300'
                      }`}>
                        {paceFactor > 1.0 ? '+' : ''}{((paceFactor - 1) * 100).toFixed(0)}%
                      </span>
                    </div>
                    
                    {/* Usage Bump */}
                    {usageBump > 0 && (
                      <div className="flex items-center justify-between bg-green-950/30 rounded px-2 py-1.5 border border-green-800/30">
                        <span className="text-[10px] text-green-400">Usage Bump</span>
                        <span className="text-xs font-bold text-green-400">+{usageBump.toFixed(0)}%</span>
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
});

LadderPropRow.displayName = 'LadderPropRow';

// ==================== PLAYER DETAIL PAGE (CACHED - No API Calls) ====================

const PlayerDetailPage = ({ playerName, onBack, highlightProp = null, highlightType = 'demon' }) => {
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedCategories, setExpandedCategories] = useState(new Set(['PTS', 'AST', 'REB'])); // Default expanded
  const highlightRef = useRef(null);
  
  // Determine glow class based on highlight type
  const glowClass = highlightType === 'goblin' ? 'emerald-glow' : 'beacon-glow';
  const glowSubtleClass = highlightType === 'goblin' ? 'emerald-glow-subtle' : 'beacon-glow-subtle';
  
  // Parse highlight info (format: "stat_type|line|direction" e.g., "AST|3.5|Over")
  const highlightInfo = useMemo(() => {
    if (!highlightProp) return null;
    const parts = highlightProp.split('|');
    if (parts.length >= 2) {
      return {
        statType: parts[0],
        line: parseFloat(parts[1]),
        direction: parts[2] || 'Over'
      };
    }
    return null;
  }, [highlightProp]);
  
  useEffect(() => {
    const fetchPlayer = async () => {
      /**
       * HARD CUTOFF: Uses ONLY the cached MongoDB endpoint.
       * NO Odds API calls. Zero credit usage.
       */
      try {
        setLoading(true);
        const response = await axios.get(`${API}/v3/cached-player/${encodeURIComponent(playerName)}`);
        
        if (response.data.success && response.data.player) {
          setPlayer(response.data.player);
        } else {
          // Show "Lines loading..." message - do NOT trigger API call
          setError(response.data.message || 'Lines loading... Player not in cache.');
        }
      } catch (err) {
        console.error('Error fetching player from cache:', err);
        setError('Lines loading... Please sync data first.');
      } finally {
        setLoading(false);
      }
    };
    
    fetchPlayer();
  }, [playerName]);
  
  // Group props by category (must be before useEffect that uses it)
  const groupedProps = useMemo(() => {
    if (!player?.props) return {};
    
    const groups = {};
    
    player.props.forEach(prop => {
      const categoryKey = getCategoryKey(prop.market);
      if (!groups[categoryKey]) {
        groups[categoryKey] = [];
      }
      groups[categoryKey].push(prop);
    });
    
    return groups;
  }, [player]);
  
  // Auto-expand category containing highlighted prop and scroll to it
  useEffect(() => {
    if (highlightInfo && player?.props && Object.keys(groupedProps).length > 0) {
      // Find which category contains the highlighted prop
      const matchingCategory = Object.entries(groupedProps).find(([key, props]) => {
        return props.some(p => 
          getCategoryKey(p.market) === highlightInfo.statType || 
          key === highlightInfo.statType
        );
      });
      
      if (matchingCategory) {
        // Expand the matching category
        setExpandedCategories(prev => {
          const newSet = new Set(prev);
          newSet.add(matchingCategory[0]);
          return newSet;
        });
      }
      
      // Scroll to highlighted element after a short delay (for expansion to complete)
      setTimeout(() => {
        if (highlightRef.current) {
          highlightRef.current.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'center' 
          });
        }
      }, 300);
    }
  }, [highlightInfo, player, groupedProps]);
  
  // Check if a prop matches the highlight
  const isHighlightedProp = useCallback((prop) => {
    if (!highlightInfo) return false;
    const propCategory = getCategoryKey(prop.market);
    return (
      (propCategory === highlightInfo.statType || prop.stat_type_extracted === highlightInfo.statType) &&
      Math.abs(prop.line - highlightInfo.line) < 0.1 &&
      prop.direction === highlightInfo.direction
    );
  }, [highlightInfo]);
  
  // Get ordered category keys (by number of props)
  const orderedCategories = useMemo(() => {
    const keys = Object.keys(groupedProps);
    // Sort by: PRA combos first, then by prop count
    const priorityOrder = ['PRA', 'P+R', 'P+A', 'R+A', 'PTS', 'AST', 'REB', '3PM', 'BLK', 'STL', 'TO'];
    return keys.sort((a, b) => {
      const aIdx = priorityOrder.indexOf(a);
      const bIdx = priorityOrder.indexOf(b);
      if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx;
      if (aIdx !== -1) return -1;
      if (bIdx !== -1) return 1;
      return groupedProps[b].length - groupedProps[a].length;
    });
  }, [groupedProps]);
  
  // Toggle category expansion
  const toggleCategory = (categoryKey) => {
    setExpandedCategories(prev => {
      const newSet = new Set(prev);
      if (newSet.has(categoryKey)) {
        newSet.delete(categoryKey);
      } else {
        newSet.add(categoryKey);
      }
      return newSet;
    });
  };
  
  // Expand/collapse all
  const expandAll = () => setExpandedCategories(new Set(orderedCategories));
  const collapseAll = () => setExpandedCategories(new Set());
  
  // Count totals
  const demons = player?.props?.filter(p => p.is_demon) || [];
  const goblins = player?.props?.filter(p => p.is_goblin) || [];
  const standard = player?.props?.filter(p => !p.is_demon && !p.is_goblin) || [];
  
  // Get stats summary for each category
  const getStatsForCategory = (categoryKey) => {
    const category = STAT_CATEGORIES[categoryKey];
    if (!category || !player?.stats_summary) return {};
    
    // Find the matching stat in stats_summary
    const baseMarket = category.markets[0]; // e.g., 'player_points'
    return player.stats_summary[baseMarket] || {};
  };
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header with Large Headshot */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="text-zinc-400 hover:text-white p-1"
            data-testid="back-btn"
          >
            <ArrowLeft className="w-5 h-5" />
          </Button>
          
          {/* Large Headshot */}
          {player && (
            <PlayerHeadshot 
              nbaId={player.nba_id} 
              playerName={playerName}
              team={player.team}
              photoUrl={player.photo_url}
              size="lg"
              className="ring-2 ring-purple-500/50"
            />
          )}
          
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-bold text-white truncate">{playerName}</h1>
            {player && (
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="font-mono">{player.team}</span>
                {player.position && <span>· {player.position}</span>}
                {player.injury_info?.has_injury && (
                  <Badge className="bg-yellow-600/20 text-yellow-400 border-yellow-600/30 text-[10px] px-1">
                    {player.injury_info.injury_status}
                  </Badge>
                )}
              </div>
            )}
          </div>
          
          {player && (
            <div className="flex items-center gap-2 flex-shrink-0">
              <div className="flex items-center gap-1">
                <DemonIcon size={16} />
                <span className="text-red-400 font-bold">{demons.length}</span>
              </div>
              <div className="flex items-center gap-1">
                <GoblinIcon size={16} />
                <span className="text-green-400 font-bold">{goblins.length}</span>
              </div>
            </div>
          )}
        </div>
      </div>
      
      {/* Content */}
      <div className="p-3">
        {loading ? (
          <SkeletonPlayerDetail />
        ) : error ? (
          <div className="text-center py-8 text-zinc-400">
            <p>{error}</p>
            <Button variant="outline" size="sm" onClick={onBack} className="mt-4">
              Go Back
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Quick Actions */}
            <div className="flex items-center justify-between">
              <div className="text-xs text-zinc-500">
                {orderedCategories.length} categories · {player?.props?.length || 0} props
              </div>
              <div className="flex items-center gap-2">
                <button 
                  onClick={expandAll}
                  className="text-xs text-zinc-400 hover:text-white transition-colors"
                >
                  Expand All
                </button>
                <span className="text-zinc-600">|</span>
                <button 
                  onClick={collapseAll}
                  className="text-xs text-zinc-400 hover:text-white transition-colors"
                >
                  Collapse All
                </button>
              </div>
            </div>
            
            {/* Category Accordions */}
            {orderedCategories.map(categoryKey => {
              const category = STAT_CATEGORIES[categoryKey];
              const categoryProps = groupedProps[categoryKey] || [];
              const categoryStats = getStatsForCategory(categoryKey);
              
              return (
                <CategoryAccordion
                  key={categoryKey}
                  categoryKey={categoryKey}
                  categoryName={category?.name || categoryKey}
                  props={categoryProps}
                  stats={categoryStats}
                  isExpanded={expandedCategories.has(categoryKey)}
                  onToggle={() => toggleCategory(categoryKey)}
                  isHighlightedProp={isHighlightedProp}
                  highlightRef={highlightRef}
                  glowClass={glowClass}
                  glowSubtleClass={glowSubtleClass}
                  highlightType={highlightType}
                  playerInsights={{
                    ...player?.insights,
                    intel_briefing: player?.intel_briefing // Include intel_briefing from player root
                  }}
                />
              );
            })}
            
            {orderedCategories.length === 0 && (
              <div className="text-center py-8 text-zinc-500">
                No props available for this player
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ==================== PLAYER ROW (List View with Headshot) ====================

const PlayerRow = memo(({ player, isExpanded, onToggle, onClick, linesLoaded }) => {
  const hasInjury = player.injury_info?.warning_level && player.injury_info.warning_level !== 'none';
  const isOut = player.injury_info?.warning_level === 'out';
  
  return (
    <div className={`border-b border-zinc-800/50 ${isOut ? 'opacity-50' : ''}`}>
      {/* Collapsed Header */}
      <div 
        className={`
          flex items-center justify-between px-3 py-2.5 cursor-pointer
          bg-zinc-900/30 hover:bg-zinc-800/50 transition-colors
          border-l-2 ${
            isOut ? 'border-l-red-500' :
            hasInjury ? 'border-l-yellow-500' :
            (player.demons_count || 0) > 0 ? 'border-l-red-500/50' :
            (player.goblins_count || 0) > 0 ? 'border-l-green-500/50' :
            'border-l-transparent'
          }
        `}
        onClick={onClick || onToggle}
        data-testid={`player-row-${player.player_name}`}
      >
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          {/* Small Headshot */}
          <PlayerHeadshot 
            nbaId={player.nba_id} 
            playerName={player.player_name}
            team={player.team}
            photoUrl={player.photo_url}
            size="sm"
          />
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm truncate">{player.player_name}</span>
              <span className="text-zinc-500 text-xs font-mono flex-shrink-0">{player.team}</span>
            </div>
            
            {hasInjury && (
              <div className={`flex items-center gap-1 text-[10px] ${isOut ? 'text-red-400' : 'text-yellow-400'}`}>
                <AlertTriangle className="w-2.5 h-2.5" />
                <span>{player.injury_info?.injury_status || (isOut ? 'OUT' : 'GTD')}</span>
              </div>
            )}
          </div>
        </div>
        
        {/* Counts */}
        <div className="flex items-center gap-3 flex-shrink-0">
          {linesLoaded ? (
            <>
              {(player.demons_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <DemonIcon size={14} className="flex-shrink-0" />
                  <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
                </div>
              )}
              {(player.goblins_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <GoblinIcon size={14} className="flex-shrink-0" />
                  <span className="text-green-400 font-bold text-sm">{player.goblins_count}</span>
                </div>
              )}
              <span className="text-zinc-600 text-xs">{player.props?.length || 0}</span>
            </>
          ) : (
            <div className="w-16 h-4 bg-zinc-800 animate-pulse rounded" />
          )}
        </div>
      </div>
    </div>
  );
});

PlayerRow.displayName = 'PlayerRow';

// ==================== MAIN DASHBOARD ====================

export const DemonGoblinDashboardOptimized = ({ isDemoMode = false }) => {
  // Auth
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [showUserMenu, setShowUserMenu] = useState(false);
  
  // State
  const [players, setPlayers] = useState([]);
  const [trending, setTrending] = useState([]);
  const [radarPicks, setRadarPicks] = useState([]);
  const [vaultPicks, setVaultPicks] = useState([]);
  const [frontLinesPicks, setFrontLinesPicks] = useState([]);
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [syncedAt, setSyncedAt] = useState(null);
  const [parlayData, setParlayData] = useState({});
  const [reconData, setReconData] = useState({});  // Goblin Recon parlays
  const [expandedParlay, setExpandedParlay] = useState(null);  // Currently expanded parlay view
  
  // Most Popular Bets Live Ticker state
  const [popularBets, setPopularBets] = useState([]);
  const [popularBetsLastUpdated, setPopularBetsLastUpdated] = useState(null);
  const [popularBetsLoading, setPopularBetsLoading] = useState(false);
  const [popularBetsStatus, setPopularBetsStatus] = useState('awaiting_action');  // 'live' or 'awaiting_action'
  
  // Injury Intelligence state
  const [injuryAlerts, setInjuryAlerts] = useState({});  // player_name -> injury_info
  const [breakingNews, setBreakingNews] = useState([]);  // Breaking news ticker
  
  // Live Scores Command Center state
  const [liveScores, setLiveScores] = useState([]);  // Live game scores
  
  // Game Lock Engine state
  const [lockStatus, setLockStatus] = useState({
    active_games: 0,
    locked_games: 0,
    t_minus_games: 0,
    t_minus_details: []
  });
  const [tMinusGames, setTMinusGames] = useState([]);  // Games starting in <15 minutes
  
  // V3.1 Truth Engine - Data Integrity Status
  const [dataStatus, setDataStatus] = useState({
    status: 'loading',
    verified_count: 0,
    failed_count: 0,
    verification_rate: 0
  });
  
  // Adaptive Sync Engine - Intel Freshness Status
  const [syncStatus, setSyncStatus] = useState({
    engine_status: 'loading',
    sync_age_display: '...',
    seconds_since_sync: 0,
    mission_critical_games: 0,
    has_stale_intel: false
  });
  
  // RAW VALIDATION TABLE - Data Integrity Check
  const [showValidationTable, setShowValidationTable] = useState(false);
  
  // Board Intelligence Status - "Last Synced" footer
  const [boardIntelStatus, setBoardIntelStatus] = useState({
    time_since_sync_display: 'Loading...',
    last_sync_type: null,
    scheduler_running: false
  });
  
  // Scouting Projections - Early Bird cards awaiting live lines
  const [scoutingProjections, setScoutingProjections] = useState([]);
  const [isEarlyBirdActive, setIsEarlyBirdActive] = useState(false);
  
  // Navigation state
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  
  // Logout handler
  const handleLogout = async () => {
    await logout();
    navigate('/auth');
    toast.success('Logged out successfully');
  };
  
  // Close user menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (showUserMenu && !e.target.closest('[data-testid="user-menu-btn"]') && !e.target.closest('.user-menu-dropdown')) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [showUserMenu]);

  // ==================== CACHED DATA LOADING (ZERO API CALLS) ====================
  
  const loadCachedBoard = useCallback(async () => {
    /**
     * HARD CUTOFF: This is the ONLY data fetch function.
     * It reads ONLY from MongoDB via /api/v3/cached-props.
     * NO Odds API calls are made here.
     */
    try {
      console.log('[CACHED] Loading from MongoDB...');
      
      // Load board, war zone, front lines, vault, parlays, recon, injuries, social signals, data status, sync status, live scores, and lock status in parallel
      const [boardResponse, warZoneResponse, frontLinesResponse, vaultResponse, parlayResponse, reconResponse, injuryResponse, newsResponse, dataStatusResponse, socialSignalsResponse, syncStatusResponse, liveScoresResponse, lockStatusResponse, tMinusResponse, boardIntelResponse, popularBetsResponse] = await Promise.all([
        axios.get(`${API}/v3/cached-props`),
        axios.get(`${API}/v3/war-zone`),
        axios.get(`${API}/v3/front-lines`),
        axios.get(`${API}/v3/goblin-vault`),
        axios.get(`${API}/v3/parlay-builder`),
        axios.get(`${API}/v3/goblin-recon`),
        axios.get(`${API}/v3/injuries/alerts`).catch(() => ({ data: { success: false, alerts: {} }})),
        axios.get(`${API}/v3/breaking-news`).catch(() => ({ data: { success: false, news: [] }})),
        axios.get(`${API}/v3/data-status`).catch(() => ({ data: { success: false, status: 'error' }})),
        axios.get(`${API}/v3/social-signals`).catch(() => ({ data: { success: false, signals: {} }})),
        axios.get(`${API}/v3/sync-status`).catch(() => ({ data: { engine_status: 'offline', sync_age_display: 'N/A' }})),
        axios.get(`${API}/v3/live-scores`).catch(() => ({ data: { success: false, games: [] }})),
        axios.get(`${API}/v3/lock-status`).catch(() => ({ data: { active_games: 0, locked_games: 0, t_minus_games: 0 }})),
        axios.get(`${API}/v3/t-minus-games`).catch(() => ({ data: { games: [], count: 0 }})),
        axios.get(`${API}/v3/board-intel/status`).catch(() => ({ data: { time_since_sync_display: 'Not synced', scheduler_running: false }})),
        axios.get(`${API}/v3/most-popular-bets`).catch(() => ({ data: { success: false, bets: [], last_updated: null }}))
      ]);
      
      // Social signals for applying to picks
      const socialSignals = socialSignalsResponse.data?.signals || {};
      
      // Helper to apply social signals to picks
      const applySignals = (picks) => {
        return picks.map(pick => {
          const signal = socialSignals[pick.player_name];
          if (signal) {
            return {
              ...pick,
              volatility_flag: signal.volatility_flag,
              volatility_reason: signal.volatility_reason,
              revenge_game: signal.revenge_game,
              revenge_opponent: signal.revenge_opponent,
              gem_modifier: signal.volatility_flag ? -1 : (signal.revenge_game ? 1 : 0)
            };
          }
          return pick;
        });
      };
      
      if (boardResponse.data.success && boardResponse.data.players_count > 0) {
        setPlayers(boardResponse.data.players || []);
        setTrending(boardResponse.data.trending || []);
        setSyncedAt(boardResponse.data.synced_at);
        setStaticLoaded(true);
        setLinesLoaded(true);
        console.log(`[CACHED] Loaded ${boardResponse.data.players_count} players from MongoDB`);
      } else {
        console.log('[CACHED] No cached data. Run /api/v3/sync-to-mongo first.');
        setStaticLoaded(true);
        setLinesLoaded(false);
      }
      
      // Load war zone picks with social signals
      if (warZoneResponse.data.success) {
        const picksWithSignals = applySignals(warZoneResponse.data.picks || []);
        setRadarPicks(picksWithSignals);
        console.log(`[WAR ZONE] Loaded ${warZoneResponse.data.picks_count} war zone picks`);
      }
      
      // Load vault picks with social signals
      if (vaultResponse.data.success) {
        const picksWithSignals = applySignals(vaultResponse.data.picks || []);
        setVaultPicks(picksWithSignals);
        console.log(`[VAULT] Loaded ${vaultResponse.data.picks_count} vault picks`);
      }
      
      // Load front lines picks with social signals
      if (frontLinesResponse.data.success) {
        const picksWithSignals = applySignals(frontLinesResponse.data.picks || []);
        setFrontLinesPicks(picksWithSignals);
        console.log(`[FRONT LINES] Loaded ${frontLinesResponse.data.picks_count} front lines picks`);
      }
      
      // Load parlay data
      if (parlayResponse.data.success) {
        setParlayData(parlayResponse.data.parlays || {});
        console.log(`[PARLAY] Loaded ${Object.keys(parlayResponse.data.parlays || {}).length} parlay types`);
      }
      
      // Load Goblin Recon data
      if (reconResponse.data.success) {
        setReconData(reconResponse.data.parlays || {});
        console.log(`[RECON] Loaded ${Object.keys(reconResponse.data.parlays || {}).length} recon tiers`);
      }
      
      // Load injury alerts
      if (injuryResponse.data.success) {
        setInjuryAlerts(injuryResponse.data.alerts || {});
        console.log(`[INJURY] Loaded ${injuryResponse.data.alerts_count || 0} injury alerts`);
      }
      
      // Load breaking news
      if (newsResponse.data.success) {
        setBreakingNews(newsResponse.data.news || []);
        console.log(`[NEWS] Loaded ${newsResponse.data.news_count || 0} breaking news items`);
      }
      
      // V3.1 Truth Engine - Load data integrity status
      if (dataStatusResponse.data.success) {
        setDataStatus(dataStatusResponse.data);
        console.log(`[DATA STATUS] Status: ${dataStatusResponse.data.status}, Verified: ${dataStatusResponse.data.verification_rate}%`);
      } else {
        setDataStatus({ status: 'error', verified_count: 0, failed_count: 0, verification_rate: 0 });
      }
      
      // Adaptive Sync Engine - Load sync status
      if (syncStatusResponse.data) {
        setSyncStatus({
          engine_status: syncStatusResponse.data.engine_status || 'offline',
          sync_age_display: syncStatusResponse.data.sync_age_display || 'N/A',
          seconds_since_sync: syncStatusResponse.data.seconds_since_sync || 0,
          mission_critical_games: syncStatusResponse.data.mission_critical_games || 0,
          active_games: syncStatusResponse.data.active_games || 0,
          has_stale_intel: (syncStatusResponse.data.seconds_since_sync || 0) > 300
        });
        console.log(`[SYNC STATUS] Engine: ${syncStatusResponse.data.engine_status}, Last sync: ${syncStatusResponse.data.sync_age_display}`);
      }
      
      // Live Scores Command Center
      if (liveScoresResponse.data.success) {
        setLiveScores(liveScoresResponse.data.games || []);
        console.log(`[LIVE SCORES] Loaded ${liveScoresResponse.data.games?.length || 0} games (Live: ${liveScoresResponse.data.live_count || 0})`);
      }
      
      // Game Lock Engine - Lock Status
      if (lockStatusResponse.data) {
        setLockStatus({
          active_games: lockStatusResponse.data.active_games || 0,
          locked_games: lockStatusResponse.data.locked_games || 0,
          t_minus_games: lockStatusResponse.data.t_minus_games || 0,
          t_minus_details: lockStatusResponse.data.t_minus_details || []
        });
        console.log(`[LOCK STATUS] Active: ${lockStatusResponse.data.active_games}, Locked: ${lockStatusResponse.data.locked_games}, T-Minus: ${lockStatusResponse.data.t_minus_games}`);
      }
      
      // T-Minus Games (games starting in <15 minutes)
      if (tMinusResponse.data?.games) {
        setTMinusGames(tMinusResponse.data.games || []);
        if (tMinusResponse.data.games.length > 0) {
          console.log(`[T-MINUS] ${tMinusResponse.data.count} games starting soon!`);
        }
      }
      
      // Board Intelligence Status - "Last Synced" display
      if (boardIntelResponse.data) {
        setBoardIntelStatus({
          time_since_sync_display: boardIntelResponse.data.time_since_sync_display || 'Not synced',
          last_sync_type: boardIntelResponse.data.last_sync_type,
          scheduler_running: boardIntelResponse.data.scheduler_running || false,
          next_scheduled_sync: boardIntelResponse.data.next_scheduled_sync
        });
        console.log(`[BOARD INTEL] ${boardIntelResponse.data.time_since_sync_display}`);
        
        // Check if early bird mode is active
        if (boardIntelResponse.data.last_sync_type === 'early_bird') {
          setIsEarlyBirdActive(true);
        }
      }
      
      // Most Popular Bets - Live Ticker (initial load)
      if (popularBetsResponse.data) {
        setPopularBets(popularBetsResponse.data.bets || []);
        setPopularBetsLastUpdated(popularBetsResponse.data.last_updated);
        setPopularBetsStatus(popularBetsResponse.data.status || 'awaiting_action');
        const status = popularBetsResponse.data.status === 'live' ? '🟢 LIVE' : '🟡 AWAITING';
        console.log(`[POPULAR BETS] ${status} - ${popularBetsResponse.data.count || 0} bets (${popularBetsResponse.data.games_filtered || 0} tipped-off filtered)`);
      }
      
      // Fetch Scouting Projections (Early Bird cards)
      try {
        const scoutingResponse = await axios.get(`${API}/v3/scouting-projections`);
        if (scoutingResponse.data?.projections) {
          setScoutingProjections(scoutingResponse.data.projections || []);
          setIsEarlyBirdActive(scoutingResponse.data.status === 'early_bird_active');
          if (scoutingResponse.data.count > 0) {
            console.log(`[SCOUTING] ${scoutingResponse.data.count} projection cards awaiting live lines`);
          }
        }
      } catch (e) {
        console.log('[SCOUTING] No projections available');
      }
      
    } catch (error) {
      console.error('[CACHED] Error loading from MongoDB:', error);
      setStaticLoaded(true);
    }
  }, []);
  
  const triggerSync = async () => {
    /**
     * THE ONLY API CALL: Manual sync to MongoDB.
     * This fetches from Odds API and stores in MongoDB.
     * Should be used sparingly to conserve API credits.
     */
    try {
      setSyncing(true);
      setLinesLoaded(false);
      toast.info('Syncing from Odds API to MongoDB...');
      
      const response = await axios.post(`${API}/v3/sync-to-mongo`, {}, { timeout: 600000 });
      
      if (response.data.success) {
        const result = response.data;
        toast.success(`Sync complete! ${result.unique_players} players, ${result.api_calls_made} API calls`);
        
        // Reload from MongoDB
        await loadCachedBoard();
      } else {
        toast.error('Sync failed: ' + (response.data.errors?.join(', ') || 'Unknown error'));
      }
    } catch (error) {
      toast.error('Sync failed: ' + error.message);
    } finally {
      setSyncing(false);
    }
  };
  
  useEffect(() => {
    // Load from MongoDB cache on mount
    // NO auto-refresh interval - data is static until manual sync
    loadCachedBoard();
  }, [loadCachedBoard]);
  
  // ==================== FILTERING ====================
  
  const filteredPlayers = players.filter(p => {
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      if (!p.player_name?.toLowerCase().includes(search) && !p.team?.toLowerCase().includes(search)) {
        return false;
      }
    }
    if (filterType === 'demons') return (p.demons_count || 0) > 0;
    if (filterType === 'goblins') return (p.goblins_count || 0) > 0;
    return true;
  });
  
  // ==================== NAVIGATION ====================
  
  // State for highlighted prop from Radar or Vault
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon'); // 'demon' = gold glow, 'goblin' = green glow
  
  // Auto-refresh live scores every 60 seconds when games are in progress
  useEffect(() => {
    const hasLiveGames = liveScores.some(g => g.status === 'in_play');
    
    if (hasLiveGames) {
      const refreshInterval = setInterval(async () => {
        try {
          const response = await axios.get(`${API}/v3/live-scores?refresh=true`);
          if (response.data.success) {
            setLiveScores(response.data.games || []);
          }
        } catch (error) {
          console.error('[LIVE SCORES] Refresh error:', error);
        }
      }, 60000); // Refresh every 60 seconds
      
      return () => clearInterval(refreshInterval);
    }
  }, [liveScores]);
  
  // Auto-refresh breaking news every 15 minutes
  useEffect(() => {
    const newsRefreshInterval = setInterval(async () => {
      try {
        console.log('[NEWS] Auto-refreshing breaking news...');
        const response = await axios.get(`${API}/v3/breaking-news`);
        if (response.data.success && response.data.news) {
          setBreakingNews(response.data.news);
          console.log(`[NEWS] Refreshed ${response.data.news.length} news items`);
        }
      } catch (error) {
        console.error('[NEWS] Refresh error:', error);
      }
    }, 900000); // Refresh every 15 minutes (900,000ms)
    
    return () => clearInterval(newsRefreshInterval);
  }, []);
  
  // ==================== MOST POPULAR BETS LIVE TICKER POLLING ====================
  // Smart polling every 45 seconds to update popularity rankings
  // STRICT: Only shows upcoming/live bettable lines, auto-purges tipped-off games
  useEffect(() => {
    const POLL_INTERVAL = 45000; // 45 seconds
    
    const fetchPopularBets = async () => {
      try {
        setPopularBetsLoading(true);
        const response = await axios.get(`${API}/v3/most-popular-bets`);
        if (response.data) {
          setPopularBets(response.data.bets || []);
          setPopularBetsLastUpdated(response.data.last_updated);
          setPopularBetsStatus(response.data.status || 'awaiting_action');
          const status = response.data.status === 'live' ? '🟢' : '🟡';
          console.log(`[POPULAR BETS] ${status} Poll: ${response.data.count || 0} live bets (${response.data.games_filtered || 0} filtered)`);
        }
      } catch (error) {
        console.error('[POPULAR BETS] Polling error:', error);
      } finally {
        setPopularBetsLoading(false);
      }
    };
    
    // Start polling interval
    const pollInterval = setInterval(fetchPopularBets, POLL_INTERVAL);
    
    return () => clearInterval(pollInterval);
  }, []);
  
  const handlePlayerClick = (playerName, highlight = null, highlightType = 'demon') => {
    setSelectedPlayer(playerName);
    setHighlightProp(highlight);
    setHighlightType(highlightType);
  };
  
  // Handler for Radar card clicks - passes highlight info (Demon - gold glow)
  const handleRadarClick = (pick) => {
    // Create highlight param: stat_type|line|direction
    const highlightParam = `${pick.stat_type}|${pick.demon_line}|${pick.direction || 'Over'}`;
    handlePlayerClick(pick.player_name, highlightParam, 'demon');
    
    // Toast notification
    toast.success(
      `Navigating to ${pick.player_name}`, 
      { description: `Looking for ${pick.stat_type} ${pick.demon_line} line...` }
    );
  };
  
  // Handler for Vault card clicks - passes highlight info (Goblin - green glow)
  const handleVaultClick = (pick) => {
    // Create highlight param: stat_type|line|direction
    const highlightParam = `${pick.stat_type}|${pick.goblin_line}|${pick.direction || 'Over'}`;
    handlePlayerClick(pick.player_name, highlightParam, 'goblin');
    
    // Toast notification  
    toast.success(
      `Opening Vault for ${pick.player_name}`, 
      { description: `Safety ${pick.safety_rating}% | ${pick.stat_type} ${pick.goblin_line} line` }
    );
  };
  
  const handleBack = () => {
    setSelectedPlayer(null);
    setHighlightProp(null);
    setHighlightType('demon');
  };
  
  // Handler for Popular Bet clicks - navigates to player with bet highlighted
  const handlePopularBetClick = (bet) => {
    const highlightType = bet.is_demon ? 'demon' : bet.is_goblin ? 'goblin' : 'standard';
    const highlightParam = `${bet.stat_type}|${bet.line}|${bet.direction || 'over'}`;
    handlePlayerClick(bet.player_name, highlightParam, highlightType);
    
    const typeLabel = bet.is_demon ? '🔥 Demon' : bet.is_goblin ? '💎 Goblin' : 'Standard';
    toast.success(
      `${bet.player_name} - ${bet.stat_type} ${bet.line}`, 
      { 
        description: `${typeLabel} | L10 Hit Rate: ${bet.h10_rate}%`,
        duration: 2000 
      }
    );
  };
  
  // ==================== RENDER ====================
  
  // If a player is selected, show detail page with highlight
  if (selectedPlayer) {
    return (
      <>
        <BeaconGlowStyles />
        <PlayerDetailPage 
          playerName={selectedPlayer} 
          onBack={handleBack}
          highlightProp={highlightProp}
          highlightType={highlightType}
        />
      </>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Demo Mode Banner */}
      {isDemoMode && (
        <div className="bg-gradient-to-r from-amber-600/20 via-purple-600/20 to-amber-600/20 border-b border-amber-500/30 px-4 py-2">
          <div className="flex items-center justify-center gap-3">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-amber-400" />
              <span className="text-amber-200 text-sm font-medium">Demo Mode</span>
            </div>
            <span className="text-zinc-400 text-xs hidden sm:inline">|</span>
            <span className="text-zinc-400 text-xs hidden sm:inline">Explore all features without an account</span>
            <Button
              onClick={() => navigate('/auth')}
              size="sm"
              className="ml-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 text-xs px-3 py-1 h-7"
              data-testid="demo-signup-btn"
            >
              Create Account
            </Button>
          </div>
        </div>
      )}
      
      {/* Live Score Ticker - Command Center */}
      {liveScores.length > 0 && (
        <LiveScoreTicker games={liveScores} />
      )}
      
      {/* Breaking News Ticker - Injury Alerts */}
      {breakingNews.length > 0 && (
        <BreakingNewsTicker news={breakingNews} />
      )}
      
      {/* Header - Mobile Optimized */}
      <header className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Eye className="w-6 h-6 text-zinc-400 flex-shrink-0" />
            <h1 className="text-lg font-bold text-white truncate">PICKVISION AI</h1>
            <Badge className="bg-purple-600/30 text-purple-400 border-purple-500/50 text-[10px] flex-shrink-0">
              v3
            </Badge>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {/* User Menu */}
            <div className="relative">
              {isDemoMode ? (
                // Demo mode - show Login button instead of user menu
                <Button
                  onClick={() => navigate('/auth')}
                  variant="ghost"
                  size="sm"
                  className="text-amber-400 hover:text-amber-300 p-1.5 flex items-center gap-1.5 border border-amber-500/30"
                  data-testid="demo-login-btn"
                >
                  <User className="w-4 h-4" />
                  <span className="text-xs hidden sm:inline">Login</span>
                </Button>
              ) : (
                // Normal mode - show user menu
                <>
                  <Button
                    onClick={() => setShowUserMenu(!showUserMenu)}
                    variant="ghost"
                    size="sm"
                    className="text-zinc-400 hover:text-white p-1.5 flex items-center gap-1.5"
                    data-testid="user-menu-btn"
                  >
                    <div className="w-6 h-6 rounded-full bg-gradient-to-br from-amber-500 to-red-500 flex items-center justify-center">
                      <User className="w-3.5 h-3.5 text-white" />
                    </div>
                  </Button>
                  
                  {showUserMenu && (
                    <div className="absolute right-0 mt-2 w-56 bg-zinc-900 border border-zinc-800 rounded-lg shadow-xl z-50 user-menu-dropdown">
                      <div className="p-3 border-b border-zinc-800">
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-amber-500 to-red-500 flex items-center justify-center">
                            <User className="w-4 h-4 text-white" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium text-white truncate">{user?.full_name || 'User'}</p>
                            <p className="text-xs text-zinc-500 truncate">{user?.email}</p>
                          </div>
                        </div>
                      </div>
                      <div className="p-1">
                        <button
                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors"
                          data-testid="pro-tier-btn"
                        >
                          <Crown className="w-4 h-4 text-amber-400" />
                          <span>Upgrade to Pro</span>
                          <Badge className="ml-auto bg-amber-500/20 text-amber-400 border-none text-[10px]">Soon</Badge>
                        </button>
                        <button
                          onClick={handleLogout}
                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-400 hover:text-red-300 hover:bg-red-950/30 rounded transition-colors"
                          data-testid="logout-btn"
                        >
                          <LogOut className="w-4 h-4" />
                          <span>Logout</span>
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
        
        {/* Sub-header info - Intel Freshness Only */}
        <div className="flex items-center justify-between mt-1.5">
          <div className="flex items-center gap-2 text-[10px] text-zinc-500">
            {/* Adaptive Sync Status - Intel Freshness */}
            <div className={`flex items-center gap-1 ${
              syncStatus.has_stale_intel 
                ? 'text-amber-400' 
                : syncStatus.engine_status === 'running' 
                  ? 'text-emerald-400' 
                  : 'text-zinc-500'
            }`}>
              {syncStatus.has_stale_intel ? (
                <AlertTriangle className="w-3 h-3 animate-pulse" />
              ) : (
                <Radio className="w-3 h-3" />
              )}
              <span className="font-mono">
                {syncStatus.has_stale_intel 
                  ? `⚠️ STALE INTEL (${syncStatus.sync_age_display})`
                  : `Intel: ${syncStatus.sync_age_display}`
                }
              </span>
            </div>
            {/* Mission Critical Badge */}
            {syncStatus.mission_critical_games > 0 && (
              <>
                <span>·</span>
                <span className="text-red-400 font-mono animate-pulse">
                  🔴 {syncStatus.mission_critical_games} CRITICAL
                </span>
              </>
            )}
          </div>
        </div>
      </header>

      {/* RAW VALIDATION TABLE MODAL */}
      <RawValidationTable 
        isVisible={showValidationTable} 
        onClose={() => setShowValidationTable(false)} 
      />

      <div className="p-3 space-y-4">
        {/* SCOUTING MISSION - Early Bird Projections (before live lines) */}
        {isEarlyBirdActive && scoutingProjections.length > 0 && (
          <div className="mb-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <div 
                  className="w-8 h-8 rounded-full flex items-center justify-center"
                  style={{ background: 'rgba(255, 165, 0, 0.2)', border: '1px solid #FFA500' }}
                >
                  <Eye className="w-4 h-4 text-orange-400" />
                </div>
                <div>
                  <span className="text-sm font-bold text-orange-400">SCOUTING MISSION</span>
                  <p className="text-[10px] text-orange-400/60">Star players awaiting official lines</p>
                </div>
              </div>
              <div 
                className="px-2 py-1 rounded text-[10px] font-bold"
                style={{ background: 'rgba(255, 165, 0, 0.2)', border: '1px solid #FFA500', color: '#FFA500' }}
              >
                EARLY BIRD • {scoutingProjections.length} PROJECTIONS
              </div>
            </div>
            
            <div className="overflow-x-auto pb-2 -mx-3 px-3">
              <div className="flex gap-3" style={{ minWidth: 'max-content' }}>
                {scoutingProjections.map((proj, idx) => (
                  <div key={proj.player_name || idx} className="w-[320px] flex-shrink-0">
                    <ScoutingMissionCard 
                      projection={proj}
                      onClick={(p) => console.log('[SCOUTING] Clicked:', p.player_name)}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* THE SAFE HAVEN - Top 10 Goblin Plays (Safest) */}
        {vaultPicks.length > 0 && (
          <GoblinReconSwipeSection 
            picks={vaultPicks.slice(0, 10)} 
            onPickClick={handleVaultClick}
            tMinusGames={tMinusGames}
          />
        )}

        {/* THE SHIELD - Safe Haven Parlay Generator (directly under Safe Haven) */}
        {Object.keys(reconData).length > 0 && (
          <TheShieldSwipeSection 
            reconData={reconData}
            onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'recon' })}
          />
        )}

        {/* THE FRONT LINES - Middle Tier (Mild Alternates 5-18% from standard) */}
        {frontLinesPicks.length > 0 && (
          <FrontLinesSwipeSection 
            picks={frontLinesPicks.slice(0, 10)}
            onPickClick={handleRadarClick}
            tMinusGames={tMinusGames}
          />
        )}

        {/* THE FRONT LINES PARLAYS - 2-6 Leg Builds */}
        {frontLinesPicks.length >= 2 && (
          <FrontLinesParlaySection 
            picks={frontLinesPicks.slice(0, 10)}
            onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'builder' })}
          />
        )}

        {/* THE WAR ZONE - Top 10 Demon Plays (Highest Risk/Reward) */}
        {radarPicks.length > 0 && (
          <WarZoneSwipeSection 
            picks={radarPicks.slice(0, 10)} 
            onPickClick={handleRadarClick}
            tMinusGames={tMinusGames}
          />
        )}

        {/* THE GAUNTLET - Demon Parlay Generator */}
        {Object.keys(parlayData).length > 0 && (
          <GauntletSwipeSection 
            parlayData={parlayData}
            onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'builder' })}
          />
        )}

        {/* Trending 10 - Swipeable Cards (Legacy - kept for players without bets) */}
        {trending.length > 0 && popularBets.length === 0 && (
          <TrendingSwipeSection 
            players={trending.slice(0, 10)}
            linesLoaded={linesLoaded}
            onPlayerClick={handlePlayerClick}
            injuryAlerts={injuryAlerts}
          />
        )}
        
        {/* MOST POPULAR BETS - Live Ticker (Top 20 hottest bets) */}
        {/* Always render - shows empty state when awaiting action */}
        <MostPopularBetsSection 
          bets={popularBets}
          lastUpdated={popularBetsLastUpdated}
          onBetClick={handlePopularBetClick}
          isLoading={popularBetsLoading}
          status={popularBetsStatus}
        />

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <Input
            placeholder="Search player..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 py-2 bg-zinc-900 border-zinc-800 text-white text-sm"
            data-testid="search-input"
          />
          {searchTerm && (
            <button 
              onClick={() => setSearchTerm('')}
              className="absolute right-3 top-1/2 transform -translate-y-1/2 text-zinc-500 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Players List */}
        <div className="rounded-lg border border-zinc-800 overflow-hidden" data-testid="players-list">
          {!staticLoaded ? (
            <div className="p-6 text-center">
              <Activity className="w-6 h-6 text-purple-500 mx-auto mb-2 animate-pulse" />
              <p className="text-zinc-400 text-sm">Loading...</p>
            </div>
          ) : filteredPlayers.length === 0 ? (
            <div className="p-6 text-center text-zinc-500 text-sm">
              No players found
            </div>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              {filteredPlayers.map((player) => (
                <PlayerRow
                  key={player.player_name}
                  player={player}
                  isExpanded={false}
                  onToggle={() => {}}
                  onClick={() => handlePlayerClick(player.player_name)}
                  linesLoaded={linesLoaded}
                />
              ))}
            </div>
          )}
        </div>
      </div>
      
      {/* Expanded Parlay View Modal */}
      {expandedParlay && (
        <ExpandedParlayView 
          parlay={expandedParlay.parlay}
          type={expandedParlay.type}
          onClose={() => setExpandedParlay(null)}
          onPickClick={(pick) => {
            // Close modal and navigate to player with highlighted prop
            setExpandedParlay(null);
            const highlightKey = `${pick.stat_type}|${pick.line}|${pick.direction || 'Over'}`;
            setHighlightProp(highlightKey);
            setHighlightType(expandedParlay.type === 'recon' ? 'goblin' : 'demon');
            handlePlayerClick(pick.player_name);
          }}
          players={players}
        />
      )}
      
      {/* Board Intelligence Footer - "Last Synced" Display */}
      <div className="fixed bottom-0 left-0 right-0 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 px-4 py-2 z-40">
        <div className="max-w-7xl mx-auto flex items-center justify-between text-xs">
          <div className="flex items-center gap-4">
            <span className="text-zinc-500 font-mono">
              {boardIntelStatus.time_since_sync_display}
            </span>
            {boardIntelStatus.last_sync_type && (
              <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                boardIntelStatus.last_sync_type === 'primary' 
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' 
                  : 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
              }`}>
                {boardIntelStatus.last_sync_type === 'primary' ? 'FULL SYNC' : 'DELTA'}
              </span>
            )}
            {boardIntelStatus.scheduler_running && (
              <span className="flex items-center gap-1 text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                AUTO
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-zinc-500">
            {boardIntelStatus.next_scheduled_sync && (
              <span className="hidden sm:inline">
                Next: <span className="text-zinc-400">{boardIntelStatus.next_scheduled_sync.time} ({boardIntelStatus.next_scheduled_sync.type?.split(' ')[0]})</span>
              </span>
            )}
            <span className="text-zinc-600">PickVision AI</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DemonGoblinDashboardOptimized;
