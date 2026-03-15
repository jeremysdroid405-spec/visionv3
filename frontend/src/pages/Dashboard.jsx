/**
 * PickVision Dashboard - Main Controller
 * 
 * Complete modular dashboard that replaces the legacy monolith.
 * All logic isolated to /logic/ and /hooks/ modules.
 */
import React, { useState, useCallback, useEffect, memo } from 'react';
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
  RefreshCw, ChevronRight, Eye, Zap, ChevronDown, Flame, ArrowLeft, Target
} from 'lucide-react';

// Dashboard Components
import { DemonIcon, GoblinIcon, VisionBadge } from '../components/dashboard/Icons';
import { PickCard } from '../components/dashboard/PickCard';
import { ParlayTicket } from '../components/dashboard/ParlayTicket';
import { PlayerDetailPage } from '../components/dashboard/PlayerDetailPage';
import CommandPost from '../components/dashboard/CommandPost';
import { 
  TEAM_LOGOS, NBA_HEADSHOT_URL, STAT_CATEGORIES, getCategoryKey 
} from '../components/dashboard/constants';

// Hooks & Logic
import { useDFSData } from '../hooks/useDFSData';
import { buildMasterParlayTickets } from '../logic/matrixEngine';

// ==================== HELPER COMPONENTS ====================

