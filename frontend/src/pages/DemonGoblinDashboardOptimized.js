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
  ChevronDown, ChevronRight, AlertTriangle,
  User, Flame, Star, Clock, Zap, HardDrive, ArrowLeft, X,
  DollarSign, TrendingUp, Target, Layers, CheckCircle, XCircle,
  LogOut, Crown
} from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// NBA CDN headshot URL
const NBA_HEADSHOT_URL = (nbaId) => `https://cdn.nba.com/headshots/nba/latest/1040x760/${nbaId}.png`;

// Team Logo URLs (fallback for missing headshots)
const TEAM_LOGOS = {
  "ATL": "https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg",
  "BOS": "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
  "BKN": "https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg",
  "CHA": "https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg",
  "CHI": "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
  "CLE": "https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg",
  "DAL": "https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg",
  "DEN": "https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg",
  "DET": "https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg",
  "GSW": "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
  "HOU": "https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg",
  "IND": "https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg",
  "LAC": "https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg",
  "LAL": "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
  "MEM": "https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg",
  "MIA": "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
  "MIL": "https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg",
  "MIN": "https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg",
  "NOP": "https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg",
  "NYK": "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
  "OKC": "https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg",
  "ORL": "https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg",
  "PHI": "https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg",
  "PHX": "https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg",
  "POR": "https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg",
  "SAC": "https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg",
  "SAS": "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
  "TOR": "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
  "UTA": "https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg",
  "WAS": "https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg",
};

// ==================== BEACON GLOW CSS ====================
// Injected CSS for the infinite pulse animation
const BeaconGlowStyles = () => (
  <style>{`
    /* ==================== DEMON RADAR - Gold/Orange Beacon ==================== */
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
    
    /* ==================== GOBLIN VAULT - Emerald Green Beacon ==================== */
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
  `}</style>
);

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

// ==================== ELITE ICON COMPONENTS ====================

// The Elite Demon - Cyber-Horns (Sharp, Minimalist, Dangerous)
const DemonIcon = memo(({ size = 24, className = '', isScanning = false, hasVision = false }) => (
  <div className={`demon-icon-container ${isScanning ? 'demon-scanning' : ''} ${className}`} style={{ width: size, height: size }}>
    <svg 
      width={size} 
      height={size} 
      viewBox="0 0 24 24" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg"
      className="demon-icon"
    >
      <defs>
        <filter id="demon-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
      {/* Main head circle with glow */}
      <path 
        d="M12 22C16.4183 22 20 18.4183 20 14C20 9.58172 16.4183 6 12 6C7.58172 6 4 9.58172 4 14C4 18.4183 7.58172 22 12 22Z" 
        fill="#FF0000" 
        filter="url(#demon-glow)"
      />
      {/* Left horn */}
      <path 
        d="M5 8L2 2L9 5" 
        stroke="#FF0000" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
      />
      {/* Right horn */}
      <path 
        d="M19 8L22 2L15 5" 
        stroke="#FF0000" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
      />
      {/* Eyes - Negative space slashes */}
      <path 
        d="M8 12L10 14M16 12L14 14" 
        stroke="white" 
        strokeWidth="2" 
        strokeLinecap="round"
      />
    </svg>
    {/* Vision sparkle orbit */}
    {hasVision && <div className="vision-sparkle" />}
  </div>
));

DemonIcon.displayName = 'DemonIcon';

// The Elite Goblin - Sneaky Elf Ears (Cunning, Technical)
const GoblinIcon = memo(({ size = 24, className = '', isClicked = false, hasVision = false }) => {
  const [clicked, setClicked] = useState(false);
  
  const handleClick = (e) => {
    e.stopPropagation();
    setClicked(true);
    setTimeout(() => setClicked(false), 500);
  };
  
  return (
    <div 
      className={`goblin-icon-container ${clicked || isClicked ? 'goblin-pulse' : ''} ${className}`} 
      style={{ width: size, height: size }}
      onClick={handleClick}
    >
      <svg 
        width={size} 
        height={size} 
        viewBox="0 0 24 24" 
        fill="none" 
        xmlns="http://www.w3.org/2000/svg"
        className="goblin-icon"
      >
        {/* Main head circle */}
        <path 
          d="M12 20C15.866 20 19 16.866 19 13C19 9.13401 15.866 6 12 6C8.13401 6 5 9.13401 5 13C5 16.866 8.13401 20 12 20Z" 
          fill="#00FF7F" 
          fillOpacity="0.9"
        />
        {/* Left ear fin */}
        <path 
          d="M5 11L1 7L6 12" 
          fill="#00FF7F"
        />
        {/* Right ear fin */}
        <path 
          d="M19 11L23 7L18 12" 
          fill="#00FF7F"
        />
        {/* Eyes - Dot style */}
        <path 
          d="M9 13H10M14 13H15" 
          stroke="black" 
          strokeWidth="2.5" 
          strokeLinecap="round"
        />
        {/* Smirk */}
        <path 
          d="M10 16.5C10.5 17.5 13.5 17.5 14 16.5" 
          stroke="black" 
          strokeWidth="1" 
          strokeLinecap="round"
        />
      </svg>
      {/* Vision sparkle orbit */}
      {hasVision && <div className="vision-sparkle vision-sparkle-green" />}
    </div>
  );
});

