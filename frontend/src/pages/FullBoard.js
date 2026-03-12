import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Activity, Crown, TrendingUp, AlertCircle, RefreshCw, Search, Flame, Snowflake, Target, Zap, Database } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Hit Rate Display Component
const HitRateCell = ({ hitRates }) => {
  if (!hitRates) {
    return <span className="text-[#52525B] text-xs">No data</span>;
  }

  const l5 = hitRates.l5 || {};
  const l10 = hitRates.l10 || {};
  const season = hitRates.season || {};

  return (
    <div className="flex flex-col gap-0.5 text-xs font-mono">
      <div className="flex items-center gap-2">
        <span className="text-[#A1A1A9] w-6">L5:</span>
        <span className={l5.hit_rate >= 0.5 ? 'text-[#22c55e] font-bold' : 'text-white'}>
          {((l5.hit_rate || 0) * 100).toFixed(0)}%
        </span>
        <span className="text-[#52525B]">({l5.games_over || 0}/{l5.total_games || 0})</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[#A1A1A9] w-6">L10:</span>
        <span className={l10.hit_rate >= 0.5 ? 'text-[#22c55e] font-bold' : l10.hit_rate >= 0.4 ? 'text-purple-400 font-bold' : 'text-white'}>
          {((l10.hit_rate || 0) * 100).toFixed(0)}%
        </span>
        <span className="text-[#52525B]">({l10.games_over || 0}/{l10.total_games || 0})</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[#A1A1A9] w-6">Avg:</span>
        <span className="text-white">{(season.avg || 0).toFixed(1)}</span>
      </div>
    </div>
  );
};

// Trend Badge Component
const TrendBadge = ({ trends, isDemon }) => {
  if (isDemon) {
    return (
      <Badge className="bg-purple-600/30 text-purple-400 border-purple-600/50 text-xs animate-pulse">
        <Zap className="w-3 h-3 mr-1" />
        DEMON
      </Badge>
    );
  }
  
  if (!trends || trends.length === 0) return <span className="text-[#52525B]">-</span>;
  
  return (
    <div className="flex gap-1">
      {trends.map((trend, idx) => {
        if (trend === 'HOT') {
          return (
            <Badge key={idx} className="bg-[#22c55e]/20 text-[#22c55e] border-[#22c55e]/30 text-xs">
              <Flame className="w-3 h-3 mr-1" />
              HOT
            </Badge>
          );
        }
        if (trend === 'COLD') {
          return (
            <Badge key={idx} className="bg-[#ef4444]/20 text-[#ef4444] border-[#ef4444]/30 text-xs">
              <Snowflake className="w-3 h-3 mr-1" />
              COLD
            </Badge>
          );
        }
        return null;
      })}
    </div>
  );
};

// Market Display Names
const MARKET_NAMES = {
  'player_points': 'PTS',
  'player_rebounds': 'REB',
  'player_assists': 'AST',
  'player_threes': '3PM'
};

export const FullBoard = () => {
  const [board, setBoard] = useState([]);
  const [demons, setDemons] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [isPro, setIsPro] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedMarket, setSelectedMarket] = useState('all');
  const [selectedBookmaker, setSelectedBookmaker] = useState('all');
  const [syncStatus, setSyncStatus] = useState(null);
  const [viewMode, setViewMode] = useState('board'); // 'board' or 'demons'

  // Fetch demon tracker status
  const fetchStatus = useCallback(async () => {
    try {
      const response = await axios.get(`${API}/demon-tracker/status`);
      if (response.data.success) {
        setSyncStatus(response.data.data);
      }
    } catch (error) {
      console.error('Error fetching status:', error);
    }
  }, []);

  // Fetch full board
  const fetchBoard = useCallback(async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/demon-tracker/board`);
      if (response.data.success) {
        setBoard(response.data.board || []);
        setLastRefresh(new Date());
        toast.success(`Loaded ${response.data.total_props} props, ${response.data.total_demons} demons`);
      }
    } catch (error) {
      console.error('Error fetching board:', error);
      toast.error('Failed to load board');
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch demons only
  const fetchDemons = useCallback(async () => {
    try {
      const response = await axios.get(`${API}/demon-tracker/demons`);
      if (response.data.success) {
        setDemons(response.data.demons || []);
      }
    } catch (error) {
      console.error('Error fetching demons:', error);
    }
  }, []);

  // Trigger full sync
  const triggerSync = async () => {
    try {
      setSyncing(true);
      toast.info('Starting three-way data sync...');
      
      const response = await axios.post(`${API}/demon-tracker/sync`);
      
      if (response.data.success) {
        const result = response.data.result;
        toast.success(`Sync complete: ${result.processed_count} props, ${result.demon_count} demons found`);
        
        // Refresh data
        await fetchBoard();
        await fetchDemons();
        await fetchStatus();
      }
    } catch (error) {
      console.error('Error syncing:', error);
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  // Initial load
  useEffect(() => {
    const loadData = async () => {
      await fetchStatus();
      await fetchBoard();
      await fetchDemons();
    };
    loadData();

    // Auto-refresh every 5 minutes
    const interval = setInterval(() => {
      fetchBoard();
      fetchDemons();
    }, 5 * 60 * 1000);

    return () => clearInterval(interval);
  }, [fetchStatus, fetchBoard, fetchDemons]);

  // Filter props based on search and filters
  const getFilteredProps = () => {
    let allProps = [];
    
    if (viewMode === 'demons') {
      allProps = demons;
    } else {
      board.forEach(event => {
        (event.props || []).forEach(prop => {
          if (prop) {
            allProps.push({
              ...prop,
              eventHome: event.home_team,
              eventAway: event.away_team
            });
          }
        });
      });
    }

    return allProps.filter(prop => {
      if (!prop) return false;
      
      // Search filter
      if (searchTerm) {
        const search = searchTerm.toLowerCase();
        const playerName = (prop.player_name || '').toLowerCase();
        const market = (prop.market || '').toLowerCase();
        if (!playerName.includes(search) && !market.includes(search)) {
          return false;
        }
      }

      // Market filter
      if (selectedMarket !== 'all') {
        if (prop.market !== selectedMarket) return false;
      }

      // Bookmaker filter
      if (selectedBookmaker !== 'all') {
        if (prop.bookmaker !== selectedBookmaker) return false;
      }

      return true;
    });
  };

  const filteredProps = getFilteredProps();

  if (loading && board.length === 0) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="text-center">
          <Activity className="w-12 h-12 text-[#22c55e] mx-auto mb-4 animate-pulse" />
          <p className="text-[#A1A1A9]">Loading Demon Tracker v2...</p>
          <p className="text-[#52525B] text-sm mt-2">Syncing odds from DraftKings & FanDuel</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090B] p-4 md:p-6 lg:p-8">
      <div className="max-w-[1800px] mx-auto">
        {/* Header */}
        <header className="mb-6 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Zap className="w-8 h-8 text-purple-500" />
              <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight" data-testid="dashboard-title">
                DEMON TRACKER v2
              </h1>
            </div>
            <p className="text-[#A1A1A9] text-sm">
              Three-Way Sync: Odds API + BallDontLie + Tank01 | {board.length} events | {demons.length} demons
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2 bg-[#18181B] px-4 py-2 rounded-md border border-[#27272A]">
              <div className="w-2 h-2 bg-[#22c55e] rounded-full animate-pulse"></div>
              <span className="text-white text-sm font-medium">LIVE</span>
            </div>
            
            <Badge 
              data-testid="tier-badge"
              className={isPro ? "bg-purple-600/20 text-purple-400 border-purple-600/30" : "bg-[#27272A] text-[#A1A1A9] border-[#3F3F46]"}
            >
              {isPro ? (
                <>
                  <Crown className="w-3 h-3 mr-1" />
                  PRO
                </>
              ) : (
                'FREE'
              )}
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
              {syncing ? 'Syncing...' : 'Sync All'}
            </Button>

            <Button
              onClick={() => { fetchBoard(); fetchDemons(); }}
              disabled={loading}
              variant="outline"
              size="sm"
              className="bg-[#18181B] border-[#27272A] text-white hover:bg-[#27272A]"
              data-testid="refresh-btn"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>

            <Button
              onClick={() => setIsPro(!isPro)}
              variant="outline"
              size="sm"
              className="bg-purple-600 border-purple-700 text-white hover:bg-purple-700"
              data-testid="toggle-pro-btn"
            >
              Toggle Pro
            </Button>
          </div>
        </header>

        {/* Sync Status Card */}
        {syncStatus && (
          <Card className="mb-6 bg-[#18181B] border-[#27272A] p-4">
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="flex items-center gap-6 text-sm">
                <span className="text-[#A1A1A9]">
                  <Target className="w-4 h-4 inline mr-1 text-purple-400" />
                  Events: <span className="text-white font-medium">{syncStatus.events_cached}</span>
                </span>
                <span className="text-[#A1A1A9]">
                  <Database className="w-4 h-4 inline mr-1 text-[#22c55e]" />
                  Props: <span className="text-white font-medium">{syncStatus.props_cached}</span>
                </span>
                <span className="text-[#A1A1A9]">
                  <Zap className="w-4 h-4 inline mr-1 text-purple-400" />
                  Demons: <span className="text-purple-400 font-bold">{syncStatus.demons_found}</span>
                </span>
              </div>
              <div className="text-xs text-[#52525B]">
                Last sync: {syncStatus.last_sync ? new Date(syncStatus.last_sync).toLocaleTimeString() : 'Never'}
              </div>
            </div>
          </Card>
        )}

        {/* View Mode Tabs */}
        <Tabs value={viewMode} onValueChange={setViewMode} className="mb-4">
          <TabsList className="grid w-full max-w-md grid-cols-2 bg-[#18181B] border border-[#27272A]">
            <TabsTrigger value="board" className="data-[state=active]:bg-[#22c55e] data-[state=active]:text-white" data-testid="tab-board">
              Full Board
            </TabsTrigger>
            <TabsTrigger value="demons" className="data-[state=active]:bg-purple-600 data-[state=active]:text-white" data-testid="tab-demons">
              <Zap className="w-4 h-4 mr-1" />
              Demons Only ({demons.length})
            </TabsTrigger>
          </TabsList>
        </Tabs>

        {/* Filters */}
        <div className="mb-6 flex flex-col md:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-[#A1A1A9]" />
            <Input
              placeholder="Search player or prop..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 bg-[#18181B] border-[#27272A] text-white"
              data-testid="search-input"
            />
          </div>

          <select
            value={selectedMarket}
            onChange={(e) => setSelectedMarket(e.target.value)}
            className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
            data-testid="market-filter"
          >
            <option value="all">All Markets</option>
            <option value="player_points">Points</option>
            <option value="player_rebounds">Rebounds</option>
            <option value="player_assists">Assists</option>
            <option value="player_threes">3-Pointers</option>
          </select>

          <select
            value={selectedBookmaker}
            onChange={(e) => setSelectedBookmaker(e.target.value)}
            className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
            data-testid="book-filter"
          >
            <option value="all">All Books</option>
            <option value="draftkings">DraftKings</option>
            <option value="fanduel">FanDuel</option>
          </select>
        </div>

        {/* Stats Info */}
        <div className="mb-4 flex items-center justify-between">
          <p className="text-[#A1A1A9] text-sm">
            Last updated: {lastRefresh.toLocaleTimeString()} | Showing {filteredProps.length} props
          </p>
        </div>

        {/* Props Table */}
        <div className="overflow-x-auto rounded-lg border border-[#27272A]">
          <table className="w-full border-collapse" data-testid="props-table">
            <thead>
              <tr className="border-b border-[#27272A] bg-[#18181B]/50">
                <th className="text-left p-3 text-white font-semibold text-xs">PLAYER</th>
                <th className="text-left p-3 text-white font-semibold text-xs">MATCHUP</th>
                <th className="text-left p-3 text-white font-semibold text-xs">PROP</th>
                <th className="text-center p-3 text-white font-semibold text-xs">LINE</th>
                <th className="text-center p-3 text-white font-semibold text-xs">BOOK</th>
                <th className="text-left p-3 text-white font-semibold text-xs">HIT RATE</th>
                <th className="text-center p-3 text-white font-semibold text-xs">STATUS</th>
                {isPro && (
                  <th className="text-center p-3 text-white font-semibold text-xs">PRICE</th>
                )}
              </tr>
            </thead>
            <tbody>
              {filteredProps.length === 0 ? (
                <tr>
                  <td colSpan={isPro ? 8 : 7} className="p-8 text-center text-[#A1A1A9]">
                    {syncing ? 'Syncing data...' : 'No props available. Click "Sync All" to fetch today\'s lines.'}
                  </td>
                </tr>
              ) : (
                filteredProps.map((prop, index) => {
                  const hitRates = prop.hit_rates || {};
                  const isDemon = hitRates.is_demon;
                  const l10HitRate = hitRates.l10?.hit_rate || 0;

                  return (
                    <tr
                      key={`${prop.player_name}-${prop.market}-${prop.line}-${prop.bookmaker}-${index}`}
                      className={`border-b border-[#27272A] hover:bg-[#18181B]/50 transition-colors ${
                        isDemon ? 'bg-purple-900/10 border-l-2 border-l-purple-500' : ''
                      }`}
                      data-testid={`prop-row-${index}`}
                    >
                      <td className="p-3">
                        <div className="font-medium text-white">{prop.player_name}</div>
                        <div className="text-xs text-[#52525B]">{prop.bdl_team || ''}</div>
                      </td>
                      <td className="p-3">
                        <div className="text-xs text-[#A1A1A9]">
                          {prop.away_team || prop.eventAway} @ {prop.home_team || prop.eventHome}
                        </div>
                      </td>
                      <td className="p-3">
                        <Badge variant="outline" className="bg-[#27272A] text-[#A1A1A9] border-[#3F3F46] text-xs font-mono">
                          {MARKET_NAMES[prop.market] || prop.market}
                        </Badge>
                      </td>
                      <td className="p-3 text-center">
                        <div className="flex flex-col items-center">
                          <span className="font-mono text-white font-bold">{prop.line}</span>
                          <span className={`text-xs ${prop.direction === 'Over' ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                            {prop.direction}
                          </span>
                        </div>
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
                        <HitRateCell hitRates={hitRates} />
                      </td>
                      <td className="p-3 text-center">
                        <TrendBadge trends={hitRates.trends} isDemon={isDemon} />
                      </td>
                      {isPro && (
                        <td className="p-3 text-center">
                          <span className={`font-mono text-sm ${prop.price > 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                            {prop.price > 0 ? '+' : ''}{prop.price}
                          </span>
                        </td>
                      )}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pro Tier CTA */}
        {!isPro && (
          <div className="mt-6 text-center">
            <Card className="inline-flex items-center gap-2 bg-[#18181B] px-4 py-2 border-[#27272A]">
              <AlertCircle className="w-4 h-4 text-purple-400" />
              <span className="text-[#A1A1A9] text-sm">
                Odds prices and advanced analytics are hidden on the free tier
              </span>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
};
