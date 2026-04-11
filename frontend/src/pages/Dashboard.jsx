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
import { useSport } from '../context/SportContext';
import { toast } from 'sonner';

// Centralized styles
import '../styles/components.css';

// UI Components
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { 
  Search, X, LogOut, Crown, User, Radio, AlertTriangle, Activity, 
  ChevronRight, ChevronLeft, Eye, Zap, ChevronDown, Flame, ArrowLeft, Target,
  TrendingUp, Newspaper, Clock, Crosshair, Lock, Maximize2, ShieldAlert, Info
} from 'lucide-react';

// Dashboard Components
import { DemonIcon, GoblinIcon, VisionBadge } from '../components/dashboard/Icons';
import UniversalPlayerCard from '../components/dashboard/UniversalPlayerCard';
import { ParlayTicket } from '../components/dashboard/ParlayTicket';
import { PlayerDetailPage } from '../components/dashboard/PlayerDetailPage';
import CommandPost from '../components/dashboard/CommandPost';
import IntelligenceModal from '../components/dashboard/IntelligenceModal';
import SportSwitcher from '../components/dashboard/SportSwitcher';
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
  useLiveVacuumAlerts,
  useMLBLiveVacuumAlerts,
  useMLBGoblins,
  useMLBDemons,
  useMLBHRRPicks,
  useMLBSafeHaven,
  useMLBFrontLines,
  useMLBWarZone
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
        <Badge className="bg-red-500/20 text-red-400 border-none text-xs">{player.demons_count} Sharp</Badge>
      )}
      {player.goblins_count > 0 && (
        <Badge className="bg-green-500/20 text-green-400 border-none text-xs">{player.goblins_count} Safe</Badge>
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
                <Badge className={`text-[10px] font-bold px-2 py-0.5 ml-1 whitespace-nowrap ${
                  isLive ? 'bg-red-600 text-white border-red-500 animate-pulse' :
                  isFinal ? 'bg-red-500/30 text-red-400 border-red-500/30' :
                  'bg-amber-500/30 text-amber-300 border-amber-500/30'
                }`}>
                  {(() => {
                    const s = (game.status || game.period || '').toString();
                    // Fix duplicate quarter: "Q4 Q4 4:02" -> "Q4 4:02"
                    const deduped = s.replace(/(Q[1-4]|OT\d?)\s+\1/gi, '$1');
                    return deduped.toUpperCase();
                  })()}
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
  const displayNews = news.length > 0 
    ? news.filter(item => item.type !== 'injury')  // Filter out injury items
    : [
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

// Swipe Container with mobile swipe indicator
const SwipeContainer = memo(({ children, className = '', itemCount = 0 }) => {
  const containerRef = React.useRef(null);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [showIndicator, setShowIndicator] = React.useState(true);
  const childCount = itemCount || React.Children.count(children);
  
  // Handle scroll to update active index
  const handleScroll = React.useCallback(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const scrollLeft = container.scrollLeft;
    const itemWidth = container.clientWidth;
    const newIndex = Math.round(scrollLeft / itemWidth);
    setActiveIndex(newIndex);
    // Hide indicator after first scroll
    if (scrollLeft > 10) setShowIndicator(false);
  }, []);
  
  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);
  
  return (
    <div className="relative">
      {/* Swipe hint gradient - only on mobile */}
      {showIndicator && childCount > 1 && (
        <div className="sm:hidden absolute right-0 top-0 bottom-0 w-12 bg-gradient-to-l from-zinc-950 via-zinc-950/50 to-transparent z-10 pointer-events-none flex items-center justify-end pr-1">
          <div className="flex flex-col items-center gap-1 animate-pulse">
            <ChevronRight className="w-5 h-5 text-zinc-500" />
            <span className="text-[8px] text-zinc-600 font-medium">SWIPE</span>
          </div>
        </div>
      )}
      
      <div ref={containerRef} className={`swipe-container ${className}`}>
        {children}
      </div>
      
      {/* Dot indicators - only on mobile when more than 1 item */}
      {childCount > 1 && (
        <div className="sm:hidden flex justify-center gap-1.5 mt-2">
          {Array.from({ length: Math.min(childCount, 10) }).map((_, idx) => (
            <div
              key={idx}
              className={`h-1.5 rounded-full transition-all duration-200 ${
                idx === activeIndex 
                  ? 'w-4 bg-yellow-500' 
                  : 'w-1.5 bg-zinc-700'
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
});

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
      subtitle="Loading sharp edge plays..." 
    />;
  }
  
  if (!picks?.length) {
    return (
      <div className="war-zone-section mb-4">
        <SectionHeader 
          icon={null}
          title="WAR ZONE"
          subtitle="High-risk, high-reward sharp edges"
          badgeText="NO GAMES"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<AlertTriangle className="w-5 h-5 text-zinc-500" />}
          title="No Sharp Picks Available"
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
        subtitle="High-risk, high-reward sharp edges"
        badgeText={`TOP ${Math.min(10, picks.length)} SHARP`}
        badgeColor="red"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`warzone-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="red"
              forceTheme="DEMON"
              isBoardPick={true}
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
          title="No Safe Picks Available"
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
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="green"
              forceTheme="GOBLIN"
              isBoardPick={true}
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
          subtitle="Sharp money +EV plays"
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
        subtitle="Sharp money +EV plays"
        badgeText={`${Math.min(10, picks.length)} PICKS`}
        badgeColor="amber"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`frontlines-${pick.player_name}-${pick.stat_type}-${pick.photo_url || 'nophoto'}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="yellow"
              forceTheme="FRONT_LINE"
              isBoardPick={true}
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// ==================== TRAP GRAVEYARD SECTION ====================

// ==================== MLB SAFE HAVEN SECTION (Sharp Goblins) ====================
const MLBSafeHavenSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return (
      <div className="goblin-recon-section mb-4">
        <SectionHeader 
          icon={<GoblinIcon size={18} />}
          title="SAFE HAVEN"
          subtitle="High-floor plays with best consistency"
          badgeText="LOADING"
          badgeColor="green"
        />
        <div className="flex justify-center py-8">
          <div className="animate-pulse text-green-400">Loading Safe Haven...</div>
        </div>
      </div>
    );
  }
  
  if (!picks?.length) {
    return (
      <div className="goblin-recon-section mb-4">
        <SectionHeader 
          icon={<GoblinIcon size={18} />}
          title="SAFE HAVEN"
          subtitle="High-floor plays with best consistency"
          badgeText="0 PICKS"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<GoblinIcon size={20} />}
          title="No Safe Haven Picks"
          message="Run Sharp Sort to classify picks"
        />
      </div>
    );
  }
  
  return (
    <div className="goblin-recon-section mb-4">
      <SectionHeader 
        icon={<GoblinIcon size={18} />}
        title="SAFE HAVEN"
        subtitle="High-floor plays with best consistency"
        badgeText={`${picks.length} PICKS`}
        badgeColor="green"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`mlb-safe-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="green"
              forceTheme="GOBLIN"
              isBoardPick={true}
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// ==================== MLB FRONT LINES SECTION (HRR Combos) ====================
const MLBFrontLinesSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return (
      <div className="front-lines-section mb-4">
        <SectionHeader 
          icon={<Target className="w-4 h-4 text-amber-400" />}
          title="THE FRONT LINES"
          subtitle="Hits+Runs+RBIs combo plays"
          badgeText="LOADING"
          badgeColor="amber"
        />
        <div className="flex justify-center py-8">
          <div className="animate-pulse text-amber-400">Loading Front Lines...</div>
        </div>
      </div>
    );
  }
  
  if (!picks?.length) {
    return (
      <div className="front-lines-section mb-4">
        <SectionHeader 
          icon={<Target className="w-4 h-4 text-amber-400" />}
          title="THE FRONT LINES"
          subtitle="Hits+Runs+RBIs combo plays"
          badgeText="0 PICKS"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<Target className="w-5 h-5 text-zinc-500" />}
          title="No Front Line Picks"
          message="Run Sharp Sort to classify picks"
        />
      </div>
    );
  }
  
  return (
    <div className="front-lines-section mb-4">
      <SectionHeader 
        icon={<Target className="w-4 h-4 text-amber-400" />}
        title="THE FRONT LINES"
        subtitle="Hits+Runs+RBIs high value"
        badgeText={`${Math.min(10, picks.length)} PICKS`}
        badgeColor="amber"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`mlb-front-${pick.player_name}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="yellow"
              forceTheme="FRONT_LINE"
              isBoardPick={true}
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// ==================== MLB WAR ZONE SECTION (Demons) ====================
const MLBWarZoneSection = memo(({ picks, onPickClick, onQuickAdd, isLoading }) => {
  if (isLoading) {
    return (
      <div className="war-zone-section mb-4">
        <SectionHeader 
          icon={<DemonIcon size={18} />}
          title="THE WAR ZONE"
          subtitle="DK/PP line discrepancy plays"
          badgeText="LOADING"
          badgeColor="red"
        />
        <div className="flex justify-center py-8">
          <div className="animate-pulse text-red-400">Loading War Zone...</div>
        </div>
      </div>
    );
  }
  
  if (!picks?.length) {
    return (
      <div className="war-zone-section mb-4">
        <SectionHeader 
          icon={<DemonIcon size={18} />}
          title="THE WAR ZONE"
          subtitle="DK/PP line discrepancy plays"
          badgeText="0 PICKS"
          badgeColor="zinc"
        />
        <EmptyStateMessage 
          icon={<DemonIcon size={20} />}
          title="No War Zone Picks"
          message="Run Sharp Sort to classify picks"
        />
      </div>
    );
  }
  
  return (
    <div className="war-zone-section mb-4">
      <SectionHeader 
        icon={<DemonIcon size={18} />}
        title="THE WAR ZONE"
        subtitle="DK ≠ PP line + high edge"
        badgeText={`${Math.min(10, picks.length)} PICKS`}
        badgeColor="red"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`mlb-war-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <UniversalPlayerCard 
              player={pick} 
              onClick={() => onPickClick(pick)} 
              onQuickAdd={onQuickAdd}
              showStats={true}
              showProps={false}
              mode="compact"
              sectionColor="red"
              forceTheme="DEMON"
              isBoardPick={true}
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Trap Card - Shows warning badges prominently


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
          <div key={`parlay-${sectionName}-${size}`} className="swipe-card-parlay">
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
              <span className="ml-2 text-white font-bold">{Math.min(parlay.combined_probability || 0, 99).toFixed(1)}%</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});

// ==================== POPULAR BETS SECTION ====================

const PopularBetCard = memo(({ bet, onClick }) => {
  const isDemon = bet.is_demon || bet.pick_type === 'demon';
  const isGoblin = bet.is_goblin || bet.pick_type === 'goblin';
  const hasLineMovement = bet.movement != null && bet.movement !== 0;
  
  const getHitRateColor = (rate) => {
    if (rate >= 80) return 'text-green-400';
    if (rate >= 60) return 'text-yellow-400';
    return 'text-red-400';
  };
  
  // Get movement badge styling
  const getMovementStyle = (category) => {
    switch(category) {
      case 'MASSIVE':
        return 'bg-red-500/20 text-red-400 border-red-500/30';
      case 'SIGNIFICANT':
        return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
      default:
        return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    }
  };
  
  return (
    <Card 
      className={`p-2.5 sm:p-3 cursor-pointer hover:scale-[1.02] transition-all min-w-[180px] sm:min-w-[220px] ${
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
            {isDemon ? <DemonIcon size={12} /> : isGoblin ? <GoblinIcon size={12} /> : null}
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs sm:text-sm font-medium text-white truncate">{bet.player_name}</div>
          <div className="text-[10px] sm:text-xs text-zinc-500">{bet.stat_type} {bet.line || bet.current_line}</div>
        </div>
      </div>
      
      {/* Line Movement Badge */}
      {hasLineMovement && (
        <div className={`text-center mb-2 px-2 py-1 rounded text-[10px] font-medium border ${getMovementStyle(bet.movement_category)}`}>
          <span>{bet.movement_badge || (bet.movement > 0 ? '📈 Line Moving UP' : '📉 Line Moving DOWN')}</span>
          <div className="text-[9px] mt-0.5 opacity-75">
            {bet.previous_line} → {bet.current_line} ({bet.movement > 0 ? '+' : ''}{bet.movement})
          </div>
        </div>
      )}
      
      {/* Fallback sentiment for non-movement picks */}
      {!hasLineMovement && bet.sentiment_label && (
        <div className="text-center mb-2 px-2 py-1 rounded text-[10px] font-medium border bg-zinc-700/50 text-zinc-400 border-zinc-600/30">
          {bet.sentiment_label}
        </div>
      )}
      
      {/* L5 Hit Rate / L10 Hit Rate / Season Avg */}
      <div className="flex items-center justify-between bg-zinc-800/50 rounded px-2 py-1.5 text-[9px] sm:text-[10px]">
        <div className="text-center flex-1">
          <div className="text-zinc-500">L5</div>
          <div className={`font-bold ${getHitRateColor(bet.h5_rate || 0)}`}>
            {bet.h5_rate != null ? `${bet.h5_rate}%` : '---'}
          </div>
        </div>
        <div className="h-4 w-px bg-zinc-700" />
        <div className="text-center flex-1">
          <div className="text-zinc-500">L10</div>
          <div className={`font-bold ${getHitRateColor(bet.h10_rate || 0)}`}>
            {bet.h10_rate != null ? `${bet.h10_rate}%` : '---'}
          </div>
        </div>
        <div className="h-4 w-px bg-zinc-700" />
        <div className="text-center flex-1">
          <div className="text-zinc-500">Avg</div>
          <div className="font-bold text-white">
            {bet.season_avg != null ? bet.season_avg.toFixed?.(1) || bet.season_avg : '---'}
          </div>
        </div>
      </div>
    </Card>
  );
});

// ==================== LIVE INJURY ADVANTAGE SECTION (USAGE VACUUM) ====================

const LiveInjuryAdvantageSection = memo(({ alerts, isLoading }) => {
  if (isLoading) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-4 h-4 bg-orange-500/30 rounded animate-pulse" />
          <div className="h-4 w-48 bg-zinc-700 rounded animate-pulse" />
        </div>
        <div className="h-16 bg-zinc-800/50 rounded animate-pulse" />
      </div>
    );
  }

  // No active alerts - show monitoring state
  if (!alerts || alerts.length === 0) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-4 h-4 text-zinc-500 animate-pulse" />
          <span className="text-sm font-bold text-zinc-400">LIVE INJURY ADVANTAGE</span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-zinc-700 text-zinc-400">MONITORING</span>
        </div>
        <p className="text-xs text-zinc-500">
          Monitoring for late-breaking injury scratches... No active usage spikes detected.
        </p>
      </div>
    );
  }

  // Group alerts by injured player
  const groupedByInjuredPlayer = alerts.reduce((acc, alert) => {
    const key = alert.injured_player;
    if (!acc[key]) {
      acc[key] = {
        injured_player: alert.injured_player,
        injured_team: alert.injured_team,
        injury_reason: alert.injury_reason,
        injured_usage_rate: alert.injured_usage_rate,
        time_ago: alert.time_ago,
        is_late_scratch: alert.is_late_scratch,
        beneficiaries: []
      };
    }
    acc[key].beneficiaries.push(alert);
    return acc;
  }, {});

  const injuredPlayers = Object.values(groupedByInjuredPlayer);

  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<ShieldAlert className="w-4 h-4 text-orange-400" />}
        title="LIVE INJURY ADVANTAGE"
        subtitle="Late-breaking injury news creating usage opportunities"
        badgeText={`${injuredPlayers.length} OUT`}
        badgeColor="red"
      />
      
      {/* Horizontal scrollable container */}
      <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent">
        {injuredPlayers.map((injury) => (
          <div
            key={injury.injured_player}
            className="flex-shrink-0 w-72 p-3 rounded-xl border bg-gradient-to-br from-red-950/40 via-zinc-900 to-zinc-900 border-red-500/30"
          >
            {/* Injured Player - THE FOCUS */}
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center ring-2 ring-red-500/50">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold text-white">{injury.injured_player}</span>
                  <span className="px-1.5 py-0.5 text-[9px] font-black bg-red-500 text-white rounded">OUT</span>
                </div>
                <div className="text-[10px] text-zinc-400">
                  {injury.injured_usage_rate}% usage • {injury.time_ago}
                </div>
              </div>
            </div>

            {/* Injury Reason Headline - larger textbox */}
            {injury.injury_reason && (
              <div className="text-sm text-zinc-200 bg-zinc-800/60 rounded-lg p-3 mb-3">
                {injury.injury_reason}
              </div>
            )}

            {/* Usage Spike Indicator */}
            <div className="flex items-center gap-1 mb-2 text-[10px] text-orange-400 font-semibold">
              <TrendingUp className="w-3 h-3" />
              <span>USAGE SPIKE DETECTED</span>
            </div>

            {/* Beneficiaries - smaller cards */}
            <div className="space-y-1.5">
              {injury.beneficiaries.map((ben, idx) => (
                <div
                  key={ben.id}
                  className="flex items-center justify-between p-2 rounded-lg bg-zinc-800/50 hover:bg-zinc-800/70 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${
                      idx === 0 ? 'bg-orange-500/30 text-orange-400' : 'bg-zinc-700 text-zinc-400'
                    }`}>
                      {idx + 1}
                    </div>
                    <div>
                      <div className="text-xs font-semibold text-white">{ben.beneficiary_name}</div>
                      <div className="text-[9px] text-zinc-500">+{ben.minutes_bump || 0} mins projected</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-bold ${idx === 0 ? 'text-orange-400' : 'text-orange-400/70'}`}>
                      +{ben.usage_bump}%
                    </div>
                    <div className="text-[8px] text-zinc-500">usage</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
});

// ==================== MLB LIVE INJURY ADVANTAGE ====================

const MLBLiveInjuryAdvantageSection = memo(({ alerts, isLoading }) => {
  if (isLoading) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-4 h-4 bg-orange-500/30 rounded animate-pulse" />
          <div className="h-4 w-48 bg-zinc-700 rounded animate-pulse" />
        </div>
        <div className="h-16 bg-zinc-800/50 rounded animate-pulse" />
      </div>
    );
  }

  // No active alerts - show monitoring state
  if (!alerts || alerts.length === 0) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-4 h-4 text-zinc-500 animate-pulse" />
          <span className="text-sm font-bold text-zinc-400">MLB LIVE INJURY ADVANTAGE</span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-zinc-700 text-zinc-400">MONITORING</span>
        </div>
        <p className="text-xs text-zinc-500">
          Monitoring for late-breaking MLB injury scratches... No active lineup changes detected.
        </p>
      </div>
    );
  }

  // Group alerts by injured player
  const groupedByInjuredPlayer = alerts.reduce((acc, alert) => {
    const key = alert.injured_player;
    if (!acc[key]) {
      acc[key] = {
        injured_player: alert.injured_player,
        injured_team: alert.injured_team,
        injury_reason: alert.injury_reason,
        injured_ops: alert.injured_ops,
        time_ago: alert.time_ago,
        is_late_scratch: alert.is_late_scratch,
        beneficiaries: []
      };
    }
    acc[key].beneficiaries.push(alert);
    return acc;
  }, {});

  const injuredPlayers = Object.values(groupedByInjuredPlayer);

  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<ShieldAlert className="w-4 h-4 text-orange-400" />}
        title="MLB LIVE INJURY ADVANTAGE"
        subtitle="Late-breaking injury news creating lineup opportunities"
        badgeText={`${injuredPlayers.length} IL/OUT`}
        badgeColor="red"
      />
      
      {/* Horizontal scrollable container */}
      <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent">
        {injuredPlayers.map((injury) => (
          <div
            key={injury.injured_player}
            className="flex-shrink-0 w-72 p-3 rounded-xl border bg-gradient-to-br from-red-950/40 via-zinc-900 to-zinc-900 border-red-500/30"
          >
            {/* Injured Player - THE FOCUS */}
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center ring-2 ring-red-500/50">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold text-white">{injury.injured_player}</span>
                  <span className="px-1.5 py-0.5 text-[9px] font-black bg-red-500 text-white rounded">IL</span>
                </div>
                <div className="text-[10px] text-zinc-400">
                  {injury.injured_team} • {injury.injured_ops ? `${injury.injured_ops} OPS` : ''} • {injury.time_ago}
                </div>
              </div>
            </div>

            {/* Injury Reason Headline */}
            {injury.injury_reason && (
              <div className="text-sm text-zinc-200 bg-zinc-800/60 rounded-lg p-3 mb-3">
                {injury.injury_reason}
              </div>
            )}

            {/* Lineup Change Indicator */}
            <div className="flex items-center gap-1 mb-2 text-[10px] text-orange-400 font-semibold">
              <TrendingUp className="w-3 h-3" />
              <span>LINEUP OPPORTUNITY</span>
            </div>

            {/* Beneficiaries - smaller cards */}
            <div className="space-y-1.5">
              {injury.beneficiaries.map((ben, idx) => (
                <div
                  key={ben.id}
                  className="flex items-center justify-between p-2 rounded-lg bg-zinc-800/50 hover:bg-zinc-800/70 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${
                      idx === 0 ? 'bg-orange-500/30 text-orange-400' : 'bg-zinc-700 text-zinc-400'
                    }`}>
                      {idx + 1}
                    </div>
                    <div>
                      <div className="text-xs font-semibold text-white">{ben.beneficiary_name}</div>
                      <div className="text-[9px] text-zinc-500">+{ben.lineup_bump || 0} lineup spots</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-bold ${idx === 0 ? 'text-orange-400' : 'text-orange-400/70'}`}>
                      +{ben.ab_bump} AB
                    </div>
                    <div className="text-[8px] text-zinc-500">projected</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
});

// ==================== MAIN DASHBOARD ====================

const Dashboard = () => {
  const navigate = useNavigate();
  const { user, logout, isDemo } = useAuth();
  const { currentSport } = useSport();
  
  // ========== SSOT: TanStack Query Subscriptions ==========
  // PIPE 2: Live Wire - 30s polling for real-time updates
  const { data: warZoneData, isLoading: warZoneLoading, refetch: refetchWarZone } = useWarZone();
  const { data: safeHavenData, isLoading: safeHavenLoading, refetch: refetchSafeHaven } = useSafeHaven();
  const { data: frontLinesData, isLoading: frontLinesLoading, refetch: refetchFrontLines } = useFrontLines();
  const { data: liveOddsData, isLoading: boardLoading, refetch: refetchBoard } = useLiveOdds();
  const { data: vacuumAlertsData, isLoading: vacuumAlertsLoading } = useLiveVacuumAlerts();
  const { data: mlbVacuumAlertsData, isLoading: mlbVacuumAlertsLoading } = useMLBLiveVacuumAlerts();
  
  // MLB-specific hooks (only active when sport=mlb)
  const { data: mlbGoblinsData, isLoading: mlbGoblinsLoading, refetch: refetchMLBGoblins } = useMLBGoblins();
  const { data: mlbDemonsData, isLoading: mlbDemonsLoading, refetch: refetchMLBDemons } = useMLBDemons();
  const { data: mlbHRRData, isLoading: mlbHRRLoading, refetch: refetchMLBHRR } = useMLBHRRPicks();
  const { data: mlbSafeHavenData, isLoading: mlbSafeHavenLoading, refetch: refetchMLBSafeHaven } = useMLBSafeHaven();
  const { data: mlbFrontLinesData, isLoading: mlbFrontLinesLoading, refetch: refetchMLBFrontLines } = useMLBFrontLines();
  const { data: mlbWarZoneData, isLoading: mlbWarZoneLoading, refetch: refetchMLBWarZone } = useMLBWarZone();
  
  // Extract picks from TanStack Query data
  const radarPicks = useMemo(() => warZoneData?.picks || [], [warZoneData]);
  const vaultPicks = useMemo(() => safeHavenData?.picks || [], [safeHavenData]);
  const frontLinesPicks = useMemo(() => frontLinesData?.picks || [], [frontLinesData]);
  const players = useMemo(() => liveOddsData?.players || [], [liveOddsData]);
  
  // MLB Sharp tiers
  const mlbGoblinsPicks = useMemo(() => mlbGoblinsData?.picks || [], [mlbGoblinsData]);
  const mlbDemonsPicks = useMemo(() => mlbDemonsData?.picks || [], [mlbDemonsData]);
  const mlbHRRPicks = useMemo(() => mlbHRRData?.picks || [], [mlbHRRData]);
  const mlbSafeHavenPicks = useMemo(() => mlbSafeHavenData?.picks || [], [mlbSafeHavenData]);
  const mlbFrontLinesPicks = useMemo(() => mlbFrontLinesData?.picks || [], [mlbFrontLinesData]);
  const mlbWarZonePicks = useMemo(() => mlbWarZoneData?.picks || [], [mlbWarZoneData]);
  
  // Live Vacuum Alerts (Usage Vacuum)
  const vacuumAlerts = useMemo(() => vacuumAlertsData?.alerts || [], [vacuumAlertsData]);
  const mlbVacuumAlerts = useMemo(() => mlbVacuumAlertsData?.alerts || [], [mlbVacuumAlertsData]);
  
  // Status flags derived from query state
  const linesLoaded = !boardLoading && players.length > 0;
  const staticLoaded = !warZoneLoading && !safeHavenLoading;
  const syncing = boardLoading || warZoneLoading || safeHavenLoading;
  const boardIntelStatus = { 
    demon_count: currentSport === 'mlb' ? mlbDemonsPicks.length : radarPicks.length, 
    goblin_count: currentSport === 'mlb' ? mlbGoblinsPicks.length : vaultPicks.length,
    total_players: players.length 
  };
  const syncStatus = { last_sync: warZoneData?.synced_at };
  
  // Market Intel verification stats (Ferrari v6)
  const verificationStats = useMemo(() => {
    const verification = safeHavenData?.verification || {};
    const activeProps = verification.active_props_verified || 0;
    const eliteCount = vaultPicks.length + frontLinesPicks.length + radarPicks.length;
    return {
      active_props_verified: activeProps,
      elite_opportunities: eliteCount,
      message: activeProps > 0 
        ? `Verified ${activeProps.toLocaleString()} active props to identify these ${eliteCount} Elite opportunities.`
        : null
    };
  }, [safeHavenData, vaultPicks.length, frontLinesPicks.length, radarPicks.length]);
  
  // Refetch all data (replaces old triggerSync)
  const triggerSync = useCallback(() => {
    refetchWarZone();
    refetchSafeHaven();
    refetchFrontLines();
    refetchBoard();
    // Also refetch MLB-specific data if on MLB
    if (currentSport === 'mlb') {
      refetchMLBGoblins();
      refetchMLBDemons();
      refetchMLBHRR();
      refetchMLBSafeHaven();
      refetchMLBFrontLines();
      refetchMLBWarZone();
    }
    toast.success('Data refreshed');
  }, [refetchWarZone, refetchSafeHaven, refetchFrontLines, refetchBoard, refetchMLBGoblins, refetchMLBDemons, refetchMLBHRR, refetchMLBSafeHaven, refetchMLBFrontLines, refetchMLBWarZone, currentSport]);
  
  // Local UI state
  const [searchTerm, setSearchTerm] = useState('');
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [expandedParlay, setExpandedParlay] = useState(null);
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon');
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [selectedPlayerData, setSelectedPlayerData] = useState(null); // Store full pick data
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
  const handlePlayerClick = useCallback((playerName, highlight = null, type = 'demon', pickData = null) => {
    setSavedScrollPosition(window.scrollY);
    setHighlightProp(highlight);
    setHighlightType(type);
    setSelectedPlayer(playerName);
    setSelectedPlayerData(pickData); // Store the full pick data
    // Push state so browser back button works
    window.history.pushState({ view: 'player', player: playerName }, '');
  }, []);
  
  const handleBackFromPlayer = useCallback(() => {
    setSelectedPlayer(null);
    setSelectedPlayerData(null);
    setHighlightProp(null);
    // Restore scroll position after render
    setTimeout(() => {
      if (savedScrollPosition !== null) {
        window.scrollTo(0, savedScrollPosition);
      }
    }, 0);
  }, [savedScrollPosition]);
  
  // Handle browser back button
  useEffect(() => {
    const handlePopState = (event) => {
      // If we have a selected player or expanded parlay, close it
      if (selectedPlayer) {
        setSelectedPlayer(null);
        setSelectedPlayerData(null);
        setHighlightProp(null);
        if (savedScrollPosition !== null) {
          setTimeout(() => window.scrollTo(0, savedScrollPosition), 0);
        }
      } else if (expandedParlay) {
        setExpandedParlay(null);
      }
    };
    
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [selectedPlayer, expandedParlay, savedScrollPosition]);
  
  const handleRadarClick = useCallback((pick) => {
    const lineValue = pick.demon_line || pick.line;
    const highlightKey = `${pick.stat_type}|${lineValue}|${pick.direction || pick.recommendation || 'Over'}`;
    
    // Transform pick into player format expected by PlayerDetailPage
    const playerData = {
      name: pick.player_name,
      player_name: pick.player_name,
      team: pick.team || pick.away_team || pick.home_team,
      photo_url: pick.photo_url || pick.headshot_url,
      props: [{
        ...pick,
        stat_type_extracted: pick.stat_type,
        direction: pick.direction || pick.recommendation || 'Over',
        market: pick.market_key || pick.stat_type,
      }]
    };
    
    handlePlayerClick(pick.player_name, highlightKey, 'demon', playerData);
  }, [handlePlayerClick]);
  
  const handleVaultClick = useCallback((pick) => {
    const lineValue = pick.goblin_line || pick.line;
    const highlightKey = `${pick.stat_type}|${lineValue}|${pick.direction || pick.recommendation || 'Over'}`;
    
    // Transform pick into player format expected by PlayerDetailPage
    const playerData = {
      name: pick.player_name,
      player_name: pick.player_name,
      team: pick.team || pick.away_team || pick.home_team,
      photo_url: pick.photo_url || pick.headshot_url,
      props: [{
        ...pick,
        stat_type_extracted: pick.stat_type,
        direction: pick.direction || pick.recommendation || 'Over',
        market: pick.market_key || pick.stat_type,
      }]
    };
    
    handlePlayerClick(pick.player_name, highlightKey, 'goblin', playerData);
  }, [handlePlayerClick]);
  
  const handleParlayClick = useCallback((parlay, sectionType) => {
    setSavedScrollPosition(window.scrollY);
    setExpandedParlay({ parlay, sectionType });
    // Push state so browser back button works
    window.history.pushState({ view: 'parlay' }, '');
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
        playerData={selectedPlayerData}
        onBack={handleBackFromPlayer}
        highlightProp={highlightProp}
        highlightType={highlightType}
        onQuickAdd={handleQuickAdd}
      />
    );
  }
  
  return (
    <div className="min-h-screen bg-zinc-950 pb-16">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800 px-3 sm:px-4 py-2 sm:py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 sm:gap-4">
            <Crosshair className="w-6 h-6 sm:w-8 sm:h-8 text-yellow-500" />
            <div>
              <h1 className="text-lg sm:text-2xl font-black tracking-tight text-white">PROPVISION</h1>
              <div className="hidden sm:flex items-center gap-2 text-[10px] text-zinc-500 -mt-0.5">
                <span>AI-POWERED PROP INTEL</span>
                <span className="text-zinc-700">•</span>
                <Radio className={`w-2.5 h-2.5 ${syncStatus.has_stale_intel ? 'text-amber-400' : 'text-emerald-400 animate-pulse'}`} />
                <span className="font-mono">
                  {boardIntelStatus.time_since_sync_display || 'syncing...'}
                </span>
              </div>
            </div>
            
            {/* Sport Switcher */}
            <div className="ml-2 sm:ml-4">
              <SportSwitcher />
            </div>
          </div>
          
          <div className="flex items-center gap-1 sm:gap-2">
            {/* Fullscreen / Open in New Tab Button */}
            <button 
              onClick={() => window.open(window.location.href, '_blank')} 
              className="flex items-center justify-center w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-zinc-800/50 hover:bg-zinc-700 border border-zinc-700/50 transition-all"
              data-testid="fullscreen-btn"
              title="Open in new tab"
            >
              <Maximize2 className="w-4 h-4 sm:w-5 sm:h-5 text-yellow-500" />
            </button>
            
            {/* Command Hub Button */}
            <button 
              onClick={() => setShowCommandPost(true)} 
              className="flex items-center justify-center gap-2 w-9 h-9 sm:w-auto sm:h-auto sm:px-4 sm:py-2.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-700 border border-zinc-700/50 transition-all"
              data-testid="command-post-btn"
            >
              <Target className="w-4 h-4 sm:w-5 sm:h-5 text-yellow-500" />
              <span className="text-sm font-medium text-white hidden sm:inline">Command Hub</span>
            </button>
            
            <div className="relative">
              <button onClick={() => setShowUserMenu(!showUserMenu)} className="flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-2.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-700 border border-zinc-700/50 transition-all" data-testid="user-menu-btn">
                <User className="w-4 h-4 sm:w-5 sm:h-5 text-yellow-400" />
                <span className="text-xs sm:text-sm font-medium text-white max-w-[60px] sm:max-w-none truncate">{isDemo ? 'Demo' : user?.email?.split('@')[0]}</span>
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
            <span className="text-[10px] text-zinc-500 hidden sm:inline">Search any player for tactical profile</span>
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
                <div className="max-h-[400px] overflow-y-auto p-2 space-y-2">
                  {searchResults.map((player) => {
                    // Check if player is on any board with Vision Intel
                    const boardPick = [...radarPicks, ...vaultPicks, ...frontLinesPicks].find(
                      pick => pick.player_name?.toLowerCase() === player.player_name?.toLowerCase()
                    );
                    
                    // If player has a board pick, show their Vision card
                    if (boardPick) {
                      const board = boardPick.board || (radarPicks.includes(boardPick) ? 'war_zone' : vaultPicks.includes(boardPick) ? 'safe_haven' : 'front_lines');
                      const sectionColor = board === 'war_zone' ? 'red' : board === 'safe_haven' ? 'green' : 'yellow';
                      const forceTheme = board === 'front_lines' ? 'FRONT_LINE' : undefined;
                      
                      return (
                        <div key={player.id || player.player_name} className="relative">
                          {/* Vision Badge */}
                          <div className="absolute -top-1 -right-1 z-10 px-2 py-0.5 text-[9px] font-black bg-gradient-to-r from-amber-500 to-yellow-400 text-black rounded-full flex items-center gap-1 shadow-lg">
                            <Eye className="w-3 h-3" />
                            VISION
                          </div>
                          <UniversalPlayerCard 
                            player={boardPick} 
                            onClick={() => {
                              const lineValue = boardPick.line;
                              const highlightKey = `${boardPick.stat_type}|${lineValue}|${boardPick.direction || 'Over'}`;
                              const type = board === 'safe_haven' ? 'goblin' : board === 'war_zone' ? 'demon' : 'demon';
                              handlePlayerClick(boardPick.player_name, highlightKey, type);
                            }}
                            onQuickAdd={handleQuickAdd}
                            showStats={true}
                            showProps={false}
                            mode="compact"
                            sectionColor={sectionColor}
                            forceTheme={forceTheme}
                            isBoardPick={true}
                          />
                        </div>
                      );
                    }
                    
                    // Regular player row (no Vision Intel) - NOT clickable
                    return (
                      <div 
                        key={player.id || player.player_name}
                        className="flex items-center gap-3 p-3 border border-zinc-800/50 rounded-lg"
                        data-testid={`player-row-${player.player_name?.replace(/\s/g, '-')}`}
                      >
                        <PlayerHeadshot playerName={player.player_name} team={player.team} photoUrl={player.photo_url || player.headshot_url} size="md" />
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-white truncate">{player.player_name}</div>
                          <div className="text-xs text-zinc-500">{player.team_name || player.team} • {player.position}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      
      {/* Main Content */}
      <div className="p-3 space-y-4">
        {/* Main Picks Content */}
        <>
          {/* Live Injury Advantage (Usage Vacuum) - NBA ONLY */}
          {currentSport === 'nba' && (
            <LiveInjuryAdvantageSection 
              alerts={vacuumAlerts} 
              isLoading={vacuumAlertsLoading} 
            />
          )}
          
          {/* MLB-SPECIFIC SECTIONS */}
          {currentSport === 'mlb' && (
            <>
              {/* MLB Live Injury Advantage (Usage Vacuum) */}
              <MLBLiveInjuryAdvantageSection 
                alerts={mlbVacuumAlerts} 
                isLoading={mlbVacuumAlertsLoading} 
              />
              
              {/* MLB Safe Haven (3-Gate Qualified) */}
              <MLBSafeHavenSection 
                picks={mlbSafeHavenPicks.length > 0 ? mlbSafeHavenPicks : mlbGoblinsPicks} 
                onPickClick={handleVaultClick} 
                onQuickAdd={handleQuickAdd} 
                isLoading={mlbSafeHavenLoading || mlbGoblinsLoading} 
              />
              
              {/* MLB Front Lines (Mid-Juice 3-Gate Qualified) */}
              <MLBFrontLinesSection 
                picks={mlbFrontLinesPicks.length > 0 ? mlbFrontLinesPicks : mlbHRRPicks} 
                onPickClick={handleRadarClick} 
                onQuickAdd={handleQuickAdd} 
                isLoading={mlbFrontLinesLoading || mlbHRRLoading} 
              />
              
              {/* MLB War Zone (Moonshot Demons with Ceiling Protocol) */}
              <MLBWarZoneSection 
                picks={mlbWarZonePicks.length > 0 ? mlbWarZonePicks : mlbDemonsPicks} 
                onPickClick={handleRadarClick} 
                onQuickAdd={handleQuickAdd} 
                isLoading={mlbWarZoneLoading || mlbDemonsLoading} 
              />
            </>
          )}
          
          {/* NBA-SPECIFIC SECTIONS */}
          {currentSport === 'nba' && (
            <>
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
            </>
          )}
          </>
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
      
      {/* Footer - Market Intel Verification */}
      <div className="fixed bottom-0 left-0 right-0 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 px-4 py-2 z-40" data-testid="market-intel-footer">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-3">
            {verificationStats.message ? (
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-emerald-400 font-medium">{verificationStats.message}</span>
              </div>
            ) : (
              <>
                <span className="text-zinc-500 font-mono">{boardIntelStatus.time_since_sync_display}</span>
                {boardIntelStatus.last_sync_type && (
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    boardIntelStatus.last_sync_type === 'primary' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-blue-500/20 text-blue-400'
                  }`}>
                    {boardIntelStatus.last_sync_type === 'primary' ? 'FULL SYNC' : 'DELTA'}
                  </span>
                )}
              </>
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
