/**
 * TACTICAL PROFILE COMPONENT
 * ===========================
 * Displays player's tactical data including:
 * - Active prop lines with DvP friction
 * - Usage Ripple status
 * - Volatility indicators
 */

import React, { memo } from 'react';
import { Shield, TrendingUp, AlertTriangle, Zap, Target } from 'lucide-react';

// Friction badge colors
const FRICTION_COLORS = {
  green: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
  yellow: 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  red: 'bg-red-500/20 text-red-400 border-red-500/40'
};

// DvP Friction Badge
const FrictionBadge = memo(({ rank, color }) => (
  <div 
    className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border ${FRICTION_COLORS[color] || FRICTION_COLORS.yellow}`}
    title={`Defense vs Position Rank: #${rank}`}
  >
    <Shield className="w-2.5 h-2.5" />
    <span>#{rank}</span>
  </div>
));

// Volatility Badge
const VolatilityBadge = memo(({ hasVolatility, hasRevenge }) => {
  if (!hasVolatility && !hasRevenge) return null;
  
  return (
    <div className="flex items-center gap-1">
      {hasRevenge && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/20 text-purple-400 border border-purple-500/40">
          REVENGE
        </span>
      )}
      {hasVolatility && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-orange-500/20 text-orange-400 border border-orange-500/40 flex items-center gap-0.5">
          <AlertTriangle className="w-2.5 h-2.5" />
          VOLATILE
        </span>
      )}
    </div>
  );
});

// Usage Ripple Badge
const UsageRippleBadge = memo(({ ripple }) => {
  if (!ripple?.active) return null;
  
  return (
    <div 
      className="flex items-center gap-1 px-2 py-1 rounded bg-cyan-500/20 border border-cyan-500/40"
      title={ripple.reason}
    >
      <Zap className="w-3 h-3 text-cyan-400" />
      <span className="text-[11px] text-cyan-400 font-medium">
        +{ripple.bump_percent?.toFixed(1)}% Usage Ripple
      </span>
    </div>
  );
});

// Single Prop Line Row
const PropLineRow = memo(({ line, onSelect }) => (
  <div 
    onClick={() => onSelect?.(line)}
    className="flex items-center justify-between p-2 rounded bg-zinc-800/50 hover:bg-zinc-700/50 cursor-pointer transition-colors border border-zinc-700/50"
    data-testid={`prop-line-${line.stat_type}`}
  >
    <div className="flex items-center gap-2">
      <Target className="w-3.5 h-3.5 text-zinc-500" />
      <div>
        <span className="text-sm font-medium text-white">
          {line.stat_type}
        </span>
        <span className="text-xs text-zinc-400 ml-2">
          {line.direction?.toUpperCase()} {line.line}
        </span>
      </div>
    </div>
    
    <div className="flex items-center gap-2">
      <FrictionBadge rank={line.dvp_rank} color={line.dvp_rank_color} />
      <span className="text-[10px] text-zinc-500 font-mono">
        {line.friction_level?.split(' ')[0]}
      </span>
    </div>
  </div>
));

// Main Tactical Profile Component
const TacticalProfile = memo(({ 
  profile, 
  onSelectLine,
  onClose 
}) => {
  if (!profile) return null;

  const { 
    player_name, 
    team, 
    position, 
    photo_url, 
    opponent,
    active_lines = [],
    usage_ripple = {},
    volatility = {},
    season_avg = {},
    l10_stats = {}
  } = profile;

  return (
    <div 
      className="bg-zinc-900 border border-zinc-700 rounded-lg overflow-hidden"
      data-testid="tactical-profile"
    >
      {/* Header */}
      <div className="p-3 border-b border-zinc-800 bg-gradient-to-r from-zinc-900 to-zinc-800">
        <div className="flex items-center gap-3">
          {/* Photo */}
          <div className="w-12 h-12 rounded-full bg-zinc-700 overflow-hidden flex-shrink-0">
            {photo_url ? (
              <img 
                src={photo_url} 
                alt={player_name}
                className="w-full h-full object-cover"
                onError={(e) => e.target.style.display = 'none'}
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-zinc-500 text-lg font-bold">
                {player_name?.charAt(0)}
              </div>
            )}
          </div>
          
          {/* Info */}
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-bold text-white truncate">{player_name}</h3>
            <div className="flex items-center gap-2 text-[11px] text-zinc-400">
              <span className="font-mono">{team}</span>
              {position && <span>· {position}</span>}
              {opponent && <span className="text-zinc-500">vs {opponent}</span>}
            </div>
          </div>
          
          {/* Close button */}
          {onClose && (
            <button 
              onClick={onClose}
              className="text-zinc-500 hover:text-white p-1"
            >
              ×
            </button>
          )}
        </div>
        
        {/* Badges row */}
        <div className="flex items-center gap-2 mt-2">
          <UsageRippleBadge ripple={usage_ripple} />
          <VolatilityBadge 
            hasVolatility={volatility?.flag} 
            hasRevenge={volatility?.revenge_game} 
          />
        </div>
      </div>

      {/* Stats Summary */}
      <div className="px-3 py-2 border-b border-zinc-800 bg-zinc-800/30">
        <div className="flex items-center justify-between text-[10px]">
          <div className="flex items-center gap-3">
            {season_avg?.pts !== undefined && (
              <span className="text-zinc-400">
                PPG: <span className="text-white font-medium">{season_avg.pts?.toFixed(1)}</span>
              </span>
            )}
            {season_avg?.reb !== undefined && (
              <span className="text-zinc-400">
                RPG: <span className="text-white font-medium">{season_avg.reb?.toFixed(1)}</span>
              </span>
            )}
            {season_avg?.ast !== undefined && (
              <span className="text-zinc-400">
                APG: <span className="text-white font-medium">{season_avg.ast?.toFixed(1)}</span>
              </span>
            )}
          </div>
          <span className="text-zinc-500">L10</span>
        </div>
      </div>

      {/* Active Prop Lines */}
      <div className="p-3">
        <h4 className="text-[11px] font-medium text-zinc-400 uppercase tracking-wide mb-2">
          Active Lines ({active_lines.length})
        </h4>
        
        {active_lines.length > 0 ? (
          <div className="space-y-1.5">
            {active_lines.map((line, idx) => (
              <PropLineRow 
                key={`${line.stat_type}-${idx}`}
                line={line}
                onSelect={onSelectLine}
              />
            ))}
          </div>
        ) : (
          <p className="text-xs text-zinc-500 text-center py-4">
            No active prop lines for today
          </p>
        )}
      </div>
    </div>
  );
});

TacticalProfile.displayName = 'TacticalProfile';
FrictionBadge.displayName = 'FrictionBadge';
VolatilityBadge.displayName = 'VolatilityBadge';
UsageRippleBadge.displayName = 'UsageRippleBadge';
PropLineRow.displayName = 'PropLineRow';

export default TacticalProfile;
export { FrictionBadge, VolatilityBadge, UsageRippleBadge };
