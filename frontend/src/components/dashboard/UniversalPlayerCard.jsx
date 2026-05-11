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
import { MarketGapBadge, MarketGapDetail } from './MarketGapBadge';

// ==================== TEAM LOGOS ====================
// Sport-aware lookup: `getTeamLogo(sport, team, [team_logo_url])`.
// Sourced from `./constants.js` so NBA/MLB/NFL/NHL never cross-populate.
import { getTeamLogo } from './constants';

// ==================== TIER ICONS - Using shared Icons from ./Icons.jsx ====================
// DemonIcon and GoblinIcon are imported from './Icons'

// ==================== THEME CONFIG (TIER-BASED GLOW — TERMINAL STYLE) ====================
// Landing-aligned DNA: near-black surfaces, tight 1px borders, glow on edges
// only (low blur, slightly higher opacity for electric precision rather than
// hazy spread). Tier remains the semantic carrier of emphasis.
//   Safe Haven  (GOBLIN)    → strongest clean glow
//   Front Lines (FRONT_LINE)→ moderate glow
//   War Zone    (DEMON)     → subtle glow
const TIER_THEMES = {
  DEMON: {
    border: 'border-red-500/30',
    bg: 'from-zinc-950 to-zinc-950',
    glow: 'shadow-[0_0_0_1px_rgba(239,68,68,0.08),0_0_10px_rgba(239,68,68,0.22)]',
    text: 'text-red-400',
    accent: 'bg-red-500',
    ring: 'ring-red-500/40',
    Icon: DemonIcon
  },
  GOBLIN: {
    border: 'border-green-500/40',
    bg: 'from-zinc-950 to-zinc-950',
    glow: 'shadow-[0_0_0_1px_rgba(34,197,94,0.12),0_0_14px_rgba(34,197,94,0.30)]',
    text: 'text-green-400',
    accent: 'bg-green-500',
    ring: 'ring-green-500/40',
    Icon: GoblinIcon
  },
  FRONT_LINE: {
    border: 'border-yellow-500/35',
    bg: 'from-zinc-950 to-zinc-950',
    glow: 'shadow-[0_0_0_1px_rgba(234,179,8,0.08),0_0_12px_rgba(234,179,8,0.24)]',
    text: 'text-yellow-400',
    accent: 'bg-yellow-500',
    ring: 'ring-yellow-500/40',
    Icon: null
  },
  STANDARD: {
    border: 'border-zinc-800/80',
    bg: 'from-zinc-950 to-zinc-950',
    glow: 'shadow-[0_0_0_1px_rgba(161,161,170,0.06)]',
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
 * Convert internal stat_type names to display-friendly abbreviations.
 *
 * 2026-05-08 — universal stat-label adapter. The implementation lives
 * in `utils/statLabel.js`; this re-export keeps existing imports
 * working without modification.
 */
import { getStatLabel as formatStatType } from '../../utils/statLabel';

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

/**
 * Format American odds with explicit +/- prefix.
 *   -444  →  "-444"
 *    190  →  "+190"
 *      0  →  "EVEN"
 *   null  →  "—"
 */
const formatAmericanOdds = (n) => {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
  const v = Math.round(Number(n));
  if (v === 0) return 'EVEN';
  return v > 0 ? `+${v}` : `${v}`;
};

/**
 * Resolve the best sportsbook odds to display on a pick.
 *
 * Universal contract — backend `services/scoring/scoring_stack._pick_reference_odds`
 * already runs the DK → FD → MGM → BOL fallback chain (NBA) or
 * DK+FD consensus → DK → FD → MGM → BOL (MLB) and stamps the winner
 * onto every scored doc as `tier_reference_book` + `tier_reference_odds`.
 *
 * This helper just reads those two fields. If they're missing we
 * walk a per-book fallback in case the prop carries the raw odds
 * fields (live-prop pool / search results / non-scored rows). When
 * nothing is found we still return a `book` label so the chip
 * renders with `—` instead of vanishing entirely.
 */
const resolveDisplayOdds = (p) => {
  if (!p) return { odds: null, book: null, sourceLabel: '—' };
  const refBook = p.tier_reference_book;
  const refOdds = p.tier_reference_odds;
  // Treat 'none' the same as missing — the scoring stack stamps
  // 'none' when no sportsbook in the chain quoted the line.
  if (refOdds != null && refBook && refBook !== 'none') {
    return { odds: refOdds, book: refBook, sourceLabel: refBook };
  }
  // Per-book fallback chain DK → FD → MGM → CSR → BOL (raw scored
  // doc doesn't carry these today, but pp_only / search-pool /
  // live-prop shapes do — Caesars added 2026-05-11; kept so the chip
  // never falsely renders '—' when a book actually has a price).
  const chain = [
    ['dk',  p.dk_odds],
    ['fd',  p.fd_odds],
    ['mgm', p.mgm_odds],
    ['csr', p.csr_odds],
    ['bol', p.bol_odds],
  ];
  for (const [book, odds] of chain) {
    if (odds != null) return { odds, book, sourceLabel: book };
  }
  return { odds: null, book: null, sourceLabel: '—' };
};

/**
 * OddsChip — terminal-style label/value pair, theme-aware.
 *
 * Renders inside the existing stat strip alongside Projection /
 * L20-L10-L5 / Avg cells so the chip inherits the surrounding
 * label typography (font-mono uppercase tracking-[0.15em]) and
 * tabular-nums alignment. The numeric value picks up the active
 * tier's text color so it reads as part of the card, not as a
 * stranded chip.
 */
const OddsChip = memo(({ pick, themeText = 'text-zinc-200', size = 'md', testid }) => {
  const { odds, sourceLabel } = resolveDisplayOdds(pick);
  const valueClasses = size === 'sm'
    ? 'text-xs font-bold font-mono tabular-nums'
    : 'text-sm md:text-[15px] font-bold font-mono tabular-nums';
  const labelClasses = size === 'sm'
    ? 'text-[8px] font-mono uppercase tracking-[0.15em] text-zinc-500 mb-0.5'
    : 'text-[9px] font-mono uppercase tracking-[0.15em] text-zinc-500 mb-0.5';
  return (
    <div className="flex-1 min-w-0" data-testid={testid || 'pick-odds-chip'}>
      <div className={labelClasses}>
        Odds · <span className="text-zinc-400">{sourceLabel}</span>
      </div>
      <div className={`${valueClasses} ${odds == null ? 'text-zinc-500' : themeText}`}>
        {formatAmericanOdds(odds)}
      </div>
    </div>
  );
});
OddsChip.displayName = 'OddsChip';

// ==================== SUB-COMPONENTS ====================

/**
 * Player Headshot with team logo fallback
 */
const PlayerHeadshot = memo(({ photoUrl, playerName, team, size = 'md', sport = 'nba', mlbId = null, teamLogoUrl: explicitLogoUrl = null }) => {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-12 h-12',
    lg: 'w-14 h-14',
    xl: 'w-20 h-20'
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

  // Sport-aware lookup. NEVER cross-populates between leagues.
  // Resolution: backend `team_logo_url` → sport+team map → null (initials).
  const resolveLogo = () => getTeamLogo(sport, team, explicitLogoUrl);
  
  // Generic silhouette for MLB players without headshot
  const MLB_SILHOUETTE = '/images/mlb_headshots/default_silhouette.png';
  
  // If no photo URL, show team logo or initials
  if (!fullPhotoUrl) {
    const teamLogo = resolveLogo();
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
          
          const teamLogo = resolveLogo();
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

  // SSOT Tier E (2026-05-04): `edge_vs_fair` is the canonical ratio.
  // Convert to raw units via × line. Legacy `vk_edge` fallback removed
  // — all frontend readers migrated. For UNDER props, invert so the
  // user sees the edge FOR the side they're betting.
  const _propSide = (prop.side || prop.recommendation || prop.direction || '').toString().toUpperCase();
  const _isUnderSide = _propSide.includes('UNDER');
  const _canonicalEdgeUnits = (prop.edge_vs_fair != null && prop.line != null)
    ? Number(prop.edge_vs_fair) * Number(prop.line)
    : null;
  const vkEdge = (_canonicalEdgeUnits != null && _isUnderSide)
    ? -_canonicalEdgeUnits
    : _canonicalEdgeUnits;
  
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
        
        {/* Hit Rate — 2026-05-07 P0 Phase 4B: canonical-only read.
            Backend no longer ships `h10_rate` on tier picks. */}
        {(() => {
          const l10 = prop.hit_rate_l10;
          return l10 != null && (
            <span className={`font-medium ${getHitRateColor(l10)}`} data-testid={`hr-l10-list-${prop.player_name || ''}`}>
              L10: {Math.round(l10)}%
            </span>
          );
        })()}
        {prop.season_avg != null && (
          <span className="text-zinc-400">
            Avg: <span className="text-white">{prop.season_avg?.toFixed?.(1) || prop.season_avg}</span>
          </span>
        )}
        {/* Odds chip — tier_reference_book (DK→FD→MGM→BOL chain). */}
        {(() => {
          const { odds, sourceLabel } = resolveDisplayOdds(prop);
          const txt = isMinefield ? 'text-orange-400'
            : isFrontLine ? 'text-yellow-400'
            : isDemon ? 'text-red-400'
            : isGoblin ? 'text-green-400'
            : 'text-zinc-200';
          return (
            <span
              className="inline-flex items-center gap-1 font-mono tabular-nums"
              data-testid={`prop-row-odds-${prop.stat_type}-${prop.line}`}
              title={`Reference book: ${sourceLabel}`}
            >
              <span className="text-[9px] uppercase tracking-[0.12em] text-zinc-500">{sourceLabel}</span>
              <span className={`text-xs font-bold ${odds == null ? 'text-zinc-500' : txt}`}>
                {formatAmericanOdds(odds)}
              </span>
            </span>
          );
        })()}
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
    season_avg, diff_from_avg,
    // 2026-05-07 P0 Phase 4B: canonical-only side-aware hit-rate
    // windows from the score doc. Backend no longer ships legacy
    // `h5_rate` / `h10_rate` aliases on tier picks; reads consume
    // these canonical fields directly with no fallback.
    hit_rate_l5, hit_rate_l10, hit_rate_l20,
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
        <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" sport={playerSport} mlbId={mlbId} teamLogoUrl={player.team_logo_url} />
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

    // Tier-matched left signal bar (NOT direction-matched).
    // Safe Haven → green, Front Lines → amber, War Zone → red, default → zinc.
    const tierKey = (theme === TIER_THEMES.GOBLIN) ? 'GOBLIN'
      : (theme === TIER_THEMES.FRONT_LINE) ? 'FRONT_LINE'
      : (theme === TIER_THEMES.DEMON) ? 'DEMON' : 'STANDARD';
    const tierBarStyles = {
      GOBLIN:     { bar: 'bg-green-500',  glow: 'shadow-[0_0_8px_rgba(34,197,94,0.45)]' },
      FRONT_LINE: { bar: 'bg-yellow-500', glow: 'shadow-[0_0_8px_rgba(234,179,8,0.45)]' },
      DEMON:      { bar: 'bg-red-500',    glow: 'shadow-[0_0_8px_rgba(239,68,68,0.45)]' },
      STANDARD:   { bar: 'bg-zinc-500',   glow: 'shadow-[0_0_6px_rgba(161,161,170,0.30)]' },
    }[tierKey];
    const sideBar = tierBarStyles.bar;
    const sideBarGlow = tierBarStyles.glow;

    // Direction-aware edge display.
    // SSOT Tier E (2026-05-04): canonical `edge_vs_fair * line`.
    // Legacy `vk_edge` fallback removed.
    const _canonicalEdge = (player.edge_vs_fair != null && player.line != null)
      ? Number(player.edge_vs_fair) * Number(player.line)
      : null;
    const dispEdge = (_canonicalEdge != null && sideIsUnder)
      ? -_canonicalEdge
      : _canonicalEdge;

    // Inline Vision Intel one-liner — UNIVERSAL DASHBOARD CARD CONTRACT
    // (2026-04-28). Backend stamps `short_sentence` (truncated
    // vision_intel, no fabricated text). Card no longer generates
    // a derived fallback — `null` renders nothing, exactly per spec.
    const visionLine = player.short_sentence
      ?? player.vision_intel
      ?? player.vision_summary
      ?? null;

    return (
      <div
        className={`relative pl-4 pr-3 py-3 rounded-md border ${theme.border} bg-gradient-to-br ${theme.bg} ${theme.glow} ${isClickable ? 'cursor-pointer hover:border-opacity-80 hover:scale-[1.005]' : ''} ${is_locked ? 'cursor-not-allowed opacity-80' : ''} transition-all w-full overflow-hidden`}
        onClick={isClickable ? handleCardClick : undefined}
        data-testid={`player-compact-${playerSlug}`}
      >
        {/* Left Signal Bar — matches TIER color (Safe Haven green / Front Lines amber / War Zone red) */}
        <div
          className={`absolute left-0 top-2 bottom-2 w-1 md:w-[3px] rounded-r ${sideBar} ${sideBarGlow}`}
          data-testid={`tier-signal-bar-${tierKey.toLowerCase()}`}
          aria-hidden="true"
        />

        {/* Locked Overlay */}
        <LockedOverlay isLocked={is_locked} gameStatus={game_status} sectionColor={sectionColor} />

        {/* Quick Add (top-right, single visual indicator per spec) */}
        {onQuickAdd && !is_locked && (
          <button
            onClick={(e) => { e.stopPropagation(); handleQuickAdd(player); }}
            className="absolute top-2 right-2 w-6 h-6 rounded-sm bg-emerald-500/10 border border-emerald-500/40 flex items-center justify-center text-emerald-400 hover:bg-emerald-500/20 hover:border-emerald-400 transition-all"
            data-testid={`quick-add-${playerSlug}`}
            aria-label="Quick add"
          >
            <Plus className="w-3 h-3" />
          </button>
        )}

        {/* Header — photo + player name + team chip (left-aligned, tight) */}
        <div className="flex items-center gap-2 mb-2 pr-7">
          <div className="relative flex-shrink-0">
            <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="sm" sport={playerSport} mlbId={mlbId} teamLogoUrl={player.team_logo_url} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 leading-tight">
              <span className="text-sm font-semibold text-white truncate">{displayName}</span>
              {team && (
                <span
                  className="shrink-0 px-1 py-[1px] rounded-sm text-[9px] font-mono font-semibold tracking-wider text-zinc-300 bg-zinc-800/80 border border-zinc-700/60 uppercase"
                  data-testid={`player-team-${playerSlug}`}
                >
                  {team}
                </span>
              )}
            </div>
            {/* Matchup row (2026-05-02): "vs OPP · TipTime" so users see
                exactly which game / opponent the prop is against. Only
                renders when at least one of opponent / game_start_utc is
                present — both are optional and degrade gracefully. */}
            {(player.opponent || player.opponent_abbr || player.game_start_utc) && (
              <div
                className="flex items-center gap-1.5 text-[10px] font-mono text-zinc-400 mt-0.5 leading-tight"
                data-testid={`matchup-row-${playerSlug}`}
              >
                {(player.opponent_abbr || player.opponent) && (
                  <span data-testid={`opponent-${playerSlug}`}>
                    <span className="text-zinc-500">vs</span>{' '}
                    <span className="text-zinc-200 uppercase tracking-wider">
                      {player.opponent_abbr || player.opponent}
                    </span>
                  </span>
                )}
                {(player.opponent || player.opponent_abbr) && player.game_start_utc && (
                  <span className="text-zinc-700">·</span>
                )}
                {player.game_start_utc && (
                  <span data-testid={`tipoff-${playerSlug}`}>
                    {(() => {
                      try {
                        const d = new Date(player.game_start_utc);
                        // Format as "Mon 7:40 PM ET" (user-local zone)
                        // — keeps the row compact while still useful.
                        const day = d.toLocaleDateString(undefined, { weekday: 'short' });
                        const time = d.toLocaleTimeString(undefined, {
                          hour: 'numeric', minute: '2-digit',
                        });
                        return `${day} ${time}`;
                      } catch {
                        return null;
                      }
                    })()}
                  </span>
                )}
              </div>
            )}
            <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-[0.12em] truncate mt-0.5 flex items-center gap-1.5">
              <span className="truncate">{sideLabel} · {line} · {formatStatType(stat_type)}</span>
              <MarketGapBadge pick={player} />
            </div>
          </div>
        </div>

        {/* PRIMARY — direction + line + stat on the left, odds on the
            right at the same typographic weight so the chip fills the
            previously-empty negative space next to the title. The
            title AND odds both render in the active tier color
            (theme.text: green=Safe Haven, yellow=Front Lines,
            red=War Zone/Minefield) so the card reads as a single
            tier-glow signal rather than splitting on OVER/UNDER. */}
        <div className={`flex items-baseline justify-between gap-3 mb-2 ${theme.text} leading-none`}>
          <div className="min-w-0">
            <span className="text-3xl md:text-2xl font-extrabold tracking-tight">{sideLabel} {line}</span>
            <span className="ml-1.5 text-xs md:text-[11px] font-mono font-semibold text-zinc-400 uppercase tracking-wider">
              {formatStatType(stat_type)}
            </span>
          </div>
          {(() => {
            const { odds, sourceLabel } = resolveDisplayOdds(player);
            return (
              <div
                className="flex items-baseline gap-1.5 shrink-0 leading-none"
                data-testid={`player-odds-${playerSlug}`}
                title={`Reference book: ${sourceLabel}`}
              >
                <span className={`text-3xl md:text-2xl font-extrabold tracking-tight font-mono tabular-nums ${odds == null ? 'text-zinc-500' : theme.text}`}>
                  {formatAmericanOdds(odds)}
                </span>
                <span className="text-xs md:text-[11px] font-mono font-semibold text-zinc-400 uppercase tracking-wider">
                  {sourceLabel}
                </span>
              </div>
            );
          })()}
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

        {/* FLAT STAT STRIP — Projection / Hit Rate / Avg (terminal label style)
            UNIVERSAL DASHBOARD CARD CONTRACT (2026-04-28): card prefers
            backend-stamped `projection` / `avg` (8-field contract).
            2026-05-07 P0 Phase 4B: legacy active-side `hit_rate` field
            removed from the contract — the card now derives the
            displayed hit rate from canonical `hit_rate_over` /
            `hit_rate_under` (gated by `side`/`recommendation`),
            falling back to the windowed L20 if those are missing. */}
        <div className="flex items-stretch gap-3 pt-1.5 border-t border-zinc-800/70 text-left">
          {(() => {
            const projection = player.projection ?? player.vk_predicted ?? null;
            const _side = String(player.side || player.recommendation || 'OVER').toUpperCase();
            const hitRate =
              (_side === 'UNDER'
                ? player.hit_rate_under ?? player.hit_rate_l20
                : player.hit_rate_over  ?? player.hit_rate_l20) ?? null;
            const avg        = player.avg        ?? season_avg ?? null;
            return (
              <>
                <div className="flex-1 min-w-0">
                  <div className="text-[9px] font-mono uppercase tracking-[0.15em] text-zinc-500 mb-0.5">Projection</div>
                  <div
                    className={`text-sm md:text-[15px] font-bold font-mono tabular-nums ${
                      projection == null ? 'text-zinc-400'
                        : dispEdge != null && dispEdge >= 1 ? 'text-emerald-400'
                        : dispEdge != null && dispEdge <= -1 ? 'text-red-400'
                        : 'text-zinc-200'
                    }`}
                    title={dispEdge != null ? `Edge vs line: ${dispEdge > 0 ? '+' : ''}${dispEdge.toFixed(2)}` : undefined}
                    data-testid={`player-projection-${playerSlug}`}
                  >
                    {projection != null ? Number(projection).toFixed(1) : '—'}
                  </div>
                </div>
                <div className="flex-[2] min-w-0">
                  {/* 2026-05-01 — Hit-rate window trio (L20 / L10 / L5)
                      laid out as three equal sub-columns. L20 is the
                      gate input; L10 is graph parity; L5 is the recent-
                      form sub-gate input. Equal visual weight makes the
                      gate decision auditable at a glance. */}
                  {(() => {
                    const l20 = player.hit_rate_l20 ?? hitRate ?? null;
                    const l10 = player.hit_rate_l10 ?? null;
                    const l5  = player.hit_rate_l5  ?? null;
                    const Cell = ({ label, rate, title, testid }) => (
                      <div className="flex-1 min-w-0">
                        <div className="text-[9px] font-mono uppercase tracking-[0.15em] text-zinc-500 mb-0.5">
                          {label}
                        </div>
                        <div
                          className={`text-sm md:text-[15px] font-bold font-mono tabular-nums ${rate != null ? getHitRateColor(rate) : 'text-zinc-500'}`}
                          title={title}
                          data-testid={testid}
                        >
                          {rate != null ? `${Math.round(rate)}%` : '—'}
                        </div>
                      </div>
                    );
                    return (
                      <div className="flex gap-3">
                        <Cell label="L20" rate={l20}
                              title="L20 hit rate — what the gate evaluates"
                              testid={`player-hit-rate-l20-${playerSlug}`} />
                        <Cell label="L10" rate={l10}
                              title="L10 hit rate — graph parity window"
                              testid={`player-hit-rate-l10-${playerSlug}`} />
                        <Cell label="L5" rate={l5}
                              title="L5 hit rate — recent-form sub-gate"
                              testid={`player-hit-rate-l5-${playerSlug}`} />
                      </div>
                    );
                  })()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[9px] font-mono uppercase tracking-[0.15em] text-zinc-500 mb-0.5">Avg</div>
                  <div className="text-sm md:text-[15px] font-bold font-mono tabular-nums text-white">
                    {avg != null ? Number(avg).toFixed(1) : '—'}
                  </div>
                </div>
              </>
            );
          })()}
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
              <PlayerHeadshot photoUrl={displayPhoto} playerName={displayName} team={team} size="lg" sport={playerSport} mlbId={mlbId} teamLogoUrl={player.team_logo_url} />
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
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className={`text-sm font-bold ${theme.text}`}>{formatStatType(stat_type)} {line}</span>
                {/* Odds chip — DK→FD→MGM→BOL chain (tier_reference_*). */}
                {(() => {
                  const { odds, sourceLabel } = resolveDisplayOdds(player);
                  return (
                    <span
                      className="inline-flex items-center gap-1 font-mono tabular-nums"
                      data-testid={`primary-prop-odds-${playerSlug}`}
                      title={`Reference book: ${sourceLabel}`}
                    >
                      <span className="text-[9px] uppercase tracking-[0.12em] text-zinc-500">{sourceLabel}</span>
                      <span className={`text-xs font-bold ${odds == null ? 'text-zinc-500' : theme.text}`}>
                        {formatAmericanOdds(odds)}
                      </span>
                    </span>
                  );
                })()}
                {/* 2026-05-07 P0 Phase 4B: canonical-only L10 read.
                    Backend no longer ships legacy `h10_rate` on tier
                    picks. */}
                {hit_rate_l10 != null && (
                  <span className={`text-xs ${getHitRateColor(hit_rate_l10)}`} data-testid={`hr-l10-chip-${player.player_name || ''}`}>
                    L10: {Math.round(hit_rate_l10)}%
                  </span>
                )}
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
