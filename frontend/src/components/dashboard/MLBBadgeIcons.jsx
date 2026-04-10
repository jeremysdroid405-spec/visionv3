/**
 * MLB Badge Icons Component
 * 
 * Maps MLB scout insight badges to frontend icons.
 * 
 * BADGE SCHEMA:
 * 🟢 PURE_CONTACT: Target icon (green)
 * 🔴 HIGH_HEAT_TRAP: Flame icon (red)
 * 🔵 WORKHORSE: Shield icon (blue)
 * 🔥 BARREL_MASTER: Zap icon (orange)
 * 💨 WIND_BOOST: Wind icon (cyan)
 * ❄️ COLD_ZONE: Snowflake icon (light blue)
 * ⚔️ BVP_DOMINATOR: Swords icon (purple)
 * 📊 SPLIT_ADVANTAGE: BarChart3 icon (teal)
 */

import React from 'react';
import { 
  Target, 
  Flame, 
  Shield, 
  Zap, 
  Wind, 
  Snowflake, 
  Swords, 
  BarChart3,
  Badge as BadgeIcon
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../ui/tooltip';

// Badge configuration
const BADGE_CONFIG = {
  pure_contact: {
    icon: Target,
    color: '#22c55e',
    bgColor: '#dcfce7',
    label: 'Pure Contact',
    description: 'Elite contact hitter - Whiff Rate < 15% + xBA > .290'
  },
  high_heat_trap: {
    icon: Flame,
    color: '#ef4444',
    bgColor: '#fee2e2',
    label: 'High-Heat Trap',
    description: 'Facing pitcher with velocity spike +1.5mph in 2026'
  },
  workhorse: {
    icon: Shield,
    color: '#3b82f6',
    bgColor: '#dbeafe',
    label: 'Workhorse',
    description: 'Reliable pitcher - 80% L10 reaching 6th inning'
  },
  barrel_master: {
    icon: Zap,
    color: '#f97316',
    bgColor: '#ffedd5',
    label: 'Barrel Master',
    description: 'Elite power - Barrel % > 15% over last 25 PA'
  },
  wind_boost: {
    icon: Wind,
    color: '#06b6d4',
    bgColor: '#cffafe',
    label: 'Wind Blowing Out',
    description: 'Wind conditions favor Over bets (+10% boost)'
  },
  cold_zone: {
    icon: Snowflake,
    color: '#60a5fa',
    bgColor: '#e0f2fe',
    label: 'Cold Zone',
    description: 'Pitcher-friendly umpire - Strike Zone Ratio > 1.05'
  },
  bvp_dominator: {
    icon: Swords,
    color: '#a855f7',
    bgColor: '#f3e8ff',
    label: 'BvP Dominator',
    description: 'Strong historical performance vs today\'s pitcher'
  },
  split_advantage: {
    icon: BarChart3,
    color: '#14b8a6',
    bgColor: '#ccfbf1',
    label: 'Split Advantage',
    description: 'Favorable handedness matchup'
  }
};

/**
 * Single MLB Badge Icon
 */
export const MLBBadgeIcon = ({ badgeId, size = 16, showTooltip = true }) => {
  const config = BADGE_CONFIG[badgeId];
  
  if (!config) {
    return null;
  }
  
  const IconComponent = config.icon;
  
  const badge = (
    <div
      className="inline-flex items-center justify-center rounded-full p-1"
      style={{ backgroundColor: config.bgColor }}
      data-testid={`mlb-badge-${badgeId}`}
    >
      <IconComponent 
        size={size} 
        color={config.color}
        strokeWidth={2.5}
      />
    </div>
  );
  
  if (!showTooltip) {
    return badge;
  }
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          {badge}
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <div className="font-semibold">{config.label}</div>
          <div className="text-xs text-gray-400">{config.description}</div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

/**
 * MLB Badge Stack - displays multiple badges
 */
export const MLBBadgeStack = ({ badges = [], maxDisplay = 4 }) => {
  if (!badges || badges.length === 0) {
    return null;
  }
  
  const displayBadges = badges.slice(0, maxDisplay);
  const remaining = badges.length - maxDisplay;
  
  return (
    <div className="flex items-center gap-1" data-testid="mlb-badge-stack">
      {displayBadges.map((badge, index) => (
        <MLBBadgeIcon 
          key={badge.id || index}
          badgeId={badge.id}
          size={14}
        />
      ))}
      {remaining > 0 && (
        <span className="text-xs text-gray-400 ml-1">+{remaining}</span>
      )}
    </div>
  );
};

/**
 * Full Badge Display with labels
 */
export const MLBBadgeList = ({ badges = [] }) => {
  if (!badges || badges.length === 0) {
    return (
      <div className="text-sm text-gray-500">No badges earned</div>
    );
  }
  
  return (
    <div className="space-y-2" data-testid="mlb-badge-list">
      {badges.map((badge, index) => {
        const config = BADGE_CONFIG[badge.id] || {};
        const IconComponent = config.icon || BadgeIcon;
        
        return (
          <div 
            key={badge.id || index}
            className="flex items-center gap-2 p-2 rounded-lg"
            style={{ backgroundColor: config.bgColor || '#f3f4f6' }}
          >
            <IconComponent 
              size={18} 
              color={config.color || '#6b7280'}
              strokeWidth={2}
            />
            <div className="flex-1">
              <div className="font-medium text-sm" style={{ color: config.color }}>
                {badge.name || config.label}
              </div>
              {badge.metrics && (
                <div className="text-xs text-gray-600">
                  {Object.entries(badge.metrics).slice(0, 2).map(([key, value]) => (
                    <span key={key} className="mr-2">
                      {key.replace(/_/g, ' ')}: {typeof value === 'number' ? value.toFixed(2) : value}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {badge.boost && badge.boost !== 1.0 && (
              <div 
                className={`text-xs font-bold px-2 py-0.5 rounded ${
                  badge.boost > 1 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                }`}
              >
                {badge.boost > 1 ? '+' : ''}{((badge.boost - 1) * 100).toFixed(0)}%
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

/**
 * Badge Legend for UI reference
 */
export const MLBBadgeLegend = () => {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 p-3 bg-gray-900/50 rounded-lg">
      {Object.entries(BADGE_CONFIG).map(([id, config]) => {
        const IconComponent = config.icon;
        const isTrap = config.color === '#ef4444' || config.color === '#60a5fa';
        
        return (
          <div 
            key={id} 
            className="flex items-center gap-2 text-xs"
          >
            <div
              className="p-1 rounded-full"
              style={{ backgroundColor: config.bgColor }}
            >
              <IconComponent size={12} color={config.color} />
            </div>
            <span className={isTrap ? 'text-red-400' : 'text-gray-300'}>
              {config.label}
            </span>
          </div>
        );
      })}
    </div>
  );
};

export default MLBBadgeIcon;
