import React, { useState, memo } from 'react';
import { Zap } from 'lucide-react';

// The Elite Demon - Cyber-Horns (Sharp, Minimalist, Dangerous)
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
      <defs>
        <filter id="demon-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
      <path 
        d="M12 22C16.4183 22 20 18.4183 20 14C20 9.58172 16.4183 6 12 6C7.58172 6 4 9.58172 4 14C4 18.4183 7.58172 22 12 22Z" 
        fill="#FF0000" 
        filter="url(#demon-glow)"
      />
      <path 
        d="M5 8L2 2L9 5" 
        stroke="#FF0000" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
      />
      <path 
        d="M19 8L22 2L15 5" 
        stroke="#FF0000" 
        strokeWidth="2.5" 
        strokeLinejoin="round"
      />
      <path 
        d="M8 12L10 14M16 12L14 14" 
        stroke="white" 
        strokeWidth="2" 
        strokeLinecap="round"
      />
    </svg>
    {hasVision && <div className="vision-sparkle" />}
  </div>
));

DemonIcon.displayName = 'DemonIcon';

// The Elite Goblin - Sneaky Elf Ears (Cunning, Technical)
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
        <path 
          d="M12 20C15.866 20 19 16.866 19 13C19 9.13401 15.866 6 12 6C8.13401 6 5 9.13401 5 13C5 16.866 8.13401 20 12 20Z" 
          fill="#00FF7F" 
          fillOpacity="0.9"
        />
        <path 
          d="M5 11L1 7L6 12" 
          fill="#00FF7F"
        />
        <path 
          d="M19 11L23 7L18 12" 
          fill="#00FF7F"
        />
        <path 
          d="M9 13H10M14 13H15" 
          stroke="black" 
          strokeWidth="2.5" 
          strokeLinecap="round"
        />
        <path 
          d="M10 16.5C10.5 17.5 13.5 17.5 14 16.5" 
          stroke="black" 
          strokeWidth="1" 
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
