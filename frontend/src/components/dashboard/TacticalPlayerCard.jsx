/**
 * TACTICAL PLAYER CARD COMPONENT
 * ===============================
 * PropVision Command Post - Military-style nested player card
 * 
 * Features:
 * - Double-nested interactive interface
 * - Prop Arsenal with Standard vs Radar distinction
 * - Stability Index (variance-based scoring)
 * - Full Intelligence Suite for Radar picks
 * 
 * Terminology:
 * - Infiltration Grade: Risk assessment (S/A/B/C/D)
 * - Stability Index: 1-100 score based on variance
 * - Objectives: Picks/props
 * - Defensive Friction: DvP-based resistance
 */

import React, { useState, memo, useCallback } from 'react';
import { 
  Target, ChevronDown, ChevronRight, Shield, Zap, TrendingUp, 
  AlertTriangle, Crosshair, Radio, Plus, Lock
} from 'lucide-react';

// ==================== CONSTANTS ====================

const STABILITY_LEVELS = {
  HIGH: { min: 80, label: 'HIGH STABILITY', color: 'text-emerald-400', bg: 'bg-emerald-500/20', border: 'border-emerald-500/40' },
  MEDIUM: { min: 50, label: 'MODERATE', color: 'text-amber-400', bg: 'bg-amber-500/20', border: 'border-amber-500/40' },
  VOLATILE: { min: 0, label: 'VOLATILE', color: 'text-red-400', bg: 'bg-red-500/20', border: 'border-red-500/40' }
};

