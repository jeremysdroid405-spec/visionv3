/**
 * TACTICAL PLAYER CARD COMPONENT
 * ===============================
 * PropVision Command Post - Player prop card with flat list layout
 * 
 * Features:
 * - Flat list of props grouped by category
 * - Clear headers for each prop type (PTS, REB, AST, PRA, PR, PA, RA, etc.)
 * - Target-Lock styling for recommended props
 * - Stats from NBA Master Hub
 */

import React, { useState, memo, useCallback } from 'react';
import { 
  Target, ChevronDown, Shield, Zap, TrendingUp, 
  Crosshair, Plus
} from 'lucide-react';

// ==================== PROP CATEGORY LABELS ====================
const PROP_LABELS = {
  'PTS': 'Points',
  'REB': 'Rebounds',
  'AST': 'Assists',
  '3PM': '3-Pointers Made',
  'STL': 'Steals',
  'BLK': 'Blocks',
  'TO': 'Turnovers',
  'PRA': 'PRA',  // Points + Rebounds + Assists
  'PR': 'PR',    // Points + Rebounds
  'PA': 'PA',    // Points + Assists
  'RA': 'RA',    // Rebounds + Assists
  'BLST': 'Blocks + Steals',
  'FGM': 'Field Goals Made',
  'FTM': 'Free Throws Made',
  'MIN': 'Minutes',
  'DD': 'Double-Double',
  'TD': 'Triple-Double',
};

// Category order for sorting
const CATEGORY_ORDER = ['PTS', 'REB', 'AST', 'PRA', 'PR', 'PA', 'RA', '3PM', 'STL', 'BLK', 'BLST', 'TO', 'FGM', 'FTM', 'MIN'];

// ==================== HELPER FUNCTIONS ====================

const getPropLabel = (statType) => {
  return PROP_LABELS[statType] || statType;
};

const groupPropsByCategory = (props) => {
  const groups = {};
  
  props.forEach(prop => {
    const cat = prop.stat_type || 'OTHER';
    if (!groups[cat]) {
      groups[cat] = [];
    }
    groups[cat].push(prop);
  });
  
  // Sort categories by predefined order
  const sortedCategories = Object.keys(groups).sort((a, b) => {
    const aIdx = CATEGORY_ORDER.indexOf(a);
    const bIdx = CATEGORY_ORDER.indexOf(b);
    if (aIdx === -1 && bIdx === -1) return a.localeCompare(b);
    if (aIdx === -1) return 1;
    if (bIdx === -1) return -1;
    return aIdx - bIdx;
  });
  
  return { groups, sortedCategories };
};

// ==================== SUB-COMPONENTS ====================

