/**
 * PlayerDetailPage - Complete player prop ladder view
 * Shows all props organized by category with highlight support
 */
import React, { useState, useEffect, useCallback, useMemo, useRef, memo } from 'react';
import axios from 'axios';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { 
  ArrowLeft, ChevronDown, ChevronRight, Flame, Target, Zap, 
  TrendingUp, TrendingDown, AlertTriangle, Shield, Lock
} from 'lucide-react';
import { DemonIcon, GoblinIcon } from './Icons';
import { STAT_CATEGORIES, getCategoryKey, TEAM_LOGOS } from './constants';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ==================== PLAYER HEADSHOT ====================
// Uses photo_url from nba_master_hub_2026 (no external API calls on render)
const PlayerHeadshot = memo(({ playerName, team, photoUrl, size = 'md', className = '' }) => {
  const [error, setError] = useState(false);
  const sizeClasses = { sm: 'w-8 h-8', md: 'w-12 h-12', lg: 'w-16 h-16', xl: 'w-24 h-24' };
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  
  const isValidPhotoUrl = photoUrl && !photoUrl.includes('nophoto');
  const teamLogoUrl = team ? TEAM_LOGOS[team] : null;
  
  if (!isValidPhotoUrl || error) {
    if (teamLogoUrl) {
      return (
        <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1.5 ${className}`}>
          <img src={teamLogoUrl} alt={team} className="w-full h-full object-contain" onError={(e) => e.target.style.display = 'none'} />
        </div>
      );
    }
    return (
      <div className={`${sizeClass} rounded-full bg-zinc-800 flex items-center justify-center text-zinc-500 font-bold ${className}`}>
        {playerName?.charAt(0) || '?'}
      </div>
    );
  }
  
  return (
    <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 ${className}`}>
      <img src={photoUrl} alt={playerName} onError={() => setError(true)} 
        className="w-full h-full object-cover" style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }} />
    </div>
  );
});

// ==================== SKELETON LOADER ====================
const SkeletonPlayerDetail = () => (
  <div className="space-y-4 animate-pulse">
    {[1, 2, 3].map(i => (
      <div key={i} className="bg-zinc-800/50 rounded-lg h-24" />
    ))}
  </div>
);

// ==================== LADDER PROP ROW ====================
const LadderPropRow = memo(({ prop, categoryStats, isFirst, isLast, isHighlighted, highlightRef, glowClass, highlightType, playerInsights }) => {
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  const isStandard = !isDemon && !isGoblin;
  
  // Hit rate colors
  const getHitRateColor = (rate) => {
    if (rate >= 80) return 'text-green-400';
    if (rate >= 60) return 'text-yellow-400';
    if (rate >= 40) return 'text-orange-400';
    return 'text-red-400';
  };
  
  const h10Rate = prop.h10_rate || 0;
  const h5Rate = prop.h5_rate || 0;
  const line = prop.line || 0;
  const seasonAvg = categoryStats?.season_avg || prop.season_avg || 0;
  const gapFromAvg = seasonAvg > 0 ? ((seasonAvg - line) / line * 100).toFixed(1) : null;
  
  // Determine row styling
  const rowBg = isDemon 
    ? 'bg-gradient-to-r from-red-950/40 to-zinc-900 border-l-2 border-red-500'
    : isGoblin 
    ? 'bg-gradient-to-r from-green-950/40 to-zinc-900 border-l-2 border-green-500'
    : 'bg-zinc-900/50 border-l-2 border-zinc-700';
  
  return (
    <div
      ref={isHighlighted ? highlightRef : null}
      className={`
        ${rowBg} rounded-lg p-3 transition-all
        ${isHighlighted ? `${glowClass} ring-2 ${highlightType === 'goblin' ? 'ring-green-500' : 'ring-amber-500'}` : ''}
        ${isFirst ? 'rounded-t-lg' : ''} ${isLast ? 'rounded-b-lg' : ''}
      `}
      data-highlighted={isHighlighted}
      data-testid={`prop-row-${prop.market}-${line}`}
    >
      <div className="flex items-center justify-between">
        {/* Left: Line + Direction */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            {isDemon && <DemonIcon size={16} />}
            {isGoblin && <GoblinIcon size={16} />}
            {isStandard && <Target className="w-4 h-4 text-zinc-500" />}
          </div>
          
          <div>
            <div className="flex items-center gap-2">
              <span className="text-white font-bold text-lg">{line}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                (prop.direction || 'over').toLowerCase() === 'over' 
                  ? 'bg-green-500/20 text-green-400' 
                  : 'bg-red-500/20 text-red-400'
              }`}>
                {prop.direction || 'Over'}
              </span>
              {isHighlighted && (
                <Badge className="bg-purple-500/30 text-purple-300 border-none text-[9px] animate-pulse">
                  VISION PICK
                </Badge>
              )}
            </div>
            
            {/* Price/Odds */}
            {prop.price && (
              <span className="text-xs text-zinc-500 font-mono">
                {prop.price > 0 ? '+' : ''}{prop.price}
              </span>
            )}
          </div>
        </div>
        
        {/* Right: Hit Rates */}
        <div className="flex items-center gap-4">
          {/* Season Avg Comparison */}
          {gapFromAvg && (
            <div className="text-right">
              <div className="text-[10px] text-zinc-500">vs Avg</div>
              <div className={`text-sm font-medium ${parseFloat(gapFromAvg) > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {parseFloat(gapFromAvg) > 0 ? '+' : ''}{gapFromAvg}%
              </div>
            </div>
          )}
          
          {/* L10 */}
          <div className="text-right">
            <div className="text-[10px] text-zinc-500">L10</div>
            <div className={`text-sm font-bold ${getHitRateColor(h10Rate)}`}>{h10Rate}%</div>
          </div>
          
          {/* L5 */}
          <div className="text-right">
            <div className="text-[10px] text-zinc-500">L5</div>
            <div className={`text-sm font-bold ${getHitRateColor(h5Rate)}`}>{h5Rate}%</div>
          </div>
        </div>
      </div>
      
      {/* AI Vision insight if available */}
      {(prop.intel_briefing || playerInsights?.intel_briefing) && isHighlighted && (
        <div className="mt-2 pt-2 border-t border-purple-800/30">
          <div className="flex items-center gap-1 mb-1">
            <Zap className="w-3 h-3 text-purple-400" />
            <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
          </div>
          <p className="text-[10px] text-purple-300/80 italic">
            "{prop.intel_briefing || playerInsights?.intel_briefing}"
          </p>
        </div>
      )}
    </div>
  );
});

