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
import React, { useState } from 'react';
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
  HeartPulse,
  X,
  // MLB-specific icons
  Flame,
  Shield,
  Zap,
  Wind,
  Snowflake,
  BarChart3
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip';

// Badge Registry - Maps badge IDs to visual config with enhanced tooltips
// Includes both NBA and MLB badges for sport-specific rendering
export const BADGE_REGISTRY = {
  // ==================== NBA BADGES ====================
  injured: {
    label: "Injured",
    icon: HeartPulse,
    glowColor: "#dc2626",  // Red
    bgClass: "bg-red-600/20",
    borderClass: "border-red-600/40",
    textClass: "text-red-500",
    glowClass: "shadow-red-600/30",
    trigger: "Player has reported injury",
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
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
    sport: "nba",
    tooltip: {
      title: "Distraction",
      description: "Player is involved in trade rumors, public disputes, or locker room issues.",
      impact: "Mental distractions can hurt focus. Watch for unusual body language or effort.",
      sentiment: "negative"
    }
  },
  
  // ==================== MLB BADGES ====================
  pure_contact: {
    label: "Pure Contact",
    icon: Target,
    glowColor: "#22c55e",  // Green
    bgClass: "bg-green-500/20",
    borderClass: "border-green-500/40",
    textClass: "text-green-400",
    glowClass: "shadow-green-500/30",
    trigger: "Whiff Rate < 15% + xBA > .290",
    sport: "mlb",
    tooltip: {
      title: "Pure Contact",
      description: "Elite contact hitter with exceptional plate discipline. Low whiff rate combined with high expected batting average.",
      impact: "This batter makes consistent contact. Great for hits and total bases overs.",
      sentiment: "positive"
    }
  },
  high_heat_trap: {
    label: "High-Heat Trap",
    icon: Flame,
    glowColor: "#ef4444",  // Red
    bgClass: "bg-red-500/20",
    borderClass: "border-red-500/40",
    textClass: "text-red-400",
    glowClass: "shadow-red-500/30",
    trigger: "Facing pitcher with +1.5mph velocity spike",
    sport: "mlb",
    tooltip: {
      title: "High-Heat Trap",
      description: "Opposing pitcher has shown a significant velocity increase (+1.5mph) in recent outings.",
      impact: "Caution: Hotter fastballs = more swings and misses. Consider unders.",
      sentiment: "negative"
    }
  },
  workhorse: {
    label: "Workhorse",
    icon: Shield,
    glowColor: "#3b82f6",  // Blue
    bgClass: "bg-blue-500/20",
    borderClass: "border-blue-500/40",
    textClass: "text-blue-400",
    glowClass: "shadow-blue-500/30",
    trigger: "80% L10 reaching 6th inning",
    sport: "mlb",
    tooltip: {
      title: "Workhorse",
      description: "Reliable starting pitcher who consistently goes deep into games. 80%+ of last 10 starts reached the 6th inning.",
      impact: "Expect 5.5+ innings. Great for pitcher strikeout overs and pitching outs.",
      sentiment: "positive"
    }
  },
  barrel_master: {
    label: "Barrel Master",
    icon: Zap,
    glowColor: "#f97316",  // Orange
    bgClass: "bg-orange-500/20",
    borderClass: "border-orange-500/40",
    textClass: "text-orange-400",
    glowClass: "shadow-orange-500/30",
    trigger: "Barrel % > 15% over L25 PA",
    sport: "mlb",
    tooltip: {
      title: "Barrel Master",
      description: "Elite power hitter with exceptional barrel rate (>15%) over the last 25 plate appearances.",
      impact: "High exit velocities = more extra-base hits. Great for total bases and home runs.",
      sentiment: "positive"
    }
  },
  wind_boost: {
    label: "Wind Blowing Out",
    icon: Wind,
    glowColor: "#06b6d4",  // Cyan
    bgClass: "bg-cyan-500/20",
    borderClass: "border-cyan-500/40",
    textClass: "text-cyan-400",
    glowClass: "shadow-cyan-500/30",
    trigger: "Wind conditions favor Over bets (+10% boost)",
    sport: "mlb",
    tooltip: {
      title: "Wind Blowing Out",
      description: "Current weather shows wind blowing out to the outfield at 10+ mph.",
      impact: "Fly balls carry further. Boost for home runs and total bases overs.",
      sentiment: "positive"
    }
  },
  cold_zone: {
    label: "Cold Zone",
    icon: Snowflake,
    glowColor: "#60a5fa",  // Light Blue
    bgClass: "bg-blue-400/20",
    borderClass: "border-blue-400/40",
    textClass: "text-blue-300",
    glowClass: "shadow-blue-400/30",
    trigger: "Pitcher-friendly umpire (SZR > 1.05)",
    sport: "mlb",
    tooltip: {
      title: "Cold Zone",
      description: "Tonight's home plate umpire has a strike zone ratio above 1.05, favoring pitchers.",
      impact: "Expanded strike zone = more called strikes. Lean towards pitcher props.",
      sentiment: "cautionary"
    }
  },
  bvp_dominator: {
    label: "BvP Dominator",
    icon: Swords,
    glowColor: "#a855f7",  // Purple
    bgClass: "bg-purple-500/20",
    borderClass: "border-purple-500/40",
    textClass: "text-purple-400",
    glowClass: "shadow-purple-500/30",
    trigger: "Strong historical performance vs today's pitcher",
    sport: "mlb",
    tooltip: {
      title: "BvP Dominator",
      description: "This batter has historically dominated the opposing pitcher (20+ PA sample, .300+ AVG or 1.000+ OPS).",
      impact: "History repeats. Extra confidence on overs for this matchup.",
      sentiment: "positive"
    }
  },
  split_advantage: {
    label: "Split Advantage",
    icon: BarChart3,
    glowColor: "#14b8a6",  // Teal
    bgClass: "bg-teal-500/20",
    borderClass: "border-teal-500/40",
    textClass: "text-teal-400",
    glowClass: "shadow-teal-500/30",
    trigger: "Favorable handedness matchup",
    sport: "mlb",
    tooltip: {
      title: "Split Advantage",
      description: "Batter has a significant platoon advantage (e.g., lefty batter vs righty pitcher with strong L vs R splits).",
      impact: "Platoon splits are real in MLB. Use this edge for hitting props.",
      sentiment: "positive"
    }
  },
  whiff_wizard: {
    label: "Whiff Wizard",
    icon: Zap,
    glowColor: "#8b5cf6",  // Violet
    bgClass: "bg-violet-500/20",
    borderClass: "border-violet-500/40",
    textClass: "text-violet-400",
    glowClass: "shadow-violet-500/30",
    trigger: "K% > 28% + SwStr% > 12%",
    sport: "mlb",
    tooltip: {
      title: "Whiff Wizard",
      description: "Elite strikeout pitcher with dominant swing-and-miss stuff. K rate >28% and swinging strike rate >12%.",
      impact: "This pitcher generates tons of whiffs. Excellent for strikeout overs.",
      sentiment: "positive"
    }
  },
  hitters_haven: {
    label: "Hitter's Haven",
    icon: Home,
    glowColor: "#22c55e",  // Green
    bgClass: "bg-green-500/20",
    borderClass: "border-green-500/40",
    textClass: "text-green-400",
    glowClass: "shadow-green-500/30",
    trigger: "Playing in hitter-friendly park (Coors, GABP)",
    sport: "mlb",
    tooltip: {
      title: "Hitter's Haven",
      description: "Game is at a hitter-friendly ballpark like Coors Field, Great American Ballpark, or Fenway Park.",
      impact: "Expect inflated offensive numbers. Boost for total bases and RBI overs.",
      sentiment: "positive"
    }
  },
  volatility_extreme: {
    label: "Extreme Volatility",
    icon: BarChart3,
    glowColor: "#ef4444",  // Red
    bgClass: "bg-red-500/20",
    borderClass: "border-red-500/40",
    textClass: "text-red-400",
    glowClass: "shadow-red-500/30",
    trigger: "Volatility Index > 8 (Gemini scored)",
    sport: "mlb",
    tooltip: {
      title: "Extreme Volatility",
      description: "AI analysis indicates this player has extreme variance (Volatility Index > 8/10). True lottery ticket.",
      impact: "High risk, high reward. Perfect for War Zone moonshots chasing 2000x payouts.",
      sentiment: "cautionary"
    }
  }
};

