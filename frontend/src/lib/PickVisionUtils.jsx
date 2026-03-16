/**
 * PICKVISION UTILITY COMPONENTS
 * =============================
 * Shared UI components for player cards, displays, and formatting.
 * Centralized utilities to maintain DRY principle.
 */

import React, { memo } from 'react';
import { Flame, Lock, User } from 'lucide-react';

// ============================================
// FORMATTING UTILITIES
// ============================================

/**
 * Format stat type for display
 */
export const formatStatType = (stat) => {
  if (!stat) return '';
  const statMap = {
    'points': 'PTS',
    'rebounds': 'REB',
    'assists': 'AST',
    'threes': '3PT',
    'steals': 'STL',
    'blocks': 'BLK',
    'pts': 'PTS',
    'reb': 'REB',
    'ast': 'AST',
    '3pm': '3PT',
    'stl': 'STL',
    'blk': 'BLK',
    'pts+reb': 'PTS+REB',
    'pts+ast': 'PTS+AST',
    'pts+reb+ast': 'PRA',
    'reb+ast': 'REB+AST',
    'stl+blk': 'STL+BLK'
  };
  return statMap[stat?.toLowerCase()] || stat?.toUpperCase?.() || stat;
};

/**
 * Get team color for styling
 */
export const getTeamColor = (team) => {
  const teamColors = {
    'LAL': '#552583', 'BOS': '#007A33', 'MIA': '#98002E', 'GSW': '#1D428A',
    'PHX': '#1D1160', 'MIL': '#00471B', 'DEN': '#0E2240', 'PHI': '#006BB6',
    'DAL': '#00538C', 'MEM': '#5D76A9', 'SAC': '#5A2D81', 'CLE': '#860038',
    'NYK': '#006BB6', 'BKN': '#000000', 'ATL': '#E03A3E', 'CHI': '#CE1141',
    'TOR': '#CE1141', 'MIN': '#0C2340', 'NOP': '#0C2340', 'OKC': '#007AC1',
    'IND': '#002D62', 'ORL': '#0077C0', 'WAS': '#002B5C', 'POR': '#E03A3E',
    'SAS': '#C4CED4', 'UTA': '#002B5C', 'HOU': '#CE1141', 'DET': '#C8102E',
    'CHA': '#1D1160', 'LAC': '#C8102E'
  };
  return teamColors[team?.toUpperCase()] || '#6B7280';
};

// ============================================
// DISPLAY COMPONENTS
// ============================================

/**
 * Player Photo with fallback
 */
export const PlayerPhoto = memo(({ photoUrl, playerName, size = 'md' }) => {
  const [hasError, setHasError] = React.useState(false);
  const [isLoading, setIsLoading] = React.useState(true);
  
  const sizeClasses = {
    sm: 'w-6 h-6',
    md: 'w-10 h-10',
    lg: 'w-14 h-14'
  };
  
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  
  // Get initials for fallback
  const initials = playerName
    ? playerName.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()
    : '?';
  
  if (!photoUrl || hasError) {
    return (
      <div className={`${sizeClass} rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center`}>
        <span className="text-zinc-400 text-xs font-medium">{initials}</span>
      </div>
    );
  }
  
  return (
    <div className={`${sizeClass} relative`}>
      {isLoading && (
        <div className={`${sizeClass} absolute rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center`}>
          <span className="text-zinc-500 text-xs">{initials}</span>
        </div>
      )}
      <img
        src={photoUrl}
        alt={playerName || 'Player'}
        className={`${sizeClass} rounded-full object-cover bg-zinc-800 border border-zinc-700`}
        onLoad={() => setIsLoading(false)}
        onError={() => {
          setHasError(true);
          setIsLoading(false);
        }}
      />
    </div>
  );
});

PlayerPhoto.displayName = 'PlayerPhoto';

/**
 * Heat Indicator - flames for hot picks
 */