// ==================== CATEGORY ACCORDION ====================
const CategoryAccordion = memo(({ 
  categoryKey, categoryName, props, isExpanded, onToggle, stats, 
  isHighlightedProp, highlightRef, glowClass, glowSubtleClass, highlightType, playerInsights 
}) => {
  // Count demons/goblins in this category
  const demons = props.filter(p => p.is_demon);
  const goblins = props.filter(p => p.is_goblin);
  
  // Sort props by line value
  const sortedProps = [...props].sort((a, b) => (a.line || 0) - (b.line || 0));
  
  // Check if any prop in this category is highlighted
  const hasHighlight = sortedProps.some(p => isHighlightedProp(p));
  
  return (
    <Card className={`
      bg-zinc-900/50 border-zinc-800 overflow-hidden
      ${hasHighlight ? glowSubtleClass : ''}
    `}>
      {/* Accordion Header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-3 hover:bg-zinc-800/50 transition-colors"
        data-testid={`category-${categoryKey}`}
      >
        <div className="flex items-center gap-2">
          <span className="text-white font-bold">{categoryName}</span>
          <span className="text-xs text-zinc-500">({props.length})</span>
          
          {demons.length > 0 && (
            <div className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-red-500/20">
              <DemonIcon size={12} />
              <span className="text-[10px] text-red-400 font-bold">{demons.length}</span>
            </div>
          )}
          
          {goblins.length > 0 && (
            <div className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-green-500/20">
              <GoblinIcon size={12} />
              <span className="text-[10px] text-green-400 font-bold">{goblins.length}</span>
            </div>
          )}
        </div>
        
        <div className="flex items-center gap-2">
          {stats?.season_avg && (
            <span className="text-xs text-zinc-400">
              Avg: <span className="text-white font-mono">{stats.season_avg.toFixed(1)}</span>
            </span>
          )}
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-zinc-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-zinc-400" />
          )}
        </div>
      </button>
      
      {/* Accordion Content */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-1">
          {sortedProps.map((prop, idx) => (
            <LadderPropRow
              key={`${prop.market}-${prop.line}-${idx}`}
              prop={prop}
              categoryStats={stats}
              isFirst={idx === 0}
              isLast={idx === sortedProps.length - 1}
              isHighlighted={isHighlightedProp(prop)}
              highlightRef={highlightRef}
              glowClass={glowClass}
              highlightType={highlightType}
              playerInsights={playerInsights}
            />
          ))}
        </div>
      )}
    </Card>
  );
});

