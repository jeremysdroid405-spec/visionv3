/**
 * IntelligenceModal.jsx
 * 
 * Reusable bottom-sheet (mobile) / centered popup (desktop) modal
 * for explaining PropVision intelligence badges (Hook Risk, Vegas Bait, Officiating Impact).
 * Uses React Portal to render at document body level for proper z-index.
 */

import React from 'react';
import { createPortal } from 'react-dom';
import { X, AlertTriangle, Info, TrendingDown, Target, Scale, Snowflake } from 'lucide-react';

// Gold Whistle Icon for High Whistle refs
const GoldWhistleIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="3" fill="#FFD700" stroke="#FFD700"/>
    <path d="M12 9V4M12 4L9 7M12 4L15 7" stroke="#FFD700"/>
    <ellipse cx="12" cy="15" rx="5" ry="3" stroke="#FFD700"/>
  </svg>
);

const IntelligenceModal = ({ 
  isOpen, 
  onClose, 
  type, // 'hook_risk' | 'suspect_bait' | 'officiating_impact'
  playerName,
  statType,
  line,
  sidecarData, // { median, mode, mode_frequency_pct, std_dev, hook_warning, bait_warning }
  whistleData  // { crew_chief, ref_ou_pct, ref_ppg, whistle_class, lift_label, point_lift, foul_rate_diff }
}) => {
  if (!isOpen) return null;

  const isHookRisk = type === 'hook_risk';
  const isBait = type === 'suspect_bait';
  const isOfficiating = type === 'officiating_impact';

  // Educational content based on badge type
  const getContent = () => {
    if (isHookRisk) {
      return {
        icon: <AlertTriangle className="w-6 h-6 text-amber-400" />,
        title: "Hook Risk Detected",
        subtitle: "Line Set at Statistical Mode",
        color: "amber",
        bgGradient: "from-amber-950/90 to-zinc-900",
        borderColor: "border-amber-500/30",
        explanation: (
          <>
            <p className="text-zinc-300 text-sm leading-relaxed mb-4">
              The <span className="text-amber-400 font-semibold">Mode</span> is the most frequently occurring outcome in a player's recent games. 
              When Vegas sets a line exactly at or near the Mode, they're maximizing the probability that the result "hooks" 
              — landing exactly on the number, causing pushes or narrow misses.
            </p>
            <div className="bg-zinc-800/50 rounded-lg p-3 mb-4">
              <div className="text-xs text-zinc-500 uppercase tracking-wide mb-2">Why This Matters</div>
              <p className="text-zinc-400 text-sm">
                A line set at the Mode means the outcome you're betting on is statistically the <em>most likely</em> to hit exactly, 
                giving the house an edge on variance. You're essentially betting against probability clustering.
              </p>
            </div>
          </>
        ),
        specificData: (
          <div className="bg-amber-950/30 border border-amber-500/20 rounded-lg p-3">
            <div className="text-xs text-amber-400/70 uppercase tracking-wide mb-2">This Line's Risk Profile</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-400">Player</span>
                <span className="text-white font-medium">{playerName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Prop</span>
                <span className="text-white font-medium">{statType} @ {line}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">L20 Mode</span>
                <span className="text-amber-400 font-bold">{sidecarData?.mode}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Mode Frequency</span>
                <span className="text-amber-400 font-bold">{sidecarData?.mode_frequency_pct}% of games</span>
              </div>
              {sidecarData?.hook_warning && (
                <div className="pt-2 border-t border-amber-500/20">
                  <span className="text-amber-300 text-xs">{sidecarData.hook_warning}</span>
                </div>
              )}
            </div>
          </div>
        ),
        recommendation: "Consider avoiding or reducing stake on lines set exactly at the Mode."
      };
    }

    if (isBait) {
      // Determine volume branch for explanation
      const median = sidecarData?.median || 0;
      let volumeBranch = "HIGH";
      let volumeExplanation = "";
      
      if (median >= 10) {
        volumeBranch = "HIGH VOLUME";
        volumeExplanation = "For high-volume stats (10+ median), this line is 1.5+ standard deviations below the median with a 3+ point absolute drop.";
      } else if (median >= 4) {
        volumeBranch = "MID VOLUME";
        volumeExplanation = "For mid-volume stats (4-9.5 median), this line is 1.5+ points below the median — a significant deviation.";
      } else {
        volumeBranch = "MICRO VOLUME";
        volumeExplanation = "For micro-volume stats (under 4 median), this line is 1+ point below the median — statistically suspicious for low-count props.";
      }

      return {
        icon: <TrendingDown className="w-6 h-6 text-red-400" />,
        title: "Suspect Line: Vegas Bait",
        subtitle: "Statistically Anomalous Line",
        color: "red",
        bgGradient: "from-red-950/90 to-zinc-900",
        borderColor: "border-red-500/30",
        explanation: (
          <>
            <p className="text-zinc-300 text-sm leading-relaxed mb-4">
              When a betting line is set <span className="text-red-400 font-semibold">significantly below</span> a player's 
              statistical median, it's often a trap. Vegas may be "baiting" the public into thinking the over is a sure thing, 
              when in reality they have information suggesting reduced minutes, injury concern, or game script changes.
            </p>
            <div className="bg-zinc-800/50 rounded-lg p-3 mb-4">
              <div className="text-xs text-zinc-500 uppercase tracking-wide mb-2">Detection Method: {volumeBranch}</div>
              <p className="text-zinc-400 text-sm">
                {volumeExplanation}
              </p>
            </div>
          </>
        ),
        specificData: (
          <div className="bg-red-950/30 border border-red-500/20 rounded-lg p-3">
            <div className="text-xs text-red-400/70 uppercase tracking-wide mb-2">This Line's Risk Profile</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-400">Player</span>
                <span className="text-white font-medium">{playerName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Prop</span>
                <span className="text-white font-medium">{statType} @ {line}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">L20 Median</span>
                <span className="text-red-400 font-bold">{sidecarData?.median}</span>
              </div>
              {sidecarData?.std_dev && (
                <div className="flex justify-between">
                  <span className="text-zinc-400">Std Deviation</span>
                  <span className="text-zinc-300">{sidecarData.std_dev}</span>
                </div>
              )}
              {sidecarData?.bait_warning && (
                <div className="pt-2 border-t border-red-500/20">
                  <span className="text-red-300 text-xs">{sidecarData.bait_warning}</span>
                </div>
              )}
            </div>
          </div>
        ),
        recommendation: "Ask yourself: Why would Vegas offer such a favorable-looking line? Proceed with extreme caution."
      };
    }

    if (isOfficiating && whistleData) {
      const isHighWhistle = whistleData.whistle_class === 'high_whistle';
      const isLowWhistle = whistleData.whistle_class === 'low_whistle';
      const foulRateDiff = whistleData.foul_rate_diff || 0;
      
      return {
        icon: isHighWhistle ? (
          <GoldWhistleIcon className="w-6 h-6" />
        ) : isLowWhistle ? (
          <Snowflake className="w-6 h-6 text-blue-400" />
        ) : (
          <Scale className="w-6 h-6 text-zinc-400" />
        ),
        title: isHighWhistle ? "High Whistle Crew" : isLowWhistle ? "Low Whistle Crew" : "Officiating Impact",
        subtitle: isHighWhistle ? "Scoring Boost Expected" : isLowWhistle ? "Scoring Ceiling Expected" : "Neutral Impact",
        color: isHighWhistle ? "amber" : isLowWhistle ? "blue" : "zinc",
        bgGradient: isHighWhistle ? "from-amber-950/90 to-zinc-900" : isLowWhistle ? "from-blue-950/90 to-zinc-900" : "from-zinc-900 to-zinc-950",
        borderColor: isHighWhistle ? "border-amber-500/30" : isLowWhistle ? "border-blue-500/30" : "border-zinc-700",
        explanation: (
          <>
            <p className="text-zinc-300 text-sm leading-relaxed mb-4">
              {isHighWhistle ? (
                <>
                  This game is officiated by a <span className="text-amber-400 font-semibold">high-whistle crew</span>. 
                  Historically, games with this lead official average <span className="text-amber-400 font-semibold">{whistleData.ref_ppg} PPG</span> and 
                  hit the over <span className="text-amber-400 font-semibold">{whistleData.ref_ou_pct}%</span> of the time.
                  This suggests more free throw opportunities and higher-scoring possessions.
                </>
              ) : isLowWhistle ? (
                <>
                  This game is officiated by a <span className="text-blue-400 font-semibold">low-whistle crew</span>. 
                  Games with this lead official average only <span className="text-blue-400 font-semibold">{whistleData.ref_ppg} PPG</span> and 
                  hit the over just <span className="text-blue-400 font-semibold">{whistleData.ref_ou_pct}%</span> of the time.
                  This suggests fewer stoppages and a lower scoring pace.
                </>
              ) : (
                <>This official has a neutral whistle profile, meaning no significant deviation from league averages.</>
              )}
            </p>
            <div className="bg-zinc-800/50 rounded-lg p-3 mb-4">
              <div className="text-xs text-zinc-500 uppercase tracking-wide mb-2">Point Lift Translation</div>
              <p className="text-zinc-400 text-sm">
                {isHighWhistle ? (
                  <>The referee modifier translates to an expected <span className="text-amber-400 font-bold">{whistleData.lift_label}</span> for high-usage scoring props in this matchup.</>
                ) : isLowWhistle ? (
                  <>The referee modifier suggests a <span className="text-blue-400 font-bold">{whistleData.lift_label}</span> for scoring props, as this crew typically suppresses offense.</>
                ) : (
                  <>No adjustment applied. This crew calls games close to league average.</>
                )}
              </p>
            </div>
          </>
        ),
        specificData: (
          <div className={`${isHighWhistle ? 'bg-amber-950/30 border-amber-500/20' : isLowWhistle ? 'bg-blue-950/30 border-blue-500/20' : 'bg-zinc-800/50 border-zinc-700'} border rounded-lg p-3`}>
            <div className={`text-xs ${isHighWhistle ? 'text-amber-400/70' : isLowWhistle ? 'text-blue-400/70' : 'text-zinc-500'} uppercase tracking-wide mb-2`}>Officiating Profile</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-400">Lead Official</span>
                <span className="text-white font-medium">{whistleData.crew_chief}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">O/U Win Rate</span>
                <span className={`font-bold ${isHighWhistle ? 'text-amber-400' : isLowWhistle ? 'text-blue-400' : 'text-zinc-300'}`}>{whistleData.ref_ou_pct}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Avg PPG</span>
                <span className={`font-bold ${isHighWhistle ? 'text-amber-400' : isLowWhistle ? 'text-blue-400' : 'text-zinc-300'}`}>{whistleData.ref_ppg}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Foul Rate vs League</span>
                <span className={`font-bold ${foulRateDiff > 0 ? 'text-amber-400' : foulRateDiff < 0 ? 'text-blue-400' : 'text-zinc-300'}`}>
                  {foulRateDiff > 0 ? '+' : ''}{foulRateDiff}%
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Player</span>
                <span className="text-white font-medium">{playerName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Prop</span>
                <span className="text-white font-medium">{statType} @ {line}</span>
              </div>
              <div className="pt-2 border-t border-zinc-700/50">
                <div className="flex justify-between">
                  <span className="text-zinc-400">Impact</span>
                  <span className={`font-bold ${isHighWhistle ? 'text-amber-400' : isLowWhistle ? 'text-blue-400' : 'text-zinc-300'}`}>
                    {whistleData.lift_label || 'Neutral'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ),
        recommendation: isHighWhistle 
          ? "High-whistle crews historically boost scoring. Consider this a tailwind for PTS/FTM props."
          : isLowWhistle 
            ? "Low-whistle crews suppress scoring. Factor this headwind into your prop selection."
            : "Neutral crews have no significant impact on scoring variance."
      };
    }

    return null;
  };

  const content = getContent();
  if (!content) return null;

  const modalContent = (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[9998] transition-opacity"
        onClick={onClose}
      />
      
      {/* Modal - Bottom sheet on mobile, centered on desktop */}
      <div className={`
        fixed z-[9999] 
        sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4
        inset-x-0 bottom-0
      `}>
        <div 
          className={`
            bg-gradient-to-b ${content.bgGradient} 
            border-t sm:border ${content.borderColor}
            sm:rounded-2xl rounded-t-2xl
            w-full sm:max-w-md
            max-h-[85vh] overflow-y-auto
            shadow-2xl
          `}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="sticky top-0 bg-inherit px-5 pt-5 pb-3 border-b border-zinc-800/50">
            {/* Drag indicator for mobile */}
            <div className="sm:hidden w-12 h-1 bg-zinc-600 rounded-full mx-auto mb-4" />
            
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-lg bg-${content.color}-500/20`}>
                  {content.icon}
                </div>
                <div>
                  <h2 className="text-white font-bold text-lg">{content.title}</h2>
                  <p className="text-zinc-500 text-xs">{content.subtitle}</p>
                </div>
              </div>
              <button 
                onClick={onClose}
                className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <X className="w-5 h-5 text-zinc-400" />
              </button>
            </div>
          </div>

          {/* Content */}
          <div className="px-5 py-4 space-y-4">
            {/* PropVision Intelligence Badge */}
            <div className="flex items-center gap-2 text-xs">
              <Target className="w-4 h-4 text-cyan-400" />
              <span className="text-cyan-400 font-mono uppercase tracking-wider">PropVision Intelligence</span>
            </div>

            {/* Educational Explanation */}
            {content.explanation}

            {/* Specific Data */}
            {content.specificData}

            {/* Recommendation */}
            <div className="flex items-start gap-2 bg-zinc-800/30 rounded-lg p-3 border border-zinc-700/30">
              <Info className="w-4 h-4 text-cyan-400 mt-0.5 flex-shrink-0" />
              <p className="text-zinc-300 text-sm">
                <span className="text-cyan-400 font-medium">Recommendation: </span>
                {content.recommendation}
              </p>
            </div>
          </div>

          {/* Footer */}
          <div className="sticky bottom-0 bg-inherit px-5 py-4 border-t border-zinc-800/50">
            <button
              onClick={onClose}
              className={`
                w-full py-3 rounded-xl font-semibold text-sm
                bg-gradient-to-r 
                ${isHookRisk ? 'from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600' : ''}
                ${isBait ? 'from-red-600 to-red-700 hover:from-red-500 hover:to-red-600' : ''}
                text-white
                transition-all duration-200
                shadow-lg
              `}
            >
              Got It
            </button>
          </div>
        </div>
      </div>
    </>
  );

  // Use portal to render at document body level
  return createPortal(modalContent, document.body);
};

export default IntelligenceModal;
