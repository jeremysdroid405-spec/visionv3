import React, { useState, useEffect, useCallback, memo, useMemo, useRef } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { 
  Activity, RefreshCw, Search, Database, 
  ChevronDown, ChevronRight, AlertTriangle, Skull, Ghost,
  User, Flame, Star, Clock, Zap, HardDrive, ArrowLeft, X
} from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// NBA CDN headshot URL
const NBA_HEADSHOT_URL = (nbaId) => `https://cdn.nba.com/headshots/nba/latest/1040x760/${nbaId}.png`;

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

const PlayerHeadshot = memo(({ nbaId, playerName, size = 'md', className = '' }) => {
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
  
  // If no NBA ID, show fallback
  if (!nbaId || error) {
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
        src={NBA_HEADSHOT_URL(nbaId)}
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
        {isDemon && <Skull className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />}
        {isGoblin && <Ghost className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />}
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

const TrendingCard = memo(({ player, rank, onClick, linesLoaded }) => {
  const hasInjury = player.injury_info?.has_injury;
  
  return (
    <Card 
      className={`
        bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 
        hover:border-purple-500/50 hover:scale-[1.02] transition-all duration-200
        cursor-pointer active:scale-[0.98]
        ${hasInjury ? 'ring-1 ring-yellow-500/30' : ''}
      `}
      onClick={onClick}
      data-testid={`trending-card-${rank}`}
    >
      <div className="p-3">
        {/* Header: Headshot + Rank Badge + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Headshot with Rank Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={player.nba_id} 
              playerName={player.player_name} 
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
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{player.player_name}</span>
              {rank <= 3 && <Flame className="w-3 h-3 text-orange-500 flex-shrink-0" />}
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{player.team || '---'}</span>
              {player.position && <span>· {player.position}</span>}
            </div>
          </div>
        </div>
        
        {/* Demon/Goblin Counts */}
        {linesLoaded ? (
          <div className="flex items-center gap-3">
            {(player.demons_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <Skull className="w-3.5 h-3.5 text-red-500" />
                <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
              </div>
            )}
            {(player.goblins_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <Ghost className="w-3.5 h-3.5 text-green-500" />
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

const RadarCard = memo(({ pick, rank, onClick }) => {
  // Radar strength determines the glow intensity
  const strengthClass = pick.radar_strength >= 80 ? 'ring-red-500/60' :
                       pick.radar_strength >= 70 ? 'ring-red-500/40' :
                       'ring-red-500/20';
  
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
      case 5: return '🔥 ON FIRE! 9-10/10 games hit';
      case 4: return '🔥 HOT! 80%+ L10 or 5-game streak';
      case 3: return '🔥 WARM! 70%+ L10 or 3-game streak';
      case 2: return '🔥 Mild - 60%+ L10';
      case 1: return '🔥 Cool - 50%+ L10';
      default: return 'Cold';
    }
  };
  
  return (
    <Card 
      className={`
        bg-gradient-to-br from-red-950/30 to-zinc-950 border-red-900/50
        hover:border-red-500/70 hover:scale-[1.02] transition-all duration-200
        cursor-pointer active:scale-[0.98] ring-1 ${strengthClass}
      `}
      onClick={onClick}
      data-testid={`radar-card-${rank}`}
    >
      <div className="p-3">
        {/* Header: Headshot + Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Headshot with Radar Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name} 
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
              <Skull className="w-3 h-3 text-red-500 flex-shrink-0" />
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
        </div>
      </div>
    </Card>
  );
});

RadarCard.displayName = 'RadarCard';

// ==================== GOBLIN VAULT CARD ====================

const VaultCard = memo(({ pick, rank, onClick }) => {
  // Safety level determines the glow intensity
  const safetyClass = pick.safety_level >= 4 ? 'ring-green-500/60' :
                     pick.safety_level >= 3 ? 'ring-green-500/40' :
                     'ring-green-500/20';
  
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
          }`}>🛡️</div>
        ))}
      </div>
    );
  };
  
  const getSafetyDescription = (level) => {
    switch(level) {
      case 5: return '🛡️ FORTRESS! Perfect 10/10 cleared';
      case 4: return '🛡️ VAULT! 90%+ hit rate';
      case 3: return '🛡️ SAFE! 85%+ hit rate';
      case 2: return '🛡️ Reliable - 80%+ hit rate';
      case 1: return '🛡️ Moderate - 70%+ hit rate';
      default: return 'Risky';
    }
  };
  
  return (
    <Card 
      className={`
        bg-gradient-to-br from-green-950/30 to-zinc-950 border-green-900/50
        hover:border-green-500/70 hover:scale-[1.02] transition-all duration-200
        cursor-pointer active:scale-[0.98] ring-1 ${safetyClass}
      `}
      onClick={onClick}
      data-testid={`vault-card-${rank}`}
    >
      <div className="p-3">
        {/* Header: Headshot + Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          {/* Headshot with Vault Badge */}
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name} 
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
              <Ghost className="w-3 h-3 text-green-500 flex-shrink-0" />
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
        </div>
      </div>
    </Card>
  );
});

VaultCard.displayName = 'VaultCard';

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

const CategoryAccordion = memo(({ categoryKey, categoryName, props, isExpanded, onToggle, stats, isHighlightedProp, highlightRef, glowClass = 'beacon-glow', glowSubtleClass = 'beacon-glow-subtle', highlightType = 'demon' }) => {
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
              <Skull className="w-3 h-3 text-red-400" />
              <span className="text-red-400 text-xs font-bold">{demons.length}</span>
            </div>
          )}
          {goblins.length > 0 && (
            <div className="flex items-center gap-1 bg-green-950/50 px-1.5 py-0.5 rounded">
              <Ghost className="w-3 h-3 text-green-400" />
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
            />
          ))}
        </div>
      )}
    </div>
  );
});

CategoryAccordion.displayName = 'CategoryAccordion';

// ==================== LADDER PROP ROW ====================

const LadderPropRow = memo(({ prop, categoryStats, isFirst, isLast, isHighlighted, highlightRef, glowClass = 'beacon-glow', highlightType = 'demon' }) => {
  const line = prop.line;
  const direction = prop.direction;
  const price = prop.price;
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  
  // Calculate hit rate for this specific line from stats
  const l10HitRate = prop.hit_rates?.l10?.hit_rate;
  const seasonHitRate = prop.hit_rates?.season?.hit_rate;
  const hitPct = Math.round((l10HitRate || 0) * 100);
  
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
  
  return (
    <div 
      ref={isHighlighted ? highlightRef : null}
      className={`
        flex items-center justify-between px-3 py-2 rounded-lg transition-all
        ${isDemon ? 'bg-red-950/30 border-l-3 border-red-500 hover:bg-red-950/50' : ''}
        ${isGoblin ? 'bg-green-950/30 border-l-3 border-green-500 hover:bg-green-950/50' : ''}
        ${!isDemon && !isGoblin ? 'bg-zinc-800/30 border-l-3 border-zinc-600 hover:bg-zinc-800/50' : ''}
        ${isHighlighted ? glowClass : ''}
      `}
      data-testid={`ladder-prop-${line}`}
      data-highlighted={isHighlighted ? 'true' : 'false'}
    >
      {/* Left: Line Value + Direction */}
      <div className="flex items-center gap-3">
        {/* Type Icon */}
        <div className="w-5 flex justify-center">
          {isDemon && <Skull className="w-4 h-4 text-red-400" />}
          {isGoblin && <Ghost className="w-4 h-4 text-green-400" />}
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
      
      {/* Right: Odds + Hit Rates */}
      <div className="flex items-center gap-4">
        {/* Hit Rates */}
        <div className="flex items-center gap-2 text-xs">
          {l10HitRate !== undefined && (
            <div className="flex items-center gap-1">
              <span className="text-zinc-500">L10:</span>
              <span className={`font-bold ${hitPct >= 60 ? 'text-green-400' : hitPct >= 40 ? 'text-yellow-400' : 'text-zinc-400'}`}>
                {hitPct}%
              </span>
            </div>
          )}
          {seasonHitRate !== undefined && (
            <div className="flex items-center gap-1">
              <span className="text-zinc-500">Szn:</span>
              <span className={`font-bold ${Math.round(seasonHitRate * 100) >= 60 ? 'text-green-400' : Math.round(seasonHitRate * 100) >= 40 ? 'text-yellow-400' : 'text-zinc-400'}`}>
                {Math.round(seasonHitRate * 100)}%
              </span>
            </div>
          )}
        </div>
        
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
                <Skull className="w-4 h-4 text-red-500" />
                <span className="text-red-400 font-bold">{demons.length}</span>
              </div>
              <div className="flex items-center gap-1">
                <Ghost className="w-4 h-4 text-green-500" />
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
                  <Skull className="w-3.5 h-3.5 text-red-400" />
                  <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
                </div>
              )}
              {(player.goblins_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <Ghost className="w-3.5 h-3.5 text-green-400" />
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

export const DemonGoblinDashboardOptimized = () => {
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
  
  // Navigation state
  const [selectedPlayer, setSelectedPlayer] = useState(null);

  // ==================== CACHED DATA LOADING (ZERO API CALLS) ====================
  
  const loadCachedBoard = useCallback(async () => {
    /**
     * HARD CUTOFF: This is the ONLY data fetch function.
     * It reads ONLY from MongoDB via /api/v3/cached-props.
     * NO Odds API calls are made here.
     */
    try {
      console.log('[CACHED] Loading from MongoDB...');
      
      // Load board, radar, and vault in parallel
      const [boardResponse, radarResponse, vaultResponse] = await Promise.all([
        axios.get(`${API}/v3/cached-props`),
        axios.get(`${API}/v3/demon-radar`),
        axios.get(`${API}/v3/goblin-vault`)
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
      {/* Header - Mobile Optimized */}
      <header className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Skull className="w-6 h-6 text-red-500 flex-shrink-0" />
            <Ghost className="w-6 h-6 text-green-500 flex-shrink-0" />
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
              <Skull className="w-5 h-5 text-red-500" />
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
              <Ghost className="w-5 h-5 text-green-500" />
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
          <div data-testid="radar-section">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Skull className="w-5 h-5 text-red-500" />
                <span className="text-sm font-bold text-red-400">DEMON RADAR</span>
                <Badge className="bg-red-950/50 text-red-400 border-red-800/50 text-[10px]">
                  TOP 10
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                P = (H10×0.6 + H5×0.4) | Min 60%
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {radarPicks.slice(0, 10).map((pick, idx) => (
                <RadarCard 
                  key={`${pick.player_name}-${pick.stat_type}-${pick.demon_line}-${idx}`} 
                  pick={pick} 
                  rank={idx + 1}
                  onClick={() => handleRadarClick(pick)}
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
                <Ghost className="w-5 h-5 text-green-500" />
                <span className="text-sm font-bold text-green-400">THE GOBLIN VAULT</span>
                <Badge className="bg-green-950/50 text-green-400 border-green-800/50 text-[10px]">
                  TOP 10 SAFE PLAYS
                </Badge>
              </div>
              <div className="text-[10px] text-zinc-500">
                Target: 90%+ Hit Rate | Low Risk
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
    </div>
  );
};

export default DemonGoblinDashboardOptimized;
