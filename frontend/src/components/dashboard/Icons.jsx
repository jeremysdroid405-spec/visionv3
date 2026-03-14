import React, { useState, memo } from 'react';
import { Zap } from 'lucide-react';

// The Elite Demon - Simple red icon without circle
export const DemonIcon = memo(({ size = 24, className = '', isScanning = false, hasVision = false }) => (
  <div className={`demon-icon-container ${isScanning ? 'demon-scanning' : ''} ${className}`} style={{ width: size, height: size }}>
    <svg 
      width={size} 
      height={size} 
      viewBox="0 0 24 24" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg"
      className="demon-icon"
    >
      {/* Devil Horns */}
      <path 
        d="M5 12L2 3L9 10L12 8L15 10L22 3L19 12" 
        fill="#EF4444"
        stroke="#DC2626" 
        strokeWidth="1.5" 
        strokeLinejoin="round"
      />
      {/* Eyes */}
      <circle cx="8" cy="14" r="1.5" fill="#EF4444"/>
      <circle cx="16" cy="14" r="1.5" fill="#EF4444"/>
      {/* Smirk */}
      <path 
        d="M9 18C10 19.5 14 19.5 15 18" 
        stroke="#EF4444" 
        strokeWidth="1.5" 
        strokeLinecap="round"
      />
    </svg>
    {hasVision && <div className="vision-sparkle" />}
  </div>
));

DemonIcon.displayName = 'DemonIcon';

// The Elite Goblin - Simple green icon without circle
export const GoblinIcon = memo(({ size = 24, className = '', isClicked = false, hasVision = false }) => {
  const [clicked, setClicked] = useState(false);
  
  const handleClick = (e) => {
    e.stopPropagation();
    setClicked(true);
    setTimeout(() => setClicked(false), 500);
  };
  
  return (
    <div 
      className={`goblin-icon-container ${clicked || isClicked ? 'goblin-pulse' : ''} ${className}`} 
      style={{ width: size, height: size }}
      onClick={handleClick}
    >
      <svg 
        width={size} 
        height={size} 
        viewBox="0 0 24 24" 
        fill="none" 
        xmlns="http://www.w3.org/2000/svg"
        className="goblin-icon"
      >
        {/* Pointy Ears */}
        <path 
          d="M5 14L1 4L8 12" 
          fill="#22C55E"
          stroke="#16A34A" 
          strokeWidth="1.5" 
          strokeLinejoin="round"
        />
        <path 
          d="M19 14L23 4L16 12" 
          fill="#22C55E"
          stroke="#16A34A" 
          strokeWidth="1.5" 
          strokeLinejoin="round"
        />
        {/* Eyes */}
        <circle cx="9" cy="13" r="1.5" fill="#22C55E"/>
        <circle cx="15" cy="13" r="1.5" fill="#22C55E"/>
        {/* Grin */}
        <path 
          d="M8 17C9.5 19 14.5 19 16 17" 
          stroke="#22C55E" 
          strokeWidth="1.5" 
          strokeLinecap="round"
        />
      </svg>
      {hasVision && <div className="vision-sparkle vision-sparkle-green" />}
    </div>
  );
});

GoblinIcon.displayName = 'GoblinIcon';

// Vision Synergy Badge - For Master Tier cards
export const VisionBadge = memo(({ type = 'demon', hasVision = false }) => {
  if (!hasVision) return null;
  
  return (
    <div className={`
      absolute -top-2 -right-2 w-8 h-8 rounded-full 
      flex items-center justify-center z-10
      ${type === 'demon' 
        ? 'bg-gradient-to-br from-red-600 to-red-900 shadow-lg shadow-red-500/50' 
        : 'bg-gradient-to-br from-green-600 to-green-900 shadow-lg shadow-green-500/50'
      }
    `}>
      <Zap className="w-4 h-4 text-white" />
    </div>
  );
});

VisionBadge.displayName = 'VisionBadge';
