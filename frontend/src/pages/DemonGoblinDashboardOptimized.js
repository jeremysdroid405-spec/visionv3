import React, { useState, useEffect, useCallback, memo } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { 
  Activity, RefreshCw, Search, Database, 
  ChevronDown, ChevronRight, AlertTriangle, Skull, Ghost,
  User, Flame, Star, Clock, Zap, HardDrive, ArrowLeft, X
} from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Cache keys
const CACHE_KEYS = {
  STATIC_SHELL: 'dg_static_shell',
  CACHE_TIMESTAMP: 'dg_cache_timestamp'
};

// Condensed market names (RPA format)
const MARKET_SHORT = {
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
  'player_turnovers_alternate': 'TO',
  'player_points_rebounds': 'P+R',
  'player_points_assists': 'P+A',
  'player_rebounds_assists': 'R+A',
  'player_points_rebounds_assists': 'PRA',
  'player_points_rebounds_alternate': 'P+R',
  'player_points_assists_alternate': 'P+A',
  'player_rebounds_assists_alternate': 'R+A',
  'player_points_rebounds_assists_alternate': 'PRA'
};

// Get condensed market name
const getMarketName = (market) => {
  if (!market) return '---';
  const clean = market.replace('_alternate', '');
  return MARKET_SHORT[market] || MARKET_SHORT[clean] || market.split('_').pop()?.toUpperCase() || '---';
};

// ==================== CACHE SERVICE ====================

const CacheService = {
  getStaticShell: () => {
    try {
      const cached = localStorage.getItem(CACHE_KEYS.STATIC_SHELL);
      const timestamp = localStorage.getItem(CACHE_KEYS.CACHE_TIMESTAMP);
      if (cached && timestamp) {
        const age = Date.now() - parseInt(timestamp);
        if (age < 24 * 60 * 60 * 1000) {
          return { hit: true, age: age / 1000, data: JSON.parse(cached) };
        }
      }
    } catch (e) { console.error('Cache read error:', e); }
    return { hit: false, data: null };
  },
  setStaticShell: (data) => {
    try {
      localStorage.setItem(CACHE_KEYS.STATIC_SHELL, JSON.stringify(data));
      localStorage.setItem(CACHE_KEYS.CACHE_TIMESTAMP, Date.now().toString());
    } catch (e) { console.error('Cache write error:', e); }
  },
  clear: () => {
    localStorage.removeItem(CACHE_KEYS.STATIC_SHELL);
    localStorage.removeItem(CACHE_KEYS.CACHE_TIMESTAMP);
  }
};

// ==================== SKELETON LOADERS ====================

const SkeletonProp = () => (
  <div className="flex items-center justify-between p-2 bg-zinc-900/50 rounded animate-pulse">
    <div className="flex items-center gap-2">
      <div className="w-10 h-5 bg-zinc-800 rounded" />
      <div className="w-8 h-5 bg-zinc-800 rounded" />
    </div>
    <div className="w-12 h-5 bg-zinc-800 rounded" />
  </div>
);

const SkeletonPlayerDetail = () => (
  <div className="space-y-2 p-3">
    {[1, 2, 3, 4, 5, 6].map(i => <SkeletonProp key={i} />)}
  </div>
);

// ==================== PROP ROW (Condensed RPA Format) ====================

