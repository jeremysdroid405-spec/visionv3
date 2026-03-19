/**
 * PickVision Dashboard - Main Controller
 * 
 * SSOT Two-Pipe Architecture with TanStack Query
 * - PIPE 1: useMasterStats (Stats Vault - 24hr cache)
 * - PIPE 2: useWarZone, useSafeHaven, useFrontLines (Live Wire - 30s polling)
 * 
 * HIGHLANDER PROTOCOL: useDFSData DELETED - all data via TanStack Query
 */
import React, { useState, useCallback, useEffect, memo, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { toast } from 'sonner';

// Centralized styles
import '../styles/components.css';

// UI Components
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { 
  Search, X, LogOut, Crown, User, Radio, AlertTriangle, Activity, 
  ChevronRight, Eye, Zap, ChevronDown, Flame, ArrowLeft, Target,
  TrendingUp, Newspaper, Clock, Crosshair
} from 'lucide-react';

// Dashboard Components
import { DemonIcon, GoblinIcon, VisionBadge } from '../components/dashboard/Icons';
import UniversalPlayerCard from '../components/dashboard/UniversalPlayerCard';
import { ParlayTicket } from '../components/dashboard/ParlayTicket';
import { PlayerDetailPage } from '../components/dashboard/PlayerDetailPage';
import CommandPost from '../components/dashboard/CommandPost';
import { 
  TEAM_LOGOS, STAT_CATEGORIES, getCategoryKey 
} from '../components/dashboard/constants';

// SSOT Global State - TanStack Query hooks ONLY
// PURGED: useDFSData - DELETED per Highlander Protocol
import { buildMasterParlayTickets } from '../logic/matrixEngine';
import { 
  useLiveScores, 
  useBreakingNews, 
  usePlayerSearch,
  useWarZone,
  useSafeHaven,
  useFrontLines,
  useLiveOdds,
  useMostPopularBets
} from '../hooks/useLiveOdds';
import { useMasterStats } from '../hooks/useMasterStats';

// ==================== HELPER COMPONENTS ====================

// Player Headshot - Uses photo_url from nba_master_hub_2026 (no external API calls on render)
// Note: NBA CDN may block requests from certain environments - fallback to team logo/initials
const PlayerHeadshot = memo(({ playerName, team, photoUrl, size = 'md', className = '' }) => {
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const sizeClasses = { sm: 'w-8 h-8', md: 'w-12 h-12', lg: 'w-16 h-16', xl: 'w-24 h-24' };
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  
  const isValidPhotoUrl = photoUrl && !photoUrl.includes('nophoto');
  const teamLogoUrl = team ? TEAM_LOGOS[team] : null;
  
  // Get player initials for fallback
  const initials = playerName ? playerName.split(' ').map(n => n[0]).join('').slice(0, 2) : '?';
  
  // Fallback component
  const FallbackDisplay = () => {
    if (teamLogoUrl) {
      return (
        <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1.5 ${className}`}>
          <img src={teamLogoUrl} alt={team} className="w-full h-full object-contain" onError={(e) => e.target.style.display = 'none'} />
        </div>
      );
    }
    return (
      <div className={`${sizeClass} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 flex items-center justify-center ${className}`}>
        <span className="text-zinc-400 font-bold text-xs">{initials}</span>
      </div>
    );
  };
  
  if (!isValidPhotoUrl || error) {
    return <FallbackDisplay />;
  }
  
  return (
    <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 relative ${className}`}>
      {!loaded && <FallbackDisplay />}
      <img 
        src={photoUrl} 
        alt={playerName} 
        onError={() => setError(true)} 
        onLoad={() => setLoaded(true)}
        className={`w-full h-full object-cover absolute inset-0 ${loaded ? 'opacity-100' : 'opacity-0'}`} 
        style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }} 
      />
    </div>
  );
});

// Locked Badge for games in progress
const LockedBadge = memo(({ isLocked }) => {
  if (!isLocked) return null;
  return (
    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm z-10 flex flex-col items-center justify-center rounded-lg">
      <div className="flex items-center gap-2 px-4 py-2 bg-red-500/30 rounded-full border border-red-500/50">
        <span className="text-red-400 font-bold text-sm">LOCKED</span>
      </div>
      <span className="text-zinc-400 text-xs mt-2">Game In Progress</span>
    </div>
  );
});