export const HeatIndicator = memo(({ level = 0, showLabel = false }) => {
  if (level < 3) return null;
  
  const flames = Math.min(level, 5);
  const colorClass = level >= 5 ? 'text-red-500' : level >= 4 ? 'text-orange-400' : 'text-amber-400';
  
  return (
    <div className="flex items-center gap-0.5" title={`Heat Level: ${level}`}>
      {[...Array(Math.min(flames, 3))].map((_, i) => (
        <Flame key={i} className={`w-3 h-3 ${colorClass}`} />
      ))}
      {showLabel && <span className={`text-[10px] ml-1 ${colorClass}`}>HOT</span>}
    </div>
  );
});

HeatIndicator.displayName = 'HeatIndicator';

/**
 * Stat Badge - display stat type and line
 */
export const StatBadge = memo(({ stat, line, direction = 'Over', isDemon = false, isGoblin = false }) => {
  const bgClass = isDemon ? 'bg-amber-500/20' : isGoblin ? 'bg-emerald-500/20' : 'bg-zinc-700/50';
  const textClass = isDemon ? 'text-amber-400' : isGoblin ? 'text-emerald-400' : 'text-white';
  
  return (
    <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${bgClass} ${textClass}`}>
      <span>{formatStatType(stat)}</span>
      <span className="opacity-70">{direction}</span>
      <span className="font-bold">{line}</span>
    </div>
  );
});

StatBadge.displayName = 'StatBadge';

/**
 * Hit Rate Display - L5 and L10 hit rates
 */
export const HitRateDisplay = memo(({ l10, l5, size = 'md' }) => {
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs';
  
  const formatRate = (rate) => {
    if (rate === null || rate === undefined) return '-';
    const num = typeof rate === 'string' ? parseFloat(rate) : rate;
    return `${Math.round(num)}%`;
  };
  
  const getColor = (rate) => {
    if (rate === null || rate === undefined) return 'text-zinc-500';
    const num = typeof rate === 'string' ? parseFloat(rate) : rate;
    if (num >= 70) return 'text-emerald-400';
    if (num >= 50) return 'text-amber-400';
    return 'text-red-400';
  };
  
  return (
    <div className={`flex items-center gap-2 ${textSize}`}>
      <span className="text-zinc-500">L10:</span>
      <span className={getColor(l10)}>{formatRate(l10)}</span>
      <span className="text-zinc-600">|</span>
      <span className="text-zinc-500">L5:</span>
      <span className={getColor(l5)}>{formatRate(l5)}</span>
    </div>
  );
});

HitRateDisplay.displayName = 'HitRateDisplay';

/**
 * Locked Badge - shows when pick is locked/premium
 */
export const LockedBadge = memo(({ isLocked }) => {
  if (!isLocked) return null;
  
  return (
    <div className="absolute top-2 right-2 bg-zinc-800/90 rounded-full p-1" title="Premium Pick">
      <Lock className="w-3 h-3 text-amber-400" />
    </div>
  );
});

LockedBadge.displayName = 'LockedBadge';

/**
 * Vision Text - AI analysis text
 */
export const VisionText = memo(({ text }) => {
  if (!text) return null;
  
  return (
    <p className="text-[11px] text-zinc-400 italic leading-relaxed border-l-2 border-amber-500/30 pl-2">
      {text}
    </p>
  );
});

VisionText.displayName = 'VisionText';

/**
 * Payout Display - multiplier badge
 */
export const PayoutDisplay = memo(({ multiplier }) => {
  if (!multiplier) return null;
  
  const value = typeof multiplier === 'string' ? parseFloat(multiplier) : multiplier;
  const colorClass = value >= 5 ? 'text-amber-400 bg-amber-500/20' : 
                     value >= 3 ? 'text-emerald-400 bg-emerald-500/20' : 
                     'text-zinc-300 bg-zinc-700/50';
  
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-bold ${colorClass}`}>
      {value.toFixed(1)}x
    </span>
  );
});

PayoutDisplay.displayName = 'PayoutDisplay';
