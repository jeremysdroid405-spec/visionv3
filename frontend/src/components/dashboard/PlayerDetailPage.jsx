/**
 * PlayerDetailPage - Complete player prop view
 * Shows ALL props as a flat list with category headers (not accordions)
 */
import React, { useState, useEffect, useCallback, useMemo, useRef, memo } from 'react';
import axios from 'axios';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { 
  ArrowLeft, Target, Zap, Crosshair, Plus
} from 'lucide-react';
import { DemonIcon, GoblinIcon } from './Icons';
import { STAT_CATEGORIES, getCategoryKey, TEAM_LOGOS } from './constants';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ==================== PROP CATEGORY CONFIG ====================
const PROP_LABELS = {
  'PTS': 'Points',
  'REB': 'Rebounds',
  'AST': 'Assists',
  '3PM': '3-Pointers Made',
  'STL': 'Steals',
  'BLK': 'Blocks',
  'TO': 'Turnovers',
  'PRA': 'PRA',
  'PR': 'PR',
  'P+R': 'PR',
  'PA': 'PA',
  'P+A': 'PA',
  'RA': 'RA',
  'R+A': 'RA',
  'BLST': 'Blocks + Steals',
  'FGM': 'Field Goals Made',
  'FTM': 'Free Throws Made',
  'MIN': 'Minutes',
  'DD': 'Double-Double',
  'TD': 'Triple-Double',
};

const CATEGORY_ORDER = ['PTS', 'REB', 'AST', 'PRA', 'PR', 'PA', 'RA', '3PM', 'STL', 'BLK', 'BLST', 'TO', 'FGM', 'FTM', 'MIN'];

const normalizeStatType = (statType) => {
  const normMap = { 'P+R': 'PR', 'P+A': 'PA', 'R+A': 'RA' };
  return normMap[statType] || statType;
};

const getPropLabel = (statType) => {
  const normalized = normalizeStatType(statType);
  return PROP_LABELS[normalized] || PROP_LABELS[statType] || statType;
};

// ==================== PLAYER HEADSHOT ====================
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
    {[1, 2, 3, 4, 5].map(i => (
      <div key={i} className="bg-zinc-800/50 rounded-lg h-16" />
    ))}
  </div>
);