// Player Row for search results
const PlayerRow = memo(({ player, onClick, linesLoaded }) => (
  <div 
    className="flex items-center gap-3 p-3 hover:bg-zinc-800/50 cursor-pointer border-b border-zinc-800/50 last:border-0"
    onClick={onClick}
    data-testid={`player-row-${player.player_name?.replace(/\s/g, '-')}`}
  >
    <PlayerHeadshot playerName={player.player_name} team={player.team} photoUrl={player.photo_url} size="md" />
    <div className="flex-1 min-w-0">
      <div className="font-medium text-white truncate">{player.player_name}</div>
      <div className="text-xs text-zinc-500">{player.team}</div>
    </div>
    <div className="flex items-center gap-2">
      {player.demons_count > 0 && (
        <Badge className="bg-red-500/20 text-red-400 border-none text-xs">{player.demons_count} Demons</Badge>
      )}
      {player.goblins_count > 0 && (
        <Badge className="bg-green-500/20 text-green-400 border-none text-xs">{player.goblins_count} Goblins</Badge>
      )}
    </div>
  </div>
));

// ==================== SECTION COMPONENTS ====================

// Section Header
const SectionHeader = memo(({ icon, title, subtitle, badgeText, badgeColor = 'red' }) => {
  const badgeColors = {
    red: 'bg-red-500/20 text-red-400 border-red-500/30',
    amber: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    green: 'bg-green-500/20 text-green-400 border-green-500/30'
  };
  
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        {icon && (
          <div className="w-8 h-8 rounded-full flex items-center justify-center bg-zinc-800/50 border border-zinc-700">
            {icon}
          </div>
        )}
        <div>
          <span className="text-sm font-bold text-white">{title}</span>
          {subtitle && <p className="text-[10px] text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {badgeText && (
        <div className={`px-2 py-1 rounded text-[10px] font-bold border ${badgeColors[badgeColor]}`}>
          {badgeText}
        </div>
      )}
    </div>
  );
});

// ==================== LIVE TICKERS ====================