GoblinIcon.displayName = 'GoblinIcon';

// Vision Synergy Badge - For Master Tier cards
const VisionBadge = memo(({ type = 'demon', hasVision = false }) => {
  if (!hasVision) return null;
  
  return (
    <div className={`
      absolute -top-2 -right-2 w-8 h-8 rounded-full 
      flex items-center justify-center z-10
      ${type === 'demon' 
        ? 'bg-gradient-to-br from-red-600 to-red-900 shadow-lg shadow-red-500/50' 
        : 'bg-gradient-to-br from-green-600 to-green-900 shadow-lg shadow-green-500/50'
      }
    `}>
      <Zap className="w-4 h-4 text-white" />
    </div>
  );
});

VisionBadge.displayName = 'VisionBadge';

// ==================== BREAKING NEWS TICKER ====================

const BreakingNewsTicker = memo(({ news }) => {
  if (!news || news.length === 0) return null;
  
  return (
    <div className="bg-gradient-to-r from-red-950/50 via-zinc-900 to-red-950/50 border-b border-red-800/30 py-1.5 overflow-hidden">
      <div className="flex items-center gap-3 animate-scroll">
        <div className="flex items-center gap-2 px-3 flex-shrink-0">
          <div className="flex items-center gap-1 bg-red-600 px-2 py-0.5 rounded">
            <Zap className="w-3 h-3 text-white" />
            <span className="text-[10px] font-bold text-white uppercase tracking-wider">Breaking</span>
          </div>
        </div>
        
        <div className="flex items-center gap-6 whitespace-nowrap">
          {news.map((item, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <AlertTriangle className="w-3 h-3 text-red-400 flex-shrink-0" />
              <span className="text-xs text-red-200">{item.headline}</span>
              {idx < news.length - 1 && <span className="text-zinc-600 mx-4">|</span>}
            </div>
          ))}
          {/* Duplicate for seamless loop */}
          {news.map((item, idx) => (
            <div key={`dup-${idx}`} className="flex items-center gap-2">
              <AlertTriangle className="w-3 h-3 text-red-400 flex-shrink-0" />
              <span className="text-xs text-red-200">{item.headline}</span>
              {idx < news.length - 1 && <span className="text-zinc-600 mx-4">|</span>}
            </div>
          ))}
        </div>
      </div>
      
      <style jsx>{`
        @keyframes scroll {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .animate-scroll {
          animation: scroll 30s linear infinite;
        }
        .animate-scroll:hover {
          animation-play-state: paused;
        }
      `}</style>
    </div>
  );
});

BreakingNewsTicker.displayName = 'BreakingNewsTicker';

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

