import React, { useState, useEffect, useCallback, memo } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { 
  Activity, RefreshCw, Search, Database, 
  ChevronDown, ChevronRight, AlertTriangle, Skull, Ghost,
  User, Flame, Star, Clock, Zap, HardDrive
} from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Cache keys for localStorage
const CACHE_KEYS = {
  STATIC_SHELL: 'dg_static_shell',
  CACHE_TIMESTAMP: 'dg_cache_timestamp'
};

// Market display names
const MARKET_NAMES = {
  'player_points': 'PTS',
  'player_rebounds': 'REB',
  'player_assists': 'AST',
  'player_threes': '3PM',
  'player_blocks': 'BLK',
  'player_steals': 'STL',
  'player_turnovers': 'TO',
  'player_points_alternate': 'PTS',
  'player_rebounds_alternate': 'REB',
  'player_assists_alternate': 'AST',
  'player_threes_alternate': '3PM',
  'player_blocks_alternate': 'BLK',
  'player_steals_alternate': 'STL',
  'player_turnovers_alternate': 'TO'
};

// ==================== CACHE SERVICE ====================

const CacheService = {
  // Check if static shell is cached and valid (24h)
  getStaticShell: () => {
    try {
      const cached = localStorage.getItem(CACHE_KEYS.STATIC_SHELL);
      const timestamp = localStorage.getItem(CACHE_KEYS.CACHE_TIMESTAMP);
      
      if (cached && timestamp) {
        const age = Date.now() - parseInt(timestamp);
        const maxAge = 24 * 60 * 60 * 1000; // 24 hours
        
        if (age < maxAge) {
          return {
            hit: true,
            age: age / 1000,
            data: JSON.parse(cached)
          };
        }
      }
    } catch (e) {
      console.error('Cache read error:', e);
    }
    return { hit: false, data: null };
  },
  
  // Store static shell in localStorage
  setStaticShell: (data) => {
    try {
      localStorage.setItem(CACHE_KEYS.STATIC_SHELL, JSON.stringify(data));
      localStorage.setItem(CACHE_KEYS.CACHE_TIMESTAMP, Date.now().toString());
    } catch (e) {
      console.error('Cache write error:', e);
    }
  },
  
  // Clear cache
  clear: () => {
    localStorage.removeItem(CACHE_KEYS.STATIC_SHELL);
    localStorage.removeItem(CACHE_KEYS.CACHE_TIMESTAMP);
  }
};

// ==================== LOADING PLACEHOLDER ====================

const LinePlaceholder = () => (
  <span className="inline-block w-8 h-4 bg-zinc-800 animate-pulse rounded" />
);

// ==================== PLAYER ROW (MEMOIZED FOR VIRTUAL SCROLLING) ====================

