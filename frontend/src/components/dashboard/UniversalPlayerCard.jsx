/**
 * UNIVERSAL PLAYER CARD
 * =====================
 * Single source of truth for ALL player profile displays across the app.
 * 
 * DATA SOURCES:
 * - nba_master_hub_2026 (Master Vault): Player info, photos, BDL stats
 * - dg_cached_board (Odds API): Lines, odds, props
 * 
 * FEATURES:
 * - BDL Vault Stats (FG%, 3P%, STL, BLK, etc.)
 * - Hit rate displays (L5, L10, Season)
 * - Multiple props display
 * - Search result compatible
 * 
 * DISPLAY MODES:
 * - "full": Complete card with all stats and props
 * - "compact": Condensed view for search results
 * - "mini": Minimal inline view
 * 
 * USED IN:
 * - CommandPost.jsx (Player search results & slate)
 * - CommandSearch.jsx (Search dropdown)
 * - PlayerDetailPage.jsx (Player profile header)
 */

import React, { memo, useCallback, useState } from 'react';
import { 
  Target, Shield, ChevronRight, Plus, ChevronDown,
  Crosshair, TrendingUp, User
} from 'lucide-react';
import { Badge } from '../ui/badge';

// ==================== CONSTANTS ====================

const TEAM_LOGOS = {
  ATL: 'https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg',
  BOS: 'https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg',
  BKN: 'https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg',
  CHA: 'https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg',
  CHI: 'https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg',
  CLE: 'https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg',
  DAL: 'https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg',
  DEN: 'https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg',
  DET: 'https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg',
  GSW: 'https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg',
  HOU: 'https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg',
  IND: 'https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg',
  LAC: 'https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg',
  LAL: 'https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg',
  MEM: 'https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg',
  MIA: 'https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg',
  MIL: 'https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg',
  MIN: 'https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg',
  NOP: 'https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg',
  NYK: 'https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg',
  OKC: 'https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg',
  ORL: 'https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg',
  PHI: 'https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg',
  PHX: 'https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg',
  POR: 'https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg',
  SAC: 'https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg',
  SAS: 'https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg',
  TOR: 'https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg',
  UTA: 'https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg',
  WAS: 'https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg',
};

const STAT_LABELS = {
  'PTS': 'Points', 'REB': 'Rebounds', 'AST': 'Assists',
  '3PM': '3-Pointers', 'STL': 'Steals', 'BLK': 'Blocks',
  'TO': 'Turnovers', 'PRA': 'Pts+Reb+Ast', 'PR': 'Pts+Reb',
  'P+R': 'Pts+Reb', 'PA': 'Pts+Ast', 'P+A': 'Pts+Ast',
  'RA': 'Reb+Ast', 'R+A': 'Reb+Ast', 'MIN': 'Minutes',
};

// ==================== HELPER FUNCTIONS ====================

const getHitRateColor = (rate) => {
  if (rate >= 80) return 'text-green-400';
  if (rate >= 60) return 'text-yellow-400';
  if (rate >= 40) return 'text-orange-400';
  return 'text-red-400';
};

const getInitials = (name) => {
  if (!name) return '??';
  return name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
};

// ==================== SUB-COMPONENTS ====================

// Player Headshot with fallback
const PlayerHeadshot = memo(({ photoUrl, playerName, team, size = 'md' }) => {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-10 h-10',
    lg: 'w-14 h-14',
    xl: 'w-20 h-20'
  };
  
  const [imgError, setImgError] = useState(false);
  
  if (!photoUrl || imgError) {
    const teamLogo = TEAM_LOGOS[team];
    if (teamLogo) {
      return (
        <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1`}>
          <img src={teamLogo} alt={team} className="w-full h-full object-contain" />
        </div>
      );
    }
    return (
      <div className={`${sizeClasses[size]} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 flex items-center justify-center`}>
        <span className="text-zinc-400 font-bold text-xs">{getInitials(playerName)}</span>
      </div>
    );
  }
  
  return (
    <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800`}>
      <img 
        src={photoUrl} 
        alt={playerName}
        className="w-full h-full object-cover"
        style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }}
        onError={() => setImgError(true)}
      />
    </div>
  );
});
PlayerHeadshot.displayName = 'PlayerHeadshot';