/**
 * BadgePill Component
 * Renders a single badge as a glowing pill with icon and tooltip
 * On mobile: Click to show description in a modal overlay
 * On desktop: Hover tooltip
 */
export const BadgePill = ({ 
  badgeId, 
  label: customLabel, 
  size = 'md',
  showGlow = true,
  showTooltip = true,
  className = '' 
}) => {
  const [showMobilePopup, setShowMobilePopup] = useState(false);
  
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
  
  // Handle click for mobile
  const handleClick = (e) => {
    e.stopPropagation();
    if (badge.tooltip) {
      setShowMobilePopup(true);
    }
  };
  
  const pillContent = (
    <span 
      onClick={handleClick}
      className={`
        inline-flex items-center rounded-full border font-semibold uppercase tracking-wide cursor-pointer
        ${badge.bgClass} ${badge.borderClass} ${badge.textClass}
        ${showGlow ? `shadow-lg ${badge.glowClass}` : ''}
        ${sizeClasses[size]}
        ${className}
        active:scale-95 transition-transform
      `}
    >
      <Icon size={iconSizes[size]} className="flex-shrink-0" />
      <span>{badge.label}</span>
    </span>
  );
  
  // Mobile popup overlay
  const mobilePopup = showMobilePopup && badge.tooltip && (
    <div 
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={() => setShowMobilePopup(false)}
    >
      <div 
        className="w-full sm:w-auto sm:max-w-sm mx-4 mb-4 sm:mb-0 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl animate-in slide-in-from-bottom-4 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full flex items-center justify-center ${badge.bgClass}`}>
              <Icon size={20} className={badge.textClass} />
            </div>
            <div>
              <span className={`text-base font-bold ${badge.textClass}`}>{badge.tooltip.title}</span>
              <span className={`block text-xs font-medium ${sentimentColors[badge.tooltip.sentiment]}`}>
                {sentimentLabels[badge.tooltip.sentiment]}
              </span>
            </div>
          </div>
          <button 
            onClick={() => setShowMobilePopup(false)}
            className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-400 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>
        
        {/* Content */}
        <div className="p-4">
          <p className="text-sm text-zinc-300 leading-relaxed mb-4">
            {badge.tooltip.description}
          </p>
          
          <div className="pt-3 border-t border-zinc-800">
            <p className="text-xs text-zinc-400 italic">
              {badge.tooltip.impact}
            </p>
          </div>
        </div>
        
        {/* Close button for mobile */}
        <div className="p-4 pt-0 sm:hidden">
          <button
            onClick={() => setShowMobilePopup(false)}
            className="w-full py-3 bg-zinc-800 rounded-lg text-zinc-300 text-sm font-medium active:bg-zinc-700"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
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
    <>
      {/* Mobile popup */}
      {mobilePopup}
      
      {/* Desktop tooltip + pill */}
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            {pillContent}
          </TooltipTrigger>
          <TooltipContent 
            side="top" 
            className="max-w-[280px] p-0 bg-zinc-900 border border-zinc-700 shadow-xl hidden sm:block"
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
    </>
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
  isActive = false,
  customDescription = null,  // Dynamic description from backend (e.g., "220 away from 1,000 career steals")
  customDetail = null        // Additional detail object from backend
}) => {
  const badge = BADGE_REGISTRY[badgeKey];
  if (!badge) return null;
  
  const Icon = badge.icon;
  const tooltip = badge.tooltip;
  
  // For active badges with custom description, show the custom one in the card
  // The tooltip will show BOTH generic and specific descriptions
  const displayTrigger = customDescription || badge.trigger;
  const hasCustomDescription = customDescription && customDescription !== badge.trigger;
  
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
        <div className={`text-[9px] truncate ${isActive ? 'text-white' : 'text-zinc-500'}`}>
          {displayTrigger}
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
      <div title={displayTrigger}>
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
          className="max-w-[300px] p-0 bg-zinc-900 border border-zinc-700 shadow-xl z-[100]"
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
            
            {/* Generic Description - What this badge means */}
            <p className="text-xs text-zinc-300 mb-2 leading-relaxed">
              {tooltip.description}
            </p>
            
            {/* Player-Specific Detail - If custom description provided */}
            {hasCustomDescription && (
              <div className="bg-zinc-800/50 rounded-md p-2 mb-2 border border-zinc-700/50">
                <p className={`text-xs font-semibold ${badge.textClass}`}>
                  {customDescription}
                </p>
              </div>
            )}
            
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