// Live Scores Ticker - PIPE 2: useLiveScores()
const LiveScoresTicker = memo(() => {
  const { data, isLoading } = useLiveScores();
  const scores = data?.games || [];
  
  if (isLoading) {
    return (
      <div className="bg-black/80 border-y border-zinc-700/30 py-2 px-4">
        <div className="flex items-center gap-2 text-zinc-300 text-xs">
          <Activity className="w-4 h-4 animate-pulse" />
          <span className="font-medium">Loading scores...</span>
        </div>
      </div>
    );
  }
  
  if (!scores.length) {
    return (
      <div className="bg-black/80 border-y border-zinc-700/30 py-2 px-4">
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 px-2 py-1 bg-zinc-800 rounded">
            <div className="w-2 h-2 rounded-full bg-zinc-500" />
            <span className="text-[10px] font-bold text-zinc-300">NO GAMES</span>
          </div>
          <span className="text-xs text-zinc-500">Check back at tip-off</span>
        </div>
      </div>
    );
  }
  
  return (
    <div className="bg-black/80 border-y border-zinc-700/30 py-1.5 overflow-hidden" data-testid="live-scores-ticker">
      <div className="ticker-scroll">
        <div className="ticker-content items-center">
          {/* LIVE indicator */}
          <div className="flex items-center gap-2 px-4 border-r border-zinc-700/50">
            <div className="flex items-center gap-1.5 px-2 py-1 bg-red-600 rounded animate-pulse">
              <div className="w-2 h-2 rounded-full bg-white" />
              <span className="text-[11px] font-black text-white">LIVE</span>
            </div>
          </div>
          {[...scores, ...scores].map((game, idx) => {
            const isLive = game.status?.startsWith('Q') || game.status === 'live';
            const isFinal = game.status === 'final';
            
            // Determine winner and loser
            const awayWins = game.away_score > game.home_score;
            const winnerTeam = awayWins ? game.away_team : game.home_team;
            const loserTeam = awayWins ? game.home_team : game.away_team;
            const winnerScore = awayWins ? game.away_score : game.home_score;
            const loserScore = awayWins ? game.home_score : game.away_score;
            
            return (
              <div key={`score-${idx}`} className={`flex items-center gap-2.5 px-4 py-1.5 border-r border-zinc-700/50 ${isLive ? 'bg-zinc-800/50' : ''}`}>
                {/* Winner First */}
                <img src={TEAM_LOGOS[winnerTeam]} alt={winnerTeam} className="w-5 h-5 flex-shrink-0" onError={(e) => e.target.style.display='none'} />
                <span className="text-sm font-bold text-white">
                  {winnerTeam}
                </span>
                <span className="text-base font-black text-white">
                  {winnerScore}
                </span>
                
                <span className="text-zinc-600 text-xs">-</span>
                
                {/* Loser Second */}
                <span className="text-base font-black text-red-400">
                  {loserScore}
                </span>
                <span className="text-sm font-bold text-white">
                  {loserTeam}
                </span>
                <img src={TEAM_LOGOS[loserTeam]} alt={loserTeam} className="w-5 h-5 flex-shrink-0" onError={(e) => e.target.style.display='none'} />
                
                {/* Status Badge - separated with margin */}
                <Badge className={`text-[10px] font-bold px-2 py-0.5 ml-1 ${
                  isLive ? 'bg-red-600 text-white border-red-500 animate-pulse' :
                  isFinal ? 'bg-red-500/30 text-red-400 border-red-500/30' :
                  'bg-amber-500/30 text-amber-300 border-amber-500/30'
                }`}>
                  {game.status?.toUpperCase() || game.period}
                </Badge>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
});

// Breaking News Ticker - PIPE 2: useBreakingNews()
const BreakingNewsTicker = memo(() => {
  const { data } = useBreakingNews();
  const news = data?.headlines || [];
  
  // Default headlines if API not available
  const displayNews = news.length > 0 ? news : [
    { text: "NBA injury reports updated hourly", type: "info" },
    { text: "Line movements tracked in real-time", type: "info" },
    { text: "AI insights refresh with each game", type: "info" }
  ];
  
  return (
    <div className="bg-red-950/30 border-b border-red-900/30 py-2 overflow-hidden" data-testid="news-ticker">
      <div className="news-ticker-scroll">
        <div className="news-ticker-content">
          {[...displayNews, ...displayNews, ...displayNews].map((item, idx) => (
            <div key={`news-${idx}`} className="flex items-center gap-2.5 px-8">
              {item.type === 'breaking' ? (
                <span className="px-2 py-0.5 bg-white text-red-950 text-[11px] font-bold rounded">BREAKING</span>
              ) : item.type === 'injury' ? (
                <AlertTriangle className="w-4 h-4 text-white" />
              ) : (
                <Newspaper className="w-4 h-4 text-white" />
              )}
              <span className="text-sm text-white whitespace-nowrap">{item.text}</span>
              <span className="text-white text-lg mx-2">•</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});

// Swipe Container
const SwipeContainer = memo(({ children, className = '' }) => (
  <div className={`swipe-container ${className}`}>
    {children}
  </div>
));

// War Zone Section (Demons)
// ==================== LOADING SKELETONS ====================

// Loading skeleton for pick cards
const PickCardSkeleton = () => (
  <div className="swipe-card">
    <div className="p-3 bg-zinc-900/50 border border-zinc-800/50 rounded-lg animate-pulse">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-12 h-12 bg-zinc-800 rounded-full" />
        <div className="flex-1">
          <div className="h-4 bg-zinc-800 rounded w-24 mb-2" />
          <div className="h-3 bg-zinc-800/50 rounded w-16" />
        </div>
      </div>
      <div className="flex gap-2">
        <div className="h-6 bg-zinc-800 rounded-full w-16" />
        <div className="h-6 bg-zinc-800/50 rounded w-12" />
      </div>
      <div className="mt-3 flex justify-between">
        <div className="h-3 bg-zinc-800/50 rounded w-20" />
        <div className="h-3 bg-zinc-800/50 rounded w-16" />
      </div>
    </div>
  </div>
);

// Loading section skeleton
const SectionLoadingSkeleton = ({ title, icon, subtitle }) => (
  <div className="mb-4">
    <SectionHeader icon={icon} title={title} subtitle={subtitle} badgeText="LOADING..." badgeColor="zinc" />
    <SwipeContainer>
      {[1, 2, 3, 4, 5].map((i) => (
        <PickCardSkeleton key={`skeleton-${i}`} />
      ))}
    </SwipeContainer>
  </div>
);

// Empty state component
const EmptyStateMessage = ({ icon, title, message }) => (
  <div className="flex flex-col items-center justify-center py-8 text-center">
    <div className="w-12 h-12 rounded-full bg-zinc-800/50 flex items-center justify-center mb-3">
      {icon}
    </div>
    <p className="text-sm font-medium text-zinc-400">{title}</p>
    <p className="text-xs text-zinc-500 mt-1">{message}</p>
  </div>
);

// War Zone Section with Loading/Empty States
const WarZoneSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return <SectionLoadingSkeleton 
      icon={null} 
      title="WAR ZONE" 
      subtitle="Loading demon plays..." 
    />;
  }
  
  if (!picks?.length) {
    return (
      <div className="war-zone-section mb-4">
        <SectionHeader 
          icon={null}
          title="WAR ZONE"
          subtitle="High-risk, high-reward demon plays"
          badgeText="NO GAMES"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<AlertTriangle className="w-5 h-5 text-zinc-500" />}
          title="No Demon Picks Available"
          message="No games today or data still syncing"
        />
      </div>
    );
  }
  
  return (
    <div className="war-zone-section mb-4">
      <SectionHeader 
        icon={null}
        title="WAR ZONE"
        subtitle="High-risk, high-reward demon plays"
        badgeText={`TOP ${Math.min(10, picks.length)} DEMONS`}
        badgeColor="red"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`warzone-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              rank={idx + 1} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="red"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Safe Haven Section with Loading/Empty States
const SafeHavenSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return <SectionLoadingSkeleton 
      icon={null} 
      title="SAFE HAVEN" 
      subtitle="Loading plays..." 
    />;
  }
  
  if (!picks?.length) {
    return (
      <div className="goblin-recon-section mb-4">
        <SectionHeader 
          icon={null}
          title="SAFE HAVEN"
          subtitle="High-floor plays with best consistency"
          badgeText="NO GAMES"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<Activity className="w-5 h-5 text-zinc-500" />}
          title="No Goblin Picks Available"
          message="No games today or data still syncing"
        />
      </div>
    );
  }
  
  return (
    <div className="goblin-recon-section mb-4">
      <SectionHeader 
        icon={null}
        title="SAFE HAVEN"
        subtitle="High-floor plays with best consistency"
        badgeText="TOP 10 SAFEST PLAYS"
        badgeColor="green"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`safehaven-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              rank={idx + 1} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="green"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Front Lines Section with Loading/Empty States
const FrontLinesSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return <SectionLoadingSkeleton 
      icon={null} 
      title="FRONT LINES" 
      subtitle="Loading tactical plays..." 
    />;
  }
  
  if (!picks?.length) {
    return (
      <div className="front-lines-section mb-4">
        <SectionHeader 
          icon={null}
          title="FRONT LINES"
          subtitle="Balanced demon/goblin mix for tactical plays"
          badgeText="NO GAMES"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<Target className="w-5 h-5 text-zinc-500" />}
          title="No Front Line Picks Available"
          message="No games today or data still syncing"
        />
      </div>
    );
  }
  
  return (
    <div className="front-lines-section mb-4">
      <SectionHeader 
        icon={null}
        title="FRONT LINES"
        subtitle="Balanced demon/goblin mix for tactical plays"
        badgeText={`${Math.min(10, picks.length)} PICKS`}
        badgeColor="amber"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`frontlines-${pick.player_name}-${pick.stat_type}-${pick.photo_url || 'nophoto'}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              rank={idx + 1} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="yellow"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Parlay Section Builder
const ParlaySection = memo(({ picks, onParlayClick, sectionName, title, subtitle, icon, badgeColor }) => {
  const tickets = React.useMemo(() => {
    if (!picks?.length || picks.length < 2) return {};
    return buildMasterParlayTickets(picks, { sectionName });
  }, [picks, sectionName]);
  
  if (Object.keys(tickets).length === 0) return null;
  
  return (
    <div className={`${sectionName === 'safe_haven' ? 'goblin-recon-section' : sectionName === 'war_zone' ? 'war-zone-section' : 'front-lines-section'} mb-4`}>
      <SectionHeader icon={icon} title={title} subtitle={subtitle} badgeText="PARLAYS" badgeColor={badgeColor} />
      <SwipeContainer>
        {Object.entries(tickets).map(([size, ticket]) => (
          <div key={`parlay-${sectionName}-${size}`} className="swipe-card">
            <ParlayTicket ticket={ticket} onClick={() => onParlayClick(ticket, sectionName)} sectionType={sectionName} />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// ==================== EXPANDED PARLAY MODAL ====================

const ExpandedParlayView = memo(({ parlay, sectionType, onClose, onPickClick }) => {
  if (!parlay) return null;
  
  const picks = parlay.picks || [];
  const isGoblin = sectionType === 'safe_haven';
  
  const colors = {
    safe_haven: { bg: 'from-green-950/90', border: 'border-green-500/50', text: 'text-green-400' },
    war_zone: { bg: 'from-red-950/90', border: 'border-red-500/50', text: 'text-red-400' },
    front_lines: { bg: 'from-amber-950/90', border: 'border-amber-500/50', text: 'text-amber-400' }
  };
  const theme = colors[sectionType] || colors.war_zone;
  
  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div 
        className={`bg-gradient-to-br ${theme.bg} to-zinc-900 border ${theme.border} rounded-2xl max-w-lg w-full max-h-[80vh] overflow-y-auto`}
        onClick={e => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-zinc-900/95 backdrop-blur-sm p-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isGoblin ? <GoblinIcon size={24} /> : <DemonIcon size={24} />}
            <div>
              <h3 className={`font-bold ${theme.text}`}>{parlay.name}</h3>
              <p className="text-xs text-zinc-500">{parlay.description}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-zinc-800 rounded-lg">
            <X className="w-5 h-5 text-zinc-400" />
          </button>
        </div>
        
        <div className="p-4 space-y-3">
          {picks.map((pick, idx) => (
            <UniversalPlayerCard 
              key={`expanded-${pick.player_name}-${pick.stat_type}-${idx}`}
              player={pick}
              rank={idx + 1}
              mode="compact"
              onClick={() => onPickClick(pick)}
              showStats={false}
              showProps={false}
            />
          ))}
        </div>
        
        <div className="sticky bottom-0 bg-zinc-900/95 backdrop-blur-sm p-4 border-t border-zinc-800">
          <div className="flex items-center justify-between text-sm">
            <div>
              <span className="text-zinc-500">Combined Prob:</span>
              <span className="ml-2 text-white font-bold">{parlay.combined_probability?.toFixed(1)}%</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});

// ==================== POPULAR BETS SECTION ====================

const PopularBetCard = memo(({ bet, rank, onClick }) => {
  const isDemon = bet.is_demon || bet.pick_type === 'demon';
  const isGoblin = bet.is_goblin || bet.pick_type === 'goblin';
  
  const getHitRateColor = (rate) => {
    if (rate >= 80) return 'text-green-400';
    if (rate >= 60) return 'text-yellow-400';
    return 'text-red-400';
  };
  
  return (
    <Card 
      className={`p-3 cursor-pointer hover:scale-[1.02] transition-all min-w-[220px] ${
        isDemon ? 'bg-red-950/30 border-red-500/30' : 
        isGoblin ? 'bg-green-950/30 border-green-500/30' : 
        'bg-zinc-900 border-zinc-800'
      }`}
      onClick={onClick}
    >
      <div className="flex items-center gap-2 mb-2">
        <div className="relative">
          <PlayerHeadshot playerName={bet.player_name} team={bet.team} photoUrl={bet.photo_url} size="sm" />
          <div className="absolute -top-1 -right-1">
            {isDemon ? <DemonIcon size={14} /> : isGoblin ? <GoblinIcon size={14} /> : null}
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white truncate">{bet.player_name}</div>
          <div className="text-xs text-zinc-500">{bet.stat_type} {bet.line}</div>
        </div>
        <Badge className="bg-zinc-800 text-zinc-300 border-none text-xs">#{rank}</Badge>
      </div>
      
      {/* L5 Avg / L10 Hit Rate / Season Avg */}
      <div className="flex items-center justify-between bg-zinc-800/50 rounded px-2 py-1.5 text-[10px]">
        <div className="text-center">
          <div className="text-zinc-500">L5</div>
          <div className="font-bold text-white">
            {bet.l5_avg != null ? bet.l5_avg.toFixed?.(1) || bet.l5_avg : '---'}
          </div>
        </div>
        <div className="h-4 w-px bg-zinc-700" />
        <div className="text-center">
          <div className="text-zinc-500">L10</div>
          <div className={`font-bold ${getHitRateColor(bet.h10_rate || 0)}`}>
            {bet.h10_rate != null ? `${bet.h10_rate}%` : '---'}
          </div>
        </div>
        <div className="h-4 w-px bg-zinc-700" />
        <div className="text-center">
          <div className="text-zinc-500">Avg</div>
          <div className="font-bold text-white">
            {bet.season_avg != null ? bet.season_avg.toFixed?.(1) || bet.season_avg : '---'}
          </div>
        </div>
      </div>
    </Card>
  );
});

const MostPopularBetsSection = memo(({ bets, status, onBetClick }) => {
  if (status === 'awaiting_action' || !bets?.length) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl text-center">
        <Activity className="w-6 h-6 text-zinc-500 mx-auto mb-2" />
        <p className="text-sm text-zinc-500">Awaiting live action...</p>
        <p className="text-xs text-zinc-600">Top picks will appear when games tip off</p>
      </div>
    );
  }
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<Flame className="w-4 h-4 text-red-400" />}
        title="TOP PICKS"
        subtitle="Best of Safe Haven, Front Lines & War Zone"
      />
      <div className="overflow-x-auto pb-2 -mx-3 px-3">
        <div className="flex gap-3" style={{ minWidth: 'max-content' }}>
          {bets.map((bet, idx) => (
            <PopularBetCard key={`top-pick-${bet.player_name}-${bet.stat_type}-${idx}`} bet={bet} rank={idx + 1} onClick={() => onBetClick(bet)} />
          ))}
        </div>
      </div>
    </div>
  );
});

// ==================== MAIN DASHBOARD ====================

const Dashboard = () => {
  const navigate = useNavigate();
  const { user, logout, isDemo } = useAuth();
  
  // ========== SSOT: TanStack Query Subscriptions ==========
  // PIPE 2: Live Wire - 30s polling for real-time updates
  const { data: warZoneData, isLoading: warZoneLoading, refetch: refetchWarZone } = useWarZone();
  const { data: safeHavenData, isLoading: safeHavenLoading, refetch: refetchSafeHaven } = useSafeHaven();
  const { data: frontLinesData, isLoading: frontLinesLoading, refetch: refetchFrontLines } = useFrontLines();
  const { data: liveOddsData, isLoading: boardLoading, refetch: refetchBoard } = useLiveOdds();
  const { data: mostPopularData, isLoading: popularLoading } = useMostPopularBets();
  
  // Extract picks from TanStack Query data
  const radarPicks = useMemo(() => warZoneData?.picks || [], [warZoneData]);
  const vaultPicks = useMemo(() => safeHavenData?.picks || [], [safeHavenData]);
  const frontLinesPicks = useMemo(() => frontLinesData?.picks || [], [frontLinesData]);
  const players = useMemo(() => liveOddsData?.players || [], [liveOddsData]);
  
  // Top Picks - Best of all sections (4 from each board)
  const trending = useMemo(() => players.slice(0, 8), [players]);
  const popularBets = useMemo(() => mostPopularData?.bets || [], [mostPopularData]);
  const popularBetsStatus = mostPopularData?.status || (popularLoading ? 'loading' : 'awaiting_action');
  
  // Status flags derived from query state
  const linesLoaded = !boardLoading && players.length > 0;
  const staticLoaded = !warZoneLoading && !safeHavenLoading;
  const syncing = boardLoading || warZoneLoading || safeHavenLoading;
  const boardIntelStatus = { 
    demon_count: radarPicks.length, 
    goblin_count: vaultPicks.length,
    total_players: players.length 
  };
  const syncStatus = { last_sync: warZoneData?.synced_at };
  
  // Refetch all data (replaces old triggerSync)
  const triggerSync = useCallback(() => {
    refetchWarZone();
    refetchSafeHaven();
    refetchFrontLines();
    refetchBoard();
    toast.success('Data refreshed');
  }, [refetchWarZone, refetchSafeHaven, refetchFrontLines, refetchBoard]);
  
  // Local UI state
  const [searchTerm, setSearchTerm] = useState('');
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [expandedParlay, setExpandedParlay] = useState(null);
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon');
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [showCommandPost, setShowCommandPost] = useState(false);
  const [savedScrollPosition, setSavedScrollPosition] = useState(null);
  
  // Scroll to top only on initial mount (not when returning from detail views)
  useEffect(() => {
    if (savedScrollPosition === null && !selectedPlayer && !expandedParlay) {
      window.scrollTo(0, 0);
    }
  }, []);
  
  // Intel Search state (API-driven)
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);
  
  // Command Post Quick-Add state
  const [pendingLeg, setPendingLeg] = useState(null);
  
  // Quick-Add handler - opens Command Post and queues the leg
  const handleQuickAdd = useCallback((pick) => {
    setPendingLeg(pick);
    setShowCommandPost(true);
    toast.success(`Added ${pick.player_name} to Command Hub`);
  }, []);
  
  // Called by CommandPost after processing the pending leg
  const handlePendingLegProcessed = useCallback(() => {
    setPendingLeg(null);
  }, []);
  
  // Navigation handlers
  const handlePlayerClick = useCallback((playerName, highlight = null, type = 'demon') => {
    setSavedScrollPosition(window.scrollY);
    setHighlightProp(highlight);
    setHighlightType(type);
    setSelectedPlayer(playerName);
  }, []);
  
  const handleBackFromPlayer = useCallback(() => {
    setSelectedPlayer(null);
    setHighlightProp(null);
    // Restore scroll position after render
    setTimeout(() => {
      if (savedScrollPosition !== null) {
        window.scrollTo(0, savedScrollPosition);
      }
    }, 0);
  }, [savedScrollPosition]);
  
  const handleRadarClick = useCallback((pick) => {
    const lineValue = pick.demon_line || pick.line;
    const highlightKey = `${pick.stat_type}|${lineValue}|${pick.direction || 'Over'}`;
    handlePlayerClick(pick.player_name, highlightKey, 'demon');
  }, [handlePlayerClick]);
  
  const handleVaultClick = useCallback((pick) => {
    const lineValue = pick.goblin_line || pick.line;
    const highlightKey = `${pick.stat_type}|${lineValue}|${pick.direction || 'Over'}`;
    handlePlayerClick(pick.player_name, highlightKey, 'goblin');
  }, [handlePlayerClick]);
  
  const handleParlayClick = useCallback((parlay, sectionType) => {
    setSavedScrollPosition(window.scrollY);
    setExpandedParlay({ parlay, sectionType });
  }, []);
  
  const handlePopularBetClick = useCallback((bet) => {
    const type = bet.is_demon ? 'demon' : bet.is_goblin ? 'goblin' : 'demon';
    handlePlayerClick(bet.player_name, null, type);
  }, [handlePlayerClick]);
  
  const handleLogout = async () => {
    await logout();
    navigate('/auth');
    toast.success('Logged out successfully');
  };
  
  // Close user menu on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (showUserMenu && !e.target.closest('[data-testid="user-menu-btn"]')) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [showUserMenu]);
  
  // PIPE 2: Player Search via usePlayerSearch hook
  const { data: searchData, isLoading: searchQueryLoading } = usePlayerSearch(searchTerm);
  
  // Sync search results from TanStack Query
  useEffect(() => {
    if (searchTerm.length < 2) {
      setSearchResults([]);
      setSearchError(null);
      setSearchLoading(false);
      return;
    }
    
    setSearchLoading(searchQueryLoading);
    
    if (searchData?.success) {
      setSearchResults(searchData.players || []);
      setSearchError(null);
    } else if (searchData && !searchData.success) {
      setSearchError(searchData.error || 'Search unavailable');
      setSearchResults([]);
    }
  }, [searchTerm, searchData, searchQueryLoading]);
  
  // If player is selected, show detail page
  if (selectedPlayer) {
    return (
      <PlayerDetailPage 
        playerName={selectedPlayer}
        onBack={handleBackFromPlayer}
        highlightProp={highlightProp}
        highlightType={highlightType}
      />
    );
  }
  
  return (
    <div className="min-h-screen bg-zinc-950 pb-16">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Crosshair className="w-8 h-8 text-yellow-500" />
            <div>
              <h1 className="text-2xl font-black tracking-tight text-white">PROPVISION</h1>
              <div className="flex items-center gap-2 text-[10px] text-zinc-500 -mt-0.5">
                <span>AI-POWERED PROP INTEL</span>
                <span className="text-zinc-700">•</span>
                <Radio className={`w-2.5 h-2.5 ${syncStatus.has_stale_intel ? 'text-amber-400' : 'text-emerald-400 animate-pulse'}`} />
                <span className="font-mono">
                  {boardIntelStatus.time_since_sync_display || 'syncing...'}
                </span>
              </div>
            </div>
          </div>
          
          <div className="flex items-center gap-2">
            {/* Command Hub Button */}
            <button 
              onClick={() => setShowCommandPost(true)} 
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-700 border border-zinc-700/50 transition-all"
              data-testid="command-post-btn"
            >
              <Target className="w-5 h-5 text-yellow-500" />
              <span className="text-sm font-medium text-white hidden sm:inline">Command Hub</span>
            </button>
            
            <div className="relative">
              <button onClick={() => setShowUserMenu(!showUserMenu)} className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-700 border border-zinc-700/50 transition-all" data-testid="user-menu-btn">
                <User className="w-5 h-5 text-zinc-400" />
                <span className="text-sm font-medium text-white">{isDemo ? 'Demo' : user?.email?.split('@')[0]}</span>
              </button>
              
              {showUserMenu && (
                <div className="absolute right-0 top-full mt-2 w-48 bg-zinc-900 border border-zinc-800 rounded-lg shadow-xl z-50">
                  <div className="p-1">
                    <button className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-400 hover:text-white hover:bg-zinc-800 rounded">
                      <Crown className="w-4 h-4 text-amber-400" />
                      <span>Upgrade to Pro</span>
                      <Badge className="ml-auto bg-amber-500/20 text-amber-400 border-none text-[10px]">Soon</Badge>
                    </button>
                    <button onClick={handleLogout} className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-400 hover:bg-red-950/30 rounded">
                      <LogOut className="w-4 h-4" />
                      <span>Logout</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>
      
      {/* Live Tickers */}
      <LiveScoresTicker />
      <BreakingNewsTicker />
      
      {/* Intel Search - TOP */}
      <div className="px-3 pt-3">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4" data-testid="intel-search-section">
          <div className="flex items-center gap-2 mb-3">
            <Search className="w-4 h-4 text-red-400" />
            <span className="text-sm font-medium text-white">INTEL SEARCH</span>
            <span className="text-[10px] text-zinc-500">Search any player for tactical profile</span>
          </div>
          
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <Input
              placeholder="Search player name..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 py-2.5 bg-zinc-800/50 border-zinc-700 text-white text-sm focus:border-cyan-500"
              data-testid="intel-search-input"
            />
            {searchTerm && (
              <button onClick={() => setSearchTerm('')} className="absolute right-3 top-1/2 -translate-y-1/2">
                <X className="w-4 h-4 text-zinc-500 hover:text-white" />
              </button>
            )}
          </div>
          
          {/* Search Results - Only show when searching */}
          {searchTerm.length >= 2 && (
            <div className="mt-3 rounded-lg border border-zinc-700 overflow-hidden" data-testid="intel-search-results">
              {searchLoading ? (
                <div className="p-4 text-center">
                  <Activity className="w-5 h-5 text-cyan-400 mx-auto mb-2 animate-pulse" />
                  <p className="text-zinc-400 text-xs">Searching player database...</p>
                </div>
              ) : searchError ? (
                <div className="p-4 text-center">
                  <AlertTriangle className="w-5 h-5 text-amber-400 mx-auto mb-2" />
                  <p className="text-amber-400 text-sm">{searchError}</p>
                </div>
              ) : searchResults.length === 0 ? (
                <div className="p-4 text-center">
                  <p className="text-zinc-500 text-sm">No players found for "{searchTerm}"</p>
                  <p className="text-zinc-600 text-xs mt-1">Try searching by full name or team</p>
                </div>
              ) : (
                <div className="max-h-64 overflow-y-auto">
                  {searchResults.map((player) => (
                    <div 
                      key={player.id || player.player_name}
                      className="flex items-center gap-3 p-3 hover:bg-zinc-800/50 cursor-pointer border-b border-zinc-800/50 last:border-0"
                      onClick={() => handlePlayerClick(player.player_name)}
                      data-testid={`player-row-${player.player_name?.replace(/\s/g, '-')}`}
                    >
                      <PlayerHeadshot playerName={player.player_name} team={player.team} photoUrl={player.photo_url || player.headshot_url} size="md" />
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-white truncate">{player.player_name}</div>
                        <div className="text-xs text-zinc-500">{player.team_name || player.team} • {player.position}</div>
                      </div>
                      <ChevronRight className="w-4 h-4 text-zinc-500" />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      
      {/* Main Content */}
      <div className="p-3 space-y-4">
        {/* Most Popular Bets - FIRST */}
        <MostPopularBetsSection bets={popularBets} status={popularBetsStatus} onBetClick={handlePopularBetClick} />
        
        {/* Safe Haven */}
        <SafeHavenSection picks={vaultPicks} onPickClick={handleVaultClick} onQuickAdd={handleQuickAdd} isLoading={safeHavenLoading} />
        
        {/* Shield Parlays */}
        <ParlaySection 
          picks={vaultPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="safe_haven"
          title="THE SHIELD"
          subtitle="Safe Haven parlay combinations"
          badgeColor="green"
        />
        
        {/* Front Lines */}
        <FrontLinesSection picks={frontLinesPicks} onPickClick={handleRadarClick} onQuickAdd={handleQuickAdd} isLoading={frontLinesLoading} />
        
        {/* Strike Parlays */}
        <ParlaySection 
          picks={frontLinesPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="front_lines"
          title="THE STRIKE"
          subtitle="Front Lines parlay combinations"
          badgeColor="amber"
        />
        
        {/* War Zone */}
        <WarZoneSection picks={radarPicks} onPickClick={handleRadarClick} onQuickAdd={handleQuickAdd} isLoading={warZoneLoading} />
        
        {/* Gauntlet Parlays */}
        <ParlaySection 
          picks={radarPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="war_zone"
          title="THE GAUNTLET"
          subtitle="War Zone parlay combinations"
          badgeColor="red"
        />
      </div>
      
      {/* Expanded Parlay Modal */}
      {expandedParlay && (
        <ExpandedParlayView 
          parlay={expandedParlay.parlay}
          sectionType={expandedParlay.sectionType}
          onClose={() => {
            setExpandedParlay(null);
            // Restore scroll position after render
            setTimeout(() => {
              if (savedScrollPosition !== null) {
                window.scrollTo(0, savedScrollPosition);
              }
            }, 0);
          }}
          onPickClick={(pick) => {
            setExpandedParlay(null);
            const lineValue = pick.demon_line || pick.goblin_line || pick.line;
            const highlightKey = `${pick.stat_type}|${lineValue}|${pick.direction || 'Over'}`;
            const type = expandedParlay.sectionType === 'safe_haven' ? 'goblin' : 'demon';
            handlePlayerClick(pick.player_name, highlightKey, type);
          }}
        />
      )}
      
      {/* Footer */}
      <div className="fixed bottom-0 left-0 right-0 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 px-4 py-2 z-40">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-3">
            <span className="text-zinc-500 font-mono">{boardIntelStatus.time_since_sync_display}</span>
            {boardIntelStatus.last_sync_type && (
              <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                boardIntelStatus.last_sync_type === 'primary' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-blue-500/20 text-blue-400'
              }`}>
                {boardIntelStatus.last_sync_type === 'primary' ? 'FULL SYNC' : 'DELTA'}
              </span>
            )}
          </div>
          <span className="text-zinc-600">PropVision AI</span>
        </div>
      </div>
      
      {/* Command Post Sidebar */}
      <CommandPost 
        isOpen={showCommandPost} 
        onClose={() => setShowCommandPost(false)}
        pendingLeg={pendingLeg}
        onPendingLegProcessed={handlePendingLegProcessed}
      />
    </div>
  );
};

export default Dashboard;
