/**
 * MomentumTracker.jsx
 * ====================
 * Visual component showing defensive momentum for a matchup.
 * 
 * Displays:
 * - 3 vertical bars (Season, L10, L5 ranks)
 * - Composite rank with color coding
 * - Momentum arrow (green = improving, red = regressing)
 * - Trend alert when significant divergence detected
 * - Tooltip showing the weighted formula
 */

import React, { memo, useState } from 'react';
import { TrendingUp, TrendingDown, Minus, AlertTriangle, Info, Shield } from 'lucide-react';

// Color coding based on rank
const getRankColor = (rank) => {
  if (rank <= 5) return 'bg-red-500';     // Elite defense (hard matchup)
  if (rank <= 10) return 'bg-orange-500'; // Good defense
  if (rank <= 20) return 'bg-yellow-500'; // Average
  if (rank <= 25) return 'bg-green-400';  // Below average
  return 'bg-green-500';                   // Weak defense (easy matchup)
};

const getRankTextColor = (rank) => {
  if (rank <= 5) return 'text-red-400';
  if (rank <= 10) return 'text-orange-400';
  if (rank <= 20) return 'text-yellow-400';
  if (rank <= 25) return 'text-green-400';
  return 'text-green-500';
};

// Calculate bar height percentage (rank 1 = 100%, rank 30 = 10%)
const getRankBarHeight = (rank) => {
  // Invert: lower rank = taller bar (better defense)
  const height = Math.max(10, 100 - ((rank - 1) / 29) * 90);
  return `${height}%`;
};

// Momentum indicator
const MomentumIndicator = memo(({ momentum, size = 'md' }) => {
  const sizeClasses = {
    sm: 'w-3 h-3',
    md: 'w-4 h-4',
    lg: 'w-5 h-5'
  };

  if (momentum === 'improving') {
    return <TrendingUp className={`${sizeClasses[size]} text-green-400`} />;
  }
  if (momentum === 'regressing') {
    return <TrendingDown className={`${sizeClasses[size]} text-red-400`} />;
  }
  return <Minus className={`${sizeClasses[size]} text-zinc-500`} />;
});
MomentumIndicator.displayName = 'MomentumIndicator';

// Single rank bar
const RankBar = memo(({ label, rank, isActive = false }) => {
  const height = getRankBarHeight(rank);
  const color = getRankColor(rank);
  
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="text-[9px] text-white font-medium uppercase">{label}</div>
      <div className="w-4 h-12 bg-zinc-800 rounded-full overflow-hidden relative">
        <div 
          className={`absolute bottom-0 w-full ${color} rounded-full transition-all duration-500`}
          style={{ height }}
        />
      </div>
      <div className={`text-[10px] font-bold ${getRankTextColor(rank)}`}>
        {rank}
      </div>
    </div>
  );
});
RankBar.displayName = 'RankBar';

// Compact mode for player cards
const MomentumTrackerCompact = memo(({ 
  momentumData, 
  opponent, 
  statType,
  onClick 
}) => {
  if (!momentumData) return null;

  const {
    composite_rank,
    momentum,
    trend_alert,
    is_elite,
    is_weak,
    using_proxy,
    proxy_label
  } = momentumData;

  const compositeInt = Math.round(composite_rank);
  
  return (
    <div 
      className={`mt-1.5 rounded-lg overflow-hidden cursor-pointer hover:opacity-90 transition-opacity ${
        is_elite 
          ? 'bg-gradient-to-r from-red-500/10 to-red-600/5 border border-red-500/30' 
          : is_weak
            ? 'bg-gradient-to-r from-green-500/10 to-green-600/5 border border-green-500/30'
            : 'bg-zinc-800/50 border border-zinc-700/50'
      }`}
      onClick={onClick}
      data-testid="momentum-tracker-compact"
    >
      {/* Header */}
      <div className={`flex items-center gap-1.5 px-2 py-1 text-[9px] font-bold ${
        is_elite ? 'text-red-400' : is_weak ? 'text-green-400' : 'text-zinc-500'
      }`}>
        <Shield className="w-3 h-3" />
        DEFENSIVE MOMENTUM
        {using_proxy && (
          <span className="text-amber-400/70 font-normal">({proxy_label})</span>
        )}
        <MomentumIndicator momentum={momentum} size="sm" />
        <Info className="w-2.5 h-2.5 ml-auto opacity-60" />
      </div>
      
      {/* Content */}
      <div className="px-2 pb-1.5">
        <div className="flex items-center justify-between text-[9px]">
          <span className="text-zinc-400">
            vs <span className="text-zinc-300">{opponent}</span>
          </span>
          <span className={`font-bold ${getRankTextColor(compositeInt)}`}>
            #{compositeInt} {is_elite ? '(Elite)' : is_weak ? '(Weak)' : ''}
          </span>
        </div>
        
        {/* Trend Alert */}
        {trend_alert && (
          <div className="flex items-center gap-1 mt-1">
            <AlertTriangle className={`w-3 h-3 ${
              momentum === 'improving' ? 'text-green-400' : 'text-amber-400'
            }`} />
            <span className={`text-[8px] ${
              momentum === 'improving' ? 'text-green-300' : 'text-amber-300'
            }`}>
              {trend_alert}
            </span>
          </div>
        )}
      </div>
    </div>
  );
});
MomentumTrackerCompact.displayName = 'MomentumTrackerCompact';

