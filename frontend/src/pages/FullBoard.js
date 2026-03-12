import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Activity, Crown, AlertCircle, RefreshCw, Search, Zap, Database, AlertTriangle, CheckCircle, XCircle, Newspaper, User, TrendingUp, TrendingDown } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Card color styles
const CARD_COLORS = {
  green: {
    bg: 'bg-green-900/20',
    border: 'border-l-4 border-l-green-500',
    badge: 'bg-green-600/30 text-green-400 border-green-500/50',
    icon: CheckCircle,
    label: 'HIGH HIT'
  },
  yellow: {
    bg: 'bg-yellow-900/20',
    border: 'border-l-4 border-l-yellow-500',
    badge: 'bg-yellow-600/30 text-yellow-400 border-yellow-500/50',
    icon: AlertTriangle,
    label: 'CAUTION'
  },
  red: {
    bg: 'bg-red-900/20',
    border: 'border-l-4 border-l-red-500',
    badge: 'bg-red-600/30 text-red-400 border-red-500/50',
    icon: XCircle,
    label: 'AVOID'
  },
  standard: {
    bg: 'bg-[#18181B]',
    border: 'border-l-4 border-l-[#27272A]',
    badge: 'bg-[#27272A] text-[#A1A1A9] border-[#3F3F46]',
    icon: null,
    label: ''
  }
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
  'player_points_rebounds': 'PTS+REB',
  'player_points_assists': 'PTS+AST',
  'player_rebounds_assists': 'REB+AST',
  'player_points_rebounds_assists': 'PRA',
  'player_double_double': '2x2',
  'player_first_basket': '1ST BSK',
  'alternate_player_points': 'ALT PTS',
  'alternate_player_rebounds': 'ALT REB',
  'alternate_player_assists': 'ALT AST'
};

