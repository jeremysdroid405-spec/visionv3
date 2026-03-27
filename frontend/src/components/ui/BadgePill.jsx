/**
 * BadgePill Component - Visual Badge Registry
 * ==========================================
 * Renders context badges as glowing pills with Lucide icons.
 * 
 * Badge Registry (11 Standard Badges):
 * - injured: Player has a reported injury
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
  AlertCircle,
  HeartPulse
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip';

// Badge Registry - Maps badge IDs to visual config with enhanced tooltips
export const BADGE_REGISTRY = {
  injured: {
    label: "Injured",
    icon: HeartPulse,
    glowColor: "#dc2626",  // Red
    bgClass: "bg-red-600/20",
    borderClass: "border-red-600/40",
    textClass: "text-red-500",
    glowClass: "shadow-red-600/30",
    trigger: "Player has reported injury",
    // Enhanced tooltip content
    tooltip: {
      title: "Injured",
      description: "Player is dealing with a reported injury that may affect their playing time or performance.",
      impact: "Could see reduced minutes or limited usage. Monitor game-time decisions.",
      sentiment: "negative"
    }
  },
  legal_noise: {
    label: "Legal Noise",
    icon: Gavel,
    glowColor: "#f97316",  // Orange
    bgClass: "bg-orange-500/20",
    borderClass: "border-orange-500/40",
    textClass: "text-orange-400",
    glowClass: "shadow-orange-500/30",
    trigger: "Active legal/personal news flag in context",
    tooltip: {
      title: "Legal Noise",
      description: "Player is involved in active legal proceedings or significant personal news.",
      impact: "Off-court issues can affect focus and on-court performance unpredictably.",
      sentiment: "cautionary"
    }
  },
  milestone: {
    label: "Milestone",
    icon: Trophy,
    glowColor: "#eab308",  // Gold/Yellow
    bgClass: "bg-yellow-500/20",
    borderClass: "border-yellow-500/40",
    textClass: "text-yellow-400",
    glowClass: "shadow-yellow-500/30",
    trigger: "Within 5% of a major career stat",
    tooltip: {
      title: "Milestone Alert",
      description: "Player is approaching a significant career milestone (e.g., 20,000 points, triple-double record).",
      impact: "Players often push harder when close to historic achievements. Increased motivation.",
      sentiment: "positive"
    }
  },
  locked_in: {
    label: "Locked In",
    icon: Target,
    glowColor: "#06b6d4",  // Cyan
    bgClass: "bg-cyan-500/20",
    borderClass: "border-cyan-500/40",
    textClass: "text-cyan-400",
    glowClass: "shadow-cyan-500/30",
    trigger: "Avg +5 PPG over season mean in L5",
    tooltip: {
      title: "Locked In",
      description: "Player is on a hot streak, averaging 5+ points above their season average over the last 5 games.",
      impact: "Momentum is real. Hot players tend to stay hot in the short term.",
      sentiment: "positive"
    }
  },
  jet_lag: {
    label: "Jet Lag",
    icon: Plane,
    glowColor: "#a855f7",  // Purple
    bgClass: "bg-purple-500/20",
    borderClass: "border-purple-500/40",
    textClass: "text-purple-400",
    glowClass: "shadow-purple-500/30",
    trigger: "Road game + traveled >1000mi in 48hrs",
    tooltip: {
      title: "Jet Lag",
      description: "Team traveled over 1,000 miles within the last 48 hours for this road game.",
      impact: "Long-distance travel can affect energy, shooting accuracy, and overall performance.",
      sentiment: "negative"
    }
  },
  revenge: {
    label: "Revenge",
    icon: Swords,
    glowColor: "#ef4444",  // Red
    bgClass: "bg-red-500/20",
    borderClass: "border-red-500/40",
    textClass: "text-red-400",
    glowClass: "shadow-red-500/30",
    trigger: "Playing against former team",
    tooltip: {
      title: "Revenge Game",
      description: "Player is facing their former team for the first time or in a meaningful matchup.",
      impact: "Extra motivation often leads to elevated performance. Circle this one.",
      sentiment: "positive"
    }
  },
  home_cookin: {
    label: "Home Cookin'",
    icon: Home,
    glowColor: "#22c55e",  // Green
    bgClass: "bg-green-500/20",
    borderClass: "border-green-500/40",
    textClass: "text-green-400",
    glowClass: "shadow-green-500/30",
    trigger: "Home PPG 15%+ higher than Away",
    tooltip: {
      title: "Home Cookin'",
      description: "Player scores significantly better at home, averaging 15%+ more points than on the road.",
      impact: "Some players thrive with home crowd energy. Look for elevated stats tonight.",
      sentiment: "positive"
    }
  },
  gassed: {
    label: "Gassed",
    icon: BatteryLow,
    glowColor: "#dc2626",  // Red-600
    bgClass: "bg-red-600/20",
    borderClass: "border-red-600/40",
    textClass: "text-red-500",
    glowClass: "shadow-red-600/30",
    trigger: "2nd night of back-to-back",
    tooltip: {
      title: "Gassed",
      description: "This is the second night of a back-to-back. Player played yesterday.",
      impact: "Fatigue is real. Expect possible minute restrictions or decreased efficiency.",
      sentiment: "negative"
    }
  },
  pay_day: {
    label: "Pay Day",
    icon: Coins,
    glowColor: "#10b981",  // Emerald
    bgClass: "bg-emerald-500/20",
    borderClass: "border-emerald-500/40",
    textClass: "text-emerald-400",
    glowClass: "shadow-emerald-500/30",
    trigger: "Final year of contract",
    tooltip: {
      title: "Pay Day",
      description: "Player is in the final year of their contract and looking to prove their value.",
      impact: "Contract year players often show increased effort and production.",
      sentiment: "positive"
    }
  },
  deep_water: {
    label: "Deep Water",
    icon: Waves,
    glowColor: "#3b82f6",  // Blue
    bgClass: "bg-blue-500/20",
    borderClass: "border-blue-500/40",
    textClass: "text-blue-400",
    glowClass: "shadow-blue-500/30",
    trigger: "Elimination or high-stakes game",
    tooltip: {
      title: "Deep Water",
      description: "High-stakes game situation: playoff elimination game, play-in tournament, or season-defining matchup.",
      impact: "Pressure reveals character. Stars often elevate, role players can be inconsistent.",
      sentiment: "cautionary"
    }
  },
  distraction: {
    label: "Distraction",
    icon: AlertCircle,
    glowColor: "#d97706",  // Amber
    bgClass: "bg-amber-500/20",
    borderClass: "border-amber-500/40",
    textClass: "text-amber-400",
    glowClass: "shadow-amber-500/30",
    trigger: "Trade rumors or locker room drama",
    tooltip: {
      title: "Distraction",
      description: "Player is involved in trade rumors, public disputes, or locker room issues.",
      impact: "Mental distractions can hurt focus. Watch for unusual body language or effort.",
      sentiment: "negative"
    }
  }
};

/**
 * BadgePill Component
 * Renders a single badge as a glowing pill with icon and tooltip
 */
