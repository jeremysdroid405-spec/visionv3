/**
 * PickVision Dashboard - Main Controller
 * 
 * This is the streamlined entry point that orchestrates:
 * - Data fetching via useDFSData hook
 * - Section rendering via modular components
 * - Navigation and state management
 */
import React, { useState, useCallback, memo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import axios from 'axios';

// UI Components
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { 
  Search, X, LogOut, Crown, User, Radio, AlertTriangle, Activity, RefreshCw
} from 'lucide-react';

// Dashboard Components
import { DemonIcon, GoblinIcon } from '../../components/dashboard/Icons';
import { 
  WarZoneSection, SafeHavenSection, FrontLinesSection,
  GauntletSection, ShieldSection, StrikeSection 
} from '../../components/dashboard/SectionContainer';
import { PickCard } from '../../components/dashboard/PickCard';

// Hooks
import { useDFSData } from '../../hooks/useDFSData';

// Constants
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Beacon glow styles for highlights
const BeaconGlowStyles = () => (
  <style>{`
    @keyframes beacon-glow-pulse {
      0%, 100% { box-shadow: 0 0 10px rgba(234, 179, 8, 0.4), 0 0 20px rgba(234, 179, 8, 0.2); }
      50% { box-shadow: 0 0 20px rgba(234, 179, 8, 0.6), 0 0 40px rgba(234, 179, 8, 0.4); }
    }
    @keyframes emerald-glow-pulse {
      0%, 100% { box-shadow: 0 0 10px rgba(16, 185, 129, 0.4), 0 0 20px rgba(16, 185, 129, 0.2); }
      50% { box-shadow: 0 0 20px rgba(16, 185, 129, 0.6), 0 0 40px rgba(16, 185, 129, 0.4); }
    }
    .beacon-glow { animation: beacon-glow-pulse 2s ease-in-out infinite; }
    .emerald-glow { animation: emerald-glow-pulse 2s ease-in-out infinite; }
  `}</style>
);

// Player row component for search results
const PlayerRow = memo(({ player, onClick, linesLoaded }) => (
  <div 
    className="flex items-center gap-3 p-3 hover:bg-zinc-800/50 cursor-pointer border-b border-zinc-800/50 last:border-0"
    onClick={onClick}
    data-testid={`player-row-${player.player_name?.replace(/\s/g, '-')}`}
  >
    <div className="w-10 h-10 rounded-full bg-zinc-800 overflow-hidden">
      {player.photo_url ? (
        <img src={player.photo_url} alt={player.player_name} className="w-full h-full object-cover" />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-zinc-500 text-sm">
          {player.player_name?.charAt(0)}
        </div>
      )}
    </div>
    <div className="flex-1 min-w-0">
      <div className="font-medium text-white truncate">{player.player_name}</div>
      <div className="text-xs text-zinc-500">{player.team}</div>
    </div>
    <div className="flex items-center gap-2">
      {player.demons_count > 0 && (
        <Badge className="bg-red-500/20 text-red-400 border-none text-xs">
          {player.demons_count} Demons
        </Badge>
      )}
      {player.goblins_count > 0 && (
        <Badge className="bg-green-500/20 text-green-400 border-none text-xs">
          {player.goblins_count} Goblins
        </Badge>
      )}
    </div>
  </div>
));

/**
 * Dashboard - Main controller component
 */
const Dashboard = () => {
  const navigate = useNavigate();
  const { user, logout, isDemo } = useAuth();
  
  // Data from hook
  const {
    players,
    radarPicks,
    vaultPicks,
    frontLinesPicks,
    liveScores,
    tMinusGames,
    linesLoaded,
    syncing,
    boardIntelStatus,
    triggerSync
  } = useDFSData();
  
  // Local UI state
  const [searchTerm, setSearchTerm] = useState('');
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [expandedParlay, setExpandedParlay] = useState(null);
  const [highlightProp, setHighlightProp] = useState(null);
  const [highlightType, setHighlightType] = useState('demon');
  
  // Navigation handlers
  const handlePlayerClick = useCallback((playerName, highlight = null, type = 'demon') => {
    setHighlightProp(highlight);
    setHighlightType(type);
    setSelectedPlayer(playerName);
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
  
  const handleLogout = async () => {
    await logout();
    navigate('/auth');
    toast.success('Logged out successfully');
  };
  
  // Filter players by search
  const filteredPlayers = players.filter(p => {
    if (!searchTerm) return true;
    const search = searchTerm.toLowerCase();
    return p.player_name?.toLowerCase().includes(search) || p.team?.toLowerCase().includes(search);
  });
  
  // If a player is selected, show their detail page
  if (selectedPlayer) {
    // TODO: Import and render PlayerDetailPage component
    return (
      <div className="min-h-screen bg-zinc-950 text-white p-4">
        <button onClick={() => setSelectedPlayer(null)} className="text-zinc-400 mb-4">
          ← Back to Dashboard
        </button>
        <h1 className="text-2xl font-bold">{selectedPlayer}</h1>
        <p className="text-zinc-500">Player detail view coming soon...</p>
      </div>
    );
  }
  
  return (
    <div className="min-h-screen bg-zinc-950 pb-16">
      <BeaconGlowStyles />
      
      {/* Header */}
      <header className="sticky top-0 z-50 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <DemonIcon size={28} />
            <div>
              <h1 className="text-lg font-black tracking-tight text-white">PICKVISION</h1>
              <p className="text-[9px] text-zinc-500 -mt-0.5">AI-POWERED PROP INTEL</p>
            </div>
          </div>
          
          {/* Controls */}
          <div className="flex items-center gap-2">
            {/* Sync Button */}
            <button
              onClick={triggerSync}
              disabled={syncing}
              className="p-2 rounded-lg bg-zinc-800/50 hover:bg-zinc-800 transition-colors"
              data-testid="sync-btn"
            >
              <RefreshCw className={`w-4 h-4 text-zinc-400 ${syncing ? 'animate-spin' : ''}`} />
            </button>
            
            {/* User Menu */}
            <div className="relative">
              <button
                onClick={() => setShowUserMenu(!showUserMenu)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-800"
                data-testid="user-menu-btn"
              >
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
                    <button
                      onClick={handleLogout}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-400 hover:bg-red-950/30 rounded"
                    >
                      <LogOut className="w-4 h-4" />
                      <span>Logout</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        
        {/* Intel Status */}
        <div className="flex items-center gap-2 mt-1.5 text-[10px] text-zinc-500">
          <Radio className="w-3 h-3" />
          <span className="font-mono">Intel: {boardIntelStatus.time_since_sync_display}</span>
        </div>
      </header>
      
      {/* Main Content */}
      <div className="p-3 space-y-4">
        {/* Safe Haven Section */}
        <SafeHavenSection 
          picks={vaultPicks} 
          onPickClick={handleVaultClick}
          tMinusGames={tMinusGames}
        />
        
        {/* Shield Parlays */}
        <ShieldSection 
          picks={vaultPicks}
          onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'recon' })}
        />
        
        {/* Front Lines Section */}
        <FrontLinesSection 
          picks={frontLinesPicks}
          onPickClick={handleRadarClick}
          tMinusGames={tMinusGames}
        />
        
        {/* Strike Parlays */}
        <StrikeSection 
          picks={frontLinesPicks}
          onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'builder' })}
        />
        
        {/* War Zone Section */}
        <WarZoneSection 
          picks={radarPicks}
          onPickClick={handleRadarClick}
          tMinusGames={tMinusGames}
        />
        
        {/* Gauntlet Parlays */}
        <GauntletSection 
          picks={radarPicks}
          onParlayClick={(parlay) => setExpandedParlay({ parlay, type: 'builder' })}
        />
        
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
          {filteredPlayers.length === 0 ? (
            <div className="p-6 text-center text-zinc-500 text-sm">
              {!linesLoaded ? (
                <><Activity className="w-6 h-6 mx-auto mb-2 animate-pulse" /> Loading...</>
              ) : (
                'No players found'
              )}
            </div>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              {filteredPlayers.map((player) => (
                <PlayerRow
                  key={player.player_name}
                  player={player}
                  onClick={() => handlePlayerClick(player.player_name)}
                  linesLoaded={linesLoaded}
                />
              ))}
            </div>
          )}
        </div>
      </div>
      
      {/* Footer */}
      <div className="fixed bottom-0 left-0 right-0 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 px-4 py-2 z-40">
        <div className="flex items-center justify-between text-xs">
          <span className="text-zinc-500 font-mono">{boardIntelStatus.time_since_sync_display}</span>
          <span className="text-zinc-600">PickVision AI</span>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