const PropRow = memo(({ prop, compact = false }) => {
  const market = getMarketName(prop.market);
  const line = prop.line;
  const direction = prop.direction;
  const price = prop.price;
  const hitRate = prop.hit_rates?.l10?.hit_rate || prop.hit_rate || 0;
  const hitPct = Math.round((hitRate || 0) * 100);
  
  const isDemon = prop.is_demon;
  const isGoblin = prop.is_goblin;
  
  // Compact single-line format for mobile
  return (
    <div 
      className={`
        flex items-center justify-between px-2 py-1.5 rounded text-sm
        ${isDemon ? 'bg-red-950/40 border-l-2 border-red-500' : ''}
        ${isGoblin ? 'bg-green-950/40 border-l-2 border-green-500' : ''}
        ${!isDemon && !isGoblin ? 'bg-zinc-900/30' : ''}
      `}
    >
      {/* Left: Type Icon + Market + Line */}
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        {isDemon && <Skull className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />}
        {isGoblin && <Ghost className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />}
        {!isDemon && !isGoblin && <div className="w-3.5" />}
        
        <span className="text-zinc-400 font-mono text-xs">{market}</span>
        
        <span className={`font-bold text-white ${compact ? 'text-sm' : 'text-base'}`}>
          {line}
        </span>
        
        <span className={`text-[10px] ${direction === 'Over' ? 'text-green-400' : 'text-red-400'}`}>
          {direction === 'Over' ? 'O' : 'U'}
        </span>
      </div>
      
      {/* Right: Hit Rate (high contrast) */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {price !== undefined && (
          <span className={`text-xs font-mono ${
            price === 100 ? 'text-red-400' : price < 0 ? 'text-green-400' : 'text-zinc-500'
          }`}>
            {price > 0 ? `+${price}` : price}
          </span>
        )}
        
        <span className={`font-bold text-sm min-w-[36px] text-right ${
          hitPct >= 70 ? 'text-green-400' :
          hitPct >= 50 ? 'text-yellow-400' :
          hitPct > 0 ? 'text-zinc-400' : 'text-zinc-600'
        }`}>
          {hitPct > 0 ? `${hitPct}%` : '---'}
        </span>
      </div>
    </div>
  );
});

PropRow.displayName = 'PropRow';

// ==================== TRENDING CARD (Clickable) ====================

const TrendingCard = memo(({ player, rank, onClick, linesLoaded }) => {
  const hasInjury = player.injury_info?.has_injury;
  
  return (
    <Card 
      className={`
        bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 
        hover:border-purple-500/50 hover:scale-[1.02] transition-all duration-200
        cursor-pointer active:scale-[0.98]
        ${hasInjury ? 'ring-1 ring-yellow-500/30' : ''}
      `}
      onClick={onClick}
      data-testid={`trending-card-${rank}`}
    >
      <div className="p-3">
        {/* Header: Rank + Name */}
        <div className="flex items-center gap-2 mb-2">
          <div className={`
            w-6 h-6 rounded-full flex items-center justify-center font-bold text-xs
            ${rank === 1 ? 'bg-yellow-500 text-black' : 
              rank === 2 ? 'bg-zinc-400 text-black' :
              rank === 3 ? 'bg-amber-700 text-white' :
              'bg-zinc-700 text-zinc-300'}
          `}>
            {rank}
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1">
              <span className="font-bold text-white text-sm truncate">{player.player_name}</span>
              {rank <= 3 && <Flame className="w-3 h-3 text-orange-500 flex-shrink-0" />}
            </div>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{player.team || '---'}</span>
              {player.position && <span>· {player.position}</span>}
            </div>
          </div>
        </div>
        
        {/* Demon/Goblin Counts */}
        {linesLoaded ? (
          <div className="flex items-center gap-3">
            {(player.demons_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <Skull className="w-3.5 h-3.5 text-red-500" />
                <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
              </div>
            )}
            {(player.goblins_count || 0) > 0 && (
              <div className="flex items-center gap-1">
                <Ghost className="w-3.5 h-3.5 text-green-500" />
                <span className="text-green-400 font-bold text-sm">{player.goblins_count}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <div className="w-10 h-4 bg-zinc-800 animate-pulse rounded" />
            <div className="w-10 h-4 bg-zinc-800 animate-pulse rounded" />
          </div>
        )}
        
        {/* Injury Warning */}
        {hasInjury && (
          <div className="mt-2 text-[10px] text-yellow-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            {player.injury_info?.injury_status || 'CHECK STATUS'}
          </div>
        )}
      </div>
    </Card>
  );
});

TrendingCard.displayName = 'TrendingCard';

// ==================== PLAYER DETAIL PAGE ====================

const PlayerDetailPage = ({ playerName, onBack }) => {
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  useEffect(() => {
    const fetchPlayer = async () => {
      try {
        setLoading(true);
        const response = await axios.get(`${API}/v3/player/${encodeURIComponent(playerName)}`);
        if (response.data.success) {
          setPlayer(response.data.player);
        } else {
          setError('Player not found');
        }
      } catch (err) {
        console.error('Error fetching player:', err);
        setError('Failed to load player data');
      } finally {
        setLoading(false);
      }
    };
    
    fetchPlayer();
  }, [playerName]);
  
  // Sort props: Demons first, then Goblins, then by hit rate
  const sortedProps = player?.props?.slice().sort((a, b) => {
    if (a.is_demon && !b.is_demon) return -1;
    if (!a.is_demon && b.is_demon) return 1;
    if (a.is_goblin && !b.is_goblin) return -1;
    if (!a.is_goblin && b.is_goblin) return 1;
    const hitA = a.hit_rates?.l10?.hit_rate || 0;
    const hitB = b.hit_rates?.l10?.hit_rate || 0;
    return hitB - hitA;
  }) || [];
  
  const demons = sortedProps.filter(p => p.is_demon);
  const goblins = sortedProps.filter(p => p.is_goblin);
  const standard = sortedProps.filter(p => !p.is_demon && !p.is_goblin);
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="text-zinc-400 hover:text-white p-1"
            data-testid="back-btn"
          >
            <ArrowLeft className="w-5 h-5" />
          </Button>
          
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-bold text-white truncate">{playerName}</h1>
            {player && (
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="font-mono">{player.team}</span>
                {player.position && <span>· {player.position}</span>}
                {player.injury_info?.has_injury && (
                  <Badge className="bg-yellow-600/20 text-yellow-400 border-yellow-600/30 text-[10px] px-1">
                    {player.injury_info.injury_status}
                  </Badge>
                )}
              </div>
            )}
          </div>
          
          {player && (
            <div className="flex items-center gap-2 flex-shrink-0">
              <div className="flex items-center gap-1">
                <Skull className="w-4 h-4 text-red-500" />
                <span className="text-red-400 font-bold">{demons.length}</span>
              </div>
              <div className="flex items-center gap-1">
                <Ghost className="w-4 h-4 text-green-500" />
                <span className="text-green-400 font-bold">{goblins.length}</span>
              </div>
            </div>
          )}
        </div>
      </div>
      
      {/* Content */}
      <div className="p-3">
        {loading ? (
          <SkeletonPlayerDetail />
        ) : error ? (
          <div className="text-center py-8 text-zinc-400">
            <p>{error}</p>
            <Button variant="outline" size="sm" onClick={onBack} className="mt-4">
              Go Back
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Demons Section */}
            {demons.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Skull className="w-4 h-4 text-red-500" />
                  <span className="text-red-400 font-bold text-sm">DEMONS (+100)</span>
                  <span className="text-zinc-600 text-xs">{demons.length} props</span>
                </div>
                <div className="space-y-1">
                  {demons.map((prop, idx) => (
                    <PropRow key={`demon-${idx}`} prop={prop} />
                  ))}
                </div>
              </div>
            )}
            
            {/* Goblins Section */}
            {goblins.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Ghost className="w-4 h-4 text-green-500" />
                  <span className="text-green-400 font-bold text-sm">GOBLINS (Default)</span>
                  <span className="text-zinc-600 text-xs">{goblins.length} props</span>
                </div>
                <div className="space-y-1">
                  {goblins.map((prop, idx) => (
                    <PropRow key={`goblin-${idx}`} prop={prop} />
                  ))}
                </div>
              </div>
            )}
            
            {/* Standard Props */}
            {standard.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-zinc-400 font-bold text-sm">STANDARD</span>
                  <span className="text-zinc-600 text-xs">{standard.length} props</span>
                </div>
                <div className="space-y-1">
                  {standard.map((prop, idx) => (
                    <PropRow key={`std-${idx}`} prop={prop} />
                  ))}
                </div>
              </div>
            )}
            
            {sortedProps.length === 0 && (
              <div className="text-center py-8 text-zinc-500">
                No props available for this player
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ==================== PLAYER ROW (List View) ====================

const PlayerRow = memo(({ player, isExpanded, onToggle, onClick, linesLoaded }) => {
  const hasInjury = player.injury_info?.warning_level && player.injury_info.warning_level !== 'none';
  const isOut = player.injury_info?.warning_level === 'out';
  
  return (
    <div className={`border-b border-zinc-800/50 ${isOut ? 'opacity-50' : ''}`}>
      {/* Collapsed Header */}
      <div 
        className={`
          flex items-center justify-between px-3 py-2.5 cursor-pointer
          bg-zinc-900/30 hover:bg-zinc-800/50 transition-colors
          border-l-2 ${
            isOut ? 'border-l-red-500' :
            hasInjury ? 'border-l-yellow-500' :
            (player.demons_count || 0) > 0 ? 'border-l-red-500/50' :
            (player.goblins_count || 0) > 0 ? 'border-l-green-500/50' :
            'border-l-transparent'
          }
        `}
        onClick={onClick || onToggle}
        data-testid={`player-row-${player.player_name}`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <div className="text-zinc-600">
            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </div>
          
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm truncate">{player.player_name}</span>
              <span className="text-zinc-500 text-xs font-mono flex-shrink-0">{player.team}</span>
            </div>
            
            {hasInjury && (
              <div className={`flex items-center gap-1 text-[10px] ${isOut ? 'text-red-400' : 'text-yellow-400'}`}>
                <AlertTriangle className="w-2.5 h-2.5" />
                <span>{player.injury_info?.injury_status || (isOut ? 'OUT' : 'GTD')}</span>
              </div>
            )}
          </div>
        </div>
        
        {/* Counts */}
        <div className="flex items-center gap-3 flex-shrink-0">
          {linesLoaded ? (
            <>
              {(player.demons_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <Skull className="w-3.5 h-3.5 text-red-400" />
                  <span className="text-red-400 font-bold text-sm">{player.demons_count}</span>
                </div>
              )}
              {(player.goblins_count || 0) > 0 && (
                <div className="flex items-center gap-1">
                  <Ghost className="w-3.5 h-3.5 text-green-400" />
                  <span className="text-green-400 font-bold text-sm">{player.goblins_count}</span>
                </div>
              )}
              <span className="text-zinc-600 text-xs">{player.props?.length || 0}</span>
            </>
          ) : (
            <div className="w-16 h-4 bg-zinc-800 animate-pulse rounded" />
          )}
        </div>
      </div>
    </div>
  );
});

PlayerRow.displayName = 'PlayerRow';

// ==================== MAIN DASHBOARD ====================

export const DemonGoblinDashboardOptimized = () => {
  // State
  const [players, setPlayers] = useState([]);
  const [trending, setTrending] = useState([]);
  const [linesLoaded, setLinesLoaded] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [cacheStatus, setCacheStatus] = useState({ static: null, lines: null });
  
  // Navigation state
  const [selectedPlayer, setSelectedPlayer] = useState(null);

  // ==================== DATA LOADING ====================
  
  const loadStaticShell = useCallback(async () => {
    const cached = CacheService.getStaticShell();
    
    if (cached.hit) {
      console.log(`[CACHE HIT] Static shell (age: ${cached.age.toFixed(0)}s)`);
      setPlayers(cached.data.players || []);
      setTrending(cached.data.trending || []);
      setStaticLoaded(true);
      setCacheStatus(prev => ({ ...prev, static: { hit: true, age: cached.age } }));
      return;
    }
    
    console.log('[CACHE MISS] Fetching from API...');
    try {
      const response = await axios.get(`${API}/v3/static-shell`);
      if (response.data.success && response.data.players_count > 0) {
        setPlayers(response.data.players || []);
        setTrending(response.data.trending || []);
        setStaticLoaded(true);
        setCacheStatus(prev => ({ ...prev, static: { hit: response.data.cache_hit, age: response.data.cache_age_seconds } }));
        CacheService.setStaticShell({ players: response.data.players, trending: response.data.trending });
        return;
      }
      
      // Fallback to board
      console.log('[FALLBACK] Using v3/board...');
      const boardResponse = await axios.get(`${API}/v3/board`);
      if (boardResponse.data.success) {
        setPlayers(boardResponse.data.players || []);
        setStaticLoaded(true);
        setLinesLoaded(true);
        setCacheStatus(prev => ({ ...prev, static: { hit: false, age: 0 } }));
        
        const trendingResponse = await axios.get(`${API}/v3/trending`);
        if (trendingResponse.data.success) {
          setTrending(trendingResponse.data.trending || []);
        }
      }
    } catch (error) {
      console.error('Error loading:', error);
    }
  }, []);
  
  const loadLiveLines = useCallback(async () => {
    try {
      const response = await axios.get(`${API}/v3/live-lines`);
      if (response.data.success) {
        const lines = response.data.lines || {};
        
        setPlayers(prev => prev.map(player => {
          const playerLines = lines[player.player_name] || [];
          return {
            ...player,
            props: playerLines,
            demons_count: playerLines.filter(l => l.is_demon).length,
            goblins_count: playerLines.filter(l => l.is_goblin).length
          };
        }));
        
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
      }
    } catch (error) {
      console.error('Error loading lines:', error);
    }
  }, []);
  
  const triggerFullSync = async () => {
    try {
      setSyncing(true);
      setLinesLoaded(false);
      toast.info('Starting full sync...');
      CacheService.clear();
      
      const response = await axios.post(`${API}/v3/sync`, {}, { timeout: 600000 });
      if (response.data.success) {
        const result = response.data.result || {};
        toast.success(`Sync complete! ${result.unique_players} players`);
        await loadStaticShell();
        await loadLiveLines();
      }
    } catch (error) {
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };
  
  useEffect(() => {
    const init = async () => {
      await loadStaticShell();
      setTimeout(() => loadLiveLines(), 100);
    };
    init();
    
    const linesInterval = setInterval(loadLiveLines, 60000);
    return () => clearInterval(linesInterval);
  }, [loadStaticShell, loadLiveLines]);
  
  // ==================== FILTERING ====================
  
  const filteredPlayers = players.filter(p => {
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      if (!p.player_name?.toLowerCase().includes(search) && !p.team?.toLowerCase().includes(search)) {
        return false;
      }
    }
    if (filterType === 'demons') return (p.demons_count || 0) > 0;
    if (filterType === 'goblins') return (p.goblins_count || 0) > 0;
    return true;
  });
  
  const totalDemons = players.reduce((acc, p) => acc + (p.demons_count || 0), 0);
  const totalGoblins = players.reduce((acc, p) => acc + (p.goblins_count || 0), 0);
  
  // ==================== NAVIGATION ====================
  
  const handlePlayerClick = (playerName) => {
    setSelectedPlayer(playerName);
  };
  
  const handleBack = () => {
    setSelectedPlayer(null);
  };
  
  // ==================== RENDER ====================
  
  // If a player is selected, show detail page
  if (selectedPlayer) {
    return <PlayerDetailPage playerName={selectedPlayer} onBack={handleBack} />;
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header - Mobile Optimized */}
      <header className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Skull className="w-6 h-6 text-red-500 flex-shrink-0" />
            <Ghost className="w-6 h-6 text-green-500 flex-shrink-0" />
            <h1 className="text-lg font-bold text-white truncate">DEMON & GOBLIN</h1>
            <Badge className="bg-purple-600/30 text-purple-400 border-purple-500/50 text-[10px] flex-shrink-0">
              v3
            </Badge>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              onClick={triggerFullSync}
              disabled={syncing}
              variant="ghost"
              size="sm"
              className="text-purple-400 hover:text-purple-300 p-1.5"
              data-testid="sync-btn"
            >
              <Database className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
            </Button>
            <Button
              onClick={loadLiveLines}
              disabled={!staticLoaded}
              variant="ghost"
              size="sm"
              className="text-zinc-400 hover:text-white p-1.5"
              data-testid="refresh-btn"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>
          </div>
        </div>
        
        {/* Sub-header info */}
        <div className="flex items-center gap-2 mt-1.5 text-[10px] text-zinc-500">
          <span>{players.length} Players</span>
          <span>·</span>
          <HardDrive className="w-3 h-3" />
          <span>{cacheStatus.static?.hit ? 'CACHED' : 'FRESH'}</span>
          <span>·</span>
          <Zap className="w-3 h-3" />
          <span>{linesLoaded ? 'LIVE' : 'Loading...'}</span>
        </div>
      </header>

      <div className="p-3 space-y-4">
        {/* Stats Bar - Compact */}
        <div className="flex items-center justify-between bg-zinc-900/50 rounded-lg px-3 py-2">
          <div className="flex items-center gap-4">
            <div 
              className={`flex items-center gap-1.5 cursor-pointer ${filterType === 'demons' ? 'ring-1 ring-red-500 rounded px-1 -mx-1' : ''}`}
              onClick={() => setFilterType(filterType === 'demons' ? 'all' : 'demons')}
            >
              <Skull className="w-5 h-5 text-red-500" />
              {linesLoaded ? (
                <span className="text-red-400 font-bold text-xl">{totalDemons}</span>
              ) : (
                <div className="w-10 h-6 bg-zinc-800 animate-pulse rounded" />
              )}
            </div>
            
            <div 
              className={`flex items-center gap-1.5 cursor-pointer ${filterType === 'goblins' ? 'ring-1 ring-green-500 rounded px-1 -mx-1' : ''}`}
              onClick={() => setFilterType(filterType === 'goblins' ? 'all' : 'goblins')}
            >
              <Ghost className="w-5 h-5 text-green-500" />
              {linesLoaded ? (
                <span className="text-green-400 font-bold text-xl">{totalGoblins}</span>
              ) : (
                <div className="w-10 h-6 bg-zinc-800 animate-pulse rounded" />
              )}
            </div>
          </div>
          
          <div className="text-xs text-zinc-500 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            60s
          </div>
        </div>

        {/* Trending 10 - Clickable Cards */}
        {trending.length > 0 && (
          <div data-testid="trending-section">
            <div className="flex items-center gap-2 mb-2">
              <Flame className="w-4 h-4 text-orange-500" />
              <span className="text-sm font-bold text-white">Most Popular Today</span>
              <Star className="w-4 h-4 text-yellow-500" />
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {trending.slice(0, 10).map((player, idx) => (
                <TrendingCard 
                  key={player.player_name} 
                  player={player} 
                  rank={idx + 1}
                  linesLoaded={linesLoaded}
                  onClick={() => handlePlayerClick(player.player_name)}
                />
              ))}
            </div>
          </div>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <Input
            placeholder="Search player..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 py-2 bg-zinc-900 border-zinc-800 text-white text-sm"
            data-testid="search-input"
          />
          {searchTerm && (
            <button 
              onClick={() => setSearchTerm('')}
              className="absolute right-3 top-1/2 transform -translate-y-1/2 text-zinc-500 hover:text-white"
            >
              <X className="w-4 h-4" />
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
            <div className="p-6 text-center text-zinc-500 text-sm">
              No players found
            </div>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              {filteredPlayers.map((player) => (
                <PlayerRow
                  key={player.player_name}
                  player={player}
                  isExpanded={false}
                  onToggle={() => {}}
                  onClick={() => handlePlayerClick(player.player_name)}
                  linesLoaded={linesLoaded}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default DemonGoblinDashboardOptimized;
