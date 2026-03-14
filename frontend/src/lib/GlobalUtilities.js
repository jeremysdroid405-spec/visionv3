/**
 * PICKVISION GLOBAL UTILITIES
 * ===========================
 * Shared components, hooks, and utilities for the PickVision dashboard.
 * Consolidated from DemonGoblinDashboardOptimized.js
 */

import React, { memo, useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  Flame, Shield, Clock, TrendingUp, Target, Eye, Lock,
  ChevronRight, AlertTriangle, Zap, Star
} from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL || '';

// ==================== LOOKUP TABLES ====================

export const STAT_LABELS = {
  'PTS': 'Points', 'REB': 'Rebounds', 'AST': 'Assists',
  'BLK': 'Blocks', 'STL': 'Steals', 'TO': 'Turnovers',
  'PRA': 'Pts+Reb+Ast', 'PR': 'Pts+Reb', 'PA': 'Pts+Ast',
  'RA': 'Reb+Ast', 'P+R': 'Pts+Reb', 'P+A': 'Pts+Ast',
  'R+A': 'Reb+Ast', 'P+R+A': 'Pts+Reb+Ast', '3PM': '3-Pointers',
  'FGM': 'Field Goals', 'FTM': 'Free Throws', 'DD': 'Double-Double',
  'TD': 'Triple-Double', 'FANTASY': 'Fantasy Score'
};

export const HEAT_LEVELS = {
  5: { label: 'ON FIRE', color: 'text-orange-400', bg: 'bg-orange-500/20', flames: 5 },
  4: { label: 'HOT', color: 'text-yellow-400', bg: 'bg-yellow-500/20', flames: 4 },
  3: { label: 'WARM', color: 'text-amber-400', bg: 'bg-amber-500/20', flames: 3 },
  2: { label: 'MILD', color: 'text-zinc-400', bg: 'bg-zinc-500/20', flames: 2 },
  1: { label: 'COOL', color: 'text-blue-400', bg: 'bg-blue-500/20', flames: 1 },
  0: { label: 'COLD', color: 'text-zinc-500', bg: 'bg-zinc-600/20', flames: 0 }
};

export const TEAM_COLORS = {
  'ATL': '#E03A3E', 'BOS': '#007A33', 'BKN': '#000000', 'CHA': '#1D1160',
  'CHI': '#CE1141', 'CLE': '#860038', 'DAL': '#00538C', 'DEN': '#0E2240',
  'DET': '#C8102E', 'GSW': '#1D428A', 'HOU': '#CE1141', 'IND': '#002D62',
  'LAC': '#C8102E', 'LAL': '#552583', 'MEM': '#5D76A9', 'MIA': '#98002E',
  'MIL': '#00471B', 'MIN': '#0C2340', 'NOP': '#0C2340', 'NYK': '#006BB6',
  'OKC': '#007AC1', 'ORL': '#0077C0', 'PHI': '#006BB6', 'PHX': '#1D1160',
  'POR': '#E03A3E', 'SAC': '#5A2D81', 'SAS': '#C4CED4', 'TOR': '#CE1141',
  'UTA': '#002B5C', 'WAS': '#002B5C'
};

// ==================== UTILITY FUNCTIONS ====================

export const formatStatType = (stat) => STAT_LABELS[stat] || stat;

export const getHeatLevel = (level) => HEAT_LEVELS[level] || HEAT_LEVELS[0];

export const getTeamColor = (team) => TEAM_COLORS[team] || '#6B7280';

export const formatPayout = (multiplier) => {
  if (!multiplier) return '—';
  return `${multiplier.toFixed(2)}x`;
};

export const formatPercentage = (value) => {
  if (value === null || value === undefined) return '—';
  return `${Math.round(value)}%`;
};

// ==================== CUSTOM HOOKS ====================