const RadarCard = memo(({ pick, rank, onClick, isScanning = false }) => {
  // Check if this has a special Vision insight (Master Tier)
  const hasVisionGlow = pick.has_high_conflict || 
    (pick.insight_summary && !pick.insight_summary.toLowerCase().includes('standard'));
  
  // Determine card styling based on Vision status
  const cardClass = hasVisionGlow 
    ? 'demon-master-card vision-pulse' 
    : 'bg-gradient-to-br from-red-950/30 to-zinc-950 border-red-900/50';
  
  // Heat Level flame rendering
  const heatLevel = pick.heat_level || 0;
  const renderFlames = () => {
    if (heatLevel === 0) return null;
    return (
      <div className="flex items-center gap-0.5" title={getHeatDescription(heatLevel)}>
        {[...Array(heatLevel)].map((_, i) => (
          <Flame key={i} className={`w-3 h-3 ${
            heatLevel >= 5 ? 'text-orange-400' :
            heatLevel >= 4 ? 'text-orange-500' :
            heatLevel >= 3 ? 'text-yellow-500' :
            'text-yellow-600'
          }`} fill="currentColor" />
        ))}
      </div>
    );
  };
  
  const getHeatDescription = (level) => {
    switch(level) {
      case 5: return 'ON FIRE! 9-10/10 games hit';
      case 4: return 'HOT! 80%+ L10 or 5-game streak';
      case 3: return 'WARM! 70%+ L10 or 3-game streak';
      case 2: return 'Mild - 60%+ L10';
      case 1: return 'Cool - 50%+ L10';
      default: return 'Cold';
    }
  };
  
  return (
    <Card 
      className={`
        ${cardClass}
        hover:scale-[1.02] transition-all duration-300
        cursor-pointer active:scale-[0.98] relative overflow-visible
      `}
      onClick={onClick}
      data-testid={`radar-card-${rank}`}
    >
      {/* Vision Synergy Badge */}
      {hasVisionGlow && <VisionBadge type="demon" hasVision={true} />}
      
      <div className="p-3">
        {/* Header: Demon Icon + Headshot + Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Demon Icon with breathing glow - add Vision sparkle if has insight */}
          <div className="flex-shrink-0">
            <DemonIcon size={20} isScanning={isScanning} hasVision={hasVisionGlow} />
          </div>
          
          {/* Headshot with Radar Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name}
              team={pick.team}
              photoUrl={pick.photo_url}
              size="md"
              className="ring-2 ring-red-800/50"
            />
            {/* Rank Badge with Radar icon */}
            <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center 
                          font-bold text-[10px] border-2 border-zinc-900 bg-red-600 text-white">
              {rank}
            </div>
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{pick.player_name}</span>
              <DemonIcon size={12} className="flex-shrink-0" />
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{pick.team || '---'}</span>
              <span>· {pick.stat_type}</span>
            </div>
          </div>
        </div>
        
        {/* Heat Level Flames */}
        {heatLevel > 0 && (
          <div className="flex items-center justify-between mb-2 px-1">
            {renderFlames()}
            <span className={`text-[10px] font-medium ${
              heatLevel >= 5 ? 'text-orange-400' :
              heatLevel >= 4 ? 'text-orange-500' :
              heatLevel >= 3 ? 'text-yellow-500' :
              'text-yellow-600'
            }`}>
              {heatLevel >= 5 ? 'ON FIRE' :
               heatLevel >= 4 ? 'HOT' :
               heatLevel >= 3 ? 'WARM' :
               heatLevel >= 2 ? 'MILD' : 'COOL'}
            </span>
          </div>
        )}
        
        {/* Radar Stats */}
        <div className="space-y-1.5">
          {/* Line Info */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Line:</span>
            <div className="flex items-center gap-1">
              <span className="text-white font-bold">{pick.demon_line}</span>
              <span className="text-red-400 font-mono">+100</span>
            </div>
          </div>
          
          {/* Gap Ratio (New v2.0 metric) */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Gap:</span>
            <span className="text-yellow-400 font-medium">
              {pick.gap_pct > 0 ? '+' : ''}{pick.gap_pct}% above std
            </span>
          </div>
          
          {/* Radar Score (Value Ratio) */}
          <div className="mt-2">
            <div className="flex items-center justify-between text-[10px] mb-1">
              <span className="text-zinc-500">Value Score</span>
              <span className={`font-bold ${
                pick.radar_score >= 0.70 ? 'text-green-400' :
                pick.radar_score >= 0.55 ? 'text-yellow-400' :
                'text-zinc-400'
              }`}>
                {(pick.radar_score * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all ${
                  pick.radar_score >= 0.70 ? 'bg-gradient-to-r from-green-500 to-green-400' :
                  pick.radar_score >= 0.55 ? 'bg-gradient-to-r from-yellow-500 to-yellow-400' :
                  'bg-gradient-to-r from-zinc-500 to-zinc-400'
                }`}
                style={{ width: `${Math.min(100, pick.radar_score * 100)}%` }}
              />
            </div>
          </div>
          
          {/* Hit Rate Info */}
          <div className="flex items-center justify-between text-[10px] text-zinc-500 mt-1">
            <span>L10: <span className="text-white">{pick.h10_rate}%</span></span>
            <span>L5: <span className="text-white">{pick.h5_rate}%</span></span>
            {pick.is_hot_streak && (
              <span className="text-orange-400 font-medium">🔥 STREAK</span>
            )}
          </div>
          
          {/* AI Explainer - Why this Demon? */}
          {pick.insight_summary && (
            <div className="mt-2 pt-2 border-t border-red-900/30">
              <div className="flex items-center gap-1 mb-1">
                <Zap className="w-2.5 h-2.5 text-purple-400" />
                <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
              </div>
              <p className="text-[10px] text-purple-300/80 leading-tight line-clamp-2 italic">
                "{pick.insight_summary}"
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
});

RadarCard.displayName = 'RadarCard';

// ==================== GOBLIN VAULT CARD ====================

const VaultCard = memo(({ pick, rank, onClick }) => {
  const [isClicked, setIsClicked] = useState(false);
  
  // Check if this has a special Vision insight
  const hasVisionGlow = pick.has_high_conflict || 
    (pick.insight_summary && !pick.insight_summary.toLowerCase().includes('standard'));
  
  // Determine card styling based on Vision status
  const cardClass = hasVisionGlow 
    ? 'goblin-vault-card' 
    : 'bg-gradient-to-br from-green-950/30 to-zinc-950 border-green-900/50';
  
  // Shield rendering for safety level
  const safetyLevel = pick.safety_level || 0;
  const renderShields = () => {
    if (safetyLevel === 0) return null;
    return (
      <div className="flex items-center gap-0.5" title={getSafetyDescription(safetyLevel)}>
        {[...Array(safetyLevel)].map((_, i) => (
          <div key={i} className={`w-3 h-3 flex items-center justify-center text-[10px] ${
            safetyLevel >= 5 ? 'text-green-400' :
            safetyLevel >= 4 ? 'text-green-500' :
            safetyLevel >= 3 ? 'text-emerald-500' :
            'text-emerald-600'
          }`}>⬡</div>
        ))}
      </div>
    );
  };
  
  const getSafetyDescription = (level) => {
    switch(level) {
      case 5: return 'FORTRESS! Perfect 10/10 cleared';
      case 4: return 'VAULT! 90%+ hit rate';
      case 3: return 'SAFE! 85%+ hit rate';
      case 2: return 'Reliable - 80%+ hit rate';
      case 1: return 'Moderate - 70%+ hit rate';
      default: return 'Risky';
    }
  };
  
  const handleClick = () => {
    setIsClicked(true);
    setTimeout(() => setIsClicked(false), 400);
    onClick?.();
  };
  
  return (
    <Card 
      className={`
        ${cardClass}
        hover:scale-[1.02] transition-all duration-300
        cursor-pointer active:scale-[0.98] relative overflow-visible
      `}
      onClick={handleClick}
      data-testid={`vault-card-${rank}`}
    >
      {/* Vision Synergy Badge */}
      {hasVisionGlow && <VisionBadge type="goblin" hasVision={true} />}
      
      <div className="p-3">
        {/* Header: Goblin Icon + Headshot + Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Goblin Icon with pulse on click - add Vision sparkle if has insight */}
          <div className="flex-shrink-0">
            <GoblinIcon size={20} isClicked={isClicked} hasVision={hasVisionGlow} />
          </div>
          
          {/* Headshot with Vault Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name}
              team={pick.team}
              photoUrl={pick.photo_url}
              size="md"
              className="ring-2 ring-green-800/50"
            />
            {/* Rank Badge with shield icon */}
            <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center 
                          font-bold text-[10px] border-2 border-zinc-900 bg-green-600 text-white">
              {rank}
            </div>
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{pick.player_name}</span>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{pick.team || '---'}</span>
              <span>· {pick.stat_type}</span>
            </div>
          </div>
        </div>
        
        {/* Safety Level Shields */}
        {safetyLevel > 0 && (
          <div className="flex items-center justify-between mb-2 px-1">
            {renderShields()}
            <span className={`text-[10px] font-medium ${
              safetyLevel >= 5 ? 'text-green-400' :
              safetyLevel >= 4 ? 'text-green-500' :
              safetyLevel >= 3 ? 'text-emerald-500' :
              'text-emerald-600'
            }`}>
              {safetyLevel >= 5 ? 'FORTRESS' :
               safetyLevel >= 4 ? 'VAULT' :
               safetyLevel >= 3 ? 'SAFE' :
               safetyLevel >= 2 ? 'RELIABLE' : 'MODERATE'}
            </span>
          </div>
        )}
        
        {/* Vault Stats */}
        <div className="space-y-1.5">
          {/* Line Info */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Line:</span>
            <div className="flex items-center gap-1">
              <span className="text-white font-bold">{pick.goblin_line}</span>
              <span className="text-green-400 font-mono">{pick.price > 0 ? `+${pick.price}` : pick.price}</span>
            </div>
          </div>
          
          {/* Safety Rating - Main metric */}
          <div className="flex items-center justify-between text-xs bg-green-950/50 px-2 py-1 rounded">
            <span className="text-green-400 font-semibold">Safety:</span>
            <span className="text-white font-bold">{pick.safety_rating}%</span>
            <span className="text-zinc-400 text-[10px]">
              Clear in {pick.safety_string}
            </span>
          </div>
          
          {/* Vault Score (Safety + Value) */}
          <div className="mt-2">
            <div className="flex items-center justify-between text-[10px] mb-1">
              <span className="text-zinc-500">Vault Score</span>
              <span className={`font-bold ${
                pick.vault_score >= 0.80 ? 'text-green-400' :
                pick.vault_score >= 0.65 ? 'text-emerald-400' :
                'text-zinc-400'
              }`}>
                {(pick.vault_score * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all ${
                  pick.vault_score >= 0.80 ? 'bg-gradient-to-r from-green-500 to-green-400' :
                  pick.vault_score >= 0.65 ? 'bg-gradient-to-r from-emerald-500 to-emerald-400' :
                  'bg-gradient-to-r from-zinc-500 to-zinc-400'
                }`}
                style={{ width: `${Math.min(100, pick.vault_score * 100)}%` }}
              />
            </div>
          </div>
          
          {/* Hit Rate Info */}
          <div className="flex items-center justify-between text-[10px] text-zinc-500 mt-1">
            <span>L10: <span className="text-white">{pick.h10_rate}%</span></span>
            <span>L5: <span className="text-white">{pick.h5_rate}%</span></span>
            {pick.is_perfect_streak && (
              <span className="text-green-400 font-medium">✓ PERFECT</span>
            )}
          </div>
          
          {/* AI Explainer - Why this Goblin? */}
          {pick.insight_summary && (
            <div className="mt-2 pt-2 border-t border-green-900/30">
              <div className="flex items-center gap-1 mb-1">
                <Zap className="w-2.5 h-2.5 text-purple-400" />
                <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
              </div>
              <p className="text-[10px] text-purple-300/80 leading-tight line-clamp-2 italic">
                "{pick.insight_summary}"
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
});

VaultCard.displayName = 'VaultCard';

// ==================== PARLAY CARD ====================

const ParlayCard = memo(({ parlay, pickCount, onClick }) => {
  const picks = parlay?.picks || [];
  const payoutMultiplier = parlay?.estimated_payout || 0;
  const combinedProb = parlay?.combined_probability || 0;
  const payoutRange = parlay?.payout_range || '';
  const lineupValid = parlay?.lineup_valid ?? true;
  const lineupStatus = parlay?.lineup_status || 'Valid (Multi-Team)';
  const teamCount = parlay?.team_count || 0;
  const hasOpponentPair = parlay?.has_opponent_pair || false;
  
  // Color scheme based on pick count
  const colorScheme = {
    2: { bg: 'from-amber-950/40', border: 'border-amber-700/50', text: 'text-amber-400', badge: 'bg-amber-500/20' },
    3: { bg: 'from-orange-950/40', border: 'border-orange-700/50', text: 'text-orange-400', badge: 'bg-orange-500/20' },
    4: { bg: 'from-red-950/40', border: 'border-red-700/50', text: 'text-red-400', badge: 'bg-red-500/20' },
    5: { bg: 'from-purple-950/40', border: 'border-purple-700/50', text: 'text-purple-400', badge: 'bg-purple-500/20' },
    6: { bg: 'from-pink-950/40', border: 'border-pink-700/50', text: 'text-pink-400', badge: 'bg-pink-500/20' }
  };
  
  const colors = colorScheme[pickCount] || colorScheme[2];
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${colors.bg} to-zinc-950 ${colors.border}
        hover:scale-[1.02] transition-all duration-200 cursor-pointer
        overflow-hidden ${!lineupValid ? 'opacity-60' : ''}
      `}
      onClick={onClick}
      data-testid={`parlay-card-${pickCount}`}
    >
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full ${colors.badge} flex items-center justify-center`}>
              <span className={`text-lg font-black ${colors.text}`}>{pickCount}</span>
            </div>
            <div>
              <div className={`text-sm font-bold ${colors.text}`}>{parlay?.name || `${pickCount}-PICK`}</div>
              <div className="text-[10px] text-zinc-500">{parlay?.description || ''}</div>
            </div>
          </div>
          
          {/* Payout Badge */}
          <Badge className={`${colors.badge} ${colors.text} border-none text-xs font-bold px-2 py-1`}>
            <DollarSign className="w-3 h-3 mr-0.5" />
            {payoutMultiplier}x
          </Badge>
        </div>
        
        {/* Lineup Status Indicator */}
        <div className={`flex items-center gap-1 mb-2 text-[9px] px-2 py-0.5 rounded ${
          lineupValid 
            ? hasOpponentPair ? 'bg-blue-500/20 text-blue-300' : 'bg-green-500/20 text-green-300'
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
        
        {/* Picks List */}
        <div className="space-y-1.5 mb-3">
          {picks.slice(0, 4).map((pick, idx) => (
            <div 
              key={`${pick.player_name}-${pick.stat_type}-${idx}`}
              className="bg-zinc-900/50 rounded px-2 py-1"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <DemonIcon size={12} className="flex-shrink-0" />
                  <span className="text-xs text-white truncate">{pick.player_name}</span>
                  <span className="text-[10px] text-zinc-500">{pick.team}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-zinc-400">{pick.stat_type}</span>
                  <span className="text-xs font-bold text-white">{pick.line}</span>
                  {pick.has_heat_boost && <Flame className="w-3 h-3 text-orange-400" />}
                  {pick.insight_summary && <Zap className="w-3 h-3 text-purple-400" title="Has AI Vision" />}
                </div>
              </div>
              {/* Mini Vision Preview */}
              {pick.insight_summary && (
                <div className="mt-1 text-[9px] text-purple-300/70 line-clamp-1 italic pl-5">
                  "{pick.insight_summary}"
                </div>
              )}
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
            <span className="text-zinc-500">Combined:</span>
            <span className={`font-bold ${combinedProb >= 20 ? 'text-green-400' : combinedProb >= 10 ? 'text-yellow-400' : 'text-zinc-400'}`}>
              {combinedProb}%
            </span>
          </div>
          <div className="text-[10px] text-zinc-500">
            Range: <span className="text-white">{payoutRange}</span>
          </div>
        </div>
      </div>
    </Card>
  );
});

ParlayCard.displayName = 'ParlayCard';

// ==================== GOBLIN RECON CARD ====================

const ReconCard = memo(({ parlay, tier, onClick }) => {
  const picks = parlay?.picks || [];
  const reliability = parlay?.reliability || 0;
  const payoutEstimate = parlay?.estimated_payout || 0;
  const badge = parlay?.badge || '';
  const flexProb = parlay?.flex_probability || null;
  const lineupValid = parlay?.lineup_valid ?? true;
  const lineupStatus = parlay?.lineup_status || 'Valid (Multi-Team)';
  const teamCount = parlay?.team_count || 0;
  
  // Emerald green theme for Recon
  const tierStyles = {
    daily_double: { 
      bg: 'from-emerald-950/60', 
      border: 'border-emerald-500/50', 
      text: 'text-emerald-400',
      badge: 'bg-emerald-500/30',
      number: 2
    },
    green_ladder_3: { 
      bg: 'from-teal-950/60', 
      border: 'border-teal-500/50', 
      text: 'text-teal-400',
      badge: 'bg-teal-500/30',
      number: 3
    },
    green_ladder_4: { 
      bg: 'from-cyan-950/60', 
      border: 'border-cyan-500/50', 
      text: 'text-cyan-400',
      badge: 'bg-cyan-500/30',
      number: 4
    },
    fortress_flex: { 
      bg: 'from-green-950/60', 
      border: 'border-green-500/50', 
      text: 'text-green-400',
      badge: 'bg-green-500/30',
      number: 6
    }
  };
  
  const style = tierStyles[tier] || tierStyles.daily_double;
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${style.bg} to-zinc-950 ${style.border}
        hover:scale-[1.02] transition-all duration-200 cursor-pointer
        overflow-hidden border-2 ${!lineupValid ? 'opacity-60' : ''}
      `}
      onClick={onClick}
      data-testid={`recon-card-${tier}`}
    >
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full ${style.badge} flex items-center justify-center`}>
              <GoblinIcon size={16} />
            </div>
            <div>
              <div className={`text-sm font-bold ${style.text}`}>{parlay?.name || tier}</div>
              <div className="text-[10px] text-zinc-400">{parlay?.description || ''}</div>
            </div>
          </div>
          
          {/* Badge */}
          {badge && (
            <Badge className={`${style.badge} ${style.text} border-none text-[10px] font-bold px-2 py-0.5`}>
              {badge}
            </Badge>
          )}
        </div>
        
        {/* Lineup Status Indicator */}
        <div className={`flex items-center gap-1 mb-2 text-[9px] px-2 py-0.5 rounded ${
          lineupValid 
            ? 'bg-emerald-500/20 text-emerald-300'
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
        
        {/* Reliability Meter */}
        <div className="mb-2">
          <div className="flex items-center justify-between text-[10px] mb-1">
            <span className="text-zinc-400">Reliability</span>
            <span className={`font-bold ${reliability >= 70 ? 'text-emerald-400' : reliability >= 50 ? 'text-yellow-400' : 'text-red-400'}`}>
              {reliability}%
            </span>
          </div>
          <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
            <div 
              className={`h-full transition-all duration-500 ${
                reliability >= 70 ? 'bg-gradient-to-r from-emerald-500 to-green-400' : 
                reliability >= 50 ? 'bg-gradient-to-r from-yellow-500 to-amber-400' : 
                'bg-gradient-to-r from-red-500 to-orange-400'
              }`}
              style={{ width: `${Math.min(reliability, 100)}%` }}
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
                  <GoblinIcon size={12} className="flex-shrink-0" />
                  <span className="text-xs text-white truncate">{pick.player_name}</span>
                  <span className="text-[10px] text-zinc-500">{pick.team}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-zinc-400">{pick.stat_type}</span>
                  <span className="text-xs font-bold text-emerald-300">{pick.line}</span>
                  {pick.is_recon_lock && (
                    <span className="text-[8px] bg-emerald-500/30 text-emerald-300 px-1 rounded">LOCK</span>
                  )}
                  {pick.insight_summary && <Zap className="w-3 h-3 text-purple-400" title="Has AI Vision" />}
                </div>
              </div>
              {/* Mini Vision Preview */}
              {pick.insight_summary && (
                <div className="mt-1 text-[9px] text-purple-300/70 line-clamp-1 italic pl-5">
                  "{pick.insight_summary}"
                </div>
              )}
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
            <DollarSign className="w-3 h-3 text-emerald-500" />
            <span className="text-zinc-400">Est. Payout:</span>
            <span className="font-bold text-white">~{payoutEstimate}x</span>
          </div>
          {flexProb && (
            <div className="text-[10px] text-zinc-400">
              Flex (5/6): <span className="text-emerald-300 font-bold">{flexProb}%</span>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
});

ReconCard.displayName = 'ReconCard';

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
        {pick.insight_summary && (
          <div className="mt-2 bg-gradient-to-r from-purple-950/40 via-zinc-900/50 to-purple-950/40 rounded-lg px-3 py-2 border border-purple-800/30">
            <div className="flex items-center gap-1.5 mb-1">
              <Zap className="w-3 h-3 text-purple-400" />
              <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
            </div>
            <p className="text-[11px] text-purple-200/80 leading-relaxed line-clamp-2 italic">
              "{pick.insight_summary}"
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
            {pick.insight_summary && (
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
                    "{pick.insight_summary}"
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
                <Badge className={`${highlightType === 'goblin' ? 'bg-green-500/20 text-green-400 border-green-500/50' : 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50'} text-[10px]`}>
                  {highlightType === 'goblin' ? 'VAULT TARGET' : 'RADAR TARGET'}
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
  const insightSummary = insights.insight_summary || '';
  const confidenceRating = insights.ai_confidence_rating || 50;
  
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
          
          {/* Radar/Vault Pick Badge */}
          {isHighlighted && (
            <Badge className={`${highlightType === 'goblin' ? 'bg-green-500/20 text-green-400 border-green-500/50' : 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50'} text-[10px] animate-pulse`}>
              {highlightType === 'goblin' ? 'VAULT PICK' : 'RADAR PICK'}
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
                      <p className="text-xs text-zinc-500">AI insight generating... Check back soon.</p>
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
                  playerInsights={player?.insights}
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
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [syncedAt, setSyncedAt] = useState(null);
  const [parlayData, setParlayData] = useState({});
  const [reconData, setReconData] = useState({});  // Goblin Recon parlays
  const [expandedParlay, setExpandedParlay] = useState(null);  // Currently expanded parlay view
  
  // Injury Intelligence state
  const [injuryAlerts, setInjuryAlerts] = useState({});  // player_name -> injury_info
  const [breakingNews, setBreakingNews] = useState([]);  // Breaking news ticker
  
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
      
      // Load board, radar, vault, parlays, recon, and injuries in parallel
      const [boardResponse, radarResponse, vaultResponse, parlayResponse, reconResponse, injuryResponse, newsResponse] = await Promise.all([
        axios.get(`${API}/v3/cached-props`),
        axios.get(`${API}/v3/demon-radar`),
        axios.get(`${API}/v3/goblin-vault`),
        axios.get(`${API}/v3/parlay-builder`),
        axios.get(`${API}/v3/goblin-recon`),
        axios.get(`${API}/v3/injuries/alerts`).catch(() => ({ data: { success: false, alerts: {} }})),
        axios.get(`${API}/v3/breaking-news?injury_only=true`).catch(() => ({ data: { success: false, news: [] }}))
      ]);
      
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
      
      // Load radar picks
      if (radarResponse.data.success) {
        setRadarPicks(radarResponse.data.picks || []);
        console.log(`[RADAR] Loaded ${radarResponse.data.picks_count} radar picks`);
      }
      
      // Load vault picks
      if (vaultResponse.data.success) {
        setVaultPicks(vaultResponse.data.picks || []);
        console.log(`[VAULT] Loaded ${vaultResponse.data.picks_count} vault picks`);
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
  
  const totalDemons = players.reduce((acc, p) => acc + (p.demons_count || 0), 0);
  const totalGoblins = players.reduce((acc, p) => acc + (p.goblins_count || 0), 0);
  
  // ==================== NAVIGATION ====================
  
  // State for highlighted prop from Radar or Vault
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon'); // 'demon' = gold glow, 'goblin' = green glow
  
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
      
      {/* Breaking News Ticker - Injury Alerts */}
      {breakingNews.length > 0 && (
        <BreakingNewsTicker news={breakingNews} />
      )}
      
      {/* Header - Mobile Optimized */}
      <header className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <DemonIcon size={24} />
            <GoblinIcon size={24} />
            <h1 className="text-lg font-bold text-white truncate">DEMON & GOBLIN</h1>
            <Badge className="bg-purple-600/30 text-purple-400 border-purple-500/50 text-[10px] flex-shrink-0">
              v3
            </Badge>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              onClick={triggerSync}
              disabled={syncing}
              variant="ghost"
              size="sm"
              className="text-purple-400 hover:text-purple-300 p-1.5"
              data-testid="sync-btn"
              title="Sync from Odds API (uses credits)"
            >
              <Database className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
            </Button>
            <Button
              onClick={loadCachedBoard}
              disabled={!staticLoaded}
              variant="ghost"
              size="sm"
              className="text-zinc-400 hover:text-white p-1.5"
              data-testid="refresh-btn"
              title="Reload from MongoDB (free)"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>
            
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
        
        {/* Sub-header info with Last Updated timestamp */}
        <div className="flex items-center gap-2 mt-1.5 text-[10px] text-zinc-500">
          <span>{players.length} Players</span>
          <span>·</span>
          <HardDrive className="w-3 h-3" />
          <span>CACHED</span>
          {syncedAt && (
            <>
              <span>·</span>
              <Clock className="w-3 h-3" />
              <span>Last Updated: {new Date(syncedAt).toLocaleString()}</span>
            </>
          )}
        </div>
      </header>

      <div className="p-3 space-y-4">
        {/* Stats Bar - Compact */}
        <div className="flex items-center justify-between bg-zinc-900/50 rounded-lg px-3 py-2">
          <div className="flex items-center gap-4">
            <div 
              className={`flex items-center gap-1.5 cursor-pointer ${filterType === 'demons' ? 'ring-1 ring-red-500 rounded px-1 -mx-1' : ''}`}
              onClick={() => setFilterType(filterType === 'demons' ? 'all' : 'demons')}
            >
              <DemonIcon size={20} />
              {linesLoaded ? (
                <span className="text-red-400 font-bold text-xl">{totalDemons}</span>
              ) : (
                <div className="w-10 h-6 bg-zinc-800 animate-pulse rounded" />
              )}
            </div>
            
            <div 
              className={`flex items-center gap-1.5 cursor-pointer ${filterType === 'goblins' ? 'ring-1 ring-green-500 rounded px-1 -mx-1' : ''}`}
              onClick={() => setFilterType(filterType === 'goblins' ? 'all' : 'goblins')}
            >
              <GoblinIcon size={20} />
              {linesLoaded ? (
                <span className="text-green-400 font-bold text-xl">{totalGoblins}</span>
              ) : (
                <div className="w-10 h-6 bg-zinc-800 animate-pulse rounded" />
              )}
            </div>
          </div>
          
          <div className="text-xs text-zinc-500 flex items-center gap-1">
            <HardDrive className="w-3 h-3" />
            MongoDB
          </div>
        </div>

        {/* DEMON RADAR - Top 10 Mathematical Picks */}
        {radarPicks.length > 0 && (
          <div data-testid="radar-section" className="demon-radar-scanning">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <DemonIcon size={24} isScanning={true} />
                <span className="text-sm font-bold text-red-400">DEMON RADAR</span>
                <Badge className="bg-red-950/50 text-red-400 border-red-800/50 text-[10px]">
                  TOP 10 HIGH-ALPHA
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                King of Longshots | Dangerous but Profitable
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {radarPicks.slice(0, 10).map((pick, idx) => (
                <RadarCard 
                  key={`${pick.player_name}-${pick.stat_type}-${pick.demon_line}-${idx}`} 
                  pick={pick} 
                  rank={idx + 1}
                  onClick={() => handleRadarClick(pick)}
                  isScanning={true}
                />
              ))}
            </div>
          </div>
        )}

        {/* GOBLIN VAULT - Top 10 Safe Plays */}
        {vaultPicks.length > 0 && (
          <div data-testid="vault-section">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <GoblinIcon size={24} />
                <span className="text-sm font-bold text-green-400">THE GOBLIN VAULT</span>
                <Badge className="bg-green-950/50 text-green-400 border-green-800/50 text-[10px]">
                  TOP 10 HEX-STACK
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                Consistent Vault-Hunters | Stack Green
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {vaultPicks.slice(0, 10).map((pick, idx) => (
                <VaultCard 
                  key={`${pick.player_name}-${pick.stat_type}-${pick.goblin_line}-${idx}`} 
                  pick={pick} 
                  rank={idx + 1}
                  onClick={() => handleVaultClick(pick)}
                />
              ))}
            </div>
          </div>
        )}

        {/* THE BIG MONEY BUILDER - Parlay Generator */}
        {Object.keys(parlayData).length > 0 && (
          <div data-testid="parlay-section" className="mt-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <DollarSign className="w-5 h-5 text-amber-500" />
                <span className="text-sm font-bold text-amber-400">THE BIG MONEY BUILDER</span>
                <Badge className="bg-amber-950/50 text-amber-400 border-amber-800/50 text-[10px]">
                  PARLAY GENERATOR
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                Whale Scoring + Correlation Filter
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              {[2, 3, 4, 5, 6].map(pickCount => {
                const parlay = parlayData[`${pickCount}_pick`];
                if (!parlay) return null;
                return (
                  <ParlayCard
                    key={`parlay-${pickCount}`}
                    parlay={parlay}
                    pickCount={pickCount}
                    onClick={() => {
                      setExpandedParlay({ parlay, type: 'builder' });
                    }}
                  />
                );
              })}
            </div>
            
            {/* Parlay Legend */}
            <div className="mt-2 flex items-center justify-center gap-4 text-[10px] text-zinc-500">
              <span><Flame className="w-3 h-3 inline text-orange-400" /> = Heat Boost (20%)</span>
              <span><Layers className="w-3 h-3 inline text-blue-400" /> = Same-Game Correlation</span>
              <span><TrendingUp className="w-3 h-3 inline text-green-400" /> = 30%+ Ceiling Frequency</span>
            </div>
          </div>
        )}

        {/* THE GOBLIN RECON - High-Consistency Parlays */}
        {Object.keys(reconData).length > 0 && (
          <div data-testid="recon-section" className="mt-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <GoblinIcon size={20} />
                <span className="text-sm font-bold text-emerald-400">THE GOBLIN RECON</span>
                <Badge className="bg-emerald-950/50 text-emerald-400 border-emerald-800/50 text-[10px]">
                  HIGH RELIABILITY
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                Floor Scoring + 88%+ Hit Rate
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {['daily_double', 'green_ladder_3', 'green_ladder_4', 'fortress_flex'].map(tier => {
                const parlay = reconData[tier];
                if (!parlay) return null;
                return (
                  <ReconCard
                    key={`recon-${tier}`}
                    parlay={parlay}
                    tier={tier}
                    onClick={() => {
                      setExpandedParlay({ parlay, type: 'recon' });
                    }}
                  />
                );
              })}
            </div>
            
            {/* Recon Legend */}
            <div className="mt-2 flex items-center justify-center gap-4 text-[10px] text-zinc-500">
              <span><GoblinIcon size={12} className="inline" /> = 88%+ Hit Rate</span>
              <span className="text-emerald-300 font-bold">LOCK</span><span> = Floor ≥ Line</span>
              <span><Layers className="w-3 h-3 inline text-cyan-400" /> = Game Diversified</span>
            </div>
          </div>
        )}

        {/* Trending 10 - Clickable Cards */}
        {trending.length > 0 && (
          <div data-testid="trending-section">
            <div className="flex items-center gap-2 mb-2">
              <Flame className="w-4 h-4 text-orange-500" />
              <span className="text-sm font-bold text-white">Most Popular Today</span>
              <Star className="w-4 h-4 text-yellow-500" />
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {trending.slice(0, 10).map((player, idx) => (
                <TrendingCard 
                  key={player.player_name} 
                  player={player} 
                  rank={idx + 1}
                  linesLoaded={linesLoaded}
                  onClick={() => handlePlayerClick(player.player_name)}
                  injuryAlerts={injuryAlerts}
                />
              ))}
            </div>
          </div>
        )}

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
    </div>
  );
};

export default DemonGoblinDashboardOptimized;
