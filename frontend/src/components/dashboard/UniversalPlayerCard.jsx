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
  Crosshair, TrendingUp, HeartPulse, Lock, Flame, TrendingDown, Info
} from 'lucide-react';
import { Badge } from '../ui/badge';
import { DemonIcon, GoblinIcon } from './Icons';
import IntelligenceModal from './IntelligenceModal';

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

// ==================== TIER ICONS - Using shared Icons from ./Icons.jsx ====================
// DemonIcon and GoblinIcon are imported from './Icons'

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
 * Convert internal stat_type names to display-friendly abbreviations
 */
const formatStatType = (statType) => {
  if (!statType) return '';
  
  const statMap = {
    // Points variations
    'points': 'PTS',
    'points_alternate': 'PTS',
    'pts': 'PTS',
    // Rebounds variations  
    'rebounds': 'REB',
    'rebounds_alternate': 'REB',
    'reb': 'REB',
    // Assists variations
    'assists': 'AST',
    'assists_alternate': 'AST',
    'ast': 'AST',
    // Threes variations
    'threes': '3PM',
    'threes_alternate': '3PM',
    '3pm': '3PM',
    'three_pointers_made': '3PM',
    // Steals
    'steals': 'STL',
    'steals_alternate': 'STL',
    'stl': 'STL',
    // Blocks
    'blocks': 'BLK',
    'blocks_alternate': 'BLK',
    'blk': 'BLK',
    // Turnovers
    'turnovers': 'TO',
    'turnovers_alternate': 'TO',
    'tov': 'TO',
    // Combos
    'pts_rebs': 'PTS+REB',
    'pts_asts': 'PTS+AST',
    'rebs_asts': 'REB+AST',
    'pts_rebs_asts': 'PRA',
    'pra': 'PRA',
    'fantasy_score': 'FPTS',
    'double_double': 'DD',
    'triple_double': 'TD',
    // Minutes
    'minutes': 'MIN',
    'min': 'MIN',
  };
  
  const lower = statType.toLowerCase();
  return statMap[lower] || statType.toUpperCase().replace(/_/g, ' ');
};

/**
 * Locked Overlay for games in progress
 * Shows when is_locked is true (game has started)
 * Color matches the SECTION: green (Safe Haven), yellow (Front Lines), red (War Zone)
 */
const LockedOverlay = memo(({ isLocked, gameStatus, sectionColor }) => {
  if (!isLocked) return null;
  
  const statusText = gameStatus === 'completed' ? 'Game Completed' : 'Game Underway';
  
  // Color based on section (passed from parent)
  const colorMap = {
    green: { bg: 'bg-green-500/30', border: 'border-green-500/50', text: 'text-green-400' },
    yellow: { bg: 'bg-yellow-500/30', border: 'border-yellow-500/50', text: 'text-yellow-400' },
    red: { bg: 'bg-red-500/30', border: 'border-red-500/50', text: 'text-red-400' },
    amber: { bg: 'bg-amber-500/30', border: 'border-amber-500/50', text: 'text-amber-400' },
  };
  
  const colors = colorMap[sectionColor] || colorMap.green;
  
  return (
    <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px] z-10 flex flex-col items-center justify-center rounded-lg">
      <div className={`flex items-center gap-1.5 px-3 py-1.5 ${colors.bg} rounded-full border ${colors.border}`}>
        <Lock className={`w-3.5 h-3.5 ${colors.text}`} />
        <span className={`${colors.text} font-bold text-xs`}>LOCKED</span>
      </div>
      <span className="text-zinc-400 text-[10px] mt-1.5">{statusText}</span>
    </div>
  );
});
LockedOverlay.displayName = 'LockedOverlay';

/**
 * Determine the highest tier from all props
 * Priority: DEMON > GOBLIN > STANDARD
 * Used for card theme coloring
 * Note: Front Lines board overrides this to use FRONT_LINE theme (yellow)
 */
