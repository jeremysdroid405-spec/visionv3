import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { 
  Activity, Crown, RefreshCw, Search, Database, 
  ChevronDown, ChevronRight, AlertTriangle, Skull, Ghost,
  TrendingUp, TrendingDown, User, Newspaper, Clock
} from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Market display names
const MARKET_NAMES = {
  'player_points': 'PTS',
  'player_rebounds': 'REB',
  'player_assists': 'AST',
  'player_threes': '3PM',
  'player_blocks': 'BLK',
  'player_steals': 'STL',
  'player_turnovers': 'TO',
  'player_points_rebounds': 'PTS+REB',
  'player_points_assists': 'PTS+AST',
  'player_rebounds_assists': 'REB+AST',
  'player_points_rebounds_assists': 'PRA',
  'player_double_double': '2x2',
  'player_first_basket': '1ST BSK',
  'alternate_player_points': 'ALT PTS',
  'alternate_player_rebounds': 'ALT REB',
  'alternate_player_assists': 'ALT AST',
  'alternate_player_threes': 'ALT 3PM'
};

// Format American odds
const formatOdds = (price) => {
  if (!price && price !== 0) return '-';
  return price > 0 ? `+${price}` : `${price}`;
};

// Hit Rate Display
const HitRateDisplay = ({ hitRates }) => {
  if (!hitRates) return <span className="text-zinc-500 text-xs">No data</span>;

  const l5 = hitRates.l5 || {};
  const l10 = hitRates.l10 || {};
  const season = hitRates.season || {};

  const getColor = (rate) => {
    if (rate >= 0.7) return 'text-green-400 font-bold';
    if (rate >= 0.5) return 'text-emerald-400';
    if (rate >= 0.4) return 'text-yellow-400';
    return 'text-zinc-400';
  };

  return (
    <div className="flex items-center gap-4 text-xs font-mono">
      <div className="flex flex-col items-center">
        <span className="text-zinc-500 text-[10px]">L5</span>
        <span className={getColor(l5.hit_rate || 0)}>
          {((l5.hit_rate || 0) * 100).toFixed(0)}%
        </span>
      </div>
      <div className="flex flex-col items-center">
        <span className="text-zinc-500 text-[10px]">L10</span>
        <span className={getColor(l10.hit_rate || 0)}>
          {((l10.hit_rate || 0) * 100).toFixed(0)}%
        </span>
      </div>
      <div className="flex flex-col items-center">
        <span className="text-zinc-500 text-[10px]">AVG</span>
        <span className="text-white">{(season.avg || 0).toFixed(1)}</span>
      </div>
    </div>
  );
};

// Demon Badge (Red - Hard Props)
const DemonBadge = ({ price }) => (
  <Badge className="bg-red-600/30 text-red-400 border-red-500/50 text-xs gap-1">
    <Skull className="w-3 h-3" />
    DEMON {formatOdds(price)}
  </Badge>
);

// Goblin Badge (Green - Easy Props)
const GoblinBadge = ({ price, hasWarning }) => (
  <Badge className={`${hasWarning ? 'bg-yellow-600/30 text-yellow-400 border-yellow-500/50' : 'bg-green-600/30 text-green-400 border-green-500/50'} text-xs gap-1`}>
    <Ghost className="w-3 h-3" />
    GOBLIN {formatOdds(price)}
    {hasWarning && <AlertTriangle className="w-3 h-3 ml-1" />}
  </Badge>
);

