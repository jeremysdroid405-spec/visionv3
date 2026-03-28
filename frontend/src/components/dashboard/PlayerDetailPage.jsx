/**
 * PlayerDetailPage - Complete player prop view
 * Shows ALL props as a flat list with category headers (not accordions)
 * 
 * SSOT Two-Pipe Architecture:
 * - PIPE 1: useMasterStats for player stats (24hr cache)
 * - PIPE 2: Live lines passed from parent or useLiveOdds
 */
import React, { useState, useCallback, useMemo, useRef, memo, useEffect } from 'react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { 
  ArrowLeft, Target, Zap, Crosshair, Plus
} from 'lucide-react';
import { DemonIcon, GoblinIcon } from './Icons';
import { STAT_CATEGORIES, getCategoryKey, TEAM_LOGOS, BACKEND_URL } from './constants';
import { BadgeRow, BADGE_REGISTRY, BadgeGridItem } from '../ui/BadgePill';
import GameLogBarChart from './GameLogBarChart';

// API URL for fetching player data
const API = BACKEND_URL || process.env.REACT_APP_BACKEND_URL || '';

// SSOT Global State Hooks
import { useMasterStats } from '../../hooks/useMasterStats';

// ==================== PROP CATEGORY CONFIG ====================
const PROP_LABELS = {
  'PTS': 'PTS',
  'REB': 'REB',
  'AST': 'AST',
  '3PM': '3PM',
  'STL': 'STL',
  'BLK': 'BLK',
  'TO': 'TO',
  'PRA': 'PRA',
  'PR': 'P+R',
  'P+R': 'P+R',
  'PA': 'P+A',
  'P+A': 'P+A',
  'RA': 'R+A',
  'R+A': 'R+A',
  'BLST': 'BLK+STL',
  'FGM': 'FGM',
  'FTM': 'FTM',
  'MIN': 'MIN',
  'DD': 'DD',
  'TD': 'TD',
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
const PropRow = memo(({ prop, isHighlighted, highlightRef, onVisionClick, gameLogs = [] }) => {
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  const line = prop.line || 0;
  const direction = (prop.direction || 'over').toUpperCase();
  const statType = prop.stat_type || prop.market || '';
  
  // Stats from baseline or hit_rates (different API formats)
  // Format 1: prop.l5_avg, prop.l10_avg, prop.season_avg
  // Format 2: prop.hit_rates.l5.avg, prop.hit_rates.l10.avg, prop.hit_rates.season.avg
  const hitRates = prop.hit_rates || {};
  const l5Avg = prop.l5_avg ?? hitRates.l5?.avg ?? hitRates.l5_avg;
  const l10Avg = prop.l10_avg ?? hitRates.l10?.avg ?? hitRates.l10_avg;
  const seasonAvg = prop.season_avg ?? hitRates.season?.avg ?? hitRates.season_avg;
  
  // Hit rates (percentage) - Handle both decimal (0-1) and percentage (0-100) formats
  // h10_rate and h5_rate are already percentages, l10_hit_rate/l5_hit_rate could be either
  const normalizeHitRate = (rate) => {
    if (rate == null) return 0;
    // If rate is > 1 and <= 100, it's already a percentage
    if (rate > 1 && rate <= 100) return Math.round(rate);
    // If rate is > 100, it's been double-converted, divide by 100
    if (rate > 100) return Math.round(rate / 100);
    // If rate is <= 1, it's a decimal, multiply by 100
    return Math.round(rate * 100);
  };
  
  const h10Rate = prop.h10_rate != null 
    ? normalizeHitRate(prop.h10_rate)
    : (prop.l10_hit_rate != null 
        ? normalizeHitRate(prop.l10_hit_rate) 
        : (hitRates.l10?.hit_rate != null ? normalizeHitRate(hitRates.l10.hit_rate) : (hitRates.l10_rate ?? 0)));
  const h5Rate = prop.h5_rate != null 
    ? normalizeHitRate(prop.h5_rate)
    : (prop.l5_hit_rate != null 
        ? normalizeHitRate(prop.l5_hit_rate) 
        : (hitRates.l5?.hit_rate != null ? normalizeHitRate(hitRates.l5.hit_rate) : (hitRates.l5_rate ?? 0)));
  
  const getHitRateColor = (rate) => {
    if (rate >= 80) return 'text-green-400';
    if (rate >= 60) return 'text-yellow-400';
    if (rate >= 40) return 'text-orange-400';
    return 'text-red-400';
  };
  
  // Handle click for Vision Pick
  const handleClick = () => {
    // Allow clicking on highlighted props OR props with intel_suite
    if ((isHighlighted || prop.intel_suite) && onVisionClick) {
      onVisionClick(prop);
    }
  };
  
  // Determine if this prop is clickable (has vision data)
  const isVisionProp = isHighlighted || prop.intel_suite;
  
  return (
    <div 
      ref={isHighlighted ? highlightRef : null}
      onClick={handleClick}
      className={`flex flex-col p-4 rounded-lg transition-all ${
        isHighlighted
          ? 'bg-gradient-to-r from-amber-950/50 via-yellow-900/30 to-amber-950/50 border-2 border-amber-400 ring-2 ring-amber-400/50 cursor-pointer hover:ring-amber-300/70' 
          : isVisionProp
            ? 'bg-gradient-to-r from-amber-950/30 to-zinc-900 border border-amber-500/40 cursor-pointer hover:border-amber-400 hover:shadow-[0_0_15px_rgba(251,191,36,0.2)]'
            : isDemon 
              ? 'bg-gradient-to-r from-red-950/40 to-zinc-900 border border-red-500/30' 
              : isGoblin 
                ? 'bg-gradient-to-r from-green-950/40 to-zinc-900 border border-green-500/30'
                : 'bg-zinc-800/30 border border-zinc-700/30'
      }`}
      style={isHighlighted ? { boxShadow: '0 0 25px rgba(251, 191, 36, 0.5), 0 0 50px rgba(251, 191, 36, 0.3), inset 0 0 20px rgba(251, 191, 36, 0.1)' } : {}}
      data-testid={`prop-row-${prop.stat_type}-${line}${isHighlighted ? '-vision' : isVisionProp ? '-clickable' : ''}`}
    >
      {/* TOP ROW: Icon + Line + Badges */}
      <div className="flex items-center gap-2 mb-3">
        {isHighlighted ? (
          <Crosshair className="w-5 h-5 text-amber-400 animate-pulse" />
        ) : isDemon ? (
          <DemonIcon size={18} />
        ) : isGoblin ? (
          <GoblinIcon size={18} />
        ) : (
          <Target className="w-4 h-4 text-zinc-500" />
        )}
        <span className={`text-lg font-bold ${
          isHighlighted ? 'text-amber-300' : isDemon ? 'text-red-400' : isGoblin ? 'text-green-400' : 'text-white'
        }`}>
          {direction} {line}
        </span>
        {isHighlighted && (
          <span className="px-1.5 py-0.5 text-[8px] font-black bg-gradient-to-r from-amber-500 to-yellow-400 text-black rounded-full flex items-center gap-0.5 animate-pulse">
            <Crosshair className="w-2.5 h-2.5" />
            VISION
          </span>
        )}
        {!isHighlighted && isVisionProp && (
          <span className="px-1.5 py-0.5 text-[8px] font-semibold bg-amber-500/20 text-amber-400 rounded-full flex items-center gap-0.5">
            <Zap className="w-2.5 h-2.5" />
            INTEL
          </span>
        )}
      </div>
      
      {/* BOTTOM ROW: Stats column on left, Chart on right - Stack on mobile */}
      <div className="flex flex-col sm:flex-row items-stretch gap-4">
        {/* LEFT: Stats Column - Full width on mobile */}
        <div className={`flex flex-row sm:flex-col justify-around sm:justify-center gap-2 py-2 px-3 rounded-md ${
          isHighlighted ? 'bg-amber-900/30' : 'bg-zinc-800/50'
        }`} style={{ minWidth: '90px' }}>
          <div className="text-center">
            <div className={`text-[9px] uppercase tracking-wide ${isHighlighted ? 'text-amber-400/70' : 'text-zinc-500'}`}>L10 Hit</div>
            <div className={`font-bold text-sm ${isHighlighted ? 'text-amber-300' : getHitRateColor(h10Rate)}`}>
              {h10Rate > 0 ? `${h10Rate}%` : '-'}
            </div>
          </div>
          <div className="text-center">
            <div className={`text-[9px] uppercase tracking-wide ${isHighlighted ? 'text-amber-400/70' : 'text-zinc-500'}`}>L5 Hit</div>
            <div className={`font-bold text-sm ${isHighlighted ? 'text-amber-300' : getHitRateColor(h5Rate)}`}>
              {h5Rate > 0 ? `${h5Rate}%` : '-'}
            </div>
          </div>
          <div className="hidden sm:block w-full h-px bg-zinc-700 my-1" />
          <div className="text-center">
            <div className={`text-[9px] uppercase tracking-wide ${isHighlighted ? 'text-amber-400/70' : 'text-zinc-500'}`}>L5 Avg</div>
            <div className={`font-bold text-sm ${isHighlighted ? 'text-amber-300' : 'text-white'}`}>
              {l5Avg != null ? l5Avg : '-'}
            </div>
          </div>
          <div className="text-center">
            <div className={`text-[9px] uppercase tracking-wide ${isHighlighted ? 'text-amber-400/70' : 'text-zinc-500'}`}>L10 Avg</div>
            <div className={`font-bold text-sm ${isHighlighted ? 'text-amber-300' : 'text-white'}`}>
              {l10Avg != null ? l10Avg : '-'}
            </div>
          </div>
          <div className="text-center">
            <div className={`text-[9px] uppercase tracking-wide ${isHighlighted ? 'text-amber-400/70' : 'text-zinc-500'}`}>Season</div>
            <div className={`font-bold text-sm ${isHighlighted ? 'text-amber-300' : 'text-white'}`}>
              {seasonAvg != null ? seasonAvg : '-'}
            </div>
          </div>
        </div>
        
        {/* RIGHT: Bar Chart - Full width on both mobile and desktop */}
        {gameLogs && gameLogs.length > 0 && (
          <div className="flex-1 flex flex-col w-full">
            <GameLogBarChart
              gameLogs={gameLogs}
              statType={statType}
              line={line}
              showGames={10}
              height="100%"
              l5Avg={l5Avg}
              l10Avg={l10Avg}
              seasonAvg={seasonAvg}
              className="flex-1 w-full"
            />
          </div>
        )}
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
// SSOT: Uses useMasterStats hook for player data (PIPE 1)
export const PlayerDetailPage = ({ playerName, playerData = null, onBack, highlightProp = null, highlightType = 'demon' }) => {
  // Direct state-based fetch
  const [player, setPlayer] = useState(playerData);
  const [loading, setLoading] = useState(!playerData);
  const [error, setError] = useState(null);
  const fetchIdRef = useRef(0);
  
  useEffect(() => {
    if (playerData) {
      setPlayer(playerData);
      setLoading(false);
      return;
    }
    
    if (!playerName) return;
    
    // Increment fetch ID to track current request
    const currentFetchId = ++fetchIdRef.current;
    
    const fetchUrl = `${API}/api/v3/player-with-badges/${encodeURIComponent(playerName)}`;
    
    setLoading(true);
    setError(null);
    
    // Use XMLHttpRequest with onreadystatechange for better compatibility
    const xhr = new XMLHttpRequest();
    xhr.open('GET', fetchUrl, true);
    xhr.setRequestHeader('Accept', 'application/json');
    
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== 4) return;
      
      // Always process, don't skip based on fetchId
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          
          if (data.success && data.player) {
            setPlayer(data.player);
          } else {
            setError('No available Bets today');
          }
        } catch (e) {
          setError('Failed to parse response');
        }
      } else if (xhr.status > 0) {
        setError(`HTTP error: ${xhr.status}`);
      }
      setLoading(false);
    };
    
    xhr.onerror = function() {
      if (fetchIdRef.current !== currentFetchId) return;
      setError('Network error');
      setLoading(false);
    };
    
    xhr.send();
  }, [playerName, playerData]);
  
  const [showIntelSuite, setShowIntelSuite] = useState(false);
  const [selectedVisionProp, setSelectedVisionProp] = useState(null);
  const highlightRef = useRef(null);
  
  // Handle Vision Pick click
  const handleVisionClick = useCallback((prop) => {
    setSelectedVisionProp(prop);
    setShowIntelSuite(true);
  }, []);
  
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
  
  // Check if prop is highlighted (Vision Pick)
  const isHighlightedProp = useCallback((prop) => {
    if (!highlightInfo) return false;
    
    // Use stat_type_extracted directly (PTS, REB, AST, etc.)
    const propStatType = normalizeStatType(prop.stat_type_extracted || prop.stat_type || '');
    const highlightStatType = normalizeStatType(highlightInfo.statType || '');
    
    const propDirection = (prop.direction || 'over').toLowerCase();
    const highlightDirection = (highlightInfo.direction || 'over').toLowerCase();
    
    const statMatch = propStatType.toUpperCase() === highlightStatType.toUpperCase();
    const lineMatch = Math.abs((prop.line || 0) - highlightInfo.line) < 0.1;
    const directionMatch = propDirection === highlightDirection;
    
    return statMatch && lineMatch && directionMatch;
  }, [highlightInfo]);
  
  // Count demons/goblins
  const demons = player?.props?.filter(p => p.is_demon) || [];
  const goblins = player?.props?.filter(p => p.is_goblin) || [];
  const totalProps = player?.props?.length || 0;
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-3 sm:gap-4">
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={onBack} 
            className="text-zinc-400 hover:text-white p-1 shrink-0" 
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
              className="shrink-0 sm:w-24 sm:h-24"
            />
          )}
          
          <div className="flex-1 min-w-0 overflow-hidden">
            <h1 className="text-lg sm:text-2xl font-bold text-white leading-tight" style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}>{playerName}</h1>
            {player && (
              <div className="flex items-center gap-2 text-xs sm:text-sm text-zinc-400 mt-0.5">
                <span className="font-mono">{player.team}</span>
                {player.position && <span>· {player.position}</span>}
              </div>
            )}
            
            {/* Player Season Stats */}
            {player?.baseline_stats && (
              <div className="flex items-center gap-4 mt-3">
                {player.baseline_stats.PTS?.season_avg != null && (
                  <div className="text-center">
                    <div className="text-lg font-bold text-white">{player.baseline_stats.PTS.season_avg.toFixed(1)}</div>
                    <div className="text-[10px] text-zinc-500">PPG</div>
                  </div>
                )}
                {player.baseline_stats.REB?.season_avg != null && (
                  <div className="text-center">
                    <div className="text-lg font-bold text-white">{player.baseline_stats.REB.season_avg.toFixed(1)}</div>
                    <div className="text-[10px] text-zinc-500">RPG</div>
                  </div>
                )}
                {player.baseline_stats.AST?.season_avg != null && (
                  <div className="text-center">
                    <div className="text-lg font-bold text-white">{player.baseline_stats.AST.season_avg.toFixed(1)}</div>
                    <div className="text-[10px] text-zinc-500">APG</div>
                  </div>
                )}
                {player.baseline_stats.STL?.season_avg != null && (
                  <div className="text-center">
                    <div className="text-lg font-bold text-cyan-400">{player.baseline_stats.STL.season_avg.toFixed(1)}</div>
                    <div className="text-[10px] text-zinc-500">STL</div>
                  </div>
                )}
                {player.baseline_stats.BLK?.season_avg != null && (
                  <div className="text-center">
                    <div className="text-lg font-bold text-cyan-400">{player.baseline_stats.BLK.season_avg.toFixed(1)}</div>
                    <div className="text-[10px] text-zinc-500">BLK</div>
                  </div>
                )}
              </div>
            )}
          </div>
          
          {/* Props count badges */}
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
        
        {/* Remove old badges and vision insight - moved stats to header */}
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
                
                // Sort: DEMON first (by line desc), then STANDARD, then GOBLIN (by line desc)
                const sortedProps = [...categoryProps].sort((a, b) => {
                  // Tier priority: DEMON (0) > STANDARD (1) > GOBLIN (2)
                  const getTierPriority = (p) => {
                    if (p.is_demon) return 0;
                    if (p.is_goblin) return 2;
                    return 1; // STANDARD
                  };
                  
                  const aPriority = getTierPriority(a);
                  const bPriority = getTierPriority(b);
                  
                  if (aPriority !== bPriority) return aPriority - bPriority;
                  
                  // Within same tier, sort by line (desc for DEMON, asc for GOBLIN)
                  if (aPriority === 0) return (b.line || 0) - (a.line || 0); // DEMON: highest first
                  if (aPriority === 2) return (a.line || 0) - (b.line || 0); // GOBLIN: lowest first
                  return 0; // STANDARD: keep as is
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
                          onVisionClick={handleVisionClick}
                          gameLogs={player?.game_logs || []}
                        />
                      ))}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="text-center py-12 text-zinc-500">
                <Target className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p className="text-lg">No available Bets today</p>
                <p className="text-sm mt-1">Check back closer to game time</p>
              </div>
            )}
          </div>
        )}
      </div>
      
      {/* Vision Intel Suite Modal */}
      {showIntelSuite && selectedVisionProp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm">
          <div className="bg-zinc-900 border-2 border-amber-500/50 rounded-xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto shadow-[0_0_50px_rgba(251,191,36,0.3)]">
            {/* Header */}
            <div className="sticky top-0 bg-gradient-to-r from-amber-950 via-yellow-900/50 to-amber-950 px-6 py-4 border-b border-amber-500/30">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Crosshair className="w-8 h-8 text-amber-400 animate-pulse" />
                  <div>
                    <h2 className="text-xl font-black text-amber-300">VISION INTEL SUITE</h2>
                    <p className="text-xs text-amber-400/70">{playerName} • {selectedVisionProp.stat_type || getPropLabel(selectedVisionProp.stat_type_extracted)}</p>
                  </div>
                </div>
                <button 
                  onClick={() => setShowIntelSuite(false)}
                  className="p-2 hover:bg-amber-500/20 rounded-lg transition-colors"
                >
                  <ArrowLeft className="w-5 h-5 text-amber-400" />
                </button>
              </div>
            </div>
            
            {/* Content */}
            <div className="p-6 space-y-6">
              {/* Vision Pick Summary */}
              <div className="bg-gradient-to-r from-amber-950/50 to-zinc-900 border border-amber-500/30 rounded-lg p-4">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-full bg-amber-500/20 flex items-center justify-center">
                      <Crosshair className="w-6 h-6 text-amber-400" />
                    </div>
                    <div>
                      <div className="text-2xl font-black text-amber-300">
                        {(selectedVisionProp.direction || 'OVER').toUpperCase()} {selectedVisionProp.line}
                      </div>
                      <div className="text-sm text-amber-400/70">
                        {getPropLabel(selectedVisionProp.stat_type_extracted || selectedVisionProp.stat_type)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-amber-400/50">ODDS</div>
                    <div className="text-lg font-bold text-amber-300">
                      {selectedVisionProp.price > 0 ? '+' : ''}{selectedVisionProp.price || '-110'}
                    </div>
                  </div>
                </div>
                
                {/* Stats Grid */}
                <div className="grid grid-cols-3 gap-4 pt-4 border-t border-amber-500/20">
                  <div className="text-center">
                    <div className="text-xs text-amber-400/50">L5 AVG</div>
                    <div className="text-xl font-bold text-white">{selectedVisionProp.l5_avg || '-'}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-xs text-amber-400/50">L10 AVG</div>
                    <div className="text-xl font-bold text-white">{selectedVisionProp.l10_avg || '-'}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-xs text-amber-400/50">SEASON AVG</div>
                    <div className="text-xl font-bold text-white">{selectedVisionProp.season_avg || '-'}</div>
                  </div>
                </div>
              </div>
              
              {/* ===== CONTEXT BADGES - 10 Situational Indicators ===== */}
              <div className="bg-gradient-to-r from-zinc-900 to-zinc-800/50 border border-zinc-700 rounded-lg p-4">
                <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
                  <Target className="w-4 h-4 text-amber-400" />
                  CONTEXT BADGES
                </h3>
                <p className="text-xs text-zinc-500 mb-4">Situational factors affecting tonight's performance</p>
                
                {/* Badge Grid - All 11 Badges with Tooltips */}
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(BADGE_REGISTRY).map(([badgeKey, badge]) => {
                    // Check if this badge is active for this player
                    const isActive = selectedVisionProp.active_badges?.includes(badgeKey) || 
                                     selectedVisionProp.intel_suite?.context_badges?.includes(badgeKey);
                    
                    return (
                      <BadgeGridItem 
                        key={badgeKey}
                        badgeKey={badgeKey}
                        isActive={isActive}
                      />
                    );
                  })}
                </div>
                
                {/* Active Badges Summary */}
                {(selectedVisionProp.active_badges?.length > 0 || selectedVisionProp.intel_suite?.context_badges?.length > 0) && (
                  <div className="mt-4 pt-4 border-t border-zinc-700">
                    <div className="text-xs text-amber-400 font-semibold mb-2">
                      ACTIVE FOR {playerName?.toUpperCase()}:
                    </div>
                    <BadgeRow 
                      badges={(selectedVisionProp.active_badges || selectedVisionProp.intel_suite?.context_badges || []).map(b => ({
                        badge_key: typeof b === 'string' ? b : b.badge_key
                      }))}
                      size="md"
                    />
                  </div>
                )}
              </div>
              
              {/* ===== INTEL SUITE ADVANCED METRICS ===== */}
              {selectedVisionProp.intel_suite && (
                <div className="space-y-4">
                  {/* Usage Ripple (Operational Volume) */}
                  <div className="bg-gradient-to-r from-purple-950/40 to-zinc-900 border border-purple-500/30 rounded-lg p-4">
                    <h3 className="text-sm font-bold text-white mb-2 flex items-center gap-2">
                      <Zap className="w-4 h-4 text-purple-400" />
                      OPERATIONAL VOLUME
                    </h3>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-2xl font-bold text-purple-300">
                          {selectedVisionProp.intel_suite.usage_ripple?.display || 'Standard Volume'}
                        </div>
                        <div className="text-xs text-zinc-400 mt-1">
                          {selectedVisionProp.intel_suite.usage_ripple?.reasoning}
                        </div>
                      </div>
                      <div className={`px-3 py-1 rounded-full text-xs font-bold ${
                        selectedVisionProp.intel_suite.usage_ripple?.bump_percent >= 5 
                          ? 'bg-purple-500 text-white' 
                          : selectedVisionProp.intel_suite.usage_ripple?.bump_percent >= 2
                            ? 'bg-purple-500/50 text-purple-200'
                            : 'bg-zinc-700 text-zinc-400'
                      }`}>
                        {selectedVisionProp.intel_suite.usage_ripple?.shift_label}
                      </div>
                    </div>
                    {selectedVisionProp.intel_suite.usage_ripple?.injuries_affecting?.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-purple-500/20">
                        <div className="text-xs text-zinc-500 mb-1">LINEUP IMPACT:</div>
                        {selectedVisionProp.intel_suite.usage_ripple.injuries_affecting.map((inj, i) => (
                          <div key={i} className="text-xs text-purple-300">
                            • {inj.player} ({inj.status}) - {inj.injury}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  
                  {/* Matchup DvP (Defensive Friction) */}
                  <div className="bg-gradient-to-r from-cyan-950/40 to-zinc-900 border border-cyan-500/30 rounded-lg p-4">
                    <h3 className="text-sm font-bold text-white mb-2 flex items-center gap-2">
                      <Target className="w-4 h-4 text-cyan-400" />
                      DEFENSIVE FRICTION
                    </h3>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-2xl font-bold text-cyan-300">
                          {selectedVisionProp.intel_suite.matchup_dvp?.display || '-'}
                        </div>
                        <div className="text-xs text-zinc-400 mt-1">
                          vs {selectedVisionProp.intel_suite.matchup_dvp?.opponent || 'Opponent'}
                        </div>
                      </div>
                      <div className={`px-3 py-1 rounded-full text-xs font-bold ${
                        selectedVisionProp.intel_suite.matchup_dvp?.color === 'green' 
                          ? 'bg-green-500 text-white' 
                          : selectedVisionProp.intel_suite.matchup_dvp?.color === 'red'
                            ? 'bg-red-500 text-white'
                            : 'bg-yellow-500 text-black'
                      }`}>
                        {selectedVisionProp.intel_suite.matchup_dvp?.friction_level} Friction
                      </div>
                    </div>
                    <div className="text-xs text-cyan-400/70 mt-2">
                      {selectedVisionProp.intel_suite.matchup_dvp?.friction_label}
                    </div>
                  </div>
                  
                  {/* Pace Delta (Tempo Multiplier) + Stability Index */}
                  <div className="grid grid-cols-2 gap-4">
                    {/* Pace Delta */}
                    <div className="bg-zinc-800/50 border border-zinc-700 rounded-lg p-4">
                      <h3 className="text-xs font-bold text-zinc-400 mb-2">TEMPO MULTIPLIER</h3>
                      <div className={`text-2xl font-bold ${
                        selectedVisionProp.intel_suite.pace_delta?.possessions >= 2 
                          ? 'text-green-400' 
                          : selectedVisionProp.intel_suite.pace_delta?.possessions <= -2
                            ? 'text-red-400'
                            : 'text-white'
                      }`}>
                        {selectedVisionProp.intel_suite.pace_delta?.display || '-'}
                      </div>
                      <div className="text-xs text-zinc-500 mt-1">
                        {selectedVisionProp.intel_suite.pace_delta?.tempo_label}
                      </div>
                      <div className="text-[10px] text-zinc-600 mt-2">
                        Game Pace: {selectedVisionProp.intel_suite.pace_delta?.expected_game_pace}
                      </div>
                    </div>
                    
                    {/* Stability Index */}
                    <div className="bg-zinc-800/50 border border-zinc-700 rounded-lg p-4">
                      <h3 className="text-xs font-bold text-zinc-400 mb-2">TACTICAL VARIANCE</h3>
                      <div className={`text-2xl font-bold ${
                        selectedVisionProp.intel_suite.stability_index?.score >= 75 
                          ? 'text-green-400' 
                          : selectedVisionProp.intel_suite.stability_index?.score >= 45
                            ? 'text-yellow-400'
                            : 'text-red-400'
                      }`}>
                        {selectedVisionProp.intel_suite.stability_index?.display || '-'}
                      </div>
                      <div className="text-xs text-zinc-500 mt-1">
                        {selectedVisionProp.intel_suite.stability_index?.consistency}
                      </div>
                      {selectedVisionProp.intel_suite.stability_index?.std_dev && (
                        <div className="text-[10px] text-zinc-600 mt-2">
                          Std Dev: {selectedVisionProp.intel_suite.stability_index.std_dev}
                        </div>
                      )}
                    </div>
                  </div>
                  
                  {/* Blowout Risk Warning */}
                  {selectedVisionProp.intel_suite?.blowout_risk?.risk_level && 
                   selectedVisionProp.intel_suite.blowout_risk.risk_level !== 'NONE' &&
                   selectedVisionProp.intel_suite.blowout_risk.risk_level !== 'UNKNOWN' && (
                    <div className={`border rounded-lg p-4 ${
                      selectedVisionProp.intel_suite.blowout_risk.risk_level === 'HIGH'
                        ? 'bg-gradient-to-r from-red-950/50 to-zinc-900 border-red-500/50'
                        : selectedVisionProp.intel_suite.blowout_risk.risk_level === 'MEDIUM'
                          ? 'bg-gradient-to-r from-orange-950/50 to-zinc-900 border-orange-500/40'
                          : 'bg-zinc-800/50 border-zinc-700'
                    }`}>
                      <h3 className={`text-sm font-bold mb-2 flex items-center gap-2 ${
                        selectedVisionProp.intel_suite.blowout_risk.risk_level === 'HIGH'
                          ? 'text-red-400'
                          : selectedVisionProp.intel_suite.blowout_risk.risk_level === 'MEDIUM'
                            ? 'text-orange-400'
                            : 'text-zinc-400'
                      }`}>
                        {selectedVisionProp.intel_suite.blowout_risk.risk_level === 'HIGH' ? '⚠️' : '⚡'} BLOWOUT RISK
                      </h3>
                      <div className="flex items-center justify-between mb-2">
                        <div className="text-sm text-white">
                          {selectedVisionProp.intel_suite.blowout_risk.player_team_record} vs {selectedVisionProp.intel_suite.blowout_risk.opponent_team_record}
                        </div>
                        <div className={`px-2 py-1 rounded text-xs font-bold ${
                          selectedVisionProp.intel_suite.blowout_risk.risk_level === 'HIGH'
                            ? 'bg-red-500 text-white'
                            : selectedVisionProp.intel_suite.blowout_risk.risk_level === 'MEDIUM'
                              ? 'bg-orange-500 text-white'
                              : 'bg-zinc-600 text-white'
                        }`}>
                          {selectedVisionProp.intel_suite.blowout_risk.risk_level} RISK
                        </div>
                      </div>
                      <div className="text-xs text-zinc-400">
                        {selectedVisionProp.intel_suite.blowout_risk.risk_reason}
                      </div>
                      {selectedVisionProp.intel_suite.blowout_risk.warning && (
                        <div className={`mt-2 text-xs font-medium ${
                          selectedVisionProp.intel_suite.blowout_risk.risk_level === 'HIGH'
                            ? 'text-red-400'
                            : 'text-orange-400'
                        }`}>
                          {selectedVisionProp.intel_suite.blowout_risk.warning}
                        </div>
                      )}
                    </div>
                  )}
                  
                  {/* Vision Insight (Target-Lock Rationale) */}
                  <div className="bg-gradient-to-r from-amber-950/50 to-zinc-900 border border-amber-500/30 rounded-lg p-4">
                    <h3 className="text-sm font-bold text-amber-300 mb-2 flex items-center gap-2">
                      <Crosshair className="w-4 h-4 text-amber-400" />
                      TARGET-LOCK RATIONALE
                    </h3>
                    
                    {/* AI Vision Summary - Generated by Gemini */}
                    {(selectedVisionProp.vision_summary || selectedVisionProp.intel_suite.vision_insight?.ai_summary) && (
                      <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 mb-4">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-[10px] font-bold text-amber-400 bg-amber-500/20 px-2 py-0.5 rounded">AI VISION</span>
                        </div>
                        <p className="text-sm text-white leading-relaxed">
                          {selectedVisionProp.vision_summary || selectedVisionProp.intel_suite.vision_insight?.ai_summary}
                        </p>
                      </div>
                    )}
                    
                    <div className="text-sm text-white mb-3">
                      {selectedVisionProp.intel_suite.vision_insight?.primary}
                    </div>
                    {selectedVisionProp.intel_suite.vision_insight?.reasons?.length > 1 && (
                      <div className="space-y-1 mb-3">
                        {selectedVisionProp.intel_suite.vision_insight.reasons.slice(1).map((reason, i) => (
                          <div key={i} className="text-xs text-zinc-400 flex items-center gap-2">
                            <span className="text-amber-400">•</span> {reason}
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center justify-between pt-3 border-t border-amber-500/20">
                      <div className="text-xs text-zinc-500">
                        {selectedVisionProp.intel_suite.vision_insight?.tactical_note}
                      </div>
                      <div className={`px-2 py-1 rounded text-[10px] font-bold ${
                        selectedVisionProp.intel_suite.vision_insight?.confidence === 'High'
                          ? 'bg-green-500 text-white'
                          : selectedVisionProp.intel_suite.vision_insight?.confidence === 'Medium-High'
                            ? 'bg-yellow-500 text-black'
                            : 'bg-zinc-600 text-white'
                      }`}>
                        {selectedVisionProp.intel_suite.vision_insight?.confidence} Confidence
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              {/* Hit Rate Analysis */}
              <div className="bg-zinc-800/50 border border-zinc-700 rounded-lg p-4">
                <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-cyan-400" />
                  HIT RATE ANALYSIS
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-zinc-900/50 rounded-lg p-3">
                    <div className="text-xs text-zinc-500">LAST 10 GAMES</div>
                    <div className="text-2xl font-bold text-green-400">
                      {selectedVisionProp.hit_rates?.l10?.hit_rate != null 
                        ? `${Math.round(selectedVisionProp.hit_rates.l10.hit_rate)}%`
                        : '-'}
                    </div>
                    <div className="text-xs text-zinc-500 mt-1">
                      {selectedVisionProp.hit_rates?.l10?.games_over || 0}/{selectedVisionProp.hit_rates?.l10?.total_games || 0} games over
                    </div>
                  </div>
                  <div className="bg-zinc-900/50 rounded-lg p-3">
                    <div className="text-xs text-zinc-500">LAST 5 GAMES</div>
                    <div className="text-2xl font-bold text-green-400">
                      {selectedVisionProp.hit_rates?.l5?.hit_rate != null 
                        ? `${Math.round(selectedVisionProp.hit_rates.l5.hit_rate)}%`
                        : '-'}
                    </div>
                    <div className="text-xs text-zinc-500 mt-1">
                      {selectedVisionProp.hit_rates?.l5?.games_over || 0}/{selectedVisionProp.hit_rates?.l5?.total_games || 0} games over
                    </div>
                  </div>
                </div>
              </div>
              
              {/* AI Analysis (if available) */}
              {player?.ai_vision && (
                <div className="bg-zinc-800/50 border border-cyan-500/30 rounded-lg p-4">
                  <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
                    <Target className="w-4 h-4 text-cyan-400" />
                    AI TACTICAL ANALYSIS
                  </h3>
                  <p className="text-sm text-zinc-300 leading-relaxed">
                    {player.ai_vision}
                  </p>
                </div>
              )}
              
              {/* Recommendation Badge */}
              <div className="flex justify-center">
                <div className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-amber-500 to-yellow-400 text-black rounded-full font-bold text-sm">
                  <Crosshair className="w-4 h-4" />
                  VISION RECOMMENDS: {(selectedVisionProp.direction || 'OVER').toUpperCase()} {selectedVisionProp.line}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PlayerDetailPage;
