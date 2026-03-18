/**
 * UNIVERSAL PLAYER CARD
 * =====================
 * THE SINGLE CARD COMPONENT FOR THE ENTIRE APP.
 * 
 * ARCHITECTURE: TWO-FUNNEL JOIN
 * =============================
 * FUNNEL 1 - VAULT (nba_master_hub_2026):
 *   - Player Identity: Name, Team, Headshot URL
 *   - Season Stats: PTS, REB, AST, FG%, 3P%, STL, BLK
 *   - Source: BallDontLie API (synced daily via CRON)
 * 
 * FUNNEL 2 - ODDS (dg_cached_board):
 *   - Active Props: All PrizePicks lines for the player
 *   - Tier Classification: DEMON, GOBLIN, or STANDARD
 *   - Source: Odds API (polled every 30 seconds)
 * 
 * CARD BEHAVIOR:
 * ==============
 * - HEADER: Headshot + Name + Season Stats (FG%, 3P%, STL, BLK)
 * - BODY: All available props for that player
 * - GLOW: Card border/glow matches HIGHEST tier (DEMON > GOBLIN > STANDARD)
 * 
 * USED IN:
 * ========
 * - War Zone section
 * - Safe Haven section
 * - Front Lines section
 * - Command Post search results
 * - Global Intel Search results
 * 
 * NO OTHER CARD COMPONENTS SHOULD EXIST.
 */

import React, { memo, useCallback, useState } from 'react';
import { 
  Target, Shield, ChevronRight, Plus, ChevronDown,
  Crosshair, TrendingUp, HeartPulse
} from 'lucide-react';
import { Badge } from '../ui/badge';

// ==================== TEAM LOGOS (FALLBACK) ====================
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