// ==================== SINGLE PROP ROW ====================
const PropRow = memo(({ prop, isHighlighted, highlightRef }) => {
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  const line = prop.line || 0;
  const direction = (prop.direction || 'over').toUpperCase();
  
  // Stats from baseline or hit_rates (different API formats)
  // Format 1: prop.l5_avg, prop.l10_avg, prop.season_avg
  // Format 2: prop.hit_rates.l5.avg, prop.hit_rates.l10.avg, prop.hit_rates.season.avg
  const hitRates = prop.hit_rates || {};
  const l5Avg = prop.l5_avg ?? hitRates.l5?.avg ?? hitRates.l5_avg;
  const l10Avg = prop.l10_avg ?? hitRates.l10?.avg ?? hitRates.l10_avg;
  const seasonAvg = prop.season_avg ?? hitRates.season?.avg ?? hitRates.season_avg;
  
  // Hit rates (percentage)
  const h10Rate = hitRates.l10?.hit_rate != null ? Math.round(hitRates.l10.hit_rate * 100) : (prop.h10_rate || 0);
  const h5Rate = hitRates.l5?.hit_rate != null ? Math.round(hitRates.l5.hit_rate * 100) : (prop.h5_rate || 0);
  
  const getHitRateColor = (rate) => {
    if (rate >= 80) return 'text-green-400';
    if (rate >= 60) return 'text-yellow-400';
    if (rate >= 40) return 'text-orange-400';
    return 'text-red-400';
  };
  
  return (
    <div 
      ref={isHighlighted ? highlightRef : null}
      className={`flex items-center justify-between py-3 px-4 rounded-lg transition-all ${
        isDemon 
          ? 'bg-gradient-to-r from-red-950/40 to-zinc-900 border border-red-500/30' 
          : isGoblin 
            ? 'bg-gradient-to-r from-green-950/40 to-zinc-900 border border-green-500/30'
            : isHighlighted
              ? 'bg-amber-950/30 border border-amber-500/50 ring-1 ring-amber-500/30'
              : 'bg-zinc-800/30 border border-zinc-700/30'
      }`}
      data-testid={`prop-row-${prop.stat_type}-${line}`}
    >
      {/* Left: Type indicator + Line */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          {isDemon && <DemonIcon size={16} />}
          {isGoblin && <GoblinIcon size={16} />}
          {!isDemon && !isGoblin && <Target className="w-4 h-4 text-zinc-500" />}
        </div>
        
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-base font-bold ${
              isDemon ? 'text-red-400' : isGoblin ? 'text-green-400' : 'text-white'
            }`}>
              {direction} {line}
            </span>
            {isDemon && (
              <span className="px-1.5 py-0.5 text-[8px] font-bold bg-red-500 text-white rounded">
                DEMON
              </span>
            )}
            {isGoblin && (
              <span className="px-1.5 py-0.5 text-[8px] font-bold bg-green-500 text-black rounded">
                GOBLIN
              </span>
            )}
            {isHighlighted && !isDemon && !isGoblin && (
              <span className="px-1.5 py-0.5 text-[8px] font-bold bg-amber-500 text-black rounded">
                VISION
              </span>
            )}
          </div>
          {prop.price && (
            <span className="text-[10px] text-zinc-500 font-mono">
              @ {prop.price > 0 ? '+' : ''}{prop.price}
            </span>
          )}
        </div>
      </div>
      
      {/* Center: L5/L10/SZN Averages */}
      <div className="flex items-center gap-5 text-xs">
        <div className="text-center min-w-[40px]">
          <div className="text-zinc-500 text-[9px]">L5</div>
          <div className={`font-bold ${l5Avg != null ? 'text-white' : 'text-zinc-600'}`}>
            {l5Avg != null ? l5Avg : '-'}
          </div>
        </div>
        <div className="text-center min-w-[40px]">
          <div className="text-zinc-500 text-[9px]">L10</div>
          <div className={`font-bold ${l10Avg != null ? 'text-white' : 'text-zinc-600'}`}>
            {l10Avg != null ? l10Avg : '-'}
          </div>
        </div>
        <div className="text-center min-w-[40px]">
          <div className="text-zinc-500 text-[9px]">SZN</div>
          <div className={`font-bold ${seasonAvg != null ? 'text-white' : 'text-zinc-600'}`}>
            {seasonAvg != null ? seasonAvg : '-'}
          </div>
        </div>
      </div>
      
      {/* Right: Hit Rates */}
      <div className="flex items-center gap-4 text-xs">
        <div className="text-center min-w-[35px]">
          <div className="text-zinc-500 text-[9px]">L10 HR</div>
          <div className={`font-bold ${getHitRateColor(h10Rate)}`}>
            {h10Rate > 0 ? `${h10Rate}%` : '-'}
          </div>
        </div>
        <div className="text-center min-w-[35px]">
          <div className="text-zinc-500 text-[9px]">L5 HR</div>
          <div className={`font-bold ${getHitRateColor(h5Rate)}`}>
            {h5Rate > 0 ? `${h5Rate}%` : '-'}
          </div>
        </div>
      </div>
    </div>
  );
});

// ==================== CATEGORY HEADER ====================
const CategoryHeader = memo(({ category, count, hasDemon, hasGoblin }) => {
  const label = getPropLabel(category);
  
  return (
    <div className="flex items-center gap-2 py-2 px-1 border-b border-zinc-700/50 mt-4 first:mt-0">
      <span className="text-sm font-bold uppercase tracking-wider text-zinc-300">
        {label}
      </span>
      <span className="text-[10px] text-zinc-500">
        ({count} line{count > 1 ? 's' : ''})
      </span>
      {hasDemon && (
        <div className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-red-500/20">
          <DemonIcon size={10} />
        </div>
      )}
      {hasGoblin && (
        <div className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-green-500/20">
          <GoblinIcon size={10} />
        </div>
      )}
    </div>
  );
});

// ==================== MAIN COMPONENT ====================
export const PlayerDetailPage = ({ playerName, onBack, highlightProp = null, highlightType = 'demon' }) => {
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const highlightRef = useRef(null);
  
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
  
  // Group props by normalized category - prioritize stat_type_extracted for specific categories
  const groupedProps = useMemo(() => {
    if (!player?.props) return {};
    
    const groups = {};
    player.props.forEach(prop => {
      // Use stat_type_extracted (AST, PTS, PRA, etc.) as primary, fallback to generic category
      const rawCat = prop.stat_type_extracted || getCategoryKey(prop.market) || 'OTHER';
      const cat = normalizeStatType(rawCat);
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push({ ...prop, stat_type: cat });
    });
    
    return groups;
  }, [player]);
  
  // Sort categories
  const sortedCategories = useMemo(() => {
    const keys = Object.keys(groupedProps);
    return keys.sort((a, b) => {
      const aIdx = CATEGORY_ORDER.indexOf(a);
      const bIdx = CATEGORY_ORDER.indexOf(b);
      if (aIdx === -1 && bIdx === -1) return a.localeCompare(b);
      if (aIdx === -1) return 1;
      if (bIdx === -1) return -1;
      return aIdx - bIdx;
    });
  }, [groupedProps]);
  
  // Auto-scroll to highlighted prop
  useEffect(() => {
    if (highlightInfo && highlightRef.current) {
      setTimeout(() => {
        highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 300);
    }
  }, [highlightInfo, player]);
  
  // Check if prop is highlighted
  const isHighlightedProp = useCallback((prop) => {
    if (!highlightInfo) return false;
    const propCategory = normalizeStatType(getCategoryKey(prop.market) || prop.stat_type_extracted || '');
    const propDirection = (prop.direction || 'over').toLowerCase();
    const highlightDirection = (highlightInfo.direction || 'over').toLowerCase();
    return (
      propCategory === highlightInfo.statType &&
      Math.abs((prop.line || 0) - highlightInfo.line) < 0.1 &&
      propDirection === highlightDirection
    );
  }, [highlightInfo]);
  
  // Count demons/goblins
  const demons = player?.props?.filter(p => p.is_demon) || [];
  const goblins = player?.props?.filter(p => p.is_goblin) || [];
  const totalProps = player?.props?.length || 0;
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-4">
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={onBack} 
            className="text-zinc-400 hover:text-white p-1" 
            data-testid="back-btn"
          >
            <ArrowLeft className="w-5 h-5" />
          </Button>
          
          {player && (
            <PlayerHeadshot 
              playerName={playerName}
              team={player.team}
              photoUrl={player.photo_url}
              size="lg"
            />
          )}
          
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-bold text-white truncate">{playerName}</h1>
            {player && (
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="font-mono">{player.team}</span>
                {player.position && <span>· {player.position}</span>}
              </div>
            )}
          </div>
          
          {/* Stats badges */}
          {player && (
            <div className="flex items-center gap-3 flex-shrink-0">
              <div className="text-center">
                <div className="text-[10px] text-zinc-500">PROPS</div>
                <div className="text-lg font-bold text-white">{totalProps}</div>
              </div>
              {demons.length > 0 && (
                <div className="flex items-center gap-1 px-2 py-1 rounded bg-red-500/20 border border-red-500/30">
                  <DemonIcon size={14} />
                  <span className="text-red-400 font-bold text-sm">{demons.length}</span>
                </div>
              )}
              {goblins.length > 0 && (
                <div className="flex items-center gap-1 px-2 py-1 rounded bg-green-500/20 border border-green-500/30">
                  <GoblinIcon size={14} />
                  <span className="text-green-400 font-bold text-sm">{goblins.length}</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      
      {/* Content - Flat list with headers */}
      <div className="p-4">
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
          <div className="space-y-2">
            {/* Summary bar */}
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
              <div className="flex items-center gap-2">
                <Target className="w-4 h-4 text-zinc-500" />
                <span className="text-sm font-medium text-zinc-400">
                  {sortedCategories.length} categories · {totalProps} total lines
                </span>
              </div>
            </div>
            
            {/* Flat list grouped by category */}
            {sortedCategories.length > 0 ? (
              sortedCategories.map(category => {
                const categoryProps = groupedProps[category] || [];
                const hasDemon = categoryProps.some(p => p.is_demon);
                const hasGoblin = categoryProps.some(p => p.is_goblin);
                
                // Sort: demons first, then goblins, then by line
                const sortedProps = [...categoryProps].sort((a, b) => {
                  if (a.is_demon && !b.is_demon) return -1;
                  if (!a.is_demon && b.is_demon) return 1;
                  if (a.is_goblin && !b.is_goblin) return -1;
                  if (!a.is_goblin && b.is_goblin) return 1;
                  return (a.line || 0) - (b.line || 0);
                });
                
                return (
                  <div key={category}>
                    <CategoryHeader 
                      category={category}
                      count={categoryProps.length}
                      hasDemon={hasDemon}
                      hasGoblin={hasGoblin}
                    />
                    <div className="space-y-1.5 mt-2">
                      {sortedProps.map((prop, idx) => (
                        <PropRow
                          key={`${prop.market}-${prop.line}-${prop.direction}-${idx}`}
                          prop={prop}
                          isHighlighted={isHighlightedProp(prop)}
                          highlightRef={highlightRef}
                        />
                      ))}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="text-center py-12 text-zinc-500">
                <Target className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p className="text-lg">No props available</p>
                <p className="text-sm mt-1">Check back closer to game time</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default PlayerDetailPage;