// Player Headshot with fallbacks
const PlayerHeadshot = memo(({ nbaId, playerName, team, photoUrl, size = 'md', className = '' }) => {
  const [error, setError] = useState(false);
  const sizeClasses = { sm: 'w-8 h-8', md: 'w-12 h-12', lg: 'w-16 h-16', xl: 'w-24 h-24' };
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  
  const isValidPhotoUrl = photoUrl && !photoUrl.includes('nophoto');
  const headshotUrl = isValidPhotoUrl ? photoUrl : (nbaId ? NBA_HEADSHOT_URL(nbaId) : null);
  const teamLogoUrl = team ? TEAM_LOGOS[team] : null;
  
  if (!headshotUrl || error) {
    if (teamLogoUrl) {
      return (
        <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1.5 ${className}`}>
          <img src={teamLogoUrl} alt={team} className="w-full h-full object-contain" onError={(e) => e.target.style.display = 'none'} />
        </div>
      );
    }
    return (
      <div className={`${sizeClass} rounded-full bg-zinc-800 flex items-center justify-center ${className}`}>
        <User className="w-6 h-6 text-zinc-500" />
      </div>
    );
  }
  
  return (
    <div className={`${sizeClass} rounded-full overflow-hidden bg-zinc-800 ${className}`}>
      <img src={headshotUrl} alt={playerName} onError={() => setError(true)} 
        className="w-full h-full object-cover" style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }} />
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
        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-zinc-800/50 border border-zinc-700">
          {icon}
        </div>
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

// Swipe Container
const SwipeContainer = memo(({ children, className = '' }) => (
  <div className={`swipe-container ${className}`}>
    {children}
  </div>
));

// War Zone Section (Demons)
const WarZoneSection = memo(({ picks, onPickClick }) => {
  if (!picks?.length) return null;
  return (
    <div className="war-zone-section mb-4">
      <SectionHeader 
        icon={<DemonIcon size={20} />}
        title="WAR ZONE"
        subtitle="High-risk, high-reward demon plays"
        badgeText={`TOP ${Math.min(10, picks.length)} DEMONS`}
        badgeColor="red"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`warzone-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <PickCard pick={pick} rank={idx + 1} onClick={() => onPickClick(pick)} colorTheme="red" emblem="fire" />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Safe Haven Section (Goblins)
const SafeHavenSection = memo(({ picks, onPickClick }) => {
  if (!picks?.length) return null;
  return (
    <div className="goblin-recon-section mb-4">
      <SectionHeader 
        icon={<GoblinIcon size={20} />}
        title="SAFE HAVEN"
        subtitle="High-floor goblin plays with best consistency"
        badgeText={`TOP ${Math.min(10, picks.length)} GOBLINS`}
        badgeColor="green"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`safehaven-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <PickCard pick={pick} rank={idx + 1} onClick={() => onPickClick(pick)} colorTheme="green" emblem="gem" />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

// Front Lines Section (Mixed)
const FrontLinesSection = memo(({ picks, onPickClick }) => {
  if (!picks?.length) return null;
  return (
    <div className="front-lines-section mb-4">
      <SectionHeader 
        icon={<span className="text-lg">🎯</span>}
        title="FRONT LINES"
        subtitle="Balanced demon/goblin mix for tactical plays"
        badgeText={`${Math.min(10, picks.length)} PICKS`}
        badgeColor="amber"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`frontlines-${pick.player_name}-${pick.stat_type}-${idx}`} className="swipe-card">
            <PickCard pick={pick} rank={idx + 1} onClick={() => onPickClick(pick)} colorTheme="amber" emblem="bullet" />
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
            <PickCard 
              key={`expanded-${pick.player_name}-${pick.stat_type}-${idx}`}
              pick={pick}
              rank={idx + 1}
              onClick={() => onPickClick(pick)}
              colorTheme={isGoblin ? 'green' : sectionType === 'front_lines' ? 'amber' : 'red'}
              emblem={isGoblin ? 'gem' : sectionType === 'front_lines' ? 'bullet' : 'fire'}
            />
          ))}
        </div>
        
        <div className="sticky bottom-0 bg-zinc-900/95 backdrop-blur-sm p-4 border-t border-zinc-800">
          <div className="flex items-center justify-between text-sm">
            <div>
              <span className="text-zinc-500">Combined Prob:</span>
              <span className="ml-2 text-white font-bold">{parlay.combined_probability?.toFixed(1)}%</span>
            </div>
            <div>
              <span className="text-zinc-500">Payout:</span>
              <span className={`ml-2 font-bold ${theme.text}`}>{parlay.estimated_payout}x</span>
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
  
  return (
    <Card 
      className={`p-3 cursor-pointer hover:scale-[1.02] transition-all min-w-[200px] ${
        isDemon ? 'bg-red-950/30 border-red-500/30' : 
        isGoblin ? 'bg-green-950/30 border-green-500/30' : 
        'bg-zinc-900 border-zinc-800'
      }`}
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
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
    </Card>
  );
});

const MostPopularBetsSection = memo(({ bets, status, onBetClick }) => {
  if (status === 'awaiting_action' || !bets?.length) {
    return (
      <div className="mb-4 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl text-center">
        <Activity className="w-6 h-6 text-zinc-500 mx-auto mb-2" />
        <p className="text-sm text-zinc-500">Awaiting live action...</p>
        <p className="text-xs text-zinc-600">Popular bets will appear when games tip off</p>
      </div>
    );
  }
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<Flame className="w-4 h-4 text-orange-400" />}
        title="MOST POPULAR"
        subtitle="Top 20 hottest bets right now"
        badgeText="LIVE"
        badgeColor="amber"
      />
      <div className="overflow-x-auto pb-2 -mx-3 px-3">
        <div className="flex gap-3" style={{ minWidth: 'max-content' }}>
          {bets.slice(0, 20).map((bet, idx) => (
            <PopularBetCard key={`popular-${bet.player_name}-${bet.stat_type}-${idx}`} bet={bet} rank={idx + 1} onClick={() => onBetClick(bet)} />
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
  
  // Data from hook
  const {
    players, trending, radarPicks, vaultPicks, frontLinesPicks,
    popularBets, popularBetsStatus, linesLoaded, staticLoaded, syncing,
    boardIntelStatus, syncStatus, triggerSync
  } = useDFSData();
  
  // Local UI state
  const [searchTerm, setSearchTerm] = useState('');
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [expandedParlay, setExpandedParlay] = useState(null);
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon');
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [showCommandPost, setShowCommandPost] = useState(false);
  
  // Navigation handlers
  const handlePlayerClick = useCallback((playerName, highlight = null, type = 'demon') => {
    setHighlightProp(highlight);
    setHighlightType(type);
    setSelectedPlayer(playerName);
  }, []);
  
  const handleBackFromPlayer = useCallback(() => {
    setSelectedPlayer(null);
    setHighlightProp(null);
  }, []);
  
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
  
  // Filter players by search
  const filteredPlayers = players.filter(p => {
    if (!searchTerm) return true;
    const search = searchTerm.toLowerCase();
    return p.player_name?.toLowerCase().includes(search) || p.team?.toLowerCase().includes(search);
  });
  
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
          <div className="flex items-center gap-2">
            <DemonIcon size={28} />
            <div>
              <h1 className="text-lg font-black tracking-tight text-white">PICKVISION</h1>
              <p className="text-[9px] text-zinc-500 -mt-0.5">AI-POWERED PROP INTEL</p>
            </div>
          </div>
          
          <div className="flex items-center gap-2">
            {/* Command Post Button */}
            <button 
              onClick={() => setShowCommandPost(true)} 
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-500/30 transition-colors"
              data-testid="command-post-btn"
            >
              <Target className="w-4 h-4 text-cyan-400" />
              <span className="text-xs font-medium text-cyan-400 hidden sm:inline">Command Post</span>
            </button>
            
            <button onClick={triggerSync} disabled={syncing} className="p-2 rounded-lg bg-zinc-800/50 hover:bg-zinc-800" data-testid="sync-btn">
              <RefreshCw className={`w-4 h-4 text-zinc-400 ${syncing ? 'animate-spin' : ''}`} />
            </button>
            
            <div className="relative">
              <button onClick={() => setShowUserMenu(!showUserMenu)} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-800" data-testid="user-menu-btn">
                <User className="w-4 h-4 text-zinc-400" />
                <span className="text-sm text-zinc-300">{isDemo ? 'Demo' : user?.email?.split('@')[0]}</span>
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
        
        <div className="flex items-center gap-2 mt-1.5 text-[10px] text-zinc-500">
          <Radio className={`w-3 h-3 ${syncStatus.has_stale_intel ? 'text-amber-400' : 'text-emerald-400'}`} />
          <span className="font-mono">
            {syncStatus.has_stale_intel ? `⚠️ STALE (${boardIntelStatus.time_since_sync_display})` : `Intel: ${boardIntelStatus.time_since_sync_display}`}
          </span>
        </div>
      </header>
      
      {/* Main Content */}
      <div className="p-3 space-y-4">
        {/* Safe Haven */}
        <SafeHavenSection picks={vaultPicks} onPickClick={handleVaultClick} />
        
        {/* Shield Parlays */}
        <ParlaySection 
          picks={vaultPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="safe_haven"
          title="SHIELD"
          subtitle="Safe Haven parlay combinations"
          icon={<span className="text-lg">🛡️</span>}
          badgeColor="green"
        />
        
        {/* Front Lines */}
        <FrontLinesSection picks={frontLinesPicks} onPickClick={handleRadarClick} />
        
        {/* Strike Parlays */}
        <ParlaySection 
          picks={frontLinesPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="front_lines"
          title="STRIKE"
          subtitle="Front Lines parlay combinations"
          icon={<span className="text-lg">🎯</span>}
          badgeColor="amber"
        />
        
        {/* War Zone */}
        <WarZoneSection picks={radarPicks} onPickClick={handleRadarClick} />
        
        {/* Gauntlet Parlays */}
        <ParlaySection 
          picks={radarPicks} 
          onParlayClick={handleParlayClick} 
          sectionName="war_zone"
          title="GAUNTLET"
          subtitle="War Zone parlay combinations"
          icon={<span className="text-lg">⚔️</span>}
          badgeColor="red"
        />
        
        {/* Most Popular Bets */}
        <MostPopularBetsSection bets={popularBets} status={popularBetsStatus} onBetClick={handlePopularBetClick} />
        
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <Input
            placeholder="Search player..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 py-2 bg-zinc-900 border-zinc-800 text-white text-sm"
            data-testid="search-input"
          />
          {searchTerm && (
            <button onClick={() => setSearchTerm('')} className="absolute right-3 top-1/2 -translate-y-1/2">
              <X className="w-4 h-4 text-zinc-500 hover:text-white" />
            </button>
          )}
        </div>
        
        {/* Players List */}
        <div className="rounded-lg border border-zinc-800 overflow-hidden" data-testid="players-list">
          {!staticLoaded ? (
            <div className="p-6 text-center">
              <Activity className="w-6 h-6 text-purple-500 mx-auto mb-2 animate-pulse" />
              <p className="text-zinc-400 text-sm">Loading...</p>
            </div>
          ) : filteredPlayers.length === 0 ? (
            <div className="p-6 text-center text-zinc-500 text-sm">No players found</div>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              {filteredPlayers.map((player) => (
                <PlayerRow key={player.player_name} player={player} onClick={() => handlePlayerClick(player.player_name)} linesLoaded={linesLoaded} />
              ))}
            </div>
          )}
        </div>
      </div>
      
      {/* Expanded Parlay Modal */}
      {expandedParlay && (
        <ExpandedParlayView 
          parlay={expandedParlay.parlay}
          sectionType={expandedParlay.sectionType}
          onClose={() => setExpandedParlay(null)}
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
          <span className="text-zinc-600">PickVision AI</span>
        </div>
      </div>
      
      {/* Command Post Sidebar */}
      <CommandPost 
        isOpen={showCommandPost} 
        onClose={() => setShowCommandPost(false)} 
      />
    </div>
  );
};

export default Dashboard;