// Hit Rate Display
const HitRateCell = ({ hitRates }) => {
  if (!hitRates) return <span className="text-[#52525B] text-xs">No data</span>;

  const l5 = hitRates.l5 || {};
  const l10 = hitRates.l10 || {};
  const season = hitRates.season || {};

  return (
    <div className="flex flex-col gap-0.5 text-xs font-mono">
      <div className="flex items-center gap-2">
        <span className="text-[#71717A] w-8">L5:</span>
        <span className={l5.hit_rate >= 0.5 ? 'text-green-400 font-bold' : 'text-white'}>
          {((l5.hit_rate || 0) * 100).toFixed(0)}%
        </span>
        <span className="text-[#52525B]">({l5.games_over || 0}/{l5.total_games || 0})</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[#71717A] w-8">L10:</span>
        <span className={l10.hit_rate >= 0.5 ? 'text-green-400 font-bold' : l10.hit_rate >= 0.4 ? 'text-purple-400 font-bold' : 'text-white'}>
          {((l10.hit_rate || 0) * 100).toFixed(0)}%
        </span>
        <span className="text-[#52525B]">({l10.games_over || 0}/{l10.total_games || 0})</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[#71717A] w-8">Avg:</span>
        <span className="text-white">{(season.avg || 0).toFixed(1)}</span>
        <span className="text-[#52525B]">({season.total_games || 0}g)</span>
      </div>
    </div>
  );
};

// News & Injuries Section
const InjuryNewsSection = ({ injuryInfo }) => {
  if (!injuryInfo || injuryInfo.warning_level === 'none') return null;
  
  const isRed = injuryInfo.warning_level === 'red';
  const hasInjury = injuryInfo.has_injury;
  const hasNews = injuryInfo.has_news;
  
  return (
    <div className={`mt-2 p-2 rounded ${isRed ? 'bg-red-900/30 border border-red-500/30' : 'bg-yellow-900/30 border border-yellow-500/30'}`}>
      {hasInjury && (
        <div className="flex items-center gap-2 text-xs">
          <AlertTriangle className={`w-3 h-3 ${isRed ? 'text-red-400' : 'text-yellow-400'}`} />
          <span className={isRed ? 'text-red-400' : 'text-yellow-400'}>
            {injuryInfo.injury_status}: {injuryInfo.injury_description || 'Check status'}
          </span>
        </div>
      )}
      {hasNews && injuryInfo.news_items?.length > 0 && (
        <div className="mt-1">
          {injuryInfo.news_items.slice(0, 2).map((news, idx) => (
            <div key={idx} className="flex items-start gap-2 text-xs mt-1">
              <Newspaper className="w-3 h-3 text-yellow-400 flex-shrink-0 mt-0.5" />
              <span className="text-yellow-200 line-clamp-2">{news.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// Card Color Badge
const CardColorBadge = ({ color }) => {
  const style = CARD_COLORS[color] || CARD_COLORS.standard;
  const Icon = style.icon;
  if (!style.label) return null;
  
  return (
    <Badge className={`${style.badge} text-xs`}>
      {Icon && <Icon className="w-3 h-3 mr-1" />}
      {style.label}
    </Badge>
  );
};

// Trend Badge
const TrendBadge = ({ trends }) => {
  if (!trends || trends.length === 0) return null;
  
  return (
    <div className="flex gap-1">
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
    </div>
  );
};

export const FullBoard = () => {
  const [allCards, setAllCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [isPro, setIsPro] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedMarket, setSelectedMarket] = useState('all');
  const [selectedColor, setSelectedColor] = useState('all');
  const [selectedBookmaker, setSelectedBookmaker] = useState('all');
  const [syncStatus, setSyncStatus] = useState(null);
  const [colorCounts, setColorCounts] = useState({ green: 0, yellow: 0, red: 0, standard: 0 });

  // Fetch status
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

  // Fetch board
  const fetchBoard = useCallback(async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/demon-tracker/board`);
      if (response.data.success) {
        setColorCounts(response.data.card_colors || {});
        
        // Flatten all cards from events
        const cards = [];
        (response.data.board || []).forEach(event => {
          (event.cards || []).forEach(card => {
            if (card) {
              cards.push({
                ...card,
                eventHome: event.home_team,
                eventAway: event.away_team
              });
            }
          });
        });
        setAllCards(cards);
        setLastRefresh(new Date());
        
        const uniquePlayers = new Set(cards.map(c => c.player_name)).size;
        toast.success(`Loaded ${cards.length} props for ${uniquePlayers} players`);
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
      toast.info('Starting Deep Ingestion (All Markets)...');
      
      const response = await axios.post(`${API}/demon-tracker/sync`);
      
      if (response.data.success) {
        const result = response.data.result;
        toast.success(`Sync complete! ${result.step2_unique_players || 0} players, ${result.demons_found || 0} demons`);
        
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

  // Initial load
  useEffect(() => {
    const loadData = async () => {
      await fetchStatus();
      await fetchBoard();
    };
    loadData();

    const interval = setInterval(() => {
      fetchBoard();
    }, 5 * 60 * 1000);

    return () => clearInterval(interval);
  }, [fetchStatus, fetchBoard]);

  // Filter cards
  const getFilteredCards = () => {
    return allCards.filter(card => {
      if (!card) return false;
      
      if (searchTerm) {
        const search = searchTerm.toLowerCase();
        const playerName = (card.player_name || '').toLowerCase();
        if (!playerName.includes(search)) return false;
      }

      if (selectedColor !== 'all' && card.card_color !== selectedColor) return false;
      if (selectedMarket !== 'all' && card.market !== selectedMarket) return false;
      if (selectedBookmaker !== 'all' && card.bookmaker !== selectedBookmaker) return false;

      return true;
    });
  };

  const filteredCards = getFilteredCards();
  const uniquePlayers = new Set(filteredCards.map(c => c.player_name)).size;

  if (loading && allCards.length === 0) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="text-center">
          <Activity className="w-12 h-12 text-green-500 mx-auto mb-4 animate-pulse" />
          <p className="text-[#A1A1A9]">Loading Deep Ingestion Engine...</p>
          <p className="text-[#52525B] text-sm mt-2">Fetching ALL markets from DraftKings & FanDuel</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090B] p-4 md:p-6">
      <div className="max-w-[1800px] mx-auto">
        {/* Header */}
        <header className="mb-6 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Zap className="w-8 h-8 text-purple-500" />
              <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight" data-testid="dashboard-title">
                DEMON TRACKER
              </h1>
              <Badge className="bg-green-600/30 text-green-400 border-green-500/50 text-xs">
                DEEP INGESTION
              </Badge>
            </div>
            <p className="text-[#A1A1A9] text-sm">
              ALL Markets | {syncStatus?.unique_players || uniquePlayers} Players | {syncStatus?.events_cached || 0} Events | Season 2025-26
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <Badge className={isPro ? "bg-purple-600/20 text-purple-400 border-purple-600/30" : "bg-[#27272A] text-[#A1A1A9] border-[#3F3F46]"}>
              {isPro ? <><Crown className="w-3 h-3 mr-1" />PRO</> : 'FREE'}
            </Badge>

            <Button
              onClick={triggerSync}
              disabled={syncing}
              variant="outline"
              size="sm"
              className="bg-green-600/20 border-green-600/50 text-green-400 hover:bg-green-600/30"
              data-testid="sync-btn"
            >
              <Database className={`w-4 h-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? 'Syncing...' : 'Deep Sync'}
            </Button>

            <Button
              onClick={() => fetchBoard()}
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
            >
              Toggle Pro
            </Button>
          </div>
        </header>

        {/* Color Stats Bar */}
        <Card className="mb-6 bg-[#18181B] border-[#27272A] p-4">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-6">
              <div 
                className="flex items-center gap-2 cursor-pointer hover:opacity-80"
                onClick={() => setSelectedColor(selectedColor === 'green' ? 'all' : 'green')}
              >
                <div className="w-4 h-4 rounded-full bg-green-500"></div>
                <span className="text-green-400 font-bold text-lg">{colorCounts.green || 0}</span>
                <span className="text-[#A1A1A9] text-sm">Green</span>
              </div>
              
              <div 
                className="flex items-center gap-2 cursor-pointer hover:opacity-80"
                onClick={() => setSelectedColor(selectedColor === 'yellow' ? 'all' : 'yellow')}
              >
                <div className="w-4 h-4 rounded-full bg-yellow-500"></div>
                <span className="text-yellow-400 font-bold text-lg">{colorCounts.yellow || 0}</span>
                <span className="text-[#A1A1A9] text-sm">Yellow</span>
              </div>
              
              <div 
                className="flex items-center gap-2 cursor-pointer hover:opacity-80"
                onClick={() => setSelectedColor(selectedColor === 'red' ? 'all' : 'red')}
              >
                <div className="w-4 h-4 rounded-full bg-red-500"></div>
                <span className="text-red-400 font-bold text-lg">{colorCounts.red || 0}</span>
                <span className="text-[#A1A1A9] text-sm">Red</span>
              </div>
              
              <div 
                className="flex items-center gap-2 cursor-pointer hover:opacity-80"
                onClick={() => setSelectedColor(selectedColor === 'standard' ? 'all' : 'standard')}
              >
                <div className="w-4 h-4 rounded-full bg-gray-500"></div>
                <span className="text-gray-400 font-bold text-lg">{colorCounts.standard || 0}</span>
                <span className="text-[#A1A1A9] text-sm">Standard</span>
              </div>

              <div className="border-l border-[#27272A] pl-6">
                <span className="text-[#A1A1A9] text-sm">
                  <User className="w-4 h-4 inline mr-1" />
                  <span className="text-white font-bold">{uniquePlayers}</span> players
                </span>
              </div>
            </div>
            
            <div className="text-xs text-[#52525B]">
              Last sync: {syncStatus?.last_sync ? new Date(syncStatus.last_sync).toLocaleTimeString() : 'Never'}
            </div>
          </div>
        </Card>

        {/* Filters */}
        <div className="mb-6 flex flex-col md:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-[#A1A1A9]" />
            <Input
              placeholder="Search player (e.g., SGA, LeBron, Tatum)..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 bg-[#18181B] border-[#27272A] text-white"
              data-testid="search-input"
            />
          </div>

          <select
            value={selectedColor}
            onChange={(e) => setSelectedColor(e.target.value)}
            className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
          >
            <option value="all">All Colors</option>
            <option value="green">🟢 Green (High Hit)</option>
            <option value="yellow">🟡 Yellow (Caution)</option>
            <option value="red">🔴 Red (Avoid)</option>
            <option value="standard">⚪ Standard</option>
          </select>

          <select
            value={selectedMarket}
            onChange={(e) => setSelectedMarket(e.target.value)}
            className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
          >
            <option value="all">All Markets</option>
            <option value="player_points">Points</option>
            <option value="player_rebounds">Rebounds</option>
            <option value="player_assists">Assists</option>
            <option value="player_threes">3-Pointers</option>
            <option value="player_points_rebounds_assists">PRA</option>
            <option value="player_double_double">Double-Double</option>
          </select>

          <select
            value={selectedBookmaker}
            onChange={(e) => setSelectedBookmaker(e.target.value)}
            className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
          >
            <option value="all">All Books</option>
            <option value="draftkings">DraftKings</option>
            <option value="fanduel">FanDuel</option>
          </select>
        </div>

        {/* Results Info */}
        <div className="mb-4 flex items-center justify-between">
          <p className="text-[#A1A1A9] text-sm">
            Showing {filteredCards.length} props for {uniquePlayers} players | Updated: {lastRefresh.toLocaleTimeString()}
          </p>
        </div>

        {/* Cards Table */}
        <div className="overflow-x-auto rounded-lg border border-[#27272A]">
          <table className="w-full border-collapse" data-testid="cards-table">
            <thead>
              <tr className="border-b border-[#27272A] bg-[#18181B]/50">
                <th className="text-left p-3 text-white font-semibold text-xs">STATUS</th>
                <th className="text-left p-3 text-white font-semibold text-xs">PLAYER</th>
                <th className="text-left p-3 text-white font-semibold text-xs">MATCHUP</th>
                <th className="text-left p-3 text-white font-semibold text-xs">PROP</th>
                <th className="text-center p-3 text-white font-semibold text-xs">LINE</th>
                <th className="text-center p-3 text-white font-semibold text-xs">BOOK</th>
                <th className="text-left p-3 text-white font-semibold text-xs">HIT RATE</th>
                <th className="text-center p-3 text-white font-semibold text-xs">TREND</th>
                {isPro && <th className="text-center p-3 text-white font-semibold text-xs">PRICE</th>}
              </tr>
            </thead>
            <tbody>
              {filteredCards.length === 0 ? (
                <tr>
                  <td colSpan={isPro ? 9 : 8} className="p-8 text-center text-[#A1A1A9]">
                    {syncing ? 'Deep sync in progress...' : 'No cards found. Click "Deep Sync" to fetch all markets.'}
                  </td>
                </tr>
              ) : (
                filteredCards.map((card, index) => {
                  const hitRates = card.hit_rates || {};
                  const cardColor = card.card_color || 'standard';
                  const colorStyle = CARD_COLORS[cardColor] || CARD_COLORS.standard;
                  const injuryInfo = card.injury_info || {};

                  return (
                    <tr
                      key={`${card.player_name}-${card.market}-${card.line}-${card.bookmaker}-${index}`}
                      className={`border-b border-[#27272A] hover:bg-[#18181B]/50 transition-colors ${colorStyle.bg} ${colorStyle.border}`}
                      data-testid={`card-row-${index}`}
                    >
                      <td className="p-3">
                        <CardColorBadge color={cardColor} />
                      </td>
                      <td className="p-3">
                        <div>
                          <div className="font-medium text-white">{card.player_name}</div>
                          <div className="text-xs text-[#52525B]">{card.bdl_team || ''} • {card.position || ''}</div>
                          <InjuryNewsSection injuryInfo={injuryInfo} />
                        </div>
                      </td>
                      <td className="p-3">
                        <div className="text-xs text-[#A1A1A9]">
                          {card.away_team || card.eventAway} @ {card.home_team || card.eventHome}
                        </div>
                      </td>
                      <td className="p-3">
                        <Badge variant="outline" className="bg-[#27272A] text-[#A1A1A9] border-[#3F3F46] text-xs font-mono">
                          {MARKET_NAMES[card.market] || card.market}
                        </Badge>
                      </td>
                      <td className="p-3 text-center">
                        <div className="flex flex-col items-center">
                          <span className="font-mono text-white font-bold">{card.line}</span>
                          <span className={`text-xs ${card.direction === 'Over' ? 'text-green-400' : 'text-red-400'}`}>
                            {card.direction}
                          </span>
                        </div>
                      </td>
                      <td className="p-3 text-center">
                        <Badge 
                          variant="outline" 
                          className={`text-xs ${
                            card.bookmaker === 'draftkings' 
                              ? 'bg-green-900/20 text-green-400 border-green-600/30' 
                              : 'bg-blue-900/20 text-blue-400 border-blue-600/30'
                          }`}
                        >
                          {card.bookmaker === 'draftkings' ? 'DK' : 'FD'}
                        </Badge>
                      </td>
                      <td className="p-3">
                        <HitRateCell hitRates={hitRates} />
                      </td>
                      <td className="p-3 text-center">
                        <TrendBadge trends={hitRates.trends} />
                      </td>
                      {isPro && (
                        <td className="p-3 text-center">
                          <span className={`font-mono text-sm ${card.price > 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {card.price > 0 ? '+' : ''}{card.price}
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

        {/* Legend */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card className="bg-green-900/10 border-green-500/30 p-3">
            <div className="flex items-center gap-2">
              <CheckCircle className="w-5 h-5 text-green-400" />
              <div>
                <h3 className="text-green-400 font-semibold text-sm">GREEN</h3>
                <p className="text-xs text-[#A1A1A9]">L10 ≥ 50%</p>
              </div>
            </div>
          </Card>
          <Card className="bg-yellow-900/10 border-yellow-500/30 p-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-yellow-400" />
              <div>
                <h3 className="text-yellow-400 font-semibold text-sm">YELLOW</h3>
                <p className="text-xs text-[#A1A1A9]">Injury/GTD</p>
              </div>
            </div>
          </Card>
          <Card className="bg-red-900/10 border-red-500/30 p-3">
            <div className="flex items-center gap-2">
              <XCircle className="w-5 h-5 text-red-400" />
              <div>
                <h3 className="text-red-400 font-semibold text-sm">RED</h3>
                <p className="text-xs text-[#A1A1A9]">L10 &lt; 30% / OUT</p>
              </div>
            </div>
          </Card>
          <Card className="bg-purple-900/10 border-purple-500/30 p-3">
            <div className="flex items-center gap-2">
              <Zap className="w-5 h-5 text-purple-400" />
              <div>
                <h3 className="text-purple-400 font-semibold text-sm">DEMON</h3>
                <p className="text-xs text-[#A1A1A9]">L10 ≥ 40%</p>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
