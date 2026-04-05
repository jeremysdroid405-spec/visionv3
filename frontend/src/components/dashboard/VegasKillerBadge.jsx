/**
 * VegasKillerBadge.jsx
 * ====================
 * Displays Vegas Killer ML prediction as an intel badge.
 * Shows predicted value, edge %, and confidence direction.
 */

import React from 'react';
import { Brain, TrendingUp, TrendingDown, Minus } from 'lucide-react';

// Edge color based on magnitude
const getEdgeColor = (edge) => {
  const absEdge = Math.abs(edge);
  if (absEdge >= 20) return edge > 0 ? 'text-green-400' : 'text-red-400';
  if (absEdge >= 10) return edge > 0 ? 'text-green-300' : 'text-red-300';
  if (absEdge >= 5) return edge > 0 ? 'text-lime-400' : 'text-orange-400';
  return 'text-zinc-400';
};

// Recommendation to styling
const getRecommendationStyle = (rec) => {
  const styles = {
    'STRONG_OVER': { 
      bg: 'bg-gradient-to-r from-green-500/30 to-emerald-500/20', 
      border: 'border-green-500/50', 
      text: 'text-green-400',
      icon: TrendingUp,
      label: 'OVER'
    },
    'LEAN_OVER': { 
      bg: 'bg-green-500/20', 
      border: 'border-green-500/30', 
      text: 'text-green-300',
      icon: TrendingUp,
      label: 'over'
    },
    'STRONG_UNDER': { 
      bg: 'bg-gradient-to-r from-red-500/30 to-rose-500/20', 
      border: 'border-red-500/50', 
      text: 'text-red-400',
      icon: TrendingDown,
      label: 'UNDER'
    },
    'LEAN_UNDER': { 
      bg: 'bg-red-500/20', 
      border: 'border-red-500/30', 
      text: 'text-red-300',
      icon: TrendingDown,
      label: 'under'
    },
    'NEUTRAL': { 
      bg: 'bg-zinc-500/20', 
      border: 'border-zinc-500/30', 
      text: 'text-zinc-400',
      icon: Minus,
      label: 'hold'
    },
  };
  return styles[rec] || styles['NEUTRAL'];
};

/**
 * Compact VK badge for prop rows
 */
export const VKBadgeCompact = ({ predicted, edge, recommendation, probOver, probUnder, onClick }) => {
  const style = getRecommendationStyle(recommendation);
  const Icon = style.icon;
  const confidence = Math.max(probOver, probUnder);
  const isStrong = recommendation?.includes('STRONG');
  
  return (
    <button
      onClick={onClick}
      className={`
        flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium
        ${style.bg} border ${style.border} ${style.text}
        hover:scale-105 transition-transform cursor-pointer
      `}
      title={`VK: ${predicted?.toFixed(1)} (${edge > 0 ? '+' : ''}${edge?.toFixed(1)} edge)`}
    >
      <Brain className="w-3 h-3" />
      <span>{predicted?.toFixed(1)}</span>
      <Icon className="w-2.5 h-2.5" />
      {isStrong && <span className="text-[8px]">{confidence?.toFixed(0)}%</span>}
    </button>
  );
};

/**
 * Full VK badge with details
 */
export const VKBadgeFull = ({ 
  predicted, 
  edge, 
  edgePct,
  recommendation, 
  probOver, 
  probUnder,
  l5Avg,
  usageRate,
  dataSource,
  isTrap,
  trapReason,
}) => {
  const style = getRecommendationStyle(recommendation);
  const Icon = style.icon;
  const confidence = Math.max(probOver || 50, probUnder || 50);
  
  return (
    <div className={`
      rounded-lg p-3 border ${style.border} ${style.bg}
      ${isTrap ? 'ring-2 ring-amber-500/50' : ''}
    `}>
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Brain className={`w-4 h-4 ${style.text}`} />
          <span className="text-xs font-bold text-white">Vegas Killer</span>
          {dataSource === 'V2_ADVANCED' && (
            <span className="text-[8px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
              V2
            </span>
          )}
        </div>
        <div className={`flex items-center gap-1 ${style.text} font-bold`}>
          <Icon className="w-4 h-4" />
          <span className="text-sm">{style.label}</span>
        </div>
      </div>
      
      {/* Prediction */}
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-2xl font-bold text-white">{predicted?.toFixed(1)}</span>
        <span className={`text-sm font-medium ${getEdgeColor(edge)}`}>
          {edge > 0 ? '+' : ''}{edge?.toFixed(1)} ({edgePct > 0 ? '+' : ''}{edgePct?.toFixed(0)}%)
        </span>
      </div>
      
      {/* Confidence bar */}
      <div className="mb-2">
        <div className="flex justify-between text-[10px] text-zinc-500 mb-1">
          <span>Under {probUnder?.toFixed(0)}%</span>
          <span>Over {probOver?.toFixed(0)}%</span>
        </div>
        <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden flex">
          <div 
            className="h-full bg-red-500 transition-all" 
            style={{ width: `${probUnder}%` }}
          />
          <div 
            className="h-full bg-green-500 transition-all" 
            style={{ width: `${probOver}%` }}
          />
        </div>
      </div>
      
      {/* Stats */}
      <div className="flex justify-between text-[10px] text-zinc-400">
        {l5Avg && <span>L5 Avg: <span className="text-white">{l5Avg}</span></span>}
        {usageRate && <span>USG: <span className="text-cyan-400">{(usageRate * 100).toFixed(0)}%</span></span>}
        <span>Conf: <span className={style.text}>{confidence.toFixed(0)}%</span></span>
      </div>
      
      {/* Trap Alert */}
      {isTrap && (
        <div className="mt-2 px-2 py-1.5 rounded bg-amber-500/10 border border-amber-500/30">
          <div className="flex items-center gap-1 text-amber-400 text-[10px]">
            <span>⚠️</span>
            <span className="font-medium">TRAP ALERT</span>
          </div>
          <p className="text-[9px] text-amber-300/80 mt-0.5">{trapReason}</p>
        </div>
      )}
    </div>
  );
};

