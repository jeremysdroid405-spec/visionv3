import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Activity, LogOut, Crown, TrendingUp, AlertCircle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export const Dashboard = () => {
  const navigate = useNavigate();
  const { user, profile, logout, token, isPro } = useAuth();
  const [bestBets, setBestBets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  const fetchBestBets = async () => {
    if (!token) return;
    
    try {
      setLoading(true);
      const response = await axios.get(`${API}/best-bets`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      setBestBets(response.data);
      setLastRefresh(new Date());
      toast.success('Data refreshed');
    } catch (error) {
      console.error('Error fetching best bets:', error);
      toast.error('Failed to load best bets');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!user) {
      navigate('/auth');
      return;
    }
    fetchBestBets();

    const interval = setInterval(() => {
      fetchBestBets();
    }, 5 * 60 * 1000);

    return () => clearInterval(interval);
  }, [user, token, navigate]);

  const handleLogout = () => {
    logout();
    navigate('/auth');
    toast.success('Logged out successfully');
  };

  if (loading && bestBets.length === 0) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="text-center">
          <Activity className="w-12 h-12 text-[#22c55e] mx-auto mb-4 animate-pulse" />
          <p className="text-[#A1A1A9]">Loading best bets...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090B] p-4 md:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Activity className="w-8 h-8 text-[#22c55e]" />
              <h1 className="text-4xl md:text-5xl font-heading font-bold text-white tracking-tight">
                BEST BETS
              </h1>
            </div>
            <p className="text-[#A1A1A9] text-sm">NBA Player Prop Arbitrage Dashboard</p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-[#18181B] px-4 py-2 rounded-md border border-[#27272A]">
              <div className="w-2 h-2 bg-[#22c55e] rounded-full animate-pulse" data-testid="live-indicator"></div>
              <span className="text-white text-sm font-medium">LIVE</span>
            </div>
            
            {profile && (
              <Badge 
                className={isPro ? "bg-purple-600/20 text-purple-400 border-purple-600/30" : "bg-[#27272A] text-[#A1A1A9] border-[#3F3F46]"}
                data-testid="tier-badge"
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
            )}

            <Button
              onClick={fetchBestBets}
              variant="outline"
              size="sm"
              className="bg-[#18181B] border-[#27272A] text-white hover:bg-[#27272A]"
              data-testid="refresh-btn"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>

            <Button
              onClick={handleLogout}
              variant="outline"
              size="sm"
              className="bg-[#18181B] border-[#27272A] text-white hover:bg-[#27272A]"
              data-testid="logout-btn"
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </header>

        {!isPro && (
          <Card className="bg-gradient-to-r from-purple-900/20 to-purple-800/20 border-purple-700/30 p-4 mb-6" data-testid="pro-upsell">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Crown className="w-5 h-5 text-purple-400" />
                <div>
                  <h3 className="text-white font-semibold">Upgrade to Pro</h3>
                  <p className="text-[#A1A1A9] text-sm">Unlock Demon lines, confidence scores, and advanced analytics</p>
                </div>
              </div>
              <Button className="bg-purple-600 hover:bg-purple-700 text-white" data-testid="upgrade-btn">
                Upgrade Now
              </Button>
            </div>
          </Card>
        )}

        <div className="mb-4 flex items-center justify-between">
          <p className="text-[#A1A1A9] text-sm">
            Last updated: {lastRefresh.toLocaleTimeString()}
          </p>
          <p className="text-[#A1A1A9] text-sm">
            {bestBets.length} opportunities found
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse" data-testid="best-bets-table">
            <thead>
              <tr className="border-b border-[#27272A] bg-[#18181B]/50">
                <th className="text-left p-3 text-white font-heading font-semibold text-sm">PLAYER</th>
                <th className="text-left p-3 text-white font-heading font-semibold text-sm">PROP</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-sm">PP LINE</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-sm">MARKET AVG</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-sm">EDGE</th>
                <th className="text-center p-3 text-white font-heading font-semibold text-sm">MATCHUP</th>
                {isPro && (
                  <>
                    <th className="text-center p-3 text-white font-heading font-semibold text-sm">CONFIDENCE</th>
                    <th className="text-center p-3 text-white font-heading font-semibold text-sm">DEMON</th>
                  </>
                )}
                <th className="text-center p-3 text-white font-heading font-semibold text-sm">SCORE</th>
              </tr>
            </thead>
            <tbody>
              {bestBets.map((bet, index) => {
                const edge = ((bet.market_avg - bet.prizepicks_line) / bet.prizepicks_line * 100).toFixed(1);
                const isPositiveEdge = parseFloat(edge) > 0;

                return (
                  <tr
                    key={index}
                    className={`border-b border-[#27272A] hover:bg-[#18181B]/50 transition-colors ${
                      bet.is_demon && isPro ? 'demon-glow border-purple-600/50' : ''
                    }`}
                    data-testid={`bet-row-${index}`}
                  >
                    <td className="p-3">
                      <div>
                        <div className="text-white font-medium">{bet.player_name}</div>
                        <div className="text-[#A1A1A9] text-xs">{bet.team}</div>
                      </div>
                    </td>
                    <td className="p-3">
                      <Badge variant="outline" className="bg-[#27272A] text-[#A1A1A9] border-[#3F3F46] uppercase text-xs">
                        {bet.prop_type}
                      </Badge>
                    </td>
                    <td className="p-3 text-center">
                      <span className="font-data text-white font-medium">{bet.prizepicks_line}</span>
                    </td>
                    <td className="p-3 text-center">
                      <span className="font-data text-white font-medium">{bet.market_avg}</span>
                    </td>
                    <td className="p-3 text-center">
                      <span
                        className={`font-data font-semibold ${
                          isPositiveEdge ? 'text-[#22c55e]' : 'text-[#ef4444]'
                        }`}
                      >
                        {isPositiveEdge ? '+' : ''}{edge}%
                      </span>
                    </td>
                    <td className="p-3 text-center">
                      <Badge
                        className={`font-data ${
                          bet.matchup_grade.startsWith('A') ? 'bg-[#22c55e]/20 text-[#22c55e] border-[#22c55e]/30' :
                          bet.matchup_grade.startsWith('B') ? 'bg-[#eab308]/20 text-[#eab308] border-[#eab308]/30' :
                          'bg-[#ef4444]/20 text-[#ef4444] border-[#ef4444]/30'
                        }`}
                      >
                        {bet.matchup_grade}
                      </Badge>
                    </td>
                    {isPro && (
                      <>
                        <td className="p-3 text-center" data-testid={`confidence-${index}`}>
                          <span className="font-data text-white font-medium">{bet.confidence}%</span>
                        </td>
                        <td className="p-3 text-center" data-testid={`demon-${index}`}>
                          {bet.is_demon ? (
                            <div className="flex flex-col items-center gap-1">
                              <Badge className="bg-purple-600/20 text-purple-400 border-purple-600/30 animate-pulse">
                                <TrendingUp className="w-3 h-3 mr-1" />
                                {bet.demon_line}
                              </Badge>
                              <span className="text-xs text-[#A1A1A9] font-data">{(bet.hit_rate * 100).toFixed(0)}% hit</span>
                            </div>
                          ) : (
                            <span className="text-[#52525B]">—</span>
                          )}
                        </td>
                      </>
                    )}
                    <td className="p-3 text-center">
                      <div className="flex items-center justify-center gap-2">
                        <div className="w-16 h-2 bg-[#27272A] rounded-full overflow-hidden">
                          <div
                            className="h-full bg-[#22c55e]"
                            style={{ width: `${Math.min(100, bet.best_bet_score)}%` }}
                          ></div>
                        </div>
                        <span className="font-data text-white font-semibold text-sm">
                          {bet.best_bet_score.toFixed(0)}
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!isPro && (
          <div className="mt-6 text-center">
            <div className="inline-flex items-center gap-2 bg-[#18181B] px-4 py-2 rounded-md border border-[#27272A]">
              <AlertCircle className="w-4 h-4 text-purple-400" />
              <span className="text-[#A1A1A9] text-sm">
                Confidence scores and Demon lines are hidden on the free tier
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};