const getHighestTier = (props, forceTheme = null) => {
  // Allow parent to force a specific theme (e.g., FRONT_LINE for Front Lines board)
  if (forceTheme) return forceTheme;
  
  if (!props || props.length === 0) return 'STANDARD';
  if (props.some(p => p.is_demon || p.tier_label === 'DEMON')) return 'DEMON';
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
  
  // Build full photo URL - handle relative paths from API
  const getPhotoUrl = (url) => {
    if (!url) return null;
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    if (url.startsWith('/api')) {
      const backendUrl = process.env.REACT_APP_BACKEND_URL || '';
      return `${backendUrl}${url}`;
    }
    return url;
  };
  
  const fullPhotoUrl = getPhotoUrl(photoUrl);
  
  // If no photo URL, show team logo or initials
  if (!fullPhotoUrl) {
    const teamLogo = TEAM_LOGOS[team];
    if (teamLogo) {
      return (
        <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1.5`}>
          <img src={teamLogo} alt={team} className="w-full h-full object-contain" />
        </div>
      );
    }
    return (
      <div className={`${sizeClasses[size]} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 flex items-center justify-center`}>
        <span className="text-zinc-400 font-bold text-sm">{getInitials(playerName)}</span>
      </div>
    );
  }
  
  // Show photo with fallback on error
  return (
    <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800`}>
      <img 
        src={fullPhotoUrl} 
        alt={playerName}
        className="w-full h-full object-cover"
        style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }}
        onError={(e) => {
          // On error, replace with team logo or initials
          const teamLogo = TEAM_LOGOS[team];
          if (teamLogo) {
            e.target.src = teamLogo;
            e.target.style.objectFit = 'contain';
            e.target.style.transform = 'none';
            e.target.style.padding = '6px';
          } else {
            e.target.style.display = 'none';
          }
        }}
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
            {formatStatType(prop.stat_type)} <span className={lineColor}>{prop.line}</span>
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
  showProps = true,
  sectionColor = 'green',  // Section color for locked overlay
  forceTheme = null        // Force a specific theme (e.g., 'FRONT_LINE' for yellow cards)
}) => {
  const [isExpanded, setIsExpanded] = useState(mode === 'full');
  
  // Intelligence Modal state
  const [intelligenceModal, setIntelligenceModal] = useState({
    isOpen: false,
    type: null, // 'hook_risk' | 'suspect_bait' | 'officiating_impact' | 'usage_vacuum' | 'defensive_momentum'
  });
  
  // Check if locked (game in progress or completed)
  const isLocked = player?.is_locked;
  
  const handleCardClick = useCallback(() => {
    if (isLocked) return; // Don't allow clicks when locked
    if (player) onClick?.(player);
  }, [onClick, player, isLocked]);
  
  const handlePropClick = useCallback((prop) => {
    if (isLocked) return; // Don't allow clicks when locked
    onClick?.({ ...player, ...prop, selectedProp: prop });
  }, [onClick, player, isLocked]);
  
  const handleQuickAdd = useCallback((prop) => {
    if (isLocked) return; // Don't allow quick add when locked
    onQuickAdd?.({ ...player, ...prop });
  }, [onQuickAdd, player, isLocked]);
  
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
    is_injured,
    // Game status (for locking)
    is_locked,
    game_status,
    minutes_since_start,
    // Whistle Matrix
    crew_chief,
    ref_ou_pct,
    ref_ppg,
    whistle_class,
    has_whistle_modifier,
    whistle_modifier,
    ferrari_power_score,
    // Point Lift (Vegas Intel)
    point_lift,
    lift_label,
    lift_type,
    foul_rate_diff,
    // Usage Vacuum
    has_vacuum_modifier,
    vacuum_modifier,
    vacuum_data,
    // Defensive Momentum
    has_momentum_modifier,
    momentum_modifier,
    momentum_data,
    // Opponent
    opponent_abbr
  } = player;
  
  const displayName = player_name || name;
  const displayPhoto = photo_url || headshot_url;
  const playerSlug = displayName?.replace(/\s+/g, '-').toLowerCase();
  
  // Get all props - from prop override or player.props
  const allProps = propsProp || player.props || [];
  const hasProps = allProps.length > 0;
  
  // Determine card theme - use forceTheme if provided, otherwise detect from tier
  const highestTier = forceTheme || getHighestTier(allProps.length > 0 ? allProps : [player]);
  const theme = TIER_THEMES[highestTier] || TIER_THEMES.STANDARD;
  const TierIcon = theme.Icon;
  
  // ==================== MINI MODE ====================
  if (mode === 'mini') {
    return (
      <div 
        className={`flex items-center gap-2 p-2 rounded-lg border ${theme.border} bg-zinc-900/80 ${is_locked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-zinc-800/80'} transition-all ${theme.glow}`}
        onClick={handleCardClick}
        data-testid={`player-mini-${playerSlug}`}
      >
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-white truncate">{displayName}</div>
          <div className="flex items-center gap-1.5 text-[10px]">
            <span className="text-zinc-500">{team}</span>
            {stat_type && <span className={theme.text}>{formatStatType(stat_type)} {line}</span>}
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
  
  // ==================== COMPACT MODE (Board Cards - matches Top Picks style) ====================
  if (mode === 'compact') {
    const isDemon = is_demon || tier_label === 'DEMON';
    const isGoblin = is_goblin || tier_label === 'GOBLIN' || tier_label === 'FRONT_LINE';
    
    return (
      <div 
        className={`relative p-3 rounded-lg border ${theme.border} bg-gradient-to-br ${theme.bg} ${is_locked ? 'cursor-not-allowed' : 'cursor-pointer hover:scale-[1.02]'} transition-all w-full ${is_locked ? 'opacity-80' : ''}`}
        onClick={handleCardClick}
        data-testid={`player-compact-${playerSlug}`}
      >
        {/* Locked Overlay */}
        <LockedOverlay isLocked={is_locked} gameStatus={game_status} sectionColor={sectionColor} />
        
        {/* Header: Photo + Name + Icon */}
        <div className="flex items-center gap-2 mb-2">
          <div className="relative flex-shrink-0">
            <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" />
            {/* Tier icon on photo - same as Top Picks */}
            <div className="absolute -top-1 -right-1">
              {isDemon ? <DemonIcon size={14} /> : isGoblin ? <GoblinIcon size={14} /> : null}
            </div>
          </div>
          <div className="flex-1 min-w-0 overflow-hidden">
            <div className="text-sm font-medium text-white truncate max-w-full">{displayName}</div>
            <div className={`text-xs ${theme.text} truncate`}>{formatStatType(stat_type)} {line}</div>
          </div>
          {rank && (
            <Badge className="bg-zinc-800 text-zinc-300 border-none text-xs flex-shrink-0">#{rank}</Badge>
          )}
        </div>
        
        {/* Sidecar Warning Flags - Hook Risk & Bait Detection (INTERACTIVE) */}
        {player.sidecar?.enabled && (player.sidecar.hook_risk || player.sidecar.suspect_line_bait) && (
          <div className="mt-1.5 space-y-1">
            {player.sidecar.suspect_line_bait && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setIntelligenceModal({ isOpen: true, type: 'suspect_bait' });
                }}
                className="w-full flex items-center justify-between gap-1 px-2 py-1.5 bg-red-950/60 border border-red-500/40 rounded text-[10px] animate-pulse hover:bg-red-900/60 transition-colors cursor-pointer"
                data-testid="suspect-bait-badge"
              >
                <div className="flex items-center gap-1">
                  <span className="text-red-400 font-bold">🚨 SUSPECT LINE:</span>
                  <span className="text-red-300">Vegas Bait</span>
                </div>
                <Info className="w-3.5 h-3.5 text-red-400/70" />
              </button>
            )}
            {player.sidecar.hook_risk && !player.sidecar.suspect_line_bait && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setIntelligenceModal({ isOpen: true, type: 'hook_risk' });
                }}
                className="w-full flex items-center justify-between gap-1 px-2 py-1.5 bg-amber-950/60 border border-amber-500/40 rounded text-[10px] hover:bg-amber-900/60 transition-colors cursor-pointer"
                data-testid="hook-risk-badge"
              >
                <div className="flex items-center gap-1">
                  <span className="text-amber-400 font-bold">⚠️ Hook Risk</span>
                  <span className="text-amber-300/70">Tap for info</span>
                </div>
                <Info className="w-3.5 h-3.5 text-amber-400/70" />
              </button>
            )}
          </div>
        )}
        
        {/* Intelligence Modal */}
        <IntelligenceModal
          isOpen={intelligenceModal.isOpen}
          onClose={() => setIntelligenceModal({ isOpen: false, type: null })}
          type={intelligenceModal.type}
          playerName={player.player_name || player.name}
          statType={player.stat_type}
          line={player.line}
          sidecarData={player.sidecar}
        />
        
        {/* Stats Row - L5 / L10 / Median (replaces Avg) */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded px-2 py-1.5 text-[10px] mt-1">
          <div className="text-center flex-1">
            <div className="text-zinc-500">L5</div>
            <div className={`font-bold ${getHitRateColor(h5_rate || 0)}`}>
              {h5_rate != null ? `${h5_rate}%` : '---'}
            </div>
          </div>
          <div className="h-4 w-px bg-zinc-700" />
          <div className="text-center flex-1">
            <div className="text-zinc-500">L10</div>
            <div className={`font-bold ${getHitRateColor(h10_rate || 0)}`}>
              {h10_rate != null ? `${h10_rate}%` : '---'}
            </div>
          </div>
          <div className="h-4 w-px bg-zinc-700" />
          <div className="text-center flex-1">
            {/* Show Median if sidecar data available, else show Avg */}
            <div className="text-zinc-500">
              {player.sidecar?.median != null ? 'Med' : 'Avg'}
            </div>
            <div className="font-bold text-white">
              {player.sidecar?.median != null 
                ? player.sidecar.median 
                : (season_avg != null ? (season_avg.toFixed?.(1) || season_avg) : '---')
              }
            </div>
          </div>
        </div>
        
        {/* Vision Intel Suite CTA */}
        <div className="mt-2 text-center">
          <span className="text-[10px] text-cyan-400 font-medium animate-pulse">
            Click for Vision Intel Suite
          </span>
        </div>
        
        {/* Quick Add Button */}
        {onQuickAdd && !is_locked && (
          <button
            onClick={(e) => { e.stopPropagation(); handleQuickAdd(player); }}
            className="absolute top-2 right-2 w-6 h-6 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
            data-testid={`quick-add-${playerSlug}`}
          >
            <Plus className="w-3 h-3" />
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
      className={`rounded-xl border ${theme.border} bg-gradient-to-b ${theme.bg} overflow-hidden transition-all ${theme.glow} ${is_locked ? 'opacity-80' : ''}`}
      data-testid={`player-card-${playerSlug}`}
    >
      {/* HEADER: Player Identity + Vault Stats */}
      <div 
        className={`p-4 ${is_locked ? 'cursor-not-allowed' : 'cursor-pointer hover:bg-zinc-800/30'} transition-all`}
        onClick={hasProps && showProps && !is_locked ? () => setIsExpanded(!isExpanded) : handleCardClick}
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
                <span className={`text-sm font-bold ${theme.text}`}>{formatStatType(stat_type)} {line}</span>
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
                title="Add to Command Hub"
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
export { UniversalPlayerCard, PlayerHeadshot, VaultStatsRow, PropRow, LockedOverlay, TIER_THEMES, getHighestTier };
export default UniversalPlayerCard;
