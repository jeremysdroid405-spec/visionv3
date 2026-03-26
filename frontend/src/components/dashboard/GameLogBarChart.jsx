/**
 * GameLogBarChart - PrizePicks-style bar graph showing game outcomes vs line
 * 
 * Bars diverge from the line:
 * - Green bars go UP from line when value > line (hit)
 * - Red bars go DOWN from line when value < line (miss)
 * 
 * Layout:
 * - Values on TOP of bars
 * - Opponent abbreviations at BOTTOM
 */
import React, { memo, useMemo } from 'react';

// BDL Team ID to Abbreviation mapping
const TEAM_ID_TO_ABBR = {
  1: 'ATL', 2: 'BOS', 3: 'BKN', 4: 'CHA', 5: 'CHI',
  6: 'CLE', 7: 'DAL', 8: 'DEN', 9: 'DET', 10: 'GSW',
  11: 'HOU', 12: 'IND', 13: 'LAC', 14: 'LAL', 15: 'MEM',
  16: 'MIA', 17: 'MIL', 18: 'MIN', 19: 'NOP', 20: 'NYK',
  21: 'OKC', 22: 'ORL', 23: 'PHI', 24: 'PHX', 25: 'POR',
  26: 'SAC', 27: 'SAS', 28: 'TOR', 29: 'UTA', 30: 'WAS'
};

// Map stat types to game log fields
const STAT_FIELD_MAP = {
  'PTS': 'pts',
  'REB': 'reb',
  'AST': 'ast',
  'STL': 'stl',
  'BLK': 'blk',
  'FG3M': 'fg3m',
  '3PM': 'fg3m',
  'TO': 'turnover',
  'PRA': ['pts', 'reb', 'ast'],
  'PR': ['pts', 'reb'],
  'PA': ['pts', 'ast'],
  'RA': ['reb', 'ast'],
  'BLST': ['blk', 'stl'],
  'FTM': 'ftm',
  'MIN': 'min',
};

const getStatValue = (game, statType) => {
  const field = STAT_FIELD_MAP[statType?.toUpperCase()];
  if (!field) return null;
  
  if (Array.isArray(field)) {
    return field.reduce((sum, f) => sum + (parseFloat(game[f]) || 0), 0);
  }
  
  if (field === 'min') {
    const min = game[field];
    if (typeof min === 'string' && min.includes(':')) {
      const [mins, secs] = min.split(':').map(Number);
      return mins + (secs / 60);
    }
    return parseFloat(min) || 0;
  }
  
  return parseFloat(game[field]) || 0;
};

const GameLogBarChart = memo(({ 
  gameLogs = [], 
  statType, 
  line, 
  showGames = 10,
  height = 80,
  className = ''
}) => {
  const chartData = useMemo(() => {
    if (!gameLogs || !Array.isArray(gameLogs) || !statType || line === undefined) {
      return null;
    }
    
    const recentGames = gameLogs.slice(0, showGames);
    
    const values = recentGames.map(game => {
      const oppId = game.opponent_team_id;
      const oppAbbr = TEAM_ID_TO_ABBR[oppId] || game.opponent || '???';
      
      return {
        value: getStatValue(game, statType),
        opponent: oppAbbr,
        isHome: game.home_game
      };
    }).filter(v => v.value !== null);
    
    if (values.length === 0) return null;
    
    // Find the range for scaling
    const allValues = values.map(v => v.value);
    const maxValue = Math.max(...allValues, line);
    const minValue = Math.min(...allValues, line);
    
    // Add padding above and below
    const range = maxValue - minValue;
    const padding = Math.max(range * 0.2, 2);
    const chartMax = maxValue + padding;
    const chartMin = Math.max(0, minValue - padding);
    
    const hits = values.filter(v => v.value >= line).length;
    const hitRate = Math.round((hits / values.length) * 100);
    
    return {
      values: values.reverse(), // Oldest first for left-to-right
      chartMax,
      chartMin,
      line,
      hits,
      total: values.length,
      hitRate
    };
  }, [gameLogs, statType, line, showGames]);
  
  if (!chartData) {
    return (
      <div className={`text-zinc-500 text-xs text-center py-2 ${className}`}>
        No game data
      </div>
    );
  }
  
  const { values, chartMax, chartMin, hits, total, hitRate } = chartData;
  const chartRange = chartMax - chartMin;
  const linePosition = ((line - chartMin) / chartRange) * 100;
  
  return (
    <div className={`relative ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-1 px-1">
        <span className="text-[10px] text-zinc-500 uppercase tracking-wide">
          L{total}
        </span>
        <span className={`text-[10px] font-bold ${hitRate >= 70 ? 'text-emerald-400' : hitRate >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
          {hits}/{total} ({hitRate}%)
        </span>
      </div>
      
      {/* Chart container */}
      <div 
        className="relative bg-zinc-900/50 rounded border border-zinc-800"
        style={{ height: `${height}px` }}
      >
        {/* Line indicator (horizontal) */}
        <div 
          className="absolute left-0 right-0 border-t-2 border-dashed border-amber-500/80 z-10"
          style={{ bottom: `${linePosition}%` }}
        >
          <span className="absolute -right-1 -top-2.5 text-[9px] text-amber-400 font-mono bg-zinc-900/90 px-1 rounded">
            {line}
          </span>
        </div>
        
        {/* Bars container */}
        <div className="absolute inset-0 flex items-stretch justify-around px-1">
          {values.map((item, idx) => {
            const isHit = item.value >= line;
            const valuePosition = ((item.value - chartMin) / chartRange) * 100;
            
            // Bar goes from line to value
            const barBottom = isHit ? linePosition : valuePosition;
            const barTop = isHit ? valuePosition : linePosition;
            const barHeight = Math.abs(barTop - barBottom);
            
            return (
              <div 
                key={idx}
                className="relative flex flex-col items-center justify-end h-full"
                style={{ width: `${100 / values.length - 1}%` }}
              >
                {/* Value label on top of bar */}
                <div 
                  className="absolute text-[9px] font-bold z-20"
                  style={{ 
                    bottom: `${Math.max(valuePosition, linePosition) + 2}%`,
                  }}
                >
                  <span className={isHit ? 'text-emerald-400' : 'text-red-400'}>
                    {Math.round(item.value)}
                  </span>
                </div>
                
                {/* The bar */}
                <div 
                  className={`absolute w-[80%] rounded-sm transition-all ${
                    isHit 
                      ? 'bg-gradient-to-t from-emerald-600 to-emerald-400' 
                      : 'bg-gradient-to-b from-red-600 to-red-400'
                  }`}
                  style={{ 
                    bottom: `${barBottom}%`,
                    height: `${Math.max(barHeight, 2)}%`,
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>
      
      {/* Opponent abbreviations at bottom */}
      <div className="flex justify-around px-1 mt-1">
        {values.map((item, idx) => (
          <span 
            key={idx} 
            className="text-[8px] text-zinc-500 font-medium"
            style={{ width: `${100 / values.length}%`, textAlign: 'center' }}
          >
            {item.opponent}
          </span>
        ))}
      </div>
    </div>
  );
});

GameLogBarChart.displayName = 'GameLogBarChart';

export default GameLogBarChart;