// Full mode for Vision Intel modal
const MomentumTrackerFull = memo(({ 
  momentumData, 
  opponent, 
  statType 
}) => {
  const [showTooltip, setShowTooltip] = useState(false);
  
  if (!momentumData) {
    // No current-source momentum data available for this pick.
    // Render nothing — `PlayerDetailPage` shows a separate "MATCHUP
    // ANALYSIS" box driven by `intel_suite.matchup_dvp` in that case.
    return null;
  }

  const {
    season_rank,
    l10_rank,
    l5_rank,
    composite_rank,
    momentum,
    trend_alert,
    is_elite,
    is_weak,
    tooltip,
    modifier
  } = momentumData;

  const compositeInt = Math.round(composite_rank);
  
  return (
    <div 
      className={`rounded-lg overflow-hidden ${
        is_elite 
          ? 'bg-gradient-to-br from-red-950/40 to-zinc-900 border border-red-500/30' 
          : is_weak
            ? 'bg-gradient-to-br from-green-950/40 to-zinc-900 border border-green-500/30'
            : 'bg-zinc-800/50 border border-zinc-700/50'
      }`}
      data-testid="momentum-tracker-full"
    >
      {/* Header */}
      <div className={`flex items-center justify-between px-3 py-2 border-b ${
        is_elite ? 'border-red-500/30' : is_weak ? 'border-green-500/30' : 'border-zinc-700/50'
      }`}>
        <div className="flex items-center gap-2">
          <Shield className={`w-4 h-4 ${
            is_elite ? 'text-red-400' : is_weak ? 'text-green-400' : 'text-zinc-400'
          }`} />
          <span className={`text-xs font-bold uppercase tracking-wide ${
            is_elite ? 'text-red-400' : is_weak ? 'text-green-400' : 'text-zinc-400'
          }`}>
            Defensive Momentum
          </span>
        </div>
        <MomentumIndicator momentum={momentum} size="md" />
      </div>
      
      {/* Content */}
      <div className="p-3">
        {/* Rank Bars */}
        <div className="flex items-end justify-center gap-6 mb-3">
          <RankBar label="SZN" rank={season_rank} />
          <RankBar label="L10" rank={l10_rank} />
          <RankBar label="L5" rank={l5_rank} />
        </div>
        
        {/* Composite Rank */}
        <div className="text-center mb-2">
          <div className="text-[10px] text-zinc-500 uppercase mb-1">Composite Rank</div>
          <div className={`text-2xl font-bold ${getRankTextColor(compositeInt)}`}>
            #{compositeInt}
          </div>
          <div className={`text-xs ${
            is_elite ? 'text-red-400' : is_weak ? 'text-green-400' : 'text-zinc-400'
          }`}>
            {is_elite ? 'Elite Defense - Difficult Matchup' : 
             is_weak ? 'Weak Defense - Favorable Matchup' : 
             'Average Defense'}
          </div>
        </div>
        
        {/* Tooltip Math */}
        <div 
          className="relative"
          onMouseEnter={() => setShowTooltip(true)}
          onMouseLeave={() => setShowTooltip(false)}
        >
          <div className="flex items-center justify-center gap-1 text-[9px] text-zinc-500 cursor-help">
            <Info className="w-3 h-3" />
            <span>See formula</span>
          </div>
          
          {showTooltip && tooltip && (
            <div className="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-10 max-w-xs">
              <div className="text-[10px] text-cyan-400 font-mono whitespace-nowrap">
                {tooltip.split(' | ').slice(0, 3).join(' | ')}
              </div>
              {/* Show proxy note if present */}
              {momentumData.using_proxy && momentumData.proxy_description && (
                <div className="mt-1 pt-1 border-t border-zinc-700">
                  <div className="text-[9px] text-amber-400">
                    {momentumData.proxy_description}
                  </div>
                </div>
              )}
              <div className="absolute bottom-0 left-1/2 transform -translate-x-1/2 translate-y-1/2 rotate-45 w-2 h-2 bg-zinc-900 border-r border-b border-zinc-700" />
            </div>
          )}
        </div>
        
        {/* Trend Alert */}
        {trend_alert && (
          <div className={`mt-2 flex items-start gap-2 px-2 py-1.5 rounded-lg ${
            momentum === 'improving' 
              ? 'bg-green-500/10 border border-green-500/30' 
              : 'bg-amber-500/10 border border-amber-500/30'
          }`}>
            <AlertTriangle className={`w-4 h-4 flex-shrink-0 mt-0.5 ${
              momentum === 'improving' ? 'text-green-400' : 'text-amber-400'
            }`} />
            <span className={`text-xs ${
              momentum === 'improving' ? 'text-green-300' : 'text-amber-300'
            }`}>
              {trend_alert}
            </span>
          </div>
        )}
      </div>
    </div>
  );
});
MomentumTrackerFull.displayName = 'MomentumTrackerFull';

// Main export component
const MomentumTracker = memo(({ 
  momentumData, 
  opponent, 
  statType,
  mode = 'compact',  // 'compact' | 'full'
  onClick
}) => {
  if (mode === 'full') {
    return (
      <MomentumTrackerFull 
        momentumData={momentumData} 
        opponent={opponent} 
        statType={statType} 
      />
    );
  }
  
  return (
    <MomentumTrackerCompact 
      momentumData={momentumData} 
      opponent={opponent} 
      statType={statType}
      onClick={onClick}
    />
  );
});

MomentumTracker.displayName = 'MomentumTracker';

export { MomentumTracker, MomentumTrackerCompact, MomentumTrackerFull, MomentumIndicator };
export default MomentumTracker;