const PlayerRow = memo(({ player, isExpanded, onToggle, linesLoaded }) => {
  const hasInjury = player.injury_info?.warning_level && player.injury_info.warning_level !== 'none';
  const isOut = player.injury_info?.warning_level === 'out';
  
  return (
    <div 
      className={`
        flex flex-col border-b border-zinc-800
        ${isOut ? 'opacity-50' : ''}
      `}
      data-testid={`player-row-${player.player_name}`}
    >
      {/* Collapsed Header */}
      <div 
        className={`
          flex items-center justify-between p-4 cursor-pointer
          bg-zinc-900/50 hover:bg-zinc-800/50 transition-colors
          border-l-4 ${
            isOut ? 'border-l-red-500' :
            hasInjury ? 'border-l-yellow-500' :
            (player.demons_count || 0) > 0 ? 'border-l-red-500/50' :
            (player.goblins_count || 0) > 0 ? 'border-l-green-500/50' :
            'border-l-zinc-700'
          }
        `}
        onClick={onToggle}
      >
        <div className="flex items-center gap-4">
          <div className="text-zinc-500">
            {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </div>
          
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-white">{player.player_name}</span>
              {player.team && (
                <Badge variant="outline" className="bg-zinc-800 text-zinc-400 border-zinc-700 text-xs">
                  {player.team}
                </Badge>
              )}
              {player.position && (
                <span className="text-zinc-500 text-xs">{player.position}</span>
              )}
            </div>
            
            {hasInjury && (
              <div className={`flex items-center gap-1 mt-1 text-xs ${isOut ? 'text-red-400' : 'text-yellow-400'}`}>
                <AlertTriangle className="w-3 h-3" />
                <span>{player.injury_info?.injury_status || (isOut ? 'OUT' : 'QUESTIONABLE')}</span>
              </div>
            )}
          </div>
        </div>
        
        {/* Live Line Counts */}
        <div className="flex items-center gap-4">
          {linesLoaded ? (
            <>
              {(player.demons_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <Skull className="w-4 h-4 text-red-400" />
                  <span className="text-red-400 font-bold">{player.demons_count}</span>
                </div>
              )}
              {(player.goblins_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <Ghost className="w-4 h-4 text-green-400" />
                  <span className="text-green-400 font-bold">{player.goblins_count}</span>
                </div>
              )}
              <div className="text-zinc-500 text-sm">
                {(player.props?.length || 0)} props
              </div>
            </>
          ) : (
            <div className="flex items-center gap-2">
              <LinePlaceholder />
              <span className="text-zinc-600 text-xs">Loading lines...</span>
            </div>
          )}
        </div>
      </div>
      
      {/* Expanded Props */}
      {isExpanded && player.props && player.props.length > 0 && (
        <div className="bg-zinc-950 border-l-4 border-l-zinc-700 ml-4">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
                <th className="text-left p-3 w-24">TYPE</th>
                <th className="text-left p-3">PROP</th>
                <th className="text-center p-3 w-20">LINE</th>
                <th className="text-center p-3 w-20">ODDS</th>
                <th className="text-left p-3 w-32">HIT RATE</th>
              </tr>
            </thead>
            <tbody>
              {player.props.slice(0, 20).map((prop, idx) => {
                const stats = player.stats_summary?.[prop.market?.replace('_alternate', '')] || {};
                const l10 = stats.l10 || {};
                
                return (
                  <tr 
                    key={idx}
                    className={`
                      border-b border-zinc-800/50 hover:bg-zinc-900/50
                      ${prop.is_demon ? 'bg-red-950/20' : ''}
                      ${prop.is_goblin ? 'bg-green-950/20' : ''}
                    `}
                  >
                    <td className="p-3">
                      {prop.is_demon && (
                        <Badge className="bg-red-600/30 text-red-400 border-red-500/50 text-xs">
                          <Skull className="w-3 h-3 mr-1" />DEMON
                        </Badge>
                      )}
                      {prop.is_goblin && (
                        <Badge className="bg-green-600/30 text-green-400 border-green-500/50 text-xs">
                          <Ghost className="w-3 h-3 mr-1" />GOBLIN
                        </Badge>
                      )}
                      {!prop.is_demon && !prop.is_goblin && (
                        <span className="text-zinc-600 text-xs">STD</span>
                      )}
                    </td>
                    <td className="p-3">
                      <span className="text-white font-mono">
                        {MARKET_NAMES[prop.market] || prop.market?.replace('_alternate', '')}
                      </span>
                    </td>
                    <td className="p-3 text-center">
                      <div className="flex flex-col items-center">
                        <span className="font-mono text-white font-bold">{prop.line}</span>
                        <span className={`text-[10px] ${prop.direction === 'Over' ? 'text-green-400' : 'text-red-400'}`}>
                          {prop.direction}
                        </span>
                      </div>
                    </td>
                    <td className="p-3 text-center">
                      <span className={`font-mono ${
                        prop.price === 100 ? 'text-red-400 font-bold' :
                        prop.price < 0 ? 'text-green-400' : 'text-zinc-300'
                      }`}>
                        {prop.price > 0 ? `+${prop.price}` : prop.price}
                      </span>
                    </td>
                    <td className="p-3">
                      {l10.hit_rate !== undefined ? (
                        <span className={`font-mono text-sm ${
                          (l10.hit_rate || 0) >= 0.7 ? 'text-green-400 font-bold' :
                          (l10.hit_rate || 0) >= 0.5 ? 'text-yellow-400' : 'text-zinc-400'
                        }`}>
                          L10: {((l10.hit_rate || 0) * 100).toFixed(0)}%
                        </span>
                      ) : (
                        <span className="text-zinc-600 text-xs">No data</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
});

PlayerRow.displayName = 'PlayerRow';

// ==================== TRENDING CARD ====================

const TrendingCard = memo(({ player, rank, linesLoaded }) => (
  <Card className="bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 p-4">
    <div className="flex items-start justify-between mb-2">
      <div className="flex items-center gap-2">
        <div className={`
          w-7 h-7 rounded-full flex items-center justify-center font-bold text-sm
          ${rank === 1 ? 'bg-yellow-500 text-black' : 
            rank === 2 ? 'bg-zinc-400 text-black' :
            rank === 3 ? 'bg-amber-700 text-white' :
            'bg-zinc-700 text-zinc-300'}
        `}>
          {rank}
        </div>
        <div>
          <span className="font-bold text-white">{player.player_name}</span>
          {rank <= 3 && <Flame className="w-4 h-4 text-orange-500 inline ml-1" />}
        </div>
      </div>
    </div>
    
    <div className="flex items-center gap-2 text-xs text-zinc-400 mb-2">
      <Badge variant="outline" className="bg-zinc-800 border-zinc-700">{player.team || 'NBA'}</Badge>
      {player.position && <span>{player.position}</span>}
    </div>
    
    {linesLoaded ? (
      <div className="flex items-center gap-3">
        {(player.demons_count || 0) > 0 && (
          <div className="flex items-center gap-1">
            <Skull className="w-4 h-4 text-red-500" />
            <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
          </div>
        )}
        {(player.goblins_count || 0) > 0 && (
          <div className="flex items-center gap-1">
            <Ghost className="w-4 h-4 text-green-500" />
            <span className="text-green-400 font-bold text-sm">{player.goblins_count}</span>
          </div>
        )}
      </div>
    ) : (
      <div className="flex items-center gap-2">
        <div className="w-12 h-4 bg-zinc-800 animate-pulse rounded" />
        <div className="w-12 h-4 bg-zinc-800 animate-pulse rounded" />
      </div>
    )}
  </Card>
));

TrendingCard.displayName = 'TrendingCard';

// ==================== MAIN DASHBOARD ====================

export const DemonGoblinDashboardOptimized = () => {
  // State
  const [players, setPlayers] = useState([]);
  const [trending, setTrending] = useState([]);
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedPlayers, setExpandedPlayers] = useState(new Set());
  const [filterType, setFilterType] = useState('all');
  const [cacheStatus, setCacheStatus] = useState({ static: null, lines: null });

  // ==================== DATA LOADING ====================
  
  // Step 1: Load static shell (instant from localStorage or API)
  const loadStaticShell = useCallback(async () => {
    // Try localStorage first
    const cached = CacheService.getStaticShell();
    
    if (cached.hit) {
      console.log(`[CACHE HIT] Static shell from localStorage (age: ${cached.age.toFixed(0)}s)`);
      setPlayers(cached.data.players || []);
      setTrending(cached.data.trending || []);
      setStaticLoaded(true);
      setCacheStatus(prev => ({ ...prev, static: { hit: true, age: cached.age } }));
      return;
    }
    
    // Cache miss - try API static shell first
    console.log('[CACHE MISS] Fetching static shell from API...');
    try {
      const response = await axios.get(`${API}/v3/static-shell`);
      if (response.data.success && response.data.players_count > 0) {
        setPlayers(response.data.players || []);
        setTrending(response.data.trending || []);
        setStaticLoaded(true);
        setCacheStatus(prev => ({ ...prev, static: { hit: response.data.cache_hit, age: response.data.cache_age_seconds } }));
        
        // Store in localStorage
        CacheService.setStaticShell({
          players: response.data.players,
          trending: response.data.trending
        });
        return;
      }
      
      // Static shell empty - fall back to regular board
      console.log('[FALLBACK] Static shell empty, using v3/board...');
      const boardResponse = await axios.get(`${API}/v3/board`);
      if (boardResponse.data.success) {
        setPlayers(boardResponse.data.players || []);
        setStaticLoaded(true);
        setLinesLoaded(true); // Board includes lines
        setCacheStatus(prev => ({ ...prev, static: { hit: false, age: 0 } }));
        
        // Also get trending
        try {
          const trendingResponse = await axios.get(`${API}/v3/trending`);
          if (trendingResponse.data.success) {
            setTrending(trendingResponse.data.trending || []);
          }
        } catch (e) {
          console.log('Trending fetch failed:', e);
        }
      }
    } catch (error) {
      console.error('Error loading static shell:', error);
    }
  }, []);
  
  // Step 2: Hydrate with live lines (background fetch)
  const loadLiveLines = useCallback(async () => {
    try {
      console.log('[LINES] Fetching live betting lines...');
      const response = await axios.get(`${API}/v3/live-lines`);
      
      if (response.data.success) {
        const lines = response.data.lines || {};
        
        // Hydrate players with live lines
        setPlayers(prev => prev.map(player => {
          const playerLines = lines[player.player_name] || [];
          const demonsCount = playerLines.filter(l => l.is_demon).length;
          const goblinsCount = playerLines.filter(l => l.is_goblin).length;
          
          return {
            ...player,
            props: playerLines,
            demons_count: demonsCount,
            goblins_count: goblinsCount
          };
        }));
        
        // Update trending with line counts
        setTrending(prev => prev.map(t => {
          const playerLines = lines[t.player_name] || [];
          return {
            ...t,
            demons_count: playerLines.filter(l => l.is_demon).length,
            goblins_count: playerLines.filter(l => l.is_goblin).length
          };
        }));
        
        setLinesLoaded(true);
        setCacheStatus(prev => ({ ...prev, lines: { hit: response.data.cache_hit, age: response.data.cache_age_seconds } }));
        
        console.log(`[LINES] Loaded ${response.data.total_lines} lines (${response.data.total_demons} D, ${response.data.total_goblins} G)`);
      }
    } catch (error) {
      console.error('Error loading live lines:', error);
    }
  }, []);
  
  // Full sync (24h refresh)
  const triggerFullSync = async () => {
    try {
      setSyncing(true);
      setLinesLoaded(false);
      toast.info('Starting full sync (this may take a few minutes)...');
      
      // Clear cache
      CacheService.clear();
      
      const response = await axios.post(`${API}/v3/sync`, {}, { timeout: 600000 });
      
      if (response.data.success) {
        const result = response.data.result || {};
        toast.success(`Sync complete! ${result.unique_players} players, ${result.demons_count} D, ${result.goblins_count} G`);
        
        // Reload data
        await loadStaticShell();
        await loadLiveLines();
      }
    } catch (error) {
      console.error('Sync error:', error);
      toast.error('Sync failed - check console for details');
    } finally {
      setSyncing(false);
    }
  };
  
  // Initial load
  useEffect(() => {
    const init = async () => {
      await loadStaticShell();
      // Small delay to let static render first
      setTimeout(() => loadLiveLines(), 100);
    };
    init();
    
    // Refresh lines every 60 seconds
    const linesInterval = setInterval(loadLiveLines, 60000);
    
    return () => clearInterval(linesInterval);
  }, [loadStaticShell, loadLiveLines]);
  
  // ==================== FILTERING ====================
  
  const filteredPlayers = players.filter(p => {
    // Search filter
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      if (!p.player_name?.toLowerCase().includes(search) && !p.team?.toLowerCase().includes(search)) {
        return false;
      }
    }
    
    // Type filter
    if (filterType === 'demons') return (p.demons_count || 0) > 0;
    if (filterType === 'goblins') return (p.goblins_count || 0) > 0;
    
    return true;
  });
  
  // Stats
  const totalDemons = players.reduce((acc, p) => acc + (p.demons_count || 0), 0);
  const totalGoblins = players.reduce((acc, p) => acc + (p.goblins_count || 0), 0);
  
  // Toggle player expansion
  const togglePlayer = (playerName) => {
    setExpandedPlayers(prev => {
      const newSet = new Set(prev);
      if (newSet.has(playerName)) {
        newSet.delete(playerName);
      } else {
        newSet.add(playerName);
      }
      return newSet;
    });
  };

  return (
    <div className="min-h-screen bg-zinc-950 p-4 md:p-6">
      <div className="max-w-[1600px] mx-auto">
        {/* Header */}
        <header className="mb-6 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Skull className="w-8 h-8 text-red-500" />
              <span className="text-zinc-500">&</span>
              <Ghost className="w-8 h-8 text-green-500" />
              <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">
                DEMON & GOBLIN
              </h1>
              <Badge className="bg-purple-600/30 text-purple-400 border-purple-500/50 text-xs">
                v3.0 OPTIMIZED
              </Badge>
            </div>
            <p className="text-zinc-400 text-sm flex items-center gap-2">
              <span>{players.length} Players</span>
              <span>|</span>
              <span className="flex items-center gap-1">
                <HardDrive className="w-3 h-3" />
                Static: {cacheStatus.static?.hit ? 'CACHED' : 'FRESH'}
              </span>
              <span>|</span>
              <span className="flex items-center gap-1">
                <Zap className="w-3 h-3" />
                Lines: {linesLoaded ? (cacheStatus.lines?.hit ? 'CACHED' : 'LIVE') : 'Loading...'}
              </span>
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={triggerFullSync}
              disabled={syncing}
              variant="outline"
              size="sm"
              className="bg-purple-600/20 border-purple-600/50 text-purple-400 hover:bg-purple-600/30"
              data-testid="sync-btn"
            >
              <Database className={`w-4 h-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? 'Syncing...' : 'Full Sync (24h)'}
            </Button>

            <Button
              onClick={loadLiveLines}
              disabled={!staticLoaded}
              variant="outline"
              size="sm"
              className="bg-zinc-800 border-zinc-700 text-white hover:bg-zinc-700"
              data-testid="refresh-lines-btn"
            >
              <RefreshCw className="w-4 h-4 mr-1" />
              Refresh Lines
            </Button>
          </div>
        </header>

        {/* Stats Bar */}
        <Card className="mb-6 bg-zinc-900 border-zinc-800 p-4">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-8">
              <div 
                className={`flex items-center gap-2 cursor-pointer hover:opacity-80 ${filterType === 'demons' ? 'ring-2 ring-red-500 rounded-lg p-2 -m-2' : ''}`}
                onClick={() => setFilterType(filterType === 'demons' ? 'all' : 'demons')}
              >
                <Skull className="w-6 h-6 text-red-500" />
                <div>
                  {linesLoaded ? (
                    <span className="text-red-400 font-bold text-2xl">{totalDemons}</span>
                  ) : (
                    <div className="w-12 h-8 bg-zinc-800 animate-pulse rounded" />
                  )}
                  <p className="text-zinc-500 text-xs">Demons (+100)</p>
                </div>
              </div>
              
              <div 
                className={`flex items-center gap-2 cursor-pointer hover:opacity-80 ${filterType === 'goblins' ? 'ring-2 ring-green-500 rounded-lg p-2 -m-2' : ''}`}
                onClick={() => setFilterType(filterType === 'goblins' ? 'all' : 'goblins')}
              >
                <Ghost className="w-6 h-6 text-green-500" />
                <div>
                  {linesLoaded ? (
                    <span className="text-green-400 font-bold text-2xl">{totalGoblins}</span>
                  ) : (
                    <div className="w-12 h-8 bg-zinc-800 animate-pulse rounded" />
                  )}
                  <p className="text-zinc-500 text-xs">Goblins (Default)</p>
                </div>
              </div>

              <div className="border-l border-zinc-700 pl-6">
                <span className="text-zinc-400 text-sm">
                  <User className="w-4 h-4 inline mr-1" />
                  <span className="text-white font-bold">{filteredPlayers.length}</span> / {players.length}
                </span>
              </div>
            </div>
            
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <Clock className="w-4 h-4" />
              Lines refresh: 60s
            </div>
          </div>
        </Card>

        {/* Trending 10 */}
        {trending.length > 0 && (
          <div className="mb-6" data-testid="trending-section">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center gap-2 bg-gradient-to-r from-purple-600/20 to-pink-600/20 px-4 py-2 rounded-lg border border-purple-500/30">
                <Flame className="w-5 h-5 text-orange-500" />
                <h2 className="text-xl font-bold text-white">Most Popular Today</h2>
                <Star className="w-5 h-5 text-yellow-500" />
              </div>
            </div>
            
            <div className="grid grid-cols-2 md:grid-cols-5 lg:grid-cols-10 gap-3">
              {trending.slice(0, 10).map((player, idx) => (
                <TrendingCard 
                  key={player.player_name} 
                  player={player} 
                  rank={idx + 1}
                  linesLoaded={linesLoaded}
                />
              ))}
            </div>
          </div>
        )}

        {/* Search */}
        <div className="mb-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-zinc-400" />
            <Input
              placeholder="Search player..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 bg-zinc-900 border-zinc-700 text-white"
              data-testid="search-input"
            />
          </div>
        </div>

        {/* Players List (with fallback to regular scroll if virtual fails) */}
        <div className="rounded-lg border border-zinc-800 overflow-hidden max-h-[600px] overflow-y-auto" data-testid="players-list">
          {!staticLoaded ? (
            <div className="p-8 text-center">
              <Activity className="w-8 h-8 text-purple-500 mx-auto mb-3 animate-pulse" />
              <p className="text-zinc-400">Loading player data...</p>
            </div>
          ) : filteredPlayers.length === 0 ? (
            <div className="p-8 text-center text-zinc-400">
              No players found. {!linesLoaded && 'Run full sync to load data.'}
            </div>
          ) : (
            filteredPlayers.map((player, index) => (
              <PlayerRow
                key={player.player_name || index}
                player={player}
                isExpanded={expandedPlayers.has(player.player_name)}
                onToggle={() => togglePlayer(player.player_name)}
                linesLoaded={linesLoaded}
              />
            ))
          )}
        </div>

        {/* Legend */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="bg-zinc-900/50 border-zinc-800 p-4">
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <HardDrive className="w-4 h-4 text-blue-400" />
              <span><strong>Static Shell (24h):</strong> Player metadata, teams, stats</span>
            </div>
          </Card>
          <Card className="bg-zinc-900/50 border-zinc-800 p-4">
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <Zap className="w-4 h-4 text-yellow-400" />
              <span><strong>Dynamic Pulse (60s):</strong> Live lines, prices, D/G tags</span>
            </div>
          </Card>
          <Card className="bg-zinc-900/50 border-zinc-800 p-4">
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <Activity className="w-4 h-4 text-green-400" />
              <span><strong>Virtual Scroll:</strong> Only renders visible rows</span>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default DemonGoblinDashboardOptimized;