// ==================== MAIN PLAYER DETAIL PAGE ====================
export const PlayerDetailPage = ({ playerName, onBack, highlightProp = null, highlightType = 'demon' }) => {
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedCategories, setExpandedCategories] = useState(new Set(['PTS', 'AST', 'REB']));
  const highlightRef = useRef(null);
  
  const glowClass = highlightType === 'goblin' ? 'emerald-glow' : 'beacon-glow';
  const glowSubtleClass = highlightType === 'goblin' ? 'emerald-glow-subtle' : 'beacon-glow-subtle';
  
  // Parse highlight info (format: "stat_type|line|direction")
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
  
  // Fetch player data
  useEffect(() => {
    const fetchPlayer = async () => {
      try {
        setLoading(true);
        const response = await axios.get(`${API}/v3/cached-player/${encodeURIComponent(playerName)}`);
        
        if (response.data.success && response.data.player) {
          setPlayer(response.data.player);
        } else {
          setError(response.data.message || 'Player not found in cache');
        }
      } catch (err) {
        console.error('Error fetching player:', err);
        setError('Failed to load player data');
      } finally {
        setLoading(false);
      }
    };
    
    fetchPlayer();
  }, [playerName]);
  
  // Group props by category
  const groupedProps = useMemo(() => {
    if (!player?.props) return {};
    
    const groups = {};
    player.props.forEach(prop => {
      const categoryKey = getCategoryKey(prop.market);
      if (!groups[categoryKey]) groups[categoryKey] = [];
      groups[categoryKey].push(prop);
    });
    
    return groups;
  }, [player]);
  
  // Auto-expand highlighted category
  useEffect(() => {
    if (highlightInfo && Object.keys(groupedProps).length > 0) {
      const matchingCategory = Object.entries(groupedProps).find(([key, props]) => {
        return props.some(p => getCategoryKey(p.market) === highlightInfo.statType || key === highlightInfo.statType);
      });
      
      if (matchingCategory) {
        setExpandedCategories(prev => new Set([...prev, matchingCategory[0]]));
      }
      
      setTimeout(() => {
        if (highlightRef.current) {
          highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 300);
    }
  }, [highlightInfo, groupedProps]);
  
  // Check if prop is highlighted
  const isHighlightedProp = useCallback((prop) => {
    if (!highlightInfo) return false;
    const propCategory = getCategoryKey(prop.market);
    const propDirection = (prop.direction || 'over').toLowerCase();
    const highlightDirection = (highlightInfo.direction || 'over').toLowerCase();
    return (
      (propCategory === highlightInfo.statType || prop.stat_type_extracted === highlightInfo.statType) &&
      Math.abs(prop.line - highlightInfo.line) < 0.1 &&
      propDirection === highlightDirection
    );
  }, [highlightInfo]);
  
  // Order categories
  const orderedCategories = useMemo(() => {
    const keys = Object.keys(groupedProps);
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
  
  const toggleCategory = (key) => {
    setExpandedCategories(prev => {
      const newSet = new Set(prev);
      if (newSet.has(key)) newSet.delete(key);
      else newSet.add(key);
      return newSet;
    });
  };
  
  const expandAll = () => setExpandedCategories(new Set(orderedCategories));
  const collapseAll = () => setExpandedCategories(new Set());
  
  const demons = player?.props?.filter(p => p.is_demon) || [];
  const goblins = player?.props?.filter(p => p.is_goblin) || [];
  
  const getStatsForCategory = (categoryKey) => {
    const category = STAT_CATEGORIES[categoryKey];
    if (!category || !player?.stats_summary) return {};
    const baseMarket = category.markets[0];
    return player.stats_summary[baseMarket] || {};
  };
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack} className="text-zinc-400 hover:text-white p-1" data-testid="back-btn">
            <ArrowLeft className="w-5 h-5" />
          </Button>
          
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
            <Button variant="outline" size="sm" onClick={onBack} className="mt-4">Go Back</Button>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Quick Actions */}
            <div className="flex items-center justify-between">
              <div className="text-xs text-zinc-500">
                {orderedCategories.length} categories · {player?.props?.length || 0} props
              </div>
              <div className="flex items-center gap-2">
                <button onClick={expandAll} className="text-xs text-zinc-400 hover:text-white">Expand All</button>
                <span className="text-zinc-600">|</span>
                <button onClick={collapseAll} className="text-xs text-zinc-400 hover:text-white">Collapse All</button>
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
                    intel_briefing: player?.intel_briefing
                  }}
                />
              );
            })}
            
            {orderedCategories.length === 0 && (
              <div className="text-center py-8 text-zinc-500">No props available</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default PlayerDetailPage;
