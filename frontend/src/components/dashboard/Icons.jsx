import React, { useState, memo } from 'react';
import { Zap } from 'lucide-react';

// The Elite Demon - Sharp Horns Only (No circle/head)
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
      {/* Left Horn */}
      <path 
        d="M4 18L2 4L10 14" 
        stroke="#FF3333" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
        fill="#FF0000"
        fillOpacity="0.8"
      />
      {/* Right Horn */}
      <path 
        d="M20 18L22 4L14 14" 
        stroke="#FF3333" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
        fill="#FF0000"
        fillOpacity="0.8"
      />
      {/* Evil Eyes */}
      <path 
        d="M7 16L9 14M17 16L15 14" 
        stroke="#FF0000" 
        strokeWidth="2.5" 
        strokeLinecap="round"
      />
    </svg>
    {hasVision && <div className="vision-sparkle" />}
  </div>
));

DemonIcon.displayName = 'DemonIcon';

// The Elite Goblin - Pointy Ears Only (No circle/head)
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
        {/* Left Ear */}
        <path 
          d="M4 20L1 6L10 14" 
          fill="#00FF7F"
          stroke="#00DD6F"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        {/* Right Ear */}
        <path 
          d="M20 20L23 6L14 14" 
          fill="#00FF7F"
          stroke="#00DD6F"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        {/* Sneaky Eyes */}
        <path 
          d="M8 15H10M14 15H16" 
          stroke="#00FF7F" 
          strokeWidth="2.5" 
          strokeLinecap="round"
        />
        {/* Smirk */}
        <path 
          d="M9 18C10 19 14 19 15 18" 
          stroke="#00FF7F" 
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