/**
 * VK Intel Modal Content
 */
export const VKIntelContent = ({ 
  playerName, 
  statType, 
  line, 
  vkData,
  onClose 
}) => {
  if (!vkData) return null;
  
  const {
    predicted,
    edge,
    prob_over,
    prob_under,
    recommendation,
    features,
    v2_advanced_stats,
    data_source,
  } = vkData;
  
  const style = getRecommendationStyle(recommendation);
  const edgePct = line ? ((predicted - line) / line * 100) : 0;
  
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-cyan-500/20">
          <Brain className="w-6 h-6 text-cyan-400" />
        </div>
        <div>
          <h3 className="text-white font-bold">Vegas Killer Prediction</h3>
          <p className="text-zinc-500 text-xs">ML-powered projection using V2 process stats</p>
        </div>
      </div>
      
      {/* Main prediction */}
      <div className={`rounded-lg p-4 ${style.bg} border ${style.border}`}>
        <div className="flex items-center justify-between mb-3">
          <span className="text-zinc-400 text-sm">{playerName} • {statType}</span>
          <span className="text-zinc-500 text-sm">Line: {line}</span>
        </div>
        
        <div className="flex items-baseline gap-3 mb-3">
          <span className="text-4xl font-bold text-white">{predicted?.toFixed(1)}</span>
          <div className="flex flex-col">
            <span className={`text-lg font-medium ${getEdgeColor(edge)}`}>
              {edge > 0 ? '+' : ''}{edge?.toFixed(1)} pts
            </span>
            <span className={`text-xs ${getEdgeColor(edgePct)}`}>
              {edgePct > 0 ? '+' : ''}{edgePct.toFixed(1)}% edge
            </span>
          </div>
        </div>
        
        {/* Direction */}
        <div className={`flex items-center gap-2 ${style.text} text-lg font-bold`}>
          {React.createElement(style.icon, { className: 'w-5 h-5' })}
          <span>{recommendation?.replace('_', ' ')}</span>
          <span className="text-sm font-normal">
            ({Math.max(prob_over, prob_under)?.toFixed(0)}% confidence)
          </span>
        </div>
      </div>
      
      {/* Features used */}
      {features && (
        <div className="bg-zinc-800/50 rounded-lg p-3">
          <div className="text-xs text-zinc-500 uppercase tracking-wide mb-2">Key Inputs</div>
          <div className="grid grid-cols-3 gap-2 text-sm">
            <div>
              <span className="text-zinc-500">L5 Avg</span>
              <div className="text-white font-medium">{features.l5_avg}</div>
            </div>
            <div>
              <span className="text-zinc-500">L10 Avg</span>
              <div className="text-white font-medium">{features.l10_avg}</div>
            </div>
            <div>
              <span className="text-zinc-500">Minutes</span>
              <div className="text-white font-medium">{features.minutes}</div>
            </div>
          </div>
        </div>
      )}
      
      {/* V2 Advanced Stats */}
      {v2_advanced_stats && data_source === 'V2_ADVANCED' && (
        <div className="bg-cyan-950/30 border border-cyan-500/20 rounded-lg p-3">
          <div className="flex items-center gap-2 text-xs text-cyan-400 uppercase tracking-wide mb-2">
            <span>V2 Advanced Stats</span>
            <span className="px-1.5 py-0.5 rounded bg-cyan-500/20 text-[10px]">REAL DATA</span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-sm">
            {v2_advanced_stats.usage_rate && (
              <div>
                <span className="text-zinc-500">USG%</span>
                <div className="text-cyan-400 font-medium">
                  {(v2_advanced_stats.usage_rate * 100).toFixed(1)}%
                </div>
              </div>
            )}
            {v2_advanced_stats.true_shooting && (
              <div>
                <span className="text-zinc-500">TS%</span>
                <div className="text-cyan-400 font-medium">
                  {(v2_advanced_stats.true_shooting * 100).toFixed(1)}%
                </div>
              </div>
            )}
            {v2_advanced_stats.pace && (
              <div>
                <span className="text-zinc-500">Pace</span>
                <div className="text-cyan-400 font-medium">
                  {v2_advanced_stats.pace?.toFixed(1)}
                </div>
              </div>
            )}
            {v2_advanced_stats.touches && (
              <div>
                <span className="text-zinc-500">Touches</span>
                <div className="text-cyan-400 font-medium">
                  {v2_advanced_stats.touches?.toFixed(0)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      
      {/* Backtest info */}
      <div className="bg-zinc-800/30 rounded-lg p-3 border border-zinc-700/30">
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <span>Model validated on 21,584 real Vegas lines</span>
          <span>•</span>
          <span className="text-green-400">58.7% win rate</span>
          <span>•</span>
          <span className="text-green-400">+12% ROI</span>
        </div>
      </div>
    </div>
  );
};

export default VKBadgeCompact;