export const BadgePill = ({ 
  badgeId, 
  label: customLabel, 
  size = 'md',
  showGlow = true,
  showTooltip = true,
  className = '' 
}) => {
  // Get badge config from registry or use custom
  const badge = BADGE_REGISTRY[badgeId] || {
    label: customLabel || badgeId,
    icon: AlertCircle,
    bgClass: "bg-zinc-500/20",
    borderClass: "border-zinc-500/40",
    textClass: "text-zinc-400",
    glowClass: "shadow-zinc-500/30",
    tooltip: null
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
  
  // Sentiment colors for tooltip
  const sentimentColors = {
    positive: "text-green-400",
    negative: "text-red-400",
    cautionary: "text-amber-400"
  };
  
  const sentimentLabels = {
    positive: "Positive Signal",
    negative: "Negative Signal",
    cautionary: "Use Caution"
  };
  
  const pillContent = (
    <span 
      className={`
        inline-flex items-center rounded-full border font-semibold uppercase tracking-wide cursor-help
        ${badge.bgClass} ${badge.borderClass} ${badge.textClass}
        ${showGlow ? `shadow-lg ${badge.glowClass}` : ''}
        ${sizeClasses[size]}
        ${className}
      `}
    >
      <Icon size={iconSizes[size]} className="flex-shrink-0" />
      <span>{badge.label}</span>
    </span>
  );
  
  // If no tooltip data or tooltips disabled, return pill with title fallback
  if (!showTooltip || !badge.tooltip) {
    return (
      <span title={badge.trigger}>
        {pillContent}
      </span>
    );
  }
  
  const { title, description, impact, sentiment } = badge.tooltip;
  
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          {pillContent}
        </TooltipTrigger>
        <TooltipContent 
          side="top" 
          className="max-w-[280px] p-0 bg-zinc-900 border border-zinc-700 shadow-xl"
          sideOffset={8}
        >
          <div className="p-3">
            {/* Header with badge icon and sentiment */}
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center ${badge.bgClass}`}>
                  <Icon size={14} className={badge.textClass} />
                </div>
                <span className={`text-sm font-bold ${badge.textClass}`}>{title}</span>
              </div>
              <span className={`text-[10px] font-medium ${sentimentColors[sentiment]}`}>
                {sentimentLabels[sentiment]}
              </span>
            </div>
            
            {/* Description */}
            <p className="text-xs text-zinc-300 mb-2 leading-relaxed">
              {description}
            </p>
            
            {/* Impact */}
            <div className="pt-2 border-t border-zinc-700">
              <p className="text-[11px] text-zinc-400 italic">
                {impact}
              </p>
            </div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

/**
 * BadgeRow Component
 * Renders multiple badges in a horizontal row
 */
export const BadgeRow = ({ badges = [], size = 'md', showTooltip = true, className = '' }) => {
  if (!badges || badges.length === 0) return null;
  
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {badges.map((badge, idx) => (
        <BadgePill 
          key={badge.id || badge.badge_key || idx}
          badgeId={badge.id || badge.badge_key}
          label={badge.label}
          size={size}
          showTooltip={showTooltip}
        />
      ))}
    </div>
  );
};

/**
 * BadgeGridItem Component
 * Renders a single badge as a grid card with tooltip (for the Intel Suite badge grid)
 */
export const BadgeGridItem = ({ 
  badgeKey, 
  isActive = false 
}) => {
  const badge = BADGE_REGISTRY[badgeKey];
  if (!badge) return null;
  
  const Icon = badge.icon;
  const tooltip = badge.tooltip;
  
  // Sentiment colors for tooltip
  const sentimentColors = {
    positive: "text-green-400",
    negative: "text-red-400",
    cautionary: "text-amber-400"
  };
  
  const sentimentLabels = {
    positive: "Positive Signal",
    negative: "Negative Signal",
    cautionary: "Use Caution"
  };
  
  const cardContent = (
    <div 
      className={`flex items-center gap-2 p-2 rounded-lg border transition-all cursor-help ${
        isActive 
          ? `${badge.bgClass} ${badge.borderClass} shadow-lg ${badge.glowClass}`
          : 'bg-zinc-800/30 border-zinc-700/50 opacity-40'
      }`}
    >
      <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
        isActive ? badge.bgClass : 'bg-zinc-800'
      }`}>
        <Icon size={16} className={isActive ? badge.textClass : 'text-zinc-600'} />
      </div>
      <div className="flex-1 min-w-0">
        <div className={`text-xs font-bold ${isActive ? badge.textClass : 'text-zinc-600'}`}>
          {badge.label}
        </div>
        <div className="text-[9px] text-zinc-500 truncate">
          {badge.trigger}
        </div>
      </div>
      {isActive && (
        <div className={`w-2 h-2 rounded-full ${badge.bgClass.replace('/20', '')} animate-pulse`} />
      )}
    </div>
  );
  
  // If no tooltip data, return card with title fallback
  if (!tooltip) {
    return (
      <div title={badge.trigger}>
        {cardContent}
      </div>
    );
  }
  
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          {cardContent}
        </TooltipTrigger>
        <TooltipContent 
          side="top" 
          className="max-w-[280px] p-0 bg-zinc-900 border border-zinc-700 shadow-xl z-[100]"
          sideOffset={8}
        >
          <div className="p-3">
            {/* Header with badge icon and sentiment */}
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center ${badge.bgClass}`}>
                  <Icon size={14} className={badge.textClass} />
                </div>
                <span className={`text-sm font-bold ${badge.textClass}`}>{tooltip.title}</span>
              </div>
              <span className={`text-[10px] font-medium ${sentimentColors[tooltip.sentiment]}`}>
                {sentimentLabels[tooltip.sentiment]}
              </span>
            </div>
            
            {/* Description */}
            <p className="text-xs text-zinc-300 mb-2 leading-relaxed">
              {tooltip.description}
            </p>
            
            {/* Impact */}
            <div className="pt-2 border-t border-zinc-700">
              <p className="text-[11px] text-zinc-400 italic">
                {tooltip.impact}
              </p>
            </div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

/**
 * Get badge config by ID
 */
export const getBadgeConfig = (badgeId) => {
  return BADGE_REGISTRY[badgeId] || null;
};

/**
 * Get tooltip content for a badge
 */
export const getBadgeTooltip = (badgeId) => {
  const badge = BADGE_REGISTRY[badgeId];
  return badge?.tooltip || null;
};

export default BadgePill;