// Player Card (Collapsed State)
const PlayerCardCollapsed = ({ player, isExpanded, onToggle }) => {
  const injuryWarning = player.injury_warning;
  const hasInjury = injuryWarning && injuryWarning !== 'none';
  const isOut = injuryWarning === 'out';
  
  return (
    <div 
      className={`
        flex items-center justify-between p-4 cursor-pointer
        bg-zinc-900/50 hover:bg-zinc-800/50 transition-colors
        border-l-4 ${
          isOut ? 'border-l-red-500' :
          hasInjury ? 'border-l-yellow-500' :
          player.demons_count > 0 ? 'border-l-red-500/50' :
          player.goblins_count > 0 ? 'border-l-green-500/50' :
          'border-l-zinc-700'
        }
      `}
      onClick={onToggle}
      data-testid={`player-card-${player.player_name}`}
    >
      <div className="flex items-center gap-4">
        {/* Expand Icon */}
        <div className="text-zinc-500">
          {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
        </div>
        
        {/* Player Info */}
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
          
          {/* Injury Status */}
          {hasInjury && (
            <div className={`flex items-center gap-1 mt-1 text-xs ${isOut ? 'text-red-400' : 'text-yellow-400'}`}>
              <AlertTriangle className="w-3 h-3" />
              <span>{player.injury_status || (isOut ? 'OUT' : 'QUESTIONABLE')}</span>
            </div>
          )}
        </div>
      </div>
      
      {/* Prop Counts */}
      <div className="flex items-center gap-4">
        {player.demons_count > 0 && (
          <div className="flex items-center gap-1">
            <Skull className="w-4 h-4 text-red-400" />
            <span className="text-red-400 font-bold">{player.demons_count}</span>
          </div>
        )}
        {player.goblins_count > 0 && (
          <div className="flex items-center gap-1">
            <Ghost className="w-4 h-4 text-green-400" />
            <span className="text-green-400 font-bold">{player.goblins_count}</span>
            {player.has_goblin_warning && (
              <AlertTriangle className="w-3 h-3 text-yellow-400" />
            )}
          </div>
        )}
        <div className="text-zinc-500 text-sm">
          {player.total_props} props
        </div>
      </div>
    </div>
  );
};

// Player Card (Expanded State - Shows all props)
const PlayerCardExpanded = ({ player }) => {
  const props = player.props || [];
  
  // Sort: Demons first, then Goblins, then rest
  const sortedProps = [...props].sort((a, b) => {
    if (a.is_demon && !b.is_demon) return -1;
    if (!a.is_demon && b.is_demon) return 1;
    if (a.is_goblin && !b.is_goblin) return -1;
    if (!a.is_goblin && b.is_goblin) return 1;
    
    // Sort by hit rate
    const hitA = a.hit_rates?.l10?.hit_rate || 0;
    const hitB = b.hit_rates?.l10?.hit_rate || 0;
    return hitB - hitA;
  });
  
  return (
    <div className="bg-zinc-950 border-l-4 border-l-zinc-700 ml-4">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
            <th className="text-left p-3 w-32">TYPE</th>
            <th className="text-left p-3">PROP</th>
            <th className="text-center p-3 w-20">LINE</th>
            <th className="text-center p-3 w-20">ODDS</th>
            <th className="text-center p-3 w-16">BOOK</th>
            <th className="text-left p-3 w-40">HIT RATE</th>
            <th className="text-center p-3 w-20">TREND</th>
          </tr>
        </thead>
        <tbody>
          {sortedProps.map((prop, idx) => {
            const hitRates = prop.hit_rates || {};
            const trends = hitRates.trends || [];
            
            return (
              <tr 
                key={`${prop.market}-${prop.line}-${prop.bookmaker}-${idx}`}
                className={`
                  border-b border-zinc-800/50 hover:bg-zinc-900/50 transition-colors
                  ${prop.is_demon ? 'bg-red-950/20' : ''}
                  ${prop.is_goblin ? 'bg-green-950/20' : ''}
                `}
                data-testid={`prop-row-${idx}`}
              >
                <td className="p-3">
                  {prop.is_demon && <DemonBadge price={prop.price} />}
                  {prop.is_goblin && <GoblinBadge price={prop.price} hasWarning={prop.has_goblin_warning} />}
                  {!prop.is_demon && !prop.is_goblin && (
                    <span className="text-zinc-500 text-xs">STANDARD</span>
                  )}
                </td>
                <td className="p-3">
                  <Badge variant="outline" className="bg-zinc-800 text-white border-zinc-700 text-xs font-mono">
                    {MARKET_NAMES[prop.market] || prop.market}
                  </Badge>
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
                  <span className={`font-mono text-sm ${
                    prop.price >= 200 ? 'text-red-400 font-bold' :
                    prop.price <= -300 ? 'text-green-400 font-bold' :
                    'text-zinc-300'
                  }`}>
                    {formatOdds(prop.price)}
                  </span>
                </td>
                <td className="p-3 text-center">
                  <Badge 
                    variant="outline" 
                    className={`text-xs ${
                      prop.bookmaker === 'draftkings' 
                        ? 'bg-green-900/20 text-green-400 border-green-600/30' 
                        : 'bg-blue-900/20 text-blue-400 border-blue-600/30'
                    }`}
                  >
                    {prop.bookmaker === 'draftkings' ? 'DK' : 'FD'}
                  </Badge>
                </td>
                <td className="p-3">
                  <HitRateDisplay hitRates={hitRates} />
                </td>
                <td className="p-3 text-center">
                  {trends.includes('HOT') && (
                    <Badge className="bg-green-600/20 text-green-400 border-green-500/30 text-xs">
                      <TrendingUp className="w-3 h-3 mr-1" />HOT
                    </Badge>
                  )}
                  {trends.includes('COLD') && (
                    <Badge className="bg-blue-600/20 text-blue-400 border-blue-500/30 text-xs">
                      <TrendingDown className="w-3 h-3 mr-1" />COLD
                    </Badge>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

// Main Dashboard Component
export const DemonGoblinDashboard = () => {
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedPlayers, setExpandedPlayers] = useState(new Set());
  const [syncStatus, setSyncStatus] = useState(null);
  const [filterType, setFilterType] = useState('all'); // all, demons, goblins
  const [isPro, setIsPro] = useState(false);

  // Fetch status
  const fetchStatus = useCallback(async () => {
    try {
      const response = await axios.get(`${API}/v3/status`);
      if (response.data.success) {
        setSyncStatus(response.data.data);
      }
    } catch (error) {
      console.error('Error fetching status:', error);
    }
  }, []);

  // Fetch board data
  const fetchBoard = useCallback(async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/v3/board`);
      if (response.data.success) {
        setPlayers(response.data.players || []);
        setSyncStatus({
          sync_date: response.data.sync_date,
          last_sync: response.data.last_sync,
          unique_players: response.data.unique_players,
          total_props: response.data.total_props,
          demons_count: response.data.demons_count,
          goblins_count: response.data.goblins_count
        });
        
        toast.success(`Loaded ${response.data.unique_players} players, ${response.data.demons_count} demons, ${response.data.goblins_count} goblins`);
      }
    } catch (error) {
      console.error('Error fetching board:', error);
      toast.error('Failed to load board');
    } finally {
      setLoading(false);
    }
  }, []);

  // Trigger sync
  const triggerSync = async () => {
    try {
      setSyncing(true);
      toast.info('Starting Demon & Goblin Sync...');
      
      const response = await axios.post(`${API}/v3/sync`);
      
      if (response.data.success) {
        const result = response.data.result;
        toast.success(`Sync complete! ${result.unique_players} players, ${result.demons_count} demons, ${result.goblins_count} goblins`);
        
        await fetchBoard();
        await fetchStatus();
      }
    } catch (error) {
      console.error('Error syncing:', error);
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

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

  // Expand all players with Demons or Goblins
  const expandSpecial = () => {
    const special = players
      .filter(p => p.demons?.length > 0 || p.goblins?.length > 0)
      .map(p => p.player_name);
    setExpandedPlayers(new Set(special));
  };

  // Initial load
  useEffect(() => {
    const loadData = async () => {
      await fetchStatus();
      await fetchBoard();
    };
    loadData();

    // Auto-refresh every 5 minutes
    const interval = setInterval(() => {
      fetchBoard();
    }, 5 * 60 * 1000);

    return () => clearInterval(interval);
  }, [fetchStatus, fetchBoard]);

  // Filter players
  const getFilteredPlayers = () => {
    let filtered = players;

    // Filter by search
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      filtered = filtered.filter(p => 
        p.player_name?.toLowerCase().includes(search) ||
        p.team?.toLowerCase().includes(search)
      );
    }

    // Filter by type
    if (filterType === 'demons') {
      filtered = filtered.filter(p => (p.demons?.length || 0) > 0);
    } else if (filterType === 'goblins') {
      filtered = filtered.filter(p => (p.goblins?.length || 0) > 0);
    }

    return filtered;
  };

  const filteredPlayers = getFilteredPlayers();

  // Count totals
  const totalDemons = players.reduce((acc, p) => acc + (p.demons?.length || 0), 0);
  const totalGoblins = players.reduce((acc, p) => acc + (p.goblins?.length || 0), 0);
  const totalWarnings = players.filter(p => p.has_goblin_warning).length;

  if (loading && players.length === 0) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-center">
          <Activity className="w-12 h-12 text-purple-500 mx-auto mb-4 animate-pulse" />
          <p className="text-zinc-400">Loading Demon & Goblin Engine v3.0...</p>
          <p className="text-zinc-600 text-sm mt-2">Syncing The Odds API, BallDontLie, Tank01</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 p-4 md:p-6">
      <div className="max-w-[1600px] mx-auto">
        {/* Header */}
        <header className="mb-6 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <div className="flex items-center gap-2">
                <Skull className="w-8 h-8 text-red-500" />
                <span className="text-zinc-500">&</span>
                <Ghost className="w-8 h-8 text-green-500" />
              </div>
              <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight" data-testid="dashboard-title">
                DEMON & GOBLIN
              </h1>
              <Badge className="bg-purple-600/30 text-purple-400 border-purple-500/50 text-xs">
                v3.0
              </Badge>
            </div>
            <p className="text-zinc-400 text-sm">
              {syncStatus?.sync_date || 'Loading...'} | {syncStatus?.unique_players || 0} Players | Season 2025-26
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <Badge className={isPro ? "bg-purple-600/20 text-purple-400 border-purple-600/30" : "bg-zinc-800 text-zinc-400 border-zinc-700"}>
              {isPro ? <><Crown className="w-3 h-3 mr-1" />PRO</> : 'FREE'}
            </Badge>

            <Button
              onClick={triggerSync}
              disabled={syncing}
              variant="outline"
              size="sm"
              className="bg-purple-600/20 border-purple-600/50 text-purple-400 hover:bg-purple-600/30"
              data-testid="sync-btn"
            >
              <Database className={`w-4 h-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? 'Syncing...' : 'Full Sync'}
            </Button>

            <Button
              onClick={() => fetchBoard()}
              disabled={loading}
              variant="outline"
              size="sm"
              className="bg-zinc-800 border-zinc-700 text-white hover:bg-zinc-700"
              data-testid="refresh-btn"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>

            <Button
              onClick={() => setIsPro(!isPro)}
              variant="outline"
              size="sm"
              className="bg-purple-600 border-purple-700 text-white hover:bg-purple-700"
            >
              Toggle Pro
            </Button>
          </div>
        </header>

        {/* Stats Bar */}
        <Card className="mb-6 bg-zinc-900 border-zinc-800 p-4">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-8">
              {/* Demons Count */}
              <div 
                className={`flex items-center gap-2 cursor-pointer hover:opacity-80 ${filterType === 'demons' ? 'ring-2 ring-red-500 rounded-lg p-2 -m-2' : ''}`}
                onClick={() => setFilterType(filterType === 'demons' ? 'all' : 'demons')}
              >
                <Skull className="w-6 h-6 text-red-500" />
                <div>
                  <span className="text-red-400 font-bold text-2xl">{totalDemons}</span>
                  <p className="text-zinc-500 text-xs">Demons (+200)</p>
                </div>
              </div>
              
              {/* Goblins Count */}
              <div 
                className={`flex items-center gap-2 cursor-pointer hover:opacity-80 ${filterType === 'goblins' ? 'ring-2 ring-green-500 rounded-lg p-2 -m-2' : ''}`}
                onClick={() => setFilterType(filterType === 'goblins' ? 'all' : 'goblins')}
              >
                <Ghost className="w-6 h-6 text-green-500" />
                <div>
                  <span className="text-green-400 font-bold text-2xl">{totalGoblins}</span>
                  <p className="text-zinc-500 text-xs">Goblins (-300)</p>
                </div>
              </div>

              {/* Warnings */}
              {totalWarnings > 0 && (
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-6 h-6 text-yellow-500" />
                  <div>
                    <span className="text-yellow-400 font-bold text-2xl">{totalWarnings}</span>
                    <p className="text-zinc-500 text-xs">Warnings</p>
                  </div>
                </div>
              )}

              <div className="border-l border-zinc-700 pl-6">
                <span className="text-zinc-400 text-sm">
                  <User className="w-4 h-4 inline mr-1" />
                  <span className="text-white font-bold">{filteredPlayers.length}</span> / {players.length} players
                </span>
              </div>
            </div>
            
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <Clock className="w-4 h-4" />
              Last sync: {syncStatus?.last_sync ? new Date(syncStatus.last_sync).toLocaleTimeString() : 'Never'}
            </div>
          </div>
        </Card>

        {/* Filters */}
        <div className="mb-6 flex flex-col md:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-zinc-400" />
            <Input
              placeholder="Search player (e.g., LeBron, SGA, Tatum)..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 bg-zinc-900 border-zinc-700 text-white placeholder:text-zinc-500"
              data-testid="search-input"
            />
          </div>

          <Button
            onClick={expandSpecial}
            variant="outline"
            size="sm"
            className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          >
            Expand Demons & Goblins
          </Button>

          <Button
            onClick={() => setExpandedPlayers(new Set())}
            variant="outline"
            size="sm"
            className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          >
            Collapse All
          </Button>
        </div>

        {/* Players List */}
        <div className="rounded-lg border border-zinc-800 overflow-hidden" data-testid="players-list">
          {filteredPlayers.length === 0 ? (
            <div className="p-8 text-center text-zinc-400">
              {syncing ? 'Sync in progress...' : 'No players found. Click "Full Sync" to fetch data.'}
            </div>
          ) : (
            filteredPlayers.map((player) => {
              const isExpanded = expandedPlayers.has(player.player_name);
              
              return (
                <div key={player.player_name} className="border-b border-zinc-800 last:border-b-0">
                  <PlayerCardCollapsed
                    player={player}
                    isExpanded={isExpanded}
                    onToggle={() => togglePlayer(player.player_name)}
                  />
                  {isExpanded && <PlayerCardExpanded player={player} />}
                </div>
              );
            })
          )}
        </div>

        {/* Legend */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="bg-red-950/20 border-red-500/30 p-4">
            <div className="flex items-center gap-3">
              <Skull className="w-8 h-8 text-red-500" />
              <div>
                <h3 className="text-red-400 font-bold">DEMONS</h3>
                <p className="text-xs text-zinc-400">Odds +200 or higher</p>
                <p className="text-xs text-zinc-500">Harder props, high payout</p>
              </div>
            </div>
          </Card>
          <Card className="bg-green-950/20 border-green-500/30 p-4">
            <div className="flex items-center gap-3">
              <Ghost className="w-8 h-8 text-green-500" />
              <div>
                <h3 className="text-green-400 font-bold">GOBLINS</h3>
                <p className="text-xs text-zinc-400">Odds -300 or lower</p>
                <p className="text-xs text-zinc-500">Easier props, high probability</p>
              </div>
            </div>
          </Card>
          <Card className="bg-yellow-950/20 border-yellow-500/30 p-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-8 h-8 text-yellow-500" />
              <div>
                <h3 className="text-yellow-400 font-bold">WARNINGS</h3>
                <p className="text-xs text-zinc-400">90%+ hit rate + Questionable</p>
                <p className="text-xs text-zinc-500">Check player status first</p>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default DemonGoblinDashboard;