// BDL Vault Stats Row - The detailed stats from Master Vault
const VaultStatsRow = memo(({ player }) => {
  // Support both direct stats and nested baseline_stats structure
  const stats = player.baseline_stats || player;
  
  // Get raw values
  const fg_pct_raw = stats.fg_pct ?? player.fg_pct;
  const fg3_pct_raw = stats.fg3_pct ?? player.fg3_pct;
  const stl = stats.stl ?? player.stl;
  const blk = stats.blk ?? player.blk;
  const pts = stats.pts ?? player.pts;
  const reb = stats.reb ?? player.reb;
  const ast = stats.ast ?? player.ast;
  
  // Format percentages (API returns 0.513, we want 51.3)
  const formatPct = (val) => {
    if (val == null) return null;
    // If already a percentage > 1, show as is
    if (val > 1) return val.toFixed(1);
    // Otherwise convert from decimal
    return (val * 100).toFixed(1);
  };
  
  const fg_pct = formatPct(fg_pct_raw);
  const fg3_pct = formatPct(fg3_pct_raw);
  
  const hasStats = fg_pct != null || fg3_pct != null || stl != null || blk != null || pts != null;
  if (!hasStats) return null;
  
  return (
    <div className="bg-zinc-800/50 rounded-lg p-2 border border-zinc-700/40" data-testid="vault-stats">
      <div className="text-[9px] text-zinc-500 uppercase tracking-wider mb-1.5 font-semibold">Season Stats</div>
      <div className="grid grid-cols-5 gap-2 text-center">
        {pts != null && (
          <div>
            <div className="text-[9px] text-zinc-500">PTS</div>
            <div className="text-sm font-bold text-white">{typeof pts === 'number' ? pts.toFixed(1) : pts}</div>
          </div>
        )}
        {reb != null && (
          <div>
            <div className="text-[9px] text-zinc-500">REB</div>
            <div className="text-sm font-bold text-white">{typeof reb === 'number' ? reb.toFixed(1) : reb}</div>
          </div>
        )}
        {ast != null && (
          <div>
            <div className="text-[9px] text-zinc-500">AST</div>
            <div className="text-sm font-bold text-white">{typeof ast === 'number' ? ast.toFixed(1) : ast}</div>
          </div>
        )}
        {fg_pct != null && (
          <div>
            <div className="text-[9px] text-zinc-500">FG%</div>
            <div className="text-sm font-bold text-cyan-400">{fg_pct}%</div>
          </div>
        )}
        {fg3_pct != null && (
          <div>
            <div className="text-[9px] text-zinc-500">3P%</div>
            <div className="text-sm font-bold text-purple-400">{fg3_pct}%</div>
          </div>
        )}
      </div>
      {(stl != null || blk != null) && (
        <div className="grid grid-cols-2 gap-2 text-center mt-2 pt-2 border-t border-zinc-700/30">
          {stl != null && (
            <div>
              <div className="text-[9px] text-zinc-500">STL</div>
              <div className="text-sm font-bold text-green-400">{typeof stl === 'number' ? stl.toFixed(1) : stl}</div>
            </div>
          )}
          {blk != null && (
            <div>
              <div className="text-[9px] text-zinc-500">BLK</div>
              <div className="text-sm font-bold text-amber-400">{typeof blk === 'number' ? blk.toFixed(1) : blk}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
VaultStatsRow.displayName = 'VaultStatsRow';

// Position Badge
const PositionBadge = memo(({ position }) => {
  if (!position) return null;
  return (
    <span className="px-1.5 py-0.5 text-[9px] font-bold bg-zinc-700/50 text-zinc-300 rounded">
      {position}
    </span>
  );
});
PositionBadge.displayName = 'PositionBadge';

// ==================== MAIN COMPONENT ====================

/**
 * UniversalPlayerCard - Single card component for all player displays
 * 
 * @param {Object} player - Player data from API (from Master Vault)
 * @param {string} mode - Display mode: 'full' | 'compact' | 'mini'
 * @param {Function} onClick - Click handler
 * @param {Function} onAddToPost - Add to Command Post handler
 * @param {boolean} showStats - Show BDL vault stats
 * @param {Array} props - Available props for this player (optional)
 */
const UniversalPlayerCard = memo(({
  player,
  mode = 'full',
  onClick,
  onAddToPost,
  showStats = true,
  props = []
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  
  const handleClick = useCallback(() => {
    if (player) onClick?.(player);
  }, [onClick, player]);
  
  const handleAddToPost = useCallback((e, prop) => {
    e?.stopPropagation();
    if (onAddToPost) {
      onAddToPost(prop || player);
    }
  }, [onAddToPost, player]);
  
  if (!player) return null;
  
  // Extract player data - support multiple data shapes
  const {
    player_name,
    name,
    team,
    position,
    photo_url,
    headshot_url,
    opponent,
    // Stats from Master Vault
    baseline_stats,
    fg_pct,
    fg3_pct,
    stl,
    blk,
    pts,
    reb,
    ast
  } = player;
  
  const displayName = player_name || name;
  const displayPhoto = photo_url || headshot_url;
  const playerSlug = displayName?.replace(/\s+/g, '-').toLowerCase();
  const playerProps = props.length > 0 ? props : (player.props || []);
  const hasProps = playerProps.length > 0;
  
  // ==================== MINI MODE ====================
  if (mode === 'mini') {
    return (
      <div 
        className="flex items-center gap-2 p-2 rounded-lg border border-zinc-700/50 bg-zinc-900/50 cursor-pointer hover:bg-zinc-800/50 transition-all"
        onClick={handleClick}
        data-testid={`player-mini-${playerSlug}`}
      >
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-white truncate">{displayName}</div>
          <div className="flex items-center gap-1 text-[10px] text-zinc-500">
            <span>{team}</span>
            {position && <PositionBadge position={position} />}
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-zinc-600" />
      </div>
    );
  }
  
  // ==================== COMPACT MODE (Search Results) ====================
  if (mode === 'compact') {
    return (
      <div 
        className="flex items-center gap-3 p-3 rounded-lg border border-zinc-700/50 bg-gradient-to-br from-zinc-800/80 to-zinc-900 cursor-pointer hover:border-cyan-500/50 hover:bg-zinc-800/80 transition-all"
        onClick={handleClick}
        data-testid={`player-compact-${playerSlug}`}
      >
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="md" />
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-bold text-white text-sm truncate">{displayName}</span>
            <span className="text-[10px] text-zinc-500">{team}</span>
            {position && <PositionBadge position={position} />}
          </div>
          
          {/* Compact stats row */}
          {showStats && (baseline_stats || pts != null) && (
            <div className="flex items-center gap-3 mt-1 text-[10px]">
              {(baseline_stats?.pts ?? pts) != null && (
                <span className="text-zinc-400">
                  <span className="text-white font-medium">{(baseline_stats?.pts ?? pts)?.toFixed?.(1) || pts}</span> PPG
                </span>
              )}
              {(baseline_stats?.reb ?? reb) != null && (
                <span className="text-zinc-400">
                  <span className="text-white font-medium">{(baseline_stats?.reb ?? reb)?.toFixed?.(1) || reb}</span> RPG
                </span>
              )}
              {(baseline_stats?.ast ?? ast) != null && (
                <span className="text-zinc-400">
                  <span className="text-white font-medium">{(baseline_stats?.ast ?? ast)?.toFixed?.(1) || ast}</span> APG
                </span>
              )}
            </div>
          )}
        </div>
        
        {onAddToPost && (
          <button
            onClick={(e) => handleAddToPost(e)}
            className="flex-shrink-0 w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
            data-testid={`add-player-${playerSlug}`}
          >
            <Plus className="w-4 h-4" />
          </button>
        )}
      </div>
    );
  }
  
  // ==================== FULL MODE (Default) ====================
  return (
    <div 
      className="rounded-xl border border-zinc-700/50 bg-gradient-to-b from-zinc-800/80 to-zinc-900 overflow-hidden"
      data-testid={`player-card-${playerSlug}`}
    >
      {/* Header */}
      <div 
        className="p-4 cursor-pointer hover:bg-zinc-800/50 transition-all"
        onClick={hasProps ? () => setIsExpanded(!isExpanded) : handleClick}
      >
        <div className="flex items-center gap-4">
          {/* Player Photo */}
          <div className="relative flex-shrink-0">
            <div className="ring-2 ring-zinc-600 rounded-full">
              <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="lg" />
            </div>
          </div>
          
          {/* Player Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-lg font-bold text-white truncate">{displayName}</h3>
              {position && <PositionBadge position={position} />}
            </div>
            <div className="flex items-center gap-2 text-xs text-zinc-400 mt-0.5">
              <span className="font-mono">{team}</span>
              {opponent && (
                <>
                  <span className="text-zinc-600">vs</span>
                  <span className="font-bold text-zinc-300">{opponent}</span>
                </>
              )}
            </div>
          </div>
          
          {/* Actions */}
          <div className="flex items-center gap-2">
            {onAddToPost && (
              <button
                onClick={(e) => handleAddToPost(e)}
                className="w-9 h-9 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
                title="Add to Command Post"
              >
                <Plus className="w-5 h-5" />
              </button>
            )}
            {hasProps && (
              <div className={`transform transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                <ChevronDown className="w-5 h-5 text-zinc-400" />
              </div>
            )}
          </div>
        </div>
        
        {/* BDL Vault Stats */}
        {showStats && (
          <div className="mt-3">
            <VaultStatsRow player={player} />
          </div>
        )}
      </div>
      
      {/* Props List (Expandable) */}
      {hasProps && isExpanded && (
        <div className="border-t border-zinc-700/50 p-3 space-y-2 max-h-80 overflow-y-auto">
          <div className="flex items-center gap-2 text-xs text-zinc-500 mb-2">
            <Target className="w-3.5 h-3.5" />
            <span className="uppercase tracking-wider font-semibold">Available Props</span>
            <span className="text-zinc-600">({playerProps.length})</span>
          </div>
          
          {playerProps.map((prop, idx) => {
            const isDemon = prop.is_demon;
            const isGoblin = prop.is_goblin;
            
            return (
              <div 
                key={`${prop.stat_type}-${prop.line}-${idx}`}
                className={`flex items-center justify-between p-2.5 rounded-lg cursor-pointer transition-all ${
                  isDemon ? 'bg-red-950/40 border border-red-500/30 hover:bg-red-950/60' :
                  isGoblin ? 'bg-green-950/40 border border-green-500/30 hover:bg-green-950/60' :
                  'bg-zinc-800/40 border border-zinc-700/30 hover:bg-zinc-700/40'
                }`}
                onClick={(e) => {
                  e.stopPropagation();
                  onClick?.({ ...player, ...prop });
                }}
              >
                <div className="flex items-center gap-2">
                  {isDemon && <Target className="w-4 h-4 text-red-400" />}
                  {isGoblin && <Crosshair className="w-4 h-4 text-green-400" />}
                  {!isDemon && !isGoblin && <TrendingUp className="w-4 h-4 text-zinc-400" />}
                  <div>
                    <span className="text-sm font-medium text-white">
                      {prop.stat_type} <span className={isDemon ? 'text-red-400' : isGoblin ? 'text-green-400' : 'text-zinc-300'}>O{prop.line}</span>
                    </span>
                    {prop.tier_label && prop.tier_label !== 'STANDARD' && (
                      <Badge variant="outline" className="ml-2 text-[8px] px-1 py-0">
                        {prop.tier_label}
                      </Badge>
                    )}
                  </div>
                </div>
                
                <div className="flex items-center gap-3 text-xs">
                  {prop.h10_rate != null && (
                    <span className={`font-medium ${getHitRateColor(prop.h10_rate)}`}>
                      {prop.h10_rate}%
                    </span>
                  )}
                  {prop.season_avg != null && (
                    <span className="text-zinc-400">
                      Avg: <span className="text-white">{prop.season_avg?.toFixed?.(1) || prop.season_avg}</span>
                    </span>
                  )}
                  {onAddToPost && (
                    <button
                      onClick={(e) => handleAddToPost(e, { ...player, ...prop })}
                      className="p-1 rounded bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30"
                    >
                      <Plus className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

UniversalPlayerCard.displayName = 'UniversalPlayerCard';

export { UniversalPlayerCard, PlayerHeadshot, VaultStatsRow, PositionBadge };
export default UniversalPlayerCard;