export const useDataFetch = (endpoint, initialData = null, refreshInterval = null) => {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const response = await axios.get(`${API}${endpoint}`);
      setData(response.data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    fetchData();
    if (refreshInterval) {
      const interval = setInterval(fetchData, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [fetchData, refreshInterval]);

  return { data, loading, error, refetch: fetchData };
};

// ==================== SHARED COMPONENTS ====================

// Player Photo - Simple hard-coded image
export const PlayerPhoto = memo(({ photoUrl, playerName, size = 'md' }) => {
  const [imgError, setImgError] = React.useState(false);
  const sizes = {
    sm: 'w-8 h-8',
    md: 'w-12 h-12',
    lg: 'w-16 h-16',
    xl: 'w-20 h-20'
  };
  
  const initials = playerName?.split(' ').map(n => n[0]).join('').slice(0, 2);
  
  return (
    <div className={`${sizes[size]} rounded-full overflow-hidden bg-zinc-800 flex-shrink-0`}>
      {photoUrl && !imgError ? (
        <img 
          src={photoUrl} 
          alt={playerName}
          className="w-full h-full object-cover"
          loading="lazy"
          onError={() => setImgError(true)}
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-zinc-600 text-xs font-bold">
          {initials}
        </div>
      )}
    </div>
  );
});
PlayerPhoto.displayName = 'PlayerPhoto';

// Heat Indicator - Flame badges
export const HeatIndicator = memo(({ level, showLabel = false }) => {
  const heat = getHeatLevel(level);
  if (level < 3) return null;
  
  return (
    <div className={`flex items-center gap-1 px-2 py-0.5 rounded ${heat.bg}`}>
      {[...Array(Math.min(level, 3))].map((_, i) => (
        <Flame key={i} className={`w-3 h-3 ${heat.color}`} />
      ))}
      {showLabel && <span className={`text-[10px] font-bold ${heat.color}`}>{heat.label}</span>}
    </div>
  );
});
HeatIndicator.displayName = 'HeatIndicator';

// Stat Badge - Prop type display
export const StatBadge = memo(({ stat, line, direction = 'Over', isDemon, isGoblin }) => {
  const bgColor = isDemon ? 'bg-amber-500/20 border-amber-500/30' : 
                  isGoblin ? 'bg-emerald-500/20 border-emerald-500/30' : 
                  'bg-zinc-700/50 border-zinc-600/30';
  const textColor = isDemon ? 'text-amber-400' : isGoblin ? 'text-emerald-400' : 'text-zinc-300';
  
  return (
    <div className={`px-2 py-1 rounded border ${bgColor}`}>
      <span className={`text-xs font-bold ${textColor}`}>
        {formatStatType(stat)} {direction} {line}
      </span>
    </div>
  );
});
StatBadge.displayName = 'StatBadge';

// Hit Rate Display
export const HitRateDisplay = memo(({ l10, l5, size = 'md' }) => {
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs';
  const getColor = (rate) => rate >= 80 ? 'text-emerald-400' : rate >= 60 ? 'text-yellow-400' : 'text-zinc-400';
  
  return (
    <div className="flex gap-2">
      <span className={`${textSize} ${getColor(l10)}`}>L10: {formatPercentage(l10)}</span>
      <span className={`${textSize} ${getColor(l5)}`}>L5: {formatPercentage(l5)}</span>
    </div>
  );
});
HitRateDisplay.displayName = 'HitRateDisplay';

// Locked Badge
export const LockedBadge = memo(({ isLocked }) => {
  if (!isLocked) return null;
  
  return (
    <div className="absolute inset-0 bg-zinc-950/80 backdrop-blur-sm flex items-center justify-center z-10 rounded-lg">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-red-500/20 border border-red-500/30 rounded">
        <Lock className="w-4 h-4 text-red-400" />
        <span className="text-xs font-bold text-red-400">LOCKED</span>
      </div>
    </div>
  );
});
LockedBadge.displayName = 'LockedBadge';

// Scouting Badge - Orange themed for projections
export const ScoutingBadge = memo(({ isProjection }) => {
  if (!isProjection) return null;
  
  return (
    <div 
      className="px-2 py-1 rounded-md flex items-center gap-1.5"
      style={{ background: 'rgba(255, 165, 0, 0.2)', border: '1px solid #FFA500', color: '#FFA500' }}
    >
      <Eye className="w-3 h-3" />
      <span className="text-[10px] font-bold uppercase">SCOUTING</span>
    </div>
  );
});
ScoutingBadge.displayName = 'ScoutingBadge';

// Payout Display
export const PayoutDisplay = memo(({ multiplier, size = 'md' }) => {
  const textSize = size === 'sm' ? 'text-sm' : size === 'lg' ? 'text-xl' : 'text-base';
  
  return (
    <div className="flex items-center gap-1">
      <span className={`${textSize} font-bold text-emerald-400`}>
        {formatPayout(multiplier)}
      </span>
    </div>
  );
});
PayoutDisplay.displayName = 'PayoutDisplay';

// Vision Text - AI insight display
export const VisionText = memo(({ text, maxLines = 3 }) => {
  if (!text) return null;
  
  return (
    <div className="bg-zinc-900/50 rounded p-2 border border-zinc-700/30">
      <div className="flex items-center gap-1 mb-1">
        <Target className="w-3 h-3 text-amber-400" />
        <span className="text-[10px] text-amber-400 font-bold">THE VISION</span>
      </div>
      <p className="text-xs text-zinc-300 leading-relaxed">{text}</p>
    </div>
  );
});
VisionText.displayName = 'VisionText';

// Section Header
export const SectionHeader = memo(({ icon: Icon, title, subtitle, badge, badgeColor = 'amber' }) => {
  const colors = {
    amber: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    emerald: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    blue: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    orange: 'bg-orange-500/20 text-orange-400 border-orange-500/30'
  };
  
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        {Icon && (
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${colors[badgeColor]}`}>
            <Icon className="w-4 h-4" />
          </div>
        )}
        <div>
          <h3 className="text-sm font-bold text-white">{title}</h3>
          {subtitle && <p className="text-[10px] text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {badge && (
        <span className={`px-2 py-1 rounded text-[10px] font-bold border ${colors[badgeColor]}`}>
          {badge}
        </span>
      )}
    </div>
  );
});
SectionHeader.displayName = 'SectionHeader';

// Empty State
export const EmptyState = memo(({ message = 'No data available' }) => (
  <div className="flex flex-col items-center justify-center py-8 text-zinc-500">
    <AlertTriangle className="w-8 h-8 mb-2 opacity-50" />
    <span className="text-sm">{message}</span>
  </div>
));
EmptyState.displayName = 'EmptyState';

// Loading Spinner
export const LoadingSpinner = memo(({ size = 'md' }) => {
  const sizes = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-8 h-8' };
  return (
    <div className={`${sizes[size]} border-2 border-zinc-600 border-t-amber-400 rounded-full animate-spin`} />
  );
});
LoadingSpinner.displayName = 'LoadingSpinner';

export default {
  // Utilities
  STAT_LABELS, HEAT_LEVELS, TEAM_COLORS,
  formatStatType, getHeatLevel, getTeamColor, formatPayout, formatPercentage,
  // Hooks
  useDataFetch,
  // Components
  PlayerPhoto, HeatIndicator, StatBadge, HitRateDisplay,
  LockedBadge, ScoutingBadge, PayoutDisplay, VisionText,
  SectionHeader, EmptyState, LoadingSpinner
};
