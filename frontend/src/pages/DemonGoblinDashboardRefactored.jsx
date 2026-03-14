/**
 * PICKVISION AI DASHBOARD v4.0
 * =========================
 * Refactored, de-bloated dashboard with shared components.
 * 
 * DATA FLOW:
 * - All data fetched via unified DataService (no direct API calls)
 * - Photos & Stats: From nba_master_hub_2026 via player_id
 * - Odds/Lines: From daily_slate_master via cached_board
 * - NO hardcoded name-matching (handled by backend OddsApiMapper)
 */

import React, { useState, useEffect, useCallback, memo } from 'react';
import { 
  Flame, Shield, RefreshCw, Search, ChevronLeft, Target, 
  Layers, TrendingUp, LogOut, Crown, Eye, Clock
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

// Import tactical CSS
import '../styles/DashboardTactical.css';

// Data service - unified backend endpoints
import DataService from '../services/DataService';

// Shared utilities and components
import {
  SectionHeader, EmptyState, LoadingSpinner,
  PlayerPhoto, HeatIndicator, PayoutDisplay, formatStatType
} from '../lib/GlobalUtilities';
import PlayerCard from '../components/dashboard/PlayerCard';
import ParlayCard from '../components/dashboard/ParlayCard';
import { DemonIcon, GoblinIcon } from '../components/dashboard/Icons';

// ==================== SECTION COMPONENTS ====================

// War Zone Section - Top 10 Demon Picks
const WarZoneSection = memo(({ picks, onPickClick }) => (
  <div className="mb-6 war-zone-section">
    <SectionHeader 
      icon={Flame}
      title="WAR ZONE"
      subtitle="Top 10 high-probability demon plays"
      badge={`${picks.length} PICKS`}
      badgeColor="amber"
    />
    {picks.length === 0 ? (
      <EmptyState message="No demon picks available" />
    ) : (
      <div className="swipe-container">
        {picks.map((pick, idx) => (
          <div key={pick.player_id || pick.player_name || idx} className="swipe-card">
            <PlayerCard 
              pick={pick} 
              type="demon" 
              variant="full"
              onClick={onPickClick}
            />
          </div>
        ))}
      </div>
    )}
  </div>
));

// Goblin Recon Section - Top 10 Safe Plays
const GoblinReconSection = memo(({ picks, onPickClick }) => (
  <div className="mb-6 goblin-recon-section">
    <SectionHeader 
      icon={Shield}
      title="GOBLIN RECON"
      subtitle="Top 10 safe, high-consistency plays"
      badge={`${picks.length} PICKS`}
      badgeColor="emerald"
    />
    {picks.length === 0 ? (
      <EmptyState message="No goblin picks available" />
    ) : (
      <div className="swipe-container">
        {picks.map((pick, idx) => (
          <div key={pick.player_id || pick.player_name || idx} className="swipe-card">
            <PlayerCard 
              pick={pick} 
              type="goblin" 
              variant="full"
              onClick={onPickClick}
            />
          </div>
        ))}
      </div>
    )}
  </div>
));

// The Gauntlet - Demon Parlay Section
const GauntletSection = memo(({ parlays, onParlayClick }) => {
  const parlayList = Object.values(parlays || {}).sort((a, b) => 
    (a.pick_count || 0) - (b.pick_count || 0)
  );
  
  return (
    <div className="mb-6">
      <SectionHeader 
        icon={Target}
        title="THE GAUNTLET"
        subtitle="High-payout demon parlay combinations"
        badge={`${parlayList.length} TIERS`}
        badgeColor="amber"
      />
      {parlayList.length === 0 ? (
        <EmptyState message="No demon parlays available" />
      ) : (
        <div className="tactical-grid">
          {parlayList.map((parlay) => (
            <div key={parlay.tier} className="parlay-card-demon">
              <ParlayCard 
                parlay={parlay}
                type="demon"
                onClick={onParlayClick}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

// The Safe Haven - High Reliability Parlay Section
const SafeHavenSection = memo(({ parlays, onParlayClick }) => {
  const parlayList = Object.values(parlays || {}).sort((a, b) => 
    (a.pick_count || 0) - (b.pick_count || 0)
  );
  
  return (
    <div className="mb-6">
      <SectionHeader 
        icon={Shield}
        title="THE SAFE HAVEN"
        subtitle="High-consistency parlay combinations"
        badge={`${parlayList.length} TIERS`}
        badgeColor="emerald"
      />
      {parlayList.length === 0 ? (
        <EmptyState message="No safe haven parlays available" />
      ) : (
        <div className="tactical-grid">
          {parlayList.map((parlay) => (
            <div key={parlay.tier} className="parlay-card-goblin">
              <ParlayCard 
                parlay={parlay}
                type="goblin"
                onClick={onParlayClick}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

// ==================== SYNC STATUS FOOTER ====================

const SyncStatusFooter = memo(({ status }) => {
  const { time_since_sync_display, last_sync_type, scheduler_running, next_scheduled_sync } = status || {};
  
  const syncTypeClasses = {
    primary: 'sync-badge-primary',
    delta: 'sync-badge-delta',
    early_bird: 'sync-badge-early-bird'
  };
  
  return (
    <div className="sync-footer">
      <div className="max-w-7xl mx-auto flex items-center justify-between text-xs">
        <div className="flex items-center gap-4">
          <span className="text-zinc-500 font-mono">
            {time_since_sync_display || 'Loading...'}
          </span>
          {last_sync_type && (
            <span className={`px-2 py-0.5 rounded text-tactical-xs font-bold uppercase ${syncTypeClasses[last_sync_type] || syncTypeClasses.delta}`}>
              {last_sync_type === 'primary' ? 'FULL SYNC' : last_sync_type === 'early_bird' ? 'EARLY BIRD' : 'DELTA'}
            </span>
          )}
          {scheduler_running && (
            <span className="flex items-center gap-1 text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              AUTO
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-zinc-500">
          {next_scheduled_sync && (
            <span className="hidden sm:inline">
              Next: <span className="text-zinc-400">{next_scheduled_sync.time}</span>
            </span>
          )}
          <span className="text-zinc-600">PickVision AI</span>
        </div>
      </div>
    </div>
  );
});

// ==================== MAIN DASHBOARD ====================

export const DemonGoblinDashboardRefactored = ({ isDemoMode = false }) => {
  const { user, logout } = useAuth() || {};
  
  // Data state
  const [warZone, setWarZone] = useState([]);
  const [goblinVault, setGoblinVault] = useState([]);
  const [demonParlays, setDemonParlays] = useState({});
  const [goblinParlays, setGoblinParlays] = useState({});
  const [syncStatus, setSyncStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // UI state
  const [refreshing, setRefreshing] = useState(false);
  const [activeSection, setActiveSection] = useState('all');
  
  // Fetch all data via unified DataService
  const fetchData = useCallback(async () => {
    try {
      // Use batch fetch for efficiency
      const result = await DataService.fetchDashboardData();
      
      if (result.success) {
        const { data } = result;
        setWarZone(data.warZone || []);
        setGoblinVault(data.goblinVault || []);
        setDemonParlays(data.parlayBuilder?.demon_parlays || data.parlayBuilder?.parlays || {});
        setGoblinParlays(data.goblinRecon || data.parlayBuilder?.goblin_parlays || {});
        setSyncStatus(data.boardIntelStatus || data.syncStatus || {});
        setError(null);
      } else {
        setError(result.error || 'Failed to fetch data');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);
  
  // Initial fetch and refresh interval
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, [fetchData]);
  
  // Manual refresh
  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    fetchData();
  }, [fetchData]);
  
  // Pick click handler
  const handlePickClick = useCallback((pick) => {
    console.log('[PICK CLICK]', pick.player_id, pick.player_name);
    // TODO: Navigate to player detail or show modal
  }, []);
  
  // Parlay click handler
  const handleParlayClick = useCallback((parlay) => {
    console.log('[PARLAY CLICK]', parlay.name);
    // TODO: Show expanded parlay view
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="lg" />
          <p className="text-zinc-500 mt-4">Loading PickVision AI...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-white pb-16">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800">
        <div className="max-w-7xl mx-auto px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <DemonIcon className="w-8 h-8" />
                <GoblinIcon className="w-8 h-8" />
              </div>
              <div>
                <h1 className="text-lg font-bold">PickVision AI</h1>
                <p className="text-[10px] text-zinc-500">NBA Player Props Intelligence</p>
              </div>
            </div>
            
            <div className="flex items-center gap-3">
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
              </button>
              
              {!isDemoMode && user && (
                <button
                  onClick={logout}
                  className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 transition-colors"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>
      </header>
      
      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 py-4">
        {error && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
            Error: {error}
          </div>
        )}
        
        {/* War Zone */}
        <WarZoneSection picks={warZone} onPickClick={handlePickClick} />
        
        {/* The Gauntlet - Demon Parlays */}
        <GauntletSection parlays={demonParlays} onParlayClick={handleParlayClick} />
        
        {/* Goblin Recon */}
        <GoblinReconSection picks={goblinVault} onPickClick={handlePickClick} />
        
        {/* The Safe Haven - High Reliability Parlays */}
        <SafeHavenSection parlays={goblinParlays} onParlayClick={handleParlayClick} />
      </main>
      
      {/* Sync Status Footer */}
      <SyncStatusFooter status={syncStatus} />
    </div>
  );
};

export default DemonGoblinDashboardRefactored;
