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
  Crosshair, TrendingUp, HeartPulse, Lock, Flame, TrendingDown, Info, Brain
} from 'lucide-react';
import { Badge } from '../ui/badge';
import { DemonIcon, GoblinIcon } from './Icons';
import IntelligenceModal from './IntelligenceModal';
import { VKBadgeCompact } from './VegasKillerBadge';

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

// ==================== THEME CONFIG (TIER-BASED GLOW — REFINED) ====================
// Refinement pass: tighten blur, remove hazy spread, differentiate per tier.
//   Safe Haven  (GOBLIN)   → strongest clean glow
//   Front Lines (FRONT_LINE)→ moderate glow
//   War Zone    (DEMON)    → subtle glow
// All glows kept tight to the card edge (low blur) so the board feels like
// a trading terminal, not a sportsbook.
const TIER_THEMES = {
  DEMON: {
    border: 'border-red-500/50',
    bg: 'from-red-950/40 to-zinc-900',
    glow: 'shadow-[0_0_10px_rgba(239,68,68,0.28)]',
    text: 'text-red-400',
    accent: 'bg-red-500',
    ring: 'ring-red-500/50',
    Icon: DemonIcon
  },
  GOBLIN: {
    border: 'border-green-500/60',
    bg: 'from-green-950/40 to-zinc-900',
    glow: 'shadow-[0_0_18px_rgba(34,197,94,0.40)]',
    text: 'text-green-400',
    accent: 'bg-green-500',
    ring: 'ring-green-500/50',
    Icon: GoblinIcon
  },
  FRONT_LINE: {
    border: 'border-yellow-500/55',
    bg: 'from-yellow-950/40 to-zinc-900',
    glow: 'shadow-[0_0_14px_rgba(234,179,8,0.32)]',
    text: 'text-yellow-400',
    accent: 'bg-yellow-500',
    ring: 'ring-yellow-500/50',
    Icon: null
  },
  STANDARD: {
    border: 'border-zinc-500/40',
    bg: 'from-zinc-800/40 to-zinc-900',
    glow: 'shadow-[0_0_8px_rgba(161,161,170,0.15)]',
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
 * Priority: MINEFIELD (trap) > SAFE_HAVEN > WAR_ZONE > FRONT_LINE > STANDARD
 * Used for card theme coloring
 * 
 * NEW DATA MODEL (Sharp Movement):
 * - sharp_movement: True = favorable line delta detected
 * - trap_risk: True = routes to MINEFIELD
 * - tier_label: SAFE_HAVEN, FRONT_LINE, WAR_ZONE, or MINEFIELD
 * 
 * LEGACY COMPATIBILITY:
 * - is_demon: Mapped to WAR_ZONE
 * - is_goblin: Mapped to SAFE_HAVEN
 */
const getHighestTier = (props, forceTheme = null) => {
  // Allow parent to force a specific theme (e.g., FRONT_LINE for Front Lines board)
  if (forceTheme) return forceTheme;
  
  if (!props || props.length === 0) return 'STANDARD';
  
  // NEW: Check for trap_risk first (routes to MINEFIELD)
  if (props.some(p => p.trap_risk || p.tier_label === 'MINEFIELD')) return 'MINEFIELD';
  
  // NEW: Check for sharp_movement (routes to appropriate tier)
  if (props.some(p => p.sharp_movement && p.tier_label === 'SAFE_HAVEN')) return 'SAFE_HAVEN';
  if (props.some(p => p.sharp_movement && p.tier_label === 'WAR_ZONE')) return 'WAR_ZONE';
  if (props.some(p => p.sharp_movement)) return 'FRONT_LINE';
  
  // LEGACY FALLBACK: Keep backward compatibility
  if (props.some(p => p.is_demon || p.tier_label === 'DEMON')) return 'WAR_ZONE';
  if (props.some(p => p.is_goblin || p.tier_label === 'GOBLIN')) return 'SAFE_HAVEN';
  
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
const PlayerHeadshot = memo(({ photoUrl, playerName, team, size = 'md', sport = 'nba', mlbId = null }) => {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-12 h-12',
    lg: 'w-14 h-14',
    xl: 'w-20 h-20'
  };
  
  // MLB Team logos (fallback)
  const MLB_TEAM_LOGOS = {
    NYY: 'https://www.mlbstatic.com/team-logos/147.svg',
    BOS: 'https://www.mlbstatic.com/team-logos/111.svg',
    LAD: 'https://www.mlbstatic.com/team-logos/119.svg',
    SF: 'https://www.mlbstatic.com/team-logos/137.svg',
    ATL: 'https://www.mlbstatic.com/team-logos/144.svg',
    NYM: 'https://www.mlbstatic.com/team-logos/121.svg',
    PHI: 'https://www.mlbstatic.com/team-logos/143.svg',
    CHC: 'https://www.mlbstatic.com/team-logos/112.svg',
    HOU: 'https://www.mlbstatic.com/team-logos/117.svg',
    TEX: 'https://www.mlbstatic.com/team-logos/140.svg',
    SEA: 'https://www.mlbstatic.com/team-logos/136.svg',
    SD: 'https://www.mlbstatic.com/team-logos/135.svg',
    ARI: 'https://www.mlbstatic.com/team-logos/109.svg',
    STL: 'https://www.mlbstatic.com/team-logos/138.svg',
    MIL: 'https://www.mlbstatic.com/team-logos/158.svg',
    BAL: 'https://www.mlbstatic.com/team-logos/110.svg',
    TOR: 'https://www.mlbstatic.com/team-logos/141.svg',
    TB: 'https://www.mlbstatic.com/team-logos/139.svg',
    MIN: 'https://www.mlbstatic.com/team-logos/142.svg',
    CLE: 'https://www.mlbstatic.com/team-logos/114.svg',
    DET: 'https://www.mlbstatic.com/team-logos/116.svg',
    KC: 'https://www.mlbstatic.com/team-logos/118.svg',
    CWS: 'https://www.mlbstatic.com/team-logos/145.svg',
    LAA: 'https://www.mlbstatic.com/team-logos/108.svg',
    OAK: 'https://www.mlbstatic.com/team-logos/133.svg',
    COL: 'https://www.mlbstatic.com/team-logos/115.svg',
    CIN: 'https://www.mlbstatic.com/team-logos/113.svg',
    PIT: 'https://www.mlbstatic.com/team-logos/134.svg',
    MIA: 'https://www.mlbstatic.com/team-logos/146.svg',
    WSH: 'https://www.mlbstatic.com/team-logos/120.svg',
  };
  
  // Build full photo URL - handle relative paths and MLB local files
  const getPhotoUrl = (url) => {
    if (!url) return null;
    
    // For MLB with local headshot path
    if (sport === 'mlb' && url.startsWith('/images/mlb_headshots/')) {
      return url; // Local public path
    }
    
    // For MLB with mlbId, try local file first
    if (sport === 'mlb' && mlbId) {
      return `/images/mlb_headshots/${mlbId}.png`;
    }
    
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
  
  // Get appropriate team logos based on sport
  const getTeamLogo = (teamAbbr) => {
    if (sport === 'mlb') {
      return MLB_TEAM_LOGOS[teamAbbr];
    }
    return TEAM_LOGOS[teamAbbr];
  };
  
  // Generic silhouette for MLB players without headshot
  const MLB_SILHOUETTE = '/images/mlb_headshots/default_silhouette.png';
  
  // If no photo URL, show team logo or initials
  if (!fullPhotoUrl) {
    const teamLogo = getTeamLogo(team);
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
          // On error, try fallbacks in order:
          // 1. ESPN fallback for MLB
          // 2. Team logo
          // 3. Silhouette/initials
          
          if (sport === 'mlb' && mlbId && !e.target.dataset.triedEspn) {
            // Try ESPN fallback for MLB
            e.target.dataset.triedEspn = 'true';
            e.target.src = `https://a.espncdn.com/combiner/i?img=/i/headshots/mlb/players/full/${mlbId}.png&w=350&h=254`;
            return;
          }
          
          const teamLogo = getTeamLogo(team);
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
 * NEW MODEL: Uses sharp_movement and trap_risk instead of demon/goblin
 * Card BG: SAFE_HAVEN = Green, FRONT_LINE = Yellow, WAR_ZONE = Red, MINEFIELD = Orange
 * NOW WITH: Vegas Killer ML predictions
 */
const PropRow = memo(({ prop, theme, onClick, onQuickAdd, onVKClick }) => {
  // NEW: Sharp movement classification
  const hasSharpMovement = prop.sharp_movement;
  const hasTrapRisk = prop.trap_risk;
  const tierLabel = prop.tier_label || 'STANDARD';
  
  // LEGACY FALLBACK for backward compatibility
  const isDemon = prop.is_demon || tierLabel === 'DEMON' || tierLabel === 'WAR_ZONE';
  const isGoblin = prop.is_goblin || tierLabel === 'GOBLIN' || tierLabel === 'SAFE_HAVEN';
  const isFrontLine = tierLabel === 'FRONT_LINE' || prop.front_line_qualified;
  const isMinefield = hasTrapRisk || tierLabel === 'MINEFIELD';
  
  // Vegas Killer prediction data
  const vkPredicted = prop.vk_predicted;
  const vkRecommendation = prop.vk_recommendation;
  const vkProbOver = prop.vk_prob_over;
  const vkProbUnder = prop.vk_prob_under;
  const hasVK = vkPredicted != null;

  // Direction-aware edge: backend stores `vk_edge` as OVER-side edge.
  // For an UNDER prop card, invert so the user sees the edge FOR the
  // side they're betting. Same logic applies universally across sports.
  const _propSide = (prop.direction || prop.recommendation || '').toString().toUpperCase();
  const _isUnderSide = _propSide.includes('UNDER');
  const vkEdge = (prop.vk_edge != null && _isUnderSide)
    ? -Number(prop.vk_edge)
    : prop.vk_edge;
  
  // Determine card styling based on tier
  const tierBg = isMinefield ? 'bg-orange-950/40 border-orange-500/30'
    : isFrontLine ? 'bg-yellow-950/40 border-yellow-500/30' 
    : isDemon ? 'bg-red-950/40 border-red-500/30' 
    : isGoblin ? 'bg-green-950/40 border-green-500/30' 
    : 'bg-zinc-800/40 border-zinc-700/30';
  
  // Line value color matches card theme
  const lineColor = isMinefield ? 'text-orange-400'
    : isFrontLine ? 'text-yellow-400' 
    : isDemon ? 'text-red-400' 
    : isGoblin ? 'text-green-400' 
    : 'text-zinc-400';
  
  // Icon based on classification
  const iconColor = isDemon ? 'text-red-400' : 'text-green-400';
  
  return (
    <div 
      className={`flex items-center justify-between p-2 rounded-lg border cursor-pointer transition-all hover:scale-[1.01] ${tierBg}`}
      onClick={() => onClick?.(prop)}
      data-testid={`prop-row-${prop.stat_type}-${prop.line}`}
    >
      <div className="flex items-center gap-2">
        {/* Show trap indicator for minefield */}
        {isMinefield && (
          <span className="text-orange-400 text-xs">⚠️</span>
        )}
        {/* Legacy icons for backward compatibility */}
        {!isMinefield && isDemon && <DemonIcon size={14} className={iconColor} />}
        {!isMinefield && (isGoblin || isFrontLine) && !isDemon && <GoblinIcon size={14} className={iconColor} />}
        <div>
          <span className="text-sm font-medium text-white">
            {formatStatType(prop.stat_type)} <span className={lineColor}>{prop.line}</span>
          </span>
        </div>
      </div>
      
      <div className="flex items-center gap-2 text-xs">
        {/* Vegas Killer Prediction Badge */}
        {hasVK && (
          <VKBadgeCompact
            predicted={vkPredicted}
            edge={vkEdge}
            recommendation={vkRecommendation}
            probOver={vkProbOver}
            probUnder={vkProbUnder}
            onClick={(e) => {
              e.stopPropagation();
              onVKClick?.(prop);
            }}
          />
        )}
        
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
  forceTheme = null,       // Force a specific theme (e.g., 'FRONT_LINE' for yellow cards)
  isBoardPick = false      // Only true for the 30 props in War Zone, Safe Haven, Front Lines
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
    // In compact mode, only board picks should trigger click
    if (mode === 'compact' && !isBoardPick) return;
    if (player) onClick?.(player);
  }, [onClick, player, isLocked, mode, isBoardPick]);
  
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
    opponent_abbr,
    // Sport-specific fields
    sport,
    official_mlb_id,
    bdl_id
  } = player;
  
  const displayName = player_name || name;
  const displayPhoto = photo_url || headshot_url || player.headshot_local;
  const playerSlug = displayName?.replace(/\s+/g, '-').toLowerCase();
  
  // Determine sport from player data
  const playerSport = sport || 'nba';
  const mlbId = official_mlb_id;
  
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
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" sport={playerSport} mlbId={mlbId} />
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
    // NEW: Use sharp_movement and trap_risk classification
    const hasSharpMovement = player.sharp_movement || propsProp?.some(p => p.sharp_movement);
    const hasTrapRisk = player.trap_risk || propsProp?.some(p => p.trap_risk);
    
    // LEGACY FALLBACK for backward compatibility
    const isDemon = is_demon || tier_label === 'DEMON' || tier_label === 'WAR_ZONE';
    const isGoblin = is_goblin || tier_label === 'GOBLIN' || tier_label === 'SAFE_HAVEN' || tier_label === 'FRONT_LINE';
    
    // Only board picks (the 30 props in War Zone, Safe Haven, Front Lines) should be clickable
    const isClickable = isBoardPick && !is_locked;

    // Direction-aware display values (presentation-only)
    const _sideRaw = (player.direction || player.recommendation || '').toString().toUpperCase();
    const sideIsUnder = _sideRaw.includes('UNDER');
    const sideLabel = sideIsUnder ? 'UNDER' : 'OVER';
    const sideColor = sideIsUnder ? 'text-red-400' : 'text-green-400';
    const sideBar = sideIsUnder ? 'bg-red-500' : 'bg-green-500';
    const sideBarGlow = sideIsUnder
      ? 'shadow-[0_0_8px_rgba(239,68,68,0.45)]'
      : 'shadow-[0_0_8px_rgba(34,197,94,0.45)]';

    // Direction-aware edge display (backend vk_edge is OVER-side)
    const dispEdge = (player.vk_edge != null && sideIsUnder)
      ? -Number(player.vk_edge)
      : (player.vk_edge != null ? Number(player.vk_edge) : null);

    // Inline Vision Intel one-liner — prefers backend vision_intel / vision_summary,
    // falls back to a tight VK-derived sentence. Never renders a CTA button.
    const visionLine = player.vision_intel || player.vision_summary || (() => {
      if (player.vk_predicted == null) return null;
      const proj = Number(player.vk_predicted).toFixed(1);
      const rel = sideIsUnder ? 'below' : 'above';
      const verb = sideIsUnder ? 'stays below' : 'clears';
      if (dispEdge != null && Math.abs(dispEdge) >= 10) {
        return `Projection ${proj} sits well ${rel} the ${line} line — model favors the ${sideLabel.toLowerCase()}.`;
      }
      return `Model ${verb} ${line} on a ${proj} projection.`;
    })();

    return (
      <div
        className={`relative pl-4 pr-3 py-3 rounded-lg border ${theme.border} bg-gradient-to-br ${theme.bg} ${theme.glow} ${isClickable ? 'cursor-pointer hover:scale-[1.01]' : ''} ${is_locked ? 'cursor-not-allowed opacity-80' : ''} transition-all w-full overflow-hidden`}
        onClick={isClickable ? handleCardClick : undefined}
        data-testid={`player-compact-${playerSlug}`}
      >
        {/* Left Signal Bar — green OVER / red UNDER */}
        <div
          className={`absolute left-0 top-2 bottom-2 w-1 md:w-[3px] rounded-r ${sideBar} ${sideBarGlow}`}
          data-testid={`signal-bar-${sideLabel.toLowerCase()}`}
          aria-hidden="true"
        />

        {/* Locked Overlay */}
        <LockedOverlay isLocked={is_locked} gameStatus={game_status} sectionColor={sectionColor} />

        {/* Quick Add (top-right, single visual indicator per spec) */}
        {onQuickAdd && !is_locked && (
          <button
            onClick={(e) => { e.stopPropagation(); handleQuickAdd(player); }}
            className="absolute top-2 right-2 w-6 h-6 rounded-full bg-cyan-500/15 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/25 transition-all"
            data-testid={`quick-add-${playerSlug}`}
            aria-label="Quick add"
          >
            <Plus className="w-3 h-3" />
          </button>
        )}

        {/* Header — photo + player name (left-aligned, tight) */}
        <div className="flex items-center gap-2 mb-2 pr-7">
          <div className="relative flex-shrink-0">
            <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" sport={playerSport} mlbId={mlbId} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-white truncate">{displayName}</div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider truncate">
              {sideLabel} {line} {formatStatType(stat_type)}
            </div>
          </div>
        </div>

        {/* PRIMARY — direction + line + stat, left-aligned, bold, color-coded */}
        <div className={`${sideColor} leading-none mb-2`}>
          <span className="text-3xl md:text-2xl font-extrabold tracking-tight">{sideLabel} {line}</span>
          <span className="ml-1.5 text-xs md:text-[11px] font-semibold text-zinc-400 uppercase">
            {formatStatType(stat_type)}
          </span>
        </div>

        {/* INLINE VISION INTEL — always visible, no CTA button */}
        {visionLine && (
          <div
            className="text-[11px] md:text-[11.5px] leading-snug text-zinc-300/90 mb-2.5 line-clamp-2"
            data-testid={`vision-intel-inline-${playerSlug}`}
          >
            {visionLine}
          </div>
        )}

        {/* Sidecar Warning Flags — preserved (interactive info) */}
        {player.sidecar?.enabled && (player.sidecar.hook_risk || player.sidecar.suspect_line_bait) && (
          <div className="mb-2 space-y-1">
            {player.sidecar.suspect_line_bait && (
              <button
                onClick={(e) => { e.stopPropagation(); setIntelligenceModal({ isOpen: true, type: 'suspect_bait' }); }}
                className="w-full flex items-center justify-between gap-1 px-2 py-1 bg-red-950/40 border border-red-500/30 rounded text-[10px] hover:bg-red-900/40 transition-colors"
                data-testid="suspect-bait-badge"
              >
                <span className="text-red-300 font-semibold">SUSPECT LINE — Vegas Bait</span>
                <Info className="w-3 h-3 text-red-400/70" />
              </button>
            )}
            {player.sidecar.hook_risk && !player.sidecar.suspect_line_bait && (
              <button
                onClick={(e) => { e.stopPropagation(); setIntelligenceModal({ isOpen: true, type: 'hook_risk' }); }}
                className="w-full flex items-center justify-between gap-1 px-2 py-1 bg-amber-950/40 border border-amber-500/30 rounded text-[10px] hover:bg-amber-900/40 transition-colors"
                data-testid="hook-risk-badge"
              >
                <span className="text-amber-300 font-semibold">Hook Risk</span>
                <Info className="w-3 h-3 text-amber-400/70" />
              </button>
            )}
          </div>
        )}

        {/* Intelligence Modal (preserved) */}
        <IntelligenceModal
          isOpen={intelligenceModal.isOpen}
          onClose={() => setIntelligenceModal({ isOpen: false, type: null })}
          type={intelligenceModal.type}
          playerName={player.player_name || player.name}
          statType={player.stat_type}
          line={player.line}
          sidecarData={player.sidecar}
        />

        {/* FLAT STAT STRIP — Edge / Hit Rate / Avg */}
        <div className="flex items-stretch gap-3 pt-1.5 border-t border-zinc-800/70 text-left">
          <div className="flex-1 min-w-0">
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mb-0.5">Edge</div>
            <div className={`text-sm md:text-[15px] font-bold tabular-nums ${
              dispEdge == null ? 'text-zinc-400'
                : dispEdge >= 10 ? 'text-green-400'
                : dispEdge >= 0 ? 'text-zinc-200'
                : 'text-red-400'
            }`}>
              {dispEdge != null ? `${dispEdge > 0 ? '+' : ''}${dispEdge.toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mb-0.5">Hit Rate</div>
            <div className={`text-sm md:text-[15px] font-bold tabular-nums ${getHitRateColor(h10_rate || 0)}`}>
              {h10_rate != null ? `${h10_rate}%` : '—'}
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mb-0.5">Avg</div>
            <div className="text-sm md:text-[15px] font-bold tabular-nums text-white">
              {season_avg != null ? (season_avg.toFixed?.(1) || season_avg) : '—'}
            </div>
          </div>
        </div>

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
              <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="lg" sport={playerSport} mlbId={mlbId} />
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