// Single Prop Row
const PropRow = memo(({ prop, isRadar, onAddToPost }) => {
  const direction = (prop.direction || 'over').toUpperCase();
  const line = prop.line;
  
  return (
    <div 
      className={`flex items-center justify-between py-2 px-3 rounded-lg transition-all ${
        isRadar 
          ? 'bg-emerald-950/40 border border-emerald-500/50' 
          : 'bg-zinc-800/30 border border-zinc-700/30 hover:bg-zinc-700/30'
      }`}
      data-testid={`prop-row-${prop.stat_type}-${line}`}
    >
      {/* Left: Direction + Line */}
      <div className="flex items-center gap-3">
        {isRadar && (
          <div className="w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center">
            <Crosshair className="w-3 h-3 text-black" />
          </div>
        )}
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-sm font-bold ${isRadar ? 'text-emerald-400' : 'text-white'}`}>
              {direction} {line}
            </span>
            {isRadar && (
              <span className="px-1.5 py-0.5 text-[8px] font-bold bg-emerald-500 text-black rounded">
                TARGET
              </span>
            )}
          </div>
          {prop.odds && (
            <span className="text-[10px] text-zinc-500">
              @ {prop.odds > 0 ? `+${prop.odds}` : prop.odds}
            </span>
          )}
        </div>
      </div>
      
      {/* Center: Stats */}
      <div className="flex items-center gap-4 text-xs">
        <div className="text-center">
          <div className="text-zinc-500 text-[9px]">L5</div>
          <div className={`font-bold ${prop.l5_avg ? 'text-white' : 'text-zinc-600'}`}>
            {prop.l5_avg ?? '-'}
          </div>
        </div>
        <div className="text-center">
          <div className="text-zinc-500 text-[9px]">L10</div>
          <div className={`font-bold ${prop.l10_avg ? 'text-white' : 'text-zinc-600'}`}>
            {prop.l10_avg ?? '-'}
          </div>
        </div>
        <div className="text-center">
          <div className="text-zinc-500 text-[9px]">SZN</div>
          <div className={`font-bold ${prop.season_avg ? 'text-white' : 'text-zinc-600'}`}>
            {prop.season_avg ?? '-'}
          </div>
        </div>
      </div>
      
      {/* Right: Add Button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onAddToPost(prop);
        }}
        className={`p-1.5 rounded-lg transition-all ${
          isRadar 
            ? 'bg-emerald-500 hover:bg-emerald-400 text-black' 
            : 'bg-zinc-700 hover:bg-zinc-600 text-white'
        }`}
        title="Add to Command Post"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
});

// Category Header
const CategoryHeader = memo(({ category, count, hasRadar }) => {
  const label = getPropLabel(category);
  
  return (
    <div className={`flex items-center gap-2 py-2 px-1 border-b ${
      hasRadar ? 'border-emerald-500/30' : 'border-zinc-700/50'
    }`}>
      {hasRadar && <Crosshair className="w-3.5 h-3.5 text-emerald-400" />}
      <span className={`text-xs font-bold uppercase tracking-wider ${
        hasRadar ? 'text-emerald-400' : 'text-zinc-400'
      }`}>
        {label}
      </span>
      <span className="text-[10px] text-zinc-500">
        ({count} line{count > 1 ? 's' : ''})
      </span>
    </div>
  );
});

// ==================== MAIN COMPONENT ====================

const TacticalPlayerCard = memo(({ 
  player, 
  radarPicks = [],
  onAddToPost,
  defaultExpanded = false 
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  
  const toggleCard = useCallback(() => {
    setIsExpanded(prev => !prev);
  }, []);
  
  const handleAddToPost = useCallback((prop) => {
    if (onAddToPost) {
      onAddToPost({
        player_name: player.player_name,
        player_id: player.player_id,
        team: player.team,
        photo_url: player.photo_url,
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
  
  // Count Target-Lock props
  const targetLockCount = props.filter(p => p.is_radar === true).length;
  
  // Group props by category
  const { groups, sortedCategories } = groupPropsByCategory(props);
  
  return (
    <div 
      className="tactical-player-card rounded-xl overflow-hidden border border-zinc-700/50 bg-gradient-to-b from-zinc-800/80 to-zinc-900"
      data-testid={`tactical-card-${player_name?.replace(/\s/g, '-')}`}
    >
      {/* Card Header */}
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
            <div className="w-14 h-14 rounded-lg overflow-hidden bg-zinc-700 border border-zinc-600">
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
                  {targetLockCount} TARGET{targetLockCount > 1 ? 'S' : ''}
                </span>
              </div>
            )}
            <div className={`transform transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
              <ChevronDown className="w-5 h-5 text-zinc-400" />
            </div>
          </div>
        </div>
      </div>
      
      {/* Props List - Expanded */}
      {isExpanded && (
        <div className="p-4 pt-2 space-y-4">
          {/* Overall header */}
          <div className="flex items-center gap-2 pb-2 border-b border-zinc-700/50">
            <Target className="w-4 h-4 text-zinc-500" />
            <span className="text-xs font-bold text-zinc-400 uppercase tracking-wider">
              Available Props
            </span>
            <span className="text-[10px] text-zinc-500">
              {props.length} total
            </span>
          </div>
          
          {sortedCategories.length > 0 ? (
            sortedCategories.map(category => {
              const categoryProps = groups[category];
              const hasRadar = categoryProps.some(p => p.is_radar === true);
              
              // Sort props within category: radar first, then by line
              const sortedCategoryProps = [...categoryProps].sort((a, b) => {
                if (a.is_radar && !b.is_radar) return -1;
                if (!a.is_radar && b.is_radar) return 1;
                return (a.line || 0) - (b.line || 0);
              });
              
              return (
                <div key={category} className="space-y-2">
                  <CategoryHeader 
                    category={category} 
                    count={categoryProps.length}
                    hasRadar={hasRadar}
                  />
                  <div className="space-y-1.5 pl-1">
                    {sortedCategoryProps.map((prop, idx) => (
                      <PropRow
                        key={`${prop.stat_type}-${prop.line}-${prop.direction}-${idx}`}
                        prop={prop}
                        isRadar={prop.is_radar === true}
                        onAddToPost={handleAddToPost}
                      />
                    ))}
                  </div>
                </div>
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
