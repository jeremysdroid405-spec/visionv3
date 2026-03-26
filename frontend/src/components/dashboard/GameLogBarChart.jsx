/**
 * GameLogBarChart - PrizePicks-style bar graph showing game outcomes vs line
 * 
 * All bars grow upward from bottom:
 * - GREEN bars bust THROUGH the line (value >= line)
 * - RED bars stop SHORT of the line with a gap (value < line)
 * 
 * Layout:
 * - Values on TOP of bars
 * - Opponent abbreviations at BOTTOM
 * - Constant horizontal line
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
  className = '',
  l5Avg = null,
  l10Avg = null,
  seasonAvg = null
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
    
    // Find max value for scaling (include line, averages and add padding)
    const allRelevantValues = [
      ...values.map(v => v.value), 
      line,
      l5Avg, 
      l10Avg, 
      seasonAvg
    ].filter(v => v != null && v > 0);
    const maxValue = Math.max(...allRelevantValues);
    const chartMax = maxValue * 1.15; // 15% padding above highest bar
    
    const hits = values.filter(v => v.value >= line).length;
    const hitRate = Math.round((hits / values.length) * 100);
    
    return {
      values: values.reverse(), // Oldest first for left-to-right
      chartMax,
      line,
      hits,
      total: values.length,
      hitRate
    };
  }, [gameLogs, statType, line, showGames, l5Avg, l10Avg, seasonAvg]);
  
  if (!chartData) {
    return (
      <div className={`text-zinc-500 text-xs text-center py-2 ${className}`}>
        No game data
      </div>
    );
  }
  
  const { values, chartMax, hits, total, hitRate } = chartData;
  // Line position as percentage from bottom
  const linePosition = (line / chartMax) * 100;
  
  // Averages are now displayed as text above the chart (no reference lines)
  
  return (
    <div className={`relative ${className}`}>
      {/* Header with averages as text */}
      <div className="flex items-center justify-between mb-1 px-1">
        <div className="flex items-center gap-3">
          <span className={`text-[10px] font-bold ${hitRate >= 70 ? 'text-emerald-400' : hitRate >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
            {hits}/{total}
          </span>
          {seasonAvg != null && (
            <span className="text-[10px] text-cyan-400 font-medium">
              SZN: <span className="font-bold">{seasonAvg}</span>
            </span>
          )}
          {l10Avg != null && (
            <span className="text-[10px] text-purple-400 font-medium">
              L10: <span className="font-bold">{l10Avg}</span>
            </span>
          )}
          {l5Avg != null && (
            <span className="text-[10px] text-pink-400 font-medium">
              L5: <span className="font-bold">{l5Avg}</span>
            </span>
          )}
        </div>
      </div>
      
      {/* Chart container - no reference lines inside, just the target line */}
      <div 
        className="relative bg-zinc-900/50 rounded border border-zinc-800"
        style={{ height: `${height}px` }}
      >
        {/* Main target line (amber, solid) */}
        <div 
          className="absolute left-0 right-0 border-t-2 border-amber-500 z-10"
          style={{ bottom: `${linePosition}%` }}
        >
          <span className="absolute -right-1 -top-2.5 text-[9px] text-amber-400 font-bold bg-zinc-900/90 px-1 rounded">
            {line}
          </span>
        </div>
        
        {/* Bars - all grow upward from bottom */}
        <div className="absolute inset-x-1 bottom-0 top-3 flex items-end justify-around">
          {values.map((item, idx) => {
            const isHit = item.value >= line;
            const barHeight = (item.value / chartMax) * 100;
            
            return (
              <div 
                key={idx}
                className="relative flex flex-col items-center h-full justify-end"
                style={{ width: `${90 / values.length}%` }}
              >
                {/* Value label on top of bar */}
                <div 
                  className={`absolute text-[9px] font-bold z-20 ${isHit ? 'text-emerald-400' : 'text-red-400'}`}
                  style={{ 
                    bottom: `${barHeight + 2}%`,
                  }}
                >
                  {Math.round(item.value)}
                </div>
                
                {/* The bar - grows from bottom */}
                <div 
                  className={`w-full rounded-t transition-all ${
                    isHit 
                      ? 'bg-emerald-500' 
                      : 'bg-red-500'
                  }`}
                  style={{ 
                    height: `${barHeight}%`,
                    minHeight: '3px'
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
            className="text-[8px] text-zinc-400 font-medium"
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
