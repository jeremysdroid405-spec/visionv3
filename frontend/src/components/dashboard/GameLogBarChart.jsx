/**
 * GameLogBarChart - PrizePicks-style bar graph showing game outcomes vs line
 * 
 * Displays last 5 or last 10 games as bars with the line as a reference
 * Green bars = hit (over line), Red bars = miss (under line)
 * Shows opponent abbreviation and stat value below each bar
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
  'PRA': ['pts', 'reb', 'ast'],  // Combined stats
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
  
  // Handle combined stats (PRA, PR, etc.)
  if (Array.isArray(field)) {
    return field.reduce((sum, f) => sum + (parseFloat(game[f]) || 0), 0);
  }
  
  // Handle minutes (can be string like "24:30")
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
  showGames = 10,  // 5 or 10
  height = 80,
  className = ''
}) => {
  // Get the relevant games and values
  const chartData = useMemo(() => {
    if (!gameLogs || !Array.isArray(gameLogs) || !statType || line === undefined) {
      return null;
    }
    
    // Take the most recent games (already sorted by date desc)
    const recentGames = gameLogs.slice(0, showGames);
    
    // Extract stat values with opponent info
    const values = recentGames.map(game => {
      const oppId = game.opponent_team_id;
      const oppAbbr = TEAM_ID_TO_ABBR[oppId] || game.opponent || '???';
      
      return {
        value: getStatValue(game, statType),
        date: game.date,
        opponent: oppAbbr,
        isHome: game.home_game
      };
    }).filter(v => v.value !== null);
    
    if (values.length === 0) return null;
    
    // Calculate max for scaling (at least 20% higher than line or max value)
    const maxValue = Math.max(...values.map(v => v.value), line);
    const chartMax = Math.max(maxValue * 1.2, line * 1.3);
    
    // Count hits
    const hits = values.filter(v => v.value >= line).length;
    const hitRate = Math.round((hits / values.length) * 100);
    
    return {
      values: values.reverse(), // Oldest first for left-to-right display
      maxValue,
      chartMax,
      line,
      hits,
      total: values.length,
      hitRate
    };
  }, [gameLogs, statType, line, showGames]);
  
  if (!chartData) {
    return (
      <div className={`text-zinc-500 text-xs text-center py-2 ${className}`}>
        No game data available
      </div>
    );
  }
  
  const { values, chartMax, hits, total, hitRate } = chartData;
  const barWidth = Math.max(12, Math.floor(100 / values.length) - 2);
  const linePosition = (line / chartMax) * 100;
  
  return (
    <div className={`relative ${className}`}>
      {/* Header with hit rate */}
      <div className="flex items-center justify-between mb-1 px-1">
        <span className="text-[10px] text-zinc-500 uppercase tracking-wide">
          L{total} Games
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
        {/* Line indicator */}
        <div 
          className="absolute left-0 right-0 border-t-2 border-dashed border-amber-500/70 z-10"
          style={{ bottom: `${linePosition}%` }}
        >
          <span className="absolute -right-1 -top-3 text-[9px] text-amber-400 font-mono bg-zinc-900 px-1 rounded">
            {line}
          </span>
        </div>
        
        {/* Bars */}
        <div className="absolute inset-0 flex items-end justify-around px-1 pb-1">
          {values.map((item, idx) => {
            const barHeight = (item.value / chartMax) * 100;
            const isHit = item.value >= line;
            
            return (
              <div 
                key={idx}
                className="relative group flex flex-col items-center"
                style={{ width: `${barWidth}%` }}
              >
                {/* Bar */}
                <div 
                  className={`w-full rounded-t transition-all duration-200 ${
                    isHit 
                      ? 'bg-gradient-to-t from-emerald-600 to-emerald-400' 
                      : 'bg-gradient-to-t from-red-600 to-red-400'
                  } group-hover:opacity-80`}
                  style={{ 
                    height: `${Math.max(barHeight, 5)}%`,
                    minHeight: '4px'
                  }}
                />
                
                {/* Value tooltip on hover */}
                <div className="absolute bottom-full mb-1 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-20">
                  <div className="bg-zinc-800 border border-zinc-700 rounded px-1.5 py-0.5 text-[10px] text-white font-mono whitespace-nowrap shadow-lg">
                    {item.value.toFixed(1)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        
        {/* Opponent & Value labels at bottom */}
        <div className="absolute bottom-0 left-0 right-0 flex justify-around px-1 transform translate-y-full pt-0.5">
          {values.map((item, idx) => {
            const isHit = item.value >= line;
            return (
              <div 
                key={idx} 
                className="flex flex-col items-center" 
                style={{ width: `${barWidth}%` }}
              >
                <span className={`text-[8px] font-bold ${isHit ? 'text-emerald-400' : 'text-red-400'}`}>
                  {Math.round(item.value)}
                </span>
                <span className="text-[7px] text-zinc-500">
                  {item.isHome ? 'vs' : '@'}{item.opponent}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
});

GameLogBarChart.displayName = 'GameLogBarChart';

export default GameLogBarChart;
