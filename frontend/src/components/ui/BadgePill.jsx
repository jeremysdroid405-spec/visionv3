/**
 * BadgePill Component - Visual Badge Registry
 * ==========================================
 * Renders context badges as glowing pills with Lucide icons.
 * 
 * Badge Registry (10 Standard Badges):
 * - legal_noise: Legal/personal news flag
 * - milestone: Within 5% of career stat
 * - locked_in: +5 PPG over season mean L5
 * - jet_lag: Road game + 1000mi travel
 * - revenge: Playing former team
 * - home_cookin: Home PPG 15%+ higher
 * - gassed: Back-to-back 2nd night
 * - pay_day: Contract year
 * - deep_water: Elimination/playoff game 5+
 * - distraction: Trade rumors/drama
 */
import React from 'react';
import { 
  Gavel, 
  Trophy, 
  Target, 
  Plane, 
  Swords, 
  Home, 
  BatteryLow, 
  Coins, 
  Waves, 
  AlertCircle 
} from 'lucide-react';

// Badge Registry - Maps badge IDs to visual config
export const BADGE_REGISTRY = {
  legal_noise: {
    label: "Legal Noise",
    icon: Gavel,
    glowColor: "#f97316",  // Orange
    bgClass: "bg-orange-500/20",
    borderClass: "border-orange-500/40",
    textClass: "text-orange-400",
    glowClass: "shadow-orange-500/30",
    trigger: "Active legal/personal news flag in context"
  },
  milestone: {
    label: "Milestone",
    icon: Trophy,
    glowColor: "#eab308",  // Gold/Yellow
    bgClass: "bg-yellow-500/20",
    borderClass: "border-yellow-500/40",
    textClass: "text-yellow-400",
    glowClass: "shadow-yellow-500/30",
    trigger: "Within 5% of a major career stat"
  },
  locked_in: {
    label: "Locked In",
    icon: Target,
    glowColor: "#06b6d4",  // Cyan
    bgClass: "bg-cyan-500/20",
    borderClass: "border-cyan-500/40",
    textClass: "text-cyan-400",
    glowClass: "shadow-cyan-500/30",
    trigger: "Avg +5 PPG over season mean in L5"
  },
  jet_lag: {
    label: "Jet Lag",
    icon: Plane,
    glowColor: "#a855f7",  // Purple
    bgClass: "bg-purple-500/20",
    borderClass: "border-purple-500/40",
    textClass: "text-purple-400",
    glowClass: "shadow-purple-500/30",
    trigger: "Road game + traveled >1000mi in 48hrs"
  },
  revenge: {
    label: "Revenge",
    icon: Swords,
    glowColor: "#ef4444",  // Red
    bgClass: "bg-red-500/20",
    borderClass: "border-red-500/40",
    textClass: "text-red-400",
    glowClass: "shadow-red-500/30",
    trigger: "Playing against former team"
  },
  home_cookin: {
    label: "Home Cookin'",
    icon: Home,
    glowColor: "#22c55e",  // Green
    bgClass: "bg-green-500/20",
    borderClass: "border-green-500/40",
    textClass: "text-green-400",
    glowClass: "shadow-green-500/30",
    trigger: "Home PPG 15%+ higher than Away"
  },
  gassed: {
    label: "Gassed",
    icon: BatteryLow,
    glowColor: "#dc2626",  // Red-600
    bgClass: "bg-red-600/20",
    borderClass: "border-red-600/40",
    textClass: "text-red-500",
    glowClass: "shadow-red-600/30",
    trigger: "2nd night of back-to-back"
  },
  pay_day: {
    label: "Pay Day",
    icon: Coins,
    glowColor: "#10b981",  // Emerald
    bgClass: "bg-emerald-500/20",
    borderClass: "border-emerald-500/40",
    textClass: "text-emerald-400",
    glowClass: "shadow-emerald-500/30",
    trigger: "Final year of contract"
  },
  deep_water: {
    label: "Deep Water",
    icon: Waves,
    glowColor: "#3b82f6",  // Blue
    bgClass: "bg-blue-500/20",
    borderClass: "border-blue-500/40",
    textClass: "text-blue-400",
    glowClass: "shadow-blue-500/30",
    trigger: "Elimination or playoff game 5+"
  },
  distraction: {
    label: "Distraction",
    icon: AlertCircle,
    glowColor: "#d97706",  // Amber
    bgClass: "bg-amber-500/20",
    borderClass: "border-amber-500/40",
    textClass: "text-amber-400",
    glowClass: "shadow-amber-500/30",
    trigger: "Trade rumors or locker room drama"
  }
};

/**
 * BadgePill Component
 * Renders a single badge as a glowing pill with icon
 */
export const BadgePill = ({ 
  badgeId, 
  label: customLabel, 
  size = 'md',
  showGlow = true,
  className = '' 
}) => {
  // Get badge config from registry or use custom
  const badge = BADGE_REGISTRY[badgeId] || {
    label: customLabel || badgeId,
    icon: AlertCircle,
    bgClass: "bg-zinc-500/20",
    borderClass: "border-zinc-500/40",
    textClass: "text-zinc-400",
    glowClass: "shadow-zinc-500/30"
  };
  
  const Icon = badge.icon;
  
  // Size variants
  const sizeClasses = {
    sm: "px-1.5 py-0.5 text-[9px] gap-0.5",
    md: "px-2 py-1 text-[10px] gap-1",
    lg: "px-3 py-1.5 text-xs gap-1.5"
  };
  
  const iconSizes = {
    sm: 10,
    md: 12,
    lg: 14
  };
  
  return (
    <span 
      className={`
        inline-flex items-center rounded-full border font-semibold uppercase tracking-wide
        ${badge.bgClass} ${badge.borderClass} ${badge.textClass}
        ${showGlow ? `shadow-lg ${badge.glowClass}` : ''}
        ${sizeClasses[size]}
        ${className}
      `}
      title={badge.trigger}
    >
      <Icon size={iconSizes[size]} className="flex-shrink-0" />
      <span>{badge.label}</span>
    </span>
  );
};

/**
 * BadgeRow Component
 * Renders multiple badges in a horizontal row
 */
export const BadgeRow = ({ badges = [], size = 'md', className = '' }) => {
  if (!badges || badges.length === 0) return null;
  
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {badges.map((badge, idx) => (
        <BadgePill 
          key={badge.id || badge.badge_key || idx}
          badgeId={badge.id || badge.badge_key}
          label={badge.label}
          size={size}
        />
      ))}
    </div>
  );
};

/**
 * Get badge config by ID
 */
export const getBadgeConfig = (badgeId) => {
  return BADGE_REGISTRY[badgeId] || null;
};

export default BadgePill;