const FRICTION_COLORS = {
  green: { color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' },
  yellow: { color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30' },
  red: { color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30' }
};

// ==================== HELPER FUNCTIONS ====================

/**
 * Calculate Stability Index from standard deviation
 * High Stability (80-100): Low variance, consistent
 * Moderate (50-79): Average variance
 * Volatile (0-49): High variance, boom-or-bust
 */
const calculateStabilityIndex = (stdDev, avg) => {
  if (!stdDev || !avg || avg === 0) return { index: 50, level: STABILITY_LEVELS.MEDIUM };
  
  // Coefficient of variation (lower = more stable)
  const cv = (stdDev / avg) * 100;
  
  // Convert to stability index (inverse relationship)
  // CV of 0 = 100 stability, CV of 50+ = low stability
  let index = Math.max(0, Math.min(100, 100 - (cv * 2)));
  
  const level = index >= 80 ? STABILITY_LEVELS.HIGH :
                index >= 50 ? STABILITY_LEVELS.MEDIUM :
                STABILITY_LEVELS.VOLATILE;
  
  return { index: Math.round(index), level };
};

// ==================== SUB-COMPONENTS ====================

// Stability Index Badge
const StabilityBadge = memo(({ index, level }) => (
  <div 
    className={`flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-bold ${level.bg} ${level.border} border`}
    title={`Stability Index: ${index} - ${level.label}`}
  >
    <div className={`w-2 h-2 rounded-full ${index >= 80 ? 'bg-emerald-400' : index >= 50 ? 'bg-amber-400' : 'bg-red-400'}`} />
    <span className={level.color}>{index}</span>
    <span className="text-zinc-400">SI</span>
  </div>
));

// Defensive Friction Badge
const FrictionBadge = memo(({ rank, color = 'yellow' }) => {
  const style = FRICTION_COLORS[color] || FRICTION_COLORS.yellow;
  return (
    <div 
      className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${style.bg} ${style.border} border`}
      title={`Defensive Friction Rank: #${rank}`}
    >
      <Shield className="w-3 h-3" />
      <span className={style.color}>#{rank}</span>
    </div>
  );
});

// Target Lock Icon (for Radar picks)
const TargetLockIcon = memo(({ isLocked }) => (
  <div className={`relative ${isLocked ? 'animate-pulse' : ''}`}>
    <Crosshair className={`w-5 h-5 ${isLocked ? 'text-emerald-400' : 'text-zinc-500'}`} />
    {isLocked && (
      <div className="absolute inset-0 rounded-full border-2 border-emerald-400 animate-ping" />
    )}
  </div>
));

// Standard Prop Stats (L5, L10, Season only)
const StandardPropStats = memo(({ l5Avg, l10Avg, seasonAvg }) => (
  <div className="grid grid-cols-3 gap-2 p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
    <div className="text-center">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider">L5 Avg</div>
      <div className="text-lg font-bold text-white">{l5Avg?.toFixed(1) || '---'}</div>
    </div>
    <div className="text-center border-x border-zinc-700">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider">L10 Avg</div>
      <div className="text-lg font-bold text-white">{l10Avg?.toFixed(1) || '---'}</div>
    </div>
    <div className="text-center">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider">Season</div>
      <div className="text-lg font-bold text-white">{seasonAvg?.toFixed(1) || '---'}</div>
    </div>
  </div>
));

// Full Intelligence Suite (for Radar picks)
const IntelligenceSuite = memo(({ prop }) => {
  const stability = calculateStabilityIndex(prop.std_dev, prop.season_avg);
  
  return (
    <div className="space-y-3 p-3 bg-gradient-to-b from-emerald-950/30 to-zinc-900 rounded-lg border border-emerald-500/30">
      {/* Header */}
      <div className="flex items-center gap-2 pb-2 border-b border-emerald-500/20">
        <Radio className="w-4 h-4 text-emerald-400 animate-pulse" />
        <span className="text-[11px] font-bold text-emerald-400 uppercase tracking-wider">
          Full Intel Suite
        </span>
      </div>
      
      {/* Averages Row */}
      <div className="grid grid-cols-3 gap-2">
        <div className="text-center p-2 bg-zinc-800/50 rounded">
          <div className="text-[9px] text-zinc-500 uppercase">L5 Avg</div>
          <div className="text-base font-bold text-white">{prop.l5_avg?.toFixed(1) || '---'}</div>
        </div>
        <div className="text-center p-2 bg-zinc-800/50 rounded">
          <div className="text-[9px] text-zinc-500 uppercase">L10 Avg</div>
          <div className="text-base font-bold text-white">{prop.l10_avg?.toFixed(1) || '---'}</div>
        </div>
        <div className="text-center p-2 bg-zinc-800/50 rounded">
          <div className="text-[9px] text-zinc-500 uppercase">Season</div>
          <div className="text-base font-bold text-white">{prop.season_avg?.toFixed(1) || '---'}</div>
        </div>
      </div>
      
      {/* Intelligence Metrics */}
      <div className="space-y-2">
        {/* Usage Ripple */}
        {prop.usage_ripple && prop.usage_ripple > 0 && (
          <div className="flex items-center justify-between p-2 bg-cyan-500/10 rounded border border-cyan-500/30">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-cyan-400" />
              <span className="text-xs text-cyan-400 font-medium">Usage Ripple™</span>
            </div>
            <span className="text-sm font-bold text-cyan-400">+{prop.usage_ripple.toFixed(1)}% Volume Shift</span>
          </div>
        )}
        
        {/* Live DvP */}
        {prop.dvp_rank && (
          <div className={`flex items-center justify-between p-2 rounded border ${
            prop.dvp_rank_color === 'green' ? 'bg-emerald-500/10 border-emerald-500/30' :
            prop.dvp_rank_color === 'red' ? 'bg-red-500/10 border-red-500/30' :
            'bg-amber-500/10 border-amber-500/30'
          }`}>
            <div className="flex items-center gap-2">
              <Shield className={`w-4 h-4 ${
                prop.dvp_rank_color === 'green' ? 'text-emerald-400' :
                prop.dvp_rank_color === 'red' ? 'text-red-400' : 'text-amber-400'
              }`} />
              <span className={`text-xs font-medium ${
                prop.dvp_rank_color === 'green' ? 'text-emerald-400' :
                prop.dvp_rank_color === 'red' ? 'text-red-400' : 'text-amber-400'
              }`}>Live DvP</span>
            </div>
            <span className={`text-sm font-bold ${
              prop.dvp_rank_color === 'green' ? 'text-emerald-400' :
              prop.dvp_rank_color === 'red' ? 'text-red-400' : 'text-amber-400'
            }`}>Opponent Rank: #{prop.dvp_rank}</span>
          </div>
        )}
        
        {/* Pace Multiplier */}
        {prop.pace_factor && (
          <div className={`flex items-center justify-between p-2 rounded border ${
            prop.pace_factor > 1.1 ? 'bg-purple-500/10 border-purple-500/30' :
            prop.pace_factor < 0.9 ? 'bg-blue-500/10 border-blue-500/30' :
            'bg-zinc-700/50 border-zinc-600'
          }`}>
            <div className="flex items-center gap-2">
              <TrendingUp className={`w-4 h-4 ${
                prop.pace_factor > 1.1 ? 'text-purple-400' :
                prop.pace_factor < 0.9 ? 'text-blue-400' : 'text-zinc-400'
              }`} />
              <span className={`text-xs font-medium ${
                prop.pace_factor > 1.1 ? 'text-purple-400' :
                prop.pace_factor < 0.9 ? 'text-blue-400' : 'text-zinc-400'
              }`}>Pace Multiplier</span>
            </div>
            <span className={`text-sm font-bold ${
              prop.pace_factor > 1.1 ? 'text-purple-400' :
              prop.pace_factor < 0.9 ? 'text-blue-400' : 'text-zinc-400'
            }`}>
              {prop.pace_factor > 1.1 ? 'High-Tempo Engagement' :
               prop.pace_factor < 0.9 ? 'Grind-Out Pace' : 'Standard Pace'}
            </span>
          </div>
        )}
        
        {/* Stability Index */}
        <div className={`flex items-center justify-between p-2 rounded border ${stability.level.border} ${stability.level.bg}`}>
          <div className="flex items-center gap-2">
            <div className={`w-4 h-4 rounded-full flex items-center justify-center ${
              stability.index >= 80 ? 'bg-emerald-400' : stability.index >= 50 ? 'bg-amber-400' : 'bg-red-400'
            }`}>
              <span className="text-[8px] text-black font-bold">S</span>
            </div>
            <span className={`text-xs font-medium ${stability.level.color}`}>Stability Index</span>
          </div>
          <span className={`text-sm font-bold ${stability.level.color}`}>
            {stability.index} - {stability.level.label}
          </span>
        </div>
      </div>
    </div>
  );
});

// Single Prop Row in Arsenal
const PropArsenalItem = memo(({ prop, isRadar, isExpanded, onToggle, onAddToPost }) => {
  const stability = calculateStabilityIndex(prop.std_dev, prop.season_avg);
  
  return (
    <div 
      className={`rounded-lg overflow-hidden transition-all duration-200 ${
        isRadar 
          ? 'border-2 border-emerald-500 shadow-lg shadow-emerald-500/20' 
          : 'border border-zinc-700/50'
      }`}
      data-testid={`prop-arsenal-${prop.stat_type}`}
    >
      {/* Prop Header */}
      <div 
        onClick={onToggle}
        className={`flex items-center justify-between p-3 cursor-pointer transition-colors ${
          isRadar 
            ? 'bg-gradient-to-r from-emerald-950/50 to-zinc-900 hover:from-emerald-950/70' 
            : 'bg-zinc-800/50 hover:bg-zinc-700/50'
        }`}
      >
        <div className="flex items-center gap-3">
          {isRadar ? (
            <TargetLockIcon isLocked={true} />
          ) : (
            <Target className="w-4 h-4 text-zinc-500" />
          )}
          
          <div>
            <div className="flex items-center gap-2">
              <span className={`text-sm font-bold ${isRadar ? 'text-emerald-400' : 'text-white'}`}>
                {prop.stat_type}
              </span>
              {isRadar && (
                <span className="px-1.5 py-0.5 text-[9px] font-bold bg-emerald-500 text-black rounded">
                  OBJECTIVE
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span>{prop.direction?.toUpperCase() || 'OVER'} {prop.line}</span>
              {prop.odds && (
                <span className="text-zinc-500">@ {prop.odds > 0 ? `+${prop.odds}` : prop.odds}</span>
              )}
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-2">
          <StabilityBadge index={stability.index} level={stability.level} />
          {prop.dvp_rank && <FrictionBadge rank={prop.dvp_rank} color={prop.dvp_rank_color} />}
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-zinc-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-zinc-400" />
          )}
        </div>
      </div>
      
      {/* Expanded Content */}
      {isExpanded && (
        <div className="p-3 bg-zinc-900/50 border-t border-zinc-700/50">
          {isRadar ? (
            <IntelligenceSuite prop={prop} />
          ) : (
            <StandardPropStats 
              l5Avg={prop.l5_avg} 
              l10Avg={prop.l10_avg} 
              seasonAvg={prop.season_avg} 
            />
          )}
          
          {/* Add to Command Post Button */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onAddToPost(prop);
            }}
            className={`w-full mt-3 py-2 rounded-lg flex items-center justify-center gap-2 font-medium text-sm transition-all ${
              isRadar 
                ? 'bg-emerald-500 hover:bg-emerald-400 text-black' 
                : 'bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-400 border border-cyan-500/40'
            }`}
            data-testid={`add-to-post-${prop.stat_type}`}
          >
            <Plus className="w-4 h-4" />
            Add to Command Post
          </button>
        </div>
      )}
    </div>
  );
});

// ==================== MAIN COMPONENT ====================

/**
 * TacticalPlayerCard
 * @param {Object} player - Player data with props array
 * @param {Array} radarPicks - Array of stat_types that are PropVision recommendations
 * @param {Function} onAddToPost - Callback when adding prop to Command Post
 * @param {boolean} defaultExpanded - Whether card starts expanded
 */
const TacticalPlayerCard = memo(({ 
  player, 
  radarPicks = [], 
  onAddToPost,
  defaultExpanded = false 
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [expandedProps, setExpandedProps] = useState({});
  
  const toggleCard = useCallback(() => {
    setIsExpanded(prev => !prev);
  }, []);
  
  const toggleProp = useCallback((statType) => {
    setExpandedProps(prev => ({
      ...prev,
      [statType]: !prev[statType]
    }));
  }, []);
  
  const handleAddToPost = useCallback((prop) => {
    if (onAddToPost) {
      onAddToPost({
        player_name: player.player_name,
        player_id: player.player_id,
        team: player.team,
        ...prop
      });
    }
  }, [onAddToPost, player]);
  
  if (!player) return null;
  
  const { 
    player_name, 
    team, 
    position,
    photo_url,
    opponent,
    props = []
  } = player;
  
  // Count actual Target-Lock props (is_radar: true)
  const targetLockCount = props.filter(p => p.is_radar === true).length;
  
  // Separate radar picks from standard props - Target-Lock first
  const sortedProps = [...props].sort((a, b) => {
    const aIsRadar = a.is_radar === true;
    const bIsRadar = b.is_radar === true;
    if (aIsRadar && !bIsRadar) return -1;
    if (!aIsRadar && bIsRadar) return 1;
    // Then sort by stat_type alphabetically
    return (a.stat_type || '').localeCompare(b.stat_type || '');
  });
  
  return (
    <div 
      className="tactical-player-card rounded-xl overflow-hidden border border-zinc-700/50 bg-gradient-to-b from-zinc-800/80 to-zinc-900"
      style={{
        backgroundImage: 'linear-gradient(135deg, rgba(39,39,42,0.9) 0%, rgba(24,24,27,0.95) 100%)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05)'
      }}
      data-testid={`tactical-card-${player_name?.replace(/\s/g, '-')}`}
    >
      {/* Card Header - Metallic texture */}
      <div 
        onClick={toggleCard}
        className="p-4 cursor-pointer transition-all hover:bg-zinc-800/50"
        style={{
          background: 'linear-gradient(180deg, rgba(63,63,70,0.5) 0%, transparent 100%)',
          borderBottom: '1px solid rgba(63,63,70,0.5)'
        }}
      >
        <div className="flex items-center gap-4">
          {/* Player Photo */}
          <div className="relative flex-shrink-0">
            <div 
              className="w-14 h-14 rounded-lg overflow-hidden bg-zinc-700 border border-zinc-600"
              style={{ boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.3)' }}
            >
              {photo_url ? (
                <img 
                  src={photo_url} 
                  alt={player_name}
                  className="w-full h-full object-cover"
                  onError={(e) => e.target.style.display = 'none'}
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-zinc-400 text-xl font-bold">
                  {player_name?.charAt(0)}
                </div>
              )}
            </div>
            
            {/* Radar indicator */}
            {targetLockCount > 0 && (
              <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center animate-pulse">
                <Crosshair className="w-3 h-3 text-black" />
              </div>
            )}
          </div>
          
          {/* Player Info */}
          <div className="flex-1 min-w-0">
            <h3 className="text-base font-bold text-white truncate">{player_name}</h3>
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span className="font-mono">{team}</span>
              {position && (
                <>
                  <span className="text-zinc-600">·</span>
                  <span>{position}</span>
                </>
              )}
            </div>
            {opponent && (
              <div className="flex items-center gap-1 mt-1 text-[11px] text-zinc-500">
                <span className="uppercase">vs</span>
                <span className="font-bold text-zinc-400">{opponent}</span>
              </div>
            )}
          </div>
          
          {/* Expand indicator */}
          <div className="flex items-center gap-2">
            {targetLockCount > 0 && (
              <div className="px-2 py-1 rounded bg-emerald-500/20 border border-emerald-500/40">
                <span className="text-[10px] font-bold text-emerald-400">
                  {targetLockCount} OBJECTIVE{targetLockCount > 1 ? 'S' : ''}
                </span>
              </div>
            )}
            <div className={`transform transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
              <ChevronDown className="w-5 h-5 text-zinc-400" />
            </div>
          </div>
        </div>
      </div>
      
      {/* Prop Arsenal - Expanded */}
      {isExpanded && (
        <div className="p-4 pt-2 space-y-2">
          <div className="flex items-center gap-2 mb-3 pb-2 border-b border-zinc-700/50">
            <Target className="w-4 h-4 text-zinc-500" />
            <span className="text-xs font-bold text-zinc-400 uppercase tracking-wider">
              Prop Arsenal
            </span>
            <span className="text-[10px] text-zinc-500">
              {props.length} Available
            </span>
          </div>
          
          {sortedProps.length > 0 ? (
            sortedProps.map((prop) => {
              // Use is_radar from prop directly (from API) - this is the only source of truth
              // Only Target-Lock props (on the PropVision board) have is_radar: true
              const isRadar = prop.is_radar === true;
              return (
                <PropArsenalItem
                  key={`${prop.stat_type}-${prop.line}-${prop.direction}`}
                  prop={prop}
                  isRadar={isRadar}
                  isExpanded={expandedProps[`${prop.stat_type}-${prop.line}-${prop.direction}`]}
                  onToggle={() => toggleProp(`${prop.stat_type}-${prop.line}-${prop.direction}`)}
                  onAddToPost={handleAddToPost}
                />
              );
            })
          ) : (
            <div className="text-center py-6 text-zinc-500 text-sm">
              No props available for this player
            </div>
          )}
        </div>
      )}
    </div>
  );
});

TacticalPlayerCard.displayName = 'TacticalPlayerCard';

export default TacticalPlayerCard;
export { calculateStabilityIndex, StabilityBadge, FrictionBadge, IntelligenceSuite };