// ==================== TIER ICONS (SVG) ====================
const DemonIcon = ({ size = 16, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" fillOpacity="0"/>
    <path d="M5 3L3 1M19 3L21 1"/>
    <circle cx="8.5" cy="10" r="1.5"/>
    <circle cx="15.5" cy="10" r="1.5"/>
    <path d="M12 2C7 2 3 6 3 11c0 3 1.5 5.5 4 7l1-2c-1.5-1-2.5-2.5-2.5-4.5 0-3.5 3-6.5 6.5-6.5s6.5 3 6.5 6.5c0 2-1 3.5-2.5 4.5l1 2c2.5-1.5 4-4 4-7 0-5-4-9-9-9z"/>
    <path d="M8 16c0 0 2 2 4 2s4-2 4-2"/>
  </svg>
);

const GoblinIcon = ({ size = 16, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className}>
    <ellipse cx="12" cy="13" rx="8" ry="7"/>
    <circle cx="9" cy="11" r="1.5" fill="black"/>
    <circle cx="15" cy="11" r="1.5" fill="black"/>
    <ellipse cx="12" cy="15" rx="2" ry="1" fill="black"/>
    <path d="M6 8Q4 6 5 4M18 8Q20 6 19 4"/>
  </svg>
);

// ==================== THEME CONFIG (TIER-BASED GLOW) ====================
const TIER_THEMES = {
  DEMON: {
    border: 'border-red-500/50',
    bg: 'from-red-950/60 to-zinc-900',
    glow: 'shadow-[0_0_25px_rgba(239,68,68,0.4)]',
    text: 'text-red-400',
    accent: 'bg-red-500',
    ring: 'ring-red-500/50',
    Icon: DemonIcon
  },
  GOBLIN: {
    border: 'border-green-500/50',
    bg: 'from-green-950/60 to-zinc-900',
    glow: 'shadow-[0_0_25px_rgba(34,197,94,0.4)]',
    text: 'text-green-400',
    accent: 'bg-green-500',
    ring: 'ring-green-500/50',
    Icon: GoblinIcon
  },
  FRONT_LINE: {
    border: 'border-yellow-500/50',
    bg: 'from-yellow-950/60 to-zinc-900',
    glow: 'shadow-[0_0_25px_rgba(234,179,8,0.4)]',
    text: 'text-yellow-400',
    accent: 'bg-yellow-500',
    ring: 'ring-yellow-500/50',
    Icon: null // Icon determined by actual pick type (DEMON/GOBLIN)
  },
  STANDARD: {
    border: 'border-zinc-500/40',
    bg: 'from-zinc-800/40 to-zinc-900',
    glow: 'shadow-[0_0_15px_rgba(161,161,170,0.2)]',
    text: 'text-zinc-400',
    accent: 'bg-zinc-500',
    ring: 'ring-zinc-500/50',
    Icon: null
  }
};

// ==================== HELPER FUNCTIONS ====================

const getInitials = (name) => {
  if (!name) return '??';
  return name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
};

const getHitRateColor = (rate) => {
  if (rate >= 80) return 'text-green-400';
  if (rate >= 60) return 'text-yellow-400';
  if (rate >= 40) return 'text-orange-400';
  return 'text-red-400';
};

/**
 * Determine the highest tier from all props
 * Priority: DEMON > FRONT_LINE > GOBLIN > STANDARD
 * FRONT_LINE gets yellow card theme, but icons are still DEMON/GOBLIN colored
 */
const getHighestTier = (props) => {
  if (!props || props.length === 0) return 'STANDARD';
  if (props.some(p => p.is_demon || p.tier_label === 'DEMON')) return 'DEMON';
  if (props.some(p => p.tier_label === 'FRONT_LINE' || p.front_line_qualified)) return 'FRONT_LINE';
  if (props.some(p => p.is_goblin || p.tier_label === 'GOBLIN')) return 'GOBLIN';
  return 'STANDARD';
};

/**
 * Format BDL percentage stats
 * API returns 0.513 -> display as 51.3%
 */
const formatPct = (val) => {
  if (val == null) return null;
  if (val > 1) return val.toFixed(1);
  return (val * 100).toFixed(1);
};

// ==================== SUB-COMPONENTS ====================

/**
 * Player Headshot with team logo fallback
 */
const PlayerHeadshot = memo(({ photoUrl, playerName, team, size = 'md' }) => {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-12 h-12',
    lg: 'w-14 h-14',
    xl: 'w-20 h-20'
  };
  
  const [imgError, setImgError] = useState(false);
  
  if (!photoUrl || imgError) {
    const teamLogo = TEAM_LOGOS[team];
    if (teamLogo) {
      return (
        <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1.5`}>
          <img src={teamLogo} alt={team} className="w-full h-full object-contain" onError={(e) => e.target.style.display = 'none'} />
        </div>
      );
    }
    return (
      <div className={`${sizeClasses[size]} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 flex items-center justify-center`}>
        <span className="text-zinc-400 font-bold text-sm">{getInitials(playerName)}</span>
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

/**
 * VAULT STATS ROW - BDL Season Stats (Open Door Read)
 * Shows: PTS, REB, AST, FG%, 3P%, STL, BLK
 */
const VaultStatsRow = memo(({ player }) => {
  // OPEN DOOR: Read directly from baseline_stats or top-level
  const bs = player.baseline_stats || {};
  
  const pts = bs.pts ?? player.pts;
  const reb = bs.reb ?? player.reb;
  const ast = bs.ast ?? player.ast;
  const fg_pct = formatPct(bs.fg_pct ?? player.fg_pct);
  const fg3_pct = formatPct(bs.fg3_pct ?? player.fg3_pct);
  const stl = bs.stl ?? player.stl;
  const blk = bs.blk ?? player.blk;
  
  const hasStats = pts != null || fg_pct != null;
  if (!hasStats) return null;
  
  return (
    <div className="bg-zinc-800/60 rounded-lg p-2.5 border border-zinc-700/50" data-testid="vault-stats">
      <div className="text-[9px] text-zinc-500 uppercase tracking-wider mb-2 font-semibold flex items-center gap-1">
        <Shield className="w-3 h-3" />
        Season Stats
      </div>
      {/* Primary Stats Row */}
      <div className="grid grid-cols-3 gap-3 text-center mb-2">
        {pts != null && (
          <div>
            <div className="text-[10px] text-zinc-500">PTS</div>
            <div className="text-base font-bold text-white">{typeof pts === 'number' ? pts.toFixed(1) : pts}</div>
          </div>
        )}
        {reb != null && (
          <div>
            <div className="text-[10px] text-zinc-500">REB</div>
            <div className="text-base font-bold text-white">{typeof reb === 'number' ? reb.toFixed(1) : reb}</div>
          </div>
        )}
        {ast != null && (
          <div>
            <div className="text-[10px] text-zinc-500">AST</div>
            <div className="text-base font-bold text-white">{typeof ast === 'number' ? ast.toFixed(1) : ast}</div>
          </div>
        )}
      </div>
      {/* Shooting & Defense Row */}
      <div className="grid grid-cols-4 gap-2 text-center pt-2 border-t border-zinc-700/30">
        {fg_pct != null && (
          <div>
            <div className="text-[8px] text-zinc-500">FG%</div>
            <div className="text-xs font-bold text-cyan-400">{fg_pct}%</div>
          </div>
        )}
        {fg3_pct != null && (
          <div>
            <div className="text-[8px] text-zinc-500">3P%</div>
            <div className="text-xs font-bold text-purple-400">{fg3_pct}%</div>
          </div>
        )}
        {stl != null && (
          <div>
            <div className="text-[8px] text-zinc-500">STL</div>
            <div className="text-xs font-bold text-green-400">{typeof stl === 'number' ? stl.toFixed(1) : stl}</div>
          </div>
        )}
        {blk != null && (
          <div>
            <div className="text-[8px] text-zinc-500">BLK</div>
            <div className="text-xs font-bold text-amber-400">{typeof blk === 'number' ? blk.toFixed(1) : blk}</div>
          </div>
        )}
      </div>
    </div>
  );
});
VaultStatsRow.displayName = 'VaultStatsRow';

/**
 * Single Prop Row - from Odds Funnel
 * Card BG: FRONT_LINE = Yellow, DEMON = Red, GOBLIN = Green
 * Icons: DEMON = Red Demon, GOBLIN = Green Goblin (regardless of card theme)
 */
const PropRow = memo(({ prop, theme, onClick, onQuickAdd }) => {
  const isDemon = prop.is_demon || prop.tier_label === 'DEMON';
  const isGoblin = prop.is_goblin || prop.tier_label === 'GOBLIN';
  const isFrontLine = prop.tier_label === 'FRONT_LINE' || prop.front_line_qualified;
  
  // Icon color is ALWAYS based on actual pick type (red demon / green goblin)
  const iconColor = isDemon ? 'text-red-400' : 'text-green-400';
  
  // Card background is yellow for Front Lines, otherwise red/green/zinc
  const tierBg = isFrontLine ? 'bg-yellow-950/40 border-yellow-500/30' 
    : isDemon ? 'bg-red-950/40 border-red-500/30' 
    : isGoblin ? 'bg-green-950/40 border-green-500/30' 
    : 'bg-zinc-800/40 border-zinc-700/30';
  
  // Line value color matches card theme
  const lineColor = isFrontLine ? 'text-yellow-400' 
    : isDemon ? 'text-red-400' 
    : isGoblin ? 'text-green-400' 
    : 'text-zinc-400';
  
  return (
    <div 
      className={`flex items-center justify-between p-2 rounded-lg border cursor-pointer transition-all hover:scale-[1.01] ${tierBg}`}
      onClick={() => onClick?.(prop)}
      data-testid={`prop-row-${prop.stat_type}-${prop.line}`}
    >
      <div className="flex items-center gap-2">
        {isDemon && <DemonIcon size={14} className={iconColor} />}
        {(isGoblin || isFrontLine) && !isDemon && <GoblinIcon size={14} className={iconColor} />}
        <div>
          <span className="text-sm font-medium text-white">
            {prop.stat_type} <span className={lineColor}>{prop.line}</span>
          </span>
        </div>
      </div>
      
      <div className="flex items-center gap-3 text-xs">
        {/* Hit Rates */}
        {prop.h10_rate != null && (
          <span className={`font-medium ${getHitRateColor(prop.h10_rate)}`}>
            L10: {prop.h10_rate}%
          </span>
        )}
        {prop.season_avg != null && (
          <span className="text-zinc-400">
            Avg: <span className="text-white">{prop.season_avg?.toFixed?.(1) || prop.season_avg}</span>
          </span>
        )}
        {/* Quick Add */}
        {onQuickAdd && (
          <button
            onClick={(e) => { e.stopPropagation(); onQuickAdd(prop); }}
            className="p-1 rounded bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 transition-all"
            data-testid={`quick-add-prop-${prop.stat_type}`}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
});
PropRow.displayName = 'PropRow';

// ==================== MAIN COMPONENT ====================

/**
 * UniversalPlayerCard - THE ONE CARD TO RULE THEM ALL
 * 
 * @param {Object} player - Player data merged from VAULT + ODDS funnels
 *   Required: player_name, team
 *   From Vault: baseline_stats (or pts, reb, ast, fg_pct, etc.), photo_url
 *   From Odds: props array with stat_type, line, is_demon, is_goblin, tier_label
 * @param {Array} props - Override: Props array (if not in player object)
 * @param {string} mode - Display mode: 'full' | 'compact' | 'mini'
 * @param {number} rank - Display rank badge
 * @param {Function} onClick - Click handler for card or prop
 * @param {Function} onQuickAdd - Quick add to Command Post
 * @param {boolean} showStats - Show vault stats (default: true)
 * @param {boolean} showProps - Show props list (default: true)
 */
const UniversalPlayerCard = memo(({
  player,
  props: propsProp,
  mode = 'full',
  rank,
  onClick,
  onQuickAdd,
  showStats = true,
  showProps = true
}) => {
  const [isExpanded, setIsExpanded] = useState(mode === 'full');
  
  const handleCardClick = useCallback(() => {
    if (player) onClick?.(player);
  }, [onClick, player]);
  
  const handlePropClick = useCallback((prop) => {
    onClick?.({ ...player, ...prop, selectedProp: prop });
  }, [onClick, player]);
  
  const handleQuickAdd = useCallback((prop) => {
    onQuickAdd?.({ ...player, ...prop });
  }, [onQuickAdd, player]);
  
  if (!player) return null;
  
  // Extract data - support multiple shapes
  const {
    player_name,
    name,
    team,
    position,
    photo_url,
    headshot_url,
    opponent,
    // Vault Stats
    baseline_stats,
    fg_pct, fg3_pct, stl, blk, pts, reb, ast,
    // Primary prop (for single-prop display)
    stat_type,
    line,
    h5_rate, h10_rate, season_avg, diff_from_avg,
    // Tier info
    is_demon, is_goblin, tier_label,
    // Injury flag
    is_injured
  } = player;
  
  const displayName = player_name || name;
  const displayPhoto = photo_url || headshot_url;
  const playerSlug = displayName?.replace(/\s+/g, '-').toLowerCase();
  
  // Get all props - from prop override or player.props
  const allProps = propsProp || player.props || [];
  const hasProps = allProps.length > 0;
  
  // Determine card theme from HIGHEST tier
  const highestTier = getHighestTier(allProps.length > 0 ? allProps : [player]);
  const theme = TIER_THEMES[highestTier];
  const TierIcon = theme.Icon;
  
  // ==================== MINI MODE ====================
  if (mode === 'mini') {
    return (
      <div 
        className={`flex items-center gap-2 p-2 rounded-lg border ${theme.border} bg-zinc-900/80 cursor-pointer hover:bg-zinc-800/80 transition-all ${theme.glow}`}
        onClick={handleCardClick}
        data-testid={`player-mini-${playerSlug}`}
      >
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-white truncate">{displayName}</div>
          <div className="flex items-center gap-1.5 text-[10px]">
            <span className="text-zinc-500">{team}</span>
            {stat_type && <span className={theme.text}>{stat_type} {line}</span>}
          </div>
        </div>
        {rank && (
          <div className={`w-5 h-5 rounded-full ${theme.accent} flex items-center justify-center text-[10px] font-bold text-white`}>
            {rank}
          </div>
        )}
        <ChevronRight className="w-4 h-4 text-zinc-600" />
      </div>
    );
  }
  
  // ==================== COMPACT MODE (Search Results) ====================
  if (mode === 'compact') {
    // Icon is ALWAYS based on actual pick type (red demon / green goblin)
    const isDemon = is_demon || tier_label === 'DEMON';
    const isGoblin = is_goblin || tier_label === 'GOBLIN' || tier_label === 'FRONT_LINE';
    const CompactIcon = isDemon ? DemonIcon : isGoblin ? GoblinIcon : null;
    const iconColor = isDemon ? 'text-red-400' : 'text-green-400';
    
    return (
      <div 
        className={`flex items-center gap-3 p-3 rounded-lg border ${theme.border} bg-gradient-to-br ${theme.bg} cursor-pointer hover:scale-[1.01] transition-all ${theme.glow}`}
        onClick={handleCardClick}
        data-testid={`player-compact-${playerSlug}`}
      >
        <div className={`relative ring-2 ${theme.ring} rounded-full`}>
          <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="md" />
          {rank && (
            <div className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full ${theme.accent} flex items-center justify-center text-[10px] font-bold text-white border-2 border-zinc-900`}>
              {rank}
            </div>
          )}
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {CompactIcon && <CompactIcon size={16} className={iconColor} />}
            <span className="font-bold text-white text-sm truncate">{displayName}</span>
            <span className="text-[10px] text-zinc-500">{team}</span>
            {is_injured && (
              <HeartPulse size={14} className="text-red-500 animate-pulse" title="Injury Alert" />
            )}
          </div>
          
          {/* Primary Prop */}
          {stat_type && (
            <div className={`text-xs ${theme.text} mt-0.5`}>
              {stat_type} {line}
            </div>
          )}
          
          {/* Stats Row */}
          <div className="flex items-center gap-3 mt-1 text-[10px]">
            {h10_rate != null && <span className="text-zinc-400">L10: <span className={getHitRateColor(h10_rate)}>{h10_rate}%</span></span>}
            {season_avg != null && <span className="text-zinc-400">Avg: <span className="text-white">{season_avg?.toFixed?.(1) || season_avg}</span></span>}
            {diff_from_avg != null && (
              <span className={`px-1 py-0.5 rounded text-[9px] ${diff_from_avg >= 0 ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'}`}>
                {diff_from_avg >= 0 ? '+' : ''}{diff_from_avg}%
              </span>
            )}
          </div>
        </div>
        
        {onQuickAdd && (
          <button
            onClick={(e) => { e.stopPropagation(); handleQuickAdd(player); }}
            className="flex-shrink-0 w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
            data-testid={`quick-add-${playerSlug}`}
          >
            <Plus className="w-4 h-4" />
          </button>
        )}
      </div>
    );
  }
  
  // ==================== FULL MODE (Default - War Zone/Safe Haven/Front Lines) ====================
  // Icon is ALWAYS based on actual pick type (red demon / green goblin)
  const isDemon = is_demon || tier_label === 'DEMON';
  const isGoblin = is_goblin || tier_label === 'GOBLIN' || tier_label === 'FRONT_LINE';
  const FullIcon = isDemon ? DemonIcon : isGoblin ? GoblinIcon : TierIcon;
  const iconColor = isDemon ? 'text-red-400' : 'text-green-400';
  
  return (
    <div 
      className={`rounded-xl border ${theme.border} bg-gradient-to-b ${theme.bg} overflow-hidden transition-all ${theme.glow}`}
      data-testid={`player-card-${playerSlug}`}
    >
      {/* HEADER: Player Identity + Vault Stats */}
      <div 
        className="p-4 cursor-pointer hover:bg-zinc-800/30 transition-all"
        onClick={hasProps && showProps ? () => setIsExpanded(!isExpanded) : handleCardClick}
      >
        <div className="flex items-center gap-4">
          {/* Photo with Rank */}
          <div className="relative flex-shrink-0">
            <div className={`ring-2 ${theme.ring} rounded-full`}>
              <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="lg" />
            </div>
            {rank && (
              <div className={`absolute -bottom-1 -right-1 w-6 h-6 rounded-full ${theme.accent} flex items-center justify-center text-xs font-bold text-white border-2 border-zinc-900`}>
                {rank}
              </div>
            )}
          </div>
          
          {/* Player Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              {FullIcon && <FullIcon size={20} className={iconColor} />}
              <h3 className="text-lg font-bold text-white truncate">{displayName}</h3>
              {position && <span className="px-1.5 py-0.5 text-[9px] bg-zinc-700/50 text-zinc-300 rounded">{position}</span>}
            </div>
            <div className="flex items-center gap-2 text-xs text-zinc-400 mt-0.5">
              <span className="font-mono">{team}</span>
              {opponent && (
                <>
                  <span className="text-zinc-600">vs</span>
                  <span className="text-zinc-300">{opponent}</span>
                </>
              )}
            </div>
            
            {/* Primary Prop Display (if single prop mode) */}
            {stat_type && !hasProps && (
              <div className="flex items-center gap-2 mt-1.5">
                <span className={`text-sm font-bold ${theme.text}`}>{stat_type} {line}</span>
                {h10_rate != null && <span className={`text-xs ${getHitRateColor(h10_rate)}`}>L10: {h10_rate}%</span>}
                {season_avg != null && <span className="text-xs text-zinc-400">Avg: {season_avg?.toFixed?.(1)}</span>}
              </div>
            )}
          </div>
          
          {/* Actions */}
          <div className="flex items-center gap-2">
            {onQuickAdd && (
              <button
                onClick={(e) => { e.stopPropagation(); handleQuickAdd(player); }}
                className="w-9 h-9 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
                title="Add to Command Post"
                data-testid={`quick-add-${playerSlug}`}
              >
                <Plus className="w-5 h-5" />
              </button>
            )}
            {hasProps && showProps && (
              <ChevronDown className={`w-5 h-5 text-zinc-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
            )}
          </div>
        </div>
        
        {/* VAULT STATS - BDL Season Stats */}
        {showStats && (
          <div className="mt-3">
            <VaultStatsRow player={player} />
          </div>
        )}
      </div>
      
      {/* BODY: Props List (from Odds Funnel) */}
      {hasProps && showProps && isExpanded && (
        <div className="border-t border-zinc-700/50 p-3 space-y-2 max-h-80 overflow-y-auto">
          <div className="flex items-center gap-2 text-[10px] text-zinc-500 mb-2">
            <span className="uppercase tracking-wider font-semibold">Available Props</span>
            <span className="text-zinc-600">({allProps.length})</span>
          </div>
          
          {allProps.map((prop, idx) => (
            <PropRow 
              key={`${prop.stat_type}-${prop.line}-${idx}`}
              prop={prop}
              theme={theme}
              onClick={handlePropClick}
              onQuickAdd={onQuickAdd ? handleQuickAdd : null}
            />
          ))}
        </div>
      )}
    </div>
  );
});

UniversalPlayerCard.displayName = 'UniversalPlayerCard';

// Export everything
export { UniversalPlayerCard, PlayerHeadshot, VaultStatsRow, PropRow, TIER_THEMES, getHighestTier };
export default UniversalPlayerCard;
