import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Activity, Crown, TrendingUp, AlertCircle, RefreshCw, Search, Filter } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export const FullBoard = () => {
  const [allProps, setAllProps] = useState([]);
  const [filteredProps, setFilteredProps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [isPro, setIsPro] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedMarket, setSelectedMarket] = useState('full');
  const [selectedSource, setSelectedSource] = useState('All');

  const fetchFullBoard = async (market = 'full') => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/full-board`, {
        params: { market }
      });
      setAllProps(response.data.data || []);
      setFilteredProps(response.data.data || []);
      setLastRefresh(new Date());
      toast.success(`Loaded ${response.data.total} props`);
    } catch (error) {
      console.error('Error fetching full board:', error);
      toast.error('Failed to load props');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFullBoard(selectedMarket);

    const interval = setInterval(() => {
      fetchFullBoard(selectedMarket);
    }, 5 * 60 * 1000);

    return () => clearInterval(interval);
  }, [selectedMarket]);

  useEffect(() => {
    let filtered = allProps;

    if (searchTerm) {
      filtered = filtered.filter(prop =>
        prop.player_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        prop.team.toLowerCase().includes(searchTerm.toLowerCase()) ||
        prop.prop_type.toLowerCase().includes(searchTerm.toLowerCase())
      );
    }

    if (selectedSource !== 'All') {
      filtered = filtered.filter(prop => prop.source === selectedSource);
    }

    setFilteredProps(filtered);
  }, [searchTerm, selectedSource, allProps]);

  const handleMarketChange = (market) => {
    setSelectedMarket(market);
    fetchFullBoard(market);
  };

  if (loading && allProps.length === 0) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="text-center">
          <Activity className="w-12 h-12 text-[#22c55e] mx-auto mb-4 animate-pulse" />
          <p className="text-[#A1A1A9]">Loading full board...</p>
        </div>
      </div>
    );
  }

  const sources = ['All', 'PrizePicks', 'DraftKings', 'FanDuel', 'BetMGM', 'Caesars'];

  return (
    <div className="min-h-screen bg-[#09090B] p-4 md:p-6 lg:p-8">
      <div className="max-w-[1600px] mx-auto">
        <header className="mb-6 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Activity className="w-8 h-8 text-[#22c55e]" />
              <h1 className="text-4xl md:text-5xl font-heading font-bold text-white tracking-tight">
                NBA PROP ARCHITECT
              </h1>
            </div>
            <p className="text-[#A1A1A9] text-sm">Full PrizePicks Board Replicator + Market Mirror</p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-[#18181B] px-4 py-2 rounded-md border border-[#27272A]">
              <div className="w-2 h-2 bg-[#22c55e] rounded-full animate-pulse"></div>
              <span className="text-white text-sm font-medium">LIVE</span>
            </div>
            
            <Badge 
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
              onClick={() => fetchFullBoard(selectedMarket)}
              variant="outline"
              size="sm"
              className="bg-[#18181B] border-[#27272A] text-white hover:bg-[#27272A]"
            >
              <RefreshCw className="w-4 h-4" />
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

        <Tabs value={selectedMarket} onValueChange={handleMarketChange} className="mb-6">
          <TabsList className="grid w-full grid-cols-4 bg-[#18181B] border border-[#27272A]">
            <TabsTrigger value="full" className="data-[state=active]:bg-[#22c55e] data-[state=active]:text-white">
              Full Game
            </TabsTrigger>
            <TabsTrigger value="1q" className="data-[state=active]:bg-[#22c55e] data-[state=active]:text-white">
              1st Quarter
            </TabsTrigger>
            <TabsTrigger value="1h" className="data-[state=active]:bg-[#22c55e] data-[state=active]:text-white">
              1st Half
            </TabsTrigger>
            <TabsTrigger value="3pt" className="data-[state=active]:bg-[#22c55e] data-[state=active]:text-white">
              3-Pointers
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="mb-6 flex flex-col md:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-[#A1A1A9]" />
            <Input
              placeholder="Search player, team, or prop type..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 bg-[#18181B] border-[#27272A] text-white"
            />
          </div>

          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-[#A1A1A9]" />
            <select
              value={selectedSource}
              onChange={(e) => setSelectedSource(e.target.value)}
              className="bg-[#18181B] border border-[#27272A] text-white px-4 py-2 rounded-md"
            >
              {sources.map(source => (
                <option key={source} value={source}>{source}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="mb-4 flex items-center justify-between">
          <p className="text-[#A1A1A9] text-sm">
            Last updated: {lastRefresh.toLocaleTimeString()} | Showing {filteredProps.length} of {allProps.length} props
          </p>
        </div>

        <div className="overflow-x-auto rounded-lg border border-[#27272A]">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-[#27272A] bg-[#18181B]/50">
                <th className="text-left p-3 text-white font-heading font-semibold text-xs">PLAYER</th>
                <th className="text-left p-3 text-white font-heading font-semibold text-xs">SOURCE</th>
                <th className="text-left p-3 text-white font-heading font-semibold text-xs">PROP</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-xs">LINE</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-xs">PP LINE</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-xs">MARKET AVG</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-xs">EDGE</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-xs">MATCHUP</th>
                {isPro && (
                  <>
                    <th className="text-center p-3 text-white font-heading font-semibold text-xs">DEF RANK</th>
                    <th className="text-center p-3 text-white font-heading font-semibold text-xs">DEMON/GOBLIN</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {filteredProps.map((prop, index) => {
                const edge = prop.market_edge || 0;
                const isPositiveEdge = edge > 0;

                return (
                  <tr
                    key={index}
                    className={`border-b border-[#27272A] hover:bg-[#18181B]/50 transition-colors ${
                      prop.is_demon && isPro ? 'demon-glow border-purple-600/50' : ''
                    }`}
                  >
                    <td className="p-3">
                      <div>
                        <div className="text-white font-medium text-sm">{prop.player_name}</div>
                        <div className="text-[#A1A1A9] text-xs">{prop.team} vs {prop.opponent}</div>
                      </div>
                    </td>
                    <td className="p-3">
                      <Badge 
                        variant="outline" 
                        className={`text-xs ${
                          prop.source === 'PrizePicks' ? 'bg-purple-600/20 text-purple-400 border-purple-600/30' :
                          'bg-[#27272A] text-[#A1A1A9] border-[#3F3F46]'
                        }`}
                      >
                        {prop.source}
                      </Badge>
                    </td>
                    <td className="p-3">
                      <Badge variant="outline" className="bg-[#27272A] text-[#A1A1A9] border-[#3F3F46] uppercase text-xs">
                        {prop.prop_type}
                      </Badge>
                    </td>
                    <td className="p-3 text-center">
                      <span className="font-data text-white font-semibold">{prop.line}</span>
                    </td>
                    <td className="p-3 text-center">
                      <span className="font-data text-white font-medium">{prop.prizepicks_line || '—'}</span>
                    </td>
                    <td className="p-3 text-center">
                      <span className="font-data text-white font-medium">{prop.market_avg}</span>
                    </td>
                    <td className="p-3 text-center">
                      <span
                        className={`font-data font-bold text-sm ${
                          isPositiveEdge ? 'text-[#22c55e]' : 'text-[#ef4444]'
                        }`}
                      >
                        {isPositiveEdge ? '+' : ''}{edge}
                      </span>
                    </td>
                    <td className="p-3 text-center">
                      <Badge
                        className={`font-data ${
                          prop.matchup_grade.startsWith('A') ? 'bg-[#22c55e]/20 text-[#22c55e] border-[#22c55e]/30' :
                          prop.matchup_grade.startsWith('B') ? 'bg-[#eab308]/20 text-[#eab308] border-[#eab308]/30' :
                          'bg-[#ef4444]/20 text-[#ef4444] border-[#ef4444]/30'
                        }`}
                      >
                        {prop.matchup_grade}
                      </Badge>
                    </td>
                    {isPro && (
                      <>
                        <td className="p-3 text-center">
                          <span className="font-data text-white text-sm">#{prop.def_rank}</span>
                        </td>
                        <td className="p-3 text-center">
                          {prop.is_demon && prop.demon_line ? (
                            <div className="flex flex-col items-center gap-1">
                              <Badge className="bg-purple-600/20 text-purple-400 border-purple-600/30 animate-pulse">
                                <TrendingUp className="w-3 h-3 mr-1" />
                                DEMON {prop.demon_line}
                              </Badge>
                              <span className="text-xs text-[#A1A1A9] font-data">{(prop.hit_rate * 100).toFixed(0)}%</span>
                            </div>
                          ) : prop.is_goblin && prop.goblin_line ? (
                            <Badge className="bg-green-600/20 text-green-400 border-green-600/30">
                              GOBLIN {prop.goblin_line}
                            </Badge>
                          ) : (
                            <span className="text-[#52525B]">—</span>
                          )}
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!isPro && (
          <div className="mt-6 text-center">
            <Card className="inline-flex items-center gap-2 bg-[#18181B] px-4 py-2 border-[#27272A]">
              <AlertCircle className="w-4 h-4 text-purple-400" />
              <span className="text-[#A1A1A9] text-sm">
                Defensive rankings and Demon/Goblin lines are hidden on the free tier
              </span>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
};